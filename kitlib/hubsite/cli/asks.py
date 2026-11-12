"""kitlib.hubsite.cli.asks: the verbs of tools/hub.py over the owner's questions, to-dos,
stories, notes to the PM, inbox and hand-offs, and pending and wait.

  hub.py questions [--status S] [--repo R]          `Q-1 v2 open [repo] title` for each; a
                                                     done one ends ` (done by the hub: WHY)`
                                                     or ` (done: WHY)`
  hub.py questions show Q-1                         one question: title, status, version, options
                                                     (the recommended marked), context, answer
                                                     and note; any status
  hub.py questions add --title T [--repo R] [--context-file F] [--option "label::description"
        ...] [--recommended N] [--refs ID ...] [--urgent] [--story S-n]
        (--record REPO:ID | --class rule|machine|cross-repo|go) [--from KEY]
                                                     `ASKED Q-2`, then a line for each warning;
                                                     --from: the asker's launcher session key
                                                     (default pm), refused when the hub does
                                                     not know it; --record: the open backlog
                                                     record it is about, refused unless the
                                                     hub reads it now in that configured repo;
                                                     else --class: what kind of question it is
  hub.py questions log Q-1 [--unfiled WHY] --if-version V
                                                     `LOGGED Q-1`: an answered question the
                                                     PM logged in the ledger (no id is given)
  hub.py questions done Q-1 --why TEXT [--unfiled WHY] --if-version V
                                                     `DONE Q-1`: an answered question that
                                                     needs no ruling. A bound answer not filed
                                                     is refused until it files; --unfiled says
                                                     why one that never can is handled anyway
  hub.py questions file [Q-1]                       `FILED Q-1`, `NOT FILED Q-1: <why> (<code>)`
                                                     or `nothing to file`: each answer to a
                                                     question bound to a record not filed yet,
                                                     filed into its record again; a running hub
                                                     queues it for its refile job and says
                                                     `QUEUED Q-1` (`QUEUED all` with no id)
  hub.py questions bind --dry-run | --apply          (PM) `BIND Q-1 repo:ID` per open question a
                                                     parked record's q or an owner-blocked
                                                     record cites, then `MISS`, `MULTI`, `SKIP`
                                                     lines; --apply binds them (open or
                                                     answered questions), `BOUND` / `NOT BOUND`
                                                     for each, and files an answered one at
                                                     once: `FILED` / `NOT FILED Q-1: <why>`
  hub.py questions edit Q-1 --from KEY --if-version V [--title T] [--context-file F]
        [--option "label::description" ...] [--recommended N] [--refs ID ...]
        [--urgent | --no-urgent] [--record REPO:ID | --no-record] [--class C]
                                                     `EDITED Q-1`; an --option list replaces
                                                     the old one; --no-record unbinds it (with
                                                     --class unless it has one)
  hub.py questions withdraw Q-1 --from KEY --if-version V    `WITHDRAWN Q-1`
                                                     edit and withdraw: an open question whose
                                                     from is KEY (pm when it has none)
  hub.py answer Q-1 --choice N|other [--note TEXT] [--if-version V]     (owner)
                                                     `ANSWERED Q-1 <label>`
  hub.py todos [--status S] [--repo R]              `T-1 open [repo] title` for each
  hub.py todos add --title T [--detail D] [--command C ...] [--steps F] [--repo R]
        [--refs ID ...] [--urgent] [--from NAME [--record REPO:ID] | --owner]
                                                     `ADDED T-2`; --owner: the owner's own to-do;
                                                     --steps F: a UTF-8 JSON file holding the
                                                     list of steps the hub makes into commands
                                                     (docs/hub-mcp.md "To-do steps"); --record:
                                                     the record waits on the owner until the
                                                     to-do is done (the hub parks it)
  hub.py todos done T-1 [--undo] [--note N] [--if-version V]   (owner) `DONE T-1`, `REOPENED T-1`
  hub.py todos show T-1                             the to-do, its commands and the owner's note
  hub.py todos ack T-1                              `ACKED T-1`
  hub.py todos edit T-1 --from NAME --if-version V [--title T] [--detail D] [--command C ...]
        [--repo R] [--refs ID ...] [--urgent | --no-urgent]     `EDITED T-1`
  hub.py todos withdraw T-1 --from NAME --if-version V      `WITHDRAWN T-1`
                                                     edit and withdraw: an open to-do whose
                                                     from is NAME (pm when it has none)
  hub.py stories [--status S] [--repo R]            `S-1 v2 submitted [repo] title` for each
  hub.py stories add --repo R --title T --story-file F [--urgent]       (owner) `SUBMITTED S-2`
  hub.py stories set S-1 --status review|questions|added|declined [--reviewer NAME]
        [--review-note TEXT] [--backlog-id ID] [--closed-note TEXT] --if-version V
                                                     `SET S-1 <status>`
  hub.py stories withdraw S-1                       (owner) `WITHDRAWN S-1`
  hub.py to-pm [--status S] [--repo R]              `P-1 queued [repo] text` for each
  hub.py to-pm add --text T [--kind K] [--repo R] [--urgent]            (owner) `QUEUED P-2`
  hub.py to-pm send                                 (owner) `SENT P-1 P-2`, or `SENT nothing`
  hub.py to-pm reply P-1 TEXT [--if-version V]                          `REPLIED P-1`
  hub.py inbox                                      `M-1 unread [repo] title` for each
  hub.py inbox add --text T [--title T] [--repo R] [--ref ID] [--asked TEXT] [--refs ID ...]
        [--from NAME]                                `POSTED M-2`

  hub.py handoffs [--from KEY]                      `C-1 restart from=KEY at=TIME PATH`: each
                                                     session's newest hand-off record, or KEY's
                                                     (docs/hub-approvals.md "handoffs/C-NNN")
  hub.py handoffs add --kind K --state PATH --from KEY       `RECORDED C-1`: beside the ready
                                                     reply (skill session-handoff), K handoff,
                                                     restart or stop, PATH the state note

  hub.py pending [--for KEY]                        `<kind> <id> [repo] to=R from=K title` for
                                                     each item of owner input not yet handled: R
                                                     is the session key to handle it now (pm
                                                     when its creator K cannot be reached);
                                                     --for KEY: only the items for session KEY
                                                     (--for pm: the ones routed to the PM and
                                                     every answered question, the classed
                                                     answers last, after an `AUDIT` line);
                                                     class=C on a classed question
  hub.py wait [--since REV] [--timeout S] [--pending [--for KEY] [--except ID...]]
      waits until the store changes after revision REV (default: the revision at the call), or,
      with --pending, until an item is pending (one that is pending already ends it at once);
      with --for KEY as well, until an item for session KEY is pending; with --except, items
      with those ids (ones already acted on, still pending until the PM logs them) are left out.
      The PM waits with --for pm. Prints `HUB <rev> <kind> <id> to=R from=K <title>` for each
      pending item (R and K as for pending) and `HUB <rev> <collection>
      <id> <op> <status>` for each change, exit 0. With --timeout S: exit 4 and no line when S
      seconds pass with neither. Without it the wait has no end of its own. A hub that goes
      down after a poll of the wait was answered (port closed, or `REFUSED store`: a restart) is
      asked again each second until S runs out, or 600 s down in a row, then that reason is the
      exit (3 or 1); one down at the first poll ends the wait at once (client.Client.wait).
"""
from kitlib.hubclient import client
from kitlib.hubclient import errors as huberrors
from kitlib.hubsite import api
from kitlib.hubsite.cli import common


def bind_lines(got):
    """The lines `questions bind` prints for the hub's answer."""
    def named(r):
        r = r if isinstance(r, dict) else {}
        return '%s:%s' % (r.get('repo'), r.get('id')) if r.get('id') else str(r.get('repo'))
    lines = []
    for p in got.get('pairs') or []:
        word = 'BOUND' if p.get('bound') else 'BIND'
        lines.append('%s %s %s' % (word, p.get('question'), named(p.get('record'))))
        f = p.get('filing')
        if not isinstance(f, dict):
            continue
        if f.get('filed'):
            lines.append('FILED %s' % p.get('question'))
        elif f.get('moved'):
            lines.append('NOT FILED %s: moved (filed by the answer handler)' % p.get('question'))
        elif f.get('unanswered'):
            lines.append('NOT FILED %s: no longer answered' % p.get('question'))
        else:
            lines.append('NOT FILED %s: %s (%s)' % (p.get('question'), f.get('message'),
                                                    f.get('code')))
    lines += ['NOT BOUND %s: %s (%s)' % (f.get('question'), f.get('message'), f.get('code'))
              for f in got.get('failed') or []]
    lines += ['MISS %s: %s' % (named(m.get('record')), m.get('why'))
              for m in got.get('misses') or []]
    lines += ['MULTI %s cites %s' % (named(m.get('record')), ' '.join(m.get('questions') or []))
              for m in got.get('multi') or []]
    lines += ['SKIP %s: %s' % (s.get('question'), s.get('why')) for s in got.get('skipped') or []]
    return lines or ['nothing to bind']


def questions(a, out, stop=None):
    hub = common.hub(a)
    if a.sub == 'add':
        context = common.text_of(a.context_file, 'context') if a.context_file else None
        got = hub.post('/questions', client.AGENT, common.body(
            title=a.title, repo=getattr(a, 'repo', None), context=context, options=a.options,
            recommended=a.recommended, refs=a.refs, urgent=a.urgent, story=a.story,
            record=a.record, **{'from': a.who, 'class': a.question_class}))
        return common.wrote(a, out, got, 'ASKED %s' % got.get('id'))
    if a.sub == 'log':
        body = {'if_version': a.if_version}
        if a.unfiled is not None:
            body['unfiled'] = a.unfiled
        got = hub.post(common.one('questions', a) + '/log', client.AGENT, body)
        return common.wrote(a, out, got, 'LOGGED %s' % got.get('id'))
    if a.sub == 'done':
        body = {'why': a.why, 'if_version': a.if_version}
        if a.unfiled is not None:
            body['unfiled'] = a.unfiled     # sent only when asked: an older hub refuses unknown fields
        got = hub.post(common.one('questions', a) + '/done', client.AGENT, body)
        return common.wrote(a, out, got, 'DONE %s' % got.get('id'))
    if a.sub == 'file':
        got = hub.post('/questions/file', client.AGENT, common.body(id=a.id))
        queued = got.get('queued')
        if queued is not None:
            # the hub's refile job does it now: the result is on the question and its push
            # (docs/hub-questions.md "Filing the answer")
            return common.show(a, out, got, ['QUEUED all'] if queued == 'all' else
                               ['QUEUED %s' % i for i in queued])
        lines = ['FILED %s' % i for i in got.get('filed') or []]
        lines += ['NOT FILED %s: %s (%s)' % (f.get('id'), f.get('message'), f.get('code'))
                  for f in got.get('failed') or [] if isinstance(f, dict)]
        return common.show(a, out, got, lines or ['nothing to file'])
    if a.sub == 'bind':
        got = hub.post('/questions/bind', client.AGENT, {'from': 'pm', 'apply': bool(a.apply)})
        return common.show(a, out, got, bind_lines(got))
    if a.sub == 'edit':
        context = common.text_of(a.context_file, 'context') if a.context_file else None
        body = common.body(title=a.title, context=context, options=a.options,
                     recommended=a.recommended, refs=a.refs, record=a.record,
                     **{'class': a.question_class})
        if a.no_record:
            body['record'] = None
        got = hub.post(common.one('questions', a) + '/edit', client.AGENT, common.edits(a, body))
        return common.wrote(a, out, got, 'EDITED %s' % got.get('id'))
    if a.sub == 'withdraw':
        got = hub.post(common.one('questions', a) + '/delete', client.AGENT, common.edits(a, {}))
        return common.wrote(a, out, got, 'WITHDRAWN %s' % got.get('id'))
    if a.sub == 'show':
        return question(a, out)
    return common.listed(a, out, 'questions', version=True)


def question(a, out):
    """questions show: the one document of `a.id` out of the list of every status."""
    got = common.hub(a).get('/questions', query={'status': 'all'})
    for d in got.get('items') or []:
        if d.get('id') == a.id:
            break
    else:
        raise huberrors.ServeError('%s is not a question the hub holds' % a.id)
    if getattr(a, 'json', False):
        return common.show(a, out, d, [])
    lines = [common.item(d, version=True)]
    for n, o in enumerate(d.get('options') or []):
        if isinstance(o, dict):
            mark = ' (recommended)' if d.get('recommended') == n else ''
            lines.append('option %d: %s%s - %s' % (n, o.get('label'), mark, o.get('description') or ''))
    if d.get('context'):
        lines.append('context: %s' % d['context'].strip())
    answer = d.get('answer') if isinstance(d.get('answer'), dict) else None
    if answer:
        lines.append('answer: %s' % answer.get('label'))
        if answer.get('note'):
            lines.append('note: %s' % answer['note'])
    return common.show(a, out, d, lines)


def todo(a, out):
    """todos show: the one to-do of `a.id` out of the list of every status, with its detail,
    commands and, once done, the owner's note."""
    got = common.hub(a).get('/todos', query={'status': 'all'})
    for d in got.get('items') or []:
        if d.get('id') == a.id:
            break
    else:
        raise huberrors.ServeError('%s is not a to-do the hub holds' % a.id)
    if getattr(a, 'json', False):
        return common.show(a, out, d, [])
    lines = [common.item(d, version=True)]
    if d.get('detail'):
        lines.append('detail: %s' % d['detail'].strip())
    for n, c in enumerate(d.get('commands') or []):
        lines.append('command %d: %s' % (n + 1, c))
    if d.get('done_note'):
        lines.append('note: %s' % d['done_note'])
    return common.show(a, out, d, lines)


def answer(a, out, stop=None):
    got = common.hub(a).post(common.one('questions', a) + '/answer', client.OWNER, common.body(
        choice=a.choice, note=a.note, if_version=a.if_version))
    answer = got.get('answer') if isinstance(got.get('answer'), dict) else {}
    return common.wrote(a, out, got, 'ANSWERED %s %s' % (got.get('id'),
                                                         answer.get('label') or a.choice))


def todos(a, out, stop=None):
    hub = common.hub(a)
    if a.sub == 'add':
        body = common.body(title=a.title, detail=a.detail, commands=a.commands,
                     repo=getattr(a, 'repo', None), refs=a.refs, urgent=a.urgent,
                     record=a.record)
        if a.steps_file:
            body['steps'] = common.json_of(a.steps_file, 'steps')
        if a.who:
            body['from'] = a.who
        got = hub.post('/todos', client.OWNER if a.owner else client.AGENT, body)
        return common.wrote(a, out, got, 'ADDED %s' % got.get('id'))
    if a.sub == 'show':
        return todo(a, out)
    if a.sub == 'done':
        body = common.body(if_version=a.if_version, note=a.note)
        body['done'] = not a.undo
        got = hub.post(common.one('todos', a) + '/done', client.OWNER, body)
        return common.wrote(a, out, got, '%s %s' % ('REOPENED' if a.undo else 'DONE',
                                                    got.get('id')))
    if a.sub == 'ack':
        got = hub.post(common.one('todos', a) + '/ack', client.AGENT)
        return common.wrote(a, out, got, 'ACKED %s' % got.get('id'))
    if a.sub == 'edit':
        got = hub.post(common.one('todos', a) + '/edit', client.AGENT, common.edits(a, common.body(
            title=a.title, detail=a.detail, commands=a.commands, repo=a.edit_repo, refs=a.refs)))
        return common.wrote(a, out, got, 'EDITED %s' % got.get('id'))
    if a.sub == 'withdraw':
        got = hub.post(common.one('todos', a) + '/delete', client.AGENT, common.edits(a, {}))
        return common.wrote(a, out, got, 'WITHDRAWN %s' % got.get('id'))
    return common.listed(a, out, 'todos')


def stories(a, out, stop=None):
    hub = common.hub(a)
    if a.sub == 'add':
        got = hub.post('/stories', client.OWNER, common.body(
            repo=a.repo, title=a.title, story=common.text_of(a.story_file, 'story'),
            urgent=a.urgent))
        return common.wrote(a, out, got, 'SUBMITTED %s' % got.get('id'))
    if a.sub == 'set':
        got = hub.post(common.one('stories', a), client.AGENT, common.body(
            status=a.status, reviewer=a.reviewer, review_note=a.review_note,
            backlog_id=a.backlog_id, closed_note=a.closed_note,
            if_version=a.if_version))
        return common.wrote(a, out, got, 'SET %s %s' % (got.get('id'), a.status))
    if a.sub == 'withdraw':
        got = hub.post(common.one('stories', a) + '/withdraw', client.OWNER)
        return common.wrote(a, out, got, 'WITHDRAWN %s' % got.get('id'))
    return common.listed(a, out, 'stories', version=True)


def to_pm(a, out, stop=None):
    hub = common.hub(a)
    if a.sub == 'add':
        got = hub.post('/to_pm', client.OWNER, common.body(
            text=a.text, kind=a.kind, repo=getattr(a, 'repo', None), urgent=a.urgent))
        return common.wrote(a, out, got, 'QUEUED %s' % got.get('id'))
    if a.sub == 'send':
        got = hub.post('/to_pm/send', client.OWNER)
        sent = [str(x) for x in got.get('sent') or []]
        return common.show(a, out, got, ['SENT %s' % (' '.join(sent) or 'nothing')])
    if a.sub == 'reply':
        got = hub.post(common.one('to_pm', a) + '/reply', client.AGENT, common.body(
            reply=a.reply, if_version=a.if_version))
        return common.wrote(a, out, got, 'REPLIED %s' % got.get('id'))
    return common.listed(a, out, 'to_pm')


def inbox(a, out, stop=None):
    hub = common.hub(a)
    if a.sub == 'add':
        got = hub.post('/inbox', client.AGENT, common.body(
            text=a.text, title=a.title, repo=a.repo, ref=a.ref, asked=a.asked, refs=a.refs,
            **{'from': a.who}))
        return common.wrote(a, out, got, 'POSTED %s' % got.get('id'))
    if a.sub == 'read':
        body = common.body(if_version=a.if_version)
        body['read'] = not a.undo
        got = hub.post(common.one('inbox', a) + '/read', client.OWNER, body)
        return common.wrote(a, out, got, '%s %s' % ('UNREAD' if a.undo else 'READ', got.get('id')))
    items = hub.inbox()
    return common.show(a, out, {'items': items},
                       [common.item(d, 'read' if d.get('read') else 'unread') for d in items])


def handoffs(a, out, stop=None):
    hub = common.hub(a)
    if a.sub == 'add':
        got = hub.post('/handoffs', client.AGENT, common.body(
            kind=a.kind, state_path=a.state, **{'from': a.who}))
        return common.wrote(a, out, got, 'RECORDED %s' % got.get('id'))
    who = getattr(a, 'who', None)
    got = hub.get('/handoffs', query={'from': who} if who else None)
    items = got.get('items') or []
    return common.show(a, out, {'items': items},
                       ['%s %s from=%s at=%s %s' % (d.get('id'), d.get('kind'), d.get('from'),
                                                    d.get('at'), d.get('state_path'))
                        for d in items])


def pending(a, out, stop=None):
    got = common.hub(a).get('/pending', client.ANY, {'for': a.for_key})
    items = got.get('items') or []
    lines = [pending_line(p) for p in items]
    classed = [p for p in items if p.get('class')]
    if a.for_key == api.AGENT_FROM and classed:
        # the PM's audit: an answer with a class has no record to be filed into
        lines = ([pending_line(p) for p in items if not p.get('class')]
                 + ['AUDIT classed answers, no record to file into:']
                 + [pending_line(p) for p in classed])
    return common.show(a, out, got, lines)


def pending_line(p):
    """`<kind> <id> [repo] to=R from=K [class=C] title` of pending item p."""
    return '%s %s [%s] %s %s%s' % (p.get('kind'), p.get('id'), p.get('repo') or '-', routed(p),
                                   'class=%s ' % p['class'] if p.get('class') else '', title(p))


def routed(p):
    """`to=<route> from=<from>` of pending item p: the session key that is to handle it now and
    the key it is for (docs/hub-pm.md "Who handles a pending item"); `-` for one not given."""
    return 'to=%s from=%s' % (p.get('route') or '-', p.get('from') or '-')


def title(p):
    """The title of pending item p, marked `(filed)` when its answer is filed into its record."""
    return (p.get('title') or '') + (' (filed)' if p.get('filed') is True else '')


def wait(a, out, stop=None):
    got = common.hub(a).wait(a.since, a.timeout, a.pending, a.for_key, a.except_ids)
    if got.get('timed_out'):
        if getattr(a, 'json', False):
            common.show(a, out, got, [])
        return client.TIMED_OUT
    rev = got.get('revision')
    lines = ['HUB %s %s %s %s %s' % (rev, p.get('kind'), p.get('id'), routed(p),
                                     title(p))
             for p in got.get('pending') or []]
    lines += ['HUB %s %s %s %s %s' % (rev, c.get('collection'), c.get('id'), c.get('op'),
                                      c.get('status') or '-')
              for c in got.get('changes') or []]
    if got.get('reset'):
        lines.append('HUB %s reset: the store is behind --since; read it again' % rev)
    return common.show(a, out, got, lines)
