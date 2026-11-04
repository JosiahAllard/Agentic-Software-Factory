"""kitlib.gate.reuse: a step's toolchain (docs/gate.md "Gate reuse", "The toolchain"). A step that
declares `toolchain` probe argvs has their output in its key on every path (the cache, the same
tree, the input tree): an unchanged toolchain is reused as before, a changed one runs the step, and
a probe that fails gives the step no key. A step that declares none is keyed exactly as before and
starts no probe. The probe reads an "SDK" file in a temp folder outside the repository, named by
SDK_ENV. Fixtures: test_reuse.ReuseCase."""
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

from kitlib import gate as G
from kitlib.gate import reuse, runner
from kitlib.gate import steps as S
from kitlib.records import gate_ok
from tests.gate import test_reuse

ReuseCase = test_reuse.ReuseCase
BRANCH = test_reuse.BRANCH
OK_CMD = test_reuse.OK_CMD
SRC = test_reuse.SRC
UNIT = test_reuse.UNIT

SDK_ENV = 'KIT_TEST_TOOLCHAIN_SDK'
# prints the SDK file; "fail" exits 1, "hang" sleeps past any patched PROBE_TIMEOUT
PROBE = ['{python}', '-c',
         'import os, sys, time\n'
         't = open(os.environ["%s"], encoding="utf-8").read()\n'
         'if t == "fail": sys.exit(1)\n'
         'if t == "hang": time.sleep(60)\n'
         'sys.stdout.write(t)\n' % SDK_ENV]
# names {base}: only the same tree and the input tree can serve it (as a repository's test tiers)
TOOLY = {'name': 'tooly', 'always': True, 'globs': ['src/*'], 'pass': '^ok$', 'reuse': True,
         'cmd': ['{python}', '-c', 'print("ok")', '{base}'], 'toolchain': [PROBE]}
# the cache serves it too
CACHY = {'name': 'cachy', 'always': True, 'globs': ['src/*'], 'cmd': OK_CMD, 'pass': '^ok$',
         'reuse': True, 'toolchain': [PROBE]}
# changes its own toolchain while it runs
BUMPER = {'name': 'bumper', 'always': True, 'globs': ['src/*'], 'pass': '^ok$', 'reuse': True,
          'cmd': ['{python}', '-c', 'import os; open(os.environ["%s"], "a", encoding="utf-8")'
                  '.write("+"); print("ok")' % SDK_ENV],
          'toolchain': [PROBE]}
TOOLED = sorted(reuse.INPUT_PARTS + ('toolchain',))


class ToolchainCase(ReuseCase):
    GATE = {'steps': [UNIT, TOOLY, CACHY]}
    KIT = {'landing': {'shared_paths': ['shared/*']}}
    ASKING = ('unit', 'tooly', 'cachy')

    def setUp(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, True)
        self.sdk = os.path.join(folder, 'sdk.txt')
        self.set_sdk('8.0.100\n')
        # before the fixture's setUp: the steps' environment is copied from this one
        p = mock.patch.dict(os.environ, {SDK_ENV: self.sdk})
        p.start()
        self.addCleanup(p.stop)
        ReuseCase.setUp(self)

    def set_sdk(self, text):
        with open(self.sdk, 'w', encoding='utf-8', newline='') as f:
            f.write(text)

    def full_tip(self, name='feature'):
        wt, tip = self.branch(name, dict(SRC))
        out = self.gate(wt, full=True)
        self.assertEqual(out.code, G.OK, out.lines)
        return wt, tip

    def keys(self, tip):
        return (gate_ok.read(self.repo.root, tip)[0].get('input_keys') or {}).get('steps') or {}

    def move_main(self, files):
        other = self.repo.worktree('other')
        for rel, text in files.items():
            self.repo.write(rel, text, root=other)
        moved = self.repo.commit('main moves', *files, cwd=other)
        self.repo.git('push', '-q', 'origin', '%s:refs/heads/main' % moved)
        return moved

    def line_of(self, word, name):
        found = [l for l in self.lines(word) if l.startswith(reuse.PREFIX + '%s %s: ' % (word, name))]
        self.assertEqual(len(found), 1, self.said)
        return found[0]


class InputTree(ToolchainCase):
    def test_the_record_key_of_a_toolchain_step_holds_its_toolchain(self):
        _, tip = self.full_tip()
        keys = self.keys(tip)
        self.assertEqual(sorted(keys), ['cachy', 'tooly', 'unit'])
        self.assertEqual(sorted(keys['unit']), sorted(reuse.INPUT_PARTS))
        self.assertEqual(sorted(keys['tooly']), TOOLED)
        self.assertEqual(sorted(keys['cachy']), TOOLED)
        self.assertEqual(keys['tooly']['toolchain'], keys['cachy']['toolchain'])
        self.assertRegex(keys['tooly']['toolchain'], '^[0-9a-f]{64}$')

    def test_main_moved_and_the_same_toolchain_is_served(self):
        _, tip = self.full_tip()
        self.move_main({'o.py': 'o\n'})
        lander, out = self.land(BRANCH, tip)
        self.assertEqual(out.code, G.OK, out.lines)
        self.assertEqual(out.verdict, 'GATE OK (reused: unit, tooly, cachy)')
        self.assert_not_ran(lander.gate_logs, 'unit', 'tooly', 'cachy')

    def test_main_moved_and_a_changed_toolchain_runs_the_steps_that_declare_it(self):
        _, tip = self.full_tip()
        self.move_main({'o.py': 'o\n'})
        self.set_sdk('8.0.200\n')
        lander, out = self.land(BRANCH, tip)
        self.assertEqual(out.code, G.OK, out.lines)
        self.assertEqual(out.verdict, 'GATE OK (reused: unit)')
        self.assertEqual(self.line_of('NONE', 'tooly'), reuse.PREFIX + 'NONE tooly: its command '
                         'names {base}: it reads git history; input tree: toolchain differs')
        self.assertTrue(self.line_of('MISS', 'cachy').endswith('; input tree: toolchain differs'),
                        self.said)
        self.assert_ran(lander.gate_logs, 'tooly', 'cachy')
        self.assert_not_ran(lander.gate_logs, 'unit')


class SameTree(ToolchainCase):
    def test_the_same_tree_and_the_same_toolchain_is_served(self):
        _, tip = self.full_tip()
        lander, out = self.land(BRANCH, tip)
        self.assertEqual(out.code, G.OK, out.lines)
        self.assertEqual(out.verdict, 'GATE OK (reused: unit, tooly, cachy)')
        self.assert_not_ran(lander.gate_logs, 'unit', 'tooly', 'cachy')

    def test_the_same_tree_and_a_changed_toolchain_runs_the_steps_that_declare_it(self):
        _, tip = self.full_tip()
        self.set_sdk('9.0.100\n')
        lander, out = self.land(BRANCH, tip)
        self.assertEqual(out.code, G.OK, out.lines)
        self.assertEqual(out.verdict, 'GATE OK (reused: unit)')
        self.assertEqual(self.line_of('NONE', 'tooly'), reuse.PREFIX + 'NONE tooly: its command '
                         'names {base}: it reads git history; same tree: toolchain differs')
        self.assertTrue(self.line_of('MISS', 'cachy').endswith('; same tree: toolchain differs'),
                        self.said)
        self.assert_ran(lander.gate_logs, 'tooly', 'cachy')

    def test_a_record_without_the_toolchain_digest_serves_nothing_that_declares_one(self):
        _, tip = self.full_tip()
        rec = gate_ok.read(self.repo.root, tip)[0]
        del rec['input_keys']['steps']['tooly']['toolchain']
        gate_ok.write(self.repo.root, rec)
        lander, out = self.land(BRANCH, tip)
        self.assertEqual(out.code, G.OK, out.lines)
        self.assertTrue(self.line_of('NONE', 'tooly').endswith(
            '; same tree: the record holds no toolchain digest for it'), self.said)
        self.assert_ran(lander.gate_logs, 'tooly')


class Cache(ToolchainCase):
    def test_a_branch_rerun_hits_on_the_same_toolchain_and_misses_on_another(self):
        wt, _ = self.gated_branch()
        self.assertTrue(self.line_of('STORED', 'cachy'), self.said)
        self.gate(wt)
        self.assertTrue(self.line_of('HIT', 'cachy'), self.said)
        self.assertTrue(self.line_of('HIT', 'unit'), self.said)
        self.set_sdk('8.0.101\n')
        out = self.gate(wt)
        self.assertEqual(out.code, G.OK, out.lines)
        self.assertTrue(self.line_of('MISS', 'cachy'), self.said)
        self.assertTrue(self.line_of('HIT', 'unit'), self.said)
        self.assert_ran(out.log_dir, 'cachy')
        self.assert_not_ran(out.log_dir, 'unit')


class ProbeFails(ToolchainCase):
    def test_a_probe_that_fails_gives_the_step_no_key_and_stores_nothing(self):
        cases = (('fail', 'it exited 1'), ('', 'it printed nothing'),
                 ('hang', 'it did not finish within 1 s'))
        for i, (text, why) in enumerate(cases):
            with self.subTest(text=text), mock.patch.object(runner, 'PROBE_TIMEOUT', 1):
                # a file of its own: unit's key is new each time, so unit is stored each time
                wt, tip = self.branch('f%d' % i, dict(SRC, **{'src/f%d.py' % i: 'f\n'}))
                self.set_sdk(text)
                out = self.gate(wt)
                self.assertEqual(out.code, G.OK, out.lines)
                for name in ('tooly', 'cachy'):
                    self.assertEqual(self.line_of('NONE', name)[-len(why):], why, self.said)
                    self.assertIn(': toolchain probe 1 (%s): ' % os.path.basename(sys.executable),
                                  self.line_of('NONE', name))
                self.assertFalse([l for l in self.lines('STORED') if ' cachy:' in l], self.said)
                self.assertTrue(self.line_of('STORED', 'unit'), self.said)
                self.assert_ran(out.log_dir, 'tooly', 'cachy')
                self.assertEqual(sorted(self.keys(tip)), ['unit'])
                out = self.gate(wt, full=True)
                self.assertEqual(out.code, G.OK, out.lines)
                self.assertEqual(sorted(self.keys(tip)), ['unit'])
                self.set_sdk('8.0.100\n')


class Bumper(ToolchainCase):
    GATE = {'steps': [BUMPER]}
    ASKING = ('bumper',)

    def test_a_toolchain_changed_while_the_gate_ran_stores_nothing_and_keys_nothing(self):
        wt, tip = self.branch('feature', dict(SRC))
        out = self.gate(wt)
        self.assertEqual(out.code, G.OK, out.lines)
        self.assertEqual(self.lines('NOT'), [
            reuse.PREFIX + 'NOT STORED bumper: its key changed while the gate ran (the kit, a '
            'program, a toolchain or the config)'], self.said)
        self.assertEqual(self.entries(), [])
        self.assertEqual(self.keys(tip), {})
        out = self.gate(wt, full=True)
        self.assertEqual(out.code, G.OK, out.lines)
        self.assertEqual(self.keys(tip), {})


class NoOptIn(ReuseCase):
    """A gate whose steps declare no toolchain starts no probe and keys as it did."""
    KIT = {'landing': {'shared_paths': ['shared/*']}}

    def test_no_toolchain_starts_no_probe_and_keys_the_input_parts_alone(self):
        p = mock.patch.object(reuse, '_probe', side_effect=AssertionError('a probe ran'))
        probe = p.start()
        self.addCleanup(p.stop)
        wt, tip = self.gated_branch()
        self.gate(wt)
        self.assertTrue([l for l in self.lines('HIT') if ' unit:' in l], self.said)
        out = self.gate(wt, full=True)
        self.assertEqual(out.code, G.OK, out.lines)
        steps = gate_ok.read(self.repo.root, tip)[0]['input_keys']['steps']
        self.assertEqual(sorted(steps['unit']), sorted(reuse.INPUT_PARTS))
        _, out = self.land(BRANCH, tip)
        self.assertEqual(out.code, G.OK, out.lines)
        self.assertFalse(probe.called)

    def test_a_step_built_without_a_toolchain_has_none(self):
        step = S.Step('x', ['x'], None, '.', [], [], (0,), False, False, [], False)
        self.assertEqual(step.toolchain, ())
        self.assertIsNone(reuse.toolchain_digest('.', step, {}))


if __name__ == '__main__':
    unittest.main()
