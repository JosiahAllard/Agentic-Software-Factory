"""A landing's merge (docs/gate-landing.md "Landing", step 5): main onto <remote>/<main>, the
landing tree prepared (TREE_NAME, TREE_LOCK) and the branches merged in it. The mixin Merge of
kitlib.landing.lander.Lander: it holds no state of its own; every attribute is
Lander.__init__'s.
"""

import os
import shutil
import stat

from kitlib import gate as G
import kitlib.config as kconfig
from kitlib.config.types import ConfigError
from kitlib.gate import gitstatus, stash
from kitlib.git import cmd, refs, repo


PUSH_TIMEOUT = 600      # seconds
MERGE_TIMEOUT = 300
TREE_NAME = '_landing'   # the landing tree's folder, under the config's worktrees_dir
# Its git worktree lock reason, kept verbatim across the move to kitlib.landing: prepare_tree
# compares an existing tree's lock reason to it, and a tree locked under another text is
# refused, not reused
TREE_LOCK = 'kit landing tree (kitlib.gate.land)'


def _paths(entries):
    return ', '.join('%s %s' % (xy.strip(), p) for xy, p in entries[:10]) or 'none'


def _rmtree(path):
    """Remove the folder `path` and everything in it, read-only files included (OSError when
    something there cannot go, such as a file another process holds open)."""
    def again(fn, p, _exc):
        os.chmod(p, stat.S_IWRITE)
        fn(p)
    try:
        shutil.rmtree(path, onexc=again)        # Python 3.12 and later
    except TypeError:
        shutil.rmtree(path, onerror=again)


class Merge(object):
    """Lander's merge onto the base in the landing tree (this module's docstring)."""

    def onto_origin(self):
        """Step 5, after the fetch: the base becomes <remote>/<base> as fetched (the work base:
        <remote>/<main>, or the staging branch when one is set). A local main only behind it is
        fast-forwarded (HEAD on main, a clean checkout, no merge in progress), never onto a
        staging branch: there the main checkout's main is not the base, and a landing never moves
        it; then, when the base is not the commit the config was read at, follow() reads the
        base's config. With --expect-main given and not the base, it says so and goes on. None, or
        why not (REFUSED, nothing merged): main with commits the remote lacks, or one that cannot
        be fast-forwarded, or a config at the base that cannot take this landing."""
        main_b, remote = self.base_branch, self.cfg.remote
        main, origin = self.ref(self.main_ref), self.ref(self.origin_ref)
        if not origin:
            return 'there is no %s/%s after the fetch' % (remote, main_b)
        if main != origin and not self.staging:
            behind, why = refs.is_ancestor(main, origin, self.root) if main else (None, 'no %s'
                                                                                    % main_b)
            if behind is None:
                return 'git cannot tell whether %s (%s) is behind %s/%s (%s): %s' % (
                    main_b, (main or '?')[:10], remote, main_b, origin[:10], why)
            if not behind:
                return ('%s is at %s, with commits %s/%s (%s) does not have: push them or set '
                        'them aside by hand; a landing merges onto %s/%s' % (
                            main_b, main[:10], remote, main_b, origin[:10], remote, main_b))
            why = self.fast_forward(main, origin)
            if why:
                return why
        if origin != self.expect_main:
            why = self.follow(origin)
            if why:
                return why
        given = self.expect_main_given
        if given and given != self.expect_main:
            self.say('main moved on from %s to %s; merging onto %s' % (
                given[:10], self.expect_main[:10], self.expect_main[:10]))
        return None

    def fast_forward(self, main, origin):
        """The local main only behind <remote>/<main>: `git merge --ff-only` it there. None, or
        why not: HEAD not on main, a checkout that is not clean, a merge in progress, or a
        fast-forward git refuses or that does not land main at <remote>/<main>."""
        main_b, remote = self.checkout_branch, self.cfg.remote
        behind = '%s (%s) is behind %s/%s (%s)' % (main_b, main[:10], remote, main_b, origin[:10])
        current, why = refs.current_branch(self.root)
        if current != main_b:
            return '%s, and the main checkout is on %s, not %s' % (
                behind, current or why or '(detached HEAD)', main_b)
        entries, why = gitstatus.status(self.root)
        if entries is None:
            return 'git status failed: %s' % why
        if entries:
            return '%s, and the main checkout is not clean, so it is not fast-forwarded: %s' % (
                behind, _paths(entries))
        merging, why = refs.merge_in_progress(self.root)
        if merging is not False:
            return why or '%s, and a merge is in progress in the main checkout' % behind
        r = self.git('merge', '--ff-only', '--quiet', self.origin_ref, timeout=MERGE_TIMEOUT)
        now = self.ref(self.main_ref)
        if r.rc != 0 or now != origin:
            return '%s, and `git merge --ff-only %s/%s` did not bring it there: %s' % (
                behind, remote, main_b, r.err or 'main is at %s' % (now or '?')[:10])
        self.say('fast-forwarded %s from %s to %s/%s %s' % (main_b, main[:10], remote, main_b,
                                                            origin[:10]))
        return None

    def follow(self, base):
        """The base <remote>/<base> is not the commit the config was read at: read the config at
        `base`, its "staging" section from <remote>/<main> as fetched (kitlib.config.load_at
        staging_from: the base's gate and owner rules, main's staging setting). None when it names
        the same main branch, remote and work base and still takes this landing
        (config_refusal); then self.expect_main and self.cfg are the base's, so the locked checks,
        the landing tree, the merged gate and the records all use it. Else why not (REFUSED,
        nothing merged; self.cfg and self.expect_main unchanged)."""
        was, remote = self.expect_main, self.cfg.remote
        main_b, staged = self.checkout_branch, self.base_branch
        # An unresolved <remote>/<main> is passed by name: git show refuses it (fail closed)
        tracking = self.ref(self.main_tracking()) or self.main_tracking()
        try:
            cfg = kconfig.load_at(self.root, base, staging_from=tracking)
            same = (cfg.main_branch, cfg.remote) == (main_b, remote)
            moved = kconfig.work_base(cfg) != staged
        except ConfigError as e:
            return 'main moved on from %s to %s, and the config there: %s' % (
                was[:10], base[:10], e)
        if not same:
            return ('main moved on from %s to %s, and the config there names another main '
                    'branch or remote than this landing holds: land again (the next landing '
                    'reads the config at %s)' % (was[:10], base[:10], base[:10]))
        if moved:
            return ('main moved on from %s to %s, and %s/%s now names another staging branch '
                    'than this landing holds (%s, not %s): land again' % (
                        was[:10], base[:10], remote, main_b, kconfig.work_base(cfg), staged))
        old = self.cfg
        self.cfg, self.expect_main = cfg, base
        why = self.config_refusal()
        if why:
            self.cfg, self.expect_main = old, was
            return 'main moved on from %s to %s, and by its config: %s' % (
                was[:10], base[:10], why)
        return None

    def behind_hint(self, main, origin):
        """'; <main> is only behind ...: fast-forward it with: <command>' when the local main is
        an ancestor of the tracking ref (nothing unpushed, main just not caught up), else ''."""
        if not main or not origin or main == origin or self.staging:
            return ''
        behind, _ = refs.is_ancestor(main, origin, self.root)
        if behind is not True:
            return ''
        return ('; %s is only behind %s/%s, with nothing unpushed: fast-forward it with: '
                'git -C "%s" merge --ff-only %s/%s' % (
                    self.base_branch, self.cfg.remote, self.base_branch, self.root,
                    self.cfg.remote, self.base_branch))

    def tree_path(self):
        """Where the landing tree is: Lander(tree=), else <main checkout>/<worktrees_dir>/_landing
        (TREE_NAME), the folder agent worktrees go in (kitlib.worktree.prep keeps it out of git
        status)."""
        if self.tree_given:
            return os.path.normpath(os.path.abspath(self.tree_given))
        return os.path.normpath(os.path.join(self.root, self.cfg.repo['worktrees_dir'], TREE_NAME))

    def tree_head(self):
        sha, _ = refs.resolve('HEAD', self.tree)
        return sha

    def prepare_tree(self):
        """Step 5b: the landing tree (self.tree) detached at the base, nothing else in it. Made
        the first time (`git worktree add --detach --lock`); reused when it is clean (a checkout of
        the base; its ignored files, such as build caches, stay); recreated, with a line saying
        why, when it is gone, dirty, mid-merge, not a work tree of its own, or cannot be put at
        the base. None, or why not (REFUSED, nothing merged): the main checkout inside it, a
        folder there that is not the landing tree (left as it is: `move it aside`), or git
        failing."""
        tree = self.tree = self.tree_path()
        if repo.inside(self.root, tree):
            return ('the landing tree %s is the main checkout or holds it; it must be a folder of '
                    'its own' % tree)
        wts, why = repo.worktrees(self.root)
        if wts is None:
            return 'git worktree list failed: %s' % why
        entry = next((w for w in wts[1:] if repo.same_path(w.path, tree)), None)
        if entry is None:
            if os.path.isdir(tree) and os.listdir(tree):
                return ("the landing tree's place %s is a folder git does not list as a worktree; "
                        "it was left as it is: move it aside" % tree)
            return self.add_tree(None)
        if not entry.detached or entry.locked != TREE_LOCK:
            return ("the landing tree's place %s is a worktree of this repository (%s), not the "
                    "landing tree; it was left as it is: move it aside" % (
                        tree, 'on %s' % entry.branch if entry.branch else
                        'locked: %s' % entry.locked if entry.locked else 'not locked'))
        why = self.tree_problem(entry)
        if why:
            return self.add_tree(why)
        r = self.git('checkout', '--quiet', '--detach', self.expect_main, cwd=tree,
                     timeout=PUSH_TIMEOUT)
        entries, _ = gitstatus.status(tree)
        if r.rc == 0 and self.tree_head() == self.expect_main and entries == []:
            return None
        return self.add_tree('could not be put at the base %s (%s)' % (
            self.expect_main[:10], (r.err or 'exit %s' % r.rc) if r.rc else 'not clean there'))

    def tree_problem(self, entry):
        """Why the listed landing tree `entry` cannot be reused as it is, or None."""
        tree = self.tree
        if entry.prunable is not None or not os.path.isdir(tree):
            return 'is gone'
        top, why = cmd.run_text(['rev-parse', '--show-toplevel'], tree)
        if not top or not repo.same_path(top, tree):
            return 'is not a work tree of its own (git top: %s)' % (top or why or 'none')
        merging, why = refs.merge_in_progress(tree)
        if merging is not False:
            return 'has a merge in progress' if merging else 'cannot be read (%s)' % why
        entries, why = gitstatus.status(tree)
        if entries is None:
            return 'cannot be read: git status failed (%s)' % why
        if entries:
            return 'is not clean (%s)' % _paths(entries)
        return None

    def add_tree(self, why):
        """Make the landing tree at the base: `why` None, the first time; else remove what is
        there first (it is the kit's own tree: unlocked, removed with --force --force, a gone one
        pruned) and say `the landing tree <path> <why>: recreated`. None, or why not."""
        tree = self.tree
        if why is not None:
            self.git('worktree', 'unlock', tree)     # not locked any more: git says so, no matter
            if os.path.isdir(tree):
                self.git('worktree', 'remove', '--force', '--force', tree, timeout=PUSH_TIMEOUT)
            else:
                self.git('worktree', 'prune')        # the gone tree's registration
            if os.path.isdir(tree):
                try:
                    _rmtree(tree)
                except OSError as e:
                    return ('the landing tree %s %s, and it could not be removed (%s); remove the '
                            'folder by hand, then land again' % (tree, why, e))
                self.git('worktree', 'prune')
        else:
            os.makedirs(os.path.dirname(tree), exist_ok=True)
        r = self.git('worktree', 'add', '--detach', '--lock', '--reason', TREE_LOCK, tree,
                     self.expect_main, timeout=PUSH_TIMEOUT)
        if r.rc != 0:
            return 'git worktree add %s failed: %s' % (tree, r.err or 'exit %s' % r.rc)
        entries, err = gitstatus.status(tree)
        if self.tree_head() != self.expect_main or entries != []:
            return 'the landing tree %s, just added, is not clean at the base %s: %s' % (
                tree, self.expect_main[:10], _paths(entries) if entries else err or 'HEAD differs')
        self.say('the landing tree %s %s: recreated' % (tree, why) if why else
                 'the landing tree %s: made' % tree)
        return None

    def do_merge(self):
        """Merge each branch in turn with --no-ff in the landing tree (self.merges): None when
        all merged, else the NOT LANDED Outcome: a branch that does not merge cleanly (that merge
        aborted, the earlier ones dropped; the CONFLICT verdict line), or a merge git reported
        that is not the merge asked for. Main is never moved."""
        self.lock.set_phase(self.token, 'merge')
        self.phase = 'merge'
        main_b, base = self.base_branch, self.expect_main
        for b, t in self.pairs:
            prev = self.merges[-1] if self.merges else base
            title = 'Merge %s%s' % (b, ': %s' % self.summary if self.summary else '')
            r = self.git('merge', '--no-ff', '--no-edit', '-m', title, t, timeout=MERGE_TIMEOUT,
                         cwd=self.tree)
            if r.rc == 0:
                head = self.tree_head()
                first, _ = cmd.run_text(['rev-parse', 'HEAD^1'], self.tree)
                second, _ = cmd.run_text(['rev-parse', 'HEAD^2'], self.tree)
                if not head or first != prev or second != t:
                    return G.Outcome(G.FAILED, [
                        'NOT LANDED: the merge of %s reported success, and HEAD of the landing tree '
                        '(%s) is not a merge of %s onto %s; nothing was pushed, and the landing '
                        'did not move %s from the base %s' % (
                            b, (head or '?')[:10], t[:10], prev[:10], main_b, base[:10])],
                        undone=True)
                self.merges.append(head)
                self.merge = head
                continue
            conflict = r.err or cmd.text(r.out).strip() or 'exit %s' % r.rc
            lines = []
            merging, _ = refs.merge_in_progress(self.tree)
            if merging:
                a = self.git('merge', '--abort', timeout=MERGE_TIMEOUT, cwd=self.tree)
                if a.rc != 0:
                    lines.append('WARNING: `git merge --abort` failed in the landing tree %s (%s); '
                                 'the next landing recreates it' % (
                                     self.tree, a.err or 'exit %s' % a.rc))
            why = '%s does not merge cleanly into %s: %s; the merge was aborted' % (
                b, main_b, conflict)
            if self.merges:
                why += (', and the merge of %s before it was undone (the landing did not move %s '
                        'from the base %s)') % (
                    ', '.join(self.branches[:len(self.merges)]), main_b, base[:10])
            return G.Outcome(G.FAILED, [why] + lines + [
                'CONFLICT %s with main %s: the author merges main' % (b, base)],
                undone=bool(self.merges), conflict=b)
        return None

    def tracked_and_untracked(self, where=None):
        """`(tracked entries, untracked paths)` of the status of `where` (default the main
        checkout); None, None when git cannot say."""
        entries, _ = gitstatus.status(where or self.root)
        if entries is None:
            return None, None
        return ([(xy, p) for xy, p in entries if xy != '??'],
                [p for xy, p in entries if xy == '??'])

    def unchanged(self):
        """None when the landing tree is exactly the merge the gate passed (HEAD at the merge, no
        tracked change, no untracked file) and the repository's stash stack is still empty
        (kitlib.gate.stash; the locked checks found it empty), else why not."""
        problems = []
        head = self.tree_head()
        if head != self.merge:
            problems.append('HEAD moved in the landing tree while the merged gate ran (HEAD %s; '
                            'the gated merge is %s)' % ((head or '?')[:10], self.merge[:10]))
        tracked, untracked = self.tracked_and_untracked(self.tree)
        if tracked is None:
            problems.append('git status failed in the landing tree')
        else:
            if tracked:
                problems.append('a step changed tracked files: %s' % _paths(tracked))
            if untracked:
                problems.append('a step left untracked files: %s' % ', '.join(untracked[:10]))
        stashed = stash.appeared(self.root)
        if stashed:
            problems.append(stashed)
        return '; '.join(problems) or None

    def merge_batch(self):
        """do_merge, dropping each folded submission whose branch does not merge cleanly: given
        back dropped (it lands alone, at its own turn), the landing tree put back at the base,
        and the rest merged again. None, or do_merge's Outcome for a branch of the landing's
        own; a landing tree that cannot be put back is NOT LANDED."""
        while True:
            failed = self.do_merge()
            b = failed.facts.get('conflict') if failed is not None else None
            fold = self.fold_of(b) if b else None
            if fold is None:
                return failed
            self.say('dropping %s (run %s) from the batch: %s' % (
                '+'.join(fold['unit']['branches']), fold['unit']['run_id'], failed.lines[0]))
            self.unfold(fold, True, '%s does not merge cleanly onto the batch before it' % b)
            self.merges, self.merge = [], None
            why = self.prepare_tree()
            if why:
                return G.Outcome(G.FAILED, [
                    'NOT LANDED: the landing tree could not be put back at the base once %s was '
                    'dropped from the batch: %s; nothing was pushed, and the landing did not move '
                    '%s from the base %s' % (b, why, self.base_branch, self.expect_main[:10])],
                    undone=True)
