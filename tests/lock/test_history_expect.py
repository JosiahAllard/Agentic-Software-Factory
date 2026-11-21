"""kitlib.lock.store.Store.history_expect: a run's expected time is the median of the finished
runs of its own repo, kind and mode (land, branch gate, branchless merged or full gate, other),
not of all of them; a mode with too few runs has none (book then uses the kind's guide)."""
import datetime
import os
import unittest

from tests.lock.helpers import StoreCase, clean_env
from tests.platforms import windows_only

try:
    from kitlib.lock import run as rm
    from kitlib.lock.store import iso, utc_now
except ImportError:                 # not Windows: StoreCase skips
    pass

LAND = {"priority": "land", "gate": True}
BRANCH = {"gate": True, "branch": "kit/x"}


class HistoryExpect(StoreCase):
    n = 0

    def add(self, held_s, how="done", repo="kit", kind="engine-test", **kw):
        HistoryExpect.n += 1
        end = utc_now() - datetime.timedelta(minutes=10)
        d = {"id": "r-20261001T0000%02dZ-%04x" % (self.n % 60, self.n), "repo": repo,
             "slug": "g", "kind": kind, "since": iso(end - datetime.timedelta(seconds=held_s + 5)),
             "held_since": iso(end - datetime.timedelta(seconds=held_s)), "ended": iso(end),
             "code": 0, "how": how, "cores": [0], "gpus": []}
        d.update(kw)
        self.store.history_append(d)

    def many(self, held, n=3, **kw):
        for _ in range(n):
            self.add(held, **kw)

    def test_each_mode_has_its_own_median(self):
        self.many(700, **LAND)
        self.many(720, **BRANCH)
        self.many(400)
        e = self.store.history_expect
        self.assertEqual(e("kit", "engine-test", "land"), 700)
        self.assertEqual(e("kit", "engine-test", "branch"), 720)
        self.assertEqual(e("kit", "engine-test", "other"), 400)

    def test_lines_under_a_kinds_retired_name_count_as_that_kind(self):
        """A history line written before a kind's rename (its old name now an alias in the kinds
        table) counts toward the kind's median under either name. Red before: the lines split by
        the name as written, and three runs under each name were each one short."""
        import json
        with open(self.kinds_path, encoding="utf-8") as f:
            doc = json.load(f)
        doc["aliases"] = {"old-test": {"to": "engine-test", "why": "renamed"}}
        with open(self.kinds_path, "w", encoding="utf-8") as f:
            json.dump(doc, f)
        self.store = self.make_store(history_min_runs=4)
        self.many(400, n=2, kind="old-test")
        self.many(600, n=2)
        self.assertEqual(self.store.history_expect("kit", "engine-test"), 500)
        self.assertEqual(self.store.history_expect("kit", "old-test"), 500)

    def test_a_thin_mode_has_none_whatever_the_others_hold(self):
        self.many(700, **LAND)
        self.many(400, n=20)
        self.assertIsNone(self.store.history_expect("kit", "engine-test", "branch"))

    def test_other_runs_stay_out_of_a_modes_median(self):
        self.many(900, **BRANCH)
        self.add(100, how="stopped", **BRANCH)
        self.add(-5, **BRANCH)
        self.many(100, n=5, repo="x")
        self.many(100, n=5, kind="measure")
        self.assertEqual(self.store.history_expect("kit", "engine-test", "branch"), 900)

    def test_a_line_without_branch_or_priority_is_other(self):
        self.many(300, gate=True)       # a gate line written before the branch key
        self.many(300, **{"gate": False})
        e = self.store.history_expect
        self.assertEqual(e("kit", "engine-test"), 300)
        self.assertIsNone(e("kit", "engine-test", "branch"))

    def test_a_promotion_has_its_own_median(self):
        """A staging promotion's gate (no branch, no land priority, gate_mode merged or full)
        keeps its time and memory apart from the short test runs of its kind, and its lines
        leave every other mode's answer as it was. Red before: its lines were "other", so
        "merged" had none and the 1 s runs' median gave the promotion a 60 s guide."""
        promote = [(5800, "full"), (5900, "merged"), (6000, "full"), (6100, "full"),
                   (6200, "merged")]
        others = dict(peak_commit_mb=900)

        def fill(with_promotions):
            if with_promotions:
                for held, mode in promote:
                    self.add(held, gate=True, gate_mode=mode, peak_commit_mb=6400)
            self.many(1, n=20, **others)                       # short test runs
            self.many(0, n=3, gate=True, gate_mode="bisect", **others)
            self.many(300, gate=True, **others)                # a gate line with no gate_mode
            self.many(300, gate=True, gate_mode="daily", **others)
            self.many(700, n=5, gate_mode="merged", peak_commit_mb=2000, **LAND)
            self.many(720, n=5, gate_mode="full", peak_commit_mb=1500, **BRANCH)

        def answers():
            return {m: (self.store.history_expect("kit", "engine-test", m),
                        self.store.history_mem("kit", "engine-test", m))
                    for m in ("land", "branch", "other")}
        fill(False)
        without = answers()
        self.root = os.path.join(self.tmp, "store-with-promotions")    # a fresh history
        self.store = self.make_store()
        fill(True)
        self.assertEqual(self.store.history_expect("kit", "engine-test", "merged"), 6000)
        self.assertEqual(self.store.history_mem("kit", "engine-test", "merged"), 6400)
        self.assertEqual(answers(), without)
        self.assertEqual(without, {"land": (700, 2000), "branch": (720, 1500), "other": (60, 900)})

    def test_run_mode(self):
        m = self.store.run_mode
        self.assertEqual(m("land", None, True), "land")
        self.assertEqual(m(None, "kit/x", True), "branch")
        self.assertEqual(m(None, "kit/x", False), "other")
        self.assertEqual(m(None, None, True), "other")
        self.assertEqual(m("land", None, True, gate_mode="merged"), "land")
        self.assertEqual(m(None, "kit/x", True, gate_mode="full"), "branch")
        self.assertEqual(m(None, None, True, gate_mode="full"), "merged")
        self.assertEqual(m(None, None, True, gate_mode="merged"), "merged")
        for mode in ("daily", "bisect", "rerun", None):
            self.assertEqual(m(None, None, True, gate_mode=mode), "other", mode)
        self.assertEqual(m(None, None, False, gate_mode="merged"), "other")

    @windows_only("the lock runs on Windows")
    def test_book_takes_the_estimate_of_its_own_mode(self):
        self.confine(1)
        self.many(700, **LAND)
        self.many(400)
        self.many(6000, gate=True, gate_mode="full")        # a staging promotion's gate
        for kw, want in (({"priority": "land", "gate": True}, 700), ({"gate": True, "branch": "b"}, None),
                         ({}, 400), ({"gate": True, "gate_mode": "merged"}, 6000)):
            with rm.book("engine-test", "kit", "t", cores=1, no_wait=True, store=self.store,
                         env=clean_env(), **kw) as slot:
                rec = self.store.read("active/%s.json" % slot.id)
            if want is None:
                self.assertEqual(rec["expect_from"], "guide")
            else:
                self.assertEqual((rec["expect_s"], rec["expect_from"]), (want, "history"))

    @windows_only("the lock runs on Windows")
    def test_a_quick_test_with_no_history_expects_its_short_guide(self):
        # The kit's own table: a quick-test run (only the touched tests) with no --expect and no
        # history expects 300 s on one core, not a build's guide, so it can backfill a gap.
        import json
        from kitlib.lock import kinds as lockkinds
        with open(lockkinds.KINDS_FILE, encoding="utf-8") as f:
            real = json.load(f)
        self.assertIn("quick-test", real["kinds"])
        with open(self.kinds_path, "w", encoding="utf-8") as f:
            json.dump(real, f)
        self.store = self.make_store()
        self.many(3000, kind="build")
        with rm.book("quick-test", "kit", "t", no_wait=True, store=self.store,
                     env=clean_env()) as slot:
            rec = self.store.read("active/%s.json" % slot.id)
        self.assertEqual((rec["expect_s"], rec["expect_from"], rec["need"]["cpu"]),
                         (300, "guide", 1))


if __name__ == "__main__":
    unittest.main()
