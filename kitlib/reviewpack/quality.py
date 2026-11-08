"""Quality signals for review_pack (docs/review-pack.md "Quality signals"): non-test lines before
and after per changed file and the branch's net, a new file over the size line, growth over 50%
past 500 lines, and a deleted test file or method whose commit message says no "covered by".

Printed only: a signal never refuses and never changes the exit status. `read` is the pack's
`git(root, *args)` (stdout text, raising on failure), so this module never imports
kitlib.reviewpack.
"""
import collections
import fnmatch
import posixpath
import re

from kitlib.git import batch
from kitlib.config.policy import DEFAULT_TEST_SOURCES

NEW_FILE_LINE = 1500        # a new non-test file past this many non-test lines is flagged
GROWTH_PCT = 50             # growth past this percentage ...
GROWTH_FLOOR = 500          # ... that takes the file past this many lines is flagged
ROWS = 40                   # per-file rows printed, largest change first
COVERED = 'covered by'
MARK = '\x00'

# The modulemap's default test paths, plus the file names it lacks.
TEST_SOURCES = DEFAULT_TEST_SOURCES + ['*.spec.*', '*.test.*', '*Test.cs']
_PY = re.compile(r'^\s*(?:async\s+)?def\s+(test\w*)\s*\(')
_JS = re.compile(r'\b(?:it|test)\(\s*([\'"`])(.+?)\1')
_METHOD = re.compile(r'\b(?:void|Task)\s+(\w+)\s*\(')
_ATTR = re.compile(r'^\s*(?:@Test\b|\[(?:Test|Fact|Theory|TestMethod)\b)')

Sizes = collections.namedtuple('Sizes', 'before after')


def is_test(path):
    """A test path: TEST_SOURCES matched on the lowercased path, as the module map's
    is_test_source() does."""
    low = path.lower()
    return any(fnmatch.fnmatchcase(low, g.lower()) for g in TEST_SOURCES)


def non_test_lines(data):
    """Non-blank lines of `data` (bytes), or None for a binary file."""
    if b'\0' in data:
        return None
    return sum(1 for l in data.decode('utf-8', 'replace').splitlines() if l.strip())


def changed_paths(read, mb, branch):
    """[(status letter, path)] of every file changed between `mb` and `branch`, one read."""
    rows = (r.partition('\t') for r in read('diff', '--name-status', '--no-renames', mb, branch,
                                            '--').splitlines())
    return [(status[:1], path) for status, _, path in rows if path]


def file_sizes(changed, root, mb, branch):
    """{path: Sizes(before, after)} for every changed non-test text file in `changed`
    (changed_paths()); a side the file does not have is None. One `cat-file --batch`."""
    paths = [(s, p) for s, p in changed if not is_test(p)]
    specs = [('%s:%s' % (mb, p)) for s, p in paths if s != 'A']
    specs += [('%s:%s' % (branch, p)) for s, p in paths if s != 'D']
    got, why = batch.cat_file(specs, root)
    if got is None:
        raise RuntimeError(why)

    def lines(rev, path):
        hit = got.get('%s:%s' % (rev, path))
        return non_test_lines(hit[1]) if hit and hit[0] == 'blob' else None
    sizes = {}
    for status, path in paths:
        before = None if status == 'A' else lines(mb, path)
        after = None if status == 'D' else lines(branch, path)
        if before is not None or after is not None:
            sizes[path] = Sizes(before, after)
    return sizes


def size_flags(sizes):
    """One SIGNAL line per new file over NEW_FILE_LINE and per file that grew past GROWTH_PCT and
    GROWTH_FLOOR."""
    out = []
    for path, s in sorted(sizes.items()):
        if s.before is None and (s.after or 0) > NEW_FILE_LINE:
            out.append('  SIGNAL new file over %d non-test lines: %s (%d)'
                       % (NEW_FILE_LINE, path, s.after))
        elif s.before and s.after and s.after * 100 > s.before * (100 + GROWTH_PCT) \
                and s.after > GROWTH_FLOOR:
            out.append('  SIGNAL grew over %d%% and past %d lines: %s %d -> %d'
                       % (GROWTH_PCT, GROWTH_FLOOR, path, s.before, s.after))
    return out


def size_lines(sizes):
    """The `size` header, with the net, and the per-file rows."""
    if not sizes:
        return []
    before = sum(s.before or 0 for s in sizes.values())
    after = sum(s.after or 0 for s in sizes.values())
    out = ['size     %d non-test file(s) | lines %d -> %d | net %+d' % (len(sizes), before, after,
                                                                       after - before)]
    ranked = sorted(sizes.items(), key=lambda kv: -abs((kv[1].after or 0) - (kv[1].before or 0)))
    for path, s in ranked[:ROWS]:
        if s.before is None:
            out.append('    new %-6d %s' % (s.after, path))
        elif s.after is None:
            out.append('    deleted (was %d) %s' % (s.before, path))
        elif s.after != s.before:
            out.append('    %d -> %d %s' % (s.before, s.after, path))
    if len(ranked) > ROWS:
        out.append('    ... %d more' % (len(ranked) - ROWS))
    return out


def test_names(path, text_lines):
    """Test method names declared on `text_lines` (diff lines without their marker) of the test
    file `path`, by its language. A Java or C# method counts after its test attribute."""
    ext = posixpath.splitext(path)[1].lower()
    names, armed = set(), False
    for l in text_lines:
        if ext == '.py':
            m = _PY.match(l)
            if m:
                names.add(m.group(1))
        elif ext in ('.java', '.cs'):
            if _ATTR.match(l):
                armed = True
                continue
            m = _METHOD.search(l)
            if m and armed:
                names.add(m.group(1))
            if m or not (l.strip().startswith(('@', '[')) or not l.strip()):
                armed = False
        elif ext in ('.js', '.ts', '.jsx', '.tsx', '.mjs'):
            names.update(m.group(2) for m in _JS.finditer(l))
    return names


def _commit_changes(chunk):
    """(message, {path: (deleted file, removed lines, added lines)}) of one `git log -p` chunk
    `<sha>\\x01<message>\\x01<patch>`."""
    _sha, _, rest = chunk.partition('\x01')
    message, _, patch = rest.partition('\x01')
    files, cur = {}, None
    for l in patch.splitlines():
        if l.startswith('diff --git '):
            path = l.rsplit(' b/', 1)[-1]
            cur = files[path] = [False, [], []] if is_test(path) else None
        elif cur is None:
            continue
        elif l.startswith('deleted file mode'):
            cur[0] = True
        elif l.startswith('-') and not l.startswith('---'):
            cur[1].append(l[1:])
        elif l.startswith('+') and not l.startswith('+++'):
            cur[2].append(l[1:])
    return message, {p: tuple(v) for p, v in files.items() if v}


def deleted_tests(log):
    """Deleted test files and removed test methods whose own commit's message has no "covered by",
    as 'path' or 'path::name', in order, from the range's `git log -p` text `log` (section()). A
    method also added anywhere on the branch moved, and is not a deletion."""
    added, gone = set(), []
    for chunk in log.split(MARK)[1:]:
        message, files = _commit_changes(chunk)
        covered = COVERED in message.lower()
        for path, (deleted, minus, plus) in files.items():
            added |= test_names(path, plus)
            if covered:
                continue
            names = test_names(path, minus)
            if deleted:
                gone.append((path, None, names))
            else:
                gone.extend((path, n, None) for n in sorted(names - test_names(path, plus)))
    out = []
    for path, name, names in gone:
        if name is None and names and names <= added:
            continue
        if name is not None and name in added:
            continue
        out.append(path if name is None else '%s::%s' % (path, name))
    return list(dict.fromkeys(out))


def not_checked(what, e):
    return 'quality  NOT CHECKED (%s): %s' % (what, (str(e).splitlines() or [type(e).__name__])[0])


def section(read, root, mb, branch):
    """The pack's quality lines (empty when there is nothing to say). A failed read gives a
    NOT CHECKED line for what it fed, never an error: the signals do not change the exit status."""
    out = []
    try:
        changed = changed_paths(read, mb, branch)
        sizes = file_sizes(changed, root, mb, branch)
        out = size_lines(sizes) + size_flags(sizes)
    except Exception as e:      # noqa: BLE001 - printed only, as the gate line is
        return [not_checked('sizes', e)]
    # Added test files too: a test moved into one is not a deletion.
    tests = [p for s, p in changed if is_test(p)]
    if not any(s != 'A' for s, p in changed if is_test(p)):
        return out
    try:
        log = read('log', '-p', '--no-renames', '--format=%x00%H%x01%B%x01',
                   '%s..%s' % (mb, branch), '--', *tests)
        gone = deleted_tests(log)
    except Exception as e:      # noqa: BLE001
        return out + [not_checked('deleted tests', e)]
    out += ['  SIGNAL deleted test, no "%s" in its commit message: %s' % (COVERED, g)
            for g in gone[:ROWS]]
    if len(gone) > ROWS:
        out.append('  ... %d more deleted tests' % (len(gone) - ROWS))
    return out
