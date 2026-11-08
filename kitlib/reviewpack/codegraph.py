"""Import cycles among a branch's changed files, for review_pack's quality signals
(docs/review-pack.md "Quality signals"). A heuristic over import lines, read with the module
map's detectors' regexes and parsers (kitlib/modulemap/detectors):
- Java: a file is its `package` line plus its file name; `import [static] a.b.C[.member|.*]`
  names the changed file of the longest such prefix, and `import a.b.*` every changed file of
  package a.b.
- C#: `using A.B;` names every changed file that declares namespace A.B, and `using static A.B.C;`
  (or an alias) the changed file that declares type C in it, so a C# cycle is a namespace cycle.
- Python: `import`s resolved as the python detector resolves them, over the tree's paths; a
  package resolves to its `__init__.py`.
Known misses: Java and C# files of one package or namespace that use each other with no import
line, fully qualified names, and computed Python imports.
"""
import ast
import posixpath
import re

from kitlib.modulemap.detectors import csharp_using, java_imports, python
from kitlib.modulemap.detectors.paths import Tree

_PACKAGE = re.compile(r'^[ \t]*package[ \t]+([\w.]+)[ \t]*;', re.M)
_IMPORT = re.compile(r'import[ \t]+(static[ \t]+)?([\w$.]+?)(\.\*)?[ \t]*;')
_NAMESPACE = re.compile(r'^[ \t]*namespace[ \t]+([\w.]+)', re.M)
_TYPE = re.compile(r'\b(?:class|struct|interface|record|enum)[ \t]+([A-Za-z_]\w*)')


def _ext(path):
    return posixpath.splitext(path)[1].lower()


def _longest(names, dotted):
    """The value of the longest dotted prefix of `dotted` that is a key of `names`, or None."""
    parts = dotted.split('.')
    for k in range(len(parts), 0, -1):
        hit = names.get('.'.join(parts[:k]))
        if hit is not None:
            return hit
    return None


def _java(texts):
    files, packages = {}, {}
    for p, t in texts.items():
        m = _PACKAGE.search(t)
        pkg = m.group(1) if m else ''
        name = posixpath.splitext(posixpath.basename(p))[0]
        files[pkg + '.' + name if pkg else name] = p
        packages.setdefault(pkg, []).append(p)
    named = [k for k in packages if k]
    if not named:
        return
    line = java_imports.import_line(named)
    for p, t in texts.items():
        for m in line.finditer(t):
            imp = _IMPORT.search(m.group(0))
            if imp is None:
                continue
            static, dotted, star = imp.groups()
            if star and not static and dotted in packages:
                targets = packages[dotted]
            else:
                hit = _longest(files, dotted)
                targets = [hit] if hit else []
            for q in targets:
                yield p, q


def _csharp(texts):
    spaces, types, clean = {}, {}, {}
    for p, t in texts.items():
        t = clean[p] = csharp_using.BLOCK.sub(lambda m: '\n' * m.group(0).count('\n'), t)
        declared = _NAMESPACE.findall(t) or ['']
        for ns in declared:
            spaces.setdefault(ns, []).append(p)
            for name in _TYPE.findall(t):
                types[ns + '.' + name if ns else name] = p
    for p, t in clean.items():
        for m in csharp_using.USING.finditer(t):
            name = m.group(1)
            for q in spaces.get(name) or ([types[name]] if name in types else []):
                yield p, q


def _python(texts, tree_paths):
    tree = Tree(tree_paths)
    for p, t in texts.items():
        try:
            node = ast.parse(t, filename=p)
        except (SyntaxError, ValueError):
            continue
        here = posixpath.dirname(p)
        roots = list(dict.fromkeys((here, python.package_root(here, tree), '')))
        for _line, level, parts, _what in python.imports(node):
            if not parts:
                continue
            if level:
                base = here
                for _ in range(level - 1):
                    base = posixpath.dirname(base)
                target = python.resolve(parts, base, tree)
            else:
                target = next((hit for hit in (python.resolve(parts, r, tree) for r in roots)
                               if hit), None)
            if target and not target.endswith('.py'):
                target = posixpath.join(target, '__init__.py')
            if target:
                yield p, target


def edges(texts, tree_paths):
    """{(importer, imported)} among the files of `texts` ({path: text}); `tree_paths` (every path
    in the tree) resolves Python imports."""
    by = {}
    for p, t in texts.items():
        by.setdefault(_ext(p), {})[p] = t
    found = set(_java(by.get('.java', {}))) | set(_csharp(by.get('.cs', {})))
    found |= set(_python(by.get('.py', {}), tree_paths))
    return {(a, b) for a, b in found if a != b and b in texts}


def _sccs(graph):
    """The strongly connected components of more than one node of `graph` ({node: sorted
    successors}), each sorted: Tarjan's algorithm with an explicit stack, O(nodes + edges)."""
    index, low, on, stack, out = {}, {}, set(), [], []
    for start in sorted(graph):
        if start in index:
            continue
        work = [(start, 0)]
        while work:
            v, i = work.pop()
            if i == 0:
                index[v] = low[v] = len(index)
                stack.append(v)
                on.add(v)
            if i < len(graph[v]):
                work.append((v, i + 1))
                w = graph[v][i]
                if w not in index:
                    work.append((w, 0))
                elif w in on:
                    low[v] = min(low[v], index[w])
                continue
            if low[v] == index[v]:
                comp = []
                while True:
                    w = stack.pop()
                    on.discard(w)
                    comp.append(w)
                    if w == v:
                        break
                if len(comp) > 1:
                    out.append(sorted(comp))
            if work:
                u = work[-1][0]
                low[u] = min(low[u], low[v])
    return out


def new_cycles(base_edges, tip_edges):
    """The cycles (sorted file lists) of `tip_edges` that hold an edge `base_edges` lacks."""
    graph = {}
    for a, b in tip_edges:
        graph.setdefault(a, []).append(b)
        graph.setdefault(b, [])
    graph = {k: sorted(v) for k, v in graph.items()}
    out = []
    for comp in _sccs(graph):
        members = set(comp)
        if any(a in members and b in members and (a, b) not in base_edges for a, b in tip_edges):
            out.append(comp)
    return out
