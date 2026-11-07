"""The guard's reading of which git lines only read (kitlib.guard.rules, which re-exports each name
here): the verbs and options that change nothing in the checkout they run in, the strict reads a
computed word leaves alone, git archive's outputs, apply --check, config reads and git_targets."""
import re

from . import commands as cm
from . import shell as sh
from .rules_base import ps_native_unsure, tail_word
from .rules_gitcore import (CONFIG_LONG_VALUES, CONFIG_READ_OPTS, CONFIG_STRICT_READ_OPTS, _config_long, computed_word,
                            stash_reads)


HISTORY_SUBS = ('commit', 'merge', 'rebase', 'cherry-pick', 'revert', 'am', 'pull', 'apply', 'rm', 'mv',
                'checkout-index', 'update-ref')
APPLY_READ_ONLY = {'check', 'stat', 'numstat', 'summary'}


def writes_main(sub, short, longs):
    """True when git `sub` with these options writes history, refs or tracked files (a reset
    --hard has a rule of its own)."""
    if sub == 'apply':
        return not (APPLY_READ_ONLY & longs)
    if sub in HISTORY_SUBS:
        return True
    if sub == 'reset':
        return 'hard' not in longs
    if sub == 'read-tree':
        return 'u' in short
    if sub == 'branch':
        return 'f' in short or 'force' in longs
    return False


# git subcommands that change nothing in the checkout they run in, whatever their arguments
GIT_READS = {'status', 'log', 'diff', 'show', 'rev-parse', 'ls-files', 'ls-tree', 'ls-remote', 'cat-file', 'blame',
             'annotate', 'grep', 'describe', 'shortlog', 'rev-list', 'merge-base', 'for-each-ref', 'show-ref',
             'name-rev', 'help', 'version', 'check-ignore', 'check-attr', 'check-ref-format', 'var', 'count-objects',
             'diff-tree', 'diff-files', 'diff-index', 'cherry', 'range-diff', 'whatchanged', 'verify-commit',
             'verify-tag', 'show-branch', 'fsck', 'patch-id'}
# git apply --check applies nothing, nor do --stat, --numstat and --summary: each turns applying
# off, and only --apply turns it back on (git 2.55, observed). These are the only options such an
# apply may carry, spelt in full: git expands a unique prefix (--app is --apply, which applies
# after all), so any other option fails closed (apply_checks)
APPLY_REPORTS = ('--check', '--stat', '--numstat', '--summary')
APPLY_CHECK_OPTS = set(APPLY_REPORTS) | {'-v', '--verbose', '-R', '--reverse', '--'}
APPLY_STRIP_RE = re.compile(r'-p\d+$')
# subcommands that only read when their first word that is no option is one of these (None: when
# they have none); stash's verb is its first word of all (stash_reads)
GIT_READ_VERBS = {'worktree': ('list',), 'remote': (None, 'show', 'get-url'),
                  'reflog': (None, 'show', 'exists'), 'notes': (None, 'list', 'show'),
                  'submodule': (None, 'status', 'summary')}
GIT_LIST_FLAGS = {'-l', '--list', '--show-current', '--merged', '--no-merged', '--contains', '--no-contains',
                  '--points-at'}
# git symbolic-ref <name> with only these options, spelt in full, prints where <name> points (a
# second name, -d/--delete or -m writes it)
SYMREF_READ_OPTS = {'-q', '--quiet', '--short', '--recurse', '--no-recurse'}
GIT_REF_WRITE_FLAGS = {'-d', '-D', '-m', '-M', '-c', '-C', '-f', '-u', '-a', '-s', '-e', '--delete', '--move',
                       '--copy', '--force', '--set-upstream-to', '--unset-upstream', '--edit-description',
                       '--annotate', '--sign'}
# strict reads (git_reads): the only options branch and tag may carry, spelt in full, and the
# short-flag clusters they may combine (branch -av, tag -n5); config's are CONFIG_STRICT_READ_OPTS
GIT_LISTING_OPTS = GIT_LIST_FLAGS | {'-i', '--ignore-case', '--sort', '--format', '--color', '--no-color', '--column',
                                     '--no-column', '--omit-empty'}
GIT_STRICT_READ_OPTS = {'branch': (GIT_LISTING_OPTS | {'--all', '--remotes', '-vv', '--verbose', '--abbrev',
                                                       '--no-abbrev'}, re.compile(r'-[ailrv]+$')),
                        'tag': (GIT_LISTING_OPTS, re.compile(r'-(?=.)[il]*(?:n\d*)?$'))}
# strict reads (strict_dyn_read): reads no option of which writes a file or runs a program the line
# names, so a computed word (any number of words) leaves them reads; and reads with such an option
# (log, show, diff and rev-list: --output), where a computed word stays a read only when the text pins it to
# one argument that is no option, or the value of one of GIT_DYN_VALUE_OPTS
GIT_DYN_ANY_READS = {'rev-parse', 'ls-files', 'cat-file', 'merge-base', 'for-each-ref', 'ls-tree'}
# rev-list takes the diff options too (--output opens a file for writing)
GIT_DYN_PINNED_READS = {'log', 'show', 'diff', 'rev-list'}
# a literal word after which git reads no option (log "$A" --end-of-options "$B"): the computed
# words behind it are revisions or paths, so they cannot be --output; not when it is the value of
# the option before it (--grep --end-of-options), unless that option is known to take no value
END_OF_OPTIONS = '--end-of-options'
NO_VALUE_OPTS = frozenset(('--oneline', '--stat', '--patch', '-p', '--name-only', '--name-status', '--numstat',
                           '--shortstat', '--cached', '--staged', '--no-color', '--count', '--graph', '--all'))
NO_VALUE_NUM_RE = re.compile(r'-\d+')
GIT_DYN_VALUE_OPTS = ('--format=', '--pretty=')
# characters a pinned word may carry outside quotes: none the shell expands, splits or joins on
# (bash globs and braces, PowerShell's array comma and splat @), and no backslash or backtick
DYN_SAFE_BARE = {sh.BASH: frozenset('ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_./~^:=%+-@,'),
                 sh.PS: frozenset('ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_./~^:=%+-')}
DYN_VAR_RE = re.compile(r'\$(?:\{[A-Za-z_][A-Za-z0-9_]*\}|[A-Za-z_][A-Za-z0-9_]*|[0-9])')
DYN_PS_VAR_RE = re.compile(r'(?i)\$(?:\{[A-Za-z_][A-Za-z0-9_:]*\}|(?:env:|script:|global:|local:)?[A-Za-z_][A-Za-z0-9_]*)')


def writes_output_file(t):
    """True for log/show/diff's --output[=<file>], spelt in full or abbreviated (--outp=x)."""
    name = t.split('=', 1)[0]
    return len(name) >= 4 and '--output'.startswith(name)


# git archive's long options in full (git expands a unique prefix: --outp is --output), those that
# take a value, and its short ones (-o takes a value; -0..-9 are compression levels)
ARCHIVE_LONGS = ('format', 'prefix', 'add-file', 'add-virtual-file', 'output', 'worktree-attributes', 'verbose',
                 'list', 'mtime', 'remote', 'exec')
ARCHIVE_VALUES = {'format', 'prefix', 'add-file', 'add-virtual-file', 'output', 'mtime', 'remote', 'exec'}
ARCHIVE_FLAGS = set('vl0123456789')
# the -c keys (lower-case) an archive may carry and stay a read: none names a program, a filter or
# another file to read config or attributes from. Any other key may make archive run a program in
# the checkout it reads (tar.<format>.command; filter.<driver>.smudge or .process with
# core.attributesFile or the checkout's own attributes; include.path), so it fails closed
ARCHIVE_SAFE_KEY_RE = re.compile(r'^(?:core\.quotepath|core\.autocrlf|core\.eol|tar\.umask|tar\..+\.remote)$')


def cmd_native_unsure(cmd, w):
    """True when the C runtime may split the word `w` that cmd.exe passes on other than cmd reads
    it: cmd toggles quoting at every `"` and keeps backslashes, the C runtime reads `\\"` as a quote
    and `""` inside quotes as one; so only a word with no `"`, or one wholly in one pair of quotes
    with no backslash before the closing one, is read the same (cmd reads a\\" -o f \\"b as one
    word, git.exe gets a", -o, f, "b). A word with no valid place in the source is unsure: its
    text has cmd's quotes taken out already (fail closed)."""
    src = cmd.source or ''
    if w.start is None or w.end is None or not 0 <= w.start <= w.end <= len(src):
        return True
    raw = src[w.start:w.end]
    if '"' not in raw:
        return False
    return not (len(raw) >= 2 and raw[0] == raw[-1] == '"' and '"' not in raw[1:-1] and not raw[:-1].endswith('\\'))


def native_unsure(cmd, w):
    """True when the shell may hand the word `w` to a native program as other than one argument,
    the word the guard reads (ps_native_unsure, cmd_native_unsure)."""
    if cmd.shell == sh.PS:
        return ps_native_unsure(w)
    if cmd.shell == sh.CMD:
        return cmd_native_unsure(cmd, w)
    return False


def dyn_head(cmd, w):
    """The literal text before the first variable of the computed word `w`, read from its source
    when the shell (bash or PowerShell) is sure to hand it to git as one argument that starts with
    that text: outside quotes only DYN_SAFE_BARE characters, inside '...' anything but the quote,
    inside "..." plain text and $name or ${name} only (no $( or backtick that runs code, no "$@" or
    other variable that is many words, no backslash, no embedded quote); else None. PowerShell
    ends an argument that opens with a quote at its closing quote and passes what follows as
    another ('HEAD'"$f" is two), so such a word with text after its closing quote is None. A word
    with no variable, or no valid place in the source, is None too (fail closed)."""
    src = cmd.source or ''
    if cmd.shell not in DYN_SAFE_BARE or w.start is None or w.end is None or not 0 <= w.start < w.end <= len(src):
        return None
    raw, ps = src[w.start:w.end], cmd.shell == sh.PS
    if ps and (cm.PS_SPLAT_RE.match(raw) or any(c in raw for c in '“”„‘’‚‛')):
        return None
    # a doubled quote or a backtick inside returns None below, so the first matching quote after
    # the opening one is its close
    if ps and raw[0] in '\'"' and raw.find(raw[0], 1) != len(raw) - 1:
        return None
    var_re, bare = (DYN_PS_VAR_RE if ps else DYN_VAR_RE), DYN_SAFE_BARE[cmd.shell]
    head, seen, i, quote = [], False, 0, None
    while i < len(raw):
        c = raw[i]
        if quote == "'":
            if c == "'":
                if ps and raw[i + 1:i + 2] == "'":
                    return None                 # '' is a quote inside the word
                quote = None
            elif not seen:
                head.append(c)
            i += 1
            continue
        if c in '\\`':
            return None
        if c == '"':
            if quote == '"' and ps and raw[i + 1:i + 2] == '"':
                return None                     # "" is a quote inside the word
            quote = None if quote == '"' else '"'
            i += 1
            continue
        if c == "'" and quote is None:
            quote = "'"
            i += 1
            continue
        if c == '$':
            m = var_re.match(raw, i)
            if quote != '"' or not m:
                return None
            seen = True
            i = m.end()
            continue
        if quote is None and c not in bare:
            return None
        if not seen:
            head.append(c)
        i += 1
    if quote is not None or not seen:
        return None
    return ''.join(head)


def respelt(cmd, w):
    """True unless the source of the word `w` is exactly its text (a plain literal): a quoted, escaped,
    locale-quoted or brace-expanded word may reach git as other than its text. No place: True."""
    src = cmd.source or ''
    return w.start is None or w.end is None or not 0 <= w.start <= w.end <= len(src) or src[w.start:w.end] != w.text


def strict_dyn_read(gc):
    """True when every computed word of git read `gc` leaves it a read whatever it stands for
    (git_reads strict): a GIT_DYN_ANY_READS subcommand in bash or PowerShell, or a
    GIT_DYN_PINNED_READS one whose every computed word is one argument (dyn_head) that starts with
    literal text that keeps it from being an option (HEAD~$n, origin/$b, ./$f) or with
    --format= or --pretty=; or any computed word after a literal --end-of-options that is not the
    value of the option before it (END_OF_OPTIONS). Residual (inferred): PowerShell 5.1 splits an argument whose value
    holds a double quote (ps_native_unsure), which the text cannot show."""
    cmd = gc.inv.cmd if gc.inv is not None else None
    if cmd is None or cmd.shell not in DYN_SAFE_BARE:
        return False
    if gc.sub in GIT_DYN_ANY_READS:
        return True
    if gc.sub not in GIT_DYN_PINNED_READS:
        return False
    done, prev = False, None
    for w in gc.rest:
        before, prev = prev, w
        if not w.dyn:
            # the word before is read as written, so one the shell changes (computed_word, native_unsure,
            # or any whose source differs from its text: $"--grep", {\--grep,}) fails closed
            if w.text == END_OF_OPTIONS and (before is None or not computed_word(before)
                                             and not native_unsure(cmd, before) and not respelt(cmd, before) and (
                    not before.text.startswith('-') or before.text in NO_VALUE_OPTS
                    or NO_VALUE_NUM_RE.fullmatch(before.text))):
                done = True
            continue
        if done:
            continue
        head = dyn_head(cmd, w)
        if not head or (head.startswith('-') and not head.startswith(GIT_DYN_VALUE_OPTS)):
            return False
    return True


def ps_splat(gc, w):
    """True when the word `w` of git `gc` is a PowerShell splat (@a) the shell spreads into words
    the text does not show; a quoted '@a' is plain text."""
    return (gc.inv is not None and gc.inv.cmd.shell == sh.PS and not w.dyn and cm.PS_SPLAT_RE.match(w.text)
            and (w.start is None or not cm.quoted(gc.inv.cmd, w)))


def archive_words(gc):
    """The Words after git archive `gc`'s subcommand as the shell hands them to git (bash drops
    every backslash it would, a leading one too: `\\-o` is -o; commands.passed)."""
    cmd = gc.inv.cmd
    return list(gc.rest[:gc.synth]) + [sh.Word(cm.passed(cmd, w), w.dyn, w.start, w.end, w.synth)
                                       for w in gc.after()]


def archive_filter_set(gc):
    """True when the line may give git archive `gc` a program to run that the checkout's own config
    does not, or the guard cannot tell: a git the text cannot place (gc.blind: --git-dir,
    --work-tree, --config-env, a GIT_DIR-like variable), a commands.GIT_ENV_NAMES variable the
    line sets (commands.mark_git_env), a user config moved (HOME and the like, Command.home_env), PATH changed (Command.path_env), and
    before the subcommand --exec-path (it and PATH say where git finds a filter's bare program
    name, as Git for Windows' filter.lfs.process = git-lfs), a word the guard cannot read
    (computed, or one PowerShell or cmd may split: native_unsure) or a -c whose key, read as the
    shell hands it to git (commands.passed), is not on ARCHIVE_SAFE_KEY_RE."""
    cmd = gc.inv.cmd
    if gc.blind or cmd.git_env or cmd.home_env or cmd.path_env:
        return True
    pre = gc.before()
    k = 0
    while k < len(pre):
        if any(w.dyn or native_unsure(cmd, w) for w in pre[k:k + 2]):
            return True
        t = cm.passed(cmd, pre[k])
        name = t.partition('=')[0]
        if name.startswith('--exec-path') or len(name) > 2 and '--exec-path'.startswith(name):
            return True
        if t == '-c':
            if k + 1 >= len(pre):
                return True
            body, k = cm.passed(cmd, pre[k + 1]), k + 2
        elif t.startswith('--config-env'):
            return True
        elif t.startswith('-c'):
            body, k = t[2:], k + 1
        else:
            k += 1
            continue
        if not ARCHIVE_SAFE_KEY_RE.match(body.partition('=')[0].lower()):
            return True
    return False


def archive_long(name):
    """The archive long option `name` (no dashes) stands for, or None when it is unknown or an
    ambiguous prefix (--add)."""
    if name in ARCHIVE_LONGS:
        return name
    hits = [o for o in ARCHIVE_LONGS if name and o.startswith(name)]
    return hits[0] if len(hits) == 1 else None


def archive_opened(words):
    """[Word]: the files git archive's first pass (builtin/archive.c cmd_archive) opens. That pass
    knows only -o/--output, --remote and --exec, keeps every other option without taking a value
    for it and reads on past words that are not options, up to `--`; so in `--prefix -o f HEAD` the
    -o is its own and f is opened, though the later pass takes -o as the prefix. Every -o/-o<f>,
    --output[=]<f> and prefix of --output counts wherever it sits (a prefix fails closed), and no
    other option takes the word after it (fail closed for --remote and --exec)."""
    outs, k = [], 0
    while k < len(words):
        w, t = words[k], words[k].text
        if t == '--':
            break
        name, eq, value = t[2:].partition('=')
        if t.startswith('--') and name and 'output'.startswith(name):
            if eq:
                outs.append(tail_word(w, value))
            elif k + 1 < len(words):
                k += 1
                outs.append(words[k])
        elif t.startswith('-o'):
            if t[2:]:
                outs.append(tail_word(w, t[2:]))
            elif k + 1 < len(words):
                k += 1
                outs.append(words[k])
        k += 1
    return outs


def archive_outputs(gc):
    """(outputs, read) for git archive `gc`: the Words its -o/--output name, those git's first pass
    opens (archive_opened) and, failing closed, those the later pass would read (-vo <f>, an -o
    after `--prefix`'s value), and True when the archive only reads the repository it runs in: no
    option the guard does not know (a --no-* among them) and no word that is not literal (an unquoted $X may hold -o) or that PowerShell or cmd
    may split (native_unsure). An unknown option does not end the scan, so an -o after it is still
    an output. Every word is read as the shell hands it to git (archive_words)."""
    words = archive_words(gc)
    read = not any(native_unsure(gc.inv.cmd, w) for w in words)
    outs, k = [], 0
    while k < len(words):
        w, t = words[k], words[k].text
        if w.dyn:
            read = False
        if t == '--':
            read = read and not any(x.dyn for x in words[k + 1:])
            break
        if t.startswith('--'):
            name, eq, value = t[2:].partition('=')
            opt = archive_long(name)
            if opt is None:
                read = False
            if opt in ARCHIVE_VALUES and not eq:
                k += 1
                if k < len(words):
                    w, value = words[k], words[k].text
                    read = read and not w.dyn
                else:
                    w = None
            if opt == 'output' and w is not None:
                outs.append(tail_word(w, value) if eq else w)
        elif t.startswith('-') and len(t) > 1:
            for j, c in enumerate(t[1:], 1):
                if c == 'o':
                    if t[j + 1:]:
                        outs.append(tail_word(w, t[j + 1:]))
                    elif k + 1 < len(words):
                        k += 1
                        outs.append(words[k])
                        read = read and not words[k].dyn
                    break
                if c not in ARCHIVE_FLAGS:
                    read = False
                    break
        k += 1
    spans = {(o.text, o.start, o.end) for o in outs}
    outs += [o for o in archive_opened(words) if (o.text, o.start, o.end) not in spans]
    return outs, read


def strict_read_opts(sub, words):
    """True when every option of branch, tag or config is one GIT_STRICT_READ_OPTS or
    CONFIG_STRICT_READ_OPTS names in full: git expands an abbreviation (branch --unset-up), so an
    option the guard does not know may be a write."""
    if sub == 'config':
        known, cluster = CONFIG_STRICT_READ_OPTS, None
    else:
        known, cluster = GIT_STRICT_READ_OPTS[sub]
    return all(t.split('=', 1)[0] in known or cluster is not None and cluster.fullmatch(t)
               for t in words if t.startswith('-'))


def git_reads(gc, strict=False):
    """True when git `gc` changes nothing in the checkout it runs in. `strict` (a checkout the text
    cannot place) counts only what is plainly a read: no computed word but those strict_dyn_read
    allows, no PowerShell splat (ps_splat), no --output file, and branch, tag and config only with
    options it names in full (strict_read_opts). symbolic-ref reads with one ref and only
    SYMREF_READ_OPTS."""
    sub = gc.sub
    words = [w.text for w in gc.rest]
    # a word bash changes (`\--output=f`) is also read as the shell hands it to git (Word.alt)
    passed = words + [w.alt for w in gc.rest if w.alt is not None]
    if strict and (any(ps_splat(gc, w) for w in gc.rest) or any(writes_output_file(t) for t in passed)):
        return False
    if strict and any(w.dyn for w in gc.rest) and not strict_dyn_read(gc):
        return False
    if sub == 'archive':
        # its -o file is a write: git_targets hands it to git_jail, which judges it first; where
        # the checkout cannot be placed, neither can a relative -o, so it is no plain read
        outs, read = archive_outputs(gc)
        return read and not (strict and outs) and not archive_filter_set(gc)
    if sub in GIT_READS:
        return True
    if sub == 'stash':
        return stash_reads(gc.rest)
    pos = [t for t in words if not t.startswith('-')]
    if sub in GIT_READ_VERBS:
        return (pos[0] if pos else None) in GIT_READ_VERBS[sub]
    if strict and sub in ('branch', 'tag', 'config') and not strict_read_opts(sub, words):
        return False
    if sub in ('branch', 'tag'):
        flags = {t.split('=', 1)[0] for t in words if t.startswith('-')}
        writes = flags & GIT_REF_WRITE_FLAGS
        if sub == 'branch':
            writes -= {'-a'}                    # branch -a lists every branch
        return not writes and (not pos or bool(flags & GIT_LIST_FLAGS))
    if sub == 'config':
        return config_reads(gc)
    if sub == 'apply':
        return apply_checks(gc)
    if sub == 'symbolic-ref':
        return len(pos) == 1 and all(t in SYMREF_READ_OPTS for t in words if t.startswith('-'))
    return False


def apply_checks(gc):
    """True when git apply `gc` only checks or reports on its patch: one of APPLY_REPORTS among its
    options (the words before the first --; after it git reads every word as a patch file,
    `--check` too) and every option one APPLY_CHECK_OPTS names (or -p<n>; --apply, which applies
    after all, is not one), read as the shell hands it to git (archive_words), and no
    word the guard cannot read (computed, a PowerShell splat, or one PowerShell or cmd may split:
    native_unsure). Any other apply writes the checkout."""
    if gc.inv is None:
        return False
    words = archive_words(gc)
    if any(w.dyn or ps_splat(gc, w) or native_unsure(gc.inv.cmd, w) for w in words):
        return False
    texts = [w.text for w in words]
    if '--' in texts:                           # git reads no option after --: those words are patches
        texts = texts[:texts.index('--')]
    opts = [t for t in texts if t.startswith('-') and t != '-']
    return any(t in APPLY_REPORTS for t in opts) and all(t in APPLY_CHECK_OPTS or APPLY_STRIP_RE.match(t)
                                                          for t in opts)


def config_reads(gc):
    """True when git config `gc` carries a read flag (CONFIG_READ_OPTS, spelt in full) as its own
    option. git config reads options only up to its first word that is no option (git 2.55,
    observed: `config a.b --list` sets a.b to "--list"), so the flag counts only there, before any
    `--` and not as the value of an option that takes one (-f/--file, -t/--type, --blob, --default,
    --comment, --value, --url, abbreviated too: _config_long). A computed word there ends the scan
    (fail closed), as does a first `--`, which git 2.55 passes over (`config -- --list` lists)."""
    words, k = gc.rest, 0
    while k < len(words):
        w, t = words[k], words[k].text
        if w.dyn or t == '--' or len(t) < 2 or not t.startswith('-'):
            return False
        if t in CONFIG_READ_OPTS:
            return True
        if t.startswith('--'):
            body, eq, _v = t[2:].partition('=')
            if not eq and any(n in CONFIG_LONG_VALUES for n in _config_long(body)):
                k += 1                          # its value, whatever it says
        else:
            at = min((t.find(c, 1) for c in 'ft' if c in t[1:]), default=-1)
            if at == len(t) - 1:
                k += 1                          # -f <file>, -t <type>: the value is the next word
        k += 1
    return False


def git_targets(gc):
    """[Word]: the folders and files git `gc` writes besides the checkout it runs in (worktree add,
    clone, archive's -o/--output file: archive_outputs)."""
    args = gc.rest
    if gc.sub == 'archive':
        return archive_outputs(gc)[0]
    if gc.sub == 'worktree' and args and args[0].text == 'add':
        rest, skip = args[1:], {'-b', '-B', '--reason', '--orphan'}
    elif gc.sub == 'clone':
        rest, skip = args, {'-b', '--branch', '-o', '--origin', '--depth', '--reference', '-c', '--config',
                            '--template', '-u', '--upload-pack', '--separate-git-dir', '--filter', '-j', '--jobs',
                            '--shallow-since', '--shallow-exclude', '--reference-if-able', '--server-option'}
    else:
        return []
    pos, k = [], 0
    while k < len(rest):
        t = rest[k].text
        if t in skip:
            k += 2
            continue
        if not t.startswith('-'):
            pos.append(rest[k])
        k += 1
    if gc.sub == 'worktree':
        return pos[:1]
    return pos[1:2]
