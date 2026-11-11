"""kitlib.hubsite.cli.setup: the setup and validate verbs of tools/hub.py.

  hub.py setup [--autostart [--whatif]]
      makes the two token files where they are absent and prints where they are and how to start
      the server. It writes nothing else: never the machine config, so no `hub` key (it says so
      when no store folder is set). --autostart (opt-in) also registers the scheduled task
      kitlib.hubsite.autostart.TASK_NAME, which runs this hub.py's `serve` at sign-in with no
      console window; refused, before any file is written, from a kit worktree or with no
      pythonw.exe beside python. --whatif prints the task's PowerShell and any refusal and
      changes nothing, not even a token file.
  hub.py setup --check
      reads, never writes (no token file, no machine config, no task): it says whether the
      machine is ready for the `hub` key and the tunnel. One line for hub.access: `access: not
      set (hub.access)`, `access: set`, or `access: cannot serve: ` and the fixed phrase of
      kitlib.hubsite.tunnel.check. With settings that can serve, one fetch of the team's Access
      keys (kitlib.hubsite.access.probe, its `access keys: ` line). Then one line for each kit
      copy on this machine (kitlib.config.copies: the plugin export folders, the pins and each
      account folder's install, kitlib.pluginsource.state.installs): `knows every key`, `would refuse `
      and the keys, or `cannot tell: ` and why (an install whose folder is gone names the
      refresh command), checked against the machine config's keys plus `hub`; a copy that
      refuses a key refuses the whole file. Last, `READY: ...` and exit 0, or `NOT READY: ...`
      and exit 1: a copy in use would refuse a key or cannot be read, the Access settings
      cannot serve, or the keys were not fetched. Export folders not in use decide nothing; a
      NOTE counts those that would refuse. Refresh the plugin and restart the sessions of
      older copies before setting `hub` (docs/hub-remote-access.md "Before you start").

  hub.py validate [--store PATH]
      reads the store folder itself (hub.store, else hub/ in the PM data worktree), with no
      server and no lock, so it may run beside one; it writes nothing. Prints each finding as
      `VALIDATE <LEVEL> <document> <field>: <message> [<code>]` and a summary line. Exit 1 when
      a finding is an error.
"""
import os

from kitlib import hubvalidate
from kitlib.config import copies, user
from kitlib.hubclient import client, settings
from kitlib.hubclient import errors as huberrors
from kitlib.hubclient import token as hubtoken
from kitlib.hubsite import access, auth, storecheck, tunnel
from kitlib.hubsite import autostart as hub_autostart
from kitlib.hubsite.cli import common, task


def check(out):
    """setup --check: reads only. 0 when ready, 1 when not."""
    from kitlib.pluginsource import layout as plugin_layout, state as plugin_state
    s = settings.load()
    why = []
    try:
        tunnel.check(s.access)
    except tunnel.TunnelConfigError as e:
        if e.unset:
            out.line('access: not set (hub.access): the tunnel listener stays off')
        else:
            out.line('access: cannot serve: %s' % e)
            why.append('the Access settings cannot serve')
    else:
        out.line('access: set')
        ok, line = access.probe(s.access.team)
        out.line(line)
        if not ok:
            why.append('the Access keys were not fetched')
    keys = copies.config_keys(user.readable_file(), (settings.KEY,))
    try:
        root = plugin_layout.load().export_dir
    except plugin_layout.RefreshError as e:
        out.line('kit copies: cannot look: %s' % e)
        why.append('the kit copies could not be looked at')
    else:
        report = copies.check(copies.find(root, plugin_layout.current_folder(root),
                                          plugin_state.installs()), keys,
                              plugin_layout.refresh_cmd())
        out.line('kit copies, checked against the keys %s:' % ', '.join(keys))
        for line in report.lines:
            out.line('  %s' % line)
        if not report.ready:
            why.append('a kit copy in use would refuse a key or cannot be read')
        if report.stale:
            out.line('NOTE: %d export folder(s) not in use would refuse a key or cannot be read: '
                'a session started from one before the last refresh still runs it; restart '
                'such sessions before setting %s' % (report.stale, settings.KEY))
    if why:
        out.line('NOT READY: %s' % '; '.join(why))
        return client.REFUSED
    out.line('READY: every kit copy in use knows these keys, and the Access settings serve or are '
        'not set')
    return 0


def setup(a, out, stop=None):
    if a.check:
        return check(out)
    if (a.autostart or a.whatif) and not settings.load().store:
        raise huberrors.ServeError('no store folder is set, so a server started at sign-in would '
                               'fail: set hub.store or pm_data in the machine config; '
                               'nothing registered or written')
    if a.whatif:
        for line in hub_autostart.install(common.TOOL, whatif=True):
            out.line(line)
        return 0
    why = hub_autostart.refusals(common.TOOL) if a.autostart else []
    if why:     # before any token file is written
        raise hub_autostart.AutostartError('; '.join(why) + '; nothing registered or written')
    s = settings.load()
    folder = hubtoken.folder()
    try:
        paths = auth.ensure_tokens(folder)
    except OSError as e:
        raise huberrors.ServeError('cannot write the token files in %s: %s' % (folder, e))
    for role in (auth.OWNER, auth.AGENT):
        out.line('%s token: %s' % (role, paths[role]))
    if s.store:
        out.line('store: %s' % s.store)
    else:
        out.line('NOTE: no store folder is set: set hub.store or pm_data in the machine config '
            '(this tool does not write it), or start with --store PATH')
    if a.autostart:
        if not task.linux():
            out.line('start the server detached, once the task is registered: %s' % task.START_TASK)
        for line in hub_autostart.install(common.TOOL):
            out.line(line)
        if task.linux():
            # said after the install: which job it wrote (systemd service or XDG entry) decides it
            if task.xdg():
                out.line('the XDG autostart entry starts the server at your next sign-in to the '
                    'desktop; to start it now: %s' % common.START)
            else:
                out.line('start the server detached, once the job is registered: %s'
                    % task.start_command())
    else:
        out.line('start the server with: %s' % common.START)
    return 0


def store_root(a):
    root = os.path.abspath(a.store) if a.store is not None else settings.load().store
    if not root:
        raise huberrors.ServeError('no store folder is set: set hub.store or pm_data in the machine '
                               'config, or give --store PATH')
    return root


def validate(a, out, stop=None):
    try:
        findings = storecheck.validate(store_root(a))
    except storecheck.StoreReadError as e:
        raise huberrors.ServeError(str(e))
    for line in storecheck.validate_lines(findings):
        common.put(out, line)
    return 1 if any(f.level == hubvalidate.ERROR for f in findings) else 0
