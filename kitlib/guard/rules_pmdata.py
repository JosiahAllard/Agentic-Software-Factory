"""The guard's push rules and the PM data checks they share (kitlib.guard.rules, which re-exports each
name here): git push from the PM data worktree, --no-verify and a changed HOME where a PM data record
exists, a push the guard cannot tell holds no PM data, and the main branch's gate-OK check."""
import os
from kitlib import pmdata
from kitlib.git import repo

from . import commands as cm
from . import paths
from .rules_base import agent_session, fwd, is_owner_session, kit_tool, quote, refuse
from .rules_gitcore import _canon_long, _opts


class Judged(object):
    """`ctx` as one judgement of the shared repository sees it from the PM data worktree: config,
    config_error and main_top answer `top`'s .kit.json (`cfg`, or None and `err`), and no place
    is the PM data worktree again."""

    def __init__(self, ctx, top, cfg, err):
        self._ctx, self._top, self._cfg, self._err = ctx, top, cfg, err

    def __getattr__(self, name):
        return getattr(self._ctx, name)

    def config(self, path):
        return self._cfg

    def config_error(self, path):
        return self._err

    def main_top(self, path):
        return self._top

    def is_pm_data(self, path):
        return False


class DataBranch(object):
    """The PM data worktree's own Config, with its data branch as the branch a push is judged for."""

    def __init__(self, cfg, branch):
        self._cfg, self.main_branch = cfg, branch

    def __getattr__(self, name):
        return getattr(self._cfg, name)


def kit_judged(ctx, where):
    """(Judged by the kit's own .kit.json, None) for git run in the PM data worktree `where`, which
    acts on refs and worktrees of the repository it shares with the kit; (None, why) when that
    .kit.json cannot be read, and the caller refuses."""
    top = ctx.repo_main_top(where)
    if not top:
        return None, 'the main checkout of the repository %s belongs to cannot be found' % where
    cfg = ctx.config_at(top)
    err = ctx.config_error_at(top)
    if cfg is None:
        return None, 'its .kit.json in %s does not load (%s)' % (top, err or 'no .kit.json')
    return Judged(ctx, top, cfg, None), None


def git_push_pm_data(event, ctx, gc, role):
    """git push from the PM data worktree: every ref but the data branch is judged by the kit's own
    .kit.json (main checkout's rules), the data branch by the data worktree's own. Both judgements
    run the git-push-pmdata checks too (git_push)."""
    kit, why = kit_judged(ctx, gc.dir)
    if kit is None:
        return refuse('git-push-main', role, '`git push` in the PM data worktree, where the kit\'s rules for the shared '
                      'repository cannot be read: %s. To see the worktrees:' % why, 'git -C %s worktree list' % quote(gc.dir))
    d = git_push(event, kit, gc, role)
    if d:
        return d
    cfg = ctx.config(gc.dir)
    cfg = DataBranch(cfg, ctx.pm_data_branch(gc.dir)) if cfg is not None else None
    return git_push(event, Judged(ctx, ctx.main_top(gc.dir), cfg, ctx.config_error(gc.dir)), gc, role)


def own_worktree(ctx, cwd, main_top=None):
    """The top of the linked worktree the call runs in (of `main_top`, when given), or None."""
    here_ = ctx.place(cwd) if cwd else None
    if here_ is None or here_.kind != repo.LINKED or not here_.top:
        return None
    if main_top is not None:
        mine = ctx.main_top(cwd)
        if not mine or paths.norm(mine) != paths.norm(main_top):
            return None
    return here_.top


# A write the guard cannot place: a computed or unread target, code given on the line (python -c,
# node -e), a git or .NET call whose directory or path the text does not tell, a place git cannot
# tell. Its default mode is warn (kitlib.guard.modes): only a write that resolves literally into a
# main checkout or outside an agent session's folders is denied (docs/guard.md "Rule modes").
UNPLACED = 'unplaced'


def _branch_of(ref):
    return ref[len('refs/heads/'):] if ref.startswith('refs/heads/') else ref


def pmdata_recorded(ctx, path):
    """True when the repository `path` is in has a PM data record (kit-pm-data.json in its git
    common dir, kitlib.pmdata), and when git cannot place `path` (fail closed); False outside git.
    The file's existence only: it is not parsed, and no git runs beyond the cached place."""
    if not path:
        return True
    place = ctx.place(path)
    if place.kind == repo.NONE:
        return False
    if place.kind not in (repo.MAIN, repo.LINKED) or not place.common:
        return True
    return os.path.exists(os.path.join(place.common, pmdata.FILE_NAME))


def _no_verify_word(word):
    t = word.text
    return t.startswith('--') and 'no-verify' in _canon_long('push', t[2:].partition('=')[0])


def _push_names_branches(pos):
    """True when a `git push`'s positional words (remote, then refspecs) name at least one refspec
    and none takes its source from HEAD or @: a push the same from any checkout of the repository."""
    specs = [w.text for w in pos[1:]]
    if not specs:
        return False
    for t in specs:
        src = t.lstrip('+').partition(':')[0]
        if src.startswith('@') or src.startswith('HEAD'):
            return False
    return True


def git_push_pmdata(event, ctx, gc, role):
    """`git push --no-verify` (any abbreviation git accepts), and a `git push` with a word computed
    at run time or arguments xargs appends, or git with a computed subcommand or one xargs
    supplies (any of them may be --no-verify or push), in a
    repository with a PM data record, in every session but the owner's own: the pre-push hook is
    what keeps PM data off public remotes (docs/pm-data-branch.md "Pre-push guard")."""
    if is_owner_session(event, ctx):
        return None
    xargs = gc.inv is not None and gc.inv.via == 'xargs'
    computed = gc.sub_dyn or xargs or any(w.dyn for w in gc.rest)
    _short, longs, _pos = _opts('push', gc.rest)
    if not computed and 'no-verify' not in longs:
        return None
    bare_xargs = xargs and gc.sub == ''
    if bare_xargs and agent_session(event, ctx):
        return None                         # the write jail denies it for them (git_jail_unknown)
    # git with no subcommand run by xargs: the input names the repository (-C, --git-dir) and the
    # command, so no record can be looked up for it: denied whatever the cwd holds (fail closed)
    where = None if bare_xargs else gc.dir
    if not pmdata_recorded(ctx, where):     # None: the text cannot place it (recorded, fail closed)
        return None
    if bare_xargs:
        return refuse('git-push-pmdata', role, '`xargs git` takes its subcommand and repository from its input: the '
                      'guard cannot tell which repository or command xargs will run git with, and a push with '
                      '--no-verify would skip the pre-push hook that keeps PM data off public remotes. Run git '
                      'with its subcommand written out:', 'git -C "<checkout>" <subcommand> <arguments>')
    if computed:
        what = ('a git subcommand' if gc.sub_dyn else
                'arguments xargs appends to `git push`' if xargs else 'a `git push` word')
        return refuse('git-push-pmdata', role, '`git %s %s` has %s computed at run time, and this repository has %s: '
                      'the guard cannot judge a computed push word with PM data present (it may be --no-verify, '
                      'which skips the pre-push hook). Write the push literally:'
                      % (gc.sub, ' '.join(w.text for w in gc.rest), what, pmdata.FILE_NAME),
                      'git -C %s push <remote> <branch>' % (quote(where) if where else '"<checkout>"'))
    words, cut = [], False
    for w in gc.rest:
        if not cut and w.text == '--':
            cut = True
        if cut or not _no_verify_word(w):
            words.append(w.text)
    # an agent session's push from the main checkout it may not write: the same push from its own
    # worktree of that checkout, which shares its branches and remotes (the write jail lets that one
    # through), only when it names each branch: with none, or HEAD (@), it pushes the branch checked
    # out where it runs, the worktree's there, so it stays in the main checkout
    run_in = gc.dir
    if gc.dir and agent_session(event, ctx) and gc.inv is not None and ctx.place(gc.dir).kind == repo.MAIN \
            and _push_names_branches(_pos):
        run_in = own_worktree(ctx, gc.inv.cmd.cwd, ctx.main_top(gc.dir)) or gc.dir
    return refuse('git-push-pmdata', role, '`git push %s` skips the pre-push hook, which keeps PM data off public '
                  'remotes in this repository (it has %s). Push with the hook:'
                  % (' '.join(w.text for w in gc.rest), pmdata.FILE_NAME),
                  ('git -C %s push %s' % (quote(run_in), ' '.join(words))).rstrip())


def home_config(event, ctx, gc, role):
    """git push on a line that sets HOME, XDG_CONFIG_HOME, USERPROFILE, HOMEDRIVE or HOMEPATH
    (commands.HOME_ENV_NAMES, Command.home_env), in a repository with a PM data record, in every
    session but the owner's own (rule git-push-pmdata): git would read another user config, whose
    url.<base>.pushInsteadOf can send the push to a public remote while the pre-push hook, reading
    the same config, takes it for the private one."""
    name = gc.inv.cmd.home_env if gc.inv is not None else False
    if not name or is_owner_session(event, ctx) or not pmdata_recorded(ctx, gc.dir):
        return None
    return refuse('git-push-pmdata', role, '`git push %s` runs on a line that sets %s, and this repository has %s: git '
                  'would read the user config of another folder, whose url.<base>.pushInsteadOf can send the push '
                  'to a public remote while the pre-push hook, reading the same config, takes it for the private '
                  'one. Push without setting %s:' % (' '.join(w.text for w in gc.rest), name, pmdata.FILE_NAME, name),
                  ('git -C %s push %s' % (quote(gc.dir), ' '.join(w.text for w in gc.rest))).rstrip())


def data_copy(event, ctx, where, role, what):
    """A push or send-pack (`what`) from a repository without a PM data record of its own, when
    the machine config names pm_data but the guard cannot read the machine's record, in every
    session but the owner's own: it fails closed, so while the data worktree folder is deleted
    every such push is denied until the owner re-adds it with git (docs/pm-data-branch.md
    "Failure modes"; init refuses while the PM-repo pointer is set). Whether the repository holds a copy of the data (its root commit, a shallow
    clone) is not read: the guard catches honest mistakes, not evasion."""
    if is_owner_session(event, ctx) or not where:
        return None
    if ctx.place(where).kind not in (repo.MAIN, repo.LINKED):
        return None
    root, why = ctx.pm_root()
    if root is None and why is None:
        return None                             # the machine config names no pm_data
    if root is None:
        path = ctx.pm_data_path() or ctx.pm_repo_pointer()
        path = quote(path) if path else '"<pm_data.path>"'
        return refuse('git-push-pmdata', role, '%s: the repository %s has no %s of its own, and the guard cannot read '
                      'the machine\'s PM data record to tell whether it holds PM data (%s), so it fails closed. When '
                      'the data worktree folder was deleted (docs/pm-data-branch.md "Failure modes"), the owner '
                      're-adds it from the PM repository\'s main checkout, one git command at a time (pm_data.py init '
                      'refuses while the PM-repo pointer is set):' % (what, fwd(where), pmdata.FILE_NAME, why),
                      'git -C "<PM repo main checkout>" worktree unlock %s, then worktree remove %s, worktree add '
                      '%s <data branch>, worktree lock --reason "PM data" %s' % (path, path, path, path))
    return None


def _unknown_dir_counts(ctx, gc):
    """For a git call whose directory the text cannot tell: True when the machine config names
    pm_data (or cannot be read), or the directory the command itself runs in has a PM data record
    (pmdata_recorded), or the folder a Start-Process -WorkingDirectory starts it in
    (commands.run_dir) has one. Otherwise an unknown directory is no ground to deny."""
    if gc.inv is None:
        return bool(ctx.pm_data_named())
    heres = [d for d in (gc.inv.cmd.cwd, cm.run_dir(gc.inv)) if d]
    return bool(ctx.pm_data_named() or any(pmdata_recorded(ctx, d) for d in heres))


def git_push(event, ctx, gc, role):
    if ctx.is_pm_data(gc.dir):
        return git_push_pm_data(event, ctx, gc, role)
    rest = ' '.join(w.text for w in gc.rest)
    if gc.unknown or gc.dir is None:
        return refuse('git-push-main', role, '`git push` runs in a directory the guard cannot tell, so it cannot '
                      'check for a gate-OK record. Name the checkout literally:',
                      ('git -C "<checkout>" push %s' % rest).rstrip())
    d = git_push_pmdata(event, ctx, gc, role) or home_config(event, ctx, gc, role)
    if d:
        return d
    if not pmdata_recorded(ctx, gc.dir):
        d = data_copy(event, ctx, gc.dir, role, '`%s`' % ('git push %s' % rest).rstrip())
        if d:
            return d
    cfg = ctx.config(gc.dir)
    if cfg is None:
        err = ctx.config_error(gc.dir)
        if not err:
            return None                         # no checkout: no main branch to judge
        top = ctx.main_top(gc.dir) or gc.dir
        return refuse('git-push-main', role, '`git push` in a repository whose .kit.json does not load (%s). Fix it '
                      'so it loads (the message names the key; docs/kit-json.md lists them), then push. To see '
                      'the error:' % err, 'python -m json.tool %s' % quote(os.path.join(top, '.kit.json')))
    # A delete or a forced push of main is refused in every repository, with or without a
    # .kit.json (docs/kit-json.md "With no file"); push_block adds the gate-OK record and
    # --no-verify checks. The PM data branch (DataBranch) is judged by its own push_block only.
    gated = cfg.push_block
    if not gated and isinstance(cfg, DataBranch):
        return None
    main = cfg.main_branch
    mains = _guarded_mains(cfg)
    short, longs, pos = _opts('push', gc.rest)
    delete = 'd' in short or 'delete' in longs
    force = 'f' in short or bool({'force', 'force-with-lease', 'force-if-includes', 'mirror'} & longs)
    no_verify = 'no-verify' in longs
    dyn = [w for w in pos if w.dyn]
    # A computed remote or refspec with a flag or a literal `+` or `:` that forces, deletes or
    # skips the hook is refused. Without one it is a push the guard cannot place: a warning by
    # default (a computed source or destination is left to the pre-push
    # hook), and the literal refspecs are judged below.
    marked = any(w.text.lstrip('"\'').startswith(('+', ':')) for w in dyn)
    if dyn and (gated or delete or force or no_verify or any(w.dyn for w in pos[1:])):
        remote = pos[0].text if pos and not pos[0].dyn else cfg.remote
        d = refuse('git-push-main' if delete or force or no_verify or marked else UNPLACED, role,
                   '`git push %s` has a refspec that is not literal, so the guard cannot '
                   'tell whether it %s %s. Name the refs literally:'
                   % (rest, 'pushes' if gated else 'rewrites or deletes', main),
                   'git -C %s push %s <branch>' % (quote(gc.dir), remote))
        if d:
            return d
    every = bool({'all', 'mirror', 'branches'} & longs)
    remote = pos[0].text if pos else cfg.remote
    specs = [w.text for w in pos[1:]]
    branch, _why = ctx.git.current_branch(gc.dir)
    targets = []
    if every:
        targets.extend(('refs/heads/' + m, m, False) for m in mains)
    if not specs and not every:
        if branch:
            targets.append((branch, branch, False))
    for s in specs:
        plus = s.startswith('+')
        s = s.lstrip('+')
        src, colon, dst = s.partition(':')
        if not colon:
            dst = branch if src == 'HEAD' else src
        if delete:
            src, dst = '', dst or src
        targets.append((src, _branch_of(dst or ''), plus))
    for src, dst, plus in targets:
        if dst not in mains:
            continue
        main = dst
        what = '`git push %s` to %s/%s' % (' '.join(w.text for w in gc.rest), remote, main)
        if not src:
            return refuse('git-push-main', role, '%s deletes %s; main is never deleted. To see where it is:' %
                          (what, main), 'git -C %s ls-remote %s %s' % (quote(gc.dir), remote, main))
        if not gated:
            if force or plus:
                return refuse('git-push-main', role, '%s rewrites %s; main is never force-pushed. Merge %s/%s into '
                              'the branch, then push it without force:' % (what, main, remote, main),
                              'git -C %s push %s %s' % (quote(gc.dir), remote, ' '.join(
                                  s.lstrip('+') for s in specs) or main))
            continue
        land = landing_command(ctx, gc, cfg, remote, branch)
        if no_verify:
            return refuse('git-push-main', role, '%s skips the pre-push hook. %s' % (what, LAND_HOW), land)
        if force or plus:
            return refuse('git-push-main', role, '%s rewrites %s. %s' % (what, main, LAND_HOW), land)
        sha, why = ctx.git.resolve(src, gc.dir)
        if not sha:
            return refuse('git-push-main', role, '%s: cannot resolve %s (%s). %s' % (what, src, why, LAND_HOW), land)
        rec, why = ctx.gate_ok(gc.dir, sha)
        if rec is None:
            return refuse('git-push-main', role, '%s pushes %s, which has no gate-OK record (%s). %s' %
                          (what, sha[:12], why, LAND_HOW), land)
    return None


LAND_HOW = 'Land it with the gate, which records the pass and pushes:'


def _guarded_mains(cfg):
    """The branches git_push holds as main (Config.guarded_mains); the PM data worktree's, and a
    test's stand-in config without the method, just main_branch."""
    if isinstance(cfg, DataBranch) or not hasattr(cfg, 'guarded_mains'):
        return [cfg.main_branch]
    return cfg.guarded_mains()


def landing_command(ctx, gc, cfg, remote, branch):
    # No --expect-main: a landing merges onto the main it finds at its turn (docs/gate.md "Landing").
    tip, _ = ctx.git.resolve(branch, gc.dir) if branch else (None, None)
    main_top = (ctx.main_top(gc.dir) if gc.dir else None) or gc.dir
    return 'python %s -C %s %s --expect-tip %s' % (
        kit_tool(ctx, 'land'), quote(main_top) if main_top else '"<main checkout>"',
        branch if branch and branch != cfg.main_branch else '<branch>',
        tip if branch and branch != cfg.main_branch and tip else '<branch tip sha>')
