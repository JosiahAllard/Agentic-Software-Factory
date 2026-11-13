"""Where each launched session's peer pipe was seen (docs/launcher.md "Pipe addresses").

Every live session publishes a registry record, <config folder>/sessions/<pid>.json, holding its
pid, sessionId, startedAt (ms), procStart (or procStartFt: either may carry the process creation
time), entrypoint, messagingSocketPath and the name the launcher gave it. A secret key file sits
beside it; nothing here opens it.

  registry_dirs(settings)   the registry folders of every configured account, each once
  records(dirs)             (folder, record) of every <digits>.json that reads as an object
  start_text(value)         one procStart value as comparable text, or None: a whole number's
                            digits; on Linux also an lstart text (below), its runs of blanks as one
  proc_start(rec)           the record's creation time as text (start_text of procStart, else of
                            procStartFt), or None
  live_ctime(rec, ctime)    the kit's creation time of the record's process (ctime(pid)) when its
                            pid runs now with the record's creation time, else None
  is_live(rec, ctime)       live_ctime is not None (a stopped session's record can stay behind, so
                            a record alone is never proof)
  find(dirs, name, after_ms, ctime)   the newest live record of that name started at or after
                            after_ms, or None
  await_record(...)         poll for a just-started session's record (one a test accepts)
  write_entry(...)          write a record's pipes.json entry
  record(...)               poll for a just-started session's record and write its pipes.json
                            entry; the line to say when it is not found in time
  remove(settings, name)    drop a stopped session's entry

pipes.json, in launcher.state_dir: {name: {pid, session_id, pipe, proc_start, entrypoint,
recorded_at}}. It is a hint and an audit, never the truth: the pipe changes on every restart, so a
sender resolves the live record at send time. No token is ever written. Two launchers writing at
once can drop one entry (the last os.replace wins); the sender's registry scan still finds it.

On Windows procStart is the process's FILETIME creation time, the kit's ctime itself. On Linux
Claude Code writes `LC_ALL=C TZ=UTC ps -o lstart= -p <pid>` trimmed at the ends ("Thu Nov  5
18:29:49 2026"): a UTC second, which live_ctime matches against the kit's ctime through
kitlib.platform.linux.process.wall_start (btime plus starttime ticks, floored; the reference VM
showed the two equal for every process, docs/linux-support-plan.md). A pid reused within the same
second is the one case that match cannot tell apart, and nothing later catches it: the socket
write's ctime check (kitlib.platform.linux.unixsock) compares against the ctime live_ctime
returned, so it confirms only that the pid did not change after this match.
"""
import calendar
import datetime
import os
import re

from kitlib import platform
from kitlib.launcher import state
from kitlib.launcher.settings import LaunchError

PIPES = 'pipes.json'
REGISTRY = 'sessions'           # the registry folder under each account's config folder
POLL_S = 1.0
TRIES = 20                      # 20 polls, 1 s apart: at most about 20 s after the window opens
NOT_YET = 'pipe not recorded yet: the hub resolves it at send time'
_RECORD = re.compile(r'^[0-9]{1,10}\.json\Z')


def registry_dirs(settings):
    """The registry folders that exist: the default config folder's, then each account's and
    org's, the same folder (a junction, a repeated entry) listed once."""
    cands = [settings.default_folder()]
    for n in settings.account_numbers():
        cands += settings.account_dirs(n)
    for n in sorted(settings.orgs, key=int):
        cands.append(settings.org_dir(n))
    seen, out = set(), []
    for c in cands:
        d = os.path.join(c, REGISTRY)
        if not os.path.isdir(d):
            continue
        key = os.path.normcase(os.path.realpath(d))
        if key not in seen:
            seen.add(key)
            out.append(d)
    return out


def records(dirs):
    """[(folder, record)] of every <digits>.json in dirs that reads as a JSON object."""
    out = []
    for d in dirs:
        try:
            names = sorted(os.listdir(d))
        except OSError:
            continue
        for n in names:
            if not _RECORD.match(n):
                continue
            try:
                rec = state.read_json(os.path.join(d, n))
            except LaunchError:
                continue
            if isinstance(rec, dict):
                out.append((d, rec))
    return out


_DAYS = ('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')
_MONTHS = ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')
_LSTART = re.compile(r'([A-Z][a-z]{2}) ([A-Z][a-z]{2}) ([0-9]{1,2}) '
                     r'([0-9]{2}):([0-9]{2}):([0-9]{2}) ([0-9]{4})\Z')
_DIGITS = re.compile(r'[0-9]+\Z')


def _lstart_seconds(text):
    """The epoch second of an lstart text read as UTC ("Thu Nov  5 18:29:49 2026": English day
    and month names whatever the locale, the weekday checked), or None when it is not one."""
    if not isinstance(text, str):
        return None
    m = _LSTART.match(' '.join(text.split()))
    if m is None or m.group(1) not in _DAYS or m.group(2) not in _MONTHS:
        return None
    try:
        when = datetime.datetime(int(m.group(7)), _MONTHS.index(m.group(2)) + 1, int(m.group(3)),
                                 int(m.group(4)), int(m.group(5)), int(m.group(6)))
    except ValueError:
        return None
    if when.weekday() != _DAYS.index(m.group(1)):
        return None
    return calendar.timegm(when.timetuple())


def start_text(value):
    """One procStart value as the text records are compared by: a whole number (or its digits)
    as its digits; on Linux an lstart text as itself with each run of blanks made one; None for
    anything else (a bool, a fraction, other text, an lstart text off Linux)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    if not isinstance(value, str):
        return None
    if _DIGITS.match(value):
        return value
    if platform.NAME == 'linux' and _lstart_seconds(value) is not None:
        return ' '.join(value.split())
    return None


def proc_start(rec):
    """The record's process creation time as text: procStart, else procStartFt (start_text);
    None when neither is one."""
    for k in ('procStart', 'procStartFt'):
        v = start_text(rec.get(k))
        if v is not None:
            return v
    return None


def live_ctime(rec, ctime):
    """The kit's creation time (an int) of the record's process when its pid runs now and
    ctime(pid) matches the record's proc_start: equal digits, or on Linux an lstart text naming the
    second wall_start gives that ctime. None otherwise."""
    pid = rec.get('pid')
    start = proc_start(rec)
    if type(pid) is not int or not 0 < pid <= 0xffffffff or start is None:
        return None
    try:
        now = ctime(pid)
    except (OSError, ValueError):
        return None
    if now is None:
        return None
    if _DIGITS.match(start):
        return int(start) if str(now) == start else None
    if type(now) is not int:
        return None
    from kitlib.platform.linux import process as linux_process
    secs = _lstart_seconds(start)
    return now if secs is not None and linux_process.wall_start(now) == secs else None


def is_live(rec, ctime):
    """True when the record's pid runs now with the record's creation time (live_ctime)."""
    return live_ctime(rec, ctime) is not None


def find(dirs, name, after_ms, ctime):
    """The live record named `name` whose startedAt is at or after after_ms; the newest
    startedAt when several are; None when there is none."""
    hits = []
    for _d, rec in records(dirs):
        started = rec.get('startedAt')
        if rec.get('name') != name or isinstance(started, bool) or not isinstance(started, (int, float)):
            continue
        if started >= after_ms and is_live(rec, ctime):
            hits.append(rec)
    return max(hits, key=lambda r: r['startedAt']) if hits else None


def entry(rec, now):
    """The pipes.json entry of a registry record: no token, no free text but the names."""
    return {'pid': rec.get('pid'), 'session_id': rec.get('sessionId'),
            'pipe': rec.get('messagingSocketPath'), 'proc_start': proc_start(rec),
            'entrypoint': rec.get('entrypoint'), 'recorded_at': now.isoformat(timespec='seconds')}


def await_record(settings, name, after_ms, ctime, sleep, tries=TRIES, poll_s=POLL_S, accept=None):
    """Poll the registry up to `tries` times, poll_s apart, for the live record of the session
    `name` started at or after after_ms (find) that accept(record) passes (any, when accept is
    None): the record, or None when none came in time."""
    for i in range(max(1, tries)):
        rec = find(registry_dirs(settings), name, after_ms, ctime)
        if rec is not None and (accept is None or accept(rec)):
            return rec
        if i < tries - 1:
            sleep(poll_s)
    return None


def write_entry(settings, name, rec, now):
    """Write the pipes.json entry of `name` from its registry record: None when written (or when
    launcher.state_dir is unset), else the one line to say."""
    try:
        p = state.path(settings, PIPES)
    except LaunchError:
        return None
    try:
        data = state.read_json(p) or {}
        data[name] = entry(rec, now())
        state.write_json(p, data)
    except (OSError, LaunchError) as e:
        return 'pipe not recorded (%s): the hub resolves it at send time' % e
    return None


def record(settings, name, after_ms, ctime, sleep, now, tries=TRIES, poll_s=POLL_S):
    """Poll the registry for the session `name` started at or after after_ms (epoch ms) and
    write its pipes.json entry: None when written (or when launcher.state_dir is unset: there is
    nowhere to write), else the one line to say. ctime(pid) is the process creation time, sleep(s)
    waits, now() is the aware time recorded."""
    try:
        state.path(settings, PIPES)
    except LaunchError:
        return None
    rec = await_record(settings, name, after_ms, ctime, sleep, tries, poll_s)
    if rec is None:
        return NOT_YET
    return write_entry(settings, name, rec, now)


def remove(settings, name):
    """Drop the pipes.json entry of `name`: True when one was removed. Nothing to do (no
    state_dir, no file, no entry) is False; a file that cannot be read or written is a
    LaunchError or an OSError for the caller to say."""
    try:
        p = state.path(settings, PIPES)
    except LaunchError:
        return False
    data = state.read_json(p)
    if not data or name not in data:
        return False
    del data[name]
    state.write_json(p, data)
    return True


def read(settings):
    """pipes.json as {name: entry}; {} when there is none or no state_dir."""
    try:
        return state.read_json(state.path(settings, PIPES)) or {}
    except LaunchError:
        return {}


def launch_ms(when):
    """An aware datetime as epoch milliseconds (the registry's startedAt unit)."""
    return when.timestamp() * 1000.0
