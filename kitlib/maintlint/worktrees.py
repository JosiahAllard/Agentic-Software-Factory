"""kitlib.maintlint.worktrees: the repository's worktrees by kitlib.worktree.classify.classify's
class, read-only (no apply, no mark; docs/maint-lint.md "Worktree checks"). It reports what the
nightly sweep (kitlib.worktree.nightly) leaves, never what that sweep removes on its own:

  findings(top, classify)   `(findings, notes)`: one per tree the sweep leaves, by class: B and C
                            only when no stale mark holds on the branch (a mark that holds is the
                            sweep's to abandon; a void one is reported), with one
                            landed-still-pulled finding per claim on a landed tree a record keeps
                            C; A, E and O only when the sweep's own route skips them
                            (classify.sweep_plan: an E tree on a branch, an O tree outside the
                            folders or keeping an unsaved commit, an A or E tree whose reflog
                            holds one, an A tree on a branch outside the worktree folders). L
                            (live or kept) is never a finding.

The class is classify's own judgement; nothing here adds a rule of its own. A tree's idle time is
its age, context only, never a finding by itself.
"""
from kitlib import kitroot
from kitlib.worktree import classify as cmod
from kitlib.worktree import remove_checks

from . import Finding

REPORTED = ('A', 'B', 'C', 'E', 'O')


def sweep_leaves(row, plan=None):
    """True when the nightly sweep leaves the tree of this classify row: a B or C tree only when
    no stale mark holds on it; an A, E or O tree only when `plan` (classify.sweep_plan's answer
    for it) is a skip. With no plan (the repository could not be opened to ask), only what the row
    itself shows: an E tree on a branch, an O tree outside the folders or not known to keep
    nothing."""
    klass = row.get('class')
    if klass in ('B', 'C'):
        mark = row.get('mark')
        return not (mark and not mark.get('void'))
    if klass not in REPORTED:
        return False
    if plan is not None:
        return 'result' in plan
    if klass == 'E':
        return bool(row.get('branch'))
    if klass == 'O':
        return bool(row.get('outside')) or row.get('unsaved') != 0
    return False


def _plan(ctx, base, row):
    """classify.sweep_plan for an A, E or O row, or None when it cannot be asked."""
    if ctx is None or not base or row.get('class') not in cmod.ACTED:
        return None
    return cmod.sweep_plan(ctx, row, row, base)


def _age(row):
    idle = row.get('idle_s')
    return 'idle %dd' % (idle // 86400) if isinstance(idle, (int, float)) else None


def _handle(path):
    return path.replace('\\', '/').rstrip('/').rsplit('/', 1)[-1]


def fix_of(klass, row, tool, handle=lambda p: '"%s"' % p.replace('\\', '/')):
    slug = handle(row['path'])
    if klass == 'A':
        return '%s %s' % (tool, slug)
    if klass == 'O':
        return '%s --classify --apply   (from the main checkout; drops the registration)' % tool
    if klass == 'E':
        return '%s %s --abandon "evidence"' % (tool, slug)
    return '%s %s --abandon "<why>"   (after it is taken up: %s)' % (
        tool, slug, cmod.HEADINGS[klass][2])


def findings(top, classify=cmod.classify):
    res = classify(top)
    trees = (res.data or {}).get('trees')
    if trees is None:
        return [], ['worktrees NOT CHECKED: %s' % ((res.data or {}).get('error')
                                                    or ' '.join(res.lines[-1:]) or 'no data')]
    tool = kitroot.command('worktree_remove', top)
    backlog = kitroot.command('backlog')
    ctx, _refused = remove_checks.open_ctx(top)
    handle = ((lambda p: remove_checks.handle(ctx, p)) if ctx is not None
              else (lambda p: '"%s"' % p.replace('\\', '/')))
    base = (res.data or {}).get('base')
    out = []
    for row in trees:
        klass = row.get('class')
        if klass not in REPORTED or not sweep_leaves(row, _plan(ctx, base, row)):
            continue
        name = cmod.HEADINGS[klass][0]
        age = _age(row)
        out.append(Finding('worktree-%s' % klass, row['path'],
                           '%s %s: %s (%s)' % (klass, name, _handle(row['path']),
                                               row.get('branch') or 'detached'),
                           fix_of(klass, row, tool, handle), age))
        if klass == 'C' and row.get('landed') and isinstance(row.get('claims'), list):
            for rid in row['claims']:
                out.append(Finding(
                    'landed-still-pulled', rid,
                    '%s is still pulled, and its branch %s is in the base (landed)'
                    % (rid, row.get('branch')),
                    '%s -C <worktree> release %s --reason "landed"   (or close it if the work is'
                    ' done)' % (backlog, rid), age))
    notes = []
    if trees and (res.data or {}).get('unreadable'):
        notes.append('worktrees: %s' % res.data['unreadable'])
    return out, notes
