"""The guard's rules over an edit's target (kitlib.guard.rules, which re-exports each name here): the
rulings ledger and the git calls that reach it, the machine lock's state, the PM data worktree, the
main checkout, .kit.json, the machine config folder, the gate result folder, generated files, and the
write jail with its Dev Drive bound, with the refusals and alternatives they give."""
import fnmatch
import os
import re
from kitlib.host import agentname
from kitlib import config as kconfig
from kitlib.config import user as kuser
from kitlib.git import repo

from . import commands_git as cg
from . import context
from . import generated
from . import jail
from . import modes
from . import paths
from . import shell as sh
from .rules_base import (ASK, LEDGER_GLOB_CHARS, UNPLACED, agent_session, bare, fwd, is_owner_session, kit_tool,
                         lock_tool, own_worktree, owner_host, quote, refuse, subagent, tag)
from .rules_owner import hub_real, hub_secret_edit, hub_session
from .rules_run import is_root
from .rules_gitcore import (GIT_LONG_VALUES, _canon_long, _opts, clean_dry_run, git_call_judged, restore_index_only,
                            stash_reads)


def delegations_edit(event, ctx, target, role, what, stand_in=False):
    """An edit of the owner's delegation list, or of a folder that holds it (kitlib.guard.delegfile.edit)."""
    from . import delegfile
    return delegfile.edit(event, ctx, target, role, what, stand_in)


def backlog_edit(event, ctx, target, role, what):
    """An edit of a folder store's records (kitlib.guard.backlogrole.raw_edit), or of the backlog's
    audit log (audit_edit)."""
    from . import backlogrole
    return backlogrole.raw_edit(event, ctx, target, role, what) or \
        backlogrole.audit_edit(event, ctx, target, role, what)


# ---------------------------------------------------------------- edits: ledger and main checkout

def stream_free(path):
    """`path` with an NTFS stream suffix (`:name`, `::$DATA`, `:name:$DATA`) dropped from each of
    its parts after the drive: `STANDING.md::$DATA` opens STANDING.md itself."""
    drive, rest = os.path.splitdrive(path)
    parts = re.split(r'([\\/])', rest)
    return drive + ''.join(p.split(':', 1)[0] if i % 2 == 0 else p for i, p in enumerate(parts))


LONG_PREFIX = re.compile(r'[\\/]{2}[?.][\\/]|(?:[A-Za-z]:)?[\\/]\?\?[\\/]')


def unprefixed(path):
    r"""`path` without a Win32 namespace prefix (`\\?\`, `\\.\`, `\??\`, either slash), which
    opens the same file: `\\?\<drive>:\pm\STANDING.md` is `<drive>:\pm\STANDING.md`, and
    `\\?\UNC\host\share` is `\\host\share`. `\??\` is also dropped after a drive
    (`<drive>:\??\<drive>:\pm`): abspath reads it as a
    folder of the current drive, but Windows opens it as the NT name it is, and no folder is
    named `??`. A prefixed name of another form (`\\?\Volume{...}\`) is left whole for realpath,
    which gives the drive name of a file that exists."""
    m = LONG_PREFIX.match(path)
    if not m:
        return path
    rest = path[m.end():]
    if rest[:4].lower() in ('unc\\', 'unc/'):
        return '\\\\' + rest[4:]
    return rest if re.match(r'[A-Za-z]:(?:[\\/]|$)', rest) else path


def file_key(path):
    """`path` as the filesystem opens it, to compare: a namespace prefix dropped (unprefixed),
    absolute, a stream suffix dropped (stream_free), a trailing dot or space dropped (abspath:
    `alpha.md.` is `alpha.md`), then links, junctions and 8.3 short names resolved and case folded
    (jail.real), as lock_state compares; a prefix realpath keeps or gives (a volume GUID name) is
    dropped from that too."""
    return unprefixed(jail.real(os.path.abspath(stream_free(os.path.abspath(unprefixed(path))))))


def ledger_guarded(ctx):
    """[(kind, real path)] of what the ledger rule holds: 'log' for ledger_path, 'rulebook' for
    the rulebooks' folder and 'digest' for the standing digest (kitlib.ledger.files.of_config over
    the machine config, Ctx.ledger_files). Paths are file_key()ed."""
    out = []
    ledger = ctx.user().get('ledger_path')
    if ledger:
        out.append(('log', file_key(ledger)))
    files = ctx.ledger_files()
    if files is not None:
        if files.rules:
            out.append(('rulebook', file_key(files.rules)))
        if files.digest:
            out.append(('digest', file_key(files.digest)))
    return out


def ledger_kind(ctx, target, above=False):
    r"""What of the ledger `target` is: 'log' for the ledger file, 'rulebook' for the rulebooks'
    folder or a `.md` file directly in it, 'digest' for the standing digest, else None. Names
    compare as the filesystem opens them (file_key: case, a stream suffix, a trailing dot or
    space, a namespace prefix, links, junctions and 8.3 names), first as the literal name (an
    edit's path is never a wildcard: `[PM]` is a folder name), then, when it has a wildcard
    character after its namespace prefix (a DOS one too, LEDGER_GLOB_CHARS), as a wildcard that is any of them it can match
    (ledger_glob; the prefix's own `?` is no wildcard). A `\??\` unprefixed leaves (no drive after
    it) is read both ways: as a prefix, and as a `??` wildcard folder (`<drive>:/??/STANDING.md`
    names `<drive>:\pm\STANDING.md`). With `above`, a folder above a held path is that path's
    kind too, and a wildcard that can name such a folder (ledger_glob): removing or moving the
    folder writes what it holds."""
    full = file_key(target)
    for kind, real in ledger_guarded(ctx):
        if full == real or (kind == 'rulebook' and os.path.dirname(full) == real and full.endswith('.md')):
            return kind
    if above:
        for kind, real in ledger_guarded(ctx):
            if jail.inside(real, full):
                return kind
    bare_target = unprefixed(target)
    end = prefix_end(bare_target)
    starts = (end, 0) if end and not re.match(r'[\\/]{2}', bare_target) else (end,)
    for start in starts:
        if any(c in LEDGER_GLOB_CHARS for c in bare_target[start:]):
            kind = ledger_glob(ctx, bare_target, start, above)
            if kind:
                return kind
    return None


DOS_WILDCARDS = str.maketrans('<>"', '***')


def prefix_end(path):
    r"""Where a namespace prefix unprefixed leaves (`\\?\Volume{...}\`) ends in `path`, else 0."""
    m = LONG_PREFIX.match(path)
    return m.end() if m else 0


def ledger_glob(ctx, target, start=0, above=False):
    """ledger_kind for the wildcard path `target`: the literal folder it starts in (file_key), and
    each part after it matched (case folded, a stream suffix dropped, as glob matching reads it)
    against the matching part of a held path below that folder. A file is matched by a wildcard
    of the same depth; the rulebooks' folder also by one a part deeper (a file in it), and every
    wildcard that starts in it is a rulebook (fail closed: `rules/*.txt`); `**` or `..` after the
    wildcard matches every held path below the folder. A part also matches a name it spells
    literally (`[PM]` the folder `[PM]`). A wildcard is searched for from `start` (past a namespace
    prefix, prefix_end, whose `?` is none); a DOS wildcard (LEDGER_GLOB_CHARS) is one too, matched
    as `*`. A part matches the 8.3 short name too (glob_part, short_parts), as FindFirstFile
    matches: `A-LONG~?` names `a-long-folder`. With `above`, a wildcard shallower than a held
    path whose parts each match the folders it passes through names a folder above it
    (ledger_kind's `above`)."""
    first = min(target.index(c, start) for c in LEDGER_GLOB_CHARS if c in target[start:])
    posix = paths.posix()
    cut = target.rfind('/', 0, first) if posix else max(target.rfind('/', 0, first), target.rfind('\\', 0, first))
    base, rest = (target[:cut + 1], target[cut + 1:]) if cut >= 0 else ('.', target)
    b = file_key(base)
    if posix:
        # Linux: `/` alone separates, and each part folds as the held path's own does (paths.fold,
        # by the mount that holds it), so both sides compare the same way
        raw = [x.split(':', 1)[0] for x in rest.split('/') if x and x != '.']
        top = paths.norm(os.path.abspath(base), real=True, fold=False).rstrip('/')
        folded = paths.fold('/'.join([top] + raw)).split('/')
        parts = [x.translate(DOS_WILDCARDS) for x in folded[len(folded) - len(raw):]] if raw else []
    else:
        parts = [x.split(':', 1)[0].lower().translate(DOS_WILDCARDS) for x in re.split(r'[\\/]', rest)
                 if x and x != '.']
    wide = any('**' in x or x == '..' for x in parts)
    for kind, real in ledger_guarded(ctx):
        if kind == 'rulebook' and b == real:
            return kind
        if real == b or not jail.inside(real, b):
            continue
        below = real[len(b.rstrip('\\/')) + 1:].split(os.sep)
        shorts = short_parts(real, len(below))
        deeper = kind == 'rulebook' and len(parts) == len(below) + 1
        shallower = above and len(parts) < len(below)
        if wide or ((len(parts) == len(below) or deeper or shallower) and
                    all(n == x or glob_part(n, s, x) for n, s, x in zip(below, shorts, parts))):
            return kind
    return None


def glob_part(name, short, pattern):
    """Whether the wildcard part `pattern` matches a path part, as FindFirstFile matches it: its
    long name `name` or its 8.3 short name `short` (None when it has none), both case folded."""
    return fnmatch.fnmatchcase(name, pattern) or (short is not None and fnmatch.fnmatchcase(short, pattern))


def short_parts(real, depth):
    """The last `depth` parts of the 8.3 short spelling of the path `real` (GetShortPathNameW),
    case folded, to match a wildcard against as FindFirstFile does. A part that does not exist
    yet (a held file not written) is None, and its nearest existing folder gives the parts above
    it; None for every part when the volume gives none (the long names alone)."""
    path, tail = real, 0
    short = _short_path(path)
    while short is None and tail < depth:
        parent = os.path.dirname(path)
        if not parent or parent == path:
            break
        path, tail = parent, tail + 1
        short = _short_path(path)
    need = depth - tail
    parts = re.split(r'[\\/]', short.lower().rstrip('\\/')) if short else []
    head = parts[-need:] if need > 0 and len(parts) >= need else [None] * need
    return head + [None] * tail


def _short_path(path):
    """GetShortPathNameW of `path`, or None when it does not resolve (a missing path)."""
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(1024)
        n = ctypes.WinDLL('kernel32').GetShortPathNameW(path, buf, len(buf))
    except (ImportError, OSError, AttributeError, ValueError):
        return None
    return buf.value if 0 < n < len(buf) else None


def ledger_git(event, ctx, cmd, inv, cwd, role):
    """git run in a work tree that holds part of the ledger (outside git: run in a folder that
    holds it) whose pathspec reaches that part (ledger_kind's files): rm and mv, restore (not
    --staged alone, and only when every long option is one GIT_LONGS names: `--w` is
    `--worktree` to git, and _canon_long leaves a one-letter body as written), checkout-index, and checkout of paths (after `--`, or every positional word
    without one: git reads a word that names no branch as a path). A pathspec reaches a held path
    as kitlib.guard.delegfile.covers reads it (the path, a folder above it, a wildcard whose
    literal folder is above it; a computed word or unread magic always), or is a `.md` file
    directly in the rulebooks' folder (a wildcard there: its literal folder, that folder). A
    branch name is read as a path too (fail closed: `git checkout pm` from
    the folder above the ledger's). Paths read from elsewhere (git_every_path:
    `--pathspec-from-file`, checkout-index `-a` or `--stdin`) reach every path of the tree.
    In such a tree, for the sessions ledger_wide_binds names, the forms that rewrite the whole
    tree are refused too (ledger_git_tree_form: checkout or switch that discards changes, reset
    --hard, --merge or --keep, a stash that is not a read, a clean that is not a dry run with no
    pathspec or one that reaches a held path); and a git call whose directory the guard cannot
    place (a computed -C, GIT_DIR, --git-dir, --work-tree and the like: gc.blind) is refused in
    any of those writing forms while a ledger is configured, since it can act on the ledger's
    tree (fail closed); a read there passes.
    A subcommand the shell computes (`git $x LEDGER.md`), or the one xargs appends to a bare
    `xargs git`, is unread: a tree form (it can be reset --hard), and in a placed tree its words
    are judged as paths. git that xargs runs takes arguments the guard cannot read: a path
    subcommand's are every path (git_every_path's reading), and checkout, switch, reset or clean
    with them is a tree form. In a placed tree, for the same sessions, git that moves the tree
    to other content is a tree form too (ledger_git_moves: switch, checkout of a branch, merge,
    pull, apply that writes the tree, read-tree -u); a checkout whose word names a held path is
    refused as that path."""
    gc = git_call_judged(inv)
    if gc is None:
        return None
    sub, args = gc.sub, gc.rest
    held = ledger_guarded(ctx)
    if not held and not ctx.user().get('ledger_unread'):
        return None
    from . import delegfile
    xargs = gc.inv is not None and gc.inv.via == 'xargs'
    unread = ('a git subcommand the shell computes' if gc.sub_dyn
              else 'the subcommand xargs appends' if xargs and not sub else None)
    short, longs, pos = _opts(sub, args, wide=True)
    tree_form = unread or ledger_git_tree_form(sub, args, short, longs, xargs)
    asked = ledger_git_unread(event, ctx, cmd, sub, args, short, longs, xargs, tree_form, role)
    d = ledger_git_held(event, ctx, cmd, gc, sub, args, short, longs, pos, xargs, unread, tree_form, held, role) \
        if held else None
    return d or asked                           # a held path's refusal before the unread ask


def ledger_git_held(event, ctx, cmd, gc, sub, args, short, longs, pos, xargs, unread, tree_form, held, role):
    """ledger_git's refusal of the git call `gc` over the held ledger paths `held`, or None."""
    from . import delegfile
    if not gc.dir or gc.blind or (gc.unknown and not gc.sub_dyn):
        if not ledger_wide_binds(event, ctx):
            return None
        form = tree_form or ledger_git_path_form(sub, args, short, longs, held, xargs)
        if not form:
            return None
        kind = held[0][0]
        where = '`%s` (%s, in a place the guard cannot tell: it can be the tree of the ledger)' % (bare(cmd), form)
        if checkout_makes_branch(sub, short, longs) and not tree_form:
            return refuse('ledger', role, UNPLACED_BRANCH_WHY % where, unplaced_branch_instead(gc))
        return refuse('ledger', role, STANDING_WHY[kind] % where, ledger_instead(ctx, kind))
    moves = None if tree_form else ledger_git_moves(sub, args, short, longs)
    tree_form = tree_form or moves
    if sub not in ('rm', 'mv', 'restore', 'checkout-index', 'checkout') and not tree_form:
        return None
    if sub == 'restore' and restore_index_only(short, longs) and not xargs:
        return None                             # the index alone
    place = ctx.place(gc.dir)
    top = place.top if place.kind in (repo.MAIN, repo.LINKED) and place.top else gc.dir
    tree = jail.real(top)
    held = [(kind, real) for kind, real in held if jail.inside(real, tree)]
    if not held:
        return None
    if sub == 'stash' and tree_form:
        pos = delegfile.after_dashes(args) or []    # push's pathspecs after `--`; none: the tree
    specs = sub in ('clean', 'stash') and tree_form and ledger_wide_binds(event, ctx)
    if sub == 'checkout' and moves and held_spec(ctx, pos, gc.dir, top, held):
        tree_form = None                        # a word that names a held path: refused as that path below
    if tree_form and ledger_wide_binds(event, ctx) and (not specs or not pos or xargs or all(
            delegfile.spec_real(ctx, w, gc.dir, top) is False for w in pos)):
        kind = held[0][0]                       # no pathspec, or exclusions alone: the tree
        return refuse('ledger', role, STANDING_WHY[kind] % ('`%s` (%s)' % (bare(cmd), tree_form)),
                      ledger_instead(ctx, kind))
    if sub == 'checkout':
        after = delegfile.after_dashes(args)
        pos = after if after is not None else pos
    elif sub not in ('rm', 'mv', 'restore', 'checkout-index') and not unread:
        if not specs:
            return None                         # a tree form the session is not held to
    every = git_every_path(sub, short, longs) or (
        'the paths xargs appends' if xargs and (unread or sub in ('rm', 'mv', 'restore', 'checkout-index',
                                                                 'checkout')) else None)
    if every:
        kind = held[0][0]
        return refuse('ledger', role, STANDING_WHY[kind] % ('`%s` (%s)' % (bare(cmd), every)),
                      ledger_instead(ctx, kind))
    hit = held_spec(ctx, pos, gc.dir, top, held)
    if hit:
        kind, text = hit
        return refuse('ledger', role, STANDING_WHY[kind] % ('`%s` (%s)' % (bare(cmd), text)),
                      ledger_instead(ctx, kind))
    return None


def ledger_git_unread(event, ctx, cmd, sub, args, short, longs, xargs, tree_form, role):
    """ledger_unread for a git call while a ledger key could not be read, wherever it runs (the
    ledger may be in any tree): a path form (rm, mv, restore but the index alone, checkout-index,
    checkout of paths) in any session, and a form that rewrites or moves the tree
    (ledger_git_tree_form, ledger_git_moves) in the sessions ledger_wide_binds names; else None."""
    if not ctx.user().get('ledger_unread'):
        return None
    moves = None if tree_form else ledger_git_moves(sub, args, short, longs)
    path_form = sub in ('rm', 'mv', 'checkout-index') or (
        sub == 'restore' and (xargs or not restore_index_only(short, longs))) or (sub == 'checkout' and not moves)
    if not path_form and not ((tree_form or moves) and ledger_wide_binds(event, ctx)):
        return None
    return ledger_unread(ctx, role, '`%s` (%s)' % (bare(cmd), tree_form or moves or 'a path form'))


def held_spec(ctx, words, cwd, top, held):
    """(kind, text) for the first of the pathspec Words `words` (read from `cwd` in the tree at
    `top`) that reaches a held path ((kind, real) pairs): the path, a folder above it, a computed
    word, or a `.md` file directly in a held rulebooks' folder; an exclusion names none. Else None."""
    from . import delegfile
    for w in words:
        spec = delegfile.spec_real(ctx, w, cwd, top)
        if spec is False:
            continue                            # an exclusion names nothing to write
        for kind, real in held:
            if spec is True or jail.inside(real, spec) or (
                    kind == 'rulebook' and os.path.dirname(spec) == real and spec.endswith('.md')):
                return kind, w.text
    return None


def ledger_wide_binds(event, ctx):
    """Whether the ledger rule's wider refusals hold the session of `event`: a folder above a held
    path or a wildcard that can name one (ledger_rule's `above`), git that rewrites a tree holding
    the ledger (ledger_git_tree_form), and a writing git call in a place the guard cannot tell.
    The one place their sessions are chosen: a marked session but the PM's (marked_not_pm), so
    a subagent, a `plain` or other agent's mark and an unknown mark are bound (fail closed); the
    owner's unmarked session and the PM's pass. Whether to widen them is the owner's open
    question: `return True` here binds every session, as the rest of the ledger rule does (only
    ledger.py writes the ledger); that change is made here alone."""
    return marked_not_pm(event, ctx)


def marked_not_pm(event, ctx):
    """True for a marked session but the PM's: a subagent, or a main session whose launcher mark
    (without the kit plugin's prefix, case ignored) is anything but the owner's and `pm`, an
    unknown mark included, and an unmarked chat on a host that never runs the owner's session
    (owner_host: Codex). False for the owner's unmarked session and the PM's."""
    if subagent(event):
        return True
    who = agentname.bare_mark(str(ctx.session_role(event) or context.OWNER)).lower()
    return who != 'pm' and (who != context.OWNER or not owner_host(ctx))


def ledger_git_tree_form(sub, args, short, longs, xargs=False):
    """The option or form of a git call (sub, its argument Words, and _opts(wide=True)) that
    rewrites the work tree as a whole, discarding what is there: checkout or switch with -f or
    --force (switch's --discard-changes; any abbreviation git could read as either, an
    ambiguous one too: fail closed), reset --hard, --merge or --keep (delegfile.GIT_RESETS), a
    stash that is not a read (stash_reads), a clean that is not a dry run (clean_dry_run). None
    for any other call. ledger_git judges a clean's pathspecs, and a stash's after `--`, as a
    path form's: one that misses the ledger passes; none, or exclusions alone, is the tree.
    Checkout, switch or reset with a word the shell computes before `--` (not the value of an
    option that names a branch, commands_git.GIT_SUB_VALUES) can be any of these (`git reset "$m"`),
    and so can checkout, switch, reset or clean that xargs runs (`xargs`: it appends words)."""
    from . import delegfile
    if xargs and sub in ('checkout', 'switch', 'reset', 'clean'):
        return 'arguments xargs appends'
    if sub in ('checkout', 'switch', 'reset'):
        dyn = computed_option_word(sub, args)
        if dyn:
            return 'a word the shell computes (%s)' % dyn
    if sub in ('checkout', 'switch'):
        names = ('force', 'discard-changes') if sub == 'switch' else ('force',)
        if 'f' in short:
            return '-f'
        for n in sorted(longs):
            if n and any(name.startswith(n) for name in names):
                return '--' + n
        return None
    if sub == 'reset':
        hit = sorted(delegfile.GIT_RESETS & longs)
        return '--' + hit[0] if hit else None
    if sub == 'stash':
        return None if stash_reads(args) else 'stash'
    if sub == 'clean':
        return None if clean_dry_run(args) else 'clean'
    return None


def computed_option_word(sub, args):
    """The text of the first word the shell computes among a git call's arguments `args` before
    a literal `--`, skipping the value of an option commands_git.GIT_SUB_VALUES names for `sub` (a
    branch name: `checkout -b "$b"`), else None."""
    values = cg.GIT_SUB_VALUES.get(sub, ())
    k = 0
    while k < len(args):
        w = args[k]
        if w.text == '--' and not w.dyn:
            return None
        if w.dyn:
            return w.text or '?'
        k += 2 if w.text in values else 1
    return None


def ledger_git_moves(sub, args, short, longs):
    """The form of a git call (sub, its argument Words, and _opts(wide=True)) that moves a placed
    work tree to other content, which can overwrite or take away the ledger's files there:
    switch (but `-c`/`-C <branch>` with no start, which keeps the tree as it is), checkout of a
    branch or commit as switch (checkout_moves), merge, pull,
    apply unless it only reads the patch or writes the index alone (--check, --stat, --numstat,
    --summary, --cached; any other option, -3 or an abbreviation fails closed), and read-tree
    -u. None for any other call. ledger_git judges it only where it can place the tree:
    unplaced, `git switch main` is a plain switch the guard cannot tell from one in another
    repository."""
    if sub == 'switch' and switch_creates_here(args):
        return None
    if sub == 'checkout':
        return checkout_moves(args, longs)
    if sub in ('switch', 'merge', 'pull'):
        return sub
    if sub == 'apply':
        reads = {'check', 'stat', 'numstat', 'summary'}
        if longs <= reads | {'cached'} and longs & (reads | {'cached'}) and '3' not in short:
            return None
        return 'apply'
    if sub == 'read-tree' and 'u' in short:
        return 'read-tree -u'
    return None


def switch_creates_here(args):
    """Whether git switch with arguments `args` only creates a branch at HEAD: exactly `-c`,
    `-C`, `--create` or `--force-create` and its literal name, nothing else (a start point, any
    other option, a computed word: False)."""
    return (len(args) == 2 and not any(w.dyn for w in args)
            and args[0].text in ('-c', '-C', '--create', '--force-create') and not args[1].text.startswith('-'))


def checkout_moves(args, longs):
    """The form of a git checkout (its argument Words, and _opts(wide=True)'s long names) that
    moves the tree as switch does, else None: `--orphan` (any abbreviation of two letters or
    more), whatever follows it, or exactly one positional word before any `--` once the values
    of -b, -B and --conflict (any abbreviation) are left out (`checkout main`, `-`, `-b new
    main`, `-t origin/x`; a word that names no branch fails closed). A `--` with nothing after
    it is no `--` (`checkout main --` moves the tree). None with paths after `--` or two or more
    positional words (a tree-ish and paths, judged as paths), and with none (`checkout`,
    `-b new`: a branch at HEAD keeps the tree, as switch -c does)."""
    from . import delegfile
    if any(len(n) > 1 and 'orphan'.startswith(n) for n in longs):
        return 'checkout --orphan'
    if delegfile.after_dashes(args):
        return None
    pos, k = 0, 0
    while k < len(args):
        t = args[k].text
        k += 1
        if t == '--':
            break
        if t.startswith('--'):
            body, eq, _v = t[2:].partition('=')
            names = _canon_long('checkout', body)
            k += bool(not eq and ((len(names) == 1 and names[0] in GIT_LONG_VALUES)
                                  or (body and 'conflict'.startswith(body))))   # --conflict <style>, --conf too
            continue
        if not t.startswith('-') or t == '-':
            pos += 1
            continue
        body = t[1:]
        for j, c in enumerate(body):
            if c in 'bB':
                k += not body[j + 1:]           # -b <name>; -bname carries it
                break
    return 'checkout' if pos == 1 else None


def ledger_git_path_form(sub, args, short, longs, held=(), xargs=False):
    """The subcommand of a git call that writes the paths it names, where the guard cannot place
    the tree: rm, mv, restore (not the index alone), checkout-index, and a checkout that can
    write paths (checkout_branch_only is False). With `xargs` (git that xargs runs, appending
    words the guard cannot read) a checkout or a restore always can. None for any other call."""
    if sub in ('rm', 'mv', 'checkout-index'):
        return sub
    if sub == 'checkout' and (xargs or not checkout_branch_only(args, short, longs, held)):
        return sub
    if sub == 'restore' and (xargs or not restore_index_only(short, longs)):
        return sub
    return None


def checkout_branch_only(args, short, longs, held=()):
    """Whether a checkout (its argument Words, and _opts(wide=True)) can only switch to a branch
    or a commit, the reading where the guard cannot place the tree; anything else fails closed.
    True for no positional word, or exactly one that is a plain name: not `.`, not computed, no
    wildcard or pathspec magic, no path separator (so `kit/x` stays refused: without the tree the
    guard cannot tell a branch from a folder), no `~` short name, and not a part of a held path
    (`LEDGER.md`, its folders, their 8.3 short names: git reads a word that names no branch as
    a path), nor a `.md` name while a rulebooks' folder is held (a rulebook in it). False with `--`, paths read from elsewhere (git_every_path), -p or --patch (any
    abbreviation git could read as it), or two or more positional words."""
    from . import delegfile
    if delegfile.after_dashes(args) is not None or git_every_path('checkout', short, longs):
        return False
    if 'p' in short or any(n and 'patch'.startswith(n) for n in longs):
        return False
    pos = _opts('checkout', args, wide=True)[2]
    if not pos:
        return True
    if len(pos) > 1:
        return False
    w = pos[0]
    t = w.text
    if (getattr(w, 'dyn', False) or not t or t in ('.', '..') or t.startswith(':')
            or any(c in t for c in '*?[]\\/~')):
        return False
    name = t.rstrip('. ').lower()
    if name.endswith('.md') and any(kind == 'rulebook' for kind, _real in held):
        return False                            # a rulebook: a `.md` file in the held folder
    return name not in held_names(held)


UNPLACED_BRANCH_WHY = ('%s creates a branch and checks out its start, which can rewrite the files of the rulings '
                       'ledger or a rulebook there; only ledger.py writes those, so the guard refuses it where it '
                       'cannot see the tree.')
UNPLACED_BRANCH_INSTEAD = ('run it with a literal `git -C <worktree>` so the guard can place the tree, or create '
                           'the branch with `git switch -c <branch> <start>`.')
BLIND_BRANCH_INSTEAD = ('drop %s (a -C does not undo it, so the guard cannot place the tree), then run it with a '
                        'literal `git -C <worktree>`, or create the branch with `git -C <worktree> switch -c <branch> '
                        '<start>`.')


def unplaced_branch_instead(gc):
    """The Instead of an unplaced checkout that creates a branch (checkout_makes_branch): with
    what points git at a place the text does not show (gc.blind: GIT_DIR, --git-dir and the like),
    drop that first, since a -C cannot undo it; else UNPLACED_BRANCH_INSTEAD."""
    if gc.blind:
        return BLIND_BRANCH_INSTEAD % gc.blind
    return UNPLACED_BRANCH_INSTEAD


def checkout_makes_branch(sub, short, longs):
    """Whether a checkout creates a branch (-b, -B, --orphan, any abbreviation of two letters or
    more git reads as it: `--orph`): its refusal names that, and its Instead the forms the guard
    can judge, not the ledger tool."""
    return sub == 'checkout' and bool('b' in short or 'B' in short or any(
        len(n) > 1 and 'orphan'.startswith(n) for n in longs))


def held_names(held):
    """The lower-case names of every part of each held path ((kind, real) pairs) and of its 8.3
    short name, trailing dots and spaces dropped as Windows drops them."""
    out = set()
    for _kind, real in held:
        for p in (real, _short_path(real)):
            if p:
                out.update(n.rstrip('. ').lower() for n in re.split(r'[\\/]', p) if n)
    return out


def git_every_path(sub, short, longs):
    """The option of a ledger_git call that takes its paths from where the guard cannot read
    them, so they can be every path, else None: `--pathspec-from-file` (rm, restore, checkout; any
    abbreviation git could read as it), and checkout-index's `-a`/`--all` and `--stdin`."""
    if sub in ('rm', 'restore', 'checkout'):
        for n in sorted(longs):
            if len(n) > 1 and 'pathspec-from-file'.startswith(n):
                return '--' + n
    if sub == 'checkout-index':
        if 'a' in short:
            return '-a'
        for n in sorted(longs):
            if n and ('all'.startswith(n) or 'stdin'.startswith(n)):
                return '--' + n
    return None


def ledger_instead(ctx, kind):
    if kind == 'log':
        return ('python %s add --repo <repo> --prov <prov> --q "<question>" --a "<answer>" '
                '--decides "<what it decides>" --source "<source>"' % kit_tool(ctx, 'ledger'))
    return 'python %s conflicts --repo <repo> --topic "<topic>" --key <key> --text "<rule>"' % kit_tool(ctx, 'ledger')


STANDING_WHY = {
    'log': '%s writes the rulings ledger, which only ledger.py writes. Add the entry with the kit\'s '
           'tools/ledger.py add (docs/ledger.md).',
    'rulebook': '%s writes a rulebook, which only ledger.py writes, each rule change paired with a log entry: read '
                'the proposed rule against the others with conflicts, then add it with add --rule KEY --topic '
                'TOPIC --checked HASH, or remove one with rule-drop (docs/ledger.md "Rulebooks").',
    'digest': '%s writes the standing digest, which the rulebooks replace: it is read, never written, until it '
              'goes, and a standing rule goes in a rulebook through ledger.py: conflicts, then add --rule '
              '(docs/ledger.md "Rulebooks").',
}


def ledger_rule(event, ctx, target, role, what, stand_in=False, unread=True):
    """The ledger rule over an edit of `target`: what of the ledger it is (ledger_kind), and, for
    the sessions ledger_wide_binds names, a folder above a held path or a wildcard that can name
    one (`above`), unless the target is a stand-in for what the guard cannot read (edit_target's
    `stand_in`: the folder python runs in), as delegfile.reaches reads folders. A broken machine
    config refuses every subagent write; one with a ledger key the guard could not read asks the
    owner about every main-session write a held path does not refuse (ledger_unread; `unread`
    False leaves that to the caller, which judges it after its other rules: edit_target)."""
    user = ctx.user()
    broken = user.get('broken')
    if broken and role == 'subagent':
        return refuse('ledger', role, '%s: the machine config %s is broken (%s), so the '
                      'guard cannot tell where the rulings ledger is and refuses every subagent write. The owner '
                      'or the PM session fixes it.' % (what, ctx.config_location().display(), broken),
                      'python -m json.tool %s' % quote(ctx.config_location().path))
    kind = ledger_kind(ctx, target, above=not stand_in and ledger_wide_binds(event, ctx)) if target else None
    if kind is None:
        return ledger_unread(ctx, role, what) if unread else None
    return refuse('ledger', role, STANDING_WHY[kind] % ledger_what(what), ledger_instead(ctx, kind))


def ledger_unread(ctx, role, what):
    """While the machine config holds a ledger key the guard could not read (Ctx.user's
    'ledger_unread': the ledger may be at any path), the write `what`: asked of the owner in a
    main session, denied in a subagent's; else None. No kit tool's ledger write is asked: ledger.py
    reads the strict config and refuses while it is broken."""
    why = ctx.user().get('ledger_unread')
    if not why:
        return None
    loc = ctx.config_location()
    if role == 'subagent':
        return refuse('ledger', role, '%s: the machine config %s is broken (%s), so the guard cannot tell where '
                      'the rulings ledger is and refuses every subagent write. The owner or the PM session fixes '
                      'it.' % (what, loc.display(), why), 'python -m json.tool %s' % quote(loc.path))
    return refuse('ledger', role, '%s: the machine config %s is broken (%s), so the guard cannot tell where the '
                  'rulings ledger is, so every write of this session needs the owner\'s yes until it is fixed (only '
                  'ledger.py writes the ledger, and it refuses while the config is broken). To read the machine '
                  'config:' % (what, loc.display(), why), 'python -m json.tool %s' % quote(loc.path), kind=ASK)


def ledger_what(what):
    """`what` for STANDING_WHY, which says what is written: "`cmd` writes X" becomes "`cmd` (X)",
    so the reason does not say "writes" twice; any other `what` is kept."""
    i = what.rfind('` writes ')
    if what.startswith('`') and i > 0:
        return '%s (%s)' % (what[:i + 1], what[i + len('` writes '):])
    return what


def lock_state_dir(ctx):
    """The machine lock's state folder: <home>/.claude/machine-lock (kitlib.lock.store.real_root).
    It may be a junction to the store on another volume; lock_state judges its target, by either
    spelling, through jail.real (tests/guard/test_lock_state_junction.py)."""
    return os.path.join(ctx.home, '.claude', 'machine-lock')


def lock_state(event, ctx, target, role, what):
    """An agent session's edit of a file in the machine lock's state folder: the lock tool alone
    writes it, under its own mutex."""
    if not target or not agent_session(event, ctx):
        return None
    # resolved paths (jail.real: links, junctions and 8.3 names followed), so a write that reaches the
    # state folder through a junction is still this rule's, whatever root the jail would open
    top, here_ = jail.real(lock_state_dir(ctx)), jail.real(target)
    if not jail.inside(here_, top):
        return None
    return refuse('lock-state', role, '%s edits the machine lock\'s state, which only the lock tool writes, under its own '
                  'mutex. Ask the owner (through the PM) when the state looks wrong. To see it:' % what,
                  'python %s status' % lock_tool(ctx))


WT_ADD = 'git -C %s worktree add .claude/worktrees/<slug> -b <branch>'
PM_DATA_WHY = ('Agents do not work in the PM data worktree %s (a worktree made from it would read the kit\'s own '
               '.kit.json, not its rules); the PM changes it, so ask the coordinator to take the change to the PM. '
               'To see its state:')


def new_worktree(ctx, top):
    """(sentence or None, command): the worktree to make of the main checkout `top`; for the PM
    data worktree no worktree is offered (ctx.is_pm_data): a sentence saying so, and a read. The
    `<main checkout>` placeholder (git_main_rules, where git cannot place the checkout) is text,
    never a path: placed, it would resolve against the folder the hook runs in."""
    if top and os.path.isabs(top) and ctx.is_pm_data(top):
        return PM_DATA_WHY % quote(top), 'git -C %s status' % quote(top)
    return None, WT_ADD % (quote(top) if top else '"<main checkout>"')


def pm_data_unknown(ctx, target, cwd, role, what):
    """A subagent write while the guard cannot tell the PM data worktree (ctx.pm_data_error):
    denied, as for a broken machine config (fail closed)."""
    if role != 'subagent':
        return None
    why = ctx.pm_data_error(target) or ctx.pm_data_error(cwd)
    if not why:
        return None
    return refuse('pm-data', role, '%s: the guard cannot tell which worktree is the PM data worktree (%s), so it '
                  'refuses every subagent write. The owner or the PM session fixes it. To read the machine config:'
                  % (what, why), 'python -m json.tool %s' % quote(ctx.config_location().path))


def edit_target(event, ctx, target, cwd, role, what, unknown_why=None, rewrite=None, run=None, redirect=False,
                shell=None, item=False, stand_in=False):
    """Deny an edit of `target` in a main checkout (or a place git cannot tell) by a role that
    may not edit one, and an agent session's write outside its roots (write_jail). `rewrite(path)`
    gives the same edit aimed at `path` (the alternative); `run` the command to rerun from the
    worktree when it writes into the directory it runs in; `redirect`, a shell redirect's target:
    output, so the alternative with no worktree of the session's own is the temp folder; `shell`,
    the dialect of the line `run` is on (the cd line of worktree_alternative); `item`, a target the
    guard cannot place that is a provider item on a line of more than plain calls (ps_provider_word);
    `stand_in`, a target that stands for what the guard cannot read (the folder python runs in, the
    pipeline's paths), which counts as the owner's delegation list only when it is that file
    (delegfile.edit)."""
    d = ledger_rule(event, ctx, target, role, what, stand_in, unread=False) or pm_data_unknown(ctx, target, cwd, role, what) or \
        lock_state(event, ctx, target, role, what) or hub_secret_edit(event, ctx, target, role, what) or \
        delegations_edit(event, ctx, target, role, what, stand_in) or \
        machine_config(event, ctx, target, role, what) or kit_json(event, ctx, target, role, what) or \
        gate_result(event, ctx, target, role, what) or \
        generated_file(ctx, target, role, what) or backlog_edit(event, ctx, target, role, what)
    if d:
        return d
    return main_checkout(event, ctx, target, cwd, role, what, unknown_why, rewrite, run, redirect, shell, item) or \
        write_jail(event, ctx, target, cwd, role, what, rewrite, run) or \
        ledger_unread(ctx, role, what)          # last: any refusal above wins over the unread ask


# the rules a write the guard cannot place would otherwise be refused under (unplaced())
PLACE_RULES = frozenset(['main-checkout', 'write-jail', 'git-main-history', 'git-restore', 'git-checkout',
                         'git-reset-hard', 'git-clean', 'git-stash', 'git-bulk-add', 'git-commit-all',
                         'dev-drive'])


def unplaced(d, role):
    """The refusal `d` of a write the guard cannot place, made again under the rule unplaced (same
    reason, alternative and verdict): None when that rule's mode is warn or off. A Decision of
    any rule outside PLACE_RULES (a protected file, the PM data) is returned as it is."""
    if d is None or d.rule not in PLACE_RULES:
        return d
    tail = ' Instead: %s %s' % (d.instead, tag(d.rule, role))
    what = d.reason[:-len(tail)] if d.reason.endswith(tail) else d.reason
    return refuse(UNPLACED, role, what, d.instead, kind=d.verdict)


def in_git_dir(target):
    """True when `target`, or a folder it is in, is named .git."""
    return '.git' in paths.norm(target).replace('/', '\\').split('\\')


GATE_RESULT = 'kit-gate-result'                 # gate.result.DIR_NAME


def gate_result(event, ctx, target, role, what):
    """An agent session's literal write into a repository's gate result folder
    (`<git common dir>/kit-gate-result/`, gate.result): the records there tell a waiting
    session how a gate or a landing ended, and only the gate writes them. The folder is matched by
    name under a git directory (a folder named .git, or one holding HEAD and objects)."""
    if not target or not agent_session(event, ctx):
        return None
    for p in (target, jail.real(target)):
        parts = paths.norm(p, fold=False).replace('/', '\\').split('\\')
        for k in range(1, len(parts)):
            if parts[k].lower() != GATE_RESULT:
                continue
            gitdir = ('/' if paths.posix() else '\\').join(parts[:k])
            if parts[k - 1].lower() == '.git' or (os.path.isfile(os.path.join(gitdir, 'HEAD')) and
                                                  os.path.isdir(os.path.join(gitdir, 'objects'))):
                return refuse('gate-result', role, '%s: %s is in the gate result folder %s, which only the '
                              'gate writes (gate.result); a record written by hand would tell a waiting '
                              'session a gate or a landing ended as it did not. To wait for a result:'
                              % (what, fwd(target), fwd(os.path.join(gitdir, parts[k]))),
                              'python %s wait-result <branch> --tip <sha>' % kit_tool(ctx, 'gate'))
    return None


# The refusal of a provider item the guard reads as a file only because of the line around it
# (ps_provider_word): it says so in one sentence, and its Instead runs the whole line from the
# worktree (whole_line)
PROVIDER_ITEM_WHY = ('%s, an item on a drive that holds no files, which the guard skips only on a line of plain '
                     'calls with literal words, so on this line it is a file the guard cannot place; run the whole '
                     'line from your worktree:')


def main_checkout(event, ctx, target, cwd, role, what, unknown_why=None, rewrite=None, run=None, redirect=False,
                  shell=None, item=False):
    # a target it cannot place may be in any checkout, so both the kit defaults and the roles of
    # the repository the call runs in must let the role edit one: the defaults alone would let a
    # role that repository keeps out of its main checkout write anywhere, and the repository's
    # alone a role it lets into its own main checkout (a lander) write into any other
    may = kconfig.permissions(event, kconfig.roles(ctx.config(target) if target else None)).get('edit_main_checkout')
    if may and target is None and cwd:
        may = kconfig.permissions(event, kconfig.roles(ctx.config(cwd))).get('edit_main_checkout')
    if may:
        return None
    main_top = ctx.main_top(cwd) if cwd else None
    no_wt, new_wt = new_worktree(ctx, main_top)
    if target is None:
        mine = own_worktree(ctx, cwd)
        if item and run and main_top and not no_wt:
            return refuse(UNPLACED, role, PROVIDER_ITEM_WHY % what,
                          cd_line(fwd(mine) if mine else '<worktree>', run, shell or sh.PS))
        only = ''
        if rewrite is not None and (mine or redirect):
            instead = rewrite(fwd(mine or ctx.tempdir) + '/<file>')
        elif no_wt or not main_top:
            instead = new_wt
        else:
            # from the main checkout the session's worktree is unknown, not missing (only_new)
            only = only_new(new_wt)
            instead = rewrite('<worktree>/<file>') if rewrite is not None else \
                'git -C %s worktree list' % quote(main_top)
        return refuse(UNPLACED, role, '%s: the guard cannot tell where it writes (%s). Name a literal path '
                      'inside your worktree%s (the Edit or Write tool for a file)%s.%s' %
                      (what, unknown_why or 'unknown path', ' or, for output, the temp folder' if redirect else '',
                       only, '' if rewrite is not None or not only else ' To see the worktrees:'),
                      instead)
    place = ctx.place(target)
    if place.kind not in (repo.MAIN, repo.UNKNOWN):
        return None
    if place.kind == repo.UNKNOWN:
        mine = own_worktree(ctx, cwd)
        name, only, see = os.path.basename(target), '', ''
        if mine and rewrite is not None:
            instead = rewrite(fwd(os.path.join(mine, name)))
        elif no_wt or not main_top:
            instead = new_wt
        else:
            # a new worktree is never the Instead: from the main checkout only in a clause (only_new)
            only = '' if mine else only_new(new_wt)
            if rewrite is not None:
                instead = rewrite('<worktree>/' + name)
            else:
                instead, see = 'git -C %s worktree list' % quote(main_top), ' To see the worktrees:'
        # a git directory is placed by its name: git answers no work tree there, and its config,
        # hooks and refs are the repository's own
        rule = 'main-checkout' if in_git_dir(target) else UNPLACED
        return refuse(rule, role, '%s: git cannot tell whether %s is in a main checkout (%s); '
                      'this role may not edit one. Write inside a path git reports as a linked worktree%s.%s' %
                      (what, target, place.detail, only, see), instead)
    what2, instead = worktree_alternative(ctx, target, place.top, cwd, rewrite, run, redirect, shell)
    return refuse('main-checkout', role, '%s is in the main checkout %s; this role may not edit a main '
                  'checkout. %s' % (what, place.top, what2), instead)


KIT_JSON = '.kit.json'


def kit_json(event, ctx, target, role, what):
    """An agent session's edit of a .kit.json in a checkout (or a place git cannot tell): the
    file names the session's own write roots (guard.data_roots) and is the owner's."""
    if not target or not agent_session(event, ctx):
        return None
    if KIT_JSON not in (os.path.basename(target).lower(), os.path.basename(jail.real(target))):
        return None
    if ctx.place(target).kind == repo.NONE:
        return None                             # a file of that name outside git configures nothing
    return refuse('kit-json', role, '%s: a .kit.json sets the repository\'s rules, the folders agent sessions may '
                  'write among them (guard.data_roots), and only the owner changes it (owner.paths). Ask the '
                  'coordinator to take the change to the owner. To read it:' % what,
                  'python -m json.tool %s' % quote(target))


MACHINE_CONFIG = 'machine-config'
MACHINE_CONFIG_WHY = ('a .pm-kit folder at the top of a checkout is the machine config folder the PM repository '
                      'carries (docs/machine-setup.md): its config sets the guard\'s rules for every session on this '
                      'machine and its hub folder holds the hub\'s tokens, so only the owner changes it.')


def machine_config_folder(ctx, target):
    """The machine config folder the write target `target` is or lies in, as written, or None:
    the PM repository's, <pointer>/.pm-kit (jail.pointer_config_dir, compared as jail.real gives
    both), else a folder named .pm-kit (kitlib.config.user.DIR_NAME, case ignored) at the top of
    the checkout git places the target in (Ctx.place: a main checkout or a linked worktree),
    whatever the pointer says. A .pm-kit folder deeper in a checkout, a folder that only holds
    one, and one outside git are not: the home folders are the write jail's (jail.touches_config),
    and a delete of a folder that holds the owner's delegation list is that rule's."""
    t = hub_real(target)
    if t is None:
        return None
    folder = jail.pointer_config_dir(ctx)
    r = hub_real(folder) if folder else None
    if r and jail.inside(t, r):
        return folder
    place = ctx.place(target)
    if place.kind not in (repo.MAIN, repo.LINKED) or not place.top:
        return None
    top = hub_real(place.top)
    if top is None or t == top or not jail.inside(t, top):
        return None
    first = t[len(top.rstrip('\\/')) + 1:].split(os.sep)[0]
    if first.lower() != kuser.DIR_NAME.lower():
        return None
    return os.path.join(place.top, kuser.DIR_NAME)


def machine_config(event, ctx, target, role, what):
    """machine-config: an edit of a file in the PM repository's machine config folder, or in a
    .pm-kit folder at the top of a checkout (machine_config_folder), in any session but the
    owner's own. The hub's token folder in it is hub-secrets' and the owner's delegation list
    its own rule's, both checked first (edit_target)."""
    if not target or is_owner_session(event, ctx):
        return None
    folder = machine_config_folder(ctx, target)
    if folder is None:
        return None
    return machine_config_refusal(event, ctx, role, '%s is in %s' % (what, quote(folder)), folder)


def machine_config_refusal(event, ctx, role, what, folder):
    """The machine-config refusal of `what`, a change in the machine config folder `folder`
    (machine_config; kitlib.guard.delegfile's wildcard and git checks)."""
    return refuse(MACHINE_CONFIG, role, '%s: %s This session is %s. Ask the owner (through the PM) to make the '
                  'change. To list the folder:' % (what, MACHINE_CONFIG_WHY, hub_session(event, ctx)),
                  'Get-ChildItem -Force %s' % quote(folder))


def generated_file(ctx, target, role, what):
    """An edit of a file the kit's renderer writes, in a kit checkout (kitlib.guard.generated):
    the next render overwrites it and the drift check fails it, so the source is edited and the
    file rendered. The renderer runs as a process, which no edit rule judges."""
    if not target:
        return None
    top = ctx.place(target).top
    if not top:
        return None
    try:
        rel = os.path.relpath(target, top)
    except ValueError:                          # another drive
        return None
    src = generated.source(top, rel)
    if src is None:
        return None
    return refuse('generated-file', role, '%s: %s is generated from %s by tools/render_content.py; a hand edit '
                  'is overwritten by the next render and fails the drift check. Edit %s, then render:'
                  % (what, rel.replace('\\', '/'), src, src),
                  'python %s --write' % quote(os.path.join(top, 'tools', 'render_content.py')))


def session_mark(event, ctx):
    """The launcher mark of a main session without the kit plugin's prefix (guard_mark_roots looks
    it up), or None for a subagent and the owner's unmarked session."""
    if subagent(event):
        return None
    who = agentname.bare_mark(ctx.session_role(event))
    return None if who in (context.OWNER, context.SUBAGENT) else who


def jail_roots(event, ctx, cwd):
    return jail_view(event, ctx, cwd)[0]


# The outer bound on the write jail (docs/guard.md "Write jail", "Outer bound"): an agent session's
# write the roots allow is refused when it is off the Dev Drive (the machine config's dev_drive).
DEV_DRIVE = 'dev-drive'


def jail_view(event, ctx, cwd):
    """(roots, dev, dropped) for an agent session running in `cwd`: its write roots (jail.roots),
    the Dev Drive's root while the outer bound judges (rule dev-drive not off; None when dev_drive
    is unset) and, while it denies, the data and mark roots dropped for lying off the Dev Drive,
    each said as a warning naming the entry and its path."""
    m = modes.mode(DEV_DRIVE)
    dev = None if m == modes.OFF else jail.dev_root(ctx)
    dropped = []
    rs = jail.roots(ctx, cwd, lambda p: is_root(p, None, ctx), session_mark(event, ctx),
                    dev=dev if m == modes.DENY else None, dropped=dropped)
    for r in dropped:
        modes.note('the %s root %s is not on the Dev Drive (%s), so it is no write root: an agent session writes only '
                   'on the Dev Drive. The owner moves the folder onto the Dev Drive and the entry with it (%s).'
                   % (r.kind, quote(r.path), fwd(dev),
                      '.kit.json guard.data_roots' if r.kind == jail.DATA else 'the machine config\'s guard_mark_roots'))
    return rs, dev, dropped


JAIL_ASK = 'Ask the coordinator (a coordinator asks the PM) when the work needs a folder outside them.'


def jail_aim(event, ctx, cwd, dest, rewrite):
    """The folder a rewrite names in place of the jail's destination `dest`, or None: when `dest`
    is a main checkout this role may not edit (main-checkout refuses the same write there), its
    own worktree of it, <worktree> when the call shows none."""
    if dest is None or rewrite is None:
        return None
    place = ctx.place(dest.path)
    try:
        barred = place.kind == repo.MAIN and not kconfig.permissions(
            event, kconfig.roles(ctx.config(place.top or dest.path))).get('edit_main_checkout')
    except Exception:  # noqa: BLE001 - the advice only: a config that does not read leaves it as it was
        barred = False
    return (own_worktree(ctx, cwd, place.top) or '<worktree>') if barred else None


def dev_drive_bound(event, ctx, cwd, rs, dev, target, role, what, rewrite=None, run=None):
    """The outer bound: `target`, which the jail's roots allow (or a dropped root holds), refused
    when it is not on the Dev Drive `dev` (jail.on_drive: links and junctions resolved). None when
    `dev` is None (dev_drive unset, or the rule off) or the target is on it. The alternative aims
    at the session's own checkout, else temp, on the Dev Drive (jail.destination)."""
    if not dev or not target or jail.on_drive(target, dev):
        return None
    why = ('%s: %s is not on the Dev Drive (%s): an agent session writes only there, whatever folder it may '
           'otherwise write. %s' % (what, fwd(target), fwd(dev), JAIL_ASK))
    dest = jail.destination(rs, dev)
    if dest is None:
        return refuse(DEV_DRIVE, role, why + ' To see where this session runs:', 'pwd')
    if rewrite is not None:
        name = os.path.basename(target.rstrip('\\/')) or 'out'
        there = jail_aim(event, ctx, cwd, dest, rewrite) or dest.path
        return refuse(DEV_DRIVE, role, why + ' The same write in %s:' % fwd(there),
                      rewrite(fwd(there) + '/' + name if there.startswith('<') else fwd(os.path.join(there, name))))
    return refuse(DEV_DRIVE, role, why + ' Run it with %s as the working directory:' % quote(dest.path),
                  run or 'cd %s' % quote(dest.path))


def in_dropped(target, dropped, dev):
    """True when `target` is off the Dev Drive `dev` and lies in a root jail_view dropped for lying
    off it. A dropped root that holds the Dev Drive (<drive>/ around <drive>/dev) opens nothing on it: a
    target there is judged by the other roots alone."""
    if not dropped or jail.on_drive(target, dev):
        return False
    t = jail.real(target)
    return any(jail.inside(t, r.real) for r in dropped)


def write_jail(event, ctx, target, cwd, role, what, rewrite=None, run=None):
    """An agent session's write outside its roots (kitlib.guard.jail): its checkout, its
    repository's other worktrees (jail.sibling), temp and the .kit.json data roots, judged by the
    resolved path; then, last, the outer bound (dev_drive_bound) on a write they allow."""
    if not target or not agent_session(event, ctx):
        return None
    rs, dev, dropped = jail_view(event, ctx, cwd)
    verdict, root = jail.judge(target, rs)
    if verdict is None or (verdict == jail.OUTSIDE and (jail.sibling(ctx, target, cwd) or in_dropped(target, dropped, dev))):
        return dev_drive_bound(event, ctx, cwd, rs, dev, target, role, what, rewrite, run)
    aim = jail_aim(event, ctx, cwd, jail.destination(rs), rewrite)
    return jail_refusal(role, rs, verdict, root, what, target, rewrite, run, aim)


def jail_why(rs, verdict, root, what, target):
    """The sentence a write-jail refusal of `target` opens with, for its verdict."""
    if verdict == jail.ADD_ONLY:
        return ('%s: %s already exists in %s, an add-only data folder: an agent session creates new files there and '
                'never overwrites, moves or deletes one. %s' % (what, fwd(target), fwd(root.path), JAIL_ASK))
    if verdict == jail.READ_ONLY:
        return ('%s: %s is in %s, a data folder that is read-only for agent sessions (.kit.json guard.data_roots). %s'
                % (what, fwd(target), fwd(root.path), JAIL_ASK))
    return ('%s: %s is outside the folders an agent session may write: %s. %s'
            % (what, fwd(target), jail.describe(rs), JAIL_ASK))


def jail_refusal(role, rs, verdict, root, what, target, rewrite=None, run=None, aim=None):
    """The write-jail refusal; `aim`, when given, is the folder the rewrite names in place of the
    jail's destination (write_jail: a main checkout the role may not edit)."""
    dest = jail.destination(rs)
    why = jail_why(rs, verdict, root, what, target)
    if verdict == jail.ADD_ONLY:
        if rewrite is not None:
            folder = target if os.path.isdir(target) else os.path.dirname(target)
            return refuse('write-jail', role, why + ' To write a new file:',
                          rewrite(fwd(folder) + '/<new file name>'))
        return refuse('write-jail', role, why + ' To see the folder\'s files:', 'ls %s' % quote(root.path))
    if dest is None:
        return refuse('write-jail', role, why + ' To see where this session runs:', 'pwd')
    if rewrite is not None:
        name = os.path.basename(target.rstrip('\\/')) or 'out'
        there = aim or dest.path
        return refuse('write-jail', role, why + ' The same write in %s:' % fwd(there),
                      rewrite(fwd(there) + '/' + name if there.startswith('<') else fwd(os.path.join(there, name))))
    return refuse('write-jail', role, why + ' Run it with %s as the working directory:' % quote(dest.path),
                  run or 'cd %s' % quote(dest.path))


def worktree_alternative(ctx, target, main_top, cwd, rewrite=None, run=None, redirect=False, shell=None):
    """(sentence, command): the same edit in the session's own worktree of this main checkout, or
    a new worktree to make it in; a redirect's output with no worktree of the session's own, the
    same redirect into the temp folder; a run that writes through the main checkout it runs in,
    the same run from the worktree, its cd in the same call (cd_line)."""
    rel = os.path.relpath(target, main_top)
    mine = own_worktree(ctx, cwd, main_top)
    if mine:
        if rewrite is not None:
            return 'Make the change in your worktree:', rewrite(fwd(os.path.join(mine, rel)))
        if run is not None:
            return 'Run it with %s as the working directory:' % quote(os.path.join(mine, rel)), run
    no_wt, instead = new_worktree(ctx, main_top)
    if no_wt:
        return no_wt, instead
    if redirect and rewrite is not None:
        name = os.path.basename(target.rstrip('\\/')) or 'out'
        return REDIRECT_HINT, rewrite(fwd(os.path.join(ctx.tempdir, name)))
    if rewrite is None and run is not None and cwd and paths.norm(target) == paths.norm(cwd):
        # it writes through the main checkout it runs in
        return MAIN_CWD_HINT % instead, cd_line('<worktree>', run, shell)
    if rewrite is not None:
        # the session may have a worktree the call does not run in: aim at it, a new one only for a session with none
        there = '' if rel == '.' else ' (%s there)' % fwd(rel)
        return ('Make the change in your worktree%s%s; the lander brings it to main:' % (there, only_new(instead)),
                rewrite('<worktree>' if rel == '.' else '<worktree>/' + fwd(rel)))
    # no word to aim (cp -t<dir>): the worktrees to make it in; a new one only in a clause (only_new)
    there = '' if rel == '.' else ' (%s there)' % fwd(rel)
    return ('Make the change in your worktree%s%s; the lander brings it to main. To see the worktrees:'
            % (there, '' if mine else only_new(instead)), 'git -C %s worktree list' % quote(main_top))


def only_new(new_wt):
    """The clause a refusal from the main checkout offers a new worktree in: the call does not
    say whether the session has one, so the Instead aims at <worktree> and only a session with
    none makes one: the new worktree is never the Instead itself."""
    return '; only a session with no worktree makes one, first (%s)' % new_wt


REDIRECT_HINT = ('A redirect writes output: send it to the temp folder (a change to the repository is made in a '
                 'worktree of it, which the lander brings to main):')
# commands whose file is the output they are given, like a redirect's (their refusal names the temp folder)
OUTPUT_WRITERS = ('out-file', 'tee-object', 'tee')
MAIN_CWD_HINT = ('It writes through its working directory, which is the main checkout: run it from your worktree, '
                 'with the cd in the same call (no tool keeps a cd between calls), or run a script or module by '
                 'absolute path (python <worktree>/tools/x.py -C <worktree>; python -m unittest discover -s '
                 '<worktree>/tests/<pkg> -t <worktree>). To read files, use the Read and Grep tools. Only a session '
                 'with no worktree makes one, first (%s). From your worktree:')


def cd_line(where, run, shell):
    """`run` from the folder `where`, the cd in the same call: PowerShell runs it only when the cd
    worked (`if ($?)`), any other shell after `&&`."""
    if shell == sh.PS:
        return 'cd "%s"; if ($?) { %s }' % (where, run)
    return 'cd "%s" && %s' % (where, run)


def outer_shell(cmd):
    """The dialect of the line `cmd` is on: its outermost shell (a bash -c inside PowerShell is on a
    PowerShell line)."""
    while cmd.nested_in is not None:
        cmd = cmd.nested_in
    return cmd.shell


def whole_line(ctx, cmd):
    """The text of the whole line `cmd` is on, as its outermost shell reads it (outer_shell), for
    a refusal that runs it from the worktree (cd_line): a cd into the top of a main checkout at
    its start (each one before the first other command, a ; or a line break after it) is left
    out, as is a comment after its last command. A line with any other cd, or a Pop-Location,
    would move off the worktree: it gives the call alone (bare)."""
    outer = cmd
    while outer.nested_in is not None:
        outer = outer.nested_in
    src = outer.source or ''
    _, cmds = sh.parse(src, outer.shell, outer.cwd)
    top = [c for c in cmds if c.depth == 0 and c.start is not None]
    if not top:
        return src.strip()
    start = top[0].start
    for i, c in enumerate(top):
        is_cd, where = sh.cd_target(c, outer.cwd)
        if not is_cd:
            break
        place = ctx.place(where) if where else None
        after = src[c.end:top[i + 1].start] if i + 1 < len(top) else None
        if (place is None or place.kind != repo.MAIN or paths.norm(where) != paths.norm(place.top)
                or after is None or after.strip() not in ('', ';')):
            return bare(cmd)
        start = top[i + 1].start
    if any(sh.cd_target(c, outer.cwd)[0] for c in cmds if c.start is None or c.start >= start):
        return bare(cmd)
    end = max(c.end for c in top)
    tail = src[end:]
    cut = tail.find('#')
    if cut >= 0:
        tail = tail[:cut - 1 if cut and tail[cut - 1] == '<' else cut]
    return (src[start:end] + tail).strip()
