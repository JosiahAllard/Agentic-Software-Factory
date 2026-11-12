"""A landing's push (docs/gate-landing.md "Landing", steps 8 to 10): the land record, the push
and main synced to it, and the STUCK recovery and its text when a push may have half happened.
The mixin Push of kitlib.landing.lander.Lander: it holds no state of its own; every attribute
is Lander.__init__'s.
"""

import os
import re

from kitlib import gate as G
from kitlib.backlog import at_landing
from kitlib.config.types import ConfigError
from kitlib.content import oncommit
from kitlib.gate import gitstatus, runner
from kitlib.landing import cleanup
from kitlib.gate import steps as S
from kitlib.git import cmd, refs
from kitlib.records import gate_ok
from kitlib.records import landing
from kitlib.records import place
from kitlib.landing.checks import _what
from kitlib.landing.merge import MERGE_TIMEOUT, PUSH_TIMEOUT, _paths


# git's own ref status line for a push it refused (transport.c; untranslated, and kitlib.git.cmd
# runs git under LC_ALL=C): `! [rejected]` decided before sending (non-fast-forward, fetch first),
# `! [remote rejected]` the remote's answer (a hook declined). `failed to push some refs` alone is
# not one: git prints it for any failed push, a status report lost after the upload included
PUSH_REFUSED = ('! [rejected] ', '! [remote rejected] ')
# git's line for a push over HTTP(S) that never connected (exit 128, nothing else on stderr):
# `fatal: unable to access '<url>': <curl's error>`, which git dies with only when its first
# request, the ref discovery (remote-curl.c discover_refs), fails, before any pack is sent; with
# curl's error one that only a connection never made gives: `Could not resolve host: <host>` (or
# `proxy`), `Failed to connect to <host>:<port>` or `<host> port <port>` (curl 8 adds `after <n>
# ms`; the reason after `: ` is the OS's or curl's, as `Connection refused`, `Timed out`, `Could
# not connect to server`), `Connection timed out after <n> milliseconds` and `Resolving timed out
# after <n> milliseconds` (curl's connect timeout in those states, before any request is sent).
# git 2.55.0.windows.5 with libcurl 8.21.0 printed the first two forms, `<host>:<port> after <n>
# ms: Could not connect to server` for both a refused port and a timed-out connect. Every other
# curl error (a reset, a TLS failure, `Operation timed out`) came after the connection: STUCK
PUSH_NEVER_CONNECTED = re.compile(
    r"fatal: unable to access '[^']*': (?:"
    r"Could not resolve (?:host|proxy): \S+"
    r"|Failed to connect to \S+(?::\d+| port \d+)(?: after \d+ ms)?: [^:]+"
    r"|(?:Connection|Resolving) timed out after \d+ milliseconds"
    r")\Z")
STEP_CMD = '    $ '      # a STUCK clear step's command line starts with this


class _Stuck(Exception):
    pass


def push_never_connected(rc, err):
    """Whether a push that ended with exit `rc` and stderr `err` never connected to the remote,
    so no byte of it can have reached it: git died (exit 128) and every line it printed is
    PUSH_NEVER_CONNECTED. Any other line, a later-phase one included, or any other exit: False."""
    lines = [l.strip() for l in (err or '').splitlines() if l.strip()]
    return rc == 128 and bool(lines) and all(PUSH_NEVER_CONNECTED.match(l) for l in lines)


class Push(object):
    """Lander's push and its STUCK recovery (this module's docstring)."""

    def source(self):
        """The merged gate's source (kitlib.gate.detect), once it ran; else None."""
        return self.gate_out.facts.get('source') if self.gate_out is not None else None

    def write_record(self, started, results):
        """The gate-OK record for the merge commit (pre-push reads it), its config_rev the
        base, its mode "docs" when the merged gate ran only the docs-only steps, its ran_on the
        land job's when the merged gate ran on a load node; its path. Raises
        gate_ok.RecordError."""
        docs = self.gate_out is not None and self.gate_out.facts.get('docs') is True
        ran_on = self.gate_out.facts.get('ran_on') if self.gate_out is not None else None
        path, _ = gate_ok.record_pass(self.root, self.merge, gate_ok.gate_config(self.cfg),
                                      runner.kit_identity(), place.repo_name(self.cfg, self.root),
                                      started, runner.record_steps(results or []),
                                      base=self.expect_main, config_rev=self.expect_main,
                                      source=self.source(), full=bool(self.full) and not docs,
                                      mode='docs' if docs else None, ran_on=ran_on)
        return path

    def push(self, started, results):
        """Step 9: write the merge's gate-OK record, push the merge to <remote>/<main> (never
        forced) and verify it there with ls-remote. None once verified (phase pushed: landed);
        else why nothing landed (NOT LANDED: the record could not be written, the remote's main
        moved on while the gate ran, so the push was refused, or git refused the push, exit 1
        with git's own ref status line `! [rejected] ...` or `! [remote rejected] ...`
        (PUSH_REFUSED), with the remote's main still the base; or the push never connected,
        push_never_connected, with the remote's main anything but the merge or a commit holding
        it, unreadable included). _Stuck when the push may have
        landed and git cannot tell (ls-remote unreadable, or a remote main this checkout cannot
        place), or when any other push did not land on a remote main still at the base or absent:
        exit 0, no exit (timed out), exit 1 without that line (no message: a git ended from
        outside, refs.KILLED_SHAPE; a stray warning; a bare `failed to push some refs`) or exit
        128 (git died: a connection dropped after the upload may still be applied on the
        remote)."""
        main_b, remote = self.base_branch, self.cfg.remote
        try:
            path = self.write_record(started, results)
        except gate_ok.RecordError as e:
            return ('the gate-OK record could not be written: %s; nothing was pushed, and the merge '
                    'of %s was undone (the landing did not move %s from the base %s)' % (
                        e, self.branch, main_b, self.expect_main[:10]))
        self.say('gate-OK record: %s' % path)
        self.lock.set_phase(self.token, 'push')
        self.phase = 'push'
        r = self.git('push', '--quiet', remote, '%s:refs/heads/%s' % (self.merge, main_b),
                     timeout=PUSH_TIMEOUT)
        pushed, why = refs.ls_remote(remote, 'refs/heads/%s' % main_b, self.root)
        how = 'exited 0' if r.rc == 0 else 'failed (%s)' % (r.err or 'exit %s' % r.rc)
        lines = [l.strip() for l in (r.err or '').splitlines() if l.strip()]
        said = ' | '.join(lines)
        unconnected = push_never_connected(r.rc, r.err)

        def never_connected(state):
            # git died before connecting (PUSH_NEVER_CONNECTED): no byte reached the remote, so
            # the merge, made only here, is not on it whatever ls-remote read (in an outage it
            # fails the same way). A remote main that is or holds the merge still counts as landed
            return ('the push of the merge %s never connected to %s (%s), and %s/%s %s; nothing '
                    'landed, and the merge of %s was undone: land again once %s can be reached'
                    % (self.merge[:10], remote, said, remote, main_b, state, self.branch, remote))
        if pushed is None:
            if unconnected:
                return never_connected('is unreadable (%s)' % why)
            raise _Stuck('the push %s, and %s/%s is unreadable (%s), not known to be the merge %s'
                         % (how, remote, main_b, why, self.merge[:10]))
        if pushed != self.merge:
            on = self.on_remote(pushed)
            if on is None:                  # a remote main not fetched here: fetch, look again
                self.fetch()
                on = self.on_remote(pushed)
            if on is None:
                if unconnected:
                    return never_connected('is %s, which this checkout cannot place' % (
                        pushed[:10] or 'absent'))
                raise _Stuck('the push %s, and %s/%s is %s, which this checkout cannot place '
                             'against the merge %s' % (how, remote, main_b, pushed[:10] or 'absent',
                                                       self.merge[:10]))
            if not on:
                if unconnected:
                    return never_connected('is still the base %s' % self.expect_main[:10]
                                           if pushed == self.expect_main else
                                           'is %s' % (pushed[:10] or 'absent'))
                refused = any(l.startswith(PUSH_REFUSED) for l in lines)
                if r.rc == 1 and refused and pushed == self.expect_main:
                    # git ran to the end and printed the ref's refusal (PUSH_REFUSED): nothing was
                    # published. Exit 1 without it (no message: a git ended from outside,
                    # refs.KILLED_SHAPE; or only a stray warning) and exit 128 (git died) may have
                    # gone after the upload, which the remote may still apply after this
                    # ls-remote: STUCK
                    return ('the push of the merge %s failed (%s), and %s/%s is still the base %s; '
                            'nothing landed, and the merge of %s was undone: land again once the '
                            'push can go' % (self.merge[:10], said, remote, main_b,
                                             self.expect_main[:10], self.branch))
                if r.rc == 0 or not pushed or pushed == self.expect_main:
                    raise _Stuck('the push %s, and %s/%s is %s, not the merge %s%s' % (
                        how, remote, main_b, (pushed or 'absent')[:10], self.merge[:10],
                        '' if said or r.rc != 1 else '; %s' % refs.KILLED_SHAPE))
                return ('%s/%s moved on to %s while the merged gate ran, so the push of the merge '
                        '%s was refused (a landing never forces); nothing landed, and the merge '
                        'of %s was undone: land again' % (remote, main_b, pushed[:10],
                                                          self.merge[:10], self.branch))
        self.landed_at = pushed
        self.phase = 'pushed'       # verified on the remote: landed, whatever stops it from here
        if self.fetch() or self.ref(self.origin_ref) != pushed:
            self.git('update-ref', self.origin_ref, pushed)
        return None

    def sync_main(self):
        """Step 10, after the verified push: the main checkout's main fast-forwarded to the merge
        (`git merge --ff-only` there) when that is safe: HEAD on main, no merge in progress, main
        an ancestor of the merge, and no local change or untracked file on a path the merge
        changes (a local edit elsewhere is carried). Else nothing in the main checkout moves, and
        a WARNING says why and how to bring it there. [] or [WARNING lines]; never raises git's
        failures. A landing onto a staging branch moves nothing in the main checkout: its main is
        not the base, and the promotion is its only writer (docs/gate-landing.md "Staging
        branch")."""
        self.synced = True
        if self.staging:
            return []
        main_b, remote = self.base_branch, self.cfg.remote
        g = 'git -C "%s"' % self.root
        ff = '%s merge --ff-only %s/%s' % (g, remote, main_b)
        main = self.ref(self.main_ref)
        if main == self.merge:
            return []
        lead = 'WARNING: %s landed at %s, and the main checkout\'s %s (%s) was not fast-forwarded to it' % (
            self.branch, self.merge[:10], main_b, (main or '?')[:10])
        if not main:
            return ['%s: it cannot be read; bring it there with: %s' % (lead, ff)]
        out, why = cmd.run_text(['log', '--format=%H %s', main, '--not', self.merge], self.root)
        if out is None:
            return ['%s: git cannot tell whether it has commits the landing lacks (%s); check, '
                    'then: %s' % (lead, why, ff)]
        own = [l for l in out.splitlines() if l.strip()]
        if own:
            return ['%s: it has %d commit(s) made in the main checkout that the landing did not '
                    'gate or push: %s. They are kept: rebase them onto %s/%s (or set them aside), '
                    'then: %s' % (lead, len(own), '; '.join(l[:10] + l[40:] for l in own[:10]),
                                  remote, main_b, ff)]
        current, why = refs.current_branch(self.root)
        if current != main_b:
            return ['%s: the main checkout is on %s, not %s; switch back, then: %s' % (
                lead, current or why or 'no branch (detached)', main_b, ff)]
        merging, why = refs.merge_in_progress(self.root)
        if merging is not False:
            return ['%s: %s; finish it, then: %s' % (
                lead, 'a merge is in progress there' if merging else 'git cannot tell whether a '
                'merge is in progress there (%s)' % why, ff)]
        entries, why = gitstatus.status(self.root)
        changed, why2 = runner.changed_between(self.root, main, self.merge)
        if entries is None or changed is None:
            return ['%s: git cannot tell whether local changes are in the way (%s); check, then: %s'
                    % (lead, why or why2, ff)]
        theirs = set(os.path.normcase(p) for p in changed)
        clash = [(xy, p) for xy, p in entries if os.path.normcase(p) in theirs]
        if clash:
            return ['%s: local changes there are on paths the merge changes (%s); keep or drop '
                    'them, then: %s' % (lead, _paths(clash), ff)]
        r = self.git('merge', '--ff-only', '--quiet', self.merge, timeout=MERGE_TIMEOUT)
        now = self.ref(self.main_ref)
        if r.rc != 0 or now != self.merge:
            return ['%s: `git merge --ff-only` did not bring it there (%s); %s' % (
                lead, r.err or 'exit %s' % r.rc if r.rc else 'main is at %s' % (now or '?')[:10],
                ff)]
        return []

    def recover(self, e):
        """After an exception `e` under the lock: release it before the merge; after the merge
        (phase merge or gate) put the landing tree's merge aside (half_merge) and release it:
        nothing was pushed, and the main checkout was never merged into. Phase push: STUCK (the
        merge may be on the remote). Phase pushed or landed: landed (landed_anyway). An Outcome
        saying what happened."""
        what = 'Ctrl+C' if isinstance(e, KeyboardInterrupt) else '%s: %s' % (type(e).__name__, e)
        main_b, base = self.base_branch, self.expect_main
        try:
            if self.phase == 'before':
                self.release()
                if isinstance(e, ConfigError):
                    return G.Outcome(G.REFUSED, ['REFUSED: %s; nothing was changed' % e])
                return G.Outcome(G.FAILED, ['NOT LANDED: %s before the merge; the landing lock was '
                                            'released; nothing was changed' % what])
            if self.phase in ('pushed', 'landed'):
                return self.landed_anyway(what)
            if self.phase == 'push':
                raise _Stuck('%s during the push: the merge may be on %s' % (what, self.cfg.remote))
            lines = self.half_merge(what)
            self.release()
            if self.phase == 'merge' and not self.merges:
                return G.Outcome(G.FAILED, lines + [
                    'NOT LANDED: %s during the merge; it was aborted, and the landing did not '
                    'move %s from the base %s' % (
                        what, main_b, base[:10])], undone=True)
            return G.Outcome(G.FAILED, lines + [
                'NOT LANDED: %s in phase %s; the merge of %s was undone, and the landing did not '
                'move %s from the base %s'
                % (what, self.phase, self.branch, main_b, base[:10])], undone=True)
        except _Stuck as s:
            return self.stuck(str(s))
        except BaseException as s:  # a second failure while recovering: leave the lock held
            return self.stuck('%s, and then %s: %s while recovering' % (what, type(s).__name__, s))

    def landed_anyway(self, what):
        """`what` stopped the landing after its push was verified (phase pushed): it is landed.
        Bring the main checkout to the merge if that has not run (sync_main), release the lock (a
        failure is a WARNING with the break command) and say what was skipped: an OK Outcome,
        never STUCK. Each step is guarded against BaseException, so a second Ctrl+C or failure
        here is one more WARNING, never a STUCK for a landed merge."""
        lines = list(self.gate_out.lines) if self.gate_out is not None else []
        at = self.landed_at or self.merge   # what ls-remote showed: the merge, or one on top
        if not self.synced:
            try:
                lines.extend(self.sync_main())
            except BaseException as e:  # noqa: BLE001 - landed: the main checkout is only behind
                lines.append('WARNING: bringing the main checkout to the merge stopped (%s); '
                             'fast-forward it with: git -C "%s" merge --ff-only %s/%s' % (
                                 _what(e), self.root, self.cfg.remote, self.base_branch))
        if at == self.merge:
            how = 'pushed and verified on %s' % self.cfg.remote
        else:
            how = 'already on %s: %s/%s %s has it' % (self.cfg.remote, self.cfg.remote,
                                                     self.base_branch, at[:10])
        lines.extend(self.scope_notes)
        lines.append('LANDED %s on %s at %s (%s)' % (self.branch, self.base_branch,
                                                     self.merge, how))
        lines.extend(self.scope_warnings)
        lines.append('WARNING: %s after the verified push; the landing stands' % what)
        try:                        # ls-remote showed `at`, so the tracking ref may say so; never
            if self.ref(self.origin_ref) != at:  # set back to the merge under commits on top
                self.git('update-ref', self.origin_ref, at)
        except BaseException:       # noqa: BLE001 - the next landing's fetch puts it right
            pass
        released = None
        try:
            self.release(landed=True)
        except BaseException as e:  # noqa: BLE001 - landed: only the lock file is left
            released = _what(e)
        if self.token:
            lines.append('WARNING: the landing lock was not released (%s); check that no landing '
                         'is running, then break it with: %s' % (
                             released or 'still held', self.break_command()))
        elif released is not None:
            lines.append('WARNING: the landing lock was released, and releasing the landing\'s '
                         'machine-lock slot stopped (%s); its queue entry goes once this process '
                         'ends' % released)
        for b in self.branches:
            try:
                hows = at_landing.landable_commands(self.root, self.cfg, b)
            except BaseException:   # noqa: BLE001 - cannot tell: name the tool anyway
                hows = ['the backlog tool']
            for how in hows:
                lines.append('WARNING: the records %s holds pulled in the backlog store were not '
                             'closed; see what is left with: %s' % (b, how))
        if not self.cleaned and not self.keep_branch:
            lines.append('WARNING: the branch cleanup did not run; once no landing is running, '
                         'clean up with: %s' % cleanup.sweep_command(self.root))
        verdict = self.gate_out.facts.get('verdict') if self.gate_out is not None else None
        return G.Outcome(G.OK, lines, merge=self.merge, verdict=verdict)

    def half_merge(self, what):
        """Merging or gating stopped part way (phase merge or gate): abort a merge in progress in
        the landing tree, and when its HEAD is exactly the merge of the next tip onto the last
        recorded merge (or the base), record it in self.merges. Best effort: the main checkout was
        never merged into, and the next landing puts the tree back at its base or recreates it,
        so this never raises for git; WARNING lines, or []."""
        tree = self.tree
        if not tree or not os.path.isdir(tree):
            return []
        lines = []
        merging, _ = refs.merge_in_progress(tree)
        if merging:
            a = self.git('merge', '--abort', timeout=MERGE_TIMEOUT, cwd=tree)
            if a.rc != 0:
                lines.append('WARNING: %s during the merge, and `git merge --abort` failed in the '
                             'landing tree %s (%s); the next landing recreates it' % (
                                 what, tree, a.err or 'exit %s' % a.rc))
                return lines
        prev = self.merges[-1] if self.merges else self.expect_main
        nxt = self.tips[len(self.merges)] if len(self.merges) < len(self.tips) else None
        head = self.tree_head()
        if not head or head == prev or not nxt:
            return lines
        first, second = self.tree_parents()
        if first == prev and second == nxt:
            self.merges.append(head)
            self.merge = head
        return lines

    def on_remote(self, pushed):
        """True when one of this landing's merges is `pushed` (the remote's main) or an ancestor
        of it: the merge is landed, and nothing may undo it; None when git cannot tell (`pushed`
        not fetched); else False."""
        if not pushed or not self.merges:
            return False
        unknown = False
        for m in self.merges:
            if m == pushed:
                return True
            yes, _ = refs.is_ancestor(m, pushed, self.root)
            if yes:
                return True
            unknown = unknown or yes is None
        return None if unknown else False

    def break_command(self):
        return ('python -c "import sys; sys.path.insert(0, r\'%s\'); from kitlib.records.landing '
                'import LandingLock as L; print(L(r\'%s\', \'%s\', \'%s\'%s)'
                '.break_lock())"' % (S.kit_live(), self.root, self.checkout_branch, self.cfg.remote,
                                     ', base=\'%s\'' % self.base_branch if self.staging else ''))

    def stuck(self, why):
        state = 'the landing lock could not be marked stuck'
        try:
            if self.token:
                self.lock.set_phase(self.token, 'stuck')
                state = 'the landing lock is left held at phase stuck (%s)' % self.lock.path
        except landing.LockError as e:
            state = '%s: %s' % (state, e)
        lines = ['STUCK: landing %s stopped in phase %s: %s' % (self.branch, self.phase, why)]
        if self.cfg is None or self.lock is None:
            main_b = self.base_branch if self.cfg else 'main'
            remote = self.cfg.remote if self.cfg else 'origin'
            lines.append('%s. Check %s against %s/%s (git log --first-parent), finish by hand, '
                         'then break the lock.' % (state, main_b, remote, main_b))
            return G.Outcome(G.STUCK, lines, merge=self.merge)
        lines.append('%s. To clear it, in this order (each command on a "$" line):' % state)
        try:
            left = self.leftovers()
        except Exception as e:      # the report must not fail
            left = None
            lines.append('left: unknown (%s: %s); check %s against %s/%s by hand' % (
                type(e).__name__, e, self.base_branch, self.cfg.remote, self.base_branch))
        if left is not None:
            lines.append(self.left_line(left))
            steps = self.clear_steps(left)
        else:
            steps = []
        steps.append(('break the landing lock (it refuses while the holder is alive, or while %s '
                      'differs from %s/%s)' % (self.base_branch, self.cfg.remote,
                                               self.base_branch), [self.break_command()]))
        for n, (text, commands) in enumerate(steps, 1):
            lines.append('%d. %s' % (n, text))
            lines.extend(STEP_CMD + c for c in commands)
        return G.Outcome(G.STUCK, lines, merge=self.merge)

    def leftovers(self):
        """What a STUCK landing leaves, as a dict: the branch HEAD is on, main, the remote's main
        (None when ls-remote cannot read it, and why), the tracking ref, tracked changes,
        untracked files, and the commits on main beyond the merge (and the branch it merged)."""
        current, _ = refs.current_branch(self.root)
        main = self.ref(self.main_ref)
        tracked, untracked = self.tracked_and_untracked()
        pushed, pushed_why = refs.ls_remote(self.cfg.remote, 'refs/heads/%s' % self.base_branch,
                                            self.root)
        extra = []
        if main and main != self.expect_main:
            known = [self.expect_main] + self.tips + self.merges
            out, _ = cmd.run_text(['log', '--format=%H %s', main, '--not'] + known, self.root)
            extra = [l for l in (out or '').splitlines() if l.strip()]
        return {'current': current, 'main': main, 'tracked': tracked, 'untracked': untracked,
                'pushed': pushed, 'pushed_why': pushed_why, 'tracking': self.ref(self.origin_ref),
                'extra': extra}

    def left_line(self, left):
        main_b, remote = self.base_branch, self.cfg.remote
        tracked = left['tracked']
        return ('left: HEAD on %s; %s at %s, %s/%s at %s (tracking ref %s; base %s, merge '
                '%s); tracked changes: %s; untracked: %s' % (
                    left['current'] or 'no branch (detached)', self.checkout_branch,
                    (left['main'] or '?')[:10], remote, main_b,
                    'unreadable' if left['pushed'] is None else (left['pushed'] or 'absent')[:10],
                    (left['tracking'] or '?')[:10], self.base_text(),
                    (self.merge or 'none')[:10],
                    _paths(tracked) if tracked is not None else '?',
                    ', '.join((left['untracked'] or [])[:10]) or 'none'))

    def base_text(self):
        """The base, short, and the --expect-main it moved on from when one was given and differs
        (onto_origin)."""
        given = self.expect_main_given
        if not given or given == self.expect_main:
            return (self.expect_main or '?')[:10]
        return '%s, moved on from --expect-main %s' % ((self.expect_main or '?')[:10], given[:10])

    def clear_steps(self, left):
        """`[(what, [commands])]` that fit what is left. The main checkout was never merged into,
        so no step resets or undoes anything: a STUCK landing only has to find out whether its
        push landed. When the remote's main (or, when ls-remote cannot read it, the tracking ref)
        holds the merge: on_origin_steps (bring the main checkout there); when it may hold it and
        git cannot tell: check_first."""
        main_b, remote = self.base_branch, self.cfg.remote
        g = 'git -C "%s"' % self.root
        tracking = 'refs/remotes/%s/%s' % (remote, main_b)
        upstream = '%s/%s' % (remote, main_b)
        pushed = left['pushed']
        if pushed is None:
            on = self.on_remote(left['tracking'])
            if on:                  # a push from this checkout set it: the merge is landed
                return [('the push may have landed: %s could not be read (%s), and the '
                         'tracking ref %s (%s) holds the merge %s, so it is on %s. Fetch, then '
                         'check what %s is' % (
                             upstream, left['pushed_why'] or 'ls-remote failed', tracking,
                             left['tracking'][:10], (self.merge or '?')[:10], remote, upstream),
                         ['%s fetch %s' % (g, remote), '%s rev-parse %s' % (g, tracking)])
                        ] + self.on_origin_steps(left, left['tracking'])
            if on is None:
                return [self.check_first(left['tracking'])]
            steps = [('the push may have landed: %s could not be read (%s). Fetch, then check '
                      'what %s is' % (upstream, left['pushed_why'] or 'ls-remote failed', upstream),
                      ['%s fetch %s' % (g, remote), '%s rev-parse %s' % (g, tracking)]),
                     ('if that printed the merge %s, the landing is done: bring %s to it (HEAD '
                      'on %s, then `%s merge --ff-only %s`), then go to the last step. If it '
                      'printed %s, the push did not land, and nothing is to be undone: the main '
                      'checkout was never merged into. If the fetch failed, stop: what it printed '
                      'is stale' % ((self.merge or '(none)'), main_b, main_b, g, upstream,
                                    self.expect_main), [])]
            if self.merge:          # neither: someone pushed, maybe on top of the merge
                steps.append(('if it printed anything else, list the remote branches that hold '
                              'the merge: if the list names %s, the merge is landed: bring %s to '
                              'it as above; if it does not, nothing landed and nothing is to be '
                              'undone' % (upstream, main_b),
                              ['%s branch -r --contains %s' % (g, self.merge)]))
            return steps
        on = self.on_remote(pushed)
        if on:
            return self.on_origin_steps(left, pushed)
        if on is None:              # may be landed: nothing is settled until that is known
            return [self.check_first(pushed)]
        if pushed != self.expect_main:
            return [('%s is at %s, neither the base (%s) nor the merge: someone else pushed. The '
                     'merge did not land, and nothing is to be undone: the main checkout was '
                     'never merged into' % (upstream, (pushed or 'absent')[:10],
                                            self.base_text()), [])]
        return [('%s is still at the base (%s): the merge did not land, and nothing is to be '
                 'undone: the main checkout was never merged into' % (upstream, self.base_text()),
                 [])]

    def check_first(self, pushed):
        """clear_steps when git cannot tell whether the remote's main (`pushed`, or the tracking
        ref when ls-remote could not be read) holds the merge: one step that finds out. Nothing is
        to be undone either way: the main checkout was never merged into."""
        main_b, remote = self.base_branch, self.cfg.remote
        g = 'git -C "%s"' % self.root
        upstream = '%s/%s' % (remote, main_b)
        return ('%s is at %s, which this checkout cannot place against the merge %s, so whether '
                'it holds the merge cannot be told here. Fetch, then list the remote branches '
                'that hold the merge. If the fetch failed, the list is stale: stop here and do '
                'not move %s. If the list names %s, the merge is landed: bring the checkout to %s '
                'by hand (HEAD on %s, %s fast-forwarded to %s), then go to the last step. If it '
                'does not, the merge is not on %s, and nothing is to be undone (the main checkout '
                'was never merged into): settle %s with whoever pushed it, then go to the last '
                'step' % (
                    upstream, (pushed or '?')[:10], (self.merge or '?')[:10], main_b, upstream,
                    upstream, main_b, main_b, upstream, remote, upstream),
                ['%s fetch %s' % (g, remote), '%s branch -r --contains %s' % (g, self.merge)])

    def on_origin_steps(self, left, pushed):
        """clear_steps when the remote's main (`pushed`) is a merge of this landing or descends
        from one: the merge is landed, so no step undoes it and none says reset. The steps bring
        the main checkout to the remote's main (fetch, switch back, a fast-forward), leave
        anything only the checkout has to a person, and say how a merge that reached the remote
        without a passing merged gate is taken back (a revert by hand, never run here). Commits
        on the remote's main on top of the landing are named, with the full gate to run in a new
        worktree (tools/new_worktree.py), never in the main checkout."""
        main_b, remote = self.base_branch, self.cfg.remote
        g = 'git -C "%s"' % self.root
        upstream = '%s/%s' % (remote, main_b)
        steps = []
        if left['tracking'] != pushed:
            steps.append(('the tracking ref %s is at %s, not %s as on %s: fetch it, or break_lock '
                          'refuses' % (upstream, (left['tracking'] or '?')[:10], pushed[:10],
                                       remote), ['%s fetch %s' % (g, remote)]))
        gate_out = self.gate_out
        if gate_out is None or gate_out.code != G.OK:
            merges = list(reversed(self.merges))
            steps.append(('the merge%s %s reached %s without a passing merged gate (pushed while '
                          'the gate ran): nothing here takes it back. Fix forward, or revert it by '
                          'hand once the lock is broken%s: %s' % (
                              's' if len(merges) > 1 else '',
                              ', '.join(m[:10] for m in merges), upstream,
                              ', newest first' if len(merges) > 1 else '',
                              '; '.join('`git revert -m 1 %s`' % m for m in merges)), []))
        known = [self.expect_main] + self.tips + self.merges
        out, _ = cmd.run_text(['log', '--format=%H %s', pushed, '--not'] + known, self.root)
        on_top = ([l for l in (out or '').splitlines() if l.strip()] if out is not None
                  else left['extra'])
        if on_top:
            # A full gate runs in a worktree, never the main checkout (AGENTS.md); in the kit,
            # with the worktree's own gate.py, as gate_command picks it.
            wt = '<worktree>'
            gate_py = os.path.join(wt if oncommit.is_kit(self.root) else S.kit_live(), 'tools',
                                   'gate.py')
            steps.append(('%s has %d commit(s) on top of the merge that no merged gate saw: %s. '
                          'Once the lock is broken, gate %s whole in a worktree, never in the main '
                          'checkout: make one on a new branch from %s with `python "%s" -C "%s" '
                          '<area>/<slug>`, then gate it: `python "%s" -C "%s" --full`' % (
                              upstream, len(on_top),
                              '; '.join(l[:10] + l[40:] for l in on_top[:10]) + (
                                  '; and %d more' % (len(on_top) - 10) if len(on_top) > 10
                                  else ''),
                              main_b, upstream,
                              os.path.join(S.kit_live(), 'tools', 'new_worktree.py'), self.root,
                              gate_py, wt),
                          []))
        here = self.checkout_branch
        if left['current'] != here:
            steps.append(('HEAD is on %s, not %s: switch back to %s first%s' % (
                left['current'] or 'no branch (detached)', here, here,
                '; keep or drop its tracked changes first' if left['tracked'] else ''),
                ['%s switch %s' % (g, here)]))
        if left['tracked']:
            steps.append(('keep or drop the tracked changes by hand (%s): the merge is landed, '
                          'and the next landing refuses a checkout that is not clean'
                          % _paths(left['tracked']), []))
        if left['untracked']:
            steps.append(('move or delete the untracked file(s) by hand (%s): the next landing '
                          'refuses a checkout that is not clean' % ', '.join(
                              left['untracked'][:10]), []))
        main = None if self.staging else left['main']  # a staging landing never moves main
        if main and main != pushed:
            behind, _ = refs.is_ancestor(main, pushed, self.root)
            if behind:
                steps.append(('%s (%s) is only behind %s (%s): fast-forward it' % (
                    main_b, main[:10], upstream, pushed[:10]),
                    ['%s merge --ff-only %s' % (g, upstream)]))
            else:
                out, _ = cmd.run_text(['log', '--format=%H %s', main, '--not', pushed], self.root)
                own = [l for l in (out or '').splitlines() if l.strip()]
                steps.append(('%s (%s) has commits %s (%s) does not have%s: push them or set them '
                              'aside by hand (the merge is landed; nothing is to be undone); '
                              'break_lock refuses until %s equals %s' % (
                                  main_b, main[:10], upstream, pushed[:10],
                                  ': %s' % '; '.join(l[:10] + l[40:] for l in own[:10])
                                  if own else '', main_b, upstream), []))
        return steps
