"""review_pack reads each side's `.kit.json` once and runs `git merge-base` once, though the pack,
owner_rules and the gate line each need them (KIT-REVIEWPACK-REPEATED-GIT)."""
from unittest import mock

from kitlib import reviewpack
from tests.reviewpack.repo import PackCase


class ReadsOnce(PackCase):
    def test_each_config_is_loaded_once_and_the_merge_base_is_read_once(self):
        self.git('checkout', '-q', '-b', 'feature')
        self.write('f.txt', '1')
        self.commit_all('change f')
        loads, spawns = [], []
        real_load, real_call = reviewpack.load_at, reviewpack.gitcmd.call

        def load_at(root, rev, *a, **k):
            loads.append(rev)
            return real_load(root, rev, *a, **k)

        def call(args, *a, **k):
            spawns.append(tuple(args))
            return real_call(args, *a, **k)

        with mock.patch.object(reviewpack, 'load_at', load_at), \
                mock.patch.object(reviewpack.gitcmd, 'call', call):
            code, out = self.run_pack('feature', '--base', 'main')
        self.assertEqual(code, 0, out)
        self.assertIn('gate     tip', out)
        self.assertEqual(sorted(loads), ['feature', 'main'])
        self.assertEqual([s for s in spawns if s[0] == 'merge-base' and 'feature' in s],
                         [('merge-base', 'main', 'feature')])
