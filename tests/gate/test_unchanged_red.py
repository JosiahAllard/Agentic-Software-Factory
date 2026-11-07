"""An unchanged red tree (docs/gate.md "An unchanged red tree"): every branch gate of a clean tree
writes a red-tree mark (kitlib.gate.rerun.mark); a later branch gate of the same tree, gate config
and kit that would only repeat a failed one is REFUSED before it books, and again at its turn,
unless --again. A pass of the same tree and mode supersedes the mark."""
import json
import os
from unittest import mock

import kitlib.config as kconfig
from kitlib import gate as G
from kitlib.gate import budget, gatecap, rerun, runner
from kitlib.lock import run as lockrun
from tests.gate import fixtures, test_targeted_rerun

BRANCH = 'kit/feature'
# A step that prints ok and exits 1 while the file UNCHANGED_STATE names exists: the same tree
# fails or passes as the test says.
STATE = ['{python}', '-c', 'import os, sys; print("ok"); '
         'p = os.environ.get("UNCHANGED_STATE"); sys.exit(1 if p and os.path.exists(p) else 0)']
OK = ['{python}', '-c', 'print("ok")']
GATE = {'steps': [{'name': 'unit', 'always': True, 'cmd': STATE, 'pass': '^ok$'},
                  {'name': 'other', 'globs': ['other/**'], 'cmd': OK, 'pass': '^ok$'}]}


class Case(fixtures.GateRepoCase):
    GATE = GATE

    def setUp(self):
        fixtures.GateRepoCase.setUp(self)
        self.wt, self.tip = self.branch('feature', {'a.py': 'x = 1\n'})
        self.state = os.path.join(self.tmp, 'unchanged-state')
        self.env['UNCHANGED_STATE'] = self.state        # the slot's environment (book_kw env)
        self.red(True)

    def red(self, on):
        if on:
            with open(self.state, 'w', encoding='utf-8') as f:
                f.write('red\n')
        elif os.path.exists(self.state):
            os.remove(self.state)

    def gate(self, **kw):
        said = []
        out = runner.branch_gate(self.wt, book_kw=self.book_kw, log_dir=self.logs,
                                 say=said.append, **kw)
        out.said = said
        return out

    def tree(self):
        return self.repo.git('rev-parse', 'HEAD^{tree}', cwd=self.wt)

    def mark_of(self, mode):
        path = rerun.mark_path(self.wt, self.tree(), mode)
        if not os.path.exists(path):
            return None
        with open(path, encoding='utf-8') as f:
            return json.load(f)

    def assert_refused(self, out, booked):
        self.assertEqual(out.code, G.REFUSED, out.lines)
        self.assertEqual(len(self.book_calls), booked, 'it booked')
        self.assertEqual(out.facts['no_record'], runner.UNCHANGED_NO_RECORD)
        self.assertIn('REFUSED: the tree of %s at %s' % (BRANCH, self.tip[:10]), out.lines[0])
        self.assertTrue(out.lines[0].endswith('; nothing queued'), out.lines[0])
        self.assertTrue(out.lines[1].startswith('Instead: fix it and commit'), out.lines[1])
        # It comes after the branch's `running` result record: its `refused` record replaces the
        # red one, and its summary, the REFUSED line, names the red gate's log folder.
        with open(out.facts['result_record'], encoding='utf-8') as f:
            rec = json.load(f)
        self.assertEqual((rec['verdict'], rec['summary']), ('refused', out.lines[0]))
        self.assertIn('; logs: ', rec['summary'])


class Marks(Case):
    def test_a_failed_gate_of_a_clean_tree_writes_a_fail_mark(self):
        out = self.gate(full=True)
        self.assertEqual(out.code, G.FAILED, out.lines)
        rec = self.mark_of('full')
        self.assertIsNotNone(rec)
        cfg = kconfig.load(self.wt)
        self.assertEqual((rec['tree'], rec['commit'], rec['branch'], rec['mode'], rec['verdict'],
                          rec['failed'], rec['log_dir']),
                         (self.tree(), self.tip, BRANCH, 'full', 'fail', ['unit'],
                          out.facts['log_dir']))
        self.assertEqual(rec['gate_config'],
                         rerun.gate_ok.config_hash(runner.gate_config(cfg)))
        self.assertEqual(rec['kit'], runner.kit_identity())
        self.assertTrue(rec['ended'])

    def test_a_pass_of_the_same_tree_and_mode_supersedes_the_fail(self):
        """--full, so the gate cache reuses no step and each gate runs `unit`."""
        self.gate(full=True)
        self.red(False)
        out = self.gate(full=True, again=True)
        self.assertEqual(out.code, G.OK, out.lines)
        self.assertEqual(self.mark_of('full')['verdict'], 'pass')
        self.red(True)
        out = self.gate(full=True)
        self.assertEqual(out.code, G.FAILED, out.lines)     # ran: the latest gate passed
        self.assertEqual(len(self.book_calls), 3)

    def test_an_unclean_tree_writes_no_mark_and_is_never_refused(self):
        self.gate()
        self.repo.write('loose.txt', 'x\n', root=self.wt)
        out = self.gate()
        self.assertEqual(out.code, G.FAILED, out.lines)
        self.assertEqual(len(self.book_calls), 2)


class Refused(Case):
    def test_the_same_full_gate_again_is_refused_before_booking(self):
        self.gate(full=True)
        before = len(self.store.history_records())
        out = self.gate(full=True)
        self.assert_refused(out, 1)
        self.assertEqual(len(self.store.history_records()), before)
        self.assertIn('failed its last full gate', out.lines[0])
        self.assertIn('--rerun', out.lines[1])
        self.assertIn('gate.py -C %s --full --again' % self.wt, out.lines[1])

    def test_the_same_select_gate_again_is_refused_and_names_no_rerun(self):
        self.gate()
        out = self.gate()
        self.assert_refused(out, 1)
        self.assertNotIn('--rerun', out.lines[1])
        self.assertTrue(out.lines[1].endswith('gate.py -C %s --again' % self.wt), out.lines[1])

    def test_again_runs_it(self):
        self.gate(full=True)
        out = self.gate(full=True, again=True)
        self.assertEqual(out.code, G.FAILED, out.lines)
        self.assertEqual(len(self.book_calls), 2)

    def test_a_new_commit_of_the_same_tree_is_refused(self):
        self.gate()
        self.repo.git('commit', '--allow-empty', '-q', '-m', 'same tree', cwd=self.wt)
        self.tip = self.repo.git('rev-parse', 'HEAD', cwd=self.wt)
        self.assert_refused(self.gate(), 1)

    def test_a_changed_tree_runs(self):
        self.gate()
        self.repo.write('a.py', 'x = 2\n', root=self.wt)
        self.repo.commit('fix', 'a.py', cwd=self.wt)
        self.assertEqual(self.gate().code, G.FAILED)
        self.assertEqual(len(self.book_calls), 2)

    def test_another_gate_config_or_kit_runs(self):
        """Each against a red mark of the real config and kit (the --again gate before it)."""
        self.gate()
        real = runner.gate_config
        with mock.patch.object(runner, 'gate_config', lambda cfg: dict(real(cfg), extra=1)):
            self.assertEqual(self.gate().code, G.FAILED)
        self.assertEqual(self.gate(again=True).code, G.FAILED)
        with mock.patch.object(runner, 'kit_identity', return_value={'commit': 'f' * 40}):
            self.assertEqual(self.gate().code, G.FAILED)
        self.assertEqual(len(self.book_calls), 4)

    def test_an_unreadable_mark_lets_the_gate_run_and_warns(self):
        self.gate()
        with open(rerun.mark_path(self.wt, self.tree(), 'select'), 'w', encoding='utf-8') as f:
            f.write('{not json')
        out = self.gate()
        self.assertEqual(out.code, G.FAILED, out.lines)
        self.assertTrue(any(s.startswith('WARNING: the red-tree mark is not read')
                            for s in out.said), out.said)


class Modes(Case):
    def test_a_full_after_a_red_select_runs(self):
        self.gate()
        out = self.gate(full=True)
        self.assertEqual(out.code, G.FAILED, out.lines)
        self.assertEqual(len(self.book_calls), 2)

    def test_a_select_after_a_red_full_is_refused_only_when_it_runs_a_failed_step(self):
        self.gate(full=True)
        out = self.gate(only=['other'])
        self.assertEqual(out.code, G.OK, out.lines)         # `other` never failed
        self.assert_refused(self.gate(only=['unit']), 2)
        self.assert_refused(self.gate(), 2)


class AtItsTurn(Case):
    def test_a_gate_whose_tree_failed_while_it_queued_is_dropped_not_started(self):
        """No mark when it asked before booking; a gate of the same tree ran ahead of it and
        failed while it waited for its turn (gatecap.wait_turn): it is released `not started`."""
        cfg = kconfig.load(self.wt)

        def wait_turn(*args, **kw):
            key = rerun.mark_key(cfg, 'select', None, None)
            rerun.mark(self.wt, key, self.tree(), self.tip, BRANCH,
                       [{'name': 'unit', 'status': 'FAIL'}], self.logs)
        with mock.patch.object(gatecap, 'wait_turn', wait_turn):
            out = self.gate()
        self.assertEqual(out.code, G.REFUSED, out.lines)
        self.assertIn('was dropped at its turn; nothing ran', out.lines[0])
        hows = [d.get('how') for d in self.store.history_records()]
        self.assertEqual(hows, [budget.NOT_STARTED])

    def test_the_mark_is_written_before_the_slot_is_released(self):
        """Nothing planted: a gate queued behind a red one of the same tree takes its turn once
        that one's slot is released, so the mark must be there by then."""
        real = lockrun.Slot.release
        at_release = []

        def release(slot, code=None, how=None):
            if how == 'done':
                at_release.append(self.mark_of('select'))
            return real(slot, code, how)
        with mock.patch.object(lockrun.Slot, 'release', release):
            out = self.gate()
        self.assertEqual(out.code, G.FAILED, out.lines)
        self.assertEqual(len(at_release), 1, at_release)
        self.assertIsNotNone(at_release[0], 'no mark when the slot was released')
        self.assertEqual(at_release[0]['verdict'], 'fail')

    def unclean_reads(self):
        """Run a gate; for each runner._unclean read, whether the slot was released by then."""
        real_release, real_unclean = lockrun.Slot.release, runner._unclean
        released, reads = [], []

        def release(slot, code=None, how=None):
            released.append(how)
            return real_release(slot, code, how)

        def unclean(*args):
            reads.append(bool(released))
            return real_unclean(*args)
        with mock.patch.object(lockrun.Slot, 'release', release), \
                mock.patch.object(runner, '_unclean', unclean):
            out = self.gate()
        return out, reads

    def test_a_pass_reads_the_tree_after_the_release_and_a_red_run_before(self):
        """A pass's gate-OK record and mark read _unclean after the release, so a child the
        release ends that writes to the tree still counts; a red run (no gate-OK record) reads
        once, before it, for its fail mark. One read per gate either way."""
        out, reads = self.unclean_reads()
        self.assertEqual(out.code, G.FAILED, out.lines)
        self.assertEqual(reads, [False])
        self.red(False)
        self.repo.write('a.py', 'x = 2\n', root=self.wt)    # a new tree: no mark, no reuse
        self.repo.commit('green', 'a.py', cwd=self.wt)
        out, reads = self.unclean_reads()
        self.assertEqual(out.code, G.OK, out.lines)
        self.assertEqual(reads, [True])
        self.assertEqual(self.mark_of('select')['verdict'], 'pass')


# A kit_tests report (kitlib.pytests.runner) whose units are as UNCHANGED_KT says, exit 1.
KT = ['{python}', '-c', 'import os, sys; units = os.environ["UNCHANGED_KT"].split(","); '
      '[print("==== tests.u.test_%d (3 tests, 600s, %s) ====" % (i, s)) '
      ' for i, s in enumerate(units)]; '
      'bad = ["tests.u.test_%d (%s)" % (i, s) for i, s in enumerate(units) if s != "OK"]; '
      'print("kit_tests: FAILED %d of 5 units; failed: %s" % (len(bad), ", ".join(bad))); '
      'sys.exit(1)']


class MachineFailures(Case):
    """A FAIL the machine caused says nothing about the tree: no fail mark, and the next gate of
    the same tree runs (rerun.machine_failure)."""
    GATE = {'steps': [      # the machine's steps `continue`: `unit` runs after a FAIL of one
        {'name': 'gone', 'always': True, 'cmd': ['kit-no-such-program-0f3c'], 'pass': '^ok$',
         'continue': True},
        {'name': 'tool', 'always': True, 'cmd': OK, 'pass': '^ok$', 'continue': True,
         'requires': ['kit-no-such-tool-0f3c'], 'requires_strict': True},
        {'name': 'kt', 'always': True, 'cmd': KT, 'continue': True},
        {'name': 'unit', 'always': True, 'cmd': STATE, 'pass': '^ok$'}]}

    def setUp(self):
        Case.setUp(self)
        self.red(False)
        self.env['UNCHANGED_KT'] = 'TIMEOUT'

    def assert_unmarked_and_runs_again(self, only):
        out = self.gate(only=only)
        self.assertEqual(out.code, G.FAILED, out.lines)
        failed = [r for r in out.facts['results'] if r['status'] == 'FAIL']
        self.assertEqual([r['name'] for r in failed], only, failed)
        self.assertIsNone(self.mark_of('select'))
        self.assertTrue(all(rerun.machine_failure(r) for r in failed), failed)
        self.assertTrue(any(s.startswith("no red-tree mark: every FAIL is the machine's")
                            for s in out.said), out.said)
        self.assertEqual(self.gate(only=only).code, G.FAILED)       # not refused: it ran
        self.assertEqual(len(self.book_calls), 2)

    def test_a_step_that_did_not_start(self):
        self.assert_unmarked_and_runs_again(['gone'])

    def test_a_required_tool_missing(self):
        self.assert_unmarked_and_runs_again(['tool'])

    def test_kit_tests_units_that_all_timed_out(self):
        self.env['UNCHANGED_KT'] = 'OK,TIMEOUT,TIMEOUT'
        self.assert_unmarked_and_runs_again(['kt'])

    def test_a_unit_that_failed_beside_one_that_timed_out_marks_the_tree(self):
        self.env['UNCHANGED_KT'] = 'TIMEOUT,FAILED'
        self.gate(only=['kt'])
        self.assertEqual(self.mark_of('select')['failed'], ['kt'])
        self.assert_refused(self.gate(only=['kt']), 1)

    def test_a_real_fail_beside_a_machine_one_marks_only_the_real_one(self):
        self.red(True)
        out = self.gate(only=['unit', 'gone'])
        self.assertEqual([r['name'] for r in out.facts['results'] if r['status'] == 'FAIL'],
                         ['gone', 'unit'], out.facts['results'])
        rec = self.mark_of('select')
        self.assertEqual((rec['verdict'], rec['failed']), ('fail', ['unit']))

    def test_a_machine_fail_leaves_an_earlier_mark_as_it_was(self):
        self.red(True)
        self.gate(only=['unit'])
        before = self.mark_of('select')
        self.red(False)
        with mock.patch.object(runner, '_run_cmd', return_value=None):  # nothing starts
            self.gate(only=['unit'], again=True)
        self.assertEqual(self.mark_of('select'), before)

    def test_mixed_reasons_are_the_tree_s(self):
        self.assertIsNone(rerun.machine_failure(
            {'name': 'x', 'status': 'FAIL', 'log': None,
             'reasons': ['did not start (see its log)', 'exit 1']}))
        self.assertTrue(rerun.machine_failure(
            {'name': 'x', 'status': 'FAIL', 'log': None,
             'reasons': ['requires a, b, which are missing']}))


class TargetedRerun(test_targeted_rerun.RerunCase):
    def test_a_red_full_then_its_targeted_rerun_runs_and_the_full_again_is_refused(self):
        wt, _ = self.work('once')
        self.red(wt)
        out = self.rerun(wt)
        self.assertEqual(out.code, G.OK, out.lines + out.said)      # FLAKE: not refused
        out = runner.branch_gate(wt, full=True, book_kw=self.book_kw,
                                 log_dir=os.path.join(self.tmp, 'again'))
        self.assertEqual(out.code, G.REFUSED, out.lines)
        self.assertEqual(out.facts['no_record'], runner.UNCHANGED_NO_RECORD)
