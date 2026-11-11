"""tools/review_pack.py's `gate` line (docs/review-pack.md): whether the branch tip has a gate-OK
record for exactly that commit that its landing takes (docs/gate-landing.md "Landing"): a --full or
default-mode pass always, a docs-only pass only for a docs-only change. No branch needs a --full
run. It is printed only: the exit status is what the rest of the pack makes it."""
from kitlib import reviewpack
from kitlib.records import gate_ok
from tests.reviewpack.repo import AGENT, EMAIL, PackCase


class GateLineCase(PackCase):
    CONFIG = {'owner': {'email': EMAIL}}

    def feature(self, path='f.txt'):
        self.git('checkout', '-q', '-b', 'feature')
        self.write(path, '1')
        self.commit_all('change %s' % path)
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
        self.assertIn('tip %s: NO gate-OK record that lands it' % tip[:10], line)
        self.assertIn('no gate-OK record for %s' % tip, line)
        self.assertTrue(line.endswith('landing will refuse'), line)
        self.assertIn('the final gate runs after review', line)

    def test_a_default_mode_record_is_said_to_land(self):
        tip = self.feature()
        rec = self.record(tip, full=False, mode='select')
        code, out = self.run_pack('feature', '--base', 'main')
        self.assertEqual(code, 0, out)
        self.assertEqual(self.gate_line(out), 'gate     tip %s: default-mode gate OK, ended %s, '
                         'steps not re-checked' % (tip[:10], rec['ended_utc']))

    def test_a_strict_branch_with_a_default_mode_record_is_said_to_land(self):
        tip = self.feature('kitlib/gate/x.txt')
        self.record(tip, full=False, mode='select')
        line = self.gate_line(self.run_pack('feature', '--base', 'main')[1])
        self.assertIn('default-mode gate OK', line)
        self.assertNotIn('--full', line)

    def test_a_record_of_unknown_mode_is_not_enough(self):
        tip = self.feature()
        self.record(tip, full=False, mode=None)
        line = self.gate_line(self.run_pack('feature', '--base', 'main')[1])
        self.assertTrue(line.endswith('landing will refuse'), line)

    def test_a_docs_record_of_a_change_that_is_not_docs_only_is_refused(self):
        tip = self.feature()
        self.record(tip, full=False, mode='docs')
        line = self.gate_line(self.run_pack('feature', '--base', 'main')[1])
        self.assertIn('landing will refuse the docs-only record', line)
        self.assertIn('the final gate is the default gate (gate.py)', line)
        self.assertNotIn('--full', line)

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


class DocsLine(GateLineCase):
    """A base whose "gate".docs_only names the change: a docs-only record lands it."""
    CONFIG = {'owner': {'email': EMAIL},
              'gate': {'docs_only': {'globs': ['docs/*'], 'steps': ['links']},
                       'steps': [{'name': 'suite', 'always': True, 'cmd': ['x'], 'pass': 'x'},
                                 {'name': 'links', 'globs': ['docs/*'], 'cmd': ['x'],
                                  'pass': 'x'}]}}

    def test_a_docs_record_of_a_docs_only_change_is_said_to_land(self):
        tip = self.feature('docs/a.md')
        rec = self.record(tip, full=False, mode='docs')
        line = self.gate_line(self.run_pack('feature', '--base', 'main')[1])
        self.assertEqual(line, 'gate     tip %s: docs-only gate OK, ended %s, steps not '
                         're-checked' % (tip[:10], rec['ended_utc']))


class UnknownBaseLine(GateLineCase):
    def test_no_base_config_and_no_record_still_refuses(self):
        tip = self.feature()
        line = reviewpack.gate_line(self.root, 'feature', None, None)
        self.assertIn('tip %s: NO gate-OK record that lands it' % tip[:10], line)
        self.assertTrue(line.endswith('landing will refuse'), line)

    def test_no_base_config_with_a_default_mode_record_lands(self):
        tip = self.feature()
        self.record(tip, full=False, mode='select')
        line = reviewpack.gate_line(self.root, 'feature', None, None)
        self.assertIn('default-mode gate OK', line)
        self.assertNotIn('landing will refuse', line)

    def test_no_base_config_with_a_docs_record_cannot_tell(self):
        tip = self.feature()
        self.record(tip, full=False, mode='docs')
        line = reviewpack.gate_line(self.root, 'feature', None, None)
        self.assertIn('cannot tell', line)
        self.assertNotIn('landing will refuse', line)
