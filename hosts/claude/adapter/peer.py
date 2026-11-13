"""Claude Code's delivery of hub push notifications (docs/hub-push.md "Push notifications").

kitlib/hubsite/push.py decides what to send and when; this module sends it in the words of
kitlib.hubclient.push. Every live Claude Code
session publishes a registry record, <config folder>/sessions/<pid>.json (kitlib.launcher.pipes),
and listens for cross-session messages: on Windows on a named pipe, on Linux on a Unix socket
under a cc-socks folder (its messagingSocketPath either way). A message is two JSON lines written to
it: an auth frame carrying the session's peer token, then a user frame whose content is
wrapped in <cross-session-message from-mode="prompting">. The session takes it as its next
prompt. A live probe proved this with no sender endpoint, so the hub publishes no
registry entry of its own and gets no receipt back: `written` means the bytes reached the pipe.

  HubPush(settings)     ClaudeAdapter.push()'s object; settings is kitlib.launcher.settings
    deliver(recipient, text)   push.Sent(state, name, session, ambiguous); state is one of
                        push.WRITTEN, push.NOT_WRITTEN, push.UNCERTAIN, push.NO_SESSION
    is_pm(recipient)    the recipient is the PM's session (push sends the PM one message)
    resolve(recipient)  Target(folder, record, name, ambiguous), or None
  canonical_pipe(value) the lower-case pipe name the key file's name hashes; ValueError
                        (Windows pipes only, on any platform: the optional bridge imports it)
  canonical_socket(value)   the socket path the key file's name hashes on Linux, lexically
                        normalised (never resolved on disk); ValueError for anything not in a
                        cc-socks folder Claude Code itself would listen in (SOCK_DIRS, SOCK_NAME)
  canonical_address(value)  canonical_socket on Linux, else canonical_pipe
  sender_address(pipe)  the `from` address of a sender's pipe
  message_frames(token, sender, text, msg_id, mode)   the two frames, as objects (unchecked)
  build_packet(token, source_pipe, text)   the two frames, checked, as bytes
  write_once(pipe, pid, creation, packet, timeout)   one write, the state: on Windows by the
                        hidden helper (peer_worker.py) in a kill-on-close job; on Linux in this
                        process, through kitlib.platform.linux.unixsock.connect (the server's
                        SO_PEERCRED pid and its ctime checked) and a send loop within timeout

The wire format and the write are here once: optional/bridge/hosts/claude (peer.py, sessions.py)
imports canonical_pipe, sender_address, message_frames and write_once from this module (the bridge's
Linux socket half is still open, docs/linux-support-plan.md slice 15).

Resolution: a launcher key is its sessions.json name; `pm` is coordinators.json's pm_session_id
matched against the record's sessionId (a relaunched PM's fork matches by its id), then the names
PM_NAME and pm-session; anything else is
taken as a session name. Only a live record counts (its pid runs with the record's creation time:
launch.py stop leaves records behind). Several live records of one name: the one pipes.json names
when its pid and creation time match, else the newest creation time, flagged ambiguous.

The peer token is read from the key file at send time only, checked against the record's
creation time (both read through kitlib.launcher.pipes.start_text, so an lstart text's double
blank before a day 1-9 compares equal), put in the packet and dropped. It is never logged, returned or shown by repr.
"""
from collections import namedtuple
import hashlib
import json
import math
import os
from pathlib import Path
import posixpath
import queue
import re
import secrets
import subprocess
import sys
import threading
import time
from urllib.parse import quote
import uuid

from kitlib import platform
from kitlib.platform import ProcError
from kitlib.platform.linux import unixsock
from kitlib.hubclient import push
from kitlib.launcher import lookup, pipes, state
from kitlib.launcher.settings import LaunchError

PIPE = re.compile(r'\\\\\.\\pipe\\(?:(LOCAL)\\)?(cc-msg-[A-Za-z0-9_-]+)\Z', re.I)
LOCAL_PIPE = re.compile(r'\\\\\.\\pipe\\(?:LOCAL\\)?[A-Za-z0-9_-]{1,160}\Z', re.I)
SOURCE_PREFIX = r'\\.\pipe\LOCAL\cc-msg-kit-hub-'
# Linux: the folders and names Claude Code's own socket check takes (2.1.323); a uid in the folder
# must be this user's. A source address is a path under a root folder nobody but root can make.
SOCK_DIRS = (re.compile(r'/tmp/cc-socks(?:-(0|[1-9][0-9]*))?\Z'),
             re.compile(r'/run/user/(0|[1-9][0-9]*)/cc-socks\Z'))
SOCK_NAME = re.compile(r'(?:[0-9]+(?:-[0-9a-f]{8})?|[0-9a-f]{1,16})\.sock\Z')
SOURCE_SOCK_PREFIX = '/pm-kit/kit-hub-'
PM_NAMES = ('Project Manager', 'pm-session')    # kitlib.launcher.start.PM_DESKTOP_NAME, stop.PM_RC_NAME
TOKEN_LEN = 32
MAX_PACKET_BYTES = 256 * 1024
MAX_RESULT_BYTES = 2048
WRITE_TIMEOUT_S = 5
REAP_S = 3                      # _cleanup's wait for the helper, after its job is closed
JOIN_S = 1                      # and for each of its two pump threads
WORKER = Path(__file__).resolve().with_name('peer_worker.py')
PYTHON_FLAGS = ('-I', '-B')

Target = namedtuple('Target', 'folder record name ambiguous')


def canonical_pipe(value):
    """The pipe name as the key file's name hashes it: `local` and the cc-msg-* name lower case.
    ValueError for anything that is not a local cc-msg-* pipe."""
    if type(value) is not str:
        raise ValueError('invalid-local-peer-pipe')
    match = PIPE.fullmatch(value)
    if match is None:
        raise ValueError('invalid-local-peer-pipe')
    return '\\\\.\\pipe\\' + ('local\\' if match[1] else '') + match[2].lower()


def canonical_socket(value):
    """The socket path as the key file's name hashes it on Linux: posixpath.normpath of an
    absolute path (lexical, as Node's path.resolve; never realpath), whose folder is a cc-socks
    folder (SOCK_DIRS, any uid in it this user's) and whose name is SOCK_NAME. ValueError for
    anything else: a relative path, a leading //, a NUL, a .. part, a path past sun_path."""
    bad = ValueError('invalid-local-peer-socket')
    if (type(value) is not str or not value.startswith('/') or value.startswith('//')
            or '\0' in value or '..' in value.split('/')):
        raise bad
    path = posixpath.normpath(value)
    folder, name = posixpath.split(path)
    if SOCK_NAME.fullmatch(name) is None:
        raise bad
    for pattern in SOCK_DIRS:
        match = pattern.fullmatch(folder)
        if match is not None:
            break
    else:
        raise bad
    if match.group(1) is not None and match.group(1) != str(os.getuid()):
        raise bad
    if len(path.encode('ascii')) > unixsock.MAX_PATH:
        raise bad
    return path


def canonical_address(value):
    """The peer address as the key file's name hashes it on this platform: canonical_socket on
    Linux, canonical_pipe elsewhere. ValueError when it is not one."""
    return canonical_socket(value) if platform.NAME == 'linux' else canonical_pipe(value)


def encode_frame(value):
    """One compact JSON line, UTF-8."""
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8') + b'\n'


def source_pipe():
    """A fresh sender address nobody listens on: the hub takes no receipt. On Linux a socket path
    under /pm-kit, a root folder only root could make."""
    if platform.NAME == 'linux':
        return SOURCE_SOCK_PREFIX + secrets.token_hex(8) + '.sock'
    return SOURCE_PREFIX + secrets.token_hex(8)


def sender_address(pipe):
    """The `from` address a frame names for a sender's pipe."""
    return 'uds:' + quote(pipe, safe=':_/.-')


def message_frames(token, sender, text, msg_id, mode='prompting'):
    """The wire format, as two objects: the auth frame, then the user frame carrying text as a
    cross-session message from `sender` (sender_address) in `mode`. Unchecked: the caller
    validates and encodes (build_packet here, the optional bridge's Claude half)."""
    frame = {'msgV': 1, 'msg_id': msg_id, 'uuid': msg_id, 'type': 'user', 'priority': 'next',
             'from': sender, 'message': {'role': 'user', 'content':
             '<cross-session-message from="' + sender + '" from-mode="' + mode + '">\n' + text
             + '\n</cross-session-message>'}}
    return {'type': 'auth', 'token': token}, frame


def build_packet(token, source, text):
    """The auth frame, then the user frame carrying text as a cross-session message from
    `source` (a pipe name) in prompting mode. Text holding < or > is a ValueError, so it cannot
    close the wrapper."""
    if type(token) is not str or len(token) != TOKEN_LEN:
        raise ValueError('no usable peer token')
    if type(text) is not str or '<' in text or '>' in text:
        raise ValueError('push text must not hold < or >')
    return b''.join(encode_frame(f) for f in
                    message_frames(token, sender_address(source), text, str(uuid.uuid4())))


def key_path(folder, rec):
    """<pid>.<sha256 of canonical_address>.key in the record's folder; ValueError for an address
    that is not one."""
    pipe = canonical_address(rec.get('messagingSocketPath'))
    return os.path.join(folder, '%d.%s.key' % (rec['pid'], hashlib.sha256(pipe.encode('utf-8')).hexdigest()))


def read_token(folder, rec):
    """The record's peer token from its key file; ValueError (naming no secret) when the key is
    missing, unreadable, of another process or not a token."""
    try:
        with open(key_path(folder, rec), encoding='utf-8-sig') as f:
            key = json.load(f)
    except (OSError, ValueError):
        raise ValueError('no readable key file') from None
    start = pipes.proc_start(rec)
    facts = [key[k] for k in ('procStart', 'procStartFt') if isinstance(key, dict) and k in key]
    if start is None or not facts or any(pipes.start_text(v) != start for v in facts):
        raise ValueError('the key file is not this process\'s')
    token = key.get('peerToken')
    if type(token) is not str or len(token) != TOKEN_LEN:
        raise ValueError('no usable peer token')
    return token


def _usable(rec):
    if rec.get('peerProtocol') != 1:
        return False
    try:
        canonical_address(rec.get('messagingSocketPath'))
    except ValueError:
        return False
    return platform.NAME == 'linux' or LOCAL_PIPE.fullmatch(rec['messagingSocketPath']) is not None


def _read_result(stream, completed, limit=MAX_RESULT_BYTES):
    data = bytearray()
    try:
        while len(data) <= limit:
            chunk = stream.read(min(4096, limit + 1 - len(data)))
            if not chunk:
                completed.put(('read', bytes(data)))
                return
            data.extend(chunk)
        completed.put(('fault', None))
    except Exception:
        completed.put(('fault', None))


def _write_input(stream, data, completed):
    try:
        view = memoryview(data)
        while view:
            count = stream.write(view)
            if not count:
                raise OSError('closed input')
            view = view[count:]
        stream.close()
        completed.put(('write', None))
    except Exception:
        completed.put(('fault', None))


def _cleanup(job, process, threads):
    """End the helper's job first (kill-on-close), then reap it and its pumps. False when that
    could not be confirmed."""
    ok = True
    try:
        job.close()
    except OSError:
        ok = False
    if process is not None:
        try:
            process.wait(timeout=REAP_S)
        except subprocess.TimeoutExpired:
            return False
        for t in threads:
            if t.ident is not None:
                t.join(timeout=JOIN_S)
        ok = not any(t.is_alive() for t in threads)
        for stream in (process.stdin, process.stdout):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    ok = False
    return ok


def _decode(raw):
    """The one JSON object a helper wrote, or None."""
    if raw.count(b'\n') != 1 or not raw.endswith(b'\n'):
        return None
    try:
        value = json.loads(raw.decode('utf-8'))
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def write_once(pipe, pid, creation, packet, timeout=WRITE_TIMEOUT_S):
    """Write packet once to the pipe served by pid with that creation time, through the helper
    in a kill-on-close job, and return the state: push.WRITTEN (all bytes taken), push.NOT_WRITTEN
    (a bad target, no helper, or the pipe's server is not that process: nothing was sent), else
    push.UNCERTAIN (a write was tried and its end is unknown; never re-sent at once). On Linux
    `pipe` is the session's socket path and creation the kit's ctime (_write_socket)."""
    if platform.NAME == 'linux':
        return _write_socket(pipe, pid, creation, packet, timeout)
    from kitlib.proc.job import Job
    if (type(pipe) is not str or LOCAL_PIPE.fullmatch(pipe) is None
            or type(creation) is not int or not 0 < creation < 2 ** 64
            or not _request_ok(pid, packet, timeout)):
        return push.NOT_WRITTEN
    if not WORKER.is_file():
        return push.NOT_WRITTEN
    meta = encode_frame({'pipe': pipe, 'pid': pid, 'creation': creation, 'length': len(packet)})
    if len(meta) > MAX_RESULT_BYTES:
        return push.NOT_WRITTEN
    job, process, threads = None, None, []
    try:
        job = Job(kill_on_close=True)
        process = job.start([sys.executable, *PYTHON_FLAGS, str(WORKER)], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, close_fds=True,
                            bufsize=0, creationflags=subprocess.CREATE_NO_WINDOW)
    except Exception:
        if job is not None:
            _cleanup(job, process, threads)
        return push.NOT_WRITTEN             # no helper ran: nothing was sent
    result = push.UNCERTAIN
    try:
        result = _pump(process, meta + packet, len(packet), timeout, threads)
    except Exception:
        result = push.UNCERTAIN
    finally:
        if not _cleanup(job, process, threads) and result == push.NOT_WRITTEN:
            result = push.UNCERTAIN         # a helper not seen to end: its write is unknown
    return result


def _request_ok(pid, packet, timeout):
    """write_once's checks both platforms share: a pid, a packet of 1 to MAX_PACKET_BYTES bytes
    and a timeout of at most 10 s."""
    return (type(pid) is int and 0 < pid <= 0xffffffff
            and type(packet) is bytes and 0 < len(packet) <= MAX_PACKET_BYTES
            and type(timeout) in (int, float) and math.isfinite(timeout) and 0 < timeout <= 10)


def _write_socket(path, pid, creation, packet, timeout):
    """write_once on Linux: connect (unixsock.connect: this user's socket in a safe folder, its
    server's SO_PEERCRED pid that pid and that pid's ctime `creation`), then send the packet in a
    loop against one deadline. Nothing sent (a bad request, a refused connect, a first send that
    failed) is push.NOT_WRITTEN; every byte sent is push.WRITTEN; some but not all is
    push.UNCERTAIN."""
    try:
        path = canonical_socket(path)
    except ValueError:
        return push.NOT_WRITTEN
    if type(creation) is not int or creation <= 0 or not _request_ok(pid, packet, timeout):
        return push.NOT_WRITTEN
    deadline = time.monotonic() + timeout
    try:
        sock = unixsock.connect(path, pid, creation, timeout)
    except (OSError, ProcError):
        return push.NOT_WRITTEN             # connect sends nothing; it closed its socket
    try:
        sent = _send_all(sock, packet, deadline)
    finally:
        try:
            sock.close()
        except OSError:
            pass
    if sent == len(packet):
        return push.WRITTEN
    return push.NOT_WRITTEN if sent == 0 else push.UNCERTAIN


def _send_all(sock, packet, deadline):
    """The count of packet's bytes sock.send took before the deadline or the first failure: a
    send that fails takes none, so the count is exact."""
    view, sent = memoryview(packet), 0
    try:
        while sent < len(packet):
            left = deadline - time.monotonic()
            if left <= 0:
                break
            sock.settimeout(left)
            count = sock.send(view[sent:])
            if not count:
                break
            sent += count
    except OSError:                         # socket.timeout included
        pass
    finally:
        view.release()
    return sent


def _pump(process, data, size, timeout, threads):
    """Feed the helper its request and read its one result line, within timeout; the state."""
    completed = queue.Queue(maxsize=2)
    threads += [threading.Thread(target=_write_input, args=(process.stdin, data, completed),
                                 name='hub-push-input', daemon=True),
                threading.Thread(target=_read_result, args=(process.stdout, completed),
                                 name='hub-push-result', daemon=True)]
    for t in threads:
        t.start()
    deadline, results = time.monotonic() + timeout, {}
    while len(results) < 2:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return push.UNCERTAIN
        try:
            kind, got = completed.get(timeout=remaining)
        except queue.Empty:
            return push.UNCERTAIN
        if kind == 'fault' or kind in results:
            return push.UNCERTAIN
        results[kind] = got
    try:
        process.wait(timeout=max(.001, deadline - time.monotonic()))
    except subprocess.TimeoutExpired:
        return push.UNCERTAIN
    said = _decode(results['read'])
    if process.returncode or said is None:
        return push.UNCERTAIN
    if said == {'state': 'not-written'}:
        return push.NOT_WRITTEN
    if (set(said) == {'state', 'bytes_written'} and said['state'] == 'written'
            and type(said['bytes_written']) is int and said['bytes_written'] == size):
        return push.WRITTEN
    return push.UNCERTAIN


class HubPush(object):
    """Delivery of hub push notifications to Claude Code sessions, for kitlib/hubsite/push.py."""

    def __init__(self, settings, ctime=None, writer=None):
        from kitlib.proc import process
        self.settings = settings
        self._ctime = ctime or process.proc_ctime
        self._write = writer or write_once

    def __repr__(self):
        return '<HubPush>'

    def _sessions(self):
        try:
            data = state.read_sessions(self.settings)
        except LaunchError:
            return {}
        return data if isinstance(data, dict) else {}

    def _name(self, recipient):
        """The session name a recipient key stands for: its sessions.json name, else itself."""
        entry = self._sessions().get(recipient)
        if isinstance(entry, dict) and isinstance(entry.get('name'), str) and entry['name']:
            return entry['name']
        return recipient

    def _coordinators_value(self, key):
        try:
            data = state.read_coordinators(self.settings)
        except LaunchError:
            return None
        value = (data or {}).get(key) if isinstance(data, dict) else None
        if not isinstance(value, str) or not value or value.startswith('<'):
            return None                     # unset, or the example file's placeholder
        return value

    def _pm_session_id(self):
        return self._coordinators_value('pm_session_id')

    def _pm_names(self):
        """coordinators.json's pm_name (pm-relaunch's), then PM_NAMES: who counts as the PM sender.
        resolve's fallback uses PM_NAMES only, so a stale pm_name is never a delivery target."""
        name = self._coordinators_value('pm_name')
        return ((name,) if name else ()) + PM_NAMES

    def is_pm(self, recipient):
        return recipient == push.PM_KEY or self._name(recipient) in self._pm_names()

    def _live(self):
        return [(d, r) for d, r in pipes.records(pipes.registry_dirs(self.settings))
                if _usable(r) and pipes.is_live(r, self._ctime)]

    def _pick(self, hits, name):
        """One of several live records: pipes.json's when its pid and creation time match one,
        else the newest creation time, ambiguous."""
        if len(hits) == 1:
            return Target(hits[0][0], hits[0][1], name, False)
        hint = pipes.read(self.settings).get(name)
        if isinstance(hint, dict):
            for d, r in hits:
                if r.get('pid') == hint.get('pid') and pipes.proc_start(r) == hint.get('proc_start'):
                    return Target(d, r, name, False)
        d, r = max(hits, key=lambda h: pipes.live_ctime(h[1], self._ctime) or -1)
        return Target(d, r, name, True)

    def resolve(self, recipient):
        live = self._live()
        if recipient == push.PM_KEY:
            sid = self._pm_session_id()
            hits = [h for h in live if sid is not None and h[1].get('sessionId') == sid]
            if hits:
                return self._pick(hits, hits[0][1].get('name') or PM_NAMES[0])
            for name in PM_NAMES:
                hits = [h for h in live if h[1].get('name') == name]
                if hits:
                    return self._pick(hits, name)
            return None
        name = self._name(recipient)
        hits = [h for h in live if h[1].get('name') == name]
        if hits:
            return self._pick(hits, name)
        # sessions.json's name can trail the live session (a successor it never recorded): a live
        # session of the entry's own stem, <stem>-a<N>[-r<k>], is the one; never another stem's
        entry = self._sessions().get(recipient)
        stem = entry.get('stem') if isinstance(entry, dict) else None
        if not isinstance(stem, str) or not stem:
            return None
        hits = [h for h in live if lookup.of_stem(h[1].get('name'), stem)]
        if not hits:
            return None
        picked = self._pick(hits, name)
        return picked._replace(name=picked.record.get('name') or name)

    def deliver(self, recipient, text):
        target = self.resolve(recipient)
        if target is None:
            return push.Sent(push.NO_SESSION, None if recipient == push.PM_KEY else self._name(recipient),
                             None, False)
        rec = target.record
        session = rec.get('sessionId') if isinstance(rec.get('sessionId'), str) else None
        creation = pipes.live_ctime(rec, self._ctime)
        if creation is None:                # it ended since resolve
            return push.Sent(push.NOT_WRITTEN, target.name, session, target.ambiguous)
        try:
            packet = build_packet(read_token(target.folder, rec), source_pipe(), text)
        except ValueError:
            return push.Sent(push.NOT_WRITTEN, target.name, session, target.ambiguous)
        try:
            said = self._write(rec['messagingSocketPath'], rec['pid'], creation, packet)
        finally:
            packet = None                   # the token goes no further
        return push.Sent(said, target.name, session, target.ambiguous)
