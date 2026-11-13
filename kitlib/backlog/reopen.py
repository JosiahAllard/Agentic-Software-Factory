"""Reopen, at a landing, what a reverted landing closed (docs/backlog-store.md "Reopen at a
revert"; called by kitlib.landing.after). A branch that reverts an earlier landing says so with a
`Reverts-landing: <merge sha>` line (at_landing.REVERTS_KEY) in one of its commits; once it has
landed, each record that landing's own close removed comes back, open and unpulled.

  reopen_reverted  after the push and the closes: each sha the branch's Reverts-landing lines name
                   must be a full merge sha on the first-parent chain of the base the branch
                   merged onto (else a NOTE, and nothing is reopened for it). A record whose
                   newest removal in a store is that landing's close (`closes <ID>: landed <sha>`,
                   at_landing.LANDED_CLOSE) and that the landing itself closed (the record was
                   pulled by the landed branch, or a Closes line of that branch names it) is
                   written back as it was before the close: `state: open`, its pull cleared, its
                   priority kept, and a `Reopened` body line. Any other close naming the sha (one
                   by hand) is a NOTE. The configured store takes part when it is a ref or branch
                   store, and the PM data store; the implicit default store is skipped (the close
                   only reads it). Whether a store holds the record again is decided on the fresh
                   snapshot the write plans on: then a NOTE, and it is left as it is. A write
                   that fails is a WARNING naming the full restore by hand.

The records the close unblocked (plan_end.dependent_rewrites dropped their `item:<ID>` blocker)
are not blocked again.
"""
import datetime
import re

from kitlib.git import cmd, refs

from . import at_landing
from . import plan as pmod
from . import record as rmod
from . import store as smod
from . import store_folder, store_read
from .errors import IndexStale, Overtaken, Refusal, Uncommitted

FULL_SHA = re.compile(r'^[0-9a-f]{40}$')
# A landing's merge subject (kitlib.landing.merge: `Merge <branch>` or `Merge <branch>: <summary>`);
# a ref name holds no space and no colon.
MERGE_SUBJECT = re.compile(r'^Merge ([^\s:]+)(?::|$)')


def today():
    """The date of a `Reopened` line (a test patches it)."""
    return datetime.date.today().isoformat()


def _one_line(text):
    return ' '.join(str(text).split())


def shas(values):
    """The shas `Reverts-landing:` values name: each value split on commas, the first word of each
    part, in order, each once. Not checked here (_landing does). Pure."""
    out = []
    for value in values:
        for part in value.split(','):
            words = part.split()
            if words and words[0] not in out:
                out.append(words[0])
    return out


class _Landing(object):
    """One reverted landing: `merge`, its full sha; `branch`, the branch it merged, from its
    subject (None when the subject is no landing's); `ids`, the ids its Closes lines name."""

    def __init__(self, merge, branch, ids):
        self.merge, self.branch, self.ids = merge, branch, list(ids)

    def closed_it(self, rid, rec):
        """Whether this landing's own close removed `rid` (`rec`, its text before the close)."""
        return ((self.branch is not None and rec.get('pulled_by') == self.branch)
                or rid in self.ids)


def _landing(root, sha, base, branch):
    """`(_Landing, None)` for a sha a Reverts-landing line of `branch` names that is a landing's
    merge on the first-parent chain of `base`; else `(None, the NOTE or WARNING line)`."""
    def note(why):
        return (None, 'NOTE: %s landed, and a Reverts-landing line of its commits names `%s`, %s, '
                'so nothing was reopened for it' % (branch, sha, why))
    if not FULL_SHA.match(sha):
        return note('which is not a full 40-character commit sha (an abbreviated sha is not '
                    'checked)')
    if cmd.call(['cat-file', '-e', '%s^{commit}' % sha], root).rc != 0:
        return note('which is no commit this clone has')
    held, why = refs.on_first_parent(sha, base, root)
    if held is None:
        return (None, 'WARNING: %s landed, and whether the merge %s its Reverts-landing line names '
                'is on the first-parent chain of %s could not be told (%s), so nothing was '
                'reopened for it' % (branch, sha, base, _one_line(why)))
    if not held:
        return note('which is not on the first-parent chain of %s, the base it merged onto (a '
                    "landing's merge is)" % base[:12])
    out, why = cmd.run_text(['log', '-1', '--format=%P%x1f%s', sha, '--'], root)
    if out is None:
        return (None, 'WARNING: %s landed, and the merge %s its Reverts-landing line names could '
                'not be read (%s), so nothing was reopened for it' % (branch, sha, _one_line(why)))
    parents, _, subject = out.partition('\x1f')
    parents = parents.split()
    if len(parents) != 2:
        return note('which is not a merge commit')
    m = MERGE_SUBJECT.match(subject)
    ids, why = at_landing.closes_trailers(root, parents[1], parents[0])
    if ids is None:
        return (None, 'WARNING: %s landed, and the Closes lines of the merge %s its Reverts-landing '
                'line names could not be read (%s), so nothing was reopened for it'
                % (branch, sha, _one_line(why)))
    return _Landing(sha, m.group(1) if m else None, ids), None


class _Done(object):
    """What the stores said: `found`, the merges a close of some store names; `unread`, whether a
    store could not be read."""

    def __init__(self):
        self.found, self.unread = set(), False


def reopen_reverted(root, cfg, branch, merge, tip, base, session=at_landing.SESSION):
    """Reopen what each landing the `Reverts-landing:` lines of `branch` (landed as `merge`; its
    commits are `tip --not base`) name closed (this module's docstring). The lines to print; []
    when the branch has no such line."""
    values, why = at_landing.reverts_trailers(root, tip, base)
    if values is None:
        return ['WARNING: %s landed, and the Reverts-landing lines of its commits could not be read '
                '(%s), so nothing was reopened; see them with: git log --format=%%B %s --not %s'
                % (branch, _one_line(why), tip, base)]
    lines, landings = [], {}
    for sha in shas(values):
        got, line = _landing(root, sha, base, branch)
        if line:
            lines.append(line)
        if got is not None:
            landings[got.merge] = got
    if not landings:
        return lines
    done = _Done()
    backend = at_landing.ref_store(root, cfg, fetch=True)
    if backend is not None and not getattr(backend, 'implicit', False):
        lines.extend(_reopen_in(backend, root, cfg, branch, merge, landings, session, done,
                                lambda *args: at_landing.close_command(root, *args)))
    pm_root, pm_cfg, pm_backend, why = at_landing.pm_data_store(
        root, backend.describe() if backend is not None else None)
    if why:
        done.unread = True
        lines.append("WARNING: %s landed, and the PM data branch's backlog store was not read (%s), "
                     'so nothing it holds was reopened for the reverted landing%s %s'
                     % (branch, _one_line(why), '' if len(landings) == 1 else 's',
                        ', '.join(sorted(landings))))
    elif pm_backend is not None:
        lines.extend(_reopen_in(pm_backend, pm_root, pm_cfg, branch, merge, landings, session,
                                done, lambda *args: at_landing.backlog_command(pm_root, *args)))
    missed = sorted(m for m in landings if m not in done.found)
    if missed and not done.unread:
        lines.append('NOTE: %s landed, and no backlog store this landing writes holds a close by '
                     'the reverted landing%s %s, so nothing was reopened for %s (a batch landed '
                     'before each branch closed with its own merge closed every record with the '
                     "batch's last merge)" % (branch, '' if len(missed) == 1 else 's',
                                              ', '.join(missed),
                                              'it' if len(missed) == 1 else 'them'))
    return lines


def _unread(branch, where, why):
    return ('WARNING: %s landed, and the backlog store %s could not be read (%s), so nothing it '
            'holds was reopened' % (branch, where, _one_line(why)))


def _reopen_in(backend, root, cfg, branch, merge, landings, session, done, command):
    """reopen_reverted for one store: `backend` of the repository or worktree `root` with `cfg`;
    `command(*args)` is the printed backlog.py command line."""
    where = backend.describe()
    if getattr(backend, 'remote', None):
        remote_tip, why = refs.ls_remote(backend.remote, backend.remote_ref, root,
                                         timeout=at_landing.FETCH_TIMEOUT)
        if remote_tip is None:
            done.unread = True
            return [_unread(branch, where, why)]
        if not remote_tip:
            return []                       # no store: it closed nothing
    section = cfg.section('backlog')
    store = smod.Store(backend, rmod.Rules(section['id_grammar'], section['phases']),
                       session=session)
    try:
        snap = store.load()
        store_read.require_readable(snap)
        removed = store.removed(snap)
    except Refusal as exc:
        done.unread = True
        return [_unread(branch, where, '%s: %s' % (exc.label, exc))]
    lines = []
    for rid, removal, text in removed:
        m = at_landing.LANDED_CLOSE.match(removal.subject)
        if not m or m.group(1) != rid or m.group(2) not in landings:
            continue
        landing = landings[m.group(2)]
        done.found.add(landing.merge)
        rec = rmod.parse(backend.path_of(rid), text, rid)
        if rec.readable and not landing.closed_it(rid, rec):
            lines.append('NOTE: %s landed, and %s was closed as `landed %s` in the backlog store '
                         '%s (%s), but not by that landing: its branch %s did not hold it pulled '
                         'and no Closes line of it names it, so it was left closed'
                         % (branch, rid, landing.merge, where, removal.sha,
                            landing.branch or '(not a landing merge)'))
            continue
        lines.extend(_reopen_one(store, backend, root, branch, merge, landing, rid, removal, rec,
                                 command))
    return lines


def restored_text(rec, landing, branch, merge, day):
    """The text of `rec` (as it was before the close) reopened: `state: open`, its pull and
    pre-emption keys cleared, everything else kept (its priority with it), and a `Reopened` line
    at the end of its body. Pure."""
    changes = [('state', ['open'])] + [(k, []) for k in rmod.PULL_CLEARS + rmod.PREEMPT_KEYS]
    pairs = rmod.apply_changes(rec.pairs(), changes)
    body = (rec.body or '').rstrip('\n')
    said = ('Reopened (%s): the landing %s that closed it was reverted by %s (merge %s).'
            % (day, landing.merge[:12], branch, (merge or '?')[:12]))
    return pmod._text(pairs, (body + '\n\n' if body.strip() else '') + said + '\n')


def _reopen_one(store, backend, root, branch, merge, landing, rid, removal, rec, command):
    where = backend.describe()

    def plan(snap):
        # Held again is read here, on the snapshot this write plans on, never on the first load.
        again = [s for s in snap.by_id if s.casefold() == rid.casefold()]
        if again:
            return smod.Change({}, '', [
                'NOTE: %s landed, and %s, which the reverted landing %s closed, is in the backlog '
                'store %s again%s, so it was left as it is'
                % (branch, rid, landing.merge, where,
                   '' if again[0] == rid else ' as %s' % again[0])])
        return smod.Change({rid: restored_text(rec, landing, branch, merge, today())},
                           'reopens %s: landing %s reverted by %s' % (rid, landing.merge, branch))

    def by_hand(why):
        path = backend.path_of(rid)
        add = ['add', rid, '--reopen', '--summary', '"%s"' % (rec.get('summary') or '<summary>')]
        for s in rec.all('scope') or ['<scope>']:
            add += ['--scope', s]
        add += ['--found-on', 'none', '--why-not-folded',
                '"landing %s was reverted"' % landing.merge[:12], '--body-file', '<its body>']
        return ('WARNING: %s landed, and %s, which the reverted landing %s closed, was not '
                'reopened in the backlog store %s (%s); restore it by hand: read its text with: '
                'git -C "%s" show %s^:%s, then put its body in a file and run: %s, then: %s'
                % (branch, rid, landing.merge, where, why, root, removal.sha, path,
                   command(*add), command('set', rid, '--priority', rec.get('priority') or '-',
                                          '--priority-by', rec.get('priority_by') or '-')))
    if not rec.readable:
        return [by_hand('its text before the close cannot be read as a record')]
    try:
        change, tip = store.write(plan)
    except Overtaken as exc:
        return [by_hand('%s: %s%s' % (exc.label, _one_line(str(exc)),
                                      '; compare each path left with HEAD first: %s' % exc.compare
                                      if exc.left else ''))]
    except Uncommitted as exc:
        return ['WARNING: %s landed, and %s was reopened in the backlog store %s but not committed '
                '(%s); commit it with: %s'
                % (branch, rid, where, _one_line(str(exc)),
                   command('catch-up', '--apply',
                           store_folder.quoted(exc.paths or [backend.path_of(rid)])))]
    except IndexStale as exc:
        return ['WARNING: %s landed, and %s was reopened in the backlog store %s (%s); repair the '
                'index with: %s' % (branch, rid, where, _one_line(str(exc)), exc.repair)]
    except Exception as exc:                # one record's failure never stops the others
        what = ('%s: %s' % (exc.label, exc) if isinstance(exc, Refusal)
                else '%s: %s' % (type(exc).__name__, exc))
        return [by_hand(_one_line(what))]
    if not change.writes:
        return change.lines
    return ['reopened %s in the backlog store %s: the landing %s that closed it was reverted by %s '
            '(store at %s); state: open, priority %s kept'
            % (rid, where, landing.merge, branch, (tip or '?')[:10], rec.get('priority') or '-')
            ] + change.lines
