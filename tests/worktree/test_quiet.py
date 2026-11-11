"""The quiet hold of a quiet release (kitlib.worktree.quiet quiet_hold; docs/backlog.md "Stopping a
pull"): the holder's tree is quiet when no process is in it, no machine or landing lock names it,
no verdict for its branch waits and 4 h pass with no activity (the git dir's mtimes not counted).
The backlog's side of a quiet release, with the probe faked, is
tests/backlog/test_plan_quiet_release.py."""
import json
import os
import time
import unittest
from unittest import mock

from kitlib.records import verdict_entry
from kitlib.worktree import classify, holders, quiet, remove
from tests.backlog.helpers import BRANCH, StoreCase
from tests.realgit import real_git

OTHER = 'tools/other-branch'
QUIET_S = 5 * 3600
NO_LOCK = (lambda: ([], None))
NO_PROCS = (lambda: (([], 0, 1), None))


@real_git('git reflog: the fake has no reflog command, and quiet_hold reads the reflog stamps')
class QuietHoldProbe(StoreCase):
    """quiet_hold on a real worktree, the run-wide reads faked."""
    remote = False

    def setUp(self):
        super().setUp()
        self.tree = self.repo.worktree('other', OTHER)
        self.later = time.time() + QUIET_S

    def tip(self, branch):
        return self.repo.git('rev-parse', 'refs/heads/%s' % branch)

    def hold(self, now=None, machine=NO_LOCK, procs=NO_PROCS, branch=OTHER, main=None):
        return quiet.quiet_hold(self.repo.root, branch, now=now or self.later, machine=machine,
                                procs=procs, main=main)

    def test_a_fresh_tree_is_active(self):
        why, quiet_s = self.hold(now=time.time())
        self.assertIsNone(quiet_s)
        self.assertIn('under the 4 h quiet bar', why)

    def test_a_tree_idle_past_the_bar_is_quiet(self):
        why, quiet_s = self.hold()
        self.assertIsNone(why)
        self.assertGreaterEqual(quiet_s, quiet.QUIET_AFTER_S)

    def test_git_dir_mtimes_are_not_activity_and_tree_files_are(self):
        soon = self.later - 600
        gitdir = self.repo.git('rev-parse', '--path-format=absolute', '--git-dir', cwd=self.tree)
        for path in (os.path.join(self.tree, '.git'), os.path.join(gitdir, 'HEAD'),
                     os.path.join(gitdir, 'index')):
            os.utime(path, (soon, soon))
        self.assertEqual(self.hold()[0], None)
        os.utime(self.repo.write('work.txt', 'x\n', root=self.tree), (soon, soon))
        why, _ = self.hold()
        self.assertIn('active 0.2 h ago', why)
        self.assertIn('work.txt', why)

    def test_a_reflog_entry_is_activity(self):
        # The only fresh signal is the reflog: a commit dated 10 days back, put on a branch no
        # tree is on (no file mtimes) now, so only its reflog entry is new.
        old = '%d +0000' % (int(time.time()) - 10 * 86400)
        with mock.patch.dict(os.environ, {'GIT_COMMITTER_DATE': old, 'GIT_AUTHOR_DATE': old}):
            sha = self.repo.git('commit-tree', self.repo.git('rev-parse', 'HEAD^{tree}'),
                                '-p', 'HEAD', '-m', 'old work')
        self.repo.git('branch', 'kit/reflog-only', sha)
        why, _ = self.hold(branch='kit/reflog-only', now=time.time() + 3600)
        self.assertIn('active 1.0 h ago (newest: a reflog entry of kit/reflog-only)', why or '')
        self.assertEqual(self.hold(branch='kit/reflog-only')[0], None)

    def test_a_locked_tree_refuses(self):
        self.repo.git('worktree', 'lock', '--reason', 'owner keeps it', self.tree)
        self.addCleanup(self.repo.git, 'worktree', 'unlock', self.tree, check=False)
        why, _ = self.hold()
        self.assertIn('is locked with `git worktree lock`: owner keeps it', why)

    def test_an_exempt_tree_refuses(self):
        with mock.patch.object(remove, 'exemption', return_value='kept by ruling X') as ex:
            why, _ = self.hold()
        self.assertIn('is exempt (kept by ruling X)', why)
        self.assertTrue(ex.called)

    def test_a_process_in_the_tree_refuses(self):
        procs = lambda: (([(4242, 'python.exe', os.path.join(self.tree, 'sub'))], 0, 1), None)
        why, _ = self.hold(procs=procs)
        self.assertIn('process pid 4242 python.exe has its current directory in', why)

    def test_an_unread_process_and_a_held_folder_refuse(self):
        with mock.patch.object(holders, 'held_folder', return_value=(self.tree, 32)):
            why, _ = self.hold(procs=lambda: (([], 2, 3), None))
        self.assertIn('2 process(es) could not be read', why)

    def test_a_machine_lock_hold_refuses(self):
        machine = lambda: ([{'repo': 'kit', 'slug': 'other', 'id': 'r1', 'note': 'gate'}], None)
        why, _ = self.hold(machine=machine)
        self.assertIn('machine lock: kit/other run r1 (gate)', why)

    def test_an_unreadable_machine_lock_refuses(self):
        why, _ = self.hold(machine=lambda: (None, 'no file'))
        self.assertIn('unreadable: the machine lock cannot be read', why)

    def test_the_landing_lock_naming_the_branch_refuses(self):
        with mock.patch.object(classify, 'landing_branches', return_value=({OTHER}, None)):
            why, _ = self.hold()
        self.assertEqual(why, 'the landing lock names %s' % OTHER)

    def queue(self, tip):
        path = verdict_entry.entry_path(self.repo.common_dir(), OTHER)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        entry = {'branch': OTHER, 'tip': tip, 'verdict': 'LAND', 'risk': 'Low',
                 'reviewer': 'pm-kit:analyst', 'reviewer_model': 'opus', 'reviewer_id': 'a1',
                 'author': 'pm-kit:worker', 'range': 'main..%s' % tip, 'session': 'kit-a',
                 'records': [], 'queued_utc': '2026-11-05T00:00:00Z', 'state': 'queued',
                 'last_result': None}
        self.assertIsNone(verdict_entry.check(entry))
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(entry, f)
        return path

    def test_a_waiting_verdict_refuses_and_a_landed_one_does_not(self):
        tip = self.tip(OTHER)
        self.queue(tip)
        why, _ = self.hold()
        self.assertIn('a verdict for %s waits in the landing queue (queued at' % OTHER, why)
        self.assertEqual(self.hold(main=tip)[0], None)

    def test_a_malformed_verdict_entry_refuses(self):
        with open(self.queue(self.tip(OTHER)), 'w', encoding='utf-8') as f:
            f.write('{not json')
        self.assertIn('is not JSON', self.hold()[0])

    def test_the_main_checkout_branch_is_never_quiet(self):
        main = self.repo.git('symbolic-ref', '--short', 'HEAD')
        self.assertEqual(self.hold(branch=main)[0], 'the main checkout is on %s' % main)

    def test_no_local_branch_is_not_quiet(self):
        self.assertIn('no local branch kit/none', self.hold(branch='kit/none')[0])

    def test_a_branch_no_tree_is_on_reads_its_tip_and_reflog(self):
        self.assertEqual(self.hold(branch=BRANCH)[0], None)
        self.assertIn('under the 4 h quiet bar', self.hold(branch=BRANCH, now=time.time())[0])


class Probes(unittest.TestCase):

    def test_the_probes_are_the_classifiers_liveness_and_the_quiet_hold(self):
        # What tools/backlog.py hands the backlog opener (tests/backlog/test_cli.py OpenerProbes).
        self.assertEqual(quiet.PROBES, (classify.branch_liveness, quiet.quiet_hold))
