"""The ref and branch backends (docs/backlog-store.md "Stores"): records as `backlog/<ID>.md`
on a ref with no history shared with main (`ref`) or on a visible `backlog` branch (`branch`),
published by compare-and-swap (kitlib/backlog/store.py, the write loop).
"""
import os
import random
import shutil
import time

from kitlib.config import user as kuser
from kitlib.git import cmd, refs

from . import store as smod
from . import store_live as slive
from . import store_read as sread
from .errors import Failure, Refusal

FETCH_TIMEOUT = 60


# --- the ref and branch backends ------------------------------------------------------------------

def find_remote(root, remote):
    """`remote` when it is configured, None when the repository has no remote at all; Failure
    when git cannot say, or `remote` is missing but others exist (a misnamed remote must not turn
    the store into a local one)."""
    r = cmd.call(['remote', 'get-url', remote], root)
    if r.rc == 0:
        return remote
    if r.rc != 2:                                  # 2: no such remote; anything else is a failure
        raise Failure('cannot look up remote %s: %s'
                      % (remote, r.err or 'git remote get-url exited %s' % r.rc))
    names, why = cmd.run_text(['remote'], root)
    if names is None:
        raise Failure('cannot list the remotes: %s' % why)
    if names:
        raise Failure('no remote named %s, but this repository has %s - set .kit.json '
                      '"repo".remote' % (remote, ', '.join(names.split())))
    return None


class RefBackend(object):
    """Records under `backlog/` in the tree of a ref. With a remote the remote's `remote_ref` is
    the store and `local_ref` caches it; without one (the repository has no remote at all)
    `local_ref` is the store. `implicit`: the store is `.kit.json`'s default, not one it names
    (open_backend), so it reads as usual and publish refuses (default_store_refusal)."""

    def __init__(self, root, remote, remote_ref, local_ref, no_remote_ref=None, fetch=True,
                 implicit=False):
        self.root = root
        self.remote_ref = remote_ref
        self.fetch = fetch
        self.implicit = implicit
        self.remote = find_remote(root, remote)
        self.local_ref = local_ref if self.remote else (no_remote_ref or local_ref)

    def describe(self):
        if self.remote:
            return '%s on %s' % (self.remote_ref, self.remote)
        return self.local_ref

    def _git(self, args, **kw):
        out, why = cmd.run(args, self.root, **kw)
        if out is None:
            raise Failure('git %s: %s' % (args[0], why))
        return out

    def ensure_refspec(self):
        """Best effort: a plain `git fetch` brings the store too (design: the tool adds it). Called
        only once a fetch found the store on the remote: a refspec naming a ref the remote lacks
        makes every plain `git fetch` fail ("couldn't find remote ref")."""
        if not self.remote:
            return
        spec = '+%s:%s' % (self.remote_ref, self.local_ref)
        out, _ = cmd.run(['config', '--get-all', 'remote.%s.fetch' % self.remote], self.root)
        if out is not None and spec in cmd.text(out).split('\n'):
            return
        cmd.call(['config', '--add', 'remote.%s.fetch' % self.remote, spec], self.root)

    def _fetch(self):
        """Bring the remote's store into `local_ref`. Returns False when the remote has none."""
        why = ''
        for _ in range(3):
            r = cmd.call(['fetch', '--no-tags', '--no-write-fetch-head', '--quiet', self.remote,
                          '+%s:%s' % (self.remote_ref, self.local_ref)], self.root,
                         timeout=FETCH_TIMEOUT)
            if r.rc == 0:
                return True
            why = r.err or 'git fetch exited %s' % r.rc
            if "couldn't find remote ref" in why:
                return False
            if 'cannot lock ref' not in why and 'unable to update local ref' not in why:
                break
            time.sleep(random.uniform(0.05, 0.25))
        raise Failure('cannot fetch the backlog store from %s: %s' % (self.remote, why))

    def _local_tip(self):
        """`local_ref`'s commit, None when the ref does not exist; Failure when git cannot tell
        (a git ended from outside included: refs.ref_commit)."""
        tip, why = refs.ref_commit(self.local_ref, self.root)
        if tip is None:
            raise Failure('cannot read %s: %s' % (self.local_ref, why))
        return tip or None

    def load(self, rules):
        tip = None
        if self.remote and self.fetch:
            if self._fetch():
                self.ensure_refspec()
                tip = self._local_tip()
            else:
                cached = self._local_tip()
                if cached is not None:
                    # A write would start a new store on the remote without these records.
                    raise Failure('%s has no %s, but %s holds a store (%s) - push it there or '
                                  'move the local ref aside first'
                                  % (self.remote, self.remote_ref, self.local_ref, cached[:10]))
        else:
            tip = self._local_tip()
        records, raw = [], {}
        if tip:
            records, raw, why = sread.records_in_tree(self.root, tip, smod.STORE_DIR)
            if records is None:
                raise Failure('cannot read the backlog store at %s: %s' % (tip[:10], why))
        return sread.Snapshot(records, tip, self.describe(), rules, raw)

    def path_of(self, stem):
        return '%s/%s.md' % (smod.STORE_DIR, stem)

    def build(self, snap, writes, message):
        """The commit holding `writes` ({stem: text or None}) on top of snap.tip, or None when the
        tree would not change. No working tree or real index is touched."""
        tmp = kuser.mkdtemp('kit-backlog-')
        try:
            env = dict(cmd.scrubbed_env(), GIT_INDEX_FILE=os.path.join(tmp, 'index'))
            self._git(['read-tree', snap.tip] if snap.tip else ['read-tree', '--empty'], env=env)
            lines = []
            for stem, text in sorted(writes.items()):
                if text is None:
                    if snap.tip:                 # the null id is as long as the repo's hashes
                        lines.append('0 %s\t%s' % ('0' * len(snap.tip), self.path_of(stem)))
                    continue
                sha = cmd.text(self._git(['hash-object', '-w', '--no-filters', '--stdin'],
                                         stdin=text.encode('utf-8'))).strip()
                lines.append('100644 %s\t%s' % (sha, self.path_of(stem)))
            self._git(['update-index', '--index-info'], env=env,
                      stdin=('\n'.join(lines) + '\n').encode('utf-8'))
            tree = cmd.text(self._git(['write-tree'], env=env)).strip()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        if snap.tip:
            old_tree = cmd.text(self._git(['rev-parse', snap.tip + '^{tree}'])).strip()
            if old_tree == tree:
                return None
        args = ['commit-tree', tree] + (['-p', snap.tip] if snap.tip else []) + ['-F', '-']
        return cmd.text(self._git(args, stdin=message.encode('utf-8'))).strip()

    def publish(self, snap, writes, message):
        """The new tip; Conflict when the store is no longer at snap.tip. Refused, nothing built,
        when the store is the implicit default (default_store_refusal)."""
        if self.implicit:
            raise Refusal(slive.default_store_refusal(self.root))
        new = self.build(snap, writes, message)
        if new is None:
            return snap.tip
        expect = snap.tip or ''
        if not self.remote:
            old = snap.tip or '0' * len(new)
            r = cmd.call(['update-ref', '-m', message.split('\n', 1)[0], self.local_ref, new, old],
                         self.root)
            if r.rc == 0:
                return new
            try:
                now = self._local_tip()
            except Failure as exc:
                raise Failure('git update-ref %s failed (%s) and %s: the write may have landed - '
                              'read the store (`backlog.py check`) before retrying'
                              % (self.local_ref, r.err or 'exit %s' % r.rc, exc))
            if now == new:                       # rc None: timed out or killed after the move
                return new
            # A held lock (another writer mid-update) or a moved ref: the store is busy.
            if 'cannot lock ref' in r.err or now != snap.tip:
                raise smod.Conflict(r.err)
            raise Failure('git update-ref %s: %s' % (self.local_ref, r.err))
        r = cmd.call(['push', '--porcelain', '--quiet',
                      '--force-with-lease=%s:%s' % (self.remote_ref, expect),
                      self.remote, '%s:%s' % (new, self.remote_ref)], self.root,
                     timeout=FETCH_TIMEOUT)
        if r.rc != 0:
            said = '\n'.join(x for x in (r.err.strip(), cmd.text(r.out).strip()) if x)
            now, why = refs.ls_remote(self.remote, self.remote_ref, self.root,
                                      timeout=FETCH_TIMEOUT)
            if now is None:
                raise Failure(self._maybe_landed('the remote cannot be read', said, why))
            if now != new:
                # Porcelain status lines are on stdout, so `said` holds both streams.
                if now == expect and 'cannot lock ref' not in said:
                    raise Failure('push to %s failed: %s' % (self.remote, said))
                if not self._landed_under(new, said):
                    raise smod.Conflict(said)
        self._advance_cache(new)
        return new

    def _landed_under(self, new, said):
        """True when the push that reported failure landed `new` and another write followed it
        (`new` is an ancestor of the remote's store): the write succeeded and must not re-plan."""
        try:
            if not self._fetch():
                return False
            now = self._local_tip()
        except Failure as exc:
            raise Failure(self._maybe_landed('the store cannot be fetched', said, exc))
        if now is None:
            return False
        ok, why = refs.is_ancestor(new, now, self.root)
        if ok is None:
            raise Failure(self._maybe_landed('whether it landed cannot be told', said, why))
        return ok

    def _maybe_landed(self, what, said, why):
        """The ERROR for a failed push whose outcome cannot be read: the write may have landed."""
        return ('push to %s reported failure (%s) and %s (%s): the write may have landed - read '
                'the store (`backlog.py check` fetches it) before retrying'
                % (self.remote, said, what, why))

    def _advance_cache(self, new):
        """Move `local_ref` to `new` only as a fast-forward from the value it holds now (another
        writer in this repository may have moved it past `new`). Best effort: every read fetches."""
        try:
            cur = self._local_tip()
        except Failure:
            return
        if cur == new:
            return
        if cur is not None and refs.is_ancestor(cur, new, self.root)[0] is not True:
            return
        cmd.call(['update-ref', self.local_ref, new, cur or '0' * len(new)], self.root)

    def history(self, rid, snap):
        """`<short sha> <subject>` of the last commit that closed, retired or deleted `rid`, or
        None."""
        if not snap.tip:
            return None
        return sread._last_closing(self.root, rid, snap.tip)

    def removal(self, rid, snap):
        """The newest store commit that deleted `backlog/<rid>.md` (a Removal), or None."""
        if not snap.tip:
            return None
        return sread.last_removal(self.root, snap.tip, smod.STORE_DIR, rid)

    def uncommitted_removal(self, rid):
        """Always False: every write to a ref store is a commit."""
        return False

    def removed(self, snap):
        """`[(stem, Removal, last text)]` of every record a store commit deleted."""
        if not snap.tip:
            return []
        return sread.removed_records(self.root, snap.tip, smod.STORE_DIR)

    def touched(self, snap):
        """`{stem: epoch}` of the last store commit that wrote each record."""
        if not snap.tip:
            return {}
        return sread._touch_times(self.root, snap.tip, smod.STORE_DIR)

    def landed(self, main_branch):
        """`(rev, folder)` holding the published records without a fetch: the local ref."""
        return self.local_ref, smod.STORE_DIR
