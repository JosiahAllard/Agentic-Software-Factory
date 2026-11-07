"""The guard's reading of .NET and PowerShell file writes (kitlib.guard.rules, which re-exports each
name here): the .NET calls and PowerShell cmdlets that write a file, the paths they are given, where
they resolve, and the stand-in write for one the guard cannot place."""
import re

from . import commands as cm
from . import shell as sh
from .rules_base import (PS_COMMON_ALIASES, PS_COMMON_SWITCHES, PS_COMMON_VALUES, agent_session, fwd, literal_word,
                         ps_param_word, refuse, split_commas)


# a .NET file call that writes (OpenRead and OpenText only read)
IO_FILE_RE = re.compile(r'(?i)\[(?:System\.)?IO\.(?:File|Directory)\]::'
                        r'(?:Write|Append|Create|Copy|Move|Delete|Replace|Open(?!Read\b|Text\b))')
# a .NET type whose constructor opens a file to write: [IO.StreamWriter]::new(...), New-Object IO.FileStream ...
NET_TYPE_RE = re.compile(r'(?i)(?<![\w.])(?:System\.)?IO\.(?:StreamWriter|FileStream)(?![\w.])')
NET_ABS_RE = re.compile(r'^[A-Za-z]:[\\/]')     # a path .NET reads the same from any directory
# \\?\<drive>:\x and \\.\<drive>:\x are <drive>:\x (a device path); any other \\ or // path is a share the guard cannot place
NET_DEVICE_RE = re.compile(r'^[\\/]{2}[?.][\\/](?=[A-Za-z]:[\\/])')


# an Export-* cmdlet's parameters that never name the file it writes (ps_export_targets)
PS_EXPORT_NO_PATH = ('inputobject', 'depth', 'encoding') + PS_COMMON_VALUES


def ps_export_targets(cmd, words):
    """[Word]: every argument of an Export-* cmdlet with no spec in PS_CMDLETS that could name the
    file it writes. Its parameters are unknown, so a positional, the next word after any parameter
    and a colon value are each a target; only the value of a parameter named exactly in
    PS_EXPORT_NO_PATH is not (`Export-Alias -As Script $p` judges Script and $p)."""
    out, k = [], 0
    while k < len(words):
        w = words[k]
        t = ps_param_word(cmd, w)
        k += 1
        if t is None:
            out.extend(split_commas(w))
            continue
        body, colon, val = t[1:].partition(':')
        body = body.lower()
        no_path = PS_COMMON_ALIASES.get(body, body) in PS_EXPORT_NO_PATH
        if colon and val:
            if not no_path:
                out.append(sh.Word(val, w.dyn, w.start, w.end))
        elif no_path and k < len(words) and ps_param_word(cmd, words[k]) is None:
            k += 1                              # its value
    return out


# Why a path a cmdlet takes from the pipeline is one the guard cannot place
PIPE_UNPLACED = 'the text does not show what the command before the | sends'
PIPE_WORD = '(a path from the pipeline)'


def pipe_stand_in(cmd):
    """The Word write_targets gives for a path a PowerShell cmdlet takes from the pipeline
    (PSCmdlet piped): no text to place, at the command's end (pipe_stand_in_word)."""
    return sh.Word(PIPE_WORD, False, cmd.end, cmd.end)


def pipe_stand_in_word(cmd, w):
    """True when the write `w` is pipe_stand_in(cmd)."""
    return w.text == PIPE_WORD and not w.dyn and w.start == w.end == cmd.end


class NetWrite(object):
    """One path a .NET file call writes (net_writes): `word` its argument, dyn when the line
    computes it (a variable, an expression, a string with $ or a backtick), with its span in the
    source; `end` the offset just past the call's closing parenthesis; `paths` every path argument
    of the call, written or read (Copy's source too), `word` among them (net_retarget)."""
    __slots__ = ('word', 'end', 'paths')

    def __init__(self, word, end, paths=None):
        self.word, self.end, self.paths = word, end, paths if paths is not None else [word]


NET_QUOTES = '‘’‚‛“”„'     # PowerShell reads these as quotes too


def net_string_end(src, k):
    """The offset just past the PowerShell string that starts at src[k] (' or ")."""
    q, n = src[k], len(src)
    k += 1
    while k < n:
        c = src[k]
        if q == '"' and c == '`':
            k += 2
            continue
        if c == q:
            if src[k + 1:k + 2] == q:
                k += 2
                continue
            return k + 1
        k += 1
    return n


def _net_literal(raw):
    """The text of the argument `raw` when it is a plain string ('...', or "..." with no $ or
    backtick), else None."""
    if any(c in NET_QUOTES for c in raw):
        return None
    m = re.match(r"^'((?:[^']|'')*)'$", raw, re.S)
    if m:
        return m.group(1).replace("''", "'")
    m = re.match(r'^"((?:[^"$`]|"")*)"$', raw, re.S)
    return m.group(1).replace('""', '"') if m else None


def _net_args(src, k):
    """([(text or None, start, end)], end): the arguments of the call whose '(' is just before
    offset `k` of `src` (text None: not a plain string, _net_literal), and the offset just past
    its closing parenthesis (len(src) when it has none)."""
    args, n = [], len(src)
    while True:
        while k < n and src[k] in ' \t\r\n':
            k += 1
        start, depth = k, 0
        while k < n:
            c = src[k]
            if c in '\'"':
                k = net_string_end(src, k)
                continue
            if c == '`':
                k += 2
                continue
            if c in '([{':
                depth += 1
            elif c in ')]}':
                if depth == 0:
                    break
                depth -= 1
            elif c == ',' and depth == 0:
                break
            k += 1
        raw = src[start:min(k, n)].rstrip()
        if raw:
            args.append((_net_literal(raw), start, start + len(raw)))
        if k >= n:
            return args, n
        if src[k] != ',':
            return args, k + 1
        k += 1


def _net_written(method, count):
    """(indexes, all found): the indexes of the arguments a .NET File or Directory method writes -
    Copy its destination, Move both, Replace all three, any other its first - among the `count`
    arguments read, and False when one of them is past the last (the guard did not read the call
    as .NET will)."""
    if method.startswith('copy'):
        idx = (1,)
    elif method.startswith('move'):
        idx = (0, 1)
    elif method.startswith('replace'):
        idx = (0, 1, 2)
    else:
        idx = (0,)
    return [i for i in idx if i < count], all(i < count for i in idx)


def _net_lost(span):
    """True when _net_args may have lost its place in the argument text `span`, so the literal it
    read as an argument may be part of another: a smart quote (PowerShell closes one with a plain
    quote), a here-string (@" or @'), a comment (#), a braced variable (${: PowerShell reads its
    name up to the }, quotes included), or a subexpression ($() where a double quote also appears
    (a " inside it does not end the outer string)."""
    if any(c in NET_QUOTES for c in span) or '@"' in span or "@'" in span or '#' in span or '${' in span:
        return True
    return '"' in span and '$(' in span


def net_mentioned(cmd):
    """True when the PowerShell command names a .NET file call or type that writes (IO_FILE_RE,
    NET_TYPE_RE): net_writes reads it."""
    return cmd.shell == sh.PS and bool(IO_FILE_RE.search(cmd.text) or NET_TYPE_RE.search(cmd.text))


def _net_word(raw):
    """The text of a command-mode argument `raw`: a plain string (_net_literal) or a bare word,
    else None."""
    text = _net_literal(raw)
    if text is None and re.match(r'^[^\s\'"`$(){}\[\]@#;,|&<>%s]+$' % NET_QUOTES, raw):
        return raw
    return text


def _net_list(src, k):
    """([(text or None, start, end)], end): the command-mode argument at offset `k` of `src`, a
    word or a comma list of them ('a', 'b'), read as _net_args reads a call's; a bare word ends at
    a '(' (PowerShell starts a new argument there)."""
    args, n = [], len(src)
    while True:
        start, depth = k, 0
        while k < n:
            c = src[k]
            if c in '\'"':
                k = net_string_end(src, k)
                continue
            if c == '`':
                k += 2
                continue
            if c in '([{':
                if c == '(' and depth == 0 and k > start and src[k - 1] != '$':
                    break
                depth += 1
            elif c in ')]}':
                if depth == 0:
                    break
                depth -= 1
            elif depth == 0 and c in ', \t\r\n;|&':
                break
            k += 1
        k = min(k, n)
        if k > start:
            args.append((_net_word(src[start:k]), start, k))
        j = k
        while j < n and src[j] in ' \t':
            j += 1
        if j >= n or src[j] != ',':
            return args, k
        k = j + 1
        while k < n and src[k] in ' \t\r\n':
            k += 1


NEW_OBJECT_PARAMS = ('typename', 'argumentlist', 'property', 'comobject', 'strict')


def _net_new_object(cmd, inv):
    """(type (text or None, start, end), [argument], end, lost) of the New-Object call `inv`, or
    None when the guard does not read it: its -TypeName and -ArgumentList, named or positional,
    each a word, a comma list, (...) or @(...)."""
    src, n = cmd.source, len(cmd.source)
    k, vals, slots, lost = inv.word.end, {}, ['typename', 'argumentlist'], ''
    while True:
        while k < n and src[k] in ' \t':
            k += 1
        if k >= n or src[k] in ';|&\r\n)}':
            break
        m = re.compile(r'-([A-Za-z]+)(:?)').match(src, k)
        if m and src[k + 1:k + 2].isalpha():
            body = m.group(1).lower()
            names = ['argumentlist'] if body == 'args' else [p for p in NEW_OBJECT_PARAMS if p.startswith(body)]
            if len(names) != 1:                 # a common parameter (-ErrorAction Stop, -Verbose)
                body = PS_COMMON_ALIASES.get(body, body)
                if not any(p.startswith(body) for p in PS_COMMON_VALUES + PS_COMMON_SWITCHES):
                    return None
                names = ['skip' if any(p.startswith(body) for p in PS_COMMON_VALUES) else 'strict']
            k = m.end()
            if names[0] == 'strict':
                continue
            while not m.group(2) and k < n and src[k] in ' \t':
                k += 1
            param = names[0]
            if param in slots:
                slots.remove(param)
        elif slots:
            param = slots.pop(0)
        else:
            return None
        stops = ' \t;|&\r\n)}'
        if src.startswith('@(', k) or src.startswith('(', k):
            open_ = k + (2 if src[k] == '@' else 1)
            args, k = _net_args(src, open_)
            lost += src[open_:k]
        else:
            args, k = _net_list(src, k)
            lost += ''.join(src[s:e] for _t, s, e in args)
            stops += '('                        # IO.StreamWriter('x'): the list starts a new argument
        if k < n and src[k] not in stops:
            return None                         # ('a') + 'b': an expression the guard does not read
        vals[param] = args
    kind = vals.get('typename') or []
    if len(kind) != 1:
        return None
    return kind[0], vals.get('argumentlist', []), k, _net_lost(lost)


def _net_ctor(src, kind, args, end, out):
    """Add the path a StreamWriter or FileStream constructor with `args` writes to `out`: its first
    argument. True when the guard does not read it: no arguments, or a StreamWriter's first argument
    that is not a plain string (a Stream it writes through, judged where it is opened, or a path the
    line computes). A FileStream opened only to read counts as a write (docs/guard-v2.md v2:114)."""
    if not args:
        return True
    text, s, e = args[0]
    if text is None and (kind == 'streamwriter' or src[s:e].lower() == '$null'):
        return kind == 'streamwriter'
    out.append(NetWrite(sh.Word(src[s:e] if text is None else text, text is None, s, e), end))
    return False


def net_writes(cmd):
    """([NetWrite], unread): each path the PowerShell command's .NET file calls (IO_FILE_RE) and
    StreamWriter and FileStream constructors (NET_TYPE_RE: ::new(...) or New-Object) write, and True
    when a match is a write the guard does not read the path of, so write_targets judges the
    directory the command runs in, as a path it cannot place: one inside a quoted word, a method named with no '(' after it (called
    later, through .Invoke or a variable), a call with fewer arguments than the ones it writes
    (_net_written), one whose arguments the parse may have lost its place in (_net_lost), a type
    named any other way (a cast, [type] as a value) and a New-Object call _net_new_object does
    not read. A $null argument (Replace's backup) writes nothing."""
    if cmd.shell != sh.PS:
        return [], False
    out, unread, src = [], False, cmd.source
    read = []                                   # the span of the type a New-Object call names
    inv = cm.invocation(cmd) if NET_TYPE_RE.search(cmd.text) else None
    if inv is not None and inv.name == 'new-object':
        got = _net_new_object(cmd, inv)
        kind = got and got[0][0] and re.match(r'(?i)^(?:System\.)?IO\.(StreamWriter|FileStream)$', got[0][0])
        if kind:
            read.append(got[0][1:])
            unread = _net_ctor(src, kind.group(1).lower(), got[1], got[2], out) or got[3]
    for m in NET_TYPE_RE.finditer(cmd.text):
        pos, after = cmd.start + m.start(), cmd.start + m.end()
        if any(s <= pos < e for s, e in read):
            continue
        w = next((x for x in cmd.words if x.start <= pos < x.end), None)
        call = re.compile(r'\]::(\w+)').match(src, after)
        if w is None or src[w.start:w.start + 1] in '\'"' + NET_QUOTES or src[pos - 1:pos] != '[':
            unread = True                       # in a string, a type name passed as a value
            continue
        if not call:
            unread = True                       # [type] passed as a value, a cast
            continue
        if call.group(1).lower() != 'new':
            continue                            # a static member: [IO.StreamWriter]::Null
        k = call.end()
        while k < len(src) and src[k] in ' \t':
            k += 1
        if src[k:k + 1] != '(':
            unread = True
            continue
        args, end = _net_args(src, k + 1)
        kind = re.search(r'(?i)(StreamWriter|FileStream)$', m.group(0)).group(1).lower()
        unread = _net_ctor(src, kind, args, end, out) or _net_lost(src[k + 1:end]) or unread
    for m in IO_FILE_RE.finditer(cmd.text):
        pos = cmd.start + m.start()
        w = next((x for x in cmd.words if x.start <= pos < x.end), None)
        if w is None or src[w.start:w.start + 1] in '\'"' + NET_QUOTES:
            unread = True
            continue
        rest = re.compile(r'\w*').match(src, cmd.start + m.end())
        method = (m.group(0).rpartition('::')[2] + rest.group(0)).lower()
        k = rest.end()
        while k < len(src) and src[k] in ' \t':
            k += 1
        if src[k:k + 1] != '(':
            unread = True
            continue
        args, end = _net_args(src, k + 1)
        written, found = _net_written(method, len(args))
        unread = unread or not found or _net_lost(src[k + 1:end])
        words = {}
        for i in sorted(set(written) | ({0} if method.startswith('copy') and args else set())):
            text, s, e = args[i]
            if text is None and src[s:e].lower() == '$null':
                continue
            words[i] = sh.Word(src[s:e] if text is None else text, text is None, s, e)
        for i in written:
            if i in words:
                out.append(NetWrite(words[i], end, [words[j] for j in sorted(words)]))
    for x in out:
        m = None if x.word.dyn else NET_DEVICE_RE.match(x.word.text)
        if m:                                   # \\?\<drive>:\x is <drive>:\x; the span stays the argument's
            x.word = sh.Word(x.word.text[m.end():], False, x.word.start, x.word.end)
    return out, unread


def net_placed(w):
    """True when the .NET argument Word `w` is a literal absolute path on a drive (NET_ABS_RE;
    net_writes reads \\\\?\\<drive>:\\x as <drive>:\\x). A share (\\\\host\\<drive>$\\x) is not: the guard cannot tell
    which folder it is, so write_targets judges it as a path it cannot place."""
    return not w.dyn and bool(NET_ABS_RE.match(w.text))


def net_relative(w):
    """True when the .NET argument Word `w` is a literal relative path (no drive, no leading slash,
    no ~): write_targets judges it where the PowerShell location puts it, as a bash one is, though
    .NET reads it against the process's directory (NET_PROCESS_DIR), which the guard cannot see;
    so an agent session's is refused as well (net_process_dir)."""
    return not w.dyn and bool(w.text) and w.text[0] not in '\\/~' and ':' not in w.text


# where .NET reads a path that is not absolute
NET_PROCESS_DIR = ('.NET reads a relative or driveless path against the process\'s working directory '
                   '([Environment]::CurrentDirectory), not the PowerShell location')
# why a .NET write the guard cannot place is counted as a write to the directory the line runs in
NET_UNPLACED = ('a .NET call whose path the guard does not read or cannot place: a driveless path, a share or '
                'device path, or one the line computes. ' + NET_PROCESS_DIR)


def net_process_dir(event, ctx, role, what, relative, target, rewrite):
    """An agent session's .NET write whose call names a literal relative path (net_relative, the
    texts `relative`: the path written, or Copy's source), already judged where the PowerShell
    location puts it (`target`): refused all the same, under write-jail, since .NET reads it
    against the process's directory, which Set-Location does not move and the guard cannot see (a
    docs-writer's [IO.File]::WriteAllText('docs/ROSTER.md', ...) from its worktree wrote the main
    checkout). The alternative is the same call with every relative path the absolute one the
    location gives (net_retarget), which .NET reads the same from any directory."""
    if not agent_session(event, ctx):
        return None
    return refuse('write-jail', role, '%s; the call names the relative path %s: .NET reads it against the process\'s '
                  'working directory ([Environment]::CurrentDirectory), which cd and Set-Location do not move, so '
                  'the guard cannot tell what it names. An agent session gives .NET absolute paths. The same call '
                  'with the paths the PowerShell location gives:' % (what, ', '.join(relative)), rewrite(fwd(target)))


def net_retarget(cmd, x, path, place=None):
    """The command `cmd` through the end of its .NET call, with the NetWrite `x`'s argument
    replaced by `path` and, with `place` (Word -> path or None), each other literal relative path
    argument of the call (net_relative: Copy's source, Move's other path) by the path it gives, so
    the alternative names no path .NET would read from the process's directory."""
    start = cmd.words[0].start if cmd.words else cmd.start
    out, k = [], start
    for o in sorted(x.paths, key=lambda o: o.start):
        own = (o.start, o.end) == (x.word.start, x.word.end)     # net_writes may have re-read x.word (a device path)
        new = path if own else place(o) if place is not None and net_relative(o) else None
        if new is None:
            continue
        out.append(cmd.source[k:o.start] + literal_word(new))
        k = o.end
    return (''.join(out) + cmd.source[k:x.end]).strip()


def net_stand_in(cmd, w):
    """True when the write `w` is the stand-in write_targets gives a .NET write it cannot place
    (NET_UNPLACED): the directory the command runs in, at its first word."""
    return net_mentioned(cmd) and bool(cmd.words) and w.text == '.' and         (w.start, w.end) == (cmd.words[0].start, cmd.words[0].end) and cmd.source[w.start:w.end] != '.'
