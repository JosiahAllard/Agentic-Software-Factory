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

def unmet_requires(rec, snap, history, root, main_branch):
    """`[(member, why)]` for every gated `requires:` line not met. `item:X` is met when X has no
    live record and the store history closed it; `commit:H` when H is on `main_branch`. Any
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
            ok, why = refs.is_ancestor(target, 'refs/heads/%s' % main_branch, root)
            if not ok:
                out.append((member, 'it is not on %s' % main_branch if ok is False
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
    """The Split `narrow(rec, other)` gives when every entry of `against` (the members taken,
    `against_by[i]` the record that took `against[i]`) that collides with `scopes` was taken by
    one record `other`, and `other` is a live pull (`live_ids`); else None (the record is passed
    over as today). Judged by the record that took each entry, never by its string: a record
    offered above may keep the very string a live pull holds. What `rec` keeps then collides
    with nothing else taken: each kept member lies inside a member of `scopes`, so whatever it
    collides with collides with that member too (members are compared by component prefix),
    and every such entry is `other`'s, which Split.keep is clear of."""
    hitters = []
    for m, by in zip(against, against_by):
        if any(smod.members_collide(c, m) for c in scopes) and not any(by is h for h in hitters):
            hitters.append(by)
    if len(hitters) != 1 or id(hitters[0]) not in live_ids:
        return None
    try:
        return narrow(rec, hitters[0])
    except smod.UndecidableScope:
        return None


def next_offer(snap, holding, cap=DEFAULT_CAP, seeds=(), session=None, unmet=None,
               folded=None, capacity=None, branch=None, narrow=None):
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
    `narrow(rec, other)` -> a Split or None (None: no narrowing, as `pull` asks; only `next`
    passes one): a record of `session` whose every collision is with the one live pull `other`
    is offered at its rank with the Split when `narrow` gives one (Offer.split, the NARROWS line)
    and takes only the scope it keeps (Split.keep), which `folded(rec, scopes=)` then judges;
    without one it is passed over as today. Nothing is written."""
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
        occupied.extend(scopes)
        occupied_by.extend(rec for _m in scopes)
        taker = (('%s (offered above)' % rid, None) if own(rec) else
                 ('%s (session %s)' % (rid, rec.get('session') or 'none'),
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
    closed = closed_reader(snap, history)
    ranks = ranked(snap)
    out = []
    for rec in held:
        for member, why, reason in cmod.releasable(rec, statuses.get, closed):
            out.append((rec, releasable_line(rec, rank_label(rec, ranks), member, why, reason)))
    return out, note


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


def narrowings(passed, holding, changes, unknown):
    """`next`'s NARROW lines: one for each record passed over on a scope collision with a live
    pull (in `holding`) where either member is broad (`is_broad`). `changes` and `unknown` are
    plan `_live_changes`'s: `{branch: [(label, paths)]}`, the paths each live branch changed
    since its merge base with the code main, and `{branch: error}`. A broad taken member gets the
    `set --scope` that narrows the live pull to the files its branch changed there, unless those
    files overlap this record too (then it waits for the landing); a broad member of this record
    only gets the overlap named."""
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
        paths = sorted(set(q for _label, qs in changes[branch] for q in qs))
        there = _under(theirs, paths)
        if not there:
            out.append('%s, which changed nothing under %s yet' % (head, theirs))
            continue
        head = '%s, which changed %d file(s) there: %s' % (head, len(there), _named(there))
        overlap = _under(mine, there)
        if is_broad(theirs) and not overlap:
            keep = [m for m in other.all('scope') if m != theirs]
            keep += [q for q in there if q not in keep]
            out.append('%s -> `set %s %s`' % (
                head, other.id, ' '.join('--scope %s' % m for m in keep)))
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


class Split(object):
    """A narrowing `next` shows and does not write. `keep` is the candidate's scope after it,
    `other_keep` the live pull `other`'s. `c_changes` and `l_changes` are `[(member, dropped,
    kept)]` for each member of either side it changes; `plan` says `other` has a `## Files`
    section (without one, only the candidate is narrowed)."""

    def __init__(self, other, keep, other_keep, c_changes, l_changes, plan):
        self.other, self.keep, self.other_keep = other, keep, other_keep
        self.c_changes, self.l_changes, self.plan = c_changes, l_changes, plan


def split(c_scopes, other, protected, tree, plan):
    """`(Split, None)`, or `(None, why)` when the candidate whose scope is `c_scopes` cannot be
    narrowed clear of the live pull `other`. `protected` is T, `other`'s protected set (the paths
    its branch changed, its worktree's dirty paths and the paths its `## Files` names); `tree` is
    the tracked paths at the code main; `plan` says `other` has a `## Files` section.
    With a plan, each colliding member of the candidate loses T (scope.complement), and each
    colliding member of `other` loses the tracked part of the overlap not in T, keeping every
    path of T under it (a file it adds that the tree has not got included). Without one, only
    the candidate is narrowed, and only off a whole member of `other` lying strictly inside a
    broad member of its own. Fails closed: complement gives None, either side is left with no
    scope, a member is not a legal scope, a comparison is undecidable, or what the candidate
    keeps still touches T or `other`'s kept scope."""
    try:
        return _split(c_scopes, other, protected, tree, plan)
    except smod.UndecidableScope as exc:
        return None, 'undecidable: %s' % exc


def _split(c_scopes, other, protected, tree, plan):
    base, rel, within = smod.member_base, smod.paths_related, smod.path_within
    l_scopes = other.all('scope')
    pairs = smod.colliding_pairs(c_scopes, l_scopes)
    if not pairs:
        return None, 'no collision with %s' % other.id
    prot = sorted(set(protected))
    pset = set(prot)
    tracked = list(tree)
    c_kept = []
    for c in c_scopes:
        ls = [l for cc, l in pairs if cc == c]
        if not ls:
            c_kept.append((c, None))
            continue
        removed = [t for t in prot if rel(base(c), t)]
        if not plan:
            for l in ls:
                if not smod.strictly_inside(l, c):
                    return None, ('%s of %s is not inside %s, and %s has no `## Files`: only a '
                                  'broad candidate is narrowed' % (l, other.id, c, other.id))
                removed += [p for p in tracked if within(base(l), p)]
        kept = smod.complement(c, sorted(set(removed)), tracked)
        if kept is None:
            return None, 'complement cannot narrow %s' % c
        c_kept.append((c, kept))
    l_kept = []
    for l in l_scopes:
        cs = [c for c, ll in pairs if ll == l]
        if not cs or not plan:
            l_kept.append((l, None))
            continue
        removed = []
        for c in cs:
            region = base(smod.deeper_member(c, l))
            removed += [p for p in tracked if within(region, p) and p not in pset]
        kept = smod.complement(l, sorted(set(removed)), tracked) if removed else [l]
        if kept is None:
            return None, 'complement cannot narrow %s of %s' % (l, other.id)
        extra = [t for t in prot if within(base(l), t)
                 and not any(rel(base(k), t) for k in kept)]
        l_kept.append((l, sorted(set(kept + extra))))

    def flat(pairs_kept):
        out = []
        for m, kept in pairs_kept:
            for k in ([m] if kept is None else kept):
                if k not in out:
                    out.append(k)
        return out
    keep, other_keep = flat(c_kept), flat(l_kept)
    if not keep:
        return None, 'nothing of the candidate\'s scope is left'
    if not other_keep:
        return None, 'nothing of %s\'s scope is left' % other.id
    for m in keep + other_keep:
        err = smod.scope_member_error(m)
        if err is not None:
            return None, '`%s` is not a scope: %s' % (m, err)
    if smod.colliding_pairs(keep, other_keep):
        return None, 'what is kept still collides with %s' % other.id
    if any(rel(base(k), t) for k in keep for t in prot):
        return None, 'what is kept still touches the protected set of %s' % other.id
    c_changes = []
    for c, kept in c_kept:
        if kept is None or kept == [c]:
            continue
        dropped = [m for m in other_keep if rel(base(m), base(c))]
        dropped += [t for t in prot if rel(base(c), t) and t not in dropped
                    and not any(rel(base(m), t) for m in dropped)]
        c_changes.append((c, sorted(dropped), kept))
    l_changes = []
    for l, kept in l_kept:
        if kept is None or kept == [l]:
            continue
        l_changes.append((l, sorted(m for m in keep if rel(base(m), base(l))), kept))
    return Split(other, keep, other_keep, c_changes, l_changes, plan), None


def narrows_line(offer):
    """`next`'s NARROWS line for an offer made by narrowing (Offer.split): what each side drops
    and keeps. Nothing is written, and `pull` does not narrow yet: it passes the record over
    as before."""
    s = offer.split

    def side(rid, changes, note=''):
        if not changes:
            return '%s keeps its scope%s' % (rid, note)
        return '%s %s' % (rid, '; '.join('drops %s from %s (keeps %s)' % (
            _named(d) or 'nothing', m, _named(k) or 'nothing') for m, d, k in changes))
    return 'NARROWS %s (rank %s) with %s on %s: %s; %s' % (
        offer.rec.id, offer.rank, s.other.id, s.other.get('pulled_by') or '?',
        side(offer.rec.id, s.c_changes),
        side(s.other.id, s.l_changes, '' if s.plan else ' (no ## Files)'))


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
