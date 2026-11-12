"""The refresh (docs/plugin-source.md): one at a time under <root>/refresh.pid, an export of the
remote's main, each account folder's plugin re-pointed at it, and pm_watch's check.

  RefreshLock                      <root>/refresh.pid, one refresh at a time
  refresh(...)                     the whole run tools/plugin_refresh.py makes (with copies, each
                                   host's own plugin copy too: tools/_host.py plugin_copies)
  ensure_export(kit, s)            an export the marketplace names, made when missing; no account touched
  poll(kit, s, last, ...)          the pm_watch check: an event line per new main, and the spawn
                                   of a refresh when plugin.auto_refresh is on

It writes under the export root and, for a refresh that went through, under each account's
plugins cache (a host's plugin-copy step, which refresh is handed, writes that host's own copy);
it fetches nothing and takes no machine lock.
"""
import json
import os
import subprocess
import time

from kitlib import platform as kitplatform
from kitlib.fs import replace as fsreplace
from kitlib.git import batch
from kitlib.host import registry
from kitlib.proc import job
from kitlib.pluginsource import layout as plugin_layout
from kitlib.pluginsource import export as plugin_export
from kitlib.pluginsource import state as plugin_state
from kitlib.pluginsource import prune as plugin_prune


PIDFILE = 'refresh.pid'
LOGFILE = 'refresh.log'
LOCK_GRACE_S = 10                    # an empty or unreadable refresh.pid younger than this is live
_DETACHED = 0x00000008 | 0x00000200 if kitplatform.NAME == 'windows' else 0  # DETACHED_PROCESS, NEW_GROUP


# ---------------------------------------------------------------- one refresh at a time

class Busy(Exception):
    """Another refresh holds refresh.pid."""


def _own():
    from kitlib.proc import process
    return os.getpid(), process.own_ctime()


def _alive(pid, ctime):
    from kitlib.proc import process
    return process.alive(pid, ctime)


class RefreshLock(object):
    """<root>/refresh.pid, created exclusively, holding {pid, ctime}. A file whose process is gone
    (pid dead, or its start time differs: a reused pid) is stale and taken over. An empty or
    unreadable file is one a starting refresh has made but not yet written: live until its mtime
    is more than LOCK_GRACE_S old, stale after."""

    def __init__(self, root, own=None, alive=None, clock=None):
        self.path = os.path.join(root, PIDFILE)
        self.own = own or _own()
        self.alive = alive or _alive
        self.clock = clock or time.time
        self.held = False

    def holder(self, path=None):
        """{pid, ctime} of a live holder (pid None: a file not yet written), or None (no file, or
        a stale one). `path`: read that file instead (a claimed refresh.pid, _break)."""
        path = path or self.path
        try:
            with open(path, encoding='utf-8') as fh:
                data = json.load(fh)
            pid, ctime = int(data['pid']), data.get('ctime')
        except FileNotFoundError:
            return None
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            try:
                age = self.clock() - os.path.getmtime(path)
            except FileNotFoundError:
                return None
            except OSError:
                age = 0
            return None if age > LOCK_GRACE_S else {'pid': None, 'ctime': None}
        return {'pid': pid, 'ctime': ctime} if self.alive(pid, ctime) else None

    def acquire(self):
        for _ in range(2):
            try:
                with open(self.path, 'x', encoding='utf-8', newline='\n') as fh:
                    json.dump({'pid': self.own[0], 'ctime': self.own[1]}, fh)
                self.held = True
                return
            except FileExistsError:
                h = self.holder()
                if h is not None:
                    raise self._busy(h)
                self._break()                   # stale: its process is gone
        raise Busy('cannot take %s' % self.path)

    def _busy(self, h):
        who = 'pid %d' % h['pid'] if h['pid'] is not None else 'its pid not yet written'
        return Busy('a refresh is already running (%s, %s)' % (who, self.path))

    def _break(self):
        """Remove the refresh.pid just read as stale, by claim: renamed to a name only this run
        uses, then read again there. Between that read and the rename another starter may have
        broken it and written its own; a plain remove would delete that live file and both would
        hold the lock. A claimed file that is still stale is removed; a live one is put back and
        refused (Busy). A file already gone is another starter's break: the caller retries."""
        claim = '%s.break-%d-%s' % (self.path, self.own[0], os.urandom(4).hex())
        try:
            fsreplace.replace(self.path, claim)     # waits out a reader on Windows
        except FileNotFoundError:
            return
        except OSError as e:
            raise Busy('cannot take %s: %s' % (self.path, e))
        h = self.holder(claim)
        if h is None:
            try:
                fsreplace.remove(claim)
            except OSError:
                pass                                # a stray claim file; refresh.pid is free
            return
        self._put_back(claim)
        raise self._busy(h)

    def _put_back(self, claim):
        """Move a claimed live file back to refresh.pid, never over a file a starter made there
        meanwhile (place_new, retried on Windows while a reader holds the file open). On failure it
        stays at `claim`."""
        try:
            fsreplace.place_new(claim, self.path, wait=fsreplace.WAIT)
        except OSError:
            pass

    def release(self):
        if not self.held:
            return
        self.held = False
        try:
            with open(self.path, encoding='utf-8') as fh:
                data = json.load(fh)
            if data.get('pid') == self.own[0] and data.get('ctime') == self.own[1]:
                os.remove(self.path)
        except (OSError, ValueError, AttributeError):
            pass


def lock_live(root, alive=None):
    """True when a live refresh holds <root>/refresh.pid."""
    return RefreshLock(root, own=(0, None), alive=alive).holder() is not None


def old_cache(config_dir, version):
    """Where an install of the kit checkout as its own marketplace kept its copy: the plugins
    cache of `config_dir`, marketplace pm-kit, plugin pm-kit, `version`. A path made from names,
    never looked up on disk."""
    return os.path.join(config_dir, 'plugins', 'cache', registry.PLUGIN_NAME, registry.PLUGIN_NAME, version)


UPDATE_STEPS = (['plugin', 'marketplace', 'update', registry.PLUGIN_NAME], ['plugin', 'update', registry.PLUGIN_ID])


def migrate_steps(root):
    return (['plugin', 'marketplace', 'remove', registry.PLUGIN_NAME], ['plugin', 'marketplace', 'add', root],
            ['plugin', 'install', registry.PLUGIN_ID, '-s', 'user'])


def migrate_cmd(folder):
    return '%s --folder "%s"' % (plugin_layout.refresh_cmd(True), folder)


def refresh(kit, s, folders, out, apply=False, migrate=False, runner=None, now=None, own=None,
            alive=None, in_use=None, copies=None):
    """One refresh (tools/plugin_refresh.py). `folders` are the account folders to run in (all of
    them, or those --folder named); `runner` is kitlib.launcher.accounts.Runner (or a test's);
    `in_use` is the prune's reader of what is in use (prune; None: the 7-day rule), with what each
    copy step's in_use() names added (with_hosts). `copies` are
    the hosts' plugin-copy steps (tools/_host.py plugin_copies), each run as step(export folder,
    out, apply) after the account folders, also when they had nothing to do: a step prints its own
    lines and never sets the exit code; None or []: none. After an update that went through, the
    old versions in each account folder's plugins cache are pruned (_prune_old_caches).
    Without `migrate`, a folder whose state record does not say `migrated: true` is refused with
    a line naming the migrate command for it, and the others still run. Dry run (apply False):
    prints the plan and writes nothing. Returns the exit code: 0 done or nothing to do, 1 refused
    (the whole run or one folder) or a folder failed."""
    root = s.export_dir
    in_use = plugin_prune.with_hosts(in_use, copies)
    try:
        sha = plugin_export.remote_main(kit, s.remote)
        state = plugin_state.read_state(root)
    except (plugin_layout.RefreshError, plugin_layout.StateError) as e:
        out('REFUSED: %s' % e)
        return 1
    name = plugin_layout.folder_name(sha)
    exported = plugin_layout.read_marker(os.path.join(root, name))
    fresh = bool(exported and exported.get('sha') == sha and plugin_layout.current_folder(root) == name
                 and state and state.get('sha') == sha)
    refused = [] if migrate else [f for f in folders if not plugin_state.migrated(state, f)]
    if migrate:
        todo = list(folders)
    else:
        todo = [f for f in folders if f not in refused
                and not (fresh and plugin_state._record(state, f).get('rc') == 0)]
    steps = migrate_steps(root) if migrate else UPDATE_STEPS
    out('kit %s/%s: %s' % (s.remote, plugin_layout.MAIN, sha[:12]))
    for f in refused:
        out('REFUSED %s: not migrated to the export yet: %s' % (f, migrate_cmd(f)))
    if fresh and apply and not todo:
        _sync_locked(s, name, out, own, alive)
    if fresh and not todo:
        if not refused:
            out('pm-kit plugin is current: %s is exported and every account folder named refreshed; '
                'nothing to do' % name)
        if copies:
            _copies_locked(s, name, sha, copies, out, apply, own, alive)
        return 1 if refused else 0
    if not apply:
        out('DRY RUN (nothing written; --apply makes these):')
        if not fresh:
            out('  export %s into %s, then point %s at it' % (sha[:12], os.path.join(root, name),
                                                         plugin_layout.marketplace_path(root)))
            out('  prune export folders other than that one, the %d newest, those a running process, '
                'scheduled task or pin lease names, and those replaced less than %d day ago (%d days '
                'when what is in use cannot be read whole)' % (plugin_prune.KEEP, plugin_prune.IN_USE_YOUNG_DAYS, plugin_prune.YOUNG_DAYS))
        for f in todo:
            for st in steps:
                out('  %s: claude %s' % (f, ' '.join(st)))
        if not todo:
            out('  no account folder to run claude in')
        if migrate:
            _old_cache_lines(kit, sha, folders, out)
        if copies:
            _copies(s, name, sha, copies, out, False)
        return 1 if refused else 0
    try:
        os.makedirs(root, exist_ok=True)
    except OSError as e:
        out('REFUSED: cannot make the export root %s: %s' % (root, e))
        return 1
    lock = RefreshLock(root, own=own, alive=alive)
    try:
        lock.acquire()
    except Busy as e:
        out('REFUSED: %s' % e)
        return 1
    try:
        code = _apply(kit, s, sha, state, fresh, todo, steps, migrate, folders, runner, out, now,
                      in_use)
        if copies:
            _copies(s, name, sha, copies, out, True)
    finally:
        lock.release()
    return 1 if refused else code


def _copies(s, name, sha, copies, out, apply):
    """Run each plugin-copy step with export <name>'s folder. With apply the caller holds the
    refresh lock, and an export that is not there whole (the export step failed) is a line per
    step, not a write. A step that raises is a line: it never fails the account folders' result."""
    folder = os.path.join(s.export_dir, name)
    whole = not apply or (plugin_layout.read_marker(folder) or {}).get('sha') == sha
    for step in copies:
        label = getattr(step, 'label', 'plugin copy')
        if not whole:
            out('%s: not refreshed: there is no export of %s at %s' % (label, sha[:12], folder))
            continue
        try:
            step(folder, out, apply)
        except Exception as e:                   # never fail the refresh over a host's copy
            out('%s: not refreshed: %s: %s' % (label, type(e).__name__, e))


def _sync_locked(s, name, out, own, alive):
    """sync_live_pointer for an export that is already current. The pointer is compared first and
    the refresh lock taken only when it is missing or differs (a poll that finds it right makes no
    lock file). Under the lock, marketplace.json is read again: when it no longer names `name`, a
    refresh landed a newer export meanwhile, which writes its own pointer, so nothing is written.
    The lock busy (that refresh writes it) is nothing either. Never raises."""
    body = plugin_export._pointer_body(s, name)
    if body is None or plugin_layout.read_live_pointer(s.live_pointer) == body:
        return
    lock = RefreshLock(s.export_dir, own=own, alive=alive)
    try:
        lock.acquire()
    except (Busy, OSError):
        return
    try:
        if plugin_layout.current_folder(s.export_dir) == name:
            plugin_export.sync_live_pointer(s, name, out)
    finally:
        lock.release()


def _copies_locked(s, name, sha, copies, out, apply, own, alive):
    """_copies when the account folders had nothing to do: with apply, under the refresh lock
    (busy: a line per step, and the copies are left for the refresh holding it)."""
    if not apply:
        _copies(s, name, sha, copies, out, False)
        return
    lock = RefreshLock(s.export_dir, own=own, alive=alive)
    try:
        lock.acquire()
    except Busy as e:
        for step in copies:
            out('%s: not refreshed: %s' % (getattr(step, 'label', 'plugin copy'), e))
        return
    try:
        _copies(s, name, sha, copies, out, True)
    finally:
        lock.release()


def _apply(kit, s, sha, state, fresh, todo, steps, migrate, folders, runner, out, now, in_use=None):
    root = s.export_dir
    name = plugin_layout.folder_name(sha)
    step = 'export'
    try:
        folder, version = plugin_export.export(kit, sha, root, now)
        out('exported %s: %s (version %s)' % (sha[:12], folder, version))
        step = 'marketplace write'
        plugin_export.write_marketplace(root, name)
        out('marketplace %s names ./%s' % (plugin_layout.marketplace_path(root), name))
        plugin_export.sync_live_pointer(s, name, out)
        if not fresh:
            step = 'state write'
            state = {'sha': sha, 'version': version, 'folder': name, 'exported_at': plugin_layout._stamp(now),
                     'folders': plugin_state._carry(state)}
            plugin_state.write_state(root, state)
        step = 'prune'
        removed, failed = plugin_prune.prune(root, now=now, in_use=in_use, out=out, pointer=s.live_pointer)
    except (plugin_layout.RefreshError, OSError, ValueError) as e:
        out('REFUSED: the %s failed: %s' % (step, e))
        return 1
    for r in removed:
        out('pruned %s' % r)
    for f, why in failed:
        out('WARNING: could not remove %s: %s' % (f, why))
    if migrate:
        _old_cache_lines(kit, sha, folders, out)
    if not todo:
        _prune_old_caches(folders, state, in_use, migrate, out, now)
        return 0
    if runner is None or not runner.available():
        out('REFUSED: claude is not on PATH: the export is made, no account folder was refreshed')
        return 1
    code = 0
    for f in todo:
        rc, cmd_line, text = 0, None, ''
        for st in steps:
            rc, text = runner.run(st, f)
            cmd_line = ' '.join(st)
            if rc != 0 and not (migrate and st[:3] == ['plugin', 'marketplace', 'remove']):
                break
            rc = 0
        rec = {'rc': rc, 'at': plugin_layout._stamp(now), 'step': cmd_line,
               'migrated': (rc == 0) if migrate else True}
        plugin_state._set_record(state, f, rec)
        try:
            plugin_state.write_state(root, state)
        except (OSError, ValueError) as e:
            out('REFUSED: the state write failed after %s: %s' % (f, e))
            return 1
        if rc == 0:
            out('%s: %s' % (f, 'migrated' if migrate else 'refreshed'))
        else:
            code = 1
            out('FAILED %s: claude %s exited %s: %s' % (f, cmd_line, rc, (text or '').strip()[-400:]))
    if code == 0:
        _prune_old_caches(folders, state, in_use, migrate, out, now)
    return code


def _prune_old_caches(folders, state, in_use, migrate, out, now):
    """After an update that went through, the old versions in the plugins cache of each account
    folder that `state` records migrated and refreshed with rc 0 go; a refused folder, not yet
    migrated, is never touched. prune_caches runs with apply, every guard of it kept: the
    installed version, the newest, one replaced less than IN_USE_YOUNG_DAYS ago, one in use, a
    link. Prints a line per version removed or failed, never the kept ones, and changes no exit
    code. A migrate run removes none: it only lists the old caches (_old_cache_lines)."""
    if migrate:
        return
    done = [f for f in folders if plugin_state.migrated(state, f) and plugin_state._record(state, f).get('rc') == 0]
    if not done:
        return
    try:
        rows = plugin_prune.prune_caches(done, in_use, apply=True, now=now)
    except (OSError, ValueError) as e:
        out('WARNING: the old plugin caches were not pruned: %s' % e)
        return
    for p, action, why in rows:
        if action == 'removed':
            out('pruned cache %s' % p)
        elif action == 'failed':
            out('WARNING: could not remove %s: %s' % (p, why))


def ensure_export(kit, s, now=None, own=None, alive=None, in_use=None):
    """The export root, holding an export of the remote's main that marketplace.json names: made
    when missing, kept when current. Runs no claude command and records no account folder (an
    installer then adds the root as a marketplace); a new export's state keeps each folder's
    migrated flag. A state.json that cannot be read raises StateError before anything is
    written, so its migrated flags are never overwritten. `in_use`: as for prune. Returns (root, sha); RefreshError,
    StateError, Busy or OSError."""
    sha = plugin_export.remote_main(kit, s.remote)
    root = s.export_dir
    name = plugin_layout.folder_name(sha)
    have = plugin_layout.read_marker(os.path.join(root, name))
    if have and have.get('sha') == sha and plugin_layout.current_folder(root) == name:
        _sync_locked(s, name, None, own, alive)
        return root, sha
    os.makedirs(root, exist_ok=True)
    lock = RefreshLock(root, own=own, alive=alive)
    lock.acquire()
    try:
        state = plugin_state.read_state(root)        # StateError: refuse rather than drop the flags
        _folder, version = plugin_export.export(kit, sha, root, now)
        plugin_export.write_marketplace(root, name)
        plugin_export.sync_live_pointer(s, name)
        if not state or state.get('sha') != sha:
            plugin_state.write_state(root, {'sha': sha, 'version': version, 'folder': name,
                               'exported_at': plugin_layout._stamp(now), 'folders': plugin_state._carry(state)})
        plugin_prune.prune(root, now=now, in_use=in_use, pointer=s.live_pointer)
    finally:
        lock.release()
    return root, sha


def _old_cache_lines(kit, sha, folders, out):
    """The copies an install from the kit checkout left (docs/plugin-source.md "Migrating"): named,
    never looked at or removed."""
    try:
        data, why = batch.cat_file(['%s:%s' % (sha, plugin_layout.MANIFEST)], kit)
        base = plugin_export.kit_version(data['%s:%s' % (sha, plugin_layout.MANIFEST)][1]) if data else None
    except (plugin_layout.RefreshError, KeyError, TypeError):
        base = None
    if not base:
        out('old caches: cannot read the kit version at %s; see docs/plugin-source.md "Migrating"' % sha[:12])
        return
    out('old caches (a migrate never removes them; after the migrate is verified, '
        'plugin_refresh.py --prune-caches lists what it would remove, docs/plugin-source.md "Old caches"):')
    for f in folders:
        out('  %s' % old_cache(f, base))


def spawn_refresh(kit, root):
    """Start tools/plugin_refresh.py --apply detached and windowless, its output in <root>/refresh.log.
    Linux: in its own session, so a hangup or Ctrl-C that ends pm_watch does not end the refresh."""
    from kitlib.schedtask import pythonw
    os.makedirs(root, exist_ok=True)
    linux = kitplatform.NAME == 'linux'
    log = open(os.path.join(root, LOGFILE), 'ab')
    try:
        subprocess.Popen([pythonw.windowless(), os.path.join(kit, 'tools', 'plugin_refresh.py'), '--apply'],
                         cwd=kit, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                         close_fds=True, creationflags=0 if linux else job.no_window_flags(_DETACHED),
                         start_new_session=linux)
    finally:
        log.close()


def poll(kit, s, last, spawn=None, alive=None, folders=None):
    """One pm_watch check. Returns (lines, last): an event line the first time the kit's
    refs/remotes/<remote>/main differs from the exported sha (`last` is the main last reported, so
    the same sha is not reported twice), none when current. No export yet, or an account folder
    (`folders`, default accounts.account_folders()) not migrated: the line names the migrate command
    and auto_refresh starts nothing. Otherwise, with plugin.auto_refresh on, that first time also
    starts a refresh (`spawn(kit, root)`), unless one holds refresh.pid already. No tracking ref:
    nothing. Raises RefreshError when the tracking ref cannot be read and StateError when
    state.json cannot, so pm_watch counts the failure."""
    main, why = plugin_layout.tracking_main(plugin_layout.source_kit(kit), s.remote)
    if main is None:
        raise plugin_layout.RefreshError('cannot read %s/%s: %s' % (s.remote, plugin_layout.MAIN, why))
    if not main:
        return [], last
    state = plugin_state.read_state(s.export_dir)
    if state is not None and main == state.get('sha'):
        return [], None
    if main == last:
        return [], last
    todo = None if state is None else plugin_state.unmigrated(state, plugin_state._folders(folders))
    if state is None or todo:
        line = 'pm-kit plugin behind kit main %s: %s' % (main[:12], plugin_layout.refresh_cmd(True))
        if s.auto_refresh:
            line += (' (auto_refresh: not started: no export yet)' if state is None else
                     ' (auto_refresh: not started: not migrated in %s)' % ', '.join(todo))
        return [line], main
    line = 'pm-kit plugin behind kit main %s: %s' % (main[:12], plugin_layout.refresh_cmd())
    if s.auto_refresh:
        if lock_live(s.export_dir, alive):
            line += ' (auto_refresh: a refresh is already running)'
        else:
            try:
                (spawn or spawn_refresh)(kit, s.export_dir)
                line += ' (auto_refresh: started)'
            except OSError as e:
                line += ' (auto_refresh: could not start it: %s)' % e
    return [line], main
