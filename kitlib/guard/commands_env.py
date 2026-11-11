"""The environment a parsed command sets (docs/guard-core.md "Parsing").

env_assignments(cmd, names) reads the variables a command sets (FOO=1 prog, export, setx,
$env:FOO = ..., Set-Item env:FOO, SetEnvironmentVariable); git_env_set names the one of
GIT_ENV_NAMES it sets; mark_git_env marks every git of a line that acts on a place the text
does not say. The program each command runs is kitlib.guard.commands'.
"""
import re

from . import shell as sh
from .commands import (ASSIGN_RE, PS_ASSIGN_OPS, cmd_invocation, invocation, quoted, said)


# the environment that moves git's directory, work tree or index, or sets its config (core.worktree)
GIT_ENV_NAMES = (r'GIT_(?:DIR|WORK_TREE|COMMON_DIR|INDEX_FILE|CONFIG_COUNT|CONFIG_KEY_\d+|CONFIG_VALUE_\d+'
                 r'|CONFIG_PARAMETERS)')
GIT_ENV_NET_RE = re.compile(r'(?i)SetEnvironmentVariable\s*\(\s*[\'"](' + GIT_ENV_NAMES + r')[\'"]')
EXPORTERS = {'export', 'declare', 'typeset', 'local', 'readonly', 'set', 'setx'}
EXPORT_LISTERS = ('export', 'declare', 'typeset', 'local', 'readonly')     # -p lists, sets nothing


# $env:FOO = v, $env:FOO='v' (one word to the parser), ${env:FOO} += v: read from the source
PS_ENV_SET_RE = re.compile(r'(?i)\$(?:\{env:([^}]+)\}|env:(\w+))\s*(?:[-+*/%]|\?\?)?=(?!=)\s*(.*)')
# the environment provider's drive in every spelling a path may give it: env:, Environment:: and
# the module-qualified Microsoft.PowerShell.Core\Environment:: (any qualifier, either slash: fail
# closed), each with an optional separator before the name
ENV_DRIVE = r'(?:env:|(?:[\w.]+[\\/])?environment::)[\\/]?'
PS_ENV_ITEM_RE = re.compile(r'(?i)^(?:-\w+:)?' + ENV_DRIVE + r'(.+)$')
PS_VALUE_OPT_RE = re.compile(r'(?i)^-va(?:l(?:ue?)?)?(?::(.*))?$')      # -Va, -Val, -Value, -Value:x
NET_SET_ENV_RE = re.compile(r'(?i)SetEnvironmentVariable\s*\(\s*[\'"]([^\'"]+)[\'"]\s*,\s*(?:\'([^\']*)|"([^"]*))')
BASH_ASSIGN_RE = ASSIGN_RE                                               # FOO=x and FOO+=x
# the cmdlets that set an env: item (Set-Content env:FOO x, New-Item -Path env: -Name FOO), the env:
# drive named bare, and New-Item's -Name (-N, -Na, -Nam, -Name, -Name:x)
PS_ENV_SETTERS = ('set-item', 'si', 'new-item', 'ni', 'set-content', 'sc', 'add-content', 'ac')
PS_ENV_DRIVE_RE = re.compile(r'(?i)^(?:-\w+:)?' + ENV_DRIVE + r'$')
PS_NAME_OPT_RE = re.compile(r'(?i)^-n(?:a(?:me?)?)?(?::(.*))?$')
# any cmdlet that may make an env: item under a new name (Rename-Item env:X -NewName FOO, Copy-Item)
PS_ENV_ITEM_CMDLETS = PS_ENV_SETTERS + ('rename-item', 'ren', 'rni', 'copy-item', 'copy', 'cp', 'cpi', 'move-item',
                                        'mi', 'move', 'mv', 'set-variable', 'sv')
PS_ENV_REF_RE = re.compile(r'(?i)^(?:-\w+:)?' + ENV_DRIVE)
PS_ENV_DRIVE_PREFIX_RE = re.compile(r'(?i)^' + ENV_DRIVE)
PS_OPT_PREFIX_RE = re.compile(r'^-\w+:')
GIT_ENV_NAME_RE = re.compile(r'(?i)^' + GIT_ENV_NAMES + r'$')


def _unquote(text):
    text = text.strip()
    if len(text) > 1 and text[0] == text[-1] and text[0] in '\'"':
        return text[1:-1]
    return text


def _assigned(c, words, name_re):
    out = []
    for w in words:
        name, eq, val = said(c, w).partition('=')
        name = name[:-1] if name.endswith('+') else name
        if eq and name_re.match(name):
            out.append((name, val))
    return out


def exporter_args(c, inv):
    """The words an exporter (EXPORTERS) may set a variable by. None when a -p before the first
    name of declare, typeset or local lists the variables named (declare -p GIT_DIR: bash then
    ignores every other option); export -p and readonly -p list only with no name, and with one
    still export, mark or set it (export -p GIT_DIR=x), so their words are read. None for set
    outside cmd: bash's set takes options and positional parameters
    (set -- GIT_DIR, set GIT_DIR=x sets $1) and PowerShell's is Set-Variable, a variable of its
    own. The option words before the first name, or before --, are passed over."""
    if inv.name == 'set':
        return list(inv.args) if c.shell == sh.CMD else []
    if inv.name not in EXPORT_LISTERS:
        return list(inv.args)
    out, opts = [], True
    for w in inv.args:
        t = said(c, w)
        if opts and not w.dyn:
            if t == '--':
                opts = False
                continue
            if len(t) > 1 and t[0] in '-+' and '=' not in t:
                if t[0] == '-' and 'p' in t[1:] and inv.name in ('declare', 'typeset', 'local'):
                    return []
                continue
        opts = False
        out.append(w)
    return out


def env_assignments(c, names):
    """[(variable, value text)] of the environment variables matching the regex `names` (case
    ignored) that the command sets: an assignment before the program (FOO=1 prog, env FOO=1 prog),
    export/declare/setx, $env:FOO = ..., ${env:FOO} += ..., Set-Item, Set-Content or Add-Content
    env:FOO, New-Item env:FOO or New-Item -Path env: -Name FOO (the drive in any spelling of
    ENV_DRIVE: Environment::FOO, Microsoft.PowerShell.Core\\Environment::FOO), or
    [Environment]::SetEnvironmentVariable('FOO', ...). A word that only mentions one (echo
    "FOO=1", curl -d foo=1, a commit message) does not."""
    name_re = re.compile(r'(?i)^(?:%s)$' % names)
    words = c.words
    out = []
    if not words:
        return out
    src = c.source or ''
    if c.shell == sh.PS:
        end = c.end if c.end is not None else len(src)
        m = PS_ENV_SET_RE.match(src, words[0].start, end) if words[0].start is not None else None
        if m and name_re.match(m.group(1) or m.group(2)):
            out.append((m.group(1) or m.group(2), _unquote(m.group(3))))
        for w in words:
            # any word: $null = [Environment]::SetEnvironmentVariable(...). The parser reads the
            # ( ) argument list as a command of its own, so read the source from the word on
            if w.text.lower().endswith('::setenvironmentvariable') and w.start is not None:
                g = NET_SET_ENV_RE.search(src, w.start)
                if g and g.start() < (w.end or len(src)) and name_re.match(g.group(1)):
                    out.append((g.group(1), g.group(2) if g.group(2) is not None else g.group(3) or ''))
    inv = cmd_invocation(c, invocation(c))
    if c.shell == sh.BASH:
        lead = []
        for w in words:                          # FOO+=x is an assignment the program finder does not skip
            if not BASH_ASSIGN_RE.match(said(c, w)):
                break
            lead.append(w)
        before = words[:inv.index] if inv is not None else words
        out += _assigned(c, lead + [w for w in before if w not in lead], name_re)
    if inv is None:
        return out
    args = inv.args
    if inv.name in EXPORTERS:
        out += _assigned(c, exporter_args(c, inv), name_re)
        if inv.name == 'setx' and len(args) > 1 and name_re.match(args[0].text):
            out.append((args[0].text, args[1].text))
    elif inv.name in PS_ENV_SETTERS:
        vals = _option_values(args, PS_VALUE_OPT_RE)
        for k, w in enumerate(args):
            m = PS_ENV_ITEM_RE.match(w.text)
            if not (m and name_re.match(m.group(1))):
                continue
            rest = [v.text for v in args[k + 1:] if not v.text.startswith('-')]
            out.append((m.group(1), (vals or rest or [''])[0]))
        if inv.name in ('new-item', 'ni') and any(PS_ENV_DRIVE_RE.match(w.text) for w in args):
            for name in _option_values(args, PS_NAME_OPT_RE):     # New-Item -Path env: -Name FOO
                if name_re.match(name):
                    out.append((name, (vals or [''])[0]))
    return out


def _option_values(args, opt_re):
    """The values a PowerShell option matching `opt_re` is given (group 1: the -Opt:value form)."""
    out = []
    for j, v in enumerate(args):
        vm = opt_re.match(v.text)
        if vm and vm.group(1) is not None:
            out.append(vm.group(1))
        elif vm and j + 1 < len(args):
            out.append(args[j + 1].text)
    return out


def git_env_set(c):
    """The variable of GIT_ENV_NAMES (GIT_DIR, GIT_WORK_TREE, GIT_COMMON_DIR, GIT_INDEX_FILE,
    GIT_CONFIG_COUNT, GIT_CONFIG_KEY_<n>, GIT_CONFIG_VALUE_<n>, GIT_CONFIG_PARAMETERS) the command
    sets, or None: every spelling env_assignments and ps_env_targets read, export (or the like) naming one with no
    value (export GIT_DIR after read GIT_DIR), and a word naming one in a cmdlet that may make an
    env: item (PS_ENV_ITEM_CMDLETS: Rename-Item env:X -NewName GIT_DIR, Copy-Item env:X
    env:GIT_DIR). A word that only names one (echo "GIT_DIR=x", Write-Output $env:GIT_DIR,
    Get-Content env:GIT_DIR) does not. Every word is read as the shell passes it (said: a
    backslash bash drops, dropped). A name the text computes, and any other way to change the
    environment, is not read: the guard catches honest mistakes, not evasion."""
    for name, _val in env_assignments(c, GIT_ENV_NAMES):
        return name
    words = c.words
    if not words:
        return None
    if c.shell == sh.PS:
        for name in ps_env_targets(c):          # $env:A, $env:GIT_DIR = 1, 'x' and $a = $env:GIT_DIR = 'x'
            if GIT_ENV_NAME_RE.match(name):
                return name
        src = c.source or ''
        for w in words:
            # the call, not a string that names it (a commit message, Write-Host "..."): read the
            # source from an unquoted word that names SetEnvironmentVariable, as env_assignments does
            if w.start is None or quoted(c, w) or 'setenvironmentvariable' not in w.text.lower():
                continue
            m = GIT_ENV_NET_RE.search(src, w.start)
            if m and m.start() < (w.end if w.end is not None else len(src)):
                return m.group(1)
    inv = cmd_invocation(c, invocation(c))
    if inv is None:
        return None
    if inv.name in EXPORTERS:
        for w in exporter_args(c, inv):
            name = said(c, w).split('=', 1)[0]
            name = name[:-1] if name.endswith('+') else name
            if GIT_ENV_NAME_RE.match(name):
                return name
    if inv.name in PS_ENV_ITEM_CMDLETS and any(PS_ENV_REF_RE.match(said(c, w)) for w in inv.args):
        for w in inv.args:
            name = PS_ENV_DRIVE_PREFIX_RE.sub('', PS_OPT_PREFIX_RE.sub('', said(c, w)))
            if GIT_ENV_NAME_RE.match(name):
                return name
    return None


# ---------------------------------------------------------------- the environment a line sets

PS_ENV_REF_ANY_RE = re.compile(r'(?i)\$(?:\{env:([^}]*)\}|env:([\w`]*))')
PS_ENV_ONE_WORD_RE = re.compile(r'(?i)^\$(?:\{env:[^}]*\}|env:[\w`]*)\s*(?:[-+*/%]|\?\?)?=(?!=)')
PS_ASSIGN_PREFIX_RE = re.compile(r'(?:[-+*/%]|\?\?)?=(?!=)')    # matched at a word's start: $env:X ="v"


def ps_env_targets(c):
    """The environment variable names (as written) a PowerShell expression assigns: $env:X = v,
    ${env:X} += v, $env:X='v' in one word, $env:X ="v" (the = starts the value's word), $env:A, $env:B = 1, 2, $a = $env:X = v, foreach
    ($env:X in ...)."""
    if c.shell != sh.PS:
        return []
    words = c.words
    src = c.source or ''
    ops = [k for k, w in enumerate(words) if w.text in PS_ASSIGN_OPS and not w.dyn and not quoted(c, w) or
           0 <= w.start < len(src) and PS_ASSIGN_PREFIX_RE.match(src, w.start)]
    targets = list(words[:ops[-1]]) if ops else []
    targets += [w for k, w in enumerate(words)
                if not quoted(c, w) and (PS_ENV_ONE_WORD_RE.match(w.text) or
                                         k + 1 < len(words) and words[k + 1].text.lower() == 'in')]
    out = []
    for w in targets:
        if quoted(c, w):
            continue
        for m in PS_ENV_REF_ANY_RE.finditer(w.text):
            out.append(m.group(1) if m.group(1) is not None else m.group(2))
    return out


def line_env_names(commands):
    """The upper-case names of the environment variables the line's commands set literally:
    NAME=value in every spelling env_assignments reads, and $env:NAME = v (ps_env_targets)."""
    names = set()
    for c in commands:
        names.update(n.upper() for n, _v in env_assignments(c, r'[A-Za-z_]\w*'))
        names.update(n.upper() for n in ps_env_targets(c))
    return names


GIT_ENV_SCOPED = 'GIT_INDEX_FILE'
# a line that may define a function or an alias: a git word there may run other commands
LINE_DEFINES_RE = re.compile(r'\(\s*\)|\b(?:function|alias)\b')


def git_env_prefix(c):
    """GIT_INDEX_FILE when that is the one variable of GIT_ENV_NAMES the command sets, as an
    assignment before the git it runs (GIT_INDEX_FILE=<tmp> git read-tree, env GIT_INDEX_FILE=<tmp>
    git ...): bash hands it to that git and what it runs alone, not to the line's other commands.
    Else None."""
    if c.shell != sh.BASH:
        return None
    inv = cmd_invocation(c, invocation(c))
    if inv is None or inv.name != 'git':
        return None
    names = {n.upper() for n, _v in env_assignments(c, GIT_ENV_NAMES)}
    return GIT_ENV_SCOPED if names == {GIT_ENV_SCOPED} else None


def mark_git_env(parsed):
    """When any command of the line sets GIT_DIR or the like (git_env_set), every git of the
    line acts on a place the text does not say: c.git_env is that variable's name, else False.
    The whole line, not the commands after the setter: expand appends nested text (eval, bash
    -c, Invoke-Expression, a sourced here-string) after the line's own commands, so order says
    nothing about which runs first. One setter reaches less: GIT_INDEX_FILE set before a git
    alone (git_env_prefix, the temp-index pattern) marks that git and the commands nested in it,
    unless the line may define a function or an alias (LINE_DEFINES_RE). A line with no git is
    not affected. c.home_env is the first
    of HOME_ENV_NAMES the line sets literally (line_env_names), else False: git then reads
    another user config (rules.home_config judges git with it). c.path_env is True when the line
    sets PATH literally: git then finds a filter's bare program name (git-lfs) elsewhere
    (rules.archive_filter_set). A name the text computes is not read (honest mistakes only)."""
    cmds = parsed.commands
    sets = line_env_names(cmds)
    defines = any(LINE_DEFINES_RE.search(c.source or '') for c in cmds)
    name, scoped = None, {}
    for c in cmds:
        n = git_env_set(c)
        if not n:
            continue
        if not defines and git_env_prefix(c):
            scoped[id(c)] = n
        elif name is None:
            name = n
    home = next((n for n in HOME_ENV_NAMES if n in sets), None)
    path = 'PATH' in sets
    for c in cmds:
        own, o = None, c
        while o is not None and own is None:
            own, o = scoped.get(id(o)), o.nested_in
        c.git_env = name or own or False
        c.home_env = home or False
        c.path_env = path


# The variables git for Windows finds the user config through: $XDG_CONFIG_HOME/git/config, and
# ~/.gitconfig with ~ from HOME, else HOMEDRIVE+HOMEPATH, else USERPROFILE (observed with git 2.55)
HOME_ENV_NAMES = ('HOME', 'XDG_CONFIG_HOME', 'USERPROFILE', 'HOMEDRIVE', 'HOMEPATH')
