"""CLAUDE-SPECIFIC SEAM of kitlib.pmwatch, the hub session-watch job's checks (ARCHITECTURE "Host split").

Everything here knows how Claude Code lays out its files: the ~/.claude* config folders, their
projects/<folder>/<id>.jsonl transcripts with subagent transcripts under <id>/, the
"custom-title" record that names a session, the session registry's bridgeSessionId field (the
Remote Control link), the CLI's usage-limit API error entry, and the machine-lock alerts folder
under ~/.claude.
Moved here from kitlib/pmwatch by the host split (step 1); step 2 puts it behind SessionHost.
Nothing in kitlib.pmwatch reads these paths or formats. The launcher's account reads take the
login in .credentials.json (login_of) and the sign-in command (LOGIN) from here too, through
kitlib.host.registry.host_module.
"""
import functools
import glob
import json
import os

# The kit modules of Claude's own that the watcher's checks import late (kitlib.pmwatch.preload):
# the launcher's account reads (kitlib.pmwatch account_folders, for the LIMIT and REMOTE checks).
PRELOAD = ("kitlib.launcher.accounts",)

TAIL_BYTES = 64 * 1024              # newest_entry reads this much: a limit entry is about 1.5 KB
# The entry types that are the session's own work. Others (title, prompt and index records) can
# follow a limit entry in the file while the session stands still.
CONVERSATION = ("user", "assistant")
# The only window that counts as a stall (the owner's rule: the five-hour window is ignored). Only
# "seven_day" has been seen in a real entry; no real five-hour entry has been, so its value is not
# known, and any other value, unknown ones too, reads as no stall.
WEEKLY = "seven_day"
UNREADABLE = object()               # newest_entry: the file's newest entry could not be told


def default_alerts_dir():
    return os.path.join(os.path.expanduser("~"), ".claude", "machine-lock", "alerts")


def project_roots(home=None):
    """The projects folder of every ~/.claude* config folder (one per signed-in account)."""
    h = home or os.path.expanduser("~")
    return [os.path.join(h, d, "projects") for d in sorted(os.listdir(h)) if d.startswith(".claude")]


CHUNK = 1024 * 1024                 # session_titles reads a transcript this much at a time
_TITLES = {}                        # path -> (end of the last complete line read, titles)


def session_titles(path):
    """Every name the session's main transcript has recorded in a custom-title record: its launch
    name (-n/--remote-control), a --name given on resume and any later rename (the app, Remote
    Control), so a renamed session is still found under each. A file is read whole once, then only
    what is appended; a line not yet ended counts on a later call, and a file that shrank is read
    again from its start. A file that cannot be read gives the titles last read from it (empty if
    none) and changes nothing cached."""
    end, titles = _TITLES.get(path, (0, frozenset()))
    try:
        with open(path, "rb") as f:
            size = f.seek(0, os.SEEK_END)
            if size == end:
                return titles
            if size < end:
                end, titles = 0, frozenset()
            f.seek(end)
            found, carry = set(), b""
            while True:
                chunk = f.read(CHUNK)
                if not chunk:
                    break
                lines = (carry + chunk).split(b"\n")
                carry = lines.pop()
                end += sum(len(x) + 1 for x in lines)
                for line in lines:
                    if b'"custom-title"' not in line:
                        continue
                    try:
                        r = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(r, dict) and r.get("type") == "custom-title" \
                            and isinstance(r.get("customTitle"), str):
                        found.add(r["customTitle"])
    except OSError:
        return _TITLES.get(path, (0, frozenset()))[1]
    titles = titles | found
    _TITLES[path] = (end, titles)
    return titles


def subagent_transcripts(main):
    """The subagent transcripts of the main transcript <id>.jsonl, all under the folder <id>/."""
    return [os.path.join(dp, x) for dp, _, fs in os.walk(main[:-len(".jsonl")])
            for x in fs if x.endswith(".jsonl")]


def newest_transcript(name, roots):
    """Newest mtime across the named session's own transcripts: each main <id>.jsonl that has
    recorded name as a title (session_titles; in any project folder of any account; a junction
    reached twice counts once) and the subagent transcripts under <id>/. None if no main
    transcript carries the name."""
    seen, newest = set(), None
    for root in roots:
        try:
            folders = os.listdir(root)
        except OSError:
            continue
        for fo in folders:
            d = os.path.join(root, fo)
            try:
                files = os.listdir(d)
            except OSError:
                continue
            for fn in files:
                p = os.path.join(d, fn)
                if not fn.endswith(".jsonl"):
                    continue
                real = os.path.normcase(os.path.realpath(p))
                if real in seen or name not in session_titles(real):
                    continue
                seen.add(real)
                for q in [real] + subagent_transcripts(real):
                    try:
                        m = os.path.getmtime(q)
                    except OSError:
                        continue
                    newest = m if newest is None or m > newest else newest
    return newest


def newest_entry(path):
    """The newest conversation entry (CONVERSATION) of a transcript, reading only its last
    TAIL_BYTES. None: the file holds no such entry. UNREADABLE: it cannot be read, a line does not
    parse before one is found (one being written says nothing about the session yet), or the tail
    ends before one is found, so a longer line above it can hide the newest entry."""
    try:
        with open(path, "rb") as f:
            size = f.seek(0, os.SEEK_END)
            f.seek(max(0, size - TAIL_BYTES))
            tail = f.read(TAIL_BYTES)
    except OSError:
        return UNREADABLE
    lines = tail.split(b"\n")
    for raw in reversed(lines[1:] if size > TAIL_BYTES else lines):     # the first was cut
        if not raw.strip():
            continue
        try:
            entry = json.loads(raw)
        except ValueError:
            return UNREADABLE
        if isinstance(entry, dict) and entry.get("type") in CONVERSATION:
            return entry
    return UNREADABLE if size > TAIL_BYTES else None


def weekly_reset(entry):
    """resetsAt (epoch seconds) of the CLI's usage-limit API error at the weekly window, else None.
    Reads the structured fields only: the display text is free, and apiErrorStatus is absent from a
    subagent's entry. A resetsAt that is no number gives None."""
    if not isinstance(entry, dict) or entry.get("isApiErrorMessage") is not True \
            or entry.get("apiError") != "usage_limit_reached":
        return None
    params = entry.get("apiErrorParams")
    info = params.get("rate_limit_info") if isinstance(params, dict) else None
    if not isinstance(info, dict):
        info = entry.get("quotaLimits")
    if not isinstance(info, dict) or info.get("rateLimitType") != WEEKLY:
        return None
    reset = info.get("resetsAt")
    return reset if type(reset) in (int, float) else None


def _when(entry):
    stamp = entry.get("timestamp")
    return stamp if isinstance(stamp, str) else ""


def weekly_stop(session_id, folders):
    """(config folder, resets_at) when the newest thing the session did is a weekly usage-limit
    error, else None. The registry is one folder shared by the accounts but transcripts are per
    account, so `folders`, the config folders, are searched for <id>.jsonl; the copy written last
    is the session's (a session moved to another account keeps its id), and its folder says the
    account. A subagent's entries live in its own file under <id>/, not in the main transcript, so
    a main session waiting on a stalled subagent shows nothing in its own file: the subagent file
    written last is read too, and the later of the two newest entries (by their ISO timestamp, the
    same form in both; the main one on a tie) decides. So a subagent's limit entry counts only
    while neither the main session nor a later-written subagent has done anything since. Either
    file UNREADABLE gives None: the other cannot decide alone."""
    if not isinstance(session_id, str) or not session_id:
        return None
    mains = []
    for folder in folders:
        for p in glob.glob(os.path.join(glob.escape(folder), "projects", "*",
                                        glob.escape(session_id) + ".jsonl")):
            try:
                mains.append((os.path.getmtime(p), folder, p))
            except OSError:
                continue
    if not mains:
        return None
    _, folder, main = max(mains)
    subs = []
    for p in subagent_transcripts(main):
        try:
            subs.append((os.path.getmtime(p), p))
        except OSError:
            continue
    entries = [newest_entry(main)] + ([newest_entry(max(subs)[1])] if subs else [])
    if any(e is UNREADABLE for e in entries):
        return None
    found = [e for e in entries if e]
    reset = weekly_reset(max(found, key=_when)) if found else None
    return None if reset is None else (folder, reset)


def rc_records(dirs):
    """Claude's session registry records (<config folder>/sessions/<pid>.json, read by
    kitlib.launcher.pipes) in the form kitlib.pmwatch.rc_events and limit_events read: {name, pid,
    proc_start, started_ms, linked, weekly_stop}. linked is None when the record has no
    bridgeSessionId key, else whether it holds a Remote Control bridge session id. weekly_stop(folders)
    reads the transcript (weekly_stop), so only when asked."""
    from kitlib.launcher import pipes
    return [{"name": rec.get("name"), "pid": rec.get("pid"), "proc_start": pipes.proc_start(rec),
             "started_ms": rec.get("startedAt"),
             "linked": bool(rec["bridgeSessionId"]) if "bridgeSessionId" in rec else None,
             "weekly_stop": functools.partial(weekly_stop, rec.get("sessionId"))}
            for _d, rec in pipes.records(dirs)]


def newest_finder(roots=None):
    """A name -> newest-mtime-or-None function over these roots (default: every account's
    projects folder, listed again on each call so a new account folder is picked up)."""
    if roots is None:
        return lambda name: newest_transcript(name, project_roots())
    return lambda name: newest_transcript(name, roots)


# The launcher's credentials reads (kitlib.launcher.accounts, docs/launcher.md "Accounts"): the
# command that signs a config folder in, run with its config-folder variable set to that folder.
LOGIN = "claude auth login"


def login_of(data):
    """The login in a config folder's parsed .credentials.json: None when it holds no claudeAiOauth
    object (no login made, or not this host's file), else (held, end): held False once the login
    has ended as the file records it (an empty refreshToken, expiresAt 0, which Claude Code writes
    when a refresh is refused at the login's time limit, or loginEnded set; a past expiresAt alone is
    the access token's, which Claude Code refreshes), end its refreshTokenExpiresAt as stored
    (epoch ms) or None. kitlib.launcher.accounts compares end with the clock."""
    oauth = data.get("claudeAiOauth") if isinstance(data, dict) else None
    if not isinstance(oauth, dict):
        return None
    held = bool(oauth.get("refreshToken")) and oauth.get("expiresAt") != 0 and not oauth.get("loginEnded")
    return held, oauth.get("refreshTokenExpiresAt")
