"""The "staging" section (docs/gate-landing.md "Staging branch"): kitlib.config.work_base, the
branches guarded as main, the setting's own rules, and overlay_staging / load_at(staging_from=),
the one loader that reads the staging setting from another revision than the rest."""
import json
import os
import types
import unittest
from unittest import mock

import kitlib.config as kconfig
from kitlib.config import policy
from kitlib.git import cmd as gitcmd
from tests import gitfixture
from tests.config.helpers import TempTree


def load(kit):
    tree = TempTree(kit)
    try:
        return kconfig.load(tree.root)
    finally:
        tree.cleanup()


class WorkBase(unittest.TestCase):
    def test_unset_the_work_base_is_the_main_branch(self):
        for kit, main in (({}, 'main'), ({'repo': {'main_branch': 'trunk'}}, 'trunk')):
            with self.subTest(kit=kit):
                cfg = load(kit)
                self.assertEqual(kconfig.work_base(cfg), main)
                self.assertFalse(kconfig.staging_on(cfg))
                self.assertEqual(cfg.work_base, main)

    def test_set_the_work_base_is_the_staging_branch(self):
        cfg = load({'repo': {'main_branch': 'main'}, 'staging': {'branch': 'staging'}})
        self.assertEqual(kconfig.work_base(cfg), 'staging')
        self.assertTrue(kconfig.staging_on(cfg))
        self.assertEqual(cfg.main_branch, 'main')       # the main branch itself is unchanged

    def test_the_staging_branch_is_guarded_as_main(self):
        self.assertEqual(load({'repo': {'main_branch': 'main'}}).guarded_mains(), ['main'])
        self.assertEqual(load({}).guarded_mains(), ['main'])
        self.assertEqual(load({'repo': {'main_branch': 'trunk'}}).guarded_mains(), ['trunk'])
        self.assertEqual(load({'repo': {'main_branch': 'main'}, 'staging': {'branch': 'stg'}})
                         .guarded_mains(), ['main', 'stg'])

    def test_a_stand_in_config_without_the_property_gives_its_main_branch(self):
        stand_in = types.SimpleNamespace(main_branch='trunk')
        self.assertEqual(kconfig.work_base(stand_in), 'trunk')
        self.assertFalse(kconfig.staging_on(stand_in))

    def test_commit_settings_carry_the_work_base(self):
        on = load({'staging': {'branch': 'stg'}})
        off = load({})
        self.assertEqual(policy.commit_settings(on).get('work_base'), 'stg')
        self.assertEqual(policy.commit_settings(off).get('work_base'), 'main')
        self.assertEqual(policy.commit_settings(on)['main_branch'], 'main')


class Rules(unittest.TestCase):
    def refused(self, kit):
        with self.assertRaises(kconfig.ConfigError) as cm:
            load(kit)
        return str(cm.exception)

    def test_the_main_branch_itself_is_refused(self):
        self.assertIn('the main branch itself', self.refused({'staging': {'branch': 'main'}}))
        self.assertIn('the main branch itself', self.refused(
            {'repo': {'main_branch': 'trunk'}, 'staging': {'branch': 'trunk'}}))

    def test_an_unwritten_main_is_the_one_read_from_the_repository(self):
        # .kit.json writes no main_branch: the derived one (kitlib.git.defaults), not 'main'
        with mock.patch('kitlib.config.load.gitdefaults.main_branch', return_value='trunk'):
            self.assertIn('"staging".branch is trunk, the main branch itself',
                          self.refused({'staging': {'branch': 'trunk'}}))
            cfg = load({'staging': {'branch': 'main'}})
        self.assertEqual((cfg.main_branch, kconfig.work_base(cfg)), ('trunk', 'main'))

    def test_a_name_that_is_no_branch_is_refused(self):
        self.refused({'staging': {'branch': '-x'}})
        self.refused({'staging': {'branch': ''}})
        self.refused({'staging': {'branch': 5}})

    def test_the_section_is_never_a_file(self):
        self.refused({'staging': 'tools/staging.json'})


class Overlay(unittest.TestCase):
    """overlay_staging and load_at(staging_from=) on a scratch repository: commit `plain` writes
    no staging section, `staged` (on another branch) writes one."""

    def setUp(self):
        env = mock.patch.dict(os.environ, gitfixture.fixture_env(), clear=True)
        env.start()
        self.addCleanup(env.stop)
        self.fx = gitfixture.TempRepo()
        self.addCleanup(self.fx.cleanup)
        self.plain = self.commit({'gate': {'steps': []}})
        self.staged = self.commit({'staging': {'branch': 'stg'}})
        self.bad = self.commit({'staging': {'branch': 'main'}})

    def git(self, *args):
        done = gitcmd.subprocess_run(('git',) + args, cwd=self.fx.root, capture_output=True,
                                     text=True, env=gitfixture.fixture_env())
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout.strip()

    def commit(self, kit):
        self.fx.write('.kit.json', json.dumps(kit) + '\n')
        self.git('add', '.kit.json')
        self.git('commit', '-q', '-m', 'config')
        return self.git('rev-parse', 'HEAD')

    def test_the_staging_section_comes_from_the_other_revision(self):
        cfg = kconfig.load_at(self.fx.root, self.plain, staging_from=self.staged)
        self.assertEqual(kconfig.work_base(cfg), 'stg')
        self.assertEqual(cfg.section('gate')['steps'], [])        # the rest is the base's

    def test_a_staging_section_at_the_base_is_not_read(self):
        cfg = kconfig.load_at(self.fx.root, self.staged, staging_from=self.plain)
        self.assertEqual(kconfig.work_base(cfg), 'main')
        self.assertFalse(kconfig.staging_on(cfg))

    def test_the_same_revision_reads_once(self):
        cfg = kconfig.load_at(self.fx.root, self.staged, staging_from=self.staged)
        self.assertEqual(kconfig.work_base(cfg), 'stg')
        with mock.patch('kitlib.config.load.overlay_staging') as overlay:
            kconfig.load_at(self.fx.root, self.staged, staging_from=self.staged)
            kconfig.load_at(self.fx.root, self.staged)
        overlay.assert_not_called()

    def test_a_bad_staging_setting_there_is_refused_by_name(self):
        with self.assertRaises(kconfig.ConfigError) as cm:
            kconfig.load_at(self.fx.root, self.plain, staging_from=self.bad)
        self.assertIn('staging read from %s' % self.bad, str(cm.exception))
        self.assertIn('the main branch itself', str(cm.exception))

    def test_a_suspects_cmd_placeholder_typo_there_is_refused(self):
        typo = self.commit({'staging': {'branch': 'stg', 'suspects_cmd': ['x', '{test_file}']}})
        with self.assertRaises(kconfig.ConfigError) as cm:
            kconfig.load_at(self.fx.root, self.plain, staging_from=typo)
        self.assertIn('staging read from %s' % typo, str(cm.exception))
        self.assertIn('"staging".suspects_cmd: unknown placeholder(s) {test_file}', str(cm.exception))

    def test_a_revision_git_cannot_show_is_refused(self):
        with self.assertRaises(kconfig.ConfigError):
            kconfig.load_at(self.fx.root, self.plain, staging_from='refs/remotes/origin/nothing')

    def fetched(self, rev):
        """Point refs/remotes/origin/main at `rev`, as a fetch would."""
        self.git('update-ref', 'refs/remotes/origin/main', rev)

    def test_the_pre_push_read_takes_the_fetched_main_s_setting(self):
        self.fetched(self.staged)
        cfg = kconfig.load_at_fetched_staging(self.fx.root, self.plain, 'origin')
        self.assertEqual(kconfig.work_base(cfg), 'stg')
        self.assertEqual(cfg.section('gate')['steps'], [])

    def test_the_pre_push_read_keeps_the_base_s_own_when_main_cannot_say(self):
        self.assertEqual(kconfig.work_base(kconfig.load_at_fetched_staging(
            self.fx.root, self.staged, 'origin')), 'stg')          # no fetched main
        self.assertEqual(kconfig.work_base(kconfig.load_at_fetched_staging(
            self.fx.root, self.staged, None)), 'stg')              # no remote
        self.fetched(self.bad)
        self.assertEqual(kconfig.work_base(kconfig.load_at_fetched_staging(
            self.fx.root, self.staged, 'origin')), 'stg')          # a section there that is bad

    def test_the_pre_push_read_holds_the_base_s_own_too(self):
        self.fetched(self.plain)                                     # a stale fetch: no staging
        cfg = kconfig.load_at_fetched_staging(self.fx.root, self.staged, 'origin')
        self.assertEqual(kconfig.work_base(cfg), 'main')
        self.assertIn('stg', cfg.guarded_mains())


if __name__ == '__main__':
    unittest.main()
