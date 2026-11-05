"""Context.history is memoized per snapshot: the readers of `next` (offer.closed_reader,
ended_reader) asking the same rid run one store git log (docs/backlog.md, `next`)."""
from unittest import mock

from kitlib.backlog import offer as omod

from tests.backlog.helpers import StoreCase, priced


class HistoryMemo(StoreCase):
    remote = False

    def test_two_readers_asking_one_rid_run_one_git_log(self):
        self.seed(priced('GONE1', state='open'))
        snap = self.store.load()
        with mock.patch.object(self.store, 'history', return_value='abc1234 closes GONE1') as hist:
            closed = omod.closed_reader(snap, self.ctx.history(snap))
            ended = omod.ended_reader(snap, self.ctx.history(snap))
            self.assertEqual(closed('NOPE'), 'abc1234')
            self.assertEqual(ended('NOPE'), 'landed (closed in abc1234)')
            self.assertEqual(hist.call_count, 1)
            closed('OTHER')
            self.assertEqual(hist.call_count, 2)

    def test_a_new_snapshot_reads_afresh(self):
        self.seed(priced('A', state='open'))
        first = self.store.load()
        with mock.patch.object(self.store, 'history', return_value=None) as hist:
            self.ctx.history(first)('X')
            self.seed(priced('B', state='open'))
            second = self.store.load()
            self.ctx.history(second)('X')
            self.assertEqual(hist.call_count, 2)
