"""Parking a pulled record (kitlib.backlog.plan plan_set_status) gives up its pull, so it takes
block's and release's holder check (stop_pull): its holder parks it, anybody parks a dead pull or
one whose worktree `Context.liveness` finds dead, and a live pull on another branch refuses,
naming its holder."""
import unittest

from kitlib.backlog import record as rmod
from kitlib.backlog.errors import Refusal
from tests.backlog.helpers import BRANCH, TODAY, StoreCase, priced

OTHER = 'tools/other-branch'
PULLED = dict(state='pulled', pulled_at=TODAY)


class ParkPulled(StoreCase):
    remote = False

    def assert_parked(self, rid):
        r = self.get(rid)
        self.assertEqual(r.get('state'), 'parked')
        for key in rmod.PULL_KEYS:
            self.assertFalse(r.has(key), key)

    def test_a_live_pull_on_another_branch_refuses_naming_its_holder(self):
        self.repo.git('branch', OTHER)
        self.seed(priced('FOO', pulled_by=OTHER, **PULLED))
        self.ctx.liveness = lambda b: ('active 1.0 h ago', None)
        with self.assertRaises(Refusal) as cm:
            self.verb('set_status', 'FOO', 'parked', 'Q-001')
        self.assertEqual(str(cm.exception),
                         '`FOO` is in flight (pulled by %s), and its tree is live: active 1.0 h '
                         'ago. Its holder parks or releases it: from that branch, or as that '
                         'branch from any checkout (backlog.py -C <store> --as %s set-status FOO '
                         'parked --q Q-001)' % (OTHER, OTHER))
        r = self.get('FOO')
        self.assertEqual((r.get('state'), r.get('pulled_by')), ('pulled', OTHER))

    def test_no_probe_refuses_a_live_branch(self):
        self.repo.git('branch', OTHER)
        self.seed(priced('FOO', pulled_by=OTHER, **PULLED))
        self.assertIsNone(self.ctx.liveness)
        with self.assertRaises(Refusal) as cm:
            self.verb('set_status', 'FOO', 'parked', None)
        self.assertIn('pulled by %s' % OTHER, str(cm.exception))
        self.assertIn('a branch or worktree by that name exists', str(cm.exception))
        self.assertEqual(self.get('FOO').get('state'), 'pulled')

    def test_the_holder_parks_its_own_pull(self):
        self.seed(priced('FOO', pulled_by=BRANCH, **PULLED))
        change = self.verb('set_status', 'FOO', 'parked', 'Q-001')
        self.assert_parked('FOO')
        self.assertIn('  pull by %s given up: its scope is free and it is out of the offer'
                      % BRANCH, change.lines)
        self.assertIn('  was pulled_by: %s' % BRANCH, change.lines)

    def test_anybody_parks_a_dead_pull(self):
        self.seed(priced('FOO', pulled_by='tools/gone', **PULLED))
        change = self.verb('set_status', 'FOO', 'parked', None)
        self.assert_parked('FOO')
        self.assertIn('  was pulled_by: tools/gone (dead)', change.lines)

    def test_anybody_parks_a_pull_whose_worktree_the_probe_finds_dead(self):
        self.repo.git('branch', OTHER)
        self.seed(priced('FOO', pulled_by=OTHER, **PULLED))
        asked = []
        self.ctx.liveness = lambda b: (asked.append(b), (None, None))[1]
        change = self.verb('set_status', 'FOO', 'parked', None)
        self.assertEqual(asked, [OTHER])
        self.assert_parked('FOO')
        self.assertIn('  was pulled_by: %s (its worktree is dead)' % OTHER, change.lines)


if __name__ == '__main__':
    unittest.main()
