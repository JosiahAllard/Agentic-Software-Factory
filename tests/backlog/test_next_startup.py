"""`backlog.py next`'s start-up cost (KIT-BACKLOG-NEXT-STARTUP): from a linked worktree it asks git
for each of the worktree list, the git dirs and the checked-out branch once, not once per caller;
and its imports leave out kitlib.backlog.check and audit, which only `check` and a write use."""
import collections
import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import unittest
from unittest import mock

from kitlib.config import pmdata
from kitlib.git import cmd
from kitlib.git import repo as gitrepo

from tests.backlog.helpers import TODAY, priced
from tests.backlog.ownertree import setUpModule, tearDownModule  # noqa: F401 - the owner's tree
from tests.gitfixture import TempRepo, fixture_env
from tests.home import temp_home

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TOOL = os.path.join(ROOT, 'tools', 'backlog.py')
STORE = 'kit/backlog'
BRANCH = 'kit/wk'
# 13 before the change; 5 now: the git dirs of the worker worktree and of the PM data worktree,
# the branch, the one worktree list and the one branch list (`for-each-ref`).
MAX_GIT_CALLS = 5


def load_tool():
    spec = importlib.util.spec_from_file_location('backlog_tool_under_test', TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class NextStartup(unittest.TestCase):

    def setUp(self):
        patch = mock.patch.dict(os.environ, fixture_env(), clear=True)
        patch.start()
        self.addCleanup(patch.stop)
        temp_home(self)
        self.repo = TempRepo(bare_remote=True)
        self.addCleanup(self.repo.cleanup)
        pmdata.write(self.repo.common_dir(), 'pmdata', 'private', self.repo.head())
        self.pm = self.repo.worktree('pm', branch='pmdata')
        self.repo.write('.kit.json', json.dumps({'backlog': {'store': STORE}}) + '\n', root=self.pm)
        text = priced('FOO', state='pulled', pulled_at=TODAY, pulled_by=BRANCH,
                      scope='tools/x/**')['FOO']
        self.repo.write(STORE + '/FOO.md', text, root=self.pm)
        self.repo.commit('the live store', '.kit.json', STORE + '/FOO.md', cwd=self.pm)
        self.wt = self.repo.worktree('wk', branch=BRANCH)

    def test_next_from_a_linked_worktree_asks_git_each_question_once(self):
        tool = load_tool()
        calls = []
        real = cmd.call

        def counting(args, cwd, *a, **kw):
            calls.append((tuple(args), cwd))
            return real(args, cwd, *a, **kw)

        out = io.StringIO()
        with mock.patch.object(cmd, 'call', counting), contextlib.redirect_stdout(out):
            rc = tool.main(['-C', self.wt, '--no-fetch', 'next'])
        self.assertEqual(rc, 0, out.getvalue())
        self.assertIn('NEXT', out.getvalue())
        listing = '\n'.join(' '.join(c) for c, _cwd in calls)
        self.assertLessEqual(len(calls), MAX_GIT_CALLS, listing)

        def question(c, cwd):
            # Any `worktree list` form is the repository's one question; any rev-parse of the git
            # dirs is one question per checkout; the rest are told apart by their words.
            if c[:2] == ('worktree', 'list'):
                return 'worktree list'
            where = os.path.normcase(os.path.abspath(cwd))
            if c[:1] == ('rev-parse',) and '--git-common-dir' in c:
                return 'git dirs of ' + where
            return ' '.join(c) + ' in ' + where

        asked = collections.Counter(question(c, cwd) for c, cwd in calls)
        again = sorted(q for q, n in asked.items() if n > 1)
        self.assertEqual(again, [], 'asked more often than allowed:\n' + listing)
        self.assertEqual(sum(1 for c, _cwd in calls if c[:2] == ('worktree', 'list') and '-z' in c),
                         1, listing)

    def test_next_reads_the_live_branches_through_the_context(self):
        tool = load_tool()
        seen = []
        real = tool.pmod.Context.live_branches

        def spy(ctx, snap):
            seen.append(snap)
            return real(ctx, snap)

        with mock.patch.object(tool.pmod.Context, 'live_branches', spy), \
                contextlib.redirect_stdout(io.StringIO()):
            rc = tool.main(['-C', self.wt, '--no-fetch', 'next'])
        self.assertEqual(rc, 0)
        self.assertTrue(seen, 'next probed the branches without the Context memo')

    def test_a_failed_worktree_list_is_asked_again_a_good_one_is_kept(self):
        answers = [(None, 'git timed out'), ([], None), ([], None)]
        with mock.patch.object(gitrepo, 'worktrees', side_effect=answers) as asked:
            listing = gitrepo.Worktrees()
            self.assertEqual(listing('x'), (None, 'git timed out'))
            self.assertEqual(listing('x'), ([], None))
            self.assertEqual(listing('y'), ([], None))
        self.assertEqual(asked.call_count, 2)

    def test_importing_the_tool_leaves_out_check_and_audit(self):
        code = ('import importlib.util, sys\n'
                'spec = importlib.util.spec_from_file_location("t", sys.argv[1])\n'
                'mod = importlib.util.module_from_spec(spec)\n'
                'spec.loader.exec_module(mod)\n'
                'print(sorted(m for m in ("kitlib.backlog.check", "kitlib.backlog.audit")'
                ' if m in sys.modules))\n')
        p = subprocess.run([sys.executable, '-c', code, TOOL], capture_output=True, text=True,
                           cwd=ROOT, env=fixture_env(), timeout=120)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(p.stdout.strip(), '[]', p.stderr)


if __name__ == '__main__':
    unittest.main()
