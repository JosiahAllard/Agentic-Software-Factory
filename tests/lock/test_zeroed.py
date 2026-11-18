"""The lock store after a crash's zero-fill: a file renamed into place whose data never reached
the disk reads back at its full length as NUL bytes only. Its writer is dead, so a zeroed
queue.json is an empty queue and a zeroed active/ record a gone run, each set aside in
`_corrupt/` (kitlib.fs.quarantine) and named by status; any other unreadable file keeps its old
handling. Every writer fsyncs its temp file before the rename, so the fill never happens."""
import json
import os
import unittest
from unittest import mock

from .helpers import StoreCase, clean_env
from tests.platforms import windows_only

if os.name == "nt":
    from kitlib.lock import api
    from kitlib.lock import queue as qm
    from kitlib.lock import run as rm
    from kitlib.lock import status as st
    from kitlib.lock import store as store_mod
    from kitlib.lock.store import LockError

R_Z = "r-20261006T120000Z-0f0f0f"


def zero_fill(path, size=300):
    """What a power loss leaves of a renamed file whose data was never written out."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"\0" * size)


class ZeroedCase(StoreCase):
    def corrupt(self, rel):
        return os.path.join(self.store.path(), "_corrupt", *rel.split("/"))

    def zero_queue(self):
        zero_fill(self.store.path("queue.json"))

    def load(self):
        """qm.load, a QueueUnreadable a failure (not an error) of the test."""
        try:
            return qm.load(self.store)
        except qm.QueueUnreadable as e:
            self.fail("a zeroed queue.json is unreadable: %s" % e)


class ZeroedQueue(ZeroedCase):
    def test_a_zeroed_queue_reads_as_empty_at_once(self):
        self.zero_queue()
        with mock.patch("time.sleep") as slept:
            q = self.load()
        self.assertEqual(q["entries"], [])
        self.assertEqual(slept.call_count, 0)
        with open(self.store.path("queue.json"), "rb") as f:
            self.assertEqual(f.read(), b"\0" * 300)          # load moves nothing

    def test_save_sets_it_aside_and_writes_the_queue(self):
        self.zero_queue()
        q = self.load()
        qm.save(self.store, q)
        with open(self.corrupt("queue.json"), "rb") as f:
            self.assertEqual(f.read(), b"\0" * 300)
        self.assertEqual(self.store.read("queue.json")["entries"], [])

    def test_save_still_writes_the_queue_when_the_move_fails(self):
        self.zero_queue()
        with mock.patch("kitlib.fs.replace.place_new", side_effect=PermissionError("denied")):
            qm.save(self.store, {"ticket": 4, "entries": []})
        self.assertEqual(self.store.read("queue.json"), {"ticket": 4, "entries": []})
        self.assertFalse(os.path.exists(self.corrupt("queue.json")))

    def test_another_unparseable_queue_is_still_unreadable(self):
        os.makedirs(self.store.path(), exist_ok=True)
        with open(self.store.path("queue.json"), "wb") as f:
            f.write(b"\0\0{")
        with self.assertRaises(qm.QueueUnreadable):
            qm.load(self.store)
        self.assertFalse(os.path.exists(self.corrupt("queue.json")))


class ZeroedRun(ZeroedCase):
    def test_a_run_books_over_a_zeroed_queue_and_a_zeroed_record(self):
        self.confine(1)
        self.zero_queue()
        zero_fill(self.store.path("active", R_Z + ".json"))
        try:
            with rm.slot("engine-test", "kit", "t", cores=1, store=self.store, wait_s=30,
                         env=clean_env()) as launch:
                self.assertEqual(len(launch.cores), 1)
        except LockError as e:
            self.fail("a zeroed store refused the run: %s" % e)
        self.assertTrue(os.path.isfile(self.corrupt("queue.json")))
        self.assertTrue(os.path.isfile(self.corrupt("active/%s.json" % R_Z)))
        self.assertFalse(os.path.exists(self.store.path("active", R_Z + ".json")))
        self.assertEqual(self.store.read("queue.json")["entries"], [])

    def test_another_unreadable_record_stays_where_it_is(self):
        p = self.store.path("active", R_Z + ".json")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write("{not json")
        self.store.live_runs(prune=True)
        self.assertTrue(os.path.isfile(p))
        self.assertEqual(self.store.corrupt(), [])


class ZeroedStatus(ZeroedCase):
    def test_snapshot_and_status_name_the_damaged_files(self):
        self.zero_queue()
        s = api.snapshot(self.store)
        self.assertEqual(s["queue"], {"entries": [], "error": None})
        self.assertEqual(s.get("damaged"), [qm.ZEROED_TEXT])
        qm.save(self.store, self.load())
        zero_fill(self.store.path("active", R_Z + ".json"))
        self.store.live_runs(prune=True)
        s = api.snapshot(self.store)
        self.assertEqual(s.get("damaged"), ["active/%s.json" % R_Z, "queue.json"])
        lines = [x for x in st.render(s) if x.startswith("damaged")]
        self.assertEqual(lines, [
            "damaged, set aside in %s (remove once read): active/%s.json, queue.json%s"
            % (os.path.join(self.store.root, "_corrupt"), R_Z, st.QUEUE_LOST)])

    def test_a_clean_store_shows_nothing_damaged(self):
        s = api.snapshot(self.store)
        self.assertEqual(s.get("damaged"), [])
        self.assertFalse(any(x.startswith("damaged") for x in st.render(s)))


@windows_only("the lock is Windows only")
class WritersFsync(unittest.TestCase):
    """write_json, create_json and Store.bump fsync the temp file before it is renamed."""

    def setUp(self):
        import shutil
        import tempfile
        self.tmp = tempfile.mkdtemp(prefix="lock-fsync-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.events = []
        real_fsync, real_replace, real_rename = os.fsync, os.replace, os.rename

        def fsync(fd):
            self.events.append("fsync")
            return real_fsync(fd)

        def replace(a, b, **kw):
            self.events.append("rename")
            return real_replace(a, b, **kw)

        def rename(a, b, **kw):
            self.events.append("rename")
            return real_rename(a, b, **kw)
        for name, fn in (("fsync", fsync), ("replace", replace), ("rename", rename)):
            p = mock.patch("os." + name, fn)
            p.start()
            self.addCleanup(p.stop)

    def check(self):
        self.assertIn("rename", self.events)
        self.assertIn("fsync", self.events[:self.events.index("rename")], self.events)

    def test_write_json(self):
        path = os.path.join(self.tmp, "a", "q.json")
        store_mod.write_json(path, {"x": [1, 2]})
        self.check()
        with open(path, encoding="utf-8") as f:
            self.assertEqual(f.read(), json.dumps({"x": [1, 2]}, indent=1))

    def test_create_json(self):
        self.assertTrue(store_mod.create_json(os.path.join(self.tmp, "c.json"), {"y": 1}))
        self.check()

    def test_bump(self):
        s = store_mod.Store(root=os.path.join(self.tmp, "store"))
        s.bump()
        self.check()
        self.assertEqual(s.revision(), 1)


if __name__ == "__main__":
    unittest.main()
