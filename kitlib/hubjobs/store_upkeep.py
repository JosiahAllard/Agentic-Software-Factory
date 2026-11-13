"""kitlib.hubjobs.store_upkeep: the hub server's daily upkeep of the git object store the PM data
worktree shares with the kit's checkouts (docs/hub-push.md "Store upkeep").

  make(sources) -> jobs.Job | None   the job; None when this machine has no PM data worktree
  StoreUpkeep(root, book=None, clock=None, holders=None)   its run(ctx): one pass
  orphans(common, now)               [(path, age)] of the common dir's deletable .idx-less packs
  scan(common, now)                  [finding text] for those packs
  prune(common, now)                 ([deleted pack name], [note for each file kept]) of those packs
  lock_age(common, now)              seconds since objects/maintenance.lock changed, or None
  lock_holders(snapshot, command_line)   [pid] of live git processes that may hold that lock

Kit git calls through kitlib.git.cmd run with auto gc and auto maintenance off. Gits run outside
the kit (agents' shells, the owner's) do not, and still auto-repack the store the PM data worktree
shares. This job packs and prunes it, on the common dir of the PM data worktree only. One pass,
in order:

  1. `git commit-graph write --reachable --changed-paths --split`, untimed: the commit graph with
     Bloom filters, which a path-limited history walk (the backlog's) reads.
  2. When there is a pack to prune or a gc to run, a machine-lock slot (kind KIND, CORES cpu
     slots, slug SLUG) that holds the landing resource of that common dir
     (kitlib.landing.lander.queue_key), so no landing runs beside it. A lock that stays busy for
     WAIT_S, or an owner or game hold, skips both to the next pass. Inside the slot, in order:
     a. The prune: each `pack-<hex>.pack` in objects/pack (a regular file) with no `.idx` and no
        `.keep` beside it and older than PACK_AGE_S (orphans) is deleted, then its `.rev`,
        `.mtimes` and `.bitmap` (SIBLINGS), each made writable first (git writes packs
        read-only, and Windows refuses to delete a read-only file). git reads a pack through its
        `.idx`, or through a multi-pack-index that lists it (git skips the `.idx` of a pack the
        MIDX names), so a pack whose name appears in objects/pack/multi-pack-index or any
        multi-pack-index.d/*.midx is kept and named in the note; a multi-pack-index that cannot
        be read keeps every pack that pass, with a note. What is left no git can read, so nothing
        reachable is lost. Such packs are left when a repack's delete is refused on Windows
        because another git holds the file open. git renames a new pack into place just before
        its `.idx`, so a young one is never touched. The multi-pack-index is read once per prune,
        after the orphans are listed; the `.idx` and `.keep` are checked again just before each
        delete. No path but a `.pack` and SIBLINGS is ever
        removed. A delete still refused after kitlib.fs.replace.remove's retries (a file in use)
        keeps that pack and its siblings; it is named in the note and retried next pass, never a
        failure.
     b. `git gc` only when `count-objects -v` reports LOOSE_MIN loose objects or more (git's own
        gc.auto default, not measured here), as `-c gc.writeCommitGraph=false -c gc.packRefs=false
        -c pack.threads=CORES gc --quiet`, untimed: step 1 wrote the graph, and packing refs would
        race the ref writes of landings and backlog writes.

An orphan still there after the pass (a young one is none), and an old .idx-less pack kept by its
`.keep` (never deleted), is a finding: named in the pass's note and pushed as the job event
`findings` with their count (see Pushes).

The recurrence check: an objects/maintenance.lock older than LOCK_AGE_S while no git process that
could hold it is alive (lock_holders: a git whose command line names maintenance or gc, or whose
command line cannot be read and which is still listed a moment later) is a stale lock. git then skips its maintenance silently for good,
so every pass that sees one names it in the note and pushes the job event STALE_EVENT, detail
its age in hours (`13h`), to the PM (see Pushes): ctx.event, which the hub's push worker sends as
`[hub push] job store-upkeep stale-lock detail:13h` (kitlib.hubsite.push PushWorker.job_event,
text in push.JOB_EACH). A lock with such a git alive is named in the note and not pushed; a
process listing that fails is pushed, the note saying the holders could not be listed.

Pushes: made at the end of the pass, after the git steps, so the run and its note are recorded
with them. An event is pushed only when its key (the count for findings; the lock being there for
STALE_EVENT) differs from the one last pushed or that push is REPUSH_S (a day) old. The last ones are kept in STATE_FILE of the common dir, so a hub
restart does not push them again.

Outcomes (kitlib.hubsite.jobs): the note says what each step did; a pass that only found nothing
to do is still a success (the commit graph is rewritten each time). A git step that fails, or a
lock refusal other than busy or hold, is a failure that names it; the other step still runs. A
stop asked between the steps ends the pass as a skip, before the prune and the gc.

The gc runs in the slot's own job (Slot.start, with kitlib.git.cmd.child_env; Slot.wait watches
it): on the slot's cores, ended by a stop of the run, and ended with the slot when the hub ends,
so it never runs on after the landing resource is free. A gc ended that way can leave a temporary
pack, which stays, or an .idx-less one, which a later pass prunes. The other git children run through
kitlib.git.cmd.call.
"""
import json
import os
import re
import stat
import subprocess
import tempfile
import time

from kitlib import config as kconfig
from kitlib.config import pmdata
from kitlib.fs import replace as fsreplace
from kitlib.config.types import ConfigError
from kitlib.landing import lander
from kitlib.git import cache, cmd
from kitlib.git import repo as gitrepo
from kitlib.hubsite import jobs
from kitlib.lock import run as lockrun
from kitlib.lock.store import EXIT_BUSY, EXIT_RESERVED, EXIT_TIMEOUT, LockError
from kitlib.proc import process
from kitlib.records import place

NAME = 'store-upkeep'
EVERY_S = 86400
FIRST_S = 900                       # not at once after a hub (re)start
LOOSE_MIN = 6700                    # git's gc.auto default; not measured for this store
KIND = 'build'
CORES = 2
SLUG = 'store-upkeep'
WAIT_S = 600                        # the longest wait for the slot before the gc is skipped
PACK_AGE_S = 86400                  # an .idx-less .pack younger than this may be a pack being written
PACK_NAME = re.compile(r'pack-[0-9a-f]+\.pack\Z')     # git's own name for a finished pack
SIBLINGS = ('.rev', '.mtimes', '.bitmap')   # what a prune removes after the .pack: never .idx, .keep
LOCK_AGE_S = 12 * 3600              # a maintenance.lock younger than this may be a maintenance running
GRAPH = ['commit-graph', 'write', '--reachable', '--changed-paths', '--split']
GC = ['-c', 'gc.writeCommitGraph=false', '-c', 'gc.packRefs=false',
      '-c', 'pack.threads=%d' % CORES, 'gc', '--quiet']
NOT_NOW = (EXIT_BUSY, EXIT_TIMEOUT, EXIT_RESERVED)
STALE_EVENT = 'stale-lock'
STATE_FILE = 'store-upkeep-pushed.json'     # in the common dir: {event: {detail, key, at}}
REPUSH_S = 86400                    # an unchanged event is pushed again after this
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


def _slot_run(slot, args, cwd):
    """cmd.Result of `git <args>` started in the booked slot's job (Slot.start, with
    cmd.child_env over the slot's environment) and watched by Slot.wait, untimed: it ends with the
    slot, and a stop of the run ends it (rc None). Its stdout is dropped; err is its stderr."""
    env = cmd.child_env(cmd.scrubbed_env(slot.env), args)
    cache.child(cmd.subcommand(args))
    try:
        with tempfile.TemporaryFile() as err:
            try:
                p = slot.start(['git'] + list(args), cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=err)
            except (OSError, LockError) as e:       # JobError is an OSError
                return cmd.Result(None, b'', 'could not run git in %s: %s' % (cwd, e))
            code, how = slot.wait(p)
            err.seek(0)
            text = err.read().decode('utf-8', 'replace').strip()
    finally:
        cache.child(cmd.subcommand(args))
    if how == 'cancelled':
        return cmd.Result(None, b'', 'the machine lock stopped run %s; git %s was ended'
                          % (slot.id, cmd.subcommand(args)))
    return cmd.Result(code, b'', text)


def _idx_less(common, now):
    """[(path, age seconds, has .keep)]: each regular file in the common dir's objects/pack named
    PACK_NAME, older than PACK_AGE_S, with no .idx beside it. Reads only."""
    folder = os.path.join(common, 'objects', 'pack')
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return []
    out = []
    for name in names:
        path = os.path.join(folder, name)
        if not PACK_NAME.match(name) or os.path.islink(path) or not os.path.isfile(path):
            continue
        age = _age(path, now)
        stem = path[:-len('.pack')]
        if age is not None and age >= PACK_AGE_S and not os.path.lexists(stem + '.idx'):
            out.append((path, age, os.path.lexists(stem + '.keep')))
    return out


def orphans(common, now):
    """[(path, age seconds)] of the common dir's prune candidates (module doc, step 2a): the
    .idx-less packs older than PACK_AGE_S with no .keep. Reads only."""
    return [(path, age) for path, age, keep in _idx_less(common, now) if not keep]


def _kept_why(pack, listed):
    """None when the .pack at `pack` may be deleted, else why not: an .idx or a .keep now beside
    it, or its .idx name or its own in `listed` (the multi-pack-index bytes)."""
    stem = pack[:-len('.pack')]
    for suffix in ('.idx', '.keep'):
        if os.path.lexists(stem + suffix):
            return 'it has a %s now' % suffix
    name = os.path.basename(stem).encode('ascii')
    if name + b'.idx' in listed or name + b'.pack' in listed:
        return 'the multi-pack-index lists it'
    return None


def _midx(folder):
    """The bytes of objects/pack/multi-pack-index and every multi-pack-index.d/*.midx in
    `folder`, joined (b'' when there is none). A pack's name in it (its PNAM chunk holds them as
    plain bytes) means git may read that pack without its .idx. OSError when one cannot be read."""
    paths = [os.path.join(folder, 'multi-pack-index')]
    chain = os.path.join(folder, 'multi-pack-index.d')
    if os.path.lexists(chain):
        paths += [os.path.join(chain, n) for n in sorted(os.listdir(chain)) if n.endswith('.midx')]
    parts = []
    for path in paths:
        try:
            with open(path, 'rb') as f:
                parts.append(f.read())
        except FileNotFoundError:
            continue
    return b'\0'.join(parts)


def scan(common, now):
    """[finding text] for each of the common dir's old .idx-less packs: an orphan, or one kept by
    its .keep. Reads only."""
    return ['%s has no .idx (%s old)%s' % (os.path.basename(path), _span(age),
                                          ', kept by .keep' if keep else '')
            for path, age, keep in _idx_less(common, now)]


def prune(common, now):
    """`([deleted pack name], [note])`: each of the common dir's orphans deleted, the .pack first
    (_kept_why checked again just before, a pack it keeps named in a note), then its SIBLINGS, each made writable first; nothing
    else is removed. A multi-pack-index that cannot be read deletes nothing, with a note. A
    remove still refused after kitlib.fs.replace.remove's retries is a note, never an error: a
    refused .pack keeps its siblings and is retried next pass (a sibling refused after its .pack
    went is only named: no git reads it without its pack)."""
    found = orphans(common, now)
    if not found:
        return [], []
    try:
        listed = _midx(os.path.join(common, 'objects', 'pack'))
    except OSError as e:
        return [], ['no pack pruned: the multi-pack-index could not be read (%s)' % e]
    gone, kept = [], []
    for pack, _age_s in found:
        name = os.path.basename(pack)
        why = _kept_why(pack, listed)
        if why:
            kept.append('%s kept: %s' % (name, why))
            continue
        try:
            _remove(pack)
        except OSError as e:
            kept.append('%s kept (retried next pass): %s' % (name, e))
            continue
        gone.append(name)
        for suffix in SIBLINGS:
            path = pack[:-len('.pack')] + suffix
            try:
                _remove(path)
            except OSError as e:
                kept.append('%s kept: %s' % (os.path.basename(path), e))
    return gone, kept


def _remove(path):
    """kitlib.fs.replace.remove of a file the prune chose, its read-only bit cleared first (git
    writes packs read-only; Windows refuses to delete such a file). A link is removed as it is,
    its target never made writable. A file already gone is no error."""
    try:
        mode = os.lstat(path).st_mode
    except FileNotFoundError:
        return
    if not stat.S_ISLNK(mode):
        os.chmod(path, mode | stat.S_IWRITE)
    fsreplace.remove(path)


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
        pushes = {}                 # event -> (detail, key), handed to the PM at the pass's end
        notes, errors, stale = [], [], []
        self._stale_lock(pushes, common, now, stale, notes)
        try:                        # a step that raises still lets the pushes go out
            r = _run(GRAPH, self.root, timeout=None)
            if r.rc == 0:
                notes.append('commit graph written')
            else:
                errors.append(_failed('commit-graph write', r))
            stopped = ctx.stop.is_set()
            if not stopped:
                self._slot_step(common, now, notes, errors)
        finally:
            left = scan(common, now)        # the orphans the prune did not delete
            if left:
                pushes['findings'] = (str(len(left)),) * 2
            self._push(ctx, common, now, pushes)
        findings = _finding_notes(left + stale)
        if stopped:
            return jobs.Skip('; '.join(notes + errors + ['stopped before the gc'] + findings))
        text = '; '.join(notes + errors + findings)
        if errors:
            raise UpkeepFailed(text)
        return text

    def _stale_lock(self, pushes, common, now, findings, notes):
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
        pushes[STALE_EVENT] = ('%dh' % int(age // 3600), 'present')    # its age is no change

    def _push(self, ctx, common, now, pushes):
        """Hand the PM each of `pushes` ({event: (detail, key)}) only when its key differs from the
        last one pushed or that push is REPUSH_S old (the key is the count for findings, and
        'present' for the stale lock, whose detail, its age, changes every hour); the last pushes are kept in STATE_FILE of
        the common dir (so a hub restart does not repeat them), written after ctx.event returns.
        An event not in `pushes` is forgotten, so it pushes again when it comes back. A state
        file that cannot be read counts as empty; one that cannot be written is ignored (the
        worst case is one repeated push)."""
        path = os.path.join(common, STATE_FILE)
        try:
            with open(path, encoding='utf-8') as f:
                last = json.load(f)
            if not isinstance(last, dict):
                last = {}
        except (OSError, ValueError):
            last = {}
        kept = {}
        for event, (detail, key) in sorted(pushes.items()):
            old = last.get(event)
            if (isinstance(old, dict) and old.get('key') == key
                    and isinstance(old.get('at'), (int, float)) and 0 <= now - old['at'] < REPUSH_S):
                kept[event] = old
                continue
            ctx.event(event, detail)
            kept[event] = {'detail': detail, 'key': key, 'at': now}
        if kept == last:
            return
        tmp = '%s.%d.tmp' % (path, os.getpid())
        try:
            with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
                json.dump(kept, f)
            fsreplace.replace(tmp, path)
        except OSError:
            try:
                fsreplace.remove(tmp)
            except OSError:
                pass

    def _slot_step(self, common, now, notes, errors):
        """Step 2 of the module doc: the prune, then the gc when the loose count calls for one,
        inside one slot, booked only when either has work."""
        r = _run(['count-objects', '-v'], self.root)
        count = loose_count(cmd.text(r.out or b'')) if r.rc == 0 else None
        if count is None:
            errors.append(_failed('count-objects', r) if r.rc != 0 else
                          'count-objects gave no loose count')
        elif count < LOOSE_MIN:
            notes.append('%d loose objects, under %d: no gc' % (count, LOOSE_MIN))
        gc = count is not None and count >= LOOSE_MIN
        to_prune = bool(orphans(common, now))
        if not (gc or to_prune):
            return
        what = ' and '.join(w for w, on in (('prune', to_prune), ('gc', gc)) if on)
        try:
            slot = self.book(KIND, _repo_name(common), SLUG, cores=CORES, wait_s=WAIT_S,
                             cmd=['git', 'gc'] if gc else None, resource=lander.queue_key(common),
                             note='store upkeep: %s of %s' % (what, common))
        except LockError as e:
            if e.code in NOT_NOW:
                notes.append('%s%s skipped to the next pass: the machine lock gave no slot '
                             '(exit %d: %s)' % ('%d loose objects; ' % count if gc else '', what,
                                               e.code, e.message))
            else:
                errors.append('%s not run: the machine lock refused it (exit %d: %s)'
                              % (what, e.code, e.message))
            return
        with slot:
            if to_prune:
                gone, kept = prune(common, now)
                if gone:
                    notes.append('pruned %d .idx-less pack%s: %s' % (
                        len(gone), '' if len(gone) == 1 else 's', ', '.join(gone)))
                notes.extend(kept)
            if gc:
                r = _slot_run(slot, GC, self.root)
        if not gc:
            return
        if r.rc == 0:
            notes.append('gc packed %d loose objects' % count)
        else:
            errors.append(_failed('gc', r))


def _repo_name(common):
    """The lock record's repo name, the one landings book under (kitlib.records.place.repo_name of
    the main checkout: `.kit.json` repo.name, else its folder name). The main checkout is the
    folder of a `.git` common dir; an unreadable config gives the folder name."""
    top = os.path.normpath(common)
    if os.path.basename(top).lower() == '.git':
        top = os.path.dirname(top)
    try:
        cfg = kconfig.load(top)
    except ConfigError:
        return os.path.basename(top) or 'repo'
    return place.repo_name(cfg, top) or 'repo'


def _finding_notes(findings):
    return ['finding: %s' % f for f in findings]
