"""kitlib.hubsite.plugin_job: the hub server's plugin refresh job (docs/plugin-source.md "Hub
refresh").

  make(sources) -> jobs.Job      the job, found through kitlib/hubjobs/plugin_refresh.py
  PluginJob(home=None, ...)      its run(ctx): one check, and the refresh when it is due

Every EVERY_S the job reads the kit's refs/remotes/<remote>/main and the export's state.json,
and runs `<kit>/tools/plugin_refresh.py --apply` as a child process, waiting for it, when the export is behind that main, a migrated
account folder's last refresh at it did not go through, or a host copy step left its install
pending (kitlib.pluginsource.state pending: Codex deferred while it ran). A child, not the library in this
process: the hub keeps the code it started with, and a landing that changes the refresh must be
run by the new code. The child does the whole refresh, the host copy steps included (so the
Codex copy too, except from a session PM_KIT_ROLE marks: docs/plugin-source.md "Codex").

A run that does nothing returns jobs.Skip, which leaves the failure count alone: no tracking ref,
no export yet, a folder not migrated, plugin.auto_refresh off, a live process holding refresh.pid
(a refresh by hand), a child that found refresh.pid taken, or a retry not due.
A child that exits non-zero, or runs past REFRESH_TIMEOUT_S (then it is ended with every
process it started), is a failure (jobs: its text in health, `failing` pushed at the third in a
row); the same main is tried again RETRY_S after it failed, not sooner, and the runs between are
skips (`waiting to retry`). A child that exits 0 with a host copy still pending is no failure,
but it too is tried again only RETRY_S later. The child's output is
appended to <export root>/refresh.log, under a header line.

The three skips the owner must act on push the PM BEHIND (`job plugin-refresh behind`, detail
`<main sha12>:<why>`; docs/hub-push.md "Timed jobs"), once for each kit main: no export yet
(why no-export) and a folder not migrated (unmigrated), both fixed by `--migrate --apply`, and
plugin.auto_refresh off while the export is behind that main (behind), fixed by `--apply`. A
folder not refreshed or a host copy deferred at that main pushes nothing: the job's own next
refresh, or the owner's, is the fix. The refresh path, auto_refresh on, pushes nothing.
"""
import datetime
import os
import subprocess
import sys
import time

from kitlib.pluginsource import (
    layout as plugin_layout, state as plugin_state, refresh as plugin_refresh)
from kitlib.proc import job, process     # loaded now: refresh.pid reads it mid-run; kill_tree
from kitlib.proc import spawn
from kitlib.hubsite import jobs

NAME = 'plugin-refresh'
EVERY_S = 300
RETRY_S = 1800
REFRESH_TIMEOUT_S = 1800
KILL_WAIT_S = 10                    # after a timeout's kill: the pipe's last bytes
BUSY = 'a refresh is already running'
BEHIND = 'behind'                   # the PM's push (push.JOB_EACH)


class RefreshFailed(Exception):
    """A refresh that exited non-zero: its exit code and its last refusal or failure line."""


def make(sources):
    return jobs.Job(NAME, EVERY_S, PluginJob(home=getattr(sources, 'home', None)))


def _kit():
    kit, why = plugin_layout.default_kit(plugin_layout.kit_root())
    if why:
        raise plugin_layout.RefreshError(why)
    return kit


def _rc(state, folder):
    k = plugin_state.folder_key(folder)
    for f, r in (state.get('folders') or {}).items():
        if plugin_state.folder_key(f) == k and isinstance(r, dict):
            return r.get('rc')
    return None


def refresh_argv(kit, home=None):
    """The child's command line: this interpreter, the kit's tools/plugin_refresh.py, --apply."""
    argv = [sys.executable, os.path.join(kit, 'tools', 'plugin_refresh.py'), '--apply']
    if home:
        argv += ['--home', home]
    return argv


def run_child(argv, cwd, start=None, kill_tree=None, ctime=None):
    """(exit code, output lines) of one windowless child (PYTHONIOENCODING=utf-8, so a non-ASCII
    path reads right in refresh.log), waited for up to REFRESH_TIMEOUT_S. Past it the child and
    every process it started (the host's own programs that write the account folders) are ended,
    as the gate's runner does, and RefreshFailed raised: a child killed alone would leave its
    refresh.pid stale while its children still wrote. `start`, `kill_tree` and `ctime` are the
    tests' seams (default kitlib.proc.spawn.popen, kitlib.proc.process kill_tree, proc_ctime)."""
    start = start or spawn.popen
    kill_tree = kill_tree or process.kill_tree
    ctime = ctime or process.proc_ctime
    env = dict(os.environ, PYTHONIOENCODING='utf-8')
    p = start(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
              stderr=subprocess.STDOUT,
              # console=False: the child must not share or inherit the parent's console
              creationflags=job.no_window_flags(0, console=False))
    born = ctime(p.pid)
    try:
        out, _ = p.communicate(timeout=REFRESH_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        if born is not None:
            kill_tree(p.pid, born)
        p.kill()
        try:
            p.communicate(timeout=KILL_WAIT_S)
        except subprocess.TimeoutExpired:
            pass
        raise RefreshFailed('the refresh did not end in %d s and was stopped' % REFRESH_TIMEOUT_S)
    return p.returncode, (out or b'').decode('utf-8', 'replace').splitlines()


class PluginJob(object):
    """run(ctx) is the job. Every part a test replaces is an argument: `kit` (default the
    checkout this kit runs from, or the one its export's marker names), `load(kit)` the plugin
    Settings, `folders()` the account folders (default kitlib.pluginsource.state.account_folders(home)),
    `child(argv, cwd)` -> (exit code, lines) (default run_child), `alive` refresh.pid's liveness
    test, `clock` epoch seconds. `home` is passed to the child as --home when given."""

    def __init__(self, home=None, kit=None, load=None, folders=None, child=None, alive=None,
                 clock=None):
        self.home = home
        self.kit = kit
        self.load = load or (lambda kit: plugin_layout.load(home, kit=kit))
        self.folders = folders or (lambda: plugin_state.account_folders(home))
        self.child = child or run_child
        self.alive = alive
        self.clock = clock or time.time
        self._failed = None             # (main, at, text) of the last refresh that failed
        self._told = None               # the kit main BEHIND was last pushed for

    def __call__(self, ctx):
        kit = self.kit or _kit()
        s = self.load(kit)
        main, why = plugin_layout.tracking_main(plugin_layout.source_kit(kit), s.remote)
        if main is None:
            raise plugin_layout.RefreshError('cannot read %s/%s: %s' % (s.remote, plugin_layout.MAIN, why))
        if not main:
            return jobs.Skip('no %s/%s tracking ref: nothing to check' % (s.remote, plugin_layout.MAIN))
        state = plugin_state.read_state(s.export_dir)       # StateError: a failure
        if state is None:
            self._behind(ctx, main, 'no-export')
            return jobs.Skip('no export yet: %s' % plugin_layout.refresh_cmd(True))
        folders = list(self.folders())
        todo = plugin_state.unmigrated(state, folders)
        if todo:
            self._behind(ctx, main, 'unmigrated')
            return jobs.Skip('not migrated in %s: %s' % (', '.join(todo), plugin_layout.refresh_cmd(True)))
        behind = state.get('sha') != main
        failed = [f for f in folders if _rc(state, f) != 0]
        deferred = sorted(plugin_state.pending(s.export_dir))
        if not behind and not failed and not deferred:
            self._failed = None
            return 'current at %s' % main[:12]
        what = ('behind kit main %s' % main[:12] if behind
                else 'not refreshed in %s' % ', '.join(failed) if failed
                else 'the %s copy deferred' % ', '.join(deferred))
        if not s.auto_refresh:
            if behind:
                self._behind(ctx, main, 'behind')
            return jobs.Skip('%s; plugin.auto_refresh is off: %s' % (what, plugin_layout.refresh_cmd()))
        if plugin_refresh.lock_live(s.export_dir, self.alive):
            return jobs.Skip('%s; %s' % (what, BUSY))
        last = self._failed
        if last is not None and last[0] == main and self.clock() - last[1] < RETRY_S:
            return jobs.Skip('%s; waiting to retry (at most every %d s) after: %s'
                             % (what, RETRY_S, last[2]))
        if ctx.stop.is_set():
            return jobs.Skip(what)
        try:
            code, lines = self._refresh(kit, s)
        except RefreshFailed as e:
            self._failed = (main, self.clock(), str(e))
            raise
        if code != 0 and any(BUSY in line for line in lines):
            return jobs.Skip('%s; %s' % (what, BUSY))
        if code != 0:
            said = [ln for ln in lines if ln.startswith('REFUSED') or 'FAILED' in ln or 'failed' in ln]
            text = 'the refresh to %s exited %s: %s' % (main[:12], code,
                                                       (said or lines or ['no output'])[-1])
            self._failed = (main, self.clock(), text)
            raise RefreshFailed(text)
        left = plugin_state.pending(s.export_dir)
        if left:
            # a host copy deferred (Codex running): not a failure, but tried again RETRY_S on
            text = '; '.join(left[k] for k in sorted(left))
            self._failed = (main, self.clock(), text)
            return 'refreshed to %s; %s' % (main[:12], text)
        self._failed = None
        return 'refreshed to %s' % main[:12]

    def _behind(self, ctx, main, why):
        """Push the PM BEHIND for `why` (module docstring), once for each kit main."""
        if self._told != main:
            self._told = main
            ctx.event(BEHIND, '%s:%s' % (main[:12], why))

    def _refresh(self, kit, s):
        """(exit code, lines) of one child refresh, its output appended to <export root>/refresh.log."""
        log = _Log(s.export_dir)
        try:
            log.write('hub plugin job %s' % datetime.datetime.now().isoformat(timespec='seconds'))
            argv = refresh_argv(kit, self.home)
            try:
                code, lines = self.child(argv, kit)
            except RefreshFailed as e:
                log.write('FAILED: %s' % e)
                raise
            except OSError as e:
                log.write('FAILED: the refresh could not start: %s' % e)
                raise RefreshFailed('the refresh could not start: %s' % e)
            for line in lines:
                log.write(line)
            log.write('hub plugin job: exit %s' % code)
        finally:
            log.close()
        return code, lines


class _Log(object):
    """refresh.log, appended to; a log that cannot be opened or written is dropped silently (the
    failure still reaches health through the job)."""

    def __init__(self, root):
        try:
            os.makedirs(root, exist_ok=True)
            self.fh = open(os.path.join(root, plugin_refresh.LOGFILE), 'a', encoding='utf-8',
                           errors='replace', newline='\n')
        except OSError:
            self.fh = None

    def write(self, line):
        if self.fh is None:
            return
        try:
            self.fh.write(line + '\n')
            self.fh.flush()
        except OSError:
            self.fh = None

    def close(self):
        if self.fh is not None:
            try:
                self.fh.close()
            except OSError:
                pass
            self.fh = None
