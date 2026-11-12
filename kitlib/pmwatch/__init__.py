"""kitlib.pmwatch: the session checks the hub's session-watch job runs (docs/session-watch.md;
kitlib.hubjobs.session_watch).

Each check turns the previous state and a fresh reading into event lines; the job polls a Watcher
and pushes the lines to the PM. Event lines and state keys keep the names the retired
standalone watcher gave them. Transcript reading is host-specific and lives in hosts/claude/adapter/claude_host.py.
The package keeps its name for the machine config's `pm_watch` key (KEY: sessions_file, alerts_dir,
state_file), which the job, kitlib.pmstart and kitlib.hubsite.push read through it.
"""
import json
import os
import re
import time

from kitlib.config import pm as pmconfig
from kitlib.config import user as kuser

KEY = "pm_watch"
WEEK_BANDS = (95, 98, 99)
RESET_DROP = 20                   # weekly points a reading must fall to count as a reset (twice running)
FIVE_BANDS = (95, 98, 99)         # only read when the host gives a five_hour figure (it may be null)
ACCOUNT = r"account (\d+|codex): "
LINE = re.compile(ACCOUNT + r"(?:five_hour=(reset|\d+|\?)%?.*?)?seven_day=(reset|\d+|\?)%?")
NO_FILE = re.compile(ACCOUNT + r"no usage (?:file|data)")
AGE = re.compile(r"\[updated (\d+|\?) min ago\]")
STALE_MINUTES = 60               # a reading older than this is shown as stale (docs/launcher.md "Usage")


def load_config(path=None):
    """The "pm_watch" dict from the user-profile config; {} when the file or key is missing. A file
    that cannot be used raises kitlib.config.ConfigError (kitlib.config.pm.section)."""
    return pmconfig.section(KEY, path) or {}


def default_state_file():
    base = os.environ.get("LOCALAPPDATA") or kuser.home_dir()
    return os.path.join(base, "pm_watch", "state.json")


def load_state(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def save_state(path, s):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(s, f)
    os.replace(tmp, path)


def _number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def parse_usage(text):
    """{account: {"five", "week", "age", "stale", "missing"}} from kitlib.launcher.usage's lines.
    five and week are whole percentages, 0 for a window that has reset, and None when the reading
    is unknown (`?`, or no usage file): unknown is never 0. age is the reading's age in minutes
    (None when unknown); stale is True when it is older than STALE_MINUTES or of unknown age;
    missing is True for an account with no usage file. RuntimeError when no account line at all."""
    out = {}
    conv = lambda v: None if v == "?" else 0 if v == "reset" else int(v)
    for line in text.splitlines():
        m = LINE.search(line)
        if m:
            a = AGE.search(line)
            age = int(a.group(1)) if a and a.group(1) != "?" else None
            out[m.group(1)] = {"five": conv(m.group(2)) if m.group(2) else None, "week": conv(m.group(3)), "age": age,
                               "stale": age is None or age > STALE_MINUTES, "missing": False}
            continue
        m = NO_FILE.search(line)
        if m:
            out[m.group(1)] = {"five": None, "week": None, "age": None, "stale": True, "missing": True}
    if not out:
        raise RuntimeError("the usage report printed no account lines")
    return out


def launcher_configured(home=None):
    """True when the machine config has a `launcher` key, so in-process usage reads the accounts
    it names; without one it would read only the default ~/.claude account. A config that cannot
    be read counts as no key."""
    from kitlib.config import user as kuser
    from kitlib.config.types import ConfigError
    try:
        return kuser.user_config(home).get("launcher") is not None
    except ConfigError:
        return False


def usage(report=None):
    """Every account's usage, read in this process (kitlib.launcher.usage, from the launcher
    settings of the machine config). `report` replaces that reader (a callable giving the lines)."""
    if report is None:
        from kitlib.launcher import settings as launcher_settings, usage as launcher_usage
        report = lambda: launcher_usage.report(launcher_settings.load())
    return parse_usage("\n".join(report()))


def band(v, bands):
    return max([b for b in bands if v >= b], default=0)


def usage_events(prev, cur):
    """Band and reset events between two parse_usage readings; a window unknown (None) in either
    is skipped, never read as 0. A weekly drop of RESET_DROP points or more is a RESET only when the
    stored reading already carries `drop` (merge_usage: the previous poll fell as far too), so one
    stray low reading between two high ones raises nothing."""
    ev = []
    for acct, u in cur.items():
        p = prev.get(acct)
        if p is None:
            continue
        known = lambda k: u.get(k) is not None and p.get(k) is not None
        if known("week"):
            wb, pwb = band(u["week"], WEEK_BANDS), band(p["week"], WEEK_BANDS)
            if wb > pwb:
                ev.append("USAGE account %s seven_day %d%% (now %d%%)" % (acct, wb, u["week"]))
        if known("five"):
            fb, pfb = band(u["five"], FIVE_BANDS), band(p["five"], FIVE_BANDS)
            if fb > pfb:
                ev.append("USAGE account %s five_hour %d%% (now %d%%)" % (acct, fb, u["five"]))
        if known("week") and p["week"] - u["week"] >= RESET_DROP and p.get("drop") is not None:
            ev.append("RESET account %s (weekly %d%% -> %d%%)" % (acct, p["week"], u["week"]))
    return ev


def merge_usage(prev, cur):
    """The watcher's stored usage after a reading: cur over prev, but a window cur does not know
    (None) keeps prev's known figure, so a band crossed while a reading was missing still fires."""
    out = dict(prev)
    for acct, u in cur.items():
        p = prev.get(acct) or {}
        out[acct] = {**u, **{k: p[k] for k in ("five", "week") if u.get(k) is None and p.get(k) is not None}}
        if u.get("week") is not None and p.get("week") is not None and p["week"] - u["week"] >= RESET_DROP:
            if p.get("drop") is None:       # the first low reading: held, not stored (usage_events)
                out[acct] = {**out[acct], "week": p["week"], "drop": u["week"]}
    return out


def alert_events(seen, alerts_dir):
    """(events, seen now): one BGFAIL per alert file not in seen. An unreadable file (mid-write)
    is left unseen for the next poll; names no longer in the folder drop out of seen."""
    try:
        names = sorted(n for n in os.listdir(alerts_dir) if n.endswith(".json"))
    except FileNotFoundError:
        return [], []
    ev, now = [], []
    for n in names:
        if n in seen:
            now.append(n)
            continue
        try:
            with open(os.path.join(alerts_dir, n), encoding="utf-8") as f:
                a = json.load(f)
        except (OSError, ValueError):
            continue
        reason = " ".join(str(a.get("reason") or a.get("how") or "failed").split())
        ev.append("BGFAIL run %s %s" % (a.get("id") or n[:-5], reason))
        now.append(n)
    return ev, now


# Lock-window notices (kitlib.lock.notice writes them): <dir>/<run id>-start|end.json, each a
# doc with its id; the hub server lists them (kitlib.hubsite.derived). A subfolder is not read.


def configured_notice_dir(path=None):
    """machine_lock.notice.dir of the user-profile config, or None. A file that cannot be used
    raises kitlib.config.ConfigError (kitlib.config.pm.section)."""
    notice = (pmconfig.section("machine_lock", path) or {}).get("notice")
    d = notice.get("dir") if isinstance(notice, dict) else None
    return d if isinstance(d, str) and d else None


def pending_notices(d):
    """[(file name, doc)] of the notices in d not yet posted, oldest first. An unreadable file
    (one being written) is left for the next look."""
    try:
        names = sorted(n for n in os.listdir(d) if n.endswith(".json"))
    except FileNotFoundError:
        return []
    out = []
    for n in names:
        try:
            with open(os.path.join(d, n), encoding="utf-8") as f:
                doc = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(doc, dict) and doc.get("id"):
            out.append((n, doc))
    out.sort(key=lambda x: (str(x[1].get("at") or ""), x[0]))
    return out


def notice_events(seen, notice_dir):
    """(events, seen now): one LOCKNOTICE per notice in notice_dir not in seen (one that is
    gone drops out of seen)."""
    ev, now = [], []
    for n, doc in pending_notices(notice_dir):
        if n not in seen:
            ev.append("LOCKNOTICE %s %s" % (doc["id"], " ".join(str(doc.get("title") or "").split())))
        now.append(n)
    return ev, now


def watched_sessions(sessions):
    """Sorted names of the sessions.json entries to watch: every entry with a name (coordinator or
    plain), except autostart=false. Empty if the file is unreadable."""
    try:
        with open(sessions, encoding="utf-8-sig") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return []
    return sorted({v["name"] for v in d.values() if isinstance(v, dict) and v.get("name")
                   and v.get("autostart") is not False})


_STOP_MARK, _SUCC_MARK = " stopped '", "; successor '"


def _stop_entry(line):
    """(stamp, name, successor) of a stop.log line kitlib.launcher.stop wrote, else None."""
    if line[19:19 + len(_STOP_MARK)] != _STOP_MARK or _SUCC_MARK not in line:
        return None
    name = line[19 + len(_STOP_MARK):].split("'", 1)[0]
    return line[:19], name, line.rsplit(_SUCC_MARK, 1)[1].split("'", 1)[0]


def stop_log_reader(sessions):
    """stopped(name, mtime) -> the epoch time of the launcher stop that ended the session `name`
    with no successor, or None. Reads stop.log beside the sessions file (kitlib.launcher.stop writes
    one line per stop: "<stamp> stopped '<name>' ...; successor '<name or ->'"). Only the newest stop
    of the name counts, and only when it is not older than `mtime`, the session's newest
    activity: later activity means it was started again. A missing or unreadable log gives None."""
    if not sessions:
        return lambda name, mtime: None
    log = os.path.join(os.path.dirname(os.path.abspath(sessions)), "stop.log")

    def stopped(name, mtime):
        last = None
        try:
            with open(log, encoding="utf-8-sig") as f:
                for line in f:
                    e = _stop_entry(line)
                    if e and e[1] == name:
                        last = (e[0], e[2])
        except OSError:
            return None
        if last is None or last[1] != "-":
            return None
        try:
            t = time.mktime(time.strptime(last[0], "%Y-%m-%d %H:%M:%S"))
        except (ValueError, OverflowError):
            return None
        return t if t + 1 > mtime else None        # the stamp is whole seconds, mtime is not
    return stopped


def idle_baseline(names, newest, now, minutes, stopped=None):
    """{name: marker} for sessions already quiet or stopped, so a relaunch-free first run stays
    silent. The marker is the newest activity time, or [time, "stopped"] for a stopped session.
    newest(name) is the newest activity time of that session, or None (hosts.claude.adapter.claude_host.newest_finder)."""
    out = {}
    for n in names:
        m = newest(n)
        if m is None:
            continue
        if stopped and stopped(n, m) is not None:
            out[n] = [m, "stopped"]
        elif now - m >= minutes * 60:
            out[n] = m
    return out


def idle_events(names, newest, fired, nolog, now, minutes, stopped=None):
    """(events, fired now, nolog now). IDLE once per quiet spell: fired maps name -> the mtime it
    fired at, and a session drops out of it when active again. A session the launcher stopped with
    no successor (`stopped`, stop_log_reader) gives STOPPED once, never IDLE. A name with no
    transcript gives one ERROR, then stays quiet (nolog) until it has a transcript."""
    ev, cur, miss = [], {}, []
    for n in names:
        m = newest(n)
        if m is None:
            miss.append(n)
            if n not in nolog:
                ev.append("ERROR idle: no transcript for %s" % n)
            continue
        t = stopped(n, m) if stopped else None
        if t is not None:
            cur[n] = [m, "stopped"]
            if fired.get(n) != cur[n]:
                ev.append("STOPPED %s at %s" % (n, time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t))))
        elif now - m >= minutes * 60:
            cur[n] = m
            if fired.get(n) != m:
                ev.append("IDLE %s %dm since %s" % (n, (now - m) // 60,
                          time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(m))))
    return ev, cur, miss


SESSION_JOB = "session-watch"     # the hub's job that runs the session checks (kitlib.hubjobs.session_watch)
SESSION_JOB_FAILS = 3             # failures in a row that end its cover (kitlib.hubsite.jobs FAIL_PUSH)
SESSION_JOB_STALE_S = 300         # its last success older than this: not watching


def _count(v):
    return type(v) is int and v >= 0


def session_job_healthy(block, now):
    """True when a session-watch health block (GET health jobs.session-watch) shows the job
    watching: not failing (`failures` below SESSION_JOB_FAILS) and a success (`ok_at`) at most
    SESSION_JOB_STALE_S before `now`. A skipping job (no sessions file) has no fresh success, so
    it does not count. Anything else is False."""
    if not isinstance(block, dict) or not _count(block.get("failures")) \
            or block["failures"] >= SESSION_JOB_FAILS:
        return False
    ok = block.get("ok_at")
    return _number(ok) and now - ok <= SESSION_JOB_STALE_S


RC_GRACE_S = 180     # a session this young may not have connected its Remote Control link yet


def rc_key(r):
    """The rc_lost entry of a session record: name, pid and creation time, so a session stopped and
    started again under the same name is a new one."""
    return "%s %s %s" % (r["name"], r.get("pid"), r.get("proc_start"))


def _newest_live(names, records, alive):
    """{name: its newest record whose process runs} of the watched names."""
    newest = {}
    for r in records:
        n, started = r.get("name"), r.get("started_ms")
        if not isinstance(n, str) or n not in names or isinstance(started, bool) \
                or not isinstance(started, (int, float)):
            continue
        if (n not in newest or started > newest[n]["started_ms"]) and alive(r):
            newest[n] = r
    return newest


def rc_events(names, records, alive, rc_pids, lost, now):
    """(events, lost now) of the Remote Control link check (docs/pm-watch.md "Remote Control link").

    records: the host's session records, dicts {name, pid, proc_start, started_ms, linked}, linked
    None when the host's record says nothing about the link. alive(rec): its pid runs now with that
    creation time. rc_pids(): {pid: --remote-control name} of the running sessions, read only when
    some session looks unlinked. lost: the rc_key of each session already reported. A watched name
    whose newest live record says unlinked, started more than RC_GRACE_S ago, and whose process was
    started with --remote-control under that name gives REMOTE ... lost once; the same process read
    linked again gives REMOTE ... back once. One too young or silent on the link keeps its earlier
    state, so neither line repeats; a session that is gone, or runs as another process now (a stop
    and a relaunch), drops out, so the new one starts clean. A record whose name is not text is
    skipped."""
    newest = _newest_live(names, records, alive)
    ev, cur, unlinked = [], [], []
    for n in sorted(names):
        r = newest.get(n)
        if r is None:
            continue
        key = rc_key(r)
        if r.get("linked") is None or now - r["started_ms"] / 1000.0 < RC_GRACE_S:
            if key in lost:
                cur.append(key)
        elif r["linked"]:
            if key in lost:
                ev.append("REMOTE %s link back" % n)
        else:
            unlinked.append(r)
    pids = rc_pids() if unlinked else {}
    for r in unlinked:
        n, key = r["name"], rc_key(r)
        if pids.get(r["pid"]) != n:                 # started without --remote-control: no link to lose
            continue
        cur.append(key)
        if key not in lost:
            ev.append("REMOTE %s link lost: the session runs, its Remote Control link is down "
                      "(pid %s)" % (n, r["pid"]))
    return ev, sorted(cur)


def at_limit(u):
    """True when an account's stored reading says it can be at its weekly limit: 100%, or 99% and
    stale (the file refreshes only while a session works, so a stalled account's reading can stop
    short of 100 and age). A reading unknown (None) or under that is False."""
    week = u.get("week")
    return week is not None and (week >= 100 or (week >= 99 and bool(u.get("stale"))))


def limit_events(names, records, alive, folders, readings, fired, now):
    """(events, stalled now) of the usage-limit check (docs/pm-watch.md "Usage-limit stall").

    records and alive as rc_events; a record's weekly_stop(folders) gives (the config folder holding
    the session's transcript, the epoch time the weekly limit that stopped the session resets), or
    None; a record without it (a host with no reader) is skipped. folders: {config folder: account}
    (account_folders). readings: the Watcher's stored usage, by account. fired: the entries already
    reported. A watched session with a live process, on an account at_limit, whose stop lies ahead
    gives LIMIT once; the entry holds the process and the reset, and one that recovers drops out,
    so only a new stall fires again."""
    ev, cur = [], []
    newest = _newest_live(names, records, alive)
    for n in sorted(newest):
        r = newest[n]
        stop = r["weekly_stop"](list(folders)) if r.get("weekly_stop") else None
        if stop is None:
            continue
        account, reset = folders.get(stop[0]), stop[1]
        if reset <= now or not at_limit(readings.get(account) or {}):
            continue
        key = "%s %s" % (rc_key(r), reset)
        cur.append(key)
        if key not in fired:
            ev.append("LIMIT %s account %s stalled at the weekly limit until %s (pid %s)"
                      % (n, account, time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(reset)),
                         r["pid"]))
    return ev, sorted(cur)


def account_folders(settings):
    """{config folder: account} of the launcher's accounts. The registry folder says nothing of the
    account (the accounts share one real folder); the transcript's config folder does. An org's
    folder is left out: it has no usage reading, so a session there gets no LIMIT."""
    return {d: n for n in settings.account_numbers() for d in settings.account_dirs(n)}


def limit_check(names, records, alive, accounts):
    """The LIMIT check for Watcher: check(fired, readings, now) -> (lines, fired), over limit_events.
    names() the watched names, records() the host's session records, accounts() account_folders.
    Those are read only while some account is at_limit, so the check costs nothing otherwise; a
    pass with none clears `fired`."""
    def check(fired, readings, now):
        if not any(at_limit(u) for u in readings.values()):
            return [], []
        return limit_events(names(), records(), alive, accounts(), readings, list(fired or []), now)
    return check


def rc_check(sessions, reader, dirs=None, ctime=None, rc_pids=None, accounts=None):
    """The REMOTE check for Watcher: check(lost) -> (lines, lost), over rc_events for the names in
    sessions.json (watched_sessions) and the PM's (kitlib.launcher.stop.PM_RC_NAME: the PM has
    no sessions.json entry). reader(dirs) gives the host's session records
    (on Claude, hosts.claude.adapter.claude_host.rc_records); dirs() the registry folders (default:
    every configured account's, kitlib.launcher.pipes.registry_dirs, read again on each call);
    ctime(pid) the process creation time; rc_pids() the running sessions' --remote-control names.
    The imports happen here, when the watcher is built, so a landing cannot mix modules later.
    The check's `limit` attribute is the LIMIT check over the same sessions and records (limit_check;
    accounts() default: account_folders of the launcher settings, read again on each call), which
    the Watcher picks up, so every builder of the REMOTE check gets LIMIT too."""
    from kitlib.launcher import lookup, pipes, settings as launcher_settings, stop
    from kitlib.proc import listing, process
    if dirs is None:
        dirs = lambda: pipes.registry_dirs(launcher_settings.load())
    ctime = ctime or process.proc_ctime
    if rc_pids is None:
        # every process: only the pids of live registry records are looked up, so no image filter
        rc_pids = lambda: {p.get("ProcessId"): lookup.remote_control_name(p.get("CommandLine"))
                           for p in listing.list_processes()}

    def alive(r):
        try:
            now = ctime(r.get("pid"))
        except (OSError, ValueError, TypeError):
            return False
        return now is not None and r.get("proc_start") is not None and str(now) == str(r["proc_start"])

    def names():
        return set(watched_sessions(sessions)) | {stop.PM_RC_NAME}

    def check(lost):
        return rc_events(names(), reader(dirs()), alive, rc_pids, list(lost or []), time.time())
    check.limit = limit_check(names, lambda: reader(dirs()), alive,
                              accounts or (lambda: account_folders(launcher_settings.load())))
    return check


def preload(modules=()):
    """Load, at start, every kit module a check imports on its first pass, so a landing that writes
    this checkout later cannot give the running watcher a mix of old and new modules
    (kitlib.launcher.usage at the first usage read). `modules` names the host's own ones, which the
    session-watch job passes in (the host adapter's PRELOAD), so no host is named here. The
    function-level imports stay where they are; they find these loaded."""
    import importlib
    import kitlib.launcher.settings
    import kitlib.launcher.usage
    import kitlib.launcher.pipes            # the REMOTE check (rc_check)
    import kitlib.launcher.stop
    import kitlib.proc.process
    for name in modules:
        importlib.import_module(name)


class Watcher:
    """One watcher run. start() records baselines; poll(now) runs the checks that are due and
    returns their event lines. The caller saves state after each poll. Every check gives an ERROR
    line on its third failure in a row.

    usage_reader() -> parse_usage's {account: {"five", "week", "age", "stale", "missing"}} (five
    or week None: unknown);
    sessions: path of sessions.json; newest(name) -> mtime or None;
    rc: check(lost) -> (lines, lost) (rc_check), or None for no REMOTE check; on the idle cadence.
    limit: check(fired, readings, now) -> (lines, fired) (limit_check), or None for rc's own `limit`,
    if it has one; on the idle cadence, after the usage check. Like REMOTE it has no baseline.
    preload_modules: the host's own modules its checks import late (preload).
    stopped: stopped(name, mtime) -> time or None (stop_log_reader over stop.log beside `sessions`).
    """

    def __init__(self, state, usage_reader, alerts_dir, sessions, newest, idle_minutes=20,
                 usage_poll=600, idle_poll=300, notice_dir=None, preload_modules=(), stopped=None,
                 rc=None, limit=None):
        preload(preload_modules)
        self.stopped = stop_log_reader(sessions) if stopped is None else stopped
        self.s = state
        self.usage_reader = usage_reader
        self.alerts_dir = alerts_dir
        self.sessions = sessions
        self.newest = newest
        self.idle_minutes = idle_minutes
        self.usage_poll, self.idle_poll = usage_poll, idle_poll
        self.notice_dir = notice_dir
        self.rc = rc
        self.limit = limit or getattr(rc, "limit", None)
        self.fails = {"usage": 0, "alerts": 0, "idle": 0, "notices": 0, "rc": 0, "limit": 0}
        self.next_u = self.next_i = self.next_r = self.next_l = 0.0

    def start(self, now):
        s = self.s
        if "usage" not in s:
            try:
                s["usage"] = self.usage_reader()
            except Exception:                       # noqa: BLE001 - unreadable: the next poll sets it
                pass
        if "bg_alerts" not in s:                    # first run: existing alerts are baseline
            s["bg_alerts"] = alert_events([], self.alerts_dir)[1]
        if self.notice_dir and "lock_notices" not in s:
            s["lock_notices"] = notice_events([], self.notice_dir)[1]
        if "idle" not in s:                         # first run: sessions already quiet are baseline
            s["idle"] = idle_baseline(watched_sessions(self.sessions), self.newest, now,
                                      self.idle_minutes, self.stopped)

    def _failed(self, what, e, events):
        self.fails[what] += 1
        if self.fails[what] == 3:
            events.append("ERROR %s check: %s" % (what, e))

    def poll(self, now):
        s, events = self.s, []
        if now >= self.next_u:
            self.next_u = now + self.usage_poll
            try:
                cur = self.usage_reader()
                self.fails["usage"] = 0
                events += usage_events(s.get("usage", {}), cur)
                s["usage"] = merge_usage(s.get("usage", {}), cur)
            except Exception as e:
                self._failed("usage", e, events)
        if now >= self.next_i:
            self.next_i = now + self.idle_poll
            try:
                ev, s["idle"], s["idle_nolog"] = idle_events(
                    watched_sessions(self.sessions), self.newest, s.get("idle", {}),
                    s.get("idle_nolog", []), now, self.idle_minutes, self.stopped)
                events += ev
                self.fails["idle"] = 0
            except Exception as e:
                self._failed("idle", e, events)
        try:                                        # every loop: a listdir is cheap
            ev, s["bg_alerts"] = alert_events(set(s.get("bg_alerts", [])), self.alerts_dir)
            events += ev
            self.fails["alerts"] = 0
        except Exception as e:
            self._failed("alerts", e, events)
        if self.notice_dir:
            try:
                ev, s["lock_notices"] = notice_events(set(s.get("lock_notices", [])),
                                                      self.notice_dir)
                events += ev
                self.fails["notices"] = 0
            except Exception as e:
                self._failed("notices", e, events)
        if self.rc and now >= self.next_r:          # on the idle cadence: a folder of small files
            self.next_r = now + self.idle_poll
            try:
                events_rc, s["rc_lost"] = self.rc(s.get("rc_lost", []))
                events += events_rc
                self.fails["rc"] = 0
            except Exception as e:
                self._failed("rc", e, events)
        if self.limit and now >= self.next_l:       # on the idle cadence: nothing read below 99%
            self.next_l = now + self.idle_poll
            try:
                ev, s["limit_stalled"] = self.limit(s.get("limit_stalled", []), s.get("usage", {}), now)
                events += ev
                self.fails["limit"] = 0
            except Exception as e:
                self._failed("limit", e, events)
        return events
