"""A merge that carries an owner change, on real git (docs/commit.md, docs/owner-files.md).
Split out of test_commit.py so each module stays well inside the kit_tests per-module limit;
the module setup and the base cases are test_commit's (as test_commit_narrowed.py).
"""
from unittest import mock

from kitlib.commit import run as C
from kitlib.git import cmd
from kitlib.ownerfiles import rules as ownerrules

from tests import gitfixture
from tests.commit import test_commit
from tests.commit.test_commit import AGENT, MergeCase, OWNER
from tests.realgit import real_git

setUpModule = test_commit.setUpModule


KEEP = object()        # main leaves the owner-only file as the base has it


@real_git('modify/delete conflicts, checkout --theirs and update-index --chmod: the fake has '
          'none; and the index and tree entries the rule compares are git\'s own')
class MergeCarriesAnOwnerChange(MergeCase):
    """A conflicted merge of main, where main (the owner) changed an owner-only file. The merge's
    own copy of it is committed unnamed only when the staged state is exactly main's copy (a
    deletion included), main changed it, and the owner authored every commit main brings that
    changes it; anything else under that path stays refused."""

    F = 'CLAUDE.md'

    def merge(self, main_side, branch_side=None, path=None, main_author=OWNER,
              main_message='owner moves main', pushed=True):
        """The base holds work.txt and the owner-only file. main sets the file to `main_side`
        (None: deletes it; KEEP: leaves it) in a commit `main_author` wrote with `main_message`;
        the branch sets it to `branch_side` when given (an owner commit made outside the tool).
        With `pushed`, origin/main holds main's tip (push_main). Both change work.txt, so the
        merge stops; work.txt is resolved in the work tree. (branch tip, main tip)."""
        f = path or self.F
        self.assertTrue(C.kconfig.load(self.wt).owner_rules().is_locked(f), f)
        self.t.write('work.txt', 'base\n')
        self.t.write(f, 'owner base\n')
        self.t.commit('base', 'work.txt', f)
        self.step('merge', '-q', '--ff-only', 'main')
        self.t.write('work.txt', 'main\n')
        if main_side is None:
            self.t._own('rm', '-q', '--', f)
        elif main_side is not KEEP:
            self.t.write(f, main_side)
            self.t._own('add', '--', f)
        self.main_commit(main_message, ['work.txt'], main_author)
        self.put('work.txt', 'branch\n')
        self.step('commit', '-q', '-m', 'branch work', '--', 'work.txt')
        if branch_side is not None:
            self.put(f, branch_side)
            self.step('commit', '-q', '-m', 'owner edit', '--author', 'Owner <%s>' % OWNER,
                      '--', f)
        tip, main = self.head(), self.step('rev-parse', 'main')
        if pushed:
            self.push_main()
        p = cmd.subprocess_run(('git', 'merge', '--no-edit', 'main'), cwd=self.wt,
                               capture_output=True, text=True, env=gitfixture.fixture_env())
        self.assertNotEqual(p.returncode, 0, p.stdout)
        self.put('work.txt', 'resolved\n')
        return tip, main

    def assert_merged(self, tip, main, out, code):
        self.assertEqual(code, 0, out)
        self.assertEqual(self.git('log', '-1', '--format=%P').split(), [tip, main])

    def tracked(self, path=None):
        return self.git('ls-tree', '--name-only', 'HEAD', '--', path or self.F)

    def test_an_owner_deletion_carried_by_a_conflicted_merge_is_committed(self):
        tip, main = self.merge(None)
        code, out = self.commit('merge main', 'work.txt')
        self.assert_merged(tip, main, out, code)
        self.assertEqual(self.tracked(), '')
        self.assertIn('files     work.txt, CLAUDE.md', out)

    def test_an_owner_deletion_under_a_locked_directory_is_committed(self):
        f = '.claude/agent-profile.md'
        tip, main = self.merge(None, path=f)
        code, out = self.commit('merge main', 'work.txt')
        self.assert_merged(tip, main, out, code)
        self.assertEqual(self.tracked(f), '')

    def test_an_owner_edit_carried_by_a_conflicted_merge_is_committed(self):
        tip, main = self.merge('owner edit\n')
        code, out = self.commit('merge main', 'work.txt')
        self.assert_merged(tip, main, out, code)
        self.assertEqual(self.git('show', 'HEAD:CLAUDE.md'), 'owner edit')

    def test_a_conflict_on_the_owner_file_resolved_to_mains_copy_is_committed(self):
        tip, main = self.merge('owner edit\n', branch_side='branch owner edit\n')
        self.git('checkout', '--theirs', '--', self.F)
        self.git('add', '--', self.F)
        code, out = self.commit('merge main', 'work.txt')
        self.assert_merged(tip, main, out, code)
        self.assertEqual(self.git('show', 'HEAD:CLAUDE.md'), 'owner edit')

    def test_a_modify_delete_conflict_resolved_to_mains_deletion_is_committed(self):
        tip, main = self.merge(None, branch_side='branch owner edit\n')
        self.git('rm', '-q', '--', self.F)
        code, out = self.commit('merge main', 'work.txt')
        self.assert_merged(tip, main, out, code)
        self.assertEqual(self.tracked(), '')

    def test_an_unresolved_conflict_on_the_owner_file_is_refused(self):
        tip, _ = self.merge(None, branch_side='branch owner edit\n')
        self.assert_refused_naming(self.F, tip)

    def test_a_conflict_resolved_to_neither_copy_is_refused(self):
        tip, _ = self.merge('owner edit\n', branch_side='branch owner edit\n')
        self.put(self.F, 'owner edit\nbranch owner edit\n')
        self.git('add', '--', self.F)
        self.assert_refused_naming(self.F, tip)

    def test_a_staged_copy_that_differs_from_mains_is_refused(self):
        tip, _ = self.merge('owner edit\n')
        self.put(self.F, 'owner edit, then the agent\'s\n')
        self.git('add', '--', self.F)
        self.assert_refused_naming(self.F, tip)

    def test_a_file_main_deleted_but_staged_with_new_content_is_refused(self):
        tip, _ = self.merge(None)
        self.put(self.F, 'agent text\n')
        self.git('add', '--', self.F)
        self.assert_refused_naming(self.F, tip)

    def test_mains_blob_with_another_mode_is_refused(self):
        tip, _ = self.merge('owner edit\n')
        self.git('update-index', '--chmod=+x', '--', self.F)
        self.assert_refused_naming(self.F, tip)

    def test_undoing_the_branchs_own_owner_change_to_mains_copy_is_refused(self):
        # main never touched the file: staging main's copy reverts the branch's owner commit.
        tip, _ = self.merge(KEEP, branch_side='branch owner edit\n')
        self.git('checkout', 'main', '--', self.F)
        self.assertEqual(self.git('diff', '--cached', '--name-only', '--', self.F), self.F)
        self.assert_refused_naming(self.F, tip)

    def test_checking_out_head_over_mains_owner_change_commits_with_a_warning(self):
        tip, main = self.merge('owner edit\n', branch_side='branch owner edit\n')
        self.git('checkout', 'HEAD', '--', self.F)
        code, out = self.commit('merge main', 'work.txt')
        self.assert_merged(tip, main, out, code)
        warn = [ln for ln in out.splitlines() if 'drops the merged' in ln]
        self.assertEqual(len(warn), 1, out)
        self.assertTrue(warn[0].startswith('WARNING: '), warn)
        self.assertIn(self.F, warn[0])

    def test_taking_mains_owner_change_has_no_drop_warning(self):
        tip, main = self.merge('owner edit\n')
        code, out = self.commit('merge main', 'work.txt')
        self.assert_merged(tip, main, out, code)
        self.assertNotIn('drops the merged', out)

    def test_an_octopus_merge_carries_no_owner_change(self):
        tip, _ = self.merge(None)
        path = self.git('rev-parse', '--path-format=absolute', '--git-path', 'MERGE_HEAD')
        with open(path, 'a', encoding='utf-8', newline='\n') as fh:
            fh.write(tip + '\n')
        self.assert_refused_naming(self.F, tip)

    def test_naming_the_merges_owner_file_is_refused_with_the_hint(self):
        tip, _ = self.merge('owner edit\n')
        code, out = self.commit('merge main', 'work.txt', self.F)
        self.assertEqual(code, 1, out)
        self.assertIn('refused: CLAUDE.md is owner-only', out)
        self.assertIn('leave it out of your list', out)
        self.assertEqual(self.head(), tip)

    def test_naming_an_owner_file_the_merge_does_not_carry_has_no_hint(self):
        tip, _ = self.merge('owner edit\n')
        self.put(self.F, 'owner edit, then the agent\'s\n')
        self.git('add', '--', self.F)
        code, out = self.commit('merge main', 'work.txt', self.F)
        self.assertEqual(code, 1, out)
        self.assertIn('refused: CLAUDE.md is owner-only', out)
        self.assertNotIn('leave it out of your list', out)
        self.assertEqual(self.head(), tip)

    def test_an_agents_edit_carried_by_a_merge_is_refused(self):
        # An agent's own commit, merged with --no-commit, does not pass as the owner's change.
        tip, _ = self.merge('agent edit\n', main_author=AGENT, pushed=False)
        self.assertNotIn('could not', self.assert_refused_naming(self.F, tip))

    def test_an_agents_deletion_carried_by_a_merge_is_refused(self):
        tip, _ = self.merge(None, main_author=AGENT, pushed=False)
        self.assert_refused_naming(self.F, tip)

    def merge_approved_main(self, why, landed=True):
        """main's owner-only change is an agent's commit carrying `Owner-Approval: hub:A-001`, and
        the hub now refuses it with `why` (the approval past its lifetime, or the hub down); with
        `landed`, <remote>/<main> as last fetched holds it. (branch tip, main tip, the approval
        ids the hub was asked about)."""
        asked = []

        class Stale(object):
            def check(self, root, doc_id, head, blobs, commit):
                asked.append(doc_id)
                return False, why
        p = mock.patch.object(ownerrules, 'default_approvals', Stale)
        p.start()
        self.addCleanup(p.stop)
        tip, main = self.merge('approved edit\n', main_author=AGENT,
                               main_message='approved edit\n\nOwner-Approval: hub:A-001',
                               pushed=landed)
        return tip, main, asked

    def test_an_approved_change_main_has_landed_is_not_judged_again_once_expired(self):
        tip, main, asked = self.merge_approved_main('the approval is past its lifetime')
        code, out = self.commit('merge main', 'work.txt')
        self.assert_merged(tip, main, out, code)
        self.assertEqual(self.git('show', 'HEAD:CLAUDE.md'), 'approved edit')
        self.assertEqual(asked, [])

    def test_an_approved_change_main_has_landed_is_not_judged_again_with_the_hub_down(self):
        tip, main, asked = self.merge_approved_main('the hub server did not answer')
        code, out = self.commit('merge main', 'work.txt')
        self.assert_merged(tip, main, out, code)
        self.assertEqual(asked, [])

    def test_an_approved_change_not_on_the_remote_main_is_still_judged(self):
        tip, _, asked = self.merge_approved_main('the approval is past its lifetime', landed=False)
        self.assert_refused_naming(self.F, tip)
        self.assertEqual(asked, ['A-001'])

    def test_outside_a_merge_a_staged_owner_file_is_still_refused(self):
        self.t.write(self.F, 'owner base\n')
        self.t.commit('base', self.F)
        self.git('merge', '-q', '--ff-only', 'main')
        self.git('rm', '-q', '--', self.F)
        self.put('a.txt', 'x\n')
        code, out = self.commit('work', 'a.txt')
        self.assertEqual(code, 1, out)
        self.assertIn('refused: already staged and not in your list', out)
        self.assertNotIn('leave it out of your list', out)
