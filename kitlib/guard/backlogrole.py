"""The backlog roles in the guard (docs/guard.md "Backlog roles"; docs/backlog.md "Roles").

  backlog-role       a subagent's run of tools/backlog.py, judged by kitlib.backlog.roles for the
                     role its hook payload names (Action.agent_role), from the command's words
                     (kitlib.backlog.policy.read_argv). A PM_KIT_ROLE the line sets changes
                     nothing. A builder's own pull (roles.OWN_PULL) is read from the store the
                     run's folder opens, as it is locally, only when it alone would decide; an
                     --as branch is its own from that branch's linked worktree (own_pull_refusal).
                     A computed script word ending in /backlog.py is the tool
                     (rules.computed_script). A script file a subagent runs, or feeds a shell
                     on standard input, is read one level deep, its first SCRIPT_CAP bytes, a
                     path that is not a regular file refused (script_file).
  backlog-raw-write  a write of the backlog that skips the tool and its role check: a subagent's
                     python code importing kitlib.backlog (-c, standard input, -m, runpy) or naming
                     tools/backlog.py (a script file's: only where its code can run it, py_reach,
                     or what it prints may run as code, piped_script);
                     any session's but the owner's python code naming the hub's
                     record writer by its module name, not its file's path
                     (kitlib.hubsite.recordwrite, plan_answer: hub_writer_code);
                     any session's but the owner's git write of the store's ref
                     (refs/backlog/*; the backlog branch when the store is `branch`); any
                     session's but the owner's edit of a folder store's records.
  pm-data-audit      any session's but the owner's write in <pm_data>/audit/, the backlog's audit
                     log (kitlib.backlog.audit), which only tools/backlog.py appends to.

A main session's backlog.py run is the tool's own check (policy.check, by its session mark).
A host with no subagent identity (kitlib.host.hookmain.UNIDENTIFIED) gives a subagent its
session's mark, so the tool's own check judges it there too.
"""
import os
import re

from kitlib import agentname
from kitlib.git import repo

from . import commands as cm
from . import jail
from . import rules

ROLE_RULE = 'backlog-role'
RAW_RULE = 'backlog-raw-write'
AUDIT_RULE = 'pm-data-audit'
AUDIT_FOLDER = 'audit'                          # kitlib.backlog.audit.FOLDER
SCRIPT = 'backlog.py'
TOOL = 'backlog'                                # rules.kit_tool's name for it
UNIDENTIFIED = 'unidentified'                   # kitlib.host.hookmain.UNIDENTIFIED
ASK = ('Ask a session whose role may take it (your coordinator; a coordinator asks the PM) to run it, '
       'and say why. Every role may read the record:')
# python code that reaches the backlog's library or runs its tool: an import of kitlib.backlog
# in any spelling, a name of tools/backlog.py, or the kit command naming it (['kit', 'backlog', ...]).
# Other standard-input code and -c's other words are refused on it alone (backlog_code); a script
# file's text, the -c code word (c_code) and a heredoc or here-string python reads (stdin_code) are
# then read as python (py_reach), so a name they only print, hold in a comment or give as data passes.
CODE_RE = re.compile(r'kitlib\s*[./\\]\s*backlog\b|from\s+kitlib\s+import\s+[^\n;]*\bbacklog\b|'
                     r'\btools\s*[./\\]\s*backlog\b|\bbacklog\.py\b|\bkit(?:\.py|\.cmd|\.exe)?\W{1,4}backlog\b')
# python code that names the hub's record writer or the store's answer filing: hubsite then
# recordwrite across a dot; any attribute .recordwrite (an alias: serve.recordwrite,
# h.recordwrite; a backslash line break allowed after the dot); `from kitlib.hubsite import`
# naming it, on one line, across backslash line breaks or inside its parentheses; or plan_answer
# (kitlib.backlog.plan). Only the hub runs them. The file's path (kitlib/hubsite/recordwrite.py,
# with either slash) is not read: code that reads the file writes nothing, and the file run as a
# script stops at its relative import (`from .store import`) before it writes.
HUB_WRITER_RE = re.compile(r'\bhubsite\s*\.\s*recordwrite\b|\.(?:\s|\\\n)*recordwrite\b|'
                           r'from\s+kitlib\s*\.\s*hubsite\s+import\s+'
                           r'(?:\([^)]*|[^\n;]*(?:\\\n[^\n;]*)*)\brecordwrite\b|\bplan_answer\b')
HUB_WRITER_MODULE = 'kitlib.hubsite.recordwrite'
HUB_FILE = 'questions file [<question id>]'     # tools/hub.py's agent verb that files bound answers
REF_DIR = 'refs/backlog/'                     # kitlib.backlog.store.STORE_REF's folder
BRANCH = 'backlog'                              # kitlib.backlog.store.BRANCH
REMOTE_BRANCH_RE = re.compile(r'^refs/remotes/[^/]+/%s$' % BRANCH)


def read_record(root, rid):
    """The record `rid` as the store the checkout `root` opens holds it locally (no fetch), or
    None: the tool's own opener, so a linked worktree whose .kit.json names no store reads its PM
    data worktree's live store (kitlib.backlog.store.worktree_live_store). The guard's one read of
    the store (patched in most tests)."""
    from kitlib.backlog import opener
    return opener.open_store(root, fetch=False).store.load().get(rid)


def shell(event, ctx, cmd, inv, cwd, role):
    """The rules.shell_command rule: role_run, raw_code, store_ref_write, then script_file and
    piped_script."""
    return runs(event, ctx, cmd, inv, cwd, role) or script_file(event, ctx, cmd, inv, cwd, role) or \
        piped_script(event, ctx, cmd, inv, cwd, role)


def runs(event, ctx, cmd, inv, cwd, role):
    """shell's rules on the command itself, without reading a script file it runs."""
    return role_run(event, ctx, cmd, inv, cwd, role) or raw_code(event, ctx, cmd, inv, role) or \
        store_ref_write(event, ctx, cmd, inv, cwd, role)


def _judged_subagent(event):
    return rules.subagent(event) and getattr(event, 'agent', None) != UNIDENTIFIED


# ---------------------------------------------------------------- a run of the tool

def py_module(inv):
    """The module python's -m runs, as written (`python -m runpy <module>`: that module), or None
    for a script, -c, standard input or a module the text does not show."""
    if inv is None or inv.dyn or not rules.PY_RUN_RE.match(inv.name or ''):
        return None
    args = list(inv.args)
    k = 0
    while k < len(args):
        t = args[k].text
        flag = rules.PY_FLAG_RE.match(t)
        if t == '-' or (flag and flag.group(1) == 'c'):
            return None
        if flag:
            if flag.group(2):
                mod, rest = flag.group(2), args[k + 1:]
            elif k + 1 < len(args) and not args[k + 1].dyn:
                mod, rest = args[k + 1].text, args[k + 2:]
            else:
                return None
            if mod == 'runpy' and rest and not rest[0].dyn and not rest[0].text.startswith('-'):
                return rest[0].text
            return mod
        if not t.startswith('-'):
            return None
        k += 2 if t in cm.PY_VALUE_OPTS else 1
    return None


def run_words(inv):
    """The Words after tools/backlog.py when `inv` runs it (rules.tool_run: the script, -m
    tools.backlog; runpy's module read as the script; the kit command's `kit backlog`:
    rules.script_run; a computed script word ending in /backlog.py: rules.computed_script), else
    None."""
    run = rules.script_run(inv) or rules.computed_script(inv)
    if run is None or run[0] != SCRIPT:
        return None
    return run[1]


def argv_words(cmd, words):
    """read_argv's (text, readable) pairs: a computed word is unreadable, and so is a PowerShell
    word holding a double quote (Windows PowerShell 5.1 passes it on unescaped, so it can split).
    A bash word that is all one double-quoted string (`"$PWD"`, `"$M/x"`; not `"$@"` or
    `${a[@]}`, which split) is policy.ONE: one word, its text unknown.
    A Start-Process -ArgumentList string comes already split as Windows splits it
    (commands._split_list): a double-quoted segment is one word, spaces and all."""
    from kitlib.backlog import policy
    ps = cmd.shell == rules.sh.PS
    out = []
    for w in words:
        if not w.dyn:
            out.append((w.text, not (ps and '"' in w.text)))
        else:
            out.append((w.text, policy.ONE if _one_word(cmd, w) else False))
    return out


def _one_word(cmd, w):
    """True when the bash word `w` is written as one double-quoted string holding no `"` or `@`."""
    src = cmd.source or ''
    if cmd.shell != rules.sh.BASH or w.synth or w.start is None or w.end is None or \
            not 0 <= w.start < w.end <= len(src):
        return False
    return '"' not in w.text and '@' not in w.text and src[w.start:w.end] == '"%s"' % w.text


def _unread(pairs, argv, who):
    """The refusal's sentence naming the words read_argv could not read when they decided it, else
    '': no verb read, or the run without its unread words, as its own pull, would be allowed (an
    unread `-C "$PWD"` on a `close` the role may never take decides nothing)."""
    from kitlib.backlog import policy, roles
    words = [t for t, ok in pairs if ok is not True]
    if not words:
        return ''
    if argv.verb is not None:
        given = dict(argv.given)
        given.pop(policy.UNKNOWN, None)
        if policy.refusal(who, policy.sort(argv.verb, given, _Unheld()), (roles.OWN_PULL,)):
            return ''
    return ' The guard cannot read %s (computed, or a word that can split), so it judged the run ' \
           'as the strictest those words could make it; write them out.' \
           % ', '.join('`%s`' % t for t in words)


def role_run(event, ctx, cmd, inv, cwd, role):
    if not _judged_subagent(event):
        return None
    mod = py_module(inv)
    if mod and _backlog_module(mod):
        return None                             # raw_code's
    words = run_words(inv)
    if words is None:
        return None
    from kitlib.backlog import policy, roles
    pairs = argv_words(cmd, words)
    argv = policy.read_argv(pairs)
    if argv.help:
        return None
    who = roles.role_of(agentname.bare(getattr(event, 'agent_role', None) or ''), True)
    rows = policy.sort(argv.verb, argv.given)
    text = policy.refusal(who, rows)
    form, instead = '', None
    here = cm.run_dir(inv)
    if text and argv.rid and not argv.given.get(policy.UNKNOWN) and \
            policy.refusal(who, rows, (roles.OWN_PULL,)) is None:
        text, form, instead = own_pull_refusal(ctx, cmd, argv, who, here, pairs)
    if not text:
        return None
    # the rows were sorted as if the record were held (not read: the strictest); when the role
    # may not take the unheld row either, that row is the one to name
    text = policy.refusal(who, policy.sort(argv.verb, argv.given, _Unheld())) or text
    return rules.refuse(ROLE_RULE, role, '`%s`: %s (kitlib/backlog/roles.py).%s%s%s'
                        % (rules.bare(cmd), text, _unread(pairs, argv, who), form, '' if instead else ' ' + ASK),
                        instead or read_alternative(ctx, cmd, here, argv))


def _root(cmd, here, argv):
    """The folder the run names: its -C, read from `here`, the directory the tool runs in
    (commands.run_dir: the command's, or a Start-Process -WorkingDirectory), else `here` itself.
    None, fail closed, when -C is a word the guard cannot read (policy.UNREAD) or when the folder
    rests on a `here` the text cannot tell (run_dir's None: a cd it cannot read, a computed or
    unplaceable -WorkingDirectory); never the hook's directory in its place."""
    from kitlib.backlog import policy
    if argv.root is policy.UNREAD:
        return None
    return rules.sh.resolve_path(argv.root, here, cmd.shell) if argv.root is not None else here


def read_alternative(ctx, cmd, here, argv):
    """The tool's read of the record the run names (`show <ID>`; `list` when it names none), at
    the folder it runs on (_root: its -C, else the directory it runs in; <worktree> when unknown)."""
    root = _root(cmd, here, argv)
    where = rules.quote(root) if root else '<worktree>'
    what = 'show %s' % argv.rid if argv.rid and re.match(r'^[\w.-]+$', argv.rid) else 'list'
    return 'python %s -C %s %s' % (rules.kit_tool(ctx, TOOL), where, what)


class _Unheld(object):
    """A record read as neither held nor priced, for naming the row a refusal is about."""

    def get(self, key):
        return None

    def all(self, key):
        return []


def _branch_at(ctx, where):
    """The branch checked out at `where` (ctx.git.current_branch), or None."""
    try:
        return ctx.git.current_branch(where)[0] or None
    except Exception:                           # noqa: BLE001 - no branch: not its own
        return None


def _worktree_on(ctx, where, branch):
    """True when the directory `where` is in a linked worktree whose checked-out branch is
    `branch`, as written: the --as branch's own worktree. ctx.place reads the real path (a
    junction or symlink on the way goes to git) and answers the main checkout, and the PM data
    worktree, as MAIN, so neither is ever one."""
    if not where or not isinstance(branch, str) or not branch:
        return False
    place = ctx.place(where)
    if place.kind != repo.LINKED or not place.top:
        return False
    return _branch_at(ctx, place.top) == branch


def own_pull_refusal(ctx, cmd, argv, who, here=None, pairs=None):
    """(policy.refusal, the form sentence, the form or None) for a run only a builder's own pull
    would allow: the record read where the tool runs (_root: its -C folder, else `here`; none
    when unknown), through the store that folder opens (read_record, the tool's own opener: a
    linked worktree whose .kit.json names no store reads its PM data worktree's live store,
    kitlib.backlog.store.worktree_live_store), its pull compared with the branch the run acts as.
    That is the branch checked out at that folder, so `-C <worktree>` alone, from any directory,
    is a builder's own form; or an --as branch when `here`, the directory the tool runs in
    (commands.run_dir: as the line leaves it, or a Start-Process -WorkingDirectory; None, never
    evidence, when the text cannot tell), is that branch's linked worktree (_worktree_on). Any
    other --as is not this session's. The main checkout and the PM data worktree are never a
    linked worktree on the pulling branch, so a run from either is never its own. When the own
    pull alone would allow the run, the form sentence names the accepted way, else it is ''; the
    form is that way written out (own_form) when the guard sees the branch's worktree and the
    store it opens holds the record pulled by that branch, else None. Nothing own_form reads
    changes the decision."""
    from kitlib.backlog import policy, roles
    root = _root(cmd, here, argv)                # None: no record read, so never its own pull
    rec, branch = None, None
    if root:
        branch = _branch_at(ctx, root)
        if argv.branch is not None and argv.branch != branch and _worktree_on(ctx, here, argv.branch):
            branch = argv.branch
        if branch and argv.branch in (None, branch):
            try:
                rec = read_record(root, argv.rid)
            except Exception:                   # noqa: BLE001 - a store that does not read: not its own
                rec = None
    facts = (roles.OWN_PULL,) if rec is not None and policy.own_pull(rec, branch) else ()
    rows = policy.sort(argv.verb, argv.given, rec)
    text = policy.refusal(who, rows, facts)
    form, instead = '', None
    if text and policy.refusal(who, rows, (roles.OWN_PULL,)) is None:
        found = own_form(ctx, argv, who, root, here, pairs)
        if found is not None:
            b, wt, instead = found
            form = OWN_FOUND % (b, rules.fwd(wt))
        else:
            form = OWN_FORM % (rules.kit_tool(ctx, TOOL), argv.verb,
                               argv.rid if re.match(r'^[\w.-]+$', argv.rid) else '<ID>')
    return text, form, instead


def own_form(ctx, argv, who, root, here, pairs):
    """(branch, worktree, form) for a run its own pull would allow but that was refused: the
    branch the run acts as (its --as, else the branch checked out at its folder), that branch's
    linked worktree as the guard sees it (the folder the tool runs in when it is one on that
    branch, else the run's -C folder when that is one), and the run written again as
    `python <tool> -C <worktree> <verb> <words>`, with no --as and no cd: one bare call, which
    reads the store the worktree opens (read_record; kitlib.backlog.store.worktree_live_store) and
    acts as its branch. None when no such worktree shows, its store does not hold the record
    pulled by that branch (someone else's pull, or none), or the words do not read. It never
    names a branch the run does not act as, nor a worktree whose store it did not read the record
    in; the guard does not look a branch's worktree up elsewhere."""
    from kitlib.backlog import policy, roles
    b = argv.branch if argv.branch is not None else (_branch_at(ctx, root) if root else None)
    if not root or not isinstance(b, str) or not b:
        return None
    hp = ctx.place(here) if here else None
    if hp is not None and hp.kind == repo.LINKED and hp.top:
        wt = hp.top if _branch_at(ctx, hp.top) == b else None
    else:
        wt = ctx.place(root).top if _worktree_on(ctx, root, b) else None
    if not wt:
        return None
    try:
        rec = read_record(wt, argv.rid)
    except Exception:                           # noqa: BLE001 - a store that does not read: not there
        return None
    if not policy.own_pull(rec, b) or \
            policy.refusal(who, policy.sort(argv.verb, argv.given, rec), (roles.OWN_PULL,)) is not None:
        return None
    words = _rerun_words(pairs)
    if words is None:
        return None
    return b, wt, 'python %s -C %s %s' % (rules.kit_tool(ctx, TOOL), rules.quote(rules.fwd(wt)), words)


def _rerun_words(pairs):
    """The run's words from its verb on, after the global options it gives before the verb but -C
    and --as (own_form writes its own -C and needs no --as), each a word both shells read as written
    (rules.shell_word); None when a word does not read."""
    from kitlib.backlog import policy
    if not pairs or any(ok is not True for _t, ok in pairs):
        return None
    out, k = [], 0
    while k < len(pairs):
        text = pairs[k][0]
        name, eq, _value = text.partition('=')
        if text == '--':
            k += 1
            continue
        if not text.startswith('-'):
            break
        if name.startswith('-C') and not name.startswith('--') and len(name) > 2:
            k += 1
            continue
        if name not in policy.GLOBAL:
            return None
        take = 2 if policy.GLOBAL[name] and not eq else 1
        if name not in ('-C', '--as'):
            out.extend(t for t, _ok in pairs[k:k + take])
        k += take
    if k >= len(pairs):
        return None
    out.extend(t for t, _ok in pairs[k:])
    return ' '.join(rules.shell_word(t) for t in out)


# the form refusal sentence for a run its own pull would allow (own_pull_refusal)
OWN_FORM = (' It is your own pull only when the record is pulled by the branch checked out in the '
            'run\'s -C folder: run it as one call, `python %s -C <your worktree> %s %s ...`, with -C '
            'your branch\'s linked worktree, no --as and no cd (it reads the store your worktree '
            'opens); a worktree on another branch, the main checkout, the PM data worktree, or a '
            'branch that does not hold the pull, does not count.')
# the form refusal sentence when own_form found the worktree and the record in its store
OWN_FOUND = (' The record is pulled by %s: run it as one call with -C that branch\'s worktree %s, no '
             '--as and no cd:')


# ---------------------------------------------------------------- code that skips the tool

def _backlog_module(mod):
    return mod == 'kitlib.backlog' or mod.startswith('kitlib.backlog.')


def raw_code(event, ctx, cmd, inv, role):
    """A subagent's python that imports kitlib.backlog or runs tools/backlog.py as code: -m or
    runpy of a kitlib.backlog module, -c or standard-input code (rules.inline_code). Then, in any
    session's but the owner's, code that names the hub's record writer (hub_writer_code). A script
    file is not read."""
    return backlog_code(event, ctx, cmd, inv, role) or hub_writer_code(event, ctx, cmd, inv, role)


def hub_writer_code(event, ctx, cmd, inv, role):
    """Any session's but the owner's python that names kitlib.hubsite.recordwrite or plan_answer
    (HUB_WRITER_RE; -m or runpy of that module): the hub's own writes of a record, an answer the
    owner gave filed into it, which no agent session makes, main sessions included."""
    if rules.is_owner_session(event, ctx):
        return None
    if py_module(inv) == HUB_WRITER_MODULE:
        how = 'runs %s' % HUB_WRITER_MODULE
    else:
        how = None
        for text in rules.inline_code(cmd, inv):
            m = HUB_WRITER_RE.search(text)
            if m:
                how = 'runs code that names %s' % m.group(0)
                break
    if not how:
        return None
    return rules.refuse(RAW_RULE, role, '`%s` %s: the hub\'s record writer (kitlib/hubsite/recordwrite.py, the '
                        'store\'s plan_answer) files the owner\'s answers and record-page writes, and only the hub '
                        'runs it; no agent session does. Let the hub file a bound question\'s answer (any other '
                        'record write is tools/backlog.py\'s verb):'
                        % (rules.hub_line(rules.bare(cmd)), how),
                        'python %s %s' % (rules.kit_tool(ctx, 'hub'), HUB_FILE))


def backlog_code(event, ctx, cmd, inv, role):
    """A subagent's python that reaches the backlog's library (raw_code's first rule)."""
    if not _judged_subagent(event):
        return None
    mod = py_module(inv)
    if mod and _backlog_module(mod):
        how = 'runs %s' % mod
    else:
        how, read = None, [t for t in (c_code(inv), stdin_code(inv)) if t is not None]
        for text in rules.inline_code(cmd, inv):
            m = CODE_RE.search(text)
            if m and text in read:              # -c or heredoc code, read as python: a path held as data passes
                read.remove(text)               # once: the same text again is another argument
                why = py_reach(text)
                if why:
                    how = 'runs code that %s' % why
                    break
            elif m:
                how = 'runs code that names %s' % m.group(0)
                break
    if not how:
        return None
    return rules.refuse(RAW_RULE, role, '`%s` %s: code that reaches the backlog\'s library skips tools/backlog.py '
                        'and its role check, so it is a raw write (kitlib/backlog/roles.py). Run the tool\'s '
                        'verb:' % (rules.hub_line(rules.bare(cmd)), how),
                        'python %s -C <worktree> <verb> <arguments>' % rules.kit_tool(ctx, TOOL))


C_FLAGS_RE = re.compile(r'^-[bBdEhiIOPqsSuvx]*c$')     # -c, or flags that take no value then c, a word of its own


def c_code(inv):
    """The text of the code python runs from -c when it is the word after -c (C_FLAGS_RE), or
    None: no -c, the code attached to it (-cCODE), any launch other than plain or xargs
    (commands.Invocation.via: a PowerShell process-start call, which re-splits its words), or a
    word up to the code the line computes or the shell may hand python split (rules.native_unsure:
    a PowerShell word holding a double quote, cmd's quotes), so python may run other code than the
    guard reads. backlog_code reads the text with py_reach and judges None by CODE_RE alone over
    the line's code texts; piped_script (_printed_name) judges no -c code at all when it is None."""
    if inv is None or rules.PY_RUN_RE.match(inv.name or '') is None or inv.via not in (None, 'xargs'):
        return None
    args, k = inv.args, 0
    while k < len(args):
        t = args[k].text
        if t in cm.PY_VALUE_OPTS:
            k += 2
            continue
        if C_FLAGS_RE.match(t):
            if k + 1 >= len(args) or any(w.dyn or (inv.cmd is not None and rules.native_unsure(inv.cmd, w))
                                         for w in args[:k + 2]):
                return None
            return args[k + 1].text
        if t.startswith('-') and t not in ('-', '-m') and not re.match(r'^-[A-Za-z]*c', t):
            k += 1
            continue
        return None
    return None


def stdin_code(inv):
    """The text of the code python reads from standard input (reads_stdin: `python -` or flags
    alone) when the line's one heredoc or here-string gives it as the guard reads it, or None: a
    `<` file or a second such redirect, a launch other than plain, a here-string the line
    computes, or an unquoted heredoc body holding $, ` or \\ (the shell rewrites those: the
    text python reads is not the text as written). backlog_code reads it with py_reach, as
    c_code's; piped_script (_printed_name) too."""
    if inv is None or inv.dyn or inv.via is not None or reads_stdin(inv) != 'py':
        return None
    given = [r for r in inv.cmd.redirects if r.op in ('<', '<<', '<<<') and r.fd == '0']
    if len(given) != 1 or given[0].op == '<' or given[0].target is None:
        return None
    w = given[0].target
    if w.dyn and (given[0].op == '<<<' or any(c in w.text for c in '$`\\')):
        return None
    return w.text


# ---------------------------------------------------------------- a script file it runs

SCRIPT_CAP = 256 * 1024                         # the most of a script file the guard reads
SOURCERS = ('source', '.')
PY_EXTS = ('.py', '.pyw')
SH_EXTS = ('.sh', '.bash')


def script_file_word(inv):
    """(the Word naming the script file `inv` runs, the shell that reads it: shell.BASH, shell.PS
    or 'py'), or None: a POSIX shell's script operand, a PowerShell shell's .ps1 (-File),
    source or . of a file, python's script (rules.tool_run reads which word it is; -c, -m and
    standard input are None: stdin_files), or a .sh, .bash, .ps1 or .py file run by its path. A
    computed word is None."""
    run = rules.tool_run(inv)
    if run is None:
        return None
    base, rest = run
    name = (inv.name or '').lower()
    py = rules.PY_RUN_RE.match(name) is not None
    if py or name in cm.BASH_SHELLS or name in cm.PS_SHELLS:
        k = len(inv.args) - len(rest) - 1
        if k < 0 or inv.args[k].dyn or cm.base_name(inv.args[k].text) != base:
            return None                         # -m's module, the kit command's tool
        return inv.args[k], 'py' if py else rules.sh.PS if name in cm.PS_SHELLS else rules.sh.BASH
    if name in SOURCERS:
        if not inv.args or inv.args[0].dyn:
            return None
        return inv.args[0], inv.cmd.shell
    if inv.via or inv.word is None or inv.word.dyn:
        return None
    low = base.lower()
    kind = 'py' if low.endswith(PY_EXTS) else rules.sh.PS if low.endswith('.ps1') else \
        rules.sh.BASH if low.endswith(SH_EXTS) else None
    return (inv.word, kind) if kind else None


BASH_C_RE = re.compile(r'^-[A-Za-z]*c')        # bash -c, -xc: the code is a word, not standard input


PS_CODE_OPTS = ('-file', '-f', '-command', '-c', '-encodedcommand', '-enc', '-e', '-ec')
FILE_READERS = {'cat', 'get-content', 'gc', 'type'}     # a command whose output is the files it names
PS_PATH_OPTS = ('-path', '-literalpath', '-raw')


def reads_stdin(inv):
    """The kind (shell.BASH, shell.PS or 'py') a POSIX shell, PowerShell shell or python reads its
    code from standard input in, when it has no script, -c, -m or -Command text of its own (a
    PowerShell shell given `-` reads it); else None."""
    if inv is None or inv.dyn:
        return None
    name = (inv.name or '').lower()
    py = rules.PY_RUN_RE.match(name) is not None
    ps = name in cm.PS_SHELLS
    if not (py or ps or name in cm.BASH_SHELLS):
        return None
    if ps and any(w.text == '-' for w in inv.args):
        return rules.sh.PS
    for w in inv.args:
        t = w.text
        if w.dyn or not t.startswith('-'):
            return None
        if rules.PY_FLAG_RE.match(t) if py else t.lower() in PS_CODE_OPTS if ps else BASH_C_RE.match(t):
            return None
    return 'py' if py else rules.sh.PS if ps else rules.sh.BASH


def reader_files(cmd):
    """The literal files `cmd` writes out whole (cat F, Get-Content F, -Path F), or None: another
    program, an option it does not read, a computed word, or none named (it copies its own input)."""
    inv = cm.invocation(cmd)
    if inv is None or inv.dyn or (inv.name or '').lower() not in FILE_READERS:
        return None
    files, skip = [], False
    for w in inv.args:
        if w.dyn:
            return None
        low = w.text.lower()
        if skip:                                # -Encoding's value
            skip = False
        elif low == '-encoding':
            skip = True
        elif low in PS_PATH_OPTS:               # -Path F, -LiteralPath F: F is the next word; -Raw
            continue
        elif low.startswith('-'):
            return None                         # an option it does not read, or cat - (its input)
        else:
            files.append(w.text)
    return files or None


def stdin_files(inv):
    """(the files, the kind they are read in) a shell or python reading its code from standard
    input (reads_stdin) is given: a literal `<` file (`bash < x.sh`), or the files a command piped
    straight into it writes out (`cat x.sh | bash`, `Get-Content x.ps1 | powershell -`:
    reader_files); else None."""
    kind = reads_stdin(inv)
    if kind is None:
        return None
    for r in inv.cmd.redirects:
        if r.op == '<' and r.target is not None and not r.target.dyn:
            return [r.target.text], kind
    src = inv.cmd.pipe_from
    files = reader_files(src) if src is not None else None
    return (files, kind) if files else None


NOT_REGULAR = object()                          # _script_text: a FIFO, a device, standard input


def _script_text(cmd, text):
    """(the absolute path, its first SCRIPT_CAP bytes as text) of the script file `text` names
    from the command's directory; (path, None) when it does not read (missing, unplaceable);
    (path, NOT_REGULAR) for a path that is there but is not a regular file (a FIFO, a folder,
    /dev/stdin), which is never opened: what it gives is not a file the guard can read."""
    if text == '/dev/null' or text.lower() == 'nul':
        return text, ''                         # empty: it runs nothing
    if text in cm.SOURCED_STDIN or text.startswith(('/dev/', '/proc/')):
        return text, NOT_REGULAR
    path = rules.sh.resolve_path(text, cmd.cwd, shell=cmd.shell, home=cmd.home) if cmd.cwd or \
        os.path.isabs(text) else None
    if not path:
        return None, None
    if not os.path.isfile(path):
        return path, (NOT_REGULAR if os.path.exists(path) else None)
    try:
        with open(path, 'rb') as f:
            data = f.read(SCRIPT_CAP)
    except (OSError, ValueError):
        return path, None
    if data[:2] in (b'\xff\xfe', b'\xfe\xff'):
        return path, data.decode('utf-16', 'replace')
    return path, data.decode('utf-8-sig', 'replace')


KIT_CODE = ('tools', 'tests', 'kitlib', 'boot', 'hosts', 'githooks')   # the kit's code folders


def _kit_code(ctx, path):
    """True for a file in a code folder of the kit (KIT_CODE): of the kit folder the guard runs
    from, or of a kit checkout (a folder above it holds kit.json and kitlib/). The kit's own tools
    and tests import its library; a script saved anywhere else in a checkout is judged."""
    from . import paths
    here = paths.norm(path, absolute=True)

    def in_code(root):
        rel = os.path.relpath(here, root).replace('\\', '/').split('/')
        return len(rel) > 1 and rel[0] in KIT_CODE
    kit = getattr(ctx, 'kit', None)
    if kit:
        root = paths.norm(kit, absolute=True)
        if jail.inside(here, root):
            return in_code(root)
    d = os.path.dirname(here)
    while True:
        if os.path.isfile(os.path.join(d, 'kit.json')) and os.path.isdir(os.path.join(d, 'kitlib')):
            return in_code(d)
        up = os.path.dirname(d)
        if up == d:
            return False
        d = up


LITERAL_WHY = 'Run the tool\'s verb itself, its words literal, directly as a command of its own:'


def literal_form(ctx):
    """The alternative to a run the guard cannot judge: the tool itself, its words literal (the
    reason ends with LITERAL_WHY; the alternative is the bare command)."""
    return 'python %s -C <worktree> <verb> <arguments>' % rules.kit_tool(ctx, TOOL)


def script_file(event, ctx, cmd, inv, cwd, role):
    """A subagent's run of a script file (script_file_word), or of the files its standard input
    is given (stdin_files), read one level deep, each one's first SCRIPT_CAP bytes, and judged by
    judge_text; a path that is there but is not a regular file (a FIFO, /dev/stdin) is refused, as
    the guard cannot read it. A missing file is not judged."""
    if not _judged_subagent(event):
        return None
    found = script_file_word(inv)
    names, kind = ([found[0].text], found[1]) if found else stdin_files(inv) or (None, None)
    if not names:
        return None
    line = rules.hub_line(rules.bare(cmd) if found or cmd.pipe_from is None else
                          rules.bare(cmd.pipe_from) + ' | ' + rules.bare(cmd))
    if found and names[0] in cm.SOURCED_STDIN:
        # /dev/stdin: its text is the line's heredoc or here-string, or the files piped in
        here = next((r.target.text for r in cmd.redirects if r.op in ('<<', '<<<') and r.target is not None), None)
        if here is not None:
            return judge_text(event, ctx, cmd, cwd, role, kind, here, '`%s` runs its standard input' % line)
        files = reader_files(cmd.pipe_from) if cmd.pipe_from is not None else None
        if files:
            names = files
        elif cmd.pipe_from is not None and (inv.name or '').lower() in SOURCERS:
            return None                         # commands reads a sourced pipe (echo) or flags it unread
    read = []
    for name in names:
        path, text = _script_text(cmd, name)
        if text is NOT_REGULAR:
            return rules.refuse(ROLE_RULE, role, '`%s` runs %s, which is not a regular file, so the guard cannot '
                                'read it to judge a run of tools/backlog.py in it for your role '
                                '(kitlib/backlog/roles.py). %s' % (line, rules.fwd(path), LITERAL_WHY),
                                literal_form(ctx))
        if text is not None:
            read.append((path, text))
    for path, text in read:
        d = judge_text(event, ctx, cmd, cwd, role, kind, text, '`%s` runs %s' % (line, rules.fwd(path)), path)
        if d:
            return d
    return None


PIPE_RUNNERS = cm.BASH_SHELLS | cm.PS_SHELLS | {'cmd', 'eval', 'iex', 'invoke-expression'} | set(SOURCERS)


def _runs_piped_text(inv):
    """True when the program `inv` names takes the text piped into it as code or a command line:
    a shell, eval, source, iex or cmd (fallback.RUNNERS, whatever its arguments), python reading
    its code from standard input (reads_stdin), any program xargs runs (the text becomes its
    words), or a program the line does not name (fail closed)."""
    if inv is None:
        return False
    if inv.dyn or inv.via == 'xargs':
        return True
    return (inv.name or '').lower() in PIPE_RUNNERS or reads_stdin(inv) == 'py'


def piped_script(event, ctx, cmd, inv, cwd, role):
    """A subagent's python script, -c or heredoc code (c_code, stdin_code) whose output may run
    as code, judged by CODE_RE alone, not by py_reach's reading of its own code: piped, straight
    or through other commands, into a runner (_runs_piped_text: `python gen.py | bash`,
    `... | tee log | python`), or held in another command's word (`eval "$(python gen.py)"`, `bash <(python gen.py)`, a
    nested shell's text: shell.Command.nested_in), whose program then reads what it prints, or run
    in a ( ) or { } group, whose output the guard does not follow (a pipe from a group has no
    pipe_from). The kit's own (_kit_code) is not judged."""
    if not _judged_subagent(event):
        return None
    if cmd.nested_in is not None or cmd.groups:
        how = 'and what it prints is a word of `%s`' % rules.hub_line(rules.bare(cmd.nested_in)) \
            if cmd.nested_in is not None else 'in a group, whose output the guard does not follow'
        d = _printed_name(ctx, role, cmd, [cmd], how)
        if d:
            return d
    if cmd.pipe_from is None or not _runs_piped_text(inv):
        return None
    chain, up = [cmd], cmd.pipe_from
    while up is not None:
        chain.insert(0, up)
        d = _printed_name(ctx, role, up, chain, 'and pipes what it prints into a command that runs it as code')
        if d:
            return d
        up = up.pipe_from
    return None


def _printed_name(ctx, role, cmd, chain, how):
    """piped_script's refusal when `cmd` runs a python script file, or -c or heredoc code (c_code,
    stdin_code, which backlog_code reads with py_reach), whose text CODE_RE matches (not the kit's
    own); else None."""
    ci = cm.invocation(cmd)
    found = script_file_word(ci) if ci is not None and not ci.dyn else None
    if found and found[1] == 'py':
        path, text = _script_text(cmd, found[0].text)
        what = rules.fwd(path) if path else found[0].text       # no path: a directory the guard cannot place
    else:
        path, text = None, c_code(ci) if ci is not None and not ci.dyn else None
        what = 'its -c code'
        if text is None:
            text, what = stdin_code(ci), 'its standard-input code'
    m = CODE_RE.search(text) if isinstance(text, str) else None
    if not m or (path and _kit_code(ctx, path)):
        return None
    line = rules.hub_line(' | '.join(rules.bare(c) for c in chain))
    return rules.refuse(RAW_RULE, role, '`%s` runs %s, whose code names %s, %s: a tools/backlog.py line printed '
                        'that way skips the guard\'s role check, so it is a raw write (kitlib/backlog/roles.py). '
                        '%s' % (line, what, m.group(0), how, LITERAL_WHY), literal_form(ctx))


# python a script file runs (py_reach): what can run code or a process, as python names it. An
# import naming one of PY_RUN_MODULES (any part of its dotted path, or a name it takes); a name or
# attribute in PY_RUN_ATTRS (os.system, from os import system, obj.__subclasses__); a bare name in
# PY_RUN_BUILTINS (exec(...), breakpoint(), not re.compile); a string that is one of these, whole
# or a dotted part of it (getattr(os, 'system'), sys.modules['subprocess'], resolve('os.system')).
# Wide on purpose: a script that names the tool and holds any of them is refused as before.
PY_RUN_MODULES = frozenset((
    'subprocess', 'runpy', 'importlib', 'imp', 'pkgutil', 'zipimport', 'pty', 'ctypes', 'cffi', '_winapi',
    '_posixsubprocess', 'multiprocessing', 'code', 'codeop', 'timeit', 'pdb', 'bdb', 'cProfile', 'profile',
    'trace', 'doctest', 'unittest', 'webbrowser', 'pydoc', 'pipes', 'tkinter', 'Tkinter', '_tkinter',
    'pickle', '_pickle', 'cPickle', 'shelve', 'marshal', 'builtins', '__builtin__', 'distutils',
    'setuptools', 'pexpect', 'plumbum', 'sh', 'commands', 'popen2'))
PY_RUN_ATTRS = frozenset((
    'system', 'popen', 'Popen', 'startfile', 'execl', 'execle', 'execlp', 'execlpe', 'execv', 'execve',
    'execvp', 'execvpe', 'spawnl', 'spawnle', 'spawnlp', 'spawnlpe', 'spawnv', 'spawnve', 'spawnvp',
    'spawnvpe', 'posix_spawn', 'posix_spawnp', 'create_subprocess_exec', 'create_subprocess_shell',
    'subprocess_exec', 'subprocess_shell', 'import_module', 'run_path', 'run_module', 'load_module',
    'exec_module', 'attrgetter', 'methodcaller', 'FunctionType', 'CodeType', 'dictConfig', 'fileConfig',
    '__import__', '__builtins__', '__dict__', '__subclasses__', '__globals__', '__code__',
    '__getattribute__', '__loader__', 'chdir'))
PY_RUN_BUILTINS = frozenset(('exec', 'eval', 'compile', 'execfile', 'getattr', 'globals', 'locals', 'vars',
                             'breakpoint'))
# an import that reaches the tool or the kit's library: a dotted part (or a name it takes) that is
# backlog (kitlib.backlog, tools.backlog, a bare `import backlog`) or kit (tools/kit.py); kitlib
# only when the code also uses backlog as code (kitlib.backlog.store, x.backlog) or holds a string
# that names the tool or the library other than as a kitlib/backlog path (_names_backlog:
# 'kitlib.backlog.store', 'tools/backlog.py', 'kit backlog', a dotted part 'backlog', which a kitlib
# helper may load or run): only a kitlib/backlog path given as data passes
PY_REACH_PARTS = frozenset(('backlog', 'kitlib', 'kit'))
PY_BACKLOG_PATH_RE = re.compile(r'^kitlib\s*[/\\]')     # a CODE_RE match that is a kitlib/backlog path
# a PEP 263 coding declaration; python reads it on the first two lines (the next one with -x)
PY_CODING_RE = re.compile(r'^[ \t\f]*#.*?coding[:=][ \t]*([-\w.]+)')
PY_PART_RE = re.compile(r'[.:]')               # a dotted name's parts, and pkgutil.resolve's 'mod:attr'


def py_reach(text):
    """How the python `text` a script file, -c code or heredoc runs reaches the backlog, as the
    refusal's words ('names backlog.py', 'imports kitlib.backlog.store', 'names backlog.py and can
    run it (subprocess)'), or None. CODE_RE finds a name first: none, None. The text is then read as
    python's own syntax tree, so a name that is only in a string or a comment passes unless the
    code can run something (PY_RUN_*), opens a file for writing (_file_write), or imports the tool
    or the kit's library (PY_REACH_PARTS). It
    is read twice: whole, and from its second line on (python -x skips the first line); a reading
    python -x cannot parse runs nothing. Fails closed to the name, as CODE_RE alone judged it,
    when the text does not parse, may be cut at SCRIPT_CAP, or declares a coding other than UTF-8
    (python would read its bytes as other text than the guard does)."""
    m = CODE_RE.search(text)
    if not m:
        return None
    named = 'names %s' % m.group(0)
    if len(text.encode('utf-8', 'surrogatepass')) >= SCRIPT_CAP - 3:
        return named
    cut = text.find('\n')                       # python -x skips up to the first \n only
    for head in (text, text[cut + 1:]) if cut >= 0 else (text,):
        for line in re.split(r'\r\n?|\n', head, maxsplit=3)[:3]:   # a lone \r ends a line too
            c = PY_CODING_RE.match(line)
            if c and c.group(1).lower().replace('_', '-') not in ('utf-8', 'utf8'):
                return named
    import ast
    import warnings
    for k, view in enumerate((text, text[cut + 1:] if cut >= 0 else '')):
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('ignore')     # a SyntaxWarning ("\q") is the script's, not the hook's output
                tree = ast.parse(view)
        except Exception:                       # noqa: BLE001 - SyntaxError, ValueError (a NUL), RecursionError
            if k == 0:
                return named
            continue                            # python -x cannot run it either
        why = _tree_reach(ast, tree)
        if why:
            return why if why.startswith('imports ') else '%s and %s' % (named, why) \
                if why.startswith('opens ') else '%s and can run it (%s)' % (named, why)
    return None


def _tree_reach(ast, tree):
    """py_reach's reading of one syntax tree: 'imports <module>' for an import that reaches the
    tool or the kit's library (PY_REACH_PARTS), else the first name that can run code or a process
    (PY_RUN_*), else the first call that opens a file for writing (_file_write), else None."""
    runner = kit_import = writer = None
    backlog_used = False                       # backlog as code (x.backlog) or named in a string (_names_backlog)
    for node in ast.walk(tree):
        found = ()
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mod = (node.module or '') if isinstance(node, ast.ImportFrom) else ''
            paths = ([mod] if mod else []) + [a.name for a in node.names]
            parts = set(p for path in paths for p in path.split('.'))
            if parts & (PY_REACH_PARTS - {'kitlib'}):
                return 'imports %s' % (mod or ', '.join(a.name for a in node.names))
            if 'kitlib' in parts and kit_import is None:
                kit_import = 'imports %s' % (mod or ', '.join(a.name for a in node.names))
            found = parts & (PY_RUN_MODULES | PY_RUN_ATTRS | PY_RUN_BUILTINS)
            if mod == 'sys' and 'path' in parts:
                found = set(found) | {'sys.path'}
        elif isinstance(node, ast.Name):
            found = {node.id} & (PY_RUN_ATTRS | PY_RUN_BUILTINS)
        elif isinstance(node, ast.Attribute):
            found = {node.attr} & PY_RUN_ATTRS
            backlog_used = backlog_used or node.attr == 'backlog'
            if node.attr == 'path' and isinstance(node.value, ast.Name) and node.value.id == 'sys':
                found = {'sys.path'}            # a folder put on the import path: sys.path.insert(0, 'kitlib/backlog')
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            s = node.value.strip()              # and each dotted part: pkgutil.resolve('os.system'), 'os:system'
            found = ({s} | set(PY_PART_RE.split(s))) & (PY_RUN_MODULES | PY_RUN_ATTRS | PY_RUN_BUILTINS)
            backlog_used = backlog_used or _names_backlog(s)
        elif isinstance(node, ast.Call) and writer is None:
            writer = _file_write(ast, node)
        if found and runner is None:
            runner = sorted(found)[0]
    if kit_import and backlog_used:
        return kit_import
    return runner or writer


# a file opened for writing (_file_write): an open mode holding w, a, x or +
PY_MODE_RE = re.compile(r'^[rwaxbtU+]+$')
PY_WRITE_MODE_RE = re.compile(r'[wax+]')
PY_WRITE_ATTRS = frozenset(('write_text', 'write_bytes'))


def _file_write(ast, node):
    """'opens a file for writing (<call>)' when the call `node` may write a file, else None:
    write_text or write_bytes, os.open, or an open() in any spelling (open, io.open, codecs.open,
    Path.open) given a mode of mode letters holding w, a, x or + (a positional string or mode=),
    a mode= (or **) that is not a literal, or the builtin's second argument that is not a literal."""
    f = node.func
    if isinstance(f, ast.Attribute) and f.attr in PY_WRITE_ATTRS:
        return 'opens a file for writing (%s)' % f.attr
    builtin = isinstance(f, ast.Name) and f.id == 'open'
    if not builtin and not (isinstance(f, ast.Attribute) and f.attr == 'open'):
        return None
    if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id == 'os':
        return 'opens a file for writing (os.open)'             # its flags are numbers: fail closed
    kw = [k.value for k in node.keywords if k.arg in ('mode', None)]

    def literal(v):
        return isinstance(v, ast.Constant) and isinstance(v.value, str)
    if not all(literal(v) for v in kw) or (builtin and len(node.args) > 1 and not literal(node.args[1])) or \
            any(literal(v) and PY_MODE_RE.match(v.value) and PY_WRITE_MODE_RE.search(v.value)
                for v in list(node.args) + kw):
        return 'opens a file for writing (open)'
    return None


def _names_backlog(s):
    """True when the string `s` names the tool or the backlog's library other than as a
    kitlib/backlog path (PY_REACH_PARTS): a CODE_RE name that is not one, or a dotted part
    'backlog' (a bare tool name a kitlib helper takes)."""
    if 'backlog' in PY_PART_RE.split(s):
        return True
    return any(PY_BACKLOG_PATH_RE.match(m.group(0)) is None for m in CODE_RE.finditer(s))


def judge_text(event, ctx, cmd, cwd, role, kind, text, head, path=None, level=1):
    """The code `cmd` runs from a script file or its standard input (`text`, read in `kind`:
    shell.BASH, shell.PS or 'py'): a shell's text is read as a command line in the shell that runs
    it and its commands judged by runs and gate-allow (gaterole.shell), an unreadable one by
    fallback.named_runs at `level` (past level 0 it reads no file the text names); a judged
    subagent's python by py_reach (CODE_RE, then its syntax tree: a name only printed or in a
    comment passes), except the kit's own (_kit_code of `path`). -c code is backlog_code's
    (py_reach for the code word, c_code). A refusal is reworded to begin with `head`."""
    if kind == 'py':
        why = py_reach(text) if _judged_subagent(event) else None
        if not why or (path and _kit_code(ctx, path)):
            return None
        return rules.refuse(RAW_RULE, role, '%s, whose code %s: code that reaches the backlog\'s library or '
                            'runs tools/backlog.py from a script skips the guard\'s role check, so it is a raw '
                            'write (kitlib/backlog/roles.py). %s' % (head, why, LITERAL_WHY),
                            literal_form(ctx))
    from . import fallback, gaterole
    p = cm.read(text, kind, cmd.cwd, home=getattr(ctx, 'home', None))
    for c in p.commands:
        ci = cm.invocation(c)
        d = runs(event, ctx, c, ci, cwd, role) or gaterole.shell(event, ctx, c, ci, cwd, role)
        if d:
            return rules.reworded(d, '%s, and in it: %s' % (head, d.reason))
    if not p.clean:
        d = fallback.named_runs(event, ctx, text, p, cmd.cwd, role, level=level)
        if d:
            return rules.reworded(d, '%s, and in it: %s' % (head, d.reason))
    return None


# ---------------------------------------------------------------- the store's refs

def store_kind(ctx, path):
    """The "backlog".store of the .kit.json that holds for `path`, or None when none reads."""
    return _store_read(ctx, path)[0]


def _store_read(ctx, path):
    """(store_kind for `path`, why the .kit.json there did not read: a text, or None). A load that
    raises other than ConfigError (a file that is not UTF-8: kitlib.config lets that through, so
    ctx.config_error has no word of it) answers no store and its fault."""
    try:
        cfg = ctx.config(path)
        return (cfg.section('backlog')['store'] if cfg is not None else None), None
    except Exception as e:                      # noqa: BLE001 - no store read: refs/backlog only
        return None, 'its .kit.json does not read: %s: %s' % (type(e).__name__, e)


def is_store_ref(name, store, short=False):
    """True when the ref `name` is the store's: under refs/backlog/ always, and with a `branch`
    store the backlog branch (refs/heads/backlog, refs/remotes/<remote>/backlog; `short`: its
    bare name too, as push and branch read one). A wildcard matches when it would take one. Case
    is not compared: on this machine's file system refs/Backlog/x is the file refs/backlog/x."""
    name = name.lstrip('+').lower()
    if '*' in name:
        import fnmatch
        samples = [REF_DIR + 'main'] + (['refs/heads/' + BRANCH] if store == 'branch' else [])
        return any(fnmatch.fnmatchcase(s, name) for s in samples)
    if name.startswith(REF_DIR) or name == REF_DIR.rstrip('/'):
        return True
    if store != 'branch':
        return False
    return name == 'refs/heads/' + BRANCH or bool(REMOTE_BRANCH_RE.match(name)) or (short and name == BRANCH)


def _store_unsure(ctx, gc, where, fault=None):
    """Why store_kind's None for `where` is no proof that no store reads there (a text), or None:
    git re-pointed (gc.blind), a directory the text does not name (gc.unknown), a .kit.json that
    does not load (ctx.config_error) or read (`fault`, _store_read), a place git cannot tell
    about, and any other place in a checkout: one whose main checkout the guard does not find
    (ctx.main_top: a linked worktree whose git common dir is not named .git, as a bare clone's
    or a --separate-git-dir repository's), so no .kit.json was read there. A folder in no
    checkout (repo.NONE) is the one sure None."""
    if gc.blind:
        return 'git is re-pointed: %s' % gc.blind
    if gc.unknown:
        return 'the directory git runs in is unknown'
    why = ctx.config_error(where) if where else None
    if why:
        return 'its .kit.json does not load: %s' % why
    if fault:
        return fault
    place = ctx.place(where)
    if place.kind == repo.UNKNOWN:
        return 'git cannot tell the place: %s' % place.detail
    if place.kind != repo.NONE:
        return ('the guard finds no main checkout for the place %s (%s, git common dir %s), so it read no '
                '.kit.json there' % (place.top, place.kind, place.common))
    return None


def _written_refs(gc, store, unsure=None):
    """The store refs the git call `gc` writes (texts), and why it may write the store's ref (a
    text) when its words do not name it: a mirror push, or, where a store reads (`store` not None)
    or may (`unsure`, _store_unsure), an update-ref whose ref the guard cannot read
    (rules._update_ref_unread: --stdin, an option it does not know, xargs, a computed word, no
    ref). Where none reads, such an update-ref still writes every store ref its words name
    literally."""
    sub = gc.sub
    short, longs, pos = rules._opts(sub, gc.rest)
    texts = [w.text for w in pos if not w.dyn]
    if sub == 'update-ref':
        unread = rules._update_ref_unread(gc, short, longs, pos)
        if unread:
            if store is None and not unsure:
                return [t for t in texts if is_store_ref(t, store)], None
            held = ('this repository reads a backlog store (%s)' % store if store is not None else
                    'the guard cannot tell whether a backlog store reads there (%s)' % unsure)
            return [], ('may write the backlog store\'s ref: the guard cannot tell which ref it writes (%s), '
                        'and %s' % (unread, held))
        return [pos[0].text] if is_store_ref(pos[0].text, store) else [], None
    if sub == 'push':
        if 'mirror' in longs:
            return [], 'pushes every ref, the backlog store\'s among them'
        delete = 'd' in short or 'delete' in longs
        out = []
        for spec in texts[1:]:
            src, colon, dst = spec.lstrip('+').partition(':')
            dst = dst if colon or delete else src
            if delete and not colon:
                dst = src
            if dst and is_store_ref(dst, store, short=True):
                out.append(spec)
        return out, None
    if sub == 'fetch':
        out = []
        for spec in texts[1:]:
            _src, colon, dst = spec.lstrip('+').partition(':')
            # a short destination is a branch (or under refs/) as git's fetch reads it
            if colon and dst and is_store_ref(rules.fetch_dst_ref(dst), store, short=False):
                out.append(spec)
        return out, None
    if sub == 'branch' and store == 'branch':
        listing = 'l' in short or bool({'list', 'show-current', 'merged', 'no-merged', 'contains', 'no-contains',
                                        'points-at'} & longs)
        writes = bool(set('dDmMcCf') & short) or bool({'delete', 'move', 'copy', 'force'} & longs)
        if listing and not writes:
            return [], None
        return [t for t in texts if is_store_ref(t, store, short=True)], None
    return [], None


def store_ref_write(event, ctx, cmd, inv, cwd, role):
    """Any session's but the owner's git write of the backlog store's ref: update-ref, a push
    or a fetch whose destination is it, a mirror push, an update-ref whose ref the guard cannot
    read where a store reads or the guard cannot tell (store_kind, _store_unsure; it fails closed:
    --stdin, xargs, a computed ref; not in a
    scratch repository, rules._scratch_repo, as git-shared-ref), and with a `branch` store a branch
    write of the backlog branch. The store tool writes it (its compare-and-swap) as a process,
    which no rule reads."""
    if rules.is_owner_session(event, ctx):
        return None
    gc = rules.git_call_judged(inv)
    if gc is None or gc.sub_dyn or gc.sub not in ('update-ref', 'push', 'fetch', 'branch'):
        return None
    where = gc.dir if gc.dir and not gc.unknown else (cmd.cwd or cwd)
    store, fault = _store_read(ctx, where) if where else (None, None)
    refs, whole = _written_refs(gc, store, None if store is not None else _store_unsure(ctx, gc, where, fault))
    if not refs and not whole:
        return None
    if not refs and gc.sub == 'update-ref' and gc.dir and not gc.unknown and not gc.blind and \
            rules._scratch_repo(ctx, ctx.place(gc.dir)):
        return None                             # unread, in a scratch repository: its refs are its own
    what = whole or 'writes the backlog store\'s ref (%s)' % ', '.join(refs)
    return rules.refuse(RAW_RULE, role, '`%s` %s. Records are written only through tools/backlog.py (its '
                        'compare-and-swap and its role check), so a direct ref write is a raw write, which no agent '
                        'session makes (kitlib/backlog/roles.py). Run the tool\'s verb:' % (rules.bare(cmd), what),
                        'python %s -C <worktree> <verb> <arguments>' % rules.kit_tool(ctx, TOOL))


# ---------------------------------------------------------------- a folder store's records

def raw_edit(event, ctx, target, role, what):
    """Any session's but the owner's edit of a file in a folder store (`main`: docs/backlog;
    another path: that folder) in the checkout it is in. An edit of the folder's records skips
    the tool and its role check."""
    if not target or rules.is_owner_session(event, ctx):
        return None
    store = store_kind(ctx, target)
    if not store or store in ('ref', 'branch'):
        return None
    top = ctx.place(target).top
    if not top:
        return None
    folder = os.path.join(top, *('docs/backlog' if store == 'main' else store).replace('\\', '/').split('/'))
    if not jail.inside(jail.real(target), jail.real(folder)):
        return None
    return rules.refuse(RAW_RULE, role, '%s: %s is the backlog\'s record folder (store %s); records are written '
                        'only through tools/backlog.py and its role check, so an edit there is a raw write, which no '
                        'agent session makes (kitlib/backlog/roles.py). Run the tool\'s verb:'
                        % (what, rules.fwd(folder), store),
                        'python %s -C %s <verb> <arguments>' % (rules.kit_tool(ctx, TOOL), rules.quote(top)))


def audit_edit(event, ctx, target, role, what):
    """Any session's but the owner's write in <pm_data>/audit/ (the machine config's pm_data.path):
    the backlog's audit log, which tools/backlog.py alone appends to after its writes. With no
    pm_data named there is no log, and nothing to refuse."""
    if not target or rules.is_owner_session(event, ctx):
        return None
    data = ctx.pm_data_path()
    if not data:
        return None
    folder = os.path.join(data, AUDIT_FOLDER)
    if not jail.inside(jail.real(target), jail.real(folder)):
        return None
    return rules.refuse(AUDIT_RULE, role, '%s: %s is the backlog\'s audit log, which only tools/backlog.py appends '
                        'to, after its own writes; no session but the owner\'s writes there. Make the change '
                        'through the tool, which logs it:' % (what, rules.fwd(folder)),
                        'python %s -C <worktree> <verb> <arguments>' % rules.kit_tool(ctx, TOOL))
