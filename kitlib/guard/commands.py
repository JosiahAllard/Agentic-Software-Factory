"""What a parsed command runs (docs/guard-core.md "Parsing").

invocation(cmd) finds the program a command runs past the words that only start it: variable
assignments (FOO=1 prog), env, command, exec, nohup, nice, time, timeout, xargs, PowerShell's call
operator (& prog) and Start-Process (-FilePath, -ArgumentList), cmd's start.
A program's arguments are never commands of their own, so `machine_lock.py run -- godot ...`
runs python, not godot. The text a nested shell runs, and read(), are
kitlib.guard.commands_nested's; the environment a line sets, commands_env's; what a git
call acts on, commands_git's.
"""
import ntpath
import re

from . import shell as sh

PYTHON_RE = re.compile(r'^(python|pythonw|py)(\d+(\.\d+)*)?$')
BASH_SHELLS = {'bash', 'sh', 'zsh', 'dash', 'ksh'}
PS_SHELLS = {'powershell', 'pwsh'}
EXE_SUFFIXES = ('.exe', '.cmd', '.bat', '.com')
PY_VALUE_OPTS = {'-X', '-W'}
ASSIGN_RE = re.compile(r'^[A-Za-z_]\w*\+?=')         # FOO=x, and bash's FOO+=x
# bash words that start a command without being its program, and words that start a construct
# whose first command is not a program run (for f in ...; select; the closing words)
BASH_SKIP = sh.BASH_PREFIX_WORDS
BASH_NO_PROGRAM = {'for', 'select', 'case', 'fi', 'done', 'esac', 'function', 'in', '}', '{', '[[', ']]'}
PS_KEYWORDS = {'if', 'elseif', 'else', 'foreach', 'for', 'while', 'do', 'until', 'switch', 'function',
               'filter', 'param', 'begin', 'process', 'end', 'try', 'catch', 'finally', 'trap',
               'return', 'throw', 'break', 'continue', 'exit', 'data', 'class', 'enum', 'using',
               'workflow', 'parallel', 'sequence', 'inlinescript', 'dynamicparam'}
PS_ASSIGN_OPS = {'=', '+=', '-=', '*=', '/=', '%=', '??='}


# wrappers whose options take a value: the option words to step over before the program
WRAPPER_VALUES = {'time': ('-f', '-o', '--format', '--output'), 'stdbuf': ('-i', '-o', '-e'),
                  'sudo': ('-u', '-g', '-C', '-D', '-p', '-U', '-h', '-r', '-t', '-T')}
# Start-Process parameters: the switches, and the ones that take a value
SP_SWITCHES = ('-wait', '-nonewwindow', '-passthru', '-usenewenvironment', '-loaduserprofile',
               '-whatif', '-confirm')
SP_VALUES = ('-filepath', '-argumentlist', '-workingdirectory', '-verb', '-windowstyle', '-credential',
             '-redirectstandardinput', '-redirectstandardoutput', '-redirectstandarderror', '-environment')
SP_ALIASES = {'-args': '-argumentlist', '-path': '-filepath', '-pspath': '-filepath', '-lup': '-loaduserprofile',
              '-nnw': '-nonewwindow', '-rso': '-redirectstandardoutput', '-rse': '-redirectstandarderror',
              '-rsi': '-redirectstandardinput', '-wd': '-workingdirectory', '-runas': '-credential',
              '-wi': '-whatif', '-cf': '-confirm',
              '-vb': '-verbose', '-db': '-debug', '-ea': '-erroraction', '-wa': '-warningaction',
              '-infa': '-informationaction', '-ev': '-errorvariable', '-wv': '-warningvariable',
              '-iv': '-informationvariable', '-ov': '-outvariable', '-ob': '-outbuffer',
              '-pv': '-pipelinevariable', '-proga': '-progressaction'}
# the common parameters every cmdlet takes, Start-Process among them: the switches, the ones that take a
# value. Kept apart from SP_SWITCHES and SP_VALUES, which name Start-Process's own
PS_COMMON_SWITCHES = ('-verbose', '-debug')
PS_COMMON_VALUES = ('-erroraction', '-warningaction', '-informationaction', '-errorvariable', '-warningvariable',
                    '-informationvariable', '-outvariable', '-outbuffer', '-pipelinevariable', '-progressaction')


class Invocation(object):
    """`name` the program's lower-case base name without .exe; `path` as written (the text bash
    passes when a backslash in it is dropped); `args` its Words (Start-Process: the -ArgumentList
    words); `cmd` the Command; `dyn` the program word is not literal; `index` the program word's
    place in cmd.words; `via` 'start-process' when Start-Process runs it, 'xargs' when xargs
    feeds it its arguments; `writes` the files Start-Process redirects into; `word` the program
    Word. Start-Process only: `listed` the -ArgumentList elements as written, before any split
    (the program's command line is their texts joined with spaces), `workdir` the
    -WorkingDirectory Word or None, `unread` why the guard cannot read the call's parameters (expand
    flags it), or None."""
    __slots__ = ('name', 'path', 'args', 'cmd', 'dyn', 'index', 'via', 'writes', 'word', 'listed', 'workdir',
                 'unread')

    def __init__(self, name, path, args, cmd, dyn, index, via=None, writes=(), word=None, listed=(),
                 workdir=None, unread=None):
        self.name, self.path, self.args, self.cmd, self.dyn, self.index = (
            name, path, args, cmd, dyn, index)
        self.via, self.writes, self.word = via, list(writes), word
        self.listed, self.workdir, self.unread = list(listed), workdir, unread

    @property
    def argv(self):
        return [w.text for w in self.args]

    def __repr__(self):
        return 'Invocation(%r, %r)' % (self.name, self.argv)


def base_name(text):
    """`C:\\x\\Godot_v4.exe` -> `godot_v4`: a program's lower-case base name without .exe."""
    b = ntpath.basename(text.replace('/', '\\')).lower()
    for suf in EXE_SUFFIXES:
        if b.endswith(suf):
            return b[:-len(suf)]
    return b


# xargs (GNU and BSD): short options that take a value, the ones whose value is optional and
# attached, and the long options that take one
XARGS_SHORT_VALUES = 'adEILnPsJR'
XARGS_SHORT_OPTIONAL = 'eil'
XARGS_LONG_VALUES = ('arg-file', 'delimiter', 'max-args', 'max-chars', 'max-procs', 'process-slot-var')


def xargs_opts(cmd, words, i):
    """For xargs at words[i]: (the replace string of -I, -J, -i or --replace (and its unique
    prefixes), else None; the index of the first word after its options). With a replace
    string xargs puts its input into the words that hold it, wherever they are."""
    k, rep = i + 1, None
    while k < len(words):
        t = passed(cmd, words[k])
        if t == '--':
            return rep, k + 1
        if not t.startswith('-') or t == '-':
            break
        if t.startswith('--'):
            nm, eq, val = t[2:].partition('=')
            if nm and 'replace'.startswith(nm):
                rep = val if eq else '{}'
            elif nm and not eq and any(o.startswith(nm) for o in XARGS_LONG_VALUES):
                k += 1
            k += 1
            continue
        j = 1
        while j < len(t):
            ch, rest = t[j], t[j + 1:]
            if ch in XARGS_SHORT_VALUES:
                val = rest
                if not rest:
                    k += 1
                    val = passed(cmd, words[k]) if k < len(words) else ''
                if ch in 'IJ':
                    rep = val
                break
            if ch in XARGS_SHORT_OPTIONAL:
                if ch == 'i':
                    rep = rest or '{}'
                break
            j += 1
        k += 1
    return rep, k


def fill_words(words, token):
    """Mark each word holding `token` (xargs' replace string, find's {}) as computed, in place:
    the program fills the token in at run time, so the word says nothing the guard can read."""
    for w in words:
        if token in w.text:
            w.dyn = True


def _skip_opts(words, i, with_value=()):
    while i < len(words) and words[i].text.startswith('-') and words[i].text != '--':
        i += 2 if words[i].text in with_value else 1
    if i < len(words) and words[i].text == '--':
        i += 1
    return i


def _param(words, names):
    """Index of the value of a PowerShell parameter (any unambiguous prefix of `names`, or
    -Name:value), or None."""
    for k, w in enumerate(words):
        t = sh.ps_dashed(w.text.lower())
        if not t.startswith('-') or len(t) < 2:
            continue
        name, colon, _v = t.partition(':')
        if any(n.startswith(name) and len(name) >= 3 or n == name for n in names):
            return ('colon', k) if colon else ('next', k + 1)
    return None


def quoted(cmd, w):
    """True when the word's source starts with a quote: a string, never a parameter or a program."""
    return 0 <= w.start < len(cmd.source) and cmd.source[w.start] in '\'"'


def canon_param(text, names, aliases=None):
    """A PowerShell parameter word (-Name, -Name:value) as the canonical name it selects among
    `names` (exact, an alias, or the one name it is a prefix of), or None. Its dash may be any of
    shell.PS_DASHES."""
    t = sh.ps_dashed(text.lower().split(':', 1)[0])
    if not t.startswith('-') or len(t) < 2:
        return None
    if t in names:
        return t
    if aliases and t in aliases:
        return aliases[t]
    hits = [n for n in names if n.startswith(t)]
    return hits[0] if len(hits) == 1 else None


def _ps_param_word(cmd, w):
    """True when PowerShell reads the word as a parameter name: not quoted, not computed, a dash
    (any of shell.PS_DASHES) and then a letter, _ or ?. `--headless` and `-1` are values."""
    if quoted(cmd, w) or w.dyn:
        return False
    t = sh.ps_dashed(w.text)
    return len(t) > 1 and t[0] == '-' and (t[1].isalpha() or t[1] in '_?')


class Unread(object):
    """A Start-Process call with no program the guard can name and a parameter it cannot read:
    `why`. invocation gives it only to expand (which flags it); every other caller gets None, as
    for any call with no program, so rolepolicy.lock_verb still reads its words (NO_PROGRAM)."""
    __slots__ = ('why',)

    def __init__(self, why):
        self.why = why


# PowerShell's stop-parsing token between two words (the scanner keeps no word for it)
PS_STOP_PARSING_RE = re.compile('[%s]{2}%%' % ''.join(sh.PS_DASHES))


def _sp_unreadable(cmd, words, i):
    """Why a Start-Process call's binding cannot be read from its words, or None: an unquoted `--`
    (every later word positional), a splat (`@p`: its parameters are in the hashtable) or `--%`
    (the rest of the line passed as written). The guard refuses these rather than read them."""
    for w in words[i + 1:]:
        if quoted(cmd, w):
            continue
        if PS_SPLAT_RE.match(w.text):
            return 'a splatted Start-Process argument (%s)' % w.text
        t = w.text
        if len(t) >= 2 and t[0] in sh.PS_DASHES and t[1] in sh.PS_DASHES and (len(t) == 2 or t[2] == '%'):
            return 'a Start-Process %s (end of parameters or stop-parsing)' % t
    src = cmd.source or ''
    for a, b in zip(words[i:], words[i + 1:]):
        if a.end is None or b.start is None or not 0 <= a.end <= b.start <= len(src):
            return 'a Start-Process call whose words the guard cannot place in its line'
        if PS_STOP_PARSING_RE.search(src[a.end:b.start]):
            return 'a Start-Process --% (stop-parsing)'
    return None


def _start_process(cmd, words, i):
    """Start-Process [-FilePath] prog [[-ArgumentList] args] with its switches anywhere.

    The words carry no commas, so an array (`'/c','echo x'`) is several words. -ArgumentList, in
    its space or colon form, takes every word up to the next parameter word: a FilePath given by
    position after it is the last of them. With no -ArgumentList, every positional word after the
    program is the list (PowerShell binds the whole array). A parameter word that is neither a
    Start-Process parameter nor a common one (or an ambiguous prefix), or a switch given `-Name:`
    and its value after a space, sets `unread`: the guard cannot tell what PowerShell binds, so the
    line is refused, never read with the program shifted. The readings are close to PowerShell's
    binding, not exact: a line PowerShell refuses is read as more text, never less."""
    rest = words[i + 1:]
    names = SP_SWITCHES + SP_VALUES + PS_COMMON_SWITCHES + PS_COMMON_VALUES
    switches = SP_SWITCHES + PS_COMMON_SWITCHES
    prog, writes, positional, listed, workdir = None, [], [], [], None
    unread = _sp_unreadable(cmd, words, i)
    k = 0

    def list_words(j):
        # the words from rest[j] up to the next parameter word
        e = j
        while e < len(rest) and not _ps_param_word(cmd, rest[e]):
            e += 1
        return rest[j:e], e

    while k < len(rest):
        w = rest[k]
        if not _ps_param_word(cmd, w):
            positional.append(w)
            k += 1
            continue
        p = canon_param(w.text, names, SP_ALIASES)
        if p is None:
            unread = unread or 'a Start-Process parameter the guard does not know (%s)' % w.text
            k += 1
            continue
        raw = cmd.source[w.start:w.end] if 0 <= w.start < w.end <= len(cmd.source) else w.text
        # -Name: value, its value the next word; -Name:C: is its value C:, never bare
        bare = ':' in w.text and ':' in raw and raw.split(':', 1)[1] == ''
        if p in switches:
            if bare:
                unread = unread or 'a Start-Process switch given a value after a space (%s)' % w.text
            k += 1
            continue
        if ':' in w.text and not bare:
            vals, k = [sh.Word(w.text.split(':', 1)[1], w.dyn, w.start, w.end)], k + 1
            if p == '-argumentlist':
                more, k = list_words(k)             # -ArgumentList:'/c','echo x'
                vals += more
        elif p == '-argumentlist':
            vals, k = list_words(k + 1)
        else:
            vals, k = rest[k + 1:k + 2], k + 2
        if p == '-filepath' and vals:
            prog = vals[0]
        elif p == '-argumentlist':
            listed.extend(vals)
        elif p in ('-redirectstandardoutput', '-redirectstandarderror'):
            writes.extend(vals)
        elif p == '-workingdirectory' and vals:
            workdir = vals[0]
    if prog is None and positional:
        prog = positional.pop(0)
    if prog is None and len(listed) > 1:
        prog = listed.pop()                         # -ArgumentList '/c ...' cmd: FilePath by position
    listed = positional + listed
    if prog is None:
        # no program the guard can name: None as for any such call, an Unread for expand to refuse
        return None if unread is None else Unread(unread)
    args = [x for v in listed for x in _split_list(v)]
    return Invocation(base_name(prog.text), prog.text, args, cmd, prog.dyn, i, via='start-process',
                      writes=writes, word=prog, listed=listed, workdir=workdir, unread=unread)


def _split_list(w):
    """An -ArgumentList string's words (split on spaces outside double quotes)."""
    if w.dyn:
        return [w]
    out = []
    for m in re.finditer(r'"([^"]*)"|(\S+)', w.text):
        out.append(sh.Word(m.group(1) if m.group(1) is not None else m.group(2), False, w.start, w.end))
    return out


def program_alt(w):
    """The program text bash runs for a word with a backslash bash drops (g\\odot runs godot), or
    None. A Windows path (<drive>:\\x, .\\x, \\\\server) keeps its backslashes, as the user meant."""
    if w.alt is None or re.match(r'^([A-Za-z]:|\.|\\)', w.text):
        return None
    return w.alt


def said(cmd, w):
    """The word's text as the shell passes it on: bash drops a backslash (_git_word), so
    `export GIT_D\\IR=x` and `env GIT\\_DIR=x` hand over GIT_DIR=x."""
    return _git_word(w).text if cmd.shell == sh.BASH else w.text


def passed(cmd, w):
    """The word's text as bash hands it to a builtin or to env: every backslash bash drops is
    dropped, a leading one too (`export \\GIT_DIR=x` sets GIT_DIR). said keeps a leading one,
    which starts a Windows path in a program word."""
    if cmd.shell == sh.BASH and w.alt is not None:
        return w.alt
    return w.text


def invocation(cmd, unread=False):
    """The Invocation `cmd` runs, or None (an assignment only, an empty command, a keyword that
    runs no program, a PowerShell expression). With `unread` (expand only), a Start-Process call
    with no program and a parameter the guard cannot read gives an Unread instead of None."""
    words = cmd.words
    i = 0
    called = False
    via = None
    while i < len(words):
        w = words[i]
        t = w.text
        name = base_name(t)
        if cmd.shell == sh.BASH and not quoted(cmd, w):
            if t == 'time':
                i = _skip_opts(words, i + 1, WRAPPER_VALUES['time'])
                continue
            if t in BASH_SKIP:
                i += 1
                continue
            if t in BASH_NO_PROGRAM:
                return None
        if cmd.shell == sh.BASH and ASSIGN_RE.match(said(cmd, w)):
            i += 1
            continue
        if cmd.shell == sh.PS and not called:
            if i + 1 < len(words) and words[i + 1].text in PS_ASSIGN_OPS:
                i += 2                          # $x = <command>: the command after the =
                continue
            if quoted(cmd, w) or (not w.dyn and t.lower() in PS_KEYWORDS):
                return None                     # a string or a keyword: no program
            if w.dyn and t not in ('&', '.'):
                return None                     # $x.Count: an expression
        if cmd.shell == sh.PS and t in ('&', '.'):
            i += 1
            called = True
            continue
        if name == 'env':
            if env_nested(words, i, cmd) is not None:
                # env -S text / env -C dir: the program is read as a nested command (expand)
                return Invocation(name, t, words[i + 1:], cmd, w.dyn, i, word=w)
            i += 1
            # env reads every word holding an = as an assignment, whatever the name
            while i < len(words) and (words[i].text.startswith('-') or '=' in passed(cmd, words[i])):
                i += 2 if words[i].text in ENV_VALUE_OPTS else 1
            continue
        if name in ('command', 'exec', 'nohup', 'builtin', 'time', 'sudo', 'stdbuf', 'caffeinate'):
            i = _skip_opts(words, i + 1, WRAPPER_VALUES.get(name, ()))
            continue
        if name == 'nice':
            i = _skip_opts(words, i + 1, ('-n',))
            continue
        if name == 'timeout' and cmd.shell != sh.CMD:
            i = _skip_opts(words, i + 1, ('-s', '-k', '--signal', '--kill-after'))
            i += 1                      # the duration
            continue
        if name == 'xargs':
            rep, i = xargs_opts(cmd, words, i)
            via = 'xargs'
            if rep is not None:                 # -I {}: the input may land in any word, git's place too
                # every later word holding it is filled in at run time: the program word, a
                # nested shell's or env -S's text, git's own words (marked in place, so the
                # program check and _nested_text read them as computed)
                fill_words(words[i:], rep)
                g = next((k for k in range(i, len(words)) if not quoted(cmd, words[k]) and
                          base_name(passed(cmd, words[k])) == 'git'), None)
                if g is not None:
                    return Invocation('git', words[g].text, words[g + 1:], cmd, words[g].dyn, g, via=via,
                                      word=words[g])
            continue
        if cmd.shell == sh.PS and name in ('start-process', 'saps', 'start'):
            sp = _start_process(cmd, words, i)
            return sp if unread or not isinstance(sp, Unread) else None
        if cmd.shell == sh.CMD and name == 'start':
            i += 1
            if i < len(words) and quoted(cmd, words[i]):
                i += 1                          # start "title" prog: a quoted first word is the title
            while i < len(words) and words[i].text.startswith('/'):
                i += 2 if words[i].text.lower() == '/d' else 1
            continue
        alt = program_alt(w) if cmd.shell == sh.BASH else None
        if alt is not None:
            return Invocation(base_name(alt), alt, words[i + 1:], cmd, w.dyn, i, via=via, word=w)
        return Invocation(name, t, words[i + 1:], cmd, w.dyn, i, via=via, word=w)
    return None


# what cmd drops before a command name: @ (no echo) and its delimiters , ; =
CMD_LEAD_RE = re.compile(r'^[@;,=]+')


def cmd_invocation(cmd, inv):
    """`inv`, cmd's invocation of `cmd`, with the @ and delimiters cmd drops before a command name
    (CMD_LEAD_RE) left out, as a prefix of the program word or as words of their own: @set, ;set
    and @ set all run set, @ path runs path. Another shell's, a computed or quoted program word,
    and one with nothing to drop come back as they are. The environment rules read cmd's set, path
    and call through it; invocation itself still names @git as `@git` (the program rules do not
    read the lead)."""
    if cmd.shell != sh.CMD or inv is None or inv.dyn or inv.word is None:
        return inv
    words, i = cmd.words, inv.index

    def placed_quoted(w):               # a word with no place in the source reads as unquoted
        return w.start is not None and quoted(cmd, w)
    while (i + 1 < len(words) and not words[i].dyn and not placed_quoted(words[i])
           and CMD_LEAD_RE.sub('', words[i].text) == ''):
        i += 1
    w = words[i]
    if w.dyn or placed_quoted(w):
        return inv if i == inv.index else Invocation(base_name(w.text), w.text, words[i + 1:], cmd, w.dyn, i,
                                                      via=inv.via, word=w)
    t = CMD_LEAD_RE.sub('', w.text)
    if not t or (i == inv.index and t == w.text):
        return inv
    return Invocation(base_name(t), t, words[i + 1:], cmd, w.dyn, i, via=inv.via, word=w)


BATCH_SUFFIXES = ('.cmd', '.bat')


def is_batch(prog):
    """True when the program text `prog` names a batch file (batch_run): .cmd or .bat, any case,
    any path, the trailing dots and blanks Windows drops dropped, or the bare kit command."""
    b = ntpath.basename(prog.replace('/', '\\')).lower().rstrip('. ')
    return b.endswith(BATCH_SUFFIXES) or b == KIT


# Where cmd ends a command name inside its own text (OBSERVED, docs/guard-jail-roles.md "Batch files and
# cmd.exe"): at its delimiters , ; = and at ( and /, so `kit,status`, `kit(x` and `kit/x` run
# kit, the rest of the word its first argument. @ and + end none. A / is in a path too, so the
# word is read both cut at the first of these and cut at the first but a /.
CMD_NAME_ENDS = (',;=(', ',;=(/')


def _cmd_names(cmd, word, text, args):
    """The (program text, argument Words) readings of cmd's program word `word` (its text `text`,
    the lead dropped): as written, then cut at the first of each CMD_NAME_ENDS set, the rest of the
    word an argument of its own before `args`. Another shell's, and a quoted word, as written only."""
    out = [(text, args)]
    if cmd.shell != sh.CMD or (word is not None and word.start is not None and quoted(cmd, word)):
        return out
    for ends in CMD_NAME_ENDS:
        k = next((j for j, c in enumerate(text) if c in ends), 0)
        if k:
            out.append((text[:k], [sh.Word(text[k:], False, None, None, synth=True)] + list(args)))
    return out


def batch_run(cmd, inv):
    """(program text, its argument Words) when `inv` runs a batch file, which CreateProcess hands
    to cmd.exe (docs/guard-jail-roles.md "Batch files and cmd.exe"): a program is_batch names, cmd's `call` of
    one, and the kit command named without an extension (`kit`, any path), which machine setup
    puts on the PATH as kit.cmd. In cmd's own text the program word is read cut where cmd ends a
    command name too (_cmd_names: `kit,status`, `call;kit`). It is read from the text alone, no
    file looked up, so a bare `kit` counts whether or not a kit.cmd is there (it fails closed).
    None otherwise, and for a computed program."""
    inv = cmd_invocation(cmd, inv)
    if inv is None or inv.dyn:
        return None
    for prog, args in _cmd_names(cmd, inv.word, inv.path, list(inv.args)):
        if cmd.shell == sh.CMD and prog.lower() == 'call':
            if not args or args[0].dyn:
                continue
            called = CMD_LEAD_RE.sub('', args[0].text)
            for prog2, args2 in _cmd_names(cmd, args[0], called, args[1:]):
                if is_batch(prog2):
                    return prog2, args2
            continue
        if is_batch(prog):
            return prog, args
    return None


# start's options that take the next word as their value (/D dir), as Git Bash spells them too
START_VALUE_OPTS = ('/d', '//d')


def bash_start(cmd, inv):
    """(program Word or None, every Word after start) when `inv` is Git Bash's start: /usr/bin/start
    is a script that runs `"$COMSPEC" //c start` with all its words, escaping only &, so cmd.exe
    reads every word before the program does, whatever the program, and a batch file it starts
    reads them again (OBSERVED, docs/guard-jail-roles.md "Batch files and cmd.exe"). The program is the first
    word after start's options (/b, //wait, /d dir) and a title (an empty word, or one with a blank,
    which Git Bash quotes and start takes as the title). None otherwise."""
    if cmd.shell != sh.BASH or inv is None or inv.dyn or inv.name != 'start' or inv.via == 'start-process':
        return None
    args = list(inv.args)
    i, titled = 0, False
    while i < len(args):
        w = args[i]
        if w.dyn:
            break
        t = w.text
        if t.startswith('/'):
            i += 2 if t.lower() in START_VALUE_OPTS else 1
            continue
        if not titled and (not t or ' ' in t or '\t' in t):
            titled = True
            i += 1
            continue
        break
    return (args[i] if i < len(args) else None), args


BASH_GLOB_CHARS = '*?['


def bash_glob(cmd, w):
    """True when bash fills in the word `w` with file names: an unquoted, unescaped * ? or [ in its
    spelling in the line (a word with no place in the line: one of them anywhere in its text, which
    fails closed). False in another shell: PowerShell and cmd pass a wildcard to a native program
    as written."""
    if cmd.shell != sh.BASH:
        return False
    if w.start is None or w.end is None:
        return any(c in BASH_GLOB_CHARS for c in w.text)
    s, i, end = cmd.source, w.start, min(w.end, len(cmd.source))
    q = None
    while i < end:
        c = s[i]
        if q == "'":
            q = None if c == "'" else q
        elif q in ('"', "$'"):
            if c == '\\':
                i += 1
            elif c == q[-1]:
                q = None
        elif c == '\\':
            i += 1
        elif c == "'" or c == '"':
            q = c
        elif c == '$' and s[i + 1:i + 2] == "'":
            q = "$'"
            i += 1
        elif c in BASH_GLOB_CHARS:
            return True
        i += 1
    return False


ENV_VALUE_OPTS = ('-u', '--unset')


def env_nested(words, i, cmd=None):
    """For `env` at words[i]: ('split', value Word, rest index) for env -S text, ('chdir', dir
    Word, rest index) for env -C dir, or None. The rest index is where the words after the
    option's value start."""
    k = i + 1
    while k < len(words):
        t = words[k].text
        if t in ('-S', '--split-string', '-C', '--chdir'):
            if k + 1 >= len(words):
                return None
            return ('split' if t in ('-S', '--split-string') else 'chdir'), words[k + 1], k + 2
        for pre, kind in (('--split-string=', 'split'), ('--chdir=', 'chdir'), ('-S', 'split'), ('-C', 'chdir')):
            if t.startswith(pre) and len(t) > len(pre):
                v = words[k]
                return kind, sh.Word(t[len(pre):], v.dyn, v.start, v.end), k + 1
        if t in ENV_VALUE_OPTS:
            k += 2
            continue
        if t.startswith('-') and t != '--' or '=' in (passed(cmd, words[k]) if cmd is not None else t):
            k += 1
            continue
        return None
    return None


# ---------------------------------------------------------------- python and the wrappers

def python_script(inv):
    """(script word index in inv.args, script base name) when inv runs a python script; else None."""
    if not PYTHON_RE.match(inv.name):
        return None
    a = inv.args
    k = 0
    while k < len(a):
        t = a[k].text
        if t in ('-c', '-m'):
            return None
        if t in PY_VALUE_OPTS:
            k += 2
            continue
        if t.startswith('-') and t != '-':
            k += 1
            continue
        return k, ntpath.basename(t.replace('/', '\\')).lower()
    return None


# The kit command (tools/kit.py, and the `kit` program on the PATH: kit, kit.cmd, kit.exe and a
# path to one all have the base name KIT) runs the kit tool its first word names, so `kit X ...`
# is read as `python <kit>/tools/X.py ...` (docs/guard-owner.md "The kit command")
KIT = 'kit'
KIT_SCRIPT = 'kit.py'
KIT_NAME_SPLIT_RE = re.compile(r'[\s"]+')


def kit_tool(words):
    """(script, Words) for the kit command given the Words `words` after it (_kit_name)."""
    script, rest, _k = _kit_name(words)
    return script, rest


def _kit_name(words):
    """(script, Words, k) for the kit command given the Words `words` after it: the tool its first
    token names as a script name ('<name>.py'), the Words after that token, and the index in
    `words` of the word holding it. The words are split at blanks and double quotes, empty tokens
    skipped: Windows PowerShell 5.1 may pass a word holding a double quote on unescaped (so the
    program splits it there or keeps the quote) and drops an empty word, so `kit '""hub' x`,
    `kit 'hub" x'` and `kit '' hub x` all read as hub with x after it. A word that may hand on
    more than one argument reads as its first token, the rest of the word split into Words of its
    own: the guard reads every token the program may see as a tool or an argument, so it fails
    closed. Script '' when no tool is named (no token, or a literal option first: tools/kit.py
    runs no tool then) with `words` as given, None when the first word that holds a token is
    computed (read as python reads a computed script: not at all)."""
    for k, w in enumerate(words):
        if w.dyn:
            return None, list(words[k + 1:]), k
        tokens = [t for t in KIT_NAME_SPLIT_RE.split(w.text) if t]
        if not tokens:
            continue
        if tokens[0].startswith('-'):
            return '', list(words), k
        split = [sh.Word(t, w.dyn, w.start, w.end, synth=True) for t in tokens[1:]]
        return tokens[0].lower() + '.py', split + list(words[k + 1:]), k
    return '', list(words), len(words)


def tool_at(inv):
    """(i, script, Words) when the invocation `inv` runs a python script, kit tools by name
    included: the script's base name ('gate.py'), the index in inv.args of the word that names
    it (-1 when the program is the script itself: gate.py, machine_lock.py, gate) and the Words
    after that word. The kit command reads as the tool it runs (kit_tool): `kit gate --full` is
    (0, 'gate.py', [--full]), `python -u tools/kit.py gate --full` (2, 'gate.py', [--full]). None
    when nothing names a script, or the kit command names no tool."""
    if inv is None:
        return None
    name = inv.name or ''
    if name == 'gate':
        return -1, 'gate.py', list(inv.args)
    if name in (KIT, KIT_SCRIPT):
        return _kit_at(inv.args, 0)
    if name.endswith('.py'):
        return -1, name, list(inv.args)
    found = python_script(inv)
    if not found:
        return None
    k, script = found
    if script == KIT_SCRIPT:
        return _kit_at(inv.args, k + 1)
    return k, script, list(inv.args[k + 1:])


def _kit_at(args, i):
    script, rest, k = _kit_name(args[i:])
    return (i + k, script, rest) if script else None


def wrapper(inv):
    """'gate' for gate.py, 'lock-run' for `machine_lock.py run`, else None, the kit command's
    `kit gate` and `kit machine_lock run` included (tool_at). These are the two entry points that
    may run heavy programs (ARCHITECTURE.md "Guard rules")."""
    found = tool_at(inv)
    if not found:
        return None
    _, script, rest = found
    if script == 'gate.py':
        return 'gate'
    if script == 'machine_lock.py':
        return 'lock-run' if rest[:1] and rest[0].text == 'run' else None
    return None


# the characters PowerShell reads in a -WorkingDirectory as a wildcard pattern (Start-Process
# resolves the text as a pattern, so '<drive>\repo?' finds <drive>\repo1) and its pattern escape
SP_WORKDIR_WILD = frozenset('*?[`')
SP_WORKDIR_DEVICE = ('\\\\.\\', '\\\\?\\')


def sp_workdir_unplaceable(text):
    """True when the literal -WorkingDirectory `text` of a Start-Process call names no one folder
    the guard can place exactly: empty; a wildcard character (SP_WORKDIR_WILD); a bare drive C:
    or a drive-relative C:x (PowerShell reads both from that drive's current location, which the
    text does not tell); a colon past the drive (another PowerShell drive or provider, a stream);
    a device path \\\\.\\ or \\\\?\\; a Git Bash /c/x spelling (shell.msys_to_win), which PowerShell
    reads as a folder on the current drive. An absolute path, a share, a FileSystem::<abs> path,
    ~ and a plain relative path are placed (shell.resolve_path)."""
    p = sh.ps_filesystem_path(text) or text
    if not p or any(c in SP_WORKDIR_WILD for c in p):
        return True
    if re.match(r'^[A-Za-z]:(?![\\/])', p) or ':' in p[2:]:
        return True
    if p[:4].replace('/', '\\') in SP_WORKDIR_DEVICE:
        return True
    return sh.msys_to_win(p) != p


def run_dir(inv):
    """The directory the program of `inv` runs in: its Command's (Command.cwd), or for a
    Start-Process call with a -WorkingDirectory the folder that names (shell.resolve_path from the
    Command's directory), None when the text is computed or names no one folder exactly
    (sp_workdir_unplaceable). PowerShell itself still places what Start-Process redirects into
    (-RedirectStandardOutput, -RedirectStandardError) from its own location, the Command's
    directory: only what the program reads relative to its directory moves."""
    if inv.via != 'start-process' or inv.workdir is None:
        return inv.cmd.cwd
    w = inv.workdir
    if w.dyn or sp_workdir_unplaceable(w.text):
        return None
    return sh.chdir_real(sh.resolve_path(w.text, inv.cmd.cwd, shell=sh.PS, home=inv.cmd.home), inv.cmd.cwd)


# a splatted argument (@h): a hashtable's keys become parameters the text does not show
PS_SPLAT_RE = re.compile(r'^@[A-Za-z_?]')


def _git_word(w):
    """The word as the shell passes it to git (a backslash bash drops, dropped)."""
    alt = program_alt(w)
    if alt is None:
        return w
    return sh.Word(alt, w.dyn, w.start, w.end, w.synth)
