"""One branch landed on main (docs/gate-landing.md "Landing").

`land()` runs in the repository's MAIN checkout, on its main branch:

  1  usage       --expect-main (optional) and every --expect-tip are full hashes, one tip per
                 branch, the branches distinct
  2  refusals    not the main checkout, HEAD not on main, no such branch, a branch is main,
                 more branches than "landing".batch_max (read from the config at the main
                 checkout's HEAD): REFUSED, nothing changed
  2b route      where the merged gate (7) will run, main or a load node, chosen before the
                 booking (kitlib.loadnode.landroute.plan; docs/load-nodes.md "A landing's merged
                 gate on a node"), its `gating here: ...` or `gating on load node ...` line said
                 unless it is quiet (a kit checkout, no registry, no node opted in)
  3  turn        ONE booking in the machine lock (kitlib.lock.run.book): a gate run of the
                 config's "gate".kind, as land-<kind> when the kinds table has one (land-test:
                 4 cores), for <repo>/<branch[+branch...]>, holding the resource
                 `landing:<git common dir>` at priority "land" ("The queue" below); routed to a
                 node, with cores=1 (the landing's own work; the node runs the gate). At once, or
                 waited for up to --wait seconds (default "landing".wait_s); not given: the lock's
                 code and a NOT RUN line, nothing changed. The merged gate (7) runs in this slot
                 and books nothing more, unless a node-routed landing falls back to main (7)
  4  lock        fetch <remote>, then the landing lock file (kitlib.records.landing), with what is
                 left of --wait; not had: BUSY. Held at phase stuck: BUSY at once; held by a
                 process that is surely gone: broken and taken unless something is unpushed (one
                 gone at phase push with <remote>/<main> moved on since the take: BUSY naming the
                 likely landed push, the fast-forward, the next landing and the break)
  5  follow      fetch again under the lock; HEAD still on main, or REFUSED. The base is <remote>/<main> as fetched: a local main
                 only behind it (nothing unpushed) is fast-forwarded first (`git merge --ff-only`,
                 HEAD on main, a clean checkout); a local main with commits the remote lacks, or
                 one the fast-forward cannot move: REFUSED, as before. When the base is not the
                 commit the config was read at, its config is read at the base (it must name the
                 same main branch, remote and landing lock, and still take this landing). With
                 --expect-main given and not the base, it says `main moved on from X to Y; merging
                 onto Y`; it never refuses for that. Then the locked checks: the branch tip ==
                 --expect-tip, a clean main checkout, an empty stash stack (kitlib.gate.stash: the
                 repository's, whichever checkout pushed), no merge in progress, the branch not
                 already in main, every branch
                 commit that changes an owner-only path the landing would change authored by the owner
                 (kitlib.ownerfiles.rules; rules of the base merged with the tip's), and the
                 branch's history free of the backlog store's root commit (`ref` and `branch`
                 stores; kitlib.backlog.at_landing). Then the tip gate check
                 (Lander.tip_gate_check): a base whose "gate" section has no step that is not
                 manual is refused first (nothing could gate a tip or the merge); then every tip
                 has a gate-OK record of exactly that commit written by a default-mode pass, a
                 `gate.py --full` pass (kitlib.records.gate_ok.verify_full) or a docs-only pass
                 the base's config takes (Lander.select_refusal: the record's diff covers the
                 landing's; a default-mode record lands any change, a docs-only one only a change
                 docs-only under the base's "gate".docs_only whose steps all passed in it; no
                 branch needs a full run to land, ARCHITECTURE.md "What a landing runs"), or by
                 none when the tip's latest red whole run (--full or default-mode) has a FLAKE
                 targeted rerun (kitlib.gate.rerun.verify_flake);
                 a tip a test failed on, that failed twice on its branch, never lands
                 (rerun.twice_refusal);
                 a miss names the `gate.py -C <worktree>` command that gates it, and, when the
                 kit lands itself and the tip does not contain the base, says to merge
                 <remote>/<main> into the branch first (its own gate.py may predate the record's
                 `full` key). Then the
                 strict check (Lander.strict_check; docs/gate-landing-policy.md "Strict paths"): a branch that changes a path in STRICT_DEFAULTS as
                 the base holds it lands alone, with a verdict queued for its tip
                 (kitlib.landing.verdicts) by a strong-tier reviewer who is not its author (a
                 verdict carried to the tip, tools/verdict.py carry, counts only when
                 kitlib.landing.carry passes it again here against the base); its
                 merged gate then runs fresh (nothing reused). Then the verdict check
                 (Lander.verdict_check): every other branch, each of a batch, lands only with a
                 verdict queued for its tip by any reviewer who is not its author (a High-risk
                 one: a strong-tier reviewer, verdicts.tier_why), a carried one
                 checked the same way. Any miss: REFUSED, the lock released, nothing
                 merged. Last, the scope check (Lander.scope_check): every path the branch changes
                 in a scope of a record it holds (a PM data store member `<area>/<path>` read
                 as <path> when the repository has no top-level <area> at the merge base or
                 the tip), in SCOPE_ALWAYS or in a folder store's folder; a miss is a WARNING
                 after the LANDED line while SCOPE_REFUSES is False, else REFUSED as above
  5b tree        the landing tree (prepare_tree): a detached linked worktree the kit owns, locked
                 with the reason TREE_LOCK, at <main checkout>/<worktrees_dir>/_landing
                 (Lander(tree=) names another place). Made the first time; reused when clean,
                 checked out at the base; recreated, with a line `the landing tree <path> <why>:
                 recreated`, when it is gone, dirty, mid-merge or not a work tree of its own. A
                 folder there that is not this tree (not listed by git, or listed and not the
                 locked detached tree): REFUSED, `move it aside`, the folder left as it is. The
                 merge, the merged gate and every check of the merged tree happen in this tree;
                 the main checkout is never merged into, reset or gated
  6  merge       `git merge --no-ff <tip>` in the landing tree; a conflict is aborted there (a
                 failed abort leaves only the tree dirty: the next landing recreates it), the
                 lock released: FAILED, the verdict line `CONFLICT <branch> with main <base>:
                 the author merges main`. Main is never moved
  7  gate        the landing tree gated in the slot of step 3 (kitlib.gate.runner.merged_gate,
                 tree=, slot=) with the config of the base: the steps a default-mode gate.py
                 selects for the paths the merge changes, the smoke steps and setup, each in its
                 cmd form (ARCHITECTURE.md "What a landing runs"); a step that
                 passed in the branch gate
                 of exactly this tree may count without running (kitlib.gate.reuse; never with
                 --fresh; a batch reuses per step like one branch; one branch also
                 from its tip's --full record of this tree, reuse.same_tree). A merge that is docs-only under the base's config
                 (not --full) runs only the "gate".docs_only steps (docs/gate-run.md "Docs-only
                 changes"). A merge whose diff changes a gate config file (reuse.config_hits)
                 runs --full once (Lander.config_escalate: every step that is not manual, each
                 its full_cmd, no reuse). Red, or a gate where nothing ran: NOT LANDED, the
                 merge dropped (it is only in the landing tree; the landing never moves main),
                 the lock released: FAILED / REFUSED.
                 Routed to a node (2b): main gates it instead when the tip's --full record has
                 the merge's tree (landroute.same_tree_why), when no node qualifies at the turn
                 (landroute.recheck; another that does takes it), or when nothing ran on the node
                 (landroute.Fallback): `gating here: <why>`, the 1-slot run released, then a
                 full run of the kind booked as in step 3 with no wait limit, and the gate run
                 in it as above. Else the land job runs it there (landroute.run_on_node, docs
                 included): a pass is this step's green; any other end is NOT LANDED, never
                 gated again on main, and so is any exception.
                 The base's config (step 4, follow) names another "gate".kind than step 3
                 booked: on main, `the base <b> books "gate".kind <k>, not <k0> ...`, the slot
                 released first, then the base's kind booked at its own size with no wait limit
                 (rebook_kind), the lock file still held; the lock refusing it is NOT LANDED.
                 Routed to a node, recheck asks for the base's kind's slots (route_need; a kind
                 that cannot be sized gates here as above), and the 1-slot run stays
  7b unchanged   green: the landing tree's HEAD still the merge, no tracked change, no untracked
                 file there, the stash stack still empty; else NOT LANDED as on red and nothing
                 pushed (the pushed commit is the gated one). What the owner does in the main
                 checkout meanwhile (a commit, an edit, a push) reaches neither the gated tree nor
                 the pushed commit
  8  record      green: the gate-OK record for the merge commit (pre-push reads it), its
                 config_rev the base, its `full` whether the merged gate ran --full, its `mode`
                 "docs" when it ran only the docs-only steps, its `ran_on` the node, host key
                 and job when the gate ran on a load node
  9  push        `git push <remote> <merge>:refs/heads/<main>` from the main checkout, never
                 forced. Someone pushed <remote>/<main> to a commit without the merge while the
                 gate ran: the push is refused, NOT LANDED (FAILED, `nothing landed`), the lock
                 released; so is a push git refuses (exit 1 with git's own `! [rejected]` or
                 `! [remote rejected]` line, PUSH_REFUSED) while <remote>/<main> is the base,
                 and a push that never connected (push_never_connected: exit 128, only
                 `fatal: unable to access '<url>': ` with a curl connect error,
                 PUSH_NEVER_CONNECTED) unless <remote>/<main> shows the merge
  10 verify      `git ls-remote` must show the merge on <remote>/<main>; then the tracking ref
                 is fetched, the main checkout's main brought to the merge (sync_main: a
                 fast-forward when HEAD is on main, main has nothing of its own and no local
                 change is in the way; else a WARNING naming the command, main left as it is),
                 the lock file and the slot released: OK (LANDED)
  11 close       after the lock: every record the `ref` or `branch` store still holds pulled by
                 the branch is closed with reason `landed <merge>`, one store commit each
                 (kitlib.backlog.at_landing), and so is every pulled record a `Closes: <ID>`
                 trailer in `<tip> --not <the main it merged onto>` names, whoever pulled it (a
                 pull made in the main checkout records main; a live holder outside the batch is
                 a WARNING: tell its builder). A close that fails, or an exception or Ctrl+C here,
                 is a WARNING naming the command to finish it; the landing stands
  12 cleanup     then each landed branch goes (kitlib.landing.cleanup; docs/gate-landing-policy.md "Branch
                 cleanup"): its remote branch, its worktree (kitlib.worktree.remove, never
                 forced) and its local branch (`git branch -d`). What main does not contain, a
                 worktree with work in it or in use, and anything git refuses are kept and named;
                 an exception or Ctrl+C here is a WARNING naming `land.py --sweep-landed`. The
                 landing stands whatever is kept. --keep-branch (keep_branch) skips it
  13 mirror      once the slot is released too (Lander.run, nothing held): the merge pushed to
                 each opted-in load node's mirror of main (kitlib.loadnode.mirror.refresh, at
                 most its time cap); its lines go before the LANDED line, a failure or an
                 exception or Ctrl+C there is a WARNING, and the landing stands
  14 feed        once the result records are finished and in the land ledger (Lander.run): one
                 event per branch, built from its land record, posted to this machine's hub
                 (Lander.post_landing; kitlib.landing.feed) with a short timeout; a hub that is
                 down, older than the route, slow or without an agent token is a WARNING said as
                 progress, the event dropped, and the landing, its lines and its exit code stand

An exception or Ctrl+C under the lock (recover): before the push the lock is released and the
merge dropped (a merge in progress in the landing tree aborted, best effort), main untouched, and
KeyboardInterrupt is raised again (exit 130); one that ends STUCK is returned as STUCK (exit 3)
whatever raised it. After the push is verified (phase pushed) the landing is LANDED whatever stops
it: a lock release that fails, or an exception or Ctrl+C there, is LANDED with a WARNING (exit 0).
The slot of step 3 is released whenever the landing ends (Lander.run): with the lock file, or at
the end; STUCK included (the stuck lock file keeps the next landing out). Exit 9
(LANDED_UNGATED) is retired: no landing produces it (docs/gate-run.md "Exit codes").

A landing's verdict is its exit code and its LANDED / NOT LANDED / CONFLICT / STUCK line; a `GATE
OK` line appears only in a landing that LANDED (a green merged gate that is not landed is
reworded).

STUCK is only a push that may have landed and cannot be told: interrupted, timed out, died (exit
128, except a push that never connected, push_never_connected: NOT LANDED), ended with exit 1 without git's refusal line (no message: a git ended from outside,
refs.KILLED_SHAPE; or only a stray warning) or reported done while <remote>/<main> is still the
base, or with <remote>/<main> unreadable. A push git refused (exit 1 with git's own `! [rejected]`
or `! [remote rejected]` line, PUSH_REFUSED) while <remote>/<main> is still the base published
nothing: NOT LANDED (FAILED), the lock released, as on a red merged gate. The landing lock stays held at phase
`stuck` so no other landing starts on top of it, and the message says what is left and the
commands that clear it. Nothing is ever reset: the main checkout was never merged into.

The queue: a landing is ONE entry in the machine lock's queue (step 3; kitlib.lock.run.book with
`resource=queue_key(<git common dir>)`, `priority="land"`). The resource lets one landing of a
repository hold at a time, every checkout of it sharing the key; the others wait in the machine
lock's queue, landings ahead of ordinary waiting runs. `land.py --queue` (queue()) lists the
entries of priority "land". The lock file (step 4) stays: the stuck record, a gone holder's break,
and a landing of a tool outside the machine lock. A node-routed landing that falls back to main
(step 7) gives the resource up while it books again, holding the lock file: the next landing of
the repository may then book and wait at the lock file, with the holder waiting in the queue
behind it. That landing sees it (Lander.yield_turn), releases its run and books again, behind the
holder, with what is left of its --wait, at most once a look (poll_s). That booking not given in
time is NOT RUN with the machine lock's exit code, not BUSY.

`land.py --check-strict BRANCH` (check_strict()) is the strict check's dry run: read-only, no lock
and no verdict, it says whether each branch changes a strict path (Lander.strict_paths).

A batch (several branches, one --expect-tip each, merged in the order given): every branch passes
the locked checks, and no tip may contain another; a branch that changes a strict path or a gate
config file (reuse.config_hits) is refused in a batch (it lands alone). The branches are merged
one after another with --no-ff in the landing tree, the merged tree is gated once against the
base, and the last merge is pushed. A named batch is all or nothing: a conflict on any branch
aborts that merge and drops the earlier ones; a red gate drops them all (main never held them).
Each branch gets its own result record, which names the batch (`batch`).

A submitted landing (kitlib.landing.submit; `land.py --submit`) is this landing run by a detached
process: its records are written `running` before it starts, and every outcome finishes them.
At its turn (the booking given) it reads its first record (submit.turn): folded into another
landing's batch, it says FOLDED and ends, writing nothing more (that landing finishes its
records); folded into one that still runs, it gives the turn back and books again until that one
lands them or gives them back; else it marks the record turn_at, so no landing folds it from
then on. A booking that fails is checked the same way before its NOT RUN or REFUSED is written.

Folding (docs/gate-landing.md "Folding queued submissions"), on by default ("landing".batch_max, default
5; a .kit.json that sets it to 1 folds nothing): a submitted landing whose locked checks
passed, that changes no strict path and no gate config file, was not given back from a batch
itself, and has room under that batch_max, folds the submissions queued behind it into its
batch, oldest first (Lander.fold_queued): whole submissions run with the same flags, each checked
with the landing's own branches by the locked checks a batch passes, then the whole batch again
(a miss gives them all back). Its own branches merge first. A folded batch releases or drops
units, never refusing the landing for them: a folded submission that does not merge cleanly is
given back, dropped (fold_dropped: it lands alone, at its own turn), and the rest merged again; a
red merged gate gives back every folded submission dropped and gates the landing's own branches
alone, in the same slot; any other outcome that does not land, an exception or Ctrl+C included,
gives them back to land at their own turn.
"""

import os
import time

from kitlib import gate as G
import kitlib.config as kconfig
from kitlib.config import policy as kpolicy
from kitlib.config.types import ConfigError
from kitlib.gate import gitstatus, result, reuse, runner, stash
from kitlib.git import cmd, refs, repo
from kitlib.lock import kinds as lockkinds
from kitlib.lock import queue as lockqueue
from kitlib.lock import run as lockrun
from kitlib.lock.store import EXIT_USAGE, LockError, Store
from kitlib.records import gate_ok
from kitlib.records import landing
from kitlib.records import place
from kitlib.landing.checks import Checks, FETCH_TIMEOUT, STRICT_SHOWN, _what
from kitlib.landing.merge import Merge
from kitlib.landing.mergedgate import MergedGate, _not_landed
from kitlib.landing.push import Push, _Stuck
from kitlib.landing.after import After


POLL_S = 5.0


def submitmod():
    """kitlib.landing.submit, imported late: it imports this module late in turn."""
    from kitlib.landing import submit
    return submit


def _as_list(x):
    """A branch or tip given alone, or several (a batch), as a list."""
    return [x] if x is None or isinstance(x, str) else list(x)


def queue_key(common):
    """The resource a landing holds in the machine lock (kitlib.lock.run.book `resource=`): its
    repository's git common dir, so every checkout of one repository shares one key and one
    landing of it holds at a time."""
    return 'landing:' + os.path.normcase(os.path.abspath(common))


class Lander(Checks, Merge, MergedGate, Push, After):
    """One landing of a branch, or a batch: `branch` and `expect_tip` are each a string or a list
    of the same length. `merged_gate(lander) -> Outcome` replaces the merged-tree gate (tests);
    `git_env` is the environment git's writes get (None: this process's, scrubbed); `book_kw`
    goes to kitlib.lock.run.book (its `store`: the machine lock's, default the machine store);
    `book` replaces kitlib.lock.run.book (tests); `lock_kw` goes to LandingLock (tests pass
    process probes). `wait_s` None: the config's "landing".wait_s; one budget covers the booking
    and the lock file. `keep_branch` True: no branch cleanup after the landing (step 12).
    `fresh` True: the merged gate reuses no step (kitlib.gate.reuse); a branch that changes a
    strict path sets it itself (strict_escalate). No caller makes a landing --full: a merge whose
    diff changes a gate config file does (config_escalate).
    `tree`: where the landing tree is (step 5b); None: <main checkout>/<worktrees_dir>/_landing.
    `submitted` (kitlib.landing.submit.restore): the detached landing of a --submit, a dict with
    `runs` (the result.Run per branch whose running records the submission wrote; every outcome
    finishes them), `output` (the file this process's output goes to) and `replaced` (the land
    records those running records replaced).
    `node_route`: where the merged gate runs (step 3), an object with kitlib.loadnode.landroute's
    plan, route_line, recheck, same_tree_why, run_on_node and Fallback (None: that module).
    `hub`: the client the landing feed is posted with (post_landing; None: this machine's hub)."""

    def __init__(self, root, branch, expect_main, expect_tip, summary=None,
                 wait_s=0, say=None, book_kw=None, git_env=None, lock_kw=None,
                 merged_gate=None, log_dir=None, poll_s=POLL_S, pid=None, keep_branch=False,
                 fresh=False, book=None, submitted=None, tree=None, node_route=None, hub=None):
        self.root = root
        self.hub = hub              # the hub client the feed post uses (None: this machine's hub)
        self.closed = {}            # branch -> the record ids close_pulled closed for it
        self.node_route = node_route    # None: kitlib.loadnode.landroute, imported when routed
        self.route = None           # the landroute.Route plan_route chose before the booking;
                                    # None again once a fallback gates on main (rebook_here)
        self.holder = None          # the landing lock file's record when another holds it
        self.yielded_to = None      # the holder yield_turn last gave the turn back to
        self.booked_kind = None     # the "gate".kind of the last booking (book_slot)
        self.tree_given = tree      # Lander(tree=), or None: the default place (tree_path)
        self.tree = None            # the landing tree's path, once prepare_tree placed it
        self.branches, self.tips = _as_list(branch), _as_list(expect_tip)
        self.branch = ', '.join(str(b) for b in self.branches)     # how the landing is named
        self.expect_main_given = expect_main or None    # optional; never a refusal
        self.expect_main = None     # the base: the commit the config was read at, then (step 5)
                                    # <remote>/<main> as fetched under the lock
        self.summary, self.wait_s, self.poll_s = summary, wait_s, poll_s
        self.full = False           # config_escalate: the merged gate runs --full
        self.say = say or (lambda line: None)
        self.book_kw, self.git_env, self.lock_kw = book_kw, git_env, dict(lock_kw or {})
        self.book_fn = book         # None: kitlib.lock.run.book, looked up when the landing books
        self.slot = None            # the machine-lock Slot the landing holds (book_turn)
        self.store = None
        self.deadline = None        # the --wait budget's end, set when the landing books
        self.merged_gate_fn, self.log_dir = merged_gate, log_dir
        self.pid = os.getpid() if pid is None else pid
        self.lock = self.token = self.cfg = self.merge = None
        self.merges = []            # the merge commits made, in order; self.merge is the last
        self.phase = 'before'
        self.gate_logs = None       # the merged gate's log folder, once it ran
        self.gate_out = None        # the merged gate's Outcome, once it ran
        sub = submitted or {}
        self.submitted = submitted is not None
        # kitlib.gate.result.Run per branch: once the locked checks passed; a submitted landing's
        # from its start (the submission wrote them running)
        self.result_runs = list(sub.get('runs') or [])
        self.own_runs = set(rr.run_id for rr in self.result_runs)
        # a direct landing's result.Run per branch, made when it joins the landing queue (_run,
        # before book_turn) so started_at says so, as a submitted landing's does; started, and
        # moved into result_runs, only once the locked checks passed (_locked)
        self.queued_runs = []
        self.output_log = sub.get('output')
        self.replaced = [r for r in (sub.get('replaced') or []) if isinstance(r, dict)]
        self.keep_branch = keep_branch  # True: no branch cleanup after the landing
        self.cleaned = False        # the branch cleanup ran (clean_up)
        self.synced = False         # sync_main ran (the main checkout brought to the merge)
        self.cleanup = None         # its facts: {branch: kitlib.landing.cleanup entry, or None}
        self.fresh = fresh          # True: the merged gate reuses no branch-gate step
        self.escalated = False      # config_escalate made the merged gate --full
        self.prior = None           # result records naming the tips, read before this landing
                                    # writes its own (_locked); None: reuse nothing
        self.tip_records = {}       # tip -> the gate-OK record tip_refusal landed it on (none for
                                    # a FLAKE rerun): the merged gate's input tree (reuse.planner)
        self.landed_at = None       # the remote's main once verified to hold the merge: the merge,
                                    # or a commit on top of it pushed right after
        self.scope_notes = []       # scope-check NOTE lines, said before the LANDED line
        self.scope_warnings = []    # scope-check misses while SCOPE_REFUSES is False, said after it
        self.scope_reads = {}       # the stores the scope check read once per landing: key -> read
        self.strict_hits = {}       # branch -> the strict paths it changes (strict_check)
        self.strict_review = None   # the verdict entry a strict branch lands with (strict_check)
        self.strict_carry = None    # its carry facts, checked again here, when it was carried
        self.flags = sub.get('flags')   # a submitted landing's flags (submit.flags_of), or None
        self.anchor_run_id = self.result_runs[0].run_id if self.result_runs else None
        self.folds = []             # the submissions folded into this batch: {unit, runs}, in order
        self.was_dropped = False    # a batch gave this submission back dropped: it lands alone
        self.folded_exit = False    # another landing folded this one: it ends writing nothing

    @property
    def pairs(self):
        return list(zip(self.branches, self.tips))

    def git(self, *args, timeout=cmd.GIT_TIMEOUT, cwd=None):
        """git `args` in `cwd` (default the main checkout); the landing tree's commands pass
        cwd=self.tree."""
        r = cmd.call(list(args), cwd or self.root, timeout=timeout, env=self.git_env)
        return r

    def ref(self, name):
        sha, _ = refs.resolve(name, self.root)
        return sha

    @property
    def checkout_branch(self):
        """The main checkout's branch: the main branch, always. A landing onto a staging branch
        never moves it (the promotion is its only writer; docs/gate-landing.md "Staging branch")."""
        return self.cfg.main_branch

    @property
    def base_branch(self):
        """The branch this landing merges onto and pushes: the work base (kitlib.config.work_base),
        the staging branch when `"staging".branch` is set, else the main branch."""
        return kconfig.work_base(self.cfg)

    @property
    def staging(self):
        """True when the base is a staging branch, not the main checkout's main branch."""
        return self.base_branch != self.checkout_branch

    def main_tracking(self):
        """refs/remotes/<remote>/<main>: where the staging setting is read (read_staging, follow)."""
        return 'refs/remotes/%s/%s' % (self.cfg.remote, self.checkout_branch)

    def read_staging(self, head):
        """The first read's staging setting from <remote>/<main> as last fetched, over the main
        checkout's own (kitlib.config.overlay_staging), when that ref is another commit than
        `head`. A config there that cannot be read leaves the checkout's own: follow() reads it
        again after the fetch, and refuses there."""
        src = self.ref(self.main_tracking())
        if src and src != head:
            try:
                kconfig.overlay_staging(self.cfg, src)
            except ConfigError:
                pass

    @property
    def main_ref(self):
        return 'refs/heads/%s' % self.checkout_branch

    @property
    def origin_ref(self):
        return 'refs/remotes/%s/%s' % (self.cfg.remote, self.base_branch)

    def fetch(self):
        r = self.git('fetch', '--quiet', '--no-tags', self.cfg.remote, timeout=FETCH_TIMEOUT)
        if r.rc != 0:
            return 'git fetch %s failed: %s' % (self.cfg.remote, r.err or 'exit %s' % r.rc)
        return None

    def usage(self):
        if len(self.tips) != len(self.branches):
            return 'one --expect-tip per branch: %d branch(es), %d tip(s)' % (
                len(self.branches), len(self.tips))
        given = [('--expect-main', self.expect_main_given)] if self.expect_main_given else []
        for flag, sha in given + [('--expect-tip', t) for t in self.tips]:
            if not (isinstance(sha, str) and refs.SHA_RE.fullmatch(sha)):
                return '%s must be a full commit hash, not %r' % (flag, sha)
        for b in self.branches:
            if not b or not isinstance(b, str) or refs.bad_rev(b):
                return 'the branch to land must be a branch name, not %r' % (b,)
        if len(set(self.branches)) != len(self.branches) or len(set(self.tips)) != len(self.tips):
            return 'a batch names each branch and each tip once'
        return None

    def preconditions(self):
        """The refusals before the landing joins the queue, or None. A landing with no result
        folder (kitlib.gate.result.result_dir: no dev_drive in the machine config) is refused:
        its land records, which the session waiting on it reads, could not be written."""
        place = repo.classify(self.root)
        if place.kind != repo.MAIN:
            return ('%s is not a main checkout (%s): a landing runs in the main checkout, on its '
                    'main branch' % (self.root, place.kind))
        self.root = place.top
        try:
            result.result_dir(self.root)
        except result.ResultError as e:
            return 'no result folder for the land records: %s' % e
        head = self.ref('HEAD')
        if not head:
            return 'the main checkout %s has no HEAD commit' % self.root
        try:
            self.cfg = kconfig.load_at(self.root, head)
        except ConfigError as e:
            return 'the config of main %s: %s' % (head[:10], e)
        self.read_staging(head)
        self.expect_main = head     # until step 5 moves it to <remote>/<main>
        return self.config_refusal()

    def config_refusal(self):
        """The refusals self.cfg decides (main branch checked out, batch_max, the branches):
        None or a refusal."""
        current, why = refs.current_branch(self.root)
        if current != self.checkout_branch:
            return 'the main checkout is on %s, not %s' % (current or why or '(detached HEAD)',
                                                         self.checkout_branch)
        most = self.cfg.section('landing')['batch_max']
        if len(self.branches) > most:
            return 'a batch of %d branches is more than "landing".batch_max (%d)' % (
                len(self.branches), most)
        for b in self.branches:
            if b in (self.base_branch, self.checkout_branch):
                return 'the branch to land is %s itself' % b
            if not self.ref('refs/heads/%s' % b):
                return 'no branch %s in this repository' % b
        return None

    def queue_store(self):
        if self.store is None:
            self.store = (self.book_kw or {}).get('store') or Store()
        return self.store

    def wait_budget(self):
        wait_s = self.wait_s if self.wait_s is not None else self.cfg.section('landing')['wait_s']
        return max(0, wait_s or 0)

    def book_turn(self):
        """Step 3: the landing's ONE booking in the machine lock, waited for up to the --wait
        budget: a gate run of the config's "gate".kind (its land kind: land_kind) for
        <repo>/<branches>, holding the
        resource queue_key(<git common dir>) at priority "land"; with cores=1 when the plan
        routed the merged gate to a load node. The merged gate runs in this slot (self.slot), or
        a node-routed landing's fallback books again (rebook_here). Raises
        kitlib.lock.store.LockError when the lock does not give it, and landing.LockError when the
        landing lock's place cannot be worked out."""
        self.lock = landing.LandingLock.for_config(self.root, self.cfg, **self.lock_kw)
        wait_s = self.wait_budget()
        self.deadline = time.monotonic() + wait_s
        self.book_slot(wait_s, cores=1 if self.on_node() else None)

    def land_kind(self, kind):
        """The lock kind a landing of "gate".kind `kind` books: `land-<kind>` (its retired name
        read as the kind it names now) when the lock's kinds table (book_kw's kinds_table, else
        the store's kinds file) has it, else `kind`. A table that cannot be read: `kind`, whose
        booking then fails closed as any run's does."""
        path = self.queue_store().kinds_path
        table = (self.book_kw or {}).get('kinds_table')
        if table is None:
            try:
                table = kpolicy.kinds_table(path)
            except ConfigError:
                return kind
        land = 'land-%s' % lockkinds.canonical(kind, path)
        return land if land in table else kind

    def book_slot(self, wait_s, cores=None):
        """Book the landing's run into self.slot: the land kind of the gate kind (land_kind), the
        landing resource, priority "land", waiting up to `wait_s` (None: no limit; 0: now or
        never), with `cores` cpu slots (None: the kind's size, or book_kw's). self.booked_kind
        keeps the "gate".kind, which rebook_kind and route_need compare with the base's. Says its
        held line. Raises LockError."""
        kw = dict(self.book_kw or {}, store=self.queue_store(), gate=True,
                  resource=queue_key(self.lock.common), priority='land', wait_s=wait_s,
                  no_wait=wait_s == 0, note='landing: %s' % self.branch)
        if cores is not None:
            kw['cores'] = cores
        if self.submitted:
            kw['detached'] = True   # its launcher's ancestry is in ORIGIN_ENV (kitlib.landing.submit)
        book = self.book_fn or lockrun.book
        kind = self.cfg.section('gate')['kind']
        self.slot = book(self.land_kind(kind), place.repo_name(self.cfg, self.root),
                         place.slug_of('+'.join(self.branches)), **kw)
        self.booked_kind = kind
        self.say(self.slot.held_text())

    def rebook_here(self, why):
        """A node-routed landing's merged gate falls back to main after its booking: says
        `gating here: <why>`, releases the 1-slot run first (booking the full kind while holding
        it may wait on itself: a kind of every slot, an owner whole hold, a gpu pool), then books
        the full kind as book_turn does, with no wait limit. The landing lock file stays held, so
        no other landing merges meanwhile; the next landing in the queue may book, and it gives
        the turn back (yield_turn). Raises LockError."""
        self.say('gating here: %s' % why)
        self.say("releasing the landing's 1-slot run and booking a full %s run for main's "
                 "merged gate, waiting with no limit"
                 % self.land_kind(self.cfg.section('gate')['kind']))
        self.route = None
        self.drop_slot(0, 'done')
        self.book_slot(None)

    def take(self):
        """Step 4, the slot held: None when this landing holds the landing lock file; else why not
        (BUSY), once what is left of the --wait budget ran out. Raises landing.LockError or
        ConfigError (REFUSED)."""
        said = False
        while True:
            why, final = self.try_take()
            if why is None:
                return None
            left = (self.deadline or 0) - time.monotonic()
            if final or left <= 0:
                return why
            if self.yield_turn(left):
                time.sleep(max(0.01, min(self.poll_s, left)))   # one booking a look, at most
                continue
            if not said:
                said = True
                self.say('waiting up to %d s for the landing lock: %s' % (left, why))
            time.sleep(max(0.01, min(self.poll_s, left)))

    def try_take(self):
        """One look: `(None, False)` taken, `(why, final)` not; final: waiting cannot help."""
        try:
            rec = self.lock.take(self.branches, pid=self.pid)
        except landing.Busy as e:
            self.holder = e.record
            return self.held_by(e.record)
        self.holder = None
        self.token = rec['token']
        return None, False

    def turn_waiters(self):
        """The machine lock's waiting queue entries that name this repository's landing
        resource."""
        key = queue_key(self.lock.common)
        q = lockqueue.load(self.queue_store())
        return [e for e in q.get('entries') or []
                if e.get('state') == 'waiting' and e.get('resource') == key]

    def yield_turn(self, left):
        """At the landing lock file, holding the landing's turn: True when it gave the turn back,
        because the lock file's holder (self.holder, not gone) is a landing of this repository
        waiting in the machine lock's queue for that turn (a node-routed landing's fallback
        booking again, rebook_here): else each waits for the other until this landing's --wait
        runs out. Its run is released and booked again, behind the holder, waiting for what is
        left (`left`) of the --wait budget, with one cpu slot again when its merged gate is routed
        to a load node. Its line is said once per holder: the new booking may be granted at once
        (a backfill, the holder's start being far off) and given back again at the next look,
        one look (poll_s) apart (take). A queue that cannot be read: False. Raises LockError (the
        new booking)."""
        rec = self.holder or {}
        branches = rec.get('branches')
        if self.slot is None or not isinstance(branches, list) or not branches:
            return False
        slug = place.slug_of('+'.join(str(b) for b in branches))
        try:
            waiting = [e for e in self.turn_waiters() if e.get('slug') == slug]
        except (OSError, ValueError):
            return False
        if not waiting:
            return False
        who = (rec.get('token'), rec.get('pid'), tuple(map(str, branches)))
        if who != self.yielded_to:
            self.yielded_to = who
            self.say('the landing holding the landing lock (%s) waits in the machine lock for '
                     "this repository's landing turn, which this landing holds: releasing it and "
                     'booking again behind it, waiting up to %d s'
                     % (', '.join(map(str, branches)), left))
        self.drop_slot(0, 'done')
        self.book_slot(left, cores=1 if self.on_node() else None)
        return True

    def held_by(self, rec):
        """`(None, False)` when a gone holder's lock was broken and taken; else `(why, final)`."""
        if rec.get('phase') == 'stuck':
            return ('%s: a stuck landing is cleared by hand, as its STUCK report says'
                    % landing.describe(rec)), True
        if not rec.get('token') or self.lock.holder_state(rec) != 'dead':
            return landing.describe(rec), False         # unreadable, alive or a token holder
        why = self.lock.unpushed()
        origin = self.ref(self.origin_ref)
        hint = self.behind_hint(self.ref(self.main_ref), origin) if why else ''
        if hint and not os.path.exists(os.path.join(self.lock.common, 'MERGE_HEAD')):
            took = rec.get('origin_at_take')
            if rec.get('phase') == 'push' and took and origin != took:   # killed after its push
                return ('%s, and its holder is gone after its push: %s/%s (%s) moved on from %s, '
                        'where it took the lock, so its push most likely landed and only the main '
                        'checkout\'s fast-forward was skipped. Not broken, because %s. Then land '
                        'again: the next landing breaks it itself; or break it with: %s' % (
                            landing.describe(rec), self.cfg.remote, self.base_branch,
                            origin[:10], took[:10], hint[2:], self.break_command())), True
            return ('%s, and its holder is gone: not broken, because %s. The next landing breaks '
                    'it itself once main has caught up, or break it with: %s' % (
                        landing.describe(rec), hint[2:], self.break_command())), True
        if why:
            return ('%s, and its holder is gone: not broken, because %s. Settle that by hand, '
                    'then break it with: %s' % (landing.describe(rec), why,
                                                self.break_command())), True
        broken, got = self.lock.break_lock()
        if not broken:
            return '%s (not broken: %s)' % (landing.describe(rec), got), False
        self.say('broke the landing lock of a holder that is gone, with nothing unpushed: %s'
                 % landing.describe(got))
        try:
            rec = self.lock.take(self.branches, pid=self.pid)
        except landing.Busy as e:
            return landing.describe(e.record), False
        self.token = rec['token']
        return None, False

    def drop_slot(self, code=0, how='done'):
        """Release the machine-lock slot, once: its queue entry goes, and the next landing of this
        repository may start. A failure is a WARNING said here (the entry goes once this process
        ends); Ctrl+C is raised."""
        slot, self.slot = self.slot, None
        if slot is None:
            return
        try:
            slot.release(code, how)
        except Exception as e:      # noqa: BLE001 - the outcome stands
            self.say('WARNING: the machine-lock slot of this landing could not be released (%s); '
                     'its queue entry goes once this process ends' % _what(e))

    def locked_checks(self):
        why = self.fetch()
        if why:
            return why
        # Again under the lock: the turn may have come long after preconditions(), and the merge
        # goes onto HEAD.
        current, why = refs.current_branch(self.root)
        if current != self.checkout_branch:
            return 'the main checkout is on %s, not %s, at the landing\'s turn' % (
                current or why or '(detached HEAD)', self.checkout_branch)
        why = self.onto_origin()
        if why:
            return why
        entries, why = gitstatus.status(self.root)
        if entries is None:
            return 'git status failed: %s' % why
        if entries:
            return 'the main checkout is not clean: %s' % ', '.join(
                '%s %s' % (xy.strip(), p) for xy, p in entries[:10])
        why = stash.refusal(self.root)      # the repository's one stack, whichever checkout pushed
        if why:
            return why
        merging, why = refs.merge_in_progress(self.root)
        if merging is not False:
            return why or 'a merge is in progress in the main checkout'
        return self.pair_checks() or self.scope_check()

    def ledger(self, rr):
        """The land ledger line of the record `rr` (a result.Run) just built: appended; a line
        that cannot be appended, or a record that was never built (rr.last None), is said as a
        WARNING and the outcome stands. Never raises."""
        try:
            if rr.last is None:
                self.say('WARNING: no land record was built, so no ledger line for %s; the '
                         'outcome stands' % rr.branch)
                return
            _, warning = result.append_ledger(self.root, rr.last)
            if warning:
                self.say(warning)
        except Exception:   # noqa: BLE001 - the outcome stands whatever happens here
            pass

    def run(self):
        """_run(), and the landing's result records (kitlib.gate.result, kind "land"), one per
        branch at its --expect-tip: `running` once the locked checks pass, just before the merge
        (_locked), then the outcome's, or `error` when an exception or Ctrl+C escapes _run (raised
        again, unchanged). The final and the `error` record name the same log: the merged gate's
        log folder once the merged gate returned, else a submitted landing's output, else none.
        Their started_at is when the landing joined the landing queue: a direct
        landing's just before book_turn, a submitted one's at the submission. A landing that ends
        before the locked checks pass (USAGE, REFUSED before the merge, BUSY, the machine lock's
        refusal: a retry or a duplicate) writes no record, so it never replaces a real result; a
        submitted landing's records were written running before it started, so every outcome
        finishes them. The record lines are said as progress: the verdict stays the
        outcome's last line. The machine-lock slot is released however _run ends. The final and
        the `error` records carry gate_once and escalated (gate_facts), and each is appended to
        the land ledger as one line (ledger; result.append_ledger), a failed append a WARNING.
        Landed, the final records are then posted to the hub's landing feed (post_landing).
        facts['result_record']: the path (one branch), the list of paths (a batch), or None.
        Each final and `error` record names the batch it landed in (`batch`). A submitted
        landing's are written only while each record is still its run's and folded as its
        result.Run says (folded_into: a fold's, into this landing; else none), judged under the
        record's lock; a record left as it is gets no ledger line. A landing another landing
        folded (folded_exit) writes no record and no ledger line at all."""
        try:
            out = self._run()
        except BaseException as e:
            ctrl_c = isinstance(e, KeyboardInterrupt)
            try:
                self.drop_slot(130 if ctrl_c else 1, 'interrupted' if ctrl_c else 'error')
            except BaseException:   # noqa: BLE001 - never replace the exception being raised
                pass
            for rr in ([] if self.folded_exit else self.result_runs):
                try:
                    rr.batch = list(self.branches)
                    self.say(rr.error(e, log=self.gate_logs or self.output_log,
                                      when=self.still_ours(rr), **self.gate_facts())[1])
                    if not rr.kept:
                        self.ledger(rr)
                except Exception:   # noqa: BLE001 - never replace the exception being raised
                    pass
            raise
        if self.folded_exit:        # another landing lands these branches and finishes the records
            self.drop_slot(0, 'done')
            return out
        try:
            self.drop_slot(0 if out.code == G.OK else out.code or 1, 'done')
        except KeyboardInterrupt as e:  # the outcome stands: the records still say it
            out.lines.insert(max(0, len(out.lines) - 1), 'WARNING: %s while the machine-lock '
                             'slot was released; its queue entry goes once this process ends'
                             % _what(e))
        if out.code == G.OK and out.facts.get('merge'):
            self.refresh_mirrors(out)
        paths = []
        once = self.gate_facts()
        for rr in self.result_runs:
            rr.batch = list(self.branches)
            path, line = rr.finish(out, base=self.expect_main,
                                   log=self.gate_logs or self.output_log, source=self.source(),
                                   when=self.still_ours(rr), **once)
            self.say(line)
            if not rr.kept:
                self.ledger(rr)
            paths.append(path)
        out.facts['result_record'] = (paths[0] if len(self.branches) == 1 else paths) if paths else None
        if out.code == G.OK and out.facts.get('merge'):
            self.post_landing()
        return out

    def still_ours(self, rr):
        """The `when` a submitted landing writes the record of `rr` with: the record on disk
        is still `rr`'s run, folded as `rr` says (into this landing, or none); None (write) for a
        direct landing."""
        if not self.submitted:
            return None
        want = rr.folded_into or None
        return lambda now: (isinstance(now, dict) and now.get('run_id') == rr.run_id
                            and (now.get('folded_into') or None) == want)

    def _run(self):
        why = self.usage()
        if why:
            return G.Outcome(G.USAGE, ['USAGE: %s' % why])
        if self.submitted:          # folded before its booking: no check, no booking at all
            out = self.turn_check()
            if out is not None:
                return out
        why = self.preconditions()
        if why:
            return G.Outcome(G.REFUSED, ['REFUSED: %s; nothing was changed' % why])
        if not self.submitted:      # joins the queue now: its records' started_at
            runs = (result.Run.maybe(self.root, 'land', b, t) for b, t in self.pairs)
            self.queued_runs = [rr for rr in runs if rr is not None]
        self.plan_route()
        try:
            self.book_turn()
            out = self.own_turn() if self.submitted else None
        except (landing.LockError, ConfigError) as e:
            return self.turn_check() or G.Outcome(
                G.REFUSED, ['REFUSED: the landing lock: %s; nothing was changed' % e])
        except LockError as e:
            return self.turn_check() or self.not_run(e)
        if out is not None:
            return out
        why = self.fetch()
        if why:
            return G.Outcome(G.REFUSED, ['REFUSED: %s; nothing was changed' % why])
        try:
            why = self.take()
        except (landing.LockError, ConfigError) as e:
            return G.Outcome(G.REFUSED, ['REFUSED: the landing lock: %s; nothing was changed' % e])
        except LockError as e:      # booking again behind the lock file's holder (yield_turn)
            return self.not_run(e)
        if why:
            return G.Outcome(G.BUSY, ['BUSY: %s; nothing was changed' % why])
        try:
            return self.unfold_unlanded(self._locked())
        except _Stuck as e:
            return self.stuck(str(e))
        except BaseException as e:  # after the lock is taken nothing may leave it silently
            out = self.recover(e)
            if out.code in (G.STUCK, G.OK):  # Ctrl+C included: STUCK is exit 3, with what is
                return out      # left; OK: stopped after the verified push, landed
            if isinstance(e, ConfigError) and out.code == G.REFUSED:
                return self.unfold_or_warn(out)
            if isinstance(e, KeyboardInterrupt) or (isinstance(e, Exception) and
                                                    self.phase == 'before'):
                for line in out.lines:  # Ctrl+C undone and released (exit 130), or a bug
                    self.say(line)      # before the merge: raised again
                self.unfold_or_warn(out)  # its folds land at their own turn, not as its error
                raise
            return self.unfold_or_warn(out)     # an exception or SystemExit after the merge

    def turn_check(self):
        """A submitted landing without its turn (before its booking, or its booking failed):
        the FOLDED Outcome (fold_exit) when another landing folded it (submit.turn, given False),
        nothing written; else None, and the landing goes on to its own outcome. A direct
        landing: None."""
        if not self.submitted or not self.result_runs:
            return None
        state, rec = submitmod().turn(self.root, self.result_runs, self.output_log, given=False)
        if state == submitmod().FOLDED:
            return self.fold_exit(rec)
        self.was_dropped = isinstance(rec, dict) and rec.get('fold_dropped') is True
        return None

    def own_turn(self):
        """Step 3 for a submitted landing, its booking given: None when the turn is its own
        (submit.turn marked its first record turn_at); the FOLDED Outcome (fold_exit) when
        another landing folded it. Folded into a landing that still runs (it lands them, or gives
        them back): the slot is released and booked again, a look every poll_s, with what is left
        of the --wait budget. Raises LockError (that booking not given; the caller checks the
        turn again)."""
        sub, said = submitmod(), False
        while True:
            state, rec = sub.turn(self.root, self.result_runs, self.output_log, given=True)
            if state == sub.FOLDED:
                return self.fold_exit(rec)
            if state == sub.MINE:
                self.was_dropped = isinstance(rec, dict) and rec.get('fold_dropped') is True
                return None
            if not said:
                said = True
                self.say('waiting: %s is folded into the batch of the landing run %s, which still '
                         'runs: it lands them or gives them back; booking again a look every %s s'
                         % ('+'.join(self.branches), rec.get('folded_into'), self.poll_s))
            self.drop_slot(0, 'done')
            left = (self.deadline or 0) - time.monotonic()
            time.sleep(max(0.01, min(self.poll_s, max(0.0, left))))
            left = (self.deadline or 0) - time.monotonic()
            self.book_slot(max(0, int(left)), cores=1 if self.on_node() else None)

    def fold_exit(self, rec):
        """Another landing folded this submission into its batch: its slot released, one line
        said as the outcome, and nothing else written (run: folded_exit)."""
        self.folded_exit = True
        self.drop_slot(0, 'done')
        into = rec.get('folded_into') if isinstance(rec, dict) else None
        return G.Outcome(G.OK, ['FOLDED: %s lands in the batch of the landing run %s; its land '
                                'records say how it ended' % ('+'.join(self.branches),
                                                              into or '?')])

    def fold_max(self):
        """The most branches a fold may grow this batch to: "landing".batch_max, the value the
        repository's .kit.json writes or else the default (5), the same cap a named batch has
        (config_refusal); 1 (the opt-out) folds nothing."""
        return self.cfg.section('landing')['batch_max']

    def can_fold(self):
        """True when this landing, its locked checks passed, may fold queued submissions into
        its batch: a submitted landing with its flags (an older kit's payload has none), that
        changes no strict path and no gate config file, was not given back dropped from a batch
        itself, with room under "landing".batch_max (fold_max; 1 folds nothing)."""
        if not self.submitted or not isinstance(self.flags, dict) or self.was_dropped:
            return False
        if self.strict_hits or not self.anchor_run_id:
            return False
        if len(self.branches) >= self.fold_max():
            return False
        hits, why = self.config_paths()
        return why is None and not hits

    def fold_queued(self):
        """Fold the submissions queued behind this landing into its batch (docs/gate-landing.md "Folding
        queued submissions"): oldest first (submit.foldable), each whole and while it fits under
        "landing".batch_max, run with the same flags (submit.same_flags), its branches none of
        this batch's, and passing pair_checks with the landing's own branches; then taken
        (submit.take; one whose records changed meanwhile is left) and appended to the batch. Each
        one left out is said with why. With any folded, the whole batch passes pair_checks and
        scope_check again, or every fold is given back (to land at its own turn) and the landing
        goes on alone."""
        sub = submitmod()
        room = self.fold_max() - len(self.branches)
        for unit in sub.foldable(self.root, self.anchor_run_id):
            if room <= 0:
                break
            label = '%s (run %s)' % ('+'.join(unit['branches']), unit['run_id'])
            why = self.unit_why(unit, room)
            if why:
                self.say('not folding %s: %s' % (label, why))
                continue
            runs = sub.take(self.root, unit, self.anchor_run_id, self.expect_main,
                             self.output_log)
            if runs is None:
                self.say('not folding %s: its records changed while this landing took them'
                         % label)
                continue
            self.folds.append({'unit': unit, 'runs': runs})
            self.branches = self.branches + list(unit['branches'])
            self.tips = self.tips + list(unit['tips'])
            self.result_runs = self.result_runs + runs
            self.own_runs.add(unit['run_id'])
            self.replaced = self.replaced + list(unit['replaced'])
            self.branch = ', '.join(self.branches)
            room -= len(unit['branches'])
            self.say('FOLDED %s into this landing: the batch is %s' % (
                label, '+'.join(self.branches)))
        if not self.folds:
            return
        kept = (self.strict_hits, self.strict_review, self.strict_carry,
                list(self.scope_notes), list(self.scope_warnings))
        self.scope_notes, self.scope_warnings = [], []
        why = self.pair_checks() or self.scope_check()
        if why:
            self.say('not folding: the batch %s does not land as one (%s); each folded submission '
                     'is given back to land at its own turn' % ('+'.join(self.branches), why))
            self.unfold_all(False, 'the batch did not land as one: %s' % why)
            (self.strict_hits, self.strict_review, self.strict_carry, self.scope_notes,
             self.scope_warnings) = kept

    def unit_why(self, unit, room):
        """Why the submission `unit` (submit.foldable's) is not folded into this batch, or None.
        Reads only: the checks run on the batch with it, and this landing's state is put back."""
        sub = submitmod()
        if len(unit['branches']) > room:
            return ('its %d branch(es) are more than the %d "landing".batch_max leaves room for'
                    % (len(unit['branches']), room))
        if not sub.same_flags(self.flags, unit['flags']):
            return 'it was submitted with other landing flags than this landing'
        if set(unit['branches']) & set(self.branches):
            return 'it names a branch of this batch'
        kept = (self.branches, self.tips, self.branch, self.strict_hits, self.strict_review,
                self.strict_carry)
        self.branches = self.branches + list(unit['branches'])
        self.tips = self.tips + list(unit['tips'])
        try:
            return self.pair_checks()
        finally:
            (self.branches, self.tips, self.branch, self.strict_hits, self.strict_review,
             self.strict_carry) = kept

    def fold_of(self, branch):
        """The fold (self.folds) whose submission holds `branch`, or None."""
        return next((f for f in self.folds if branch in f['unit']['branches']), None)

    def unfold(self, fold, dropped, why):
        """Give the folded submission `fold` back (submit.give_back; `dropped`: it lands alone)
        and take it out of the batch; the record lines said."""
        unit = fold['unit']
        for line in submitmod().give_back(self.root, unit, self.anchor_run_id, dropped, why):
            self.say(line)
        self.folds = [f for f in self.folds if f is not fold]
        gone = set(unit['branches'])
        keep = [(b, t) for b, t in self.pairs if b not in gone]
        self.branches, self.tips = [b for b, _ in keep], [t for _, t in keep]
        self.result_runs = [rr for rr in self.result_runs if rr.run_id != unit['run_id']]
        self.own_runs.discard(unit['run_id'])
        self.replaced = [r for r in self.replaced if not any(r is x for x in unit['replaced'])]
        self.branch = ', '.join(self.branches)

    def unfold_all(self, dropped, why):
        for fold in list(self.folds):
            self.unfold(fold, dropped, why)

    def unfold_unlanded(self, out):
        """`out`, once every folded submission is given back when it did not land (any code but
        OK and STUCK): each lands at its own turn."""
        if self.folds and out.code not in (G.OK, G.STUCK):
            self.unfold_all(False, 'the batch it was folded into did not land')
        return out

    def unfold_or_warn(self, out):
        """unfold_unlanded(out) on the exception path of _run, where a give-back that raises
        never replaces `out` or the exception being raised: it says one WARNING line naming each
        fold not given back (those end with this landing's records) and returns `out`."""
        try:
            return self.unfold_unlanded(out)
        except BaseException as err:    # noqa: BLE001 - the landing's own outcome stands
            left = '; '.join('%s (run %s)' % (', '.join(f['unit']['branches']),
                                              f['unit']['run_id']) for f in self.folds)
            try:
                self.say('WARNING: folded submissions not given back (%s): %s; their records end '
                         "with this landing's outcome, so submit each again"
                         % (_what(err), left or 'none left'))
            except BaseException:   # noqa: BLE001 - nor may the warning replace them
                pass
            return out

    def regate_alone(self, out):
        """The merged gate of a batch holding folded submissions failed (`out`): its lines said,
        every fold given back dropped (each lands alone, at its own turn), and the landing's own
        branches merged again and gated in the same slot. The new gate's Outcome, or the
        merge's NOT LANDED."""
        for line in out.lines:
            self.say(line)
        batch = '+'.join(self.branches)
        self.unfold_all(True, 'the merged gate of the batch %s failed' % batch)
        self.say('the merged gate of the batch %s failed: each folded submission is given back '
                 'to land alone, at its own turn; gating %s alone' % (batch, '+'.join(self.branches)))
        self.merges, self.merge = [], None
        why = self.prepare_tree()
        if why:
            return G.Outcome(G.FAILED, [
                'NOT LANDED: the landing tree could not be put back at the base to gate %s alone: '
                '%s; nothing was pushed, and the landing did not move %s from the base %s' % (
                    self.branch, why, self.base_branch, self.expect_main[:10])], undone=True)
        failed = self.do_merge()
        if failed is not None:
            return failed
        self.say('merged %s at %s in the landing tree %s; gating the merged tree' % (
            self.branch, self.merge[:10], self.tree))
        return self.gate()

    def not_run(self, e):
        """The outcome of the machine lock's LockError `e` before the landing lock file is held."""
        if e.code == EXIT_USAGE:
            return runner.lock_refusal(e)
        return G.Outcome(e.code, ['NOT RUN: the machine lock did not give the landing its turn: '
                                  '%s; nothing was changed' % e.message])

    def _locked(self):
        why = self.locked_checks() or self.prepare_tree()
        if why:
            self.release()
            return G.Outcome(G.REFUSED, ['REFUSED: %s; nothing was merged' % why])
        if self.strict_hits:        # strict_check passed it: fresh, said once
            self.strict_escalate()
        elif self.can_fold():
            self.fold_queued()
        if not self.fresh:          # before this landing's own records replace them
            self.prior = self.prior_records()
        if self.submitted:          # running since the submission: now with the base
            for rr in self.result_runs:
                rr.base = self.expect_main
                self.say(rr.start(log=self.output_log,
                                  when=self.still_ours(rr) if rr.folded_into else None)[1])
        else:                       # made when the landing joined the queue (_run)
            for rr in self.queued_runs:
                rr.base = self.expect_main
                self.result_runs.append(rr)
                self.say(rr.start()[1])
        started = gate_ok.utc()
        failed = self.merge_batch()
        if failed is not None:
            self.release()
            return failed
        self.say('merged %s at %s in the landing tree %s; gating the merged tree' % (
            self.branch, self.merge[:10], self.tree))
        self.config_escalate()
        out = self.gate_out = self.gate()
        if out.code == G.FAILED and self.folds:     # a folded batch drops its folds, never fails them
            out = self.gate_out = self.regate_alone(out)
        main_b, base = self.base_branch, self.expect_main
        if out.code == G.OK:
            changed = self.unchanged()      # the commit pushed must be the commit gated
            if changed:
                self.release()
                return G.Outcome(G.FAILED, _not_landed(out.lines) + [
                    'NOT LANDED: the merged gate passed, and %s; nothing was pushed, and the merge '
                    'of %s was undone (the landing did not move %s from the base %s)' % (
                        changed, self.branch, main_b, base[:10])],
                    merge=self.merge, verdict=out.facts.get('verdict'), undone=True)
        if out.code != G.OK:
            self.release()
            return G.Outcome(out.code,
                             out.lines + ['NOT LANDED: the merge of %s was undone; the landing '
                                          'did not move %s from the base %s'
                                          % (self.branch, main_b, base[:10])],
                             verdict=out.facts.get('verdict'), undone=True)
        why = self.push(started, out.facts.get('results'))
        if why:
            self.release()
            return G.Outcome(G.FAILED, _not_landed(out.lines) + ['NOT LANDED: %s' % why],
                             merge=self.merge)
        synced = self.sync_main()
        landed = ['LANDED %s on %s at %s (pushed and verified on %s)' % (
            self.branch, main_b, self.merge, self.cfg.remote)] + self.scope_warnings
        try:
            self.release(landed=True)
        except landing.LockError as e:  # the landing is done; only the lock file is left
            landed.append('WARNING: the landing lock could not be released: %s; check that no '
                          'landing is running, then break it with: %s' % (e, self.break_command()))
        landed.extend(self.close_pulled())
        tidy = self.clean_up()
        return G.Outcome(G.OK, out.lines + tidy + synced + self.scope_notes + landed,
                         merge=self.merge,
                         verdict=out.facts.get('verdict'), cleanup=self.cleanup)

    def prior_records(self):
        """The result records naming the tips, as before this landing: a submitted landing's own
        running records left out, and the land records they replaced put back
        (kitlib.gate.reuse.prior_records; None: reuse nothing)."""
        prior = reuse.prior_records(self.root, self.tips)
        if prior is None or not self.submitted:
            return prior
        return ([r for r in prior if r.get('run_id') not in self.own_runs]
                + [r for r in self.replaced if r.get('tip') in self.tips])

    def release(self, landed=False):
        """Release the landing lock, then the machine-lock slot (even when the release fails)."""
        try:
            if self.token:
                self.lock.release(self.token)
                self.token = None
        finally:
            self.drop_slot(0 if landed else 1, 'done')


def land(root, branch, expect_main, expect_tip, **kw):
    """Land `branch` (or a batch: a list, with a list of tips) from the main checkout `root`: an
    Outcome (OK, FAILED, USAGE, STUCK, REFUSED, BUSY, or a machine lock code when the booking was
    refused; LANDED_UNGATED is retired, never returned)."""
    return Lander(root, branch, expect_main, expect_tip, **kw).run()


def check_strict(root, branches):
    """Read-only, the dry strict check a coordinator runs before it picks a reviewer (docs/gate-landing-policy.md
    "Strict paths"): for each of `branches`, its local tip against `<remote>/<main>` as last
    fetched (runner.default_base; no fetch), decided by Lander.strict_paths, the matcher
    strict_check refuses by. An OK Outcome, one line per branch: `STRICT <branch>: <paths>: ...`
    or `NOT STRICT <branch>: ...`. Takes no lock, needs no verdict and writes nothing. REFUSED
    when the config, the base, a branch or the strict paths cannot be read."""
    try:
        cfg = kconfig.load(root)
    except ConfigError as e:
        return G.Outcome(G.REFUSED, ['REFUSED: the config of %s: %s' % (root, e)])
    ref, why = runner.default_base(cfg, root)
    base = refs.resolve(ref, root)[0] if ref else None
    if not base:
        return G.Outcome(G.REFUSED, ['REFUSED: no base to check against: %s' % (
            why or '%s does not resolve' % ref)])
    tips = []
    for b in branches:
        tip = None if refs.bad_rev(b) else refs.resolve('refs/heads/%s' % b, root)[0]
        if not tip:
            return G.Outcome(G.REFUSED, ['REFUSED: no branch %s in this repository' % b])
        tips.append(tip)
    lander = Lander(root, list(branches), None, tips)
    lander.cfg, lander.expect_main = cfg, base
    hits, why = lander.strict_paths()
    if why:
        return G.Outcome(G.REFUSED, ['REFUSED: %s' % why])
    lines = []
    for b, t in zip(branches, tips):
        on = 'tip %s, base %s %s' % (t[:10], ref, base[:10])
        found = hits.get(b)
        if not found:
            lines.append('NOT STRICT %s: no strict path changed (%s)' % (b, on))
            continue
        more = len(found) - STRICT_SHOWN
        lines.append('STRICT %s: %s%s (%s): its reviewer must run on a strong-tier model, and it '
                     'lands alone' % (b, ', '.join(found[:STRICT_SHOWN]),
                                      ' and %d more' % more if more > 0 else '', on))
    return G.Outcome(G.OK, lines)


def queue(root, store=None):
    """Read-only: the repository's landing lock file and the landings in the machine lock's queue
    (entries of priority "land"; kitlib.lock.queue), as an OK Outcome: one line for the lock file,
    then one per landing, holding first, then waiting in turn order. A landing of another
    repository is marked so: they share the machine lock's slots, not the turn. A lock file that
    is not a record is shown held, phase ?, with the read error: that is when an operator checks
    here before clearing it by hand. REFUSED when the config, the lock's place or the queue cannot
    be read."""
    try:
        cfg = kconfig.load(root)
        lock = landing.LandingLock.for_config(root, cfg)
        try:
            rec, unreadable = lock.read(), None
        except landing.LockError as e:
            rec, unreadable = {'phase': '?'}, str(e)
        q = lockqueue.load(store or Store())
    except (ConfigError, landing.LockError, OSError) as e:
        return G.Outcome(G.REFUSED, ['REFUSED: the landing queue of %s cannot be read: %s' % (
            root, e)])
    key = queue_key(lock.common)
    lands = [e for e in q.get('entries') or [] if e.get('priority') == 'land']
    held = sorted([e for e in lands if e.get('state') != 'waiting'],
                  key=lambda e: str(e.get('held_at') or ''))
    waiting = sorted([e for e in lands if e.get('state') == 'waiting'],
                     key=lambda e: e.get('ticket') or 0)
    lines = [landing.describe(rec) + ('; unreadable: %s' % unreadable if unreadable else ''),
             'landing queue: %s' % ('%d holding or waiting' % len(lands) if lands else 'empty')]
    for n, e in enumerate(held + waiting, 1):
        lines.append('  %d. %s %s/%s (%s, run %s, pid %s) since %s%s' % (
            n, e.get('state'), e.get('repo'), e.get('slug'), e.get('kind'), e.get('id'),
            e.get('pid'), e.get('held_at') or e.get('since') or '?',
            '' if e.get('resource') == key else '; another repository'))
    return G.Outcome(G.OK, lines)
