"""statusline-usage.ps1's merge on write (docs/launcher.md "Usage"): every session on an account
writes the one usage-status.json, and an idle session's rate_limits are stale, so a reading never
replaces a newer one for the same window. The 11-02 replay runs the readers the PM watcher and the
usage crossover use over the file the script leaves.

    python -m unittest tests.optional.usage.test_statusline_merge -v
"""
import concurrent.futures
import datetime as dt
import json
import os
import shutil
import subprocess
import unittest

from kitlib import pmwatch
from kitlib.launcher import accounts as A
from kitlib.launcher import usage
from tests.launcher.fakes import Home
from tests.platforms import windows_only

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, '..', '..', '..', 'optional', 'usage', 'statusline-usage.ps1')
HOUR = dt.timedelta(hours=1)
DAY = dt.timedelta(days=1)


def now():
    return dt.datetime.now().astimezone()


def epoch(t):
    return int(t.timestamp())


@windows_only("needs Windows PowerShell")
@unittest.skipUnless(shutil.which("powershell"), "needs Windows PowerShell")
class Merge(unittest.TestCase):
    def setUp(self):
        self.home = Home(self, accounts=('1',))
        self.dir = self.home.accounts['1'][0]
        self.path = os.path.join(self.dir, 'usage-status.json')
        self.t0 = now()

    def write(self, five, week, session='s', lock_ms=None):
        """Run the status line as a session whose last rate_limits were five and week ((pct, reset)).
        lock_ms: how long it waits for the lock (PMKIT_USAGE_LOCK_MS; the script's own is 2 s)."""
        limits = {}
        for k, v in (('five_hour', five), ('seven_day', week)):
            if v is not None:
                limits[k] = {'used_percentage': v[0], 'resets_at': epoch(v[1])}
        env = dict(os.environ, CLAUDE_CONFIG_DIR=self.dir)
        env.pop('PMKIT_USAGE_LOCK_MS', None)
        if lock_ms is not None:
            env['PMKIT_USAGE_LOCK_MS'] = str(lock_ms)
        p = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", SCRIPT],
                           input=json.dumps({'session_id': session, 'model': {'display_name': 'Opus'},
                                             'rate_limits': limits}),
                           capture_output=True, text=True, timeout=180, env=env)
        self.assertEqual(p.returncode, 0, p.stderr)
        return p

    def file(self):
        with open(self.path, encoding='utf-8-sig') as f:
            return json.load(f)

    def week(self):
        w = self.file()['seven_day']
        return w['used_percentage'], A.parse_time(w['resets_at'])

    def reading(self):
        """What the PM watcher reads: kitlib.launcher.usage's lines through parse_usage."""
        return pmwatch.parse_usage('\n'.join(usage.report(self.home.settings, now())))

    def test_the_11_02_sequence_gives_no_reset_no_refire_and_crossover_reads_95(self):
        reset = self.t0 + 4 * DAY
        self.write((30, self.t0 + 2 * HOUR), (86, reset), 'fresh')
        state = self.reading()
        self.write((40, self.t0 + 2 * HOUR), (95, reset), 'fresh')
        cur = self.reading()
        self.assertEqual(pmwatch.usage_events(state, cur), ['USAGE account 1 seven_day 95% (now 95%)'])
        state = pmwatch.merge_usage(state, cur)
        updated = self.file()['updated_at']
        # An idle session's stale figure in the same week, its five-hour window long gone; then one
        # whose cached week reset has passed (an older week).
        for five, week, who in (((12, self.t0 - HOUR), (54, reset), 'idle-a'),
                                ((70, self.t0 - 3 * DAY), (80, self.t0 - 3 * DAY), 'idle-b')):
            self.write(five, week, who)
            cur = self.reading()
            self.assertEqual(pmwatch.usage_events(state, cur), [], who)
            self.assertEqual(cur['1']['week'], 95, who)
            state = pmwatch.merge_usage(state, cur)
            self.assertEqual(self.file()['updated_at'], updated, '%s moved the reading time' % who)
            self.assertEqual(self.file()['session_id'], 'fresh')
        self.write((41, self.t0 + 2 * HOUR), (95, reset), 'fresh')
        self.assertEqual(pmwatch.usage_events(state, self.reading()), [])       # no USAGE re-fire
        # The usage crossover's reader (kitlib.hubjobs.usage_crossover, _accounts) and pick-account's.
        _, data = A.newest_usage(self.home.settings, '1', by='updated')
        self.assertEqual(data['seven_day']['used_percentage'], 95)
        self.assertEqual(A.week_rows(self.home.settings, now()), [('1', 95, A.parse_time(data['seven_day']['resets_at']))])

    def test_a_new_window_replaces_a_higher_figure(self):
        self.write((90, self.t0 + HOUR), (95, self.t0 + HOUR / 2))
        self.write((2, self.t0 + 5 * HOUR), (1, self.t0 + 7 * DAY))
        self.assertEqual(self.week()[0], 1)
        self.assertEqual(self.file()['five_hour']['used_percentage'], 2)

    def test_the_same_window_keeps_the_higher_figure_and_takes_a_rise(self):
        reset = self.t0 + 2 * DAY
        self.write((50, self.t0 + HOUR), (60, reset))
        self.write((50, self.t0 + HOUR), (61, reset + dt.timedelta(seconds=40)))   # resets_at a few seconds off: same week
        self.assertEqual(self.week()[0], 61)
        self.write((49, self.t0 + HOUR), (61, reset), 'other')
        self.assertEqual(self.file()['five_hour']['used_percentage'], 50)

    def test_the_session_a_window_came_from_can_lower_it(self):
        # A plan upgrade or raised limit: the same window's figure falls. The session whose reading
        # the window holds read it after that reading, so its lower figure is taken; another
        # session's lower figure may be stale and is not.
        reset = self.t0 + 2 * DAY
        self.write((50, self.t0 + HOUR), (60, reset), 'a')
        before = self.file()['updated_at']
        self.write((40, self.t0 + HOUR), (30, reset), 'b')
        self.assertEqual((self.file()['five_hour']['used_percentage'], self.week()[0]), (50, 60))
        self.assertEqual(self.file()['updated_at'], before)
        self.write((45, self.t0 + HOUR), (31, reset), 'a')
        self.assertEqual((self.file()['five_hour']['used_percentage'], self.week()[0]), (45, 31))
        self.assertEqual(self.file()['seven_day']['session_id'], 'a')
        self.assertGreater(A.parse_time(self.file()['updated_at']), A.parse_time(before))
        self.write((40, self.t0 + HOUR), (30, reset), 'b')           # still not b's to lower
        self.assertEqual(self.week()[0], 31)

    def test_an_equal_reading_is_written_so_an_active_session_stays_fresh(self):
        reset = self.t0 + 2 * DAY
        self.write((50, self.t0 + HOUR), (60, reset), 'a')
        before = self.file()['updated_at']
        self.write((50, self.t0 + HOUR), (60, reset), 'b')
        self.assertEqual(self.file()['session_id'], 'b')
        self.assertGreater(A.parse_time(self.file()['updated_at']), A.parse_time(before))

    def test_a_file_that_does_not_parse_is_replaced(self):
        with open(self.path, 'w') as f:
            f.write('{"updated_at": ')
        self.write((5, self.t0 + HOUR), (7, self.t0 + DAY))
        self.assertEqual(self.week()[0], 7)

    def test_concurrent_writers_lose_no_reading_and_leave_nothing_behind(self):
        reset = self.t0 + 3 * DAY
        self.write((10, self.t0 + HOUR), (50, reset))
        # A lock wait long enough for a loaded machine: a writer that gave up would lose its reading
        # by design, not by a race, and this test is about the race.
        with concurrent.futures.ThreadPoolExecutor(8) as pool:
            list(pool.map(lambda pct: self.write((10, self.t0 + HOUR), (pct, reset), 's%d' % pct, lock_ms=120000),
                          range(51, 59)))
        self.assertEqual(self.week()[0], 58)
        self.assertEqual(sorted(os.listdir(self.dir)), ['.credentials.json', 'usage-status.json'])


if __name__ == '__main__':
    unittest.main()
