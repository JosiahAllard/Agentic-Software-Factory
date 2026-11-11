"""Reading a store's records (docs/backlog-store.md): the snapshot of every record at one tip
and its validation, the id checks a write runs, the records of one tree, and the history reads
(a record's removal, the removed records, when each record was last written) every backend
shares. kitlib/backlog/store.py holds the write loop.
"""
import os
import re

from kitlib.git import batch, cmd

from . import record as rmod
from . import removed_cache
from .errors import Failure, Usage

MAX_ID_LEN = 64
WINDOWS_DEVICE_NAMES = frozenset(['CON', 'PRN', 'AUX', 'NUL'] + ['COM%d' % i for i in range(1, 10)]
                                 + ['LPT%d' % i for i in range(1, 10)])


# --- the snapshot ---------------------------------------------------------------------------------

class Snapshot(object):
    """Every record of the store at one tip. `tip` is the commit (None: the store does not exist
    yet, or a main store); `raw` the bytes each record was read from (stem -> bytes)."""

    def __init__(self, records, tip, where, rules, raw=None):
        self.records = sorted(records, key=lambda r: r.stem)
        self.tip = tip
        self.where = where
        self.rules = rules
        self.raw = raw or {}
        self.by_id = {r.stem: r for r in self.records}

    @property
    def readable(self):
        return [r for r in self.records if r.readable]

    @property
    def unreadable(self):
        return [r for r in self.records if not r.readable]

    @property
    def file_count(self):
        return len(self.records)

    def get(self, rid):
        return self.by_id.get(rid)

    def all_findings(self):
        out = []
        for r in self.records:
            out.extend(r.structural)
            out.extend(rmod.validate(r, self.rules))
        return out + validate_store(self.records, self.rules)

    def head_label(self):
        return (self.tip or '')[:10] or 'empty'


def require_readable(snap):
    """Every write verb refuses while any record is unreadable: an unreadable record cannot be
    checked against, so nothing the write decides would be sound."""
    bad = snap.unreadable
    if bad:
        raise Failure('%d record(s) unreadable (%s) - fix them first; `check` names why'
                      % (len(bad), ', '.join(r.stem for r in bad[:5])))


def validate_store(records, rules):
    """Findings about the store as a whole: case-only filename collisions, `item:` cycles, a pin
    held twice."""
    out = []
    folded = {}
    for r in records:
        folded.setdefault(r.stem.casefold(), []).append(r)
    for group in folded.values():
        if len(group) > 1:
            for r in group[1:]:
                out.append(rmod.Finding(rmod.ID_FILENAME, r.path, 1, '`%s` differs from `%s` only '
                                        'in case - one file on Windows' % (r.stem, group[0].stem)))
    live = [r for r in records if r.readable]
    edges = {}
    for r in live:
        edges[r.stem] = [m[len('item:'):] for m in r.all('blocked_on') if m.startswith('item:')]
    cycle = _find_cycle(edges)
    if cycle:
        first = next(r for r in live if r.stem == cycle[0])
        out.append(rmod.Finding(rmod.ITEM_TARGETS, first.path, first.line_of('blocked_on'),
                                'an `item:` cycle: %s - nothing in it can ever be pulled'
                                % ' -> '.join(cycle + [cycle[0]])))
    pins = {}
    for r in live:
        p = r.get('pin')
        if p is not None:
            pins.setdefault(p, []).append(r)
    for p, group in sorted(pins.items()):
        if len(group) > 1:
            for r in group[1:]:
                out.append(rmod.Finding(rmod.PIN_UNIQUENESS, r.path, r.line_of('pin'), '`pin: %s` '
                                        'is also held by %s - a pin is held once' % (p, group[0].stem)))
    return out


def _find_cycle(edges):
    """One `item:` cycle as a list of ids, or None."""
    WHITE, GREY, BLACK = 0, 1, 2
    colour = {k: WHITE for k in edges}
    for start in sorted(edges):
        if colour[start] != WHITE:
            continue
        stack = [(start, iter(edges[start]))]
        path = [start]
        colour[start] = GREY
        while stack:
            node, it = stack[-1]
            nxt = next(it, None)
            if nxt is None:
                colour[node] = BLACK
                stack.pop()
                path.pop()
                continue
            if nxt not in colour:
                continue
            if colour[nxt] == GREY:
                return path[path.index(nxt):]
            if colour[nxt] == WHITE:
                colour[nxt] = GREY
                stack.append((nxt, iter(edges[nxt])))
                path.append(nxt)
    return None


def dangling_items(by_id):
    """`item:X` blockers naming no live record: warnings (X may have closed)."""
    out = []
    for r in by_id.values():
        if not r.readable:
            continue
        for m in r.all('blocked_on'):
            if m.startswith('item:') and m[len('item:'):] not in by_id:
                out.append(rmod.Finding(rmod.ITEM_TARGETS, r.path, r.line_of('blocked_on'),
                                        '`%s` names no live record - closed? `unblock %s --on %s '
                                        '--reason "<why>"`' % (m, r.id, m)))
    return out


def id_refusal(snap, rid):
    """None if `rid` may name a new record, else why not (shape errors first)."""
    err = snap.rules.id_error(rid)
    if err is not None:
        return err
    if len(rid) > MAX_ID_LEN:
        return '`%s` is longer than %d characters' % (rid, MAX_ID_LEN)
    if rid.split('.')[0].upper() in WINDOWS_DEVICE_NAMES:
        return '`%s` is a Windows device name and cannot be a file' % rid
    for stem in snap.by_id:
        if stem.casefold() == rid.casefold():
            return '`%s` already exists%s' % (rid, '' if stem == rid else ' as `%s`' % stem)
    return None


def parse_blob(stem, data, path):
    try:
        text = data.decode('utf-8')
    except UnicodeDecodeError as exc:
        return rmod.unreadable(path, stem, 'not UTF-8: %s' % exc)
    if text.startswith('﻿'):
        text = text[1:]
    return rmod.parse(path, text, stem)


def record_entries(root, rev, rel):
    """`([(stem, path, blob sha)], None)`, or `(None, why)`: every `*.md` directly under `rel` in
    the tree of `rev`, one `git ls-tree` and no blob read (preflight counts the default store
    with it: kitlib/preflight/defaultstore.py)."""
    out, why = cmd.run(['ls-tree', '-z', '--full-tree', rev, '--', rel + '/'], root)
    if out is None:
        return None, '`git ls-tree` failed: %s' % why
    entries = []
    for item in out.split(b'\0'):
        meta, _, path = item.partition(b'\t')
        parts = meta.decode('ascii', 'replace').split()
        path = path.decode('utf-8', 'replace')
        name = path[len(rel) + 1:]
        if len(parts) == 3 and parts[1] == 'blob' and '/' not in name and name.endswith('.md'):
            entries.append((name[:-3], path, parts[2]))
    return entries, None


def records_in_tree(root, rev, rel):
    """`(records, raw, None)`, or `(None, None, why)`: every `*.md` directly under `rel` in the
    tree of `rev` (record_entries), parsed, with `raw` mapping each stem to its bytes."""
    entries, why = record_entries(root, rev, rel)
    if entries is None:
        return None, None, why
    blobs, why = batch.cat_file([sha for _, _, sha in entries], root)
    if blobs is None:
        return None, None, why
    records, raw = [], {}
    for stem, path, sha in entries:
        got = blobs.get(sha)
        if got is None:
            return None, None, 'no blob for %s' % path
        raw[stem] = got[1]
        records.append(parse_blob(stem, got[1], path))
    return records, raw, None


def _last_closing(root, rid, rev):
    pattern = '^(closes|retires|deletes) %s: ' % re.escape(rid)
    out, why = cmd.run_text(['log', '-1', '--format=%h %s', '-E', '--grep', pattern, rev, '--'],
                            root)
    if out is None:
        raise Failure('cannot read the store history: %s' % why)
    return out or None


class Removal(object):
    """The commit that deleted a record's file: short sha, strict ISO commit date, the
    `Session:` line of its message (None when it has none) and its subject."""

    def __init__(self, sha, when, session, subject):
        self.sha = sha
        self.when = when
        self.session = session
        self.subject = subject


def last_removal(root, rev, rel, rid):
    """The newest commit reachable from `rev` that deleted `<rel>/<rid>.md`, or None. Only the id
    is checked (Usage unless PLAIN_ID): the folder is configuration, a backslash in it read as
    `/`. `--full-history`: a record added and deleted on a side branch that was merged leaves the
    merge TREESAME to its first parent, and the default simplification would prune the side
    branch, deleting commit and all. `--no-renames`: a record renamed away counts as deleted."""
    if not PLAIN_ID.match(rid):
        raise Usage('`%s` is not a plain id: as a git pathspec it could match other files' % rid)
    path = '%s/%s.md' % (rel.replace('\\', '/').strip('/'), rid)
    out, why = cmd.run_text(['log', '-1', '--full-history', '--no-renames', '--diff-filter=D',
                             '--format=%h%x1f%cI%x1f%s%x1f%b', rev, '--', path], root)
    if out is None:
        raise Failure('cannot read the store history: %s' % why)
    if not out:
        return None
    sha, when, subject, body = (out.split('\x1f', 3) + ['', '', ''])[:4]
    return Removal(sha, when, session_of(body), subject)


def removed_records(root, rev, rel):
    """`scan_removed`, served from the tip-keyed cache (removed_cache.py)."""
    return removed_cache.cached_removed(root, rev, rel, scan_removed, Removal)


def scan_removed(root, rev, rel):
    """`[(stem, Removal, text)]`, newest removal first, one per record id: every `<rel>/<id>.md` a
    commit reachable from `rev` deleted, with the text it had in that commit's first parent. One
    `git log` names every deleted path and one `cat-file --batch` reads them all, however many
    records there are. A record added back later is listed too; the caller drops the ids the
    store holds. A path git cannot read at the parent is left out."""
    rel = rel.replace('\\', '/').strip('/')
    out, why = cmd.run_text(['-c', 'core.quotePath=false', 'log', '--full-history', '--no-renames',
                             '--diff-filter=D', '--name-only',
                             '--format=%x01%H%x1f%h%x1f%cI%x1f%s%x1f%b%x02', rev, '--', rel + '/'],
                            root, timeout=120, strip=False)
    if out is None:
        raise Failure('cannot read the store history: %s' % why)
    found, seen = [], set()
    for head, names in batch.log_records_names(out):
        full, sha, when, subject, body = (head.split('\x1f', 4) + ['', '', '', ''])[:5]
        for name in names:
            stem, ext = os.path.splitext(name[len(rel) + 1:])
            if not name.startswith(rel + '/') or ext != '.md' or '/' in stem or stem in seen:
                continue
            seen.add(stem)
            found.append((stem, Removal(sha, when, session_of(body), subject),
                          '%s^:%s' % (full, name)))
    blobs, why = batch.cat_file([spec for _s, _r, spec in found], root, timeout=120)
    if blobs is None:
        raise Failure('cannot read the store history: %s' % why)
    done = []
    for stem, removal, spec in found:
        got = blobs.get(spec)
        if got and got[0] == 'blob':
            done.append((stem, removal, got[1].decode('utf-8', 'replace')))
    return done


# An id the history lookup may put in a pathspec: no glob or pathspec-magic character, no path
# separator (`show 'F*'` must not match other records' files).
PLAIN_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]*$')


def session_of(body):
    """The value of the first `Session:` line of a commit body; None when there is none or it is
    empty."""
    for line in body.split('\n'):
        if line.startswith('Session:'):
            return line[len('Session:'):].strip() or None
    return None


def _touch_times(root, rev, rel):
    """`{stem: epoch}` of the newest commit reachable from `rev` that wrote `<rel>/<stem>.md`, from
    one `git log`. A record no commit wrote (uncommitted) is absent."""
    out, why = cmd.run_text(['-c', 'core.quotePath=false', 'log', '--no-renames',
                             '--format=%x01%ct%x02', '--name-only', rev, '--', rel + '/'], root,
                            timeout=120, strip=False)
    if out is None:
        raise Failure('cannot read the store history: %s' % why)
    seen = {}
    for stamp, names in batch.log_records_names(out):
        for name in names:
            stem, ext = os.path.splitext(name[len(rel) + 1:])
            if ext == '.md' and '/' not in stem and stamp.strip().isdigit():
                seen.setdefault(stem, int(stamp))
    return seen
