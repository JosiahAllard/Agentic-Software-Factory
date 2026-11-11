"""kitlib.landing.checks's restore clause for a narrowed record (Lander._scope_restore; docs/gate-landing.md
"Landing", step 4). A record narrowed against several live pulls (one `narrowed:` line each) gets
no restore command while one of them still holds its part: given back then, its original member
would cover that pull's live paths again. `next`'s RESTORE line gives each part back once the
records it was narrowed for have ended (kitlib/backlog/offer.py `_restore`). Scratch repository
and bare remote in temp only."""
import unittest

from kitlib.backlog.errors import Failure
from kitlib.landing import checks
from tests.backlog.helpers import priced
from tests.landing import test_land_scope as tls
from tests.landing.test_land_store import pulled

LIVE = 'still holds its part'
RESTORE = 'set CEE --scope "src/**" --unset narrowed'


class NarrowedRestore(tls.ScopeCase):
    def split(self, *extra):
        """CEE, held by kit/feature, narrowed off src/** for ELL (kit/ell) and EMM (kit/emm),
        both pulled; then each of `extra`, a store write on top."""
        self.seed(pulled('CEE', scope=['src/c/**'],
                         narrowed=['src/** ELL kit/ell', 'src/** EMM kit/emm']),
                  pulled('ELL', by='kit/ell', scope=['src/l/**'],
                         narrowed='src/** CEE kit/feature'),
                  pulled('EMM', by='kit/emm', scope=['src/m/**'],
                         narrowed='src/** CEE kit/feature'))
        for writes, message in extra:
            self.seed(writes, message=message)
        _, tip = self.branch('feature', {'src/c/x.py': 'x\n', 'other.py': 'o\n'})
        line = self.refused(self.land(tip))
        self.assertIn('kit/feature changes 1 path(s) %s (CEE): other.py; ' % tls.OUTSIDE, line)
        self.assertIn('CEE was narrowed off src/** for ELL, EMM: ', line)
        self.assertNotIn('could not read', line)        # every record seeded was read
        return line

    def landed(self, rid):
        return {rid: None}, 'closes %s: landed %s\n' % (rid, 'f' * 40)

    def assert_no_restore(self, line):
        self.assertNotIn('--unset narrowed', line)
        self.assertNotIn('--scope "src/**"', line)
        self.assertNotIn('waits until it lands', line)
        self.assertIn('`next`\'s RESTORE line gives each part back once the records it was '
                      'narrowed for have ended', line)
        # The widen command for the path outside is still given.
        self.assertIn('set CEE --scope "src/c/**" --scope "other.py"; or land them', line)

    def test_two_live_holders_get_no_restore_and_both_are_named(self):
        line = self.split()
        self.assertIn('ELL, EMM still hold their parts, so no restore is given here', line)
        self.assert_no_restore(line)

    def test_one_holder_landed_gives_no_restore_over_the_other_live_one(self):
        line = self.split(self.landed('ELL'))
        self.assertIn('EMM %s' % LIVE, line)
        self.assertNotIn('ELL %s' % LIVE, line)
        self.assert_no_restore(line)

    def test_a_holder_removed_by_a_commit_that_does_not_close_it_still_holds_its_part(self):
        # A hand `git rm` (or a revert) is no close: `next`'s ended_reader reads only a subject
        # that closes, retires or deletes the record, and the landing reads it the same way.
        line = self.split(({'ELL': None}, 'tidy the store by hand\n'))
        self.assertIn('ELL, EMM still hold their parts', line)
        self.assert_no_restore(line)

    def test_a_blocked_holder_still_holds_its_part(self):
        line = self.split(self.landed('ELL'),
                          (priced('EMM', scope='src/m/**', state='blocked', blocked_on='owner',
                                  pulled_at='2026-10-07', pulled_by='kit/emm',
                                  narrowed='src/** CEE kit/feature'), 'block EMM\n'))
        self.assertIn('EMM %s' % LIVE, line)
        self.assert_no_restore(line)

    def test_the_last_holder_ended_gives_the_restore(self):
        line = self.split(self.landed('ELL'), self.landed('EMM'))
        self.assertIn('a path given to ELL, EMM waits until it lands, then restore the original '
                      'member with ', line)
        self.assertIn('%s; or widen' % RESTORE, line)
        self.assertNotIn(LIVE, line)

    def test_a_holder_released_to_open_has_ended(self):
        line = self.split(self.landed('ELL'),
                          (priced('EMM', scope='src/m/**'), 'release EMM\n'))
        self.assertIn('%s; or widen' % RESTORE, line)
        self.assertNotIn(LIVE, line)

    def test_a_single_live_holder_keeps_its_restore(self):
        self.seed(pulled('FOO', scope=['src/a/**'], narrowed='src/** BAR kit/bar'),
                  pulled('BAR', by='kit/bar', scope=['src/b/**'],
                         narrowed='src/** FOO kit/feature'))
        _, tip = self.branch('feature', {'src/a/x.py': 'x\n', 'other.py': 'o\n'})
        line = self.refused(self.land(tip))
        self.assertIn('; FOO was narrowed off src/** for BAR: a path given to BAR waits until it '
                      'lands, then restore the original member with ', line)
        self.assertIn('set FOO --scope "src/**" --unset narrowed; or widen', line)
        self.assertNotIn(LIVE, line)


class Live(unittest.TestCase):
    """checks._Held.live over stores read (states) and their histories, without git."""

    def held(self, *histories, **states):
        found = checks._Held()
        found.states = {rid: list(s) for rid, s in states.items()}
        found.histories = list(histories)
        return found

    def test_an_id_no_store_holds_and_no_history_ends_is_live(self):
        self.assertTrue(self.held(lambda rid: None).live('ELL'))

    def test_an_id_a_history_closes_has_ended(self):
        self.assertFalse(self.held(lambda rid: None,
                                   lambda rid: 'abc1234 closes %s: landed' % rid).live('ELL'))

    def test_an_id_that_is_not_plain_is_live(self):
        self.assertTrue(self.held(lambda rid: 'abc1234 closes %s: landed' % rid).live('ELL/../x'))

    def test_a_history_git_cannot_read_says_nothing(self):
        def boom(rid):
            raise Failure('cannot read the store history: boom')
        self.assertTrue(self.held(boom).live('ELL'))
        self.assertFalse(self.held(boom, lambda rid: 'abc1234 retires ELL: x').live('ELL'))

    def test_a_store_holding_it_decides_without_history(self):
        closes = lambda rid: 'abc1234 closes %s: landed' % rid      # noqa: E731
        self.assertTrue(self.held(closes, ELL=['pulled']).live('ELL'))
        self.assertTrue(self.held(closes, ELL=['open', 'parked']).live('ELL'))
        self.assertFalse(self.held(ELL=['open']).live('ELL'))


if __name__ == '__main__':
    unittest.main()
