"""kitlib.ownerfiles.rules on a scratch repository: a commit that changes an owner-only path is a
violation unless its AUTHOR email is the owner's, for the paths that landing the tip on main would
change (NetChange), or it carries an Owner-Approval the hub verifies (OwnerApprovals); a merge is
judged by what it changes beyond the clean merge of its parents; commits already on main are not
judged."""
import datetime
import json
import unittest
from unittest import mock

from kitlib.commit import checks as C
from kitlib.config import owner
from kitlib.ownerfiles import rules as oc
from kitlib.hubclient import errors as huberrors
from kitlib.ownerfiles import approvals as ap
from kitlib.git import cmd
from tests import gitfixture
from tests.realgit import real_git

OWNER = 'owner@example.invalid'
AGENT = 'agent@example.invalid'
KEY = 'k' * 64                                    # the approval key the stand-in hub signs with
REPO = 'git.example.invalid/owner/repo'
NOW = datetime.datetime(2026, 1, 2, 3, 4, 5, tzinfo=datetime.timezone.utc)
MAIN = 'refs/remotes/origin/main'                 # main as last fetched: where spent looks
LINES = ''.join('line %d\n' % n for n in range(1, 13))


def edit(text, n, new):
    """`text` with its line `n` (1-based) replaced by `new`."""
    rows = text.splitlines(keepends=True)
    rows[n - 1] = new + '\n'
    return ''.join(rows)


class OwnerCommitsCase(unittest.TestCase):
    def setUp(self):
        self.repo = gitfixture.TempRepo()
        self.addCleanup(self.repo.cleanup)
        self.main = self.repo.initial
        self.rules = owner.Rules(owner_email=OWNER)

    def commit_as(self, email, message, files, cwd=None):
        for rel, text in files.items():
            self.repo.write(rel, text, root=cwd)
        self.repo.git('add', '--', *files, cwd=cwd)
        self.repo.git('-c', 'user.email=%s' % email, 'commit', '-q', '-m', message, cwd=cwd)
        return self.repo.git('rev-parse', 'HEAD', cwd=cwd)

    def bad(self, tip, rules=None, exclude=None):
        found, why = oc.violations(self.repo.root, rules or self.rules, tip,
                                   [self.main] if exclude is None else exclude)
        self.assertIsNone(why)
        return found


class Commits(OwnerCommitsCase):
    def test_an_agent_commit_to_an_owner_only_path_is_a_violation(self):
        sha = self.commit_as(AGENT, 'agent edits CLAUDE.md', {'CLAUDE.md': 'x\n', 'a.py': 'a\n'})
        self.assertEqual(self.bad(sha), [oc.Violation(sha, AGENT, ['CLAUDE.md'])])

    def test_the_owners_commit_is_not(self):
        sha = self.commit_as(OWNER, 'owner edits CLAUDE.md', {'CLAUDE.md': 'x\n'})
        self.assertEqual(self.bad(sha), [])

    def test_owner_email_compares_case_insensitively(self):
        sha = self.commit_as(OWNER.upper(), 'owner edits CLAUDE.md', {'CLAUDE.md': 'x\n'})
        self.assertEqual(self.bad(sha), [])

    def test_ordinary_paths_and_excepted_paths_are_not(self):
        sha = self.commit_as(AGENT, 'agent work', {'a.py': 'a\n', '.claude/worktrees/x/y.txt': 'y\n'})
        self.assertEqual(self.bad(sha), [])

    def test_a_directory_entry_locks_what_is_under_it(self):
        sha = self.commit_as(AGENT, 'agent edits a workflow', {'.github/workflows/ci.yml': 'x\n'})
        self.assertEqual(self.bad(sha), [oc.Violation(sha, AGENT, ['.github/workflows/ci.yml'])])

    def test_a_deletion_counts(self):
        self.commit_as(OWNER, 'owner adds CLAUDE.md', {'CLAUDE.md': 'x\n'})
        self.main = self.repo.git('rev-parse', 'HEAD')
        self.repo.git('rm', '-q', 'CLAUDE.md')
        self.repo.git('-c', 'user.email=%s' % AGENT, 'commit', '-q', '-m', 'agent deletes it')
        sha = self.repo.git('rev-parse', 'HEAD')
        self.assertEqual(self.bad(sha), [oc.Violation(sha, AGENT, ['CLAUDE.md'])])

    def test_commits_in_exclude_are_not_judged(self):
        sha = self.commit_as(AGENT, 'agent edits CLAUDE.md', {'CLAUDE.md': 'x\n'})
        later = self.commit_as(AGENT, 'agent work', {'a.py': 'a\n'})
        self.assertEqual(self.bad(later, exclude=[sha]), [])
        self.assertEqual([v.sha for v in self.bad(later)], [sha])

    def test_every_offending_commit_is_named(self):
        one = self.commit_as(AGENT, 'one', {'CLAUDE.md': '1\n'})
        two = self.commit_as(AGENT, 'two', {'.kit.json': '{}\n'})
        self.assertEqual(sorted(v.sha for v in self.bad(two)), sorted([one, two]))

    def test_no_owner_email_fails_closed(self):
        sha = self.commit_as(OWNER, 'owner edits CLAUDE.md', {'CLAUDE.md': 'x\n'})
        rules = owner.Rules()
        found = self.bad(sha, rules=rules)
        self.assertEqual([v.sha for v in found], [sha])
        self.assertIn('identity is not configured', oc.describe(rules, found))

    def test_an_owner_email_also_listed_as_an_agents_proves_nothing(self):
        sha = self.commit_as(OWNER, 'owner edits CLAUDE.md', {'CLAUDE.md': 'x\n'})
        rules = owner.Rules(owner_email=OWNER, agent_emails=[OWNER])
        self.assertEqual([v.sha for v in self.bad(sha, rules=rules)], [sha])

    def test_a_mailmap_does_not_make_an_agent_the_owner(self):
        self.commit_as(OWNER, 'owner maps the agent', {'.mailmap': 'Owner <%s> <%s>\n' % (OWNER, AGENT)})
        self.main = self.repo.git('rev-parse', 'HEAD')
        sha = self.commit_as(AGENT, 'agent edits CLAUDE.md', {'CLAUDE.md': 'x\n'})
        self.assertEqual([v.author for v in self.bad(sha)], [AGENT])

    def test_empty_and_root_commits_in_one_batch(self):
        self.repo.git('-c', 'user.email=%s' % AGENT, 'commit', '-q', '--allow-empty', '-m', 'empty')
        empty = self.repo.git('rev-parse', 'HEAD')
        sha = self.commit_as(AGENT, 'agent edits CLAUDE.md', {'CLAUDE.md': 'x', 'a.py': 'a'})
        found, why = oc.plain_changes(self.repo.root, [sha, empty, self.repo.initial])
        self.assertIsNone(why)
        self.assertEqual(found, {sha: ['CLAUDE.md', 'a.py'], empty: [], self.repo.initial: ['README.md']})
        for one in (sha, empty, self.repo.initial):           # the batch agrees with one at a time
            parents = self.repo.git('log', '-1', '--format=%P', one).split()
            self.assertEqual(oc.changed(self.repo.root, one, parents), (found[one], None))
        self.assertEqual(self.bad(sha, exclude=[]), [oc.Violation(sha, AGENT, ['CLAUDE.md'])])

    def test_a_bad_revision_is_an_error_not_a_pass(self):
        found, why = oc.violations(self.repo.root, self.rules, '1' * 40, [self.main])
        self.assertIsNone(found)
        self.assertTrue(why)
        found, why = oc.violations(self.repo.root, self.rules, '--all')
        self.assertIsNone(found)
        self.assertTrue(why)

    def test_describe_names_sha_author_and_paths(self):
        sha = self.commit_as(AGENT, 'agent edits CLAUDE.md', {'CLAUDE.md': 'x\n'})
        line = oc.describe(self.rules, self.bad(sha))
        self.assertIn('%s by %s changes CLAUDE.md' % (sha[:10], AGENT), line)
        self.assertNotIn('not configured', line)


GRANTS = '.claude/layering-grants.txt'


class ShrinkOnly(OwnerCommitsCase):
    """A `"owner".shrink_only` path: an agent commit that only deletes lines of it is no
    violation; any other change to it is, judged commit by commit (ownerrules.deletes_only)."""

    def setUp(self):
        super().setUp()
        self.rules = owner.Rules(owner_email=OWNER, shrink_only=[GRANTS, '.claude/new.txt'])
        self.main = self.commit_as(OWNER, 'owner adds the grants', {GRANTS: LINES})

    def agent(self, git_args=(), files=None, message='agent'):
        """An agent commit of `files` (written and added), then `git_args` (a git command, or a
        callable)."""
        for rel, text in (files or {}).items():
            self.repo.write(rel, text)
        self.repo.git('add', '-A')
        if callable(git_args):
            git_args()
        elif git_args:
            self.repo.git(*git_args)
        self.repo.git('-c', 'user.email=%s' % AGENT, 'commit', '-q', '-m', message)
        return self.repo.git('rev-parse', 'HEAD')

    def make_executable(self, path):
        blob = self.repo.git('rev-parse', ':%s' % path)
        r = oc.cmd.call(['update-index', '--index-info'], self.repo.root,
                        stdin=('100755 %s\t%s\n' % (blob, path)).encode('utf-8'))
        self.assertEqual(r.rc, 0, r.err)

    def test_a_delete_only_commit_passes(self):
        sha = self.agent(files={GRANTS: LINES.replace('line 3\n', '').replace('line 9\n', '')})
        self.assertEqual(self.bad(sha), [])
        self.assertEqual(oc.deletes_only(self.repo.root, GRANTS, [self.main, sha]), (True, None))

    def test_every_other_change_is_refused(self):
        dropped = LINES.replace('line 3\n', '')
        cases = {   # name: (the owner's commit first, or None; the git step after; files added)
            'add only': (None, (), {GRANTS: LINES + 'grant\n'}),
            'a changed line': (None, (), {GRANTS: edit(LINES, 3, 'grant')}),
            'delete and add in one commit': (None, (), {GRANTS: dropped + 'grant\n'}),
            'a new file': (None, (), {'.claude/new.txt': 'grant\n'}),
            'a whole-file delete': (None, ('rm', '-q', GRANTS), None),
            'a rename': (None, ('mv', GRANTS, '.claude/new.txt'), None),
            'a mode change': (None, lambda: self.make_executable(GRANTS), {GRANTS: dropped}),
            'a binary file': ({GRANTS: 'a\0b\0c\n'}, (), {GRANTS: 'a\0b\n'}),
            'a path not on the list': ({'.claude/settings.json': 'x\ny\n'}, (),
                                       {'.claude/settings.json': 'x\n'}),
        }
        base = self.main
        for name, (first, git_args, files) in cases.items():
            with self.subTest(name):
                self.repo.git('reset', '-q', '--hard', base)
                self.main = self.commit_as(OWNER, 'owner', first) if first else base
                sha = self.agent(git_args, files)
                found = self.bad(sha)
                self.assertEqual([v.sha for v in found], [sha])
                self.assertTrue(found[0].paths)

    def test_a_delete_then_an_add_in_one_push_refuses_the_add(self):
        one = self.agent(files={GRANTS: LINES.replace('line 3\n', '')})
        two = self.agent(files={GRANTS: LINES.replace('line 3\n', '') + 'grant\n'})
        self.assertEqual(self.bad(two), [oc.Violation(two, AGENT, [GRANTS])])
        self.assertEqual(self.bad(one), [])

    def test_a_merge_that_only_deletes_lines_still_counts(self):
        here = self.repo.git('rev-parse', '--abbrev-ref', 'HEAD')
        self.repo.git('checkout', '-q', '-b', 'side')
        self.commit_as(OWNER, 'side work', {'a.py': 'a\n'})
        self.repo.git('checkout', '-q', here)
        self.repo.git('-c', 'user.email=%s' % AGENT, 'merge', '-q', '--no-ff', '--no-commit', 'side')
        sha = self.agent(files={GRANTS: LINES.replace('line 3\n', '')}, message='merge')
        self.assertEqual(len(self.repo.git('rev-list', '--parents', '-n', '1', sha).split()), 3)
        self.assertEqual(self.bad(sha), [oc.Violation(sha, AGENT, [GRANTS])])

    def test_a_git_failure_is_none_and_refuses(self):
        sha = self.agent(files={GRANTS: LINES.replace('line 3\n', '')})
        got, why = oc.deletes_only(self.repo.root, GRANTS, ['1' * 40, sha])
        self.assertIsNone(got)
        self.assertIn('git diff failed', why)
        with mock.patch.object(oc, 'deletes_only', return_value=(None, 'git diff failed: boom')):
            self.assertEqual(oc.violations(self.repo.root, self.rules, sha, [self.main]),
                             (None, 'git diff failed: boom'))


class NetChange(OwnerCommitsCase):
    """An owner-only path counts only when landing the tip on the target main would change it;
    when it would, every commit in the range that touches it must be the owner's."""

    def setUp(self):
        OwnerCommitsCase.setUp(self)
        self.main = self.commit_as(OWNER, 'owner adds CLAUDE.md', {'CLAUDE.md': 'owner\n'})

    def revert(self, sha):
        self.repo.git('-c', 'user.email=%s' % AGENT, 'revert', '--no-edit', sha)
        return self.repo.git('rev-parse', 'HEAD')

    def test_an_agent_change_then_revert_is_allowed(self):
        edit = self.commit_as(AGENT, 'agent edits CLAUDE.md', {'CLAUDE.md': 'agent\n', 'a.py': 'a\n'})
        tip = self.revert(edit)
        self.assertEqual(self.bad(tip), [])

    def test_a_kept_agent_change_is_refused_naming_every_commit_that_touched_it(self):
        edit = self.commit_as(AGENT, 'agent edits CLAUDE.md', {'CLAUDE.md': 'agent\n'})
        undo = self.revert(edit)
        again = self.commit_as(AGENT, 'agent edits it again', {'CLAUDE.md': 'kept\n'})
        tip = self.commit_as(AGENT, 'agent work', {'a.py': 'a\n'})
        self.assertEqual(sorted(v.sha for v in self.bad(tip)), sorted([edit, undo, again]))

    def test_a_rename_away_and_back_is_allowed(self):
        self.repo.git('mv', 'CLAUDE.md', 'notes.md')
        self.repo.git('-c', 'user.email=%s' % AGENT, 'commit', '-q', '-m', 'rename away')
        self.repo.git('mv', 'notes.md', 'CLAUDE.md')
        self.repo.git('-c', 'user.email=%s' % AGENT, 'commit', '-q', '-m', 'rename back')
        self.assertEqual(self.bad(self.repo.git('rev-parse', 'HEAD')), [])

    def test_a_kept_rename_away_is_refused(self):
        self.repo.git('mv', 'CLAUDE.md', 'notes.md')
        self.repo.git('-c', 'user.email=%s' % AGENT, 'commit', '-q', '-m', 'rename away')
        sha = self.repo.git('rev-parse', 'HEAD')
        self.assertEqual(self.bad(sha), [oc.Violation(sha, AGENT, ['CLAUDE.md'])])

    def test_with_no_target_every_touching_commit_is_judged(self):
        edit = self.commit_as(AGENT, 'agent edits CLAUDE.md', {'CLAUDE.md': 'agent\n'})
        tip = self.revert(edit)
        self.assertEqual(sorted(v.sha for v in self.bad(tip, exclude=[])), sorted([edit, tip]))


class NetChangeGitFailures(OwnerCommitsCase):
    """Every git failure inside net_change is `(None, why)`, never an empty set (which would pass):
    the real git on a revision it does not have, and a git that answers wrongly (a fake for one
    subcommand; every other call goes to the real git)."""

    def setUp(self):
        OwnerCommitsCase.setUp(self)
        self.tip = self.commit_as(AGENT, 'agent work', {'a.py': 'a\n'})
        self.real_call = oc.cmd.call

    def fake_git(self, subcommand, rc, out='', err=''):
        real = self.real_call
        calls = []

        def call(args, *a, **kw):
            if subcommand in args:
                calls.append(list(args))
                return oc.cmd.Result(rc, out.encode(), err)       # out bytes, err text, as cmd.call
            return real(args, *a, **kw)
        patcher = mock.patch.object(oc.cmd, 'call', call)
        patcher.start()
        self.addCleanup(patcher.stop)
        return calls

    def net(self, targets=None):
        return oc.net_change(self.repo.root, self.tip, [self.main] if targets is None else targets)

    def test_a_target_git_does_not_have_is_an_error_not_an_empty_net_change(self):
        found, why = self.net(['1' * 40])
        self.assertIsNone(found)
        self.assertIn('git merge-tree failed', why)

    def test_merge_tree_exiting_with_an_error_status_is_an_error(self):
        self.fake_git('merge-tree', 128, err='fatal: boom')
        found, why = self.net()
        self.assertIsNone(found)
        self.assertIn('git merge-tree failed', why)
        self.assertIn('rc 128', why)
        self.assertIn('fatal: boom', why)

    def test_git_that_cannot_run_or_times_out_is_an_error(self):
        for sub in ('merge-tree', 'diff'):
            with self.subTest(sub=sub):
                self.fake_git(sub, None, err='git %s timed out after 120 seconds' % sub)
                found, why = self.net()
                self.assertIsNone(found)
                self.assertIn('timed out', why)

    def test_merge_tree_printing_no_tree_is_an_error(self):
        for rc in (0, 1):
            with self.subTest(rc=rc):
                self.fake_git('merge-tree', rc, out='')
                found, why = self.net()
                self.assertIsNone(found)
                self.assertIn('no output', why)

    def test_merge_tree_printing_something_that_is_not_a_tree_id_is_an_error(self):
        self.fake_git('merge-tree', 0, out='not-a-tree\0a.py\0')
        found, why = self.net()
        self.assertIsNone(found)
        self.assertIn('git merge-tree failed', why)

    def test_a_failing_diff_against_the_merged_tree_is_an_error(self):
        calls = self.fake_git('diff', 128, err='fatal: bad object')
        found, why = self.net()
        self.assertEqual(calls[0][1], 'diff')                 # reached, after merge-tree succeeded
        self.assertIsNone(found)
        self.assertEqual(why, 'git diff failed: fatal: bad object')

    def test_a_failing_diff_without_stderr_names_its_exit_status(self):
        self.fake_git('diff', 3)
        self.assertEqual(self.net(), (None, 'git diff failed: exit 3'))

    def test_one_failing_target_among_several_fails_the_whole(self):
        found, why = self.net([self.main, '1' * 40])
        self.assertIsNone(found)
        self.assertIn('git merge-tree failed', why)

    def test_violations_refuses_when_the_net_change_cannot_be_read(self):
        self.fake_git('merge-tree', 128, err='fatal: boom')
        found, why = oc.violations(self.repo.root, self.rules, self.tip, [self.main])
        self.assertIsNone(found)
        self.assertIn('git merge-tree failed', why)


class PushWithOwnerMainCommits(OwnerCommitsCase):
    """docs/guard-hooks.md "Owner-only commits", Known limits, "One net change per push"."""

    def test_known_over_refusal_an_undone_agent_edit_counts_when_an_owner_commit_shares_the_push(self):
        # Pins a documented limit: if the over-refusal is ever fixed, update this test; it is not a regression.
        remote_main = self.main
        wt = self.repo.worktree('x')                          # the branch, forked at the remote main
        edit = self.commit_as(AGENT, 'agent edits CLAUDE.md', {'CLAUDE.md': 'agent\n'}, cwd=wt)
        self.repo.git('-c', 'user.email=%s' % AGENT, 'revert', '--no-edit', edit, cwd=wt)
        branch_tip = self.repo.git('rev-parse', 'HEAD', cwd=wt)
        self.assertEqual(self.bad(branch_tip, exclude=[remote_main]), [])     # alone: allowed
        self.commit_as(OWNER, 'owner edits CLAUDE.md', {'CLAUDE.md': 'owner\n'})   # unpushed, local main
        self.repo.git('-c', 'user.email=%s' % AGENT, 'merge', '-q', '--no-ff', '--no-edit', branch_tip)
        push = self.repo.git('rev-parse', 'HEAD')
        self.assertEqual(sorted(self.bad(push, exclude=[remote_main])),
                         sorted([oc.Violation(edit, AGENT, ['CLAUDE.md']),
                                 oc.Violation(branch_tip, AGENT, ['CLAUDE.md'])]))

    def test_pushing_the_owners_commits_first_avoids_the_over_refusal(self):
        wt = self.repo.worktree('x')
        edit = self.commit_as(AGENT, 'agent edits CLAUDE.md', {'CLAUDE.md': 'agent\n'}, cwd=wt)
        self.repo.git('-c', 'user.email=%s' % AGENT, 'revert', '--no-edit', edit, cwd=wt)
        branch_tip = self.repo.git('rev-parse', 'HEAD', cwd=wt)
        owner_edit = self.commit_as(OWNER, 'owner edits CLAUDE.md', {'CLAUDE.md': 'owner\n'})
        self.repo.git('-c', 'user.email=%s' % AGENT, 'merge', '-q', '--no-ff', '--no-edit', branch_tip)
        push = self.repo.git('rev-parse', 'HEAD')
        self.assertEqual(self.bad(push, exclude=[owner_edit]), [])


class MergeCase(OwnerCommitsCase):
    """A branch at self.main; main gains the owner's CLAUDE.md edit; the agent merges main in."""

    def setUp(self):
        OwnerCommitsCase.setUp(self)
        self.wt = self.repo.worktree('x')
        self.commit_as(AGENT, 'agent work', {'a.py': 'a\n'}, cwd=self.wt)
        self.owner_edit = self.commit_as(OWNER, 'owner edits CLAUDE.md', {'CLAUDE.md': 'owner\n'})

    def merge_main(self, *extra):
        self.repo.git('-c', 'user.email=%s' % AGENT, 'merge', '-q', '--no-ff', '--no-edit', 'main',
                      *extra, cwd=self.wt, check=not extra)
        return self.repo.git('rev-parse', 'HEAD', cwd=self.wt)

    def resolve(self, text=None, executable=False):
        """Merge main into the branch, set CLAUDE.md to `text` (None: delete it), commit as the
        agent; returns the merge."""
        self.merge_main('--no-commit')
        if text is None:
            self.repo.git('rm', '-q', '-f', '--ignore-unmatch', '--', 'CLAUDE.md', cwd=self.wt)
        else:
            self.repo.write('CLAUDE.md', text, root=self.wt)
            self.repo.git('add', 'CLAUDE.md', cwd=self.wt)
        if executable:
            self.repo.git('update-index', '--chmod=+x', 'CLAUDE.md', cwd=self.wt)
        self.repo.git('-c', 'user.email=%s' % AGENT, 'commit', '-q', '--no-edit', cwd=self.wt)
        return self.repo.git('rev-parse', 'HEAD', cwd=self.wt)


class Merges(MergeCase):
    def test_a_clean_merge_changes_nothing_of_its_own(self):
        merge = self.merge_main()
        self.assertEqual(self.bad(merge), [])

    def test_an_evil_merge_is_judged_by_its_author(self):
        self.repo.git('-c', 'user.email=%s' % AGENT, 'merge', '-q', '--no-ff', '--no-commit', 'main',
                      cwd=self.wt)
        self.repo.write('CLAUDE.md', 'agent rewrote it in the merge\n', root=self.wt)
        self.repo.git('add', 'CLAUDE.md', cwd=self.wt)
        self.repo.git('-c', 'user.email=%s' % AGENT, 'commit', '-q', '--no-edit', cwd=self.wt)
        merge = self.repo.git('rev-parse', 'HEAD', cwd=self.wt)
        self.assertEqual(self.bad(merge), [oc.Violation(merge, AGENT, ['CLAUDE.md'])])

    def test_a_conflict_resolved_in_an_owner_only_path_is_judged_by_its_author(self):
        self.commit_as(AGENT, 'agent edits CLAUDE.md too', {'CLAUDE.md': 'agent\n'}, cwd=self.wt)
        branch_edit = self.repo.git('rev-parse', 'HEAD', cwd=self.wt)
        self.repo.git('-c', 'user.email=%s' % AGENT, 'merge', '-q', '--no-ff', '--no-edit', 'main',
                      cwd=self.wt, check=False)
        self.repo.write('CLAUDE.md', 'owner\n', root=self.wt)       # resolved to the owner's text
        self.repo.git('add', 'CLAUDE.md', cwd=self.wt)
        self.repo.git('-c', 'user.email=%s' % AGENT, 'commit', '-q', '--no-edit', cwd=self.wt)
        merge = self.repo.git('rev-parse', 'HEAD', cwd=self.wt)
        found = {v.sha: v.paths for v in self.bad(merge)}
        self.assertEqual(found, {merge: ['CLAUDE.md'], branch_edit: ['CLAUDE.md']})

    def test_an_owner_change_through_a_main_merge_does_not_count_against_the_branch(self):
        edit = self.commit_as(AGENT, 'agent edits CLAUDE.md', {'CLAUDE.md': 'agent\n'}, cwd=self.wt)
        self.repo.git('-c', 'user.email=%s' % AGENT, 'revert', '--no-edit', edit, cwd=self.wt)
        merge = self.merge_main()
        self.assertEqual(self.bad(merge, exclude=[self.owner_edit]), [])

    def test_a_conflict_resolved_to_mains_text_changes_nothing_on_that_main(self):
        self.commit_as(AGENT, 'agent edits CLAUDE.md too', {'CLAUDE.md': 'agent\n'}, cwd=self.wt)
        self.merge_main('--no-commit')
        self.repo.write('CLAUDE.md', 'owner\n', root=self.wt)
        self.repo.git('add', 'CLAUDE.md', cwd=self.wt)
        self.repo.git('-c', 'user.email=%s' % AGENT, 'commit', '-q', '--no-edit', cwd=self.wt)
        merge = self.repo.git('rev-parse', 'HEAD', cwd=self.wt)
        self.assertEqual(self.bad(merge, exclude=[self.owner_edit]), [])

    def test_a_branch_behind_an_owner_change_is_judged_by_what_it_would_land(self):
        edit = self.commit_as(AGENT, 'agent edits CLAUDE.md', {'CLAUDE.md': 'agent\n'}, cwd=self.wt)
        self.repo.git('-c', 'user.email=%s' % AGENT, 'revert', '--no-edit', edit, cwd=self.wt)
        tip = self.repo.git('rev-parse', 'HEAD', cwd=self.wt)
        self.assertEqual(self.bad(tip, exclude=[self.owner_edit]), [])

    def test_a_kept_change_that_conflicts_with_main_is_refused(self):
        edit = self.commit_as(AGENT, 'agent edits CLAUDE.md', {'CLAUDE.md': 'agent\n'}, cwd=self.wt)
        self.assertEqual(self.bad(edit, exclude=[self.owner_edit]),
                         [oc.Violation(edit, AGENT, ['CLAUDE.md'])])

    def test_a_sync_merge_resolved_to_landed_mains_copy_passes(self):
        # Seen in a game repository: both sides changed the path with the owner's approval, the agent's
        # merge took main's copy, and a later approved branch commit keeps the path in the net.
        self.commit_as(OWNER, 'owner edits CLAUDE.md on the branch', {'CLAUDE.md': 'branch\n'},
                       cwd=self.wt)
        merge = self.resolve('owner\n')
        tip = self.commit_as(OWNER, 'owner edits it after the sync', {'CLAUDE.md': 'owner\nmore\n'},
                             cwd=self.wt)
        self.assertEqual(self.bad(tip, exclude=[self.owner_edit]), [])
        self.assertEqual([v.sha for v in self.bad(tip, exclude=[])], [merge])    # no target

    def test_a_failing_diff_against_a_target_is_an_error_not_a_pass(self):
        self.commit_as(OWNER, 'owner edits CLAUDE.md on the branch', {'CLAUDE.md': 'branch\n'},
                       cwd=self.wt)
        merge = self.resolve('owner\n')
        tip = self.commit_as(OWNER, 'owner edits it after the sync', {'CLAUDE.md': 'owner\nmore\n'},
                             cwd=self.wt)
        real, calls = oc.cmd.call, []

        def call(args, *a, **kw):
            if 'diff' in args and list(args[-2:]) == [self.owner_edit, merge]:
                calls.append(list(args))
                return oc.cmd.Result(128, b'', 'fatal: bad object')
            return real(args, *a, **kw)
        with mock.patch.object(oc.cmd, 'call', call):
            self.assertEqual(oc.changed(self.repo.root, merge, [self.repo.git('rev-parse',
                             merge + '^1'), self.owner_edit], targets=[self.owner_edit]),
                             (None, 'git diff failed: fatal: bad object'))
            found, why = oc.violations(self.repo.root, self.rules, tip, [self.owner_edit])
        self.assertTrue(calls)                          # the diff against the target was reached
        self.assertIsNone(found)
        self.assertEqual(why, 'git diff failed: fatal: bad object')

    def test_a_sync_merge_whose_resolution_edits_the_path_is_refused(self):
        self.commit_as(OWNER, 'owner edits CLAUDE.md on the branch', {'CLAUDE.md': 'branch\n'},
                       cwd=self.wt)
        merge = self.resolve('the agent merged both\n')
        tip = self.commit_as(OWNER, 'owner edits it after the sync', {'CLAUDE.md': 'owner\nmore\n'},
                             cwd=self.wt)
        self.assertEqual(self.bad(tip, exclude=[self.owner_edit]),
                         [oc.Violation(merge, AGENT, ['CLAUDE.md'])])

    def test_a_merge_matching_only_the_branch_parent_launders_nothing(self):
        branch_edit = self.commit_as(AGENT, 'agent edits CLAUDE.md', {'CLAUDE.md': 'agent\n'},
                                     cwd=self.wt)
        merge = self.resolve('agent\n')
        found = {v.sha: v.paths for v in self.bad(merge, exclude=[self.owner_edit])}
        self.assertEqual(found, {merge: ['CLAUDE.md'], branch_edit: ['CLAUDE.md']})

    def test_an_owner_merge_is_not(self):
        self.repo.git('-c', 'user.email=%s' % OWNER, 'merge', '-q', '--no-ff', '--no-commit', 'main',
                      cwd=self.wt)
        self.repo.write('CLAUDE.md', 'owner again\n', root=self.wt)
        self.repo.git('add', 'CLAUDE.md', cwd=self.wt)
        self.repo.git('-c', 'user.email=%s' % OWNER, 'commit', '-q', '--no-edit', cwd=self.wt)
        merge = self.repo.git('rev-parse', 'HEAD', cwd=self.wt)
        self.assertEqual(self.bad(merge), [])


@real_git('a modify/delete conflict, git rm -f of a staged path, update-index --chmod and '
          'commit-tree: the fake has none')
class MergesOnRealGit(MergeCase):
    def test_a_sync_merge_resolved_to_a_landed_delete_passes(self):
        self.merge_main()
        self.commit_as(OWNER, 'owner edits CLAUDE.md on the branch', {'CLAUDE.md': 'branch\n'},
                       cwd=self.wt)
        self.repo.git('-c', 'user.email=%s' % OWNER, 'rm', '-q', 'CLAUDE.md')
        self.repo.git('-c', 'user.email=%s' % OWNER, 'commit', '-q', '-m', 'owner deletes it')
        deleted = self.repo.git('rev-parse', 'HEAD')
        self.resolve(None)
        tip = self.commit_as(OWNER, 'owner adds it back on the branch', {'CLAUDE.md': 'back\n'},
                             cwd=self.wt)
        self.assertEqual(self.bad(tip, exclude=[deleted]), [])

    def test_a_merge_holding_old_mains_copy_through_an_old_main_parent_is_refused(self):
        # main is initial -> owner_edit; the branch has owner_edit; a merge made with plumbing has
        # the branch and the old main (initial) as parents and initial's copy (no CLAUDE.md).
        branch = self.merge_main()
        self.repo.git('rm', '-q', 'CLAUDE.md', cwd=self.wt)
        tree = self.repo.git('write-tree', cwd=self.wt)
        merge = self.repo.git('-c', 'user.email=%s' % AGENT, 'commit-tree', tree, '-p', branch,
                              '-p', self.main, '-m', 'revert the owner through an old main parent',
                              cwd=self.wt)
        self.assertEqual(self.bad(merge, exclude=[self.owner_edit]),
                         [oc.Violation(merge, AGENT, ['CLAUDE.md'])])

    def test_a_merge_resolved_to_an_older_landed_tips_copy_is_refused(self):
        self.merge_main()                       # the branch holds the owner's CLAUDE.md
        other = self.repo.path('.claude', 'worktrees', 's')
        self.repo.git('worktree', 'add', '-q', other, '-b', 'kit/s', self.main)   # before the owner
        older = self.commit_as(AGENT, 'an earlier branch', {'s.txt': 's\n'}, cwd=other)
        self.repo.git('-c', 'user.email=%s' % OWNER, 'merge', '-q', '--no-ff', '--no-edit', 'kit/s')
        landed = self.repo.git('rev-parse', 'HEAD')
        self.repo.git('-c', 'user.email=%s' % AGENT, 'merge', '-q', '--no-ff', '--no-commit', older,
                      cwd=self.wt)
        self.repo.git('rm', '-q', '-f', 'CLAUDE.md', cwd=self.wt)     # resolved to the older copy
        self.repo.git('-c', 'user.email=%s' % AGENT, 'commit', '-q', '--no-edit', cwd=self.wt)
        merge = self.repo.git('rev-parse', 'HEAD', cwd=self.wt)
        self.assertEqual(self.bad(merge, exclude=[landed]),
                         [oc.Violation(merge, AGENT, ['CLAUDE.md'])])

    def test_a_merge_that_drops_mains_owner_change_is_refused(self):
        merge = self.resolve(None)          # the branch side has no CLAUDE.md: main's add dropped
        self.assertEqual(self.bad(merge, exclude=[self.owner_edit]),
                         [oc.Violation(merge, AGENT, ['CLAUDE.md'])])

    def test_a_sync_merge_that_changes_the_mode_is_refused(self):
        self.commit_as(OWNER, 'owner edits CLAUDE.md on the branch', {'CLAUDE.md': 'branch\n'},
                       cwd=self.wt)
        merge = self.resolve('owner\n', executable=True)
        self.assertEqual(self.bad(merge, exclude=[self.owner_edit]),
                         [oc.Violation(merge, AGENT, ['CLAUDE.md'])])


SIX = b''.join(b'line %d\n' % n for n in range(1, 7))


def drop(text, *numbers):
    """`text` (bytes) without its lines `numbers` (1-based)."""
    rows = text.splitlines(keepends=True)
    return b''.join(r for n, r in enumerate(rows, 1) if n not in numbers)


class CombinesDeletions(unittest.TestCase):
    """oc.combines_deletions: a merge's copy that only joins the deletions both sides made from
    the base (ours drops line 2, theirs line 5, unless a case says otherwise)."""

    O, T = drop(SIX, 2), drop(SIX, 5)
    BOTH = drop(SIX, 2, 5)
    CRLF = SIX.replace(b'\n', b'\r\n')
    CASES = [
        # (name, ours, theirs, result, bases, combines)
        ("both sides' deletions", O, T, BOTH, [SIX], True),
        ('the same deletion on both sides', O, O, O, [SIX], True),
        ('CRLF lines deleted', drop(CRLF, 2), drop(CRLF, 5), drop(CRLF, 2, 5), [CRLF], True),
        ('one copy of a duplicate deleted', b'a\nb\n', b'a\na\n', b'a\n', [b'a\na\nb\n'], True),
        ('a line one side deleted kept', O, T, T, [SIX], False),
        ('a new line', O, T, BOTH + b'new\n', [SIX], False),
        ('a line moved', O, T, drop(SIX, 2, 3, 5).replace(b'line 4\n', b'line 4\nline 3\n'),
         [SIX], False),
        ('a line both kept dropped', O, T, drop(SIX, 2, 3, 5), [SIX], False),
        ("both copies of a duplicate dropped", b'a\nb\n', b'a\na\n', b'', [b'a\na\nb\n'], False),
        ("one side's addition dropped", SIX + b'added\n', T, T, [SIX], False),
        ('an addition against one base of two', SIX + b'added\n', SIX, SIX,
         [SIX + b'added\n', SIX], False),
        ('a changed line end is a change', O, T, BOTH.replace(b'line 1\n', b'line 1\r\n'), [SIX],
         False),
        ('the last line end dropped', O, T, BOTH[:-1], [SIX], False),
        ('a NUL byte', O + b'\0', T + b'\0', BOTH + b'\0', [SIX + b'\0'], False),
        ('no base', O, T, BOTH, [], False),
    ]

    def test_the_table(self):
        for name, ours, theirs, result, bases, combines in self.CASES:
            with self.subTest(name):
                self.assertIs(oc.combines_deletions(ours, theirs, result, bases), combines)


@real_git('commit-tree for a forged parent and an octopus merge: the fake has none')
class DeletionMerges(OwnerCommitsCase):
    """oc.violations and a two-parent commit whose owner-only copy only joins the deletions both
    sides made (deletion_merge): the merge is not a violation; any other resolution is, and every
    commit on either side is still judged on its own."""

    def setUp(self):
        OwnerCommitsCase.setUp(self)
        self.base = self.commit_as(OWNER, 'base', {'CLAUDE.md': LINES})
        self.wt = self.repo.worktree('x')
        self.ours = self.commit_as(OWNER, 'branch drops line 3', {'CLAUDE.md': self.without(3)},
                                   cwd=self.wt)
        self.main = self.commit_as(OWNER, 'main drops line 4', {'CLAUDE.md': self.without(4)})

    @staticmethod
    def without(*numbers):
        return drop(LINES.encode(), *numbers).decode()

    def resolve(self, text):
        """Merge main into the branch (CLAUDE.md conflicts), resolve it to `text`, commit as the
        agent; returns the merge."""
        self.repo.git('-c', 'user.email=%s' % AGENT, 'merge', '-q', '--no-ff', '--no-commit',
                      'main', cwd=self.wt, check=False)
        self.assertIn('CLAUDE.md', self.repo.git('diff', '--name-only', '--diff-filter=U',
                                                 cwd=self.wt))
        self.repo.write('CLAUDE.md', text, root=self.wt)
        self.repo.git('add', 'CLAUDE.md', cwd=self.wt)
        self.repo.git('-c', 'user.email=%s' % AGENT, 'commit', '-q', '--no-edit', cwd=self.wt)
        return self.repo.git('rev-parse', 'HEAD', cwd=self.wt)

    def forge(self, text, *parents):
        """An agent's commit of CLAUDE.md = `text` with `parents`, made with commit-tree."""
        self.repo.write('CLAUDE.md', text, root=self.wt)
        self.repo.git('add', 'CLAUDE.md', cwd=self.wt)
        tree = self.repo.git('write-tree', cwd=self.wt)
        args = []
        for p in parents:
            args += ['-p', p]
        return self.repo.git('-c', 'user.email=%s' % AGENT, 'commit-tree', tree, *args, '-m',
                             'forged', cwd=self.wt)

    def test_a_merge_that_only_joins_both_deletions_is_not_a_violation(self):
        merge = self.resolve(self.without(3, 4))
        self.assertEqual(self.bad(merge), [])
        self.assertEqual(oc.changed(self.repo.root, merge, [self.ours, self.main])[0],
                         ['CLAUDE.md'])               # the merge does change it: the rule passes it

    def test_any_other_resolution_is_a_violation(self):
        # (A copy equal to main's is a main sync, not counted on that main: Merges.)
        for name, text in [('a deleted line kept', self.without(3)),
                           ('a line added', self.without(3, 4) + 'agent\n'),
                           ('a line both kept dropped', self.without(3, 4, 5))]:
            with self.subTest(name):
                self.repo.git('reset', '-q', '--hard', self.ours, cwd=self.wt)
                merge = self.resolve(text)
                self.assertEqual(self.bad(merge), [oc.Violation(merge, AGENT, ['CLAUDE.md'])])

    def test_a_forged_parent_is_judged_on_its_own(self):
        forged = self.forge(self.without(1, 2, 3), self.base)
        merge = self.forge(self.without(1, 2, 3, 4), forged, self.main)
        self.assertEqual(self.bad(merge), [oc.Violation(forged, AGENT, ['CLAUDE.md'])])

    def test_an_octopus_merge_is_a_violation(self):
        other = self.forge(self.without(3), self.base)
        merge = self.forge(self.without(3, 4), self.ours, self.main, other)
        self.assertIn(oc.Violation(merge, AGENT, ['CLAUDE.md']), self.bad(merge))

    def test_a_git_failure_is_an_error_not_a_pass(self):
        merge = self.resolve(self.without(3, 4))
        real = oc.cmd.call
        for verb, said in [('cat-file', 'fatal: bad object'),
                           ('merge-base', 'git merge-base --all failed'),
                           ('ls-tree', 'git ls-tree could not list CLAUDE.md')]:
            def call(args, *a, **kw):
                if verb in args and '-r' not in args and '--is-ancestor' not in args:
                    return oc.cmd.Result(128, b'', 'fatal: bad object')
                return real(args, *a, **kw)
            with self.subTest(verb), mock.patch.object(oc.cmd, 'call', call):
                found, why = oc.violations(self.repo.root, self.rules, merge, [self.main])
                self.assertIsNone(found)
                self.assertIn(said, why)


class MergeTreePieces(unittest.TestCase):
    """The merge-tree parse and merge_owned on their own (moved from tests/commit)."""

    def test_parse_merge_tree_layouts(self):
        oid = '1' * 40
        self.assertEqual(oc.parse_merge_tree(oid + '\0p.txt\0'), (oid, {'p.txt'}))
        self.assertEqual(oc.parse_merge_tree(oid + '\np.txt\0'), (oid, {'p.txt'}))
        self.assertEqual(oc.parse_merge_tree(oid + '\n'), (oid, set()))
        self.assertEqual(oc.parse_merge_tree(oid + '\0p.txt\0 q dir/r.txt\0'),
                         (oid, {'p.txt', ' q dir/r.txt'}))

    def test_no_merge_or_an_octopus_owns_nothing(self):
        self.assertEqual(oc.merge_owned('.', owner.Rules(), {'a.txt'}, []), (set(), None))
        self.assertEqual(oc.merge_owned('.', owner.Rules(), {'a.txt'}, ['a' * 40, 'b' * 40]),
                         (set(), None))

    def test_a_merge_tree_failure_reason_reaches_the_refusal(self):
        def fake(args, cwd, **kw):
            if args[0] == 'merge-tree':
                return cmd.Result(128, b'', 'fatal: merge-tree stubbed to fail')
            return cmd.Result(0, b'', '')
        with mock.patch.object(oc.cmd, 'call', fake):
            owned, why = oc.merge_owned('.', owner.Rules(), {'other.txt'}, ['f' * 40])
        self.assertEqual(owned, set())
        self.assertIn('merge-tree stubbed to fail', why)
        text = C.foreign_refusal({'other.txt'}, why, True)
        self.assertTrue(text.startswith('refused: already staged'), text)
        self.assertIn('merge-tree stubbed to fail', text)


class StandInHub(object):
    """The approvals seam of violations(): it answers as the hub server's verify route would
    (approvals.check on the documents it holds, under KEY, for REPO unless told another), and
    keeps every question it was asked."""

    def __init__(self, repo=REPO):
        self.docs, self.repo, self.asked = {}, repo, []

    def check(self, root, doc_id, head, blobs, commit):
        self.asked.append((doc_id, head, blobs, commit))
        doc = self.docs.get(doc_id)
        if doc is None:
            return False, ap.WHY_UNKNOWN
        return ap.check(KEY, doc_id, doc, self.repo, head, blobs, NOW, commit)


class VerifyRoute(object):
    """A stand-in hub client whose verify route runs approvals.check, as kitlib.hubsite.api's,
    and whose snapshot holds the hub's documents. `asked`: the path of each call."""

    def __init__(self, hub):
        self.hub, self.asked = hub, []

    def get(self, path, role=None, query=None):
        self.asked.append(path)
        if path == '/snapshot':
            return {'ok': True, 'collections': {'approvals': json.loads(json.dumps(
                self.hub.docs))}}
        doc_id = path.split('/')[2]
        valid, why = self.hub.check(None, doc_id, query['head'], json.loads(query['files']),
                                    query.get('commit'))
        if query['repo'] != REPO:
            valid, why = False, ap.WHY_REPO
        return {'ok': True, 'valid': valid, 'why': why}


class Down(object):
    def get(self, *a, **kw):
        raise huberrors.NotRunning(1)


def approval_message(subject, *values):
    return '%s\n\n%s' % (subject, '\n'.join('Owner-Approval: %s' % v for v in values))


class ApprovalsCase(OwnerCommitsCase):
    """main holds the owner's CLAUDE.md; approvals are signed into a stand-in hub."""

    def setUp(self):
        OwnerCommitsCase.setUp(self)
        self.main = self.commit_as(OWNER, 'owner adds CLAUDE.md', {'CLAUDE.md': 'owner\n'})
        self.hub = StandInHub()

    def blob_at(self, rev, path):
        out = self.repo.git('rev-parse', '--verify', '-q', '%s:%s' % (rev, path), check=False)
        return out or None

    def approve(self, files, head=None, doc_id='A-001'):
        head = head or self.main
        doc = ap.new_doc(REPO, 'owner-only edit', head, [
            {'path': p, 'base_blob': self.blob_at(head, p), 'content': c}
            for p, c in sorted(files.items())], NOW)
        doc.update(ap.decide_fields(KEY, doc_id, doc, ap.APPROVE, NOW))
        self.hub.docs[doc_id] = doc
        return doc_id

    def consume(self, doc_id, sha):
        doc = self.hub.docs[doc_id]
        doc.update(ap.consume_fields(KEY, doc_id, doc, sha, NOW))

    def judge(self, tip, approvals=None):
        found, why = oc.violations(self.repo.root, self.rules, tip, [self.main],
                                   approvals=approvals or self.hub)
        self.assertIsNone(why)
        return found

    def approved_commit(self, files=None, values=('hub:A-001',), consume=True):
        files = files or {'CLAUDE.md': 'approved\n'}
        doc_id = self.approve({'CLAUDE.md': 'approved\n'})
        sha = self.commit_as(AGENT, approval_message('owner-approved edit', *values), files)
        if consume:
            self.consume(doc_id, sha)
        return sha


class OwnerApprovals(ApprovalsCase):
    """An agent commit of an owner-only path counts as the owner's when its Owner-Approval:
    hub:A-<n> trailer names an approval the hub verifies for exactly that commit: its parent as
    the base, every path it changes with the approved blob, and consumed by it."""

    def test_an_approved_commit_that_consumed_its_approval_is_not_a_violation(self):
        sha = self.approved_commit()
        self.assertEqual(self.judge(sha), [])
        self.assertEqual(self.hub.asked, [('A-001', self.main, {
            'CLAUDE.md': ap.blob_id('approved\n')}, sha)])

    def test_an_approved_delete_is_not_a_violation(self):
        doc_id = self.approve({'CLAUDE.md': None})
        self.repo.git('rm', '-q', 'CLAUDE.md')
        self.repo.git('-c', 'user.email=%s' % AGENT, 'commit', '-q', '-m',
                      approval_message('owner-approved delete', 'hub:A-001'))
        sha = self.repo.git('rev-parse', 'HEAD')
        self.consume(doc_id, sha)
        self.assertEqual(self.judge(sha), [])

    def test_an_approval_not_consumed_is_refused(self):
        sha = self.approved_commit(consume=False)
        found = self.judge(sha)
        self.assertEqual([(v.sha, v.paths) for v in found], [(sha, ['CLAUDE.md'])])
        self.assertIn('owner approval hub:A-001 refused: %s' % ap.WHY_COMMIT, found[0].why)

    def test_a_rebuild_of_an_approval_another_commit_consumed_is_not_a_violation(self):
        # No exact binding: the same change made again (a rebuilt branch) still holds until it
        # is on main (DeltaApprovals: spent) or the approval expires.
        doc_id = self.approve({'CLAUDE.md': 'approved\n'})
        first = self.commit_as(AGENT, approval_message('first', 'hub:A-001'),
                               {'CLAUDE.md': 'approved\n'})
        self.consume(doc_id, first)
        self.repo.git('reset', '-q', '--hard', self.main)       # the same change, made again
        again = self.commit_as(AGENT, approval_message('again', 'hub:A-001'),
                               {'CLAUDE.md': 'approved\n'})
        self.assertEqual(self.judge(first), [])
        self.assertEqual(self.judge(again), [])

    def test_other_content_than_the_approved_is_refused(self):
        sha = self.approved_commit(files={'CLAUDE.md': 'something else\n'})
        self.assertIn(ap.WHY_FILES, self.judge(sha)[0].why)

    def test_an_extra_path_in_the_approved_commit_is_refused(self):
        sha = self.approved_commit(files={'CLAUDE.md': 'approved\n', 'a.py': 'a\n'})
        found = self.judge(sha)
        self.assertEqual(found[0].paths, ['CLAUDE.md'])
        self.assertIn(ap.WHY_FILES, found[0].why)
        self.assertEqual(sorted(self.hub.asked[0][2]), ['CLAUDE.md', 'a.py'])

    def test_the_server_alone_refuses_a_commit_on_another_base(self):
        # The stand-in seam is the server's check, which compares the base exactly; another
        # parent passes only through HubApprovals, judged by its delta (DeltaApprovals).
        self.commit_as(AGENT, 'agent work first', {'a.py': 'a\n'})
        sha = self.approved_commit()
        self.assertIn(ap.WHY_HEAD, self.judge(sha)[0].why)

    def test_an_approval_of_another_repository_is_refused(self):
        sha = self.approved_commit()
        self.hub.repo = 'git.example.invalid/owner/other'
        self.assertIn(ap.WHY_REPO, self.judge(sha)[0].why)

    def test_an_unknown_approval_is_refused(self):
        sha = self.approved_commit(values=('hub:A-009',), consume=False)
        self.assertIn(ap.WHY_UNKNOWN, self.judge(sha)[0].why)

    def test_a_commit_without_the_trailer_is_judged_by_its_author_and_the_hub_is_not_asked(self):
        sha = self.commit_as(AGENT, 'agent edits CLAUDE.md', {'CLAUDE.md': 'x\n'})
        self.assertEqual(self.judge(sha), [oc.Violation(sha, AGENT, ['CLAUDE.md'])])
        self.assertEqual(self.hub.asked, [])

    def test_a_value_that_is_not_one_hub_approval_is_refused_unasked(self):
        for values in (('A-001',), ('hub:B-1',), ('hub:A-001', 'hub:A-002'), ('hub:A-001 extra',),
                       ('hub:A-1',), ('hub:A-01',), ('hub:A-000',)):
            with self.subTest(values=values):
                self.repo.git('reset', '-q', '--hard', self.main)
                sha = self.commit_as(AGENT, approval_message('edit', *values),
                                     {'CLAUDE.md': 'approved\n'})
                found = self.judge(sha)
                self.assertEqual([v.sha for v in found], [sha])
                self.assertIn('is not one hub:A-<n>', found[0].why)
        self.assertEqual(self.hub.asked, [])

    def test_the_ids_the_hub_issues_are_read(self):
        for value, did in (('hub:A-001', 'A-001'), ('hub:A-999', 'A-999'),
                           ('hub:A-1000', 'A-1000')):
            with self.subTest(value=value):
                found = oc.APPROVAL_RE.match(value)
                self.assertIsNotNone(found)
                self.assertEqual(found.group(1), did)

    def test_a_merge_cannot_carry_an_approval(self):
        wt = self.repo.worktree('x')
        self.commit_as(AGENT, 'branch work', {'a.py': 'a\n'}, cwd=wt)
        self.commit_as(OWNER, 'owner work on main', {'b.txt': 'b\n'})
        self.repo.git('-c', 'user.email=%s' % AGENT, 'merge', '-q', '--no-ff', '--no-commit',
                      'main', cwd=wt)
        self.repo.write('CLAUDE.md', 'approved\n', root=wt)
        self.repo.git('add', 'CLAUDE.md', cwd=wt)
        self.repo.git('-c', 'user.email=%s' % AGENT, 'commit', '-q', '-m',
                      approval_message('evil merge', 'hub:A-001'), cwd=wt)
        merge = self.repo.git('rev-parse', 'HEAD', cwd=wt)
        found = self.judge(merge)
        self.assertIn('a merge cannot carry an owner approval', found[0].why)
        self.assertEqual(self.hub.asked, [])

    def test_the_hub_not_running_is_a_refusal_not_a_pass(self):
        sha = self.approved_commit()
        found = self.judge(sha, approvals=oc.HubApprovals(client=Down(), repo=REPO, main=MAIN))
        self.assertIn(ap.WHY_SERVER % 'NotRunning', found[0].why)

    def test_the_hub_seam_asks_the_verify_route_with_the_commit(self):
        sha = self.approved_commit()
        seam = oc.HubApprovals(client=VerifyRoute(self.hub), repo=REPO, main=MAIN)
        self.assertEqual(self.judge(sha, approvals=seam), [])
        self.assertEqual(self.hub.asked[-1][3], sha)
        self.hub.docs['A-001']['commit'] = '0' * 40     # consumed by another: the sig still holds
        seam = oc.HubApprovals(client=VerifyRoute(self.hub), repo=REPO, main=MAIN)
        self.assertEqual(self.judge(sha, approvals=seam), [])
        self.hub.docs['A-001']['status'] = ap.APPROVED  # never consumed: refused
        seam = oc.HubApprovals(client=VerifyRoute(self.hub), repo=REPO, main=MAIN)
        self.assertIn(ap.WHY_COMMIT, self.judge(sha, approvals=seam)[0].why)

    def test_the_default_seam_is_the_hub(self):
        self.assertIsInstance(oc.default_approvals(), oc.HubApprovals)

    def test_describe_names_why_an_approval_did_not_count(self):
        sha = self.approved_commit(consume=False)
        line = oc.describe(self.rules, self.judge(sha))
        self.assertIn('%s by %s changes CLAUDE.md (owner approval hub:A-001 refused: %s)'
                      % (sha[:10], AGENT, ap.WHY_COMMIT), line)

    def test_a_root_commit_cannot_carry_an_approval(self):
        self.repo.git('checkout', '-q', '--orphan', 'other')
        self.repo.git('rm', '-q', '-r', '--cached', '.')
        sha = self.commit_as(AGENT, approval_message('root', 'hub:A-001'),
                             {'CLAUDE.md': 'approved\n'})
        found, why = oc.violations(self.repo.root, self.rules, sha, approvals=self.hub)
        self.assertIsNone(why)
        self.assertEqual([v.sha for v in found], [sha])
        self.assertIn('a root commit has no approved base', found[0].why)
        self.assertEqual(self.hub.asked, [])


class DeltaCase(ApprovalsCase):
    """main (the approval's base B0) holds the owner's CLAUDE.md as twelve lines and AGENTS.md;
    the seam is HubApprovals on a stand-in hub client, with main as last fetched at MAIN."""

    def setUp(self):
        ApprovalsCase.setUp(self)
        self.main = self.commit_as(OWNER, 'owner rules', {'CLAUDE.md': LINES,
                                                          'AGENTS.md': 'agents\n'})
        self.new = edit(LINES, 2, 'two')

    def seam(self, main=MAIN):
        self.route = VerifyRoute(self.hub)
        return oc.HubApprovals(client=self.route, repo=REPO, main=main)

    def agent_commit(self, files, deleted=(), doc_id='A-001', consume=True,
                     subject='owner-approved edit'):
        """An agent commit with `Owner-Approval: hub:<doc_id>` that writes `files` and removes
        `deleted`, consumed by the hub unless told not to (or consumed already)."""
        for rel, text in files.items():
            self.repo.write(rel, text)
        if files:
            self.repo.git('add', '--', *files)
        if deleted:
            self.repo.git('rm', '-q', '--', *deleted)
        self.repo.git('-c', 'user.email=%s' % AGENT, 'commit', '-q', '-m',
                      approval_message(subject, 'hub:%s' % doc_id))
        sha = self.repo.git('rev-parse', 'HEAD')
        if consume and not self.hub.docs[doc_id].get('commit'):
            self.consume(doc_id, sha)
        return sha

    def refused(self, sha, *parts, main=MAIN):
        found = self.judge(sha, approvals=self.seam(main))
        self.assertEqual([v.sha for v in found], [sha])
        self.assertIn('owner approval hub:A-001 refused: ', found[0].why)
        for part in parts:
            self.assertIn(part, found[0].why)
        return found[0].why


class DeltaApprovals(DeltaCase):
    """Through HubApprovals (the hub's seam) the approval is a delta: the commit's parent P may be
    any commit, each approved file at the commit must be what the approved change gives on P
    (approvals.delta_result), the commit changes no other path, and once a commit carrying the
    approval is on main the approval is spent. Then the hub verifies the signed base and blobs.
    None of these needs a merge: P holds each file as the base or the approved content."""

    def test_the_approved_change_on_its_base_is_not_a_violation(self):
        self.approve({'CLAUDE.md': self.new})
        sha = self.agent_commit({'CLAUDE.md': self.new})
        self.assertEqual(self.judge(sha, approvals=self.seam()), [])
        self.assertEqual(self.route.asked, ['/snapshot', '/approvals/A-001/verify'])
        self.assertEqual(self.hub.asked, [('A-001', self.main, {
            'CLAUDE.md': ap.blob_id(self.new)}, sha)])

    def test_a_rebuild_on_a_base_that_moved_on_elsewhere_is_not_a_violation(self):
        did = self.approve({'CLAUDE.md': self.new})
        self.consume(did, '0' * 40)                     # an earlier build of the branch used it
        self.commit_as(OWNER, 'main moves on', {'a.py': 'a\n'})
        sha = self.agent_commit({'CLAUDE.md': self.new})
        self.assertEqual(self.judge(sha, approvals=self.seam()), [])
        self.assertEqual(self.hub.asked[-1][1], self.main)     # verified for the signed base

    def test_a_base_on_another_line_of_history_is_judged_by_its_delta(self):
        self.repo.git('checkout', '-q', '-b', 'side')
        side = self.commit_as(OWNER, 'another line', {'b.txt': 'b\n'})
        self.repo.git('checkout', '-q', 'main')
        self.approve({'CLAUDE.md': self.new}, head=side)
        sha = self.agent_commit({'CLAUDE.md': self.new})
        self.assertEqual(self.judge(sha, approvals=self.seam()), [])
        self.assertEqual(self.hub.asked[-1][1], side)

    def test_finishing_a_delta_the_parent_already_began_is_not_a_violation(self):
        self.approve({'CLAUDE.md': self.new, 'AGENTS.md': 'agents 2\n'})
        self.commit_as(OWNER, 'the owner made one half', {'CLAUDE.md': self.new})
        sha = self.agent_commit({'AGENTS.md': 'agents 2\n'})
        self.assertEqual(self.judge(sha, approvals=self.seam()), [])

    def test_an_approved_delete_and_new_file_are_not_a_violation(self):
        self.approve({'AGENTS.md': None, '.github/workflows/new.yml': 'on: push\n'})
        sha = self.agent_commit({'.github/workflows/new.yml': 'on: push\n'}, deleted=['AGENTS.md'])
        self.assertEqual(self.judge(sha, approvals=self.seam()), [])

    def test_another_line_than_the_approved_ones_is_a_violation(self):
        self.approve({'CLAUDE.md': self.new})
        sha = self.agent_commit({'CLAUDE.md': self.new + 'line 13\n'})
        self.refused(sha, ap.WHY_FILES, 'CLAUDE.md at %s is not the approved change'
                     % sha[:12])
        self.assertEqual(self.route.asked, ['/snapshot'])     # the hub never verified it

    def test_a_path_the_approval_does_not_hold_is_a_violation(self):
        self.approve({'CLAUDE.md': self.new})
        sha = self.agent_commit({'CLAUDE.md': self.new, 'docs/x.md': 'x\n'})
        self.refused(sha, ap.WHY_FILES, 'docs/x.md is not in the approval')

    def test_an_unfinished_delta_is_a_violation(self):
        self.approve({'CLAUDE.md': self.new, 'AGENTS.md': 'agents 2\n'})
        sha = self.agent_commit({'CLAUDE.md': self.new})
        self.refused(sha, 'AGENTS.md at %s is not the approved change' % sha[:12])

    def test_a_partly_reverted_delta_is_a_violation(self):
        self.approve({'CLAUDE.md': self.new, 'AGENTS.md': 'agents 2\n'})
        self.commit_as(OWNER, 'the owner made one half', {'CLAUDE.md': self.new})
        sha = self.agent_commit({'CLAUDE.md': LINES, 'AGENTS.md': 'agents 2\n'})
        self.refused(sha, 'CLAUDE.md at %s is not the approved change' % sha[:12])

    def test_an_approved_delete_whose_commit_keeps_the_file_is_a_violation(self):
        self.approve({'AGENTS.md': None, 'CLAUDE.md': self.new})
        sha = self.agent_commit({'CLAUDE.md': self.new})
        self.refused(sha, 'AGENTS.md at %s is not the approved change' % sha[:12])
        self.assertEqual(self.route.asked, ['/snapshot'])     # the hub never verified it

    def test_an_approved_delete_whose_commit_rewrites_the_file_is_a_violation(self):
        self.approve({'AGENTS.md': None})
        sha = self.agent_commit({'AGENTS.md': 'agents, rewritten\n'})
        self.refused(sha, 'AGENTS.md at %s is not the approved change' % sha[:12])
        self.assertEqual(self.route.asked, ['/snapshot'])

    def test_an_approved_delete_of_a_file_gone_at_the_parent_that_the_commit_makes_is_refused(
            self):
        self.approve({'AGENTS.md': None})
        self.repo.git('rm', '-q', '--', 'AGENTS.md')
        self.repo.git('-c', 'user.email=%s' % OWNER, 'commit', '-q', '-m', 'the owner deleted it')
        sha = self.agent_commit({'AGENTS.md': 'an owner file nobody approved\n'})
        self.refused(sha, 'AGENTS.md at %s is not the approved change' % sha[:12])
        self.assertEqual(self.route.asked, ['/snapshot'])

    def test_a_new_file_the_parent_holds_otherwise_is_a_violation(self):
        self.approve({'.github/workflows/new.yml': 'on: push\n'})
        self.commit_as(OWNER, 'the owner wrote it first', {'.github/workflows/new.yml': 'x\n'})
        sha = self.agent_commit({'.github/workflows/new.yml': 'on: push\n'})
        self.refused(sha, 'already at the parent')

    def test_an_approval_whose_change_is_on_main_is_spent(self):
        self.approve({'CLAUDE.md': self.new})
        first = self.agent_commit({'CLAUDE.md': self.new})
        self.repo.git('update-ref', MAIN, first)            # it landed: main as fetched holds it
        self.repo.git('reset', '-q', '--hard', self.main)
        again = self.agent_commit({'CLAUDE.md': self.new}, subject='the same edit, rebuilt')
        self.refused(again, 'spent: landed in %s' % first[:12])
        self.assertEqual(self.route.asked, [])               # spent first: the hub is not asked

    def test_the_default_main_is_the_configured_remote_and_branch(self):
        self.approve({'CLAUDE.md': self.new})
        first = self.agent_commit({'CLAUDE.md': self.new})
        self.repo.git('update-ref', 'refs/remotes/origin/main', first)
        self.repo.git('reset', '-q', '--hard', self.main)
        again = self.agent_commit({'CLAUDE.md': self.new}, subject='the same edit, rebuilt')
        self.refused(again, 'spent: landed in %s' % first[:12], main=None)

    def test_the_commit_that_landed_is_not_spent_by_itself(self):
        self.approve({'CLAUDE.md': self.new})
        sha = self.agent_commit({'CLAUDE.md': self.new})
        self.repo.git('update-ref', MAIN, sha)
        self.assertEqual(self.judge(sha, approvals=self.seam()), [])

    def test_no_main_fetched_is_nothing_landed(self):
        self.approve({'CLAUDE.md': self.new})
        sha = self.agent_commit({'CLAUDE.md': self.new})
        seam = self.seam(main='refs/remotes/origin/none')
        self.assertEqual(self.judge(sha, approvals=seam), [])

    def test_a_spent_lookup_git_cannot_answer_is_a_refusal(self):
        self.approve({'CLAUDE.md': self.new})
        sha = self.agent_commit({'CLAUDE.md': self.new})
        self.repo.git('update-ref', MAIN, self.main)
        with mock.patch.object(ap, 'spent', return_value=(None, 'git log failed: boom')):
            self.refused(sha, 'whether it is spent cannot be told: git log failed: boom')

    def test_an_unknown_approval_is_a_violation(self):
        self.approve({'CLAUDE.md': self.new})
        sha = self.agent_commit({'CLAUDE.md': self.new})
        del self.hub.docs['A-001']
        self.refused(sha, ap.WHY_UNKNOWN)

    def test_a_base_blob_the_signed_head_does_not_hold_is_refused(self):
        self.approve({'CLAUDE.md': self.new})
        sha = self.agent_commit({'CLAUDE.md': self.new})
        self.hub.docs['A-001']['files'][0]['base_blob'] = ap.blob_id('else\n')
        self.refused(sha, 'CLAUDE.md at the approved base %s is not the approved base blob'
                     % self.main[:12])
        self.assertEqual(self.route.asked, ['/snapshot'])

    def test_approved_content_that_is_not_its_blob_is_refused(self):
        self.approve({'CLAUDE.md': self.new})
        sha = self.agent_commit({'CLAUDE.md': self.new})
        self.hub.docs['A-001']['files'][0]['content'] = self.new + 'more\n'
        self.refused(sha, 'not its approved blob')

    def test_a_base_git_cannot_read_is_a_refusal(self):
        did = self.approve({'CLAUDE.md': self.new})
        doc = ap.new_doc(REPO, 'owner-only edit', '1' * 40, [
            {'path': 'CLAUDE.md', 'base_blob': ap.blob_id(LINES), 'content': self.new}], NOW)
        doc.update(ap.decide_fields(KEY, did, doc, ap.APPROVE, NOW))
        self.hub.docs[did] = doc
        sha = self.agent_commit({'CLAUDE.md': self.new})
        self.refused(sha, 'git ls-tree')

    def test_no_trailer_or_two_never_ask_the_hub(self):
        plain = self.commit_as(AGENT, 'agent edits CLAUDE.md', {'CLAUDE.md': self.new})
        self.assertEqual([v.why for v in self.judge(plain, approvals=self.seam())], [''])
        self.repo.git('reset', '-q', '--hard', self.main)
        two = self.commit_as(AGENT, approval_message('edit', 'hub:A-001', 'hub:A-002'),
                             {'CLAUDE.md': self.new})
        self.assertIn('is not one hub:A-<n>', self.judge(two, approvals=self.seam())[0].why)
        self.assertEqual(self.route.asked, [])

    def test_the_trailer_is_the_guards(self):
        self.assertEqual(ap.TRAILER, oc.TRAILER)

    def test_the_regular_modes_are_the_guards(self):
        self.assertEqual(ap.REGULAR_MODES, oc.REGULAR_MODES)

    def test_the_seam_reads_each_approval_once(self):
        self.approve({'CLAUDE.md': self.new, 'AGENTS.md': 'agents 2\n'})
        self.agent_commit({'CLAUDE.md': self.new})
        sha = self.agent_commit({'AGENTS.md': 'agents 2\n'})
        seam = self.seam()
        found, why = oc.violations(self.repo.root, self.rules, sha, [self.main], approvals=seam)
        self.assertIsNone(why)
        self.assertEqual(len(found), 1)                   # the first commit left half undone
        self.assertEqual(self.route.asked.count('/snapshot'), 1)

    def test_the_default_seam_is_made_once_per_call(self):
        # two commits under one approval, judged with no seam given: one snapshot read and one
        # spent lookup, so the default seam's caches hold across the commits
        self.approve({'CLAUDE.md': self.new, 'AGENTS.md': 'agents 2\n'})
        self.agent_commit({'CLAUDE.md': self.new})
        sha = self.agent_commit({'AGENTS.md': 'agents 2\n'})
        self.repo.git('update-ref', MAIN, self.main)        # main fetched: spent is looked up
        route, made, looked = VerifyRoute(self.hub), [], []
        real_spent = ap.spent

        def seam():
            made.append(1)
            return oc.HubApprovals(client=route, repo=REPO, main=MAIN)

        def spent(*args, **kwargs):
            looked.append(args[1])
            return real_spent(*args, **kwargs)
        with mock.patch.object(oc, 'default_approvals', seam):
            with mock.patch.object(ap, 'spent', spent):
                found, why = oc.violations(self.repo.root, self.rules, sha, [self.main])
        self.assertIsNone(why)
        self.assertEqual(len(found), 1)                   # the first commit left half undone
        self.assertEqual(made, [1])
        self.assertEqual(route.asked.count('/snapshot'), 1)
        self.assertEqual(looked, ['A-001'])


@real_git('git merge-file: the fake has none')
class DeltaMerges(DeltaCase):
    """The parent moved on in an approved file since the base: the commit must hold the clean
    three-way merge of the approved change onto it."""

    def test_a_clean_merge_onto_a_newer_base_is_not_a_violation(self):
        self.approve({'CLAUDE.md': self.new})
        moved = edit(LINES, 11, 'eleven')
        self.commit_as(OWNER, 'the owner edits a far line', {'CLAUDE.md': moved})
        sha = self.agent_commit({'CLAUDE.md': edit(moved, 2, 'two')})
        self.assertEqual(self.judge(sha, approvals=self.seam()), [])
        self.assertEqual(self.hub.asked[-1][1:3], (self.main, {
            'CLAUDE.md': ap.blob_id(self.new)}))

    def test_the_approved_content_undoing_the_owners_later_line_is_a_violation(self):
        self.approve({'CLAUDE.md': self.new})
        self.commit_as(OWNER, 'the owner edits a far line', {'CLAUDE.md': edit(LINES, 11, 'eleven')})
        sha = self.agent_commit({'CLAUDE.md': self.new})
        self.refused(sha, 'CLAUDE.md at %s is not the approved change' % sha[:12])

    def test_an_owner_change_to_the_approved_line_is_a_conflict(self):
        self.approve({'CLAUDE.md': self.new})
        self.commit_as(OWNER, 'the owner edits that line', {'CLAUDE.md': edit(LINES, 2, 'TWO')})
        sha = self.agent_commit({'CLAUDE.md': self.new})
        self.refused(sha, 'CLAUDE.md', 'does not merge cleanly')


@real_git('update-index --chmod and --cacheinfo with a symbolic link: the fake has neither')
class DeltaModes(DeltaCase):
    """An approval holds regular files in the mode they have: a commit that gives an approved
    path another mode, or a parent that holds it as a symbolic link, is refused."""

    def test_a_commit_that_changes_the_mode_is_a_violation(self):
        self.approve({'CLAUDE.md': self.new})
        self.repo.write('CLAUDE.md', self.new)
        self.repo.git('add', 'CLAUDE.md')
        self.repo.git('update-index', '--chmod=+x', 'CLAUDE.md')
        self.repo.git('-c', 'user.email=%s' % AGENT, 'commit', '-q', '-m',
                      approval_message('owner-approved edit', 'hub:A-001'))
        sha = self.repo.git('rev-parse', 'HEAD')
        self.consume('A-001', sha)
        self.refused(sha, 'CLAUDE.md at %s changes its mode from 100644 to 100755' % sha[:12])

    def test_a_parent_that_holds_the_path_as_a_symbolic_link_is_a_violation(self):
        self.approve({'CLAUDE.md': self.new})
        self.repo.write('link.txt', 'AGENTS.md')
        link = self.repo.git('hash-object', '-w', 'link.txt')
        self.repo.git('update-index', '--cacheinfo', '120000,%s,CLAUDE.md' % link)
        self.repo.git('-c', 'user.email=%s' % OWNER, 'commit', '-q', '-m', 'the owner links it')
        parent = self.repo.git('rev-parse', 'HEAD')
        self.repo.git('rm', '-q', '--cached', 'CLAUDE.md')
        sha = self.agent_commit({'CLAUDE.md': self.new})
        self.refused(sha, 'CLAUDE.md at %s is not a regular file' % parent[:12])


@real_git('update-index --cacheinfo with a symbolic link or gitlink mode: the fake has none')
class NotARegularFile(ApprovalsCase):
    """An approval holds regular files: a commit that records an approved path as a symbolic link
    (with the approved blob) or a gitlink (which has no blob, as a deletion has none) is refused
    without asking the hub."""

    def entry_commit(self, mode, oid, doc_id):
        self.repo.git('update-index', '--cacheinfo', '%s,%s,CLAUDE.md' % (mode, oid))
        self.repo.git('-c', 'user.email=%s' % AGENT, 'commit', '-q', '-m',
                      approval_message('owner-approved edit', 'hub:A-001'))
        sha = self.repo.git('rev-parse', 'HEAD')
        self.consume(doc_id, sha)
        return sha

    def assert_refused_unasked(self, sha, mode):
        found = self.judge(sha)
        self.assertEqual([(v.sha, v.paths) for v in found], [(sha, ['CLAUDE.md'])])
        self.assertIn('owner approval hub:A-001 refused: CLAUDE.md (mode %s) %s'
                      % (mode, oc.NOT_A_FILE), found[0].why)
        self.assertEqual(self.hub.asked, [])

    def test_a_symbolic_link_with_the_approved_blob_is_refused(self):
        doc_id = self.approve({'CLAUDE.md': 'approved\n'})
        self.repo.write('blob.txt', 'approved\n')
        blob = self.repo.git('hash-object', '-w', 'blob.txt')
        self.assertEqual(blob, ap.blob_id('approved\n'))
        self.assert_refused_unasked(self.entry_commit('120000', blob, doc_id), '120000')

    def test_a_gitlink_in_place_of_an_approved_delete_is_refused(self):
        doc_id = self.approve({'CLAUDE.md': None})
        self.assert_refused_unasked(self.entry_commit('160000', self.main, doc_id), '160000')


if __name__ == '__main__':
    unittest.main()
