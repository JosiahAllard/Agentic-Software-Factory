"""tools/trailers.py over kitlib.trailerscheck (docs/repo-checks.md "trailers"): the gate step that
warns on each branch commit with no agent Co-Authored-By trailer and never fails for one, judged
by the rule review_pack uses (kitlib.reviewpack.trailers). The fixture repository is the
review-pack one: `main` names the fixture identity as the owner, an agent commit passes
`-c user.email=agent@example.invalid`."""
import contextlib
import io
import os
import sys

from tests.reviewpack.repo import AGENT, KIT, TRAILERED_MSG, PackCase

sys.path.insert(0, os.path.join(KIT, 'tools'))
import trailers as cli  # noqa: E402

WARNING = 'WARNING: '       # the token a reader of the step's log finds a commit by


class TrailersCase(PackCase):
    def setUp(self):
        super().setUp()
        self.git('checkout', '-q', '-b', 'feature')

    def run_tool(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(['-C', self.root] + list(argv))
        return code, out.getvalue()

    def commit(self, text, message, author=None):
        self.write('f.txt', text)
        self.commit_all(message, author=author)

    def assert_warned(self, code, out, named):
        """Exit 0, a WARNING line naming `named`, and no failure line."""
        self.assertEqual(code, 0, out)
        [line] = [l for l in out.splitlines() if l.startswith(WARNING) and named in l]
        self.assertIn('has no Co-Authored-By trailer', line)
        self.assertNotIn('FAILED', out)


class Branch(TrailersCase):
    def test_a_trailerless_commit_warns_names_it_and_passes(self):
        self.commit('1', 'with\n\n' + TRAILERED_MSG)
        self.commit('2', 'without')
        code, out = self.run_tool('--base', 'main')
        self.assertIn('trailers: 2 commit(s) on this branch and not on main, 1 without a '
                      'Co-Authored-By trailer\n', out)
        self.assert_warned(code, out, '"without"')
        self.assertNotIn('"with" has', out)

    def test_a_trailered_branch_is_clean(self):
        self.commit('1', 'work\n\n' + TRAILERED_MSG)
        code, out = self.run_tool('--base', 'main')
        self.assertEqual(code, 0, out)
        self.assertIn('0 without a Co-Authored-By trailer', out)
        self.assertNotIn(WARNING, out)

    def test_an_owner_approved_commit_by_the_owner_warns_and_passes(self):
        self.commit('1', 'owner work\n\nOwner-Approved: CLAUDE.md')
        code, out = self.run_tool('--base', 'main')
        self.assert_warned(code, out, '"owner work"')
        self.assertIn('carries Owner-Approved from the owner', out)

    def test_an_owner_claim_on_an_agent_commit_warns_twice_and_passes(self):
        self.commit('1', 'agent work\n\nOwner-Approved: CLAUDE.md', author=AGENT)
        code, out = self.run_tool('--base', 'main')
        self.assert_warned(code, out, '"agent work"')
        self.assertIn('an owner claim that proves nothing', out)

    def test_an_owner_approval_on_an_agent_commit_is_not_an_owner_claim(self):
        # Owner-Approval: hub:A-<n> (docs/commit.md "Owner approval") is checked against the hub by
        # gate land and the pre-push hook; this rule neither warns on it nor lets it excuse a commit.
        self.commit('1', 'approved\n\nOwner-Approval: hub:A-3\n' + TRAILERED_MSG, author=AGENT)
        code, out = self.run_tool('--base', 'main')
        self.assertEqual(code, 0, out)
        self.assertNotIn('WARNING', out)
        self.commit('2', 'approval alone\n\nOwner-Approval: hub:A-3', author=AGENT)
        code, out = self.run_tool('--base', 'main')
        self.assert_warned(code, out, '"approval alone"')
        self.assertNotIn('owner claim', out)

    def test_a_sync_merge_without_a_trailer_warns_and_passes(self):
        self.commit('1', 'work\n\n' + TRAILERED_MSG)
        self.git('checkout', '-q', 'main')
        self.write('g.txt', 'g')
        self.commit_all('main moves')
        self.git('checkout', '-q', 'feature')
        self.git('merge', '--no-ff', '-q', '-m', 'sync main', 'main')
        code, out = self.run_tool('--base', 'main')
        self.assert_warned(code, out, 'merge "sync main"')

    def test_the_default_base_is_main_here(self):
        self.commit('1', 'work')
        code, out = self.run_tool()
        self.assert_warned(code, out, '"work"')
        self.assertIn('not on main', out)

    def test_a_base_that_is_not_a_rev_exits_2(self):
        code, out = self.run_tool('--base=--evil')
        self.assertEqual(code, 2, out)

    def test_a_base_that_does_not_resolve_exits_2(self):
        code, out = self.run_tool('--base', 'no-such-branch')
        self.assertEqual(code, 2, out)
        self.assertIn('no-such-branch', out)

    def test_a_bad_option_is_usage(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as e:
            cli.main(['--nope'])
        self.assertEqual(e.exception.code, 2)


class Merged(TrailersCase):
    def land(self):
        """feature (one trailered commit) merged into main by a trailerless lander merge; returns
        main's hash before the merge."""
        self.commit('1', 'work\n\n' + TRAILERED_MSG)
        self.git('checkout', '-q', 'main')
        before = self.git('rev-parse', 'HEAD').strip()
        self.git('merge', '--no-ff', '-q', '-m', 'Merge feature', 'feature')
        return before

    def test_the_landers_merge_on_main_is_exempt(self):
        before = self.land()
        code, out = self.run_tool('--base', before, '--merged')
        self.assertEqual(code, 0, out)
        self.assertIn('1 lander merge(s) exempt', out)
        self.assertNotIn(WARNING, out)
        code, out = self.run_tool('--base', before)
        self.assert_warned(code, out, 'merge "Merge feature"')   # without --merged: a warning

    def test_a_trailerless_branch_commit_still_warns_when_merged(self):
        self.commit('1', 'bare')
        self.git('checkout', '-q', 'main')
        before = self.git('rev-parse', 'HEAD').strip()
        self.git('merge', '--no-ff', '-q', '-m', 'Merge feature', 'feature')
        code, out = self.run_tool('--base', before, '--merged')
        self.assert_warned(code, out, '"bare"')

    def test_merged_off_main_is_refused(self):
        self.commit('1', 'work\n\n' + TRAILERED_MSG)
        code, out = self.run_tool('--base', 'main', '--merged')
        self.assertEqual(code, 2, out)
        self.assertIn('REFUSED', out)


class LandingTree(TrailersCase):
    """A landing merges and gates in a detached landing tree (kitlib.gate.land; docs/gate-landing.md
    "Landing", step 5), main never checked out there. `--merged` takes a detached HEAD
    whose first-parent chain from the base is merges only (the landing's), and refuses any other
    detached HEAD and every branch but main."""

    def merge_detached(self, *branches):
        """HEAD detached at main, then each branch merged --no-ff by a trailerless lander merge,
        main itself never moved; main's hash."""
        base = self.git('rev-parse', 'main').strip()
        self.git('checkout', '-q', '--detach', base)
        for b in branches:
            self.git('merge', '--no-ff', '-q', '-m', 'Merge %s' % b, b)
        return base

    def test_the_landers_merge_in_a_detached_landing_tree_is_exempt(self):
        self.commit('1', 'work\n\n' + TRAILERED_MSG)
        base = self.merge_detached('feature')
        self.assertEqual(self.git('rev-parse', 'main').strip(), base)
        code, out = self.run_tool('--base', base, '--merged')
        self.assertEqual(code, 0, out)
        self.assertIn('1 lander merge(s) exempt', out)
        self.assertNotIn(WARNING, out)
        code, out = self.run_tool('--base', base)
        self.assert_warned(code, out, 'merge "Merge feature"')   # without --merged: a warning

    def test_a_batch_of_landing_merges_is_exempt(self):
        self.commit('1', 'work\n\n' + TRAILERED_MSG)
        self.git('checkout', '-q', '-b', 'second', 'main')
        self.write('s.txt', '2')
        self.commit_all('more\n\n' + TRAILERED_MSG)
        base = self.merge_detached('feature', 'second')
        code, out = self.run_tool('--base', base, '--merged')
        self.assertEqual(code, 0, out)
        self.assertIn('2 lander merge(s) exempt', out)

    def test_a_trailerless_branch_commit_still_warns_in_the_landing_tree(self):
        self.commit('1', 'bare')
        base = self.merge_detached('feature')
        code, out = self.run_tool('--base', base, '--merged')
        self.assert_warned(code, out, '"bare"')

    def test_a_detached_branch_commit_is_refused(self):
        self.commit('1', 'work\n\n' + TRAILERED_MSG)
        self.git('checkout', '-q', '--detach')
        code, out = self.run_tool('--base', 'main', '--merged')
        self.assertEqual(code, 2, out)
        self.assertIn('REFUSED', out)
        self.assertIn('HEAD is detached and not a landing merge', out)

    def test_a_detached_sync_merge_is_refused(self):
        # the branch's own merge of main: its first parent is the branch commit, not the base
        self.commit('1', 'work\n\n' + TRAILERED_MSG)
        self.git('checkout', '-q', 'main')
        self.write('g.txt', 'g')
        self.commit_all('main moves\n\n' + TRAILERED_MSG)
        self.git('checkout', '-q', 'feature')
        self.git('merge', '--no-ff', '-q', '-m', 'sync main', 'main')
        self.git('checkout', '-q', '--detach')
        code, out = self.run_tool('--base', 'main', '--merged')
        self.assertEqual(code, 2, out)
        self.assertIn('REFUSED', out)
        self.assertIn('not a landing merge', out)
        self.assertIn('that are not merges', out)

    def test_a_merge_onto_an_older_main_is_refused(self):
        # every first-parent commit a merge, and the oldest not made onto the base
        self.commit('1', 'work\n\n' + TRAILERED_MSG)
        old = self.merge_detached('feature')
        self.git('checkout', '-q', 'main')
        self.write('g.txt', 'g')
        self.commit_all('main moves\n\n' + TRAILERED_MSG)
        self.git('checkout', '-q', '--detach', old)
        self.git('merge', '--no-ff', '-q', '-m', 'Merge feature', 'feature')
        self.assertEqual(self.git('rev-parse', 'HEAD^1').strip(), old)
        code, out = self.run_tool('--base', 'main', '--merged')
        self.assertEqual(code, 2, out)
        self.assertIn('does not start at the base', out)

    def test_a_merge_on_top_of_a_branch_commit_is_refused(self):
        # a landing-shaped merge whose first-parent chain from the base holds a plain commit
        self.commit('1', 'work\n\n' + TRAILERED_MSG)
        base = self.git('rev-parse', 'main').strip()
        self.git('checkout', '-q', '-b', 'side', 'main')
        self.write('s.txt', '2')
        self.commit_all('side\n\n' + TRAILERED_MSG)
        self.git('checkout', '-q', '--detach')
        self.git('merge', '--no-ff', '-q', '-m', 'Merge feature', 'feature')
        code, out = self.run_tool('--base', base, '--merged')
        self.assertEqual(code, 2, out)
        self.assertIn('REFUSED', out)
        self.assertIn('that are not merges', out)

    def test_detached_at_the_base_is_refused(self):
        base = self.git('rev-parse', 'main').strip()
        self.git('checkout', '-q', '--detach', base)
        code, out = self.run_tool('--base', base, '--merged')
        self.assertEqual(code, 2, out)
        self.assertIn('REFUSED', out)
        self.assertIn('with no merge on it', out)

    def test_a_landing_shaped_merge_on_a_feature_branch_is_refused(self):
        # the same shape on a named branch is a branch's merge, never the lander's
        self.commit('1', 'work\n\n' + TRAILERED_MSG)
        base = self.git('rev-parse', 'main').strip()
        self.git('checkout', '-q', '-b', 'other', base)
        self.git('merge', '--no-ff', '-q', '-m', 'Merge feature', 'feature')
        code, out = self.run_tool('--base', base, '--merged')
        self.assertEqual(code, 2, out)
        self.assertIn('REFUSED', out)
        self.assertIn('HEAD is on the branch other', out)
