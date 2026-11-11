"""kitlib.hubsite.cli.common: what the tools/hub.py verbs share. TOOL is the tool's path,
START and OPEN its start and pairing commands; the hub client of a verb (hub), its printed
lines (put, show, listed, wrote) and the bodies it posts.
"""
import json

from kitlib import hubvalidate
from kitlib.hubclient import client, localask, settings
from kitlib.hubclient import errors as huberrors
from kitlib.hubclient import token as hubtoken
from kitlib.hubsite import auth, gitsync, selfrestart
from kitlib.hubsite.gitsync import scrub   # noqa: F401 - the tool scrubs its refusal lines

TOOL = selfrestart.HUB_TOOL
START = 'python "%s" serve' % TOOL
OPEN = 'python "%s" open' % TOOL
# the refusals the tool prints as `REFUSED: <why>`, exit 1
REFUSED_ERRORS = (huberrors.ServeError, auth.TokenFileError)


def refused(say, port, status, obj):
    err = obj.get('error') if isinstance(obj.get('error'), dict) else {}
    say(gitsync.scrub('REFUSED: the server on port %d answered %d %s: %s' % (
        port, status, err.get('code') or '', err.get('message') or 'not the expected answer')),
        err=True)
    return 1


def owner_token():
    folder = hubtoken.folder()
    token = hubtoken.read_token(folder, hubtoken.OWNER)
    if not token:
        raise huberrors.ServeError('no owner token in %s: run hub.py setup' % folder)
    return token


def put(say, text):
    """One line to stdout: `text` on one line, with no user or password of a URL, and with a
    character the console cannot show written as its escape (by `say`)."""
    say(gitsync.scrub(' '.join(str(text).split())))


def show(a, say, obj, lines):
    """The answer `obj` of the server: as one JSON line with --json, else `lines`."""
    if getattr(a, 'json', False):
        say(gitsync.scrub(json.dumps(obj, sort_keys=True)))     # ASCII; a `"` ends a URL's user
    else:
        for line in lines:
            put(say, line)
    return client.OK


def hub(a):
    return client.Client(getattr(a, 'port', None) or settings.load().port, ask=localask.ask)


def item(d, state=None, version=False):
    title = d.get('title') or d.get('text') or ''
    name = '%s v%s' % (d.get('id'), d.get('version')) if version else d.get('id')
    return '%s %s [%s] %s%s' % (name, state or d.get('status') or '-', d.get('repo') or '-', title,
                                done_note(d))


def done_note(d):
    """` (done by the hub: <why>)` after a question the hub marked done once its asker had the
    filed answer (done_by, docs/hub-questions.md "Done by the hub"), ` (done: <why>)` after one its
    asker or the PM marked done; nothing for any other document. The why on one line."""
    by, why = d.get('done_by'), d.get('done_why')
    why = ' '.join(why.split()) if isinstance(why, str) else ''
    if isinstance(by, str) and by.strip():
        who = 'the hub' if by == 'hub' else by.strip()
        return ' (done by %s%s)' % (who, ': ' + why if why else '')
    return ' (done: %s)' % why if why else ''


def listed(a, say, collection, version=False):
    """The lines of a collection. `version`: each line shows its document's version, for the
    collections that have a write which needs it named (--if-version)."""
    got = hub(a).get('/' + collection, query={'status': getattr(a, 'status', None),
                                             'repo': getattr(a, 'repo', None),
                                             'since': getattr(a, 'since', None)})
    return show(a, say, got, [item(d, version=version) for d in got.get('items') or []])


def wrote(a, say, got, line):
    """The answer of a write: its line, then one for each warning the store gave."""
    lines = [line]
    for w in got.get('warnings') or []:
        if isinstance(w, dict):
            lines.append(hubvalidate.format_line(hubvalidate.Finding(
                *[str(w.get(k) or '') for k in hubvalidate.Finding._fields])))
    return show(a, say, got, lines)


def text_of(path, what):
    try:
        with open(path, encoding='utf-8') as f:
            return f.read()
    except (OSError, ValueError) as e:
        raise huberrors.ServeError('cannot read the %s file %s: %s' % (what, path, type(e).__name__))


def json_of(path, what):
    """The JSON value of a UTF-8 file (a BOM is skipped); the server judges its shape."""
    try:
        with open(path, encoding='utf-8-sig') as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        raise huberrors.ServeError('cannot read the %s file %s: %s' % (what, path, type(e).__name__))


def body(**fields):
    """The fields that were given: None, an empty list and a flag that is off are left out."""
    return {k: v for k, v in fields.items()            # 0 is a value: a place, a version
            if not (v is None or v is False or (isinstance(v, list) and not v))}


def one(collection, a):
    return '/%s/%s' % (collection, client.part(a.id))


def edits(a, body):
    """The body of a creator's edit or withdraw: its fields, --from and --if-version, and
    --urgent or --no-urgent when one was given (body leaves a False out)."""
    body.update({'from': a.who, 'if_version': a.if_version})
    if getattr(a, 'urgent', None) is not None:
        body['urgent'] = a.urgent
    return body
