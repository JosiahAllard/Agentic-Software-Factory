"""kitlib.hubjobs.store_upkeep: the hub server's daily upkeep of the git object store the PM data
worktree shares with the kit's checkouts (docs/action-hub.md "Store upkeep").

  make(sources) -> jobs.Job | None   the job; None when this machine has no PM data worktree
  StoreUpkeep(root, book=None, clock=None)   its run(ctx): one pass
  scan(common, now)                  [finding text] for the common dir's leftovers

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

It deletes nothing itself. A `.pack` with no `.idx` beside it older than PACK_AGE_S, and an
objects/maintenance.lock older than LOCK_AGE_S, are findings: named in the pass's note and pushed
as the job event `findings` with their count. git's own gc may still prune what it prunes.

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
    PACK_AGE_S, and an objects/maintenance.lock older than LOCK_AGE_S. Reads only."""
    found = []
    for pack in sorted(glob.glob(os.path.join(glob.escape(common), 'objects', 'pack', '*.pack'))):
        age = _age(pack, now)
        if age is not None and age >= PACK_AGE_S and not os.path.exists(pack[:-5] + '.idx'):
            found.append('%s has no .idx (%s old)' % (os.path.basename(pack), _span(age)))
    lock = os.path.join(common, 'objects', 'maintenance.lock')
    age = _age(lock, now)
    if age is not None and age >= LOCK_AGE_S:
        found.append('objects/maintenance.lock is %s old: git skips its maintenance while it is '
                     'there' % _span(age))
    return found


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
    kitlib.lock.run.book), `clock` epoch seconds, and the module's `_run`."""

    def __init__(self, root, book=None, clock=None):
        self.root = root
        self.book = book or lockrun.book
        self.clock = clock or time.time

    def __call__(self, ctx):
        common, why = gitrepo.common_dir(self.root)
        if not common:
            raise UpkeepFailed('the git common dir of %s: %s' % (self.root, why))
        findings = scan(common, self.clock())
        if findings:
            ctx.event('findings', str(len(findings)))
        notes, errors = [], []
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
