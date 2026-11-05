"""A booking reads the lock's history (Store.history_records: every month file of the last
history_keep_days, json-parsed) at most once, while its run id's mutex is held: book reads it
once and hands the lines to both history_expect and history_mem, and not at all when --expect
and --mem leave nothing to guess. Red before: each of the two read it itself, twice a booking."""
import datetime
import unittest
from unittest import mock

from tests.lock.helpers import StoreCase, clean_env
from tests.platforms import windows_only

try:
    from kitlib.lock import run as rm
    from kitlib.lock.store import iso, utc_now
except ImportError:                 # not Windows: StoreCase skips
    pass


@windows_only("the lock runs on Windows")
class HistoryReadOnce(StoreCase):
    n = 0

    def add(self, held_s=400, peak=2000):
        HistoryReadOnce.n += 1
        end = utc_now() - datetime.timedelta(minutes=10)
        self.store.history_append({
            "id": "r-20261001T0002%02dZ-%04x" % (self.n % 60, self.n), "repo": "kit",
            "slug": "g", "kind": "engine-test",
            "since": iso(end - datetime.timedelta(seconds=held_s + 5)),
            "held_since": iso(end - datetime.timedelta(seconds=held_s)), "ended": iso(end),
            "code": 0, "how": "done", "cores": [0], "gpus": [], "peak_commit_mb": peak})

    def booked(self, **kw):
        """(the run record, how many times history was read while booking and releasing)."""
        with mock.patch.object(self.store, "history_records",
                               wraps=self.store.history_records) as seen:
            with rm.book("engine-test", "kit", "t", cores=1, no_wait=True, store=self.store,
                         env=clean_env(), **kw) as slot:
                rec = self.store.read("active/%s.json" % slot.id)
        return rec, seen.call_count

    def setUp(self):
        super().setUp()
        self.confine(1)
        for _ in range(5):          # enough for both history_min_runs and mem_min_runs
            self.add()

    def test_both_guesses_come_from_one_read(self):
        rec, reads = self.booked()
        self.assertEqual((rec["expect_s"], rec["expect_from"]), (400, "history"))
        self.assertEqual((rec["need_mem_mb"], rec["mem_from"]), (2000, "history"))
        self.assertEqual(reads, 1)

    def test_one_read_when_only_one_guess_is_needed(self):
        rec, reads = self.booked(expect_s=120)
        self.assertEqual((rec["expect_from"], rec["mem_from"]), ("--expect", "history"))
        self.assertEqual(reads, 1)
        rec, reads = self.booked(mem_mb=512)
        self.assertEqual((rec["expect_from"], rec["mem_from"]), ("history", "flag"))
        self.assertEqual(reads, 1)

    def test_no_read_when_nothing_is_guessed(self):
        rec, reads = self.booked(expect_s=120, mem_mb=512)
        self.assertEqual((rec["expect_from"], rec["mem_from"]), ("--expect", "flag"))
        self.assertEqual(reads, 0)


if __name__ == "__main__":
    unittest.main()
