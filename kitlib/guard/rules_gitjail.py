"""The guard's git jail (kitlib.guard.rules, which re-exports each name here): the lander's landing
forms in the main checkout, git that writes a checkout outside the session's own (git_jail), where a
git line runs, and the text of a git line a refusal quotes."""
import os
import re
from kitlib import config as kconfig
from kitlib.git import repo

from . import commands as cm
from . import jail
from . import paths
from . import shell as sh
from .rules_base import SHELLS, UNPLACED, agent_session, bare, fwd, own_worktree, quote, refuse, subagent
from .rules_gitcore import git_call_judged
from .rules_edit import (JAIL_ASK, KIT_JSON, dev_drive_bound, in_dropped, jail_refusal, jail_view, jail_why,
                         new_worktree, pm_data_unknown)
from .rules_write import retarget
from .rules_gitread import git_reads, git_targets
from .rules_gitcommit import commit_alternative, stash_alternative, stash_shared


LANDER = 'lander'


LANDER_VERBS = ('fetch', 'merge')
# the tests lander_check runs, in order; a refusal names the first that failed
LANDER_NO_CONFIG = 'the main checkout\'s .kit.json does not load'
LANDER_NO_ROLE = ('the main checkout\'s .kit.json does not set roles.lander.edit_main_checkout to true, so no lander '
                  'may write it; the owner sets it (docs/guard.md "Go-live", step 3)')
LANDER_NOT_ALONE = ('git is not alone on the line (at most one literal cd, Set-Location or pushd to that checkout '
                    'may come before it, joined by && or ;)')
LANDER_WRAPPED = ('a wrapper, an environment assignment, a git option other than -C <checkout>, or a word that is '
                  'not literal comes with it')
LANDER_ARGS = 'its arguments are not exactly one of the landing forms'


def lander_forms(cfg):
    """The landing verbs a lander may run in a main checkout, as git argument lists."""
    remote, main = cfg.remote, cfg.main_branch
    return {'fetch': ([remote], [remote, main]), 'merge': (['--ff-only', '%s/%s' % (remote, main)],)}


def lander_alone(event, cmd, gc, home=None):
    """True when `cmd` (git) is the whole command line of `event`, or follows one literal cd,
    Set-Location or pushd into the checkout git runs in, joined by && or ;. The line is read again
    from the event, `~` as `home` (ctx.home); an unclean parse, a pipe, a group, a nested shell or
    any third command is not alone."""
    shell = SHELLS.get(event.shell) if event is not None and event.kind == 'shell' else None
    text, cwd = (event.command, event.workdir or event.cwd) if event is not None else (None, None)
    if shell is None or not isinstance(text, str) or cmd.depth or cmd.nested_in is not None:
        return False
    parsed = cm.read(text, shell, cwd, home=home)
    cmds = parsed.commands
    if not parsed.clean or not cmds or len(cmds) > 2 or cmds[-1].start != cmd.start or cmds[-1].end != cmd.end:
        return False
    if any(c.depth or c.groups or c.piped_in or c.piped_out or c.nested_in is not None for c in cmds):
        return False
    if len(cmds) == 1:
        return True
    is_cd, where = sh.cd_target(cmds[0], cwd, home)
    if not is_cd or where is None or cmds[1].prev_op not in ('&&', ';') or cmds[0].redirects:
        return False
    return jail.real(where) == jail.real(gc.dir)


def lander_check(event, ctx, cmd, inv, gc):
    """(allowed, why) for a lander subagent's `git fetch` or `git merge` in a main checkout:
    (True, None) for one of the landing forms (lander_forms: `fetch <remote>`, `fetch <remote>
    <main>`, `merge --ff-only <remote>/<main>`, remote and main from that checkout's .kit.json repo
    section), alone on its line (lander_alone), with at most `-C <main checkout>` before the verb
    (git, git.exe or PowerShell's `& git`), in a checkout whose .kit.json sets
    roles.lander.edit_main_checkout: the one main-checkout write the jail leaves a lander that runs
    from a worktree. (False, <the first test that failed>) when one fails. (False, None) for any
    other session, verb or place: the other rules judge it as before."""
    if not subagent(event) or event.agent_role != LANDER:
        return False, None
    if gc.unknown or gc.sub not in LANDER_VERBS or not gc.dir or ctx.place(gc.dir).kind != repo.MAIN:
        return False, None
    # the repository's own settings first: while they refuse every landing form, a refusal that
    # named the command would offer an Instead that is refused too
    cfg = ctx.config(gc.dir)
    if cfg is None:
        return False, LANDER_NO_CONFIG
    table = kconfig.roles(cfg)
    if kconfig.role_name(event, table) != LANDER or not table[LANDER].get('edit_main_checkout'):
        return False, LANDER_NO_ROLE
    if not lander_alone(event, cmd, gc, getattr(ctx, 'home', None)):
        return False, LANDER_NOT_ALONE
    pre = gc.before()
    if inv.via is not None or inv.dyn or [w.text for w in cmd.words[:inv.index]] not in ([], ['&']) or \
            [w.text for w in pre[:1]] not in ([], ['-C']) or len(pre) not in (0, 2) or \
            any(w.dyn for w in pre) or any(w.dyn for w in gc.rest):
        return False, LANDER_WRAPPED
    if [w.text for w in gc.rest] not in lander_forms(cfg)[gc.sub]:
        return False, LANDER_ARGS
    return True, None


def lander_refusal(ctx, cmd, gc, rule, role, why):
    """The refusal for a lander's landing step that failed lander_check's test `why`: it names the
    test, the forms a lander may run, and the command to use (the config read when the config is
    the cause)."""
    top = ctx.place(gc.dir).top or gc.dir
    kit = os.path.join(top, KIT_JSON)
    cfg = ctx.config(gc.dir)
    if cfg is None or why == LANDER_NO_ROLE:
        forms = 'the landing forms'
        instead, end = 'python -m json.tool %s' % quote(kit), 'Ask the coordinator to take it to the owner. To read the file:'
    else:
        remote, main = cfg.remote, cfg.main_branch
        forms = 'git fetch %s, git fetch %s %s or git merge --ff-only %s/%s' % (remote, remote, main, remote, main)
        want = '%s %s' % (remote, main) if gc.sub == 'fetch' else '--ff-only %s/%s' % (remote, main)
        instead, end = 'git -C %s %s %s' % (quote(top), gc.sub, want), 'Run it alone, as:'
    return refuse(rule, role, '`%s` is refused as a lander\'s landing step in the main checkout %s: %s. A lander may '
                  'run only %s there, each alone on its line. %s' % (bare(cmd), fwd(top), why, forms, end), instead)


def git_jail_unknown(ctx, cmd, gc, cwd, role):
    """An agent session's git that writes a checkout the text cannot place (GIT_DIR and the rest
    of commands.GIT_ENV_NAMES set, --git-dir, --work-tree, -c core.worktree, --config-env, a
    computed -C; a -C after any of them does not place it), by any role: it may be a main
    checkout, and the main-checkout git rules judge only history writes there, so a symbolic-ref
    or branch -m that re-points main's HEAD would pass them. Only a plain read passes
    (git_reads strict; a computed word only as strict_dyn_read allows). The hint names what hides the place (git_unplaced): drop it, or name a
    computed -C literally. A computed -C is refused under UNPLACED (warn by default); the
    re-pointing forms (gc.blind) stay write-jail."""
    if git_reads(gc, strict=True):
        return None
    if gc.blind:                                # a deliberate re-pointing: denied, not unplaced
        how, instead = git_unplaced(ctx, cmd, gc, cwd, True)
        return refuse('write-jail', role, '`%s` writes a checkout the guard cannot place: %s can point git at a '
                      'git directory, work tree, index or config the text does not show, and a -C does not undo '
                      'it. %s' % (bare(cmd), gc.blind, how), instead)
    return refuse(UNPLACED, role, '`%s` writes a checkout the guard cannot place (a computed -C, or a '
                  'folder it cannot tell). Name the checkout literally:' % bare(cmd),
                  ('git -C "<checkout>" %s %s' % (gc.sub, ' '.join(w.text for w in gc.rest))).rstrip())


def git_jail(event, ctx, cmd, inv, cwd, role):
    """An agent session's git that writes a checkout (or makes a folder) outside its roots
    (kitlib.guard.jail): git -C <elsewhere> commit, a cd chain into another checkout, worktree
    add or clone to a folder outside them. Its repository's other worktrees (jail.sibling) are
    not elsewhere."""
    gc = git_call_judged(inv)
    if gc is None or not agent_session(event, ctx):
        return None
    if not gc.dir:                              # commands.git_call: no place the text can tell
        return git_jail_unknown(ctx, cmd, gc, cwd, role)
    if gc.unknown:
        return None                             # a computed subcommand: the unreadable rule's
    view = None
    for w in git_targets(gc):
        if w.dyn:
            continue
        target = sh.resolve_path(w.text, gc.dir, home=getattr(ctx, 'home', None))
        if not target:
            continue
        view = view or jail_view(event, ctx, cwd)
        rs, dev, dropped = view
        verdict, root = jail.judge(target, rs)
        what = '`%s` makes %s' % (bare(cmd), w.text)
        allowed = verdict is None or (verdict == jail.OUTSIDE and (jail.sibling(ctx, target, cwd) or
                                                                   in_dropped(target, dropped, dev)))
        if allowed and (not dev or jail.on_drive(target, dev)):
            continue
        # a word an inline alias gave has no span in the line to aim (inline_alias)
        rewrite = None if w.start is None else git_retarget(ctx, cmd, inv, gc, w, cwd)
        if allowed:
            d = dev_drive_bound(event, ctx, cwd, rs, dev, target, role, what, rewrite)
            if d:
                return d
            continue
        return jail_refusal(role, rs, verdict, root, what, target, rewrite=rewrite)
    if git_reads(gc):
        return None
    rs, dev, dropped = view or jail_view(event, ctx, cwd)
    changes = '`%s` changes the checkout %s' % (bare(cmd), fwd(gc.dir))
    landed, why = lander_check(event, ctx, cmd, inv, gc)
    if landed:
        return dev_drive_bound(event, ctx, cwd, rs, dev, gc.dir, role, changes)
    d = pm_data_unknown(ctx, gc.dir, cwd, role, '`%s`' % bare(cmd))
    if d:
        return d
    verdict, root = jail.judge(gc.dir, rs)
    if verdict is None or (verdict == jail.OUTSIDE and (jail.sibling(ctx, gc.dir, cwd) or in_dropped(gc.dir, dropped, dev))):
        return dev_drive_bound(event, ctx, cwd, rs, dev, gc.dir, role, changes)
    if why is not None:
        return lander_refusal(ctx, cmd, gc, 'write-jail', role, why)
    what = '`%s` changes the checkout %s' % (bare(cmd), fwd(gc.dir))
    dest = jail.destination(rs)
    if dest is None:
        return jail_refusal(role, rs, verdict, root, what, gc.dir)
    if ctx.place(dest.path).kind not in (repo.MAIN, repo.LINKED):
        # the folder it may write is no checkout (the temp folder, a plain folder): git there has no
        # repository to act on, so no same command is offered
        return refuse('write-jail', role, jail_why(rs, verdict, root, what, gc.dir) +
                      ' To see where this session runs:', 'pwd')
    if verdict == jail.OUTSIDE:
        why = '%s: it is outside the folders an agent session may write: %s. %s' % (what, jail.describe(rs), JAIL_ASK)
    else:
        why = jail_why(rs, verdict, root, what, gc.dir)
    note, instead = git_elsewhere(ctx, cmd, gc, dest.path, True)
    return refuse('write-jail', role, why + (' In %s, %s' % (fwd(dest.path), note) if note else
                                             ' The same command in %s:' % fwd(dest.path)), instead)


def git_elsewhere(ctx, cmd, gc, where, agent):
    """(note, command): git `gc` run again in the checkout `where`, `git -C <where> <sub> <rest>`
    and no note. In a linked worktree an agent session's commit (commit_alternative) or shared
    stash entry (stash_shared) is refused there too (git_plain_commit, git_stash_shared): the kit
    tool's form, the note saying why."""
    plain = ('', ('git -C %s %s %s' % (quote(where), gc.sub, git_rest_text(cmd, gc))).rstrip())
    if not agent:
        return plain
    place = ctx.place(where)
    if place.kind != repo.LINKED or not place.top:
        return plain
    alt = commit_alternative(ctx, gc, place.top)
    if alt is not None:
        return ('plain git writes no Co-Authored-By trailer, which the gate\'s trailer check fails. ' + alt[0],
                alt[1])
    if gc.sub == 'stash' and stash_shared(gc):
        how, instead = stash_alternative(ctx, place.top)
        return 'the stash stack is shared by every worktree of the repository. ' + how, instead
    return plain


def git_retarget(ctx, cmd, inv, gc, w, cwd):
    """rewrite(path) for the refused output word `w` of git `gc`: the same command with `w` aimed
    at `path`. A git archive with no -C of its own also names the checkout it archives: an agent's
    shell keeps no cd between calls, so the bare command run again from a main checkout archives
    main, not the branch. That is the session's own worktree of the main checkout git runs in
    (`<worktree>` when it has none, its output aimed there too), else the folder git runs in."""
    def rewrite(path):
        if gc.sub != 'archive' or inv.via is not None or inv.word is None or inv.word.start is None or \
                any(x.text.startswith('-C') for x in gc.before()):
            return retarget(cmd, w, path)
        place = ctx.place(gc.dir)
        where = gc.dir
        if place.kind == repo.MAIN and place.top:
            where = own_worktree(ctx, cwd, place.top) or '<worktree>'
            top = paths.norm(place.top)
            if jail.inside(paths.norm(path), top):
                path = fwd(where) + fwd(paths.norm(path, fold=False)[len(top):])
        line = retarget(cmd, w, path)
        at = inv.word.end - min(cmd.words[0].start, w.start)
        return line[:at] + ' -C ' + quote(fwd(where)) + line[at:]
    return rewrite


def git_in_worktree(ctx, cmd, gc, main_top, cwd, agent=False):
    """(sentence, command): the same git command in the session's own worktree of this main
    checkout, when it has one (git_elsewhere: an agent session's commit there is the kit tool's),
    else the worktree to make; git_unplaced when what hides the place is in the text."""
    if gc.blind:
        return git_unplaced(ctx, cmd, gc, cwd, agent)
    mine = own_worktree(ctx, cwd, main_top)
    if mine:
        note, instead = git_elsewhere(ctx, cmd, gc, mine, agent)
        return 'Run it in your worktree:' + (' ' + note if note else ''), instead
    no_wt, instead = new_worktree(ctx, main_top)
    if no_wt:
        return no_wt, instead
    return 'Work in a worktree and run git %s there; the lander brings it to main:' % gc.sub, instead


SAFE_WORD_RE = re.compile(r'^[\w@%+=:,./-]+$')


def spelt(text):
    """`text` as one shell word: bare when it needs no quoting, else single-quoted (double-quoted,
    with bash's escapes, when it holds a single quote)."""
    if SAFE_WORD_RE.match(text):
        return text
    if "'" not in text:
        return "'%s'" % text
    return '"%s"' % re.sub(r'(["\\$`])', r'\\\1', text)


def git_rest_text(cmd, gc):
    """The text of `gc.rest` for a hint: the words an alias the line defines gave (gc.synth, no
    span in the line) spelt as git split them, then the line's own words as written."""
    words = [spelt(w.text) for w in gc.rest[:gc.synth]]
    own = gc.rest[gc.synth:]
    if own and own[0].start is not None and own[-1].end is not None:
        words.append(cmd.source[own[0].start:own[-1].end])
    elif own:
        words.extend(spelt(w.text) for w in own)
    return ' '.join(words)


def git_unplaced(ctx, cmd, gc, cwd, agent=False):
    """(sentence, command) for git whose place `gc.blind` hides (GIT_DIR and the like, --git-dir,
    --work-tree, core.worktree, --config-env, a computed -c): drop it and run the same git
    command in the session's own worktree (git_elsewhere), or where it runs when it is in none."""
    mine = own_worktree(ctx, cwd)
    if mine:
        note, instead = git_elsewhere(ctx, cmd, gc, mine, agent)
        return 'Drop %s and run it in your worktree:%s' % (gc.blind, ' ' + note if note else ''), instead
    rest = git_rest_text(cmd, gc)
    return 'Drop %s and run it in your worktree:' % gc.blind, ('git %s %s' % (gc.sub, rest)).rstrip()
