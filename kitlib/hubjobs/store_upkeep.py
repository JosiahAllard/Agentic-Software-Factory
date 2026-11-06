"""kitlib.hubjobs.store_upkeep: the hub server's daily upkeep of the git object store the PM data
worktree shares with the kit's checkouts (docs/action-hub.md "Store upkeep").

  make(sources) -> jobs.Job | None   the job; None when this machine has no PM data worktree
  StoreUpkeep(root, book=None, clock=None, holders=None)   its run(ctx): one pass
  scan(common, now)                  [finding text] for the common dir's .idx-less packs
  lock_age(common, now)              seconds since objects/maintenance.lock changed, or None
  lock_holders(snapshot, command_line)   [pid] of live git processes that may hold that lock

Every kit git child runs with auto gc and auto maintenance off (kitlib.git.cmd), so nothing packs
the store by itself any more; this job does, on the common dir of the PM data worktree only. One
pass, in order:

  1. `git commit-graph write --reachable --changed-paths --split`, untimed: the commit graph with
     Bloom filters, which a path-limited history walk (the backlog's) reads.
  2. `git gc` only when `count-objects -v` reports LOOSE_MIN loose objects or more (git's own
     gc.auto default, not measured here), as `-c gc.writeCommitGraph=false -c gc.packRefs=false
     -c pack.threads=CORES gc --quiet`, untimed: step 1 wrote the graph, and packing refs would
     race the ref writes of landings and backlog writes. It runs inside a machine-lock slot
     (kind KIND, CORES cpu slots, slug SLUG) that holds the landing resource of that common dir
     (kitlib.gate.land.queue_key), so no landing runs beside it. A lock that stays busy for
     WAIT_S, or an owner or game hold, skips the gc to the next pass.

It deletes nothing itself. A `.pack` with no `.idx` beside it older than PACK_AGE_S is a finding:
named in the pass's note and pushed as the job event `findings` with their count. git's own gc may
still prune what it prunes.

The recurrence check: an objects/maintenance.lock older than LOCK_AGE_S while no git process that
could hold it is alive (lock_holders: a git whose command line names maintenance or gc, or whose
command line cannot be read and which is still listed a moment later) is a stale lock. git then skips its maintenance silently for good,
so every pass that sees one names it in the note and pushes the job event STALE_EVENT, detail
its age in hours (`13h`), to the PM: ctx.event, which the hub's push worker sends as
`[hub push] job store-upkeep stale-lock detail:13h` (kitlib.hubsite.push PushWorker.job_event,
text in push.JOB_EACH). A lock with such a git alive is named in the note and not pushed; a
process listing that fails is pushed, the note saying the holders could not be listed.

Outcomes (kitlib.hubsite.jobs): the note says what each step did; a pass that only found nothing
to do is still a success (the commit graph is rewritten each time). A git step that fails, or a
lock refusal other than busy or hold, is a failure that names it; the other step still runs. A
stop asked between the steps ends the pass as a skip.

The git children run through kitlib.git.cmd.call, outside the slot's job: the lock counts the
slot's cpu slots, and pack.threads keeps gc to that count, but its threads are not pinned to them.
If the hub ends while gc runs, gc runs on (the child's job is not kill-on-close) and the slot ends
with the hub.
"""
import glob
import os
import time

from kitlib import config as kconfig
from kitlib import pmdata
from kitlib.gate import land
from kitlib.git import cmd
from kitlib.git import repo as gitrepo
from kitlib.hubsite import jobs
from kitlib.lock import run as lockrun
from kitlib.lock.store import EXIT_BUSY, EXIT_RESERVED, EXIT_TIMEOUT, LockError
from kitlib.proc import process

NAME = 'store-upkeep'
EVERY_S = 86400
FIRST_S = 900                       # not at once after a hub (re)start
LOOSE_MIN = 6700                    # git's gc.auto default; not measured for this store
KIND = 'build'
CORES = 2
SLUG = 'store-upkeep'
WAIT_S = 600                        # the longest wait for the slot before the gc is skipped
PACK_AGE_S = 86400                  # an .idx-less .pack younger than this may be a pack being written
LOCK_AGE_S = 12 * 3600              # a maintenance.lock younger than this may be a maintenance running
GRAPH = ['commit-graph', 'write', '--reachable', '--changed-paths', '--split']
GC = ['-c', 'gc.writeCommitGraph=false', '-c', 'gc.packRefs=false',
      '-c', 'pack.threads=%d' % CORES, 'gc', '--quiet']
NOT_NOW = (EXIT_BUSY, EXIT_TIMEOUT, EXIT_RESERVED)
STALE_EVENT = 'stale-lock'
HOLDER_WORDS = frozenset(('maintenance', 'gc'))     # the git subcommands that take maintenance.lock


class UpkeepFailed(Exception):
    """A step of the pass failed; the text names each and what the pass did."""


def make(sources):
    try:
        settings = pmdata.machine_settings(getattr(sources, 'home', None))
    except (kconfig.ConfigError, pmdata.PmDataError):
        settings = None             # the hub's own start-up reports an unreadable machine config
    if not settings or not settings.get('path'):
        return None
    return jobs.Job(NAME, EVERY_S, StoreUpkeep(settings['path']), first_s=FIRST_S)


def _run(args, cwd, timeout=cmd.GIT_TIMEOUT):
    """One git child: kitlib.git.cmd.call (the tests' seam)."""
    return cmd.call(args, cwd, timeout=timeout)


def scan(common, now):
    """[finding text] for the common dir `common`: each .pack with no .idx beside it older than
    PACK_AGE_S. Reads only."""
    found = []
    for pack in sorted(glob.glob(os.path.join(glob.escape(common), 'objects', 'pack', '*.pack'))):
        age = _age(pack, now)
        if age is not None and age >= PACK_AGE_S and not os.path.exists(pack[:-5] + '.idx'):
            found.append('%s has no .idx (%s old)' % (os.path.basename(pack), _span(age)))
    return found


def lock_age(common, now):
    """Seconds since the common dir's objects/maintenance.lock last changed, or None (none)."""
    return _age(os.path.join(common, 'objects', 'maintenance.lock'), now)


def lock_holders(snapshot=None, command_line=None):
    """[pid] of the live git processes that may hold objects/maintenance.lock: a git (exe name
    git or git.exe) whose command line names maintenance or gc (HOLDER_WORDS), or whose command
    line cannot be read and which a second listing still shows (it might; most kit gits end in
    well under a second, so one gone by the read is not counted). This process is left out.
    snapshot and command_line default to kitlib.proc.process's; OSError when the processes cannot
    be listed."""
    snapshot = snapshot or process.snapshot
    command_line = command_line or process.command_line
    out, unread = [], []
    for pid in _gits(snapshot()):
        line = command_line(pid)
        if line is None:
            unread.append(pid)
        elif HOLDER_WORDS & set(line.replace('"', ' ').split()):
            out.append(pid)
    if unread:
        still = set(_gits(snapshot()))
        out.extend(pid for pid in unread if pid in still)
    return sorted(out)


def _gits(rows):
    """The pids of the git processes in snapshot rows, this process left out."""
    me = os.getpid()
    return [pid for pid, _ppid, name in rows
            if pid != me and os.path.basename(name or '').lower() in ('git', 'git.exe')]


def _age(path, now):
    try:
        return now - os.stat(path).st_mtime
    except OSError:
        return None


def _span(seconds):
    hours = int(seconds // 3600)
    return '%d d' % (hours // 24) if hours >= 48 else '%d h' % hours


def loose_count(out):
    """The `count:` of `git count-objects -v` output (text), or None."""
    for line in out.splitlines():
        key, _, value = line.partition(':')
        if key.strip() == 'count':
            try:
                return int(value.strip())
            except ValueError:
                return None
    return None


def _failed(what, r):
    """`what` failed, with the last line git said (or its exit code)."""
    why = (r.err or '').strip()
    return '%s failed: %s' % (what, why.splitlines()[-1] if why else 'exit %s' % r.rc)


class StoreUpkeep(object):
    """run(ctx) is the job. `root` is the PM data worktree. The tests' seams: `book` (default
    kitlib.lock.run.book), `clock` epoch seconds, `holders` (default lock_holders), and the
    module's `_run`."""

    def __init__(self, root, book=None, clock=None, holders=None):
        self.root = root
        self.book = book or lockrun.book
        self.clock = clock or time.time
        self.holders = holders or lock_holders

    def __call__(self, ctx):
        common, why = gitrepo.common_dir(self.root)
        if not common:
            raise UpkeepFailed('the git common dir of %s: %s' % (self.root, why))
        now = self.clock()
        findings = scan(common, now)
        if findings:
            ctx.event('findings', str(len(findings)))
        notes, errors = [], []
        self._stale_lock(ctx, common, now, findings, notes)
        r = _run(GRAPH, self.root, timeout=None)
        if r.rc == 0:
            notes.append('commit graph written')
        else:
            errors.append(_failed('commit-graph write', r))
        if ctx.stop.is_set():
            return jobs.Skip('; '.join(notes + errors + ['stopped before the gc']
                                       + _finding_notes(findings)))
        self._gc(common, notes, errors)
        text = '; '.join(notes + errors + _finding_notes(findings))
        if errors:
            raise UpkeepFailed(text)
        return text

    def _stale_lock(self, ctx, common, now, findings, notes):
        """The recurrence check (module doc): a maintenance.lock older than LOCK_AGE_S with no git
        alive that may hold it is a finding and pushes STALE_EVENT; one with such a git alive is
        a note."""
        age = lock_age(common, now)
        if age is None or age < LOCK_AGE_S:
            return
        path = os.path.join(common, 'objects', 'maintenance.lock')
        try:
            pids = self.holders()
            unlisted = ''
        except OSError as e:
            pids, unlisted = [], '; the git processes could not be listed (%s)' % e
        if pids:
            notes.append('%s is %s old, but a git that may hold it is alive (pid %s): not pushed '
                         'this pass' % (path, _span(age), ', '.join(str(p) for p in pids)))
            return
        findings.append('stale %s, %s old, no git maintenance or gc alive%s: git skips its '
                        'maintenance while it is there' % (path, _span(age), unlisted))
        ctx.event(STALE_EVENT, '%dh' % int(age // 3600))

    def _gc(self, common, notes, errors):
        r = _run(['count-objects', '-v'], self.root)
        count = loose_count(cmd.text(r.out or b'')) if r.rc == 0 else None
        if count is None:
            errors.append(_failed('count-objects', r) if r.rc != 0 else
                          'count-objects gave no loose count')
            return
        if count < LOOSE_MIN:
            notes.append('%d loose objects, under %d: no gc' % (count, LOOSE_MIN))
            return
        try:
            slot = self.book(KIND, _repo_name(common), SLUG, cores=CORES, wait_s=WAIT_S,
                             cmd=['git', 'gc'], resource=land.queue_key(common),
                             note='store upkeep: git gc of %s' % common)
        except LockError as e:
            if e.code in NOT_NOW:
                notes.append('%d loose objects; gc skipped to the next pass: the machine lock '
                             'gave no slot (exit %d: %s)' % (count, e.code, e.message))
            else:
                errors.append('gc not run: the machine lock refused it (exit %d: %s)'
                              % (e.code, e.message))
            return
        with slot:
            r = _run(GC, self.root, timeout=None)
        if r.rc == 0:
            notes.append('gc packed %d loose objects' % count)
        else:
            errors.append(_failed('gc', r))


def _repo_name(common):
    """The lock record's repo name: the main checkout's folder name for a `.git` common dir."""
    common = os.path.normpath(common)
    if os.path.basename(common).lower() == '.git':
        common = os.path.dirname(common)
    return os.path.basename(common) or 'repo'


def _finding_notes(findings):
    return ['finding: %s' % f for f in findings]
