"""kitlib.hubsite.apifindings: the api's finding routes ("Findings" in the api module docstring):
the maintenance lint's findings, the owner's marks on them, and the cutting of absolute paths
from what a remote caller sees. kitlib.hubsite.api re-exports every name here."""
import re

from kitlib import hubvalidate

from .apibase import _fields, _invalid, _one_of, _pin, _put_pinned, _q, _text, _version, _via, _when
from .store import HubError

# ---- the maintenance lint's findings and the owner's marks on them ("Findings")
_FINGERPRINT = re.compile(r'[0-9a-f]{16}\Z')
FINDING_COPY = ('repo', 'check', 'target', 'text')   # what a mark keeps of its finding
FINDING_PATHS = ('target', 'fix')   # a finding's fields that can name a path on this machine
# an absolute Windows path: a drive's (a drive letter, a colon, then \ or /) or a share's,
# \\host\ or //host/ (a host is needed: a lone doubled backslash is no path, and a URL's :// is
# no share). Quoted, it runs to its quote; bare, on past each space whose next word holds a
# separator and starts no new path, absolute or ./ ../ relative (<drive>\home\A B\x is one
# path), so no folder after a space shows
_PATH_HEAD = r'(?:[A-Za-z]:[\\/]|(?<![:\\/])[\\/]{2,}[^\s\\/"\'`<>|]+[\\/]+)'
_PATH_WORD = r'[^\s"\'`<>|]*'
_ABS_PATH = re.compile(r'"%s[^"]*"' % _PATH_HEAD
                       + r'|(?<![A-Za-z0-9])%s%s' % (_PATH_HEAD, _PATH_WORD)
                       + r'(?:[ \t]+(?![A-Za-z]:[\\/]|[\\/]{2}|\.{1,2}[\\/])[^\s"\'`<>|\\/]*[\\/]%s)*'
                       % _PATH_WORD)


def _unpathed(text):
    """text with each absolute path cut to `.../<its last part>`."""
    def cut(m):
        parts = [p for p in re.split(r'[\\/]+', m.group(0).strip('"')) if p]
        return '.../' + parts[-1] if len(parts) > 1 else '...'
    return _ABS_PATH.sub(cut, text)


def _seen(f, req):
    """Finding f as this caller sees it: whole for the local owner (Request.full), else with no
    absolute path in FINDING_PATHS."""
    if req.full:
        return f
    return dict(f, **{k: _unpathed(f[k]) for k in FINDING_PATHS if isinstance(f.get(k), str)})


def _fp_field(b, name):
    v = b.get(name)
    if not isinstance(v, str) or not _FINGERPRINT.match(v):
        raise _invalid('%s is a finding\'s fingerprint, 16 hex digits (GET findings gives them)'
                       % name, name)
    return v


def _finding(api, fp, field):
    """The finding the lint shows with fingerprint fp, marked or not; not_found on `field`."""
    for f in api.shown('findings'):
        if isinstance(f, dict) and f.get('fingerprint') == fp:
            return f
    raise HubError('not_found', 'the maintenance lint shows no finding %s' % fp, field)


def findings_list(api, req):
    repo = _q(req, 'repo').lower()
    marked = api.store.docs('finding_marks')
    return {'items': [_seen(f, req) for f in api.shown('findings')
                      if isinstance(f, dict) and f.get('fingerprint') not in marked
                      and (not repo or str(f.get('repo') or '').lower() == repo)],
            'runs': [r for r in _lint_runs(api) if not repo or r['repo'].lower() == repo]}


LEDGER_COUNTS = ('stale_cites', 'conflict_pairs', 'overrides')   # kitlib/maintlint/ledger.py


def _utc(text):
    """An ISO time (a trailing Z is UTC) as an aware datetime, or None when it does not parse."""
    if not isinstance(text, str):
        return None
    return _when(text[:-1] + '+00:00' if text.endswith('Z') else text, '')


def _lint_runs(api):
    """Each repo's last lint run from the optional lint_runs view, as {repo, started, ledger,
    skipped}: ledger the LEDGER_COUNTS (whole numbers) or None; skipped the skip marker's {reason,
    at} only when its `at` is later than the run's `started` and both parse (a marker left beside
    a newer result is no skip), else None. No view, a view that raises or a row that is not one:
    nothing, never a failed GET findings."""
    try:
        rows = api.view('lint_runs')
    except Exception:               # noqa: BLE001 - context only; the findings still show
        return []
    out = []
    for r in rows if isinstance(rows, list) else ():
        if not isinstance(r, dict) or not isinstance(r.get('repo'), str):
            continue
        led = r.get('ledger')
        led = ({k: led[k] for k in LEDGER_COUNTS if type(led.get(k)) is int}
               if isinstance(led, dict) else None)
        skip, skipped = r.get('skip'), None
        if isinstance(skip, dict) and isinstance(skip.get('reason'), str):
            at, started = _utc(skip.get('at')), _utc(r.get('started'))
            if at is not None and started is not None and at > started:
                skipped = {'reason': skip['reason'], 'at': skip['at']}
        out.append({'repo': r['repo'], 'started': r['started'] if isinstance(r.get('started'), str)
                    else None, 'ledger': led, 'skipped': skipped})
    return out


def _unresolved_marks(api, marks):
    """The finding marks (id -> doc) still to show: one whose repo has a lint run with a `started`
    time that no longer lists its fingerprint is resolved and left out (hidden, never deleted: a
    finding that comes back with the same fingerprint is marked again). A mark with no repo, a
    repo with no run or only a skip marker, or no findings view keeps its mark."""
    try:
        listed = {f.get('fingerprint') for f in api.shown('findings') if isinstance(f, dict)}
    except Exception:               # noqa: BLE001 - no findings read: hide nothing
        return marks
    ran = {r['repo'].lower() for r in _lint_runs(api) if r['started'] is not None}
    return {i: m for i, m in marks.items()
            if not (isinstance(m, dict) and isinstance(m.get('repo'), str)
                    and m['repo'].lower() in ran and i not in listed)}


def finding_marks_list(api, req):
    shown = _unresolved_marks(api, {m['id']: m for m in api.items('finding_marks', req)})
    return {'items': [_seen(m, req) for m in shown.values()]}


def finding_marks_set(api, req):
    b = _fields(req, ('fingerprint', 'status', 'note', 'if_version'),
                required=('fingerprint', 'status'))
    fp = _fp_field(b, 'fingerprint')
    status = _one_of(b, 'status', hubvalidate.STATUSES['finding_marks'])
    note = _text(b, 'note')
    pin = _pin(b)
    f = _finding(api, fp, 'fingerprint')
    fields = {k: f[k] for k in FINDING_COPY if isinstance(f.get(k), str)}
    fields.update(status=status, marked_at=api.clock(), via=_via(req))
    if note is not None:
        fields['note'] = note
    return _put_pinned(api, 'finding_marks', fp, fields, pin)


def finding_marks_delete(api, req):
    b = _fields(req, ('if_version',))
    _pin(b)
    doc_id = req.params['id']
    d = api.doc('finding_marks', doc_id, b)
    r = api.store.delete('finding_marks', doc_id, if_version=_version(d))
    return {'ok': True, 'id': doc_id, 'deleted': True, 'revision': r.revision}


def _act_note(api, b, req):
    """An `act` note's fields prefilled from its finding as the caller sees it (_seen): repo,
    refs (the fingerprint and the finding's target first, each once; a target's absolute path is
    never a ref) and a text naming the check, target, text and fix."""
    fp = b.get('finding')
    if fp is None:
        raise _invalid('an act note names the finding it acts on (finding)', 'finding')
    f = _seen(_finding(api, _fp_field(b, 'finding'), 'finding'), req)
    target = f.get('target')
    refs = []
    for r in [fp, _unpathed(target) if isinstance(target, str) else None] + list(
            b.get('refs') or ()):
        if isinstance(r, str) and r and r not in refs:
            refs.append(r)
    b['refs'] = refs
    if isinstance(f.get('repo'), str):
        b.setdefault('repo', f['repo'])
    if not (b.get('text') or '').strip():
        b['text'] = 'Act on maintenance finding %s %s: %s (fix: %s)' % tuple(
            f.get(k) if isinstance(f.get(k), str) else '-' for k in ('check', 'target', 'text',
                                                                      'fix'))
