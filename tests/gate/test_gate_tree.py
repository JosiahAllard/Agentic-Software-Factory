"""kitlib.gate.runner.gated: the lock run of a gate records the git tree it tested (book's `tree`;
Slot.note_tree in a landing's held slot) and its history line keeps it: a branch gate's is the
tree of HEAD when the work tree is clean, and none when it is not (uncommitted files test no
commit's tree); a landing's is its merge commit's tree. The daily full run gates its worktree at
the day's commit the same way, so its run has that commit's tree. The history line also keeps
`gate` and `priority`, which the branch gate budget reads (kitlib.gate.budget): a branch gate's has
gate and no priority, a landing's has priority "land". A landing's also has its gate_mode
("merged", "full" or "docs"), noted on the slot it holds."""
import os

from kitlib import gate as G
from kitlib.gate import runner
from tests.gate import test_land
from tests.gate.test_land_tip_gated import SELECT_GATE

BRANCH = test_land.BRANCH


class GateTree(test_land.LandCase):
    def tree(self, rev, cwd=None):
        return self.repo.git('rev-parse', '%s^{tree}' % rev, cwd=cwd or self.repo.root)

    def trees(self):
        """The `tree` of every history line on the scratch store, oldest first."""
        return [d.get('tree') for d in self.store.history_records()]

    def test_a_clean_branch_gate_records_its_heads_tree(self):
        wt, tip = self.branch('feature', {'a.py': 'x = 1\n'})
        out = runner.branch_gate(wt, book_kw=self.book_kw, log_dir=self.logs)
        self.assertEqual(out.code, G.OK, out.lines)
        self.assertEqual(self.trees(), [self.tree(tip, wt)])

    def test_a_branch_gate_of_uncommitted_files_records_no_tree(self):
        wt, _tip = self.branch('feature', {'a.py': 'x = 1\n'})
        self.repo.write('b.py', 'y = 2\n', root=wt)          # untracked: not the commit's tree
        out = runner.branch_gate(wt, book_kw=self.book_kw, log_dir=self.logs)
        self.assertEqual(out.code, G.OK, out.lines)
        self.assertEqual(self.trees(), [None])

    def test_tested_tree_is_none_when_git_cannot_answer(self):
        self.assertIsNone(runner.tested_tree(os.path.join(self.tmp, 'no-such-checkout')))

    def test_a_landing_records_its_merge_trees_on_the_slot_it_holds(self):
        _wt, tip = self.branch('feature', {'a.py': 'x = 1\n'})
        out = self.lander(BRANCH, self.main, tip).run()
        self.assertEqual(out.code, G.OK, out.lines)
        merged = self.main_now()
        self.assertNotEqual(merged, tip)
        self.assertIn(self.tree(merged), self.trees())

    def test_the_history_line_keeps_gate_and_priority(self):
        wt, tip = self.branch('feature', {'a.py': 'x = 1\n'})
        out = runner.branch_gate(wt, book_kw=self.book_kw, log_dir=self.logs)
        self.assertEqual(out.code, G.OK, out.lines)
        out = self.lander(BRANCH, self.main, tip).run()
        self.assertEqual(out.code, G.OK, out.lines)
        got = [(d.get('gate'), d.get('priority')) for d in self.store.history_records()]
        self.assertEqual(got[0], (True, None))
        self.assertIn((True, 'land'), got[1:])


class LandingGateMode(test_land.LandCase):
    """The slot a landing holds notes its merged gate's gate_mode (Slot.note_tree): "merged";
    "full" for a landing whose merge changes a gate config file (here the hook module), whose
    merged gate runs every step in full; "docs" for a docs-only change under the base's
    "gate".docs_only."""
    GATE = SELECT_GATE
    KIT = dict(test_land.LandCase.KIT, hook={'module': 'kit_hooks'})
    HOOKS = 'GATE_CHECKS = {}\n'

    def setUp(self):
        test_land.LandCase.setUp(self)
        self.repo.write('tools/kit_hooks.py', self.HOOKS)
        self.main = self.repo.commit('hook module', 'tools/kit_hooks.py')
        self.repo.git('push', '-q', 'origin', 'main')

    def landed_modes(self, files, **kw):
        _wt, tip = self.branch('feature', files)
        out = self.lander(BRANCH, self.main, tip, **kw).run()
        self.assertEqual(out.code, G.OK, out.lines)
        return [d.get('gate_mode') for d in self.store.history_records()
                if d.get('priority') == 'land']

    def test_a_landing_notes_the_merged_gate_mode_on_the_slot_it_holds(self):
        self.assertEqual(self.landed_modes({'a.py': 'x = 1\n'}), ['merged'])

    def test_a_gate_config_landing_notes_full(self):
        self.assertEqual(self.landed_modes({'tools/kit_hooks.py': '# changed\n' + self.HOOKS}),
                         ['full'])

    def test_a_docs_only_landing_notes_docs(self):
        self.assertEqual(self.landed_modes({'docs/guide.md': 'words\n'}), ['docs'])
