"""kitlib.proc (ARCHITECTURE.md): process work for the kit, the same names on every platform it
runs on. The OS code is kitlib.platform's (the Windows modules in kitlib.platform.windows, the
Linux ones in kitlib.platform.linux); each module here binds the running platform's names.

    process   snapshots, creation times, ancestry, verified kills, affinity
    job       job objects: pinned, measured, killed as a tree
    console   has_console, and the CreateProcess hook `import kitlib` installs: a process with
              no console gets one with no window before its first bare start; its incident log
    tcp       the machine's TCP connections and listeners, each with its owner
    spawn     subprocess's run, call and Popen with no console window
    bash      a bare `bash` names Git Bash, never WSL's
    daemons   the build daemons a build leaves running
"""
