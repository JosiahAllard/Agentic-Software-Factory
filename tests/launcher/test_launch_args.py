"""launch.py handoff --state: an existing file only (arguments and exit codes)."""
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), 'tools'))
import launch                                                       # noqa: E402
from tests.launcher.fakes import FakeHost, Home                     # noqa: E402


class HandoffState(unittest.TestCase):
    def test_a_missing_file_exits_2(self):
        with tempfile.TemporaryDirectory() as d:
            missing = os.path.join(d, 'no-such-state.md')
            err = io.StringIO()
            with redirect_stderr(err), self.assertRaises(SystemExit) as e:
                launch.parser().parse_args(['handoff', '--session', 'alpha', '--account', '2', '--state', missing])
            self.assertEqual(e.exception.code, 2)
            self.assertIn('no such file: ' + missing, err.getvalue())

    def test_a_relative_path_is_made_absolute(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, 'state-r2.md'), 'w', encoding='utf-8') as f:
                f.write('note\n')
            here = os.getcwd()
            os.chdir(d)
            try:
                expected = os.path.join(os.getcwd(), 'state-r2.md')
                a = launch.parser().parse_args(['handoff', '--session', 'alpha', '--account', '2',
                                                '--state', 'state-r2.md'])
            finally:
                os.chdir(here)
            self.assertTrue(os.path.isabs(a.state))
            self.assertEqual(os.path.normcase(a.state), os.path.normcase(expected))

    def test_an_existing_file_is_taken_and_no_state_is_none(self):
        with tempfile.TemporaryDirectory() as d:
            note = os.path.join(d, 'state-r2.md')
            with open(note, 'w', encoding='utf-8') as f:
                f.write('note\n')
            a = launch.parser().parse_args(['handoff', '--session', 'alpha', '--account', '2', '--state', note])
            self.assertEqual(a.state, note)
        a = launch.parser().parse_args(['handoff', '--session', 'alpha', '--account', '2'])
        self.assertIsNone(a.state)


class TopicArgs(unittest.TestCase):
    """launch.py topic: --worktree, --brief and --slug are required; run passes them through."""

    def test_each_required_argument_missing_exits_2(self):
        full = ['topic', '--worktree', 'w', '--brief', 'b.md', '--slug', 'ab']
        for drop in ('--worktree', '--brief', '--slug'):
            with self.subTest(drop=drop):
                i = full.index(drop)
                with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as e:
                    launch.parser().parse_args(full[:i] + full[i + 2:])
                self.assertEqual(e.exception.code, 2)
        a = launch.parser().parse_args(full + ['--plan'])
        self.assertEqual((a.worktree, a.brief, a.slug, a.account, a.plan), ('w', 'b.md', 'ab', 'auto', True))

    def test_run_starts_a_topic_session(self):
        from kitlib.git import repo as gitrepo
        h = Home(self, accounts=('1',))
        host = FakeHost(h.root)
        host.where = gitrepo.LINKED
        wt = h.repos['alpha']
        with open(os.path.join(wt, 'b.md'), 'w', encoding='utf-8') as f:
            f.write('brief\n')
        a = launch.parser().parse_args(['topic', '--worktree', wt, '--brief', 'b.md', '--slug', 'ab',
                                        '--account', '1'])
        launch.run(a, h.settings, host)
        text, = host.opened
        self.assertIn("$env:PM_KIT_ROLE = 'topic'", text)
        self.assertIn("-n 'topic-ab'", text)


class HandoffStateWiring(unittest.TestCase):
    def test_run_passes_the_state_file_to_the_successors_first_message(self):
        h = Home(self, accounts=('1', '2'))
        h.write_state('sessions.json', {'alpha': {'repo': 'alpha', 'agent': True, 'stem': 'alpha-coord',
                                                   'state': 'entry-state.md', 'account': 1,
                                                   'name': 'alpha-coord-a1'}})
        h.write_state('coordinators.json', {'alpha': 1})
        host = FakeHost(h.root)
        note = os.path.join(h.root, 'state-r2.md')
        with open(note, 'w', encoding='utf-8') as f:
            f.write('note\n')
        a = launch.parser().parse_args(['handoff', '--session', 'alpha', '--account', '2', '--state', note])
        launch.run(a, h.settings, host)
        text, = host.opened
        self.assertIn('state-r2.md', text)
        self.assertNotIn('entry-state.md', text)


if __name__ == '__main__':
    unittest.main()
