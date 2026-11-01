"""A run's memory need: Store.history_mem is the p90 of the peak commit of
the finished runs of its own repo, kind and mode, once there are mem_min_runs (5) of them since
the kind's mem_since; until then the kinds table's optional mem_mb floor stands in; --mem (book's
mem_mb) overrides both. The run record keeps need_mem_mb and mem_from (flag, history, kind or
none). Nothing here changes admission."""
import datetime
import json
import unittest
from unittest import mock

from tests.lock.helpers import StoreCase, clean_env
from tests.platforms import windows_only

try:
    from kitlib.lock import api
    from kitlib.lock import kinds
    from kitlib.lock import run as rm
    from kitlib.lock import status as st
    from kitlib.lock.store import iso, utc_now
except ImportError:                 # not Windows: StoreCase skips
    pass

LAND = {"priority": "land", "gate": True}
BRANCH = {"gate": True, "branch": "kit/x"}


class HistoryCase(StoreCase):
    n = 0

    def add(self, peak, how="done", repo="kit", kind="engine-test", **kw):
        HistoryCase.n += 1
        end = utc_now() - datetime.timedelta(minutes=10)
        d = {"id": "r-20261001T0001%02dZ-%04x" % (self.n % 60, self.n), "repo": repo,
             "slug": "g", "kind": kind, "since": iso(end - datetime.timedelta(seconds=65)),
             "held_since": iso(end - datetime.timedelta(seconds=60)), "ended": iso(end),
             "code": 0, "how": how, "cores": [0], "gpus": [], "peak_commit_mb": peak}
        d.update(kw)
        self.store.history_append(d)


class HistoryMem(HistoryCase):
    def test_the_need_is_the_p90_of_the_peak_commit(self):
        for peak in range(100, 1001, 100):          # ten runs: the ninth by size is the p90
            self.add(peak)
        self.assertEqual(self.store.history_mem("kit", "engine-test"), 900)
        self.add(5000)                              # eleven: rank ceil(9.9) = 10th, 1000
        self.assertEqual(self.store.history_mem("kit", "engine-test"), 1000)

    def test_each_mode_has_its_own_need(self):
        for _ in range(5):
            self.add(700, **LAND)
            self.add(720, **BRANCH)
            self.add(400)
        m = self.store.history_mem
        self.assertEqual(m("kit", "engine-test", "land"), 700)
        self.assertEqual(m("kit", "engine-test", "branch"), 720)
        self.assertEqual(m("kit", "engine-test", "other"), 400)

    def test_a_mode_with_fewer_than_five_runs_has_none(self):
        for _ in range(4):
            self.add(700)
        for _ in range(5):
            self.add(700, **LAND)
        self.assertIsNone(self.store.history_mem("kit", "engine-test"))
        self.add(700)
        self.assertEqual(self.store.history_mem("kit", "engine-test"), 700)

    def test_runs_that_ended_before_the_history_start_stay_out(self):
        old = utc_now() - datetime.timedelta(days=3)
        for _ in range(5):
            self.add(205, ended=iso(old))
        start = (utc_now() - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        self.assertEqual(self.store.history_mem("kit", "engine-test"), 205)
        self.assertIsNone(self.store.history_mem("kit", "engine-test", since=start))
        for _ in range(5):
            self.add(9000)
        self.assertEqual(self.store.history_mem("kit", "engine-test", since=start), 9000)

    def test_other_runs_and_lines_with_no_peak_stay_out(self):
        for _ in range(5):
            self.add(300)
        self.add(90000, how="stopped")
        self.add(None)
        self.add("big")
        self.add(-1)
        for _ in range(5):
            self.add(90000, repo="x")
            self.add(90000, kind="measure")
        self.assertEqual(self.store.history_mem("kit", "engine-test"), 300)


class KindsFloor(unittest.TestCase):
    def test_mem_since_is_an_optional_date(self):
        self.assertEqual(kinds.check_entry("b", {"cpu": 2, "mem_since": "2026-11-15"})["mem_since"],
                         "2026-11-15")
        for bad in ("2026-13-01", "15/11/2026", "2026-11-15T00:00:00Z", 20261115, None):
            with self.subTest(bad=bad):
                with self.assertRaises(kinds.KindsError) as e:
                    kinds.check_entry("b", {"cpu": 2, "mem_since": bad})
                self.assertIn("mem_since", str(e.exception))

    def test_mem_mb_is_an_optional_whole_number_of_mb(self):
        self.assertEqual(kinds.check_entry("b", {"cpu": 2, "mem_mb": 24576}),
                         {"cpu": 2, "gpu": 0, "share": False, "warn_min": 60, "mem_mb": 24576})
        self.assertNotIn("mem_mb", kinds.check_entry("b", {"cpu": 2}))
        for bad in (0, -5, 1.5, True, "24g", None):
            with self.subTest(bad=bad):
                with self.assertRaises(kinds.KindsError) as e:
                    kinds.check_entry("b", {"cpu": 2, "mem_mb": bad})
                self.assertIn("mem_mb", str(e.exception))


@windows_only("the lock runs on Windows")
class BookNeed(HistoryCase):
    """book records need_mem_mb and mem_from; status shows them."""

    def floor(self, mb, **more):
        with open(self.kinds_path, encoding="utf-8") as f:
            doc = json.load(f)
        doc["kinds"]["engine-test"]["mem_mb"] = mb
        doc["kinds"]["engine-test"].update(more)
        with open(self.kinds_path, "w", encoding="utf-8") as f:
            json.dump(doc, f)

    def booked(self, **kw):
        with rm.book("engine-test", "kit", "t", cores=1, no_wait=True, store=self.store,
                     env=clean_env(), **kw) as slot:
            rec = self.store.read("active/%s.json" % slot.id)
            snap = api.snapshot(self.store)
        return rec, snap

    def need(self, **kw):
        rec = self.booked(**kw)[0]
        return rec["need_mem_mb"], rec["mem_from"]

    def test_none_with_no_flag_no_history_and_no_floor(self):
        self.confine(1)
        self.assertEqual(self.need(), (None, "none"))

    def test_the_floor_until_five_runs_then_history_replaces_it(self):
        self.confine(1)
        self.floor(24576)
        for _ in range(4):
            self.add(2000)
        self.assertEqual(self.need(), (24576, "kind"))
        self.add(2000)                                  # the fifth: history, under the floor
        self.assertEqual(self.need(), (2000, "history"))
        self.assertEqual(self.need(mem_mb=512), (512, "flag"))

    def test_history_far_under_the_floor_adds_no_gap_note(self):
        self.confine(1)
        for _ in range(5):
            self.add(2000)
        self.floor(24576)
        rec, snap = self.booked()
        self.assertEqual((rec["need_mem_mb"], rec["mem_from"]), (2000, "history"))
        self.assertNotIn("mem_gap", rec)
        [row] = [r for r in snap["runs"] if r["id"] == rec["id"]]
        self.assertNotIn("mem_gap", row)
        self.assertFalse(any("work outside the job" in x for x in st.render(snap)))

    def test_the_need_is_kept_in_the_history_line(self):
        self.confine(1)
        for _ in range(5):
            self.add(2000)
        self.floor(24576)
        rec = self.booked()[0]
        [h] = [d for d in self.store.history_records() if d["id"] == rec["id"]]
        self.assertEqual((h["need_mem_mb"], h["mem_from"]), (2000, "history"))
        self.assertNotIn("mem_gap", h)

    def test_mem_reads_no_history(self):
        self.confine(1)
        with mock.patch.object(type(self.store), "history_mem",
                               side_effect=AssertionError("history read")):
            self.assertEqual(self.need(mem_mb=512), (512, "flag"))

    def test_history_without_a_floor_needs_five_runs_too(self):
        self.confine(1)
        for _ in range(4):
            self.add(2000)
        self.assertEqual(self.need(), (None, "none"))
        self.add(2000)
        self.assertEqual(self.need(), (2000, "history"))

    def test_history_before_the_kinds_start_does_not_replace_the_floor(self):
        self.confine(1)
        for _ in range(6):
            self.add(205)
        self.floor(26368, mem_since="9999-12-31")       # a start not yet reached
        self.assertEqual(self.need(), (26368, "kind"))
        self.floor(26368, mem_since="2000-01-01")
        self.assertEqual(self.need(), (205, "history"))

    def test_the_floor_alone(self):
        self.confine(1)
        self.floor(4096)
        self.assertEqual(self.need(), (4096, "kind"))

    def test_history_is_read_in_the_runs_own_mode(self):
        self.confine(1)
        for _ in range(5):
            self.add(3000, **LAND)
        self.assertEqual(self.need(priority="land", gate=True), (3000, "history"))
        self.assertEqual(self.need(), (None, "none"))

    def test_a_bad_mem_is_usage(self):
        for bad in (0, -1, 1.5, True, "4096"):
            with self.subTest(bad=bad):
                with self.assertRaises(rm.LockError) as e:
                    self.booked(mem_mb=bad)
                self.assertEqual(e.exception.code, rm.EXIT_USAGE)
                self.assertIn("--mem", str(e.exception))

    def test_status_shows_the_need_and_where_it_came_from(self):
        self.confine(1)
        rec, snap = self.booked(mem_mb=4096)
        [row] = [r for r in snap["runs"] if r["id"] == rec["id"]]
        self.assertEqual((row["need_mem_mb"], row["mem_from"]), (4096, "flag"))
        self.assertTrue(any("needs 4096 MB of memory (--mem)" in x for x in st.render(snap)),
                        st.render(snap))
        rec, snap = self.booked()
        self.assertFalse(any("MB of memory" in x for x in st.render(snap)))


if __name__ == "__main__":
    unittest.main()
