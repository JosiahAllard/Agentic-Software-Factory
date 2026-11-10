"""Static state and caller counts of a branch's changed files, for review_pack's quality signals
(docs/review-pack.md "Quality signals"). Heuristics over text:
- static_fields(): Java and C# `static` fields that are not `final`, `readonly` or `const`; a line
  is a field when `=` or `;` comes before any `(`, and its key is its words up to that mark.
- global_rebinds(): Python `global` names, keyed by their function.
- public_symbols(): Java and C# `public` types and methods (not an override, not `main`), read with
  the code map's TYPE and MEMBER (kitlib/docs/codemap.py: a method indented 2-8 spaces), and
  Python module and class level defs and classes whose name has no leading `_`.
Each is new on a branch when its key is in no merge-base text of the changed files, so code moved
from one changed file to another is not new. grep_hits() is the one `git grep` that finds every
line naming the new symbols, and callers() sorts those lines.
"""
import ast
import collections
import posixpath
import re

from kitlib.docs import codemap
from kitlib.git import cmd as gitcmd

GREP_TIMEOUT = 60       # seconds the one `git grep` may take before the callers read NOT CHECKED
Found = collections.namedtuple('Found', 'key path line text')

_NOT_MUTABLE = {'final', 'readonly', 'const', 'class', 'interface', 'enum', 'record', 'struct',
                'import', 'using', 'return', 'delegate'}


def _ext(path):
    return posixpath.splitext(path)[1].lower()


def static_fields(path, text):
    """[Found] of the static mutable fields of a Java or C# `text`."""
    out = []
    for n, raw in enumerate(text.splitlines(), 1):
        s = raw.strip()
        if s.startswith(('//', '*', '/*', '@', '[')):
            continue
        cut = min((i for i in (s.find('='), s.find(';'), s.find('(')) if i >= 0), default=-1)
        if cut < 0 or s[cut] == '(' or s[cut:cut + 2] in ('=>', '=='):
            continue
        head = s[:cut].split()
        if 'static' in head and len(head) >= 3 and not _NOT_MUTABLE & set(head) \
                and re.match(r'^[A-Za-z_$][\w$]*$', head[-1]) and '{' not in s[:cut]:
            out.append(Found(' '.join(head), path, n, s[:cut].strip()))
    return out


def global_rebinds(path, text):
    """[Found] of the `global` names of a Python `text`, keyed by (function, name)."""
    try:
        node = ast.parse(text, filename=path)
    except (SyntaxError, ValueError):
        return []
    fns = (ast.FunctionDef, ast.AsyncFunctionDef)
    out = []
    for fn in ast.walk(node):
        if not isinstance(fn, fns):
            continue
        todo = list(fn.body)        # this function's own statements, not a nested function's
        while todo:
            g = todo.pop()
            if isinstance(g, ast.Global):
                out.extend(Found((fn.name, name), path, g.lineno, name) for name in g.names)
            elif not isinstance(g, fns):
                todo.extend(ast.iter_child_nodes(g))
    return sorted(out, key=lambda f: f.line)


def type_match(line):
    """codemap.TYPE's match of a Java or C# type declared on `line`, or None. TYPE's Kotlin
    keyword `object` is a type name in Java and C# (`public object Value;`), never a declaration."""
    m = codemap.TYPE.match(line)
    return m if m and m.group(1) != 'object' else None


def public_symbols(path, text):
    """[Found] of the public types and methods `text` declares, keyed by name."""
    if _ext(path) == '.py':
        try:
            node = ast.parse(text, filename=path)
        except (SyntaxError, ValueError):
            return []
        kinds = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        tops = [n for n in node.body if isinstance(n, kinds)]
        tops += [m for n in node.body if isinstance(n, ast.ClassDef) for m in n.body
                 if isinstance(m, kinds)]
        return [Found(n.name, path, n.lineno, n.name) for n in tops if not n.name.startswith('_')]
    out, prev = [], ''
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.expandtabs(4)        # MEMBER wants 2-8 spaces of indent; a tab is one level
        m, g = type_match(line), 2
        if m is None:
            m, g = codemap.MEMBER.match(line), 1
        head = line[:m.start(1)].split() if m else []   # modifiers (and a method's return type)
        if m and 'public' in head and 'override' not in head and prev != '@Override' \
                and m.group(g) != 'main':
            out.append(Found(m.group(g), path, n, m.group(g)))
        if raw.strip():
            prev = raw.strip()
    return out


def new_found(find, base, tip):
    """[Found] that `find(path, text)` gives over `tip` ({path: text}) whose key it gives for no
    text of `base`, in path and line order."""
    old = {f.key for p, t in base.items() for f in find(p, t)}
    return [f for p in sorted(tip) for f in find(p, tip[p]) if f.key not in old]


def grep_hits(root, branch, names):
    """[(path, line, text)] of every line of a Java, C# or Python file at `branch` naming one of
    `names` as a whole word: one `git grep`, the names on stdin (no command-line limit). Raises
    RuntimeError when it fails or takes past GREP_TIMEOUT."""
    stdin = ''.join(n + '\n' for n in names).encode('utf-8')
    r = gitcmd.call(('-c', 'core.quotepath=false', 'grep', '-n', '-w', '-F', '-I', '-f', '-',
                     branch, '--', '*.java', '*.cs', '*.py'), root, stdin=stdin,
                    timeout=GREP_TIMEOUT)
    if r.rc == 1:
        return []
    if r.rc != 0:
        first = (r.err or '').strip().splitlines() or ['(nothing on stderr)']
        raise RuntimeError('`git grep` %s: %s' % (
            'exit %d' % r.rc if r.rc is not None else 'did not finish', first[0]))
    hits, prefix = [], branch + ':'
    for l in gitcmd.text(r.out).splitlines():
        path, _, rest = l[len(prefix):].partition(':') if l.startswith(prefix) else ('', '', '')
        num, _, text = rest.partition(':')
        if num.isdigit():
            hits.append((path, int(num), text))
    return hits


def callers(symbols, hits, is_test):
    """[(Found, 'one', (path, line)) or (Found, 'tests', count)] for each name of `symbols` (its
    first declaration, so overloads give one row) named on exactly one non-test line of `hits`
    (grep_hits()), or on test lines only (`is_test(path)`). A symbol's own declaration line is not
    a caller."""
    if not symbols:
        return []
    word = re.compile(r'(?<![\w$])(%s)(?![\w$])'
                      % '|'.join(re.escape(n) for n in sorted({f.key for f in symbols},
                                                              key=len, reverse=True)))
    own = {(f.path, f.line, f.key) for f in symbols}
    seen = collections.defaultdict(lambda: ([], []))
    for path, num, text in hits:
        for name in set(word.findall(text)):
            if (path, num, name) not in own:
                seen[name][bool(is_test(path))].append((path, num))
    out, done = [], set()
    for f in symbols:
        if f.key in done:
            continue
        done.add(f.key)
        plain, tests = seen.get(f.key, ([], []))
        if len(plain) == 1:
            out.append((f, 'one', plain[0]))
        elif not plain and tests:
            out.append((f, 'tests', len(tests)))
    return out
