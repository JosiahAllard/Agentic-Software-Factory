"""kitlib.launcher.unlisted: a launched consultant or deep-worker read back from its window, round
trip from start.py's own window script, and every reason one is never crossed."""
import datetime
import os
import unittest
from unittest import mock

from kitlib.launcher import start, unlisted, window
from tests.launcher.fakes import EXE, FakeHost, Home, NOW

DAY = datetime.timedelta(days=1)


def rows(text, pid, name, parent_name='powershell.exe'):
    """The window's powershell.exe and its claude.exe child, for the window `text` start opened."""
    parent = {'Name': parent_name, 'ProcessId': pid + 1, 'ParentProcessId': 1,
              'CommandLine': 'powershell.exe -NoExit -EncodedCommand %s' % window.encode(text)}
    child = {'Name': 'claude.exe', 'ProcessId': pid, 'ParentProcessId': pid + 1, 'ExecutablePath': EXE,
             'Created': '2026-10-10T11:00:00', 'CommandLine': "claude --remote-control '%s' -n '%s' 'x'" % (name, name)}
    return [parent, child]


class Base(unittest.TestCase):
    def setUp(self):
        self.h = Home(self, accounts=('1', '2'), repos=('alpha',))
        for n in ('1', '2'):
            self.h.usage(n, five=(10, NOW + DAY / 8), week=(10, NOW + 2 * DAY))
        self.repo = self.h.repos['alpha']
        os.makedirs(os.path.join(self.repo, 'docs'))
        self.brief = os.path.join(self.repo, 'brief.md')
        with open(self.brief, 'w', encoding='utf-8') as f:
            f.write('brief')
        self.temp = os.path.join(self.h.root, 'agent-temp')
        self.host = FakeHost(self.h.root, env={'AGENT_TEMP': self.temp}, ignored=True)

    def opened(self):
        text, = self.host.opened
        self.host.opened = []
        return text

    def deep(self, **kw):
        args = dict(repo_path=self.repo, requester="o'brien-coord-a1", slug='net-race', brief=self.brief,
                    account='1', plan=False)
        args.update(kw)
        start.deep(self.h.settings, self.host, **args)
        return self.opened()

    def consultant(self, **kw):
        args = dict(repo_path=self.repo, account='2', plan=False)
        args.update(kw)
        start.consultant(self.h.settings, self.host, **args)
        return self.opened()

    def classify(self, procs, **kw):
        """classify's sessions, and its never-crossed sessions' reasons alone (accounts: `accounts`)."""
        found, never = unlisted.classify(procs, self.h.settings, **kw)
        self.accounts = {n: acct for n, (_, acct) in never.items()}
        return found, {n: why for n, (why, _) in never.items()}


class RoundTrip(Base):
    def test_a_deep_worker_comes_back_with_its_launch(self):
        found, never = self.classify(rows(self.deep(), 700, 'deep-net-race'))
        self.assertEqual(never, {})
        s, = found['deep-net-race']
        self.assertEqual((s.kind, s.key, s.name, s.pid, s.account), (unlisted.DEEP, 'deep-net-race', 'deep-net-race',
                                                                     700, '1'))
        self.assertEqual((s.requester, s.brief, s.slug, s.repo_path, s.home),
                         ("o'brien-coord-a1", self.brief, 'net-race', self.repo, False))
        self.assertEqual((s.model, s.effort, s.autocompact, s.agent_temp, s.predecessor),
                         ('opus[1m]', 'xhigh', 500000, self.temp, None))

    def test_a_consultant_comes_back_with_its_notes_file(self):
        found, never = self.classify(rows(self.consultant(), 710, 'consultant-alpha'))
        self.assertEqual(never, {})
        s, = found['consultant-alpha']
        self.assertEqual((s.kind, s.key, s.account, s.slug, s.repo_path),
                         (unlisted.CONSULTANT, 'consultant-alpha', '2', 'alpha', self.repo))
        self.assertEqual(s.notes, start.consultant_notes_path(self.host, 'alpha'))

    def test_a_successor_keys_on_its_name_without_the_account(self):
        state = os.path.join(self.temp, 'state.md')
        checkin = os.path.join(self.temp, 'checkin')
        text = self.deep(account='2', predecessor='deep-net-race', state=state, checkin=checkin)
        found, never = self.classify(rows(text, 720, 'deep-net-race-a2'))
        s, = found['deep-net-race']
        self.assertEqual((s.name, s.account, s.predecessor, s.slug, s.brief),
                         ('deep-net-race-a2', '2', 'deep-net-race', 'net-race', self.brief))

    def test_the_default_folder_is_the_account_that_has_it(self):
        text = self.deep().replace("$env:CLAUDE_CONFIG_DIR = %s" % window.q(self.h.accounts['1'][0]),
                                   'Remove-Item Env:CLAUDE_CONFIG_DIR -ErrorAction SilentlyContinue')
        self.assertIn('; Remove-Item Env:CLAUDE_CONFIG_DIR ', text)
        with mock.patch.object(self.h.settings, 'is_default', lambda d: d == self.h.accounts['1'][0]):
            found, never = self.classify(rows(text, 730, 'deep-net-race'))
        self.assertEqual(found['deep-net-race'][0].account, '1')

    def test_a_check_in_note_and_a_quote_in_the_brief_path_still_read(self):
        brief = os.path.join(self.repo, "it's.md")
        with open(brief, 'w', encoding='utf-8') as f:
            f.write('b')
        text = self.deep(brief=brief, checkin=os.path.join(self.temp, 'c'))
        found, _ = self.classify(rows(text, 740, 'deep-net-race'))
        self.assertEqual(found['deep-net-race'][0].brief, brief)


class Never(Base):
    def test_no_window(self):
        procs = rows(self.deep(), 800, 'deep-net-race')
        procs[0]['Name'] = 'cmd.exe'
        self.assertEqual(self.classify(procs), ({}, {'deep-net-race': unlisted.NO_WINDOW}))
        self.assertEqual(self.classify(procs[1:]), ({}, {'deep-net-race': unlisted.NO_WINDOW}))
        self.assertEqual(self.accounts, {'deep-net-race': None})

    def test_another_role(self):
        start.start(self.h.settings, self.host, repo_path=self.repo, account='1', name='alpha-coord-a1',
                    agent='pm-kit:coordinator', prompt='hi')
        self.assertEqual(self.classify(rows(self.opened(), 810, 'alpha-coord-a1')),
                         ({}, {'alpha-coord-a1': unlisted.OTHER_ROLE}))
        self.assertEqual(self.accounts, {'alpha-coord-a1': '1'})

    def test_a_role_and_agent_that_disagree(self):
        text = self.deep().replace("$env:PM_KIT_ROLE = 'deep-worker'", "$env:PM_KIT_ROLE = 'consultant'")
        self.assertEqual(self.classify(rows(text, 820, 'deep-net-race')),
                         ({}, {'deep-net-race': unlisted.ROLE_MISMATCH}))

    def test_a_first_message_start_did_not_write(self):
        text = self.deep().replace('a deep-worker session started for', 'a session for')
        self.assertEqual(self.classify(rows(text, 830, 'deep-net-race')),
                         ({}, {'deep-net-race': unlisted.UNREADABLE}))

    def test_a_config_folder_of_no_account(self):
        text = self.deep().replace(window.q(self.h.accounts['1'][0]), window.q(os.path.join(self.h.root, 'other')))
        self.assertEqual(self.classify(rows(text, 840, 'deep-net-race')),
                         ({}, {'deep-net-race': unlisted.UNKNOWN_ACCOUNT}))
        self.assertEqual(self.accounts, {'deep-net-race': None})

    def test_a_key_sessions_json_holds(self):
        procs = rows(self.deep(), 850, 'deep-net-race')
        self.assertEqual(self.classify(procs, keys={'deep-net-race'}), ({}, {'deep-net-race': unlisted.KEY_TAKEN}))
        self.assertEqual(self.accounts, {'deep-net-race': '1'})

    def test_skipped_names_are_left_out(self):
        self.assertEqual(self.classify(rows(self.deep(), 860, 'deep-net-race'), skip={'deep-net-race'}), ({}, {}))

    def test_every_reason_fits_a_push_detail(self):
        for why in unlisted.REASONS:
            self.assertRegex(why, r'^[a-z-]+\Z')


if __name__ == '__main__':
    unittest.main()
