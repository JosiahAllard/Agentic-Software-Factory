"""The partition, the rank order and the offer.

Every record lands in exactly one bucket, and the banner refuses to print a partition that does
not add up. The offer is a greedy fill down the rank order: scopes of live pulls are occupied
first, and a comparison that cannot be decided is a collision, never a clearance.
"""
import re

from kitlib.git import cmd, refs

from . import record as rmod
from . import scope as smod

PULLABLE = 'pullable'
HELD = 'held'
PARKED = 'parked'
IN_FLIGHT = 'in flight'
UNPRIORITISED_OPEN = 'unprioritised+open'
REVIEW = 'to review'
UNCLASSIFIED = 'unclassifiable'
NOT_READ = 'unreadable'
BUCKETS = (PULLABLE, HELD, PARKED, IN_FLIGHT, UNPRIORITISED_OPEN, REVIEW, UNCLASSIFIED, NOT_READ)
EFFORT_ORDER = {'s': 0, 'm': 1, 'l': 2, '-': 3}
PROBE_TIMEOUT = 60
DEFAULT_CAP = 5


class PartitionError(Exception):
    """The buckets did not account for every file."""


def classify(rec):
    """`(bucket, None)`, or `(UNCLASSIFIED, why)`. Nothing is inferred."""
    if not rec.readable:
        return NOT_READ, None
    state = rec.get('state')
    if state is None or rmod._state(state) is not None:
        return UNCLASSIFIED, 'no readable `state`'
    if state == 'blocked':
        return HELD, None
    if state == 'parked':
        return PARKED, None
    if state == 'pulled':
        return IN_FLIGHT, None
    if state != 'open':
        return UNCLASSIFIED, '`state: %s` parses but has no bucket' % state
    if rec.has('review'):
        # Marked by the Action Hub's add: never offered, priced or not, until `clear-review`.
        return REVIEW, None
    priority = rec.get('priority')
    if priority is None or rmod._priority(priority) is not None:
        return UNCLASSIFIED, 'no readable `priority`'
    if priority == '-':
        return UNPRIORITISED_OPEN, None
    if rmod.is_band(priority):
        return PULLABLE, None
    return UNCLASSIFIED, '`priority: %s` parses but has no bucket' % priority


class Banner(object):
    """The line every read prints first, and the partition behind it."""

    def __init__(self, snap):
        self.snap = snap
        self.buckets = {k: [] for k in BUCKETS}
        self.unclassifiable_why = []
        for rec in snap.records:
            bucket, why = classify(rec)
            self.buckets[bucket].append(rec)
            if why:
                self.unclassifiable_why.append((rec, why))
        total = sum(len(v) for v in self.buckets.values())
        if total != snap.file_count:
            raise PartitionError('%d records in buckets, %d files read' % (total, snap.file_count))

    def count(self, bucket):
        return len(self.buckets[bucket])

    def held_groups(self):
        """`([(group, n)], multi)`: each held record counted once, under its first group."""
        counts = dict.fromkeys(rmod.HELD_ORDER, 0)
        multi = unaccounted = 0
        for rec in self.buckets[HELD]:
            kinds = [k for k in (rmod.blocker_kind(v) for v in rec.all('blocked_on')) if k]
            if len(kinds) > 1:
                multi += 1
            for group in rmod.HELD_ORDER:
                if group in kinds:
                    counts[group] += 1
                    break
            else:
                unaccounted += 1
        groups = [(g, counts[g]) for g in rmod.HELD_ORDER if counts[g]]
        if unaccounted:
            groups.append(('unaccounted (blocked with no nameable blocker)', unaccounted))
        if sum(n for _, n in groups) != self.count(HELD):
            raise PartitionError('the held breakdown does not sum to the held count')
        return groups, multi

    def lines(self):
        parts = ['BACKLOG %s' % self.snap.head_label(),
                 '%d records' % self.snap.file_count,
                 '%d pullable' % self.count(PULLABLE),
                 '%d held' % self.count(HELD),
                 '%d unprioritised+open' % self.count(UNPRIORITISED_OPEN)]
        if self.count(REVIEW):
            parts.append('%d %s' % (self.count(REVIEW), REVIEW))
        for bucket in (PARKED, IN_FLIGHT, NOT_READ, UNCLASSIFIED):
            if self.count(bucket):
                parts.append('%d %s' % (self.count(bucket), bucket))
        out = [' | '.join(parts)]
        groups, multi = self.held_groups()
        if self.count(HELD):
            line = 'held %d: %s' % (self.count(HELD), ', '.join('%d %s' % (n, g) for g, n in groups))
            if multi:
                line += '  (%d held on more than one thing, counted under the first)' % multi
            out.append(line)
        return out

    def review_lines(self):
        """`next`'s REVIEW lines, one per record in the `to review` bucket, by id: who marked
        it and what to read before `clear-review`. Nothing clears the mark but that command."""
        return [review_line(rec) for rec in sorted(self.buckets[REVIEW], key=lambda r: r.id)]

    def session_note(self, session, offered):
        """`next --session`'s NOTE when the offer may be short because of the key: the pullable
        records of other sessions or of none, which this key never offers; None without
        `session` or when there are none."""
        if session is None:
            return None
        counts = {}
        for rec in self.buckets[PULLABLE]:
            other = rec.get('session')
            if other != session:
                counts[other] = counts.get(other, 0) + 1
        if not counts:
            return None
        none = counts.pop(None, 0)
        named = ', '.join('%s: %d' % (s, counts[s]) for s in sorted(counts))
        return ('NOTE: %d pullable carry other sessions (%s) or none (%d); this key offers %d'
                % (sum(counts.values()) + none, named or '-', none, offered))

    def queue_warning(self):
        """A line under 10 pullable, or under 3 pullable at priority 1-2; never an exit code."""
        pullable = self.buckets[PULLABLE]
        high = [r for r in pullable if r.get('priority') in ('1', '2')]
        if len(pullable) >= 10 and len(high) >= 3:
            return None
        groups, _ = self.held_groups()
        largest = max(groups, key=lambda gn: gn[1]) if groups else None
        why = ('%d pullable' % len(pullable) if len(pullable) < 10
               else '%d pullable at priority 1-2' % len(high))
        return 'QUEUE DEPTH: %s. Largest held group: %s.' % (
            why, '%s (%d)' % largest if largest else 'none - nothing is held')


ANSWER_REVIEW = 'owner-answer'      # the mark plan.plan_answer writes for a free-text answer


def review_line(rec):
    """`REVIEW <ID>: ...`: the record's review mark, what to read and the `clear-review` that
    drops it. An owner-answer mark names the question whose answer was filed that day (the last
    such `answer:` line; else the last one)."""
    source, _, date = (rec.get('review') or '').partition(' ')
    clear = 'clear-review %s --reason "<what you read>"' % rec.id
    if source == ANSWER_REVIEW:
        answers = [v.split(' ', 1) for v in rec.all('answer')]
        same_day = [a for a in answers if len(a) == 2 and a[1].startswith(date)]
        pick = (same_day or answers or [['?']])[-1][0]
        return ("REVIEW %s: owner answer %s filed %s -> read '## Owner answer', then %s"
                % (rec.id, pick, date, clear))
    return 'REVIEW %s: marked by %s %s -> read the record, then %s' % (rec.id, source, date, clear)


# --- rank -----------------------------------------------------------------------------------------

def is_ranked(rec):
    return rec.readable and rmod.is_band(rec.get('priority'))


def rank_key(rec):
    """Preempt, pin, then `resume` (an unparked record: above priority, never a pin),
    priority, effort, id."""
    pin = rec.get('pin')
    pinned = pin is not None and rmod._pin(pin) is None
    resume = rec.get('resume')
    resumed = resume is not None and rmod._ruling(resume) is None
    effort = rec.get('effort')
    if effort not in EFFORT_ORDER:
        effort = '-'
    return (0 if rec.has('preempt') else 1, 0 if pinned else 1, int(pin) if pinned else 0,
            0 if resumed else 1, int(rec.get('priority')), EFFORT_ORDER[effort], rec.id)


def ranked(snap):
    """`{id: rank}` over every banded record, 1-based. Blocked and pulled records rank too."""
    order = sorted((r for r in snap.readable if is_ranked(r)), key=rank_key)
    return {r.id: i for i, r in enumerate(order, 1)}


def rank_label(rec, ranks):
    if rec.has('preempt'):
        return '*'
    return str(ranks.get(rec.id, '-')) if is_ranked(rec) else '-'


# --- in flight ------------------------------------------------------------------------------------

def live_branches(root, remote):
    """`(names, None)`: every local branch, `<remote>/<name>` and worktree branch; `(None, why)`
    when git fails (half an answer is no answer)."""
    out, why = cmd.run_text(['for-each-ref', '--format=%(refname)', 'refs/heads',
                             'refs/remotes/%s' % remote], root, timeout=PROBE_TIMEOUT)
    if out is None:
        return None, why
    trees, why = cmd.run_text(['worktree', 'list', '--porcelain'], root, timeout=PROBE_TIMEOUT)
    if trees is None:
        return None, why
    names = set()
    for ref in out.split('\n'):
        for prefix in ('refs/heads/', 'refs/remotes/%s/' % remote):
            if ref.startswith(prefix):
                names.add(ref[len(prefix):])
    for line in trees.split('\n'):
        if line.startswith('branch refs/heads/'):
            names.add(line[len('branch refs/heads/'):].strip())
    return names, None


def in_flight_split(snap, root, remote):
    """`(holding, dead, why)` over the pulled records. A pull is dead only when git answered and
    found no branch by its `pulled_by`; a failed probe holds every pull (fail closed)."""
    pulled = [r for r in snap.records if classify(r)[0] == IN_FLIGHT]
    if not pulled:
        return [], [], None
    names, why = live_branches(root, remote)
    if names is None:
        return pulled, [], why
    holding = [r for r in pulled if r.get('pulled_by') in names]
    return holding, [r for r in pulled if r not in holding], None


# --- requires -------------------------------------------------------------------------------------

def unmet_requires(rec, snap, history, root, main_ref):
    """`[(member, why)]` for every gated `requires:` line not met. `item:X` is met when X has no
    live record and the store history closed it; `commit:H` when H is on `main_ref` (the code
    main's rev, not the store's own branch: a PM data store shares no history with the code). Any
    other `requires:` line is a note and gates nothing. A git that cannot answer is unmet."""
    out = []
    for member in rec.all('requires'):
        kind, _, target = member.partition(':')
        if kind == 'item' and target and snap.rules.id_re.match(target):
            live = snap.get(target)
            if live is not None:
                out.append((member, 'it is still `state: %s`, not closed' % live.get('state')))
                continue
            last = history(target)
            if last is None or not re.match(r'^\S+ closes ', last):
                out.append((member, 'retired or deleted, so it will never land' if last else
                            'no record has that id and none was closed'))
        elif kind == 'commit' and target:
            ok, why = refs.is_ancestor(target, main_ref, root)
            if not ok:
                out.append((member, 'it is not on %s' % main_ref if ok is False
                            else 'git cannot tell (%s)' % why))
    return out


# --- the offer ------------------------------------------------------------------------------------

class Offer(object):
    # `split`: the Split `next` shows for a record offered only by narrowing (NARROWS), else None.
    def __init__(self, rec, rank, seeded=False, split=None):
        self.rec, self.rank, self.seeded, self.split = rec, rank, seeded, split


class PassedOver(object):
    def __init__(self, rec, rank, why, blocker=None, pair=None):
        # `blocker`: `(who, record id)` of the live pull or other session's record whose scope or
        # resource this one collides with; None for any other reason (`blocker_summary` counts
        # these). `pair`: `(this record's member, the taken member)` of a scope collision
        # (`narrowings` reads it); None for any other reason.
        self.rec, self.rank, self.why, self.blocker = rec, rank, why, blocker
        self.pair = pair


# --- resources ------------------------------------------------------------------------------------

def uses(rec):
    """The record's `uses:` names, each once."""
    return list(dict.fromkeys(rec.all('uses')))


def live_label(rec):
    return '%s (in flight on %s)' % (rec.id, rec.get('pulled_by') or '?')


def resource_load(holding, capacity):
    """`{name: [the live pulls of `holding` using it]}` for every name `capacity` declares. A name
    it does not declare counts nothing (`check` flags it). One git-free pass, so `show` shares it
    with the offer without computing one."""
    load = {name: [] for name in capacity}
    for rec in holding:
        for name in uses(rec):
            if name in load:
                load[name].append(rec)
    return load


def capacity_why(name, limit, takers):
    return 'resource %s at capacity %d, taken by %s' % (name, limit, ', '.join(takers))


def undeclared_why(names):
    return 'uses %s, which .kit.json does not declare' % ', '.join(names)


def uses_line(rec, holding, capacity):
    """`show`'s line: `uses dev-clone (1/1: X on B), gpu (0/1)`."""
    load = resource_load(holding, capacity)
    parts = []
    for name in uses(rec):
        if name not in capacity:
            parts.append('%s (not declared in .kit.json)' % name)
            continue
        taken = ['%s on %s' % (r.id, r.get('pulled_by') or '?') for r in load[name]]
        parts.append('%s (%d/%d%s)' % (name, len(taken), capacity[name],
                                       ': %s' % ', '.join(taken) if taken else ''))
    return 'uses %s' % ', '.join(parts)


def held_for_batch(rec, branch):
    """The branch `rec` is held for (its `batch:`, set for a topic session) when that is not
    `branch` (None: no branch, as the main checkout's `next`), else None."""
    batch = rec.get('batch')
    return batch if batch is not None and batch != branch else None


def _narrowed(rec, scopes, against, against_by, live_ids, narrow):
    """The Split `narrow(rec, others)` gives when every entry of `against` (the members taken,
    `against_by[i]` the record that took `against[i]`) that collides with `scopes` was taken by
    a live pull (`live_ids`), `others` those live pulls, each record once, in the order first
    hit; else None (the record is passed over as today). One hitter that is not a live pull
    (a record offered above, another session's) is no narrowing. Judged by the record that
    took each entry, never by its string: a record offered above may keep the very string a
    live pull holds. What `rec` keeps then collides with nothing else taken: each kept member
    lies inside a member of `scopes`, so whatever it collides with collides with that member
    too (members are compared by component prefix), and every such entry is one of `others`',
    all of which Split.keep is clear of."""
    hitters = []
    for m, by in zip(against, against_by):
        if any(smod.members_collide(c, m) for c in scopes) and not any(by is h for h in hitters):
            hitters.append(by)
    if not hitters or any(id(h) not in live_ids for h in hitters):
        return None
    try:
        return narrow(rec, hitters)
    except smod.UndecidableScope:
        return None


def next_offer(snap, holding, cap=DEFAULT_CAP, seeds=(), session=None, unmet=None,
               folded=None, capacity=None, branch=None, narrow=None, session_live=None):
    """`((offers, passed), refusals, ranks)`. `holding` are the live pulls whose scopes are taken
    first. `cap` None offers every startable record (`next --all`), with the same pass-overs. `unmet(rec)` -> `[(member, why)]` gates `requires:`. Only records whose `session:`
    equals `session` are offered; another session's record takes its scope silently. A record
    passed over on a collision names the holder: a live pull by id and branch, another session's
    record by id and session, a record already in this offer as `(offered above)`. Scopes are each record's current `scope:` lines, so a pulled record
    narrowed with `set --scope` frees what it gave up. A seed (`next --with`) goes first and is
    not passed over for the rest of the offer, but one that collides with a live pull is passed
    over like any record: only `pull --reason`, by a role that may take the `pull-collision` row
    (kitlib/backlog/roles.py), takes scope a live pull holds.
    `folded(rec)` -> a reason or None: a record of `session` it gives a reason for (its scope
    folds into another live pull's branch, or git cannot tell) is passed over with that reason
    and takes no scope; it is asked only of a record that would otherwise be offered.
    `capacity` ({resource: limit}; None: none declared) counts `uses:` the way scope is counted:
    the live pulls of `holding` first, then, in rank order, the records offered above and other
    sessions' records. A record with no room in any one of its resources is passed over, a live
    pull named as its blocker; each record taking a place takes one unit of each. A seed is
    measured against the live pulls alone. A candidate naming a resource `capacity` does not
    declare is passed over, never offered and never a crash.
    `branch` is the branch the offer is for: a record batched for another branch
    (held_for_batch) is passed over as held for that batch, and takes no scope.
    `narrow(rec, others)` -> a Split or None (None: no narrowing, as `pull` asks; only `next`
    passes one): a record of `session` whose every collision is with live pulls (`others`)
    is offered at its rank with the Split when `narrow` gives one (Offer.split, the NARROWS line)
    and takes only the scope it keeps (Split.keep), which `folded(rec, scopes=)` then judges;
    without one it is passed over as today.
    `session_live` (None: every other session's record reserves, as before; else a
    SessionLiveness) is asked of another session's record (one with a `session:`) about to take
    its place: a session not running takes nothing, scope or resource, and is added to
    `session_live.idle`; one whose liveness cannot be read reserves as before, its holder named
    `(liveness unread, so reserved)`. Nothing is written."""
    capacity = capacity or {}
    ranks = ranked(snap)
    occupied, occupied_by = [], []      # each member taken, and the record that took it
    for rec in holding:
        occupied.extend(rec.all('scope'))
        occupied_by.extend(rec for _m in rec.all('scope'))
    bad = ['an in-flight record has `scope: %s` - %s' % (m, smod.scope_member_error(m, True))
           for m in occupied if smod.scope_member_error(m, True) is not None]
    if bad:
        return None, bad, ranks
    live, live_by = list(occupied), list(occupied_by)
    live_ids = set(id(r) for r in holding)

    def own(r):
        return r.get('session') == session

    candidates = sorted((r for r in snap.readable if classify(r)[0] == PULLABLE), key=rank_key)
    mine_by_id = {r.id: r for r in candidates if own(r)}
    ordered = []
    for sid in seeds:
        if sid in mine_by_id and mine_by_id[sid] not in ordered:
            ordered.append(mine_by_id[sid])
    seeded = set(id(r) for r in ordered)
    ordered.extend(r for r in candidates if r not in ordered)

    offers, passed, reached, holders = [], [], set(), {}
    for rec in holding:
        for m in rec.all('scope'):
            holders.setdefault(m, ('%s (in flight on %s)' % (rec.id, rec.get('pulled_by') or '?'),
                                   (rec.get('pulled_by') or '?', rec.id)))
    live_takers = {name: [(live_label(r), (r.get('pulled_by') or '?', r.id)) for r in recs]
                   for name, recs in resource_load(holding, capacity).items()}
    takers = {name: list(held) for name, held in live_takers.items()}
    for rec in ordered:
        reached.add(id(rec))
        rid = rec.id
        held = held_for_batch(rec, branch)
        if held is not None:
            if own(rec):
                passed.append(PassedOver(rec, ranks.get(rid, '-'), 'held for the batch on %s'
                                         % held))
            continue
        scopes = rec.all('scope')
        errs = [(m, smod.scope_member_error(m, True)) for m in scopes]
        errs = [(m, why) for m, why in errs if why is not None]
        if not scopes:
            errs = [('(no scope line)', 'a record with no `scope:` is never offered')]
        if errs:
            if own(rec):
                passed.append(PassedOver(rec, ranks.get(rid, '-'), 'scope undecidable, so this '
                                         'is a collision: `%s` - %s' % errs[0]))
            continue
        # A seed goes first over the rest of this offer, never over a live pull's scope.
        against = live if id(rec) in seeded else occupied
        against_by = live_by if id(rec) in seeded else occupied_by
        try:
            hit = smod.colliding_pairs(scopes, against) if against else []
        except smod.UndecidableScope as exc:
            if own(rec):
                passed.append(PassedOver(rec, ranks.get(rid, '-'),
                                         'scope undecidable, so this is a collision: %s' % exc))
            continue
        split = None
        if hit and narrow is not None and own(rec):
            split = _narrowed(rec, scopes, against, against_by, live_ids, narrow)
        if hit and split is None:
            if own(rec):
                holder, who = holders.get(hit[0][1]) or (None, None)
                passed.append(PassedOver(rec, ranks.get(rid, '-'), 'scope %s collides with %s '
                                         'already taken%s' % (hit[0][0], hit[0][1], ' by %s'
                                         % holder if holder else ''), who, hit[0]))
            continue
        if split is not None:
            scopes = list(split.keep)
        names = uses(rec)
        unknown = [n for n in names if n not in capacity]
        if unknown:
            if own(rec):
                passed.append(PassedOver(rec, ranks.get(rid, '-'), undeclared_why(unknown)))
            continue
        # As for scope: a seed goes first over the rest of this offer, never over a live pull.
        room = live_takers if id(rec) in seeded else takers
        full = [n for n in names if len(room[n]) >= capacity[n]]
        if full:
            if own(rec):
                held = room[full[0]]
                passed.append(PassedOver(rec, ranks.get(rid, '-'),
                                         capacity_why(full[0], capacity[full[0]],
                                                      [label for label, _ in held]),
                                         next((who for _, who in held if who), None)))
            continue
        if own(rec) and unmet is not None:
            missing = unmet(rec)
            if missing:
                passed.append(PassedOver(rec, ranks.get(rid, '-'), 'requires %s, which is unmet: '
                                         '%s' % missing[0]))
                continue
        if own(rec) and folded is not None:
            why = folded(rec) if split is None else folded(rec, scopes=scopes)
            if why:
                passed.append(PassedOver(rec, ranks.get(rid, '-'), why))
                continue
        unread = ''
        if not own(rec) and session_live is not None and rec.get('session') is not None:
            running, _why = session_live.live(rec.get('session'))
            if running is False:            # an idle session's record takes no place
                if (rid, rec.get('session')) not in session_live.idle:
                    session_live.idle.append((rid, rec.get('session')))
                continue
            if running is None:
                unread = ' (liveness unread, so reserved)'
        occupied.extend(scopes)
        occupied_by.extend(rec for _m in scopes)
        taker = (('%s (offered above)' % rid, None) if own(rec) else
                 ('%s (session %s)%s' % (rid, rec.get('session') or 'none', unread),
                  ('session %s' % (rec.get('session') or 'none'), rid)))
        for m in scopes:
            holders.setdefault(m, taker)
        for name in names:
            takers[name].append(taker)
        if not own(rec):
            continue
        offers.append(Offer(rec, ranks.get(rid, '-'), id(rec) in seeded, split))
        if cap is not None and len(offers) >= cap:
            break
    for rec in ordered:
        if id(rec) not in reached and own(rec):
            passed.append(PassedOver(rec, ranks.get(rec.id, '-'),
                                     'not reached - the cap was already full'))
    return (offers, passed), [], ranks


def blocker_summary(passed, top=5):
    """`['blocks N: <branch or session> (<record id>)', ...]`: the live pulls and other sessions'
    records that hold the scope of the most passed-over records, most first (ties by name), at
    most `top` lines. Empty when nothing was passed over on such a collision."""
    counts = {}
    for p in passed:
        if p.blocker is not None:
            counts[p.blocker] = counts.get(p.blocker, 0) + 1
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:top]
    return ['blocks %d: %s (%s)' % (n, who, rid) for (who, rid), n in ordered]


# --- whether another session runs (next_offer's `session_live`) ----------------------------------

def launcher_stems():
    """{sessions.json key: stem} of the launcher's sessions.json (docs/launcher.md "State
    files"); a record's `session:` is such a key. Raises when it cannot be read."""
    from kitlib.launcher import settings, state
    return {k: e.get('stem') for k, e in state.entries(state.read_sessions(settings.load()))}


def running_names():
    """The names of the session processes running now, as the hub's coordinator restart job reads
    them (kitlib.launcher.lookup: launched, rc_name). Raises when they cannot be listed."""
    from kitlib import pmstart
    from kitlib.launcher import lookup
    rows = lookup.launched(pmstart.list_processes(timeout=pmstart.TIMEOUT))
    return [n for n in (lookup.rc_name(p.get('CommandLine')) for p in rows) if n]


def _failure(exc):
    return '%s: %s' % (type(exc).__name__, ' '.join(str(exc).split()))


class SessionLiveness(object):
    """`live(session)` -> `(True|False, None)` or `(None, why it cannot be read)`, never a raise:
    True when a running session's name starts with the stem sessions.json gives that key (any
    account, any recycle: lookup.running_stem's test). `sessions()` and `names()` (default
    launcher_stems, running_names) are each read at most once, and the process list only for a
    key that has a stem. `idle` [(record id, session)]: the records next_offer let take no place
    because their session is not running."""

    def __init__(self, sessions=None, names=None):
        self._sessions_reader = sessions or launcher_stems
        self._names_reader = names or running_names
        self._stems = self._names = None
        self.idle = []
        self.unread = {}                    # session -> why its liveness could not be read

    def _read(self, attr, reader):
        if getattr(self, attr) is None:
            try:
                setattr(self, attr, (reader(), None))
            except Exception as exc:        # noqa: BLE001 - any failure is unread, never a raise
                setattr(self, attr, (None, _failure(exc)))
        return getattr(self, attr)

    def live(self, session):
        stems, why = self._read('_stems', self._sessions_reader)
        if why is None:
            stem = (stems or {}).get(session)
            if not isinstance(stem, str) or not stem:
                why = 'session %s is not in sessions.json' % session if session not in (
                    stems or {}) else 'session %s has no stem in sessions.json' % session
        if why is None:
            names, why = self._read('_names', self._names_reader)
            if why is None:
                return any((n or '').startswith(stem) for n in names), None
        self.unread[session] = why
        return None, why


def idle_lines(session_live):
    """`next`'s NOTE for each record that reserved no scope because its session is not running."""
    if session_live is None:
        return []
    return ['NOTE: %s (session %s not running) reserves no scope' % (rid, s)
            for rid, s in session_live.idle]


def unread_lines(session_live, passed):
    """`next`'s NOTE for each session whose liveness could not be read and whose record holds the
    scope of a passed-over record: it reserves as before, and why."""
    if session_live is None:
        return []
    out = []
    for p in passed:
        who = p.blocker[0] if p.blocker else ''
        session = who[len('session '):] if who.startswith('session ') else None
        if session in session_live.unread:
            line = ('NOTE: session %s liveness unread (%s): its records reserve their scope as '
                    'before' % (session, session_live.unread[session]))
            if line not in out:
                out.append(line)
    return out


# --- a short offer's causes: releasable holds and narrowable pass-overs (`next`) -----------------

HUB_TIMEOUT_S = 3       # one hub listing; `next` never waits longer on the hub than this
NARROW_SHOWN = 8        # changed files a NARROW line names before `+N more`


def is_broad(member):
    """A whole-folder glob (`x/**`) or a literal doc file (`*.md`): the scope members whose
    collision with a live pull `next` names a narrowing for (`narrowings`)."""
    if member.endswith(smod.GLOB_SUFFIX):
        return True
    return member.lower().endswith('.md') and not any(c in member for c in smod.WILDCARD_CHARS)


def hub_statuses(listing=None, timeout=HUB_TIMEOUT_S):
    """`({hub id: status}, None)` of every hub question and to-do on this machine, read with the
    agent token (read only); `(None, why)` on any failure, never a raise. `listing(collection)`
    gives a collection's documents (default: the hub client, `timeout` seconds a call)."""
    try:
        if listing is None:
            from kitlib.hubsite import client, settings
            hub = client.Client(settings.load().port, timeout=timeout)

            def listing(coll):
                return hub.listing(coll, status='all', role=client.AGENT)
        out = {}
        for coll in ('questions', 'todos'):
            for doc in listing(coll) or ():
                if isinstance(doc, dict) and isinstance(doc.get('status'), str):
                    out[str(doc.get('id'))] = doc['status']
        return out, None
    except Exception as exc:            # noqa: BLE001 - any failure is the one note line
        return None, '%s: %s' % (type(exc).__name__, ' '.join(str(exc).split()))


def closed_reader(snap, history):
    """`closed(rid)` for conditions.releasable: the short sha of the store commit that closed
    `rid`, or None when it is still live, was retired or deleted, or was never closed (as
    `unmet_requires` reads an `item:` requirement)."""
    def closed(rid):
        if snap.get(rid) is not None:
            return None
        last = history(rid)
        return last.split(' ', 1)[0] if last and re.match(r'^\S+ closes ', last) else None
    return closed


def held_of(snap, session):
    """The held records of `session`, by id."""
    return sorted((r for r in snap.records if classify(r)[0] == HELD
                   and r.get('session') == session and r.readable), key=lambda r: r.id)


def cites_hub(recs):
    """True when any record in `recs` cites a hub id where conditions.releasable reads one: an
    `until` line."""
    from . import conditions as cmod
    return any(cmod.cited_ids(' '.join(r.all('until'))) for r in recs)


def releasable_holds(snap, session, history, hub=None):
    """`([(rec, RELEASABLE line)], note)`: the held records of `session` whose cited end is met
    (conditions.releasable). `hub()` -> `(statuses, why)` (default `hub_statuses`) is asked only
    when one of them cites a hub id; a hub that cannot be read gives the one `note` line, and
    then only `item:` ends are judged. `history(rid)` is the store's (Context.history)."""
    from . import conditions as cmod
    held = held_of(snap, session)
    if not held:
        return [], None
    statuses, note = {}, None
    if cites_hub(held):
        statuses, why = (hub or hub_statuses)()
        if statuses is None:
            statuses, note = {}, 'releasable: hub not read (%s); Q-/T- holds not checked' % why
    closed, ended = closed_reader(snap, history), ended_reader(snap, history)
    ranks = ranked(snap)
    out = []
    for rec in held:
        restore = _restore(rec, ended, snap.rules)
        merged = False
        for member, why, reason in cmod.releasable(rec, statuses.get, closed):
            rank = rank_label(rec, ranks)
            if restore and not merged and member in ['item:%s' % o for o in restore[0]]:
                merged = True
                out.append((rec, 'RESTORE+RELEASABLE %s (rank %s): %s -> `%s`; `unblock %s --on %s '
                                 '--reason "%s"`' % (rec.id, rank, restore[1], restore[2], rec.id,
                                                     member, reason)))
                continue
            out.append((rec, releasable_line(rec, rank, member, why, reason)))
    return out, note


def ended_reader(snap, history):
    """`ended(rid)`: why the other record of a `narrowed:` line holds its part no more - `landed
    (closed in <sha>)`, `retired (in <sha>)` or `deleted (in <sha>)` by the store's history, or
    `released (open again)` for one back to open; None while it is pulled, blocked or parked (its
    branch may go on) or when the history cannot say."""
    def ended(rid):
        rec = snap.get(rid)
        if rec is not None:
            return 'released (open again)' if rec.get('state') == 'open' else None
        m = re.match(r'^(\S+) (closes|retires|deletes) ', history(rid) or '')
        if m is None:
            return None
        return ('landed (closed in %s)' if m.group(2) == 'closes' else
                ('retired' if m.group(2) == 'retires' else 'deleted') + ' (in %s)') % m.group(1)
    return ended


def _restore(rec, ended, rules):
    """`(other ids, why, set command)` that gives `rec` back the original members of each
    `narrowed:` line whose other record holds its part no more (`ended(rid)` -> why or None,
    ended_reader), clearing those lines and keeping the rest (narrowed.restore_set, the landing
    scope check's form too); None when no line names such a record. A member narrowed for
    several records (one `narrowed:` line each, a split against several live pulls) comes back
    only once every one of them has ended: given back while one still holds its part, the
    member would cover that record's live paths again."""
    from . import narrowed as nmod
    whys, by_member = {}, {}
    for v in rec.all('narrowed'):
        line, _err = rmod.narrowed_line(v, rules)
        if line is not None:
            by_member.setdefault(line[0], []).append(line[1])

    def gone(rid):
        whys.setdefault(rid, ended(rid))
        return whys[rid] is not None

    def done(line):
        return all(gone(o) for o in by_member.get(line[0], [line[1]]))
    got = nmod.restore_set(rec.id, rec.all('scope'), rec.all('narrowed'), rules, done)
    if got is None:
        return None
    _originals, others, args = got
    why = ', '.join('%s %s' % (o, whys[o]) for o in others)
    return others, why, ' '.join(args)


def restore_lines(snap, session, history):
    """`next`'s plain RESTORE lines: for each record of `session` carrying a `narrowed:` line
    whose other record holds its part no more - landed, retired, deleted or released
    (ended_reader; `_restore`) - the `set` that restores its original members and clears those
    lines. A held record releasable_holds gives a RELEASABLE line on one of those records (an
    `item:` hold on one now closed: conditions.releasable, read as it reads it) is left out:
    releasable_holds prints the two as one RESTORE+RELEASABLE line. One whose other record was
    released, retired or deleted has no such line, so it gets the plain RESTORE line."""
    from . import conditions as cmod
    ended, closed = ended_reader(snap, history), closed_reader(snap, history)
    out = []
    for rec in sorted(snap.readable, key=lambda r: r.id):
        if rec.get('session') != session or not rec.has('narrowed'):
            continue
        restore = _restore(rec, ended, snap.rules)
        if restore is None:
            continue
        if classify(rec)[0] == HELD:
            listed = [m for m, _w, _r in cmod.releasable(rec, {}.get, closed)]
            if any('item:%s' % o in listed for o in restore[0]):
                continue
        out.append('RESTORE %s: %s -> `%s`' % (rec.id, restore[1], restore[2]))
    return out


def releasable_line(rec, rank, member, why, reason):
    """`next`'s RELEASABLE line: what is met, and the `unblock` that ends the hold. An `owner`
    member is the PM's or the owner's to clear (the unblock-owner row)."""
    who = "the PM's (unblock-owner): " if member == 'owner' else ''
    return 'RELEASABLE %s (rank %s): %s -> %s`unblock %s --on %s --reason "%s"`' % (
        rec.id, rank, why, who, rec.id, member, reason)


def _under(member, paths):
    out = []
    for p in paths:
        try:
            if smod.members_collide(member, p):
                out.append(p)
        except smod.UndecidableScope:
            out.append(p)
    return out


def _named(paths):
    shown = ', '.join(paths[:NARROW_SHOWN])
    return shown + (' +%d more' % (len(paths) - NARROW_SHOWN) if len(paths) > NARROW_SHOWN else '')


def wants_narrowing(passed):
    """True when a record in `passed` collided on scope with a broad member (`narrowings` would
    ask the branch diffs)."""
    return any(p.pair is not None and p.blocker is not None
               and (is_broad(p.pair[0]) or is_broad(p.pair[1])) for p in passed)


_UNQUOTED = re.compile(r'[^A-Za-z0-9_./@%+=:,-]')


def narrowings(passed, holding, changes, unknown, dirty=None):
    """`next`'s NARROW lines: one for each record passed over on a scope collision with a live
    pull (in `holding`) where either member is broad (`is_broad`). `changes` and `unknown` are
    plan `_live_changes`'s: `{branch: [(label, paths)]}`, the paths each live branch changed
    since its merge base with the code main, and `{branch: error}`. A broad taken member gets the
    `set --scope` that narrows the live pull to the files its branch changed there, unless those
    files overlap this record too (then it waits for the landing); a broad member of this record
    only gets the overlap named. `dirty` is `{branch: (paths, why)}` (plan `live_dirty`): the
    uncommitted files of each live branch's worktree, or `(None, why)` when they could not be
    read. The `set` keeps those files and the pull's `## Files` paths with the committed ones (the
    NARROWS split's protected set, plan `_narrower`). It is not suggested when either cannot be
    read, when `dirty` has no entry for the branch, or when a kept path has a space, a quote or a
    shell-special character (it would split when pasted)."""
    dirty = dirty or {}
    live = {r.id: r for r in holding}
    out = []
    for p in passed:
        if p.pair is None or p.blocker is None:
            continue
        other = live.get(p.blocker[1])
        if other is None or other.get('pulled_by') != p.blocker[0]:
            continue
        mine, theirs = p.pair
        if not (is_broad(mine) or is_broad(theirs)):
            continue
        branch = other.get('pulled_by')
        head = 'NARROW %s (rank %s): %s collides with %s of %s on %s' % (
            p.rec.id, p.rank, mine, theirs, other.id, branch)
        if branch in unknown:
            out.append('%s; cannot tell what %s changed (%s)' % (head, branch, unknown[branch]))
            continue
        if branch not in changes:
            out.append('%s; %s\'s diff was not compared' % (head, branch))
            continue
        files, files_why = files_section(other.body)
        extra, extra_why = dirty.get(branch) or (None, 'its uncommitted files were not read')
        unread = files_why if files is None else (extra_why if extra is None else None)
        paths = sorted(set(q for _label, qs in changes[branch] for q in qs)
                       | set(extra or []) | set(files or []))
        there = _under(theirs, paths)
        if not there and unread and is_broad(theirs):
            out.append('%s; not narrowing %s: %s' % (head, other.id, unread))
            continue
        if not there:
            out.append('%s, which changed nothing under %s yet' % (head, theirs))
            continue
        head = '%s, which changed %d file(s) there: %s' % (head, len(there), _named(there))
        overlap = _under(mine, there)
        if is_broad(theirs) and not overlap and unread:
            out.append('%s; not narrowing %s: %s' % (head, other.id, unread))
        elif is_broad(theirs) and not overlap:
            keep = [m for m in other.all('scope') if m != theirs]
            added = [q for q in there if q not in keep]
            # the record's own members print as they are (a glob single-quoted); only the paths
            # added must paste whole
            odd = [m for m in added if _UNQUOTED.search(m)] + [m for m in keep if "'" in m]
            if odd:
                out.append('%s; not narrowing %s: %s would split when pasted into a shell' % (
                    head, other.id, odd[0]))
                continue
            out.append('%s -> `set %s %s`' % (head, other.id, ' '.join(
                "--scope '%s'" % m if '*' in m else '--scope %s' % m for m in keep + added)))
        elif is_broad(mine) and not is_broad(theirs):
            out.append('%s; %s is broad: narrow %s\'s own scope to leave out %s' % (
                head, mine, p.rec.id, theirs))
        else:
            out.append('%s; %s overlaps that work too (%s): pull %s after %s lands' % (
                head, mine, _named(overlap), p.rec.id, branch))
    return out


# --- NARROWS: a record held only by a live pull's protected set ---------------------------------

FILES_HEADING = 'files'
_HEADING = re.compile(r'^(#{1,6})(?:\s+(.*?))?\s*#*\s*$')
_FENCE = ('```', '~~~')
_OTHER_LIST = re.compile(r'^(?:\+|\d+[.)])(?:\s|$)')     # a `+` bullet or a numbered item
# One list item: exactly one backticked path, which a ` - note` or ` — note` may follow, or one
# bare path with nothing after it.
_FILES_ITEM = re.compile(r'^[-*]\s+(?:`([^`\s]+)`(?:\s+[-\u2014]\s+\S.*)?|([^`\s]+))$')


def files_section(body):
    """`(paths, None)`: the paths the list items (`- ` or `* `) of a record body's `## Files`
    section name, each once, in order; `[]` when there is no such section or it lists nothing
    (no plan). The section runs from a level-2 `## Files` heading (any letter case) to the next
    heading of level 1 or 2; a heading inside a code fence is not one. Lines that are not list
    items are prose and are skipped. An item is exactly one backticked path, which a ` - note`
    or ` — note` may follow, or one bare path with nothing after it. `(None, why)` when the plan
    cannot be read, so nothing is narrowed by it: an item of any other shape (a word before
    the backticked path, `- a.py: rewrite`), a path that is not a plain repo path (a glob, a
    backslash, an absolute path), a list item of another shape (a `+` bullet, a numbered item),
    or a deeper heading or a code fence inside the section."""
    paths, inside, fenced = [], False, False
    for line in (body or '').split('\n'):
        s = line.strip()
        if s.startswith(_FENCE):
            if inside:
                return None, '`## Files` holds a code fence (`%s`)' % s
            fenced = not fenced
            continue
        h = None if fenced else _HEADING.match(s)
        if h:
            level = len(h.group(1))
            if inside and level <= 2:
                break
            if inside:
                return None, '`## Files` holds a subheading (`%s`)' % s
            inside = level == 2 and ' '.join((h.group(2) or '').split()).casefold() == \
                FILES_HEADING
            continue
        if inside and _OTHER_LIST.match(s):
            return None, ('`## Files` item `%s`: only `- ` and `* ` items are read; another '
                          'list shape would leave part of the plan unread' % s)
        if not inside or not (s[:2] in ('- ', '* ') or s in ('-', '*')):
            continue
        m = _FILES_ITEM.match(s)
        if m is None:
            return None, ('`## Files` item `%s`: not one path (one backticked path, or one bare '
                          'path with nothing after it)' % s)
        path = m.group(1) or m.group(2)
        err = ('a glob, not a path' if any(c in path for c in smod.WILDCARD_CHARS)
               else smod.path_shape_error(path, 'path', comma_ok=True))
        if err is not None:
            return None, '`## Files` item `%s`: %s' % (s, err)
        if path not in paths:
            paths.append(path)
    return paths, None


class Part(object):
    """One live pull's side of a Split: `other` the live pull, `other_keep` its scope after the
    narrowing, `l_changes` `[(member, dropped, kept)]` for each member of it the narrowing
    changes, `plan` that it has a `## Files` section (without one it is not narrowed), and
    `c_members` the candidate's changed members that collided with it (each gets a `narrowed:`
    line naming `other`)."""

    def __init__(self, other, other_keep, l_changes, plan, c_members):
        self.other, self.other_keep, self.l_changes = other, other_keep, l_changes
        self.plan, self.c_members = plan, c_members


class Split(object):
    """A narrowing `next` shows and `pull` writes: the candidate narrowed clear of every live
    pull it collides with, one Part per live pull (`parts`). `keep` is the candidate's scope
    after it, `c_changes` `[(member, dropped, kept)]` for each candidate member it changes."""

    def __init__(self, keep, c_changes, parts):
        self.keep, self.c_changes, self.parts = keep, c_changes, parts

    def narrows_other(self):
        """True when the split narrows a live pull's record too (a Part has l_changes)."""
        return any(p.l_changes for p in self.parts)


def split_all(c_scopes, parts, tree):
    """`(Split, None)`, or `(None, why)` when the candidate whose scope is `c_scopes` cannot be
    narrowed clear of every live pull of `parts`, `[(other, protected, plan)]`, one per record.
    `protected` is T, that live pull's protected set (the paths its branch changed, its
    worktree's dirty paths and the paths its `## Files` names); `plan` says it has a `## Files`
    section; `tree` is the tracked paths at the code main. Each colliding member of the
    candidate loses, for each live pull it collides with, that pull's T (scope.complement) and,
    for a pull without a plan, the whole of each of its colliding members, which must lie
    strictly inside the candidate's member (inside its `/**` glob, or below it as a bare
    folder). A live pull with a plan loses the tracked part of its overlap with the candidate
    that is in no live pull's T (so no path one gives up is left held by nobody), keeping every
    path of its own T under it (a file it adds that the tree has not got included). Fails
    closed, for every part if for one: complement gives None, a side is left with no scope, a
    member is not a legal scope, a comparison is undecidable, or what the candidate keeps still
    touches any T or collides with any live pull's kept scope."""
    try:
        return _split(c_scopes, parts, tree)
    except smod.UndecidableScope as exc:
        return None, 'undecidable: %s' % exc


def _split(c_scopes, parts, tree):
    base, rel, within = smod.member_base, smod.paths_related, smod.path_within
    if not parts:
        return None, 'no live pull to narrow against'
    if len(set(id(o) for o, _t, _f in parts)) != len(parts):
        return None, 'a live pull named twice'
    sides = []      # (other, its scope, colliding pairs, its sorted T, plan)
    for other, protected, plan in parts:
        l_scopes = other.all('scope')
        pairs = smod.colliding_pairs(c_scopes, l_scopes)
        if not pairs:
            return None, 'no collision with %s' % other.id
        sides.append((other, l_scopes, pairs, sorted(set(protected)), plan))
    prot_all = sorted(set(t for side in sides for t in side[3]))
    pset_all = set(prot_all)
    tracked = list(tree)
    c_kept, c_hit = [], {}          # c_hit: candidate member -> the indexes of the sides it hits
    for c in c_scopes:
        removed = []
        for i, (other, _l, pairs, prot, plan) in enumerate(sides):
            ls = [l for cc, l in pairs if cc == c]
            if not ls:
                continue
            c_hit.setdefault(c, []).append(i)
            removed += [t for t in prot if rel(base(c), t)]
            if not plan:
                for l in ls:
                    if not (smod.strictly_inside(l, c) or smod.inside_bare_folder(l, c)):
                        return None, ('%s of %s is not inside %s, and %s has no `## Files`: '
                                      'only a broad candidate is narrowed'
                                      % (l, other.id, c, other.id))
                    removed += [p for p in tracked if within(base(l), p)]
        if c not in c_hit:
            c_kept.append((c, None))
            continue
        kept = smod.complement(c, sorted(set(removed)), tracked)
        if kept is None:
            return None, 'complement cannot narrow %s' % c
        c_kept.append((c, kept))
    l_kept_all = []
    for other, l_scopes, pairs, _prot, plan in sides:
        l_kept = []
        for l in l_scopes:
            cs = [c for c, ll in pairs if ll == l]
            if not cs or not plan:
                l_kept.append((l, None))
                continue
            removed = []
            for c in cs:
                region = base(smod.deeper_member(c, l))
                removed += [p for p in tracked if within(region, p) and p not in pset_all]
            kept = smod.complement(l, sorted(set(removed)), tracked) if removed else [l]
            if kept is None:
                return None, 'complement cannot narrow %s of %s' % (l, other.id)
            extra = [t for t in prot_all if within(base(l), t)
                     and not any(rel(base(k), t) for k in kept)]
            l_kept.append((l, sorted(set(kept + extra))))
        l_kept_all.append(l_kept)

    def flat(pairs_kept):
        out = []
        for m, kept in pairs_kept:
            for k in ([m] if kept is None else kept):
                if k not in out:
                    out.append(k)
        return out
    keep = flat(c_kept)
    if not keep:
        return None, 'nothing of the candidate\'s scope is left'
    other_keeps = [flat(lk) for lk in l_kept_all]
    for side, other_keep in zip(sides, other_keeps):
        if not other_keep:
            return None, 'nothing of %s\'s scope is left' % side[0].id
    for m in keep + [m for ok in other_keeps for m in ok]:
        err = smod.scope_member_error(m)
        if err is not None:
            return None, '`%s` is not a scope: %s' % (m, err)
    for side, other_keep in zip(sides, other_keeps):
        if smod.colliding_pairs(keep, other_keep):
            return None, 'what is kept still collides with %s' % side[0].id
        if any(rel(base(k), t) for k in keep for t in side[3]):
            return None, 'what is kept still touches the protected set of %s' % side[0].id
    all_other_keep = [m for ok in other_keeps for m in ok]
    c_changes, changed = [], []
    for c, kept in c_kept:
        if kept is None or kept == [c]:
            continue
        changed.append(c)
        dropped = []
        for m in all_other_keep:
            if rel(base(m), base(c)) and m not in dropped:
                dropped.append(m)
        dropped += [t for t in prot_all if rel(base(c), t) and t not in dropped
                    and not any(rel(base(m), t) for m in dropped)]
        c_changes.append((c, sorted(dropped), kept))
    out = []
    for i, (side, l_kept, other_keep) in enumerate(zip(sides, l_kept_all, other_keeps)):
        l_changes = [(l, sorted(m for m in keep if rel(base(m), base(l))), kept)
                     for l, kept in l_kept if kept is not None and kept != [l]]
        out.append(Part(side[0], other_keep, l_changes, side[4],
                        [c for c in changed if i in c_hit.get(c, ())]))
    return Split(keep, c_changes, out), None


def narrows_line(offer):
    """`next`'s NARROWS line for an offer made by narrowing (Offer.split): what the candidate,
    then each live pull, drops and keeps. `next` writes nothing; `pull` of the record recomputes
    the split and writes it (plan.plan_pull)."""
    s = offer.split
    return 'NARROWS %s (rank %s) with %s: %s %s; %s' % (
        offer.rec.id, offer.rank,
        ', '.join('%s on %s' % (p.other.id, p.other.get('pulled_by') or '?') for p in s.parts),
        offer.rec.id, side_text(s.c_changes, named=_named),
        '; '.join('%s %s' % (p.other.id, side_text(p.l_changes, p.plan, _named))
                  for p in s.parts))


def side_text(changes, plan=True, named=', '.join):
    """One side of a narrowing (Split.c_changes or l_changes): `drops D from M (keeps K)` per
    member changed, or `keeps its scope` (` (no ## Files)` when `plan` is False: only the
    candidate is narrowed). `named(paths)` lists D and K. The one text of `next`'s NARROWS line
    (_named, cut short) and `pull`'s NARROWED lines (plan._apply_split, every path)."""
    if not changes:
        return 'keeps its scope%s' % ('' if plan else ' (no ## Files)')
    return '; '.join('drops %s from %s (keeps %s)' % (named(d) or 'nothing', m,
                                                      named(k) or 'nothing')
                     for m, d, k in changes)


def pass_counts(passed):
    """`(n, [(kind, count)])` of the passed-over records by reason, for `next`'s counts line."""
    kinds = [('scope', 0), ('fold', 0), ('resource', 0), ('not reached', 0), ('other', 0)]
    counts = dict(kinds)
    for p in passed:
        why = p.why
        if p.pair is not None or why.startswith('scope undecidable'):
            kind = 'scope'
        elif why.startswith('scope overlaps the work of') or why.startswith('cannot tell whether'):
            kind = 'fold'
        elif why.startswith('resource ') or why.startswith('uses '):
            kind = 'resource'
        elif why.startswith('not reached'):
            kind = 'not reached'
        else:
            kind = 'other'
        counts[kind] += 1
    return len(passed), [(k, counts[k]) for k, _ in kinds]


def not_offered_why(rec, passed, session=None):
    """Why `rec` is not in the offer, named."""
    bucket, why = classify(rec)
    if bucket == HELD:
        return 'it is held on %s' % ', '.join(rec.all('blocked_on'))
    if bucket == PARKED:
        return 'it is parked'
    if bucket == REVIEW:
        return ('it is marked for review (review: %s): `clear-review %s --reason T` first'
                % (rec.get('review'), rec.id))
    if bucket == UNPRIORITISED_OPEN:
        return 'it is unprioritised, so it has no rank and is never offered'
    if bucket == UNCLASSIFIED:
        return 'it cannot be classified: %s' % why
    if rec.get('session') != session:
        if rec.get('session'):
            return ('it belongs to session `%s`, so only `next --session %s` offers it'
                    % (rec.get('session'), rec.get('session')))
        return 'it has no `session:`, and `--session %s` offers only that session\'s records' % session
    for p in passed:
        if p.rec is rec:
            return 'it was passed over at rank %s: %s' % (p.rank, p.why)
    return 'it is not in the offer'
