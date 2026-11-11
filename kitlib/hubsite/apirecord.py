"""kitlib.hubsite.apirecord: the checks a bound ask shares (a to-do, a file approval, a question):
the record it is bound to (_bound) and an agent's claim on an item it added. kitlib.hubsite.api
re-exports every name here."""
from .apibase import AGENT_FROM, OWNER_FROM, _invalid, _reason, _version
from .store import HubError

RECORD_SHAPE = 'record is {"repo": <a configured repository>, "id": <one of its record ids>}'


def _holders(api, repo, rid):
    """The other configured repo names whose backlog has the record `rid` (a kit record is keyed
    on the PM repo's folder name, not the kit repo's own), for the not_found refusal. Best
    effort: a view that is missing or fails names none."""
    try:
        names = [r.get('name') for r in api._view('repos')() if isinstance(r, dict)]
    except Exception:       # noqa: BLE001 - a view is another module's code
        return []
    held = []
    for name in names:
        if not isinstance(name, str) or name.lower() == repo.lower():
            continue
        try:
            got = api._view('record')(name, rid)
        except Exception:   # noqa: BLE001
            continue
        if isinstance(got, dict) and got.get('where') == 'open':
            held.append(name)
    return held


def _bound(api, value, what='question'):
    """The `record` a question (or `what`: an agent's to-do, a file approval) is bound to, {repo,
    id}, after the configured repository's
    backlog was read now through the `record` view (docs/hub-questions.md "questions/Q-NNN"): repo
    is the configured name, whatever its case was as given. Refused, field record: `invalid` for
    another shape, a name that can be no repository or record id, and a completed record;
    `not_found` for a repository that is not configured and a record its backlog does not have;
    `unavailable` when the record cannot be checked (no record view on this server, or a store
    that cannot be read). It fails closed: an answer is filed into this record, so a binding
    nobody checked is never stored. A record whose view says `writable` false (its repository
    names no backlog store the hub may write, kitlib.hubsite.backlog writable) is `invalid` too:
    its answer could never be filed (recordwrite answer, `no_store`), nor its record parked."""
    if not isinstance(value, dict) or sorted(value) != ['id', 'repo'] or not all(
            isinstance(value[k], str) and value[k].strip() for k in ('repo', 'id')):
        raise _invalid(RECORD_SHAPE, 'record')
    repo, rid = value['repo'].strip(), value['id'].strip()
    try:
        got = api._view('record')(repo, rid)
    except HubError as e:
        if e.code == 'invalid':
            raise _invalid('record: %s' % e.message, 'record')
        named = '%s:%s' % (repo, rid)
        if e.code == 'not_found' and e.field == 'repo':
            raise HubError('not_found', 'record names repo %s, which is not a repository the '
                           'hub has configured' % ascii(repo), 'record')
        if e.code == 'not_found':
            held = _holders(api, repo, rid)
            raise HubError('not_found', "record %s: repo %s's backlog has no record %s%s"
                           % (named, repo, rid, '; the repo key that holds it is %s' % ', '.join(held)
                              if held else ''), 'record')
        raise HubError('unavailable', 'record %s could not be checked: %s; %s' % (
            named, e.message, 'ask again, or ask without a record' if what == 'question'
            else 'add it again, or add it without a record'), 'record')
    except Exception as e:          # noqa: BLE001 - a view is another module's code
        raise HubError('unavailable', 'record %s:%s could not be checked: the record view '
                       'failed: %s' % (repo, rid, _reason(e)), 'record')
    got = got if isinstance(got, dict) else {}
    name = got.get('repo') if isinstance(got.get('repo'), str) else repo
    if got.get('where') != 'open':
        raise _invalid('record %s:%s is completed: bind a %s to an open record'
                       % (name, rid, what), 'record')
    if got.get('writable') is False:
        raise _invalid('record %s:%s: repo %s names no backlog store the hub may write (.kit.json '
                       '"backlog".store), so %s: %s without a record'
                       % (name, rid, name, 'its answer could not be filed' if what == 'question'
                          else 'the hub could not park the record',
                          'ask' if what == 'question' else 'add it'), 'record')
    return {'repo': name, 'id': rid}


def _no_record(req, why):
    """Refused `invalid`, field record, when the body gives a `record` this route never takes."""
    if isinstance(req.body, dict) and 'record' in req.body:
        raise _invalid(why, 'record')


def _creator(b, doc_id, d, what):
    """An agent's edit or withdrawal: refused (`forbidden`, field from) unless the `from` the
    agent gives is the item's own (AGENT_FROM when the item has none) and the item is not the
    owner's. All agents share one token, so this is a claim checked against the item: it stops a
    mistake, not an agent that gives another's name."""
    stored = d.get('from') or AGENT_FROM
    claimed = (b.get('from') or '').strip()
    if stored == OWNER_FROM or claimed != stored:
        raise HubError('forbidden', '%s is from %s: an agent can %s only an item it added, '
                       'giving that item\'s from (%s was given)'
                       % (doc_id, stored, what, ascii(claimed)), 'from')


def _still_open(doc_id, d, what):
    """Refused (`conflict`, field status) once the item is no longer open: an answered, logged
    or done item is the owner's record and is not changed under it."""
    if d.get('status') != 'open':
        raise HubError('conflict', '%s is %s: only an open item can be %s'
                       % (doc_id, d.get('status'), what), 'status')


def _withdrawn(api, coll, doc_id, d):
    r = api.store.delete(coll, doc_id, if_version=_version(d))
    return {'ok': True, 'id': doc_id, 'deleted': True, 'revision': r.revision}
