"""`landable`: may a branch land without leaving a dead pull?

Landing deletes the branch, so a record still `state: pulled` with `pulled_by: <branch>` would
name a branch that no longer exists, unless the landing itself ends that pull. It does in a store
it closes records in (at_landing.close_landed): a `ref` or `branch` store `.kit.json` names, and
the PM data branch's folder store, read here in its own worktree. There a pull the branch holds is
LANDABLE, and the verdict says what the landing will do to each record, from the `Closes:` and
`Part-of:` lines of the branch's commits above `base` (the code main, as the landing reads them):
close it, or release it when the branch has a Part-of line and no Closes line names it, or when
it is a live-proof record (`requires: live-run`) no `Live-pass:` line of those commits cites a
passing live run for (at_landing.is_live_proof, live_pass_trailers). A folder
store in the code repository (its records travel with the branch, so a landing closes none) and
the implicit default store (a landing only reads it) still say NOT LANDABLE for a held pull; with
a folder store the records are read from the branch's own tip. Reads git only, writes nothing.
"""
import collections

from kitlib.git import refs

from . import at_landing
from .errors import Failure
from .plan import branch_name_error
from .store import MainBackend, records_in_tree

LANDABLE = 'LANDABLE'
NOT_LANDABLE = 'NOT LANDABLE'
CANNOT_TELL = 'CANNOT TELL'
EXIT = {LANDABLE: 0, CANNOT_TELL: 1, NOT_LANDABLE: 2}

# `held`: the ids the branch holds pulled; `closes` and `releases`: what its landing does to them,
# in a store that landing closes records in; `partial`: the branch has a Part-of line;
# `unproven`: the live-proof ids among `releases` that no Live-pass line cites.
Verdict = collections.namedtuple('Verdict',
                                 'branch verdict held detail closes releases partial unproven')
Verdict.__new__.__defaults__ = ((), (), False, ())


def _closing_store(backend):
    """`(closes, None)`: whether a landing closes the pulls `backend` holds (close_landed); or
    `(None, why)` when that cannot be told."""
    if isinstance(backend, MainBackend):
        try:
            return backend.commit_branch() is not None, None    # the PM data worktree's store
        except Failure as exc:
            return None, str(exc)
    return not getattr(backend, 'implicit', False), None


def _landing_acts(root, tip, base, held, proof=()):
    """`((closes, releases, partial, unproven), None)` for the ids in `held`, as close_landed
    decides them from the branch's Closes, Part-of and Live-pass lines in `tip --not base`: a
    live-proof id (in `proof`) it would close and no Live-pass line cites is released instead
    (`unproven`); or `(None, why)`."""
    named, why = at_landing.closes_trailers(root, tip, base)
    found = passed = None
    if named is not None:
        found, why = at_landing.part_trailers(root, tip, base)
    if found is not None:
        passed, why = at_landing.live_pass_trailers(root, tip, base)
    if passed is None:
        return None, ('cannot read the Closes, Part-of and Live-pass lines of its commits (git log '
                      '%s --not %s): %s' % (tip[:10], base, ' '.join(str(why).split())))
    partial = found[0]
    closes = [r for r in held if r in named or not partial]
    unproven = [r for r in closes if r in proof and r not in passed]
    return (tuple(sorted(r for r in closes if r not in unproven)),
            tuple(sorted(r for r in held if r not in closes or r in unproven)), partial,
            tuple(sorted(unproven))), None


def code_base(ctx):
    """The code main a landing of a branch merges onto, as plan.Context `ctx` names it (the main
    checkout's in the PM data worktree): `<code_remote>/<code_main>` as last fetched, else the
    local branch (refs.default_base)."""
    return refs.default_base(ctx.root, ctx.code_remote, ctx.code_main)[0]


def check(backend, snap, root, branches, base=None):
    """One Verdict per branch. `base`: the code main the landing merges onto (refs.default_base);
    None reads no Closes or Part-of line, so a held pull a landing would end is CANNOT TELL."""
    out = []
    closing, closing_why = _closing_store(backend)
    for branch in branches:
        bad = branch_name_error(branch, root)
        if bad:
            out.append(Verdict(branch, CANNOT_TELL, (), bad))
            continue
        # known=True: a git ended from outside is no answer, never "no such branch"
        # (`rev-parse --verify --quiet` said both as exit 1).
        tip, why = refs.resolve('refs/heads/%s' % branch, root, known=True)
        if tip is None:
            out.append(Verdict(branch, CANNOT_TELL, (), 'cannot read refs/heads/%s: %s'
                               % (branch, why)))
            continue
        if not tip:
            out.append(Verdict(branch, CANNOT_TELL, (), 'no local branch of that name'))
            continue
        if closing is None:
            out.append(Verdict(branch, CANNOT_TELL, (), closing_why))
            continue
        if isinstance(backend, MainBackend) and not closing:
            records, _, why = records_in_tree(root, tip, backend.rel)
            if records == []:
                why = 'no record under %s/ at its tip' % backend.rel
            if not records:
                out.append(Verdict(branch, CANNOT_TELL, (), why))
                continue
        else:
            records = snap.records
        unread = [x.stem for x in records if not x.readable]
        if unread:
            out.append(Verdict(branch, CANNOT_TELL, (), 'unreadable record(s): %s'
                               % ', '.join(unread)))
            continue
        held = [x.id for x in records if x.get('state') == 'pulled' and x.get('pulled_by') == branch]
        detail = '%d records read' % len(records)
        if not held or not closing:
            out.append(Verdict(branch, NOT_LANDABLE if held else LANDABLE, tuple(held), detail))
            continue
        if not base:
            out.append(Verdict(branch, CANNOT_TELL, tuple(held), 'no code main to read the Closes '
                               'and Part-of lines of its commits above'))
            continue
        proof = [x.id for x in records if at_landing.is_live_proof(x)]
        acts, why = _landing_acts(root, tip, base, held, proof)
        if acts is None:
            out.append(Verdict(branch, CANNOT_TELL, tuple(held), why))
            continue
        out.append(Verdict(branch, LANDABLE, tuple(held),
                           '%s; its commits above %s read' % (detail, base), *acts))
    return out


def lines(v, command):
    """The lines `backlog.py landable` prints for Verdict `v`; `command` is the backlog.py
    command line the NOT LANDABLE advice names."""
    if v.verdict == CANNOT_TELL:
        return ['CANNOT TELL %s: %s' % (v.branch, v.detail)]
    if v.verdict == NOT_LANDABLE:
        out = []
        for rid in v.held:
            out += ['NOT LANDABLE %s: %s is still pulled by it; landing deletes the branch and '
                    'would leave the pull dead.' % (v.branch, rid),
                    '  Done:     %s -C <worktree> close %s --reason "<what was done>"'
                    % (command, rid),
                    '  Not done: %s -C <worktree> --as %s release %s --reason "<why>"'
                    % (command, v.branch, rid)]
        return out
    if not v.held:
        return ['LANDABLE %s: no record is pulled by it (%s)' % (v.branch, v.detail)]
    out = ['LANDABLE %s: its landing ends each pull it holds (%s)' % (v.branch, v.detail)]
    for rid in v.closes:
        why = ('a Closes line names it' if v.partial else
               'a slice branch needs a `Part-of: %s` line' % rid)
        out.append('  closes %s, which it holds pulled (%s)' % (rid, why))
    for rid in v.releases:
        out.append('  releases %s, which it holds pulled (%s)' % (rid, (
            'a live-proof record: no `Live-pass: %s <run>` line in its commits cites a passing '
            'live run' % rid) if rid in v.unproven else
            'the branch has a Part-of line and no Closes line names it'))
    return out


def exit_code(verdicts):
    """0 when every branch is LANDABLE, 1 when one CANNOT TELL and none is NOT LANDABLE, else 2."""
    return max([EXIT[v.verdict] for v in verdicts] or [0])
