"""commit.py's narrowed-record check (docs/commit.md "A narrowed record").

A pull that narrows two colliding records apart writes a `narrowed: <original member> <other id>
<other branch>` line on each (kitlib/backlog/record.py). A commit on the branch holding one of
them may not record a path inside an original member that its record no longer covers while the
other record is still pulled: that part was given to the other branch. Both sides of a rename are
named paths of the commit, so each is checked; one hit refuses the whole commit.

  check(root, paths)          `(refusal lines, warnings)` for the commit in `root` naming `paths`
  hits(records, branch, ...)  the same over records already read (pure)
"""
from kitlib import config as kconfig
from kitlib.git import refs
from kitlib.git import repo as gitrepo

from . import record as rmod
from . import scope as scmod
from . import store as smod
from .errors import Failure

# The fixed refusal (KIT-NEXT-NARROW-AUTO "Fixed refusal text"); its only Instead is the block.
REFUSAL = ('REFUSED: needs %(path)s, held by %(other)s on %(branch)s (narrowed for %(rid)s). '
           'Stop and report "needs %(path)s, held by %(other)s". Instead: backlog.py block '
           '%(rid)s --on item:%(other)s --reason "needs %(path)s"')
MALFORMED = ('REFUSED: %s carries a narrowed: line this check cannot read (%s), so the part of '
             'its scope it gave up is unknown. Stop and report it. Instead: a lead corrects the '
             'line with backlog.py set %s')
UNREAD = ('WARNING: the narrowed-record check could not read the backlog store (%s); the commit '
          'went ahead unchecked, and the landing scope check reads the store again')


def covers(members, path):
    """A member in `members` covers the literal path `path` (casefolded components, one list a
    prefix of the other: the landing scope check's comparison). `none` covers nothing."""
    return any(m != scmod.NONE and scmod.paths_related(scmod.member_base(m), path)
               for m in members)


def hits(records, branch, paths, rules):
    """`(refusal lines, warnings)`: for each record `branch` holds (pulled by it) that carries
    `narrowed:` lines, each of `paths` inside an original member and outside the record's scope
    while that line's other record is still pulled (REFUSAL); a malformed line refuses (MALFORMED).
    A record whose other side is no longer pulled is let through: the landing scope check refuses
    the path until the scope is restored. Unreadable records are a warning: one of them may be
    the branch's own."""
    readable = {r.stem: r for r in records if r.readable}
    bad = sorted(r.stem for r in records if not r.readable)
    warnings = [UNREAD % ('%d record(s) unreadable: %s' % (len(bad), ', '.join(bad[:5])))] if bad \
        else []
    out = []
    for rid in sorted(readable):
        rec = readable[rid]
        if not branch or rec.get('state') != 'pulled' or rec.get('pulled_by') != branch:
            continue
        lines, errors = rmod.narrowed(rec, rules)
        if errors:
            out.extend(MALFORMED % (rid, err, rid) for err in errors)
            continue
        if not lines:
            continue
        scopes = rec.all('scope')
        for p in paths:
            if covers(scopes, p):
                continue
            for member, other, other_branch in lines:
                o = readable.get(other)
                if not covers([member], p) or o is None or o.get('state') != 'pulled':
                    continue
                out.append(REFUSAL % dict(path=p, other=other, rid=rid,
                                          branch=o.get('pulled_by') or other_branch))
                break
    return out, warnings


def read(root):
    """`(branch, records, rules)` of the store `backlog.py -C <root>` opens (the PM data
    worktree's live store for a linked worktree, store.worktree_live_store; else the configured
    one), read without a fetch; raises when it cannot be read."""
    top, why = gitrepo.toplevel(root)
    if not top:
        raise Failure(why)
    cfg = kconfig.load(top)
    live = smod.worktree_live_store(top, cfg)
    if live is not None:
        store_root, branch = live
        cfg = kconfig.load(store_root)
    else:
        store_root = top
        branch, why = refs.current_branch(top)
        if branch is None:
            raise Failure('git cannot name the checked-out branch: %s' % why)
    section = cfg.section('backlog')
    rules = rmod.Rules(section['id_grammar'], section['phases'])
    snap = smod.open_backend(store_root, cfg, fetch=False).load(rules)
    return branch, snap.records, rules


def check(root, paths):
    """`(refusal lines, warnings)` for a commit in `root` that records `paths`. Fails open: a
    store that cannot be read is a warning (UNREAD), never a refusal - every commit in every
    repository goes through here."""
    if not paths:
        return [], []
    try:
        branch, records, rules = read(root)
    except Exception as e:      # noqa: BLE001 - fail open, said in the warning
        return [], [UNREAD % ('%s: %s' % (type(e).__name__, e) if str(e) else type(e).__name__)]
    return hits(records, branch, paths, rules)
