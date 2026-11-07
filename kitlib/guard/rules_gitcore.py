"""How the guard's rules read a git command line (kitlib.guard.rules, which re-exports each name here):
the long and short options per subcommand and their abbreviations, an alias the line itself defines,
git config's options and the config files git reads, and the two rules built only on those:
core.hooksPath and the owner's identity."""
import os
import posixpath
import re

from . import commands as cm
from . import paths
from . import shell as sh
from .rules_base import bare, is_owner_session, quote, refuse, same_dir


# Long options per subcommand, so an abbreviation git accepts (--ha for --hard) is read as git
# reads it; the ones that take a value; the short options that take one.
GIT_LONGS = {
    'reset': ('hard', 'soft', 'mixed', 'keep', 'merge', 'quiet', 'patch', 'pathspec-from-file',
              'pathspec-file-nul', 'recurse-submodules', 'no-recurse-submodules', 'intent-to-add',
              'refresh', 'no-refresh'),
    'commit': ('all', 'allow-empty', 'allow-empty-message', 'amend', 'message', 'file', 'author', 'date',
               'template', 'reuse-message', 'reedit-message', 'fixup', 'squash', 'patch', 'include', 'only',
               'interactive', 'quiet', 'verbose', 'dry-run', 'no-verify', 'verify', 'signoff', 'no-signoff',
               'no-edit', 'edit', 'cleanup', 'status', 'no-status', 'gpg-sign', 'no-gpg-sign',
               'pathspec-from-file', 'pathspec-file-nul', 'reset-author', 'short', 'branch', 'porcelain',
               'long', 'null', 'untracked-files', 'trailer'),
    'push': ('force', 'force-with-lease', 'no-force-with-lease', 'force-if-includes', 'no-force-if-includes',
             'no-verify', 'verify', 'delete', 'all', 'mirror', 'branches', 'tags', 'follow-tags',
             'no-follow-tags', 'dry-run', 'porcelain', 'prune', 'set-upstream', 'quiet', 'verbose', 'progress',
             'no-progress', 'atomic', 'no-atomic', 'signed', 'no-signed', 'push-option', 'repo', 'receive-pack',
             'exec', 'thin', 'no-thin', 'recurse-submodules', 'no-recurse-submodules', 'ipv4', 'ipv6'),
    'restore': ('staged', 'worktree', 'source', 'patch', 'quiet', 'progress', 'no-progress', 'ours', 'theirs',
                'merge', 'conflict', 'ignore-unmerged', 'ignore-skip-worktree-bits', 'recurse-submodules',
                'no-recurse-submodules', 'overlay', 'no-overlay', 'pathspec-from-file', 'pathspec-file-nul'),
    'clean': ('dry-run', 'force', 'interactive', 'quiet', 'exclude'),
    'branch': ('delete', 'force', 'move', 'copy', 'create-reflog', 'set-upstream-to', 'unset-upstream', 'track',
               'no-track', 'list', 'all', 'remotes', 'verbose', 'quiet', 'abbrev', 'no-abbrev', 'column',
               'no-column', 'sort', 'merged', 'no-merged', 'contains', 'no-contains', 'points-at', 'format',
               'color', 'no-color', 'ignore-case', 'show-current', 'edit-description', 'recurse-submodules'),
    'add': ('all', 'update', 'no-all', 'ignore-removal', 'no-ignore-removal', 'dry-run', 'verbose', 'force',
            'interactive', 'patch', 'edit', 'intent-to-add', 'refresh', 'ignore-errors', 'ignore-missing',
            'renormalize', 'chmod', 'pathspec-from-file', 'pathspec-file-nul', 'sparse'),
    'apply': ('check', 'stat', 'numstat', 'summary', 'index', 'cached', 'intent-to-add', '3way', 'reverse',
              'reject', 'unidiff-zero', 'apply', 'no-add', 'exclude', 'include', 'directory', 'verbose',
              'quiet', 'whitespace', 'recount', 'allow-empty', 'inaccurate-eof', 'build-fake-ancestor',
              'unsafe-paths', 'ignore-space-change', 'ignore-whitespace'),
    'stash': ('message', 'patch', 'staged', 'keep-index', 'no-keep-index', 'include-untracked', 'all', 'quiet',
              'pathspec-from-file', 'pathspec-file-nul', 'index'),
    'update-ref': ('no-deref', 'stdin', 'create-reflog', 'batch-updates'),
    # --prefix and --stage take a value; left out of GIT_LONG_VALUES, the value is read as a path
    # (fail closed)
    'checkout-index': ('index', 'quiet', 'force', 'all', 'no-create', 'prefix', 'stage', 'temp',
                       'ignore-skip-worktree-bits', 'stdin'),
}
GIT_LONGS['stage'] = GIT_LONGS['add']
GIT_LONG_VALUES = {'message', 'file', 'author', 'date', 'template', 'reuse-message', 'reedit-message', 'fixup',
                   'squash', 'cleanup', 'trailer', 'source', 'conflict', 'exclude', 'push-option', 'repo',
                   'receive-pack', 'exec', 'set-upstream-to', 'sort', 'format', 'contains', 'no-contains',
                   'points-at', 'pathspec-from-file', 'directory', 'whitespace', 'include', 'build-fake-ancestor'}
GIT_SHORT_VALUES = {'commit': 'mFCct', 'restore': 's', 'clean': 'e', 'push': 'o', 'stash': 'm', 'branch': 'u',
                    'apply': 'pC', 'update-ref': 'm'}


def _canon_long(sub, body):
    """The long options `--body` selects for `sub`: itself when exact or unknown, the one it
    abbreviates, or every one it could abbreviate (an ambiguous prefix fails closed). git reads
    `--no-<prefix>` as the negation of an option too (`--no-st` is `--no-staged`), so `--no`,
    `--no-` and `--no-<prefix>` also select `no-<name>` for each name the prefix abbreviates."""
    table = GIT_LONGS.get(sub, ())
    if body in table or len(body) < 2:
        return [body]
    hits = [n for n in table if n.startswith(body)]
    if body.startswith('no-') or 'no-'.startswith(body):
        hits += ['no-' + n for n in table if n.startswith(body[3:]) and not n.startswith('no-')
                 and 'no-' + n not in hits]
    return hits or [body]


def restore_index_only(short, longs):
    """Whether git restore with these options (_opts) writes the index alone: --staged (-S)
    without --worktree (-W), and every long option one GIT_LONGS names (fail closed: _canon_long
    leaves a one-letter body as written, and `--w` is `--worktree` to git; `--no-st` is
    `--no-staged`, which it names and the table does not; `--no` and `--no-` git rejects as
    ambiguous, and _canon_long's names for them are not in the table either)."""
    return (('S' in short or 'staged' in longs) and not ('W' in short or 'worktree' in longs)
            and longs <= set(GIT_LONGS['restore']))


def _one_letter(sub, body):
    """The long option a one-letter `--body` abbreviates for `sub` when exactly one starts with it
    (git reads `--h` as reset's `--hard`), else None (ambiguous or unknown: git rejects it)."""
    hits = [n for n in GIT_LONGS.get(sub, ()) if len(body) == 1 and n.startswith(body)]
    return hits[0] if len(hits) == 1 else None


def _opts(sub, args, wide=False):
    """(short option letters, long option names, positional Words) of a git subcommand's
    arguments, option values left out. `wide` also names the long option a one-letter body
    abbreviates (_one_letter) beside the body itself, for a check that refuses on that option:
    the body stays, so a check that passes only on known options (restore_index_only) still
    fails closed, and a caller that passes on an option it names (a listing, --no-deref) keeps
    the narrow reading."""
    shorts, longs, pos = set(), set(), []
    values = GIT_SHORT_VALUES.get(sub, '')
    k = 0
    while k < len(args):
        t = args[k].text
        if t == '--':
            pos.extend(args[k + 1:])
            break
        if t.startswith('--'):
            body, eq, _v = t[2:].partition('=')
            names = _canon_long(sub, body)
            longs.update(names)
            one = _one_letter(sub, body) if wide else None
            if one:
                longs.add(one)
            k += 1
            if not eq and len(names) == 1 and names[0] in GIT_LONG_VALUES:
                k += 1
            continue
        if t.startswith('-') and len(t) > 1:
            body = t[1:]
            k += 1
            for j, c in enumerate(body):
                shorts.add(c)
                if c in values:
                    if not body[j + 1:]:
                        k += 1
                    break
            continue
        pos.append(args[k])
        k += 1
    return shorts, longs, pos


def _pos(args):
    return [w for w in args if not w.text.startswith('-')]


def computed_word(w):
    """True for a Word whose text is not what git gets: computed, an alias's, or one bash changes."""
    return bool(w.dyn or w.alt is not None or w.synth)


def inline_alias(gc):
    """`gc` as git runs it when its subcommand names an alias the line itself defines (-c
    alias.<name>=<value>): the value's words are the subcommand and its first arguments, so the
    rules judge the command git runs. A builtin name (git ignores an alias that would hide one),
    --config-env (its value is in the environment), a computed setting, a shell alias (!...) and
    a value that starts with an option or cannot be split end the expansion where it is
    (git_alias warns). A chain is followed at most ALIAS_DEPTH steps: the call is then judged as
    the last expansion reached."""
    if gc is None or gc.inv is None or not gc.alias or gc.sub_dyn:
        return gc
    inline = {}
    for key, val, first, last in git_prefix_configs(gc):
        if key.startswith('alias.') and not cm.passed(gc.inv.cmd, first).startswith('--') and \
                not first.dyn and not last.dyn:
            inline[key[len('alias.'):].lower()] = val   # the last setting of a name wins, as in git
    for _depth in range(ALIAS_DEPTH):
        val = inline.get(gc.sub.lower())
        if val is None or gc.sub in GIT_BUILTINS or val.startswith('!'):
            return gc
        words = split_alias(val)
        if not words or words[0].startswith('-'):
            return gc
        rest = [sh.Word(t, False, None, None) for t in words[1:]] + list(gc.rest)
        gc = cm.GitCall(gc.dir, words[0], rest, gc.inv, gc.unknown, gc.alias, False, gc.blind,
                        len(words) - 1 + gc.synth)
    return gc


def git_call_judged(inv):
    """commands.git_call, with an alias the line defines expanded (inline_alias)."""
    return inline_alias(cm.git_call(inv))


def clean_dry_run(args):
    """True when git clean with arguments `args` (the Words after `clean`) is a dry run, read in
    order as git reads it: the last of `-n` (in a short cluster), `--dry-run` (an abbreviation of
    two letters or more) and its negation `--no-dry-run` (`--no`, `--no-`, `--no-d`: git rejects
    the ambiguous ones, and a refused line deletes nothing) wins; `-fn --no-dry-run` deletes. A
    one-letter `--d` is `--dry-run` to git (-d has no long form), but is left unread: the line is
    refused (fail closed). The value of `-e`/`--exclude` (any abbreviation, `--e` too) is skipped,
    so `-f -e -n` is no dry run; a word the shell computes cancels one. Words after `--` are paths."""
    dry = False
    values = GIT_SHORT_VALUES['clean']
    k = 0
    while k < len(args):
        w = args[k]
        t = w.text
        k += 1
        if w.dyn:
            dry = False                         # a word the shell computes may be --no-dry-run
            continue
        if t == '--':
            break
        if t.startswith('--'):
            body, eq, _v = t[2:].partition('=')
            if body.startswith('no') and 'no-dry-run'.startswith(body):
                dry = False
            elif not eq and len(body) > 1 and 'dry-run'.startswith(body):
                dry = True
            elif body and 'exclude'.startswith(body) and not eq:
                k += 1
            continue
        if t.startswith('-') and len(t) > 1:
            body = t[1:]
            for j, c in enumerate(body):
                if c == 'n':
                    dry = True
                if c in values:
                    if not body[j + 1:]:
                        k += 1
                    break
    return dry


def stash_reads(args):
    """True when git stash with arguments `args` (the Words after `stash`) only reads: `list` or
    `show` as the first word. git reads a verb only there: a line that starts with an option or
    `--` is a push (`git stash -- show` stashes the path `show`) or one git refuses (`git stash -q
    list` exits 128), never a list or show (git 2.55, observed)."""
    return bool(args) and args[0].text in ('list', 'show')


# ---------------------------------------------------------------- git aliases

# git's builtin commands (git --list-cmds=builtins, git 2.55): git ignores an alias that would hide one
GIT_BUILTINS = frozenset((
    'add', 'am', 'annotate', 'apply', 'archive', 'backfill', 'bisect', 'blame', 'branch', 'bugreport', 'bundle',
    'cat-file', 'check-attr', 'check-ignore', 'check-mailmap', 'check-ref-format', 'checkout', 'checkout--worker',
    'checkout-index', 'cherry', 'cherry-pick', 'clean', 'clone', 'column', 'commit', 'commit-graph', 'commit-tree',
    'config', 'count-objects', 'credential', 'credential-cache', 'credential-cache--daemon', 'credential-store',
    'describe', 'diagnose', 'diff', 'diff-files', 'diff-index', 'diff-pairs', 'diff-tree', 'difftool',
    'fast-export', 'fast-import', 'fetch', 'fetch-pack', 'fmt-merge-msg', 'for-each-ref', 'for-each-repo',
    'format-patch', 'format-rev', 'fsck', 'fsck-objects', 'fsmonitor--daemon', 'gc', 'get-tar-commit-id', 'grep',
    'hash-object', 'help', 'history', 'hook', 'index-pack', 'init', 'init-db', 'interpret-trailers',
    'last-modified', 'log', 'ls-files', 'ls-remote', 'ls-tree', 'mailinfo', 'mailsplit', 'maintenance', 'merge',
    'merge-base', 'merge-file', 'merge-index', 'merge-ours', 'merge-recursive', 'merge-recursive-ours',
    'merge-recursive-theirs', 'merge-subtree', 'merge-tree', 'mktag', 'mktree', 'multi-pack-index', 'mv',
    'name-rev', 'notes', 'pack-objects', 'pack-redundant', 'pack-refs', 'patch-id', 'pickaxe', 'prune',
    'prune-packed', 'pull', 'push', 'range-diff', 'read-tree', 'rebase', 'receive-pack', 'reflog', 'refs',
    'remote', 'remote-ext', 'remote-fd', 'repack', 'replace', 'replay', 'repo', 'rerere', 'reset', 'restore',
    'rev-list', 'rev-parse', 'revert', 'rm', 'send-pack', 'shortlog', 'show', 'show-branch', 'show-index',
    'show-ref', 'sparse-checkout', 'stage', 'stash', 'status', 'stripspace', 'submodule--helper', 'survey',
    'switch', 'symbolic-ref', 'tag', 'unpack-file', 'unpack-objects', 'update-index', 'update-ref',
    'update-server-info', 'upload-archive', 'upload-archive--writer', 'upload-pack', 'url-parse', 'var',
    'verify-commit', 'verify-pack', 'verify-tag', 'version', 'whatchanged', 'worktree', 'write-tree'))
ALIAS_DEPTH = 10


# the characters alias words are split by: git 2.55 splits a quoted alias value at a space, tab or
# CR (observed: "rev-parse<c>--git-dir" runs for those, and is "not a git command" for VT and FF).
# VT and FF are split here too: a word holding one is no git command or option git accepts, so
# splitting it can only make the guard judge more, never less
ALIAS_BLANKS = ' \t\n\v\f\r'


def split_alias(text):
    """The words of an alias value as git splits them (split_cmdline): ALIAS_BLANKS separate,
    '...' and "..." group, a backslash outside '...' takes the next character as it is. None for
    an unclosed quote or a trailing backslash."""
    words, cur, have, q, i = [], [], False, None, 0
    while i < len(text):
        c = text[i]
        if q is None and c in ALIAS_BLANKS:
            if have:
                words.append(''.join(cur))
                cur, have = [], False
        elif q is None and c in '\'"':
            q, have = c, True
        elif c == q:
            q = None
        elif c == '\\' and q != "'":
            i += 1
            if i >= len(text):
                return None
            cur.append(text[i])
            have = True
        else:
            cur.append(c)
            have = True
        i += 1
    if q is not None:
        return None
    if have:
        words.append(''.join(cur))
    return words


# ---------------------------------------------------------------- environment assignments

# kept in commands, which marks git whose place an assignment hides (commands.mark_git_env)
env_assignments = cm.env_assignments


# ---------------------------------------------------------------- core.hooksPath

HOOKS_KEY = 'core.hookspath'
CONFIG_READ_OPTS = ('--get', '--get-all', '--get-regexp', '--get-urlmatch', '--get-color', '--get-colorbool',
                    '-l', '--list')
# the only options config may carry where a strict read is wanted (strict_read_opts)
CONFIG_STRICT_READ_OPTS = set(CONFIG_READ_OPTS) | {'--show-origin', '--show-scope', '--name-only', '-z', '--null',
                                                   '--global', '--system', '--local', '--worktree', '--file', '-f',
                                                   '--blob', '--type', '--no-type', '--default', '--includes',
                                                   '--no-includes', '--bool', '--int', '--bool-or-int', '--path',
                                                   '--expiry-date', '--fixed-value'}


def kit_githooks():
    """The kit's own githooks folder: the value the install sets (docs/guard.md "Go-live")."""
    return os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'githooks')


def repo_config_file(text):
    """True when a config file path names a config git reads: one inside a .git folder
    (.git/config, .git/worktrees/<name>/config, .git/modules/<name>/config, config.worktree
    beside any), or the user's or system's (.gitconfig, gitconfig, .config/git/config). The path
    is normalised first, so .git/./config and .git/info/../config count."""
    norm = posixpath.normpath((text or '').replace('\\', '/'))
    parts = [p.lower() for p in norm.split('/') if p]
    if not parts:
        return False
    if parts[-1] in ('.gitconfig', 'gitconfig'):
        return True
    if len(parts) < 2 or parts[-1] not in ('config', 'config.worktree'):
        return False
    return '.git' in parts[:-1] or (len(parts) >= 3 and parts[-3:-1] == ['.config', 'git'])


# git config's long options (git 2.55, the legacy form's and its subcommands' together), so an
# abbreviation git accepts (--glo, --fil, --ed) is read as git reads it; those that take a value;
# its short options by the long one each stands for (-f and -t take a value)
CONFIG_LONGS = ('global', 'system', 'local', 'worktree', 'file', 'blob', 'get', 'get-all', 'get-regexp',
                'get-urlmatch', 'replace-all', 'add', 'unset', 'unset-all', 'rename-section', 'remove-section',
                'list', 'fixed-value', 'edit', 'get-color', 'get-colorbool', 'type', 'no-type', 'bool', 'int',
                'bool-or-int', 'bool-or-str', 'path', 'expiry-date', 'null', 'name-only', 'includes', 'show-origin',
                'show-scope', 'show-names', 'default', 'comment', 'all', 'regexp', 'value', 'url', 'append')
CONFIG_LONG_VALUES = ('file', 'blob', 'type', 'default', 'comment', 'value', 'url')
CONFIG_SHORTS = {'f': 'file', 't': 'type', 'l': 'list', 'e': 'edit', 'z': 'null'}
CONFIG_READS = frozenset(('get', 'get-all', 'get-regexp', 'get-urlmatch', 'get-color', 'get-colorbool', 'list'))
CONFIG_WRITES = frozenset(('unset', 'unset-all', 'replace-all', 'add'))
CONFIG_SECTIONS = frozenset(('remove-section', 'rename-section'))


def _config_long(body):
    """The git config long options `--body` selects: itself when exact or unknown, the one it
    abbreviates (a --no-<name> negation too, as no-<name>), or every one it could abbreviate (git
    refuses an ambiguous prefix)."""
    if body in CONFIG_LONGS or len(body) < 1:
        return [body]
    hits = [n for n in CONFIG_LONGS if n.startswith(body)]
    if body.startswith('no-'):
        hits += ['no-' + n for n in CONFIG_LONGS if n.startswith(body[3:])]
    out = []
    for n in hits:
        if n not in out:
            out.append(n)
    return out or [body]


def config_args(gc, dyn_opts=False):
    """(names, sure, pos, files) of a `git config` call's arguments, read as git reads them:
    `names` every long option name a word may select (an abbreviation as the option it
    abbreviates, an ambiguous one as each, a short one by its long name, -f<path> split), `sure`
    the names a word selects alone, `pos` the positional Words, `files` the Words its -f/--file
    options name. A computed word (a PowerShell splat @a too, which may stand for any words) is
    positional, unless `dyn_opts` and it starts with '-' (then an option whose name is unknown)."""
    names, sure, pos, files = set(), set(), [], []
    args = gc.rest
    ps = gc.inv is not None and gc.inv.cmd.shell == sh.PS
    k = 0
    while k < len(args):
        w = args[k]
        t = w.text
        if ps and not w.dyn and w.start is not None and cm.PS_SPLAT_RE.match(t) and not cm.quoted(gc.inv.cmd, w):
            w = sh.Word(t, True, w.start, w.end)
        if t == '--':
            pos.extend(args[k + 1:])
            break
        k += 1
        if not t.startswith('-') or len(t) < 2 or (w.dyn and not dyn_opts):
            pos.append(w)
            continue
        if w.dyn:
            names.add(t)
            continue
        if t.startswith('--'):
            body, eq, val = t[2:].partition('=')
            hits = _config_long(body)
            names.update(hits)
            if len(hits) != 1:
                continue
            sure.add(hits[0])
            if hits[0] not in CONFIG_LONG_VALUES:
                continue
            if eq:
                value = sh.Word(val, w.dyn, w.start, w.end)
            else:
                value = args[k] if k < len(args) else None
                k += 1
            if hits[0] == 'file' and value is not None:
                files.append(value)
            continue
        body = t[1:]
        for j, c in enumerate(body):
            name = CONFIG_SHORTS.get(c, '-' + c)
            names.add(name)
            sure.add(name)
            if c not in 'ft':
                continue
            if body[j + 1:]:
                value = sh.Word(body[j + 1:], w.dyn, w.start, w.end)
            else:
                value = args[k] if k < len(args) else None
                k += 1
            if c == 'f' and value is not None:
                files.append(value)
            break
    return names, sure, pos, files


def git_config_call(gc):
    """(key, value Word or None, writes) of a `git config` call on a config git reads, or None (no
    key, --blob, or --file names a file git does not read). Options are read as git reads them
    (config_args: --fil <path>, -f<path>, --glo). A --file the text does not name (a variable, a
    substitution) counts as one git reads: fail closed."""
    names, sure, pos, files = config_args(gc, dyn_opts=True)
    if any(not f.dyn and not repo_config_file(f.text) for f in files):
        return None                             # a file git does not read
    if not pos or 'blob' in sure:
        return None
    verb = pos[0].text
    if verb in ('get', 'list'):
        return (pos[1].text.lower() if len(pos) > 1 else ''), None, False
    if verb in ('set', 'unset'):
        if len(pos) < 2:
            return None
        return pos[1].text.lower(), (pos[2] if len(pos) > 2 else None), True
    if sure & CONFIG_READS:
        return verb.lower(), None, False
    writes = bool(names & CONFIG_WRITES) or len(pos) > 1
    return verb.lower(), (pos[1] if len(pos) > 1 else None), writes


def git_prefix_configs(gc):
    """[(key, value text, first Word, last Word)] of the -c and --config-env settings before the
    git subcommand (key lower-case), each word read as the shell hands it to git (commands.passed:
    bash's `-\\c core.hooksPat\\h=x` is -c core.hooksPath=x)."""
    a = gc.before()
    out, k = [], 0
    while k < len(a):
        t = cm.passed(gc.inv.cmd, a[k])
        if t in ('-c', '--config-env') and k + 1 < len(a):
            key, _eq, val = cm.passed(gc.inv.cmd, a[k + 1]).partition('=')
            out.append((key.lower(), val, a[k], a[k + 1]))
            k += 2
            continue
        if (t.startswith('-c') and not t.startswith('--')) or t.startswith('--config-env='):
            body = t[2:] if t.startswith('-c') else t.split('=', 1)[1]
            key, _eq, val = body.partition('=')
            out.append((key.lower(), val, a[k], a[k]))
        k += 1
    return out


def _is_kit_hooks(text, cwd, home=None):
    p = sh.resolve_path(text, cwd, home=home) if text else None
    return bool(p) and same_dir(p, kit_githooks())


def _without(cmd, first, last):
    """The command, from its first word, with the words first..last cut out."""
    return (cmd.source[cmd.words[0].start:first.start].rstrip() + ' ' +
            cmd.source[last.end:cmd.words[-1].end].lstrip()).strip()


def _under_temp(ctx, d):
    """True when the folder `d` is the temp folder or below it. It lifts the core.hooksPath checks,
    so on Linux it compares with the strict fold (a mount in doubt does not fold) and a `\\` is a
    name's character."""
    if not d:
        return False
    strict = paths.posix()
    top = paths.norm(ctx.tempdir, absolute=True, strict=strict).rstrip('/' if strict else '\\/')
    here_ = paths.norm(d, absolute=True, strict=strict)
    return here_ == top or here_.startswith(top + os.sep)


def clone_configs(gc):
    """[(key, value text, first Word, last Word)] of a `git clone`'s -c and --config settings,
    which it writes into the new repository's config (key lower-case)."""
    a = gc.rest
    out, k = [], 0
    while k < len(a):
        t = a[k].text
        if t in ('-c', '--config') and k + 1 < len(a):
            key, _eq, val = a[k + 1].text.partition('=')
            out.append((key.lower(), val, a[k], a[k + 1]))
            k += 2
            continue
        if (t.startswith('-c') and not t.startswith('--')) or t.startswith('--config='):
            body = t[2:] if t.startswith('-c') else t.split('=', 1)[1]
            key, _eq, val = body.partition('=')
            out.append((key.lower(), val, a[k], a[k]))
        k += 1
    return out


def _config_files(gc):
    """The paths a `git config` call names with -f/--file (config_args: --fil, -f<path> too)."""
    return [w.text for w in config_args(gc, dyn_opts=True)[3]]


def _config_scope_all(gc):
    """True when a git call names --global or --system; for `git config`, in any spelling git
    accepts (config_args: --glo, --sys)."""
    if gc.sub == 'config':
        return bool({'global', 'system'} & config_args(gc, dyn_opts=True)[0])
    return any(w.text in ('--global', '--system') for w in gc.rest)


# The variables git reads config settings from: GIT_CONFIG_PARAMETERS ('key'='value' ...) and
# GIT_CONFIG_KEY_<n> (with GIT_CONFIG_VALUE_<n>).
HOOKS_ENV = r'GIT_CONFIG_PARAMETERS|GIT_CONFIG_KEY_\d+'
HOOKS_WHY = ('core.hooksPath picks the folder git runs hooks from, the kit\'s pre-push checks among them; the owner '
             'sets it at install.')


def git_hooks_path(event, ctx, cmd, inv, cwd, role):
    """A change of core.hooksPath (git config, including -f on a repository's own .git/config;
    git -c, git --config-env, git clone -c; GIT_CONFIG_PARAMETERS or GIT_CONFIG_KEY_<n>), in any
    session: it switches the repository's hooks off or elsewhere. Setting it to the kit's own
    githooks (the install step), and repositories under the temp folder (not --global or
    --system), are left alone. A git alias the line defines that runs `config` is judged the same
    way (inline_alias)."""
    why = HOOKS_WHY
    for name, val in env_assignments(cmd, HOOKS_ENV):
        if HOOKS_KEY in val.lower():
            where = quote(cmd.cwd or cwd) if (cmd.cwd or cwd) else '"<checkout>"'
            return refuse('git-hooks-path', role, '`%s` sets %s to change core.hooksPath for the git commands that '
                          'read it. %s Ask the owner (through the PM) when it needs to change. To see it:' %
                          (bare(cmd), name, why), 'git -C %s config --get core.hooksPath' % where)
    gc = git_call_judged(inv)
    if gc is None:
        return None
    if _under_temp(ctx, gc.dir) and not _config_scope_all(gc):
        return None
    configs = git_prefix_configs(gc) + (clone_configs(gc) if gc.sub == 'clone' else [])
    for key, val, first, last in configs:
        if key == HOOKS_KEY and not _is_kit_hooks(val, gc.dir, getattr(ctx, 'home', None)):
            return refuse('git-hooks-path', role, '`%s` runs git with core.hooksPath=%s, which replaces the '
                          'repository\'s hooks. %s Run it without the setting:' %
                          (bare(cmd), val, why), _without(cmd, first, last))
    if gc.sub != 'config':
        return None
    return hooks_config_write(ctx, gc, role, bare(cmd))


def hooks_config_write(ctx, gc, role, shown):
    """git_hooks_path's judgement of a `git config` call `gc` (shown as `shown`): a write of
    core.hooksPath to a config git reads, other than the kit's own githooks; a repository under
    the temp folder (not --global or --system), and -f on a config under it, are left alone."""
    if _under_temp(ctx, gc.dir) and not _config_scope_all(gc):
        return None
    home = getattr(ctx, 'home', None)
    if any(_under_temp(ctx, os.path.dirname(sh.resolve_path(f, gc.dir, home=home) or '')) for f in _config_files(gc)):
        return None                              # -f on a scratch repository's config under temp
    call = git_config_call(gc)
    if call is None:
        return None
    key, value, writes = call
    if key != HOOKS_KEY or not writes:
        return None
    if value is not None and not value.dyn and _is_kit_hooks(value.text, gc.dir, home):
        return None
    where = quote(gc.dir) if gc.dir else '"<checkout>"'
    return refuse('git-hooks-path', role, '`%s` changes core.hooksPath. %s Ask the owner (through the PM) when it '
                  'needs to change. To see it:' % (shown, HOOKS_WHY), 'git -C %s config --get core.hooksPath' % where)


# ---------------------------------------------------------------- the owner's identity

IDENTITY_KEYS = ('user.email', 'author.email', 'committer.email')
IDENTITY_ENV = r'(?:GIT_(?:AUTHOR|COMMITTER)_)?EMAIL'     # the variables git reads an identity's email from


def _names_email(text, email):
    """True when `text` holds `email` as a whole address (not inside a longer one)."""
    return re.search(r'(?i)(?<![\w.+-])%s(?![\w.-])' % re.escape(email), text or '') is not None


def _owner_identity_use(cmd, gc, email):
    """What the command does with the owner's email as a commit identity, or None."""
    for name, val in env_assignments(cmd, IDENTITY_ENV):
        if _names_email(val, email):
            return 'sets %s, which git reads a commit\'s identity from, to the owner\'s email' % name
    if gc is None:
        return None
    for key, val, _f, _l in git_prefix_configs(gc):
        if key in IDENTITY_KEYS and _names_email(val, email):
            return 'runs git with %s set to the owner\'s email' % key
    if gc.sub == 'commit':
        args = gc.rest
        for k, w in enumerate(args):
            body, eq, val = w.text.partition('=')
            if body.startswith('--') and 'author' in _canon_long('commit', body[2:]):
                v = val if eq else (args[k + 1].text if k + 1 < len(args) else '')
                if _names_email(v, email):
                    return 'names the owner as the commit\'s author'
    if gc.sub == 'config':
        call = git_config_call(gc)
        if call and call[0] in IDENTITY_KEYS and call[2] and call[1] is not None and _names_email(call[1].text, email):
            return 'sets %s to the owner\'s email' % call[0]
    return None


def owner_identity(event, ctx, cmd, inv, cwd, role):
    """A session other than the owner's own giving a commit the owner's email: git commit
    --author, git config or git -c user/author/committer.email, or GIT_AUTHOR_EMAIL,
    GIT_COMMITTER_EMAIL or EMAIL. Owner-only paths are judged by the author email
    (kitlib.guard.ownercommits), so these would pass an agent's change as the owner's."""
    if is_owner_session(event, ctx):
        return None
    where = cmd.cwd or cwd
    cfg = ctx.config(where) if where else None
    email = cfg.owner_rules().owner_email if cfg is not None else ''
    if not email:
        return None
    gc = git_call_judged(inv)
    found = _owner_identity_use(cmd, gc, email)
    if found is None:
        return None
    d = (gc.dir if gc is not None else None) or where
    return refuse('owner-identity', role, '`%s` %s. Owner-only paths are judged by the author email, so that '
                  'passes an agent\'s change as the owner\'s. Commit as yourself, with the identity git config '
                  'holds; to see it:' % (bare(cmd), found), 'git -C %s config --get user.email' % quote(d))
