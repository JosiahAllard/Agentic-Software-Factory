"""A fake launcher host and a temp home with signed-in accounts, for tests/launcher. Nothing here
opens a window, reads the live process list or stops a process."""
import datetime
import json
import os
import re
import shutil
import tempfile
from unittest import mock

from kitlib.git import repo as gitrepo
from kitlib.launcher import settings as S
from kitlib.launcher.host import Host

def windows_text(test):
    """Run `test` as on Windows: window scripts are PowerShell text and process rows are claude.exe."""
    patch = mock.patch('kitlib.platform.NAME', 'windows')
    patch.start()
    test.addCleanup(patch.stop)


NOW = datetime.datetime(2026, 10, 10, 12, 0, tzinfo=datetime.timezone.utc)
ROLE = re.compile(r"(?:\$env:PM_KIT_ROLE = |^export PM_KIT_ROLE=)'([^']*)'", re.M)
EXE = r'C:\bin\claude.exe'


def credentials(signed=True, ends_ms=None, token='fixture-token'):
    """The text of a .credentials.json in Claude Code's shape: the field names and types of a real
    file, none of its values. signed: a live login (refreshTokenExpiresAt `ends_ms`, absent when
    None); else what Claude Code writes when a refresh is refused at the login's time limit
    (tokens emptied, expiresAt 0, loginEnded), the end kept. An mcpOAuth entry, which never
    counts, either way."""
    oauth = {'accessToken': token, 'refreshToken': token, 'expiresAt': 1, 'scopes': ['user:inference'],
             'subscriptionType': 'max', 'subscriptionTypeSource': 'profile', 'rateLimitTier': 'tier'}
    if ends_ms is not None:
        oauth['refreshTokenExpiresAt'] = ends_ms
    if not signed:
        oauth.update(accessToken='', refreshToken='', expiresAt=0, loginEnded='time_limit')
    mcp = {'server|0': {'serverName': 'x', 'accessToken': token, 'refreshToken': token, 'expiresAt': 1}}
    return json.dumps({'claudeAiOauth': oauth, 'mcpOAuth': mcp})


def sign_in(folder, signed=True, ends_ms=None):
    """Write `folder`'s .credentials.json (credentials)."""
    with open(os.path.join(folder, '.credentials.json'), 'w') as f:
        f.write(credentials(signed, ends_ms))


def role_of(text):
    m = ROLE.search(text)
    return m.group(1) if m else None


def session(pid, name, parent=1, exe=EXE, agent='coordinator'):
    return {'Name': 'claude.exe', 'ProcessId': pid, 'ParentProcessId': parent, 'ExecutablePath': exe,
            'CommandLine': "claude --agent %s --remote-control '%s' -n '%s' 'x'" % (agent, name, name)}


class FakeHost(Host):
    def __init__(self, home, procs=(), env=None, ignored=None, own_pid=99999, uptime=5.0):
        Host.__init__(self, home, dict(env or {}))
        self.procs = list(procs)
        self.opened, self.killed, self.slept, self.piped = [], [], [], []
        self.open_kw = []           # open_window's keyword arguments, one dict a window (Linux)
        self.ignored, self.mypid, self.uptime = ignored, own_pid, uptime
        self.where, self.homes = None, []      # place()'s kind (None: place() fails the test); home_worktree calls

    def env(self, name):
        return self.environ.get(name)

    def processes(self):
        return list(self.procs)

    def now(self):
        return NOW

    def open_window(self, text, **kw):
        if not role_of(text):
            raise AssertionError('a window without a PM_KIT_ROLE mark: %s' % text[:200])
        self.opened.append(text)
        self.open_kw.append(kw)

    def git_ignored(self, repo, path):
        return self.ignored

    def place(self, folder):
        if self.where is None:
            raise AssertionError('place(%s) asked with no place set' % folder)
        top = folder if self.where in (gitrepo.MAIN, gitrepo.LINKED) else None
        return gitrepo.Place(self.where, top, 'fake %s' % self.where, None, None)

    def home_worktree(self, main, path, whatif=False):
        self.homes.append((main, path, whatif))
        return ['HOME %s whatif=%s' % (path, whatif)]

    def own_pid(self):
        return self.mypid

    def ctime(self, pid):
        return 1000 + int(pid)

    def command_line(self, pid, ctime):
        for p in self.procs:
            if int(p['ProcessId']) == int(pid) and ctime == 1000 + int(pid):
                return p.get('CommandLine')
        return None

    def terminate(self, pid, ctime):
        self.killed.append((int(pid), ctime))
        return 'killed'

    def uptime_minutes(self):
        return self.uptime

    def sleep(self, seconds):
        self.slept.append(seconds)

    def record_pipe(self, settings, name, after_ms):
        """No registry to poll: the call is noted (self.piped) and nothing is said."""
        self.piped.append((name, after_ms))
        return None


class Home(object):
    """A temp home: account folders a1..aN (signed in unless listed in signed_out), a repo
    folder per name, a state folder."""

    def __init__(self, test, accounts=('1', '2'), signed_out=(), repos=('alpha',), extra=None):
        self.root = tempfile.mkdtemp(prefix='kit-launch-')
        test.addCleanup(shutil.rmtree, self.root, True)
        self.state = os.path.join(self.root, 'state')
        os.makedirs(self.state)
        self.repos = {}
        for r in repos:
            self.repos[r] = os.path.join(self.root, 'repos', r)
            os.makedirs(self.repos[r])
        self.accounts = {}
        for n in accounts:
            d = os.path.join(self.root, 'acct%s' % n)
            os.makedirs(d)
            if n not in signed_out:
                sign_in(d)
            self.accounts[n] = [d]
        obj = {'state_dir': self.state, 'repos': self.repos, 'accounts': self.accounts}
        obj.update(extra or {})
        self.obj = obj
        self.settings = S.Settings(obj, self.root)

    def usage(self, n, five=None, week=None, updated=NOW, i=0):
        def w(v):
            if v is None:
                return None
            pct, reset = v
            return {'used_percentage': pct, 'resets_at': reset.isoformat()}
        data = {'updated_at': updated.isoformat(), 'five_hour': w(five), 'seven_day': w(week)}
        with open(os.path.join(self.accounts[n][i], 'usage-status.json'), 'w', encoding='utf-8') as f:
            json.dump(data, f)

    def write_state(self, name, data):
        with open(os.path.join(self.state, name), 'w', encoding='utf-8') as f:
            json.dump(data, f)

    def read_state(self, name):
        with open(os.path.join(self.state, name), encoding='utf-8') as f:
            return json.load(f)
