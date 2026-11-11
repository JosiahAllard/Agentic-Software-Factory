"""The pieces of the one PowerShell line that registers a kit scheduled task. Each feature writes
its own line from these: `$py` is the task's pythonw as a literal path (kitlib.schedtask.pythonw),
and the line throws, registering nothing, when that path is under WindowsApps, so a hand-edited
copy is checked too.
"""
# PowerShell ends a single-quoted string at any of these, not only at ' (its own
# CodeGeneration.EscapeSingleQuotedStringContent doubles all five).
SINGLE_QUOTES = ("'", '‘', '’', '‚', '‛')
# `$py` is the resolved pythonw.exe, a literal (PYTHONW_IS % its quoted path).
PYTHONW_IS = '$py = %s'
NOT_THE_STORE_ALIAS = (r"if (-not $py -or $py -match '\\WindowsApps\\') { throw ('pythonw is ' + $py +"
                       r" ': no pythonw, or the Microsoft Store alias; nothing was registered') }")


def quoted(text):
    """`text` as a PowerShell single-quoted string: every single-quote character doubled."""
    for q in SINGLE_QUOTES:
        text = text.replace(q, q + q)
    return "'" + text + "'"
