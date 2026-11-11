"""kitlib.hubsite.hublog: the hub's own log file, and the appending the hub's log files share.

  append(path, line, cap)   add one line to a file, moved to `<path>.1` first once it holds
                            `cap` bytes or more (one older file is kept). OSError.
  HubLog(path)              the hub log: .write(level, text) adds one line `<UTC ISO time>
                            <LEVEL> <text>`, never raises; .filed(level, text, out) writes the
                            levels FILED and hands every line on to `out`
  LOG_NAME, LOG_MAX, FILED

The hub log is LOG_NAME in the token folder (kitlib.hubsite.cli.server serve wires it). It holds
the server's 'error' and 'note' lines, which the console shows too, and the local listener's
'slow' lines (kitlib.hubsite.httpd, kitlib.hubsite.serve): one a request that took httpd.SLOW_S
seconds or longer. The token folder is guarded, so only the owner reads it. No request line and no
header ever reaches it (httpd's 'access' lines are not filed). Each line goes through
gitsync.scrub, so none holds the user or password of a URL; a line's newlines are kept as
`\\n` so one entry is one line. A file that cannot be written loses the line, never the
request or the server.
"""
import datetime
import os
import threading

from . import gitsync

LOG_NAME = 'hub.log'
LOG_MAX = 1 << 20           # bytes: hub.log moves to hub.log.1 past this
FILED = ('error', 'note', 'slow')


def append(path, line, cap):
    """Add `line` and a newline to the file at `path`, after moving it to `path + '.1'` (which
    it replaces) when it holds `cap` bytes or more. A file that is not there yet is made. A
    move that fails is skipped; a write that fails raises OSError. A character UTF-8 cannot
    hold (a lone surrogate) is written as `?`."""
    try:
        if os.path.getsize(path) >= cap:
            os.replace(path, path + '.1')
    except OSError:
        pass
    with open(path, 'a', encoding='utf-8', errors='replace', newline='\n') as f:
        f.write(line + '\n')


def _utc_now():
    return datetime.datetime.now(datetime.timezone.utc)


class HubLog(object):
    """The hub log at `path` (module docstring). `clock` gives an aware datetime; `cap` is the
    size it rotates at. One writer at a time, so two lines never interleave."""

    def __init__(self, path, clock=_utc_now, cap=LOG_MAX):
        self.path = path
        self.clock = clock
        self.cap = cap
        self._lock = threading.Lock()

    def write(self, level, text):
        """One line; True when it was written, False when the file could not be."""
        stamp = self.clock().astimezone(datetime.timezone.utc).isoformat(timespec='seconds')
        said = gitsync.scrub(str(text)).replace('\r', '').replace('\n', '\\n')
        try:
            with self._lock:
                append(self.path, '%s %s %s' % (stamp, str(level).upper(), said), self.cap)
            return True
        except (OSError, UnicodeError):     # never expected past errors='replace': still a lost line
            return False

    def filed(self, level, text, out=None):
        """The server's log(level, text): a level in FILED is written here, and every line is
        handed on to `out(level, text)` as it came (None: to nothing)."""
        if level in FILED:
            self.write(level, text)
        if out is not None:
            out(level, text)
