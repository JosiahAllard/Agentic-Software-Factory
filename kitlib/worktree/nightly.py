"""The nightly sweep, `--sweep-merged [--apply]` (docs/worktree-remove.md): classify decides,
the sweep acts on its list. A dry run unless `apply`. Returns Outcome(code, lines).

Run-wide, each failing closed (exit 1, nothing removed), in this order: the landing lock free
(sweep.landing_hold: a landing moves the base, so it is read before anything is fetched); `git
fetch` of the base (sweep.fetch_base); the machine lock readable (sweep.machine_lock_entries).
Then `classify._classify` runs on the fetched base: every tree it calls A, E or O goes through
the remove path `--classify --apply` uses (classify.plan_tree, classify.apply_tree), each making
its own checks again, and B, C and L trees are never touched. Classify reads the landing lock and
the machine lock again before each tree; a landing lock taken mid-run stops it (deferred, exit 1).
A run-wide probe classify cannot read makes every tree L, and the sweep defers (exit 1).

The one exception, the owner's rule (docs/worktree-remove.md "Stale marks"): an UNLANDED worktree
a coordinator marked stale is ABANDONED, by `remove_abandon._abandon` (its archive tag first,
then the tree, then the branch), after sweep.sweep_skip's checks. A mark is void (the tree is
skipped, the mark kept) when the tree is not the one it names, its tip moved, or its last
activity is newer than the mark. The marks are read once, after classify: a marks file that
cannot be read counts as a failure (exit 1) and no marked tree is acted on. Under `apply`, the
abandon runs under the marks lock and only while the mark is still the one read; it is held to
the checked tip (`expect`), and the mark is dropped once the abandon is done. Last, a mark whose
branch is gone is dropped (`apply`), and a mark whose branch has no worktree is named for
`--unmark-stale`.

Exit 1 only on a failure, a deferral or a marks file that cannot be read; trees skipped and B or C
trees left give 0.
"""
import datetime
import os
import time

from kitlib.config import user

from . import classify, holders, marks, remove_abandon, remove_checks
from . import sweep as checks
from .holders import canon
from .remove_checks import OK, REFUSED


def sweep_log_name(ctx):
    """`<repo name>-worktree-sweep.log` (`"repo".name`, else the main checkout's folder name)."""
    return '%s-worktree-sweep.log' % ctx.s['name']


def sweep(top, apply=False, now=None, machine=checks.machine_lock_entries,
          idle_s=checks.SWEEP_IDLE_S, log_dir=None, cwd=None, procs=classify.process_cwds,
          environ=None):
    """`--sweep-merged [--apply]` on the repository `top` is in, which must be its main checkout.
    `now` (epoch seconds), `machine` (a sweep.machine_lock_entries-like callable), `procs` (a
    classify.process_cwds-like callable) and `environ` (the scratch roots' environment) are test
    seams. Under `apply` the lines and the exit status are appended to `<log_dir or shared
    temp>/<sweep_log_name>`."""
    ctx, refused = remove_checks.open_ctx(top, cwd)
    if refused:
        return refused
    if canon(top) != canon(ctx.main):
        ctx.say('refused   the sweep runs on the main checkout only (%s), not %s; nothing was'
                ' removed' % (ctx.main, top))
        return ctx.outcome(REFUSED)
    now = time.time() if now is None else now
    status = _sweep(ctx, apply, now, machine, idle_s, procs, environ)
    if apply:
        try:
            log = os.path.join(log_dir or user.shared_temp(), sweep_log_name(ctx))
            with open(log, 'a', encoding='utf-8') as fh:
                fh.write(''.join(line + '\n' for line in ctx.out) + 'exit      %d\n\n' % status)
        except (OSError, RuntimeError) as e:
            ctx.say('note      the sweep log could not be written: %s' % e)
    return ctx.outcome(status)


def _sweep(ctx, apply, now, machine, idle_s, procs, environ):
    s, main = ctx.s, ctx.main
    up = s['upstream']
    mode = 'apply' if apply else 'dry run (nothing is removed; --apply acts)'
    ctx.say('sweep     %s, %s UTC, dead bar %d h, repository %s' % (
        mode, datetime.datetime.fromtimestamp(now, datetime.timezone.utc).strftime('%Y-%m-%d %H:%M'),
        idle_s // 3600, main))
    why = checks.landing_hold(ctx)
    if why:
        ctx.say('deferred  %s: a landing is in progress or its lock is unreadable; nothing was'
                ' removed' % why)
        return REFUSED
    why = checks.fetch_base(ctx)
    if why:
        ctx.say('failed    the fetch of %s failed (%s); nothing was removed (fail closed)'
                % (up, why))
        return REFUSED
    held, why = machine()
    if held is None:
        ctx.say('deferred  the machine lock cannot be read (%s); nothing was removed' % why)
        return REFUSED
    if apply:
        def act(entry, row, base):
            return classify.apply_tree(ctx, entry, row, base, environ)
    else:
        def act(entry, row, base):
            plan = classify.plan_tree(ctx, entry, row, base, environ)
            if 'result' in plan:
                return dict(plan, result='would skip')
            return {'result': 'would remove', 'why': plan['how'], 'lines': []}
    got = classify._classify(ctx, now, machine, procs, idle_s, act=act, environ=environ,
                             fetched=True)
    data = got.data
    if 'error' in data:
        return REFUSED
    counts = {'removed': 0, 'would remove': 0, 'skipped': 0, 'failed': 0, 'abandoned': 0,
              'would abandon': 0}
    applied = data.get('applied', {})
    for key in ('removed', 'would remove', 'failed'):
        counts[key] += applied.get(key, 0)
    counts['skipped'] += applied.get('skipped', 0) + applied.get('would skip', 0)
    deferred = data.get('stopped') or data.get('unreadable')
    if deferred:
        ctx.say('deferred  %s: no marked worktree is abandoned' % deferred)
    else:
        deferred = _stale_pass(ctx, apply, now, held, idle_s, data['base'], counts)
    ctx.say('summary   %s%s' % (', '.join('%s %d' % kv for kv in counts.items()),
                                ', deferred' if deferred else ''))
    return REFUSED if counts['failed'] or deferred else OK


def _stale_pass(ctx, apply, now, held, idle_s, base, counts):
    """Abandon each UNLANDED tree whose stale mark holds (the owner's rule), then end_marks; adds
    to `counts`. Landed trees are classify's: never acted on here. Returns why it deferred (a
    landing lock taken during it), else None."""
    try:
        listing = remove_checks.worktree_listing(ctx.main)
    except remove_checks.GitReadError as e:
        ctx.say('failed    %s; no marked worktree is abandoned' % e)
        counts['failed'] += 1
        return None
    got, why = remove_checks.ancestry(ctx, base)
    if got is None:
        ctx.say('failed    git rev-list --parents %s: %s; no marked worktree is abandoned'
                % (base, why))
        counts['failed'] += 1
        return None
    ancestors = got[1]
    entries = remove_checks.worktree_entries(listing)
    where, marked = read_marks(ctx)
    if marked is None:
        counts['failed'] += 1
        return None
    for entry in entries:
        mark = marks.find(marked, entry['branch']) if entry['branch'] else None
        if mark is None or not entry['head'] or entry['head'] in ancestors:
            continue
        path = os.path.normpath(entry['path'])
        name = '%s [%s]' % (path, entry['branch'])
        reason = checks.sweep_skip(ctx, entry, base, ancestors, entries, held, now, idle_s, mark)
        if reason:
            ctx.say('skipped   %s: %s' % (name, reason))
            counts['skipped'] += 1
            continue
        blocked = holders.blockers(path)
        if blocked:
            ctx.say('skipped   %s: held - %d path(s) cannot be deleted; git was NOT run'
                    % (name, len(blocked)))
            ctx.describe(path, blocked)
            counts['skipped'] += 1
            continue
        if not apply:
            ctx.say('would     abandon %s under %s%s (marked stale %s by %s: %s), then delete'
                    ' branch %s' % (path, remove_checks.ARCHIVE, os.path.basename(path),
                                    marks.shown_at(mark), mark['by'], mark['why'],
                                    entry['branch']))
            counts['would abandon'] += 1
            continue
        why = checks.landing_hold(ctx)
        if why:
            ctx.say('deferred  %s: a landing started during the sweep; %s and every marked'
                    ' worktree after it were not abandoned' % (why, name))
            return why
        changed = checks.sweep_status(ctx, path)
        if changed:
            ctx.say('skipped   %s: changed since it was checked - %s; git was NOT run'
                    % (name, changed))
            counts['skipped'] += 1
            continue
        counts[abandon_marked(ctx, where, entry, mark, name)] += 1
    counts['failed'] += end_marks(ctx, where, marked, entries, apply)
    return None


def read_marks(ctx):
    """`(marks file, marks)` for the sweep, or `(file or None, None)` when they cannot be read:
    said as a failure, and then no marked tree is acted on."""
    where = None
    try:
        where = marks.file_of(ctx)
        got, why = marks.load(where)
    except marks.MarksError as e:
        got, why = None, str(e)
    if got is None:
        ctx.say('failed    the stale marks cannot be read (%s): no marked worktree is abandoned'
                % why)
    return where, got


def abandon_marked(ctx, where, entry, mark, name):
    """Abandon the marked tree of `entry` under the marks lock, only while its mark is still
    `mark`, held to the tip it was checked at; drop the mark once that is done. Returns the count
    it adds to: abandoned, skipped or failed."""
    branch = entry['branch']
    try:
        with marks.locked(where):
            now_marks, why = marks.load(where)
            if now_marks is None:
                ctx.say('failed    %s: the stale marks cannot be read again (%s); git was NOT run'
                        % (name, why))
                return 'failed'
            if marks.find(now_marks, branch) != mark:
                ctx.say('skipped   %s: its stale mark changed or was cleared since it was read;'
                        ' git was NOT run' % name)
                return 'skipped'
            status = remove_abandon._abandon(ctx, entry['path'], marks.tag_why(mark), False, None,
                                             expect=entry['head'])
            if status != OK:
                ctx.say('  mark     the stale mark of %s stays' % branch)
                return 'failed'
            marks.save(where, [m for m in now_marks if m['branch'] != branch])
    except marks.MarksError as e:
        ctx.say('failed    %s: %s' % (name, e))
        return 'failed'
    ctx.say('unmarked  %s: abandoned, so its stale mark is dropped' % branch)
    return 'abandoned'


def end_marks(ctx, where, marked, entries, apply):
    """The last pass over the marks read: a mark whose branch is gone is dropped (`apply`; else
    `would drop`), and a mark whose branch has no worktree is named for `--unmark-stale`. Returns
    the failures to count."""
    on = {e['branch'] for e in entries if e['branch']}
    gone, failed = [], 0
    for mark in marked:
        branch = mark['branch']
        tip, why = remove_abandon.branch_tip(ctx.main, branch)
        if tip is None:
            ctx.say('failed    the stale mark of %s: its branch cannot be read (%s)' % (branch, why))
            failed += 1
        elif not tip:
            gone.append(mark)
        elif branch not in on:
            ctx.say('note      the stale mark of %s names no worktree (none is on the branch, and'
                    ' the sweep abandons only a tree): clear it with `%s %s --unmark-stale`'
                    % (branch, ctx.s['tool'], branch))
    if not gone:
        return failed
    if not apply:
        for mark in gone:
            ctx.say('would     drop the stale mark of %s: the branch is gone' % mark['branch'])
        return failed
    drop = []
    try:
        with marks.locked(where):
            now_marks, why = marks.load(where)
            if now_marks is None:
                ctx.say('failed    the stale marks cannot be read again (%s); no mark was dropped'
                        % why)
                return failed + 1
            drop = [m for m in gone if marks.find(now_marks, m['branch']) == m]
            if drop:
                names = {m['branch'] for m in drop}
                marks.save(where, [m for m in now_marks if m['branch'] not in names])
    except marks.MarksError as e:
        ctx.say('failed    the stale marks of gone branches were not dropped: %s' % e)
        return failed + 1
    for mark in drop:
        ctx.say('dropped   the stale mark of %s: the branch is gone' % mark['branch'])
    return failed
