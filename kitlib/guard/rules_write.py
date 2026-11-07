"""The guard's shell-write targets (kitlib.guard.rules, which re-exports each name here):
write_targets, the copy forms, the PowerShell provider paths, the unread writes, the rules a write is
judged by, find -exec runs and the rewrite of a write's word aimed at another path."""
import ntpath
import os
import re
from kitlib.git import repo

from . import commands as cm
from . import jail
from . import paths
from . import shell as sh
from .rules_base import (Asked, NULL_TARGETS, PS_ALIASES, PS_CMDLETS, UNPLACED, agent_session, bare, fwd, here,
                         literal_word, ps_args, refuse)
from .rules_run import _find_paths, kit_tool_dir
from .rules_edit import OUTPUT_WRITERS, edit_target, jail_roots, ledger_unread, outer_shell, unplaced, whole_line
from .rules_net import (BASH_WRITERS, CMD_WRITERS, COPIERS, EVALS, NET_PROCESS_DIR, NET_UNPLACED, PIPE_UNPLACED,
                        _net_string_end, net_mentioned, net_placed, net_process_dir, net_relative, net_retarget,
                        net_stand_in, net_writes, pipe_stand_in, pipe_stand_in_word, ps_export_targets)
from .rules_tools import (EDITORS, PY_PS_WHY, PY_UNREAD_WHY, Under, _awk, _copier, _curl, _editor, _evals, _patch,
                          _perl, _python, _rsync, _ruby, _sed, _tar, _unzip, _wget, python_unread)


# The coarse rule for a PowerShell writer (ps_unread). Parameters whose value is no path: their one
# value is passed over when it holds no command (_ps_value_end).
PS_VALUE_PARAMS = frozenset(('value', 'encoding', 'filter', 'include', 'exclude', 'newname', 'inputobject',
                             'delimiter', 'itemtype', 'uri', 'body', 'headers'))
_PS_SQ = r"'(?:[^'‘’‚‛]|'')*'"
_PS_DQ_PLAIN = r'"[^"“”„`$]*"'
_PS_BARE = r'[^\s$(){}\[\]@`\'"“”„‘’‚‛,;|&<>#]+'
_PS_VAR = r'\$(?:\{[A-Za-z_]\w*(?::[A-Za-z_]\w*)?\}|[A-Za-z_]\w*(?::[A-Za-z_]\w*)?)'
_PS_DQ = r'"(?:[^"“”„`$]|`[^\r\n]|%s)*"' % _PS_VAR
_PS_END = r'(?=[\s;|)}]|$)'
# a native program's argument: plain words and strings with no variable
PS_PLAIN_ARG_RE = re.compile(r'(?:%s|%s|%s)+%s' % (_PS_BARE, _PS_SQ, _PS_DQ_PLAIN, _PS_END))
# a cmdlet's argument: a variable, or a string with one, is one argument too
PS_CMDLET_ARG_RE = re.compile(r'(?:%s|%s|%s|%s(?:\.[A-Za-z_]\w*)*)+%s' % (_PS_BARE, _PS_SQ, _PS_DQ, _PS_VAR, _PS_END))
_PS_CALL_ARG = r"\s*(?:%s|%s|-?\d+)\s*" % (_PS_SQ, _PS_VAR)
_PS_CHAIN = re.compile(r'(?:\.[A-Za-z_]\w*(?:\((?:%s(?:,%s)*)?\))?)*' % (_PS_CALL_ARG, _PS_CALL_ARG))
_PS_VALUE_ATOM = re.compile(r'%s|%s|%s|%s' % (_PS_VAR, _PS_SQ, _PS_DQ, _PS_BARE))
_PS_VALUE_END = re.compile(_PS_END)
# a here-string body line PowerShell may end it on: any quote of its kind then @, indented or not
_PS_HERE_CLOSE = {"'": re.compile("(?m)^\\s*['‘’‚‛]@"), '"': re.compile('(?m)^\\s*["“”„]@')}


def _ps_group_end(src, k):
    """The offset just past the ( ) that opens at src[k], or None when it holds what this does not
    follow (a comment, a here-string, a typographic quote) or does not close."""
    depth, n = 0, len(src)
    while k < n:
        c = src[k]
        if c == '`':
            k += 2
            continue
        if c in "'\"":
            if src[k - 1:k] == '@':
                return None
            k = _net_string_end(src, k)
            continue
        if c in '#“”„‘’‚‛':
            return None
        if c == '(':
            depth += 1
        elif c == ')':
            depth -= 1
            if depth == 0:
                return k + 1
        k += 1
    return None


def _ps_value_end(cmd, p):
    """The offset just past the one value that starts at source offset p, when it holds no command
    and PowerShell passes it as one argument: a plain word or string, a variable, a ( ) the
    scanner's word spans too, each with member calls on plain arguments after it ($s.Trim(),
    (Get-Date).AddDays(1)), then a space or the end; else None."""
    src = cmd.source
    if src.startswith('(', p):
        e = _ps_group_end(src, p)
        if e is None or not any(w.start <= p and w.end == e for w in cmd.words if w.start is not None):
            return None
    elif src.startswith(("@'", '@"'), p):
        q = src[p + 1]
        w = next((w for w in cmd.words if w.start == p and w.end is not None and
                  src[w.end - 2:w.end] == q + '@' and src[p + 2:p + 4].lstrip('\r')[:1] == '\n'), None)
        if w is None:
            return None
        body = src[p + 2:w.end - 2]
        # a $( ) runs a command; a line PowerShell would close on (a typographic quote) is not
        # the end the scanner read
        if (q == '"' and '$(' in body) or _PS_HERE_CLOSE[q].search(body):
            return None
        e = w.end
    else:
        m = _PS_VALUE_ATOM.match(src, p)
        if not m:
            return None
        e = m.end()
    e = _PS_CHAIN.match(src, e).end()
    return e if _PS_VALUE_END.match(src, e) else None


def ps_unread(cmd, args, spec=None):
    """[Word]: for a PowerShell writer (a write cmdlet, an Export-*, robocopy or xcopy, curl, wget
    or tar), a target that is not literal and has no span when any argument is one the guard does
    not read as the scanner did, else []. Coarse by design, it does not depend on how the scanner
    read a ( or a { : the arguments of a cmdlet (`spec`) other than one value of a parameter in
    PS_VALUE_PARAMS must each be a plain word, string or variable (PS_CMDLET_ARG_RE), a native
    program's a plain word or string (PS_PLAIN_ARG_RE: PowerShell spreads a splat or an array into
    its arguments); none may follow a comma (an array binds to the parameter before it); and the
    command may be neither split nor followed by a ( ) or { } that no value accounts for."""
    stand_in = [sh.Word('(an argument the guard does not read)', True, cmd.end, cmd.end)]
    if cmd.split:
        return stand_in
    src = cmd.source
    spans = []                          # [start, end) of the values passed over
    if spec is not None:
        for k, w in enumerate(args):
            t = sh.ps_dashed(w.text)
            if w.start is None or cm.quoted(cmd, w) or not t.startswith('-') or len(t) < 2:
                continue
            body, colon, val = t[1:].partition(':')
            body = body.lower()
            names = spec.names()
            cands = [body] if body in names else [spec.aliases[body]] if body in spec.aliases \
                else [x for x in names if x.startswith(body)]
            if not cands or not all(c in PS_VALUE_PARAMS for c in cands) or not any(c in spec.values for c in cands):
                continue
            if colon and val:
                p = src.find(':', w.start) + 1
            elif k + 1 < len(args) and args[k + 1].start is not None:
                p = args[k + 1].start
            else:
                continue
            e = _ps_value_end(cmd, p)
            if e is not None:
                spans.append((p, e))
    arg_re = PS_CMDLET_ARG_RE if spec is not None else PS_PLAIN_ARG_RE
    first = next((w.start for w in args if w.start is not None), None)
    prev = None                         # the word before the first argument: the program's name
    if first is not None:
        prev = max((x for x in cmd.words if x.end is not None and x.end <= first),
                   key=lambda x: x.end, default=None)
    for w in args:
        if w.start is None or w.start == w.end:
            if w.dyn:
                return stand_in
            continue
        if prev is not None and prev.end is not None and ',' in src[prev.end:w.start]:
            return stand_in
        prev = w
        if any(s <= w.start and w.end <= e for s, e in spans):
            continue
        if sh.ps_dashed(w.text).startswith('-') and not cm.quoted(cmd, w) and spec is not None and \
                any(s == src.find(':', w.start) + 1 and w.start < s and w.end <= e for s, e in spans):
            continue                    # -Value:$v: the value passed over
        m = arg_re.match(src, w.start)
        if not m or m.end() != w.end:
            return stand_in
    if cmd.next_op in ('(', '{') and not any(s < cmd.end < e for s, e in spans):
        return stand_in
    return []


def write_targets(cmd, inv):
    """[Word]: what the command writes, by its redirections and the file commands the guard
    knows. A Word whose text is '.' stands for the directory the command runs in."""
    out = []
    for r in cmd.redirects:
        if not r.writes:
            continue
        t = r.target
        if t is None:
            if cmd.shell == sh.PS:      # `> <#c#>(...)`: a target the scanner did not read
                out.append(sh.Word('(a redirection target the guard does not read)', True, cmd.end, cmd.end))
            continue
        if t.text.lower() in NULL_TARGETS:
            continue
        if cmd.shell == sh.PS and not t.dyn and t.start is not None and t.start != t.end:
            # the coarse rule (ps_unread) for a redirection: a target that is not one plain word,
            # string or variable (`> `<newline>(...)`) is one the line computes
            m = PS_CMDLET_ARG_RE.match(cmd.source, t.start)
            if not m or m.end() != t.end:
                t = sh.Word(t.text, True, t.start, t.end)
        out.append(t)
    if net_mentioned(cmd):
        net, unread = net_writes(cmd)
        # a literal path is judged where it points, a relative one where the location puts it (and,
        # for an agent session, refused: net_process_dir), a computed one as a cmdlet's computed
        # target is; for any other the directory the command runs in stands in, as a path it
        # cannot place (NET_UNPLACED)
        out.extend(x.word for x in net if net_placed(x.word) or net_relative(x.word) or x.word.dyn)
        if unread or any(not (net_placed(x.word) or net_relative(x.word) or x.word.dyn) for x in net):
            out.append(here(cmd.words[0] if cmd.words else None))
    if inv is None:
        return out
    out.extend(inv.writes)
    name, args = inv.name, inv.args
    word = inv.word
    if inv.via == 'xargs':
        # xargs appends the names it reads: a stand-in name in the directory it runs in
        args = list(args) + [here(word)]
    if name in COPIERS:
        return out + _copier(args) + (ps_unread(cmd, args) if cmd.shell == sh.PS else [])
    if cmd.shell != sh.PS and name == 'find':
        if any(w.text == '-delete' for w in args):
            return out + (_find_paths(args)[0] or [here(word)])
        return out
    if cmd.shell == sh.PS and not inv.path.lower().endswith('.exe'):
        cmdlet = PS_ALIASES.get(name, name)
        if cmdlet in PS_CMDLETS and cmdlet != 'get-childitem':
            spec = PS_CMDLETS[cmdlet]
            bound = ps_args(cmd, args, cmdlet)
            # a provider-aware cmdlet's item on a drive that holds no files (env:FOO) is no file;
            # every other writer (a native program, a script function, Out-File) gets a file
            native = name not in PS_PROVIDER_CMDLETS or inv.via is not None
            for p in spec.writes:
                out.extend(x for x in bound.get(p, []) if native or not ps_provider_item(cmd, x))
            if spec.writes:
                out.extend(ps_unread(cmd, args, spec))
            piped = [g for g in spec.piped if cmd.piped_in and not any(bound.get(p) for p in g)]
            if piped:
                out.append(pipe_stand_in(cmd))
            if spec.here and not bound.get(spec.here) and not any(spec.here in g for g in piped):
                out.append(here(word))
            if cmdlet == 'new-item' and bound.get('name'):
                base = (bound.get('path') or [None])[0]
                for n in bound['name']:
                    x = sh.Word(ntpath.join(base.text, n.text) if base is not None else n.text,
                                n.dyn or (base is not None and base.dyn), n.start, n.end)
                    if native or not ps_provider_item(cmd, x):
                        out.append(x)
            if name not in ('curl', 'wget'):
                return out
        elif cmdlet.startswith('export-') and cmdlet not in PS_CMDLETS:
            return out + ps_export_targets(cmd, args) + ps_unread(cmd, args, PS_CMDLETS['export-clixml'])
    if cmd.shell == sh.CMD and name in CMD_WRITERS:
        pos = [w for w in args if not w.text.startswith('/')]
        if name == 'type':
            return out
        if name in ('copy', 'xcopy', 'robocopy', 'ren', 'rename'):
            return out + pos[-1:] if name != 'robocopy' else out + pos[1:2]
        return out + pos
    # a native writer PowerShell runs (curl and wget are also Invoke-WebRequest's aliases there)
    grouped = ps_unread(cmd, args) if cmd.shell == sh.PS and name in ('curl', 'wget', 'tar') else []
    if grouped and any(x.start == x.end == cmd.end and x.dyn for x in out):
        grouped = []                            # the cmdlet branch's stand-in already
    if name == 'curl':
        return out + _curl(args, word) + grouped
    if name == 'wget':
        return out + _wget(args, word) + grouped
    if cm.PYTHON_RE.match(name):
        return out + _python(cmd, name, args, word, inv.via)
    if name == 'ruby' and cmd.shell != sh.PS:
        return out + _ruby(args, word)
    if name in EVALS:
        return out + _evals(name, args, word)
    if name == 'tar':                           # Windows' own tar.exe runs from PowerShell too
        return out + _tar(args, word) + grouped
    if cmd.shell == sh.PS:
        return out
    if name == 'sed':
        return out + _sed(args)
    if name == 'perl':
        return out + _perl(args, word)
    if name in ('awk', 'gawk'):
        return out + _awk(args, word)
    if name in EDITORS:
        return out + _editor(name, args)
    if name == 'unzip':
        return out + _unzip(args, word)
    if name == 'dd':
        return out + [sh.Word(w.text[3:], w.dyn, w.start, w.end) for w in args if w.text.startswith('of=')]
    if name == 'patch':
        return out + _patch(args, word)
    if name == 'rsync':
        return out + _rsync(args)
    if name in BASH_WRITERS:
        if name in COPY_LONGS:
            return out + copy_writes(name, args, word)
        pos = [w for w in args if not (w.text.startswith('-') and len(w.text) > 1)]
        return out + pos
    return out


GLOB_CHARS = set('*?[]{}+:')                   # ':' a drive-qualified name (D:x is relative to D:'s folder)

# GNU coreutils cp, mv, ln and install: the long options each takes (getopt_long reads a unique
# prefix as the option; a name missing here reads as unknown, and an unknown option makes every
# operand count), those whose value may be the next word, the short options with a value, and the
# options a copy into an existing folder may carry for the guard to judge it by the sources' names
# (with any other option the folder itself is judged).
COPY_LONGS = {
    'cp': ('archive', 'attributes-only', 'backup', 'copy-contents', 'context', 'debug', 'dereference', 'force',
           'help', 'interactive', 'keep-directory-symlink', 'link', 'no-clobber', 'no-dereference',
           'no-preserve', 'no-target-directory', 'one-file-system', 'parents', 'preserve', 'recursive',
           'reflink', 'remove-destination', 'sparse', 'strip-trailing-slashes', 'suffix', 'symbolic-link',
           'target-directory', 'update', 'verbose', 'version'),
    'mv': ('backup', 'context', 'debug', 'exchange', 'force', 'help', 'interactive', 'no-clobber', 'no-copy',
           'no-target-directory', 'strip-trailing-slashes', 'suffix', 'target-directory', 'update', 'verbose',
           'version'),
    'ln': ('backup', 'directory', 'force', 'help', 'interactive', 'logical', 'no-dereference',
           'no-target-directory', 'physical', 'relative', 'suffix', 'symbolic', 'target-directory', 'verbose',
           'version'),
    'install': ('backup', 'compare', 'context', 'debug', 'directory', 'group', 'help', 'mode',
                'no-target-directory', 'owner', 'preserve-context', 'preserve-timestamps', 'strip',
                'strip-program', 'suffix', 'target-directory', 'verbose', 'version'),
}
COPY_VALUE_LONGS = {'suffix', 'target-directory', 'no-preserve', 'sparse', 'group', 'mode', 'owner', 'strip-program'}
COPY_VALUE_SHORTS = {'cp': 'St', 'mv': 'St', 'ln': 'St', 'install': 'Stmog'}
COPY_INTO_SHORTS = {'cp': set('rRapvnt'), 'mv': set('vnt'), 'ln': set('svt'), 'install': set('vmt')}
COPY_INTO_LONGS = {'cp': {'recursive', 'archive', 'verbose', 'no-clobber', 'target-directory'},
                   'mv': {'verbose', 'no-clobber', 'target-directory'},
                   'ln': {'symbolic', 'verbose', 'target-directory'},
                   'install': {'verbose', 'mode', 'target-directory'}}


class CopyArgs(object):
    """cp, mv, ln or install's arguments: `dirs` the -t/--target-directory values, `pos` the
    operands, `shorts` and `longs` the options (a long one by its full name), `unsure` an option
    the text does not name (not literal, or a long prefix that is unknown or ambiguous)."""

    def __init__(self, name, args):
        self.dirs, self.pos, self.shorts, self.longs, self.unsure = [], [], set(), set(), False
        names = COPY_LONGS[name]
        k = 0
        while k < len(args):
            w = args[k]
            t = w.text
            if t == '--':
                self.pos.extend(args[k + 1:])
                break
            if not (t.startswith('-') and len(t) > 1):
                self.pos.append(w)
                k += 1
                continue
            if w.dyn:
                self.unsure = True
                k += 1
                continue
            value = None
            if t.startswith('--'):
                body, eq, val = t[2:].partition('=')
                full = body if body in names else None
                if full is None:
                    cands = [n for n in names if n.startswith(body)]
                    full = cands[0] if len(cands) == 1 else None
                if full is None:
                    self.unsure = True
                    k += 1
                    continue
                self.longs.add(full)
                if eq:
                    value = sh.Word(val, w.dyn, w.start, w.end)
                elif full in COPY_VALUE_LONGS:
                    value = args[k + 1] if k + 1 < len(args) else None
                    k += 1
                if full == 'target-directory' and value is not None:
                    self.dirs.append(value)
                k += 1
                continue
            body = t[1:]
            for j, c in enumerate(body):
                self.shorts.add(c)
                if c in COPY_VALUE_SHORTS[name]:
                    rest = body[j + 1:]
                    if rest:
                        value = sh.Word(rest, w.dyn, w.start, w.end)
                    else:
                        value = args[k + 1] if k + 1 < len(args) else None
                        k += 1
                    if c == 't' and value is not None:
                        self.dirs.append(value)
                    break
            k += 1

    def no_target(self):
        return 'T' in self.shorts or 'no-target-directory' in self.longs

    def makes_dirs(self, name):
        return name == 'install' and ('d' in self.shorts or 'directory' in self.longs)


def copy_writes(name, args, word):
    """[Word]: what cp, mv, ln or install writes. An option the text does not name may shift
    the operands, so then every operand counts."""
    ca = CopyArgs(name, args)
    if ca.unsure or name == 'mv' or ca.makes_dirs(name):
        return ca.dirs + ca.pos                 # mv also removes its sources
    if ca.dirs:
        return ca.dirs
    if name == 'ln' and len(ca.pos) == 1 and not ca.no_target():
        return [here(word)]                     # ln <target>: a link in the directory it runs in
    return ca.pos[-1:]


def copy_into(cmd, inv):
    """(destination Word, [source Word], sources must be files) of a copy, move or link that,
    when the destination is an existing folder, writes <folder>/<each source's name>: cp, mv, ln
    and install with only the options COPY_INTO_SHORTS/LONGS allow, Copy-Item and Move-Item,
    cmd's copy and move (of a file: cmd copies a folder's files, not the folder). None for
    anything else, and then the folder itself is judged."""
    if inv is None or inv.via == 'xargs':
        return None
    name, args = inv.name, inv.args
    if cmd.shell == sh.PS and not inv.path.lower().endswith('.exe'):
        cmdlet = PS_ALIASES.get(name, name)
        if cmdlet not in ('copy-item', 'move-item'):
            return None
        bound = ps_args(cmd, args, cmdlet)
        dest = bound.get('destination') or []
        srcs = bound.get('path', []) + bound.get('literalpath', [])
        return (dest[0], srcs, False) if len(dest) == 1 else None
    if cmd.shell == sh.CMD and name in ('copy', 'move'):
        pos = [w for w in args if not w.text.startswith('/')]
        return (pos[-1], pos[:-1], name == 'copy') if len(pos) > 1 else None
    if cmd.shell == sh.PS or name not in COPY_LONGS:
        return None
    ca = CopyArgs(name, args)
    if ca.unsure or ca.no_target() or ca.makes_dirs(name) or len(ca.dirs) > 1 or \
            ca.shorts - COPY_INTO_SHORTS[name] or ca.longs - COPY_INTO_LONGS[name]:
        return None
    if ca.dirs:
        return ca.dirs[0], ca.pos, False
    if name == 'ln' and len(ca.pos) == 1:
        return here(inv.word), ca.pos, False
    return (ca.pos[-1], ca.pos[:-1], False) if len(ca.pos) > 1 else None


def into_folder(target, w, into, cwd, home=None):
    """[path]: what a write of `target` (the Word `w`) writes. When `into` (copy_into) names `w`
    as the destination and `target` is an existing folder, <target>/<each source's name>; a
    source the guard cannot name (not literal, a wildcard, a drive-qualified name, no name, or a
    folder where only a file is read so) keeps the folder itself. `~` is `home` (ctx.home)."""
    if into is None or not os.path.isdir(target):
        return [target]
    dest, srcs, files_only = into
    if (dest.start, dest.end, dest.text) != (w.start, w.end, w.text) or not srcs:
        return [target]
    out = []
    for s in srcs:
        if s.dyn:
            base = ''
        elif paths.posix():
            base = s.text.rstrip('/').split('/')[-1]    # Linux: `\` is a name's character
        else:
            base = re.split(r'[\\/]', s.text.rstrip('\\/'))[-1]
        if not base or base in ('.', '..') or GLOB_CHARS & set(base):
            return [target]
        if files_only:
            src = sh.resolve_path(s.text, cwd, home=home)
            if not src or not os.path.isfile(src):
                return [target]
        out.append(os.path.join(target, base))
    return out


# A PowerShell item on a built-in drive that holds no files: env:FOO, Env:\FOO, variable:x,
# function:f, alias:a, HKCU:\x, HKLM:/x, Cert:\x, WSMan:\x, or the provider spelled out
# (Environment::FOO, Microsoft.PowerShell.Core\Registry::HKEY_CURRENT_USER\x). Temp: and
# FileSystem:: hold files and are not here.
PS_PROVIDER_ITEM_RE = re.compile(
    r'(?i)^(?:(?:env|variable|function|alias|hkcu|hklm|cert|wsman):'
    r'|(?:microsoft\.(?:powershell\.(?:core|security)|wsman\.management)\\)?'
    r'(?:environment|variable|function|alias|registry|certificate|wsman)::)')
DOTDOT_RE = re.compile(r'(?:^|[\\/:])\.\.(?:[\\/]|$)')
# A line that may remap a drive name to a folder (New-PSDrive env FileSystem <folder>) or run code
# the text does not show: there env:x may be a file, so it is judged as one. Matched against the
# raw line and against each word as the parser decodes it (n`dr reads ndr, 'New-PSDr'+'ive' reads
# New-PSDr+ive).
PS_DRIVE_REMAP_RE = re.compile(r'(?im)drive|psdr|\b(?:ndr|rdr|mount|iex|ipmo)\b|invoke-expression|import-module'
                               r'|\.ps1\b|\.psm1\b|scriptblock|sessionstate|(?:^|[;&|({])\s*\.\s')
# Session objects, .NET types and static calls a line may reach a drive through
PS_DOTNET_RE = re.compile(r'(?im)\$executioncontext|\$host\b|new-object|add-type|^\s*using\b|[\])]\s*::')
# The provider-aware item and content cmdlets (PSCmdlet provider), as the line names them: the
# canonical name or one of PS_PROVIDER_ALIASES (any other alias is judged as a file write). Their
# item on a drive that holds no files is no file. Script functions (Compress-Archive, mkdir) and
# the file-only writers (Out-File, Tee-Object, Export-*, Start-Transcript, Invoke-WebRequest
# -OutFile) are not here and are judged as files.
PS_PROVIDER_ALIASES = ('ni', 'sc', 'ac', 'clc', 'ri', 'del', 'rni', 'mi', 'move', 'cpi', 'copy', 'sp')
PS_PROVIDER_CMDLETS = frozenset([n for n, s in PS_CMDLETS.items() if s.provider] +
                                [a for a in PS_PROVIDER_ALIASES if PS_CMDLETS[PS_ALIASES[a]].provider])
# Read-only cmdlets a line of provider items may also hold
PS_READ_CMDLETS = frozenset(('get-item', 'gi', 'get-content', 'gc', 'get-childitem', 'gci', 'test-path',
                             'get-location', 'gl', 'pwd', 'write-output', 'echo', 'write-host'))
# The one native program such a line may also run: git, named literally (git.exe cannot remap a
# PowerShell drive, and its words meet the git rules)
PS_PLAIN_GIT = ('git', 'git.exe')
# A word that only reads an environment variable, with a literal tail: $env:TEMP, "$env:TEMP\x.txt"
PS_ENV_READ_WORD_RE = re.compile(r'(?i)^\$(?:env:[A-Za-z_]\w*|\{env:[A-Za-z_]\w*\})(?:[\\/][^$`()\[\]{}]*)?$')


def ps_line_plain(cmd):
    """True when every command on the PowerShell line `cmd` comes from is a plain call: the parser
    reads the line cleanly; each command's first word is a literal name of a cmdlet in
    PS_PROVIDER_CMDLETS or PS_READ_CMDLETS, or git (PS_PLAIN_GIT) (no call or dot-source operator,
    no assignment or other expression statement, no computed name, no Start-Process); every word is
    literal or only reads an environment variable (PS_ENV_READ_WORD_RE; no other variable, member
    call or subexpression); and neither the raw line nor any decoded word matches
    PS_DRIVE_REMAP_RE, commands.PS_SCRIPT_TEXT_RE or PS_DOTNET_RE. Anything else may remap a drive.
    The line is read again with `~` as the home its own parse read (Command.home)."""
    src = cmd.source
    if any(r.search(src) for r in (PS_DRIVE_REMAP_RE, cm.PS_SCRIPT_TEXT_RE, PS_DOTNET_RE)):
        return False
    parsed = cm.read(src, sh.PS, cmd.cwd, home=cmd.home)
    if not parsed.clean:
        return False
    for c in parsed.commands:
        if not c.words:
            return False
        inv = cm.invocation(c)
        if inv is None or inv.dyn or inv.via is not None or inv.index != 0 or c.words[0].dyn:
            return False
        git = inv.name == 'git' and c.words[0].text.lower() in PS_PLAIN_GIT
        if not git and inv.name not in PS_PROVIDER_CMDLETS | PS_READ_CMDLETS:
            return False
        for w in c.words:
            if w.dyn and not PS_ENV_READ_WORD_RE.match(w.text) or any(r.search(w.text) for r in (PS_DRIVE_REMAP_RE, cm.PS_SCRIPT_TEXT_RE, PS_DOTNET_RE)):
                return False
    return True


def ps_provider_word(cmd, inv, w):
    """True when the Word `w`, written by the command `cmd` (invocation `inv`), is a literal item
    of a provider-aware cmdlet on a drive that holds no files (PS_PROVIDER_ITEM_RE, no `..` part),
    whatever the line around it: ps_provider_item without ps_line_plain. Its refusal says why
    (PROVIDER_ITEM_WHY) instead of naming a file to write."""
    return cmd.shell == sh.PS and inv is not None and inv.via is None and inv.name in PS_PROVIDER_CMDLETS and \
        not w.dyn and bool(PS_PROVIDER_ITEM_RE.match(w.text)) and not DOTDOT_RE.search(w.text)


def ps_provider_item(cmd, w):
    """True when the Word `w`, an item a cmdlet in PS_PROVIDER_CMDLETS writes (write_targets calls
    it only for those, never for a native program, curl, wget, Start-Process or another cmdlet),
    names an item on a built-in drive that holds no files (PS_PROVIDER_ITEM_RE): a literal word,
    no `..` part, on a line of plain calls (ps_line_plain). Writing such an item writes no file. A
    native program reads env:FOO as the stream FOO of a file named env: a file write."""
    if cmd.shell != sh.PS or w.dyn or not PS_PROVIDER_ITEM_RE.match(w.text) or DOTDOT_RE.search(w.text):
        return False
    return ps_line_plain(cmd)


# a PowerShell word that is the location and a literal rest: $PWD, "${PWD}\x", $pwd/x/y
PS_PWD_WORD_RE = re.compile(r'(?i)^(?:\$pwd|\$\{pwd\})((?:[\\/][^$`]*)?)$')
PS_BRACED_VAR_RE = re.compile(r'\$\{[^}]*\}')     # ${name}: a variable, no code


def ps_pwd_path(cmd, w):
    """The path text a PowerShell word spells as $PWD (or ${PWD}) and a literal rest, with the
    location as cmd.cwd, or None: the word is not one, the directory is unknown, or the write is
    not the line's only command. $PWD can be given another value under many spellings of its
    name (sv PWD, -OutVariable ('P'+'WD'), [scriptblock]::Create(...).Invoke()), and only code
    run before the target is read can do it: another command (a ; && || or newline, a pipeline,
    a script block or ( ) group around it) or an argument of its own that runs code (a ( ),
    $( ) or { } in a computed word). Each of those fails closed."""
    if cmd.shell != sh.PS or not w.dyn or cmd.cwd is None:
        return None
    m = PS_PWD_WORD_RE.match(w.text)
    if not m:
        return None
    src = cmd.source or ''
    if cmd.start is None or cmd.nested_in is not None or cmd.groups or cmd.subs or cmd.prev_op or cmd.next_op or \
            cmd.piped_in or cmd.piped_out or src[:cmd.start].strip() or src[cmd.end:].strip():
        return None                             # not the line's one command
    args = list(cmd.words) + [r.target for r in cmd.redirects if r.target is not None]
    if any(x is not w and x.dyn and re.search(r'[({]', PS_BRACED_VAR_RE.sub('', x.text)) for x in args):
        return None                             # an argument that runs code: (...), $(...), {...}
    return cmd.cwd + m.group(1)


BASH_DEVICE_RE = re.compile(r'^/dev/')         # /dev/stderr, /dev/tty, /dev/fd/3: no file
BASH_TMP_RE = re.compile(r'^/tmp(?:/|$)')        # Git Bash mounts /tmp on a temp folder: jail.bash_tmp


def py_unread(cmd, inv, w):
    """Why python's stand-in write `w` (_python's here()) is one (python_unread), or None for any
    other write: the reason a refusal gives, not a file it names. The verdict is unchanged."""
    if inv is None or not cm.PYTHON_RE.match(inv.name or '') or w.text != '.' or \
            cmd.source[w.start:w.end] == '.':
        return None
    return python_unread(cmd, inv.name, inv.args, inv.via) or PY_UNREAD_WHY


def unread_write(cmd, inv, w):
    """True when the write `w` stands for the directory a program runs in (here()) because the
    guard does not read what the program writes: python's code (py_unread), node -e, ruby -e or
    perl -e code, the names xargs reads, a .NET call's path. Such a write is unplaced(); a stand-in
    for a program that does write there (tar -x, curl -O, ln) is not."""
    if w.text != '.' or cmd.source[w.start:w.end] == '.':
        return False
    if py_unread(cmd, inv, w) is not None:
        return True
    if inv is not None and (inv.name in EVALS or inv.name == 'perl' or inv.via == 'xargs'):
        return True
    return net_stand_in(cmd, w)


def _write_from(event, ctx, cmd, inv, cwd, role, w, base, net, into, searched):
    """(Decision or None, searched) for the write target `w` of shell_writes read from `base`
    (OUTER, or the folder a Start-Process -WorkingDirectory names)."""
    runs_in = cmd.cwd if base is OUTER else base
    asked = Asked()                             # find's ask waits for this target's own refusal
    piped = pipe_stand_in_word(cmd, w)
    target, why = (None, PIPE_UNPLACED) if piped else write_place(ctx, cmd, w, base)
    if target is None and w.dyn and (w.start, w.end) in net:
        why = '%s; %s' % (why, NET_PROCESS_DIR)
    unread = py_unread(cmd, inv, w) or (NET_UNPLACED if net_stand_in(cmd, w) else None)
    # a kit tool acts on the checkout its -C names: that folder, not the one it runs in
    checked = kit_tool_dir(event, ctx, cmd, inv, cwd) if base is OUTER and unread == PY_PS_WHY else None
    if checked is not None:
        target, why = checked[1], None
    if target is None and not searched:
        searched = True                         # find's start paths answer for every such target at once
        d = asked.firm(find_exec_writes(event, ctx, cmd, cwd, role))
        if d:
            return d, searched
    computed = target is None and cmd.shell == sh.PS and w.dyn
    if computed:
        pass                                    # PowerShell's, from any location: one $p or (...) can name the main checkout (B15)
    elif target is None and runs_in is not None and \
            ctx.place(runs_in).kind not in (repo.MAIN, repo.UNKNOWN):
        # a target it cannot place, from a worktree or elsewhere: passes, but while a ledger key is
        # unread it can be the ledger
        return asked.first or ledger_unread(ctx, role, '`%s` writes %s' % (cmd.text, w.text)), searched
    what = '`%s` writes the path the pipeline gives it' % cmd.text if piped else \
        '`%s` writes %s' % (cmd.text, w.text if w.text != '.' else 'files in the directory it runs in') \
        if unread is None else \
        'the directory `%s` runs in (counted as written: %s)' % (cmd.text, unread) if checked is None else \
        'the checkout `%s` acts on, %s (counted as written: %s)' % (cmd.text, checked[0].text, unread)
    # here() or ps_unread(): no word to aim
    stand_in = (w.text == '.' and cmd.source[w.start:w.end] != '.') or w.start == w.end
    # a value its option's word also spells (of=, --output=, -tDIR) keeps that option before it
    rewrite = None if stand_in else word_rewrite(cmd, w)
    attached = attached_lead(cmd, w) if rewrite is not None and not isinstance(w, Under) else None
    if (w.start, w.end) in net:
        rewrite, attached = (lambda path, x=net[(w.start, w.end)]: net_retarget(
            cmd, x, path, lambda o: write_place(ctx, cmd, o, base)[0])), None
    if checked is not None:
        x = checked[0]
        # -C'dir' spelt other than its text: the value's span is the whole word, so the rewrite spells -C again
        lead = '' if any(a is x for a in inv.args) or cmd.source[x.start:x.end] == x.text else '-C '
        rewrite = (lambda path: retarget(cmd, x, path if path.startswith('<') else
                                         fwd(paths.norm(path, fold=False)), lead)) if x.start is not None else None
    if stand_in and base is not OUTER and base is not None:
        # the folder it runs in is the -WorkingDirectory value: aim that word instead
        rewrite = lambda path, x=inv.workdir: retarget(cmd, x, paths.norm(path, fold=False))
    placed = target is not None and not unread_write(cmd, inv, w)
    output = any(r.writes and r.target is not None and r.target.start is not None and
                 (r.target.start, r.target.end) == (w.start, w.end) for r in cmd.redirects) or (getattr(inv, 'name', None) or '').lower() in OUTPUT_WRITERS
    item = target is None and ps_provider_word(cmd, inv, w)
    for t in into_folder(target, w, into, runs_in, getattr(ctx, 'home', None)) if target else [target]:
        aim = rewrite
        if attached is not None and checked is None and t != target and rewrite is not None:
            # -tDIR names the folder: the same copy into the folder that holds the file it would write
            aim = lambda path, r=rewrite: r(fwd(path).rsplit('/', 1)[0] if '/' in fwd(path) else path)
        # a provider item's refusal runs the whole line from the worktree: a statement before the
        # call may set what it reads (PROVIDER_ITEM_WHY)
        d = edit_target(event, ctx, t, cwd, role, what, why, rewrite=aim,
                        run=whole_line(ctx, cmd) if item else bare(cmd), redirect=output,
                        shell=outer_shell(cmd), item=item,
                        stand_in=piped or w.start == w.end or unread_write(cmd, inv, w))
        if not placed:
            d = unplaced(d, role) or ledger_unread(ctx, role, what)   # a warned refusal leaves the ask
        d = asked.firm(d)
        if d:
            return d, searched
    # the relative path this write names, or a relative one the call only reads (Copy's source);
    # another relative path it writes is judged as its own target first (Move's into the main checkout)
    others = net[(w.start, w.end)].paths if (w.start, w.end) in net else []
    relative = [o.text for o in others if net_relative(o)]
    if target is not None and base is OUTER and others and \
            (net_relative(w) or any(net_relative(o) and (o.start, o.end) not in net for o in others)):
        d = asked.firm(net_process_dir(event, ctx, role, what, relative, target, rewrite))
        if d:
            return d, searched
    # A computed PowerShell target, or a path read from a directory the guard cannot tell (after a
    # `cd $x`): for a role main_checkout lets through, the jail cannot judge it either. A .NET
    # call's path is refused under write-jail, not unplaced (ps_drive_jail).
    if computed or (target is None and runs_in is None):
        d = ps_drive_jail(event, ctx, cwd, role, what, why, rewrite, net=(w.start, w.end) in net,
                          here=runs_in)
        if d:
            return d, searched
    return asked.first, searched


OUTER = object()                                # write_place: the command's own directory


def write_place(ctx, cmd, w, base=OUTER):
    """(path, why) of the Word `w` the command `cmd` writes, read from cmd's directory and shell:
    path None when the guard cannot place it (why says why), a PowerShell drive or provider path
    shell.resolve_path cannot place among them (docs/guard-v2.md B16). `base`, when given, is the
    folder a relative `w` is read from instead of cmd's directory: the -WorkingDirectory a
    Start-Process program runs in (commands.run_dir), None when the guard cannot place that."""
    here_dir = cmd.cwd if base is OUTER else base
    pwd = ps_pwd_path(cmd, w)
    if pwd is not None:
        target, why = sh.resolve_path(pwd, cmd.cwd, cmd.shell, getattr(ctx, 'home', None)), None
    elif w.dyn:
        target, why = None, 'a target that is not literal: %s' % w.text
    elif cmd.shell == sh.BASH and BASH_TMP_RE.match(w.text) and not paths.posix():
        target, why = paths.norm(os.path.join(jail.bash_tmp(ctx), w.text[5:]), fold=False), None
    else:
        target = sh.resolve_path(w.text, here_dir, cmd.shell, getattr(ctx, 'home', None))
        why = None if target else 'the directory it runs in is unknown' if here_dir is None \
            else 'a path the text cannot place: %s' % w.text
        if target is None and base is None:
            why = 'the folder its Start-Process -WorkingDirectory names is computed or not one the guard can place'
    return target, why


def find_runs(cmd):
    """[(find command, its invocation)]: each bash find whose -exec, -execdir, -ok or -okdir runs
    `cmd`, nearest first, through any shell nested between (commands.expand nests only those
    words under a find)."""
    out, outer = [], cmd.nested_in
    while outer is not None:
        if outer.shell == sh.BASH:
            inv = cm.invocation(outer)
            if inv is not None and not inv.dyn and inv.name == 'find':
                out.append((outer, inv))
        outer = outer.nested_in
    return out


def find_exec_writes(event, ctx, cmd, cwd, role):
    """A write the guard cannot place by a command a bash find runs (find_runs): the found path
    filled into {}, a path the line computes ("$1" in a shell given {}), or a relative path in a
    found file's folder (-execdir). It is judged as each such find's start paths, or the directory
    find runs in when it names none, as -delete is (write_targets): every path find hands the
    command is under them. A start path the guard cannot place passes from a worktree, as any
    target does (shell_writes), unless a ledger key is unread (ledger_unread). An ask waits for
    every start path's refusal (Asked)."""
    asked = Asked()
    for f, inv in find_runs(cmd):
        for w in _find_paths(inv.args)[0] or [here(inv.word)]:
            if not w.dyn and BASH_DEVICE_RE.match(w.text):
                continue
            target, why = write_place(ctx, f, w)
            if target is None and f.cwd is not None and ctx.place(f.cwd).kind not in (repo.MAIN, repo.UNKNOWN):
                d = asked.firm(ledger_unread(ctx, role, '`%s` runs `%s` on what it finds in %s'
                                             % (bare(f), bare(cmd), w.text)))
                if d:
                    return d                    # a subagent's refusal
                continue
            what = '`%s` runs `%s` on what it finds in %s' % (
                bare(f), bare(cmd), w.text if w.text != '.' else 'the directory it runs in')
            stand_in = (w.text == '.' and f.source[w.start:w.end] != '.') or w.start == w.end
            part = any((x.start, x.end) == (w.start, w.end) and x.text != w.text for x in f.words)
            swaps = execdir_swaps(f, cmd)
            if swaps:
                what += ' (with -exec in place of -execdir, find hands it the same files by their full paths)'
            rewrite = None if stand_in or part else \
                (lambda path, w=w, f=f, s=swaps: retarget(f, w, path, swaps=s))
            d = edit_target(event, ctx, target, cwd, role, what, why, rewrite=rewrite, run=bare(f),
                            shell=outer_shell(f))
            if target is None:
                d = unplaced(d, role) or ledger_unread(ctx, role, what)   # a warned refusal leaves the ask
            d = asked.firm(d)
            if d:
                return d
    return asked.first


def execdir_swaps(f, cmd):
    """[(word, '-exec' or '-ok')] for the one -execdir or -okdir of the find command `f` when it runs
    `cmd` and `cmd` takes only options and {}: -exec runs it on the same files, named by full path,
    which the guard can place; [] otherwise (a relative name the command writes would land
    elsewhere)."""
    dirs = [i for i, x in enumerate(f.words) if not x.dyn and x.text in ('-execdir', '-okdir')]
    words = [x.text for x in cmd.words]
    # find's {} is the one word computed at run time (the path it fills in)
    if len(dirs) != 1 or len(words) < 2 or any(x.dyn and x.text != '{}' for x in cmd.words):
        return []
    if not all(t == '{}' or t.startswith('-') for t in words[1:]) or '{}' not in words[1:]:
        return []
    i = dirs[0]
    x = f.words[i]
    # the words after it are the command find runs, as `cmd` spells them (a nested command has its own source)
    if x.start is None or [y.text for y in f.words[i + 1:i + 1 + len(words)]] != words:
        return []
    return [(x, x.text.replace('dir', ''))]


def ps_drive_jail(event, ctx, cwd, role, what, why, rewrite, net=False, here=None):
    """An agent session's PowerShell write to a computed target ($p, a ( ) expression: _write_from),
    or any shell's write of a path read from a directory the guard cannot tell (after `cd $x`),
    by a role main_checkout lets through (it may edit a main checkout): the jail cannot judge a
    place it cannot tell, so it refuses, as git_jail_unknown does for git. With `net`, the path a
    .NET call writes: refused under write-jail, not unplaced (whose default mode only warns), as
    net_process_dir refuses a literal relative one, since .NET may read it against the process's
    directory (a docs-writer's `cd <worktree>; $p='docs\\guard.md'; ...;
    [IO.File]::WriteAllText($p,$t)` wrote the main checkout under a warning). Its alternative
    aims at `here`, the PowerShell location, when that is a linked worktree the session may write
    (the session's own folder may be the main checkout it cd'd out of)."""
    if not agent_session(event, ctx):
        return None
    rule = 'write-jail' if net else UNPLACED
    rs = jail_roots(event, ctx, cwd)
    dest = jail.destination(rs)
    there = dest.path if dest is not None else None
    if net and here is not None and ctx.place(here).kind == repo.LINKED and jail.judge(here, rs)[0] is None:
        there = here
    head = '%s: the guard cannot tell where it writes (%s). Write to a literal path inside the folders an ' \
           'agent session may write%s.' % (what, why, '; an agent session gives .NET a literal absolute path'
                                          if net else '')
    if there is not None and rewrite is not None:
        return refuse(rule, role, head + ' The same write in %s:' % fwd(there), rewrite(fwd(there) + '/<file>'))
    return refuse(rule, role, head + ' To see where this session runs:', 'pwd')


DUPS_RE = re.compile(r'(?:[ \t]*[0-9]*[<>]&[0-9-]+)+')


def retarget(cmd, w, path, lead='', swaps=()):
    """The command `cmd` (from its program word, redirections kept) with the word `w` replaced
    by `path`, quoted so both shells read it literally (literal_word: a `::$DATA` stream name is
    not expanded), after `lead` (an option the word's span also covers, as -C'dir' does); each
    (word, text) of `swaps` replaces another word of the line as well."""
    start = cmd.words[0].start if cmd.words else w.start
    start = min(start, w.start)
    end = max(cmd.end, w.end)
    m = DUPS_RE.match(cmd.source, end)          # a dup (2>&1) has no word, so no span
    end = m.end() if m else end
    out, at = [], start
    for x0, x1, text in sorted([(w.start, w.end, lead + literal_word(path))] +
                               [(x.start, x.end, t) for x, t in swaps]):
        out.append(cmd.source[at:x0] + text)
        at = x1
    return (''.join(out) + cmd.source[at:end]).strip()


def attached_lead(cmd, w):
    """The option text the word holding the value Word `w` also spells before it (`of=`,
    `--output=`, `-d`, `-t`), or None when `w` is a word of its own or not such a value."""
    for x in cmd.words:
        if (x.start, x.end) != (w.start, w.end) or x.text == w.text:
            continue
        if w.dyn or x.dyn or len(x.text) <= len(w.text) or not x.text.endswith(w.text):
            return None
        lead = x.text[:-len(w.text)]
        return lead if ATTACHED_LEAD_RE.match(lead) else None
    return None


# an option an attached value follows: -d, -sSLo, --output=, of=
ATTACHED_LEAD_RE = re.compile(r'^(?:--?[A-Za-z][\w-]*=?|[A-Za-z]+=)$')


def word_rewrite(cmd, w):
    """rewrite(path) for the write target Word `w` of `cmd`: the word aimed at `path`, an attached
    value after its option (attached_lead), a file curl or ruby places under a folder (Under) by
    aiming that folder; None when no word of the line can be aimed."""
    if isinstance(w, Under):
        aim = word_rewrite(cmd, w.folder) if w.folder.start is not None and not w.folder.dyn else None
        if aim is None:
            return None
        name = fwd(w.name.text)

        def under(path):
            p = fwd(path)
            # the folder that keeps the name as written; a destination named by its last part only: its folder
            p = p[:-len(name) - 1] if p.endswith('/' + name) else p.rsplit('/', 1)[0]
            return aim(p)
        return under
    if not any((x.start, x.end) == (w.start, w.end) and x.text != w.text for x in cmd.words):
        return lambda path: retarget(cmd, w, path)
    lead = attached_lead(cmd, w)
    if lead is None:
        return None
    return lambda path: retarget(cmd, w, path, lead)
