"""A hold's end condition that a program can check: the `until` key (docs/backlog.md "Records",
"block --until").

An `until` line is `<member> <condition>`: the `blocked_on` member it ends and, after one space,
the condition. `block ID --on M --until "<condition>"` writes it; the Action Hub's
unblock-conditions job (kitlib.hubjobs.unblock_conditions) reads it, and when the condition is met
clears that member (plan.plan_condition_met). The conditions it reads:

  todo:T-n done                the hub to-do T-n is done
  question:Q-n answered        the hub question Q-n is answered (filed, logged or done count too)
  approval:A-n decided         the hub approval A-n is decided: approved, denied or consumed
                               (DECIDED); a request that expired undecided is never met
  record:<repo>:<ID> closed    record ID is gone from repo's store: a commit removed it (close,
                               retire or delete)
  path:<path> exists|absent    the file or folder is there, or not; <path> is absolute, or starts
                               with a %VAR% (machine paths stay out of records), and may hold spaces
  plugin:<folder> absent       the plugin export folder <folder> is gone from the export root
  resource:<name> free         the repo's declared resource <name> (`.kit.json` backlog.resources)
                               has a unit free: fewer live pulls use it than its capacity

Any other text is free text: it is kept, and nothing acts on it (it is cleared by hand, with
`unblock --on M`). An `owner` member ends only on an owner event, a to-do, a question or an
approval (OWNER_KINDS): any other condition on `owner` is refused at block time, and the job
skips one.

  split(value)              (member, condition) of an until line; condition '' when it has none
  parse(text)               a Condition, or None when the text is not one of the forms
  kind_of(text)             the form a text names by its prefix (`path:` ...), readable or not
  owner_error(member, text) None, or why `member` may not end on `text`
  resource_error(text, declared)
                            None, or why a `resource:` condition names an undeclared resource
  evaluate(cond, readers)   True (met), False (not met) or None (cannot tell); only True acts
  expand(path, environ)     a path's %VAR%s filled from `environ`; None when one is not set
  cited_ids(text)           the hub ids (Q-n, T-n, A-n) a free text cites, each once
  releasable(rec, status, closed)
                            the blocked_on members of `rec` whose `until` end is already met
                            (`next`'s RELEASABLE lines; docs/backlog.md
                            "next: a short offer's causes")

Nothing here reads a file, a store or the hub: evaluate and releasable ask the readers they are
given.
"""
import collections
import os
import re

KINDS = ('todo', 'question', 'approval', 'record', 'path', 'plugin', 'resource')
# the owner's own acts: the only end of an `owner` hold
OWNER_KINDS = ('todo', 'question', 'approval')
# The one member that holds an `until` line per ask (each owner question bound to the record, say)
# instead of one line; its lines read as AND: the hold ends when the last one is met.
SEVERAL = 'owner'
# The hub question statuses that hold the owner's answer (kitlib.hubvalidate STATUSES; the same
# set as audit_check.ANSWERED).
ANSWERED = ('answered', 'filed', 'logged', 'done')
# The stored hub approval statuses that hold the owner's decision (kitlib.ownerfiles.approvals): a
# request still stored `pending` is not decided, however long ago it expired (the PM's ruling: an
# expired, undecided approval keeps its hold).
DECIDED = ('approved', 'denied', 'consumed')

Condition = collections.namedtuple('Condition', 'kind target state')

_VAR = re.compile(r'%([A-Za-z_][A-Za-z0-9_]*)%')
_CITED = re.compile(r'\b([QTA]-[0-9]+)\b')
_FORMS = {
    'todo': re.compile(r'^todo:(T-[0-9]+) (done)$'),
    'question': re.compile(r'^question:(Q-[0-9]+) (answered)$'),
    'approval': re.compile(r'^approval:(A-[0-9]+) (decided)$'),
    'record': re.compile(r'^record:([A-Za-z0-9][A-Za-z0-9._-]*:[A-Za-z0-9][A-Za-z0-9.-]*) '
                         r'(closed)$'),
    'path': re.compile(r'^path:(\S(?:.*\S)?) (exists|absent)$'),
    'plugin': re.compile(r'^plugin:([A-Za-z0-9][A-Za-z0-9._-]*) (absent)$'),
    'resource': re.compile(r'^resource:([a-z0-9][a-z0-9-]*) (free)$'),
}


def split(value):
    """(member, condition) of an `until` value: the member is its first word."""
    member, _, cond = (value or '').partition(' ')
    return member, cond


def kind_of(text):
    """The form `text` names by its prefix (`todo`, `question`, `approval`, `record`, `path`,
    `plugin`), or None: read before the text is parsed, so a malformed `path:...` is still a path
    condition."""
    prefix, sep, _ = (text or '').partition(':')
    return prefix if sep and prefix in KINDS else None


def parse(text):
    """The Condition `text` states, or None when it is not one of the forms (free text)."""
    kind = kind_of(text)
    if kind is None:
        return None
    m = _FORMS[kind].match(text)
    if m is None:
        return None
    target, state = m.group(1), m.group(2)
    if kind == 'path' and not (os.path.isabs(target) or _VAR.match(target)):
        return None
    return Condition(kind, target, state)


def owner_error(member, text):
    """None, or why the hold `member` may not end on `text`: an `owner` hold ends only on an
    owner event, a to-do, a question or an approval (OWNER_KINDS)."""
    kind = kind_of(text)
    if member != 'owner' or kind is None or kind in OWNER_KINDS:
        return None
    return ('`owner` ends only on an owner event (%s), not on a `%s:` condition: the tool never '
            'clears the owner\'s hold on its own reading' % (
                ', '.join('`%s:`' % k for k in OWNER_KINDS), kind))


def resource_error(text, declared):
    """None, or why `text`, a `resource:<name> free` condition, names a resource `declared`
    ({name: capacity}, `.kit.json` backlog.resources) does not hold. Any other text is None."""
    cond = parse(text)
    if cond is None or cond.kind != 'resource' or cond.target in declared:
        return None
    return ('`resource:%s free`: .kit.json "backlog".resources does not declare `%s` (declared: %s)'
            % (cond.target, cond.target, ', '.join(sorted(declared)) or 'none'))


def expand(path, environ=None):
    """`path` with each %VAR% filled from `environ` (default os.environ), or None when one is not
    set or is empty."""
    env = os.environ if environ is None else environ
    missing = []

    def fill(m):
        v = env.get(m.group(1))
        if not v:
            missing.append(m.group(1))
            return ''
        return v
    out = _VAR.sub(fill, path)
    return None if missing else out


def evaluate(cond, readers):
    """Whether `cond` (a Condition) is met: True, False, or None when it cannot be told (a store,
    a hub document or a folder that cannot be read). `readers` gives

      todo_status(tid)            the to-do's status, or None (no such to-do, unreadable)
      question_status(qid)        the question's status, or None
      approval_status(aid)        the approval's stored status, or None (a readers object
                                  without it cannot tell an approval)
      record_closed(repo, rid)    True (a commit removed it), False (still there), None
      path_exists(path)           True or False for an absolute path, None when unreadable
      plugin_present(folder)      True or False, None when the export root is not known
      resource_free(name)         True (a unit is free), False (every unit is taken), None when
                                  the capacity or the pulls cannot be read (a readers object
                                  without it cannot tell)
    """
    if cond.kind == 'todo':
        status = readers.todo_status(cond.target)
        return None if status is None else status == 'done'
    if cond.kind == 'question':
        status = readers.question_status(cond.target)
        return None if status is None else status in ANSWERED
    if cond.kind == 'approval':
        read = getattr(readers, 'approval_status', None)
        status = read(cond.target) if callable(read) else None
        return None if status is None else status in DECIDED
    if cond.kind == 'record':
        repo, _, rid = cond.target.partition(':')
        return readers.record_closed(repo, rid)
    if cond.kind == 'path':
        path = expand(cond.target)
        if path is None or not os.path.isabs(path):
            return None
        there = readers.path_exists(path)
        return None if there is None else there == (cond.state == 'exists')
    if cond.kind == 'plugin':
        there = readers.plugin_present(cond.target)
        return None if there is None else not there
    if cond.kind == 'resource':
        read = getattr(readers, 'resource_free', None)
        return read(cond.target) if callable(read) else None
    return None


# --- releasable holds (`next`) --------------------------------------------------------------------

def cited_ids(text):
    """The hub ids (`Q-n`, `T-n`, `A-n`) `text` cites, in order, each once."""
    return list(dict.fromkeys(_CITED.findall(text or '')))


def _met(hub_id, status):
    """True when the hub item `hub_id` is answered (a question), done (a to-do) or decided (an
    approval)."""
    got = status(hub_id)
    if got is None:
        return False
    if hub_id.startswith('Q-'):
        return got in ANSWERED
    if hub_id.startswith('A-'):
        return got in DECIDED
    return got == 'done'


def _shown(hub_id, status):
    return '%s %s' % (hub_id, status(hub_id))


def releasable(rec, status, closed):
    """`[(member, why, reason)]` for each `blocked_on` member of `rec` whose cited end is already
    met, in the record's order:

      item:X    X is closed: `closed(X)` gives the short sha of the commit that closed it (None:
                still live, never closed, or cannot tell)
      any other the member's `until` text cites at least one hub id and every one is met: a
                question answered, filed, logged or done (ANSWERED), a to-do done, an approval
                decided (DECIDED; `next` reads no approval, so one is never met there); an `owner`
                hold's several lines (SEVERAL) are read together, so every ask must be met.
                `status(id)` gives the hub status, or None (unknown, or the hub was not read:
                never met). A member with no `until` line, or one that cites no id, is not judged.

    Only `until` states a hold's end. `owner_ask` and a block's reason carry the citation the hold
    rests on (record.block_citation_error), which is often the answered question that set it (a
    "Defer" answer), so an id there is the hold's ground, never its end. A question asked in
    `owner_ask` and still open needs no reading here: its answer, filed on the record, clears the
    `owner` hold itself (plan.plan_answer).

    `why` is what `next` prints, `reason` the `unblock --reason` it suggests."""
    out = []
    untils = [split(v) for v in rec.all('until')]
    for member in rec.all('blocked_on'):
        if member.startswith('item:'):
            target = member[len('item:'):]
            sha = closed(target) if target else None
            if sha:
                out.append((member, '%s closed (%s)' % (member, sha), '%s closed' % target))
            continue
        ids = cited_ids(' '.join(cond for m, cond in untils if m == member))
        if ids and all(_met(i, status) for i in ids):
            shown = ', '.join(_shown(i, status) for i in ids)
            out.append((member, '%s cites %s' % (member, shown), shown))
    return out
