"""kitlib.hubjobs.unblock_conditions: the hub server's check of the backlog's hold end conditions
(docs/hub-push.md "Timed jobs", docs/backlog.md "block --until").

  make(sources) -> jobs.Job | None   the job; None when launcher.repos names no repository
  UnblockJob(roots, readers, ...)    its run(ctx): one pass
  Readers(hub_store, plugin_root, record_closed)
                                     what a condition is read against (conditions.evaluate)

`block ID --on M --until "<condition>"` writes `until: M <condition>` on a record. Every EVERY_S
the job reads each repository's store (launcher.repos, the store coord_restart.store_root
names; no fetch: the hub's own backlog fetch keeps the local copy fresh) and, on each blocked
record, each `until` line whose member the record is still blocked on. A condition that is one of
the forms kitlib/backlog/conditions.py reads, and is met, clears that member: one store commit
under Session: hub-condition with the reason `condition met: <condition>`
(kitlib.hubsite.recordwrite.condition_met, which re-reads the record and writes nothing when the
line is gone), then one `unblocked` job push to the PM, detail `<repo>:<ID>`. The commit drops the
`until` line, so the next pass finds nothing to do: a rerun is idempotent. An `owner` member holds
one line per ask (conditions.SEVERAL, read as AND): a met line of several drops only that line
and the hold stays, with no push (recordwrite.condition_met's "dropped"); its last line clears it.

Never acted on: free text (not one of the forms), a member the record is not blocked on, an
`owner` member with any condition but a to-do, a question or an approval (the owner's own act; the job never
clears the owner's hold on its own reading), a condition holding a machine path
(kitlib.machinepaths: the store refuses it in the unblock's reason), and a condition that cannot
be told (a hub document, a store or the plugin export root that cannot be read, a path that is
not on a local drive (on Linux, not absolute or starting with `//`) after its %VAR% expansion or has a `..` part: conditions.evaluate's None). A coordinator
learns of the opened record from coord_restart's backlog wake (a stopped coordinator whose offer
gained it is restarted); a live one gets no push of its own.

An `approval:A-n decided` line is met when the approval is approved, denied or consumed
(conditions.DECIDED). One whose approval expired undecided stays (the record stays blocked) and
the job pushes the PM `approval-expired` (EXPIRED_EVENT), detail `<repo>:<ID>:<A-n>`, once per
line per hub run: the memory of what was pushed is not kept, so a hub restart pushes each such
line once more (a known limit). Withdrawing the approval drops the line (kitlib.hubsite.api).

Where each condition is read: a to-do, question or approval, the hub store's document on disk
(read only, `<store>/todos/<T-n>.json`, `<store>/questions/<Q-n>.json`,
`<store>/approvals/<A-n>.json`: the store writes each with
os.replace, so a read sees a whole document); a record, the named repository's store (closed: gone
from it, with a commit that removed it: a file missing with no commit is not closed); a path,
the file system; a plugin folder, the plugin
export root (kitlib.pluginsource settings, `export_dir`).

Outcomes (kitlib.hubsite.jobs): nothing met is a skip; an unblock is a success whose note names
the records. A store that cannot be read, or a write the store refused, is a failure once the
pass has done what it could (health shows it; the runner pushes `failing` at the third in a row).
A repository launcher.repos names whose store cannot be found is a note, not a failure.
"""
import json
import ntpath
import os
import re

from kitlib import platform as kplatform
from kitlib.hubsite import jobs

NAME = 'unblock-conditions'
EVERY_S = 300
EVENT = 'unblocked'
EXPIRED_EVENT = 'approval-expired'      # an `approval:` line's approval expired undecided
NOTE_MAX = 200
# A path condition is read only on a local drive (a drive letter, a colon, then a slash or a
# backslash; on Linux, an absolute path not starting with `//`): a UNC path (`\\host\share`) would
# have the hub reach a network host every pass.
LOCAL_DRIVE = re.compile(r'^[A-Za-z]:[\\/]')


class ConditionsFailed(Exception):
    """A store that could not be read, or a write that did not go through."""


class Readers(object):
    """conditions.evaluate's readers. `hub_store` is the hub store folder (None: to-dos,
    questions and approvals cannot be told), `plugin_root` the plugin export root (None: plugin folders cannot
    be told), `record_closed(repo, rid)` True, False or None."""

    def __init__(self, hub_store, plugin_root, record_closed):
        self.hub_store = hub_store
        self.plugin_root = plugin_root
        self.record_closed = record_closed

    scope = None            # (store root, snapshot) of the repository being read: `at`
    why = None              # why the last resource_free could not tell
    _read = None            # the scope's resource reads, made once per `at`: capacity, remote, split

    def at(self, root, snap):
        """Read `resource:` conditions against the store at `root`, read as `snap`. The resource
        reads (`.kit.json`, git) are made once per `at` pass and shared by every `resource:`
        condition read in it; the next `at` starts them afresh."""
        self.scope = (root, snap)
        self._read = {}

    def resource_free(self, name):
        """True when fewer live pulls use resource `name` than `.kit.json` backlog.resources
        declares for it (offer.resource_load's count), False when every unit is taken, None when
        that cannot be told (`why` says why): no store, a `.kit.json` that cannot be read, a name it
        does not declare, or git that cannot tell which pulls are live. The `.kit.json` and git
        reads are made once per `at`, whatever number of `resource:` conditions ask."""
        from kitlib import config as kconfig
        from kitlib.backlog import offer, opener
        self.why = None
        if self.scope is None:
            self.why = 'no store is being read'
            return None
        root, snap = self.scope
        read = self._read
        if 'config' not in read:
            try:
                read['config'] = (opener.repo_resources(root), kconfig.load(root).remote)
            except Exception:           # noqa: BLE001 - not told: the condition waits
                read['config'] = None
        if read['config'] is None:
            self.why = '.kit.json cannot be read'
            return None
        capacity, remote = read['config']
        if name not in capacity:
            self.why = '.kit.json does not declare resource %s' % name
            return None
        if 'split' not in read:
            read['split'] = offer.in_flight_split(snap, root, remote)
        holding, _dead, git_why = read['split']
        if git_why:
            self.why = 'git cannot tell which pulls are live (%s)' % git_why
            return None
        return len(offer.resource_load(holding, capacity)[name]) < capacity[name]

    def _doc(self, coll, doc_id):
        if not self.hub_store:
            return None
        try:
            with open(os.path.join(self.hub_store, coll, doc_id + '.json'), encoding='utf-8-sig') as f:
                doc = json.load(f)
        except (OSError, ValueError):
            return None
        return doc if isinstance(doc, dict) else None

    def _status(self, coll, doc_id):
        doc = self._doc(coll, doc_id)
        status = doc.get('status') if doc is not None else None
        return status if isinstance(status, str) else None

    def todo_status(self, tid):
        return self._status('todos', tid)

    def question_status(self, qid):
        return self._status('questions', qid)

    def approval_status(self, aid):
        """The approval's stored status (conditions.DECIDED ends the hold), or None."""
        return self._status('approvals', aid)

    def approval_expired(self, aid, now=None):
        """True when approval `aid` is still stored `pending` and expired undecided
        (kitlib.hubsite.approvals status_at at `now`, default the current time); its hold stays
        (the PM's ruling) and the job pushes the PM once (EXPIRED_EVENT)."""
        doc = self._doc('approvals', aid)
        if doc is None or doc.get('status') != 'pending':
            return False
        import datetime
        from kitlib.hubsite import approvals
        when = now or datetime.datetime.now(datetime.timezone.utc)
        return approvals.status_at(doc, when) == approvals.EXPIRED

    def path_exists(self, path):
        r"""True or False for a path on a local drive (conditions.evaluate expands its %VAR%
        first); None, cannot tell, for any other (a UNC or device path, a `..` part), which is
        never probed."""
        if not local_path(path):
            return None
        return os.path.exists(path)

    def plugin_present(self, folder):
        if not self.plugin_root:
            return None
        return os.path.isdir(os.path.join(self.plugin_root, folder))


def local_path(path):
    r"""True when `path` names a place on a local drive: a drive letter, a colon and a slash or
    backslash first (LOCAL_DRIVE), no `..` part, and
    a drive ntpath reads as a letter (not `\\?\` or `\\host\share`). On Linux: an absolute path
    that does not start with `//` and has no `..` part."""
    if not isinstance(path, str):
        return False
    if kplatform.NAME == 'linux':
        return (path.startswith('/') and not path.startswith('//')
                and '..' not in path.split('/'))
    if not LOCAL_DRIVE.match(path):
        return False
    if len(ntpath.splitdrive(path)[0]) != 2:
        return False
    return '..' not in re.split(r'[\\/]', path)


def open_snapshot(root):
    """(snapshot, store) of the store at `root`, read with no fetch, or (None, None) for the
    implicit store (the hub writes none: kitlib.hubsite.recordwrite). A store that cannot be read
    raises."""
    from kitlib.backlog import opener
    from kitlib.hubsite import recordwrite
    ctx = opener.open_store(root, fetch=False, session=recordwrite.CONDITION_SESSION,
                            probes=opener.NO_PROBES)
    if getattr(ctx.store.backend, 'implicit', False):
        return None, None
    return ctx.store.load(), ctx.store


def watched(snap):
    """[(rid, member, condition)] of each blocked record's `until` lines whose member it is still
    blocked on and that this job may act on: a readable condition, on `owner` an owner event
    only, and no machine path in it (kitlib.machinepaths: the unblock's reason and commit subject
    would carry it, which the store refuses, so the line would fail every pass)."""
    from kitlib import machinepaths
    from kitlib.backlog import conditions as condmod
    from kitlib.backlog import record as rmod
    folders = None
    out = []
    for rec in snap.readable:
        if rec.get('state') != 'blocked':
            continue
        members = rec.all('blocked_on')
        for member, cond in rmod.until_lines(rec):
            if member not in members or condmod.parse(cond) is None:
                continue
            if condmod.owner_error(member, cond):
                continue
            if folders is None:
                folders = machinepaths.folders()[0]
            if machinepaths.find(cond, folders):
                continue
            out.append((rec.id, member, cond))
    return out


class UnblockJob(object):
    """One pass per run. `roots()` -> ([(repo, root)], notes): the stores to read; `readers()`
    -> a fresh Readers for the pass; `write(root, rid, member, cond)` -> {"lines", "unblocked",
    "dropped"} (recordwrite.condition_met); `snapshot(root)` -> (snapshot, store)
    (open_snapshot)."""

    def __init__(self, roots, readers, write=None, snapshot=None):
        self.roots = roots
        self.readers = readers
        self.write = write
        self.snapshot = snapshot or open_snapshot
        # the (repo, rid, A-n) lines whose expiry this job has pushed: in memory, so a hub
        # restart pushes each once more while its line stands (a known limit)
        self.told = set()

    def _expired(self, ctx, readers, repo, rid, cond):
        """Push the PM once (EXPIRED_EVENT, detail `<repo>:<ID>:<A-n>`) for an `approval:` line
        whose approval expired undecided: the line stays and the record stays blocked."""
        c = cond and cond.kind == 'approval'
        check = getattr(readers, 'approval_expired', None)
        if not c or not callable(check) or (repo, rid, cond.target) in self.told:
            return
        if check(cond.target):
            self.told.add((repo, rid, cond.target))
            ctx.event(EXPIRED_EVENT, '%s:%s:%s' % (repo, rid, cond.target))

    def __call__(self, ctx):
        from kitlib.backlog import conditions as condmod
        from kitlib.hubsite.store import HubError
        write = self.write
        if write is None:
            from kitlib.hubsite import recordwrite
            write = recordwrite.condition_met
        roots, notes = self.roots()
        readers = self.readers()
        done, dropped, failed, count, unread = [], [], [], 0, []
        for repo, root in roots:
            if ctx.stop.is_set():
                break
            try:
                snap, store = self.snapshot(root)
            except Exception as e:      # noqa: BLE001 - one store's fault leaves the others
                failed.append('%s: the store could not be read (%s)' % (repo, type(e).__name__))
                continue
            if snap is None:
                continue
            seed = getattr(getattr(readers, 'record_closed', None), 'seed', None)
            if callable(seed):
                seed(repo, snap, store)     # a `record:` condition reuses this load
            at = getattr(readers, 'at', None)
            if callable(at):
                at(root, snap)
            for rid, member, cond in watched(snap):
                count += 1
                parsed = condmod.parse(cond)
                met = condmod.evaluate(parsed, readers)
                if met is None and parsed.kind == 'resource':
                    unread.append('%s:%s: %s not told (%s)' % (
                        repo, rid, parsed.target, getattr(readers, 'why', None) or 'unknown'))
                if met is False:
                    self._expired(ctx, readers, repo, rid, parsed)
                if met is not True:
                    continue
                try:
                    out = write(root, rid, member, cond)
                except HubError as e:
                    failed.append('%s:%s: %s' % (repo, rid, e.message))
                    continue
                if out.get('unblocked'):
                    done.append('%s:%s' % (repo, rid))
                    ctx.event(EVENT, '%s:%s' % (repo, rid))
                elif out.get('dropped'):
                    # one `owner` line of several met: the line goes, the hold stays (no push)
                    dropped.append('%s:%s' % (repo, rid))
        note = '; '.join(
            unread      # first: the cut below must not drop why a condition is not told
            + (['unblocked %s' % ', '.join(done)] if done else [])
            + (['line met, still held: %s' % ', '.join(dropped)] if dropped else [])
            + notes)[:NOTE_MAX]
        if failed:
            raise ConditionsFailed('; '.join(failed + ([note] if note else []))[:NOTE_MAX * 2])
        if not done and not dropped:
            return jobs.Skip(('no condition met (%d watched)' % count
                              + ('; ' + note if note else ''))[:NOTE_MAX])
        return note


def repo_roots(settings):
    """([(repo, store root)], notes): launcher.repos' repositories, each once per store root (two
    repositories sharing a store read it once, under the first name)."""
    from kitlib.hubjobs import coord_restart
    out, seen, notes = [], set(), []
    for repo in sorted(settings.repos):
        try:
            root = coord_restart.store_root(settings, repo)
        except coord_restart.BacklogUnread as e:
            notes.append('%s skipped: %s' % (repo, e))
            continue
        key = os.path.normcase(os.path.abspath(root))
        if key not in seen:
            seen.add(key)
            out.append((repo, root))
    return out, notes


class RecordReader(object):
    """record_closed for one pass: each repository's store read once."""

    def __init__(self, settings, snapshot=None):
        self.settings = settings
        self.snapshot = snapshot or open_snapshot
        self._read = {}

    def seed(self, repo, snap, store):
        """Take the pass's own read of `repo`'s store (UnblockJob has it already) in place of a
        second one; a read made earlier in the pass stands."""
        self._read.setdefault(repo, (snap, store))

    def __call__(self, repo, rid):
        from kitlib.hubjobs import coord_restart
        if repo not in self._read:
            try:
                root = coord_restart.store_root(self.settings, repo)
                self._read[repo] = self.snapshot(root)
            except Exception:           # noqa: BLE001 - not told: the condition waits
                self._read[repo] = (None, None)
        snap, store = self._read[repo]
        if snap is None:
            return None
        if snap.get(rid) is not None:
            return False
        try:
            gone = store.removal(rid, snap)     # committed only: a file missing is not closed
        except Exception:               # noqa: BLE001
            return None
        return True if gone else None


def plugin_root(home):
    """The plugin export root, or None when it cannot be told."""
    from kitlib import pluginsource
    try:
        return pluginsource.load(home).export_dir
    except Exception:                   # noqa: BLE001 - a bad config: plugin conditions wait
        return None


def hub_store(home):
    """The hub store folder, or None when it cannot be told."""
    from kitlib.hubsite import settings as hsettings
    try:
        return hsettings.load(home=home).store
    except Exception:                   # noqa: BLE001
        return None


def make(sources):
    from kitlib.launcher import settings as lsettings
    home = getattr(sources, 'home', None)
    s = lsettings.load(home)
    if not s.repos:
        return None                     # no repository to read on this machine
    job = UnblockJob(lambda: repo_roots(s),
                     lambda: Readers(hub_store(home), plugin_root(home), RecordReader(s)))
    return jobs.Job(NAME, EVERY_S, job)
