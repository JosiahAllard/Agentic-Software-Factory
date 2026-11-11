"""Reading scheduled tasks back. Read only.

`read_tasks` reads every task's actions from `schtasks /Query /XML ONE` (`xml_input` says how its
bytes are read, `parse` what it gives); `task_registered` asks for one task by name
(`schtasks /Query /TN`). On Linux both read the pm-kit jobs (kitlib.platform.linux.schedule):
`read_tasks` gives every job read back in the same form, for the readers that look tasks up by
name. `words` splits a task line into the words that may be paths.
"""
import codecs
import re
import subprocess
import xml.etree.ElementTree as ET

from kitlib import platform
from kitlib.proc import spawn

TIMEOUT_S = 4       # the session start's own TIMEOUT (kitlib.proc.listing.TIMEOUT)
TASK_TIMEOUT = 30
NS = '{http://schemas.microsoft.com/windows/2004/02/mit/task}'
BOMS = (codecs.BOM_UTF8, codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)
DECLARATION = re.compile(rb'^\s*<\?xml[^>]*?\?>')
DECLARED = re.compile(rb'encoding\s*=\s*["\']([A-Za-z0-9._-]+)["\']')
WORD = re.compile(r'"([^"]*)"|(\S+)')
OPTION = re.compile(r'^--?[A-Za-z][\w-]*[:=]')     # `-opt:` / `--opt=` before a path


def words(text):
    """Each word of a task line that may be a path: a double-quoted run whole (a path with a
    space) and each of its own words, then each word with a leading `&` (PowerShell's call
    operator), quotes and an option's `-opt:` / `--opt=` prefix taken off, so
    `-Command "& '<kit>\\tools\\x.ps1'"` and `--flag=<kit>\\x` give the path."""
    out = []
    for m in WORD.finditer(text):
        whole = m.group(1) if m.group(1) is not None else m.group(2)
        for w in [whole] + whole.split():
            w = w.lstrip('&').strip('"\';')
            w = OPTION.sub('', w, count=1).strip('"\'')
            if w:
                out.append(w)
    return out


def xml_input(raw):
    """What `parse` reads for schtasks' output bytes `raw`. Bytes that start with a byte order mark
    are given to the XML parser as they are, so it reads the declaration and the encoding itself;
    UTF-16 with no mark (a NUL among the first bytes) gets its mark first. Single-byte output is
    text in the console code page whatever its declaration says: `schtasks /Query /XML` declares
    encoding="UTF-16" and writes one byte per character to a pipe (measured on Windows 11), and
    `/XML ONE` writes no declaration. So such bytes go to the parser as bytes only when they
    declare a single-byte encoding; otherwise they are decoded as the OEM code page (the console
    code page of a process with no console of its own) with the declaration dropped."""
    if raw.startswith(BOMS):
        return raw
    if b'\0' in raw[:64]:
        return (codecs.BOM_UTF16_BE if raw[:1] == b'\0' else codecs.BOM_UTF16_LE) + raw
    m = DECLARATION.match(raw)
    if m:
        enc = DECLARED.search(m.group(0))
        name = enc.group(1).decode('ascii').lower().replace('-', '').replace('_', '') if enc else ''
        if name and not name.startswith(('utf16', 'ucs2', 'utf32')):
            return raw
        raw = raw[:m.start()] + raw[m.end():]
    return raw.decode('oem', errors='replace')


def parse(data):
    """[{"path", "actions": [{"exe", "args"}]}] for every task in `schtasks /Query /XML ONE`'s
    output (`data`: text, or bytes as `xml_input` gives them): the path from the comment before
    each <Task> (its URI when there is none), the Exec actions only. Raises ET.ParseError on data
    that is not that XML."""
    root = ET.fromstring(data, ET.XMLParser(target=ET.TreeBuilder(insert_comments=True)))
    tasks, path = [], None
    for el in root:
        if el.tag is ET.Comment:
            path = (el.text or '').strip()
            continue
        if el.tag != NS + 'Task':
            continue
        uri = el.findtext('%sRegistrationInfo/%sURI' % (NS, NS))
        actions = [{'exe': (e.findtext(NS + 'Command') or '').strip(),
                    'args': (e.findtext(NS + 'Arguments') or '').strip()}
                   for e in el.iter(NS + 'Exec')]
        tasks.append({'path': path or uri or '?', 'actions': actions})
        path = None
    return tasks


def read_tasks(run=None, timeout=TIMEOUT_S):
    """Every registered task's Exec actions (`parse` of `xml_input`), from `schtasks /Query /XML
    ONE`. Raises RuntimeError when schtasks fails or times out, OSError when it cannot be run,
    ET.ParseError on output that is not the XML. run: the tests' stand-in for spawn.run. On Linux:
    schedule.read_back (ScheduleError, a RuntimeError, when the crontab cannot be read)."""
    if platform.NAME == 'linux':
        from kitlib.platform.linux import schedule
        return schedule.read_back(run=run)
    try:
        r = (run or spawn.run)(['schtasks', '/Query', '/XML', 'ONE'], capture_output=True,
                               timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError('schtasks /Query /XML timed out after %s s' % timeout)
    if r.returncode != 0:
        said = ((r.stdout or b'') + (r.stderr or b'')).decode('oem', errors='replace').strip()
        raise RuntimeError('schtasks /Query /XML exit %d: %s' % (r.returncode, said[:200] or 'no output'))
    return parse(xml_input(r.stdout or b''))


def _not_found_text():
    """Windows' localised text for ERROR_FILE_NOT_FOUND (2), which schtasks /Query prints for a
    task that does not exist; None off Windows."""
    try:
        import ctypes
        return ctypes.FormatError(2).strip() or None
    except (ImportError, AttributeError, OSError):
        return None


def task_registered(name, timeout=TASK_TIMEOUT, run=None):
    """Whether the scheduled task `name` is registered (`schtasks /Query /TN name`): True on exit 0,
    False only when schtasks says the task does not exist (its output holds Windows' text for error
    2, file not found). Any other failure (access denied, a timeout after `timeout` seconds) raises
    RuntimeError, never False; OSError when schtasks cannot be run. run: the tests' subprocess.run.
    On Linux: whether the pm-kit job `name` is written (schedule.exists; ScheduleError, a
    RuntimeError, when the crontab cannot be read)."""
    if platform.NAME == 'linux':
        from kitlib.platform.linux import schedule
        return schedule.exists(name, run=run)
    run = run or subprocess.run
    try:
        r = run(['schtasks', '/Query', '/TN', name], capture_output=True, text=True, timeout=timeout,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except subprocess.TimeoutExpired:
        raise RuntimeError('schtasks /Query timed out after %s s' % timeout)
    if r.returncode == 0:
        return True
    said = ' '.join(((r.stderr or '') + ' ' + (r.stdout or '')).split())
    gone = _not_found_text()
    if gone and ' '.join(gone.split()).lower() in said.lower():
        return False
    raise RuntimeError('schtasks /Query exit %d: %s' % (r.returncode, said[:200] or 'no output'))
