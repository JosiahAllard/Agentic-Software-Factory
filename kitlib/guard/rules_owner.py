"""The guard's rules on the owner's own powers (kitlib.guard.rules, which re-exports each name here):
the lock MCP's owner tools, the owner's tools a shell command runs, and the Action Hub's secrets,
owner verbs and MCP tools. The session checks they read (is_owner_session, unowned_chat) live in
rules_base."""
import json
import ntpath
import os
import re

from kitlib.config import user as kuser
from kitlib import lockmcp as lockmcp_names

from . import commands as cm
from . import hub as hubg
from . import jail
from . import shell as sh
from .rules_base import (LEDGER_GLOB_CHARS, bare, fwd, is_owner_session, kit_tool, lock_tool, quote, refuse,
                         subagent, unowned_chat)

# ---------------------------------------------------------------- lock MCP

def host_mcp_prefix(profile):
    """The lock MCP's tool-name prefix on the host `profile` (kitlib.host.HostProfile) describes:
    its mcp_tool_format with the kit's plugin and the lock server's name filled in and the tool
    left off. INFERRED for Claude Code, not observed: the kit has no installed plugin yet."""
    return profile.mcp_tool_format.format(plugin=lockmcp_names.PLUGIN_NAME,
                                          server=lockmcp_names.SERVER_NAME, tool='')


def host_mcp_separator(profile):
    """What the host's MCP tool names put between the server and the tool (`__` in
    mcp__<server>__<tool>): the text between {server} and {tool} in mcp_tool_format."""
    fmt = profile.mcp_tool_format
    i, j = fmt.find('{server}'), fmt.find('{tool}')
    return fmt[i + len('{server}'):j] if 0 <= i < j else ''


def mcp_prefix(ctx):
    """The machine config's guard_mcp_prefix, else the session's host's (host_mcp_prefix)."""
    return ctx.user().get('guard_mcp_prefix') or host_mcp_prefix(ctx.host_profile())


def mcp_owner_tool(ctx, tool, kind='mcp'):
    """The owner-only lock MCP tool `tool` names, or None: under the configured prefix, under any
    other server name (the tail after the host's separator, `mcp__<server>__<tool>`), or listed in
    full in guard_mcp_tools. Only an MCP call (Action kind 'mcp') names one. The names are the
    retired owner writes pinned in kitlib.lockmcp.OWNER_ONLY: the server no longer serves them,
    but one started from an older release still does until it restarts."""
    if kind != 'mcp' or not tool:
        return None
    owner_only = lockmcp_names.OWNER_ONLY
    prefix = mcp_prefix(ctx)
    if tool.startswith(prefix) and tool[len(prefix):] in owner_only:
        return tool[len(prefix):]
    sep = host_mcp_separator(ctx.host_profile())
    tail = tool.rsplit(sep, 1)[-1] if sep else tool
    if tail in owner_only:
        return tail                             # the same tool under another server name
    if tool in (ctx.user().get('guard_mcp_tools') or ()):
        return tool
    return None


def lock_mcp(event, ctx, tool, role):
    name = mcp_owner_tool(ctx, tool, event.kind)
    if not name:
        return None
    if is_owner_session(event, ctx):
        return None                             # the owner's own session
    who = unowned_name(ctx) if unowned_chat(event, ctx) else ctx.session_role(event)
    what ='%s changes the machine lock and is the owner\'s alone (ARCHITECTURE.md "Guard rules").' % name
    status = 'python %s status' % lock_tool(ctx)
    return refuse('lock-mcp', role, what + ' This session is %s. Ask the owner (through the PM) to make the change; '
                  'the read tools (lock_snapshot, lock_run, lock_hold) stay open. To see the lock state:' % who, status)


# ---------------------------------------------------------------- owner tools

# The lock's own owner check reads process ancestry, which a detached process can forge, so agents do
# not run the owner's tools: reserve_ui, the lock CLI's owner-only writes and machine setup to act.
# The rule reads the file a command runs (tool_run), never text that only names one: a read, git, a
# name the shell builds at run time and python code that imports the package are not read.
UI_NAME_RE = re.compile(r'(?i)^reserve_ui(?:\.pyw?)?$')
OPENERS = ('invoke-item', 'ii', 'explorer')     # open a file through its association: runs a .pyw
# the interpreters the guard knows: commands.PYTHON_RE plus the pyw launcher
PY_RUN_RE = re.compile(r'^(python|pythonw|py|pyw)(\d+(\.\d+)*)?$')
# python's -c or -m, alone or ending a cluster of its no-value flags (-Bm), its value maybe joined
PY_FLAG_RE = re.compile(r'^-[bBdEiIOPqsSuvx]*([cm])(.*)$')
LOCK_SCRIPT = 'machine_lock.py'
# options of either lock CLI that take a value, so their value is not read as a subcommand
LOCK_VALUE_OPTS = {'--test-root', '--test-hardware', '--test-kinds', '--test-actor', '--kind', '--repo',
                   '--slug', '--cores', '--hold', '--reservation', '--gpu-share', '--on-release',
                   '--max-wait', '--expect', '--note', '--booking', '--detached-id', '--from', '--until',
                   '--date', '--for', '--cpu', '--gpu', '--set'}
OWNER_LOCK_WHY = ('owner-only lock tools are run by the owner, not agents; the lock\'s own owner check stays '
                  'as a second layer. Ask the owner to make the change. To see the lock state:')


def tool_run(inv):
    """(name, Words) for the file the invocation `inv` runs, or None: its program's base name
    (commands.base_name) and arguments; python's script (its first operand) or -m module (as
    <its last part>.py) and the words after it; PowerShell's .ps1 script; a POSIX shell's script
    (its first operand; -c text and -s standard input are None); the file an opener
    (Invoke-Item, explorer, Git Bash's start) opens. The kit command (`kit X`, tools/kit.py X,
    -m tools.kit X) is read as the tool X it runs (kit_run). python -c, standard input and a
    program or script the text does not show are None: the guard reads what a line runs, not
    what it builds."""
    return kit_run(_tool_run(inv))


def kit_run(run):
    """The (name, Words) `run` of tool_run, with the kit command read as the tool it runs
    (commands.kit_tool): ('backlog.py', the Words after `backlog`) for `kit backlog ...`; None
    for a computed tool name; `run` itself when it names no tool (kit -h) or is not the kit."""
    if run is None or run[0] not in (cm.KIT, cm.KIT_SCRIPT):
        return run
    script, rest = cm.kit_tool(run[1])
    if script is None:
        return None
    return (script, rest) if script else run


def script_run(inv):
    """tool_run, with runpy's module argument (`python -m runpy tools.X`) read as the script it
    runs, the kit command's included (`-m runpy tools.kit X` is X.py)."""
    run = tool_run(inv)
    if run is not None and run[0] == 'runpy.py':
        words = run[1]
        if not words or words[0].dyn:
            return None
        run = kit_run((words[0].text.rsplit('.', 1)[-1].lower() + '.py', words[1:]))
    return run


# a computed script word whose text ends in a literal file name after a slash or backslash
# (`$K/tools/backlog.py`): its folder is computed, its file is not
COMPUTED_TAIL_RE = re.compile(r'[/\\]([\w.-]+\.py)$')


def computed_script(inv):
    """(the file's lower-case name, the Words after it) when python runs a script whose word is
    computed but ends in a literal file name after a slash or backslash (`python
    "$K/tools/backlog.py" block X-1`), which tool_run gives as None; else None. The options before
    the script are python's own, read as tool_run reads them; -c, -m and standard input are None."""
    if inv is None or inv.dyn or not PY_RUN_RE.match(inv.name or ''):
        return None
    args, k = list(inv.args), 0
    while k < len(args) and not args[k].dyn:
        t = args[k].text
        if t == '-' or PY_FLAG_RE.match(t) or not t.startswith('-'):
            return None                                 # -c, -m, stdin, or a literal script: tool_run's
        k += 2 if t in cm.PY_VALUE_OPTS else 1
    if k >= len(args):
        return None
    m = COMPUTED_TAIL_RE.search(args[k].text)
    return (m.group(1).lower(), args[k + 1:]) if m else None


def _tool_run(inv):
    if inv is None or inv.dyn:
        return None
    name, args = inv.name or '', list(inv.args)
    if name in OPENERS or (name == 'start' and inv.via != 'start-process'):
        while args and (args[0].text.startswith('/') or not args[0].text):   # start [/opts] ["title"]
            args.pop(0)
        if not args or args[0].dyn:
            return None
        if name in OPENERS:
            return cm.base_name(args[0].text), args[1:]
        name, args = cm.base_name(args[0].text), args[1:]
    if name in cm.PS_SHELLS:
        for k, a in enumerate(args):
            if a.text.lower().endswith('.ps1') and not a.dyn:
                return cm.base_name(a.text), args[k + 1:]
        return None
    if name in cm.BASH_SHELLS:                          # sh [opts] script.sh args (installer/machine-setup.sh)
        k = 0
        while k < len(args):
            t = args[k].text
            if t in cm.BASH_VALUE_OPTS:
                k += 2
                continue
            if t in ('-', '--'):
                k += 1
                break
            if t[:1] in '-+' and len(t) > 1:
                if t[:1] == '-' and not t.startswith('--') and ('c' in t[1:] or 's' in t[1:]):
                    return None                         # -c text (the nested parse reads it), -s stdin
                k += 1
                continue
            break
        if k >= len(args) or args[k].dyn:
            return None
        return cm.base_name(args[k].text), args[k + 1:]
    if not PY_RUN_RE.match(name):
        return name, args
    k = 0
    while k < len(args):
        t = args[k].text
        flag = PY_FLAG_RE.match(t)
        if t == '-' or (flag and flag.group(1) == 'c'):
            return None
        if flag:                                        # -m, -Bm, -mtools.hub: python's own -m
            if not flag.group(2):
                if k + 1 >= len(args) or args[k + 1].dyn:
                    return None
                k += 1
            mod = flag.group(2) or args[k].text
            return mod.rsplit('.', 1)[-1].lower() + '.py', args[k + 1:]
        if not t.startswith('-'):
            break
        k += 2 if t in cm.PY_VALUE_OPTS else 1
    if k >= len(args) or args[k].dyn:
        return None
    return cm.base_name(args[k].text), args[k + 1:]


def tool_words(words):
    """The texts of tool_run's Words, one holding spaces split (a Start-Process -ArgumentList string)."""
    out = []
    for w in words:
        out.extend(w.text.split() if ' ' in w.text else [w.text])
    return out


def lock_owner_write(words):
    """The owner-only write of a lock CLI call whose arguments (after the script) are `words`,
    as text, or None. The live CLI: reserve, release, stop/unbook --force. The kit CLI
    (tools/machine_lock.py): hold whole|half|slots, division --set|--default, game on|off|set,
    top (move a waiting run to the front), stop --force. Reads, runs, agent holds and bookings, and ending one's own run or hold stay
    open; the lock's own check refuses an agent the rest (kitlib.lock.api.authorize)."""
    head = []
    for w in words:
        if w == '--':
            break
        head.append(w)
    pos, k = [], 0
    while k < len(head):
        t = head[k]
        if t.startswith('-'):
            k += 2 if t in LOCK_VALUE_OPTS else 1
            continue
        pos.append(t)
        k += 1
    if not pos:
        return None
    sub, args = pos[0], pos[1:]
    if sub in ('reserve', 'release', 'top'):
        return sub
    if sub == 'hold' and args[:1] and args[0] in ('whole', 'half', 'slots'):
        return 'hold ' + args[0]
    if sub == 'game' and args[:1] and args[0] in ('on', 'off', 'set'):
        return 'game ' + args[0]
    if sub == 'division':
        for t in head:
            if t in ('--set', '--default') or t.startswith('--set='):
                return 'division ' + t.split('=', 1)[0]
    if sub in ('stop', 'unbook') and '--force' in head:
        return sub + ' --force'
    return None


# Machine setup's --apply writes the machine config, the owner's email among them (plan decision 27),
# and the owner's email is what proves a commit the owner's (owner-only paths, owner-identity). An agent
# that ran it could make its own address the owner's, so only the owner runs it to act. Its dry run stays
# open. The entry points: tools/machine_setup.py, its PowerShell front ends and its Linux front end
# installer/machine-setup.sh (run as a program or as a shell's script, tool_run; it takes --apply exactly).
SETUP_SCRIPTS = {'machine_setup.py': 'py', 'machine-setup.ps1': 'ps', 'new-repo.ps1': 'ps',
                 'machine-setup.sh': 'py'}
# argparse takes a unique prefix of --apply (--ap..); PowerShell a prefix of -Apply, -Apply:$true too
SETUP_APPLY = {'py': re.compile(r'^--ap(?:p(?:l(?:y)?)?)?(?:=.*)?$'),
               'ps': re.compile(r'(?i)^-a(?:p(?:p(?:l(?:y)?)?)?)?(?::.*)?$')}
OWNER_SETUP_WHY = ('machine setup writes the machine config, and the owner\'s email there decides which commits '
                   'count as the owner\'s, so only the owner runs it to act. Ask the owner to run it. To see '
                   'what it would do (the dry run):')


def owner_tool_run(inv):
    """What the invocation `inv` runs of the owner's tools (tool_run), as text, or None: reserve_ui;
    the lock CLI with an owner-only write (lock_owner_write); a machine setup entry point
    (SETUP_SCRIPTS) with --apply / -Apply before any `--`. Its dry run is None."""
    run = tool_run(inv)
    if run is None:
        return None
    name, words = run
    if UI_NAME_RE.match(name):
        return 'reserve_ui, the owner\'s lock reservation UI'
    texts = tool_words(words)
    if name == LOCK_SCRIPT:
        found = lock_owner_write(texts)
        return 'machine_lock.py %s' % found if found else None
    style = SETUP_SCRIPTS.get(name)
    for t in texts if style else ():
        if t == '--':
            break
        if SETUP_APPLY[style].match(t):
            return '%s %s' % (name, t)
    return None


def owner_tool(event, ctx, cmd, inv, cwd, role):
    """owner-tool: a command that runs one of the owner's tools to act (owner_tool_run), in any
    session but the owner's own."""
    if inv is None or is_owner_session(event, ctx):
        return None
    found = owner_tool_run(inv)
    if not found:
        return None
    if found.split()[0] in SETUP_SCRIPTS:
        return refuse('owner-tool', role, '`%s` runs %s: %s' % (bare(cmd), found, OWNER_SETUP_WHY),
                      'python %s' % kit_tool(ctx, 'machine_setup'))
    return refuse('owner-tool', role, '`%s` runs %s, the owner\'s: %s' % (bare(cmd), found, OWNER_LOCK_WHY),
                  'python %s status' % lock_tool(ctx))


# ---------------------------------------------------------------- the Action Hub

# docs/guard.md "Hub secrets". The hub's token folder (owner.token answers as the owner,
# agent.token is every agent's, approval.key will sign the owner's approvals) and its store are
# reached by agents only through tools/hub.py and the hub MCP server, which read agent.token in
# their own process; the hub server's git sync writes the store. So an agent's own edit or shell
# command that names them literally is refused (hub-secrets), as are tools/hub.py's owner verbs on
# a hub tool run the parser reads (hub-owner-verb), the hub MCP server's owner tools (hub-mcp) and
# a browser or computer-use call aimed at the hub's own page (hub-browser). Every session but the
# owner's own is held, as owner-tool holds it: each is the owner's power, not a place an agent
# works. A name the shell or code builds at run time is not read: the rules catch mistakes, not
# an adversary.
HUB_SCRIPT = 'hub.py'
NODE_EVAL = ('-e', '--eval', '-p', '--print', '-pe')
# node's options whose value is the next word (inline_code): a module it loads first, not a script
NODE_VALUE_OPTS = ('-r', '--require', '--import', '--loader', '--experimental-loader', '-C', '--conditions',
                   '--input-type')
HUB_SECRETS_WHY = ('the Action Hub\'s token folder holds the owner\'s key to the hub (owner.token answers as the '
                   'owner) and its store is the hub server\'s to write; an agent reaches the hub only through its '
                   'tool and its MCP server, which read agent.token themselves (docs/guard.md "Hub secrets").')
HUB_OWNER_WHY = ('answering, closing, submitting, sending and approving on the Action Hub are the owner\'s alone '
                 '(docs/guard.md "Hub secrets").')


def hub_config(ctx):
    """The machine config's `hub` object: the strict read's, else (a config the strict read
    refuses) its file's own JSON (the PM repository's local.json, kitlib.config.user LOCAL_KEYS),
    else {}. Its store and ports are read even from a config with a fault elsewhere."""
    user = ctx.user()
    obj = user.get('hub')
    if isinstance(obj, dict):
        return obj
    if not user.get('error'):
        return {}
    loc = ctx.config_location()
    try:
        with open(loc.local or loc.path, encoding='utf-8-sig') as f:
            data = json.loads(f.read())
    except (OSError, ValueError, UnicodeDecodeError):
        return {}
    obj = data.get('hub') if isinstance(data, dict) else None
    return obj if isinstance(obj, dict) else {}


def hub_real(path):
    """jail.real(path), or None when the text is no path the filesystem can take."""
    try:
        return jail.real(path)
    except (OSError, ValueError):
        return None


def hub_secret_folders(ctx):
    """[(path, real)]: the hub's token folders, each as written and as jail.real gives it. `hub`
    in the home folder's machine config folder (kitlib.config.user.DIR_NAME), where the hub kept
    them before the move and the move leaves its source; the one beside the machine config the
    guard reads (kitlib.guard.hub.secret_folder of Location.new, where kitlib.hubsite.auth.folder
    finds them); and `hub` in the PM repository's machine config folder
    (jail.pointer_config_dir), where the owner's machine setup copies the hub's files before the
    hub reads them from there. A folder named twice is listed once, and one jail.real cannot take
    is left out."""
    out = []
    pointer = jail.pointer_config_dir(ctx)
    folders = [os.path.join(ctx.home, kuser.DIR_NAME, hubg.FOLDER_NAME),
               hubg.secret_folder(ctx.config_location().new)]
    if pointer:
        folders.append(os.path.join(pointer, hubg.FOLDER_NAME))
    for p in folders:
        r = hub_real(p)
        if r and r not in [x[1] for x in out]:
            out.append((p, r))
    return out


class HubPlaces(object):
    """The hub's folders for one guard call: `secrets` the token folders (hub_secret_folders)
    and `stores` the store folders (kitlib.guard.hub.stores), each as written and as jail.real
    gives it."""

    def __init__(self, ctx):
        self.secrets = hub_secret_folders(ctx)
        self.store_paths = hubg.stores(hub_config(ctx), ctx.pm_data_path())
        self.stores = [s for s in (hub_real(p) for p in self.store_paths) if s]

    def secret_of(self, path):
        """The token folder (as written) the absolute `path` is in, or None."""
        here_ = hub_real(path)
        if here_ is None:
            return None
        for p, r in self.secrets:
            if jail.inside(here_, r):
                return p
        return None

    def where(self, path):
        """'secret', 'store' or None for the absolute `path` (real()ed here)."""
        if self.secret_of(path):
            return 'secret'
        here_ = hub_real(path)
        if here_ is not None and any(jail.inside(here_, s) for s in self.stores):
            return 'store'
        return None

    def label(self, where, path):
        """What a refusal names for `path`, which where() placed at `where`."""
        if where == 'secret':
            return 'the hub token folder %s' % quote(self.secret_of(path))
        return 'the hub store'


def hub_session(event, ctx):
    """The session as an owner-only check's refusal names it."""
    if subagent(event):
        return 'a subagent'
    if unowned_chat(event, ctx):
        return unowned_name(ctx)
    return 'marked %s' % ctx.session_role(event)


def unowned_name(ctx):
    """An unowned_chat as a refusal names it, with where owner actions are done."""
    return ("an unmarked %s chat, never the owner: owner actions are done from the owner's chat on the "
            'default host, or by hand'
            % ctx.host_profile().attribution.name)


def hub_read_alternative(ctx):
    return 'python %s questions' % kit_tool(ctx, 'hub')


def hub_ask_alternative(ctx):
    # a question needs a record or a class (docs/action-hub.md "A question's class")
    return ('python %s questions add --title "<what the owner should decide>" '
            '(--record <repo>:<id> | --class <rule|machine|cross-repo|go>)' % kit_tool(ctx, 'hub'))


def hub_secrets_refusal(event, ctx, role, what):
    return refuse('hub-secrets', role, '%s: %s This session is %s. To read the hub through its tool:' %
                  (what, HUB_SECRETS_WHY, hub_session(event, ctx)), hub_read_alternative(ctx))


def hub_secret_edit(event, ctx, target, role, what):
    """hub-secrets for an edit: a file in the hub's token folder or store (HubPlaces), in any
    session but the owner's own."""
    if not target or is_owner_session(event, ctx):
        return None
    places = HubPlaces(ctx)
    where = places.where(target)
    if where is None:
        return None
    return hub_secrets_refusal(event, ctx, role, '%s is in %s' % (what, places.label(where, target)))


def hub_secret_read(event, ctx, cwd, role):
    """hub-secrets for a read tool (Action kind 'read'), in any session but the owner's own: a
    read of the hub's token folder or a file in it, and a contents search that can read one. The
    store stays readable. A search starting in the folder is refused; one from a parent is refused
    unless its file filter can take in none of the folder's files (hub.filter_reaches). A search
    with no filter is refused at every parent: any of its output modes, counts and file lists too,
    would answer for a token's text. A names search (Glob) is not hooked (it reads no token's text; docs/guard.md
    "Hub secrets"). Each token folder (hub_secret_folders) is held."""
    if is_owner_session(event, ctx):
        return None
    for secret_path, secret in hub_secret_folders(ctx):
        d = hub_folder_read(event, ctx, cwd, role, secret_path, secret)
        if d:
            return d
    return None


def hub_folder_read(event, ctx, cwd, role, secret_path, secret):
    """hub_secret_read for the one token folder `secret_path` (real()ed: `secret`)."""
    params = event.params if isinstance(event.params, dict) else None
    target = event.path if isinstance(event.path, str) else None
    tool = event.tool or 'a read'
    label = 'the hub token folder %s' % quote(secret_path)
    if params is None:
        if not target:
            return None
        full = hub_real(sh.resolve_path(target, cwd, home=ctx.home) or target)
        if full and jail.inside(full, secret):
            return hub_secrets_refusal(event, ctx, role, '%s of %s is in %s' % (tool, fwd(target), label))
        return None
    start = sh.resolve_path(target or cwd, cwd, home=ctx.home) or target or cwd
    root = hub_real(start)
    if root is None:
        return None
    what = '%s from %s' % (tool, fwd(start))
    if jail.inside(root, secret):
        return hub_secrets_refusal(event, ctx, role, '%s is in %s' % (what, label))
    if not jail.inside(secret, root):
        return None
    flt = params.get('filter')
    rel = [p for p in ntpath.relpath(secret, root).split('\\') if p]
    names = hub_secret_names(secret) if flt is not None else None
    if flt is None or names is None or hubg.filter_reaches(flt, rel, names):
        how = 'with no file filter' if flt is None else 'with a file filter (%s) that can take in a file in it' % flt
        return hub_secrets_refusal(event, ctx, role, '%s, a parent of %s, %s' % (what, label, how))
    return None


HUB_NAMES_LIMIT = 2000


def hub_secret_names(secret):
    """Every name a search could meet in the token folder, its subfolders' too, with
    hub.SECRET_FILES; None past HUB_NAMES_LIMIT (the caller then counts the folder as reached)."""
    found = list(hubg.SECRET_FILES)
    for _top, dirs, files in os.walk(secret):
        found.extend(dirs)
        found.extend(files)
        if len(found) > HUB_NAMES_LIMIT:
            return None
    return found


def hub_path_texts(text):
    """The texts of one word that may be paths: the word, and the value of `--opt=value` or
    PowerShell's `-Opt:value`; an option alone is none."""
    if not text.startswith('-'):
        return [text]
    for sep in ('=', ':'):
        if sep in text:
            return [text.split(sep, 1)[1]]
    return []


def glob_folder(text, chars=hubg.GLOB_CHARS):
    """The literal folder a wildcard path `text` starts with (everything before the separator
    ahead of its first wildcard character, one of `chars`), or None when it has none:
    `<token folder>/o*` is in the token folder whatever the wildcard matches."""
    first = min(text.index(c) for c in chars if c in text)
    cut = max(text.rfind('/', 0, first), text.rfind('\\', 0, first))
    return text[:cut] if cut > 0 else None


def inline_code(cmd, inv, skip_pipe=None):
    """The code python or node runs from its own command line (-c, -e) or from standard input
    (a heredoc, a here-string, the command piped in), as texts; [] for a script run or another
    program. The command line's texts are every argument (a path the code is given counts).
    `skip_pipe(c)` true leaves out the text of the command `c` piped in when the code is on the
    command line (it is then the code's data, not code); code read from standard input always
    counts what is piped in."""
    if inv is None:
        return []
    py, node = PY_RUN_RE.match(inv.name or '') is not None, inv.name == 'node'
    if not (py or node):
        return []
    args = [w.text for w in inv.args]
    code, stdin, k = False, True, 0
    while k < len(args):
        t = args[k]
        if (py and t in cm.PY_VALUE_OPTS) or (node and t in NODE_VALUE_OPTS):
            k += 2
            continue
        if (py and re.match(r'^-[A-Za-z]*c', t)) or (node and t.split('=', 1)[0] in NODE_EVAL):
            code = True
            break
        if py and t == '-m':
            stdin = False
            break
        if t.startswith('-') and t != '-':
            k += 1
            continue
        stdin = t == '-'                        # a script file, or `-`: standard input
        break
    texts = list(args) if code else []
    if stdin or code:
        for c in (cmd, cmd.pipe_from):
            if c is None:
                continue
            if c is not cmd and not (code and skip_pipe is not None and skip_pipe(c)):
                texts.append(c.text)
            texts.extend(r.target.text for r in c.redirects
                         if r.target is not None and (r.body or r.op == '<<<'))
    return texts


def hub_secret_shell(ctx, cmd, inv, places):
    """What the command `cmd` names of the hub's secrets, as text, or None: a literal path in
    the token folder (a word that only names a token file or looks like the folder, with
    no path into it, passes), and python or node code that names a token file or the folder
    (kitlib.guard.hub.INLINE_RE; docs/guard-v2.md W3: a parsed hub tool run of an agent verb piped into
    code given on the command line is that code's data, hub_run_verb). A wildcard path is read as the literal folder before its first
    wildcard (glob_folder); a DOS wildcard (LEDGER_GLOB_CHARS: `<`, `>`, `"`) is read both ways,
    as that folder and as the literal path (hub_texts); the store and the word hub are not refused (the guard blocks only
    what it can place; an edit tool's write into the store is still hub_secret_edit's).
    A path may start with the home folder as `~` or a home variable ($HOME, $env:USERPROFILE,
    %USERPROFILE%, $env:HOMEDRIVE$env:HOMEPATH: shell.home_text, read as ctx.home), and a
    relative one is read from the directory a cd to the home folder, or a bare cd, moved to
    (the command's cwd_home). `places()` gives the HubPlaces."""
    targets = [r.target for r in cmd.redirects if r.target is not None and not r.body and r.op != '<<<']
    for w in list(cmd.words) + targets:
        for text in hub_path_texts(w.text):
            for text in hub_texts(sh.home_text(text, ctx.home, w.dyn)):
                try:
                    p = sh.resolve_path(text, cmd.cwd_home, cmd.shell, ctx.home)
                except (OSError, ValueError):
                    p = None
                if not p:
                    continue
                p_ = places()
                if p_.where(p) == 'secret':
                    return 'reaches %s (%s)' % (p_.label('secret', p), w.text)
    for text in inline_code(cmd, inv, lambda c: hub_run_verb(c) == ''):
        m = hubg.INLINE_RE.search(text)
        if m:
            return 'runs code that names %s' % m.group(0)
    return None


def hub_texts(text):
    """The paths hub_secret_shell judges for one path text: a wildcard one as its literal folder
    (glob_folder); one with a DOS wildcard (`<`, `>`, `"`: FindFirstFile reads them as wildcards)
    also as the literal folder before the first of those, and, when it has no other wildcard, as
    the literal path too (each reading only adds a path judged)."""
    if text is None:
        return []
    out = [glob_folder(text) if hubg.has_glob(text) else text]
    if any(c in text for c in '<>"'):
        out.append(glob_folder(text, LEDGER_GLOB_CHARS))
    return [t for t in out if t is not None]


def hub_run_args(inv):
    """The Words after the hub tool when `inv` runs it (script_run: python with tools/hub.py as
    its script, or -m tools.hub; tools/hub.py run as the program itself; runpy's module argument,
    `python -m runpy tools.hub`, read as the script; the kit command's `kit hub`), else None."""
    run = script_run(inv)
    if run is None or run[0] != HUB_SCRIPT:
        return None
    return run[1]


def hub_owner_run(parsed):
    """The owner verb a parsed hub tool run on the line `parsed` runs (hub_run_args, every
    command at any depth; kitlib.guard.hub.owner_verb), as text, or None. A word the guard cannot
    read counts as unreadable there, and so does a PowerShell word holding a double quote, which
    Windows PowerShell 5.1 passes on unescaped (it can split into --owner)."""
    for cmd in parsed.commands:
        verb = hub_run_verb(cmd)
        if verb:
            return verb
    return None


def hub_run_verb(cmd):
    """For the command `cmd`: None when it is not a parsed hub tool run (hub_run_args), else the
    owner verb it runs (kitlib.guard.hub.owner_verb, read as hub_owner_run reads it), or '' for
    an agent verb."""
    args = hub_run_args(cm.invocation(cmd))
    if args is None:
        return None
    ps = cmd.shell == sh.PS
    return hubg.owner_verb([hubg.Word(w.text, w.dyn or (ps and '"' in w.text)) for w in args]) or ''


def hub_line(text):
    """The line `text` for a refusal: stripped, and cut to its first 200 characters."""
    text = text.strip()
    return text if len(text) <= 200 else text[:200] + '...'


class HubShell(object):
    """The hub rules of one shell call, as rules with the shell rules' arguments; the owner's
    own session is read once and the hub's folders only when a word needs them."""

    def __init__(self, event, ctx, parsed):
        self.owner = is_owner_session(event, ctx)
        self.ctx = ctx
        self.parsed = parsed
        self._places = None
        self._done = False

    def places(self):
        if self._places is None:
            self._places = HubPlaces(self.ctx)
        return self._places

    def owner_verb(self, event, ctx, cmd, inv, cwd, role):
        """hub-owner-verb, read once for the whole line: a parsed hub tool run running an owner
        verb (hub_owner_run)."""
        if self.owner or self._done:
            return None
        self._done = True
        verb = hub_owner_run(self.parsed)
        if not verb:
            return None
        return refuse('hub-owner-verb', role, '`%s` runs the hub tool\'s owner verb (%s): %s This session is %s. '
                      'Ask the owner through a question:' % (hub_line(self.parsed.text), verb, HUB_OWNER_WHY,
                                                             hub_session(event, ctx)), hub_ask_alternative(ctx))

    def secrets(self, event, ctx, cmd, inv, cwd, role):
        """hub-secrets: a shell command that names the hub's secrets (hub_secret_shell)."""
        if self.owner:
            return None
        found = hub_secret_shell(ctx, cmd, inv, self.places)
        if not found:
            return None
        return hub_secrets_refusal(event, ctx, role, '`%s` %s' % (bare(cmd), found))


def hub_mcp(event, ctx, tool, role):
    """hub-mcp: a hub MCP owner tool (kitlib.guard.hub.owner_mcp); hub-browser: a browser or
    computer-use MCP call whose input names the hub's own origin (a loopback host on a hub
    port). In any session but the owner's own."""
    if event.kind != 'mcp' or not tool or is_owner_session(event, ctx):
        return None
    server, tail = hubg.split_mcp(tool, host_mcp_separator(ctx.host_profile()) or '__')
    who = hub_session(event, ctx)
    if hubg.owner_mcp(tail, server):
        return refuse('hub-mcp', role, '%s acts as the owner on the Action Hub: %s This session is %s; the agent '
                      'tools (asking, listing) stay open. Ask the owner through a question:' %
                      (tail, HUB_OWNER_WHY, who), hub_ask_alternative(ctx))
    if not hubg.browser_server(server):
        return None
    found = hubg.hub_origins(hubg.strings(getattr(event, 'params', None)), hubg.ports(hub_config(ctx)))
    if not found:
        return None
    return refuse('hub-browser', role, '%s reaches the Action Hub\'s own page (%s), where the owner answers as the '
                  'owner: %s This session is %s. Ask the owner through a question:' %
                  (tool, found[0], HUB_OWNER_WHY, who), hub_ask_alternative(ctx))
