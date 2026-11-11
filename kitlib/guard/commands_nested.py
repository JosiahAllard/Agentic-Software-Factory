"""The text a nested shell runs, and a line read whole (docs/guard-core.md "Parsing").

read(text, shell, cwd) parses a line, then expand(parsed) parses the text each nested shell
runs as commands of their own (bash/sh -c, powershell/pwsh -Command, cmd /c, Invoke-Expression,
eval, a trap handler, find -exec); a nested text the guard cannot read (a variable, an encoded
command) marks the parse unclean. The program each command runs is kitlib.guard.commands'.
"""
import re

from . import shell as sh
from .commands import (BASH_SHELLS, PS_SHELLS, Unread, _param, env_nested, fill_words, invocation, passed,
                       quoted, run_dir)
from .commands_env import mark_git_env
from .commands_git import git_dyn_word


SOURCED_STDIN = ('/dev/stdin', '/dev/fd/0', '/proc/self/fd/0')     # source / . of these reads standard input


# ---------------------------------------------------------------- nested shells

STDIN = object()                # _nested_text: the shell reads its commands from standard input
# a word anywhere in a line that may change the shell's directory (a trap handler's place)
LINE_CD_RE = re.compile(r'(?i)(?<![\w.-])(?:%s)(?![\w.-])' % '|'.join(
    re.escape(v) for v in sorted(sh.CD_VERBS | sh.CD_UNKNOWN)))
BASH_VALUE_OPTS = {'-o', '+o', '-O', '+O', '--rcfile', '--init-file'}
PS_VALUE_PARAMS = ('executionpolicy', 'windowstyle', 'workingdirectory', 'configurationname',
                   'outputformat', 'inputformat', 'version', 'psconsolefile', 'settingsfile',
                   'custompipename', 'configurationfile')
PS_VALUE_ALIASES = {'ep', 'ex', 'w', 'wd', 'of', 'o', 'if', 'v', 'ea'}
# switches whose next word is read both as their value and as the start of the command: -i and
# -in are -InputFormat with a value to powershell.exe and -Interactive without one to pwsh; an
# -EncodedArguments prefix is judged both ways too (so it is read before PS_VALUE_PARAMS is)
PS_VALUE_EITHER = {'i', 'in', 'ea'}
# such switches on one reading path that _nested_text follows both ways; past that it flags the
# line and reads each further one as taking no value
PS_FORKS_MAX = 16
# the switches powershell.exe 5.1 knows, each (name, shortest prefix it takes), and its aliases
# (observed on 5.1 for every prefix). It runs any other switch, and every word after it, as the
# command. The server modes are left out: they read no command line, so treating them as
# unknown only reads more.
PS51_SWITCHES = (('command', 'c'), ('configurationname', 'config'), ('encodedcommand', 'e'),
                 ('encodedarguments', 'encodeda'), ('executionpolicy', 'ex'), ('file', 'f'),
                 ('help', 'h'), ('inputformat', 'i'), ('mta', 'mta'), ('noexit', 'noe'),
                 ('nologo', 'nol'), ('noninteractive', 'noni'), ('noprofile', 'nop'),
                 ('outputformat', 'o'), ('sta', 'st'), ('windowstyle', 'w'), ('workingdirectory', 'wo'))
PS51_ALIASES = {'ec', 'ea', 'ep', 'if', 'of', 'wd', '?'}


def _ps51_known(t):
    """True when powershell.exe 5.1 reads the switch word `t` ('-name', lower case) as a switch."""
    body = t[1:]
    return body in PS51_ALIASES or any(body.startswith(short) and name.startswith(body)
                                       for name, short in PS51_SWITCHES)


def _ps_takes_value(t):
    body = t[1:]
    if body in PS_VALUE_ALIASES:
        return True
    return len(body) >= 2 and body not in ('in', 'i') and any(p.startswith(body) for p in PS_VALUE_PARAMS)


def _ps_value_either(t):
    body = t[1:]
    return body in PS_VALUE_EITHER or (body.startswith('encodeda') and 'encodedarguments'.startswith(body))


def _ps_switch(w):
    """A powershell.exe or pwsh switch word as '-name' in lower case (it reads /Command and
    –Command as -Command), or its text in lower case."""
    t = w.text.lower()
    return '-' + t[1:] if t[:1] == '/' else sh.ps_dashed(t)


def _ps_command_switch(t):
    return t.startswith('-') and len(t) > 1 and ('command'.startswith(t[1:]) or t == '-cwa' or
                                                 'commandwithargs'.startswith(t[1:]))


def span_text(cmd, words):
    """The words as the command's source writes them (quotes kept), or joined when the source
    positions do not fit."""
    src = cmd.source
    if words and all(0 <= x.start <= x.end <= len(src) for x in words) and words[0].start <= words[-1].end:
        return src[words[0].start:words[-1].end]
    return ' '.join(x.text for x in words)


SP_SHELLS = BASH_SHELLS | PS_SHELLS | {'cmd'}
CMD_RUN_SWITCHES = ('/c', '/k', '/r', '//c', '//k', '//r')
# a word that may hold a run switch cmd reads, not on its own: cmd splits its switches at , ; =
# as well as spaces and finds a / switch inside a word (/cecho, /q/c, ;/c, x/c, "/c"), so a /c,
# /k or /r anywhere in a word before the run switch is searched for, never matched at its start
CMD_RUN_HIDDEN_RE = re.compile(r'(?i)/[ckr]')


def _cmd_hidden_switch(text):
    """True when a cmd word that is not a run switch on its own may still hold one."""
    return bool(CMD_RUN_HIDDEN_RE.search(text))


def _nested_text(inv, parsed):
    """(text, shell) a nested shell or eval runs, (text, shell, cwd) when it runs elsewhere
    (env -C, Start-Process -WorkingDirectory), (STDIN, shell) when the shell reads its commands
    from standard input, or None. For powershell and pwsh, and cmd started by Start-Process, the
    text is a tuple of readings, each a text or STDIN, once each."""
    if inv.via == 'start-process' and inv.name in SP_SHELLS:
        return _sp_shell_text(inv, parsed)
    return _shell_text(inv, parsed)


def _sp_shell_text(inv, parsed):
    """_nested_text for a shell Start-Process starts. Start-Process hands the program one command
    line, its -ArgumentList elements joined with spaces; a computed element makes the text
    unreadable. A computed -WorkingDirectory, or a literal one the guard cannot place exactly
    (sp_workdir_unplaceable), gives the nested line no directory (None, run_dir), so a write there
    is refused as one whose directory is unknown. cmd runs the rest of that line after its
    /c (/k, /r), read with its outer quotes kept and dropped (cmd keeps or drops them by what lies
    between); bash and powershell read the words _split_list made of each element."""
    if any(w.dyn for w in inv.listed):
        parsed.flag('a shell Start-Process starts with an argument list that is not literal (%s)' % inv.path)
        return None
    where = ()
    if inv.workdir is not None:
        where = (run_dir(inv),)
    if inv.name != 'cmd':
        found = _shell_text(inv, parsed)
        return found and found[:2] + where
    line = ' '.join(w.text for w in inv.listed)
    for m in re.finditer(r'\S+', line):
        t = m.group(0).lower()
        if t in CMD_RUN_SWITCHES:
            rest = line[m.end():].strip()
            if not rest:
                return None
            readings = (rest,)
            if rest.startswith('"'):
                k = rest.rfind('"')
                readings += ((rest[1:k] + rest[k + 1:]) if k > 0 else rest[1:],)
            return (readings, sh.CMD) + where
        if _cmd_hidden_switch(t):
            parsed.flag('a cmd switch Start-Process passes that the guard cannot read (%s)' % m.group(0))
            return None
    return None


def _shell_text(inv, parsed):
    """_nested_text for every program but a shell Start-Process starts."""
    name, a = inv.name, inv.args
    if name == 'env':
        found = env_nested(inv.cmd.words, inv.index, inv.cmd)
        if found is None:
            return None
        kind, val, k = found
        rest = inv.cmd.words[k:]
        if kind == 'split':
            if val.dyn:
                parsed.flag('an env -S string that is not literal')
                return None
            return (val.text + ' ' + span_text(inv.cmd, rest)).strip(), inv.cmd.shell
        if not rest:
            return None
        where = None if val.dyn else sh.resolve_path(val.text, inv.cmd.cwd, home=parsed.home)
        return span_text(inv.cmd, rest), inv.cmd.shell, sh.chdir_real(where, inv.cmd.cwd)
    if name in ('source', '.') and inv.cmd.shell == sh.BASH:
        if a and a[0].dyn:
            parsed.flag('a script sourced from text that is not literal')
        if a and a[0].text in SOURCED_STDIN:
            return STDIN, sh.BASH                # . /dev/stdin <<< "...": the text runs in this shell
        return None
    if name in BASH_SHELLS:
        k = 0
        while k < len(a):
            t = a[k].text
            if t in BASH_VALUE_OPTS:
                k += 2
                continue
            if t.startswith('-') and not t.startswith('--') and 'c' in t[1:]:
                if k + 1 >= len(a):
                    return None
                if a[k + 1].dyn:
                    parsed.flag('a nested bash -c whose text is not literal')
                    return None
                return a[k + 1].text, sh.BASH
            if t in ('-', '--') or (t[:1] in '-+' and len(t) > 1):
                k += 1
                continue
            if a[k].dyn:
                parsed.flag('a shell running a script the text does not name')
            return None                         # bash script.sh: a file, not text
        return STDIN, sh.BASH
    if name in PS_SHELLS:
        # the readings, each a text or STDIN, kept once each and parsed each on its own
        # (expand): one reading's unclosed <# or quote hides no other
        lit = 'a nested powershell command whose text is not literal'
        seen = {}

        def nested(why, *readings):
            # every way the shells may read the words is judged
            texts = [words for words in readings if words]
            if any(w.dyn for words in texts for w in words):
                parsed.flag(why)
                return None
            return joined(tuple(' '.join(w.text for w in words) for words in texts))

        def joined(*found):
            out = []
            for r in (r for f in found for r in f or ()):
                if r not in out:
                    out.append(r)
            return tuple(out) or None

        def scan(k, unknown, forks):
            # forks is in the key: past PS_FORKS_MAX a path reads less, so its result is its own
            if (k, unknown, forks) not in seen:
                seen[(k, unknown, forks)] = read_from(k, unknown, forks)
            return seen[(k, unknown, forks)]

        def read_from(k, unknown, forks):
            while k < len(a):
                t = _ps_switch(a[k])
                if t.startswith('-e') and 'encodedcommand'.startswith(t[1:]) or t in ('-ec', '-enc'):
                    parsed.flag('an encoded PowerShell command')
                    return None
                # powershell.exe runs a switch it does not know, and every word after it, as the
                # command (unknown); pwsh refuses it, or reads it as a switch, and goes on
                first = a[unknown:] if unknown is not None else []
                if _ps_command_switch(t):
                    rest = a[k + 1:]
                    if [w.text for w in rest] == ['-']:
                        if unknown is None:
                            return (STDIN,)
                        # the script comes from the pipe; the words from the unknown switch on
                        # are still judged as powershell.exe's command
                        parsed.flag(lit)
                        return nested(lit, first)
                    return nested('a nested powershell -Command whose text is not literal', first, rest)
                if unknown is None and t.startswith('-') and t != '-' and not _ps51_known(t):
                    # a value switch included: powershell.exe never takes its value
                    unknown = k
                if t.startswith('-') and len(t) > 1 and 'file'.startswith(t[1:]):
                    if k + 1 < len(a) and not a[k + 1].dyn and a[k + 1].text == '-':
                        # `-File -` reads the script from standard input, as `-Command -` does;
                        # the words after it, if any, never make it a file
                        if unknown is None:
                            return (STDIN,)
                        parsed.flag(lit)
                        return nested(lit, first)
                    return nested(lit, first) if first else None
                if t.startswith('-') and _ps_value_either(t) and k + 1 < len(a):
                    if forks < PS_FORKS_MAX:
                        # the next word as the switch's value, and as the start of the command
                        return joined(scan(k + 1, unknown, forks + 1), scan(k + 2, unknown, forks + 1))
                    # past the cap the line is flagged, and the reading with no value still judged
                    parsed.flag('a powershell switch line with too many readings')
                    k += 1
                    continue
                if t.startswith('-'):
                    k += 2 if _ps_takes_value(t) else 1      # as pwsh reads it
                    continue
                # a word that is no switch starts the command: pwsh's, and powershell.exe's when no
                # unknown switch started it before (pwsh reads `--c x` as `-c x`, powershell.exe
                # runs `--c x`); a shell that took it as the value of a switch before it reads a
                # later -Command
                later = next((j for j in range(k + 1, len(a)) if _ps_command_switch(_ps_switch(a[j]))), None)
                then = a[later + 1:] if later is not None else []
                if [w.text for w in then] == ['-']:
                    parsed.flag(lit)
                    return None
                return nested(lit, first, a[k:], then)
            if unknown is not None:
                # powershell.exe runs the words from the unknown switch on; pwsh, when it knows
                # the switch, reads standard input
                found = nested(lit, a[unknown:])
                return found and joined(found, (STDIN,))
            return (STDIN,)
        readings = scan(0, None, 0)
        return (readings, sh.PS) if readings else None
    if name == 'cmd':
        for k, w in enumerate(a):
            if w.dyn:
                # a computed word before the run switch may itself be /c
                parsed.flag('a cmd switch that is not literal')
                return None
            if w.text.lower() not in CMD_RUN_SWITCHES and _cmd_hidden_switch(w.text):
                parsed.flag('a cmd switch the guard cannot read (%s)' % w.text)
                return None
            if w.text.lower() in CMD_RUN_SWITCHES:
                rest = a[k + 1:]
                if any(x.dyn for x in rest):
                    parsed.flag('a nested cmd /c whose text is not literal')
                    return None
                if len(rest) == 1:
                    return rest[0].text, sh.CMD
                src = inv.cmd.source
                if rest and all(0 <= x.start <= x.end <= len(src) for x in rest):
                    # as written, so a quoted start "title" keeps its quotes
                    return src[rest[0].start:rest[-1].end], sh.CMD
                return ' '.join(x.text for x in rest), sh.CMD
        return None
    if name in ('invoke-expression', 'iex'):
        p = _param(a, ('-command',))
        words = a
        if p:
            how, k = p
            words = [sh.Word(a[k].text.split(':', 1)[1], a[k].dyn, 0, 0)] if how == 'colon' else a[k:k + 1]
        words = [w for w in words if not sh.ps_dashed(w.text).startswith('-')][:1]
        if not words:
            if inv.cmd.piped_in:
                return STDIN, sh.PS
            parsed.flag('Invoke-Expression of a value the text computes')
            return None
        if words[0].dyn:
            parsed.flag('Invoke-Expression on text that is not literal')
            return None
        return words[0].text, sh.PS
    if name == 'eval' and inv.cmd.shell == sh.BASH:
        if any(w.dyn for w in a):
            parsed.flag('eval of text that is not literal')
            return None
        return ' '.join(w.text for w in a), sh.BASH
    if name == 'trap' and inv.cmd.shell == sh.BASH:
        # the handler runs in this shell later (at exit, or on a signal), wherever it then is:
        # where the trap was set only when nothing on the line, nested text too, changes directory
        found = trap_operands(inv.cmd, a)
        if found is None:
            return None
        if found[0].dyn:
            parsed.flag('a trap handler that is not literal')
            return None
        where = None if LINE_CD_RE.search(parsed.text or '') else inv.cmd.cwd
        return (found[0].text, sh.BASH, where) if found[0].text.strip() else None
    if name in ('mapfile', 'readarray') and inv.cmd.shell == sh.BASH:
        cb = mapfile_callback(inv.cmd, a)       # runs here, now, once per line read
        if cb is None:
            return None
        if cb.dyn:
            parsed.flag('a mapfile callback that is not literal')
            return None
        return cb.text, sh.BASH
    return None


STDIN_WRITERS = {'echo', 'printf', 'write-output', 'write-host', 'echo.exe'}


def _stdin_text(cmd, parsed):
    """The text a shell reads on standard input when the line says what it is (a heredoc body, a
    here-string, a pipe from echo, printf, Write-Output or a PowerShell string), else None (the
    parse is marked unclean when a pipe feeds it something else)."""
    for r in cmd.redirects:
        if r.op in ('<<', '<<<') and r.target is not None:
            if r.target.dyn and ('$' in r.target.text or '`' in r.target.text):
                parsed.flag('a shell reading text that is not literal')
            return r.target.text
    src = cmd.pipe_from
    if src is not None:
        inv = invocation(src)
        if inv is not None and inv.name in STDIN_WRITERS:
            args = [w for w in inv.args if not (inv.name == 'echo' and w.text in ('-e', '-n', '-E'))]
            if any(w.dyn for w in args):
                parsed.flag('a shell reading text that is not literal')
            return ' '.join(w.text for w in args)
        if inv is None and src.shell == sh.PS and src.words and quoted(src, src.words[0]):
            # "$b" | iex runs the string after its $ expands: the text as written is not the code
            if any(w.dyn for w in src.words):
                parsed.flag('a shell reading text that is not literal')
            return ' '.join(w.text for w in src.words)
        parsed.flag('a shell reading its commands from a pipe')
        return None
    if cmd.piped_in:
        parsed.flag('a shell reading its commands from a pipe')
    return None


# text PowerShell compiles and runs in this process: InvokeScript, NewScriptBlock, ExpandString,
# GetScriptBlock, AddScript, [scriptblock]::Create and [powershell]::Create under any namespace
# prefix, and a static call on a type the text computes (([type]'scriptblock')::Create; the parser
# splits the ( ) off, so the call's word starts with ::)
PS_SCRIPT_TEXT_RE = re.compile(r'(?i)\b(?:invokescript|newscriptblock|expandstring|getscriptblock|addscript)\b|'
                               r'\[\s*(?:system\.)?(?:management\.)?(?:automation\.)?'
                               r'(?:scriptblock|powershell)\s*\]\s*::\s*create\b|'
                               r'(?:^|\))\s*::\s*[A-Za-z_]')


MAPFILE_VALUE_OPTS = 'dnOsuCc'                  # mapfile/readarray options that take a value


def trap_operands(c, args):
    """(handler Word, [signal Words]) a trap sets, or None when it lists (-p, -l) or resets
    (trap SIG, trap - SIG)."""
    ops = []
    for w in args:
        t = passed(c, w)
        if not ops and not w.dyn and t in ('-p', '-l', '-lp', '-pl', '--'):
            continue
        ops.append(w)
    if len(ops) < 2 or not ops[0].dyn and passed(c, ops[0]) == '-':
        return None
    return ops[0], ops[1:]


def mapfile_callback(c, args):
    """The -C callback Word of mapfile/readarray, or None."""
    k = 0
    while k < len(args):
        w = args[k]
        t = passed(c, w)
        if w.dyn or not t.startswith('-') or t in ('-', '--'):
            k += 1
            continue
        for j in range(1, len(t)):
            if t[j] in MAPFILE_VALUE_OPTS:
                if t[j + 1:]:
                    val = sh.Word(t[j + 1:], w.dyn, w.start, w.end) if t[j] == 'C' else None
                else:
                    k += 1
                    val = args[k] if k < len(args) and t[j] == 'C' else None
                if val is not None:
                    return val
                break
        k += 1
    return None


FIND_EXEC = ('-exec', '-execdir', '-ok', '-okdir')


def find_exec_words(cmd, inv):
    """(option, word list) for each command find -exec/-execdir/-ok/-okdir runs, each up to its
    ; or {} +."""
    out = []
    a = inv.args
    k = 0
    while k < len(a):
        if a[k].text in FIND_EXEC and not a[k].dyn:
            j = k + 1
            while j < len(a) and not (a[j].text == ';' or a[j].text == '+' and j > k + 1 and a[j - 1].text == '{}'):
                j += 1
            if j > k + 1:
                out.append((a[k].text, a[k + 1:j]))
            k = j + 1
            continue
        k += 1
    return out


def call_subexpr(inv):
    """True when PowerShell's call operator runs what a ( ) subexpression computes (`& (Get-Command
    git) push`, `& (Join-Path $r x.exe) a`; kitlib.guard.shell reads the ( ) as the program word).
    The program is not literal, but the line stays clean: rules.call_subexpr_pmdata judges it (a PM
    data repository in every session but the owner's own; an agent session anywhere), so it makes
    no other line unreadable."""
    if inv is None or not inv.dyn or inv.cmd.shell != sh.PS or inv.word is None or inv.via is not None:
        return False
    k = inv.index
    return inv.word.text.startswith('(') and k > 0 and inv.cmd.words[k - 1].text == '&'


def expand(parsed):
    """Parse every nested shell's text into `parsed` (in place); returns parsed."""
    k = 0
    while k < len(parsed.commands):
        cmd = parsed.commands[k]
        k += 1
        first = cmd.words[0].text.lower() if cmd.words else ''
        if cmd.shell == sh.CMD and first == 'if':
            parsed.flag('a cmd if statement')
        if cmd.shell == sh.CMD and first == 'for':
            parsed.flag('a cmd for loop')
        inv = invocation(cmd, unread=True)
        if isinstance(inv, Unread):
            parsed.flag(inv.why)                # Start-Process, no program and a parameter unread
            continue
        if inv is None:
            continue
        if inv.unread:
            # Start-Process with a parameter the guard cannot read: refused, its program still read
            parsed.flag(inv.unread)
        if cmd.split:
            # `Set-Content -Value $s.Trim() -Path x`: the scanner cut the command at a ( ) or { }
            # and read the rest as commands of their own; its arguments are not all known
            parsed.flag('a command the line splits around a ( ) or { } group, its arguments past it '
                        'read as other commands (%s)' % inv.path)
        if inv.dyn:
            if not call_subexpr(inv):           # rules.call_subexpr_pmdata judges `& (...)`
                parsed.flag('a command whose program is not literal (%s)' % inv.path)
            continue
        why = git_dyn_word(inv)
        if why:
            parsed.flag(why)
        if inv.name == 'find' and cmd.shell == sh.BASH:
            for opt, words in find_exec_words(cmd, inv):
                # -execdir/-okdir run in each found file's folder; {} is the found path, filled
                # into any word that holds it (its own nested commands come later in the loop)
                where = None if opt in ('-execdir', '-okdir') else cmd.cwd
                for c in _nest(parsed, cmd, span_text(cmd, words), sh.BASH, where,
                               None if where is None else cmd.cwd_home):
                    fill_words(c.words, '{}')
            continue
        if inv.word is not None and inv.path != inv.word.text:
            parsed.flag('a backslash in a program name')
        if inv.word is not None and inv.word.synth:
            # an argument the shell rewrites reads as it runs; a program name so spelled is hiding
            src = cmd.source[inv.word.start:inv.word.end]
            parsed.flag('an ANSI-C escape in a program name' if "$'" in src else 'a brace expansion in a program name')
        found = _nested_text(inv, parsed)
        if not found:
            continue
        text, shell = found[:2]
        where, where_home = (found[2], found[2]) if len(found) > 2 else (cmd.cwd, cmd.cwd_home)
        # a tuple is a powershell line's readings: each is parsed on its own, standard input once
        for one in text if isinstance(text, tuple) else (text,):
            if one is STDIN:
                one = _stdin_text(cmd, parsed)
                if one is None:
                    continue
            _nest(parsed, cmd, one, shell, where, where_home)
    return parsed


# the nested texts one line's read parses at most (every one passes through _nest), past which the
# line is flagged: each nested powershell reading may fork again, so without a cap on the total a
# line of repeated `-i pwsh` makes some 17 readings per level, 17^6 parses down to MAX_DEPTH. The
# readings are not merged across levels: the same text under another outer command is judged with
# that command's chain (nested_in), so only this total bounds the work.
MAX_NESTS = 64


def _nest(parsed, cmd, text, shell, cwd, cwd_home):
    """Parse `text` as commands `cmd` runs, at `cwd` (cwd_home: shell.track), into parsed;
    returns those commands. A nested shell started in `cmd`'s own directory inherits its $PWD, so
    that directory's link flag (Command.cwd_link) goes with it; env -C's real folder has none."""
    if cmd.depth + 1 > sh.MAX_DEPTH:
        parsed.flag('shells nested deeper than %d' % sh.MAX_DEPTH)
        return []
    if parsed.nests >= MAX_NESTS:
        parsed.flag('more than %d nested shell texts' % MAX_NESTS)
        return []
    parsed.nests += 1
    linked = cwd is not None and cwd == cmd.cwd and cmd.cwd_link
    linked_home = cwd_home is not None and cwd_home == cmd.cwd_home and cmd.cwd_home_link
    _p, cmds = sh.parse(text, shell, cwd, depth=cmd.depth + 1, parsed=parsed, cwd_home=cwd_home,
                        linked=linked, linked_home=linked_home)
    for c in cmds:
        if c.nested_in is None:
            c.nested_in = cmd
    return cmds


def read(text, shell, cwd, home=None):
    """parse() then expand(): every command `text` runs, nested shells included. `~` is `home`
    (shell.home_folder)."""
    parsed, _cmds = sh.parse(text, shell, cwd, home=home)
    expand(parsed)
    mark_git_env(parsed)
    return parsed
