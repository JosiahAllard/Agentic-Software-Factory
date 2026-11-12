"""The pre-push guard and a staging branch (docs/gate-landing.md "Staging branch"): the staging
branch is held as main (no delete, no forced push), and its setting is read from <remote>/<main>
as last fetched, so a staging tip whose .kit.json drops the section cannot drop its own guard."""
import json

from kitlib.guard import prepush
from tests.guard import prepush_case as pc

ZERO = '0' * 40


class StagingGuard(pc.PrePushCase):
    def on_main(self, kit):
        self.repo.write('.kit.json', json.dumps(kit))
        sha = self.repo.commit('kit config', '.kit.json')
        self.repo.git('push', '-q', 'origin', 'main', '%s:refs/heads/staging' % sha)
        return sha

    def staging_tip(self, kit):
        """A commit on staging, pushed there with no hook, writing `kit` as its .kit.json."""
        self.repo.git('checkout', '-q', '--detach', 'origin/staging')
        self.repo.write('.kit.json', json.dumps(kit))
        sha = self.repo.commit('staging edit', '.kit.json')
        self.repo.git('push', '-q', 'origin', '%s:refs/heads/staging' % sha)
        self.repo.git('checkout', '-q', 'main')
        return sha

    def check(self, line):
        return prepush.check(self.repo.root, [line], gate_ok=lambda r, s: (None, 'none'))

    def test_deleting_the_staging_branch_is_refused(self):
        tip = self.on_main({'repo': {'name': 'kit-test', 'push_block': False},
                            'staging': {'branch': 'staging'}})
        ok, msg = self.check('(delete) %s refs/heads/staging %s' % (ZERO, tip))
        self.assertFalse(ok)
        self.assertIn('this push deletes staging', msg)

    def test_a_staging_tip_that_drops_the_setting_keeps_its_guard(self):
        self.on_main({'repo': {'name': 'kit-test', 'push_block': False},
                      'staging': {'branch': 'staging'}})
        tip = self.staging_tip({'repo': {'name': 'kit-test', 'push_block': False}})
        ok, msg = self.check('(delete) %s refs/heads/staging %s' % (ZERO, tip))
        self.assertFalse(ok)
        self.assertIn('this push deletes staging', msg)

    def test_a_stale_fetch_of_main_does_not_drop_the_tip_s_own_guard(self):
        self.on_main({'repo': {'name': 'kit-test', 'push_block': False}})
        tip = self.staging_tip({'repo': {'name': 'kit-test', 'push_block': False},
                                'staging': {'branch': 'staging'}})   # main as fetched: none
        ok, msg = self.check('(delete) %s refs/heads/staging %s' % (ZERO, tip))
        self.assertFalse(ok)
        self.assertIn('this push deletes staging', msg)

    def test_with_no_staging_setting_a_branch_named_staging_is_not_held(self):
        tip = self.on_main({'repo': {'name': 'kit-test', 'push_block': False}})
        self.assertEqual(self.check('(delete) %s refs/heads/staging %s' % (ZERO, tip)),
                         (True, None))


if __name__ == '__main__':
    import unittest
    unittest.main()
