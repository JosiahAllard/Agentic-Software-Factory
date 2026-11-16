"""What a landing onto a staging branch runs (docs/gate-landing.md "What a landing re-runs and what
it reuses"): a step with "staging_landing": false is left out and named in the merge's gate-OK
record (staging_skipped), a step with staging_args runs them and is never REUSED, and every other
step reuses as at any landing; a strict branch's landing (fresh) and a --full one (a gate config
change) run every selected step. What each books (docs/gate-landing.md "The landing queue"): the
compile-only one land-test-staging (2 cores), a fresh one land-test (4), and one made strict or
--full after its booking releases land-test-staging, then books land-test before its merged gate.
Kept out of test_land_staging so each module's real-git run stays well under the test pool's
unit timeout."""
import os
import unittest
from unittest import mock

from kitlib import gate as G
import kitlib.config as kconfig
from kitlib.gate import reuse, runner
from kitlib.landing import lander as land
from kitlib.landing import verdicts
from kitlib.git import repo as repomod
from kitlib.records import gate_ok
from tests.gate import fixtures, test_reuse
from tests.gate.test_merged_steps import COMPILES
from tests.landing import test_land_strict
from tests.landing.test_land_queue import add_land_kinds, land_runs, real_cpu

BRANCH = test_reuse.BRANCH
STAGING = 'staging'
SRC = dict(test_reuse.SRC)
UNIT = dict(test_reuse.UNIT, staging_landing=False)
BUILD = {'name': 'build', 'always': True, 'globs': ['src/*'], 'cmd': ['{python}', '-c', COMPILES],
         'pass': '^tests ok$', 'staging_args': ['--compile'], 'staging_pass': '^compiled$',
         'reuse': True}
LINT = {'name': 'lint', 'always': True, 'globs': ['src/*'], 'cmd': test_reuse.OK_CMD,
        'pass': '^ok$', 'reuse': True}
ALL_RAN = {'unit': 'pass', 'build': 'pass', 'lint': 'pass'}


class StagingBase(test_reuse.ReuseCase):
    KIT = {'staging': {'branch': STAGING}}
    GATE = {'steps': [UNIT, BUILD, LINT]}
    SAME_TREE_ONLY = True       # the records carry no input keys: only the same tree serves

    def setUp(self):
        test_reuse.ReuseCase.setUp(self)
        self.on_main()
        self.repo.git('push', '-q', 'origin', 'main:refs/heads/%s' % STAGING)
        self.repo.git('fetch', '-q', 'origin')
        if self.SAME_TREE_ONLY:
            p = mock.patch.object(reuse, 'record_keys', return_value=(None, None))
            p.start()
            self.addCleanup(p.stop)
        self.kind = kconfig.load(self.repo.root).section('gate')['kind']
        add_land_kinds(self.kinds_path, self.kind)
        self.full_land, self.staging_land = 'land-' + self.kind, 'land-%s-staging' % self.kind

    def land_kinds(self):
        """The kinds the landing booked, in order (its bookings have priority "land")."""
        return [c['kind'] for c in self.book_calls if c.get('priority') == 'land']

    def assert_rebooked(self, why):
        """The landing booked the compile-only land kind, then, made `why` after it, said so,
        released that run and only then booked the full land kind for its merged gate."""
        self.assertEqual(self.land_kinds(), [self.staging_land, self.full_land], self.book_calls)
        self.assertIn("the merged gate runs every selected step (%s), not a staging landing's "
                      "compile-only run: releasing the landing's %s run and booking a %s run for "
                      'it, waiting with no limit' % (why, self.staging_land, self.full_land),
                      self.said)
        first, second = land_runs(self.store)
        self.assertEqual((first['kind'], second['kind']), (self.staging_land, self.full_land))
        self.assertLessEqual(first['ended'], second['held_since'])

    def on_main(self):
        """Main's own commits before the staging branch is made from it: none here."""

    def full_tip(self, files=None):
        wt, tip = self.branch('feature', files or dict(SRC))
        out = self.gate(wt, full=True)
        self.assertEqual(out.code, G.OK, out.lines)
        return wt, tip

    def record(self, out):
        self.assertEqual(out.code, G.OK, out.lines)
        self.assertTrue(out.lines[-1].startswith('LANDED %s on %s' % (BRANCH, STAGING)), out.lines)
        rec, why = gate_ok.verify(self.repo.root, out.merge)
        self.assertIsNone(why)
        return rec

    def results(self, rec):
        return {s['name']: s['result'] for s in rec['steps']}

    def build_args(self, lander):
        with open(os.path.join(lander.gate_logs, 'build.log'), encoding='utf-8') as f:
            return [l for l in f.read().splitlines() if l.startswith('args ')]

    def assert_no_staging_line(self):
        self.assertFalse([l for l in self.said if l.startswith('STAGING')], self.said)


class StagingSteps(StagingBase):

    def test_a_one_branch_staging_landing_leaves_unit_out_compiles_and_reuses_the_rest(self):
        _, tip = self.full_tip()
        lander, out = self.land(BRANCH, tip)
        rec = self.record(out)
        self.assertEqual(self.results(rec), {'build': 'pass', 'lint': 'reused'})
        self.assertEqual((rec['staging_skipped'], rec['full'], gate_ok.mode_of(rec)),
                         (['unit'], False, 'select'))
        self.assertIn(runner.STAGING_SKIPPED % 'unit', self.said)
        self.assertIn(runner.STAGING_ARGS % 'build', self.said)
        self.assertTrue([l for l in self.lines() if l.startswith('REUSED lint: ')], self.said)
        self.assertFalse([l for l in self.lines() if l.startswith('REUSED build')], self.said)
        self.assertIn(reuse.PREFIX + 'NONE build: it runs its staging_args (the compile-only run '
                      'of a staging landing)', self.lines())
        self.assertEqual(self.build_args(lander), ["args ['--compile']"])
        self.assert_not_ran(lander.gate_logs, 'unit', 'lint')
        self.assertFalse([l for l in self.lines('STORED') if 'build' in l], self.said)
        self.assertEqual(self.land_kinds(), [self.staging_land], self.book_calls)
        self.assertEqual(real_cpu('land-test-staging'), 2)      # the owner's call, 11-16

    def test_a_fresh_staging_landing_runs_every_selected_step(self):
        _, tip = self.full_tip()
        lander, out = self.land(BRANCH, tip, fresh=True)
        rec = self.record(out)
        self.assertEqual((self.results(rec), rec['staging_skipped']), (ALL_RAN, None))
        self.assert_no_staging_line()
        self.assertEqual(self.build_args(lander), ['args []'])
        self.assertEqual(self.land_kinds(), [self.full_land], self.book_calls)  # known at booking


class InputTreeStagingSteps(StagingBase):
    """Staging moves on every landing, so a one-branch staging landing's merge rarely holds its
    tip's tree: what it reuses comes from the input keys of the tip's --full record."""
    SAME_TREE_ONLY = False

    def move_staging(self, files):
        other = self.repo.worktree('other')
        for rel, text in files.items():
            self.repo.write(rel, text, root=other)
        moved = self.repo.commit('staging moves', *files, cwd=other)
        self.repo.git('push', '-q', 'origin', '%s:refs/heads/%s' % (moved, STAGING))
        return moved

    def test_a_staging_landing_after_staging_moved_reuses_lint_from_the_input_tree(self):
        _, tip = self.full_tip()
        moved = self.move_staging({'o.py': 'o\n'})
        ended = gate_ok.read(self.repo.root, tip)[0]['ended_utc']
        lander, out = self.land(BRANCH, tip)
        rec = self.record(out)
        self.assertEqual(lander.expect_main, moved)
        self.assertEqual((self.results(rec), rec['staging_skipped']),
                         ({'build': 'pass', 'lint': 'reused'}, ['unit']))
        self.assertIn(reuse.PREFIX + 'HIT lint: the input tree of the full record of %s (ended %s)'
                      % (tip[:10], ended), self.lines())
        self.assertFalse([l for l in self.lines() if l.startswith(reuse.PREFIX + 'SAME TREE')],
                         self.said)
        self.assert_not_ran(lander.gate_logs, 'unit', 'lint')
        self.assertEqual(self.build_args(lander), ["args ['--compile']"])


class ConfigStagingSteps(StagingBase):
    """A landing onto staging whose merge changes a gate config file (the hook module, which a
    branch may change without the owner): config_escalate makes it --full."""
    KIT = dict(StagingBase.KIT, hook={'module': 'kit_hooks'})
    HOOKS = 'GATE_CHECKS = {}\n'

    def on_main(self):
        self.repo.write('tools/kit_hooks.py', self.HOOKS)
        self.main = self.repo.commit('hook module', 'tools/kit_hooks.py')
        self.repo.git('push', '-q', 'origin', 'main')

    def test_a_staging_landing_that_changes_the_gate_config_runs_every_step_in_full(self):
        _, tip = self.full_tip(dict(SRC, **{'tools/kit_hooks.py': '# changed\n' + self.HOOKS}))
        lander, out = self.land(BRANCH, tip)
        rec = self.record(out)
        self.assertTrue(lander.full)
        self.assertEqual((self.results(rec), rec['staging_skipped'], gate_ok.mode_of(rec)),
                         (ALL_RAN, None, 'full'))
        self.assert_no_staging_line()
        self.assert_rebooked('--full, as the GATE CONFIG line says')


class StrictStagingSteps(StagingBase):
    """A strict branch landing onto staging: strict_escalate makes it fresh before the merged gate
    reads staging_gate, so every selected step runs."""

    def land_strict(self, tip):
        self.said[:] = []
        common, why = repomod.common_dir(self.repo.root)
        self.assertIsNotNone(common, why)
        fixtures.gate_full(self.repo.root, tip)
        verdicts.add(common, dict(test_land_strict.REVIEWER, branch=BRANCH, tip=tip,
                                  range='%s..%s' % (self.main[:10], tip)))
        lander = land.Lander(self.repo.root, BRANCH, self.main, tip, say=self.said.append,
                             git_env=self.env, book_kw=self.book_kw, log_dir=self.logs)
        return lander, lander.run()

    def test_a_strict_staging_landing_runs_every_selected_step(self):
        _, tip = self.full_tip(dict(SRC, **{test_land_strict.STRICT: 'x = 1\n'}))
        lander, out = self.land_strict(tip)
        rec = self.record(out)
        self.assertTrue(lander.fresh and not lander.full)
        self.assertEqual((self.results(rec), rec['staging_skipped']), (ALL_RAN, None))
        self.assert_no_staging_line()
        self.assert_rebooked('fresh: a strict branch')


if __name__ == '__main__':
    unittest.main()
