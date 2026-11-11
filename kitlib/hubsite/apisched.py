"""kitlib.hubsite.apisched: the api's schedule routes ("Schedules" below):
the owner's schedules for the kit's automatic tasks, the defaults of Api's schedule_repos,
tick_seen and tick_state hooks, and the two handlers of ROUTES. kitlib.hubsite.api re-exports every
name here.

Schedules. POST schedules (OWNER) sets when one of the kit's automatic tasks runs for one repo:
{task, repo, cadence, at, days?, if_version?} into `schedules/<task>-<repo>`. task is one of
SCHEDULE_TASKS; cadence one of CADENCES (`off` keeps the document and runs nothing); at is HH:MM,
24-hour; days, a list from DAYS, is required for `weekly` and refused for the others, and is
stored once each in DAYS order. repo must be a repository the hub knows (its repos/ documents'
names and aliases, and the store's known_repos), case ignored, and is stored as that repo's name.
A task that runs for one repository (kitlib.autotasks.tick.TASKS per_repo), on any cadence but
`off`, also needs exactly one checkout of it in the machine config's launcher.repos, as the tick
finds it (tick.find_repo over Api's schedule_repos, default launcher_checkouts: tick.launcher_repos);
none, or two, is `invalid`, field repo, and so is a launcher config that cannot be read.
A refusal is `invalid` naming the field, with nothing written. Writing over a schedule takes its
`if_version`; without it, or with another, it is `conflict`, as for a page, and that refusal's
error names the schedule's `id` (so a new one for a task and repo that has one can open it,
the repo matched as above). The answer carries
`first_run`: the first slot strictly after set_at by kitlib.autotasks.due's rule, naive local
time to the minute (None when off). GET schedules (ANY) lists them, with `tick`: {last, running,
every_s}, last when the tick was last seen (Api's tick_seen, default tick_last_seen: the time of
its state file) and running false when that is None or more than TICK_STALE_S before the clock.
Each listed schedule also carries `last_outcome` (the tick's record for it: started, skipped, seen
or failed; None for none), `last_run` (that record's `at` when it is `started`, else None) and
`next_due` (the first slot after the clock by due's rule, naive local time to the minute; None when
off), read from Api's tick_state, default tick_run_state; none is stored.
Nothing here runs a task: a tick outside the hub reads the list.
"""
import datetime
import os
import re

from . import schema
from .apibase import _fields, _invalid, _one_of, _pin, _put_pinned, _text, _typed, _via
from .store import HubError

# ---- the owner's schedules for the kit's automatic tasks ("Schedules")
# The tasks a schedule may name, a fixed table: maint-lint (the maintenance lint, being built),
# worktree-sweep (kitlib.worktree.task) and agent-temp-sweep (kitlib.machinesetup.agenttemp).
# Nothing here runs one.
SCHEDULE_TASKS = ('maint-lint', 'worktree-sweep', 'agent-temp-sweep')
CADENCES = ('off', 'daily', 'weekly')
DAYS = ('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')
_AT = re.compile(r'(?:[01][0-9]|2[0-3]):[0-5][0-9]\Z')


def _schedule_repo(api, name):
    """The name of the repository the hub knows as `name` (case ignored): one the store's
    known_repos gives, a repos/ document's name (its id when it has none), or one of its aliases.
    `invalid`, field repo, for any other."""
    names, aliases = {}, {}
    keys = getattr(api.store.known_repos, 'session_keys', None)     # {repo: [hub keys]}
    keyed = {k.lower(): r for r, ks in (keys() if keys else {}).items() for k in ks}
    for n in api.store.known_repos() or ():
        if isinstance(n, str) and n.strip() and n.strip().lower() not in keyed:
            names.setdefault(n.strip().lower(), n.strip())
    for doc_id, d in sorted(api.store.docs('repos').items()):
        own = d.get('name') if isinstance(d.get('name'), str) and d['name'].strip() else doc_id
        own = names.setdefault(own.strip().lower(), own.strip())
        for a in d.get('aliases') if isinstance(d.get('aliases'), list) else ():
            if isinstance(a, str) and a.strip():
                aliases.setdefault(a.strip().lower(), own)
    for k, r in keyed.items():              # a session key stands for its repository
        if r.lower() in names:
            aliases.setdefault(k, names[r.lower()])
    want = name.strip().lower()
    found = names.get(want) or aliases.get(want)
    if found is None:
        raise _invalid('repo %r is not a repository the hub knows (GET repos lists them)' % name,
                       'repo')
    return found


def launcher_checkouts():
    """Api's default schedule_repos: the machine config's launcher.repos as the tick reads them
    (kitlib.autotasks.tick.launcher_repos)."""
    from kitlib.autotasks import tick
    return tick.launcher_repos()


def tick_last_seen():
    """Api's default tick_seen: when the tick last wrote its state file (it does on every tick
    that reads the hub and takes its lock), an ISO time in UTC, or None when there is none."""
    from kitlib.autotasks import tick
    from kitlib.config import ConfigError
    try:
        mtime = os.path.getmtime(os.path.join(tick.state_dir(), tick.STATE_NAME))
    except (OSError, ConfigError):
        return None
    return datetime.datetime.fromtimestamp(mtime, datetime.timezone.utc).isoformat(
        timespec='seconds')


TICK_STALE_S = 40 * 60          # no tick seen for this long: it is not running


def tick_every_s():
    """The kit-automatic-tasks task's interval in seconds, the one machine setup registers
    (kitlib.autotasks.AUTO_EVERY_MINUTES)."""
    from kitlib import autotasks
    return autotasks.AUTO_EVERY_MINUTES * 60


def _aware(text):
    """The aware datetime an ISO text names (one with no offset is local), None for no time."""
    try:
        at = datetime.datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    return at if at.tzinfo is not None else at.astimezone()


def _tick_status(api):
    last = api.tick_seen()
    seen, now = _aware(last), _aware(api.clock())
    running = (seen is not None and now is not None
               and (now - seen).total_seconds() <= TICK_STALE_S)
    return {'last': last, 'running': running, 'every_s': tick_every_s()}


def tick_run_state():
    """Api's default tick_state: the tick's records by schedule key (kitlib.autotasks.tick.
    read_state), {} when there is none or the file cannot be read."""
    from kitlib.autotasks import tick
    from kitlib.config import ConfigError
    try:
        return tick.read_state(os.path.join(tick.state_dir(), tick.STATE_NAME))[0]
    except (OSError, ConfigError):
        return {}


def _next_due(fields, now):
    """The first slot strictly after `now` (naive local) of a schedule by kitlib.autotasks.due's
    rule, as naive local ISO text to the minute. None when off or not a schedule due can place."""
    from kitlib.autotasks import due
    at = due.parse_at(fields.get('at'))
    if fields.get('cadence') not in (due.DAILY, due.WEEKLY) or at is None or now is None:
        return None
    slot = due.last_slot(fields['cadence'], at, fields.get('days'), now)
    if slot is None:
        return None
    for _ in range(9):
        slot += datetime.timedelta(days=1)
        if slot > now and due.last_slot(fields['cadence'], at, fields.get('days'), slot) == slot:
            return slot.isoformat(timespec='minutes')
    return None


def schedules_list(api, req):
    from kitlib.autotasks import due, tick
    try:
        state = api.tick_state()
    except (OSError, ValueError):
        state = {}
    now = due.local(api.clock())
    items = []
    for d in api.items('schedules', req):
        rec = state.get(tick.key(d))
        rec = rec if isinstance(rec, dict) else {}
        what = rec.get('what') if isinstance(rec.get('what'), str) else None
        items.append(dict(d, last_outcome=what,
                          last_run=rec.get('at') if what == tick.STARTED
                          and isinstance(rec.get('at'), str) else None,
                          next_due=_next_due(d, now)))
    return {'items': items, 'tick': _tick_status(api)}


def _first_run(fields):
    """The first slot strictly after the schedule's set_at, as naive local HH:MM ISO text: the
    first candidate slot kitlib.autotasks.due.decide calls due with no record. None when off."""
    from kitlib.autotasks import due
    start, at = due.local(fields.get('set_at')), due.parse_at(fields.get('at'))
    if fields.get('cadence') == due.OFF or start is None or at is None:
        return None
    day = start.replace(hour=at[0], minute=at[1], second=0, microsecond=0)
    for k in range(9):
        slot = day + datetime.timedelta(days=k)
        if due.decide(fields, None, slot) == (due.DUE, slot):
            return slot.isoformat(timespec='minutes')
    return None


def _one_checkout(api, task, cadence, repo):
    """`invalid`, field repo, unless the tick could run task for repo: exactly one checkout of
    the machine config's launcher.repos matches it (kitlib.autotasks.tick.find_repo). Off, and a
    task that runs for no repository, need none."""
    from kitlib.autotasks import tick
    from kitlib.config import ConfigError
    if cadence == 'off' or not tick.TASKS[task].per_repo:
        return
    try:
        repos = api.schedule_repos()
    except (ConfigError, OSError) as e:      # what the tick takes as an unreadable list
        raise _invalid("the machine config's launcher.repos cannot be read: %s" % e, 'repo')
    if tick.find_repo(repo, repos) is None:
        raise _invalid("repo %r is not exactly one checkout in the machine config's "
                       "launcher.repos, so the automatic task could not run for it: add it "
                       "there once (launcher.repos), then set the schedule again" % repo, 'repo')


def schedules_set(api, req):
    b = _fields(req, ('task', 'repo', 'cadence', 'days', 'at', 'if_version'),
                required=('task', 'repo', 'cadence', 'at'))
    task = _one_of(b, 'task', SCHEDULE_TASKS)
    cadence = _one_of(b, 'cadence', CADENCES)
    at = _text(b, 'at')
    if not _AT.match(at):
        raise _invalid('at is a time of day as HH:MM, 24-hour (00:00 to 23:59), not %r' % at, 'at')
    days = _typed(b, 'days', (list,), 'a list of days') or []
    if not all(isinstance(d, str) and d in DAYS for d in days):
        raise _invalid('days is a list of %s' % ', '.join(DAYS), 'days')
    if cadence == 'weekly' and not days:
        raise _invalid('a weekly schedule needs days: one or more of %s' % ', '.join(DAYS), 'days')
    if cadence != 'weekly' and days:
        raise _invalid('days is for a weekly schedule, not a %s one' % cadence, 'days')
    pin = _pin(b)
    repo = _schedule_repo(api, _text(b, 'repo'))
    doc_id = '%s-%s' % (task, repo)
    why = schema.check_id(doc_id)
    if why:
        raise _invalid(why, 'repo')
    _one_checkout(api, task, cadence, repo)
    fields = {'task': task, 'repo': repo, 'cadence': cadence, 'at': at, 'set_at': api.clock(),
              'via': _via(req)}
    if cadence == 'weekly':
        fields['days'] = [d for d in DAYS if d in days]
    try:
        out = _put_pinned(api, 'schedules', doc_id, fields, pin)
    except HubError as e:
        if e.code == 'conflict':
            e.extra = {'id': doc_id}    # the schedule there, so the page can open it
        raise
    out['first_run'] = _first_run(fields)
    return out
