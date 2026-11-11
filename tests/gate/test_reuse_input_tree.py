"""kitlib.gate.reuse: the input-tree path (docs/gate-landing-policy.md "Gate reuse"), split from test_reuse so
each module's run stays well under the test pool's unit timeout: a one-branch landing whose main
moved since the tip's gate. Fixtures: test_reuse.ReuseCase."""
import datetime
import sys
import unittest
from unittest import mock

from kitlib import gate as G
from kitlib.gate import reuse, runner
from kitlib.landing import land
from kitlib.gate import steps as S
from kitlib.records import gate_ok
from tests.gate import fixtures, test_reuse

ReuseCase = test_reuse.ReuseCase
BRANCH = test_reuse.BRANCH
OK_CMD = test_reuse.OK_CMD
SRC = test_reuse.SRC
UNIT = test_reuse.UNIT
MERGED = test_reuse.MERGED
PLAIN = test_reuse.PLAIN
WHOLE = test_reuse.WHOLE
BASEY = test_reuse.BASEY


# full_cmd differs from cmd: a --full record ran another argv than a landing runs
FULLY = {'name': 'fully', 'always': True, 'globs': ['src/*'], 'pass': '^ok$', 'reuse': True,
         'cmd': OK_CMD, 'full_cmd': ['{python}', '-c', 'print("ok")', '--all']}
# a group whose other member declares no input: the input tree serves gu, never gw
GU = {'name': 'gu', 'always': True, 'globs': ['src/*'], 'cmd': OK_CMD, 'pass': '^ok$',
      'reuse': 'pair'}
GW = {'name': 'gw', 'always': True, 'cmd': OK_CMD, 'pass': '^ok$', 'reuse': 'pair'}


class InputTree(ReuseCase):
    """A one-branch landing whose main moved since the tip's gate takes a step from the tip's
    gate-OK record when every part of the step's input key is the one the record holds: its argv
    (the base written <base>), tools, its inputs at the tip and the merge and at both bases, the
    changed paths and the kit (docs/gate-landing-policy.md "Gate reuse", "The input tree"). A step that declares
    no input, a batch, a docs-only record, a result that was not a pass, or a record without input
    keys serves nothing."""
    GATE = {'steps': [UNIT, BASEY, WHOLE, FULLY, GU, GW, MERGED, PLAIN]}
    KIT = {}        # a default-mode record lands; the merged gate of a plain change may reuse
    ASKING = ('unit', 'basey', 'whole', 'fully', 'gu', 'gw', 'merged')

    def full_tip(self):
        wt, tip = self.branch('feature', dict(SRC))
        out = self.gate(wt, full=True)
        self.assertEqual(out.code, G.OK, out.lines)
        self.assertEqual(gate_ok.mode_of(gate_ok.verify_full(self.repo.root, tip)[0]), 'full')
        return wt, tip

    def move_main(self, files):
        other = self.repo.worktree('other')
        for rel, text in files.items():
            self.repo.write(rel, text, root=other)
        moved = self.repo.commit('main moves', *files, cwd=other)
        self.repo.git('push', '-q', 'origin', '%s:refs/heads/main' % moved)
        return moved

    def retouch(self, tip, step, **parts):
        rec = gate_ok.read(self.repo.root, tip)[0]
        rec['input_keys']['steps'][step].update(parts)
        gate_ok.write(self.repo.root, rec)
        return rec

    def land_as_is(self, tip):
        """A landing of `tip` with the record its branch gate wrote (never marked --full)."""
        self.said[:] = []
        fixtures.queue_review(self.repo.root, BRANCH, tip)
        lander = land.Lander(self.repo.root, BRANCH, None, tip, say=self.said.append,
                             git_env=self.env, book_kw=self.book_kw, log_dir=self.logs)
        return lander, lander.run()

    def tree_lines(self):
        return [l for l in self.lines() if l.startswith(reuse.PREFIX + 'INPUT TREE:')]

    def line_of(self, word, name):
        found = [l for l in self.lines(word) if l.startswith(reuse.PREFIX + '%s %s: ' % (word, name))]
        self.assertEqual(len(found), 1, self.said)
        return found[0]

    def assert_served(self, tip, mode, names):
        rec = gate_ok.read(self.repo.root, tip)[0]
        ended = rec['ended_utc']
        self.assertEqual(self.tree_lines(), [
            reuse.PREFIX + 'INPUT TREE: the %s record of %s at %s (base %s, ended %s) serves a '
            'step whose key parts all match' % (mode, BRANCH, tip[:10], rec['base'][:10], ended)],
            self.said)
        for n in names:
            self.assertIn(reuse.PREFIX + 'HIT %s: the input tree of the %s record of %s (ended %s)'
                          % (n, mode, tip[:10], ended), self.lines())
            self.assertIn('REUSED %s: passed in the %s gate of %s on the same inputs, base side '
                          'and change (ended %s)' % (n, mode, tip[:10], ended), self.lines())

    def test_the_record_holds_a_key_for_each_step_it_can_serve(self):
        _, tip = self.full_tip()
        keys = gate_ok.read(self.repo.root, tip)[0]['input_keys']
        self.assertEqual(keys['schema'], reuse.INPUT_SCHEMA)
        # whole and gw declare no input, merged has merged_args, plain asks for no reuse
        self.assertEqual(sorted(keys['steps']), ['basey', 'fully', 'gu', 'unit'])
        for parts in keys['steps'].values():
            self.assertEqual(sorted(parts), sorted(reuse.INPUT_PARTS))
            for digest in parts.values():
                self.assertRegex(digest, '^[0-9a-f]{64}$')

    def test_main_moved_on_no_input_takes_steps_from_the_tip_record(self):
        _, tip = self.full_tip()
        moved = self.move_main({'o.py': 'o\n'})
        lander, out = self.land(BRANCH, tip)
        self.assertEqual(out.code, G.OK, out.lines)
        self.assertEqual(lander.expect_main, moved)
        self.assertEqual(lander.tip_records[tip]['commit'], tip)
        self.assertEqual(out.verdict, 'GATE OK (reused: unit, basey)')
        self.assert_served(tip, 'full', ('unit', 'basey'))
        self.assert_not_ran(lander.gate_logs, 'unit', 'basey')
        self.assert_ran(lander.gate_logs, 'whole', 'fully', 'gu', 'gw', 'merged', 'plain')
        # the --full record ran fully's full_cmd: a landing runs its cmd
        self.assertTrue(self.line_of('MISS', 'fully').endswith('; input tree: argv differs'),
                        self.said)
        self.assertEqual(self.line_of('NONE', 'whole'), reuse.PREFIX + 'NONE whole: no declared '
                         'inputs (no "globs" and no "content".globs)')
        # nothing the record served is stored
        self.assertFalse([l for l in self.lines('STORED') if ' unit:' in l or ' basey:' in l],
                         self.said)
        rec, why = gate_ok.verify(self.repo.root, out.merge)
        self.assertIsNone(why)
        self.assertEqual({s['name']: s['result'] for s in rec['steps']}['basey'], 'reused')

    def test_a_group_with_a_member_the_record_cannot_serve_runs_whole(self):
        _, tip = self.full_tip()
        self.move_main({'o.py': 'o\n'})
        lander, out = self.land(BRANCH, tip)
        self.assertEqual(out.code, G.OK, out.lines)
        self.assertEqual(self.line_of('MISS', 'gu'),
                         reuse.PREFIX + 'MISS gu: gw of its group pair runs')
        self.assert_ran(lander.gate_logs, 'gu', 'gw')

    def test_main_adding_an_input_runs_the_steps(self):
        _, tip = self.full_tip()
        self.move_main({'src/b.py': 'b = 1\n'})
        lander, out = self.land(BRANCH, tip)
        self.assertEqual(out.code, G.OK, out.lines)
        self.assertEqual(out.verdict, 'GATE OK')
        self.assert_ran(lander.gate_logs, 'unit', 'basey')
        self.assertTrue(self.line_of('NONE', 'basey').endswith(
            '; input tree: head_inputs, base_inputs differ'), self.said)
        self.assertTrue(self.line_of('MISS', 'unit').endswith(
            '; input tree: head_inputs, base_inputs differ'), self.said)

    def test_each_part_that_differs_is_named(self):
        _, tip = self.full_tip()
        self.move_main({'o.py': 'o\n'})
        self.retouch(tip, 'unit', head_inputs='0' * 64)
        self.retouch(tip, 'basey', base_inputs='0' * 64, kit='1' * 64)
        lander, out = self.land(BRANCH, tip)
        self.assertEqual(out.verdict, 'GATE OK')
        self.assert_ran(lander.gate_logs, 'unit', 'basey')
        self.assertTrue(self.line_of('MISS', 'unit').endswith('; input tree: head_inputs differs'),
                        self.said)
        self.assertTrue(self.line_of('NONE', 'basey').endswith(
            '; input tree: base_inputs, kit differ'), self.said)

    def test_a_step_the_record_did_not_pass_runs(self):
        _, tip = self.full_tip()
        self.move_main({'o.py': 'o\n'})
        rec = gate_ok.read(self.repo.root, tip)[0]
        rec['steps'] = [dict(s, result='reused') if s['name'] == 'basey' else s
                        for s in rec['steps']]
        gate_ok.write(self.repo.root, rec)
        lander, out = self.land(BRANCH, tip)
        self.assertEqual(out.verdict, 'GATE OK (reused: unit)')
        self.assert_ran(lander.gate_logs, 'basey')
        self.assertTrue(self.line_of('NONE', 'basey').endswith(
            '; input tree: the record says reused'), self.said)

    def test_a_record_without_input_keys_serves_nothing(self):
        _, tip = self.full_tip()
        rec = gate_ok.read(self.repo.root, tip)[0]
        self.assertIn('basey', rec['input_keys']['steps'])
        del rec['input_keys']                       # an older kit's record
        gate_ok.write(self.repo.root, rec)
        self.move_main({'o.py': 'o\n'})
        lander, out = self.land(BRANCH, tip)
        self.assertEqual(out.verdict, 'GATE OK')
        self.assertEqual(self.tree_lines(), [], self.said)
        self.assert_ran(lander.gate_logs, 'unit', 'basey')

    def test_a_batch_reads_no_input_tree(self):
        _, tip = self.full_tip()
        self.move_main({'o.py': 'o\n'})
        real = reuse.planner
        with mock.patch.object(reuse, 'input_tree', wraps=reuse.input_tree) as read, \
                mock.patch.object(reuse, 'planner',
                                  side_effect=lambda *a, **k: real(*a, **dict(k, batch=2))):
            lander, out = self.land(BRANCH, tip)
        read.assert_not_called()
        self.assertEqual(out.verdict, 'GATE OK')
        self.assert_ran(lander.gate_logs, 'unit', 'basey')

    def test_a_default_mode_record_serves(self):
        wt, tip = self.branch('feature', dict(SRC))
        self.assertEqual(self.gate(wt).code, G.OK)
        self.assertEqual(gate_ok.mode_of(gate_ok.read(self.repo.root, tip)[0]), 'select')
        self.move_main({'o.py': 'o\n'})
        lander, out = self.land_as_is(tip)
        self.assertEqual(out.code, G.OK, out.lines)
        # unit and fully hit the cache the branch gate stored; basey, which names {base} and is
        # never stored, comes from the record
        self.assertEqual(out.verdict, 'GATE OK (reused: unit, basey, fully)')
        self.assert_served(tip, 'select', ('basey',))
        self.assert_not_ran(lander.gate_logs, 'unit', 'basey', 'fully')

    def test_the_record_must_be_one_the_landing_takes(self):
        _, tip = self.full_tip()
        moved = self.move_main({'o.py': 'o\n'})
        lander, out = self.land(BRANCH, tip)
        self.assertEqual(out.code, G.OK, out.lines)
        cfg = runner.kconfig.load(self.repo.root)
        rec = gate_ok.read(self.repo.root, tip)[0]

        def why(merge=out.merge, base=moved, **changes):
            got, line = reuse.input_tree(self.repo.root, cfg, BRANCH, tip, dict(rec, **changes),
                                         base, merge)
            self.assertIsNone(got)
            return line

        head = reuse.PREFIX[:0] + 'INPUT TREE: the %s record of ' + BRANCH + ' at ' + tip[:10] \
            + ' is not used: %s'
        self.assertEqual(why(mode='docs', full=False), head % (
            'docs', 'only a --full or default-mode record serves (mode docs)'))
        self.assertEqual(why(gate_config='0' * 64), head % (
            'full', 'config: the record was made with another gate config'))
        self.assertEqual(why(base='d' * 40), head % (
            'full', 'parents: the merge %s is not the merge of %s onto dddddddddd'
            % (out.merge[:10], tip[:10])))
        old = gate_ok.utc(datetime.datetime.now(datetime.timezone.utc)
                          - datetime.timedelta(hours=25))
        self.assertEqual(why(ended_utc=old), head % (
            'full', 'age: the record ended %s, not within "landing".reuse_max_age_h (24)' % old))
        self.assertEqual(reuse.input_tree(self.repo.root, cfg, BRANCH, tip, None, moved,
                                          out.merge), (None, None))      # a FLAKE-only tip
        self.assertEqual(reuse.input_tree(self.repo.root, cfg, BRANCH, tip, dict(
            rec, commit='e' * 40), moved, out.merge), (None, None))      # another commit's

    def test_the_bars_are_relaxed_for_base_only(self):
        step = S.Step('basey', [sys.executable, '-c', 'print("ok")', 'b' * 40], None,
                      self.repo.root, [], [], (0,), False, False, [], False)
        self.assertIsNone(reuse.undeclared(BASEY, step, 'b' * 40, input_tree=True))
        self.assertEqual(reuse.undeclared(WHOLE, step, None, input_tree=True),
                         'no declared inputs (no "globs" and no "content".globs)')
        check = S.Step('pins', None, 'pins', '.', [], [], (0,), False, False, [], False)
        self.assertEqual(reuse.undeclared({'name': 'pins', 'check': 'pins', 'reuse': True}, check,
                                          None, input_tree=True), 'a check step')


if __name__ == '__main__':
    unittest.main()
