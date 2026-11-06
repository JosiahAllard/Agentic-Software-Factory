"""kitlib.hubjobs.store_upkeep: the hub's daily commit graph and gc of the PM data worktree's
git common dir, with the git children (`_run`), the common dir and the machine lock stubbed."""
import os
import tempfile
import threading
import time
import unittest
from unittest import mock

from kitlib import hubjobs
from kitlib import pmdata
from kitlib.gate import land
from kitlib.git import cmd
from kitlib.hubjobs import store_upkeep as su
from kitlib.hubsite import jobs
from kitlib.lock.store import EXIT_FAIL, EXIT_RESERVED, EXIT_TIMEOUT, LockError

NOW = 2000000000.0


class FakeSlot(object):
    def __init__(self, log):
        self.log = log

    def __enter__(self):
        self.log.append('slot held')
        return self

    def __exit__(self, *exc):
        self.log.append('slot released')
        return False


class UpkeepCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.common = os.path.join(tmp.name, 'repo', '.git')
        self.pack = os.path.join(self.common, 'objects', 'pack')
        os.makedirs(self.pack)
        self.root = os.path.join(tmp.name, 'pm')
        self.log = []               # git steps and slot events, in order
        self.calls = []             # (args, cwd, timeout) of each git child
        self.booked = []
        self.answers = {}           # git subcommand -> Result
        self.loose = 7000
        self.book_error = None
        self.stop = threading.Event()
        self.events = []
        self.slot_runs = []         # (slot, args) of each git child started in a slot's job
        for p in (mock.patch.object(su, '_run', side_effect=self.fake_run),
                  mock.patch.object(su, '_slot_run', side_effect=self.fake_slot_run),
                  mock.patch.object(su.gitrepo, 'common_dir',
                                    side_effect=lambda root: (self.common, None))):
            p.start()
            self.addCleanup(p.stop)
        self.job = su.StoreUpkeep(self.root, book=self.fake_book, clock=lambda: NOW)
        self.holder_pids = []       # live git processes that may hold maintenance.lock
        self.job.holders = self.fake_holders

    def fake_holders(self):
        if isinstance(self.holder_pids, Exception):
            raise self.holder_pids
        return list(self.holder_pids)

    def fake_run(self, args, cwd, timeout=cmd.GIT_TIMEOUT):
        self.calls.append((list(args), cwd, timeout))
        sub = cmd.subcommand(args)
        self.log.append(sub)
        if sub in self.answers:
            return self.answers[sub]
        if sub == 'count-objects':
            return cmd.Result(0, ('count: %d\nsize: 9\nin-pack: 5\n' % self.loose).encode(), '')
        if sub == 'commit-graph':
            self.stop_after_graph()
        return cmd.Result(0, b'', '')

    def fake_slot_run(self, slot, args, cwd):
        self.slot_runs.append((slot, list(args)))
        return self.fake_run(args, cwd, None)

    def stop_after_graph(self):
        pass

    def fake_book(self, kind, repo, slug, **kw):
        self.booked.append((kind, repo, slug, kw))
        if self.book_error:
            raise self.book_error
        return FakeSlot(self.log)

    def run_job(self):
        ctx = jobs.Ctx(self.stop, lambda word, detail=None: self.events.append((word, detail)))
        return self.job(ctx)

    def place(self, name, age_s):
        path = os.path.join(self.common, *name.split('/'))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as f:
            f.write(b'x')
        os.utime(path, (NOW - age_s, NOW - age_s))
        return path


class Steps(UpkeepCase):
    def test_writes_the_commit_graph_untimed_then_leaves_a_small_store_unpacked(self):
        self.loose = 6699
        out = self.run_job()
        self.assertIsInstance(out, str)
        self.assertEqual(self.calls[0], (su.GRAPH, self.root, None))
        self.assertEqual(su.GRAPH, ['commit-graph', 'write', '--reachable', '--changed-paths',
                                    '--split'])
        self.assertEqual(self.log, ['commit-graph', 'count-objects'])
        self.assertEqual(self.booked, [])
        self.assertIn('6699 loose objects, under 6700: no gc', out)
        self.assertEqual(self.events, [])

    def test_gc_from_the_threshold_on_runs_untimed_inside_a_slot_holding_the_landing_resource(self):
        self.loose = 6700
        out = self.run_job()
        self.assertEqual(self.log, ['commit-graph', 'count-objects', 'slot held', 'gc',
                                    'slot released'])
        args, cwd, timeout = self.calls[-1]
        self.assertEqual((cwd, timeout), (self.root, None))
        self.assertEqual(args, ['-c', 'gc.writeCommitGraph=false', '-c', 'gc.packRefs=false',
                                '-c', 'pack.threads=%d' % su.CORES, 'gc', '--quiet'])
        (kind, repo, slug, kw), = self.booked
        self.assertEqual((kind, repo, slug), (su.KIND, 'repo', su.SLUG))
        self.assertEqual(kw['resource'], land.queue_key(self.common))
        self.assertEqual((kw['cores'], kw['wait_s']), (su.CORES, su.WAIT_S))
        self.assertNotIn('priority', kw)            # a landing plans ahead of it, not it of one
        self.assertIn('gc packed 6700 loose objects', out)
        (slot, args), = self.slot_runs              # gc, and only gc, runs in the slot's job
        self.assertIsInstance(slot, FakeSlot)
        self.assertEqual(args, su.GC)

    def test_the_lock_books_under_the_name_landings_use(self):
        top = os.path.dirname(self.common)
        cfg = mock.Mock(repo={'name': 'Kit-Name'})
        with mock.patch.object(su.kconfig, 'load', return_value=cfg) as load:
            self.run_job()
        load.assert_called_with(top)
        self.assertEqual(self.booked[-1][1], 'Kit-Name')
        self.assertEqual(su._repo_name(self.common + os.sep), 'repo')   # no .kit.json: the folder
        with mock.patch.object(su.kconfig, 'load', side_effect=su.ConfigError('bad')):
            self.assertEqual(su._repo_name(self.common), 'repo')

    def test_a_busy_lock_or_a_hold_skips_the_gc_to_the_next_pass(self):
        for code in (EXIT_TIMEOUT, EXIT_RESERVED):
            self.log[:] = []
            self.book_error = LockError(code, 'busy here')
            out = self.run_job()
            self.assertIsInstance(out, str)
            self.assertNotIn('gc', self.log)
            self.assertIn('gc skipped to the next pass', out)
            self.assertIn('exit %d: busy here' % code, out)

    def test_another_lock_refusal_is_a_failure(self):
        self.book_error = LockError(EXIT_FAIL, 'broken')
        with self.assertRaises(su.UpkeepFailed) as cm:
            self.run_job()
        self.assertNotIn('gc', self.log)
        self.assertIn('commit graph written', str(cm.exception))
        self.assertIn('machine lock refused it (exit 1: broken)', str(cm.exception))

    def test_a_failed_commit_graph_still_lets_the_gc_run_and_fails_the_pass(self):
        self.answers['commit-graph'] = cmd.Result(128, b'', "fatal: Unable to create\n"
                                                  "'commit-graph-chain.lock': File exists.\n")
        with self.assertRaises(su.UpkeepFailed) as cm:
            self.run_job()
        self.assertIn('gc', self.log)
        self.assertIn("commit-graph write failed: 'commit-graph-chain.lock': File exists.",
                      str(cm.exception))
        self.assertIn('gc packed', str(cm.exception))

    def test_a_failed_gc_or_count_fails_the_pass(self):
        self.answers['gc'] = cmd.Result(None, b'', 'timed out')
        with self.assertRaises(su.UpkeepFailed) as cm:
            self.run_job()
        self.assertIn('gc failed: timed out', str(cm.exception))
        self.assertIn('slot released', self.log)
        self.answers['count-objects'] = cmd.Result(1, b'', '')
        with self.assertRaises(su.UpkeepFailed) as cm:
            self.run_job()
        self.assertIn('count-objects failed: exit 1', str(cm.exception))

    def test_a_stop_between_the_steps_ends_the_pass_as_a_skip(self):
        self.stop_after_graph = self.stop.set
        out = self.run_job()
        self.assertIsInstance(out, jobs.Skip)
        self.assertEqual(self.log, ['commit-graph'])
        self.assertIn('stopped before the gc', out.note)

    def test_no_common_dir_is_a_failure_and_runs_no_git(self):
        with mock.patch.object(su.gitrepo, 'common_dir', return_value=(None, 'not a repo')):
            with self.assertRaises(su.UpkeepFailed):
                self.run_job()
        self.assertEqual(self.calls, [])


class Findings(UpkeepCase):
    def test_old_idx_less_packs_are_reported_and_left_in_place(self):
        orphan = self.place('objects/pack/pack-old.pack', su.PACK_AGE_S + 60)
        young = self.place('objects/pack/pack-young.pack', su.PACK_AGE_S - 60)
        whole = self.place('objects/pack/pack-whole.pack', 10 * su.PACK_AGE_S)
        idx = self.place('objects/pack/pack-whole.idx', 10 * su.PACK_AGE_S)
        self.loose = 10
        out = self.run_job()
        self.assertEqual(self.events, [('findings', '1')])
        self.assertIn('finding: pack-old.pack has no .idx (24 h old)', out)
        self.assertNotIn('pack-young', out)
        self.assertNotIn('pack-whole', out)
        for path in (orphan, young, whole, idx):
            self.assertTrue(os.path.exists(path), path)

    def test_ages_past_two_days_read_in_days(self):
        self.place('objects/pack/pack-old.pack', 3 * 86400)
        self.assertIn('3 d old', su.scan(self.common, NOW)[0])


class StaleLock(UpkeepCase):
    """The recurrence check: a planted objects/maintenance.lock older than LOCK_AGE_S."""

    def test_a_stale_lock_with_no_git_alive_is_pushed_to_the_pm_and_left_in_place(self):
        lock = self.place('objects/maintenance.lock', 13 * 3600)
        self.loose = 10
        out = self.run_job()
        self.assertEqual(self.events, [('stale-lock', '13h')])
        self.assertIn('finding: stale %s, 13 h old, no git maintenance or gc alive' % lock, out)
        self.assertTrue(os.path.exists(lock))
        # the push names the job event; push.JOB_EACH gives the PM its text, once per detail
        from kitlib.hubsite import push
        self.assertIn('maintenance.lock', push.JOB_EACH['stale-lock'])

    def test_it_is_pushed_again_every_pass_while_it_stays(self):
        self.place('objects/maintenance.lock', 13 * 3600)
        self.run_job()
        self.run_job()
        self.assertEqual(self.events, [('stale-lock', '13h')] * 2)

    def test_a_lock_a_live_git_may_hold_is_a_note_not_a_push(self):
        lock = self.place('objects/maintenance.lock', 13 * 3600)
        self.holder_pids = [4242]
        out = self.run_job()
        self.assertEqual(self.events, [])
        self.assertIn('%s is 13 h old, but a git that may hold it is alive (pid 4242)' % lock, out)

    def test_a_process_listing_that_fails_still_pushes(self):
        self.place('objects/maintenance.lock', 3 * 86400)
        self.holder_pids = OSError('no snapshot')
        out = self.run_job()
        self.assertEqual(self.events, [('stale-lock', '72h')])
        self.assertIn('3 d old', out)
        self.assertIn('could not be listed (no snapshot)', out)

    def test_a_young_lock_is_left_alone(self):
        self.place('objects/maintenance.lock', su.LOCK_AGE_S - 60)
        self.holder_pids = AssertionError('a young lock lists no processes')
        out = self.run_job()
        self.assertEqual(self.events, [])
        self.assertNotIn('maintenance.lock', out)

    def test_lock_holders_are_the_gits_running_maintenance_or_gc_or_unreadable(self):
        rows = [(10, 1, 'git.exe'), (11, 1, 'git.exe'), (12, 1, 'git'), (13, 1, 'python.exe'),
                (14, 1, 'git.exe'), (os.getpid(), 1, 'git.exe'), (15, 1, 'GIT.EXE')]
        lines = {10: '"C:\\Git\\cmd\\git.exe" maintenance run --auto --no-quiet',
                 11: 'git cat-file --batch', 12: 'git -C /x gc --auto', 13: 'python gc maintenance',
                 14: None, os.getpid(): 'git gc', 15: 'git.exe fetch origin'}
        got = su.lock_holders(snapshot=lambda: rows, command_line=lines.get)
        self.assertEqual(got, [10, 12, 14])

    def test_an_unreadable_git_gone_by_a_second_listing_is_no_holder(self):
        listings = [[(20, 1, 'git.exe'), (21, 1, 'git.exe')], [(21, 1, 'git.exe')]]
        got = su.lock_holders(snapshot=lambda: listings.pop(0), command_line=lambda pid: None)
        self.assertEqual(got, [21])


class LaunchSlot(object):
    """A booked slot's start and wait (kitlib.lock.run.Slot), recording what was started."""
    id = 'r-test'

    def __init__(self, how='done', code=0, start_error=None):
        self.env = {'MACHINE_LOCK_RUN': 'r-test', 'GIT_DIR': 'Z:/elsewhere', 'PATH': 'p'}
        self.how, self.code, self.start_error = how, code, start_error
        self.started = []

    def start(self, argv, **kw):
        if self.start_error:
            raise self.start_error
        self.started.append((argv, kw))
        kw['stderr'].write(b'warning: one\n')
        return 'proc'

    def wait(self, p):
        assert p == 'proc'
        return self.code, self.how


class SlotRun(unittest.TestCase):
    def test_gc_starts_in_the_slots_job_with_the_git_child_environment(self):
        slot = LaunchSlot()
        r = su._slot_run(slot, su.GC, 'Z:/pm')
        self.assertEqual(r, cmd.Result(0, b'', 'warning: one'))
        (argv, kw), = slot.started
        self.assertEqual(argv, ['git'] + su.GC)
        self.assertEqual(kw['cwd'], 'Z:/pm')
        env = kw['env']
        self.assertEqual(env['MACHINE_LOCK_RUN'], 'r-test')     # a lock call inside it nests
        self.assertNotIn('GIT_DIR', env)                        # scrubbed like every git child
        pairs = {env['GIT_CONFIG_KEY_%d' % i]: env['GIT_CONFIG_VALUE_%d' % i]
                 for i in range(int(env['GIT_CONFIG_COUNT']))}
        self.assertEqual((pairs['maintenance.auto'], pairs['gc.auto']), ('false', '0'))

    def test_a_stopped_run_or_a_start_that_fails_gives_no_exit_code(self):
        r = su._slot_run(LaunchSlot(how='cancelled', code=1), su.GC, 'Z:/pm')
        self.assertIsNone(r.rc)
        self.assertIn('the machine lock stopped run r-test', r.err)
        r = su._slot_run(LaunchSlot(start_error=OSError('no git')), su.GC, 'Z:/pm')
        self.assertEqual((r.rc, r.err), (None, 'could not run git in Z:/pm: no git'))
        r = su._slot_run(LaunchSlot(code=128), su.GC, 'Z:/pm')
        self.assertEqual(r.rc, 128)


class Make(unittest.TestCase):
    def test_no_pm_data_worktree_gives_no_job(self):
        with mock.patch.object(pmdata, 'machine_settings', return_value=None):
            self.assertIsNone(su.make(mock.Mock(home=None)))

    def test_an_unreadable_pm_data_setting_gives_no_job(self):
        with mock.patch.object(pmdata, 'machine_settings', side_effect=pmdata.PmDataError('bad')):
            self.assertIsNone(su.make(mock.Mock(home=None)))

    def test_the_job_runs_daily_on_the_pm_data_worktree_and_not_at_hub_start(self):
        with mock.patch.object(pmdata, 'machine_settings',
                               return_value={'path': 'Z:/pm', 'branch': 'pmdata', 'remote': 'o'}):
            job = su.make(mock.Mock(home=None))
        self.assertEqual((job.name, job.every_s), ('store-upkeep', 86400))
        self.assertGreater(job.first_s, jobs.FIRST_S)
        self.assertEqual(job.run.root, 'Z:/pm')

    def test_discovery_finds_it(self):
        self.assertIn('kitlib.hubjobs.store_upkeep', [m.module for m in hubjobs.makers()])

    def test_loose_count_reads_count_objects(self):
        self.assertEqual(su.loose_count('count: 63147\nsize: 1\n'), 63147)
        self.assertIsNone(su.loose_count('size: 1\n'))


if __name__ == '__main__':
    unittest.main()
