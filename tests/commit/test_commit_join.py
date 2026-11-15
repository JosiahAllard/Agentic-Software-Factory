"""A merge that joins two changes to an owner-only file, on real git, and a merge driver
(docs/commit.md, docs/owner-files.md). Split out of test_commit.py so each module stays well
inside the kit_tests per-module limit; the module setup and the base cases are test_commit's
(as test_commit_narrowed.py).
"""
from unittest import mock

from kitlib.commit import checks
from kitlib.commit import run as C
from kitlib.git import cmd
from kitlib.ownerfiles import rules as ownerrules

from tests import gitfixture
from tests.commit import test_commit
from tests.commit.test_commit import AGENT, MergeCase, OWNER, rules
from tests.realgit import real_git

setUpModule = test_commit.setUpModule


SEVEN = ''.join('line %d\n' % i for i in range(1, 8))     # edits on lines 1 and 7 merge cleanly


class Approves(object):
    """The approvals seam: the hub verifies exactly the ids in `ok`."""

    def __init__(self, ok):
        self.ok, self.asked = set(ok), []

    def check(self, root, doc_id, head, blobs, commit):
        self.asked.append(doc_id)
        return (doc_id in self.ok), 'the hub has no %s for this commit' % doc_id


class JoinCase(MergeCase):
    """Both sides changed an owner-only file, each in a commit the owner approved (an agent's
    commit carrying Owner-Approval: hub:A-<n>, verified by the hub): a merge whose staged copy is
    exactly the clean merge of the two is carried unnamed. Anything
    beyond that clean merge stays refused."""

    F = 'CLAUDE.md'

    def approve(self, *ids):
        seam = Approves(ids)
        p = mock.patch.object(ownerrules, 'default_approvals', lambda: seam)
        p.start()
        self.addCleanup(p.stop)
        return seam

    def merge(self, main_text, branch_text, main_id='A-002', branch_id='A-001', path=None,
              attributes=None, main_rename=None, main_author=AGENT, main_mode=None, pushed=True):
        """The base holds work.txt and `path` (SEVEN), and .gitattributes = `attributes` when
        given. main sets the file to `main_text` (as index mode `main_mode` when given; or
        `git mv`s it to `main_rename`) in an agent
        commit carrying Owner-Approval: hub:`main_id` (None: no trailer); the branch sets it to
        `branch_text` the same way with `branch_id`. With `pushed`, origin/main holds main's tip
        (push_main), so main's commits are not judged again (landed_main). Both change work.txt,
        so the merge stops on it; work.txt is resolved in the work tree. (branch tip, main
        tip)."""
        f = path or self.F
        self.t.write('work.txt', 'base\n')
        self.t.write(f, SEVEN)
        names = ['work.txt', f]
        if attributes:
            self.t.write('.gitattributes', attributes)
            names.append('.gitattributes')
        self.t.commit('base', *names)
        self.step('merge', '-q', '--ff-only', 'main')
        self.t.write('work.txt', 'main\n')
        if main_rename:
            self.t.git('mv', '--', f, main_rename)          # the fake has no mv of a path
        else:
            self.t.write(f, main_text)
            self.t._own('add', '--', f)
            if main_mode:                                   # the fake has no --cacheinfo
                blob = self.t._own('rev-parse', ':%s' % f)
                self.t.git('update-index', '--cacheinfo', '%s,%s,%s' % (main_mode, blob, f))
        trailer = '\n\nOwner-Approval: hub:%s' % main_id if main_id else ''
        self.main_commit('main edit' + trailer, ['work.txt'], main_author)
        self.put('work.txt', 'branch\n')
        self.step('commit', '-q', '-m', 'branch work', '--', 'work.txt')
        self.put(f, branch_text)
        trailer = '\n\nOwner-Approval: hub:%s' % branch_id if branch_id else ''
        self.step('commit', '-q', '-m', 'branch edit' + trailer, '--author', 'Agent <%s>' % AGENT,
                  '--', f)
        tip, main = self.head(), self.step('rev-parse', 'main')
        if pushed:
            self.push_main()
        p = cmd.subprocess_run(('git', 'merge', '--no-edit', 'main'), cwd=self.wt,
                               capture_output=True, text=True, env=gitfixture.fixture_env())
        self.assertNotEqual(p.returncode, 0, p.stdout)
        self.put('work.txt', 'resolved\n')
        return tip, main

    def top(self):
        return SEVEN.replace('line 1\n', 'line 1, main\n')

    def bottom(self):
        return SEVEN.replace('line 7\n', 'line 7, branch\n')

    def both(self):
        return SEVEN.replace('line 1\n', 'line 1, main\n').replace('line 7\n', 'line 7, branch\n')



@real_git('merge-tree, check-attr and merge-base --all over a three-way merge: the fake has none '
          'of them')
class MergeJoinsTwoApprovedChanges(JoinCase):

    def test_the_clean_merge_of_two_approved_changes_is_committed(self):
        seam = self.approve('A-001', 'A-002')
        tip, main = self.merge(self.top(), self.bottom())
        self.assertEqual(self.git('show', ':%s' % self.F) + '\n', self.both())
        code, out = self.commit('merge main', 'work.txt')
        self.assertEqual(code, 0, out)
        self.assertEqual(self.git('log', '-1', '--format=%P').split(), [tip, main])
        self.assertEqual(self.git('show', 'HEAD:%s' % self.F) + '\n', self.both())
        self.assertIn('files     work.txt, CLAUDE.md', out)
        self.assertEqual(sorted(set(seam.asked)), ['A-001'])      # main's, landed, not again

    def test_the_merge_commit_passes_the_landing_check(self):
        self.approve('A-001', 'A-002')
        tip, main = self.merge(self.top(), self.bottom())
        self.assertEqual(self.commit('merge main', 'work.txt')[0], 0)
        rules = C.kconfig.load(self.wt).owner_rules()
        self.assertEqual(ownerrules.violations(self.wt, rules, 'HEAD', [main]), ([], None))

    def test_an_extra_edit_on_top_of_the_merge_is_refused(self):
        self.approve('A-001', 'A-002')
        tip, _ = self.merge(self.top(), self.bottom())
        self.put(self.F, self.both() + 'and the agent\'s\n')
        self.git('add', '--', self.F)
        self.assert_refused_naming(self.F, tip)

    def test_a_conflict_resolved_by_hand_is_refused(self):
        self.approve('A-001', 'A-002')
        tip, _ = self.merge(self.top(), SEVEN.replace('line 1\n', 'line 1, branch\n'))
        self.put(self.F, SEVEN.replace('line 1\n', 'line 1, main and branch\n'))
        self.git('add', '--', self.F)
        self.assert_refused_naming(self.F, tip)

    def test_an_unapproved_change_on_the_branch_is_refused(self):
        self.approve('A-002')
        tip, _ = self.merge(self.top(), self.bottom())
        self.assertNotIn('could not', self.assert_refused_naming(self.F, tip))

    def test_a_branch_change_with_no_approval_at_all_is_refused(self):
        self.approve('A-002')
        tip, _ = self.merge(self.top(), self.bottom(), branch_id=None)
        self.assert_refused_naming(self.F, tip)

    def test_an_unapproved_change_on_main_is_refused(self):
        self.approve('A-001')
        tip, _ = self.merge(self.top(), self.bottom(), pushed=False)
        self.assert_refused_naming(self.F, tip)

    def test_an_owner_authored_side_joins_an_approved_one(self):
        self.approve('A-001')
        tip, main = self.merge(self.top(), self.bottom(), main_id=None, main_author=OWNER)
        code, out = self.commit('merge main', 'work.txt')
        self.assertEqual(code, 0, out)
        self.assertEqual(self.git('show', 'HEAD:%s' % self.F) + '\n', self.both())

    def test_a_rename_is_refused(self):
        self.approve('A-001', 'A-002')
        old, new = '.github/workflows/a.yml', '.github/workflows/b.yml'
        tip, _ = self.merge(None, self.bottom(), path=old, main_rename=new)
        self.assertEqual(self.git('show', ':%s' % new) + '\n', self.bottom())
        self.assert_refused_naming(new, tip)

    def test_a_merge_default_driver_is_refused(self):
        self.approve('A-001', 'A-002')
        tip, _ = self.merge(self.top(), self.bottom())
        self.git('config', 'merge.default', 'union')
        self.assertFalse(ownerrules.merge_attribute_unset(self.wt, self.F))
        self.assert_refused_naming(self.F, tip)

    def test_a_mode_change_on_one_side_is_refused(self):
        self.approve('A-001', 'A-002')
        tip, _ = self.merge(self.top(), self.bottom(), main_mode='100755')
        self.assertEqual(self.git('ls-files', '-s', '--', self.F).split()[0], '100755')
        self.assert_refused_naming(self.F, tip)

    def test_an_octopus_merge_is_refused(self):
        self.approve('A-001', 'A-002')
        tip, _ = self.merge(self.top(), self.bottom())
        path = self.git('rev-parse', '--path-format=absolute', '--git-path', 'MERGE_HEAD')
        with open(path, 'a', encoding='utf-8', newline='\n') as fh:
            fh.write(tip + '\n')
        self.assert_refused_naming(self.F, tip)

    def test_the_helper_sync_base_calls_names_what_is_refused(self):
        self.approve('A-002')
        self.merge(self.top(), self.bottom())
        self.git('add', '--', 'work.txt')
        rules = C.kconfig.load(self.wt).owner_rules()
        self.assertEqual(checks.merge_owner_refusal(self.wt, rules), ([self.F], None))
        self.approve('A-001', 'A-002')
        self.assertEqual(checks.merge_owner_refusal(self.wt, rules), ([], None))


@real_git('merge-base --all and a conflict in the owner-only file: as MergeJoinsTwoApprovedChanges')
class AMergeThatJoinsBothSides(JoinCase):
    """Each side, in an approved commit, changes CLAUDE.md next to the other's change (deletes a
    line, or adds one after line 3), so it conflicts; a resolution that only joins both sides'
    changes is committed with no new approval (ownerrules.joined_merge on the staged copy). Any
    other one is refused."""

    UNION = SEVEN.replace('line 3\n', 'line 3\nmain row\nbranch row\n')

    def conflict(self, main_text, branch_text, *ids):
        self.approve(*ids)
        tip, main = self.merge(main_text, branch_text)
        self.assertIn(self.F, self.git('diff', '--name-only', '--diff-filter=U'))
        return tip, main

    def merge_deletions(self, *ids):
        return self.conflict(SEVEN.replace('line 3\n', ''), SEVEN.replace('line 4\n', ''), *ids)

    def merge_additions(self, *ids):
        return self.conflict(SEVEN.replace('line 3\n', 'line 3\nmain row\n'),
                             SEVEN.replace('line 3\n', 'line 3\nbranch row\n'), *ids)

    def test_the_union_of_both_sides_additions_is_committed_and_lands(self):
        tip, main = self.merge_additions('A-001', 'A-002')
        self.put(self.F, self.UNION)
        code, out = self.commit('merge main', 'work.txt', self.F)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.git('log', '-1', '--format=%P').split(), [tip, main])
        self.assertEqual(self.git('show', 'HEAD:%s' % self.F) + '\n', self.UNION)
        rules = C.kconfig.load(self.wt).owner_rules()
        self.assertEqual(ownerrules.violations(self.wt, rules, 'HEAD', [main]), ([], None))

    def test_a_side_whose_rows_begin_with_the_others_is_committed_as_it_is(self):
        # main adds X, the branch X then Y; the result X Y holds X once (rules._block_joins)
        held = SEVEN.replace('line 3\n', 'line 3\nmain row\nbranch row\n')
        tip, main = self.conflict(SEVEN.replace('line 3\n', 'line 3\nmain row\n'), held,
                                  'A-001', 'A-002')
        self.put(self.F, held)
        code, out = self.commit('merge main', 'work.txt', self.F)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.git('log', '-1', '--format=%P').split(), [tip, main])
        rules = C.kconfig.load(self.wt).owner_rules()
        self.assertEqual(ownerrules.violations(self.wt, rules, 'HEAD', [main]), ([], None))

    def test_a_union_with_a_line_neither_side_has_is_refused(self):
        tip, _ = self.merge_additions('A-001', 'A-002')
        self.put(self.F, self.UNION.replace('branch row\n', 'branch row\nagent row\n'))
        self.assert_refused(tip)

    def test_a_union_with_an_unapproved_side_is_refused(self):
        tip, _ = self.merge_additions('A-002')      # the branch's A-001 is not approved
        self.put(self.F, self.UNION)
        self.assert_refused(tip)

    def test_a_resolution_that_only_deletes_is_committed_and_lands(self):
        tip, main = self.merge_deletions('A-001', 'A-002')
        both = SEVEN.replace('line 3\n', '').replace('line 4\n', '')
        self.put(self.F, both)
        code, out = self.commit('merge main', 'work.txt', self.F)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.git('log', '-1', '--format=%P').split(), [tip, main])
        self.assertEqual(self.git('show', 'HEAD:%s' % self.F) + '\n', both)
        rules = C.kconfig.load(self.wt).owner_rules()
        self.assertEqual(ownerrules.violations(self.wt, rules, 'HEAD', [main]), ([], None))

    def assert_refused(self, tip):
        code, out = self.commit('merge main', 'work.txt', self.F)
        self.assertEqual(code, 1, out)
        self.assertIn('refused: CLAUDE.md is owner-only', out)
        self.assertEqual(self.head(), tip)

    def test_a_resolution_that_adds_a_line_is_refused(self):
        tip, _ = self.merge_deletions('A-001', 'A-002')
        self.put(self.F, SEVEN.replace('line 3\n', '').replace('line 4\n', 'line 4, agent\n'))
        self.assert_refused(tip)

    def test_an_unapproved_side_is_refused(self):
        tip, _ = self.merge_deletions('A-002')
        self.put(self.F, SEVEN.replace('line 3\n', '').replace('line 4\n', ''))
        self.assert_refused(tip)

    def test_when_what_the_merge_carries_cannot_be_judged_nothing_is_exempt(self):
        tip, _ = self.merge_deletions('A-001', 'A-002')
        self.put(self.F, SEVEN.replace('line 3\n', '').replace('line 4\n', ''))
        real = ownerrules.merge_owned

        def merge_owned(root, rules, staged, *a, **kw):     # only the named owner-only path's ask
            return (set(), 'merge-tree stubbed to fail') if self.F in staged else real(
                root, rules, staged, *a, **kw)
        with mock.patch.object(ownerrules, 'merge_owned', merge_owned):
            code, out = self.commit('merge main', 'work.txt', self.F)
        self.assertEqual(code, 1, out)
        self.assertIn('refused: CLAUDE.md is owner-only', out)
        self.assertIn('what the merge carries could not be judged: merge-tree stubbed to fail.',
                      out)
        self.assertEqual(self.head(), tip)


@gitfixture.real_fixture('a committed .gitattributes with merge=union: the fake git refuses the '
                         'attribute')
@real_git('check-attr and a merge driver: the fake has neither')
class AMergeDriverIsRefused(JoinCase):

    def test_a_merge_driver_attribute_is_refused(self):
        self.approve('A-001', 'A-002')
        tip, _ = self.merge(self.top(), self.bottom(), attributes='CLAUDE.md merge=union\n')
        self.assertEqual(self.git('show', ':%s' % self.F) + '\n', self.both())
        self.assertFalse(ownerrules.merge_attribute_unset(self.wt, self.F))
        self.assert_refused_naming(self.F, tip)
