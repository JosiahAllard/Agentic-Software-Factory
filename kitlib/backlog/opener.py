"""Open the configured backlog store from a worktree, and map a write verb to its plan (moved
from tools/backlog.py `main`, `planner` and `do_retarget_dry`, behaviour unchanged;
docs/backlog.md).

  open_repo(root)   -> (top, cfg, rules); Failure when `root` is no repository or its `.kit.json`
                       cannot be read
  open_store(root, fetch=True, session=None, branch=None, *, probes) -> plan.Context over the
                       store, its `liveness` and `quiet` the caller's `probes` bound to the
                       store's repository (NO_PROBES: none), its `cap` repo_cap's; from a linked
                       worktree naming no store, the PM data worktree's live store
                       (store_live.worktree_live_store)
  follow_up_states(root, ids) -> ({id: (state, open)}, why) from the live store (no fetch)
  repo_cap(top)     -> the main checkout's `.kit.json` backlog.cap `next` and `pull` share
  repo_resources(top) -> the main checkout's `.kit.json` backlog.resources, {name: capacity}
  repo_shared_paths(top) -> the main checkout's `.kit.json` backlog.shared_paths, [(path, why)]
  planner(args, ctx) -> the plan `Store.write` runs for `args.verb` (one of WRITES, `retarget` or
                       `uncite`); Usage for any other verb, raised before a plan exists
  retarget_dry(args, ctx) -> the Change `retarget` would write, checked as a write would be;
                       nothing is published
  uncite_dry(ctx)    -> the same for `uncite` (plan_citations.plan_uncite)

`planner` and `retarget_dry` take an object with tools/backlog.py's argument names (`verb`, `id`,
`f_<field>`, `old`, `new`...).
"""
import os

import kitlib.config as kconfig
from kitlib.config.types import is_note
from kitlib.git import repo as gitrepo

from . import plan as pmod
from . import plan_citations, plan_claim, plan_edit, plan_end, plan_hold
from . import policy
from . import pull_branch as pullmod
from . import record as rmod
from . import retarget as retmod
from . import scope as bscope
from . import store as smod
from . import store_live, store_open, store_read
from .errors import Failure, Refusal, Usage

# `set --<field>` for every field; plan_set refuses the ones a verb writes (plan_edit.WRITTEN_BY).
FIELD_FLAGS = list(rmod.FIELDS)
WRITES = ('add', 'set', 'block', 'unblock', 'pull', 'release', 'close', 'retire', 'delete',
          'set-status', 'unpark', 'clear-review')
# open_store's `probes` for a caller whose plans never stop another branch's pull (a read, an
# add, a set, a pull, a stop as the pull's own branch): the context gets no liveness and no quiet
# probe, so such a stop would be refused as live.
NO_PROBES = (None, None)


def open_repo(root):
    return store_open.open_repo(root)


def open_store(root, fetch=True, session=None, branch=None, *, probes):
    """The plan.Context over the store `root`'s checkout opens. A linked worktree whose
    `.kit.json` names no store opens the live store in its repository's PM data worktree, acting
    as its own checked-out branch unless `branch` (`--as`) names one
    (store_live.worktree_live_store): `-C <worktree>` then reads and writes what
    `-C <pm data> --as <its branch>` does.
    `probes` (required; the backlog has no default) is `(liveness, quiet)`, called as
    `liveness(top, branch)` and `quiet(top, branch, main=<code main commit>)` on the store's
    repository `top`: the worktree package's quiet.PROBES, or NO_PROBES; a None member is no
    probe."""
    live, quiet = probes
    # `why` (the PM data worktree unread) is the write's to refuse (default_store_refusal).
    # One worktree list for the run: the store lookup, the code config and the cap and resources
    # read lazily after all ask the one repository for it.
    listing = gitrepo.Worktrees()
    top, cfg, rules, own, _why = store_open.resolve_store(root, worktrees=listing)
    branch = branch or own
    section = cfg.section('backlog')
    backend = store_open.open_backend(top, cfg, fetch=fetch)
    store = smod.Store(backend, rules, session=session)
    code_main, code_remote = section['code_main'], cfg.remote
    code = pullmod.code_config(top, worktrees=listing)
    if code is not None:
        # The PM data worktree: its own main and remote are the data's; the code lands on the
        # main checkout's (docs/backlog.md "The fold-in guard").
        code_main, code_remote = code_main or kconfig.work_base(code), code.remote
    ctx = pmod.Context(store, top, remote=cfg.remote, main_branch=cfg.main_branch,
                       branch=branch, code_main=code_main or kconfig.work_base(cfg),
                       code_remote=code_remote,
                       liveness=None if live is None else (lambda name: live(top, name)),
                       quiet=None if quiet is None else (
                           lambda name, main: quiet(top, name, main=main)),
                       cap=lambda: repo_cap(top, worktrees=listing),
                       resources=lambda: repo_resources(top, worktrees=listing),
                       shared=lambda: repo_shared_paths(top, worktrees=listing))
    return ctx


def follow_up_states(root, ids):
    """`({id: (state, open)}, None)` for each of `ids` in the live store of `root`'s records
    (store_live.live_store_root: from a main checkout naming no store, its PM data worktree's), as
    last fetched: `state` the record's `state:`, None when the store holds none of that id (a closed
    or retired one), `unreadable` when it cannot be parsed; `open` True when the state is one of
    record.STATES. `(None, why)` when the live store cannot be found or opened. A read, so it opens
    the store with NO_PROBES."""
    try:
        path, why = store_live.live_store_root(root)
        if why:
            return None, why
        snap = open_store(path, fetch=False, probes=NO_PROBES).store.load()
    except (Failure, Refusal, Usage, kconfig.ConfigError, OSError) as e:
        return None, str(e)
    out = {}
    for rid in ids:
        rec = snap.get(rid)
        state = None if rec is None else rec.get('state') if rec.readable else 'unreadable'
        out[rid] = (state, state in rmod.STATES)
    return out, None


def repo_cap(top, *, worktrees=None):
    """The cap `next` offers up to and `pull` checks against (plan.Context.cap, read lazily, so
    only an offer reads it): `.kit.json` "backlog".cap of the repository's main checkout, its
    first worktree (as pull_branch._main_checkout finds it), whatever checkout `top` is. A worker
    worktree cut before the owner set the cap, or the PM data worktree's data branch, would
    otherwise read a value the main checkout's `next` does not. A bare first worktree: `top`'s
    own. Main checkout config that cannot be read, or worktrees git cannot list, is a Failure
    (ERROR), never a silent default; the schema refuses a value that is not a whole number >= 1
    when the file loads (kitlib/config/schema.py), and checked_cap refuses one that reaches here
    otherwise."""
    section, main = _main_backlog(top, 'the backlog cap', worktrees=worktrees)
    return checked_cap(section['cap'], main)


def repo_resources(top, *, worktrees=None):
    """`{name: capacity}` from `.kit.json` "backlog".resources of the main checkout, read as
    repo_cap reads the cap and for the same reason (plan.Context.resources). No key: {}."""
    section, _main = _main_backlog(top, 'the backlog resources', worktrees=worktrees)
    return {k: v for k, v in section['resources'].items() if not is_note(k)}


def repo_shared_paths(top, *, worktrees=None):
    """`[(path, why)]` for each `.kit.json` "backlog".shared_paths entry of the main checkout,
    read as repo_cap reads the cap (plan.Context.shared_paths): `why` None for a literal file,
    else why the entry is no shared path (a glob, a bad path: scope.shared_path_error; a folder
    in the main checkout's tree). An entry with a `why` exempts nothing, and `check` reports it.
    No key: []."""
    section, main = _main_backlog(top, 'the backlog shared paths', worktrees=worktrees)
    out = []
    for p in section['shared_paths']:
        why = bscope.shared_path_error(p)
        if why is None and os.path.isdir(os.path.join(main, p)):
            why = '`%s` is a folder: a shared path is one literal file' % p
        out.append((p, why))
    return out


def _main_backlog(top, what, *, worktrees=None):
    """`(the "backlog" section of the main checkout's .kit.json, that checkout)`; Failure naming
    `what` when it cannot be read. `worktrees`: gitrepo.Worktrees, when the caller has the list."""
    wts, why = (worktrees or gitrepo.worktrees)(top)
    if not wts:
        raise Failure('cannot read %s: git cannot list the worktrees of %s, so its main '
                      'checkout\'s .kit.json is unknown (%s)' % (what, top, why))
    main = top if wts[0].bare else wts[0].path
    try:
        cfg = kconfig.load(main)
    except (kconfig.ConfigError, OSError) as exc:
        raise Failure('cannot read %s from the main checkout %s: %s' % (what, main, exc))
    return cfg.section('backlog'), main


def checked_cap(cap, where):
    """`cap` when it is a whole number >= 1, else a Refusal naming `where`."""
    if type(cap) is not int or cap < 1:
        raise Refusal('.kit.json "backlog".cap of %s is %r: it must be a whole number >= 1'
                      % (where, cap))
    return cap


def set_changes(args):
    return [(key, getattr(args, 'f_' + key)) for key in FIELD_FLAGS
            if getattr(args, 'f_' + key) is not None]


def planner(args, ctx):
    v = args.verb
    if v == 'add':
        body = plan_edit.read_body_file(args.body_file) if args.body_file else None
        return lambda s: plan_edit.plan_add(s, ctx, args.id, args.summary, args.scope, args.effort,
                                            args.ref, args.reopen, session=ctx.session,
                                            found_on=args.found_on, why_not_folded=args.why_not_folded,
                                            body=body, summary_max=plan_edit.SUMMARY_MAX, uses=args.uses)
    if v == 'set':
        body = plan_edit.read_body_file(args.body_file) if args.body_file else None
        changes = set_changes(args)
        # A widening into a live pull is the plan's to find; the role decides whether it may.
        may_collide = policy.may(plan_edit.WIDEN_ROW)
        return lambda s: plan_edit.plan_set(s, ctx, args.id, changes, args.unset, body=body,
                                            role_checked=True, may_collide=may_collide)
    if v == 'block':
        return lambda s: plan_hold.plan_block(s, ctx, args.id, args.on, args.owner_ask,
                                              reason=args.reason, until=args.until,
                                              role_checked=True)
    if v == 'retarget':
        return lambda s: retmod.plan_retarget(s, ctx, args.old, args.new)
    if v == 'uncite':
        return lambda s: plan_citations.plan_uncite(s, ctx)
    if v == 'unblock':
        return lambda s: plan_hold.plan_unblock(s, ctx, args.id, args.on, args.reason,
                                                role_checked=True)
    if v == 'pull':
        may_collide = policy.may('pull-collision')
        batch = policy.cli_role() == policy.TOPIC
        return lambda s: pullmod.plan_pull(s, ctx, args.id, args.reason, may_collide,
                                           args.blocker, batch=batch)
    if v == 'release':
        return lambda s: plan_claim.plan_release(s, ctx, args.id, args.reason)
    if v == 'close':
        return lambda s: plan_end.plan_close(s, ctx, args.id, args.reason, role_checked=True)
    if v == 'set-status':
        return lambda s: plan_hold.plan_set_status(s, ctx, args.id, args.status, args.q)
    if v == 'unpark':
        return lambda s: plan_hold.plan_unpark(s, ctx, args.id)
    if v == 'retire':
        return lambda s: plan_end.plan_retire(s, ctx, args.id, args.why, role_checked=True)
    if v == 'delete':
        return lambda s: plan_end.plan_delete(s, ctx, args.id, args.why, role_checked=True)
    if v == 'clear-review':
        return lambda s: plan_edit.plan_clear_review(s, ctx, args.id, args.reason)
    # The caller's mistake, so Usage (errors.py); raised here, before any plan could run.
    raise Usage('`%s` is not a write verb: `planner` plans %s'
                % (v, ', '.join(WRITES + ('retarget', 'uncite'))))


def retarget_dry(args, ctx):
    """`retarget` without `--apply`: the change it would write, planned against a fresh read and
    checked as a write would be; nothing is published."""
    return _dry(ctx, lambda snap: retmod.plan_retarget(snap, ctx, args.old, args.new))


def uncite_dry(ctx):
    """`uncite` without `--apply`: the change it would write (its NEEDS HAND EDIT list included),
    planned and checked as `retarget_dry`'s is; nothing is published."""
    return _dry(ctx, lambda snap: plan_citations.plan_uncite(snap, ctx))


def _dry(ctx, plan):
    snap = ctx.store.load()
    store_read.require_readable(snap)
    with smod.plan_notes() as notes, smod.notes_on_refusal(notes):
        change = plan(snap)
    change = smod.with_plan_notes(change, notes)
    if change.writes:
        with smod.notes_on_refusal(notes):
            smod.check_change(snap, change)
    return change
