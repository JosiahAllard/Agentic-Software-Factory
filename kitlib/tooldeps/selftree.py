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
    every tool they import or name as `<tool>.py`. A test package reaches what its files do.

A change selects its direct users only, never the transitive closure: a test that uses a package
which uses the changed one is left to the landing's `--full` merged gate (docs/test-tools.md
"kit_tests").

`selection(changed, tree, cfg)` for each changed repo-relative path:
  - `full` (globs: the test helpers, the selector and its table) -> ALL;
  - kitlib/<m>/...: ALL when m is in `shared_core` or is not a package of the tree; else the
    whole tests/<m>, every test module that uses kitlib.m, and every test module that names a
    tool importing kitlib.m;
  - tools/<x>.py: every test module that imports or names x, or names a tool importing x (a
    deleted x too: its importers still name it); ALL when there is none;
  - tests/<t>/<file>.py the scan read: the test modules using it (a test module uses itself),
    the whole tests/<t> when none does; any other file under tests/<t> (data, a deleted file):
    the whole tests/<t> and every test module naming tests.<t>;
  - `paths` [glob, [packages]]: those whole packages, on top of any rule above (a test package
    that reads another folder, such as a scrub test); with no rule above, plus the test modules
    naming the file;
  - `by_name` globs: the test modules naming the file (its base name);
  - `prose` globs: the same, but a file no test names selects nothing (it is read only by name);
  - anything else -> ALL.
After the paths, the `always` entries (test packages that read every tracked file, such as
tests/scrub, and single "tests.<pkg>.<module>" names for a repo-wide scan inside a package that
is otherwise not wanted) join any non-empty change that is not ALL, whatever the paths are. A
whole test package is returned by its name, a single test module by its dotted name;
`split_picked` parts them, and a module whose package is picked whole is dropped. A path that
maps to no test (but a `prose` one) is unmapped and selects ALL: unmapped never means "skip".
Each selected name carries the reasons it was picked.
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
from kitlib.tooldeps.deps import ALL, glob_regex

CONFIG_REL = 'tests/selection.json'
DEFAULTS = {
    'shared_core': ['git', 'config', 'proc', 'lock'],
    'full': [],
    'paths': [],
    'by_name': [],
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
_TESTS_REF = re.compile(r'\btests\.([A-Za-z_]\w*)')
# A string that runs kitlib code in a child (`-c "from kitlib.X import ..."`, `-m kitlib.X`);
# a string that only names a package (a label, a message) is not a reference.
_CHILD_IMPORT = re.compile(r'\b(?:import|from)\s+kitlib\b|-m\s+kitlib\.')
_TOOL_FILE = re.compile(r'(?<![\w.])([A-Za-z_]\w*)\.py\b')


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
    for k in ('shared_core', 'full', 'by_name', 'prose', 'always'):
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
            raise ConfigError('%s: paths[%d] must be [glob, [test package, ...]]' % (CONFIG_REL, i))
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


def _docstring_ids(tree):
    out = set()
    for node in ast.walk(tree):
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


def code_refs(text, package=None):
    """(kitlib packages, top-level names) a code file (kitlib or tools) reaches: its imports, its
    relative imports out of kitlib/`package`, and `kitlib.X` in a non-docstring string that
    imports it in a child (_CHILD_IMPORT). A file
    that does not parse falls back to its whole text (it only selects more)."""
    return _refs(_parse(text), text, package)


def _refs(tree, text, package=None):
    if tree is None:
        return set(_KITLIB_REF.findall(text)), set()
    pkgs, tops, docs = set(), set(), _docstring_ids(tree)
    for node in ast.walk(tree):
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


class Tree(object):
    """The kit's tree at `root`: `kitlib` {package: kitlib packages it reaches}, `tools` {tool:
    (kitlib packages, top-level names it imports)}, `tests` {test package: TestPackage},
    `modules` {dotted test module: TestPackage of its file and the test files it uses}."""

    def __init__(self, root, kitlib, tools, tests, modules=None):
        self.root = root
        self.kitlib = kitlib
        self.tools = tools
        self.tests = tests
        self.modules = modules or {}
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

    def __init__(self, kitlib, tests, tools, text, files=()):
        self.kitlib = kitlib      # kitlib packages it names
        self.tests = tests        # other test packages it imports (tests.<pkg>)
        self.tools = tools        # tools it imports by module name
        self.text = text          # every .py file's text, joined
        self.files = set(files)   # a module's test files, dotted ("tests.<pkg>.__init__" too)


def _test_imports(tree, name):
    """The dotted names the test file `name` ("tests.<pkg>....<file>", an __init__ as
    "....__init__") may import: each import, relative ones resolved, and each name an
    import-from takes (it may be a module)."""
    out = set()
    if tree is None:
        return out
    here = name.split('.')[:-1]
    for node in ast.walk(tree):
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


def _module_refs(files, modules):
    """{test module: TestPackage} for each dotted name in `modules`, from `files` {dotted test
    file: (text, kitlib packages, top-level names, dotted names it may import)}: the module's
    file, its packages' __init__ files and the test files it imports or names as `<file>.py`
    in its own test package, transitively."""
    by_base = {}
    for d in files:
        parts = d.split('.')
        by_base.setdefault((parts[1], parts[-1]), []).append(d)
    links = {}
    for d, (text, _p, _t, imports) in files.items():
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
        texts, pkgs, tops = [], set(), set()
        for d in sorted(seen):
            text, p, t, _i = files[d]
            texts.append(text)
            pkgs |= p
            tops |= t
        text = '\n'.join(texts)
        refs[m] = TestPackage(pkgs | set(_KITLIB_REF.findall(text)),
                              set(_TESTS_REF.findall(text)), tops, text, seen)
    return refs


def scan(root):
    """The Tree of the checkout at `root`."""
    tools = {}
    tdir = os.path.join(root, 'tools')
    names = {f[:-3] for f in os.listdir(tdir) if f.endswith('.py')} if os.path.isdir(tdir) else set()
    for name in sorted(names):
        pkgs, tops = code_refs(_read(os.path.join(tdir, name + '.py')))
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
            refs |= code_refs(_read(f), package=name)[0]
        kitlib[name] = refs - {name}
    tests, files = {}, {}
    sdir = os.path.join(root, 'tests')
    for name in sorted(os.listdir(sdir)) if os.path.isdir(sdir) else ():
        folder = os.path.join(sdir, name)
        if not os.path.isfile(os.path.join(folder, '__init__.py')):
            continue
        texts, pkgs, tops = [], set(), set()
        for f in _py_files(folder):
            text = _read(f)
            texts.append(text)
            parsed = _parse(text)
            p, t = _refs(parsed, text)
            pkgs |= p
            tops |= t
            dotted = os.path.relpath(f, root)[:-3].replace(os.sep, '.')
            files[dotted] = (text, p, t, _test_imports(parsed, dotted))
        text = '\n'.join(texts)
        # imports (`from kitlib import X` too) and every `kitlib.X` / `kitlib/X` the text names
        tests[name] = TestPackage(pkgs | set(_KITLIB_REF.findall(text)),
                                  set(_TESTS_REF.findall(text)) - {name}, tops, text)
    modules = [m for m in all_test_modules(root) if m.split('.')[1] in tests]
    return Tree(root, kitlib, tools, tests, _module_refs(files, modules))


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


def _kitlib_change(path, pkg, tree, cfg, picks):
    if pkg in cfg['shared_core']:
        return 'shared core: kitlib/%s changed (%s)' % (pkg, path)
    if pkg not in tree.kitlib:
        return '%s is not in a kitlib package of this tree' % path
    found = picks.add(pkg, 'own tests of changed kitlib/%s' % pkg)
    for m, tm in sorted(tree.modules.items()):
        if pkg in tm.kitlib:
            found |= picks.add(m, 'uses kitlib/%s (%s)' % (pkg, path))
    users = {x for x, (pkgs, _tops) in tree.tools.items() if pkg in pkgs}
    found |= picks.tool_users(users, lambda tool: 'names tools/%s.py, which imports kitlib/%s '
                              '(%s)' % (tool, pkg, path))
    return None if found else 'nothing maps %s to a test' % path


def _tool_change(path, name, tree, picks):
    found = picks.tool_users({name}, lambda tool: 'names %s' % path)
    # A deleted tool is not in the tree, but its importers still name it.
    users = {x for x, (_pkgs, tops) in tree.tools.items() if name in tops}
    found |= picks.tool_users(users, lambda tool: 'names tools/%s.py, which imports %s'
                              % (tool, path))
    return None if found else 'nothing maps %s to a test' % path


def _tests_change(path, pkg, tree, picks):
    if pkg not in tree.tests:
        return '%s is not in a test package of this tree' % path
    dotted = path[:-3].replace('/', '.') if path.endswith('.py') else None
    found = False
    for m, tm in sorted(tree.modules.items()):
        if dotted in tm.files:
            found |= picks.add(m, 'changed (%s)' % path if m == dotted else
                               'uses %s (%s)' % (dotted, path))
    if not found:
        # a file the scan did not read (data, a deleted file) or one no test module uses
        picks.add(pkg, 'changed in tests/%s (%s)' % (pkg, path))
        for m, tm in sorted(tree.modules.items()):
            if pkg in tm.tests:
                picks.add(m, 'names tests.%s (%s)' % (pkg, path))
    return None


def _by_name(path, tree, picks):
    base = path.rsplit('/', 1)[-1]
    found = False
    for t in sorted(tree.naming(base)):
        found |= picks.add(t, 'names %s' % base)
    return found


def selection(changed, tree, cfg):
    """(ALL, [why]) or (sorted names, {name: [reasons]}, notes) - see the docstring; a name is a
    whole test package or a dotted test module. `notes` lists the prose paths no test names.
    Pure: reads only `tree` and `cfg`."""
    picks, notes = _Picks(tree), []
    for f in sorted({p.replace('\\', '/') for p in changed}):
        why = None
        parts = f.split('/')
        # `paths` adds to every rule below: a test package that reads another folder.
        mapped = [mods for g, mods in cfg['paths'] if glob_regex(g).match(f)]
        found = False
        for mods in mapped:
            for m in mods:
                found |= picks.add(m, 'reads %s (%s)' % (f, CONFIG_REL))
        if _matches(f, cfg['full']):
            why = '%s changed (a test helper or the selection itself)' % f
        elif parts[0] == 'kitlib' and len(parts) > 2:
            why = _kitlib_change(f, parts[1], tree, cfg, picks)
        elif parts[0] == 'tools' and len(parts) == 2 and f.endswith('.py'):
            why = _tool_change(f, parts[1][:-3], tree, picks)
        elif parts[0] == 'tests' and len(parts) > 2:
            why = _tests_change(f, parts[1], tree, picks)
        else:
            if mapped:
                found |= _by_name(f, tree, picks)
                why = None if found else 'its %s packages do not exist (%s)' % (CONFIG_REL, f)
            elif _matches(f, cfg['by_name']):
                why = None if _by_name(f, tree, picks) else 'no test package names %s' % f
            elif _matches(f, cfg['prose']):
                if not _by_name(f, tree, picks):
                    notes.append(f)
            else:
                why = '%s is not mapped (%s)' % (f, CONFIG_REL)
        if why is not None:
            return ALL, ['full suite: ' + why], notes
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
