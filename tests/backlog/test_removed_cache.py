"""kitlib/backlog/removed_cache.py: `removed_records` served from a tip-keyed cache returns exactly
what the uncached read does, including deletions on a merged side branch (`--full-history`)."""
import glob
import os
import unittest
from unittest import mock

from kitlib.backlog import removed_cache
from kitlib.backlog import store as smod
from tests.gitfixture import TempRepo, real_fixture
from tests.realgit import real_git


def _rows(items):
    return [(s, r.sha, r.when, r.session, r.subject, text) for s, r, text in items]


@real_git('a merge with deletions on both sides needs real git history')
@real_fixture('a merge commit')
class RemovedCache(unittest.TestCase):
    def setUp(self):
        self.repo = TempRepo()
        self.addCleanup(self.repo.cleanup)
        for name in ('A', 'B', 'C'):
            self.repo.write('backlog/%s.md' % name, 'id: %s\n' % name)
        self.repo.commit('add three', 'backlog/A.md', 'backlog/B.md', 'backlog/C.md')
        self.repo.git('checkout', '-q', '-b', 'side')
        self.repo.git('rm', '-q', 'backlog/A.md')
        self.repo.git('commit', '-q', '-m', 'deletes A\n\nSession: s1')
        self.repo.git('checkout', '-q', 'main')
        self.repo.git('rm', '-q', 'backlog/B.md')
        self.repo.git('commit', '-q', '-m', 'deletes B')
        self.repo.git('merge', '-q', '--no-ff', '-m', 'merge side', 'side')
        self.root = self.repo.root

    def cache_files(self):
        return glob.glob(os.path.join(self.root, '.git', 'kit-removed-*.json'))

    def uncached(self):
        return smod.scan_removed(self.root, 'HEAD', 'backlog')

    def test_cached_equals_uncached_over_a_merge_and_runs_no_log_the_second_time(self):
        want = _rows(self.uncached())
        self.assertEqual(sorted(r[0] for r in want), ['A', 'B'])
        self.assertEqual(_rows(smod.removed_records(self.root, 'HEAD', 'backlog')), want)
        self.assertEqual(len(self.cache_files()), 1)
        with mock.patch.object(smod, 'scan_removed') as scan:
            self.assertEqual(_rows(smod.removed_records(self.root, 'HEAD', 'backlog')), want)
            scan.assert_not_called()

    def test_a_tip_with_no_new_deletion_keeps_the_list_and_one_with_a_deletion_rescans(self):
        smod.removed_records(self.root, 'HEAD', 'backlog')
        self.repo.write('backlog/D.md', 'id: D\n')
        self.repo.commit('add D', 'backlog/D.md')
        self.assertEqual(_rows(smod.removed_records(self.root, 'HEAD', 'backlog')),
                         _rows(self.uncached()))
        self.repo.git('rm', '-q', 'backlog/C.md')
        self.repo.git('commit', '-q', '-m', 'deletes C')
        got = smod.removed_records(self.root, 'HEAD', 'backlog')
        self.assertEqual(_rows(got), _rows(self.uncached()))
        self.assertIn('C', [s for s, _r, _t in got])

    def test_a_corrupt_cache_falls_back_and_a_failed_write_does_not_fail_the_read(self):
        smod.removed_records(self.root, 'HEAD', 'backlog')
        with open(self.cache_files()[0], 'w', encoding='utf-8') as f:
            f.write('{not json')
        want = _rows(self.uncached())
        self.assertEqual(_rows(smod.removed_records(self.root, 'HEAD', 'backlog')), want)
        with mock.patch.object(removed_cache.atomic, 'write_text', side_effect=OSError('full')):
            self.assertEqual(_rows(smod.removed_records(self.root, 'HEAD', 'backlog')), want)


if __name__ == '__main__':
    unittest.main()
