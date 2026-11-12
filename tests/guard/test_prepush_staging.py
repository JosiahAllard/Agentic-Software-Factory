"""The pre-push guard and a staging branch (docs/gate-landing.md "Staging branch"): the staging
branch is held as main (no delete, no forced push), and its setting is read from <remote>/<main>
as last fetched, so a staging tip whose .kit.json drops the section cannot drop its own guard.
A push to main itself then needs a tested record (kitlib.records.gate_ok KINDS: a daily full
record, or a promotion record made on the main it replaces; docs/gate-records.md "Tested
records")."""
import json
import os
from unittest import mock

from kitlib.guard import prepush
from kitlib.records import gate_ok
from tests.guard import prepush_case as pc
from tests.realgit import real_git

ZERO = '0' * 40
STAGING_ON = {'repo': {'name': 'kit-test', 'push_block': False}, 'staging': {'branch': 'staging'}}
STEPS = [{'name': 'unit', 'result': 'pass', 'seconds': 1.0}]
STARTED = '2026-11-12T10:00:00Z'


class StagingRepo(pc.PrePushCase):
    """The helpers; no tests of its own, so a subclass does not run them again."""

    def on_main(self, kit):
        self.repo.write('.kit.json', json.dumps(kit))
        sha = self.repo.commit('kit config', '.kit.json')
        self.repo.git('push', '-q', 'origin', 'main', '%s:refs/heads/staging' % sha)
        return sha

    def staging_tip(self, kit):
        """A commit on staging, pushed there with no hook, writing `kit` as its .kit.json."""
        self.repo.git('checkout', '-q', '--detach', 'origin/staging')
        self.repo.write('.kit.json', json.dumps(kit))
        sha = self.repo.commit('staging edit', '.kit.json')
        self.repo.git('push', '-q', 'origin', '%s:refs/heads/staging' % sha)
        self.repo.git('checkout', '-q', 'main')
        return sha

    def check(self, line):
        return prepush.check(self.repo.root, [line], gate_ok=lambda r, s: (None, 'none'))


class StagingGuard(StagingRepo):
    def test_deleting_the_staging_branch_is_refused(self):
        tip = self.on_main({'repo': {'name': 'kit-test', 'push_block': False},
                            'staging': {'branch': 'staging'}})
        ok, msg = self.check('(delete) %s refs/heads/staging %s' % (ZERO, tip))
        self.assertFalse(ok)
        self.assertIn('this push deletes staging', msg)

    def test_a_staging_tip_that_drops_the_setting_keeps_its_guard(self):
        self.on_main({'repo': {'name': 'kit-test', 'push_block': False},
                      'staging': {'branch': 'staging'}})
        tip = self.staging_tip({'repo': {'name': 'kit-test', 'push_block': False}})
        ok, msg = self.check('(delete) %s refs/heads/staging %s' % (ZERO, tip))
        self.assertFalse(ok)
        self.assertIn('this push deletes staging', msg)

    def test_a_stale_fetch_of_main_does_not_drop_the_tip_s_own_guard(self):
        self.on_main({'repo': {'name': 'kit-test', 'push_block': False}})
        tip = self.staging_tip({'repo': {'name': 'kit-test', 'push_block': False},
                                'staging': {'branch': 'staging'}})   # main as fetched: none
        ok, msg = self.check('(delete) %s refs/heads/staging %s' % (ZERO, tip))
        self.assertFalse(ok)
        self.assertIn('this push deletes staging', msg)

    def test_with_no_staging_setting_a_branch_named_staging_is_not_held(self):
        tip = self.on_main({'repo': {'name': 'kit-test', 'push_block': False}})
        self.assertEqual(self.check('(delete) %s refs/heads/staging %s' % (ZERO, tip)),
                         (True, None))


class LandedOnStaging(StagingRepo):
    """Staging on, and a commit landed on origin/staging that main has not reached. Helpers only."""

    def setUp(self):
        StagingRepo.setUp(self)
        self.main = self.on_main(STAGING_ON)
        self.repo.git('checkout', '-q', '--detach', 'origin/staging')
        self.repo.write('f.txt', 'landed\n')
        self.tip = self.repo.commit('landed on staging', 'f.txt')   # not yet on main
        self.repo.git('push', '-q', 'origin', '%s:refs/heads/staging' % self.tip)
        self.repo.git('checkout', '-q', 'main')

    def record(self, rev, kind, base=None, full=True, forged=False):
        """A tested record of `kind` for `rev`, as the daily run or a promotion writes it.
        `forged`: built and written past record_tested_pass, which refuses a daily non-full one."""
        cfg = gate_ok.gate_config_at(self.repo.root, rev)
        if not forged:
            return gate_ok.record_tested_pass(self.repo.root, rev, kind, cfg, pc.KIT, 'kit-test',
                                              STARTED, STEPS, base=base, config_rev=rev, full=full)
        rec = gate_ok.build(self.repo.root, rev, cfg, pc.KIT, 'kit-test', STARTED, STEPS,
                            base=base, config_rev=rev, full=full)
        return gate_ok.write(self.repo.root, rec, kind), rec

    def to_main(self, sha=None):
        sha = sha or self.tip
        return prepush.check(self.repo.root, ['refs/heads/main %s refs/heads/main %s'
                                              % (sha, self.main)],
                             gate_ok=lambda r, s: (None, 'none'))


class TestedRecordGuard(LandedOnStaging):
    """With staging on, main moves only to a commit with a tested record. A record that is missing,
    invalid or made on another main is a final refusal; one that cannot be read is transient
    (prepush.TRANSIENT_PREFIX: a retry may pass)."""

    def refused(self, transient):
        ok, msg = self.to_main()
        self.assertFalse(ok)
        self.assertEqual(msg.startswith(prepush.TRANSIENT_PREFIX), transient, msg)
        return msg

    def test_a_push_to_main_with_no_tested_record_is_refused(self):
        msg = self.refused(transient=False)
        self.assertIn('%s has no daily full record, and no promotion record made on main %s'
                      % (self.tip[:12], self.main[:12]), msg)
        self.assertIn('no daily full record for %s' % self.tip, msg)
        self.assertIn('no promotion record for %s' % self.tip, msg)
        self.assertIn('Instead: land onto origin/staging', msg)
        self.assertNotIn('promote.py', msg)

    def test_a_daily_full_record_lets_main_move(self):
        path, rec = self.record(self.tip, 'daily')
        self.assertIn(os.sep + 'kit-daily-full' + os.sep, path)
        self.assertEqual((rec['full'], rec['mode']), (True, 'full'))
        self.assertEqual(self.to_main(), (True, None))

    def test_a_branch_gate_record_is_not_a_tested_record(self):
        self.passed(self.tip)                       # the gate-OK record: a fast-tier gate's
        ok, msg = self.to_main()
        self.assertFalse(ok)
        self.assertIn('no daily full record', msg)

    def test_a_daily_record_from_a_run_without_full_is_refused(self):
        with self.assertRaises(gate_ok.RecordError):
            self.record(self.tip, 'daily', full=False)
        self.record(self.tip, 'daily', full=False, forged=True)
        msg = self.refused(transient=False)
        self.assertIn('the daily full record for %s is not from a --full run' % self.tip, msg)

    def test_a_promotion_record_counts_only_on_the_main_it_was_made_on(self):
        self.record(self.tip, 'promotion', base=self.with_kit)     # made on an older main
        msg = self.refused(transient=False)
        self.assertIn('was made on main', msg)
        self.record(self.tip, 'promotion', base=self.main)
        self.assertEqual(self.to_main(), (True, None))

    def test_a_select_promotion_record_is_written_as_select_and_counts(self):
        path, rec = self.record(self.tip, 'promotion', base=self.main, full=False)
        self.assertIn(os.sep + 'kit-promotion' + os.sep, path)
        self.assertEqual((rec['full'], rec['mode'], rec['base']), (False, 'select', self.main))
        self.assertEqual(self.to_main(), (True, None))

    def test_a_promotion_record_with_no_known_mode_is_refused(self):
        _path, rec = self.record(self.tip, 'promotion', base=self.main, full=False, forged=True)
        rec['mode'] = 'full'                         # disagrees with full: mode_of reads None
        with open(gate_ok.record_path(self.repo.root, self.tip, 'promotion'), 'w',
                  encoding='utf-8', newline='\n') as f:
            json.dump(rec, f)
        ok, msg = self.to_main()
        self.assertFalse(ok)
        self.assertIn('names no mode it ran in', msg)

    def test_a_record_file_that_cannot_be_read_is_a_transient_refusal(self):
        path = gate_ok.record_path(self.repo.root, self.tip, 'daily')
        os.makedirs(path)                            # open() fails: not a missing file
        msg = self.refused(transient=True)
        self.assertIn('pre-push: refused, cannot read the tested records of %s (cannot read %s'
                      % (self.tip[:12], path), msg)
        self.assertIn('; push again.', msg)
        os.rmdir(path)                               # the read clears: the retry passes
        self.record(self.tip, 'daily')
        self.assertEqual(self.to_main(), (True, None))

    def test_git_failing_to_read_a_record_s_commit_is_a_transient_refusal(self):
        self.record(self.tip, 'daily')
        with mock.patch.object(gate_ok.refs, 'tree_of', return_value=(None, 'git ended')):
            msg = self.refused(transient=True)
        self.assertIn('cannot read the tree of %s: git ended' % self.tip, msg)
        real = gate_ok.refs.is_ancestor

        def is_ancestor(a, d, cwd):                  # only the record's config_rev check fails
            return (None, 'git ended') if (a, d) == (self.tip, self.tip) else real(a, d, cwd)
        with mock.patch.object(gate_ok.refs, 'is_ancestor', side_effect=is_ancestor):
            msg = self.refused(transient=True)       # config_rev is known; git gave no answer
        self.assertIn('cannot tell whether config_rev %s is in the history of %s: git ended'
                      % (self.tip, self.tip), msg)
        self.assertEqual(self.to_main(), (True, None))

    def test_a_reader_bug_is_a_final_refusal(self):
        with mock.patch.object(gate_ok, 'verify_for_main', side_effect=ValueError('bug')):
            msg = self.refused(transient=False)
        self.assertIn('(cannot read tested records: ValueError: bug)', msg)

    def test_a_push_to_the_staging_branch_needs_no_tested_record(self):
        line = 'refs/heads/staging %s refs/heads/staging %s' % (self.tip, self.main)
        self.assertEqual(prepush.check(self.repo.root, [line], gate_ok=lambda r, s: (None, 'none')),
                         (True, None))


@real_git('git push runs the real pre-push hook (core.hooksPath)')
class TestedRecordHook(LandedOnStaging):
    """The rule through the installed hook."""

    def test_the_hook_refuses_and_then_passes_a_promotion_push(self):
        rc, err = self.push('origin', '%s:refs/heads/main' % self.tip)
        self.assertNotEqual(rc, 0, err)
        self.assertIn('has no daily full record, and no promotion record', err)
        self.assertEqual(self.remote_main(), self.main)
        self.record(self.tip, 'daily')
        rc, err = self.push('origin', '%s:refs/heads/main' % self.tip)
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.remote_main(), self.tip)


class StagingOffNeedsNoTestedRecord(StagingRepo):
    def test_a_push_to_main_without_staging_is_as_before(self):
        kit = {'repo': {'name': 'kit-test', 'push_block': False}}
        base = self.on_main(kit)
        self.repo.write('m.txt', 'm\n')
        sha = self.repo.commit('on main', 'm.txt')
        line = 'refs/heads/main %s refs/heads/main %s' % (sha, base)
        asked = []
        self.assertEqual(prepush.check(self.repo.root, [line], gate_ok=lambda r, s: (None, 'none'),
                                       tested=lambda *a: asked.append(a) or (None, 'none')),
                         (True, None))
        self.assertEqual(asked, [])


class TestedRecordFolders(pc.PrePushCase):
    """kitlib.records.gate_ok's tested records: the gate-OK format, a folder per kind."""

    def test_each_kind_has_its_own_folder_and_none_counts_for_another(self):
        sha = self.with_kit
        cfg = gate_ok.gate_config_at(self.repo.root, sha)
        daily, _ = gate_ok.record_tested_pass(self.repo.root, sha, 'daily', cfg, pc.KIT,
                                              'kit-test', STARTED, STEPS, config_rev=sha)
        self.assertEqual(daily, gate_ok.record_path(self.repo.root, sha, 'daily'))
        self.assertEqual(os.path.basename(os.path.dirname(os.path.dirname(daily))),
                         'kit-daily-full')
        self.assertIsNotNone(gate_ok.verify_tested(self.repo.root, sha, 'daily')[0])
        self.assertIsNone(gate_ok.verify_record(self.repo.root, sha)[0])     # not a gate-OK one
        self.assertIsNone(gate_ok.verify_tested(self.repo.root, sha, 'promotion')[0])
        self.passed(sha)
        self.assertNotEqual(gate_ok.record_path(self.repo.root, sha), daily)
        self.assertIsNotNone(gate_ok.verify_tested(self.repo.root, sha, 'daily')[0])   # kept

    def test_a_kind_is_one_of_the_fixed_kinds(self):
        sha = self.with_kit
        cfg = gate_ok.gate_config_at(self.repo.root, sha)
        for kind in (None, 'kit-gate-ok', '../x'):
            with self.assertRaises(gate_ok.RecordError):
                gate_ok.record_tested_pass(self.repo.root, sha, kind, cfg, pc.KIT, 'kit-test',
                                           STARTED, STEPS, config_rev=sha)
            self.assertIsNone(gate_ok.verify_tested(self.repo.root, sha, kind)[0])
        with self.assertRaises(gate_ok.RecordError):
            gate_ok.record_dir(self.repo.root, 'nightly')
        self.assertEqual(gate_ok.KINDS, {'daily': 'kit-daily-full', 'promotion': 'kit-promotion'})


if __name__ == '__main__':
    import unittest
    unittest.main()
