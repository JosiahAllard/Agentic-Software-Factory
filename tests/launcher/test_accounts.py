"""Accounts: the launch folder, the headroom refusal, pick-account, and the usage report kitlib.pmwatch parses."""
import datetime
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

from kitlib import pmwatch
from kitlib.hubsite import derived
from kitlib.config import user as kuser
from kitlib.launcher import accounts as A
from kitlib.launcher import settings as lsettings
from kitlib.launcher import usage
from tests.home import fence_decoy
from tests.launcher.fakes import NOW, Home, credentials, sign_in

DAY = datetime.timedelta(days=1)


class LaunchDir(unittest.TestCase):
    def test_the_first_signed_in_folder(self):
        h = Home(self, accounts=('1',))
        second = os.path.join(h.root, 'acct1b')
        os.makedirs(second)
        h.obj['accounts'] = {'1': [os.path.join(h.root, 'nope'), h.accounts['1'][0], second]}
        s = type(h.settings)(h.obj, h.root)
        self.assertEqual(A.launch_dir(s, '1'), h.accounts['1'][0])

    def test_an_account_not_signed_in_is_refused(self):
        h = Home(self, accounts=('1', '2'), signed_out=('2',))
        with self.assertRaisesRegex(A.LaunchError, 'Account 2 is not signed in'):
            A.launch_dir(h.settings, '2')

    def test_an_ended_login_is_refused_with_its_login_command(self):
        h = Home(self, accounts=('1',))
        sign_in(h.accounts['1'][0], signed=False, ends_ms=1000)
        with self.assertRaisesRegex(A.LaunchError, 'its login has ended.*claude auth login'):
            A.launch_dir(h.settings, '1')


class SignedIn(unittest.TestCase):
    """The login read from .credentials.json, never the file's presence alone."""
    NOW = 2000000000.0
    LATER_MS = int((NOW + 86400) * 1000)

    def folder(self, text=None):
        d = tempfile.mkdtemp(prefix='kit-cred-')
        self.addCleanup(shutil.rmtree, d, True)
        if text is not None:
            with open(os.path.join(d, A.CREDENTIALS), 'w') as f:
                f.write(text)
        return d

    def oauth(self, **fields):
        data = json.loads(credentials(ends_ms=self.LATER_MS))
        data['claudeAiOauth'].update(fields)
        return self.folder(json.dumps(data))

    def test_a_live_login_is_signed_in_even_with_its_access_token_expired(self):
        self.assertTrue(A.signed_in(self.oauth(expiresAt=1), self.NOW))

    def test_the_file_claude_writes_at_the_time_limit_is_signed_out(self):
        self.assertFalse(A.signed_in(self.folder(credentials(signed=False, ends_ms=self.LATER_MS)), self.NOW))

    def test_expires_at_0_or_an_empty_refresh_token_is_signed_out(self):
        self.assertFalse(A.signed_in(self.oauth(expiresAt=0), self.NOW))
        self.assertFalse(A.signed_in(self.oauth(refreshToken=''), self.NOW))

    def test_a_past_login_end_is_signed_out_before_claude_marks_it(self):
        self.assertFalse(A.signed_in(self.oauth(refreshTokenExpiresAt=int(self.NOW * 1000)), self.NOW))

    def test_no_login_end_stays_signed_in(self):
        self.assertTrue(A.signed_in(self.folder(credentials()), self.NOW))

    def test_an_empty_unreadable_or_missing_file_is_signed_out(self):
        for text in (None, '', '{}', '[]', '{"mcpOAuth": {}}'):
            self.assertFalse(A.signed_in(self.folder(text), self.NOW), text)

    def test_account_login_gives_the_end_the_ended_state_and_the_newest_mtime(self):
        h = Home(self, accounts=('1', '2', '3'), signed_out=('3',))
        sign_in(h.accounts['1'][0], ends_ms=self.LATER_MS)
        sign_in(h.accounts['2'][0], signed=False, ends_ms=1000)
        os.utime(os.path.join(h.accounts['1'][0], A.CREDENTIALS), (self.NOW, self.NOW))
        self.assertEqual(A.account_login(h.settings, '1', self.NOW),
                         A.Login(True, False, self.NOW + 86400, self.NOW))
        self.assertEqual(A.account_login(h.settings, '2', self.NOW)[:3], (False, True, None))
        self.assertEqual(A.account_login(h.settings, '3', self.NOW), A.Login(False, False, None, None))

    def test_the_login_command_leaves_the_shell_as_it_was(self):
        with mock.patch('kitlib.platform.NAME', 'windows'):
            cmd = A.login_command(r'C:\h\.claude-2', home=r'C:\h')
            self.assertEqual(cmd, "$p = $env:CLAUDE_CONFIG_DIR; try { $env:CLAUDE_CONFIG_DIR = "
                                  "'C:\\h\\.claude-2'; claude auth login } finally { $env:CLAUDE_CONFIG_DIR = $p }")
            self.assertIn('Remove-Item Env:CLAUDE_CONFIG_DIR', A.login_command(r'C:\h\.claude', home=r'C:\h'))
        home = os.path.abspath('/h')
        with mock.patch('kitlib.platform.NAME', 'linux'):
            other = os.path.join(home, '.claude-2')
            self.assertEqual(A.login_command(other, home=home), "CLAUDE_CONFIG_DIR='%s' claude auth login" % other)
            self.assertEqual(A.login_command(os.path.join(home, '.claude'), home=home),
                             'env -u CLAUDE_CONFIG_DIR claude auth login')

    def test_an_org_needs_an_oauth_account_in_its_claude_json(self):
        h = Home(self)
        org = os.path.join(h.root, 'org1')
        os.makedirs(org)
        h.obj['orgs'] = {'1': org}
        s = type(h.settings)(h.obj, h.root)
        with self.assertRaisesRegex(A.LaunchError, 'Org 1 is not signed in'):
            A.org_launch_dir(s, '1')
        with open(os.path.join(org, '.claude.json'), 'w') as f:
            f.write('{"oauthAccount": {"x": 1}}')
        self.assertEqual(A.org_launch_dir(s, '1'), org)


class HostReads(unittest.TestCase):
    """The host's own file readers, imported by their computed name (kitlib.host.registry.host_module):
    a host without that module reads the file's presence; an error inside one propagates."""
    NOW = 2000000000.0

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='kit-hostreads-')
        self.addCleanup(shutil.rmtree, self.root, True)
        self.pkg = 'kit_hostreads_%d' % id(self)
        os.makedirs(os.path.join(self.root, self.pkg))
        open(os.path.join(self.root, self.pkg, '__init__.py'), 'w').close()
        self.name = self.pkg + '.claude_host'
        for p in (mock.patch.object(sys, 'path', [self.root] + sys.path),
                  mock.patch.object(A.registry, 'host_module_name', return_value=self.name)):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(lambda: [sys.modules.pop(m) for m in list(sys.modules)
                                 if m.split('.')[0] == self.pkg])

    def module(self, text):
        with open(os.path.join(self.root, self.pkg, 'claude_host.py'), 'w') as f:
            f.write(text)

    def assert_presence_reads(self):
        h = Home(self, accounts=('1', '2'), signed_out=('2',))
        sign_in(h.accounts['1'][0], signed=False, ends_ms=1000)
        self.assertTrue(A.signed_in(h.accounts['1'][0], self.NOW))
        self.assertFalse(A.signed_in(h.accounts['2'][0], self.NOW))
        self.assertEqual(A.account_login(h.settings, '1', self.NOW)[:3], (True, False, None))
        self.assertIsNone(A.login_command(h.accounts['1'][0], h.root))
        with self.assertRaisesRegex(A.LaunchError, r"Sign it in with its host's own sign-in\.$"):
            A.launch_dir(h.settings, '2')

    def test_a_host_without_the_module_reads_the_files_presence(self):
        self.assert_presence_reads()

    def test_a_module_without_login_of_or_login_reads_the_files_presence(self):
        self.module('X = 1\n')
        self.assert_presence_reads()

    def test_an_error_inside_the_module_propagates(self):
        for text, error in (('import kit_no_such_module_xyz\n', ModuleNotFoundError),
                            ('raise ImportError("inside")\n', ImportError),
                            ('raise ValueError("inside")\n', ValueError)):
            self.module(text)
            sys.modules.pop(self.name, None)
            with self.assertRaises(error, msg=text):
                A.signed_in(self.root, self.NOW)
            with self.assertRaises(error, msg=text):
                A.login_command(self.root)

    def test_a_missing_package_is_not_the_hosts_missing_module(self):
        A.registry.host_module_name.return_value = self.pkg + '_gone.claude_host'
        with self.assertRaises(ModuleNotFoundError):
            A.signed_in(self.root, self.NOW)


class Headroom(unittest.TestCase):
    def test_refused_at_the_limit_unless_forced_and_a_reset_week_is_free(self):
        h = Home(self, accounts=('1', '2'))
        h.usage('1', week=(95, NOW + DAY))
        out = []
        with self.assertRaisesRegex(A.LaunchError, "Account 1 is at 95% of its seven_day window.*95% limit"):
            A.check_headroom(h.settings, '1', NOW, False, out)
        A.check_headroom(h.settings, '1', NOW, True, out)
        self.assertIn('--force: starting on account 1 anyway', out[-1])
        h.usage('2', week=(99, NOW - DAY))
        A.check_headroom(h.settings, '2', NOW, False, out)       # its week has reset

    def test_the_five_hour_window_never_refuses(self):
        # The plan gives no five-hour readings any more: one a file still holds blocks nothing.
        h = Home(self)
        five = {'five_hour': {'used_percentage': 100, 'resets_at': (NOW + DAY / 24).isoformat()}}
        self.assertEqual(A.over_limit(five, NOW), [])
        h.usage('1', five=(100, NOW + DAY / 24), week=(20, NOW + DAY))
        A.check_headroom(h.settings, '1', NOW, False, [])

    def test_the_limit_is_95_at_or_over_is_refused_and_94_is_not(self):
        self.assertEqual(A.HEADROOM_LIMIT, 95)
        h = Home(self, accounts=('1', '2'))
        h.usage('1', five=(94, NOW + DAY / 24), week=(94, NOW + DAY))
        h.usage('2', five=(20, NOW + DAY / 24), week=(95, NOW + DAY))
        A.check_headroom(h.settings, '1', NOW, False, [])
        with self.assertRaisesRegex(A.LaunchError, "Account 2 is at 95% of its seven_day window"):
            A.check_headroom(h.settings, '2', NOW, False, [])

    def test_a_week_that_rounds_to_the_limit_is_refused_as_pick_skips_it(self):
        h = Home(self, accounts=('1',))
        h.usage('1', week=(94.6, NOW + DAY))
        with self.assertRaisesRegex(A.LaunchError, "Account 1 is at 95% of its seven_day window"):
            A.check_headroom(h.settings, '1', NOW, False, [])

    def test_no_usage_file_is_said_not_refused(self):
        h = Home(self)
        out = []
        A.check_headroom(h.settings, '1', NOW, False, out)
        self.assertIn('no usage-status.json', out[0])


class Pick(unittest.TestCase):
    def test_the_soonest_week_reset_under_the_limit(self):
        h = Home(self, accounts=('1', '2', '3'))
        h.usage('1', week=(50, NOW + 3 * DAY))
        h.usage('2', week=(96, NOW + DAY))           # over 95: passed over
        h.usage('3', week=(10, NOW + 2 * DAY))
        n, rows = A.pick(h.settings, NOW)
        self.assertEqual(n, '3')
        self.assertEqual([r[:2] for r in rows], [('1', 50), ('2', 96), ('3', 10)])
        self.assertEqual(len(A.explain(rows)), 3)
        self.assertTrue(A.explain(rows)[0].startswith('account 1: 50% of week, resets '))

    def test_a_passed_reset_is_fresh_and_moves_a_week_on(self):
        h = Home(self, accounts=('1',))
        h.usage('1', week=(80, NOW - DAY))
        (n, pct, reset), = A.week_rows(h.settings, NOW)
        self.assertEqual((pct, reset), (0, NOW + 6 * DAY))

    def test_no_reading_is_unknown_not_0_and_still_eligible(self):
        h = Home(self, accounts=('1', '2', '3'))
        h.usage('1', week=(50, NOW + 8 * DAY))
        h.usage('2', five=(10, NOW + DAY / 24))      # a file with no seven-day window
        rows = A.week_rows(h.settings, NOW)
        self.assertEqual(rows, [('1', 50, NOW + 8 * DAY), ('2', None, None), ('3', None, None)])
        self.assertEqual(A.explain(rows)[1:], ['account 2: week unknown (no usage reading)',
                                               'account 3: week unknown (no usage reading)'])
        self.assertEqual(A.pick(h.settings, NOW)[0], '2')   # unknown counts as resetting a week from now

    def test_a_week_with_a_reset_time_but_no_percentage_is_unknown_not_0(self):
        h = Home(self, accounts=('1',))
        h.usage('1', week=(None, NOW + DAY))
        self.assertEqual(A.week_rows(h.settings, NOW), [('1', None, None)])
        self.assertEqual(A.explain(A.week_rows(h.settings, NOW)), ['account 1: week unknown (no usage reading)'])
        self.assertIn('seven_day=?', usage.report(h.settings, NOW)[0])

    def test_every_account_over_still_picks_the_soonest_and_signed_out_ones_are_skipped(self):
        h = Home(self, accounts=('1', '2', '3'), signed_out=('3',))
        h.usage('1', week=(99, NOW + 2 * DAY))
        h.usage('2', week=(97, NOW + DAY))
        self.assertEqual(A.pick(h.settings, NOW)[0], '2')
        self.assertEqual([r[0] for r in A.pick(h.settings, NOW)[1]], ['1', '2'])

    def test_a_fraction_rounds_as_powershell_did(self):
        h = Home(self, accounts=('1', '2'))
        h.usage('1', week=(94.6, NOW + DAY))         # rounds to 95: at the limit, passed over
        h.usage('2', week=(10, NOW + 2 * DAY))
        n, rows = A.pick(h.settings, NOW)
        self.assertEqual([r[:2] for r in rows], [('1', 95), ('2', 10)])
        self.assertEqual(n, '2')
        self.assertEqual([A.whole(v) for v in (94.4, 94.5, 95.5, None)], [94, 94, 96, 0])

    def test_no_signed_in_account_is_an_error(self):
        h = Home(self, accounts=('1',), signed_out=('1',))
        with self.assertRaisesRegex(A.LaunchError, 'No account in launcher.accounts is signed in'):
            A.pick(h.settings, NOW)


def week_doc(pct, reset, updated=None):
    doc = {'seven_day': {'used_percentage': pct,
                         'resets_at': reset if isinstance(reset, str) else reset.isoformat()}}
    if updated is not None:
        doc['updated_at'] = updated.isoformat()
    return doc


class WeekState(unittest.TestCase):
    """week_state: the one weekly rule of restart_pick, the usage crossover and launch.py usage."""

    def test_a_reset_ahead_keeps_its_value_fresh_or_stale(self):
        for age in (DAY / 24 / 60, 3 * DAY):
            self.assertEqual(A.week_state(week_doc(87, NOW + DAY, NOW - age), NOW), (87, NOW + DAY, False))

    def test_a_passed_reset_is_0_and_moves_a_week_on_even_on_a_stale_reading(self):
        self.assertEqual(A.week_state(week_doc(100, NOW - DAY, NOW - 3 * DAY), NOW), (0, NOW + 6 * DAY, True))

    def test_the_reset_rolls_across_two_weeks(self):
        self.assertEqual(A.week_state(week_doc(100, NOW - 10 * DAY, NOW - 12 * DAY), NOW),
                         (0, NOW + 4 * DAY, True))
        self.assertEqual(A.week_state(week_doc(100, NOW - 14 * DAY), NOW), (0, NOW + 7 * DAY, True))

    def test_a_reading_at_the_limit_keeps_it_until_its_reset_and_the_grace(self):
        # The grace: a local clock a few minutes ahead of the server never resets a week early.
        self.assertEqual(A.RESET_GRACE_S, 300)
        grace = datetime.timedelta(seconds=A.RESET_GRACE_S)
        for at in (NOW + DAY, NOW, NOW - grace + datetime.timedelta(seconds=1)):
            self.assertEqual(A.week_state(week_doc(100, at), NOW), (100, at, False), at)
        self.assertEqual(A.week_state(week_doc(100, NOW - grace), NOW), (0, NOW - grace + 7 * DAY, True))

    def test_a_missing_or_unreadable_week_is_unknown(self):
        no_reset = week_doc(10, NOW + DAY)
        del no_reset['seven_day']['resets_at']
        for data in (None, [], {}, {'seven_day': None}, {'seven_day': 'x'}, no_reset,
                     week_doc(10, 'Fri 11:00'), week_doc(10, ''), week_doc(None, NOW + DAY),
                     week_doc('10', NOW + DAY), week_doc(True, NOW + DAY),
                     week_doc(float('nan'), NOW + DAY), week_doc(float('inf'), NOW + DAY)):
            self.assertEqual(A.week_state(data, NOW), (None, None, False), data)


def reading(five=10, week=10, week_reset=2 * DAY, age=datetime.timedelta(minutes=5), five_reset=DAY / 24):
    """A usage-status.json dict as of NOW: `age` old, each window (pct, reset NOW + delta)."""
    win = lambda pct, delta: {'used_percentage': pct, 'resets_at': (NOW + delta).isoformat()}
    return {'updated_at': (NOW - age).isoformat(), 'five_hour': win(five, five_reset),
            'seven_day': win(week, week_reset)}


class RestartPick(unittest.TestCase):
    """restart_pick: the hub's coordinator restart account (docs/launcher.md "Hub restart")."""

    def pick(self, states, last=None):
        return A.restart_pick({n: (True, d) for n, d in states.items()}, NOW, last, pmwatch.STALE_MINUTES)

    def test_the_soonest_week_reset_among_the_fresh_ones_under_the_limit(self):
        self.assertEqual(self.pick({'1': reading(week_reset=3 * DAY), '2': reading(week_reset=DAY),
                                    '3': reading(week_reset=2 * DAY)}, last='1'), '2')

    def test_a_stale_reading_is_never_under_the_limit(self):
        m = datetime.timedelta(minutes=1)
        states = {'1': reading(week_reset=3 * DAY), '2': reading(week_reset=DAY, age=(pmwatch.STALE_MINUTES + 1) * m)}
        self.assertEqual(self.pick(states), '1')
        states['2'] = reading(week_reset=DAY, age=pmwatch.STALE_MINUTES * m)
        self.assertEqual(self.pick(states), '2')

    def test_a_missing_file_an_unknown_week_or_no_updated_at_is_skipped(self):
        no_pct = reading(week_reset=DAY)
        no_pct['seven_day']['used_percentage'] = None
        no_reset = reading(week_reset=DAY)
        del no_reset['seven_day']['resets_at']
        bad_reset = reading(week_reset=DAY)
        bad_reset['seven_day']['resets_at'] = 'Fri 11:00'
        null_week = dict(reading(week_reset=DAY), five_hour=None, seven_day=None)
        no_upd = reading(week_reset=DAY)
        del no_upd['updated_at']
        bad_upd = dict(reading(week_reset=DAY), updated_at='not a time')
        for bad in (None, no_pct, no_reset, bad_reset, null_week, no_upd, bad_upd):
            self.assertEqual(self.pick({'1': reading(week_reset=3 * DAY), '2': bad}), '1', bad)
            self.assertIsNone(self.pick({'2': bad}), bad)

    def test_a_five_hour_window_is_never_read(self):
        no_pct = reading(week_reset=DAY)
        no_pct['five_hour']['used_percentage'] = None
        no_reset = reading(week_reset=DAY)
        del no_reset['five_hour']['resets_at']
        for five in (no_pct, no_reset, reading(week_reset=DAY, five=100)):
            self.assertEqual(self.pick({'1': reading(week_reset=3 * DAY), '2': five}), '2', five)

    def test_a_fresh_reading_with_no_five_hour_counts_as_0_of_it(self):
        # 2026-11-04: the host gives no five_hour; every account's file reads `"five_hour": null`
        for absent in (lambda d: d.update(five_hour=None), lambda d: d.pop('five_hour')):
            fresh = reading(week_reset=DAY)
            absent(fresh)
            self.assertEqual(self.pick({'1': reading(week_reset=3 * DAY), '2': fresh}), '2')

    def test_a_stale_reading_whose_week_reset_passed_counts_as_0(self):
        # 2026-11-04: account 2's last reading 41 h old, at 100% of a week that has reset since
        stale = dict(reading(week=100, week_reset=-DAY / 2, age=41 * DAY / 24), five_hour=None)
        self.assertEqual(self.pick({'2': stale}), '2')
        self.assertEqual(self.pick({'1': reading(week_reset=3 * DAY), '2': stale}), '1')   # 2 resets in 6.5 days
        self.assertEqual(self.pick({'1': reading(week_reset=7 * DAY), '2': stale}), '2')

    def test_a_stale_reading_whose_week_has_not_reset_never_counts(self):
        m = datetime.timedelta(minutes=1)
        stale = dict(reading(week=0, week_reset=DAY, age=(pmwatch.STALE_MINUTES + 1) * m), five_hour=None)
        self.assertIsNone(self.pick({'2': stale}))

    def test_a_week_at_the_limit_is_skipped(self):
        over = reading(week_reset=DAY, week=A.HEADROOM_LIMIT)
        self.assertEqual(self.pick({'1': reading(week_reset=3 * DAY), '2': over}), '1')
        self.assertEqual(self.pick({'2': reading(week_reset=DAY, week=A.HEADROOM_LIMIT - 0.5)}), '2')

    def test_a_passed_reset_counts_as_0_and_the_week_moves_on(self):
        passed = reading(five=99, five_reset=-DAY / 24, week=99, week_reset=-DAY / 2)   # resets again in 6.5 days
        self.assertEqual(self.pick({'1': passed}), '1')
        self.assertEqual(self.pick({'1': passed, '2': reading(week_reset=6 * DAY)}), '2')

    def test_signed_out_is_skipped_and_ties_go_to_the_last_account_then_the_number(self):
        r = reading(week_reset=DAY)
        states = {'1': (False, reading(week_reset=DAY / 2)), '2': (True, r), '10': (True, r), '3': (True, r)}
        self.assertEqual(A.restart_pick(states, NOW, '3', pmwatch.STALE_MINUTES), '3')
        self.assertEqual(A.restart_pick(states, NOW, None, pmwatch.STALE_MINUTES), '2')
        self.assertEqual(A.restart_pick(states, NOW, '1', pmwatch.STALE_MINUTES), '2')


class Usage(unittest.TestCase):
    def test_the_lines_pmwatch_parses(self):
        h = Home(self, accounts=('1', '2', '3'))
        h.usage('1', five=(12.0, NOW + DAY / 24), week=(51, NOW + DAY), updated=NOW - datetime.timedelta(minutes=3))
        h.usage('2', five=(70, NOW - DAY), week=(30, NOW + DAY), updated=NOW - DAY)
        lines = usage.report(h.settings, NOW)
        self.assertTrue(lines[0].startswith('account 1: five_hour=12% (resets '))
        self.assertTrue(lines[0].endswith('[updated 3 min ago]'))
        self.assertIn('five_hour=reset', lines[1])
        self.assertEqual(lines[2], 'account 3: no usage file')
        self.assertEqual(lines[3], 'current account: 1')
        self.assertEqual(lines[4], 'account codex: no usage data')
        unknown = {'five': None, 'week': None, 'age': None, 'stale': True, 'missing': True}
        self.assertEqual(pmwatch.parse_usage('\n'.join(lines)),
                         {'1': {'five': 12, 'week': 51, 'age': 3, 'stale': False, 'missing': False},
                          '2': {'five': 0, 'week': 30, 'age': 1440, 'stale': True, 'missing': False},
                          '3': unknown, 'codex': unknown})

    def test_a_passed_week_reset_reads_0_computed_with_no_reading_after_it(self):
        # No reading since the week's reset (the 11-02 file, written before it): the reset is
        # computed, 0% to the next one a week on, and said so. A reading after it: a plain 0%.
        h = Home(self, accounts=('1', '2'))
        hour = datetime.timedelta(hours=1)
        h.usage('1', five=(40, NOW - 3 * hour), week=(95, NOW - hour), updated=NOW - 2 * hour)
        h.usage('2', five=(40, NOW - 3 * hour), week=(95, NOW - hour), updated=NOW - hour / 2)
        lines = usage.report(h.settings, NOW)
        nxt = A.short(NOW - hour + 7 * DAY)
        self.assertEqual(lines[0], 'account 1: five_hour=reset  seven_day=0%% (resets %s) reset passed, '
                                   'computed  [updated 120 min ago]' % nxt)
        self.assertEqual(lines[1], 'account 2: five_hour=reset  seven_day=0%% (resets %s)  [updated 30 min ago]' % nxt)
        got = pmwatch.parse_usage('\n'.join(lines))
        self.assertEqual((got['1']['five'], got['1']['week']), (0, 0))
        prev = {'1': {'five': 40, 'week': 95}}
        self.assertEqual(pmwatch.usage_events(prev, got), [])        # one low reading is held, not a RESET
        self.assertEqual(pmwatch.usage_events(pmwatch.merge_usage(prev, got), got),
                         ['RESET account 1 (weekly 95% -> 0%)'])

    def test_the_computed_line_parses_in_pmwatch_and_the_hub_page(self):
        h = Home(self, accounts=('1',))
        h.usage('1', week=(100, NOW - 10 * DAY), updated=NOW - 12 * DAY)     # rolled across two weeks
        lines = usage.report(h.settings, NOW)
        self.assertIn('seven_day=0%% (resets %s) reset passed, computed' % A.short(NOW + 4 * DAY), lines[0])
        got = derived.usage(lambda: lines, lambda: NOW.isoformat())
        one = got['accounts']['1']
        self.assertEqual((one['five'], one['week'], one['stale']), (None, 0, True))
        self.assertEqual(one['week_reset'], (NOW + 4 * DAY).astimezone(datetime.timezone.utc).isoformat())
        self.assertNotIn('five_reset', one)

    def test_a_passed_five_hour_reset_reads_reset_with_no_reading_after_it(self):
        # The rule above is the weekly window's alone (kitlib.pmwatch's RESET is weekly): a five-hour
        # window that reset after the file's last write still reads reset, as launch.py usage and
        # the hub Usage tab showed it before.
        h = Home(self, accounts=('1',))
        hour = datetime.timedelta(hours=1)
        h.usage('1', five=(40, NOW - hour), week=(60, NOW + DAY), updated=NOW - 2 * hour)
        line = usage.report(h.settings, NOW)[0]
        self.assertTrue(line.startswith('account 1: five_hour=reset  seven_day=60% (resets '), line)
        self.assertEqual(pmwatch.parse_usage(line)['1']['five'], 0)

    def test_a_window_without_a_figure_reads_unknown_and_is_not_dropped(self):
        h = Home(self, accounts=('1', '2'))
        h.usage('1', week=(98, NOW + DAY), updated=NOW - DAY)          # five_hour null, as the status line writes it
        h.usage('2', five=(None, NOW + DAY / 24), week=(12.5, NOW + DAY))
        lines = usage.report(h.settings, NOW)
        self.assertTrue(lines[0].startswith('account 1: five_hour=?  seven_day=98% (resets '), lines[0])
        self.assertTrue(lines[1].startswith('account 2: five_hour=?  seven_day=12% (resets '), lines[1])
        got = pmwatch.parse_usage('\n'.join(lines))
        self.assertEqual((got['1']['five'], got['1']['week'], got['1']['stale']), (None, 98, True))
        self.assertEqual((got['2']['five'], got['2']['week'], got['2']['stale']), (None, 12, False))


    def test_the_codex_week_rounds_as_powershell_did(self):
        h = Home(self)
        d = os.path.join(h.root, '.codex', 'sessions', '2026')
        os.makedirs(d)
        reset = int((NOW + DAY).timestamp())
        with open(os.path.join(d, 'a.jsonl'), 'w', encoding='utf-8') as f:
            f.write('{"primary":{"used_percent":94.6,"window_minutes":10080,"resets_at":%d}}\n' % reset)
        line = usage.codex_line(h.root, NOW)
        self.assertTrue(line.startswith('account codex: five_hour=reset  seven_day=95% (resets '), line)
        self.assertEqual(pmwatch.parse_usage(line)['codex']['week'], 95)


class ParseTime(unittest.TestCase):
    def test_powershell_round_trip_times_with_seven_fraction_digits(self):
        want = datetime.datetime(2026, 10, 10, 14, 21, 2, 123456, tzinfo=datetime.timezone(datetime.timedelta(hours=2)))
        self.assertEqual(A.parse_time('2026-10-10T14:21:02.1234567+02:00'), want)
        self.assertEqual(A.parse_time('2026-10-10T12:21:02.1234567Z'), want)
        self.assertEqual(A.parse_time('2026-10-10T12:21:02.123Z'), want.replace(microsecond=123000))
        for bad in ('', None, 'soon', 5):
            self.assertIsNone(A.parse_time(bad))


class HomeFence(unittest.TestCase):
    """docs/test-tools.md "Home fence": the launcher's home lookups refuse the fenced home."""

    def test_no_home_is_refused(self):
        fence_decoy(self)
        machine = {'launcher': {'accounts': {'1': ['~/.claude']}}}
        with self.assertRaises(kuser.HomeFenced):
            A.current_account(machine, environ={})
        with self.assertRaises(kuser.HomeFenced):
            lsettings.load()
        given = Home(self, accounts=('1',))
        self.assertEqual(A.current_account(machine, environ={}, home=given.root), '1')


if __name__ == '__main__':
    unittest.main()
