"""No console window from any start in a kit process: the process-wide hook on CreateProcess
(kitlib.platform.windows.console, which says how it works), with its incident log.

Why: Windows opens a console window for a console program only when its parent has NO console and
the start makes no console choice of its own. kitlib.proc.spawn and kitlib.proc.job cover the
kit's own starts one call at a time (no_window_flags); a bare subprocess call, multiprocessing and
the tests do not go through them. The hook covers those: on the first start that makes no console
choice it gives a process with no console one with no window, which every child after it shares.

kitlib/__init__.py calls install() on `import kitlib`, so every kit process has it. Importing it
loads no Win32 binding: those load at the first such start.

Incident log: each time the hook gives a process a console it appends one JSON line (time, how,
pid, exe, this process's argv, the child's command line, the parent's pid and exe) to LOG_NAME in
the kit temp folder (kitlib.config.user.work_temp of the kit checkout; docs/test-tools.md "Temp
folders"). LOG_PATH overrides that (the tests). Over LOG_MAX bytes the log is moved to
`<log>.1` first. The log can fail in any way (a fenced test process, a bad machine config, a full
disk): the start goes on. A command line can carry a token or a prompt, so both go in redacted
(redact_command): the program and the first KEEP_WORDS words after it, each cut to WORD_MAX
characters, with secrets masked (_mask); it never raises.

Windows only: off Windows install() does nothing, and has_console() answers True without asking
kernel32, so kitlib.proc.spawn and no_window_flags add nothing there.
"""
import os
import sys

from kitlib.platform.windows import console as _win
from kitlib.platform.windows.console import (  # noqa: F401  (the face's names)
    ALLOCATED, ATTACHED, CONSOLE_CHOICE, CREATE_NO_WINDOW, MARK, NONE, PRESENT, has_console,
    installed)

LOG_NAME = 'kit-console-incidents.log'
CARRIER_LOG_ENV = 'KIT_CONSOLE_INCIDENT_LOG'   # a path that replaces the carrier's log: the tests' own file
CARRIER_FAIL = 'carrier-fail'           # the "how" of the carrier's line (note_carrier_failure)
LOG_MAX = 1 << 20
KEEP_WORDS = 6                          # a logged command line: the program and this many words
WORD_MAX = 120                          # each word cut to its start and end past this length
MASK = '***'
# a flag or NAME=VALUE name holding one of these hides its value (pass: password, passcode,
# passphrase; pw: pwd) ...
SECRET_WORDS = ('token', 'secret', 'pass', 'pw', 'key', 'auth', 'credential', 'cookie', 'bearer')
# ... once these words, which hold `pass` or `pw` and name no secret, are taken out of it
NOT_SECRET = ('passthru', 'passthrough', 'passive', 'bypass', 'stopwatch', 'compass')

LOG_PATH = None                         # tests: the incident log file; None: the kit temp folder


def install():
    """Install the CreateProcess hook once per process, with _log_incident as its incident log
    (kitlib.platform.windows.console.install). Off Windows nothing."""
    return _win.install(_log_incident)


def log_path():
    """The incident log file: LOG_PATH, else LOG_NAME in the kit temp folder of this kit
    checkout (kitlib.config.user.work_temp). Raises when that cannot be worked out (a fenced test
    process: HomeFenced; a bad machine config: ConfigError)."""
    if LOG_PATH:
        return LOG_PATH
    from ..config import user
    kit = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(user.work_temp(kit), LOG_NAME)


def _parent():
    """(parent pid, parent exe name) from one Toolhelp snapshot, or (None, None)."""
    from . import process
    rows = process.snapshot()
    me = os.getpid()
    ppid = next((pp for pid, pp, _n in rows if pid == me), None)
    exe = next((n for pid, _pp, n in rows if pid == ppid), None)
    return ppid, exe


def split_command_line(line):
    """A Windows command line as its words, by the C runtime's rules (the ones
    subprocess.list2cmdline writes for): blanks part words outside quotes, 2n backslashes before
    a quote are n and the quote counts, 2n+1 are n and a literal quote, "" inside quotes is a
    literal quote; other backslashes are themselves."""
    words, cur, quoted, have, i, n = [], [], False, False, 0, len(line)
    while i < n:
        c = line[i]
        if c == '\\':
            j = i
            while j < n and line[j] == '\\':
                j += 1
            if j < n and line[j] == '"':
                cur.append('\\' * ((j - i) // 2))
                if (j - i) % 2:
                    cur.append('"')
                    j += 1
            else:
                cur.append('\\' * (j - i))
            i, have = j, True
        elif c == '"':
            if quoted and line[i + 1:i + 2] == '"':
                cur.append('"')
                i += 1
            else:
                quoted = not quoted
            i, have = i + 1, True
        elif c in ' \t' and not quoted:
            if have:
                words.append(''.join(cur))
                cur, have = [], False
            i += 1
        else:
            cur.append(c)
            i, have = i + 1, True
    if have:
        words.append(''.join(cur))
    return words


def _secret(name):
    """True when a flag or pair name, lowered and without its digits, holds a secret word once
    the NOT_SECRET words are taken out of it (`DBPASS`, `adminpw`, `--passcode`; not
    `-PassThru` or `/passive`). When in doubt it says secret: a masked word costs nothing."""
    low = ''.join(c for c in name.lower() if not c.isdigit())
    for word in NOT_SECRET:
        low = low.replace(word, ' ')
    return any(s in low for s in SECRET_WORDS)


_RX = None


def _rx():
    """The masks' patterns, compiled on first use (importing kitlib loads no `re`). Each one
    is linear in its word: a long or hostile word must not stall the start it is logged for."""
    global _RX
    if _RX is None:
        import re
        import types
        quoted = r'"(?:[^"\\]|\\.)*(?:"|.*)|\'(?:[^\'\\]|\\.)*(?:\'|.*)'   # unclosed: to the end
        _RX = types.SimpleNamespace(
            flag=re.compile(r'(?:--?|/)([A-Za-z][\w.-]*)'),
            named=re.compile(r'((?:--?|/)?([A-Za-z_][\w.-]*))([=:])(.*)', re.S),
            # a user flag (curl's -u, a cluster ending in it as in -sSu, --proxy-user, /u)
            user_flag=re.compile(r'-[a-z]*u|(?:--?|/)(?:[\w.-]*[-_.])?user|/u', re.I),
            glued_user=re.compile(r'-[A-Za-z]*?[uU]([^:]+:.*)', re.S),        # -uuser:password
            glued_pw=re.compile(r'(-p|/r?p:)(.+)', re.I | re.S),        # -ppassword, /p:password
            # a word's last name and the = or : after it (`$env:TOKEN =`, `"password":`)
            # (no two white-space runs side by side: that is quadratic on a long run)
            trail=re.compile(r'(?<![\w.-])([\w.-]+)[\'"\]]*(?:\s*([=:])\s*|\s*)\Z'),
            # name=value, name = value; "name": value, 'name': value, ['name']=value; {name: value
            pair=re.compile(r'(?<![\w.-])([\w.-]+)\s*=\s*'
                            r'|(["\'])([^"\'\\]*)\2\]?\s*[:=]\s*'
                            r'|(?<=[{,])\s*([A-Za-z_][\w.-]*)\s*:\s*'),
            eq_value=re.compile(quoted + r'|[^&;\s"\']*', re.S),
            json_value=re.compile(quoted + r'|[^,}\]\s]*', re.S),
            authority_end=re.compile(r'[/?#]'),
            space=re.compile(r'(\s+)'),
            bearer=re.compile(r'\b(bearer|basic)(\s+)\S+', re.I),
            # the JWT form starts only where a run of word characters starts, so a run of
            # `eyJ` is tried once and not again from each of its letters
            token=re.compile(r'(?:sk-|gh[pousr]_|github_pat_|xox[abprs]-|glpat-|AKIA|ASIA)'
                             r'[A-Za-z0-9_\-]{8,}|(?<![\w-])eyJ[\w-]{8,}\.[\w-]{8,}\.[\w-]*'),
            drive=re.compile(r'[A-Za-z]:[\\/]'))
    return _RX


def _user_value(v):
    """A user flag's value with all after its first `:` masked, whatever comes before it
    (`user:***`, `DOMAIN\\user:***`, `:***`, `https:***`); a value ending at that `:` (an API
    key as the user, `sk_test_x:`) is masked whole. A drive path (a letter, a colon and a slash:
    `python -u SCRIPT`) and a value with no `:` are kept."""
    i = v.find(':')
    if i < 0 or _rx().drive.match(v):
        return v
    return v[:i + 1] + MASK if v[i + 1:] else MASK + ':'


def _pairs(w):
    """`w` with the value of each secret `name=value` (a URL query, a form body, a keyword
    argument, `name = value`), `"name": value`, `'name': value`, `['name']=value` and
    `{name: value` masked, wherever in the word they stand. A quoted value is masked to its
    closing quote (or the end), and keeps its quotes."""
    rx = _rx()
    out, pos = [], 0
    while True:
        m = rx.pair.search(w, pos)
        if not m:
            break
        name = m.group(1) or m.group(3) or m.group(4) or ''
        out.append(w[pos:m.end()])
        pos = m.end()
        v = _secret(name) and (rx.eq_value if m.group(1) else rx.json_value).match(w, pos).group()
        if v:                               # an empty one is in the next word (_mask)
            q = v[:1] if v[:1] in ('"', "'") else ''
            out.append(q + MASK + q)
            pos += len(v)
    out.append(w[pos:])
    return ''.join(out)


def _url_user(w):
    """`w` with each URL's user info masked. When a `:` follows `scheme://` before the last
    `@` (of the stretch with no white space), all up to that `@` goes: a password may hold
    `/`, `#` or `@`, so `https://host:8080/@scope` is masked too. Else, the user info is the
    authority (to the first / ? or #) up to its last `@`."""
    rx = _rx()
    out = []
    for s in rx.space.split(w):
        pos, i = 0, s.find('://')
        while i >= 0:
            start = i + 3
            at = s.rfind('@', start)
            if at < 0:
                break
            if s.find(':', start, at) >= 0:
                cut = at
            else:
                end = rx.authority_end.search(s, start)
                cut = s.rfind('@', start, end.start() if end else len(s))
            if cut >= 0:
                out.append(s[pos:start] + MASK)
                pos = cut
            i = s.find('://', max(cut, start))
        out.append(s[pos:])
    return ''.join(out)


def _word(w):
    """One word's own secrets masked: all of `--token=VALUE`, `/password:VALUE` and
    `GITHUB_TOKEN=VALUE`; secret pairs inside it (_pairs); a URL's user info (_url_user); the
    word after `Bearer` or `Basic`; and anything shaped like a well-known API token."""
    rx = _rx()
    m = rx.named.fullmatch(w)
    if m and _secret(m.group(2)):
        return m.group(1) + m.group(3) + MASK
    w = rx.token.sub(MASK, _url_user(_pairs(w)))
    return rx.bearer.sub(r'\1\2' + MASK, w)


def _mask(words):
    """`words` with secrets masked: each word's own (_word), and these whole words:
    - the one after a secret flag (`--token VALUE`, `/key VALUE`), after `Bearer`/`Basic`, after
      a word ending in a secret name and `=` or `:` (`"password":`), and after a lone `=` or
      `:` that follows a secret name (`$env:TOKEN = VALUE`), unless it is a secret flag itself;
    - all after the first `:` of a user flag's value, as _user_value (`-u`, `-sSu`,
      `-uuser:password`, `--proxy-user`, `/u`); a flag after it is not its value, and
      `/user:NAME PASSWORD` (net use) hides the next word;
    - once a word starting `-u` or `--user` (or sshpass) is seen, the value of `-p`, `-P`, `/p`,
      `/p:` and a glued `-PVALUE` (mysql, schtasks: the password goes with the user); always
      the value of `/RP` and `/rp:`; and always a glued `-pVALUE` (mysql's password), the next
      word too when the word is also a secret flag (`-password VALUE`) or a user flag."""
    rx = _rx()
    out, hide, prev, user_seen = [], None, None, False
    for w in words:
        last, prev = prev, None
        low = w.lower()
        f = rx.flag.fullmatch(w)
        secret_flag = bool(f) and _secret(f.group(1))
        if hide == 'all' and not secret_flag:
            if low in ('bearer', 'basic'):
                out.append(w)                   # Authorization: Bearer VALUE
                continue
            out.append(MASK)
            hide = None
            continue
        if hide == 'word' and not w.startswith(('/', '-')):
            out.append(MASK)
            hide = None
            continue
        if hide == 'user' and not (f and w.startswith('-')):
            out.append(_user_value(_word(w)))
            hide = None
            continue
        hide = None
        if (low.startswith(('-u', '--user'))
                or low.replace('\\', '/').rsplit('/', 1)[-1] in ('sshpass', 'sshpass.exe')):
            user_seen = True
        g = rx.glued_pw.fullmatch(w)
        if g and (g.group(1) == '-p' or user_seen or g.group(1).lower() == '/rp:'):
            out.append(g.group(1) + MASK)
            hide = 'all' if secret_flag else 'user' if rx.user_flag.fullmatch(w) else None
            continue
        if (secret_flag or low in ('bearer', 'basic', '/rp')
                or (user_seen and low in ('-p', '/p'))
                or (w.strip() in ('=', ':') and last and _secret(last))):
            out.append(w)
            hide = 'all'
            continue
        if rx.user_flag.fullmatch(w):
            out.append(w)
            hide, user_seen = 'user', True
            continue
        g = rx.glued_user.fullmatch(w) if ':' in w and w[1:2] != '-' else None
        if g:
            out.append(w[:g.start(1)] + _user_value(_word(g.group(1))))
            user_seen = True
            continue
        m = rx.named.fullmatch(w)
        if m and rx.user_flag.fullmatch(m.group(1)):
            out.append(m.group(1) + m.group(3) + _user_value(_word(m.group(4))))
            if m.group(1).startswith('/'):
                hide = 'word'                   # net use X /user:NAME PASSWORD
            user_seen = True
            continue
        out.append(_word(w))
        t = rx.trail.search(w)
        if t:
            prev = t.group(1)
            if t.group(2) and _secret(prev):
                hide = 'all'
    return out


def _shorten(words):
    """The program and the first KEEP_WORDS words after it, each longer one cut to its start and
    end (a path keeps its file name), and `...(+N)` for the N words left out."""
    head, tail = WORD_MAX * 2 // 3, WORD_MAX // 3 - 3
    kept = [w if len(w) <= WORD_MAX else w[:head] + '...' + w[-tail:]
            for w in words[:1 + KEEP_WORDS]]
    if len(words) > len(kept):
        kept.append('...(+%d)' % (len(words) - len(kept)))
    return kept


def _text(b):
    """Bytes as text: as the file system decodes them, else with each bad byte as `\\xNN`."""
    try:
        return os.fsdecode(bytes(b))
    except UnicodeDecodeError:
        return bytes(b).decode('utf-8', 'backslashreplace')


def redact_command(cmd):
    """A command line as the incident log keeps it: secrets masked (_mask), then shortened
    (_shorten). A string (or bytes) command line gives a string, a list of words a list; None
    gives None. It never raises: a command line it cannot read is logged as MASK alone."""
    if cmd is None:
        return None
    try:
        if isinstance(cmd, (bytes, bytearray)):
            cmd = _text(cmd)
        if isinstance(cmd, str):
            import subprocess
            return subprocess.list2cmdline(_shorten(_mask(split_command_line(cmd))))
        return _shorten(_mask([_text(w) if isinstance(w, (bytes, bytearray)) else str(w)
                               for w in cmd]))
    except Exception:                   # noqa: BLE001 - the log keeps nothing it cannot read
        return MASK if isinstance(cmd, (str, bytes, bytearray)) else [MASK]


def _incident(how, child):
    """The incident line's fields; both command lines redacted (redact_command)."""
    import time
    try:
        ppid, pexe = _parent()
    except Exception:                   # noqa: BLE001 - the line goes without them
        ppid, pexe = None, None
    return {'time': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'how': how,
            'pid': os.getpid(), 'exe': sys.executable,
            'argv': redact_command(list(getattr(sys, 'orig_argv', sys.argv))),
            'child': redact_command(child), 'parent_pid': ppid, 'parent_exe': pexe}


def _log_incident(how, child):
    """Append one line to the incident log; any failure is swallowed (the start goes on)."""
    try:
        import json
        from ..config import user
        try:
            path = log_path()
        except user.HomeFenced:
            return
        line = json.dumps(_incident(how, child)) + '\n'
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            if os.path.getsize(path) > LOG_MAX:
                os.replace(path, path + '.1')
        except OSError:
            pass
        with open(path, 'a', encoding='utf-8') as fh:
            fh.write(line)
    except Exception:                   # noqa: BLE001 - a log that fails never stops the start
        pass


def carrier_log_path(environ=None):
    """The file `note_carrier_failure` appends to: CARRIER_LOG_ENV when set, else `log_path()`.
    None when that cannot be worked out (a fenced test process: HomeFenced, a BaseException; a bad
    machine config; no LOCALAPPDATA): the line is skipped, rather than kept in a second folder
    under the same name."""
    env = os.environ if environ is None else environ
    if env.get(CARRIER_LOG_ENV):
        return env[CARRIER_LOG_ENV]
    try:
        from ..config import user
        try:
            return log_path()
        except user.HomeFenced:
            return None
    except Exception:                   # noqa: BLE001
        return None


def note_carrier_failure(program, why, path=None, now=None, pid=None):
    """Append one JSON line to `path` (default `carrier_log_path()`), in the same log as
    `_log_incident`'s: {"time" (UTC), "how": "carrier-fail", "pid" (the carrier's), "exe" (the file
    name of `program`, never its arguments), "why"}. The carrier tools/no_window.py calls it when
    it cannot start its program: under pythonw it has no stderr, so this line is all that is left.
    Returns the path written, or None when there is no log or it could not be written; never
    raises, a HomeFenced included: it runs on the carrier's way out."""
    try:
        import json
        import time
        from ..config import user
        try:
            path = path or carrier_log_path()
            if not path:
                return None
            name = str(program).strip().strip('"').replace('/', '\\').split('\\')[-1]
            line = json.dumps({
                'time': time.strftime('%Y-%m-%dT%H:%M:%SZ', now or time.gmtime()),
                'how': CARRIER_FAIL, 'pid': os.getpid() if pid is None else pid,
                'exe': name or '?', 'why': ' '.join(str(why).split())}) + '\n'
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            with open(path, 'a', encoding='utf-8') as f:
                f.write(line)
            return path
        except user.HomeFenced:
            return None
    except Exception:                   # noqa: BLE001
        return None
