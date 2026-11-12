"""The work base has one read outside kitlib.config: kitlib.config.work_base (docs/gate-landing.md
"Staging branch"). Every other read of `main_branch` in kitlib (an attribute, or a subscript by
the constant 'main_branch') is a read of the published main branch on purpose, and is named in
ALLOWED with why; a new read fails here until it goes through the funnel or is named."""
import ast
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 'path:qualname' -> why that function reads the main branch itself, not the work base.
ALLOWED = {
    'kitlib/backlog/at_landing.py:_close_in':
        "the store's main branch (Context.main_branch); its code_main is the work base",
    'kitlib/backlog/at_landing.py:_live_holder_warning':
        "a pull held by the store's main branch is quiet: the published branch's name",
    'kitlib/backlog/opener.py:open_store':
        "the store's main branch (Context.main_branch); its code_main is the work base",
    'kitlib/backlog/plan.py:Context.__init__': 'the Context field itself',
    'kitlib/backlog/plan_narrow.py:own_branch':
        'a main bundles nothing: the main branch and the code main (the work base) both',
    'kitlib/backlog/pull_branch.py:main_branches':
        "a pull may not record the store's main branch (a published branch name)",
    'kitlib/commit/run.py:_commit':
        "the lander's main checkout is on the main branch, staging or not (main_checkout_error)",
    'kitlib/gate/runner.py:merged_tip': 'neither the main branch nor the work base is gated as a branch',
    'kitlib/guard/prepush.py:<module>': 'the guard holds the published main branch',
    'kitlib/guard/prepush.py:_default_mains': 'the guard holds the published main branch',
    'kitlib/guard/prepush.py:_exclude':
        'a push that creates its branch is judged against the published main',
    'kitlib/guard/rules_gitjail.py:lander_forms':
        "the main checkout's own fast-forward, of the main branch",
    'kitlib/guard/rules_gitjail.py:lander_refusal':
        "the main checkout's own fast-forward, of the main branch",
    'kitlib/guard/rules_gitref.py:git_worktree_remove': 'the guard holds the published main branch',
    'kitlib/guard/rules_push.py:DataBranch.__init__': "the PM data branch's own main",
    'kitlib/guard/rules_push.py:_guarded_mains':
        "the stand-in's main; a Config's guarded_mains holds the work base too",
    'kitlib/guard/rules_push.py:git_push': 'the guard holds the published main branch',
    'kitlib/guard/rules_push.py:landing_command': "a hint's placeholder for the main branch",
    'kitlib/hubsite/repoweb.py:_main_branch':
        "the hub shows the published main branch (hubbacklog.main_branch, a function's name)",
    'kitlib/landing/lander.py:Lander.checkout_branch':
        "the main checkout's branch, which a landing onto a staging branch never moves",
    'kitlib/landing/merge.py:Merge.follow':
        'the config at the base names the same main branch (fix 2: the staging read is apart)',
    'kitlib/ledger/vcs.py:default_base': "the ledger repository's own settings",
    'kitlib/ledger/vcs.py:prepare': "the ledger repository's own settings",
    'kitlib/machinesetup/repo.py:RepoContext.__init__': 'the --main-branch argument of a new repo',
    'kitlib/machinesetup/repo.py:Setup.next_steps': 'the --main-branch argument of a new repo',
    'kitlib/machinesetup/repo.py:Setup.plan': 'the --main-branch argument of a new repo',
    'kitlib/machinesetup/repo.py:Setup.report': 'the --main-branch argument of a new repo',
    'kitlib/ownerfiles/apply.py:_owner_rules': 'owner rules are read from the published main',
    'kitlib/ownerfiles/approvals.py:on_main': 'an approval is spent on the published main',
    'kitlib/ownerfiles/rules.py:HubApprovals._spent_why': 'an approval is spent on the published main',
    'kitlib/pmdata/init.py:_check_data_config': "the PM data repository's own main",
    'kitlib/pmdata/init.py:_check_names': "the PM data repository's own main",
    'kitlib/pmdata/init.py:_check_unshared': "the PM data repository's own main",
    'kitlib/preflight/settings.py:base_names':
        'the main branch when no staging branch is set (the work base is asked first)',
    'kitlib/records/landing.py:LandingLock.for_config':
        "the lock's main checkout branch; its base is the work base",
    'kitlib/worktree/remove_checks.py:settings':
        'the main branch, protected beside the work base (is_base)',
}


def reads(root):
    """{'path:qualname': [line, ...]} of every main_branch read in kitlib outside kitlib/config."""
    out = {}
    top = os.path.join(root, 'kitlib')
    for folder, dirs, files in os.walk(top):
        dirs[:] = sorted(d for d in dirs if d != '__pycache__')
        for name in sorted(files):
            if not name.endswith('.py'):
                continue
            path = os.path.join(folder, name)
            rel = os.path.relpath(path, root).replace(os.sep, '/')
            if rel.startswith('kitlib/config/'):
                continue
            with open(path, encoding='utf-8') as f:
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


def is_read(node):
    """`x.main_branch`, or `x['main_branch']`."""
    if isinstance(node, ast.Attribute):
        return node.attr == 'main_branch'
    if isinstance(node, ast.Subscript):
        key = node.slice
        return isinstance(key, ast.Constant) and key.value == 'main_branch'
    return False


class WorkBaseFunnelTest(unittest.TestCase):

    def test_every_main_branch_read_outside_config_is_named(self):
        found = reads(ROOT)
        new = sorted('%s (line %s)' % (k, ', '.join(map(str, v)))
                     for k, v in found.items() if k not in ALLOWED)
        self.assertEqual(new, [], 'read the work base through kitlib.config.work_base(cfg), or '
                                  'name the read in ALLOWED with why it is the main branch')

    def test_no_allowed_entry_is_stale(self):
        found = reads(ROOT)
        self.assertEqual(sorted(k for k in ALLOWED if k not in found), [])

    def test_the_scan_sees_both_forms(self):
        tree = ast.parse("a = cfg.main_branch\nb = d['main_branch']\nc = d.get('main_branch')\n")
        self.assertEqual(sum(is_read(n) for n in ast.walk(tree)), 2)


if __name__ == '__main__':
    unittest.main()
