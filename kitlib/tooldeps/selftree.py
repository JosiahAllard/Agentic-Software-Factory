"""Which of the kit's own tests a branch's change can break directly: the kit's tree laid out as
`kitlib/<pkg>/` (logic), `tools/<x>.py` (entry points) and `tests/<pkg>/` (unittest packages).
`deps` maps a flat tools folder; this maps those packages. tools/kit_tests.py is the entry point,
docs/test-tools.md ("kit_tests") the doc.

The graph is derived (`scan`), never hand-written:
  - a kitlib package reaches kitlib.X by `import kitlib.X`, `from kitlib.X import`, `from kitlib
    import X`, a relative import that climbs out of its package, or a non-docstring string that
    imports it in a child (`-c "from kitlib.X import ..."`, `-m kitlib.X`); a string that only
    names a package (a label, a message) is not a reference;
  - a tool reaches the kitlib packages the same way, and another tool by `import <tool>`;
  - a test module reaches, through its own file, its packages' __init__ files and the test files
    it imports (absolute or relative, or names as `<file>.py` in its own test package; followed
    transitively, since they are its fixture): every kitlib package they import and every
    `kitlib.X` / `kitlib/X` their text names (comments too: a test only ever selects more), and
    every tool they import, name as `<tool>.py` or load by bare name (_tool_loads:
    `load_tool('gate')`, `os.path.join(ROOT, 'tools', 'gate')`). A test package reaches what
    its files do;
  - a test module lists the repo folders its own file and its non-test helpers pass to a
    listing call (os.walk, os.listdir, glob, Path.iterdir ...; _walked), read from the strings
    the call's path is built from (`os.path.join(ROOT, 'tools')`, a name assigned one). A
    listing built from no repo-folder string (the repo root, a temporary folder) names none:
    such a test goes in `always`;
  - module by module (CodeFile, TestPackage.mods): the dotted modules each code file (kitlib,
    tools, hosts, boot) and each test module imports - absolute, relative, the names an
    import-from takes, a literal `importlib.import_module('kitlib.a.b')` / `__import__` (a
    computed name is not followed), a child's `-c` / `-m` string - each with its parents (importing
    kitlib.a.b runs kitlib.a's __init__ too). A test module also counts every `kitlib.a.b` /
    `kitlib/a/b.py` its text names, and the imports of the top-level tests/*.py helpers it
    uses (not their text).

The one rule: a changed path selects the tests that read it, directly, never the transitive
closure: a test that uses a package which uses the changed one is left to a later full run
(docs/test-tools.md "kit_tests").

`selection(changed, tree, cfg)` for each changed repo-relative path:
  - `full` (globs: the selection table, the gate config and the code every test runs inside)
    -> ALL;
  - kitlib/<p>/<m>.py: the whole tests/<p>, and every test module reading a carrier of
    kitlib.p.m - m itself, and each file of package p importing a carrier (a package's __init__
    is kitlib.p: importing it is importing the package) - then one hop: for each code file
    outside p importing a carrier, the test modules reading that file (a kitlib module: its
    importers and namers; a tool: the tests importing, naming or loading it; hosts/boot: their
    importers and the tests naming the path), and no further. A package or module that is gone
    resolves the same way: its importers still name it. Any other file under kitlib/<p>/: the
    whole tests/<p> and the file's readers below;
  - kitlib/<x>.py, a module directly in kitlib/ (its __init__ is `full`): the whole tests/<x>
    when there is one, every test module reading kitlib.x, then the same one hop over the code
    files importing it;
  - tools/<x>.py: every test module that imports, names or loads x, or names a tool importing x (a
    deleted x too: its importers still name it); when there is none, the readers of the code
    files naming x.py;
  - tests/<t>/<file>.py the scan read: the test modules using it (a test module uses itself),
    the whole tests/<t> when none does; any other file under tests/<t> (data, a deleted file):
    the whole tests/<t> and every test module naming tests.<t>; a top-level tests/<x>.py
    helper: the test modules using it, and its readers below;
  - `paths` [glob, [test package or module, ...]]: those, on top of any rule here (a test that
    reads a folder through a computed name, such as a scrub test); with no rule above, plus the
    test modules naming the file (its base name): the row names its readers;
  - any path in a folder a test module lists: that module too, on top of any rule here;
  - `prose` globs: the test modules naming the file;
  - any other path: its readers - the test modules naming its base name, and the test modules
    reading each code file that names it (as above, no further hop).
A path no test reads selects nothing of its own and is returned in `notes`: it runs `always`
only, never ALL. After the paths, the `always` entries (test packages that read every tracked
file, such as tests/scrub, and single "tests.<pkg>.<module>" names for a repo-wide scan inside a
package that is otherwise not wanted) join any non-empty change that is not ALL, whatever the
paths are. A whole test package is returned by its name, a single test module by its dotted
name; `split_picked` parts them, and a module whose package is picked whole is dropped. Each
selected name carries the reasons it was picked.
The table's `serial` list, [module, why] pairs, names the test modules that cannot run beside
others; kitlib.tooldeps.selfrun runs them alone after its parallel phase. Its `long` list names
the modules selfrun starts first and splits by class while it has no recorded time for them. Its
`skip_fails` list, [module, why] pairs, names the proof modules: one that skips any test FAILS in
selfrun (a skip would void the proof); `stale_skip_fails` finds an entry naming no module. Its
`smoke` list names the test modules kit_tests --smoke adds to a selection unless every changed
path is prose (`smoke`; the gate's smoke_steps, docs/gate.md).
"""
import ast
import json
import os
import re

from kitlib.git import cmd as gitcmd
from kitlib.tooldeps.deps import ALL, _dynamic_call, glob_regex

CONFIG_REL = 'tests/selection.json'
DEFAULTS = {
    'full': [],
    'paths': [],
    'prose': [],
    'always': [],
    'serial': [],
    'long': [],
    'skip_fails': [],
    'smoke': [],
}
# The one test package kit_tests --scrub runs (the checks every tracked file must pass).
SCRUB = 'scrub'
_KITLIB_REF = re.compile(r'\bkitlib[./\\]+([A-Za-z_]\w*)')
# A dotted kitlib module a text names: `kitlib.a.b`, `kitlib/a/b.py` (its parents count too).
_KITLIB_MOD = re.compile(r'\bkitlib((?:[./\\]+[A-Za-z_]\w*)+)')
# The folders whose .py files the module-level scan reads as code (besides tests/).
CODE_TOPS = ('kitlib', 'tools', 'hosts', 'boot')
_TESTS_REF = re.compile(r'\btests\.([A-Za-z_]\w*)')
# A string that runs kitlib code in a child (`-c "from kitlib.X import ..."`, `-m kitlib.X`);
# a string that only names a package (a label, a message) is not a reference.
_CHILD_IMPORT = re.compile(r'\b(?:import|from)\s+kitlib\b|-m\s+kitlib\.')
_TOOL_FILE = re.compile(r'(?<![\w.])([A-Za-z_]\w*)\.py\b')
# The calls that list a folder: os.walk / listdir / scandir, glob.glob / iglob, Path.iterdir /
# glob / rglob.
_WALKS = {'walk', 'listdir', 'scandir', 'glob', 'iglob', 'iterdir', 'rglob'}
# The calls a listed folder's path is built with.
_PATH_CALLS = {'join', 'Path', 'PurePath', 'abspath', 'normpath', 'realpath', 'joinpath'}


class ConfigError(ValueError):
    """The selection table is not of the shape this module reads."""


def config(root, data=None):
    """The selection table: `data`, else `<root>/tests/selection.json`, over DEFAULTS. Raises
    ConfigError on an unknown key or a value of the wrong shape."""
    if data is None:
        path = os.path.join(root, *CONFIG_REL.split('/'))
        data = {}
        if os.path.isfile(path):
            try:
                with open(path, encoding='utf-8') as fh:
                    data = json.load(fh)
            except (OSError, ValueError) as e:
                raise ConfigError('%s: %s' % (CONFIG_REL, e))
    if not isinstance(data, dict):
        raise ConfigError('%s must be an object' % CONFIG_REL)
    unknown = sorted(set(data) - set(DEFAULTS) - {'comment'})
    if unknown:
        raise ConfigError('%s: unknown key(s) %s' % (CONFIG_REL, ', '.join(unknown)))
    out = dict(DEFAULTS)
    out.update({k: v for k, v in data.items() if k in DEFAULTS})
    for k in ('full', 'prose', 'always'):
        v = out[k]
        if not isinstance(v, list) or not all(isinstance(x, str) and x for x in v):
            raise ConfigError('%s: %s must be a list of non-empty strings' % (CONFIG_REL, k))
    for x in out['always']:
        if '.' in x and not (x.startswith('tests.') and len(x.split('.')) == 3):
            raise ConfigError('%s: always entry %r must be a test package or "tests.<pkg>.<module>"'
                              % (CONFIG_REL, x))
    pairs = []
    for i, e in enumerate(out['paths']):
        ok = (isinstance(e, list) and len(e) == 2 and isinstance(e[0], str) and e[0]
              and isinstance(e[1], list) and all(isinstance(x, str) and x for x in e[1]))
        if not ok:
            raise ConfigError('%s: paths[%d] must be [glob, [test package or module, ...]]'
                              % (CONFIG_REL, i))
        pairs.append((e[0], list(e[1])))
    out['paths'] = pairs
    serial = []
    for i, e in enumerate(out['serial']):
        ok = (isinstance(e, list) and len(e) == 2 and all(isinstance(x, str) and x.strip() for x in e)
              and e[0].startswith('tests.'))
        if not ok:
            raise ConfigError('%s: serial[%d] must be ["tests.<pkg>.<module>", "why it cannot run '
                              'beside other tests"]' % (CONFIG_REL, i))
        serial.append((e[0], e[1]))
    out['serial'] = serial
    proof = []
    for i, e in enumerate(out['skip_fails']):
        ok = (isinstance(e, list) and len(e) == 2 and all(isinstance(x, str) and x.strip() for x in e)
              and e[0].startswith('tests.'))
        if not ok:
            raise ConfigError('%s: skip_fails[%d] must be ["tests.<pkg>.<module>", "why a skipped '
                              'test voids it"]' % (CONFIG_REL, i))
        proof.append((e[0], e[1]))
    out['skip_fails'] = proof
    for k in ('long', 'smoke'):
        v = out[k]
        if not isinstance(v, list) or not all(isinstance(x, str) and x.startswith('tests.')
                                              for x in v):
            raise ConfigError('%s: %s must be a list of "tests.<pkg>.<module>" names'
                              % (CONFIG_REL, k))
        out[k] = list(v)
    return out


def smoke(changed, cfg, selected, modules):
    """(test modules to add, why) for kit_tests --select --smoke: the table's `smoke` modules
    that `selected` (the selection's modules; None for none) does not hold already. None are
    added when the change is empty or every changed path is `prose`: a docs change reaches no
    smoke area. Raises ConfigError when `smoke` names a module `modules` (every test module of
    the tree) lacks, since a stale entry would drop a landing check without a word."""
    stale = [m for m in cfg.get('smoke', ()) if m not in set(modules)]
    if stale:
        raise ConfigError('%s: smoke names %s, which is not a test module'
                          % (CONFIG_REL, ', '.join(stale)))
    paths = [p.replace('\\', '/') for p in changed]
    if not paths:
        return [], 'no changed path'
    if all(_matches(p, cfg['prose']) for p in paths):
        return [], 'every changed path is prose'
    have = set(selected or ())
    add = [m for m in cfg.get('smoke', ()) if m not in have]
    if not add:
        return [], 'the selection already holds every smoke module'
    return add, '%d smoke module(s) added' % len(add)


def _matches(path, globs):
    return any(glob_regex(g).match(path) for g in globs)


def _read(path):
    try:
        with open(path, encoding='utf-8', errors='replace') as fh:
            return fh.read()
    except OSError:
        return ''


def _py_files(folder):
    for d, dirs, files in os.walk(folder):
        dirs[:] = sorted(x for x in dirs if x != '__pycache__')
        for n in sorted(files):
            if n.endswith('.py'):
                yield os.path.join(d, n)


def _docstring_ids(nodes):
    out = set()
    for node in nodes:
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                out.add(id(body[0].value))
    return out


def _parse(text):
    try:
        return ast.parse(text)
    except (SyntaxError, ValueError):
        return None


def _nodes(tree):
    """Every node of `tree` (None: none), walked once for all the readers below: a walk costs more
    than any one reader."""
    return list(ast.walk(tree)) if tree is not None else []


def code_refs(text, package=None):
    """(kitlib packages, top-level names) a code file (kitlib or tools) reaches: its imports, its
    relative imports out of kitlib/`package`, and `kitlib.X` in a non-docstring string that
    imports it in a child (_CHILD_IMPORT). A file
    that does not parse falls back to its whole text (it only selects more)."""
    tree = _parse(text)
    return _refs(tree, _nodes(tree), text, package)


def _refs(tree, nodes, text, package=None):
    if tree is None:
        return set(_KITLIB_REF.findall(text)), set()
    pkgs, tops, docs = set(), set(), _docstring_ids(nodes)
    for node in nodes:
        if isinstance(node, ast.Import):
            for a in node.names:
                parts = a.name.split('.')
                if parts[0] == 'kitlib' and len(parts) > 1:
                    pkgs.add(parts[1])
                tops.add(parts[0])
        elif isinstance(node, ast.ImportFrom):
            mod = (node.module or '').split('.')
            if node.level == 0:
                if mod[0] == 'kitlib':
                    if len(mod) > 1:
                        pkgs.add(mod[1])
                    else:
                        pkgs.update(a.name for a in node.names)
                tops.add(mod[0])
            elif node.level >= 2 and package is not None:
                # from ..X import y inside kitlib/<package>/: X (or y with no X) is a kitlib package
                pkgs.update([mod[0]] if mod[0] else [a.name for a in node.names])
        elif (isinstance(node, ast.Constant) and isinstance(node.value, str)
              and id(node) not in docs and _CHILD_IMPORT.search(node.value)):
            pkgs.update(_KITLIB_REF.findall(node.value))
    return pkgs, tops


def _kitlib_names(text):
    """The dotted kitlib modules `text` names (`kitlib.a.b`, `kitlib/a/b.py`; a trailing `.py`
    stays as a part, harmless once _expand adds the parents)."""
    return {'kitlib.' + re.sub(r'[./\\]+', '.', g).strip('.') for g in _KITLIB_MOD.findall(text)}


def _expand(names):
    """`names` with every dotted parent: importing a.b.c runs a and a.b first."""
    out = set()
    for n in names:
        parts = [p for p in n.split('.') if p]
        for i in range(1, len(parts) + 1):
            out.add('.'.join(parts[:i]))
    return out


def _imports(tree, nodes, text, dotted):
    """The dotted modules the file `dotted` ("kitlib.a.b", an __init__ as "kitlib.a.__init__")
    imports, with their parents (_expand): its imports, relative ones resolved (_test_imports),
    the module an import_module / __import__ call names as a literal (deps._dynamic_call; a
    computed name is not followed), and the kitlib modules a non-docstring string imports in a
    child (_CHILD_IMPORT). A file that does not parse falls back to the kitlib modules its text
    names (it only selects more)."""
    if tree is None:
        return _expand(_kitlib_names(text))
    names, docs = _test_imports(nodes, dotted), _docstring_ids(nodes)
    for node in nodes:
        literal = _dynamic_call(node)
        if literal:
            names.add(literal)
        elif (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and id(node) not in docs and _CHILD_IMPORT.search(node.value)):
            names |= _kitlib_names(node.value)
    return _expand(names)


def _module_of(rel):
    """The dotted module of the repo-relative .py path `rel` (a package's __init__: the
    package)."""
    parts = rel[:-3].split('/')
    if parts[-1] == '__init__':
        parts = parts[:-1]
    return '.'.join(parts)


def _names_file(text, base):
    """Whether `text` names the file `base` as a whole name: `solo.py` is not in `my_solo.py`."""
    if base not in text:
        return False
    return re.search(r'(?<![\w-])%s(?![\w-])' % re.escape(base), text) is not None


class CodeFile(object):
    """One code file of the module-level scan (kitlib, tools, hosts, boot)."""

    def __init__(self, rel, module, imports, text):
        self.rel = rel            # repo-relative path
        self.module = module      # dotted module (a package's __init__: the package; a tool: its name)
        self.imports = imports    # dotted modules it imports, with their parents (_imports)
        self.text = text


class Tree(object):
    """The kit's tree at `root`: `kitlib` {package: kitlib packages it reaches}, `tools` {tool:
    (kitlib packages, top-level names it imports)}, `tests` {test package: TestPackage},
    `modules` {dotted test module: TestPackage of its file and the test files it uses}, `code`
    {repo-relative path: CodeFile} for every .py file under CODE_TOPS."""

    def __init__(self, root, kitlib, tools, tests, modules=None, code=None):
        self.root = root
        self.kitlib = kitlib
        self.tools = tools
        self.tests = tests
        self.modules = modules or {}
        self.code = code or {}
        self._named = {}          # {test module: the <name>.py files its text names}

    def naming(self, word):
        """The test modules whose files' text holds `word`."""
        return {m for m, tm in self.modules.items() if word in tm.text}

    def tool_users(self, tools):
        """{test module: the first of `tools` it imports or names as `<tool>.py`}. A name counts
        whole: `test_gate.py` or `land_gate.py` does not name `gate.py` (_TOOL_FILE)."""
        out = {}
        for m, tm in sorted(self.modules.items()):
            named = self._named.get(m)
            if named is None:
                named = self._named[m] = set(_TOOL_FILE.findall(tm.text))
            for tool in sorted(tools):
                if tool in tm.tools or tool in named:
                    out[m] = tool
                    break
        return out


class TestPackage(object):
    """What a test package, or a test module with the test files it uses, refers to."""

    def __init__(self, kitlib, tests, tools, text, files=(), walks=(), mods=()):
        self.kitlib = kitlib      # kitlib packages it names
        self.tests = tests        # other test packages it imports (tests.<pkg>)
        self.tools = tools        # tools it imports by module name or loads by bare name
        self.text = text          # every .py file's text, joined
        self.files = set(files)   # a module's test files, dotted ("tests.<pkg>.__init__" too)
        self.walks = set(walks)   # repo folders its files list ("tools", "content/roles")
        self.mods = set(mods)     # dotted modules it imports or names, with parents (a module)


def _test_imports(nodes, name):
    """The dotted names the test file `name` ("tests.<pkg>....<file>", an __init__ as
    "....__init__") may import, from its `nodes` (_nodes): each import, relative ones resolved,
    and each name an import-from takes (it may be a module)."""
    out = set()
    here = name.split('.')[:-1]
    for node in nodes:
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                if node.level > len(here):
                    continue
                base = here[:len(here) - node.level + 1] + ([node.module] if node.module else [])
                mod = '.'.join(base)
            else:
                mod = node.module or ''
            out.add(mod)
            out.update('%s.%s' % (mod, a.name) for a in node.names)
    return out


def _func_name(call):
    f = call.func
    return f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else ''


def _tool_loads(nodes):
    """The tools a test file (its `nodes`) loads by bare name: the first argument of a call whose
    name holds `tool` or `load` (`load_tool('gate')`, `inproc.load_tool('backlog')`,
    `tool('commit')`), and the name after a 'tools' argument (`os.path.join(ROOT, 'tools',
    'gate')`). Every name is kept, not only tools that exist, as for an import."""
    out = set()
    for node in nodes:
        if not isinstance(node, ast.Call):
            continue
        args = [a.value if isinstance(a, ast.Constant) and isinstance(a.value, str) else None
                for a in node.args]
        name = _func_name(node).lower()
        if args and args[0] and ('tool' in name or 'load' in name):
            out.add(args[0])
        out.update(b for a, b in zip(args, args[1:]) if a == 'tools' and b)
    out.discard('tools')
    return {x for x in out if x.isidentifier()}


def _assigned(nodes):
    """{name: [value nodes]} for each `name = ...`, `x.name = ...` and `for name in ...` in the
    file, whatever the scope."""
    out = {}
    for node in nodes:
        if isinstance(node, ast.Assign):
            pairs = [(t, node.value) for t in node.targets]
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            pairs = [(node.target, node.value)]
        elif isinstance(node, (ast.For, ast.comprehension)):
            pairs = [(node.target, node.iter)]
        else:
            continue
        for target, value in pairs:
            key = (target.id if isinstance(target, ast.Name) else
                   target.attr if isinstance(target, ast.Attribute) else None)
            if key:
                out.setdefault(key, []).append(value)
    return out


def _strings(node, assigned, seen):
    """The string constants the path `node` is built from, in source order: a name (or
    `x.name`) is read through its assignments, once each; a call that builds a path (_PATH_CALLS,
    a listing call) gives its arguments and a method's receiver; any other call gives none (its
    arguments do not say what it returns)."""
    if isinstance(node, ast.Constant):
        return [node.value] if isinstance(node.value, str) else []
    key = (node.id if isinstance(node, ast.Name) else
           node.attr if isinstance(node, ast.Attribute) else None)
    if key is not None:
        if key in seen:
            return []
        seen.add(key)
        return [s for v in assigned.get(key, ()) for s in _strings(v, assigned, seen)]
    if isinstance(node, ast.Call):
        if _func_name(node) not in _PATH_CALLS | _WALKS:
            return []
        parts = [node.func.value] if isinstance(node.func, ast.Attribute) else []
        parts += list(node.args) + [k.value for k in node.keywords]
        return [s for p in parts for s in _strings(p, assigned, seen)]
    return [s for c in ast.iter_child_nodes(node) for s in _strings(c, assigned, seen)]


def _walked(nodes, root, tops):
    """The repo folders ("tools", "content/roles") a test file (its `nodes`) lists (_WALKS): from
    each string the listing call is built from that is a top folder of `root` (`tops`), the
    longest run of them that is still a folder there (`os.path.join(ROOT, 'tools', '*.py')` lists
    tools). A listing built from no such string (a temporary folder, the repo root itself) names
    none."""
    out = set()
    calls = [n for n in nodes if isinstance(n, ast.Call) and _func_name(n) in _WALKS]
    assigned = _assigned(nodes) if calls else {}
    for node in calls:
        segs = [s for c in _strings(node, assigned, set()) for s in re.split(r'[\\/]', c) if s]
        for i, seg in enumerate(segs):
            if seg not in tops:
                continue
            j = i + 1
            while (j < len(segs) and re.match(r'^[\w.-]+$', segs[j])
                   and segs[j] not in ('.', '..')
                   and os.path.isdir(os.path.join(root, *segs[i:j + 1]))):
                j += 1
            out.add('/'.join(segs[i:j]))
    return out


def _module_refs(files, modules):
    """{test module: TestPackage} for each dotted name in `modules`, from `files` {dotted test
    file: (text, kitlib packages, top-level and loaded tool names, dotted names it may import,
    folders it lists, dotted modules it imports with their parents)}: the module's file, its
    packages' __init__ files and the test files it imports or names as `<file>.py` in its own
    test package, transitively. The folders come from the module's own file and its helpers,
    not from another test module it imports. A top-level tests/<x>.py helper ("tests.<x>")
    gives its imports only: its text (names, tools, folders) is shared by too many modules to
    say what any one of them reads."""
    by_base = {}
    for d in files:
        parts = d.split('.')
        by_base.setdefault((parts[1], parts[-1]), []).append(d)
    links = {}
    for d, (text, _p, _t, imports, _w, _m) in files.items():
        out = set()
        parts = d.split('.')
        for i in range(2, len(parts)):
            out.add('.'.join(parts[:i]) + '.__init__')
        for c in imports:
            cp = c.split('.')
            for i in range(2, len(cp) + 1):
                out.add('.'.join(cp[:i]) + '.__init__')
            out.add(c)
        for base in _TOOL_FILE.findall(text):
            out.update(by_base.get((parts[1], base), ()))
        links[d] = {x for x in out if x in files and x != d}
    refs = {}
    for m in modules:
        seen, todo = set(), [m]
        while todo:
            d = todo.pop()
            if d not in seen and d in files:
                seen.add(d)
                todo.extend(links[d] - seen)
        texts, pkgs, tops, walks, mods = [], set(), set(), set(), set()
        for d in sorted(seen):
            text, p, t, _i, w, mo = files[d]
            pkgs |= p
            mods |= mo
            if d.count('.') == 1:
                continue        # a top-level helper: its imports only
            texts.append(text)
            tops |= t
            if d == m or not d.rsplit('.', 1)[-1].startswith('test'):
                walks |= w      # another test module's listing runs in that module, not here
        text = '\n'.join(texts)
        refs[m] = TestPackage(pkgs | set(_KITLIB_REF.findall(text)),
                              set(_TESTS_REF.findall(text)), tops, text, seen, walks,
                              mods | _expand(_kitlib_names(text)))
    return refs


def scan(root):
    """The Tree of the checkout at `root`."""
    tools, code = {}, {}

    def read_code(path, package=None):
        rel = os.path.relpath(path, root).replace(os.sep, '/')
        text = _read(path)
        parsed = _parse(text)
        nodes = _nodes(parsed)
        name = rel[:-3].replace('/', '.')
        module = name.split('.', 1)[1] if rel.startswith('tools/') else _module_of(rel)
        code[rel] = CodeFile(rel, module, _imports(parsed, nodes, text, name), text)
        return _refs(parsed, nodes, text, package)

    tdir = os.path.join(root, 'tools')
    names = {f[:-3] for f in os.listdir(tdir) if f.endswith('.py')} if os.path.isdir(tdir) else set()
    for name in sorted(names):
        pkgs, tops = read_code(os.path.join(tdir, name + '.py'))
        # every top-level name, not only tools that exist: a deleted tool's importers still name it
        tools[name] = (pkgs, tops - {name})
    kitlib = {}
    kdir = os.path.join(root, 'kitlib')
    for name in sorted(os.listdir(kdir)) if os.path.isdir(kdir) else ():
        folder = os.path.join(kdir, name)
        if not os.path.isfile(os.path.join(folder, '__init__.py')):
            continue
        refs = set()
        for f in _py_files(folder):
            refs |= read_code(f, package=name)[0]
        kitlib[name] = refs - {name}
    for top in CODE_TOPS:
        if top not in ('kitlib', 'tools'):
            for f in _py_files(os.path.join(root, top)):
                read_code(f)
    tests, files = {}, {}
    top_dirs = {n for n in os.listdir(root) if n not in ('.git', '__pycache__')
                and os.path.isdir(os.path.join(root, n))}

    def read_test(f):
        text = _read(f)
        parsed = _parse(text)
        nodes = _nodes(parsed)
        p, t = _refs(parsed, nodes, text)
        t |= _tool_loads(nodes)
        dotted = os.path.relpath(f, root)[:-3].replace(os.sep, '.')
        files[dotted] = (text, p, t, _test_imports(nodes, dotted),
                         _walked(nodes, root, top_dirs), _imports(parsed, nodes, text, dotted))
        return text, p, t

    sdir = os.path.join(root, 'tests')
    for name in sorted(os.listdir(sdir)) if os.path.isdir(sdir) else ():
        folder = os.path.join(sdir, name)
        if name.endswith('.py') and name != '__init__.py' and os.path.isfile(folder):
            read_test(folder)       # a top-level helper (tests/gitfixture.py): its users follow it
            continue
        if not os.path.isfile(os.path.join(folder, '__init__.py')):
            continue
        texts, pkgs, tops = [], set(), set()
        for f in _py_files(folder):
            text, p, t = read_test(f)
            texts.append(text)
            pkgs |= p
            tops |= t
        text = '\n'.join(texts)
        # imports (`from kitlib import X` too) and every `kitlib.X` / `kitlib/X` the text names
        tests[name] = TestPackage(pkgs | set(_KITLIB_REF.findall(text)),
                                  set(_TESTS_REF.findall(text)) - {name}, tops, text)
    modules = [m for m in all_test_modules(root) if m.split('.')[1] in tests]
    return Tree(root, kitlib, tools, tests, _module_refs(files, modules), code)


class _Picks(object):
    def __init__(self, tree):
        self.tree = tree
        self.why = {}

    def add(self, name, reason):
        """Pick a whole test package (its name) or one test module (its dotted name)."""
        if name in self.tree.tests or name in self.tree.modules:
            self.why.setdefault(name, [])
            if reason not in self.why[name]:
                self.why[name].append(reason)
            return True
        return False

    def tool_users(self, tools, why):
        """Pick the test modules importing or naming one of `tools`; `why(tool)` the reason."""
        found = False
        for m, tool in sorted(self.tree.tool_users(tools).items()):
            found |= self.add(m, why(tool))
        return found

    def whole_packages_win(self):
        """Drop a module whose test package is picked whole: the package runs it."""
        for name in [n for n in self.why if '.' in n]:
            if name.split('.')[1] in self.why:
                del self.why[name]


def _readers(tree, names, why, picks):
    """Pick the test modules importing or naming one of the dotted modules `names`; `why(name)`
    the reason, from the first name a module reads."""
    found = False
    for m, tm in sorted(tree.modules.items()):
        hit = tm.mods & names
        if hit:
            found |= picks.add(m, why(min(hit)))
    return found


def _code_readers(tree, cf, tail, picks):
    """Pick the test modules reading the code file `cf`: a tool's importers, namers and loaders
    (Tree.tool_users); a module's importers and namers, and for hosts/ and boot/ the test
    modules naming its path. `tail` ends each reason (", which imports ...")."""
    if cf.rel.startswith('tools/'):
        return picks.tool_users({cf.module}, lambda tool: 'names %s%s' % (cf.rel, tail))
    found = _readers(tree, {cf.module}, lambda name: 'uses %s%s' % (name, tail), picks)
    if not cf.rel.startswith('kitlib/'):
        for m, tm in sorted(tree.modules.items()):
            if cf.rel in tm.text:
                found |= picks.add(m, 'names %s%s' % (cf.rel, tail))
    return found


def _carriers(module, pkg, tree):
    """`module` and every file of kitlib/`pkg` importing it or another carrier: importing any of
    them runs the changed code."""
    files = [cf for rel, cf in tree.code.items() if rel.startswith('kitlib/%s/' % pkg)]
    out, grew = {module}, True
    while grew:
        grew = False
        for cf in files:
            if cf.module not in out and cf.imports & out:
                out.add(cf.module)
                grew = True
    return out


def _carried(path, carriers, inside, tree, picks):
    """Pick the readers of `carriers` and, one hop, of each code file not `inside` (a rel
    predicate) importing one: the selection of a changed kitlib module `path`."""
    found = _readers(tree, carriers, lambda name: 'uses %s (%s)' % (name, path), picks)
    for rel, cf in sorted(tree.code.items()):
        hit = cf.imports & carriers
        if hit and not inside(rel):
            found |= _code_readers(tree, cf, ', which imports %s (%s)' % (min(hit), path), picks)
    return found


def _kitlib_change(path, pkg, tree, picks):
    found = picks.add(pkg, 'own tests of changed kitlib/%s' % pkg)
    if not path.endswith('.py'):
        return _named_file(path, tree, picks) or found
    return _carried(path, _carriers(_module_of(path), pkg, tree),
                    lambda rel: rel.startswith('kitlib/%s/' % pkg), tree, picks) | found


def _kitlib_module_change(path, tree, picks):
    """A module directly in kitlib/ (kitlib/clock.py): the tests/<x> of its name whole, its
    readers and one hop; nothing else carries it (kitlib/__init__.py is `full`)."""
    name = path[len('kitlib/'):-3]
    found = picks.add(name, 'own tests of changed %s' % path)
    return _carried(path, {_module_of(path)}, lambda rel: rel == path, tree, picks) | found


def _tool_change(path, name, tree, picks):
    found = picks.tool_users({name}, lambda tool: 'names %s' % path)
    # A deleted tool is not in the tree, but its importers still name it.
    users = {x for x, (_pkgs, tops) in tree.tools.items() if name in tops}
    found |= picks.tool_users(users, lambda tool: 'names tools/%s.py, which imports %s'
                              % (tool, path))
    # no test reaches it: the tests of the code that runs it by name
    return found or _named_by_code(path, tree, picks)


def _test_users(path, tree, picks):
    """Pick the test modules whose files hold the test file `path`: True when one does."""
    dotted = path[:-3].replace('/', '.') if path.endswith('.py') else None
    found = False
    for m, tm in sorted(tree.modules.items()):
        if dotted in tm.files:
            found |= picks.add(m, 'changed (%s)' % path if m == dotted else
                               'uses %s (%s)' % (dotted, path))
    return found


def _tests_change(path, pkg, tree, picks):
    if pkg not in tree.tests:
        return _test_users(path, tree, picks) | _named_file(path, tree, picks)
    if not _test_users(path, tree, picks):
        # a file the scan did not read (data, a deleted file) or one no test module uses
        picks.add(pkg, 'changed in tests/%s (%s)' % (pkg, path))
        for m, tm in sorted(tree.modules.items()):
            if pkg in tm.tests:
                picks.add(m, 'names tests.%s (%s)' % (pkg, path))
    return True


def _named_by_code(path, tree, picks):
    """Pick the readers of each code file (but `path` itself) naming `path`'s base name; no
    further hop."""
    base = path.rsplit('/', 1)[-1]
    found = False
    for rel, cf in sorted(tree.code.items()):
        if rel != path and _names_file(cf.text, base):
            found |= _code_readers(tree, cf, ', which names %s' % path, picks)
    return found


def _named_file(path, tree, picks):
    """The reader rule for a path no other rule owns: the test modules naming its base name and
    the readers of the code files naming it."""
    return _by_name(path, tree, picks) | _named_by_code(path, tree, picks)


def _listers(path, tree, picks):
    """Pick the test modules listing a folder `path` is in (a test over every tool, say)."""
    found = False
    for m, tm in sorted(tree.modules.items()):
        hit = sorted(w for w in tm.walks if path.startswith(w + '/'))
        if hit:
            found |= picks.add(m, 'lists %s/ (%s)' % (hit[-1], path))
    return found


def _by_name(path, tree, picks):
    base = path.rsplit('/', 1)[-1]
    found = False
    for t in sorted(tree.naming(base)):
        found |= picks.add(t, 'names %s' % base)
    return found


def selection(changed, tree, cfg):
    """(ALL, [why]) or (sorted names, {name: [reasons]}, notes) - see the docstring; a name is a
    whole test package or a dotted test module. `notes` lists the paths no test reads (they run
    `always` only). Pure: reads only `tree` and `cfg`."""
    picks, notes = _Picks(tree), []
    for f in sorted({p.replace('\\', '/') for p in changed}):
        parts = f.split('/')
        if _matches(f, cfg['full']):
            return ALL, ['full suite: %s changed (the selection, the gate config or code every '
                         'test runs inside)' % f], notes
        # `paths` adds to every rule below: a test that reads a folder through a computed name
        found = mapped = False
        for g, mods in cfg['paths']:
            if glob_regex(g).match(f):
                mapped = True
                for m in mods:
                    found |= picks.add(m, 'reads %s (%s)' % (f, CONFIG_REL))
        # so do the test modules listing a folder f is in
        found |= _listers(f, tree, picks)
        if parts[0] == 'kitlib' and len(parts) > 2:
            found |= _kitlib_change(f, parts[1], tree, picks)
        elif parts[0] == 'kitlib' and len(parts) == 2 and f.endswith('.py'):
            found |= _kitlib_module_change(f, tree, picks)
        elif parts[0] == 'tools' and len(parts) == 2 and f.endswith('.py'):
            found |= _tool_change(f, parts[1][:-3], tree, picks)
        elif parts[0] == 'tests' and len(parts) > 2:
            found |= _tests_change(f, parts[1], tree, picks)
        elif parts[0] == 'tests' and len(parts) == 2 and f.endswith('.py'):
            found |= _test_users(f, tree, picks) | _named_file(f, tree, picks)
        elif mapped or _matches(f, cfg['prose']):
            # a row names its readers; prose is read by name only
            found |= _by_name(f, tree, picks)
        else:
            found |= _named_file(f, tree, picks)
        if not found:
            notes.append(f)
    if changed:
        for name in cfg['always']:
            why = 'reads every tracked file (always, %s)' % CONFIG_REL
            if not picks.add(name, why) and name.startswith('tests.'):
                picks.why.setdefault(name, [why])
    picks.whole_packages_win()
    return sorted(picks.why), picks.why, notes


def split_picked(picked):
    """(packages, modules) of a selection's names: a dotted "tests.<pkg>....<module>" name is a
    single test module, the rest are whole test packages."""
    return ([p for p in picked if '.' not in p], [p for p in picked if '.' in p])


def changed_paths(base, root):
    """(paths, None) changed against the merge-base of `base` and HEAD in `root` - committed, in
    the work tree, and untracked - or (None, why) when git could not say."""
    mb, why = gitcmd.run_text(['merge-base', base, 'HEAD'], root)
    if mb is None or not mb:
        return None, 'git merge-base %s HEAD: %s' % (base, why or 'no merge-base printed')
    diff, why = gitcmd.run_text(['diff', '--name-only', '--no-renames', '-z', mb], root, strip=False)
    if diff is None:
        return None, 'git diff %s: %s' % (mb[:12], why)
    new, why = gitcmd.run_text(['ls-files', '-z', '--others', '--exclude-standard'], root, strip=False)
    if new is None:
        return None, 'git ls-files: %s' % why
    return sorted({p for p in (diff + new).split('\0') if p.strip()}), None


def test_modules(root, packages):
    """The dotted test modules `python -m unittest discover -s tests -t .` would load from the
    named test packages: `test*.py` files in folders that are packages, in sorted order."""
    return _walk_modules(root, [os.path.join(root, 'tests', pkg) for pkg in packages])


def all_test_modules(root):
    """Every dotted test module `python -m unittest discover -s tests -t .` loads, in sorted
    order (`tests/test*.py` included)."""
    return _walk_modules(root, [os.path.join(root, 'tests')])


def stale_skip_fails(skip_fails, modules):
    """The `skip_fails` modules ([module, why] pairs) that `modules` (every test module of the
    tree) does not hold: a renamed or removed proof module, whose entry would guard nothing."""
    have = set(modules)
    return [m for m, _why in skip_fails if m not in have]


def _walk_modules(root, tops):
    out = []
    for top in tops:
        for d, dirs, files in os.walk(top):
            if not os.path.isfile(os.path.join(d, '__init__.py')):
                dirs[:] = []
                continue
            dirs[:] = sorted(x for x in dirs if x != '__pycache__')
            rel = os.path.relpath(d, root).replace(os.sep, '.')
            for n in sorted(files):
                if n.startswith('test') and n.endswith('.py') and n[:-3].isidentifier():
                    out.append('%s.%s' % (rel, n[:-3]))
    return out
