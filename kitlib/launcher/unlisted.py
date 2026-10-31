"""The launched consultant and deep-worker sessions that have no sessions.json entry, read back from
their windows, so the hub's usage crossover can start each one again on another account
(docs/launcher.md "Usage crossover").

  classify(procs, settings, skip=(), keys=()) -> (sessions, never)
      sessions {key: [Session]}: every session that can be crossed, by its crossing key;
      never {name: (reason, account)}: every other live session, with why it is never crossed
      (REASONS) and the account its window names (None when unknown).
      `skip`: the names to leave out (the sessions.json entries' sessions, the PM); `keys`: the
      sessions.json keys (an unlisted session whose key is one of them is never crossed).

A session is read from its window, the powershell.exe parent whose script start.py wrote
(window.script): its title and the claude line both carry the session's name, PM_KIT_ROLE is
consultant or deep-worker and matches its --agent, CLAUDE_CONFIG_DIR (or its removal: the default
folder) is the folder of exactly one account in launcher.accounts, and its first message is one of
start.py's (CONSULTANT_PROMPT, DEEP_PROMPT, DEEP_SUCCESSOR_PROMPT), from which the notes file, the
brief, the requester and the repository come back. The key: a consultant's name; a deep-worker's
name, without the -a<N> a successor's name carries. Nothing here reads a file or a process itself.
"""
import collections
import os
import re

from kitlib import agentname
from kitlib.launcher import lookup, start, stop, window

CONSULTANT, DEEP = 'consultant', 'deep'
# the reasons a live session is never crossed (docs/launcher.md "Usage crossover")
NO_WINDOW = 'no-window'            # not started by launch.py: no window of its name (a terminal, the desktop app)
OTHER_ROLE = 'other-role'          # launched, but not a consultant or a deep-worker
ROLE_MISMATCH = 'role-mismatch'    # its PM_KIT_ROLE, --agent and name do not agree
UNREADABLE = 'unreadable'          # its window script or first message is not one start.py writes
UNKNOWN_ACCOUNT = 'unknown-account'   # its config folder is no single account's
KEY_TAKEN = 'key-taken'            # its key is a sessions.json key
SEVERAL = 'several'                # several sessions of one key, with no crossing open
REASONS = (NO_WINDOW, OTHER_ROLE, ROLE_MISMATCH, UNREADABLE, UNKNOWN_ACCOUNT, KEY_TAKEN, SEVERAL)

Session = collections.namedtuple(
    'Session', 'kind key name pid created account model effort autocompact repo_path home '
               'requester brief notes slug agent_temp predecessor')

_Q = r"'((?:[^']|'')*)'"
_SET = re.compile(r"; Set-Location %s; (claude .*)\Z" % _Q, re.S)
_CMD = re.compile(r"^claude --agent (?P<agent>\S+)(?: --model (?P<model>'(?:[^']|'')*'))?(?: --effort (?P<effort>\S+))?"
                  r"(?: --autocompact (?P<ac>\d+))?(?: --add-dir (?P<add>'(?:[^']|'')*'))?(?: --permission-mode \S+)?"
                  r" --remote-control (?P<rc>'(?:[^']|'')*') -n (?P<n>'(?:[^']|'')*') (?P<prompt>'(?:[^']|'')*')\Z",
                  re.S)
_ROLE = re.compile(r"(?:^|; )\$env:%s = %s" % (re.escape(window.ROLE_ENV), _Q))
_CONFIG = re.compile(r"(?:^|; )\$env:%s = %s" % (re.escape(window.CONFIG_ENV), _Q))
_NO_CONFIG = '; Remove-Item Env:%s ' % window.CONFIG_ENV
_TEMP = re.compile(r"(?:^|; )\$env:AGENT_TEMP = %s" % _Q)
_SUFFIX = re.compile(r'-a\d+\Z')


def _unq(text):
    """A single-quoted value (window.q), quotes included, back to its text; None stays None."""
    return text[1:-1].replace("''", "'") if text else text


def _val(m):
    """The text of a match of _Q's group 1 (quotes left out); None for no match."""
    return m.group(1).replace("''", "'") if m else None


def _pattern(template):
    """A regex for a start.py first message: each %(field)s a named group (a repeated one must be
    the same text), %(pm)s any text (start names the PM live), anything after the template allowed
    (the check-in and home notes)."""
    parts = re.split(r'%\((\w+)\)s', template)
    out, seen = [], set()
    for i, part in enumerate(parts):
        if i % 2 == 0:
            out.append(re.escape(part))
        elif part == 'pm':
            out.append('.*?')
        elif part in seen:
            out.append('(?P=%s)' % part)
        else:
            seen.add(part)
            out.append('(?P<%s>.*?)' % part)
    return re.compile('^' + ''.join(out), re.S)


_PROMPTS = {CONSULTANT: (_pattern(start.CONSULTANT_PROMPT),),
            DEEP: (_pattern(start.DEEP_PROMPT), _pattern(start.DEEP_SUCCESSOR_PROMPT))}


def read_prompt(kind, prompt):
    """The fields of a first message of `kind` (CONSULTANT or DEEP), or None."""
    for pat in _PROMPTS[kind]:
        m = pat.match(prompt)
        if m:
            return m.groupdict()
    return None


def _same(a, b):
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def account_of(settings, config_dir):
    """The account whose folder is `config_dir` (None: the default folder), or None when it is
    no account's or several accounts'."""
    hits = set()
    for n in settings.account_numbers():
        for d in settings.account_dirs(n):
            if (settings.is_default(d) if config_dir is None else _same(d, config_dir)):
                hits.add(str(n))
    return hits.pop() if len(hits) == 1 else None


def read_window(script, name):
    """{role, config_dir, agent_temp, folder, agent, model, effort, autocompact, add_dir, prompt}
    of a window script start.py wrote for the session `name`, or None."""
    if not script or not script.startswith('$Host.UI.RawUI.WindowTitle = %s; ' % window.q(name)):
        return None
    m = _SET.search(script)
    c = _CMD.match(m.group(2)) if m else None
    if not c or _unq(c.group('rc')) != name or _unq(c.group('n')) != name:
        return None
    head = script[:m.start()]
    role, config = _ROLE.search(head), _CONFIG.search(head)
    if not role or (not config and _NO_CONFIG not in head):
        return None
    return {'role': _val(role), 'config_dir': _val(config), 'agent_temp': _val(_TEMP.search(head)),
            'folder': _val(m), 'agent': c.group('agent'), 'model': _unq(c.group('model')),
            'effort': c.group('effort'), 'autocompact': c.group('ac') and int(c.group('ac')),
            'add_dir': _unq(c.group('add')), 'prompt': _unq(c.group('prompt'))}


def _window_of(row, by_pid):
    """The script of the powershell.exe window `row` runs in, or None."""
    try:
        parent = by_pid.get(int(row.get('ParentProcessId') or 0))
    except (TypeError, ValueError):
        return None
    if not parent or parent.get('Name') != 'powershell.exe' or not parent.get('CommandLine'):
        return None
    return stop.window_script(parent['CommandLine'])


def session_of(row, by_pid, settings, name):
    """(Session, None, account) for a launched session `row` named `name`, or (None, reason,
    account): `account` the one its window's config folder names, None when it has no readable
    window or the folder is no single account's."""
    script = _window_of(row, by_pid)
    if not script or not script.startswith('$Host.UI.RawUI.WindowTitle = %s; ' % window.q(name)):
        return None, NO_WINDOW, None
    w = read_window(script, name)
    if w is None:
        return None, UNREADABLE, None
    account = account_of(settings, w['config_dir'])
    roles = {window.CONSULTANT: CONSULTANT, window.DEEP_WORKER: DEEP}
    agent = agentname.bare(w['agent'])
    if w['role'] not in roles and agent not in roles:
        return None, OTHER_ROLE, account
    kind = roles.get(w['role'])
    prefix = 'consultant-' if kind == CONSULTANT else 'deep-'
    if w['role'] != agent or not name.startswith(prefix):
        return None, ROLE_MISMATCH, account
    fields = read_prompt(kind, w['prompt'])
    if fields is None or fields.get('name') != name:
        return None, UNREADABLE, account
    predecessor = fields.get('predecessor')
    key = name
    if predecessor is not None:             # a successor: deep-<slug>-a<N>
        if not _SUFFIX.search(name):
            return None, UNREADABLE, account
        key = _SUFFIX.sub('', name)
    if not start.SLUG.match(key[len(prefix):]):
        return None, UNREADABLE, account
    if account is None:
        return None, UNKNOWN_ACCOUNT, None
    repo_path = fields['repo_path'] if kind == CONSULTANT else (w['add_dir'] or w['folder'])
    return Session(kind, key, name, row.get('ProcessId'), row.get('Created'), account, w['model'], w['effort'],
                   w['autocompact'], repo_path, bool(w['add_dir']), fields.get('requester'), fields.get('brief'),
                   fields.get('notes'), key[len(prefix):], w['agent_temp'], predecessor), None, account


def classify(procs, settings, skip=(), keys=()):
    """({key: [Session]}, {name: (reason, account)}) of the launched sessions in `procs` (module
    docstring); a never-crossed session's account is its window's, None when unknown."""
    procs = list(procs)
    by_pid = {}
    for p in procs:
        try:
            by_pid[int(p.get('ProcessId'))] = p
        except (TypeError, ValueError):
            continue
    sessions, never = {}, {}
    for row in lookup.launched(procs):
        name = lookup.rc_name(row.get('CommandLine'))
        if not name or name in skip:
            continue
        sess, why, account = session_of(row, by_pid, settings, name)
        if sess is not None and sess.key in keys:
            sess, why = None, KEY_TAKEN
        if sess is None:
            never[name] = (why, account)
        else:
            sessions.setdefault(sess.key, []).append(sess)
    return sessions, never
