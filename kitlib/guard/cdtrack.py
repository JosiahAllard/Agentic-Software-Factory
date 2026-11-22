"""Where each command of a parsed line runs: a cd, Set-Location, pushd or popd moves the shell for
the commands after it (docs/guard-core.md "Parsing"). track() sets each Command's cwd, cwd_home,
their link flags and cwd_alt, the other folders a cd that may fail or may not run leaves it in
(kitlib.guard.cdfail); cd_target and cd_move read one command as a cd. Moved out of shell.py as is;
shell.parse calls track().
"""
import os
import re

from . import cdfail, paths
from .shell import (BASH, CD_UNKNOWN, CD_VERBS, LINUX_BASH_CD, PS, PS_QUALIFIED_CD, flatten, home_folder,
                    home_text, ps_filesystem_relative, resolve_path)

# bash cd's own options (cd -P, cd -L -e)
BASH_CD_OPT_RE = re.compile(r'^-[LPe@]+$')
# a line that may change where bash's cd goes on Linux: CDPATH, cdable_vars (and BASHOPTS), or
# set -P / set -o physical (and SHELLOPTS) making every cd physical; cd_target then reads the
# place of every cd the line runs as unknown, in its nested shells, evals and substitutions too
BASH_ASSIGN_WORD_RE = re.compile(r'^[A-Za-z_]\w*\+?=')   # FOO=x before a command (cd_words)
BASH_CD_UNSURE_RE = re.compile(r'CDPATH|SHELLOPTS|BASHOPTS|cdable_vars|\bphysical\b|\bset\b[^;&|\n]*\s[-+]\w*P')


def cd_words(cmd):
    """The command's words, with a bash `builtin` or `command` before a cd or popd left out
    (`builtin cd ~`, `command cd x`: the cd itself runs), and a body's opening word (`then cd x`:
    a cd in the body, track). On Linux a bash assignment before it is left out too (`X=1 cd /x`
    moves the shell; CDPATH=... is read by cd_target)."""
    words = cmd.words
    if cmd.shell == BASH and len(words) > 1 and not words[0].dyn and words[0].text in ('then', 'else', 'do'):
        words = words[1:]
    if cmd.shell == BASH and paths.posix():
        k = 0
        while k < len(words) - 1 and BASH_ASSIGN_WORD_RE.match(words[k].text):
            k += 1
        words = words[k:]
    if (cmd.shell == BASH and len(words) > 1 and not words[0].dyn and words[0].text in ('builtin', 'command')
            and is_cd_word(words[1].text, cmd.shell)):
        return words[1:]
    return words


def is_cd_word(text, shell):
    """True when `shell` runs the command word `text` as a cd verb (CD_VERBS, CD_UNKNOWN) in any
    case; bash on Linux only by its exact name (LINUX_BASH_CD)."""
    if shell == BASH and paths.posix():
        return text in LINUX_BASH_CD
    return text.lower() in CD_VERBS | CD_UNKNOWN


def cd_target(cmd, cwd, home=None, home_vars=False, linked=False):
    """(is a cd, the directory after it): cd_move without the link flag."""
    return cd_move(cmd, cwd, home, home_vars, linked)[:2]


def cd_move(cmd, cwd, home=None, home_vars=False, linked=False):
    """(is a cd, the directory after it, entered through a link): None when the text cannot tell.
    `linked` says `cwd` was entered through a link (Command.cwd_link; _linux_cd). `~` is `home`
    (home_folder); with `home_vars`, a target that starts with a home variable (home_text) is
    read too, bash cd's own options and a `--` are passed over, and a bash or PowerShell cd with
    no place goes home (bash's cd, and PowerShell 7's; Windows PowerShell's stays put, but only a
    deny reads it). A bash cd behind `builtin` or `command` is the cd itself (cd_words), and
    PowerShell's `cd $null` is a cd with no place. A PowerShell cd to a relative FileSystem:: path
    (ps_filesystem_relative: FileSystem::., FileSystem::sub) goes where the text cannot tell:
    PowerShell reads it from the process's directory, which no cd before it moved. A PowerShell
    `Microsoft.PowerShell.Management\\Set-Location` (or Push-, Pop-Location) is that cmdlet
    (PS_QUALIFIED_CD); any other module-qualified cd word is a cd to a place unknown. Bash on
    Linux runs cd, pushd and popd by their exact name only (is_cd_word), and its `cd /d` goes to
    the folder /d (cmd's drive switch is Windows')."""
    words = cd_words(cmd)
    if not words:
        return False, cwd, linked
    verb = words[0].text.lower()
    linux_bash = cmd.shell == BASH and paths.posix()
    if linux_bash and words[0].text not in LINUX_BASH_CD:
        return False, cwd, linked               # `CD x`, `chdir x`: no cd bash runs on Linux
    # CDPATH, or cd made physical by set -P, on the line: a place starting with `/` and holding no
    # `..` goes where its text says all the same
    unsure = linux_bash and bool(BASH_CD_UNSURE_RE.search(cmd.source or '') or BASH_CD_UNSURE_RE.search(cmd.line or ''))
    name = re.split(r'[\\/]', verb)[-1]
    if cmd.shell == PS and name != verb and name in CD_VERBS | CD_UNKNOWN:
        if verb not in PS_QUALIFIED_CD:
            return True, None, False            # a qualified cd word PowerShell does not run
        verb = PS_QUALIFIED_CD[verb]
    if verb in CD_UNKNOWN:
        return True, None, False
    if verb not in CD_VERBS:
        return False, cwd, linked
    rest = words[1:]
    if cmd.shell == PS and len(rest) == 1 and rest[0].text.lower() == '$null':
        rest = []                               # no place, never a folder named $null
    if rest and rest[0].text.lower() in ('-path', '-literalpath', '-lp'):
        rest = rest[1:]
    elif rest and verb in ('cd', 'chdir') and not linux_bash and rest[0].text.lower() == '/d':
        rest = rest[1:]
    logical = linux_bash                        # bash's cd -L: `..` taken from the text, not the links
    if home_vars and cmd.shell in (BASH, PS):
        while cmd.shell == BASH and rest and not rest[0].dyn and BASH_CD_OPT_RE.match(rest[0].text):
            for ch in rest[0].text[1:]:
                if ch in 'LP':
                    logical = linux_bash and ch == 'L'
            rest = rest[1:]
        if rest and not rest[0].dyn and rest[0].text == '--':
            rest = rest[1:]
        if not rest and verb in ('cd', 'chdir', 'set-location', 'sl'):
            if unsure:
                return True, None, False
            if linux_bash:
                return (True,) + _linux_cd(home_folder(home), cwd, home, logical, linked)
            return True, resolve_path(home_folder(home), cwd, cmd.shell, home, logical=logical), False
    if len(rest) != 1:
        return True, None, False
    p = rest[0].text
    if rest[0].dyn:
        p = home_text(p, home, dyn=True) if home_vars else None
        if p is None:
            return True, None, False
    if p in ('-', '+') or p.startswith('-') or any(ch in p for ch in '*?['):
        return True, None, False
    if unsure and not (p.startswith('/') and '..' not in p.split('/')):
        return True, None, False
    if linux_bash and p.startswith('+'):
        return True, None, False                # pushd +N turns the stack
    if cmd.shell == PS and ps_filesystem_relative(p) is not None:
        return True, None, False                # read from the process's directory, not the location
    if linux_bash:
        return (True,) + _linux_cd(p, cwd, home, logical, linked)
    return True, resolve_path(p, cwd, cmd.shell, home, logical=logical), False


def _linux_cd(p, cwd, home, logical, linked=False):
    """(where bash's cd to `p` from `cwd` goes on Linux, entered through a link): the folder is a
    real path (realpath), None when the text cannot tell; it need not exist yet. `linked` says
    `cwd` was entered through a link, so the shell's $PWD is another text than `cwd`. A place with
    no `..` is the real path of its text: bash's -L and -P readings land on the same folder. It
    is entered through a link when its logical text (or $PWD before it) is not that real path. A
    place with a `..` (cd -L, `logical`): bash folds it from $PWD's text, and takes the unfolded
    text when the folded folder is missing; so after a cd through a link, or when the real path
    of the folded text is not the real path of the unfolded one, it is unknown. cd -P reads the
    text physically, and its $PWD is that real path."""
    phys = resolve_path(p, cwd, BASH, home)
    if phys is None:
        return None, False
    real = paths.norm(phys, real=True, fold=False)
    if not logical:
        return real, False
    text = resolve_path(p, cwd, BASH, home, logical=True)
    if '..' in p.split('/'):
        if linked or paths.norm(text, real=True, fold=False) != real:
            return None, False
        return real, text != real
    return real, text != real or (linked and not p.startswith(('/', '~')))


def chdir_real(path, outer=None):
    """The folder a program started in `path` (env -C, Start-Process -WorkingDirectory) runs in, as
    cd_target keeps a folder: on Linux its real path, None when it is no existing folder, or when
    it is the folder of `outer` (the starting command's) but `outer` is not that real path: a bash
    started there keeps the inherited $PWD, which a later cd -L folds from. Elsewhere `path`."""
    if path is None or not paths.posix():
        return path
    real = paths.norm(path, real=True, fold=False)
    if not os.path.isdir(real):
        return None
    if outer and outer != real and paths.norm(outer, real=True, fold=False) == real:
        return None
    return real


BASH_PREFIX_WORDS = {'if', 'then', 'else', 'elif', 'do', 'while', 'until', '!', 'time'}
BASH_BODY_OPEN, BASH_BODY_SPLIT = {'if', 'while', 'until', 'for', 'select', 'case'}, {'else', 'elif', 'fi', 'done', 'esac'}


def _keyword_cd(c):
    """A cd behind a bash keyword (if cd x; then): it runs, but whether it ran is unknown. After a
    body's opening word alone (then cd x) it is a cd in the body (_body_ends)."""
    if c.shell != BASH:
        return False
    k = 0
    while k < len(c.words) and c.words[k].text in BASH_PREFIX_WORDS:
        k += 1
    cond = any(w.text not in ('then', 'else', 'do') for w in c.words[:k])
    if k + 1 < len(c.words) and c.words[k].text in ('builtin', 'command'):
        k += 1
    return cond and k < len(c.words) and is_cd_word(c.words[k].text, c.shell)


def _body_ends(cmds):
    """{index of a bash command in an if or loop body: the index of the body's last command (before
    its else, elif, fi or done)}; in a case body None (a later pattern's commands may run instead)."""
    ends, stack = {}, []                        # per open body, its commands so far (None: a case)
    for k, c in enumerate(cmds):
        w = c.words[0].text if c.shell == BASH and c.words else None
        if w in BASH_BODY_SPLIT and stack:
            if stack[-1] is not None:
                ends.update((j, k - 1) for j in stack[-1])
            if w in ('else', 'elif'):
                stack[-1] = [] if stack[-1] is not None else None
            else:
                stack.pop()
        if stack:
            ends[k] = None
            if stack[-1] is not None:
                stack[-1].append(k)
        if w in BASH_BODY_OPEN:
            stack.append(None if w == 'case' else [])
    return ends


def track(cmds, cwd, home=None, cwd_home=None, linked=False, linked_home=False, alts=()):
    """Each command's cwd, in order: a cd changes it for the commands after it. A bash ( ) group
    is a subshell: a cd inside it holds to its end, then the directory before it comes back; a
    bash { } group shares the directory. A cd the text cannot tell, one inside a PowerShell or
    cmd group, one behind a keyword, one in a pipeline, in the background, after ||, or one that may
    fail before || (but `|| exit`) makes the directory unknown (None) from there on. A cd that may
    fail (not to a literal folder that exists) or may not run (after &&, in a group entered after &&
    or ||, in a bash if/while/for/case body) leaves the folders before it as the other
    folders later commands may run in (Command.cwd_alt; `alts`, `cwd`'s), past those it guards
    (cdfail) and, for a cd that only its body or group may keep from running, past the rest of
    that body or group (_body_ends; not a case body); a cd that cannot fail moves them. Substitutions run in the directories of the command
    that holds them; a cd inside a PowerShell one makes its holder's directory unknown, and every
    later one's. `~` is `home` (home_folder). cwd_home is the directory tracked from `cwd_home` (default `cwd`) with
    a cd to a home variable read too (cd_target home_vars): only for the rules that deny, since a
    line can set the variable to another folder. `linked` (`linked_home`) says `cwd` (`cwd_home`)
    was entered through a link (Command.cwd_link, cd_move), carried with each directory."""
    here, here_l = cwd, linked
    here_h, here_hl = (cwd, linked) if cwd_home is None else (cwd_home, linked_home)
    held = cdfail.Held(alts)
    open_ = []                  # (group, the directory before it, its cwd_home, their link flags, held.save(), entered
    sub_from = {}               # after && or ||); id(command): (cwd, cwd_home) its substitutions start in
    bodies = _body_ends(cmds)
    for k, c in enumerate(cmds):
        common = 0
        while common < len(open_) and common < len(c.groups) and open_[common][0] == c.groups[common]:
            common += 1
        while len(open_) > common:
            g, before, before_h, before_l, before_hl, saved, _ = open_.pop()
            if g[1] == '(' and c.shell == BASH:
                here, here_h, here_l, here_hl = before, before_h, before_l, before_hl
                held.restore(saved)
        others = held.at(k)
        for g in c.groups[common:]:
            open_.append((g, here, here_h, here_l, here_hl, held.save(), k > 0 and cmds[k - 1].next_op in ('&&', '||')))
        sub_from[id(c)] = (here, here_h, here_l, here_hl)
        # PowerShell runs a $( ) or an argument's ( ) in the caller's scope: a cd there moves this
        # command and every one after it (bash's $( ) is a subshell)
        if c.shell == PS and any(cd_target(x, None, home)[0] for x in flatten(c.subs)):
            here = here_h = None
        c.cwd, c.cwd_home = here, here_h
        c.cwd_link, c.cwd_home_link = here_l, here_hl
        c.cwd_alt = _others(here, others)
        changed, to, to_l = cd_move(c, here, home, linked=here_l)
        _, to_h, to_hl = cd_move(c, here_h, home, home_vars=True, linked=here_hl)
        if _keyword_cd(c):
            changed, to, to_h = True, None, None
        if changed:
            guard = cdfail.guarded(cmds, k)
            skip = (c.piped_in or c.piped_out or c.prev_op == '||' or (c.next_op == '&' and c.shell == BASH)
                    or (c.groups and c.shell != BASH))      # the cd may not run, or moves no shell after it
            moved = [cd_move(c, a, home)[1] for a in c.cwd_alt]
            sure = not skip and c.prev_op != '&&' and all(t is not None and os.path.isdir(t) for t in [to] + moved)
            # a sure cd that only its body or group may keep from running: the rest of that runs only if it did
            ends = ([bodies[k]] if k in bodies else []) + [cdfail.group_end(cmds, k, n, e[0])
                                                          for n, e in enumerate(open_) if e[6]]
            fails = not sure or bool(ends)
            if fails:
                last = min(k if x is None else x for x in ends) if sure else k
                held.hold([f for f in (here,) + c.cwd_alt if f], max(guard.stop - 1, last))
            unsure = skip or (fails and c.next_op == '||' and not guard)
            held.alts = [] if unsure else [t for t in moved if t]
            here, here_l = (None, False) if unsure or to is None else (to, to_l)
            here_h, here_hl = (None, False) if unsure or to_h is None else (to_h, to_hl)
    for c in cmds:
        if c.subs:
            at, at_h, at_l, at_hl = sub_from[id(c)]
            track(c.subs, at, home, at_h, at_l, at_hl, c.cwd_alt)
    return cmds


def _others(here, folders):
    """The folders apart from `here`, each once."""
    keys = [paths.norm(f) for f in folders]
    return tuple(f for k, f in enumerate(folders) if keys[k] not in keys[:k] and (not here or keys[k] != paths.norm(here)))
