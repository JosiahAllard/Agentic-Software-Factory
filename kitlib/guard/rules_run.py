"""The guard's rules on how a command runs (kitlib.guard.rules, which re-exports each name here):
a kit gate or lock run piped into another command, a recursive search from a drive or profile root,
a heavy program run with no machine lock, a foreground sleep loop with no time limit, and an agent
ending processes by name."""
import ntpath
import os
import re

from kitlib import config as kconfig
from kitlib.git import repo

from . import commands as cm
from . import paths
from . import shell as sh
from .rules_base import (PS_ALIASES, PS_COMMON_SWITCHES, PS_COMMON_VALUES, WIN_OPT_RE, advice_kit, agent_session,
                         bare, literal_word, lock_tool, ps_args, ps_native_unsure, quote, refuse, same_dir, tail_word)

# ---------------------------------------------------------------- wrapped gates (P16)

def bare_wrapper(ctx, cmd, inv, cwd):
    """(command, from_dir) the wrapped_gate alternative names: the call from its first word to its
    last (bare: no pipe, no redirection), the script by absolute path, and for a gate.py run with
    no -C and no subcommand ` -C <the directory it ran in>` after it, so it runs the same from any
    directory. The script is the kit's (tools/<name>.py of advice_kit) when it is the kit the guard
    runs from or no such file is there (a repository holds no copy of the kit's tools); a script
    that is there, such as the kit repository's own tools/gate.py, keeps its folder. from_dir is
    the directory the alternative must still run in (a lock run's program, or a gate run with a
    subcommand), else None. The kit command's run (`kit gate`, tools/kit.py gate) keeps its words
    as written up to the tool's name, kit.py re-pathed as any script, and the -C goes after that
    name. (bare(cmd), from_dir) when the script word is not literal, or no python runs one (a
    gate.py run as a program). When the directory it runs in is unknown (a
    computed cd before it: cmd.cwd None) a relative script is kept as written, no -C is added and
    from_dir is None: wrapped_gate then says to run it from the worktree root."""
    where = cmd.cwd
    at = cm.tool_at(inv) if inv.via != 'start-process' else None
    found = cm.python_script(inv) if at else None
    word = inv.args[found[0]] if found else None
    name = inv.args[at[0]] if at and at[0] >= 0 else None      # the word that names the tool
    if name is None or name.dyn or name.synth or not cmd.words or name.end > len(cmd.source) or \
            word is not None and (word.dyn or word.synth):
        return bare(cmd), where
    rest = at[2]
    if rest and rest[0].synth and rest[0].start == name.start:  # the tool's name word holds more
        return bare(cmd), where
    tail = cmd.source[name.end:cmd.words[-1].end] if rest else ''
    if word is None:                                            # the kit command run as a program
        head = cmd.source[cmd.words[0].start:name.end]
    else:
        path = sh.resolve_path(word.text, where, cmd.shell, getattr(ctx, 'home', None))
        if not path:
            return bare(cmd), where
        kit_tools = paths.norm(os.path.join(ctx.kit, 'tools'), real=True)
        if paths.norm(os.path.dirname(path), real=True) == kit_tools or not os.path.isfile(path):
            path = os.path.join(advice_kit(ctx), 'tools', ntpath.basename(path.replace('/', '\\')))
        head = cmd.source[cmd.words[0].start:word.start] + literal_word(path) + cmd.source[word.end:name.end]
    from_dir = where
    if cm.wrapper(inv) == 'gate' and not (rest and not rest[0].text.startswith('-')):    # no subcommand
        given = None
        for k, a in enumerate(rest):
            if a.text == '-C':
                given = rest[k + 1] if k + 1 < len(rest) else a
                break
            if a.text.startswith('-C'):
                given = sh.Word(a.text[2:], a.dyn, a.start, a.end)
                break
        if given is None and where:
            head += ' -C %s' % literal_word(where)
            from_dir = None
        elif given is not None and not given.dyn and os.path.isabs(given.text):
            from_dir = None                     # -C <absolute>: it runs the same from anywhere
    return (head + tail).strip(), from_dir


def wrapped_gate(event, ctx, cmd, inv, cwd, role):
    kind = cm.wrapper(inv)
    if not kind or not cmd.piped_out or kind == 'lock-run' and lock_run_help(inv) \
            or kind == 'gate' and gate_runs_nothing(inv):
        return None
    instead, from_dir = bare_wrapper(ctx, cmd, inv, cwd)
    read = 'read the log gate.py names' if kind == 'gate' else 'read its output'
    where = ' with %s as the working directory' % quote(from_dir) if from_dir and not same_dir(from_dir, cwd) \
        else ' from the worktree root' if cmd.cwd is None else ''
    how = ('Run it bare, as a call of its own in the background%s (run_in_background: true; no pipe and no '
           'redirection), and when its completion notice comes, %s:' % (where, read))
    return refuse('wrapped-gate', role, '%s is piped into another command; a wrapped gate reads to the '
                  'classifier as something else and is refused (refusal pattern P16). %s' % (bare(cmd), how), instead)


def lock_run_help(inv):
    """True when `machine_lock.py run` `inv` has exactly one word after run, a literal --help or -h:
    it prints its usage and runs nothing, so a pipe changes nothing the lock sees."""
    found = cm.tool_at(inv)
    if not found:
        return False
    rest = found[2][1:]
    return len(rest) == 1 and not rest[0].dyn and rest[0].text in ('--help', '-h')


GATE_NOTHING = ('-h', '--help', '--plan', '--measure')


def gate_runs_nothing(inv):
    """True when gate.py `inv` names no subcommand (its first word is a literal option: gate.py
    reads land and wait-result only there) and has a literal -h, --help, --plan or --measure: it
    runs no gate step and takes no lock, so a pipe changes nothing (docs/guard-v2.md "Kit tools
    may be piped")."""
    found = cm.tool_at(inv)
    if not found:
        return False
    rest = found[2]
    if not rest or rest[0].dyn or rest[0].synth or not rest[0].text.startswith('-'):
        return False
    return any(not a.dyn and not a.synth and a.text in GATE_NOTHING for a in rest)


# ---------------------------------------------------------------- root searches

# A whole-profile or whole-drive spelling that names a root whatever its value (it may be dynamic)
_ROOT_NAMES = r'(?:HOME|USERPROFILE|HOMEDRIVE|SystemDrive|HOMEPATH)'
_ROOT_VAR = (r'(?:~|\$%s|\$\{%s\}|\$env:%s|\$\{env:%s\}|%%%s%%)' %
             (_ROOT_NAMES, _ROOT_NAMES, _ROOT_NAMES, _ROOT_NAMES, _ROOT_NAMES))
ROOT_VAR_RE = re.compile(r'(?i)^%s{1,2}[\\/]?$' % _ROOT_VAR)       # $env:HOMEDRIVE$env:HOMEPATH too
WILD = re.compile(r'[*?\[]')


def is_root(text, cwd, ctx=None, shell=None):
    """True when `text` names a drive or filesystem root, the user's home, or a variable that is
    one of those; `shell` the shell whose word it is (sh.resolve_path: FileSystem::<drive root> in PS).
    `~` is ctx.home; with no ctx, or one without a home, sh.home_folder's."""
    t = text.strip()
    if not t:
        return False
    if ROOT_VAR_RE.match(t):
        return True
    p = sh.resolve_path(t, cwd, shell, getattr(ctx, 'home', None))
    if not p:
        return False
    if ctx is not None and getattr(ctx, 'home', None) and same_dir(p, ctx.home):
        return True
    if paths.posix():
        return p in ('/', '//')                 # Linux: no drives; `/` (and `//`) is the root
    drive, rest = os.path.splitdrive(p)
    return bool(drive) and rest in ('', '\\', '/')


def split_wild(text):
    """(parent, leaf) when the last part of `text` is a wildcard (<drive>:\\*.cs), else (text, None)."""
    m = re.match(r'^(.*[\\/])([^\\/]*)$', text)
    if m and WILD.search(m.group(2)):
        return m.group(1), m.group(2)
    if not m and WILD.search(text) and not re.match(r'^[A-Za-z]:', text):
        return '.', text
    return text, None


def find_paths(args):
    k, out = 0, []
    while k < len(args) and args[k].text in ('-H', '-L', '-P') or (k < len(args) and args[k].text.startswith('-O')):
        k += 1
    while k < len(args):
        t = args[k].text
        if t.startswith('-') or t in ('(', '!', '\\(', ')'):
            break
        out.append(args[k])
        k += 1
    return out, True


def search_paths(cmd, inv):
    """(path Words, default-to-cwd) a recursive search walks, or None when it is no recursive
    search."""
    if inv is None:
        return None
    name, args = inv.name, inv.args
    if cmd.shell == sh.PS and PS_ALIASES.get(name, name) == 'get-childitem' and \
            not inv.path.lower().endswith('.exe'):
        bound = ps_args(cmd, args, 'get-childitem')
        if 'recurse' not in bound and 'depth' not in bound:
            return None
        return (bound.get('path') or bound.get('literalpath') or []), True
    if cmd.shell != sh.PS and name == 'find':
        return find_paths(args)
    if cmd.shell == sh.CMD and name == 'dir' or (cmd.shell != sh.PS and name == 'dir' and
                                                 any(w.text.lower() == '/s' for w in args)):
        if not any(w.text.lower().startswith('/s') for w in args):
            return None
        return [w for w in args if not w.text.startswith('/')], True
    if cmd.shell == sh.BASH and name in ('ls', 'dir'):
        if not any(re.match(r'^-[A-Za-z]*R', w.text) or w.text == '--recursive' for w in args):
            return None
        return [w for w in args if not w.text.startswith('-')], True
    if name == 'where' and (cmd.shell != sh.PS or inv.path.lower().endswith('.exe')):
        for k, w in enumerate(args[:-1]):
            if w.text.lower() == '/r':
                return [args[k + 1]], False
        return None
    if name == 'tree' and (cmd.shell != sh.PS or not PS_ALIASES.get(name)):
        opt = (lambda t: t.startswith('-')) if cmd.shell == sh.BASH else (lambda t: bool(WIN_OPT_RE.match(t)))
        return [w for w in args if not opt(w.text)], True
    if name == 'findstr':
        if not any(w.text.lower() == '/s' for w in args):
            return None
        return [w for w in args if not w.text.startswith('/')][1:], True
    if name == 'du' and cmd.shell != sh.PS:
        return [w for w in args if not w.text.startswith('-')], True
    if name in ('grep', 'egrep', 'fgrep', 'rg'):
        if name != 'rg' and not any(re.match(r'^-[A-Za-z]*[rR]', w.text) or
                                    w.text in ('--recursive', '--dereference-recursive') for w in args):
            return None
        pos = [w for w in args if not w.text.startswith('-')]
        if name == 'rg' and any(w.text == '--files' for w in args):
            return pos, True                    # rg --files: every positional is a path
        has_pattern_opt = any(w.text in ('-e', '-f', '--regexp', '--file') for w in args)
        return (pos if has_pattern_opt else pos[1:]), True
    return None


def root_search(event, ctx, cmd, inv, cwd, role):
    found = search_paths(cmd, inv)
    if not found:
        return None
    paths, default_cwd = found
    where = cmd.cwd
    for w in paths:
        parent, leaf = split_wild(w.text)
        if ROOT_VAR_RE.match(parent.strip()) or (not w.dyn and is_root(parent, where, ctx, cmd.shell)):
            what = '`%s` searches the whole drive or profile from %s.' % (cmd.text, w.text)
            if cwd and not is_root(cwd, None, ctx):
                new = quote(cwd + '/' + leaf) if leaf else quote(cwd)
                fixed = cmd.source[cmd.start:w.start] + new + cmd.source[w.end:cmd.end]
                return refuse('root-search', role, '%s Search the project folder instead (the Glob or Grep tool '
                              'with path %s does the same):' % (what, quote(cwd)), fixed)
            return refuse('root-search', role, what + ' ' + GLOB_HINT, GLOB_ANY)
    if not paths and default_cwd and where and is_root(where, None, ctx):
        return refuse('root-search', role, '`%s` runs from the root %s and searches all of it. %s' %
                      (cmd.text, where, GLOB_HINT), GLOB_ANY)
    return None


GLOB_HINT = 'Search inside a project folder, with the Glob or Grep tool:'
GLOB_ANY = 'Glob(pattern="**/*", path="<a project folder>")'


# ---------------------------------------------------------------- heavy programs

def plain_run(cmd, inv):
    """The command line that runs `inv`, written so it reads plainly: from the program word on (no
    env prefixes, no keyword, no & call operator, no start or Start-Process wrapper), with any
    word the shell rewrites (a brace expansion, an ANSI-C escape, a backslash bash drops) written
    as the shell passes it."""
    if inv.via == 'start-process':
        return ' '.join([inv.path] + [w.text for w in inv.args])
    words = cmd.words[inv.index:]
    if not words:
        return inv.path
    if any(w.synth for w in words) or inv.path != words[0].text:
        return ' '.join([inv.path] + [w.text for w in words[1:]])
    return cmd.source[words[0].start:words[-1].end]


def lock_alternative(ctx, cmd, entry, cwd, run=None):
    """The machine_lock run line for a heavy command; `run` the command text (plain_run's by
    default). A command whose directory differs from the call's says where to run it."""
    where = cmd.cwd or cwd
    cfg = ctx.config(where) if where else None
    top = ctx.main_top(where) if where else None
    name = (cfg.repo.get('name') if cfg is not None else '') or (os.path.basename(top) if top else '<repo>')
    place = ctx.place(where) if where else None
    slug = os.path.basename(place.top) if place is not None and place.kind == repo.LINKED else 'main'
    if run is None:
        inv = cm.invocation(cmd)
        run = plain_run(cmd, inv) if inv is not None else bare(cmd)
    return 'python %s run --kind %s --repo %s --slug %s -- %s' % (
        lock_tool(ctx), entry['kind'], name, slug, run)


def run_from(cmd, cwd):
    """The sentence that says where to run a command whose directory differs from the call's."""
    if cmd.cwd and cwd and not same_dir(cmd.cwd, cwd):
        return ' Run it with %s as the working directory:' % quote(cmd.cwd)
    return ''


def kit_wrapper(ctx, cmd, inv, cwd, event=None):
    """cm.wrapper(inv) when the script it runs is the kit's own (tools/gate.py or
    tools/machine_lock.py of the kit the guard runs from, ctx.kit, or, given the Action `event`, of
    an export of it the session cannot write, rolepolicy.kit_export, the folder advice names
    (advice_kit) among them; the path resolved from the command's directory with links followed),
    else None. Only the kit's tool takes the machine lock itself, so only it may run an
    interpreter a repository lists as heavy: a script that shares its file name (a branch's copy,
    `python <temp>/gate.py`) is judged as any other script. A repository holds no copy of the
    kit's tools (decision 21 in docs/multi-host-plan.md section 7), so an opt-in stub fails closed
    here too."""
    kind = cm.wrapper(inv)
    if not kind:
        return None
    found = cm.python_script(inv)
    word = inv.args[found[0]] if found else inv.word
    return kind if kit_script(ctx, cmd, word, cwd, event, inv) else None


def kit_script(ctx, cmd, word, cwd, event=None, inv=None):
    """True when the program or script Word `word` is one of the kit's own tools: in the tools
    folder of the kit the guard runs from (ctx.kit) or, given the Action `event`, of an export of
    it the session cannot write (rolepolicy.kit_export), the path resolved, links followed, from
    the directory the program runs in: the command's, or for a Start-Process call `inv` with a
    -WorkingDirectory the folder that names (cm.run_dir), where only a path with a drive or a
    share is read when the guard cannot place that folder (a rooted \\x takes that folder's
    drive). The heavy program's repository (heavy) is still the command's."""
    if word is None or word.dyn:
        return False
    if inv is not None and inv.via == 'start-process' and inv.workdir is not None:
        base = cm.run_dir(inv)
    else:
        base = cmd.cwd or cwd
    path = sh.resolve_path(word.text, base, cmd.shell, getattr(ctx, 'home', None)) or (
        word.text if sh.is_absolute(word.text) else None)
    if not path:
        return False
    tools = paths.norm(os.path.join(ctx.kit, 'tools'), real=True)
    folder = paths.norm(os.path.dirname(path), real=True)
    if folder == tools:
        return True
    if event is None or os.path.basename(folder) != 'tools':
        return False
    from . import rolepolicy
    return bool(rolepolicy.kit_export(ctx, os.path.dirname(folder), event, cwd))


# a piece Windows PowerShell 5.1 may split out of an unsure word (ps_native_unsure) that python's
# argparse may read as a -C: a dash after a blank or a quote, then a dash (a long option, which
# argparse also takes abbreviated) or a run with a C in it (-C, a cluster -vC)
PS_SPLIT_C_RE = re.compile(r'(?:^|[\s"])-(?:-|\S*C)')


def kit_tool_dir(event, ctx, cmd, inv, cwd):
    """(Word, path) of the one literal absolute -C <dir> (or -C<dir>) a kit tool (kit_script) is
    given, which is the checkout it acts on, or None. Only for a python that PowerShell runs
    directly (not Start-Process), on no PowerShell line around it with a word it may hand on as
    other than one argument, and only when no such word comes before the -C value or, after it,
    may split into a word argparse reads as another -C (PS_SPLIT_C_RE); a second -C, or a --root,
    is none either."""
    if inv is None or inv.via is not None or not cm.PYTHON_RE.match(inv.name or ''):
        return None
    c = cmd.nested_in
    while c is not None:
        if c.shell == sh.PS and any(ps_native_unsure(w) for w in c.words):
            return None
        c = c.nested_in
    found = cm.python_script(inv)
    if not found or not kit_script(ctx, cmd, inv.args[found[0]], cwd, event):
        return None
    args = inv.args
    marks = [k for k, w in enumerate(args) if k > found[0] and
             (w.text.startswith('-C') or w.text.split('=')[0] in ('--r', '--ro', '--roo', '--root'))]
    if len(marks) != 1 or args[marks[0]].text.startswith('--'):
        return None
    k = marks[0]
    w = args[k]
    if w.text == '-C':
        if k + 1 >= len(args):
            return None
        value, last = args[k + 1], k + 1
    else:
        value, last = tail_word(w, w.text[2:]), k
    if value.dyn or not value.text or not (value.text.startswith(('/', '\\')) or
                                           ntpath.splitdrive(value.text)[0]):
        return None
    words = [inv.word] + list(args) if inv.word is not None else list(args)
    cut = len(words) - (len(args) - last)
    if cmd.shell == sh.PS:
        if any(ps_native_unsure(x) for x in words[:cut + 1]):
            return None
        after = words[cut + 1:]
        first = next((i for i, x in enumerate(after) if ps_native_unsure(x)), None)
        if first is not None and any(PS_SPLIT_C_RE.search(x.text) for x in after[first:]):
            return None
    path = sh.resolve_path(value.text, cmd.cwd or cwd, cmd.shell, getattr(ctx, 'home', None))
    return (value, path) if path else None


def heavy(event, ctx, cmd, inv, cwd, role):
    if inv is None or inv.dyn or kit_wrapper(ctx, cmd, inv, cwd, event):
        return None
    where = cmd.cwd or cwd
    cfg = ctx.config(where) if where else None
    entry = kconfig.heavy_match(inv.path, inv.argv, kconfig.heavy_programs(cfg))
    if not entry:
        return None
    held, why = ctx.lock_held()
    if held:
        return None
    return refuse('heavy', role, '`%s` runs %s, a heavy program (kind %s: %s), and this session holds no '
                  'machine lock (%s).%s' % (bare(cmd), inv.name, entry['kind'], entry.get('why', ''), why,
                                            run_from(cmd, cwd)),
                  lock_alternative(ctx, cmd, entry, cwd, plain_run(cmd, inv)))


# ---------------------------------------------------------------- busy-wait loops

SLEEPERS = ('sleep', 'start-sleep')
THREAD_SLEEP_RE = re.compile(r'(?i)Thread\]::Sleep')
# A loop that counts or watches a clock is bounded: a numeric comparison, bash arithmetic, the
# $SECONDS variable (case-sensitive: -Seconds is a Start-Sleep parameter), a stopwatch's .Elapsed.
# -eq and -ne are left out: they are status checks too.
BOUND_RE = re.compile(r'(?i:(?:^|[\s(])-(?:lt|le|gt|ge)(?=[\s)]|$))|\(\(|\$\{?SECONDS\b|(?i:\.Elapsed\b)')
BASH_LEAD_SKIP = ('do', 'then', 'else', 'elif', '{', '!')
BUSY_MINUTES = 10                                # the time limit an alternative gives
BUSY_WHY = ('waits in a sleep loop with no time limit, in the foreground: if what it waits for never '
            'comes, the call never returns, and ending a stuck process by hand is refused as interfering '
            'with workloads. Wait in the background instead (run_in_background: true, so the completion '
            'notice wakes you, with a command that exits when the condition holds; or the Monitor tool), '
            'or give the loop a time limit:')


def _first(cmd):
    """The command's first word as lower-case text ('' when quoted or none)."""
    w = cmd.words[0] if cmd.words else None
    return '' if w is None or cm.quoted(cmd, w) else w.text.lower()


def _lead(cmd):
    """The command's first word that is not a bash keyword prefix (do, then, else, elif, {, !), as
    lower-case text ('' when quoted or none)."""
    for w in cmd.words:
        if cm.quoted(cmd, w):
            return ''
        if w.text not in BASH_LEAD_SKIP:
            return w.text.lower()
    return ''


def _sleeps(cmd):
    """True when the command pauses: sleep, Start-Sleep, timeout.exe /t, [Threading.Thread]::Sleep."""
    inv = cm.invocation(cmd)
    if inv is not None and not inv.dyn:
        if inv.name in SLEEPERS:
            return True
        if inv.name == 'timeout' and any(a.text.lower() in ('/t', '//t') for a in inv.args):
            return True
    return bool(THREAD_SLEEP_RE.search(cmd.text or ''))


def _under_timeout(cmd):
    """True when a command this one is nested in runs it under `timeout`."""
    outer = cmd.nested_in
    while outer is not None:
        inv = cm.invocation(outer)
        stop = inv.index if inv is not None else len(outer.words)
        if any(not w.dyn and cm.base_name(w.text) == 'timeout' for w in outer.words[:stop]):
            return True
        outer = outer.nested_in
    return False


def _nested_under(c, ids):
    outer = c.nested_in
    while outer is not None:
        if id(outer) in ids:
            return True
        outer = outer.nested_in
    return False


def _segments(parsed):
    """The parse's commands grouped by the text they were read from (the call's line, or the text
    a nested shell runs), each list in order."""
    out, order = {}, []
    for c in parsed.commands:
        key = id(c.nested_in) if c.nested_in is not None else None
        if key not in out:
            out[key] = []
            order.append(key)
        out[key].append(c)
    return [out[k] for k in order]


def _bash_loops(cmds):
    """[(first, last)] index pairs of the while and until loops in a bash segment."""
    loops, stack = [], []
    for i, c in enumerate(cmds):
        lead = _lead(c)
        if lead in ('while', 'until', 'for', 'select'):
            stack.append((lead, i))
        elif _first(c) == 'done' and stack:
            kind, start = stack.pop()
            if kind in ('while', 'until'):
                loops.append((start, i))
    return loops


def _extends(groups, base):
    return len(groups) > len(base) and tuple(groups[:len(base)]) == tuple(base)


def _ps_loops(cmds):
    """[(first, last)] index pairs of the while and do loops in a PowerShell segment."""
    loops, tails = [], set()
    for i, c in enumerate(cmds):
        kind = _first(c)
        if i in tails or kind not in ('while', 'do') or len(c.words) != 1:
            continue
        base, j = c.groups, i + 1
        while j < len(cmds) and _extends(cmds[j].groups, base):
            j += 1
        if kind == 'do' and j < len(cmds) and _first(cmds[j]) in ('until', 'while') and \
                tuple(cmds[j].groups) == tuple(base) and cmds[j].prev_op == '}':
            tails.add(j)
            j += 1
            while j < len(cmds) and _extends(cmds[j].groups, base):
                j += 1
        if j - 1 > i:
            loops.append((i, j - 1))
    return loops


def _match_bracket(text, k):
    """The index after the bracket group that opens at text[k], skipping quoted text, or None."""
    pairs = {'(': ')', '{': '}', '[': ']'}
    stack, quote_ch = [], None
    i = k
    while i < len(text):
        ch = text[i]
        if quote_ch:
            if ch == '`' and quote_ch == '"':
                i += 2
                continue
            if ch == quote_ch:
                quote_ch = None
        elif ch in ('"', "'"):
            quote_ch = ch
        elif ch in pairs:
            stack.append(pairs[ch])
        elif stack and ch == stack[-1]:
            stack.pop()
            if not stack:
                return i + 1
        i += 1
    return None


def _ps_loop_span(src, start, kind):
    """(end, body_open) of the PowerShell loop whose keyword starts at src[start], or None: `end` is
    the index after the loop, `body_open` the index of its body's '{'."""
    k = start + len(kind)
    parts = ('(', '{') if kind == 'while' else ('{', None, '(')
    body_open = None
    for want in parts:
        while k < len(src) and src[k] in ' \t\r\n':
            k += 1
        if want is None:                         # do { } until|while ( )
            m = re.match(r'(?i)(?:until|while)\b', src[k:])
            if not m:
                return None
            k += m.end()
            continue
        if k >= len(src) or src[k] != want:
            return None
        if want == '{':
            body_open = k
        k = _match_bracket(src, k)
        if k is None:
            return None
    return k, body_open


def _busy_loop(parsed, cmds, first, last):
    """(loop text, alternative) when cmds[first..last] is a sleep loop nothing bounds, else None."""
    inner = cmds[first:last + 1]
    ids = {id(c) for c in inner}
    nested = [c for c in parsed.commands if _nested_under(c, ids)]
    if not any(_sleeps(c) for c in inner + nested) or _under_timeout(cmds[first]):
        return None
    head = cmds[first]
    src = head.source
    if head.shell == sh.PS:
        span = _ps_loop_span(src, head.start, _first(head))
        if span is None:
            return None                          # a loop the guard cannot read is left alone
        end, body_open = span
        loop = src[head.start:end]
        if BOUND_RE.search(loop):
            return None
        cut = body_open + 1 - head.start
        bounded = '$deadline = (Get-Date).AddMinutes(%d); %s if ((Get-Date) -gt $deadline) { break }; %s' % (
            BUSY_MINUTES, loop[:cut], loop[cut:].lstrip())
        return loop, _in_outer(head, src[:head.start] + bounded + src[end:], bounded)
    words = [w for w in head.words if w.text not in BASH_LEAD_SKIP]
    start = words[0].start if words else head.start
    done = cmds[last]
    done_end = done.words[0].end if done.words else done.end
    loop = src[start:done_end]
    if BOUND_RE.search(loop):
        return None
    tested = [w for w in words[1:] if not cm.ASSIGN_RE.match(w.text)]       # while IFS= read: the assignment is no program
    if tested and cm.base_name(tested[0].text) == 'read':
        return None                              # while read: it reads lines, it waits on nothing
    rest = src[done_end:done.end] + DUP_RE.match(src, done.end).group(0)   # the loop's redirects
    do = _bash_loop_do(cmds, first, last)
    if do is None:
        return loop, "timeout %d bash -c '%s'%s" % (BUSY_MINUTES * 60, loop.replace("'", "'\\''"), rest.rstrip())
    cut = do.end - start
    bounded = 'SECONDS=0; %s [ $SECONDS -ge %d ] && break;%s%s' % (
        loop[:cut], BUSY_MINUTES * 60, loop[cut:] if loop[cut:cut + 1].isspace() else ' ' + loop[cut:],
        rest.rstrip())
    return loop, _in_outer(head, src[:start] + bounded + src[done_end + len(rest):], bounded)


def _bash_loop_do(cmds, first, last):
    """The `do` Word of the bash loop cmds[first..last] (not one of a loop nested in it), or None."""
    depth = 1
    for c in cmds[first + 1:last + 1]:
        w = c.words[0] if c.words else None
        if depth == 1 and w is not None and w.text == 'do' and not cm.quoted(c, w):
            return w
        if _lead(c) in ('while', 'until', 'for', 'select'):
            depth += 1
        elif _first(c) == 'done':
            depth -= 1
    return None


def _in_outer(cmd, text, alone):
    """`alone` (the bounded loop) when `cmd` is on the call's own line. When it is nested (bash -c
    '...', powershell -Command '...'), the outer command that runs `text` (the nested source with
    the loop bounded) instead: each outer command with the literal word that held the old text
    replaced by the new, single-quoted for its shell; `alone` when that word cannot be found."""
    if cmd.nested_in is None:
        return alone
    c = cmd
    while c.nested_in is not None:
        outer = c.nested_in
        hits = [w for w in outer.words if not w.dyn and w.text == c.source]
        if len(hits) != 1 or outer.start is None:
            return alone
        w = hits[0]
        quoted = "'%s'" % (text.replace("'", "''") if outer.shell == sh.PS else text.replace("'", "'\\''"))
        src = outer.source
        text = src[outer.start:w.start] + quoted + src[w.end:outer.end]
        c = outer
    return text


DUP_RE = re.compile(r'(?:\s*\d*[<>]&\d+)*')       # fd duplications (2>&1) a command's span leaves out


def _backgrounded(cmd):
    """True when the command, or a command it is nested in, ends with & (runs in the background)."""
    c = cmd
    while c is not None:
        if c.next_op == '&':
            return True
        c = c.nested_in
    return False


def busy_wait(event, ctx, parsed, cwd, role):
    """A foreground while/until (bash) or while/do (PowerShell) loop that sleeps, with no counter,
    clock or `timeout` bounding it. A call the host runs in the background (Action.background),
    or a loop sent to the background with &, is left alone: neither holds the call."""
    if event is not None and event.background:
        return None
    for cmds in _segments(parsed):
        shell = cmds[0].shell
        loops = _ps_loops(cmds) if shell == sh.PS else _bash_loops(cmds) if shell == sh.BASH else []
        for first, last in loops:
            if _backgrounded(cmds[last]):
                continue                         # `done &`: it does not hold the call
            found = _busy_loop(parsed, cmds, first, last)
            if found:
                return refuse('busy-wait', role, '`%s` %s' % (found[0], BUSY_WHY), found[1])
    return None


# ---------------------------------------------------------------- kill by process name

PS_STOP = ('stop-process', 'spps')
PS_PROC_VALUES = ('-id', '-name', '-inputobject', '-module', '-computername') + \
    tuple('-' + p for p in PS_COMMON_VALUES)
PS_PROC_SWITCHES = ('-passthru', '-force', '-includeusername', '-fileversioninfo') + \
    tuple('-' + p for p in PS_COMMON_SWITCHES)
PS_PROC_ALIASES = {'-processname': '-name', '-pid': '-id', '-processid': '-id'}


def ps_proc_params(cmd, args):
    """({canonical parameter: value text}, [positional text]) of Stop-Process or Get-Process
    arguments. A word that is no known parameter counts as a switch."""
    names = PS_PROC_VALUES + PS_PROC_SWITCHES
    named, pos = {}, []
    k = 0
    while k < len(args):
        w = args[k]
        t = sh.ps_dashed(w.text)
        if t.startswith('-') and len(t) > 1 and not w.dyn and not cm.quoted(cmd, w):
            p = cm.canon_param(t, names, PS_PROC_ALIASES) or t.lower()
            k += 1
            if ':' in t:
                named[p] = t.split(':', 1)[1]
            elif p in PS_PROC_VALUES and k < len(args):
                named[p] = args[k].text
                k += 1
            else:
                named[p] = ''
            continue
        pos.append(w.text)
        k += 1
    return named, pos


# The options of pkill and killall that take a value (-P 1234, -u name, --signal TERM): the value
# is no process name.
KILL_VALUE_SHORT = {'pkill': 'dgGPstuUF', 'killall': 'suoynZ'}
KILL_VALUE_LONG = {
    'pkill': ('--delimiter', '--pgroup', '--group', '--parent', '--session', '--terminal', '--euid', '--uid',
              '--pidfile', '--ns', '--nslist', '--signal', '--env', '--cgroup'),
    'killall': ('--signal', '--user', '--older-than', '--younger-than', '--ns', '--context'),
}
SIGNAL_OPT_RE = re.compile(r'^-(?:\d+|(?:SIG)?[A-Z][A-Z0-9+-]+)$')    # -9, -HUP, -SIGTERM, -RTMIN+1


def _kill_patterns(name, args):
    """The process-name patterns of a pkill or killall call: its words that are no option and no
    option's value."""
    short, long_ = KILL_VALUE_SHORT[name], KILL_VALUE_LONG[name]
    out, k = [], 0
    while k < len(args):
        t = args[k].text
        k += 1
        if t == '--':
            out += [w.text for w in args[k:]]
            break
        if not t.startswith('-') or t == '-':
            out.append(t)
            continue
        if t.startswith('--'):
            if '=' not in t and t in long_:
                k += 1
            continue
        if SIGNAL_OPT_RE.match(t):
            continue
        body = t[1:]
        for i, ch in enumerate(body):
            if ch in short:                      # the first value letter takes the rest of the word,
                if i == len(body) - 1:           # or the next word when it is last (-P 1234, -fu name;
                    k += 1                       # -ubuilds holds its own value)
                break
    return out


def killed_name(cmd, inv):
    """The process name a command ends processes by, or None (by id, or no kill)."""
    name, args = inv.name, inv.args
    if name == 'taskkill':
        for k, w in enumerate(args[:-1]):
            t = w.text.lower()
            if t in ('/im', '//im', '-im'):
                return args[k + 1].text
            if t in ('/fi', '//fi', '-fi') and 'imagename' in args[k + 1].text.lower():
                return args[k + 1].text.split()[-1]
        return None
    if name in ('pkill', 'killall'):
        pos = _kill_patterns(name, args)
        return pos[0] if pos else None           # no pattern: by parent, group, session, user or terminal
    if cmd.shell != sh.PS or name not in PS_STOP + ('kill',):
        return None
    named, pos = ps_proc_params(cmd, args)
    if '-name' in named:
        return named['-name'] or '<name>'
    if '-id' in named or '-inputobject' in named or pos:
        return None                              # a positional binds to -Id
    src = cmd.pipe_from
    ginv = cm.invocation(src) if src is not None else None
    if ginv is None or ginv.name not in ('get-process', 'gps', 'ps'):
        return None
    gnamed, gpos = ps_proc_params(src, ginv.args)
    if '-id' in gnamed or '-inputobject' in gnamed:
        return None
    return gnamed.get('-name') or (gpos[0] if gpos else '') or '<name>'


def kill_by_name(event, ctx, cmd, inv, cwd, role):
    """An agent session (a subagent, or a session marked as one of guard_agent_marks) ending
    processes by name: that ends every match, other sessions' builds and gates among them."""
    if inv is None or inv.dyn or not agent_session(event, ctx):
        return None
    target = killed_name(cmd, inv)
    if target is None:
        return None
    target = target.strip('"\'')
    if cmd.shell == sh.PS:
        look = 'tasklist /FI "IMAGENAME eq %s"' % target if inv.name == 'taskkill' else \
            'Get-Process -Name %s | Select-Object Id, StartTime, Path' % target
    elif os.name == 'nt':
        look = 'tasklist //FI "IMAGENAME eq %s"' % target
    else:
        look = 'pgrep -a %s' % target
    return refuse('kill-by-name', role, '`%s` ends every process named %s, other sessions\' builds and gates '
                  'among them. Find the one you started, check its start time is the one you saw, and stop that '
                  'one by its id. To list them:' % (bare(cmd), target), look)
