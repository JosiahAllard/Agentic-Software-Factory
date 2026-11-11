"""kitlib.gate.reuse: the same-tree path (docs/gate-landing-policy.md "Gate reuse"), split from test_reuse so each
module's run stays well under the test pool's unit timeout: a landing whose merge holds the very
tree its tip passed gate.py --full on. Fixtures: test_reuse.ReuseCase."""
import datetime
import sys
import unittest
from unittest import mock

from kitlib import gate as G
from kitlib.gate import reuse, runner
from kitlib.landing import land, verdicts
from kitlib.gate import steps as S
from kitlib.records import gate_ok
from tests.gate import test_reuse

ReuseCase = test_reuse.ReuseCase
BRANCH = test_reuse.BRANCH
SRC = test_reuse.SRC
UNIT = test_reuse.UNIT
MERGED = test_reuse.MERGED
PLAIN = test_reuse.PLAIN
WHOLE = test_reuse.WHOLE
BASEY = test_reuse.BASEY
NOT_USED = 'SAME TREE %s: the --full record of ' + BRANCH + ' at %s is not used: %s'


class SameTree(ReuseCase):
    """A landing whose merge holds the very tree its tip passed gate.py --full on takes the steps
    the cache does not serve from the tip's gate-OK record (docs/gate-landing-policy.md "Gate reuse", "The same
    tree"): only a one-branch, non-strict landing, and only the record of the same base, gate
    config and kit, within "landing".reuse_max_age_h."""
    GATE = {'steps': [UNIT, BASEY, WHOLE, MERGED, PLAIN]}
    ASKING = ('unit', 'basey', 'whole', 'merged')

    def setUp(self):
        ReuseCase.setUp(self)
        # the same tree alone: its records carry no input keys (the input tree: class InputTree)
        p = mock.patch.object(reuse, 'record_keys', return_value=(None, None))
        p.start()
        self.addCleanup(p.stop)

    def full_tip(self):
        wt, tip = self.branch('feature', dict(SRC))
        out = self.gate(wt, full=True)
        self.assertEqual(out.code, G.OK, out.lines)
        rec, why = gate_ok.verify_full(self.repo.root, tip)
        self.assertIsNone(why)
        self.assertEqual(gate_ok.mode_of(rec), 'full')
        return wt, tip

    def tree(self, rev):
        return self.repo.git('rev-parse', rev + '^{tree}')

    def retouch(self, tip, **changes):
        rec = gate_ok.read(self.repo.root, tip)[0]
        rec.update(changes)
        gate_ok.write(self.repo.root, rec)
        return rec

    def same_lines(self):
        return [l for l in self.lines() if l.startswith(reuse.PREFIX + 'SAME TREE ')]

    def assert_unused(self, out, lander, tip, why, reused=None):
        """The trees match, the record serves nothing for `why`, and the steps the record would
        have served ran."""
        self.assertEqual(out.code, G.OK, out.lines)
        self.assertEqual(self.same_lines(), [reuse.PREFIX + NOT_USED % (
            self.tree(tip)[:10], tip[:10], why)], self.said)
        self.assertEqual(out.verdict, 'GATE OK' + (' (reused: %s)' % reused if reused else ''))
        self.assert_ran(lander.gate_logs, *[n for n in ('basey', 'whole') if n != reused])

    def test_a_same_tree_landing_takes_steps_from_the_tip_full_record(self):
        _, tip = self.full_tip()
        ended = gate_ok.read(self.repo.root, tip)[0]['ended_utc']
        lander, out = self.land(BRANCH, tip)
        self.assertEqual(out.code, G.OK, out.lines)
        self.assertEqual(self.tree(out.merge), self.tree(tip))
        self.assertEqual(out.verdict, 'GATE OK (reused: unit, basey, whole)')
        self.assert_not_ran(lander.gate_logs, 'unit', 'basey', 'whole')
        self.assert_ran(lander.gate_logs, 'merged', 'plain')         # merged_args; no reuse
        tree = self.tree(tip)[:10]
        self.assertEqual(self.same_lines(), [
            reuse.PREFIX + 'SAME TREE %s: %s at %s passed gate.py --full on this tree (record '
            'ended %s)' % (tree, BRANCH, tip[:10], ended)], self.said)
        for n in ('unit', 'basey', 'whole'):
            self.assertIn(reuse.PREFIX + 'HIT %s: the --full record of %s (same tree %s, ended %s)'
                          % (n, tip[:10], tree, ended), self.lines())
            self.assertIn('REUSED %s: passed in the --full gate of %s on the same tree (ended %s)'
                          % (n, tip[:10], ended), self.lines())
        # nothing the record served is stored: the relaxation never reaches the cache
        stored = self.lines('STORED')
        self.assertEqual(len(stored), 1, self.said)
        self.assertTrue(stored[0].startswith(reuse.PREFIX + 'STORED merged: pass, key '), stored)
        rec, why = gate_ok.verify(self.repo.root, out.merge)
        self.assertIsNone(why)
        self.assertEqual({s['name']: s['result'] for s in rec['steps']},
                         {'unit': 'reused', 'basey': 'reused', 'whole': 'reused',
                          'merged': 'pass', 'plain': 'pass'})
        self.assertIs(rec['full'], False)
        # the same tree as the merge of the tip onto another base is not the same landing
        cfg = runner.kconfig.load(self.repo.root)
        self.assertEqual(reuse.same_tree(self.repo.root, cfg, BRANCH, tip, 'd' * 40, out.merge),
                         (None, NOT_USED % (tree, tip[:10], 'the merge %s is not the merge of %s '
                                            'onto dddddddddd' % (out.merge[:10], tip[:10]))))

    def test_main_moved_runs_every_step_and_relaxes_nothing(self):
        _, tip = self.full_tip()
        other = self.repo.worktree('other')
        self.repo.write('o.py', 'o\n', root=other)
        moved = self.repo.commit('other', 'o.py', cwd=other)
        self.repo.git('push', '-q', 'origin', '%s:refs/heads/main' % moved)
        lander, out = self.land(BRANCH, tip)
        self.assertEqual(out.code, G.OK, out.lines)
        self.assertNotEqual(self.tree(out.merge), self.tree(tip))
        self.assertEqual(out.verdict, 'GATE OK')
        self.assertEqual(self.same_lines(), [], self.said)
        self.assertIn(reuse.PREFIX + 'NONE basey: its command names {base}: it reads git history',
                      self.lines())
        self.assertIn(reuse.PREFIX + 'NONE whole: no declared inputs (no "globs" and no '
                      '"content".globs)', self.lines())
        self.assert_ran(lander.gate_logs, 'unit', 'basey', 'whole', 'merged', 'plain')
        cfg = runner.kconfig.load(self.repo.root)
        self.assertEqual(reuse.same_tree(self.repo.root, cfg, BRANCH, tip, moved, out.merge),
                         (None, None))

    def test_the_relaxation_is_for_the_same_tree_only(self):
        cfg = runner.kconfig.load(self.repo.root)
        step = S.Step('basey', [sys.executable, '-c', 'print("ok")', 'b' * 40], None,
                      self.repo.root, [], [], (0,), False, False, [], False)
        self.assertEqual(reuse.undeclared(BASEY, step, 'b' * 40),
                         'its command names {base}: it reads git history')
        self.assertIsNone(reuse.undeclared(BASEY, step, 'b' * 40, same_tree=True))
        self.assertEqual(reuse.undeclared(WHOLE, step, None),
                         'no declared inputs (no "globs" and no "content".globs)')
        self.assertIsNone(reuse.undeclared(WHOLE, step, None, same_tree=True))
        self.assertIsNone(reuse._from_record(None, BASEY, step))         # no same tree: nothing
        with self.assertRaises(reuse.NoInputs):
            reuse.step_key(self.repo.root, cfg, WHOLE, step, ['src/a.py'], {'files': '0' * 64})
        check = S.Step('pins', None, 'pins', '.', [], [], (0,), False, False, [], False)
        same = reuse.SameTree({'steps': [{'name': 'pins', 'result': 'pass'}],
                               'ended_utc': '2026-10-11T12:00:00Z'}, 'a' * 40, 'c' * 40)
        self.assertEqual(reuse.undeclared({'name': 'pins', 'check': 'pins', 'reuse': True}, check,
                                          None, same_tree=True), 'a check step')
        self.assertIsNone(reuse._from_record(same, {'name': 'pins', 'check': 'pins',
                                                    'reuse': True}, check))

    def test_a_kit_change_since_the_tip_gate_serves_nothing(self):
        _, tip = self.full_tip()
        self.ident['commit'] = 'b' * 40
        lander, out = self.land(BRANCH, tip)
        self.assert_unused(out, lander, tip, 'the record was made by another kit (aaaaaaaaaa)')
        self.assert_ran(lander.gate_logs, 'unit')

    def unclean(self, record, landing):
        """The tip gated --full by a kit whose clean is `record`, landed by one whose clean is
        `landing`: the record serves nothing and every step runs."""
        self.ident['clean'] = record
        _, tip = self.full_tip()
        self.assertIs(gate_ok.read(self.repo.root, tip)[0]['kit']['clean'], record)
        self.ident['clean'] = landing
        lander, out = self.land(BRANCH, tip)
        self.assert_unused(out, lander, tip, "the record's kit and the landing's kit are the same "
                           'commit but differ in uncommitted state (record clean=%s, landing '
                           'clean=%s)' % (record, landing))
        self.assert_ran(lander.gate_logs, 'unit')

    def test_a_kit_with_uncommitted_files_serves_nothing(self):
        self.unclean(False, False)

    def test_a_kit_git_cannot_read_serves_nothing(self):
        self.unclean(None, None)

    def test_a_record_of_an_unclean_kit_serves_a_clean_one_nothing(self):
        self.unclean(False, True)

    def test_a_record_of_a_clean_kit_serves_an_unclean_one_nothing(self):
        self.unclean(True, None)

    def test_an_old_record_serves_nothing(self):
        _, tip = self.full_tip()
        old = gate_ok.utc(datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=25))
        self.retouch(tip, ended_utc=old)
        lander, out = self.land(BRANCH, tip)
        self.assert_unused(out, lander, tip, 'the record ended %s, not within "landing".'
                           'reuse_max_age_h (24)' % old)

    def test_a_record_a_load_node_ran_serves_nothing(self):
        # gate_ok.import_record: a node's record lands its tip, but no step is served from it
        _, tip = self.full_tip()
        self.retouch(tip, ran_on={'node': 'box1', 'host_key': 'SHA256:' + 'k' * 43,
                                  'job': 'j-20261028T120000Z-abcdef'}, input_keys=None)
        lander, out = self.land(BRANCH, tip)
        self.assert_unused(out, lander, tip, 'the record ran on a load node (box1)')
        self.assert_ran(lander.gate_logs, 'unit')

    def test_a_record_of_another_base_serves_nothing(self):
        _, tip = self.full_tip()
        self.retouch(tip, base='c' * 40)
        lander, out = self.land(BRANCH, tip)
        self.assert_unused(out, lander, tip, 'the record gated against cccccccccc, not %s'
                           % self.main[:10])

    def test_a_record_of_another_gate_config_serves_nothing(self):
        _, tip = self.full_tip()
        self.retouch(tip, gate_config='0' * 64)
        lander, out = self.land(BRANCH, tip)
        self.assert_unused(out, lander, tip, 'the record was made with another gate config')

    def test_a_select_record_serves_nothing(self):
        # the landing fixture marks the select record full; its mode still says select
        wt, tip = self.branch('feature', dict(SRC))
        self.assertEqual(self.gate(wt).code, G.OK)
        lander, out = self.land(BRANCH, tip)
        self.assert_unused(out, lander, tip, 'the record is not a --full record (mode None)',
                           reused='unit')                            # the cache's hit stays

    def test_a_step_the_record_warned_runs(self):
        _, tip = self.full_tip()
        rec = gate_ok.read(self.repo.root, tip)[0]
        self.retouch(tip, steps=[dict(s, result='warn') if s['name'] == 'unit' else s
                                 for s in rec['steps']])
        lander, out = self.land(BRANCH, tip)
        self.assertEqual(out.verdict, 'GATE OK (reused: basey, whole)')
        self.assert_ran(lander.gate_logs, 'unit')
        self.assertEqual(len(self.lines('MISS')), 2, self.said)      # unit and merged

    def test_an_error_while_reading_the_record_runs_every_step(self):
        _, tip = self.gated_branch()            # the cache holds unit: the error drops it too
        with mock.patch.object(reuse, 'same_tree', side_effect=RuntimeError('boom')):
            lander, out = self.land(BRANCH, tip)
        self.assert_none(out, lander.gate_logs, 'RuntimeError: boom')

    def test_a_batch_reads_no_record(self):
        _, tip = self.full_tip()
        real = reuse.planner
        with mock.patch.object(reuse, 'same_tree', wraps=reuse.same_tree) as read, \
                mock.patch.object(reuse, 'planner',
                                  side_effect=lambda *a, **k: real(*a, **dict(k, batch=2))):
            lander, out = self.land(BRANCH, tip)
        read.assert_not_called()
        self.assertEqual(out.verdict, 'GATE OK')
        self.assert_ran(lander.gate_logs, 'unit', 'basey', 'whole')

    def test_a_strict_tip_reads_no_record(self):
        path = reuse.STRICT_DEFAULTS[0].replace('**', 'strict.py')
        wt, tip = self.branch('feature', dict(SRC, **{path: 'x = 1\n'}))
        self.assertEqual(self.gate(wt, full=True).code, G.OK)
        self.assertIsNotNone(gate_ok.verify_full(self.repo.root, tip)[0])
        common, _ = land.repo.common_dir(self.repo.root)
        verdicts.add(common, {'branch': BRANCH, 'tip': tip, 'verdict': 'LAND', 'risk': 'High',
                              'reviewer': 'pm-kit:reviewer', 'reviewer_model': 'opus',
                              'reviewer_id': 'agent-r1', 'author': 'Kit Test / strong-worker',
                              'range': '%s..%s' % (self.main[:10], tip), 'session': 's',
                              'records': []})
        with mock.patch.object(reuse, 'same_tree', wraps=reuse.same_tree) as read:
            lander, out = self.land(BRANCH, tip)
        read.assert_not_called()
        self.assert_none(out, lander.gate_logs, '--fresh')


if __name__ == '__main__':
    unittest.main()
