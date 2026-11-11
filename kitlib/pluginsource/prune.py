"""Pruning old export folders and old plugin cache versions, and what is in use
(docs/plugin-source.md "Pruning").

  read_in_use(root)                what running processes, scheduled tasks and pin leases name
  prune(root, in_use=)             old export folders removed by claim (never the named one, the 3
                                   newest, one in use, or one replaced less than 7 days ago, 1 when
                                   every in-use read succeeded); stale <sha12>.tmp folders removed
  prune_caches(folders, in_use)    old versions in each account's plugins cache (dry run by default)
"""
import collections
import datetime
import os
import re
import subprocess

from kitlib import platform as kitplatform
from kitlib.host import registry
from kitlib.pluginsource import layout as plugin_layout
from kitlib.pluginsource import state as plugin_state


KEEP = 3                             # newest export folders prune keeps, besides the named one
YOUNG_DAYS = 7                       # prune keeps every export replaced less than this many days ago
IN_USE_YOUNG_DAYS = 1                # ... cut to this when every in-use read succeeded (read_in_use)
CLAIM = '.pruning-'                  # a folder prune is removing: <name>.pruning-<pid>
LIST_TIMEOUT = 30                    # seconds for the process list and the task list
_TMP = re.compile(r'^([0-9a-f]{12})\.tmp$')
_CLAIMED = re.compile(r'^.+\.pruning-\d+$')
_ENDS = r'(?=[\\/"\'\s<;,)&]|$)'      # what may follow a named path part: a separator, a quote,
                                       # a PYTHONPATH-style list, a closing paren, a cmd && chain
_NAMED = re.compile(r'(?:^|(?<=[\\/"\'\s=>]))([0-9a-f]{12})' + _ENDS)


def _when(text):
    """A marker's exported_at as a naive local datetime, or None."""
    try:
        when = datetime.datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    return when.astimezone().replace(tzinfo=None) if when.tzinfo else when


# ---------------------------------------------------------------- what is in use

InUse = collections.namedtuple('InUse', 'texts named errors')
# texts: lower-case text that may name a folder (each process's command line and program, each
# scheduled task's definition); named: <sha12>s named outright (pin leases); errors: why each read
# that failed failed. Prune counts what is in use as whole only when errors is empty.


def _processes():
    from kitlib.proc.listing import list_processes
    return list_processes(timeout=LIST_TIMEOUT)


class PartialTasks(RuntimeError):
    """_tasks read every job but could not tell what some run: `texts` are every job's texts,
    which still count; the error says which jobs, so the read is not whole."""

    def __init__(self, message, texts):
        RuntimeError.__init__(self, message)
        self.texts = texts


def _tasks():
    """[the text of every registered scheduled task's definition] from `schtasks /Query /XML ONE`:
    program, arguments and working folder alike. RuntimeError when schtasks fails or times out.
    Linux: the jobs kitlib.platform.linux.schedule wrote (schedtask.read.read_tasks), each one's
    name, program, arguments, argv and working folder. A job whose command did not split (its argv
    empty) runs something unknown: PartialTasks, carrying every job's text (that job's name,
    working folder and file or line) and naming the jobs."""
    import xml.etree.ElementTree as ET
    from kitlib.proc import spawn
    from kitlib.schedtask import read as taskread
    if kitplatform.NAME == 'linux':
        out, bad = [], []
        for t in taskread.read_tasks(timeout=LIST_TIMEOUT):
            parts = [t.get('path') or '', t.get('workdir') or '']
            if not t.get('argv'):
                where = t.get('file') or t.get('line') or ''
                bad.append('%s (%s)' % (t.get('path') or '?', where or '?'))
                parts.append(where)
            parts += [str(a) for a in t.get('argv') or ()]
            for a in t.get('actions') or ():
                parts += [a.get('exe') or '', a.get('args') or '']
            out.append(' '.join(p for p in parts if p))
        if bad:
            raise PartialTasks('the command of scheduled job %s could not be split'
                               % ', '.join(bad), out)
        return out
    try:
        r = spawn.run(['schtasks', '/Query', '/XML', 'ONE'], capture_output=True, timeout=LIST_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise RuntimeError('schtasks /Query /XML timed out after %s s' % LIST_TIMEOUT)
    if r.returncode != 0:
        said = ((r.stdout or b'') + (r.stderr or b'')).decode('oem', errors='replace').strip()
        raise RuntimeError('schtasks /Query /XML exit %d: %s' % (r.returncode, said[:200] or 'no output'))
    return [' '.join(ET.fromstring(taskread.xml_input(r.stdout or b'')).itertext())]


def _leases(root):
    """The <sha12>s the pin leases name (<root>/pins/leases/<sha12>-<pid>.json, kitlib.gate.pin),
    live or not: a dead one keeps its export only until kitlib.gate.pin prunes the lease. No
    leases folder: none. OSError when the folder cannot be listed."""
    try:
        names = os.listdir(os.path.join(root, 'pins', 'leases'))
    except FileNotFoundError:
        return set()
    return {n[:12] for n in names if plugin_layout._SHA12.match(n[:12])}


def read_in_use(root, procs=None, tasks=None):
    """InUse: the command line and program of every running process (one Win32_Process read,
    kitlib.proc.listing.list_processes), the definition of every scheduled task (`_tasks`) and the
    pin leases under `root`. A read that fails adds its error and the others still run. An empty
    process list (None or []) is a failed read: a real one always holds the reader itself, and
    kitlib.proc.listing.list_processes answers [] when PowerShell printed nothing. procs and tasks:
    the tests' stand-ins for the two reads."""
    texts, named, errors = [], set(), []
    try:
        listed = (procs or _processes)()
        if not listed:
            raise RuntimeError('it came back empty')
        for p in listed:
            for key in ('CommandLine', 'ExecutablePath'):
                v = p.get(key)
                if v:
                    texts.append(str(v).lower())
    except Exception as e:
        errors.append('the process list could not be read (%s: %s)' % (type(e).__name__, e))
    try:
        texts.extend(str(t).lower() for t in (tasks or _tasks)() or ())
    except PartialTasks as e:
        texts.extend(str(t).lower() for t in e.texts)
        errors.append('the scheduled tasks could not be read whole (%s)' % e)
    except Exception as e:
        errors.append('the scheduled tasks could not be read (%s: %s)' % (type(e).__name__, e))
    try:
        named |= _leases(root)
    except OSError as e:
        errors.append('the pin leases could not be read (%s)' % e)
    return InUse(texts, named, errors)


def with_hosts(in_use, copies):
    """The in-use reader `in_use` (None: nothing read) with what each plugin-copy step's in_use()
    names added to its `named`: the export folders a host's own plugin copy was installed from
    (Codex's: its cache's export markers), which prune then keeps. A step's read that fails,
    or a base reader that raises, is an error of the InUse, so prune keeps every young export.
    `in_use` itself when no step has in_use()."""
    hosts = [(getattr(c, 'label', 'plugin copy'), c.in_use) for c in copies or ()
             if callable(getattr(c, 'in_use', None))]
    if not hosts:
        return in_use

    def read():
        if in_use is None:
            u = InUse([], set(), ['nothing but the host plugin copies was read'])
        else:
            try:
                u = in_use()
            except Exception as e:
                u = InUse([], set(), ['%s: %s' % (type(e).__name__, e)])
        named, errors = set(u.named), list(u.errors)
        for label, host in hosts:
            try:
                named |= {n for n in host() if plugin_layout._SHA12.match(n)}
            except Exception as e:
                errors.append('the %s plugin copy could not be read (%s: %s)' % (label, type(e).__name__, e))
        return InUse(u.texts, named, errors)
    return read


def in_use_sha12s(u):
    """Every <sha12> `u` names: a path part of 12 hex digits (not the tail of a uuid), or a lease."""
    found = set(u.named)
    for t in u.texts:
        found.update(_NAMED.findall(t))
    return found


def _version_named(u, version):
    """True when a text of `u` names <...>\\pm-kit\\pm-kit\\<version> (a plugins cache folder)."""
    name = registry.PLUGIN_NAME
    pat = re.compile(r'[\\/]%s[\\/]%s[\\/]%s' % (name, name, re.escape(version.lower())) + _ENDS)
    return any(pat.search(t) for t in u.texts)


def _read(in_use):
    """(InUse or None, why it is not whole or None) from the reader `in_use` (None: not read)."""
    if in_use is None:
        return None, 'nothing was read'
    try:
        u = in_use()
    except Exception as e:
        return None, '%s: %s' % (type(e).__name__, e)
    return u, '; '.join(u.errors) or None


def _claim_remove(full):
    """Remove folder `full` by claim: renamed aside to <name>.pruning-<pid> first, which Windows
    refuses while a file in it is open, so a folder in use is kept whole. A claim a stopped run
    left is removed as it is. None when removed, else why not. A claimed folder that cannot be
    removed whole stays claimed, and the next prune removes the rest."""
    name = os.path.basename(full)
    claimed = full
    if not _CLAIMED.match(name):
        claimed = '%s%s%d' % (full, CLAIM, os.getpid())
        try:
            os.rename(full, claimed)
        except OSError as e:
            return 'in use: it could not be claimed (%s)' % (e.strerror or e)
    try:
        plugin_layout._rmtree(claimed)
    except OSError as e:
        return 'claimed as %s but not wholly removed (%s); the next prune removes the rest' % (
            os.path.basename(claimed), e)
    return None


def prune(root, keep=KEEP, now=None, young_days=None, in_use=None, out=None, pointer=None):
    """Remove old export folders, each by claim (`_claim_remove`). Never removed: the folder
    marketplace.json names (the plugin loads from it, in place or by a copy at the next update),
    the folder the live pointer file `pointer` names under `root` (when it holds the marker),
    the `keep` newest by their marker's exported_at, every export a running process, a scheduled
    task or a pin lease names (`in_use`, a reader returning InUse, as read_in_use does), and every
    export replaced less than `young_days` ago, or whose dates cannot be read (a session whose MCP
    server has exited still runs hooks from its folder, and no process names it). `young_days`
    defaults to IN_USE_YOUNG_DAYS when `in_use` read everything without an error, else YOUNG_DAYS:
    what cannot be read counts as possibly in use. An export is dated by when it was replaced, the
    exported_at of the next newer export, not by its own exported_at: one that was current for
    weeks and then replaced by a few quick landings may still be in use. An unreadable exported_at
    counts as newer than every readable one. Only a <sha12> folder holding the marker is a
    candidate; a link or junction is never one, and anything else in the root stays. A
    <sha12>.tmp folder other than the named folder's is a half-written export left by a run that
    stopped (prune runs under refresh.pid, so no export is being written), and a <sha12>.pruning-
    <pid> folder is a claim a stopped prune left: both are removed too. `out` gets a line on what
    the in-use read kept or why it was not whole. Returns ([removed folder], [(folder, why) not
    removed])."""
    say = out or (lambda line: None)
    u, why = _read(in_use)
    used = in_use_sha12s(u) if u else set()
    if young_days is None:
        young_days = YOUNG_DAYS if why else IN_USE_YOUNG_DAYS
    if why and in_use is not None:
        say('prune: what is in use could not be read whole (%s): keeping every export replaced '
            'less than %d days ago' % (why, young_days))
    current = plugin_layout.current_folder(root)
    now = now or datetime.datetime.now()
    found, stale = [], []
    try:
        names = os.listdir(root)
    except OSError:
        return [], []
    for n in names:
        full = os.path.join(root, n)
        if not os.path.isdir(full) or plugin_layout._is_link(full):
            continue
        if _CLAIMED.match(n) and plugin_layout._SHA12.match(n[:12]):
            stale.append(n)
            continue
        tmp = _TMP.match(n)
        if tmp:
            if tmp.group(1) != current:
                stale.append(n)
            continue
        if not plugin_layout._SHA12.match(n):
            continue
        marker = plugin_layout.read_marker(full)
        if marker is None:
            continue
        found.append((_when(str(marker.get('exported_at') or '')), n))
    # newest first; an unreadable date first of all (it may be the newest)
    found.sort(key=lambda w: (w[0] is None, w[0] or datetime.datetime.min, w[1]), reverse=True)
    spare = {n for _, n in found[:keep]}
    if current:
        spare.add(current)
    for i, (when, n) in enumerate(found):
        replaced = found[i - 1][0] if i else None      # the exported_at of the next newer export
        if when is None or replaced is None or now - replaced < datetime.timedelta(days=young_days):
            spare.add(n)
    pointed = plugin_layout._pointed_folder(pointer, root)
    if pointed in {n for _, n in found}:
        if pointed not in spare:
            say('prune: kept, the live pointer names it: %s' % pointed)
        spare.add(pointed)
    held = sorted(n for _, n in found if n in used and n not in spare)
    if held:
        say('prune: kept, a running process, scheduled task, pin lease or host plugin copy names it: %s'
            % ', '.join(held))
    spare.update(used)
    removed, failed = [], []
    for n in [n for _, n in found if n not in spare] + sorted(stale):
        full = os.path.join(root, n)
        why_not = _claim_remove(full)
        if why_not is None:
            removed.append(full)
        else:
            failed.append((full, why_not))
    return removed, failed


def cache_dir(folder):
    """Account folder `folder`'s plugins cache of the kit: <folder>\\plugins\\cache\\pm-kit\\pm-kit,
    one <version> folder per install Claude Code copied there."""
    return os.path.join(folder, 'plugins', 'cache', registry.PLUGIN_NAME, registry.PLUGIN_NAME)


def prune_caches(folders, in_use, apply=False, now=None):
    """Old versions in each account folder's plugins cache (cache_dir): [(version folder, action,
    why)], action 'kept', 'would' (a dry run), 'removed' or 'failed'. Kept: every version when
    `in_use` (a reader, as for prune) could not read everything, or the account's install record
    (accounts.installed_path) is missing, unreadable or names no install, or a link lies on the way
    to the cache; the version the install record's installPath names; the newest, and each
    replaced (the next newer version folder's modified time) less than IN_USE_YOUNG_DAYS ago, or
    whose time cannot be read; one a running process or scheduled task names; a link or
    junction. Every other version folder goes, by claim (`_claim_remove`), when `apply`; a claim
    a stopped run left goes too."""
    now = now or datetime.datetime.now()
    u, unread = _read(in_use)
    rows = []
    for f in folders:
        cache = cache_dir(f)
        try:
            names = sorted(os.listdir(cache))
        except OSError:
            continue                    # no cache: nothing to prune
        hops = [os.path.join(f, 'plugins'), os.path.join(f, 'plugins', 'cache'),
                os.path.dirname(cache), cache]
        linked = any(plugin_layout._is_link(h) for h in hops)
        installed = plugin_state._host().installed_path(f)
        vers, claims = [], []
        for n in names:
            p = os.path.join(cache, n)
            if plugin_layout._is_link(p):
                rows.append((p, 'kept', 'a link or junction, never followed'))
                continue
            if not os.path.isdir(p):
                continue
            if _CLAIMED.match(n):
                claims.append(n)        # a claim a stopped run left: never dates a version
                continue
            try:
                t = datetime.datetime.fromtimestamp(os.stat(p).st_mtime)
            except (OSError, ValueError, OverflowError):
                t = None
            vers.append((t, n))
        vers.sort(key=lambda w: (w[0] is None, w[0] or datetime.datetime.min, w[1]), reverse=True)
        todo = [(t, n, vers[i - 1][0] if i else None) for i, (t, n) in enumerate(vers)]
        todo += [(None, n, None) for n in claims]
        for t, n, replaced in todo:
            p = os.path.join(cache, n)
            if unread:
                why = 'what is in use could not be read whole (%s)' % unread
            elif linked:
                why = 'a link or junction lies on the way to %s' % cache
            elif installed is None:
                why = ('the install record %s is missing, cannot be read or names no %s install'
                       % (os.path.join(f, 'plugins', 'installed_plugins.json'), registry.PLUGIN_ID))
            elif plugin_layout._under(installed, p):
                why = 'the install record names it'
            elif _CLAIMED.match(n):
                why = None              # a claim a stopped run left
            elif t is None or replaced is None:
                why = 'the newest, or its time cannot be read'
            elif now - replaced < datetime.timedelta(days=IN_USE_YOUNG_DAYS):
                why = 'replaced less than %d day ago' % IN_USE_YOUNG_DAYS
            elif _version_named(u, n):
                why = 'a running process or scheduled task names it'
            else:
                why = None
            if why:
                rows.append((p, 'kept', why))
            elif not apply:
                rows.append((p, 'would', None))
            else:
                why_not = _claim_remove(p)
                rows.append((p, 'removed', None) if why_not is None else (p, 'failed', why_not))
    return rows
