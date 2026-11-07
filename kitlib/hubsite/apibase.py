"""kitlib.hubsite.apibase: what the api's handlers share: the role and status constants, Route and
Request, the error body, the readers of a request's body and query, and ApiConfig (the api module docstring
has the rules; kitlib.hubsite.api re-exports every name here, so a caller keeps importing it
from there)."""
import collections
import dataclasses
import datetime
import math
import re

from . import pagecheck
from .store import HubError

BASE = '/api/v1'
API_VERSION = 'v1'
OWNER, AGENT, ANY = 'owner', 'agent', 'any'
APPROVER = 'approver'               # a route for the strong owner alone ("Roles")
LOCK_WRITE = 'lock-write'           # a lock write: the owner here, the strong owner remotely
ROLES = (OWNER, AGENT, ANY, APPROVER, LOCK_WRITE)   # what a route may ask for
CALLERS = (OWNER, AGENT)            # what a caller may be
# why a route of role APPROVER refused a caller who is not the strong owner
NOT_STRONG = ('%s %s needs the owner logged in through Cloudflare Access: the owner token, a '
              'browser paired from it and an agent are not enough')
# why a route of role LOCK_WRITE refused a caller
NOT_LOCK_WRITER = ('%s %s is the owner\'s; over the tunnel it needs the owner logged in through '
                   'Cloudflare Access')
# why the page may not write the lock (GET lock/view's `writes`)
WRITES_WHY = {
    'agent': 'the machine lock takes this hub for an agent (it was started under an agent '
             'session): restart the hub from your own console or its scheduled task',
    'tunnel': 'over the tunnel a lock change needs the owner logged in through Cloudflare Access',
    'strong': 'reservations and the division need the strong owner login (a browser paired as '
              'the strong owner, or Cloudflare Access)'}
STATUS = {'invalid': 400, 'forbidden': 403, 'not_found': 404, 'conflict': 409, 'store': 503,
          'unavailable': 503, 'internal': 500}
WAIT_MAX_S = 55
FEED_MAX = 200                      # the landing feed events GET snapshot and GET landings give
LIMIT_DIGITS = 9                    # GET landings `limit`: longer is refused, never a huge int()
LOCK_POLL_S = 0.5                   # how often GET lock/wait looks at the lock's revision
LOCK_DAYS_MAX = 366                 # GET lock/stats `days`
TUNNEL_VIA = 'tunnel'               # the tunnel listener's via (kitlib.hubsite.httpd): a remote view
ACK_WINDOW_S = 30 * 60              # a creator's time to handle an item handed to it ("Routes")
REACH_GRACE_S = WAIT_MAX_S + 30     # a key is reachable this long after its last wait ("Routes")
ROUTE_SLACK_S = 0.01                # a PM wait looks again this long after a route's turn is due
VIEWS = ('backlog', 'completed', 'notices', 'repos', 'usage', 'agents', 'skills')
OWNER_FROM = 'you'                  # a to-do's or a story's `from` when the owner wrote it
AGENT_FROM = 'pm'                   # a to-do's `from` when an agent gives none
AGENT_STORY_STATUSES = ('review', 'questions', 'added', 'declined')
DAYS_MAX = 3650
# what a caller who is not the owner is told in place of the store's own message, by code
AGENT_SAYS = {'store': 'the hub store is not available: the owner sees why'}

Route = collections.namedtuple('Route', 'method pattern role handler')
# full: the local owner, told the store's own words (agent_words False) and not through the tunnel
Request = collections.namedtuple('Request', 'role params body query via strong full')

_TRUE = ('1', 'true', 'yes', 'on')
_FALSE = ('', '0', 'false', 'no', 'off')
_ID_SPLIT = re.compile(r'([0-9]+)')
_NAME = re.compile(r'[\x21-\x7e]+(?: [\x21-\x7e]+)*')
_SURROGATE = re.compile('[\ud800-\udfff]')     # a lone one: half a character
# A hub item's id or a ruling's anywhere in a page id ("Pages"): hubvalidate._REF's pattern, plus
# A, E and R and the per-repo ledger id H-<repo>-nnnn, searched, so neither `plan-Q-1` nor `Q-1.v2` holds a ref a reader would follow.
_REF_SHAPED = re.compile(r'(?<![A-Za-z0-9])(?:[QMTPSARE]-\d+|H-[a-z0-9][a-z0-9-]*-\d+)(?![A-Za-z0-9])', re.I)
PAGE_HTML_MAX = pagecheck.MAX_BYTES


def error_body(code, message, field=''):
    return {'ok': False, 'error': {'code': code, 'message': message, 'field': field or ''}}


def _invalid(message, field=''):
    return HubError('invalid', message, field)


def _natural(doc_id):
    # split on a group: text at the even places, ASCII digits at the odd ones
    return [int(p) if i % 2 else p.lower() for i, p in enumerate(_ID_SPLIT.split(doc_id))]


def _version(doc):
    v = doc.get('version', 0)
    return v if isinstance(v, int) and not isinstance(v, bool) else 0


def _fields(req, allowed, required=(), flags=(), blank=()):
    """The body's fields: an object holding only `allowed` names, a null one read as absent
    (refused for a name in `flags`: null is neither true nor false; read as "" for a name in
    `blank`), every `required` one present."""
    body = req.body
    if body is None:
        body = {}
    if not isinstance(body, dict):
        raise _invalid('the request body is a JSON object', 'body')
    for name in sorted(body, key=str):
        if name not in allowed:
            raise _invalid('this route takes no %r field (it takes: %s)'
                           % (name, ', '.join(allowed) or 'none'), str(name))
    for name in flags:
        if name in body and not isinstance(body[name], bool):
            raise _invalid('%s must be true or false, not %s'
                           % (name, 'null' if body[name] is None else type(body[name]).__name__),
                           name)
    out = {k: ('' if v is None else v) for k, v in body.items() if v is not None or k in blank}
    for name in required:
        v = out.get(name)
        if v is None or (isinstance(v, str) and not v.strip()):
            raise _invalid('%s is required' % name, name)
    return out


def _typed(b, name, types, what):
    v = b.get(name)
    if v is None:
        return None
    if (isinstance(v, bool) and bool not in types) or not isinstance(v, types):
        raise _invalid('%s must be %s, not %s' % (name, what, type(v).__name__), name)
    return v


def _text(b, name):
    return _typed(b, name, (str,), 'a string')


def _flag(b, name, default):
    v = _typed(b, name, (bool,), 'true or false')
    return default if v is None else v


def _pin(b, required=False):
    v = _typed(b, 'if_version', (int,), 'a whole number')
    if v is None and required:
        raise _invalid('if_version is required: the version of the document you read',
                       'if_version')
    if v is not None and v < 0:
        raise _invalid('if_version must be a whole number, not %r' % (v,), 'if_version')
    return v


def _q(req, name):
    v = (req.query or {}).get(name)
    if isinstance(v, (list, tuple)):
        v = v[0] if v else None
    if v is None:
        return ''
    if isinstance(v, bool):
        return '1' if v else '0'
    return str(v).strip()


def _q_flag(req, name):
    v = _q(req, name).lower()
    if v in _TRUE:
        return True
    if v in _FALSE:
        return False
    raise _invalid('%s is 1 or 0, not %r' % (name, v), name)


def _q_int(req, name, low, high):
    v = _q(req, name)
    if not v:
        return None
    if not re.match(r'^\d{1,12}$', v) or not low <= int(v) <= high:
        raise _invalid('%s is a whole number from %d to %d, not %r' % (name, low, high, v), name)
    return int(v)


def _when(text, field):
    try:
        t = datetime.datetime.fromisoformat(text)
    except (TypeError, ValueError):
        t = None
    if t is None or t.tzinfo is None:
        if field:
            raise _invalid('%s is an ISO time with its offset, not %r' % (field, text), field)
        return None
    return t


def _via(req):
    return req.via or 'api'


def _reason(e):
    """Why a view gave nothing, short and with no path in it: a HubError's code, else the
    exception's class name."""
    return e.code if isinstance(e, HubError) else type(e).__name__


def _timeout(req):
    """A wait's `timeout` query: seconds, WAIT_MAX_S when not given; not capped here."""
    raw = _q(req, 'timeout')
    try:
        timeout = float(raw) if raw else float(WAIT_MAX_S)
    except ValueError:
        timeout = float('nan')
    if not math.isfinite(timeout) or timeout < 0:
        raise _invalid('timeout is a number of seconds from 0 to %d, not %r' % (WAIT_MAX_S, raw),
                       'timeout')
    return timeout


def _one_of(b, name, allowed):
    v = _text(b, name)
    if v not in allowed:
        raise _invalid('%s is one of %s, not %r' % (name, ', '.join(allowed), v), name)
    return v


def _put_pinned(api, coll, doc_id, fields, pin):
    """Put fields as coll/doc_id, pinned: writing over a document takes its version as `pin`
    (None, or another, is `conflict`), and a pin for a document not there is `conflict` too."""
    have = api.store.get(coll, doc_id)
    if have is not None and pin is None:
        raise HubError('conflict', '%s/%s is at version %d: give it as if_version to set it '
                       'again' % (coll, doc_id, _version(have)), 'if_version')
    if have is not None and pin != _version(have):
        raise HubError('conflict', '%s/%s is at version %d, not %d'
                       % (coll, doc_id, _version(have), pin), 'if_version')
    if have is None and pin:
        raise HubError('conflict', 'there is no %s/%s at version %d: it is not there'
                       % (coll, doc_id, pin), 'if_version')
    # pinned to the version read (0: not there yet), so a second writer with the same pin loses
    r = api.store.put(coll, doc_id, fields, if_version=_version(have) if have is not None else 0)
    return api.written(r)


# A record route's path segments, checked before the view is asked: what a repository name or a
# record id may be (kitlib.hubsite.backlog checks each again, more narrowly). The refusal is a
# fixed phrase: it never repeats the segment.
_SEGMENT = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z')
SEGMENT_SAYS = {'repo': 'not a repository name', 'id': 'not a record id', 'name': 'not a skill name'}


def _record_view(api, name, *args):
    """The record view `name` called with args: its HubError (not_found, invalid, unavailable,
    each with a fixed phrase) is the answer; anything else it raises is `unavailable` with the
    short reason. Unlike `shown`, a not_found stays a 404."""
    fn = api._view(name)
    try:
        return fn(*args)
    except HubError:
        raise
    except Exception as e:      # noqa: BLE001 - a view is another module's code
        raise HubError('unavailable', 'the %s view failed: %s' % (name, _reason(e)), 'views')


@dataclasses.dataclass(frozen=True)
class ApiConfig(object):
    """The hooks an Api reads from this machine, each a function (or None, which keeps the
    Api's own default; the Api class docstring says what each one gives and its default)."""
    session_keys: object = None
    approval_key: object = None
    checkout: object = None
    repo_name: object = None
    schedule_repos: object = None
    tick_seen: object = None
    tick_state: object = None
    kit_key: object = None
    kit_blob: object = None
    kit_text: object = None
    coordinators: object = None
