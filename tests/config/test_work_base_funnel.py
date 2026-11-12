"""The work base has one read outside kitlib.config: kitlib.config.work_base (docs/gate-landing.md
"Staging branch"). Every other read of `main_branch` in the kit's Python (kitlib, tools, boot,
githooks, each host's adapter) is a read of the published main branch on purpose, and is named in
ALLOWED with how many reads that function makes and why; a new read fails here until it goes
through the funnel or is named, a new read in a function already named included.

A read is `x.main_branch`, `x['main_branch']`, `x.get('main_branch', ...)` or
`getattr(x, 'main_branch', ...)`. Not reads, so not counted: `main_branch=` as a keyword argument
(a value passed, already read where it came from), a function or parameter named main_branch (a
definition), and the string anywhere else (a doc line, a message)."""
import ast
import glob
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# The folders scanned, relative to the root; kitlib/config is the funnel itself.
SCANNED = ('kitlib', 'tools', 'boot', 'githooks', 'hosts/*/adapter')
SKIPPED = ('kitlib/config/',)

MAIN = "the guard holds the published main branch"
STORE = "the store's main branch (Context.main_branch); its code_main is the work base"
NEW_REPO = 'the --main-branch argument of a new repo'
PMDATA = "the PM data repository's own main"
STAGING_FROM = 'main as fetched, where the staging setting is read from'
LANDER_MAIN = "the lander's main checkout is on the main branch, staging or not"

# 'path:qualname' -> (reads in it, why that function reads the main branch, not the work base).
ALLOWED = {
    'kitlib/backlog/at_landing.py:_close_in': (1, STORE),
    'kitlib/backlog/at_landing.py:_live_holder_warning':
        (1, "a pull held by the store's main branch is quiet: the published branch's name"),
    'kitlib/backlog/opener.py:open_store': (1, STORE),
    'kitlib/backlog/plan.py:Context.__init__': (1, 'the Context field itself'),
    'kitlib/backlog/plan_narrow.py:own_branch':
        (1, 'a main bundles nothing: the main branch and the code main (the work base) both'),
    'kitlib/backlog/pull_branch.py:main_branches':
        (1, "a pull may not record the store's main branch (a published branch name)"),
    'kitlib/commit/run.py:_commit': (1, LANDER_MAIN + ' (main_checkout_error)'),
    'kitlib/dailyfull/promote.py:main_of':
        (1, 'the branch a promotion moves and the one the staging setting is read from: main '
            'itself, never the work base'),
    'kitlib/gate/runner.py:merged_tip':
        (1, 'neither the main branch nor the work base is gated as a branch'),
    'kitlib/guard/backlogrole.py:_landed':
        (1, "the landed trust is a script as the local main writes it; a staging tip is not trusted"),
    'kitlib/guard/prepush.py:<module>': (1, MAIN),
    'kitlib/guard/prepush.py:_default_mains': (1, MAIN),
    'kitlib/guard/prepush.py:_exclude':
        (1, 'a push that creates its branch is judged against the published main'),
    'kitlib/guard/rules_gitjail.py:lander_forms':
        (1, "the main checkout's own fast-forward, of the main branch"),
    'kitlib/guard/rules_gitjail.py:lander_refusal':
        (1, "the main checkout's own fast-forward, of the main branch"),
    'kitlib/guard/rules_gitref.py:git_worktree_remove': (1, MAIN),
    'kitlib/guard/rules_push.py:DataBranch.__init__': (1, PMDATA),
    'kitlib/guard/rules_push.py:_guarded_mains':
        (1, "the stand-in's main; a Config's guarded_mains holds the work base too"),
    'kitlib/guard/rules_push.py:git_push': (1, MAIN),
    'kitlib/guard/rules_push.py:landing_command': (2, "a hint's placeholder for the main branch"),
    'kitlib/hubbacklog/__init__.py:main_branch':
        (1, 'the main branch itself; landed_branch asks staging first'),
    'kitlib/hubsite/backlog.py:main_commit':
        (1, 'the main branch when no staging branch is set (hubbacklog.staging is asked first)'),
    'kitlib/landing/lander.py:Lander.checkout_branch':
        (1, "the main checkout's branch, which a landing onto a staging branch never moves"),
    'kitlib/landing/merge.py:Merge.follow':
        (1, 'the config at the base names the same main branch (the staging read is apart)'),
    'kitlib/ledger/vcs.py:default_base': (1, "the ledger repository's own settings"),
    'kitlib/ledger/vcs.py:prepare': (2, "the ledger repository's own settings"),
    'kitlib/machinesetup/repo.py:RepoContext.__init__': (1, NEW_REPO),
    'kitlib/machinesetup/repo.py:Setup.next_steps': (1, NEW_REPO),
    'kitlib/machinesetup/repo.py:Setup.plan': (4, NEW_REPO),
    'kitlib/machinesetup/repo.py:Setup.report': (1, NEW_REPO),
    'kitlib/ownerfiles/apply.py:_owner_rules':
        (3, "the main checkout's branch, and " + STAGING_FROM + ' (the rules are the base\'s)'),
    'kitlib/ownerfiles/approvals.py:landed_ref': (1, STAGING_FROM),
    'kitlib/pmdata/init.py:_check_data_config': (2, PMDATA),
    'kitlib/pmdata/init.py:_check_names': (2, PMDATA),
    'kitlib/pmdata/init.py:_check_unshared': (2, PMDATA),
    'kitlib/preflight/settings.py:base_names':
        (1, 'the main branch when no staging branch is set (the work base is asked first)'),
    'kitlib/records/landing.py:LandingLock.for_config':
        (2, "the lock's main checkout branch; its base is the work base"),
    'kitlib/records/landing.py:describe':
        (1, "the lock record's main_branch field: the base the landing holds, already chosen"),
    'kitlib/worktree/remove_checks.py:settings':
        (1, 'the main branch, protected beside the work base (is_base)'),
    'tools/machine_setup.py:main': (1, NEW_REPO),
}


def files(root):
    """Every Python file under SCANNED, as root-relative paths with '/'. A githooks file with no
    extension is Python when its first lines exec python (pre-push's polyglot head)."""
    out = set()
    for pattern in SCANNED:
        for top in glob.glob(os.path.join(root, pattern)):
            for folder, dirs, names in os.walk(top):
                dirs[:] = sorted(d for d in dirs if d != '__pycache__')
                for name in names:
                    path = os.path.join(folder, name)
                    rel = os.path.relpath(path, root).replace(os.sep, '/')
                    if rel.startswith(SKIPPED):
                        continue
                    if name.endswith('.py') or (rel.startswith('githooks/') and '.' not in name
                                                and _python_head(path)):
                        out.add(rel)
    return sorted(out)


def _python_head(path):
    with open(path, encoding='utf-8') as f:
        head = [f.readline() for _ in range(3)]
    return any('exec python' in line for line in head)


def reads(root):
    """{'path:qualname': [line, ...]} of every main_branch read in files(root)."""
    out = {}
    for rel in files(root):
        with open(os.path.join(root, rel), encoding='utf-8') as f:
            tree = ast.parse(f.read(), rel)
        _visit(tree, [], rel, out)
    return out


def _visit(node, names, rel, out):
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        names = names + [node.name]
    if is_read(node):
        out.setdefault('%s:%s' % (rel, '.'.join(names) or '<module>'), []).append(node.lineno)
    for child in ast.iter_child_nodes(node):
        _visit(child, names, rel, out)


def _is_key(node):
    return isinstance(node, ast.Constant) and node.value == 'main_branch'


def is_read(node):
    """`x.main_branch`, `x['main_branch']`, `x.get('main_branch', ...)` or
    `getattr(x, 'main_branch', ...)`."""
    if isinstance(node, ast.Attribute):
        return node.attr == 'main_branch'
    if isinstance(node, ast.Subscript):
        return _is_key(node.slice)
    if isinstance(node, ast.Call):
        f = node.func
        if isinstance(f, ast.Attribute) and f.attr == 'get':
            return bool(node.args) and _is_key(node.args[0])
        if isinstance(f, ast.Name) and f.id == 'getattr':
            return len(node.args) >= 2 and _is_key(node.args[1])
    return False


def unnamed(found, allowed):
    """'path:qualname (lines)' for every function whose reads ALLOWED does not name, or names
    fewer of than it makes."""
    out = []
    for k, lines in sorted(found.items()):
        want = allowed.get(k, (0, ''))[0]
        if len(lines) > want:
            out.append('%s: %d read(s), %d named (line %s)'
                       % (k, len(lines), want, ', '.join(map(str, lines))))
    return out


class WorkBaseFunnelTest(unittest.TestCase):

    def test_every_main_branch_read_outside_config_is_named(self):
        self.assertEqual(unnamed(reads(ROOT), ALLOWED), [],
                         'read the work base through kitlib.config.work_base(cfg), or name the '
                         'read in ALLOWED with why it is the main branch')

    def test_no_allowed_entry_is_stale(self):
        found = reads(ROOT)
        self.assertEqual(sorted('%s: %d named, %d read(s)' % (k, n, len(found.get(k, ())))
                                for k, (n, _) in ALLOWED.items()
                                if len(found.get(k, ())) != n), [])

    def test_every_entry_says_why(self):
        self.assertEqual(sorted(k for k, (n, why) in ALLOWED.items() if n < 1 or not why), [])

    def test_the_scan_sees_every_form(self):
        tree = ast.parse("a = cfg.main_branch\nb = d['main_branch']\nc = d.get('main_branch')\n"
                         "e = getattr(cfg, 'main_branch', 'main')\nf(main_branch=x)\n"
                         "g = d.get('remote')\nh = getattr(cfg, 'remote')\n")
        self.assertEqual(sum(is_read(n) for n in ast.walk(tree)), 4)

    def test_a_second_read_in_a_named_function_is_caught(self):
        found = {'kitlib/x.py:f': [3, 9]}
        self.assertEqual(unnamed(found, {'kitlib/x.py:f': (1, 'why')}),
                         ['kitlib/x.py:f: 2 read(s), 1 named (line 3, 9)'])
        self.assertEqual(unnamed(found, {'kitlib/x.py:f': (2, 'why')}), [])

    def test_the_scan_covers_the_entry_points(self):
        seen = files(ROOT)
        for rel in ('tools/verdict.py', 'boot/hook.py', 'githooks/pre-push',
                    'hosts/codex/adapter/workflow.py', 'kitlib/landing/lander.py'):
            self.assertIn(rel, seen)
        self.assertFalse([r for r in seen if r.startswith('kitlib/config/')])


if __name__ == '__main__':
    unittest.main()
