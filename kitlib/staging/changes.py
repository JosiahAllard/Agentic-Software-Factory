"""kitlib.staging.changes: what has landed on the staging branch and is not on the main branch yet,
and whether a promotion is due (docs/modules/staging-design.md "Promotion").

Untested is the first-parent commits of `<remote>/<staging>` that `<remote>/<main>` lacks: each is
one landing (or a commit pushed straight to staging). A promotion is due at
`"staging".promote_after_landings` of them (default DUE_COUNT), or once the oldest is
`"staging".promote_after_hours` old (default DUE_HOURS), both read from the config the caller
read at `<remote>/<main>` (kitlib.dailyfull.promote.staging_read). Every git read goes through
the caller's `git(cwd, *args) -> (rc, out, err)` (kitlib.dailyfull.git). Nothing here prints.
"""

DUE_COUNT = 4            # "staging".promote_after_landings' default (kitlib.config.schema)
DUE_HOURS = 6            # "staging".promote_after_hours' default


def thresholds(cfg):
    """`(landings, hours)` from `cfg`'s "staging" section (its defaults when unset)."""
    s = cfg.section('staging')
    landings, hours = s.get('promote_after_landings'), s.get('promote_after_hours')
    return (DUE_COUNT if landings is None else landings), (DUE_HOURS if hours is None else hours)


def first_parent(root, git, base, tip):
    """`([(sha, committer time)], None)`: the first-parent commits of `tip` that `base` lacks,
    oldest first (each one landing, or a commit pushed straight to staging; the culprit hunt's
    candidates too, kitlib.staging.culprit), or `(None, why)` when git cannot say."""
    rc, out, err = git(root, 'log', '--first-parent', '--reverse', '--format=%H %ct',
                       '%s..%s' % (base, tip))
    if rc != 0:
        return None, 'git log %s..%s failed: %s' % (base, tip, err or 'exit %d' % rc)
    pairs = [line.split() for line in out.splitlines() if line.strip()]
    return [(sha, int(t)) for sha, t in pairs], None


def untested(root, cfg, staging, git, main_ref=None):
    """`((count, oldest), None)`: the first-parent commits of `<remote>/<staging>` that
    `main_ref` (default `<remote>/<main>`) lacks (first_parent), and the committer time (epoch
    seconds) of the oldest of them, None when there are none. `(None, why)` when git cannot say."""
    tip = '%s/%s' % (cfg.remote, staging)
    base = main_ref or '%s/%s' % (cfg.remote, cfg.main_branch)
    found, why = first_parent(root, git, base, tip)
    if found is None:
        return None, why
    times = [t for _sha, t in found]
    return (len(times), min(times) if times else None), None


def due(count, oldest, now, landings=DUE_COUNT, hours=DUE_HOURS):
    """Whether a promotion is due: `landings` untested commits, or the oldest of them `hours`
    old (`oldest` and `now` in epoch seconds); never with nothing untested. Pure."""
    if not count:
        return False
    return count >= landings or (oldest is not None and (now - oldest) / 3600.0 >= hours)


def not_due_line(count, oldest, now, landings, hours):
    """The line of a check that found nothing due."""
    age = '' if oldest is None else ', oldest %.1f h' % ((now - oldest) / 3600.0)
    return 'promote: NOT DUE (%d untested%s; due at %d, or at %g h)' % (count, age, landings,
                                                                       hours)
