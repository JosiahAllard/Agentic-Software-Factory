"""Memory admission: a run whose slots are free starts only when the machine's commit charge
plus its memory need fits under the commit limit less the reserve (settings.mem_reserve_mb, 8192
MB). Short, it keeps its place and waits, its entry's mem_wait and status saying "memory: need X
MB, headroom Y MB"; nothing is paused. Exempt: a run nested in a held run, and any run when
nothing else holds slots, so it never waits for ever. The commit and the limit are read at each
admission, never kept."""
import os
import sys
import threading
import time
import unittest

from tests.lock.helpers import StoreCase, clean_env

try:
    from kitlib.lock import api
    from kitlib.lock import background as bg
    from kitlib.lock import queue as qm
    from kitlib.lock import run as rm
    from kitlib.lock import run_held
    from kitlib.lock import status as st
    from kitlib.lock.store import EXIT_BUSY, LockError, Settings, memory_now
    from kitlib.proc import process
except ImportError:                 # not Windows: StoreCase skips
    pass

GB = 1024


def entry(rid, state="held", **kw):
    e = {"id": rid, "state": state, "need": {"cpu": 1, "gpu": 0}, "cores": [], "gpus": []}
    e.update(kw)
    return e


class Pure(unittest.TestCase):
    def test_the_headroom_is_the_limit_less_the_reserve_less_the_commit(self):
        mem = {"commit_mb": 60000, "commit_limit_mb": 83400}
        self.assertEqual(qm.headroom_mb(mem, 8192), 83400 - 8192 - 60000)
        self.assertEqual(qm.headroom_mb(dict(mem, commit_mb=80000), 8192), -4792)
        for unmeasured in (None, {}, {"total_mb": 65536, "free_mb": 9000},
                           {"commit_mb": None, "commit_limit_mb": 83400},
                           {"commit_mb": 1, "commit_limit_mb": 0}):
            with self.subTest(mem=unmeasured):
                self.assertIsNone(qm.headroom_mb(unmeasured, 8192))

    def test_exempt_nested_or_with_nothing_else_held(self):
        me = entry("r-me", "waiting")
        self.assertTrue(qm.mem_exempt({"entries": [me]}, me))
        others = [entry("r-w", "waiting"), entry("r-bg", "bg")]     # nothing they hold
        self.assertTrue(qm.mem_exempt({"entries": [me] + others}, me))
        for holder in (entry("r-h", cores=[0]), entry("r-h2"), entry("r-bg2", "bg", gpus=[0])):
            with self.subTest(holder=holder["id"]):
                q = {"entries": [me, holder]}
                self.assertFalse(qm.mem_exempt(q, me))
                nested = dict(me, parent=holder["id"])
                self.assertTrue(qm.mem_exempt(dict(q, entries=[nested, holder]), nested))

    def test_the_reserve_and_the_default_need_are_lock_settings(self):
        self.assertEqual(Settings().mem_reserve_mb, 8 * GB)
        self.assertEqual(Settings().mem_default_mb, 4 * GB)
        self.assertEqual(Settings(mem_reserve_mb=1024).mem_reserve_mb, 1024)


class MemCase(StoreCase):
    """self.mem is the machine's memory as the store reads it; self.reads counts the reads."""

    def setUp(self):
        super().setUp()
        self.confine(3)
        self.mem = {"total_mb": 64 * GB, "free_mb": 8 * GB, "commit_mb": 60000,
                    "commit_limit_mb": 83400}
        self.reads = 0
        self.store.memory = self.read_mem
        self.said = []
        self.store.say = self.said.append

    def read_mem(self):
        self.reads += 1
        return dict(self.mem)

    def book(self, slug, **kw):
        kw.setdefault("store", self.store)
        kw.setdefault("env", clean_env())
        return rm.book("engine-test", "kit", slug, cores=1, **kw)

    def busy(self, slug, **kw):
        with self.assertRaises(LockError) as cm:
            self.book(slug, no_wait=True, **kw)
        return cm.exception

    def entries(self):
        return (self.store.read("queue.json") or {}).get("entries", [])

    def holder(self):
        s = self.book("holder", no_wait=True)
        self.addCleanup(s.release)
        return s

    def in_thread(self, fn):
        out = []

        def go():
            try:
                out.append(fn())
            except BaseException as e:      # noqa: BLE001 - handed to the test thread
                out.append(e)
        t = threading.Thread(target=go, daemon=True)
        t.start()
        return t, out

    def until(self, what, timeout=30):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            got = what()
            if got:
                return got
            time.sleep(0.05)
        self.fail("timed out waiting; said: %s" % self.said)

    def waiting_entry(self, slug):
        return self.until(lambda: next((e for e in self.entries() if e.get("slug") == slug
                                        and e.get("mem_wait")), None))


class Admission(MemCase):
    def test_a_run_whose_need_does_not_fit_waits_in_its_place_and_status_says_why(self):
        self.holder()
        self.mem["commit_mb"] = 72000                       # headroom 3208 MB
        err = self.busy("big", mem_mb=6000)
        self.assertEqual(err.code, EXIT_BUSY)
        self.assertIn("memory: need 6000 MB, headroom 3208 MB", err.message)
        self.assertEqual([e["slug"] for e in self.entries()], ["holder"])
        t, out = self.in_thread(lambda: self.book("big", mem_mb=6000, wait_s=60))
        e = self.waiting_entry("big")
        self.assertEqual((e["state"], e["mem_wait"]), ("waiting", 6000))
        snap = api.snapshot(self.store)
        [w] = snap["queue"]["entries"]
        self.assertEqual(w["position"], 1)
        self.assertTrue(w["why"].startswith("memory: need 6000 MB, headroom 3208 MB"), w["why"])
        self.assertTrue(any(x.startswith("waiting: kit/big") and "memory: need 6000 MB, "
                            "headroom 3208 MB" in x for x in st.render(snap)), st.render(snap))
        [row] = [r for r in snap["runs"] if r["id"] == e["id"]]
        self.assertEqual(row["mem_wait"], {"need_mb": 6000, "headroom_mb": 3208})
        self.assertTrue(any("memory: need 6000 MB, headroom 3208 MB" in x for x in self.said),
                        self.said)
        ticket = e["ticket"]
        self.mem["commit_mb"] = 60000                       # headroom 15208 MB: it fits
        t.join(30)
        [s] = out
        self.assertNotIsInstance(s, BaseException)
        self.addCleanup(s.release)
        [mine] = [x for x in self.entries() if x["id"] == s.id]
        self.assertEqual((mine["state"], mine["ticket"]), ("held", ticket))
        self.assertNotIn("mem_wait", mine)

    def test_a_memory_wait_is_kept_as_its_cause(self):
        """Its record, then its history line, say how long it waited on memory (wait_by)."""
        self.holder()
        self.mem["commit_mb"] = 72000                       # headroom 3208 MB
        t, out = self.in_thread(lambda: self.book("big", mem_mb=6000, wait_s=60))
        self.waiting_entry("big")
        time.sleep(0.3)
        self.mem["commit_mb"] = 60000                       # it fits
        t.join(30)
        [s] = out
        self.assertNotIsInstance(s, BaseException)
        rec = self.store.read("active/%s.json" % s.id)
        s.release()
        self.assertEqual(set(rec.get("wait_by") or {}), {"mem"}, rec)
        self.assertGreater(rec["wait_by"]["mem"], 0)
        self.assertEqual(rec.get("wait_on"), {})
        [line] = [x for x in self.store.history_records() if x["id"] == s.id]
        self.assertEqual((line.get("wait_by"), line.get("wait_on")), (rec["wait_by"], {}))

    def test_a_run_with_nothing_else_held_starts_whatever_the_memory(self):
        self.mem["commit_mb"] = 90000                       # past the limit itself
        s = self.book("alone", mem_mb=60000, no_wait=True)
        s.release()
        self.assertEqual(self.reads, 0)                     # exempt: memory not even read

    def test_the_limit_is_read_at_each_admission(self):
        """The commit limit drops between two admissions (Windows shrinks it when it lets a page
        file extension go): the second run is admitted against the new limit, read anew."""
        self.holder()
        first = self.book("first", mem_mb=8000, no_wait=True)   # 83400 - 8192 - 60000 = 15208
        self.addCleanup(first.release)
        n = self.reads
        self.assertGreaterEqual(n, 1)
        self.mem["commit_limit_mb"] = 74400                     # 74400 - 8192 - 60000 = 6208
        err = self.busy("second", mem_mb=8000)
        self.assertGreater(self.reads, n)
        self.assertIn("memory: need 8000 MB, headroom 6208 MB", err.message)
        self.mem["commit_limit_mb"] = 83400
        self.mem["commit_mb"] = 52000                           # the commit is read anew too
        self.book("second", mem_mb=8000, no_wait=True).release()

    def test_the_reserve_is_the_setting(self):
        self.store.settings = self.make_store(mem_reserve_mb=1024).settings
        self.holder()
        self.mem["commit_mb"] = 78000                           # 83400 - 78000 = 5400
        self.book("fits", mem_mb=4000, no_wait=True).release()  # 4000 <= 5400 - 1024
        self.store.settings = self.make_store().settings        # the 8192 MB default
        self.assertIn("headroom -2792 MB", self.busy("fits", mem_mb=4000).message)

    def test_a_kind_with_no_floor_and_no_history_needs_the_default(self):
        self.holder()
        self.mem["commit_mb"] = 83400 - 8192 - 4095             # headroom 4095 MB
        err = self.busy("unknown")
        self.assertIn("memory: need 4096 MB, headroom 4095 MB", err.message)
        self.mem["commit_mb"] -= 1
        s = self.book("unknown", no_wait=True)
        rec = self.store.read("active/%s.json" % s.id)
        s.release()
        self.assertEqual((rec["need_mem_mb"], rec["mem_from"]), (4096, "default"))

    def test_not_measured_admits_on_slots_alone(self):
        self.holder()
        self.mem = {"total_mb": 64 * GB, "free_mb": 1}          # no commit figures
        self.book("blind", mem_mb=60000, no_wait=True).release()

    def test_a_reader_that_raises_is_not_measured_and_admits_on_slots(self):
        self.holder()
        calls = []

        def broken():
            calls.append(1)
            raise OSError("GlobalMemoryStatusEx failed")
        self.store.memory = broken
        s = self.book("blind", mem_mb=60000, no_wait=True)
        s.release()
        self.assertTrue(calls)                                  # acquire did read, and fail
        self.assertIsNone(memory_now(self.store))

    def test_a_nested_run_joining_the_queue_starts_while_memory_is_short(self):
        anc = process.own_ancestors()
        pp, ct = os.getppid(), process.proc_ctime(os.getppid())
        if ct is None or (pp, ct) not in anc:
            self.skipTest("this process's parent cannot be read as an ancestor")
        self.holder()
        self.mem["commit_mb"] = 80000                           # headroom -4792 MB
        res = "landing:q:\\repo\\.git"
        self.assertIn("memory: need 4096 MB", self.busy("plain", resource=res).message)
        prid = "r-20261006T120000Z-abcdef"
        self.live_record(prid, pid=pp, ctime=ct, has={"cpu": 2}, cores=[0, 1])
        env = clean_env(**{run_held.NEST_ENV: "%s:%d:%d" % (prid, pp, ct)})
        s = self.book("child", env=env, resource=res, no_wait=True)     # its parent lacks res
        try:
            self.assertFalse(s.nested)                          # it went through acquire
            [e] = [x for x in self.entries() if x["id"] == s.id]
            self.assertEqual((e["state"], e["parent"]), ("held", prid))
            self.assertNotIn("mem_wait", e)
        finally:
            s.release()


class Landing(MemCase):
    def test_a_landing_waits_for_memory_and_keeps_its_place(self):
        self.holder()
        self.mem["commit_mb"] = 72000                           # headroom 3208 MB
        t, out = self.in_thread(lambda: self.book("land", mem_mb=6000, wait_s=60,
                                                  priority="land", gate=True))
        e = self.waiting_entry("land")
        self.assertEqual((e["priority"], e["mem_wait"]), ("land", 6000))
        later = self.in_thread(lambda: self.book("later", mem_mb=6000, wait_s=60))
        self.waiting_entry("later")
        rows = {w["slug"]: w for w in api.snapshot(self.store)["queue"]["entries"]}
        self.assertEqual((rows["land"]["position"], rows["later"]["position"]), (1, 2))
        self.assertEqual(rows["land"]["ticket"], e["ticket"])
        self.assertIn("memory: need 6000 MB", rows["land"]["why"])
        self.mem["commit_mb"] = 60000 - 6000                    # room for both
        for th, o in ((t, out), later):
            th.join(30)
            [s] = o
            self.assertNotIsInstance(s, BaseException)
            self.addCleanup(s.release)
        self.assertEqual(self.store.read("active/%s.json" % out[0].id)["priority"], "land")


class Background(MemCase):
    def test_the_child_starts_only_when_its_need_fits(self):
        self.holder()
        self.mem["commit_mb"] = 72000                           # headroom 3208 MB
        t, out = self.in_thread(lambda: bg.run_background(
            [sys.executable, "-c", "import sys; sys.exit(0)"], "kit", "bgm", store=self.store,
            env=clean_env(), mem_mb=6000))
        e = self.waiting_entry("bgm")
        self.assertEqual((e["state"], e["cores"], e["gpus"]), ("bg", [], []))
        lines = st.render(api.snapshot(self.store))
        self.assertTrue(any(x.startswith("background: kit/bgm") and "memory: need 6000 MB, "
                            "headroom 3208 MB" in x for x in lines), lines)
        self.assertNotIn("child started", " ".join(self.said))
        self.mem["commit_mb"] = 60000
        t.join(60)
        self.assertEqual(out, [0])
        self.assertIn("child started", " ".join(self.said))


if __name__ == "__main__":
    unittest.main()
