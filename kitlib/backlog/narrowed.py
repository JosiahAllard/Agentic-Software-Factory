"""commit.py's narrowed-record check (docs/commit.md "A narrowed record").

A narrowing writes a `narrowed: <original member> <other id> <other branch>` line on each of two
colliding records (kitlib/backlog/record.py): `pull` of a record `next` offers by narrowing
(plan_claim.plan_pull), or a lead's `set --narrowed`. A commit on the branch holding one of them may
not record a path inside an original member that its record no longer covers while the other record
is still pulled: that part was given to the other branch. Both sides of a rename are named paths of
the commit, so each is checked; a refused side names the other only for a rename with its content
unchanged (the same blob: rename_pairs), and an edited move names only the refused side. One hit
refuses the whole commit. Paths and members compare as the landing scope check compares them
(scope.covers), a member of the store shared by repositories read without its area prefix
(scope.in_repo).

  check(root, paths)          `(refusal lines, warnings)` for the commit in `root` naming `paths`
  hits(records, branch, ...)  the same over records already read (pure)
  merge_lines(values, new)    a record's `narrowed:` lines with `new` merged in (pure)
  restore_set(rid, ...)       the `set` that restores a record's original members (pure)
  restored_scope(members, originals)  the scope that gives a record its original members back
"""
import os

from kitlib.git import cmd
from kitlib.git import refs
from kitlib.git import repo as gitrepo

from . import record as rmod
from . import scope as scmod
from . import store as smod
from .errors import Failure

# The fixed refusal (docs/commit.md "A narrowed record"); its only Instead is the block.
# `named` is the path, with the other side of a rename after it (RENAMED_FROM / RENAMED_TO).
REFUSAL = ('REFUSED: needs %(named)s, held by %(other)s on %(branch)s (narrowed for %(rid)s). '
           'Stop and report "needs %(path)s, held by %(other)s". Instead: backlog.py block '
           '%(rid)s --on item:%(other)s --reason "needs %(path)s"')
RENAMED_FROM = '%s (rename of %s)'
RENAMED_TO = '%s (renamed to %s)'
MALFORMED = ('REFUSED: %s carries a narrowed: line this check cannot read (%s), so the part of '
             'its scope it gave up is unknown. Stop and report it. Instead: a lead corrects the '
             'line with backlog.py set %s')
UNREAD = ('WARNING: the narrowed-record check could not read the backlog store (%s); the commit '
          'went ahead unchecked, and the landing scope check reads the store again')


def held(records, branch):
    """The readable records `branch` holds (pulled by it) that carry a `narrowed:` line."""
    return [r for r in records if r.readable and branch and r.get('state') == 'pulled'
            and r.get('pulled_by') == branch and r.all('narrowed')]


def hits(records, branch, paths, rules, shared=False, tops=(), renames=None):
    """`(refusal lines, warnings)`: for each record `branch` holds that carries `narrowed:`
    lines, each of `paths` inside an original member and outside the record's scope while that
    line's other record is still pulled (REFUSAL); a malformed line refuses (MALFORMED). `shared`:
    the records are the PM data store's, whose members carry their area unless it names one of
    `tops`, the repository's top-level entries casefolded (scope.in_repo). `renames`: path ->
    `(other side, RENAMED_FROM or RENAMED_TO)`, so a refused side names the other. A record
    whose other side is no longer pulled is let through: the landing scope check refuses the
    path until the scope is restored. Unreadable records are a warning: one of them may be the
    branch's own."""
    readable = {r.stem: r for r in records if r.readable}
    bad = sorted(r.stem for r in records if not r.readable)
    warnings = [UNREAD % ('%d record(s) unreadable: %s' % (len(bad), ', '.join(bad[:5])))] if bad \
        else []
    renames = renames or {}
    tops = set(tops)
    out = []
    for rec in sorted(held(records, branch), key=lambda r: r.stem):
        rid = rec.stem
        lines, errors = rmod.narrowed(rec, rules)
        if errors:
            out.extend(MALFORMED % (rid, err, rid) for err in errors)
            continue
        area = rec.get('area') if shared else None

        def here(members):
            return scmod.in_repo(members, area, tops)
        scopes = here(rec.all('scope'))
        for p in paths:
            if scmod.covers(scopes, p):
                continue
            for member, other, other_branch in lines:
                o = readable.get(other)
                if not scmod.covers(here([member]), p) or o is None \
                        or o.get('state') != 'pulled':
                    continue
                side, form = renames.get(p, (None, None))
                out.append(REFUSAL % dict(path=p, named=form % (p, side) if side else p,
                                          other=other, rid=rid,
                                          branch=o.get('pulled_by') or other_branch))
                break
    return out, warnings


def read(root):
    """`(top, branch, records, rules, shared)`: the checkout `root` is in, and the store
    `backlog.py -C <root>` opens (store.resolve_store: the PM data worktree's live store for a
    linked worktree, `shared` then True; else the configured one), read without a fetch. Raises
    when it cannot be read, and when the PM data worktree cannot be found or read: its own store
    is then not the one backlog.py would read for it."""
    code_top, why = gitrepo.toplevel(root)
    if not code_top:
        raise Failure(why)
    store_root, cfg, rules, branch, why = smod.resolve_store(code_top)
    if why:
        raise Failure('the PM data worktree\'s store cannot be opened: %s' % why)
    shared = branch is not None
    if not shared:
        branch, why = refs.current_branch(code_top)
        if branch is None:
            raise Failure('git cannot name the checked-out branch: %s' % why)
    snap = smod.open_backend(store_root, cfg, fetch=False).load(rules)
    return code_top, branch, snap.records, rules, shared


def top_entries(top, paths):
    """The repository's top-level entries, casefolded: HEAD's, and each path's first component
    (a commit may add one). An unborn HEAD gives the paths' alone."""
    out, _why = cmd.run_text(['ls-tree', '--name-only', '-z', 'HEAD'], top, strip=False)
    names = [n for n in (out or '').split('\0') if n] + [p.split('/')[0] for p in paths]
    return set(n.casefold() for n in names)


def rename_pairs(top, paths):
    """path -> `(other side, form)` for each pair of `paths` that is a rename with its content
    unchanged: one in HEAD and gone from the work tree, one not in HEAD whose file has that
    blob. A rename with its content changed is not paired (each side is still checked)."""
    out, _why = cmd.run_text(['ls-tree', '-z', 'HEAD', '--'] + list(paths), top, strip=False)
    in_head = {}
    for entry in (out or '').split('\0'):
        meta, _, path = entry.partition('\t')
        if path and len(meta.split()) == 3:
            in_head[path] = meta.split()[2]
    gone = {in_head[p]: p for p in paths
            if p in in_head and not os.path.lexists(os.path.join(top, p))}
    pairs = {}
    for p in paths:
        if p in in_head or not gone or not os.path.isfile(os.path.join(top, p)):
            continue
        sha, _why = cmd.run_text(['hash-object', '--', p], top)
        old = gone.get((sha or '').strip())
        if old and old not in pairs:
            pairs[p] = (old, RENAMED_FROM)
            pairs[old] = (p, RENAMED_TO)
    return pairs


def check(root, paths):
    """`(refusal lines, warnings)` for a commit in `root` that records `paths`. Fails open: a
    store that cannot be read is a warning (UNREAD), never a refusal - every commit in every
    repository goes through here."""
    if not paths:
        return [], []
    try:
        top, branch, records, rules, shared = read(root)
        mine = held(records, branch)
        tops = top_entries(top, paths) if shared and any(r.get('area') for r in mine) else ()
    except Exception as e:      # noqa: BLE001 - fail open, said in the warning
        return [], [UNREAD % ('%s: %s' % (type(e).__name__, e) if str(e) else type(e).__name__)]
    refusal, warnings = hits(records, branch, paths, rules, shared, tops)
    if refusal and len(paths) > 1:
        try:
            renames = rename_pairs(top, paths)
        except OSError:         # the refusal stands; only the rename's other side goes unnamed
            renames = {}
        if renames:
            refusal, warnings = hits(records, branch, paths, rules, shared, tops, renames)
    return refusal, warnings


def merge_lines(values, new):
    """The `narrowed:` values of a record narrowed again: `values` (its current lines) with each
    `(member, other id, other branch)` of `new` written in, one line per (member, other id), a
    new line replacing the old one for the same pair in its place. A line that cannot be read is
    kept as written, for check's refusal to name."""
    out, at = [], {}
    for v in values:
        parts = rmod.split_narrowed(v)
        key = tuple(parts[:2]) if parts else None
        if key is not None and key in at:
            continue
        if key is not None:
            at[key] = len(out)
        out.append(v)
    for member, other, branch in new:
        line = '%s %s %s' % (member, other, branch)
        key = (member, other)
        if key in at:
            out[at[key]] = line
        else:
            at[key] = len(out)
            out.append(line)
    return out


def restore_set(rid, members, values, rules, done=None):
    """`(originals, other ids, set args)` of the `set` that gives record `rid` (scope `members`,
    `narrowed:` values `values`) back the original member of each line `done(line)` picks (every
    well-formed line when `done` is None): `--scope` of restored_scope, then each other value
    kept as `--narrowed`, or `--unset narrowed` when none is left. Lines are read with
    record.narrowed_line; a malformed one is kept as written. None when no line is picked. The
    one form of `next`'s RESTORE line (offer._restore) and the landing scope check's restore
    clause (kitlib/gate/land.py); the `set` of `narrowed` is a lead's (set-other)."""
    originals, others, rest = [], [], []
    for v in values:
        line, _err = rmod.narrowed_line(v, rules)
        if line is None or (done is not None and not done(line)):
            rest.append(v)
            continue
        if line[0] not in originals:
            originals.append(line[0])
        if line[1] not in others:
            others.append(line[1])
    if not originals:
        return None
    args = ['set', rid] + ['--scope "%s"' % m for m in restored_scope(members, originals)]
    args += ['--narrowed "%s"' % v for v in rest] if rest else ['--unset narrowed']
    return originals, others, args


def restored_scope(members, originals):
    """The scope that gives a narrowed record its `originals` back (the members of the
    `narrowed:` lines restored): each original, then each current member of `members` none of
    them holds (equal or strictly inside; one that cannot be compared is kept). `none` is
    dropped. Shared by `next`'s RESTORE line (offer.restore_lines) and the landing scope check's
    restore clause (kitlib/gate/land.py)."""
    def held(m):
        try:
            return any(m == o or scmod.strictly_inside(m, o) for o in originals)
        except scmod.UndecidableScope:
            return False
    keep = []
    for m in list(originals) + [m for m in members if m != scmod.NONE and not held(m)]:
        if m not in keep:
            keep.append(m)
    return keep
