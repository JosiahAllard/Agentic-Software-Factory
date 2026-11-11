"""The hook runner behind boot/hook.py, the one hook entry of every host (main: parse, decide,
degrade, render, fail closed; every hook command line), the kit header every session and subagent
start adds to the context (header), and that header after a plugin refresh (headerfollow).

Import-free on purpose: boot/hook.py imports main before the guard, and run() imports the guard
late, so a guard that will not import is a deny rather than a crash.
"""
