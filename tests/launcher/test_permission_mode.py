"""--permission-mode on the verbs that start a session (start, deep, consultant, topic, handoff,
pm-relaunch, pm-crossover, after-reboot): the flag reaches window.session_command; with none given every command is as before."""
import io
import os
import sys
import unittest
from contextlib import redirect_stderr

from kitlib.git import repo as gitrepo

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), 'tools'))
import launch                                                       # noqa: E402
from kitlib.launcher import handoff, relaunch, start, window        # noqa: E402
from tests.launcher.fakes import FakeHost, Home, session          # noqa: E402
from tests.launcher.test_start import Base                         # noqa: E402

FLAG = ' --permission-mode '
VERBS = (['start', '--repo', 'alpha', '--account', '1'], ['pm-relaunch', '--name', 'x'],
         ['pm-crossover', '--account', '1'], ['after-reboot'],
         ['deep', '--repo-path', 'r', '--brief-text', 'b', '--requester', 'q', '--slug', 's'],
         ['consultant', '--repo-path', 'r'],
         ['topic', '--worktree', 'w', '--brief', 'b', '--slug', 's'],
         ['handoff', '--session', 'alpha', '--account', '2'])


class Parsing(unittest.TestCase):
    def test_every_mode_window_knows_is_accepted_and_none_is_none(self):
        for verb in VERBS:
            self.assertIsNone(launch.parser().parse_args(verb).permission_mode, verb)
            for mode in window.PERMISSION_MODES:
                self.assertEqual(launch.parser().parse_args(verb + ['--permission-mode', mode]).permission_mode,
                                 mode)

    def test_bypass_and_dontask_exit_2(self):
        for mode in ('bypassPermissions', 'dontAsk'):
            for verb in VERBS:
                with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as e:
                    launch.parser().parse_args(verb + ['--permission-mode', mode])
                self.assertEqual(e.exception.code, 2)


class Commands(unittest.TestCase):
    def test_fork_and_crossover_commands_are_unchanged_without_the_flag(self):
        fork = relaunch.fork_command('sid', 'n', 'c.json')
        self.assertNotIn(FLAG, fork)
        self.assertEqual(fork, relaunch.fork_command('sid', 'n', 'c.json', None))
        cross = relaunch.crossover_command('n', 'p')
        self.assertNotIn(FLAG, cross)
        self.assertEqual(cross, window.session_command('n', 'p', 'pm-kit:pm').replace(FLAG + 'auto', '', 1))

    def test_an_explicit_mode_is_in_both(self):
        self.assertIn(FLAG + 'acceptEdits', relaunch.fork_command('sid', 'n', 'c.json', 'acceptEdits'))
        cross = relaunch.crossover_command('n', 'p', 'auto')
        self.assertEqual(cross.count(FLAG), 1)
        self.assertIn(FLAG + 'auto', cross)


class Start(unittest.TestCase):
    def setUp(self):
        self.h = Home(self, accounts=('1',), repos=('alpha',))

    def what_if(self, **kw):
        out = start.start(self.h.settings, FakeHost(self.h.root), repo='alpha', account='1', whatif=True, **kw)
        return '\n'.join(out)

    def test_no_flag_is_byte_identical_and_a_flag_replaces_the_default(self):
        base = self.what_if()
        self.assertEqual(base, self.what_if(permission_mode=None))
        self.assertIn(FLAG + 'auto', base)
        got = self.what_if(permission_mode='default')
        self.assertEqual(got, base.replace(FLAG + 'auto', FLAG + 'default', 1))


class Reboot(unittest.TestCase):
    def test_the_mode_reaches_every_window_after_reboot_opens_and_none_changes_nothing(self):
        from kitlib.launcher import reboot
        from tests.launcher import test_reboot as T
        texts = {}
        for mode in (None, 'acceptEdits'):
            h = Home(self)
            h.write_state('sessions.json', {'alpha': dict(T.ALPHA), 'beta': dict(T.BETA)})
            h.write_state('coordinators.json', dict(T.PM, pm_folder=h.root))
            host = FakeHost(h.root)
            reboot.run(h.settings, host, permission_mode=mode)
            texts[mode] = host.opened
            self.assertTrue(host.opened)
        self.assertEqual((len(texts[None]), len(texts['acceptEdits'])), (3, 3))     # alpha, beta, the PM resume
        self.assertFalse([t for t in texts[None] if FLAG + 'acceptEdits' in t])
        self.assertTrue(all(FLAG + 'acceptEdits' in t for t in texts['acceptEdits']))


class OtherVerbs(Base):
    """deep, consultant, topic and handoff pass the mode on; with none the command is as before."""
    def check(self, make):
        base = make()
        self.assertEqual(base, make(permission_mode=None))
        self.assertIn(FLAG + 'auto', base)
        got = make(permission_mode='acceptEdits')
        self.assertEqual(got, base.replace(FLAG + 'auto', FLAG + 'acceptEdits', 1))

    def test_deep(self):
        brief = os.path.join(self.repo, 'brief.md')
        with open(brief, 'w', encoding='utf-8') as f:
            f.write('brief')
        self.check(lambda **kw: '\n'.join(start.deep(
            self.h.settings, self.host, repo_path=self.repo, requester='alpha-coord-a1', slug='net-race', brief=brief,
            account='1', brief_dir=os.path.join(self.h.root, 'briefs'), plan=True, **kw)))

    def test_consultant(self):
        self.check(lambda **kw: '\n'.join(start.consultant(
            self.h.settings, self.host, self.repo, account='1', plan=True, **kw)))

    def test_topic(self):
        self.host.where = gitrepo.LINKED
        brief = os.path.join(self.repo, 'topic-brief.md')
        with open(brief, 'w', encoding='utf-8') as f:
            f.write('brief')
        self.check(lambda **kw: '\n'.join(start.topic(
            self.h.settings, self.host, self.repo, 'brain-ui', brief, account='1', plan=True, **kw)))

    def test_handoff(self):
        self.h.write_state('sessions.json', {'alpha': {'repo': 'alpha', 'agent': True, 'stem': 'alpha-lead',
                                                       'state': 's.md', 'account': 1, 'name': 'alpha-lead-a1'}})
        self.host.procs = [session(10, 'alpha-lead-a1', agent='coordinator')]
        self.check(lambda **kw: '\n'.join(handoff.handoff(
            self.h.settings, self.host, 'alpha', '2', whatif=True, **kw)))


if __name__ == '__main__':
    unittest.main()
