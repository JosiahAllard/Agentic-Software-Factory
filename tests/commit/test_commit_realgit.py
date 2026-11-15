"""tests/commit/test_commit.py's real-git commit cases (docs/commit.md): the private index
checked against git's own commit, its stat data and intent-to-add entries, and hooks and the
reflog. Split out of test_commit.py so each module stays well inside the kit_tests per-module
limit; the module setup and the base cases are test_commit's (as test_commit_narrowed.py).
"""
import os
import shutil
import tempfile
from unittest import mock

from kitlib.commit import checks
from kitlib.commit import run as C
from kitlib.git import cmd

from tests import gitfixture
from tests.commit import test_commit
from tests.commit.test_commit import LinkedCase
from tests.realgit import real_git

setUpModule = test_commit.setUpModule


@real_git('the commit is built in a private index (GIT_INDEX_FILE): checked against git\'s own '
          'commit as well as the fake\'s')
class AnotherWriterStagesDuringTheCallRealGit(test_commit.AnotherWriterStagesDuringTheCall):
    pass


@real_git('ls-files --debug prints an entry\'s stat data: the fake has no --debug')
class ThePrivateIndexKeepsStatData(LinkedCase):
    """A path the commit does not name keeps the real index's stat
    data in the private index, so `git commit` does not hash every tracked work-tree file again
    (a read-tree index records size 0 for every entry)."""

    private = test_commit.AnotherWriterStagesDuringTheCall.private

    def size(self, env, path):
        r = cmd.call(['ls-files', '--debug', '--', path], self.wt, env=env)
        self.assertEqual(r.rc, 0, r.err)
        for line in cmd.text(r.out).splitlines():
            if line.strip().startswith('size:'):
                return int(line.split()[1])
        self.fail('no size line for %s: %r' % (path, r.out))

    def test_an_unnamed_tracked_file_keeps_its_stat_size(self):
        # An old mtime, refreshed into the real index: its entry is not racily clean (size kept).
        old = os.path.getmtime(os.path.join(self.wt, 'seed.txt')) - 3 * 86400
        os.utime(os.path.join(self.wt, 'seed.txt'), (old, old))
        self.git('update-index', '-q', '--refresh')
        self.assertNotEqual(self.size(None, 'seed.txt'), 0)
        self.put('a.py', 'a = 1\n')
        self.git('add', '--', 'a.py')
        _got, env = self.private(C.index_snapshot(self.wt, {'a.py': 'A'}))
        self.assertNotEqual(self.size(env, 'seed.txt'), 0)


@real_git('git add -N (intent to add): the fake has no -N')
class AnIntentToAddEntryInTheRealIndex(LinkedCase):
    """An intent-to-add entry holds the empty blob and `ls-files --stage` does not show its flag:
    for a file empty at HEAD it looks like HEAD's entry, yet `git commit` leaves it out of the
    tree. The private index puts HEAD's entry back for it, so the commit keeps the file (another
    writer may make the entry during the call, after commit.py's staged-files check)."""

    private = test_commit.AnotherWriterStagesDuringTheCall.private

    def test_a_file_empty_at_head_stays_in_the_private_index_tree(self):
        self.put('empty.txt', '')
        self.git('add', '--', 'empty.txt')
        self.git('commit', '-q', '-m', 'empty', '--', 'empty.txt')
        self.git('rm', '-q', '--cached', '--', 'empty.txt')
        self.git('add', '-N', '--', 'empty.txt')
        self.put('a.py', 'a = 1\n')
        self.git('add', '--', 'a.py')
        _got, env = self.private(C.index_snapshot(self.wt, {'a.py': 'A'}))
        r = cmd.call(['write-tree'], self.wt, env=env)
        self.assertEqual(r.rc, 0, r.err)
        tree = cmd.text(r.out).strip()
        self.assertIn('empty.txt', self.git('ls-tree', '--name-only', tree).splitlines())


@gitfixture.real_fixture('appends to the linked worktree\'s reflog (logs/HEAD), which only real '
                         'git\'s `worktree add` writes')
@real_git('hooks, and the reflog entry `git commit` writes under this call\'s tag: the fake runs '
          'no hooks and writes no reflog')
class HooksAndTheReflog(LinkedCase):
    """A hook may change the commit (a commit-msg hook appends a trailer, a pre-commit hook stages
    a file into the private index): it is still this call's commit. On a moved HEAD, the reflog
    entry tagged by this call (GIT_REFLOG_ACTION) names this call's commit and its base."""

    def hook(self, name, body):
        hooks = tempfile.mkdtemp(prefix='kit-test-hooks-')
        self.addCleanup(shutil.rmtree, hooks, True)
        with open(os.path.join(hooks, name), 'w', encoding='utf-8', newline='\n') as fh:
            fh.write('#!/bin/sh\n%s\n' % body)
        self.git('config', 'core.hooksPath', hooks.replace('\\', '/'))

    def around_commit(self, before=None, after=None):
        """Run `before` just before this call's `git commit` and `after` just after it; the list
        their answers are appended to, in that order."""
        real, seen, done = cmd.call, [], []

        def wrapper(args, cwd, *a, **kw):
            first = args[:1] == ['commit'] and not done
            if first:
                done.append(1)
                if before:
                    seen.append(before())
            r = real(args, cwd, *a, **kw)
            if first and after:
                seen.append(after())
            return r
        p = mock.patch.object(C.cmd, 'call', wrapper)
        p.start()
        self.addCleanup(p.stop)
        return seen

    def test_a_commit_msg_hook_that_appends_a_line_commits(self):
        self.hook('commit-msg', 'printf "Change-Id: I0123\\n" >> "$1"')
        start = self.head()
        self.put('a.py', 'a = 1\n')
        code, out = self.commit('add a', 'a.py')
        self.assertEqual(code, 0, out)
        self.assertIn('Change-Id: I0123', self.message())
        self.assertEqual(self.git('rev-parse', 'HEAD^'), start)
        self.assertEqual(self.git('show', 'HEAD:a.py'), 'a = 1')

    def test_a_pre_commit_hook_that_stages_a_file_commits(self):
        self.hook('pre-commit', 'printf "hook\\n" > extra.txt && git add extra.txt')
        start = self.head()
        self.put('a.py', 'a = 1\n')
        code, out = self.commit('add a', 'a.py')
        self.assertEqual(code, 0, out)
        self.assertEqual(self.git('show', 'HEAD:extra.txt'), 'hook')
        self.assertEqual(self.git('rev-parse', 'HEAD^'), start)

    def test_an_allow_empty_commit_a_pre_commit_hook_adds_a_file_to_is_undone(self):
        self.hook('pre-commit', 'printf "hook\\n" > extra.txt && git add extra.txt')
        start = self.head()
        code, out = self.commit('close it', allow_empty=True)
        self.assertEqual(code, 1, out)
        self.assertIn('refused: --allow-empty, but a hook changed the commit', out)
        self.assertIn('Undone', out)
        self.assertNotIn('NOT undone', out)
        self.assertEqual(self.head(), start)
        self.assertEqual(self.git('diff', '--cached', '--name-only'), '')

    def test_head_moved_under_a_hooked_commit_is_still_undone(self):
        self.hook('commit-msg', 'printf "Change-Id: I0123\\n" >> "$1"')
        seen = self.around_commit(before=self.other_writer_commits)
        self.put('a.py', 'a = 1\n')
        code, out = self.commit('add a', 'a.py')
        self.assertEqual(code, 1, out)
        self.assertIn('Undone', out)
        self.assertNotIn('NOT undone', out)
        self.assertEqual(self.head(), seen[0])
        self.assertEqual(self.git('show', 'HEAD:b.py'), 'theirs')
        self.assertIn('A  a.py', self.git('status', '--porcelain'))

    def test_ours_on_a_moved_head_under_a_stacked_commit_names_what_it_may_undo(self):
        """X lands before git reads HEAD, Y on top of ours before the read-back: ours, which
        lacks X's change, is not at HEAD and cannot be undone; the refusal names ours and X."""
        seen = self.around_commit(before=lambda: self.other_writer_commits('b.py'),
                                  after=lambda: self.other_writer_commits('c.py'))
        self.put('a.py', 'a = 1\n')
        code, out = self.commit('add a', 'a.py')
        self.assertEqual(code, 1, out)
        self.assertIn('NOT undone', out)
        self.assertEqual(self.head(), seen[1])
        ours = self.git('rev-parse', 'HEAD^')
        self.assertEqual(self.git('rev-parse', 'HEAD^^'), seen[0])
        self.assertIn(ours[:12], out)
        self.assertIn(seen[0][:12], out)
        self.assertIn('restore', out)
        self.assertNotIn('would have reverted', out)

    # -- the real index after a hook changed the commit

    def index_blob(self, path):
        return self.git('ls-files', '-s', '--', path).split()[1]

    def test_a_file_a_pre_commit_hook_stages_is_in_the_real_index_too(self):
        """The hook's `git add` lands in the private index only: without the real index brought
        into line, it holds a staged deletion of extra.txt that the next commit would make."""
        self.hook('pre-commit', 'printf "hook\\n" > extra.txt && git add extra.txt')
        self.put('a.py', 'a = 1\n')
        code, out = self.commit('add a', 'a.py')
        self.assertEqual(code, 0, out)
        self.assertNotIn('WARNING', out)
        self.assertIn('index     set to the commit\'s copy of what a hook changed: extra.txt', out)
        self.assertEqual(self.git('diff', '--cached', '--name-only'), '')
        self.assertEqual(self.index_blob('extra.txt'), self.git('rev-parse', 'HEAD:extra.txt'))

    def test_a_named_file_a_formatter_hook_restages_is_in_the_real_index_too(self):
        self.hook('pre-commit', 'printf "a = 2\\n" > a.py && git add a.py')
        self.put('a.py', 'a = 1\n')
        code, out = self.commit('add a', 'a.py')
        self.assertEqual(code, 0, out)
        self.assertEqual(self.git('show', 'HEAD:a.py'), 'a = 2')
        self.assertEqual(self.git('diff', '--cached', '--name-only'), '')
        self.assertEqual(self.index_blob('a.py'), self.git('rev-parse', 'HEAD:a.py'))

    def test_a_hook_change_another_writer_also_staged_keeps_the_commit_and_refuses(self):
        """Another writer stages extra.txt in the real index during the call, and the hook commits
        its own copy: the commit stands, the real index keeps theirs, and the refusal names the
        path and the command that would bring the index into line with the commit."""
        self.hook('pre-commit', 'printf "hook\\n" > extra.txt && git add extra.txt')
        start = self.head()

        def theirs():
            self.put('extra.txt', 'theirs\n')
            self.git('add', '--', 'extra.txt')
            return self.index_blob('extra.txt')
        seen = self.around_commit(before=theirs)
        self.put('a.py', 'a = 1\n')
        code, out = self.commit('add a', 'a.py')
        self.assertEqual(code, 1, out)
        self.assertEqual(self.git('rev-parse', 'HEAD^'), start)
        self.assertEqual(self.git('show', 'HEAD:extra.txt'), 'hook')
        self.assertEqual(self.index_blob('extra.txt'), seen[0])
        self.assertIn('extra.txt', out)
        self.assertIn('git restore --staged --source=', out)
        self.assertIn('committed ', out)

    def test_a_path_a_pre_commit_hook_removes_is_removed_from_the_real_index_too(self):
        """The hook's `git rm` drops seed.txt from the private index only: the real index must
        drop it too (a mode-0 index line), or the next commit would add it back."""
        self.hook('pre-commit', 'git rm -q seed.txt')
        self.put('a.py', 'a = 1\n')
        code, out = self.commit('add a', 'a.py')
        self.assertEqual(code, 0, out)
        self.assertIn('index     set to the commit\'s copy of what a hook changed: seed.txt', out)
        self.assertEqual(self.git('ls-tree', '--name-only', 'HEAD', '--', 'seed.txt'), '')
        self.assertEqual(self.git('ls-files', '--stage', '--', 'seed.txt'), '')
        self.assertEqual(self.git('diff', '--cached', '--name-only'), '')

    def test_a_failing_index_write_after_a_hook_change_refuses_every_path(self):
        """update-index on the real index fails: the commit stands, nothing is reported as set,
        and the refusal names every path the hook changed."""
        self.hook('pre-commit', 'printf "hook\\n" > extra.txt && printf "a = 2\\n" > a.py && '
                  'git add extra.txt a.py')
        start, real = self.head(), cmd.call

        def wrapper(args, cwd, *a, **kw):
            if args[:2] == ['update-index', '--index-info'] and not kw.get('env'):
                return cmd.Result(128, b'', 'fatal: injected update-index')
            return real(args, cwd, *a, **kw)
        self.put('a.py', 'a = 1\n')
        with mock.patch.object(C.cmd, 'call', wrapper):
            code, out = self.commit('add a', 'a.py')
        self.assertEqual(code, 1, out)
        self.assertEqual(self.git('rev-parse', 'HEAD^'), start)
        self.assertNotIn('index     set to', out)
        self.assertIn('a.py, extra.txt', out)
        self.assertIn('git restore --staged --source=', out)
        self.assertIn('-- a.py extra.txt', out)

    # -- the reflog checks

    def test_a_parentless_head_after_the_commit_refuses_with_its_own_line(self):
        def orphan():
            tree = self.git('rev-parse', 'HEAD^{tree}')
            new = self.git('commit-tree', tree, '-m', 'orphan')
            self.git('update-ref', 'HEAD', new)
            return new
        seen = self.around_commit(after=orphan)
        self.put('a.py', 'a = 1\n')
        code, out = self.commit('add a', 'a.py')
        self.assertEqual(code, 1, out)
        self.assertIn('NOT undone', out)
        self.assertIn('%s, a commit with no parent' % seen[0][:12], out)
        self.assertNotIn('committed nothing', out)
        self.assertNotIn('would have reverted', out)
        self.assertEqual(self.head(), seen[0])

    def test_a_logged_base_that_is_not_heads_parent_refuses_and_undoes_nothing(self):
        real = C.tagged_entry

        def wrong_base(root, tag):
            got = real(root, tag)
            return ('1' * 40, got[1])
        self.around_commit(before=self.other_writer_commits)
        self.put('a.py', 'a = 1\n')
        with mock.patch.object(C, 'tagged_entry', wrong_base):
            code, out = self.commit('add a', 'a.py')
        self.assertEqual(code, 1, out)
        self.assertIn('NOT undone', out)
        self.assertIn('git logged', out)
        self.assertEqual(self.git('show', 'HEAD:a.py'), 'a = 1')

    def test_ours_on_the_checked_head_that_head_no_longer_contains_refuses(self):
        """Another writer points HEAD back at the start and commits there: this call's commit,
        made on the HEAD it checked, is no longer in HEAD's history."""
        start = self.head()

        def rewound():
            self.git('update-ref', 'HEAD', start)
            return self.other_writer_commits()
        seen = self.around_commit(after=rewound)
        self.put('a.py', 'a = 1\n')
        code, out = self.commit('add a', 'a.py')
        self.assertEqual(code, 1, out)
        self.assertIn('does not contain it', out)
        self.assertIn('NOT undone', out)
        self.assertEqual(self.head(), seen[0])

    def test_two_entries_with_one_tag_are_none(self):
        path = C.repo.git_path(self.wt, 'logs/HEAD')[0]
        tag, old, new = C.reflog_tag(), '2' * 40, '3' * 40
        line = '%s %s A <a@example.invalid> 1 +0000\t%s: x\n' % (old, new, tag)
        with open(path, 'a', encoding='utf-8', newline='\n') as fh:
            fh.write(line)
        self.assertEqual(C.tagged_entry(self.wt, tag), (old, new))
        with open(path, 'a', encoding='utf-8', newline='\n') as fh:
            fh.write(line)
        self.assertIsNone(C.tagged_entry(self.wt, tag))
