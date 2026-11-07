"""The helpers every family of the guard's rules shares (kitlib.guard.rules, which re-exports each name
here): the verdicts, refuse and the tag a refusal ends with, the quoting an alternative's command uses,
the kit folder and tools an alternative names, the PowerShell cmdlet tables several families read, the
session helpers (is_owner_session, owner_host, unowned_chat, agent_session), own_worktree and the
unplaced rule's name (UNPLACED)."""
import contextvars
import os
import re

from kitlib import agentname
from kitlib import kitroot
from kitlib.git import repo
from kitlib.host import Decision

from . import commands as cm
from . import context
from . import hub as hubg
from . import modes
from . import paths
from . import shell as sh

DENY, ASK, ALLOW = 'deny', 'ask', 'allow'
# kitlib.host.Action.shell -> the parser's dialect
SHELLS = {'bash': sh.BASH, 'powershell': sh.PS}
TAG = '[kit guard: rule=%s role=%s]'


def tag(rule, role):
    return TAG % (rule, role)


CITE_MAX = 120                  # the most of the command a warning quotes (cited)
ELIDED = ' [...]'
_COMMAND = contextvars.ContextVar('kit_guard_command', default=None)   # the shell text decide() judges


def cited(what, command, cap=CITE_MAX):
    """`what` with each backtick quote of the shell text `command` (or of a part of it) longer than
    `cap` cut to its first `cap` characters and ELIDED. A quote runs from a backtick to the furthest
    later backtick whose text between is all in `command`, so a PowerShell escape inside it does not
    end it; text `command` does not hold is never cut."""
    if not command or len(command) <= cap or '`' not in what:
        return what
    ticks = [i for i, c in enumerate(what) if c == '`']
    out, done, k = [], 0, 0
    while k < len(ticks):
        i, end = ticks[k], None
        for n in range(k + 1, len(ticks)):
            if what[i + 1:ticks[n]] not in command:
                break
            end = n
        if end is not None and ticks[end] - i - 1 > cap:
            out.append(what[done:i + 1 + cap] + ELIDED)
            done, k = ticks[end], end + 1
        else:
            k += 1
    return ''.join(out) + what[done:]


def refuse(rule, role, what, instead, kind=DENY):
    """The refusal `rule` makes, or None when its mode (kitlib.guard.modes) is warn, which keeps the
    reason as the call's warning, or off. A warning quotes at most CITE_MAX characters of the
    command decide() judges (cited); the Instead text and the tag stay whole, and a refusal keeps
    its full text."""
    m = modes.mode(rule)
    if m == modes.OFF:
        return None
    if m == modes.WARN:
        what = cited(what, _COMMAND.get())
    reason = '%s Instead: %s %s' % (what.rstrip(), instead.strip(), tag(rule, role))
    if m == modes.WARN:
        modes.note(reason)
        return None
    return Decision(kind, rule, reason, instead.strip())


def reworded(d, reason):
    """`d` with its reason replaced (a rule that adds what led to it)."""
    return Decision(d.verdict, d.rule, reason, d.instead)


def fwd(path):
    return path.replace('\\', '/')


def quote(path):
    return '"%s"' % fwd(path)


SAFE_WORD_RE = re.compile(r'^[\w@%+=:,./-]+$')


def spelt(text):
    """`text` as one shell word: bare when it needs no quoting, else single-quoted (double-quoted,
    with bash's escapes, when it holds a single quote)."""
    if SAFE_WORD_RE.match(text):
        return text
    if "'" not in text:
        return "'%s'" % text
    return '"%s"' % re.sub(r'(["\\$`])', r'\\\1', text)


def shell_word(text):
    """`text` as one word bash and PowerShell both read as written: bare when it holds only
    characters neither shell treats specially (no `,`, which PowerShell reads as an array, and no
    leading `@`, its splat), else single-quoted (literal in both); one that holds a single quote
    too has no such word, so it is `<value>` for the agent to fill in."""
    if re.fullmatch(r'[A-Za-z0-9_=.:/%+-][A-Za-z0-9_=.:/@%+-]*', text):
        return text
    return "'%s'" % text if "'" not in text else '<value>'


def kept_word(lead, sep, value, dyn):
    """One kept option of a held form (commit_scan's `keep`) as words both shells read as
    written: `lead` (`-X`, `--strategy`) and its `value`, joined (`sep` '') or as two words (' ');
    a value the shell computes, or one holding a single quote, is `<value>` after its lead."""
    if dyn or "'" in value:
        return lead + sep + '<value>'
    return shell_word(lead) + ' ' + shell_word(value) if sep else shell_word(lead + value)


def literal_word(path):
    """`path` as one word that bash and PowerShell both read literally: single-quoted when it holds
    a `$` or a backtick (which expand inside double quotes) and no single quote; otherwise
    double-quoted, as quote() does. A path with both a single quote and one of those has no such
    word and stays double-quoted (docs/guard.md "Rules", the `heavy` row)."""
    p = fwd(path)
    if ('$' in p or '`' in p) and "'" not in p:
        return "'%s'" % p
    return quote(p)


def advice_kit(ctx):
    """The kit folder an alternative names (docs/guard.md "Kit paths in advice"): the installed
    export the sessions' kit tools run from, when the guard can know it, else the kit the guard
    runs from (ctx.kit). The guard names the export the machine's plugin marketplace names now
    (kitlib.pluginsource.current_folder under the plugin export root), when that folder is an
    export (a 12-hex-digit name, the marker and kit.json in it) whose marker names the kit checkout
    the guard is from (export_checkout): the guard's own checkout, or, for a guard run from an
    export, the checkout that export's marker names. So a guard still running from an export a
    refresh replaced names the current one, which prune keeps, and which kitlib.host.headerfollow
    then tells the session its header names. The hook is not told which folder a session's header
    named, so a session that started on an older export is still pointed at the current one."""
    kit = getattr(ctx, 'kit', None)
    if not kit:
        return kit
    from kitlib import pluginsource
    from . import rolepolicy
    source = kit
    if os.path.isfile(os.path.join(kit, pluginsource.MARKER)):
        source = pluginsource.export_checkout(kit)
        if not source:
            return kit
    user = ctx.user()
    if not isinstance(user, dict) or user.get('error'):
        return kit
    try:
        root = pluginsource.settings(user.get(pluginsource.CONFIG_KEY), ctx.environ).export_dir
    except (pluginsource.RefreshError, OSError, ValueError, TypeError):
        return kit
    name = pluginsource.current_folder(root)
    if not isinstance(name, str) or not rolepolicy.EXPORT_NAME_RE.fullmatch(name):
        return kit
    folder = os.path.join(root, name)
    if not all(os.path.isfile(os.path.join(folder, f)) for f in rolepolicy.EXPORT_FILES):
        return kit
    checkout = pluginsource.export_checkout(folder)
    if not checkout or paths.norm(checkout) != paths.norm(source):
        return kit
    return folder


def lock_tool(ctx):
    """The lock CLI an alternative names: tools/machine_lock.py of the kit folder advice names
    (advice_kit, kitlib.kitroot.lock_tool), as one literal word (literal_word) since a kit path may
    hold a space or a `$`. Its `run` takes no --lock flag: it always locks."""
    return literal_word(kitroot.lock_tool(advice_kit(ctx)))


def kit_tool(ctx, name):
    """A kit tool an alternative names: tools/<name>.py of the kit folder advice names
    (advice_kit), quoted. A repository holds no copy of the kit's tools (decision 21 in
    docs/multi-host-plan.md section 7), so an alternative never names a repository's own tools
    folder."""
    return quote(kitroot.tool(name, advice_kit(ctx)))


def subagent(event):
    """True when the Action names a subagent (Action.agent): a host's hook told it apart."""
    return bool(getattr(event, 'agent', None))


def tag_role(event):
    """'main' for the main session, else 'subagent' (the tag the self-test reads)."""
    return 'subagent' if subagent(event) else 'main'


# FindFirstFile also reads `<` (DOS_STAR), `>` (DOS_QM) and `"` (DOS_DOT) as wildcards: `cmd /c del
# "STANDING<"` deletes STANDING.md. ledger_glob matches each as `*`, which matches all they can
# (fail closed); no file name holds one, so none is read as a literal name.
LEDGER_GLOB_CHARS = hubg.GLOB_CHARS | frozenset('<>"')


# PowerShell common parameters every cmdlet takes: the ones that take a value, the switches
PS_COMMON_VALUES = ('erroraction', 'warningaction', 'informationaction', 'progressaction', 'errorvariable',
                    'warningvariable', 'informationvariable', 'outvariable', 'outbuffer', 'pipelinevariable')
PS_COMMON_SWITCHES = ('verbose', 'debug', 'whatif', 'confirm')
PS_COMMON_ALIASES = {'ea': 'erroraction', 'wa': 'warningaction', 'infa': 'informationaction',
                     'proga': 'progressaction', 'ev': 'errorvariable', 'wv': 'warningvariable',
                     'iv': 'informationvariable', 'ov': 'outvariable', 'ob': 'outbuffer',
                     'pv': 'pipelinevariable', 'vb': 'verbose', 'db': 'debug', 'wi': 'whatif',
                     'cf': 'confirm', 'lp': 'literalpath', 'pspath': 'literalpath'}


class PSCmdlet(object):
    """A cmdlet's parameters as far as the guard needs them: `values` take a value, `switches`
    do not, `pos` names the positional slots in order, `writes` the parameters naming a file it
    writes (`'*'` in pos: every further positional binds to the last slot). `piped`: the groups of
    those it binds from the pipeline when the line binds no parameter of the group (a path the
    text does not show, PIPE_UNPLACED). `here`: the parameter whose absence makes it write the
    location it runs in (Copy-Item with no -Destination). `provider`: a provider-aware item or
    content cmdlet, whose item on a drive that holds no files is no file (PS_PROVIDER_CMDLETS)."""

    def __init__(self, values, switches=(), pos=(), writes=(), aliases=None, piped=(), here=None, provider=False):
        self.values = tuple(values) + PS_COMMON_VALUES
        self.switches = tuple(switches) + PS_COMMON_SWITCHES
        self.pos, self.writes = tuple(pos), tuple(writes)
        self.aliases = dict(PS_COMMON_ALIASES, **(aliases or {}))
        self.piped, self.here, self.provider = tuple(piped), here, provider

    def names(self):
        return self.values + self.switches


_PATHS = ('path', 'literalpath')
_FILTERS = ('filter', 'include', 'exclude', 'credential')
PS_CMDLETS = {
    'out-file': PSCmdlet(('filepath', 'literalpath', 'encoding', 'width', 'inputobject'),
                         ('append', 'force', 'noclobber', 'nonewline'), ('filepath', 'encoding'),
                         ('filepath', 'literalpath'), {'path': 'filepath'}),
    'set-content': PSCmdlet(_PATHS + _FILTERS + ('value', 'encoding', 'stream'),
                            ('passthru', 'force', 'nonewline', 'asbytestream'), ('path', 'value'), _PATHS,
                            piped=(_PATHS,), provider=True),
    'add-content': PSCmdlet(_PATHS + _FILTERS + ('value', 'encoding', 'stream'),
                            ('passthru', 'force', 'nonewline', 'asbytestream'), ('path', 'value'), _PATHS,
                            piped=(_PATHS,), provider=True),
    'clear-content': PSCmdlet(_PATHS + _FILTERS + ('stream',), ('force',), ('path',), _PATHS,
                              piped=(_PATHS,), provider=True),
    'tee-object': PSCmdlet(('filepath', 'literalpath', 'variable', 'inputobject', 'encoding'), ('append',),
                           ('filepath',), ('filepath', 'literalpath'), {'path': 'filepath'}),
    'new-item': PSCmdlet(('path', 'name', 'itemtype', 'value', 'credential'), ('force',), ('path',), ('path',),
                         {'type': 'itemtype'}, piped=(('path',),), provider=True),
    'remove-item': PSCmdlet(_PATHS + _FILTERS + ('stream',), ('recurse', 'force'), ('path', '*'), _PATHS,
                            piped=(_PATHS,), provider=True),
    # the paths Copy-Item takes from the pipeline are what it reads; its -Destination is the write
    'copy-item': PSCmdlet(_PATHS + _FILTERS + ('destination', 'tosession', 'fromsession'),
                          ('container', 'force', 'recurse', 'passthru'), ('path', 'destination'), ('destination',),
                          piped=(('destination',),), here='destination', provider=True),
    'move-item': PSCmdlet(_PATHS + _FILTERS + ('destination',), ('force', 'passthru'), ('path', 'destination'),
                          _PATHS + ('destination',), piped=(_PATHS, ('destination',)), here='destination',
                          provider=True),
    'rename-item': PSCmdlet(_PATHS + ('newname', 'credential'), ('force', 'passthru'), ('path', 'newname'), _PATHS,
                            piped=(_PATHS,), provider=True),
    'expand-archive': PSCmdlet(_PATHS + ('destinationpath',), ('force', 'passthru'), ('path', 'destinationpath'),
                               ('destinationpath',), here='destinationpath'),
    'compress-archive': PSCmdlet(_PATHS + ('destinationpath', 'compressionlevel'), ('update', 'force', 'passthru'),
                                 ('path', 'destinationpath'), ('destinationpath',)),
    'export-csv': PSCmdlet(_PATHS + ('inputobject', 'delimiter', 'encoding', 'quotefields', 'usequotes'),
                           ('append', 'force', 'noclobber', 'notypeinformation', 'includetypeinformation',
                            'useculture'), ('path', 'delimiter'), _PATHS),
    'export-clixml': PSCmdlet(_PATHS + ('inputobject', 'depth', 'encoding'), ('force', 'noclobber'), ('path',), _PATHS),
    'invoke-webrequest': PSCmdlet(('uri', 'outfile', 'method', 'body', 'headers', 'contenttype', 'useragent',
                                   'credential', 'websession', 'sessionvariable', 'timeoutsec', 'maximumredirection',
                                   'proxy', 'proxycredential', 'infile', 'certificate', 'certificatethumbprint',
                                   'transferencoding', 'maximumretrycount', 'retryintervalsec', 'form',
                                   'custommethod', 'authentication', 'token', 'httpversion'),
                                  ('usebasicparsing', 'passthru', 'usedefaultcredentials', 'disablekeepalive',
                                   'skipcertificatecheck', 'skipheadervalidation', 'resume', 'noproxy'),
                                  ('uri',), ('outfile',)),
    'get-childitem': PSCmdlet(_PATHS + ('filter', 'include', 'exclude', 'depth', 'attributes'),
                              ('recurse', 'force', 'name', 'directory', 'file', 'hidden', 'readonly', 'system',
                               'followsymlink'), ('path', 'filter'), ()),
    'set-itemproperty': PSCmdlet(_PATHS + _FILTERS + ('name', 'value', 'type', 'inputobject'), ('passthru', 'force'),
                                 ('path', 'name', 'value'), _PATHS, piped=(_PATHS,), provider=True),
    'start-transcript': PSCmdlet(_PATHS + ('outputdirectory',),
                                 ('append', 'force', 'noclobber', 'includeinvocationheader', 'useminimalheader'),
                                 ('path',), _PATHS + ('outputdirectory',)),
}
PS_CMDLETS['invoke-restmethod'] = PS_CMDLETS['invoke-webrequest']
PS_ALIASES = {'sc': 'set-content', 'ac': 'add-content', 'clc': 'clear-content', 'tee': 'tee-object',
              'ni': 'new-item', 'mkdir': 'new-item', 'md': 'new-item', 'ri': 'remove-item',
              'rm': 'remove-item', 'del': 'remove-item', 'erase': 'remove-item', 'rmdir': 'remove-item',
              'rd': 'remove-item', 'cpi': 'copy-item', 'cp': 'copy-item', 'copy': 'copy-item',
              'mi': 'move-item', 'mv': 'move-item', 'move': 'move-item', 'rni': 'rename-item',
              'ren': 'rename-item', 'iwr': 'invoke-webrequest', 'irm': 'invoke-restmethod',
              'curl': 'invoke-webrequest', 'wget': 'invoke-webrequest', 'epcsv': 'export-csv',
              'epal': 'export-alias', 'epsn': 'export-pssession',
              'gci': 'get-childitem', 'ls': 'get-childitem', 'dir': 'get-childitem', 'sp': 'set-itemproperty'}


WIN_OPT_RE = re.compile(r'^/[A-Za-z]+(?::\S*)?$')


PS_NAMED_DYN_RE = re.compile(r'^-[A-Za-z_]\w*:')    # -Name: and a computed value, in one word


def ps_param_word(cmd, w):
    """The text of `w` with its dash as '-' when PowerShell reads it as a parameter name (-Path,
    –Path, -Path:$p), else None. `-Destination:$p`: a literal name and a colon bind the
    computed rest to that parameter. A quoted word or a negative number is never a parameter."""
    t = sh.ps_dashed(w.text)
    if t.startswith('-') and len(t) > 1 and (not w.dyn or PS_NAMED_DYN_RE.match(t)) and \
            not cm.quoted(cmd, w) and not re.match(r'^-\d', t):
        return t
    return None


def ps_params(cmd, words, spec):
    """({canonical param: [value Word]}, [positional Word]) of a cmdlet's arguments, by `spec`.
    A parameter is matched as PowerShell does: exactly, by an alias, or by the one name it is a
    prefix of. An ambiguous prefix binds to every name it could be (the guard fails closed). A
    quoted word is never a parameter."""
    names = spec.names()
    named, pos = {}, []
    k = 0
    while k < len(words):
        w = words[k]
        t = ps_param_word(cmd, w)
        if t is not None:
            body, colon, val = t[1:].partition(':')
            body = body.lower()
            if body in names:
                cands = [body]
            elif body in spec.aliases:
                cands = [spec.aliases[body]]
            else:
                cands = [n for n in names if n.startswith(body)]
            k += 1
            vals = []
            if colon and val == '' and k < len(words):
                vals = [words[k]]               # `-Path: x`: a colon then a space binds the next word
                k += 1
            elif colon:
                vals = [sh.Word(val, w.dyn, w.start, w.end)]
            elif any(c in spec.values for c in cands) and k < len(words):
                vals = [words[k]]
                k += 1
            for c in cands:
                named.setdefault(c, []).extend(vals)
            continue
        pos.extend(split_commas(w))
        k += 1
    return named, pos


def ps_bound(spec, named, pos):
    """{param: [Word]}: the named parameters with the positionals bound to the slots left over."""
    out = dict((k, list(v)) for k, v in named.items())
    free = [p for p in spec.pos if p != '*' and p not in named]
    rest = list(pos)
    for k, p in enumerate(free):
        if not rest:
            break
        if k == len(free) - 1 and '*' in spec.pos:
            out.setdefault(p, []).extend(rest)
            rest = []
            break
        out.setdefault(p, []).append(rest.pop(0))
    return out


def ps_args(cmd, words, cmdlet):
    spec = PS_CMDLETS[cmdlet]
    named, pos = ps_params(cmd, words, spec)
    return ps_bound(spec, named, pos)


def split_commas(w):
    if w.dyn or ',' not in w.text:
        return [w]
    return [sh.Word(p, False, w.start, w.end) for p in w.text.split(',') if p]


def here(w):
    """A Word for the directory a command runs in: the file it writes there is not named."""
    return sh.Word('.', False, w.start if w is not None else 0, w.end if w is not None else 0)


def bare(cmd):
    if not cmd.words:
        return cmd.text
    return cmd.source[cmd.words[0].start:cmd.words[-1].end]


def same_dir(a, b):
    return bool(a and b) and paths.norm(a) == paths.norm(b)


def ps_native_unsure(w):
    """True when Windows PowerShell 5.1 may hand the word `w` to a program as other than one
    argument (observed): a double quote in it ('\\"a -c b' arrives as "a, -c, b), an empty word
    (dropped), or a blank in it and a backslash at its end (it takes in the words after it)."""
    t = w.text
    return '"' in t or not t or (t.endswith('\\') and any(c.isspace() for c in t))


def tail_word(w, value):
    """The Word for `value`, the end of the word `w` after an option attached to it (--output=x,
    -ox, -Cdir): its span covers only the value when `w` is spelt as its text (unquoted), so a
    rewrite (retarget) keeps the option; else the whole word's. A word with no span in the line
    (one an alias's value gave, inline_alias) gives a value with none either."""
    if w.start is None or w.end is None:
        return sh.Word(value, w.dyn, None, None)
    start = w.end - len(value) if w.end - w.start == len(w.text) else w.start
    return sh.Word(value, w.dyn, start, w.end)


def is_owner_session(event, ctx):
    """True for the owner's own main session: no subagent and no launcher mark (docs/guard.md
    "Session mark"), on a host whose hooks tell a subagent apart (owner_host). Every owner-only
    check reads it. A Codex chat is never the owner, the unmarked main chat too (an owner
    answer): owner actions are done from the owner's chat on the default host, or by hand."""
    return not subagent(event) and ctx.session_role(event) == context.OWNER and owner_host(ctx)


def owner_host(ctx):
    """True when the session's host can run the owner's session: its hosts.json entry says its
    hooks carry subagent identity (capabilities subagent_identity_in_hooks). False on Codex, and on
    a host no hosts.json entry has the profile of (fail closed); agent_session reads the same."""
    caps = ctx.host_capabilities()
    return caps is not None and caps.subagent_identity_in_hooks


def unowned_chat(event, ctx):
    """True for an unmarked main session on a host that never runs the owner's (owner_host)."""
    return not subagent(event) and ctx.session_role(event) == context.OWNER and not owner_host(ctx)


def agent_session(event, ctx):
    """True for a subagent, or a session whose mark is one of guard_agent_marks (default
    AGENT_MARKS: coordinator, deep-worker, consultant and pm), case
    ignored; a guard_agent_marks value replaces the default list. The rules only agents are held to (kill by name, lock state,
    kit-json, the write jail, git-stash-shared) use it. The mark is compared without the kit plugin's prefix
    (kitlib.agentname.bare_mark), so a role_resolver that does not strip it fails closed too.
    On a host whose hooks carry no subagent identity (hosts.json capabilities
    subagent_identity_in_hooks false: Codex) every session is an agent session, the unmarked
    main chat too, and so is one on a host no hosts.json entry has the profile of (fail closed)."""
    if subagent(event):
        return True
    marks = ctx.user().get('guard_agent_marks') or AGENT_MARKS
    who = agentname.bare_mark(str(ctx.session_role(event) or ''))
    if who.lower() in {str(m).lower() for m in marks}:
        return True
    return not owner_host(ctx)


# every session kind the kit launcher marks but `plain` and other agents' names
AGENT_MARKS = ('coordinator', 'deep-worker', 'consultant', 'pm')


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


class Asked(object):
    """The first ask among a run of decisions, held back so that a refusal judged after it wins:
    firm(d) gives `d` unless it is an ask (kept in `first`, the earliest), when it gives None."""

    def __init__(self):
        self.first = None

    def firm(self, d):
        if d is not None and d.verdict == ASK:
            self.first = self.first or d
            return None
        return d
