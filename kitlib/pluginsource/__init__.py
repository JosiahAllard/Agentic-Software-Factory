"""The plugin's source: an export of pushed kit main that every account folder's Claude Code
installs from (docs/plugin-source.md; docs/modules/install-design.md "Packages and files").

  layout    where the export is and its marker: the settings, the export root, the marker, the
            live pointer, the marketplace file's path; RefreshError and StateError
  export    writing one: the tracked files at a commit, stamped, and the marketplace file
  state     each account's state: state.json, the pending flags, the account folders, the
            session start's lines
  prune     pruning old export folders and plugin cache versions, by what is in use
  refresh   the refresh under its lock, ensure_export and pm_watch's poll

Import the module that holds a name; this package re-exports none.
"""
