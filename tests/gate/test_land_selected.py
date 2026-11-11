"""What a landing runs (ARCHITECTURE.md "What a landing runs"; docs/gate-landing.md "Landing"):
the merged gate selects its steps by the merge's diff, as a default-mode gate does, so a step
globbed on paths the merge does not touch does not run, nor its full_cmd. A strict branch's merged
gate runs that selection fresh (nothing reused), never in full. Only a merge that changes a gate
config file (here the hook module) runs every step that is not manual, with its full_cmd, once,
on the merged tree (Lander.config_escalate). No tip needs a --full gate-OK record: the default-mode
record of its default-mode gate lands it. The retired "landing" keys gate_once and shared_paths
still load, are ignored, and get one WARNING line on stderr per land.py or gate.py run.

Each tip here is gated by a real default-mode branch gate (GATE_TIPS off), never given the --full
record LandCase.lander would add."""
import os
import subprocess
import sys
import unittest

from kitlib import config as kconfig
from kitlib import gate as G
from kitlib.gate import result, runner
from kitlib.records import gate_ok
from tests.gate import fixtures, test_land, test_land_strict

BRANCH = test_land.BRANCH
FAILS = ['{python}', '-c', 'import sys; print("ok"); sys.exit(1)']
GATE = {'steps': [
    {'name': 'unit', 'always': True, 'cmd': fixtures.RED_AWARE, 'pass': '^ok$'},
    # globbed on paths no branch here changes; its full_cmd fails, so a run of it would be seen
    {'name': 'asset-import', 'globs': ['assets/*'], 'cmd': fixtures.RED_AWARE,
     'full_cmd': FAILS, 'pass': '^ok$'},
    {'name': 'engine', 'manual': True, 'cmd': FAILS},
]}
# the gate config landing's: every step passes in full, so the escalated run can go green
FULL_GATE = {'steps': [
    {'name': 'unit', 'always': True, 'cmd': fixtures.RED_AWARE, 'pass': '^ok$'},
    {'name': 'asset-import', 'globs': ['assets/*'], 'cmd': fixtures.RED_AWARE, 'pass': '^ok$'},
]}
HOOKS = 'GATE_CHECKS = {}\n'
FRESH = 'the merged gate runs fresh (nothing reused)'
RETIRED = ('landing.shared_paths', 'landing.gate_once')     # schema order
WARNING = 'WARNING: %s still writes "%s", a retired key: it is ignored; remove it'


def default_gate(case, wt, tip):
    """A default-mode branch gate of `wt`, green, whose record of `tip` is "select"."""
    out = runner.branch_gate(wt, book_kw=case.book_kw, log_dir=os.path.join(case.tmp, 'own'))
    case.assertEqual(out.code, G.OK, out.lines)
    rec, why = gate_ok.verify(case.repo.root, tip)
    case.assertIsNone(why)
    case.assertEqual(gate_ok.mode_of(rec), 'select')
    case.assertIsNone(gate_ok.verify_full(case.repo.root, tip)[0])     # no --full record
    return rec


def merged(case, out):
    """The merged gate's record: (its mode, its step names)."""
    rec, why = gate_ok.verify(case.repo.root, out.merge)
    case.assertIsNone(why)
    return gate_ok.mode_of(rec), [s['name'] for s in rec['steps']]


class PlainSelected(test_land.LandCase):
    GATE = GATE
    GATE_TIPS = False
    KIT = {}

    def test_plain_landing_runs_selected(self):
        wt, tip = self.branch('feature', {'scripts/a.py': 'x = 1\n'})
        default_gate(self, wt, tip)
        said = []
        lander = self.lander(BRANCH, self.main, tip, say=said.append)
        out = lander.run()
        self.assertEqual(out.code, G.OK, out.lines)
        self.assertFalse(lander.full or lander.escalated)
        self.assertEqual(merged(self, out), ('select', ['unit']))     # not asset-import
        line = [l for l in said if l.startswith('merged-tree gate at ')]
        self.assertTrue(line and line[0].endswith(': unit'), said)


class StrictSelected(test_land_strict.StrictCase):
    GATE = GATE
    GATE_TIPS = False
    KIT = {}

    def test_strict_landing_runs_selected_fresh(self):
        wt, tip = self.branch('feature', {test_land_strict.STRICT: 'x = 1\n'})
        default_gate(self, wt, tip)
        self.queue(BRANCH, tip)
        lander, out, said = self.land_it(BRANCH, tip)
        self.assertEqual(out.code, G.OK, out.lines)
        self.assertEqual(self.remote_main(), out.merge)
        self.assertTrue(lander.fresh)
        self.assertFalse(lander.full or lander.escalated)
        self.assertTrue(any(l.startswith('STRICT: %s changes strict path %s'
                                         % (BRANCH, test_land_strict.STRICT))
                            and l.endswith(FRESH) for l in said), said)
        self.assertEqual(merged(self, out), ('select', ['unit']))


class GateConfigFull(test_land.LandCase):
    """The hook module is a gate config file a branch may change without the owner."""
    GATE = FULL_GATE
    GATE_TIPS = False
    KIT = {'hook': {'module': 'kit_hooks'}}

    def setUp(self):
        test_land.LandCase.setUp(self)
        self.repo.write('tools/kit_hooks.py', HOOKS)
        self.main = self.repo.commit('hook module', 'tools/kit_hooks.py')
        self.repo.git('push', '-q', 'origin', 'main')

    def config_tip(self):
        wt, tip = self.branch('feature', {'tools/kit_hooks.py': '# changed\n' + HOOKS})
        default_gate(self, wt, tip)
        return tip

    def test_gate_config_landing_runs_full_once(self):
        tip = self.config_tip()
        said = []
        lander = self.lander(BRANCH, self.main, tip, say=said.append)
        out = lander.run()
        self.assertEqual(out.code, G.OK, out.lines)
        self.assertTrue(lander.full and lander.escalated)
        self.assertEqual(len([l for l in said if l.startswith('GATE CONFIG: ')]), 1, said)
        self.assertEqual(merged(self, out), ('full', ['unit', 'asset-import']))
        rec = result.read(self.repo.root, BRANCH, 'land')[0]
        self.assertEqual((rec['gate_once'], rec['escalated']), (None, True))

    def test_no_full_record_needed(self):
        # a gate config change was the one tip a default-mode record could not land
        tip = self.config_tip()
        out = self.lander(BRANCH, self.main, tip).run()
        self.assertEqual(out.code, G.OK, out.lines)
        self.assertEqual(self.remote_main(), out.merge)
        self.assertIsNone(gate_ok.verify_full(self.repo.root, tip)[0])


class RetiredKeys(test_land.LandCase):
    GATE = GATE
    GATE_TIPS = False
    KIT = {'landing': {'gate_once': True, 'shared_paths': ['scripts/*']}}

    def stderr_of(self, tool, *args):
        r = subprocess.run([sys.executable, os.path.join(test_land.KIT_ROOT, 'tools', tool)]
                           + list(args), capture_output=True, text=True, timeout=120,
                           stdin=subprocess.DEVNULL, env=self.env)
        return r.returncode, r.stdout, r.stderr.splitlines()

    def test_retired_keys_load_and_warn(self):
        cfg = kconfig.load(self.repo.root)
        self.assertEqual(getattr(cfg, 'retired', None), RETIRED)
        self.assertNotIn('gate_once', cfg.section('landing'))
        self.assertNotIn('shared_paths', cfg.section('landing'))
        want = [WARNING % (cfg.where, k) for k in RETIRED]
        wt, _tip = self.branch('feature', {'scripts/a.py': 'x = 1\n'})
        code, out, err = self.stderr_of('land.py', '-C', self.repo.root, BRANCH, '--check-strict')
        self.assertEqual(code, G.OK, (out, err))
        self.assertEqual([l for l in err if l.startswith('WARNING: ')], want, err)
        code, out, err = self.stderr_of('gate.py', '-C', wt, '--plan')
        self.assertEqual(code, G.OK, (out, err))
        wt_where = kconfig.load(wt).where
        self.assertEqual([l for l in err if l.startswith('WARNING: ')],
                         [WARNING % (wt_where, k) for k in RETIRED], err)
        self.assertEqual(kconfig.retired_warnings(self.repo.root), want)


if __name__ == '__main__':
    unittest.main()
