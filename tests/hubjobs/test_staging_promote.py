"""kitlib.hubjobs.staging_promote: the hub's trigger of a promotion run, with the look and the start
stood in; and its real look on a repository whose staging setting is absent."""
import threading
import unittest
from unittest import mock

from kitlib.hubjobs import staging_promote as sp
from kitlib.hubsite import jobs


class Attempt(object):
    def __init__(self, phase, sha):
        self.phase, self.sha = phase, sha


class Ctx(object):
    def __init__(self):
        self.stop = threading.Event()
        self.events = []

    def event(self, word, detail=None):
        self.events.append((word, detail))


class Job(unittest.TestCase):
    def setUp(self):
        self.looks = {}             # root -> (verdict, note, [attempts to announce])
        self.started = []
        self.start_why = None
        self.ctx = Ctx()

    def look(self, root, announce):
        verdict, note, owed = self.looks[root]
        for a in owed:
            announce(a)
        return verdict, note

    def start(self, root):
        if self.start_why:
            return None, self.start_why
        self.started.append(root)
        return 4242, None

    def run_job(self, roots):
        return sp.PromoteJob(lambda: roots, look=self.look, start=self.start)(self.ctx)

    def test_staging_off_everywhere_does_nothing(self):
        self.looks = {'/a': ('off', None, []), '/b': ('off', None, [])}
        out = self.run_job({'a': '/a', 'b': '/b'})
        self.assertIsInstance(out, jobs.Skip)
        self.assertEqual(out.note, 'staging is off in every repository')
        self.assertEqual((self.started, self.ctx.events), ([], []))

    def test_a_busy_or_idle_repository_is_a_skip(self):
        self.looks = {'/a': ('busy', 'a promotion runs (pid 9)', []),
                      '/b': ('idle', '2 untested', [])}
        out = self.run_job({'a': '/a', 'b': '/b'})
        self.assertIsInstance(out, jobs.Skip)
        self.assertEqual(out.note, 'a: a promotion runs (pid 9); b: 2 untested')
        self.assertEqual(self.started, [])

    def test_an_owed_run_is_started_detached(self):
        self.looks = {'/a': ('start', '4 untested at abc', []), '/b': ('idle', '1 untested', [])}
        out = self.run_job({'a': '/a', 'b': '/b'})
        self.assertEqual(out, 'started a (4 untested at abc, pid 4242)')
        self.assertEqual(self.started, ['/a'])

    def test_each_end_is_announced_as_its_event(self):
        self.looks = {'/a': ('idle', 'held', [Attempt('refused', 'f' * 40),
                                               Attempt('red', None)])}
        self.run_job({'a': '/a'})
        self.assertEqual(self.ctx.events, [('promote-refused', 'a:' + 'f' * 10),
                                           ('promote-red', 'a:?')])
        for end in ('red', 'refused', 'diverged', 'failed', 'error'):
            self.assertIn(end, sp.EVENTS)

    def test_an_error_with_nothing_started_fails(self):
        self.looks = {'/a': ('error', 'git log failed', [])}
        with self.assertRaises(sp.PromoteFailed) as cm:
            self.run_job({'a': '/a'})
        self.assertIn('a: git log failed', str(cm.exception))

    def test_a_start_that_failed_fails(self):
        self.looks = {'/a': ('start', 'due', [])}
        self.start_why = 'no python'
        with self.assertRaises(sp.PromoteFailed) as cm:
            self.run_job({'a': '/a'})
        self.assertIn('the run did not start: no python', str(cm.exception))

    def test_an_error_beside_a_start_is_a_note(self):
        self.looks = {'/a': ('start', 'due', []), '/b': ('error', 'bad config', [])}
        out = self.run_job({'a': '/a', 'b': '/b'})
        self.assertEqual(out, 'started a (due, pid 4242); b: bad config')

    def test_a_stop_ends_the_pass(self):
        self.ctx.stop.set()
        self.looks = {}
        self.assertIsInstance(self.run_job({'a': '/a'}), jobs.Skip)

    def test_no_repository_is_no_job(self):
        class S(object):
            repos = {}
        with mock.patch('kitlib.launcher.settings.load', return_value=S()):
            self.assertIsNone(sp.make(object()))

    def test_a_repository_is_one_job(self):
        class S(object):
            repos = {'kit': '/kit'}
        with mock.patch('kitlib.launcher.settings.load', return_value=S()):
            job = sp.make(object())
        self.assertEqual((job.name, job.every_s), (sp.NAME, sp.EVERY_S))

    def test_a_config_that_cannot_be_read_is_an_error_verdict(self):
        with mock.patch('kitlib.config.load', side_effect=ValueError('broken')):
            verdict, note = sp._look('/nowhere', lambda a: None)
        self.assertEqual(verdict, 'error')
        self.assertIn('broken', note)

    def test_the_start_runs_the_promote_tool_detached(self):
        made = []

        def create(argv, cwd, env):
            made.append((argv, cwd))
            return 77, None
        with mock.patch('kitlib.lock.detach.missing', return_value=None), \
                mock.patch('kitlib.lock.detach.create', side_effect=create):
            self.assertEqual(sp.start_run('/repo'), (77, None))
        argv, cwd = made[0]
        self.assertEqual(cwd, '/repo')
        self.assertTrue(any(str(a).replace('\\', '/').endswith('tools/promote.py') for a in argv),
                        argv)
        self.assertEqual(argv[-2:], ['-C', '/repo'])


if __name__ == '__main__':
    unittest.main()
