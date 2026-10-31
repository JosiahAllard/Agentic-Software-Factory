"""tools/review_pack.py's `gate` line (docs/review-pack.md): whether the branch tip has a full-gate
OK record for exactly that commit, the record a landing requires (docs/gate.md "Landing"). It is
printed only: the exit status is what the rest of the pack makes it."""
from kitlib import reviewpack
from kitlib.records import gate_ok
from tests.reviewpack.repo import AGENT, EMAIL, PackCase


class GateLineCase(PackCase):
    def feature(self):
        self.git('checkout', '-q', '-b', 'feature')
        self.write('f.txt', '1')
        self.commit_all('change f')
        return self.git('rev-parse', 'feature')

    def record(self, tip, **keys):
        rec = gate_ok.build(self.root, tip, {}, {'version': 'test'}, 'test', gate_ok.utc(),
                            [{'name': 'unit'}])
        rec.update(keys)
        gate_ok.write(self.root, rec)
        return rec

    def gate_line(self, out):
        got = [l for l in out.splitlines() if l.startswith('gate     ')]
        self.assertEqual(len(got), 1, out)
        return got[0]


class GateLine(GateLineCase):
    def test_no_record_is_said_and_the_exit_status_is_unchanged(self):
        tip = self.feature()
        code, out = self.run_pack('feature', '--base', 'main')
        self.assertEqual(code, 0, out)
        line = self.gate_line(out)
        self.assertIn('tip %s: NO full-gate OK record' % tip[:10], line)
        self.assertIn('no gate-OK record for %s' % tip, line)
        self.assertIn('landing will refuse', line)

    def test_a_default_mode_record_is_not_enough(self):
        tip = self.feature()
        self.record(tip, full=False)
        code, out = self.run_pack('feature', '--base', 'main')
        self.assertEqual(code, 0, out)
        self.assertIn('without --full', self.gate_line(out))

    def test_a_full_record_of_the_tip_is_said(self):
        tip = self.feature()
        rec = self.record(tip, full=True)
        code, out = self.run_pack('feature', '--base', 'main')
        self.assertEqual(code, 0, out)
        self.assertEqual(self.gate_line(out), 'gate     tip %s: full gate OK, ended %s'
                         % (tip[:10], rec['ended_utc']))

    def test_a_pack_that_fails_still_fails_with_a_full_record(self):
        self.git('checkout', '-q', '-b', 'feature')
        self.write('CLAUDE.md', 'v1')
        self.commit_all('agent edits claude, no trailer', author=AGENT)
        tip = self.git('rev-parse', 'feature')
        self.record(tip, full=True)
        code, out = self.run_pack('feature', '--base', 'main')
        self.assertEqual(code, 1, out)
        self.assertIn('full gate OK', self.gate_line(out))


class GateOnceLine(GateLineCase):
    """A base that sets "landing".gate_once takes a default-mode record (docs/gate.md "Gate once
    at landing"): the line says so instead of "landing will refuse", unless the branch changes a
    strict path, which always ends on --full (docs/gate.md "The final gate")."""
    CONFIG = {'owner': {'email': EMAIL}, 'landing': {'gate_once': True}}

    def strict_feature(self):
        self.git('checkout', '-q', '-b', 'feature')
        self.write('kitlib/gate/x.txt', '1')
        self.commit_all('change a strict path')
        return self.git('rev-parse', 'feature')

    def test_a_default_mode_record_is_said_as_enough(self):
        tip = self.feature()
        rec = self.record(tip, full=False, mode='select')
        code, out = self.run_pack('feature', '--base', 'main')
        self.assertEqual(code, 0, out)
        line = self.gate_line(out)
        self.assertTrue(line.startswith('gate     tip %s: scoped gate OK (the base sets '
                                        '"landing".gate_once), ended %s'
                                        % (tip[:10], rec['ended_utc'])), line)
        self.assertNotIn('landing will refuse', line)

    def test_a_strict_branch_with_no_full_record_still_refuses(self):
        tip = self.strict_feature()
        self.record(tip, full=False, mode='select')
        code, out = self.run_pack('feature', '--base', 'main')
        self.assertEqual(code, 0, out)
        line = self.gate_line(out)
        self.assertIn('landing will refuse', line)
        self.assertIn('kitlib/gate/x.txt', line)

    def test_a_docs_record_is_still_not_enough(self):
        tip = self.feature()
        self.record(tip, full=False, mode='docs')
        code, out = self.run_pack('feature', '--base', 'main')
        self.assertEqual(code, 0, out)
        line = self.gate_line(out)
        self.assertIn('landing will refuse', line)
        self.assertIn('gate.py without --full', line)
        self.assertNotIn('needs --full', line)

    def test_no_record_names_the_scoped_final_gate(self):
        self.feature()
        code, out = self.run_pack('feature', '--base', 'main')
        self.assertEqual(code, 0, out)
        self.assertIn('scoped final gate', self.gate_line(out))

    def test_a_full_record_is_said_as_before(self):
        tip = self.feature()
        rec = self.record(tip, full=True)
        code, out = self.run_pack('feature', '--base', 'main')
        self.assertEqual(code, 0, out)
        self.assertEqual(self.gate_line(out), 'gate     tip %s: full gate OK, ended %s'
                         % (tip[:10], rec['ended_utc']))


class SmokeStepsLine(GateLineCase):
    """A base with "gate".smoke_steps and neither gate_once nor shared_paths: the landing's record
    check takes a select record even of a strict path (the merged gate escalates), so the line must
    not say it refuses; the procedure's --full is named instead."""
    CONFIG = {'owner': {'email': EMAIL},
              'gate': {'smoke_steps': ['boot'],
                       'steps': [{'name': 'suite', 'always': True, 'cmd': ['x'], 'pass': 'x'},
                                 {'name': 'boot', 'manual': True, 'cmd': ['x'], 'pass': 'x'}]}}

    def test_a_scoped_non_strict_branch_says_the_new_wording(self):
        tip = self.feature()
        rec = self.record(tip, full=False, mode='select')
        line = self.gate_line(self.run_pack('feature', '--base', 'main')[1])
        self.assertEqual(line, 'gate     tip %s: scoped gate OK (this branch needs no --full), '
                         'ended %s, steps not re-checked' % (tip[:10], rec['ended_utc']))

    def test_a_strict_branch_with_a_select_record_is_not_said_to_be_refused(self):
        self.git('checkout', '-q', '-b', 'feature')
        self.write('kitlib/gate/x.txt', '1')
        self.commit_all('change a strict path')
        tip = self.git('rev-parse', 'feature')
        self.record(tip, full=False, mode='select')
        code, out = self.run_pack('feature', '--base', 'main')
        self.assertEqual(code, 0, out)
        line = self.gate_line(out)
        self.assertNotIn('landing will refuse', line)
        self.assertIn("final gate must be --full", line)
        self.assertIn('kitlib/gate/x.txt', line)


class UnknownBaseLine(GateLineCase):
    def test_no_base_config_and_no_record_still_refuses(self):
        tip = self.feature()
        line = reviewpack.gate_line(self.root, 'feature', None, None)
        self.assertIn('tip %s: NO full-gate OK record' % tip[:10], line)
        self.assertTrue(line.endswith('landing will refuse'), line)

    def test_no_base_config_with_a_scoped_record_cannot_tell(self):
        tip = self.feature()
        self.record(tip, full=False, mode='select')
        line = reviewpack.gate_line(self.root, 'feature', None, None)
        self.assertIn('cannot tell', line)
        self.assertNotIn('landing will refuse', line)
