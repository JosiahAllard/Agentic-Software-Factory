"""Claude accounts: which config folder a launch uses, whether it is signed in, how much of its
usage windows is left, which account to pick (docs/launcher.md "Accounts"; restart_pick for the
hub's coordinator restart, docs/launcher.md "Hub restart"), and which account
this session runs on (current_account, for the Action Hub's one copy per account).

Every Claude session's status line writes usage-status.json into its account's config folder:
{"updated_at": iso, "five_hour": {"used_percentage": n, "resets_at": iso} | null,
 "seven_day": {...} | null}. Only the week counts (week_state): the plan gives no five-hour
readings, and a five_hour a file still holds is never read here. A week whose resets_at has
passed has reset (0%), even on a stale reading.

The account folders and the `claude` runner the plugin refresh and the machine setup use
(docs/plugin-source.md): default_config_dirs and dedupe (~/.claude and every ~/.claude-*, each
real folder once); account_folders (those, then the launcher's account and org folders);
installed_path, installs and marketplace_mismatches (where an account loads the kit plugin from,
read only); Runner and run_claude (`claude <args>` in one folder, CLAUDE_CONFIG_DIR set to it, or
removed for Claude Code's own ~/.claude, since setting it, even to that folder, moves where claude
keeps its .claude.json; is_default). With no `home`, each reads the user's home through
kitlib.config.user.home_dir(), which the kit's tests fence (docs/test-tools.md "Home fence").
"""
import datetime
import json
import math
import os
import re
import shutil
import subprocess
import sys

from kitlib.config import user as kuser
from kitlib.host import registry
from kitlib.launcher.settings import LaunchError

CREDENTIALS = '.credentials.json'
USAGE_FILE = 'usage-status.json'
HEADROOM_LIMIT = 95      # deep, consultant and topic launches refuse an account at or over this % of its week, the band account picking also skips
RESET_GRACE_S = 300      # a recorded week reset counts as passed this long after it: a local clock ahead of the server never resets a full week early
WEEK = datetime.timedelta(days=7)
PICK_LIMIT = 95          # pick-account's default
FRACTION = re.compile(r'\.\d{7,}')   # PowerShell ToString('o') writes 7 fraction digits
TIMEOUT = 300
_NOWIN = 0x08000000 if sys.platform == 'win32' else 0      # CREATE_NO_WINDOW
INSTALLED = ('plugins', 'installed_plugins.json')   # Claude Code's install record, in an account folder
KNOWN_MARKETPLACES = ('plugins', 'known_marketplaces.json')   # its marketplaces, in an account folder
USER_SCOPE = 'user'
PLUGIN_ID = '%s@%s' % (registry.PLUGIN_NAME, registry.PLUGIN_NAME)   # the kit plugin in an install record


def parse_time(text):
    """An ISO time (PowerShell's 7-digit fractions and offsets too) as an aware datetime, or None."""
    if not isinstance(text, str) or not text:
        return None
    text = FRACTION.sub(lambda m: m.group(0)[:7], text.replace('Z', '+00:00'))   # Python before 3.11 takes 6 digits
    try:
        dt = datetime.datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.astimezone()


def whole(pct):
    """A percentage as a whole number, rounded half to even as PowerShell's [int] cast did (94.6 is
    95, so a 95% limit counts it as over); None or '' is 0."""
    return int(round(float(pct or 0)))


def short(dt):
    """'ddd HH:mm' in local time, as the PowerShell launchers printed it."""
    return dt.astimezone().strftime('%a %H:%M')


def signed_in(folder):
    return os.path.isfile(os.path.join(folder, CREDENTIALS))


def org_signed_in(folder):
    """An org sign-in keeps .credentials.json empty; its .claude.json names the account instead."""
    try:
        with open(os.path.join(folder, '.claude.json'), encoding='utf-8-sig') as f:
            return bool((json.load(f) or {}).get('oauthAccount'))
    except (OSError, ValueError, AttributeError):
        return False


def account_signed_in(settings, n):
    return any(signed_in(d) for d in settings.account_dirs(n))


def launch_dir(settings, n):
    """The config folder an account-n session starts with: the first of its folders that holds a
    sign-in. LaunchError when none does."""
    dirs = settings.account_dirs(n)
    for d in dirs:
        if signed_in(d):
            return d
    raise LaunchError('Account %s is not signed in: no %s under %s.' % (n, CREDENTIALS, ' or '.join(dirs)))


def org_launch_dir(settings, n):
    d = settings.org_dir(n)
    if not org_signed_in(d):
        raise LaunchError('Org %s is not signed in: sign in under %s (its .claude.json names no '
                          'oauthAccount).' % (n, d))
    return d


def _read(path):
    try:
        with open(path, encoding='utf-8-sig') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def newest_usage(settings, n, by='mtime'):
    """(path, data) of the account's freshest usage-status.json, or (None, None). by='mtime' picks
    the newest file write (deep, consultant, pick); by='updated' the newest updated_at (usage)."""
    found = []
    for d in settings.account_dirs(n):
        p = os.path.join(d, USAGE_FILE)
        if not os.path.isfile(p):
            continue
        data = _read(p)
        if data is None:
            continue
        if by == 'updated':
            key = parse_time(data.get('updated_at'))
            key = key.timestamp() if key else float('-inf')
        else:
            key = os.path.getmtime(p)
        found.append((key, p, data))
    if not found:
        return None, None
    _, p, data = max(found, key=lambda t: t[0])
    return p, data


def week_state(data, now):
    """(pct, reset, passed) of a reading's week: the one weekly rule of restart_pick, the usage
    crossover and launch.py usage (docs/launcher.md "Usage"). (None, None, False), unknown, when
    the reading has no seven_day object, its resets_at is missing or not a time, or its
    used_percentage is not a finite number. A reset still ahead, or passed by less than
    RESET_GRACE_S: the recorded percentage and reset, fresh or stale (a reading at the limit
    keeps it until then). Passed by RESET_GRACE_S or more: 0%, the reset moved on whole weeks to
    the first one after `now`, and passed True, even on a stale reading with no reading after it.
    The arithmetic is on absolute instants, so a clock change does not move a reset."""
    w = data.get('seven_day') if isinstance(data, dict) else None
    if not isinstance(w, dict):
        return None, None, False
    pct, reset = w.get('used_percentage'), parse_time(w.get('resets_at'))
    if (reset is None or not isinstance(pct, (int, float)) or isinstance(pct, bool)
            or not math.isfinite(pct)):
        return None, None, False
    if now < reset + datetime.timedelta(seconds=RESET_GRACE_S):
        return pct, reset, False
    return 0, reset + ((now - reset) // WEEK + 1) * WEEK, True


def over_limit(data, now, limit=HEADROOM_LIMIT):
    """The week at or over `limit`% that has not reset yet (week_state), as ['N% of its seven_day
    window (resets ddd HH:mm)'], else []. The percentage is whole() as pick counts it, so 94.6 is
    95 and is over. A five_hour window is never read."""
    pct, reset, _ = week_state(data, now)
    if pct is None or whole(pct) < limit:
        return []
    return ['%d%% of its seven_day window (resets %s)' % (whole(pct), short(reset))]


def check_headroom(settings, n, now, force, out, limit=HEADROOM_LIMIT):
    """Refuse (LaunchError) an account at or over `limit`% of a window unless force; say so in out."""
    path, data = newest_usage(settings, n)
    if path is None:
        out.append('Account %s: no %s, so its headroom is not checked.' % (n, USAGE_FILE))
        return
    over = over_limit(data, now, limit)
    if not over:
        return
    msg = 'Account %s is at %s' % (n, ' and '.join(over))
    if not force:
        raise LaunchError("%s; it is at or over the %d%% limit. Pick another account, wait for the "
                          'reset, or pass --force.' % (msg, limit), out)
    out.append('--force: starting on account %s anyway; it is at %s.' % (n, ' and '.join(over)))


def week_rows(settings, now):
    """[(account, pct, reset)] of every signed-in account's seven-day window (week_state, pct a
    whole number). A week whose reset has passed is fresh (0%) and resets a whole number of weeks
    after the old time. No file, or a file with no seven-day window or no percentage in it, is
    unknown: pct and reset None, never 0%."""
    rows = []
    for n in settings.account_numbers():
        if not account_signed_in(settings, n):
            continue
        _, data = newest_usage(settings, n)
        pct, reset, _ = week_state(data, now)
        rows.append((n, None if pct is None else whole(pct), reset))
    return rows


def pick(settings, now, limit=PICK_LIMIT):
    """(account, rows): the signed-in account under `limit`% of its week whose week resets
    soonest (use up the allowance that expires first); when every one is over, the soonest reset
    anyway, so something always runs. An account whose week is unknown (no usage file) counts as
    under the limit, resetting a week from now, as before. LaunchError when no account is signed in."""
    rows = week_rows(settings, now)
    if not rows:
        raise LaunchError('No account in launcher.accounts is signed in.')
    unknown = now + datetime.timedelta(days=7)
    ok = [r for r in rows if r[1] is None or r[1] < limit]
    chosen = min(ok or rows, key=lambda r: unknown if r[2] is None else r[2])
    return chosen[0], rows


def _restart_reset(data, now, stale_minutes, limit):
    """The week reset of a reading restart_pick may start on, or None: a week that has reset
    (week_state passed, 0%), fresh or stale; or a fresh reading under `limit`% of its week. Fresh:
    an updated_at at most `stale_minutes` whole minutes old (kitlib.pmwatch's stale line). An
    unknown week, or a stale reading whose week has not reset, never qualifies."""
    pct, reset, passed = week_state(data, now)
    if pct is None or passed:
        return reset
    upd = parse_time(data.get('updated_at'))
    if upd is None or int((now - upd).total_seconds() // 60) > stale_minutes or pct >= limit:
        return None
    return reset


def restart_pick(states, now, last, stale_minutes, limit=HEADROOM_LIMIT):
    """The account the hub's coordinator restart starts on (docs/launcher.md "Hub restart"): of
    `states` {account: (signed_in, usage data or None)}, the signed-in one whose week has reset or
    whose fresh reading is under `limit`% of its week (_restart_reset), the week that resets
    soonest; a tie goes to `last`, then the lowest number. None when no account qualifies: a
    missing or unknown reading never does, nor a stale one whose week has not reset."""
    rows = []
    for n, (signed, data) in states.items():
        reset = _restart_reset(data, now, stale_minutes, limit) if signed else None
        if reset is not None:
            rows.append((reset, str(n) != str(last), int(n) if str(n).isdigit() else float('inf'), str(n)))
    return min(rows)[3] if rows else None


DEFAULT_LABEL = 'default'    # current_account's answer with no launcher accounts or no match
CONFIG_DIR_ENV = 'CLAUDE_CONFIG_DIR'


def _same_folder_key(folder, home):
    """`folder` (absolute, or ~ / ~/<path>) as a comparable key: home expanded, normalised,
    case and slashes folded (os.path.normcase on Windows)."""
    f = folder.strip()
    if f == '~':
        f = home
    elif f.startswith(('~/', '~\\')):
        f = os.path.join(home, f[2:])
    return os.path.normcase(os.path.normpath(f))


def current_account(machine, environ=None, home=None):
    """The label of the Claude account this process runs on: the `launcher.accounts` key (of the
    machine config dict `machine`) whose folder list holds this session's config folder, which is
    CLAUDE_CONFIG_DIR, or Claude Code's default folder ~/.claude when that is unset or empty.
    DEFAULT_LABEL when the machine config has no launcher accounts (a fresh user) or no account
    lists the folder. Pure: it reads only its arguments (environ defaults to os.environ, home to
    the user's home folder) and never raises for a malformed launcher object."""
    launcher = machine.get('launcher') if isinstance(machine, dict) else None
    accts = launcher.get('accounts') if isinstance(launcher, dict) else None
    if not isinstance(accts, dict) or not accts:
        return DEFAULT_LABEL
    env = os.environ if environ is None else environ
    h = home or kuser.home_dir()
    cur = env.get(CONFIG_DIR_ENV) or ''
    here = _same_folder_key(cur if cur.strip() else '~/.claude', h)
    # Numbered labels in number order ("2" before "10"), as the launcher lists them; others after.
    for n in sorted(accts, key=lambda k: (0, int(k), '') if str(k).isdigit() else (1, 0, str(k))):
        dirs = accts[n]
        if not isinstance(dirs, list):
            continue
        if any(isinstance(d, str) and d.strip() and _same_folder_key(d, h) == here for d in dirs):
            return str(n)
    return DEFAULT_LABEL


def resolve_current(settings, now, environ=None):
    """(account, note) for `--account current`: current_account over this machine's launcher
    accounts (`settings.accounts`, `settings.home`). A session whose folder no account lists
    (DEFAULT_LABEL) falls back to pick, and note says so. LaunchError when no account is signed in."""
    label = current_account({'launcher': {'accounts': settings.accounts}}, environ=environ, home=settings.home)
    if label in settings.accounts:
        return label, None
    n, _ = pick(settings, now)
    return n, 'current account not identified; picked %s' % n


def explain(rows):
    return ['account %s: week unknown (no usage reading)' % n if pct is None else
            'account %s: %d%% of week, resets %s' % (n, pct, reset.astimezone().strftime('%a %b %d %H:%M'))
            for n, pct, reset in rows]


def default_config_dirs(home):
    """`<home>/.claude` and every `<home>/.claude-*` folder."""
    out = []
    try:
        names = sorted(os.listdir(home))
    except OSError:
        return out
    for n in names:
        if (n == '.claude' or n.startswith('.claude-')) and os.path.isdir(os.path.join(home, n)):
            out.append(os.path.join(home, n))
    return out


def dedupe(dirs):
    """`dirs` in order, each real folder once: a folder that is a link to another one listed (an
    account folder following ~/.claude) would otherwise be edited twice."""
    out, seen = [], set()
    for d in dirs:
        key = os.path.normcase(os.path.realpath(d))
        if key not in seen:
            seen.add(key)
            out.append(d)
    return out


def account_folders(home=None):
    """Existing account folders, each real folder once: ~/.claude and every ~/.claude-*, then every folder of the
    machine config's launcher.accounts and launcher.orgs. A launcher config that cannot be read
    adds nothing (the default folders still count)."""
    home = home or kuser.home_dir()
    dirs = list(default_config_dirs(home))
    try:
        from kitlib.launcher import settings as lsettings
        ls = lsettings.load(home)
        for n in ls.account_numbers():
            dirs.extend(ls.account_dirs(n))
        for n in sorted(ls.orgs, key=int):
            dirs.append(ls.org_dir(n))
    except Exception:            # a missing or bad launcher config: pm_start reports it on its own
        pass
    return dedupe(os.path.abspath(d) for d in dirs if os.path.isdir(d))


def installed_path(folder):
    """The folder account folder `folder`'s Claude Code loads the kit plugin from, read from its
    install record: the user-scope install's installPath, else the first record's absolute
    installPath. None for no record, one that cannot be read, or no install of the kit. The
    path is not checked on disk."""
    try:
        with open(os.path.join(folder, *INSTALLED), encoding='utf-8-sig') as fh:
            recs = json.load(fh)['plugins'][PLUGIN_ID]
        paths = [(r.get('scope'), r.get('installPath')) for r in recs]
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None
    paths = [(scope, p) for scope, p in paths if isinstance(p, str) and os.path.isabs(p)]
    for scope, p in paths:
        if scope == USER_SCOPE:
            return p
    return paths[0][1] if paths else None


def installs(folders=None):
    """[(install path, account folder)] for each folder of `folders` (default account_folders())
    whose install record names the kit plugin, in that order."""
    out = []
    for f in (account_folders() if folders is None else folders):
        path = installed_path(f)
        if path is not None:
            out.append((path, f))
    return out


def _same_path(a, b):
    return (os.path.normcase(os.path.normpath(os.path.abspath(a)))
            == os.path.normcase(os.path.normpath(os.path.abspath(b))))


def _read_json(path):
    """The parsed JSON at `path`; None when the file does not exist. OSError or ValueError when it
    cannot be read or parsed."""
    if not os.path.lexists(path):
        return None
    with open(path, encoding='utf-8-sig') as fh:
        return json.load(fh)


def _check_one(path, keys, what, where, export_dir, out):
    """The pm-kit marketplace path in the JSON file `path`, reached by `keys` from its top object,
    against `export_dir`: a line in `out` when it differs or cannot be read. A missing file, or no
    entry at keys[:-1] (no pm-kit marketplace there), adds nothing."""
    try:
        node = _read_json(path)
        if node is None:
            return
        for i, k in enumerate(keys):
            if not isinstance(node, dict):
                raise ValueError('%s is not a JSON object' % ('the file' if i == 0 else keys[i - 1]))
            if k not in node and i < len(keys) - 1:
                return
            node = node.get(k)
        if not isinstance(node, str) or not node:
            raise ValueError('%s is not a path' % what)
        if not _same_path(node, export_dir):
            out.append('pm-kit marketplace %s is %s, not the export %s (%s: %s)'
                       % (where, node, export_dir, path, what))
    except (OSError, ValueError) as e:
        out.append('could not check the pm-kit marketplace path in %s: %s' % (path, e))


def marketplace_mismatches(export_dir, folders, managed=None):
    """Lines for each place that points the pm-kit marketplace somewhere other than `export_dir`
    (docs/plugin-source.md "Refresh"): each account folder's plugins/known_marketplaces.json
    installLocation, and the managed settings file `managed` (its path; None: not checked)
    extraKnownMarketplaces source path. Reads only, never writes, never raises: a missing file or
    entry adds nothing, one that cannot be read or has the wrong shape adds a 'could not check'
    line."""
    out = []
    for f in folders:
        _check_one(os.path.join(f, *KNOWN_MARKETPLACES), (registry.PLUGIN_NAME, 'installLocation'),
                   'installLocation', 'in %s' % f, export_dir, out)
    if managed:
        _check_one(managed, ('extraKnownMarketplaces', registry.PLUGIN_NAME, 'source', 'path'),
                   'extraKnownMarketplaces source path', 'in the managed settings', export_dir, out)
    return out


def is_default(folder, home=None):
    """True for ~/.claude, Claude Code's own folder."""
    home = home or kuser.home_dir()
    return (os.path.normcase(os.path.normpath(os.path.abspath(folder)))
            == os.path.normcase(os.path.normpath(os.path.join(home, '.claude'))))


def find_claude():
    return shutil.which('claude')


def env_for(folder, home=None, environ=None):
    env = dict(os.environ if environ is None else environ)
    if is_default(folder, home):
        env.pop(CONFIG_DIR_ENV, None)
    else:
        env[CONFIG_DIR_ENV] = folder
    return env


class Runner(object):
    """`claude <args>` in one account folder. `run_proc` is subprocess.run (a test passes its own)."""

    def __init__(self, home=None, claude=None, run_proc=None, timeout=TIMEOUT, environ=None):
        self.home = home
        self.claude = claude if claude is not None else find_claude()
        self.run_proc = run_proc or subprocess.run
        self.timeout = timeout
        self.environ = environ

    def available(self):
        return bool(self.claude)

    def run(self, args, folder):
        """(rc, output text); rc None when claude could not start or ran past the timeout."""
        try:
            r = self.run_proc([self.claude] + list(args), env=env_for(folder, self.home, self.environ),
                              stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, timeout=self.timeout,
                              creationflags=_NOWIN)
        except subprocess.TimeoutExpired:
            return None, 'claude ran past %d s' % self.timeout
        except OSError as e:
            return None, 'cannot run claude: %s' % e
        out = r.stdout or b''
        text = out.decode('utf-8', 'replace') if isinstance(out, bytes) else out
        return r.returncode, text


def run_claude(args, config_dir, home=None):
    """One `claude <args>` against `config_dir`: (rc, text)."""
    return Runner(home).run(args, config_dir)
