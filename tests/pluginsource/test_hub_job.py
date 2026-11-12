"""kitlib.hubsite.plugin_job: the hub's plugin refresh job (docs/plugin-source.md "Hub refresh").
A temp kit with a bare remote (tests.pluginsource Kit) and a temp export root: the job reads the
kit's tracking ref and state.json, and starts tools/plugin_refresh.py --apply as a child, here a
fake that records its command line and answers an exit code (no real process is started)."""
import os
import subprocess
import sys
import threading
import unittest
from unittest import mock

from kitlib.pluginsource import (
    layout as plugin_layout, state as plugin_state, refresh as plugin_refresh)
from kitlib.launcher import accounts
from kitlib.hubsite import jobs, plugin_job
from tests.pluginsource import Kit

WAIT_S = 10


def ctx(stop=False, events=None):
    """A run's Ctx; `events`, when given, gets each (word, detail) the run pushes."""
    ev = threading.Event()
    if stop:
        ev.set()
    told = events if events is not None else []
    return jobs.Ctx(ev, lambda word, detail=None: told.append((word, detail)))


class Child(object):
    """The job's child: records (argv, cwd); answers (code, lines). A run that exits 0 writes
    state.json as a real refresh would (the export at `sha`, every folder rc 0)."""

    def __init__(self, case, code=0, lines=('done',)):
        self.case, self.code, self.lines = case, code, list(lines)
        self.calls = []
        self.entered, self.release = threading.Event(), threading.Event()
        self.release.set()

    def __call__(self, argv, cwd):
        self.calls.append((argv, cwd))
        self.entered.set()
        self.release.wait(WAIT_S)
        if self.code == 0:
            state = plugin_state.read_state(self.case.k.root) or {'folders': {}}
            state['sha'] = self.case.main()
            for f in self.case.folders:
                state['folders'].setdefault(f, {'migrated': True})['rc'] = 0
            plugin_state.write_state(self.case.k.root, state)
        return self.code, list(self.lines)


class FakePopen(object):
    """run_child's child: communicate(timeout) answers `out` or, hung, raises TimeoutExpired
    until kill() is called."""
    pid = 4242

    def __init__(self, argv, kw, out=b'', code=0, hang=False):
        self.argv, self.kw, self.out, self.returncode, self.hang = argv, kw, out, code, hang
        self.waits, self.killed = [], False

    def communicate(self, timeout=None):
        self.waits.append(timeout)
        if self.hang and not self.killed:
            raise subprocess.TimeoutExpired(self.argv, timeout)
        return self.out, None

    def kill(self):
        self.killed = True


class JobCase(unittest.TestCase):
    def setUp(self):
        self.k = Kit(self)
        self.auto = True
        self.folders = self.k.folders('.claude')
        self.t = [1000.0]
        self.child = Child(self)

    def settings(self, kit):
        return plugin_layout.Settings(self.k.root, self.auto, 'origin')

    def job(self, **kw):
        kw.setdefault('kit', self.k.kit)
        kw.setdefault('load', self.settings)
        kw.setdefault('folders', lambda: self.folders)
        kw.setdefault('child', self.child)
        kw.setdefault('clock', lambda: self.t[0])
        return plugin_job.PluginJob(**kw)

    def main(self):
        return self.k.repo.git('rev-parse', 'refs/remotes/origin/main')

    def note(self, got):
        return got.note if isinstance(got, jobs.Skip) else got

    def move_main(self):
        """A new commit on the kit's origin/main tracking ref (a landing fetched): its sha."""
        sha = self.k.repo.git('commit-tree', 'HEAD^{tree}', '-p', 'HEAD', '-m', 'landed')
        self.k.repo.git('update-ref', 'refs/remotes/origin/main', sha)
        return sha

    def told(self, why, main=None):
        return (plugin_job.BEHIND, '%s:%s' % ((main or self.main())[:12], why))


class BehindPushTest(JobCase):
    """The skips the owner must act on push the PM `behind`, once for each kit main
    (kitlib/hubsite/plugin_job.py docstring; docs/hub-push.md "Timed jobs")."""

    def test_no_export_pushes_behind_once_for_each_main(self):
        events = []
        job = self.job()
        for _ in range(2):
            self.assertIn('no export yet', self.note(job(ctx(events=events))))
        self.assertEqual(events, [self.told('no-export')])
        new = self.move_main()
        job(ctx(events=events))
        self.assertEqual(events[1:], [self.told('no-export', new)])
        self.assertEqual(self.child.calls, [])

    def test_a_folder_not_migrated_pushes_behind_unmigrated_once_for_each_main(self):
        os.makedirs(self.k.root, exist_ok=True)
        plugin_state.write_state(self.k.root, {'folders': {}})
        events = []
        job = self.job()
        for _ in range(2):
            self.assertIn('not migrated in', self.note(job(ctx(events=events))))
        self.assertEqual(events, [self.told('unmigrated')])
        new = self.move_main()
        job(ctx(events=events))
        self.assertEqual(events[1:], [self.told('unmigrated', new)])

    def test_auto_refresh_off_and_behind_main_pushes_behind_once_for_each_main(self):
        self.auto = False
        self.k.seed_migrated(self.folders)
        events = []
        job = self.job()
        for _ in range(2):
            self.assertIn('plugin.auto_refresh is off', self.note(job(ctx(events=events))))
        self.assertEqual(events, [self.told('behind')])
        new = self.move_main()
        job(ctx(events=events))
        self.assertEqual(events[1:], [self.told('behind', new)])
        self.assertEqual(self.child.calls, [])

    def test_auto_refresh_off_at_main_pushes_none_for_a_folder_not_refreshed_or_a_copy_deferred(self):
        self.k.seed_migrated(self.folders)
        self.job()(ctx())                       # refreshed: the export at main, every rc 0
        self.auto = False
        state = plugin_state.read_state(self.k.root)
        state['folders'][self.folders[0]]['rc'] = 1
        plugin_state.write_state(self.k.root, state)
        events = []
        got = self.job()(ctx(events=events))
        self.assertIn('not refreshed in', self.note(got))
        state['folders'][self.folders[0]]['rc'] = 0
        plugin_state.write_state(self.k.root, state)
        plugin_state.set_pending(self.k.root, 'codex', 'codex: deferred: Codex is running')
        got = self.job()(ctx(events=events))
        self.assertIn('the codex copy deferred', self.note(got))
        self.assertEqual(events, [], 'at main: nothing the owner must refresh to')

    def test_behind_pushes_none_on_the_refresh_path(self):
        self.k.seed_migrated(self.folders)
        events = []
        job = self.job()
        self.assertEqual(job(ctx(events=events)), 'refreshed to %s' % self.main()[:12])
        self.move_main()
        self.child.code, self.child.lines = 1, ['FAILED: x']
        with self.assertRaises(plugin_job.RefreshFailed):
            job(ctx(events=events))
        self.assertEqual(events, [], 'auto_refresh on: the job refreshes, the PM is not told')


class PluginJobTest(JobCase):
    def test_behind_starts_the_refresh_child_once_then_current_does_nothing(self):
        self.k.seed_migrated(self.folders)
        job = self.job()
        self.assertEqual(job(ctx()), 'refreshed to %s' % self.main()[:12])
        self.assertEqual(self.child.calls, [(
            [sys.executable, os.path.join(self.k.kit, 'tools', 'plugin_refresh.py'), '--apply'],
            self.k.kit)])
        with open(os.path.join(self.k.root, plugin_refresh.LOGFILE), encoding='utf-8') as fh:
            log = fh.read()
        self.assertIn('hub plugin job ', log)
        self.assertIn('done\nhub plugin job: exit 0\n', log)
        self.assertEqual(job(ctx()), 'current at %s' % self.main()[:12])
        self.assertEqual(len(self.child.calls), 1, 'current: no second refresh')

    def test_the_default_folders_are_those_of_the_home_the_child_gets(self):
        with mock.patch.object(plugin_job.plugin_state, 'account_folders',
                               return_value=['F']) as af:
            self.assertEqual(plugin_job.PluginJob(home='H').folders(), ['F'])
        af.assert_called_once_with('H')
        self.assertEqual(plugin_state.account_folders(self.k.home),
                         accounts.account_folders(self.k.home))

    def test_a_home_is_passed_to_the_child(self):
        self.assertEqual(plugin_job.refresh_argv('K', 'H')[1:],
                         [os.path.join('K', 'tools', 'plugin_refresh.py'), '--apply', '--home', 'H'])

    def test_a_child_that_exits_non_zero_raises_with_its_failure_line(self):
        self.k.seed_migrated(self.folders)
        self.child.code, self.child.lines = 1, ['export ok', 'FAILED: claude plugin update in x', 'tail']
        with self.assertRaises(plugin_job.RefreshFailed) as got:
            self.job()(ctx())
        self.assertIn('exited 1: FAILED: claude plugin update in x', str(got.exception))
        with open(os.path.join(self.k.root, plugin_refresh.LOGFILE), encoding='utf-8') as fh:
            self.assertIn('hub plugin job: exit 1', fh.read())

    def test_the_default_child_is_windowless_utf8_and_returns_its_code_and_lines(self):
        procs = []

        def start(argv, **kw):
            procs.append(FakePopen(argv, kw, out=b'one\r\ntwo\n', code=3))
            return procs[0]
        kills = []
        with mock.patch('kitlib.platform.NAME', 'windows'):
            got = plugin_job.run_child(['a', 'b'], 'C', start=start,
                                       kill_tree=lambda *a: kills.append(a), ctime=lambda pid: 77)
        self.assertEqual(got, (3, ['one', 'two']))
        kw = procs[0].kw
        self.assertEqual((procs[0].argv, kw['cwd'], kw['stdin'], kw['creationflags']),
                         (['a', 'b'], 'C', subprocess.DEVNULL, 0x08000000))
        self.assertEqual(kw['env']['PYTHONIOENCODING'], 'utf-8')
        self.assertEqual(procs[0].waits[0], plugin_job.REFRESH_TIMEOUT_S)
        self.assertEqual(kills, [])

    def test_linux_passes_no_creationflags_which_POSIX_Popen_refuses(self):
        procs = []

        def start(argv, **kw):
            procs.append(FakePopen(argv, kw, out=b'', code=0))
            return procs[0]
        with mock.patch('kitlib.platform.NAME', 'linux'):
            plugin_job.run_child(['a'], 'C', start=start, kill_tree=lambda *a: None,
                                 ctime=lambda pid: 77)
        self.assertEqual(procs[0].kw['creationflags'], 0)

    def test_a_child_past_its_timeout_is_ended_with_every_process_it_started(self):
        proc = FakePopen(['a'], {}, hang=True)
        kills = []
        with self.assertRaises(plugin_job.RefreshFailed) as got:
            plugin_job.run_child(['a'], 'C', start=lambda argv, **kw: proc,
                                 kill_tree=lambda *a: kills.append(a), ctime=lambda pid: 77)
        self.assertEqual(kills, [(proc.pid, 77)], 'the whole tree, by pid and creation time')
        self.assertTrue(proc.killed)
        self.assertIn('did not end', str(got.exception))

    def test_a_child_that_cannot_start_is_a_failure(self):
        self.k.seed_migrated(self.folders)

        def missing(argv, cwd):
            raise FileNotFoundError(2, 'no python')
        with self.assertRaises(plugin_job.RefreshFailed):
            self.job(child=missing)(ctx())

    def test_a_refresh_held_by_another_process_is_not_started(self):
        self.k.seed_migrated(self.folders)
        held = plugin_refresh.RefreshLock(self.k.root)
        held.acquire()
        self.addCleanup(held.release)
        got = self.job()(ctx())
        self.assertIsInstance(got, jobs.Skip)
        self.assertIn(plugin_job.BUSY, got.note)
        self.assertEqual(self.child.calls, [], 'refresh.pid is live: nothing is started')

    def test_a_child_that_finds_the_lock_taken_is_a_skip_not_a_failure(self):
        self.k.seed_migrated(self.folders)
        self.child.code, self.child.lines = 1, ['REFUSED: %s (pid 9)' % plugin_job.BUSY]
        got = self.job()(ctx())
        self.assertIsInstance(got, jobs.Skip)
        self.assertIn(plugin_job.BUSY, got.note)

    def test_two_runs_at_once_start_one_child(self):
        self.k.seed_migrated(self.folders)
        self.child.release.clear()
        js = jobs.Jobs([jobs.Job(plugin_job.NAME, 300, self.job())])
        first = threading.Thread(target=js.run_now, args=(plugin_job.NAME,))
        first.start()
        self.assertTrue(self.child.entered.wait(WAIT_S))
        self.assertIs(js.run_now(plugin_job.NAME), False, 'the second run is refused')
        self.child.release.set()
        first.join(WAIT_S)
        self.assertEqual(len(self.child.calls), 1)

    def test_auto_refresh_off_is_a_skip_and_starts_nothing(self):
        self.auto = False
        self.k.seed_migrated(self.folders)
        got = self.job()(ctx())
        self.assertIsInstance(got, jobs.Skip)
        self.assertIn('behind kit main %s' % self.main()[:12], got.note)
        self.assertIn('plugin.auto_refresh is off', got.note)
        self.assertEqual(self.child.calls, [])

    def test_no_export_or_a_folder_not_migrated_starts_nothing(self):
        job = self.job()
        self.assertIn('no export yet', self.note(job(ctx())))
        os.makedirs(self.k.root, exist_ok=True)
        plugin_state.write_state(self.k.root, {'folders': {}})
        self.assertIn('not migrated in %s' % self.folders[0], self.note(job(ctx())))
        self.assertEqual(self.child.calls, [])

    def test_a_folder_whose_last_refresh_failed_is_refreshed_again(self):
        self.k.seed_migrated(self.folders)
        self.job()(ctx())
        state = plugin_state.read_state(self.k.root)
        state['folders'][self.folders[0]]['rc'] = 1
        plugin_state.write_state(self.k.root, state)
        self.assertEqual(self.job()(ctx()), 'refreshed to %s' % self.main()[:12])
        self.assertEqual(len(self.child.calls), 2)

    def test_a_failed_refresh_waits_retry_s_and_the_runs_between_are_skips(self):
        self.k.seed_migrated(self.folders)
        self.child.code, self.child.lines = 1, ['FAILED: x']
        job = self.job()
        with self.assertRaises(plugin_job.RefreshFailed):
            job(ctx())
        self.t[0] += 60
        got = job(ctx())
        self.assertIsInstance(got, jobs.Skip)
        self.assertIn('waiting to retry', got.note)
        self.assertIn('FAILED: x', got.note)
        self.assertEqual(len(self.child.calls), 1, 'not tried again before RETRY_S')
        self.t[0] += plugin_job.RETRY_S
        with self.assertRaises(plugin_job.RefreshFailed):
            job(ctx())
        self.assertEqual(len(self.child.calls), 2)

    def test_a_pending_codex_copy_is_retried_retry_s_apart_until_it_clears(self):
        # the export current, every folder rc 0, but the Codex copy deferred
        self.k.seed_migrated(self.folders)
        job = self.job()
        job(ctx())
        line = 'codex: deferred: Codex is running; close it and run plugin_refresh.py --apply'
        plugin_state.set_pending(self.k.root, 'codex', line)
        self.assertEqual(job(ctx()), 'refreshed to %s; %s' % (self.main()[:12], line))
        self.assertEqual(len(self.child.calls), 2, 'a pending copy is refreshed again')
        self.t[0] += plugin_job.EVERY_S
        got = job(ctx())
        self.assertIsInstance(got, jobs.Skip)
        self.assertIn('the codex copy deferred; waiting to retry', got.note)
        self.assertEqual(len(self.child.calls), 2, 'not tried again before RETRY_S')
        self.t[0] += plugin_job.RETRY_S
        real = self.child.__call__

        def closed(argv, cwd):                  # Codex closed: the retry's add goes through
            plugin_state.clear_pending(self.k.root, 'codex')
            return real(argv, cwd)
        job.child = closed
        self.assertEqual(job(ctx()), 'refreshed to %s' % self.main()[:12])
        self.assertEqual(len(self.child.calls), 3)
        self.assertEqual(job(ctx()), 'current at %s' % self.main()[:12])

    def test_the_session_start_names_a_pending_codex_copy_until_it_clears(self):
        self.k.seed_migrated(self.folders)
        self.job()(ctx())
        self.assertEqual(plugin_state.current_lines(self.k.kit, self.settings(None), folders=self.folders, managed=None), [])
        line = 'codex: deferred: Codex is running; close it and run plugin_refresh.py --apply'
        plugin_state.set_pending(self.k.root, 'codex', line)
        self.assertEqual(plugin_state.current_lines(self.k.kit, self.settings(None), folders=self.folders, managed=None),
                         ['pm-kit plugin codex copy not refreshed: %s' % line])
        plugin_state.clear_pending(self.k.root, 'codex')
        self.assertEqual(plugin_state.current_lines(self.k.kit, self.settings(None), folders=self.folders, managed=None), [])

    def test_one_real_failure_counts_once_in_the_runner(self):
        self.k.seed_migrated(self.folders)
        self.child.code, self.child.lines = 1, ['FAILED: x']
        events = []
        js = jobs.Jobs([jobs.Job(plugin_job.NAME, 300, self.job())],
                       notify=lambda n, w: events.append((n, w)))
        for _ in range(5):              # one attempt, then four runs inside the retry wait
            js.run_now(plugin_job.NAME)
            self.t[0] += plugin_job.EVERY_S
        st = js.status()[plugin_job.NAME]
        self.assertEqual((st['failures'], len(self.child.calls), events), (1, 1, []))
        self.assertIn('RefreshFailed', st['error'])
        for _ in range(jobs.FAIL_PUSH - 1):     # two more real attempts, RETRY_S apart
            self.t[0] += plugin_job.RETRY_S
            js.run_now(plugin_job.NAME)
        self.assertEqual(js.status()[plugin_job.NAME]['failures'], jobs.FAIL_PUSH)
        self.assertEqual(events, [(plugin_job.NAME, 'failing')])
        self.child.code = 0
        self.t[0] += plugin_job.RETRY_S
        js.run_now(plugin_job.NAME)
        self.assertEqual(events, [(plugin_job.NAME, 'failing'), (plugin_job.NAME, 'recovered')])

    def test_no_tracking_ref_is_a_skip(self):
        self.k.repo.git('update-ref', '-d', 'refs/remotes/origin/main')
        got = self.job()(ctx())
        self.assertIsInstance(got, jobs.Skip)
        self.assertIn('tracking ref', got.note)

    def test_a_stop_before_the_refresh_starts_none(self):
        self.k.seed_migrated(self.folders)
        self.job()(ctx(stop=True))
        self.assertEqual(self.child.calls, [])


if __name__ == '__main__':
    unittest.main()
