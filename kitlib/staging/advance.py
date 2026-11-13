"""kitlib.staging.advance: an attempt's phases after its test, to the end (docs/modules/
staging-design.md "Phases and recovery").

Mover(...).advance(attempt) takes an attempt in one of record.LIVE and moves it on until it ends
or has to wait; a run that finds an attempt a dead run left (kitlib.staging.promote) calls the same
thing, so a recovery is the normal path from that phase:

  tested    `<remote>/<main>` is fetched (`_based`). Still the attempt's base: the landing lock
            is taken (below), and main is fetched and checked again under it, since the wait
            can be long and the daily run's own promotion moves main without that lock. Main
            moved on to an ancestor of the tip: behind (tested again next time). Main holds the
            tip already: done. Main has commits the tip lacks: diverged. The landing lock not
            given in time, or main not readable: still tested (the next run tries again).
  pushing   (a dead run's push) the same check under the landing lock, main holding the tip
            already being pushed; then kitlib.dailyfull.promote.promote: a fetch, `current` when
            main holds the tip already, else the one plain push (never forced, never past the
            pre-push hook) read back with ls-remote. promoted or current: pushed. The pre-push
            guard refusing (REFUSED_MARK in git's output: the kit hook's own refusal): refused,
            never tried again, unless the refusal is one of TRANSIENT (the hook could not read
            what it judges by), which is a failed push. diverged: diverged; off-staging: behind.
            A push that failed otherwise, or a main that cannot be read after it: tried again
            PUSH_TRIES times in all, then failed.
  pushed    main is the tip on the remote: the main checkout's local main is fast-forwarded where
            that is safe (`sync`, kitlib.landing.push.sync_checkout), then done. Its warnings are
            lines; they never undo the move.

The landing lock (kitlib.records.landing.LandingLock, with this process's pid) is held from before
the push to the end, so no landing is mid-merge while the main checkout's main moves. take()
refuses at once while anyone holds it, so it is polled every LOCK_POLL_S up to `lock_wait_s`;
a holder kitlib.proc says is gone is broken (break_lock, which refuses while a merge is in
progress), except a stuck landing's (kitlib.records.landing.is_stuck): that one is cleared by
hand only, as the lander's own rule says (kitlib.landing.lander held_by), so the attempt stays at
its phase. The caller has released its machine-lock slot before advance is called: the gate's
slot is never held while waiting on a landing.

Git reads go through the caller's `git(cwd, *args)` (kitlib.dailyfull.git). Nothing here prints.
"""
import os
import time

from kitlib.dailyfull import promote as P
from kitlib.landing import push as LP
from kitlib.records import landing as LL

PUSH_TRIES = 3              # pushes of one attempt before it ends failed
PUSH_PAUSE_S = 15           # between them
LOCK_WAIT_S = 900           # how long the landing lock is waited for
LOCK_POLL_S = 5
REFUSED_MARK = 'pre-push: refused'      # the kit pre-push hook's refusal (kitlib.guard.prepush)
# Its refusals that say only that the hook could not read what it judges by (a remote list, git's
# ancestry, an object list): a failed push, tried again, never a refused hold.
# It is kitlib.guard.prepush.TRANSIENT_PREFIX, copied: importing it would be an upward edge to
# guard, a higher layer than staging. tests/staging/test_advance.py TransientCopy keeps them equal.
TRANSIENT = REFUSED_MARK + ', cannot '


def _quiet(_line):
    pass


def sync_main(main, cfg, sha):
    """The main checkout's local main fast-forwarded to `sha` where safe: the landing's step 10
    (kitlib.landing.push.sync_checkout). [] or WARNING lines; a failure is a WARNING line."""
    ff = 'git -C "%s" merge --ff-only %s/%s' % (main, cfg.remote, cfg.main_branch)
    try:
        return list(LP.sync_checkout(main, cfg, cfg.main_branch, sha))
    except Exception as e:          # noqa: BLE001 - the move is made; a sync never undoes it
        return ['WARNING: the main checkout\'s %s was not fast-forwarded to %s (%s: %s); bring '
                'it there with: %s' % (cfg.main_branch, sha[:10], type(e).__name__, e, ff)]


class Mover(object):
    """The phases after the test for the repository whose main checkout is `main`. `cfg` is the
    config with the staging setting `<remote>/<main>` writes (staging on), `staging` the staging
    branch. The rest are stand-ins a test passes: `lock` (a LandingLock), `promote` (P.promote's
    signature), `sync` (sync_main's), `sleep` and `clock`."""

    def __init__(self, main, cfg, staging, git, say=None, lock=None, promote=None, sync=None,
                 sleep=time.sleep, clock=time.monotonic, lock_wait_s=LOCK_WAIT_S,
                 push_pause_s=PUSH_PAUSE_S):
        self.main, self.cfg, self.staging, self.git = main, cfg, staging, git
        self.say = say or _quiet
        self.lock = lock
        self.promote = promote or P.promote
        self.sync = sync or sync_main
        self.sleep, self.clock = sleep, clock
        self.lock_wait_s, self.push_pause_s = lock_wait_s, push_pause_s
        self.token = None

    # ---------------------------------------------------------------- the phases
    def advance(self, a):
        """Move attempt `a` on from its phase until it ends or waits: the lines to say."""
        lines = []
        try:
            if a.phase == 'tested' and not self._based(a, lines):
                return lines            # no wait for the landing lock when there is nothing to push
            if not self._take(a, lines):
                return lines
            if a.phase in ('tested', 'pushing') and self._based(a, lines):
                self._push(a, lines)
            if a.phase == 'pushed':
                more = self.sync(self.main, self.cfg, a.sha)
                lines += more
                a.set('done', why='; '.join(more) if more else None)
                lines.append('promote: DONE %s is at %s' % (self.cfg.main_branch, a.sha[:10]))
        finally:
            self._release(lines)
        return lines

    def _main_now(self):
        """`(sha, None)` of `<remote>/<main>` after a fetch, or `(None, why)`."""
        remote, m = self.cfg.remote, self.cfg.main_branch
        rc, _, err = self.git(self.main, 'fetch', remote, '--quiet')
        if rc != 0:
            return None, 'git fetch %s failed: %s' % (remote, err or 'exit %d' % rc)
        rc, sha, err = self.git(self.main, 'rev-parse', '--verify', '-q',
                                'refs/remotes/%s/%s' % (remote, m))
        if rc != 0 or not sha:
            return None, '%s/%s cannot be read: %s' % (remote, m, err or 'exit %d' % rc)
        return sha, None

    def _anc(self, a, b):
        return self.git(self.main, 'merge-base', '--is-ancestor', a, b)[0] == 0

    def _based(self, a, lines):
        """Phase tested or pushing: True when `<remote>/<main>`, fetched now, is still the
        attempt's base. Else the attempt moves on: main holding the tip already is done (tested)
        or pushed (pushing: a dead run's push landed); behind or diverged otherwise; or it stays
        at its phase when main cannot be read."""
        now, why = self._main_now()
        if now is None:
            lines.append('promote: WAITING - %s; the next run tries again' % why)
            return False
        base, sha, m = a.rec.get('base'), a.sha, self.cfg.main_branch
        if now == base:
            return True
        if self._anc(sha, now):
            held = '%s/%s already holds %s' % (self.cfg.remote, m, sha[:10])
            if a.phase == 'pushing':
                a.set('pushed', why=held)
                lines.append('promote: PUSHED - %s' % held)
            else:
                a.set('done', why=held)
                lines.append('promote: nothing to do - %s' % held)
        elif self._anc(now, sha):
            a.set('behind', why='%s/%s moved from %s to %s after the test' % (
                self.cfg.remote, m, (base or '?')[:10], now[:10]))
            lines.append('promote: BEHIND - %s; tested again next time' % a.rec['why'])
        else:
            a.set('diverged', why='%s/%s (%s) has commits that are not on %s (%s)' % (
                self.cfg.remote, m, now[:10], self.staging, sha[:10]))
            lines.append('promote: DIVERGED - %s' % a.rec['why'])
        return False

    def _push(self, a, lines):
        """Phase tested (based) or pushing: the push, tried up to PUSH_TRIES in all."""
        while True:
            err = []

            def push(args, cwd):
                rc, text = P._push(args, cwd)
                err.append(text)
                return rc, text
            a.set('pushing', tries=int(a.rec.get('tries') or 0) + 1)
            self._lock_phase('push')
            p = self.promote(self.main, self.cfg, a.sha, self.staging, self.git, push=push)
            said = '\n'.join(err)
            if p['status'] in P.OK:
                a.set('pushed', why=p['why'])
                lines.append(P.line(p).replace('daily: ', 'promote: '))
                return
            refusal = next((l.strip() for l in said.splitlines() if REFUSED_MARK in l), None)
            if refusal is not None and TRANSIENT not in refusal:
                a.set('refused', why=refusal)
                lines.append('promote: REFUSED by the pre-push guard - %s; promotion is held '
                             'until the owner acts' % a.rec['why'])
                return
            if p['status'] == 'diverged':
                a.set('diverged', why=p['why'])
                lines.append('promote: DIVERGED - %s' % p['why'])
                return
            if p['status'] == 'off-staging':
                a.set('behind', why=p['why'])
                lines.append('promote: BEHIND - %s; tested again next time' % p['why'])
                return
            why = refusal or p['why']
            if a.rec['tries'] >= PUSH_TRIES:
                a.set('failed', why='%d pushes: %s' % (a.rec['tries'], why))
                lines.append('promote: FAILED - %s' % a.rec['why'])
                return
            lines.append('promote: push %d of %d did not land (%s); trying again' % (
                a.rec['tries'], PUSH_TRIES, why))
            self.sleep(self.push_pause_s)

    # ---------------------------------------------------------------- the landing lock
    def _take(self, a, lines):
        """True once this process holds the landing lock; False (the attempt left at its phase)
        when it was not given within lock_wait_s."""
        if self.lock is None:
            self.lock = LL.LandingLock.for_config(self.main, self.cfg)
        label = ['promote %s->%s at %s' % (self.staging, self.cfg.main_branch, a.sha[:10])]
        end = self.clock() + self.lock_wait_s
        said = False
        while True:
            try:
                self.token = self.lock.take(label, pid=os.getpid())['token']
                return True
            except LL.Busy as e:
                rec = e.record or {}
                if LL.is_stuck(rec):
                    lines.append('promote: WAITING at %s - the landing lock is held by a stuck '
                                 'landing (%s), which is cleared by hand only, as its STUCK '
                                 'report says; the next run tries again' % (a.phase,
                                                                          LL.describe(rec)))
                    return False
                if rec.get('token') and self.lock.holder_state(rec) == 'dead':
                    broke, why = self.lock.break_lock()
                    if broke:
                        lines.append('promote: broke the landing lock of a run that is gone (%s)'
                                     % LL.describe(rec))
                        continue
                busy = str(e)
            except LL.LockError as e:
                busy = str(e)
            if self.clock() >= end:
                lines.append('promote: WAITING at %s - the landing lock was not free in %d s (%s); '
                             'the next run tries again' % (a.phase, self.lock_wait_s, busy))
                return False
            if not said:
                self.say('promote: waiting for the landing lock (%s)' % busy)
                said = True
            self.sleep(LOCK_POLL_S)

    def _lock_phase(self, phase):
        try:
            self.lock.set_phase(self.token, phase)
        except LL.LockError:
            pass                    # the phase is for readers; the lock is still held

    def _release(self, lines):
        if self.token is None:
            return
        token, self.token = self.token, None
        try:
            self.lock.release(token)
        except LL.LockError as e:
            lines.append('WARNING: the landing lock could not be released: %s' % e)
