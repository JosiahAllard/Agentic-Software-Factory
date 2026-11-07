"""The guard's reading of the programs that write files from their own options (kitlib.guard.rules,
which re-exports each name here): sed, perl, awk, ruby and the editors in place, curl and wget,
patch, rsync, tar, unzip, the copiers, the eval forms and python, and the targets each one writes."""
import ntpath
import posixpath
import re

from . import paths
from . import shell as sh
from .rules_base import WIN_OPT_RE, here, ps_native_unsure, tail_word


def _cluster_values(args, value_shorts, value_longs, k):
    """Step over one option at args[k]: (next k, [(option, value Word or None)])."""
    t = args[k].text
    if t.startswith('--'):
        name, eq, val = t.partition('=')
        if eq:
            return k + 1, [(name, sh.Word(val, args[k].dyn, args[k].start, args[k].end))]
        if name in value_longs:
            return k + 2, [(name, args[k + 1] if k + 1 < len(args) else None)]
        return k + 1, [(name, None)]
    out = []
    body = t[1:]
    for j, c in enumerate(body):
        if c in value_shorts:
            rest = body[j + 1:]
            if rest:
                return k + 1, out + [('-' + c, sh.Word(rest, args[k].dyn, args[k].start, args[k].end))]
            return k + 2, out + [('-' + c, args[k + 1] if k + 1 < len(args) else None)]
        out.append(('-' + c, None))
    return k + 1, out


def unix_opts(args, value_shorts='', value_longs=()):
    """([(option, value Word or None)], [positional Word]) of a Unix command's arguments."""
    opts, pos = [], []
    k = 0
    while k < len(args):
        t = args[k].text
        if t == '--':
            pos.extend(args[k + 1:])
            break
        if t.startswith('-') and len(t) > 1:
            k, got = _cluster_values(args, value_shorts, value_longs, k)
            opts.extend(got)
            continue
        pos.append(args[k])
        k += 1
    return opts, pos


def sed_writes(args):
    in_place = False
    for w in args:
        t = w.text
        if t.startswith('--in-place'):
            in_place = True
        elif t.startswith('-') and not t.startswith('--') and 'i' in t:
            body = t[1:]
            stop = min([body.index(c) for c in 'efl' if c in body] or [len(body)])
            in_place = in_place or body.index('i') < stop
    if not in_place:
        return []
    opts, pos = unix_opts(args, 'efl', ('--expression', '--file', '--line-length'))
    script = any(o in ('-e', '-f', '--expression', '--file') for o, _v in opts)
    return pos if script else pos[1:]


def perl_writes(args, cmd_word=None):
    in_place, script, pos = False, False, []
    k = 0
    while k < len(args):
        t = args[k].text
        if t == '--':
            pos.extend(args[k + 1:])
            break
        if t.startswith('-') and len(t) > 1:
            body = t[1:]
            k += 1
            for j, c in enumerate(body):
                if c == 'i':
                    in_place = True
                    break                       # -i.bak: the rest is the suffix
                if c in 'eEMmI':
                    script = script or c in 'eE'
                    if not body[j + 1:]:
                        k += 1                  # the value is the next word
                    break
            continue
        pos.append(args[k])
        k += 1
    if not in_place:
        return [here(cmd_word)] if script else []     # perl -e: as python -c
    return pos if script else pos[1:]


# gawk 5's options (its getopt list "+F:f:v:W;bcCd::D::e:E:ghi:l:L::nNo::Op::MPrSstVYZ:", options
# ending at the first operand): the short ones that take a value, the ones whose value is only the
# rest of their word, and the long names (a unique prefix names one) that take a value, take one
# only after `=`, or take none
AWK_VALUE_SHORTS = 'FfvieElZ'
AWK_OPTIONAL_SHORTS = 'dDLop'
AWK_LONGS = {'assign': 'v', 'exec': 'E', 'field-separator': 'F', 'file': 'f', 'include': 'i', 'load': 'l',
             'source': 'e', 'locale': 'Z'}
AWK_OPTIONAL_LONGS = {'dump-variables': 'd', 'debug': 'D', 'lint': 'L', 'pretty-print': 'o', 'profile': 'p'}
AWK_FLAG_LONGS = ('bignum', 'characters-as-bytes', 'copyright', 'csv', 'gen-pot', 'help', 'lint-old',
                  'no-optimize', 'non-decimal-data', 'optimize', 'parsedebug', 'persist', 'posix',
                  're-interval', 'sandbox', 'trace', 'traditional', 'use-lc-numeric', 'version')
AWK_ASSIGN_RE = re.compile(r'^[A-Za-z_]\w*(?:::[A-Za-z_]\w*)?=')   # an operand gawk reads as var=value


def _awk_inplace_lib(w):
    """True when the -i/-f/-l value Word `w` may load gawk's inplace library: its name, or a value
    the line computes (fail closed)."""
    if w is None or w.dyn:
        return w is not None
    base = re.split(r'[\\/]', w.text)[-1].lower()
    return base in ('inplace', 'inplace.awk')


def _awk_inplace_text(w):
    """True when the program text Word `w` may @include the inplace library."""
    return w is not None and not w.dyn and '@include' in w.text and 'inplace' in w.text


def _awk_long(name):
    """The letter the long option `name` (without its dashes) stands for, or None when gawk does
    not know it or it is ambiguous (gawk then runs nothing)."""
    names = list(AWK_LONGS) + list(AWK_OPTIONAL_LONGS) + list(AWK_FLAG_LONGS)
    if name in names:
        hits = [name]
    else:
        hits = [n for n in names if n.startswith(name)]
    if len(hits) != 1:
        return None
    return AWK_LONGS.get(hits[0]) or AWK_OPTIONAL_LONGS.get(hits[0]) or ''


def awk_writes(args, cmd_word):
    """What awk/gawk writes: every file operand under the inplace library (`-i inplace` in any
    spelling, `-f inplace`, `-l inplace`, `@include "inplace"` in the program text; counted even
    with -v inplace::enable=0), and the file -d, -o or -p names (else the directory it runs in,
    for awkvars.out or awkprof.out). A run that is neither writes nothing."""
    in_place, program_given, dumps = False, False, []
    k = 0

    def take(letter, value):
        nonlocal in_place, program_given
        if letter in 'ifl' and _awk_inplace_lib(value):
            in_place = True
        if letter in 'feE':
            program_given = True
        if letter == 'e' and _awk_inplace_text(value):
            in_place = True
        if letter in 'dop':
            dumps.append(value if value is not None and value.text else here(cmd_word))

    def word(t, w):
        return sh.Word(t, w.dyn, w.start, w.end)

    while k < len(args):
        w = args[k]
        t = w.text
        if t == '--':
            k += 1
            break
        if not t.startswith('-') or len(t) < 2:
            break
        k += 1
        if t.startswith('--'):
            spec = t[2:]
        else:
            body, spec = t[1:], None
            for j, c in enumerate(body):
                rest = body[j + 1:]
                if c == 'W':
                    if rest:
                        spec = rest
                    elif k < len(args):
                        spec = args[k].text
                        w = args[k]
                        k += 1
                    break
                if c in AWK_VALUE_SHORTS:
                    if rest:
                        value = word(rest, w)
                    else:
                        value = args[k] if k < len(args) else None
                        k += 1
                    take(c, value)
                    break
                if c in AWK_OPTIONAL_SHORTS:
                    take(c, word(rest, w) if rest else None)
                    break
            if spec is None:
                if c == 'E':
                    break                       # -E file: the rest are the program's arguments
                continue
        name, eq, val = spec.partition('=')     # --name[=value], or -W name[=value]
        letter = _awk_long(name)
        if letter is None or letter == '':
            continue
        if eq:
            value = word(val, w)
        elif letter in AWK_OPTIONAL_SHORTS:
            value = None
        else:
            value = args[k] if k < len(args) else None
            k += 1
        take(letter, value)
        if letter == 'E':
            break
    rest = args[k:]
    if not program_given and rest:
        if _awk_inplace_text(rest[0]):
            in_place = True
        rest = rest[1:]
    if not in_place:
        return dumps
    files = [w for w in rest if w.dyn or (w.text != '-' and not AWK_ASSIGN_RE.match(w.text))]
    return dumps + files


def ruby_writes(args, cmd_word):
    """ruby -i[ext]: the files it edits, as perl -i (after the script unless -e gives the code),
    placed under each -C or -x folder in turn; without -i, code on the line (eval_writes)."""
    code = eval_writes('ruby', args, cmd_word)
    in_place, script, pos, dirs = False, False, [], []
    k = 0
    while k < len(args):
        w = args[k]
        t = w.text
        if t == '--':
            pos.extend(args[k + 1:])
            break
        if t.startswith('--') or t == '-':
            if t == '-':
                pos.append(w)
            k += 1
            continue                            # a long option: any value it takes is an operand (fail closed)
        if t.startswith('-') and len(t) > 1:
            body = t[1:]
            k += 1
            for j, c in enumerate(body):
                rest = body[j + 1:]
                if c == 'i':
                    in_place = True
                    break                       # -i.bak: the rest is the suffix
                if c in 'eIrCE':
                    script = script or c == 'e'
                    value = sh.Word(rest, w.dyn, w.start, w.end) if rest else (args[k] if k < len(args) else None)
                    if not rest:
                        k += 1
                    if c == 'C' and value is not None:
                        dirs.append(value)
                    break
                if c == 'x':
                    if rest:
                        dirs.append(sh.Word(rest, w.dyn, w.start, w.end))
                    break
                if c in 'FWT0K':
                    break                       # their value is the rest of the word
            continue
        pos.append(w)
        k += 1
    if not in_place:
        return code
    files = pos if script else pos[1:]
    for d in reversed(dirs):
        files = [_under(d, f) for f in files]
    return code + files


# vi, vim, nvim, ex and ed: a scripted run writes the files it is given, so every file operand
# counts, an operand-only read too; the options whose value is the next word (a short one last in
# its word, as `-sc <cmd>`) are passed over, bar -w/-W's script output file, which is written
EDITOR_VALUE_SHORTS = 'cuUiTtwW'


def editor_writes(name, args):
    out, k = [], 0
    while k < len(args):
        w = args[k]
        t = w.text
        if t == '--':
            out.extend(args[k + 1:])
            break
        if name != 'ed' and t.startswith('+'):
            k += 1                              # +cmd
            continue
        if t.startswith('-') and len(t) > 1:
            short = not t.startswith('--')
            if (t == '-p') if name == 'ed' else (t == '--cmd' or (short and t[-1] in EDITOR_VALUE_SHORTS)):
                if short and t[-1] in 'wW' and k + 1 < len(args):
                    out.append(args[k + 1])     # the script output file
                k += 2
            else:
                k += 1
            continue
        out.append(w)
        k += 1
    return [w for w in out if w.dyn or w.text != '-']


def curl_writes(args, cmd_word):
    out = []
    opts, _pos = unix_opts(args, 'oDcHduXAebTFxmrwKEQYyzCtP',
                           ('--output', '--dump-header', '--cookie-jar', '--header', '--data', '--user',
                            '--request', '--user-agent', '--referer', '--cookie', '--upload-file', '--form',
                            '--proxy', '--max-time', '--range', '--write-out', '--config', '--cert',
                            '--quote', '--time-cond', '--url', '--output-dir'))
    folder = None
    for o, v in opts:
        if o == '--output-dir' and v is not None:
            folder = v
    for o, v in opts:
        if o in ('-o', '--output') and v is not None:
            if v.text != '-':
                placed = _under(folder, v)
                out.append(placed)
                if folder is not None and placed is v:
                    out.append(Under(folder, v))    # curl joins --output-dir to an absolute name too
        elif o in ('-D', '--dump-header', '-c', '--cookie-jar') and v is not None:
            if v.text != '-':
                out.append(v)
        elif o in ('-O', '--remote-name', '--remote-name-all'):
            out.append(folder if folder is not None else here(cmd_word))
    return out


class Under(sh.Word):
    """A file a folder option places (_under): its text is the folder's joined with the name's;
    `folder` and `name` are the two words, which only the refusal's rewrite reads (word_rewrite)."""
    __slots__ = ('folder', 'name')

    def __init__(self, folder, name):
        cut = '/' if paths.posix() else '/\\'          # on Linux `\` is a name's character
        sh.Word.__init__(self, folder.text.rstrip(cut) + '/' + name.text, folder.dyn or name.dyn,
                         name.start, name.end)
        self.folder, self.name = folder, name


def _under(folder, w):
    """`w` placed in `folder` (curl --output-dir), unless it is absolute (on Linux: starts with `/`)."""
    if folder is None or (w.text.startswith('/') if paths.posix() else re.match(r'^(?:[A-Za-z]:)?[\\/]', w.text)):
        return w
    return Under(folder, w)


def wget_writes(args, cmd_word):
    out, prefix = [], None
    opts, pos = unix_opts(args, 'OoaPiBetTwQUlA',
                          ('--output-document', '--output-file', '--append-output', '--directory-prefix',
                           '--input-file', '--base', '--execute', '--tries', '--timeout', '--wait',
                           '--quota', '--user-agent', '--level', '--accept', '--reject', '--user', '--password'))
    if any(o == '--spider' for o, _v in opts):
        return []                               # checks the URL, writes nothing
    document = False
    for o, v in opts:
        if o in ('-O', '--output-document'):
            document = True
            if v is not None and v.text != '-':
                out.append(v)
        elif o in ('-o', '--output-file', '-a', '--append-output') and v is not None:
            out.append(v)
        elif o in ('-P', '--directory-prefix') and v is not None:
            prefix = v
    if not document and pos:
        out.append(prefix if prefix is not None else here(cmd_word))
    return out


def patch_writes(args, cmd_word):
    opts, _pos = unix_opts(args, 'odipFzBYVDrg',
                           ('--output', '--directory', '--input', '--strip', '--fuzz', '--prefix', '--suffix',
                            '--basename-prefix', '--version-control', '--reject-file', '--get'))
    names = [o for o, _v in opts]
    if '--dry-run' in names:
        return []
    for o, v in opts:
        if o in ('-o', '--output') and v is not None:
            return [v] if v.text != '-' else []
    for o, v in opts:
        if o in ('-d', '--directory') and v is not None:
            return [v]
    return [here(cmd_word)]


def rsync_writes(args):
    opts, pos = unix_opts(args, 'eTfB', ('--rsh', '--temp-dir', '--filter', '--exclude', '--include',
                                        '--exclude-from', '--include-from', '--files-from', '--block-size',
                                        '--partial-dir', '--backup-dir', '--suffix', '--chmod', '--log-file'))
    if any(o in ('-n', '--dry-run') for o, _v in opts):
        return []                               # a dry run lists, writes nothing
    return pos[-1:] if len(pos) >= 2 else []


# tar's short options that take no value in GNU tar and in bsdtar (Windows' tar.exe) alike, and
# those that take one besides -C; a letter in neither may take one. Long options known to take none.
TAR_FLAGS = set('xtcruvzjJZapkmOohPUwSBln')
TAR_VALUES = set('bfFgHIKLNTVX')
TAR_LONG_FLAGS = {'extract', 'get', 'list', 'create', 'append', 'update', 'verbose', 'gzip', 'gunzip', 'ungzip',
                  'bzip2', 'xz', 'lzma', 'zstd', 'lzip', 'compress', 'uncompress', 'auto-compress',
                  'keep-old-files', 'keep-newer-files', 'overwrite', 'preserve-permissions', 'same-permissions',
                  'no-same-owner', 'same-owner', 'no-same-permissions', 'unlink-first', 'absolute-names',
                  'dereference', 'totals', 'to-stdout', 'interactive', 'sparse', 'numeric-owner',
                  'no-recursion', 'touch', 'ignore-zeros', 'check-links', 'verify'}


class _TarDirs:
    """The folders a tar line may extract into: `bases` the folders in force (None is the working
    directory), `seen` every folder a -C named, `unsure` once a -C was read that may not be one."""

    def __init__(self):
        self.bases, self.seen, self.unsure = [None], [], False

    def move(self, folder, sure):
        """A -C to `folder`, relative to the one in force as tar reads it (-C /a -C b is /a/b);
        not `sure`: it may not be a -C, so the folders in force stay possible too."""
        new = []
        for b in self.bases:
            if paths.posix():                   # Linux: `\` is a name's character, no drives
                rooted, join = folder.text.startswith(('/', '~')), posixpath.join
            else:
                rooted = folder.text.startswith(('/', '\\', '~')) or ntpath.splitdrive(folder.text)[0]
                join = ntpath.join
            new.append(folder if b is None or rooted else
                       sh.Word(join(b.text, folder.text), folder.dyn or b.dyn, folder.start, folder.end))
        self.bases = new if sure else self.bases + new
        self.seen += new
        self.unsure = self.unsure or not sure


def _tar_old(args, dirs):
    """(index of the first word after the values, the state after them) for the old-style first
    word (tar xCf <dir> a.tar): each letter that takes a value takes the next word in turn. A letter
    the guard does not know may take one too, so a C after one names any of the words it could."""
    k, unknown = 1, 0
    for c in args[0].text:
        if c == 'C':
            for j in range(k, min(k + unknown + 1, len(args))):
                dirs.move(args[j], not unknown)
            k += 1
        elif c in TAR_VALUES:
            k += 1
        elif c not in TAR_FLAGS:
            unknown += 1
    return k, ('maybe' if unknown else None)


def _tar_word(args, k, state, dirs):
    """The state after tar's word args[k] (None, 'value': the next word is a value, 'maybe': it may
    be one, 'folder': it is -C's), moving `dirs` for a -C. A -C is sure only in a word that is no
    value of the word before it and, in a cluster, after letters that take none (-xvC <dir>);
    otherwise (-xfC <dir>: a file C; -NC<dir>: a date; --exclude -C<dir>) both readings stay."""
    w, t = args[k], args[k].text
    if state == 'folder':
        dirs.move(w, True)
        return None
    doubt = state is not None
    nxt = args[k + 1] if k + 1 < len(args) else None
    if t.startswith('--'):
        name, eq, value = t.partition('=')
        # --di is also --diff; bsdtar (Windows' tar.exe) reads --cd as -C
        if name == '--cd' or len(name) >= 5 and '--directory'.startswith(name):
            if eq:
                dirs.move(tail_word(w, value), not doubt)
                return None
            if not doubt:
                return 'folder'
            if nxt is not None:
                dirs.move(nxt, False)
            return 'maybe'
        return None if eq or name[2:] in TAR_LONG_FLAGS or state == 'value' else 'maybe'
    if not t.startswith('-') or len(t) < 2:
        return None
    clean = not doubt
    for j, c in enumerate(t[1:], 1):
        if c == 'C':
            rest = t[j + 1:]
            if clean:
                if rest:
                    dirs.move(tail_word(w, rest), True)
                    return None
                return 'folder'
            folder = tail_word(w, rest) if rest else nxt
            if folder is not None:
                dirs.move(folder, False)
            return 'maybe'
        if c not in TAR_FLAGS:
            clean = False
    if state == 'value':
        return None
    i = next((j for j, c in enumerate(t[1:], 1) if c not in TAR_FLAGS), None)
    if i is None:
        return None
    if t[i] not in TAR_VALUES:
        return 'maybe'
    if i + 1 < len(t):                          # -fa.tar: the value is attached
        return None
    return 'maybe' if doubt else 'value'


def _tar_stdout(t):
    """True when tar's word `t`, no value of the word before it, is -O/--to-stdout (GNU tar and
    bsdtar alike: -x prints each file and writes none): --to-stdout, or an O in a cluster before
    any letter that takes or may take a value (-xvOf; in -xfO the O is f's file)."""
    if t.startswith('--'):
        return t == '--to-stdout'
    if not t.startswith('-'):
        return False
    for c in t[1:]:
        if c == 'O':
            return True
        if c not in TAR_FLAGS:
            return False
    return False


def tar_writes(args, cmd_word):
    """[Word]: the folders tar -x writes: each folder a -C/--directory names, relative to the one
    before it (_TarDirs), with none the working directory, and the working directory too once a -C
    was read that may not be one (_tar_word, _tar_old: fail closed). None with -O/--to-stdout
    where tar reads it as that option (_tar_stdout; any O of the old-style first word)."""
    first = args[0].text if args else ''
    extract = bool(re.match(r'^[A-Za-z]*x', first)) and not first.startswith('-')       # tar xf a.tar
    dirs, k, state = _TarDirs(), 0, None
    stdout = extract and 'O' in first
    if extract:
        k, state = _tar_old(args, dirs)
    while k < len(args):
        t = args[k].text
        if t == '--' and state is None:
            break
        if t in ('--extract', '--get') or re.match(r'^-[A-Za-z]*x', t) and not t.startswith('--'):
            extract = True
        stdout = stdout or state is None and _tar_stdout(t)
        state = _tar_word(args, k, state, dirs)
        k += 1
    if not extract or stdout:
        return []
    if not dirs.seen:
        return [here(cmd_word)]
    return dirs.seen + ([here(cmd_word)] if dirs.unsure else [])


def unzip_writes(args, cmd_word):
    for k, w in enumerate(args):
        t = w.text
        if t == '-d' and k + 1 < len(args):
            return [args[k + 1]]
        if t.startswith('-') and not t.startswith('--') and re.search(r'[ltvZpz]', t[1:]):
            return []                           # lists, tests or prints to standard output
    return [here(cmd_word)]


def copier_writes(args):
    """robocopy src dst [files] [/opts]; xcopy src [dst] [/opts]: the destination Word."""
    pos = [w for w in args if not WIN_OPT_RE.match(w.text)]
    return pos[1:2]


def eval_writes(name, args, cmd_word):
    """node -e, ruby -e: code given on the line writes where it likes, as python -c does."""
    for w in args:
        if w.text in EVALS[name] or w.text.partition('=')[0] in EVALS[name]:
            return [here(cmd_word)]
        if not w.text.startswith('-'):
            return []                           # a script file
    return []


EVALS = {'node': ('-e', '--eval', '-p', '--print'), 'ruby': ('-e',)}
# python's short options that take a value: the rest of the word, or the next word (-c CODE,
# -cCODE, -Ic CODE); its long option that takes one
PY_SHORT_VALUE = 'cmWX'
PY_LONG_VALUE = {'--check-hash-based-pycs'}
# the py launcher's version choice (-3, -3.12, -3.12-64), taken before python's own options
PY_LAUNCHER_VER_RE = re.compile(r'^-\d+(\.\d+)*(-(32|64))?$')


def py_program(name, args):
    """(kind, value): how python reads its program from `args`. kind is 'code' (value the -c
    Word), 'module' (-m), 'stdin' (`-`), 'script' or 'none' (no program named: python reads
    standard input)."""
    k = 0
    if name == 'py' and args and PY_LAUNCHER_VER_RE.match(args[0].text):
        k = 1
    while k < len(args):
        w = args[k]
        t = w.text
        if t == '-':
            return 'stdin', None
        if t == '--':
            return ('script' if k + 1 < len(args) else 'none'), None
        if t.startswith('--'):
            k += 2 if t in PY_LONG_VALUE else 1
            continue
        if not t.startswith('-') or len(t) < 2:
            return 'script', None
        for j in range(1, len(t)):
            ch = t[j]
            if ch not in PY_SHORT_VALUE:
                continue
            if t[j + 1:]:
                value, k = sh.Word(t[j + 1:], w.dyn, w.start, w.end), k + 1
            else:
                value, k = (args[k + 1] if k + 1 < len(args) else None), k + 2
            if ch == 'c':
                return 'code', value
            if ch == 'm':
                return 'module', value
            break
        else:
            k += 1
    return 'none', None


def python_writes(cmd, name, args, cmd_word, via=None):
    return [here(cmd_word)] if python_unread(cmd, name, args, via) else []


PY_SP_WHY = 'Start-Process hands python a command line the guard does not read'
PY_PS_WHY = 'PowerShell may hand python other words than the guard read'
PY_UNREAD_WHY = 'the guard does not read what the code python is given writes'


def python_unread(cmd, name, args, via=None):
    """Why a python run counts as a write of the directory it runs in, or None when its own file
    decides what it writes (a script or module, or nothing to run)."""
    # Start-Process hands python one command line the guard re-split with its quotes taken out
    # (commands._split_list), so what python reads is not the words below
    if via == 'start-process':
        return PY_SP_WHY
    c = cmd
    while c is not None:                        # every shell on the way to python
        # a word PowerShell may hand on as other than one argument (dropped when empty, split at a
        # double quote): python's own words, or the text of a bash -c around it, are not the
        # ones read here
        if c.shell == sh.PS and any(ps_native_unsure(w) for w in c.words):
            return PY_PS_WHY
        c = c.nested_in
    kind, _ = py_program(name, args)
    if kind in ('code', 'stdin'):
        return PY_UNREAD_WHY                    # the guard does not read what the code writes
    if kind in ('module', 'script'):
        return None                             # its own file decides what it writes
    if cmd.piped_in or any(r.op in ('<<', '<<<', '<') for r in cmd.redirects):
        return PY_UNREAD_WHY                    # a script read from standard input
    return None
