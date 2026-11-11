"""Each account folder's state against the export (docs/plugin-source.md): state.json, the host
copy steps' pending flags, the account folders, and the session start's lines.

  read_state / write_state         <root>/state.json: {sha, version, folder, exported_at, folders}
  set_pending / clear_pending / pending
                                   <root>/<label>.pending: a host copy step's deferred install
  pick_folders(folders, wanted)    the account folders --folder names, and the names that are not one
  installs(folders)                [(install path, account folder)] of each account's kit install
  marketplace_mismatches(export_dir, folders, managed)
                                   lines where a marketplace path is not the export (read only)
  current_lines(kit, s, folders)   the pm_start warning lines (no export, not migrated, behind, failed, marketplace off the export)
"""
import json
import os

from kitlib.pluginsource import layout as plugin_layout


STATE = 'state.json'
PENDING = '.pending'                 # <root>/<label>.pending: a host copy step deferred (set_pending)


def read_state(root):
    """state.json as a dict, None when there is none; StateError when it cannot be read."""
    path = os.path.join(root, STATE)
    try:
        with open(path, encoding='utf-8-sig') as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        raise plugin_layout.StateError('cannot read %s: %s' % (path, e))
    if not isinstance(data, dict) or not isinstance(data.get('folders', {}), dict):
        raise plugin_layout.StateError('%s is not a state object' % path)
    data.setdefault('folders', {})
    return data


def write_state(root, state):
    plugin_layout._write_json(os.path.join(root, STATE), state)


def set_pending(root, label, line):
    """<root>/<label>.pending holds `line`: the host copy step `label` deferred its install (Codex:
    the app was running). The hub's plugin job retries the refresh while one is there and
    current_lines names it; the step clears it (clear_pending). OSError when it cannot be written."""
    path = os.path.join(root, label + PENDING)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write(line + '\n')
    os.replace(tmp, path)


def clear_pending(root, label):
    """Remove <root>/<label>.pending; none there is no error. OSError when it cannot be removed."""
    try:
        os.remove(os.path.join(root, label + PENDING))
    except FileNotFoundError:
        pass


def pending(root):
    """{label: line} of each <root>/<label>.pending (set_pending); {} when there is none or the
    root cannot be listed. A flag that cannot be read still counts, its line a placeholder."""
    try:
        names = os.listdir(root)
    except OSError:
        return {}
    out = {}
    for n in names:
        if not n.endswith(PENDING) or len(n) == len(PENDING):
            continue
        try:
            with open(os.path.join(root, n), encoding='utf-8-sig') as fh:
                out[n[:-len(PENDING)]] = fh.read().strip() or 'deferred'
        except OSError as e:
            out[n[:-len(PENDING)]] = 'deferred (%s)' % e
    return out


def folder_key(folder):
    """The key an account folder is matched by (state.json keys, --folder names)."""
    return plugin_layout._norm(folder)


def _record(state, folder):
    """state.json's record of `folder` ({} when none)."""
    k = folder_key(folder)
    for f, r in (state or {}).get('folders', {}).items():
        if folder_key(f) == k and isinstance(r, dict):
            return r
    return {}


def migrated(state, folder):
    """True when `folder`'s last migrate succeeded (its record holds `migrated: true`)."""
    return _record(state, folder).get('migrated') is True


def _carry(state):
    """The folder records a new export's state starts with: each migrated folder, as migrated."""
    return {f: {'migrated': True} for f, r in (state or {}).get('folders', {}).items()
            if isinstance(r, dict) and r.get('migrated') is True}


def _set_record(state, folder, rec):
    k = folder_key(folder)
    for f in [f for f in state['folders'] if folder_key(f) == k]:
        del state['folders'][f]
    state['folders'][folder] = rec


def pick_folders(folders, wanted):
    """([account folder], [name]): the folders of `folders` that `wanted` (--folder) names, each
    once and in the order named, and the names that are not one of `folders`. Nothing wanted:
    every folder."""
    if not wanted:
        return list(folders), []
    by = {folder_key(f): f for f in folders}
    picked, bad = [], []
    for w in wanted:
        f = by.get(folder_key(w))
        if f is None:
            bad.append(w)
        elif f not in picked:
            picked.append(f)
    return picked, bad


# ---------------------------------------------------------------- the account folders and pm_start

def _host():
    from kitlib.launcher import accounts
    return accounts


def _folders(folders):
    if folders is None:
        folders = _host().account_folders()
    return list(folders)


def account_folders(home=None):
    """This machine's account folders on the plugin's host (the ones `folders=None` means here);
    `home` (default the user's home) as plugin_refresh.py --home names it."""
    return list(_host().account_folders(home)) if home else _folders(None)


def installs(folders=None):
    """[(install path, account folder)]: where each account folder of `folders` (default every
    one) loads the kit plugin from, for the folders that have it installed. Reads each folder's
    install record only."""
    return _host().installs(_folders(folders))


def marketplace_mismatches(export_dir, folders=None, managed=None):
    """Lines for each place pointing the pm-kit marketplace off `export_dir`: each account folder's
    (`folders`, default every one) known marketplaces record and the managed settings file
    `managed` (None: not checked). Reads only; never raises."""
    return _host().marketplace_mismatches(export_dir, _folders(folders), managed=managed)


def unmigrated(state, folders):
    """The account folders of `folders` whose state record does not say `migrated: true`."""
    return [f for f in folders if not migrated(state, f)]


def _marketplace_lines(export_dir, folders, managed):
    """marketplace_mismatches for current_lines: `managed` is the managed settings file, None, or
    the caller's zero-argument reader of its path, called here. Never raises: a session start
    must not break over a report, so any failure is one 'could not check' line."""
    try:
        if callable(managed):
            managed = managed()
        return marketplace_mismatches(export_dir, folders, managed=managed)
    except Exception as e:                       # never break a session start over a report
        return ['could not check the pm-kit marketplace paths: %s: %s' % (type(e).__name__, e)]


def current_lines(kit, s, folders=None, managed=None):
    """pm_start lines: none when the export is at the kit's refs/remotes/<remote>/main, every
    account folder (`folders`, default accounts.account_folders()) is migrated and its last refresh
    succeeded, no host copy step's install is pending (a line each: pending), and the pm-kit
    marketplace points at the export. The refresh checks read the export
    state only: a folder's last refresh result stands in for what it has installed. No export yet,
    or a folder not migrated: the line names the migrate command, not --apply alone (which refuses
    such a folder). The marketplace check (marketplace_mismatches) reads each migrated folder's
    known marketplaces record and the managed settings file `managed` (its path, or a zero-argument
    reader of the path that is called inside the check; None: not checked); it never raises."""
    try:
        state = read_state(s.export_dir)
    except plugin_layout.StateError as e:
        return ['could not check the pm-kit plugin: %s' % e]
    if state is None:
        return ['pm-kit plugin: no export at %s yet: %s' % (s.export_dir, plugin_layout.refresh_cmd(True))]
    try:
        main, why = plugin_layout.tracking_main(plugin_layout.source_kit(kit), s.remote)
    except plugin_layout.RefreshError as e:
        return ['could not check the pm-kit plugin: %s' % e]
    if main is None:
        return ['could not check the pm-kit plugin: %s' % why]
    folders = _folders(folders)
    todo = unmigrated(state, folders)
    cmd = plugin_layout.refresh_cmd(bool(todo))
    lines = []
    if todo:
        lines.append('pm-kit plugin: not migrated to the export in %s: %s (one folder first: '
                     '--folder DIR)' % (', '.join(todo), plugin_layout.refresh_cmd(True)))
    if main and main != state.get('sha'):
        lines.append('pm-kit plugin behind kit main (exported %s, %s/%s %s): %s'
                     % (str(state.get('sha') or '?')[:12], s.remote, plugin_layout.MAIN, main[:12], cmd))
    for f in sorted(folders):       # only the folders asked about: a retired folder's record is no line
        r = _record(state, f)
        if r.get('migrated') is not True or r.get('rc') == 0:
            continue
        if r.get('rc') is None:     # migrated, carried into this export, never refreshed to it
            lines.append('pm-kit plugin in %s: not refreshed to %s: %s'
                         % (f, str(state.get('sha') or '?')[:12], plugin_layout.refresh_cmd()))
        else:
            lines.append('pm-kit plugin refresh failed in %s (claude %s exited %s at %s): %s'
                         % (f, r.get('step') or '?', r.get('rc'), r.get('at') or '?', plugin_layout.refresh_cmd()))
    for label, line in sorted(pending(s.export_dir).items()):
        lines.append('pm-kit plugin %s copy not refreshed: %s' % (label, line))
    lines.extend(_marketplace_lines(s.export_dir, [f for f in folders if f not in todo], managed))
    return lines
