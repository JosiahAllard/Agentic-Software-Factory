"""kitlib.tooldeps.selftree and tools/kit_tests.py: which of the kit's own test packages a change
selects (docs/test-tools.md "kit_tests"). Most tests run on a throwaway tree laid out like the
kit's; a few check the kit's real tree, so a table or scan regression shows on the kit itself.
"""
import importlib.util
import io
import json
import os
import tempfile
import unittest
import unittest.mock

from kitlib.tooldeps import selftree
from kitlib.tooldeps.deps import ALL
from tests.gitfixture import TempRepo, rmtree
from tests.home import TempHomeCase

KIT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_spec = importlib.util.spec_from_file_location('kit_tests_entry_for_tests',
                                               os.path.join(KIT, 'tools', 'kit_tests.py'))
cli = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cli)

PASSING = 'import unittest\n\n\nclass T(unittest.TestCase):\n    def test_ok(self):\n        pass\n'

TABLE = {
    'comment': 'fixture table',
    'full': ['tests/selection.json', 'tests/__init__.py'],
    'paths': [['data/**', ['leaf']]],
    'prose': ['docs/**', '*.md'],
}

FILES = {
    'kitlib/__init__.py': '',
    'kitlib/git/__init__.py': "HELPER = 'solo.py'\n",
    'kitlib/leaf/__init__.py': 'import kitlib.git\n',
    'kitlib/mid/__init__.py': 'from kitlib import leaf\n',
    'kitlib/top/__init__.py': 'from ..mid import thing\n',
    'kitlib/child/__init__.py': "CMD = 'python -c \"from kitlib.leaf import x\"'\n",
    'kitlib/lone/__init__.py': "LABEL = 'kitlib.leaf'\n\n\ndef f():\n    \"\"\"Uses kitlib.leaf.\"\"\"\n",
    'tools/leaftool.py': 'from kitlib.leaf import x\nfrom kitlib.git import y\n',
    'tools/wrapper.py': 'import leaftool\n',
    'tests/__init__.py': '',
    'tests/testutil.py': '',
    'tests/selection.json': json.dumps(TABLE),
    'tests/git/__init__.py': '',
    'tests/git/test_git.py': PASSING,
    'tests/leaf/__init__.py': '',
    'tests/leaf/test_leaf.py': PASSING,
    'tests/leaf/helper.py': '',
    'tests/leaf/loose/test_skipped.py': PASSING,
    'tests/mid/__init__.py': '',
    'tests/mid/test_mid.py': PASSING + '# runs tools/my_leaftool.py, not the leaf tool\n',
    'tests/top/__init__.py': '',
    'tests/top/test_top.py': PASSING,
    'tests/child/__init__.py': '',
    'tests/child/test_child.py': PASSING + '# runs tools/leaftool.py\n',
    'tests/lone/__init__.py': '',
    'tests/lone/test_lone.py': PASSING + '# checks kitlib.leaf\n',
    'tests/cli/__init__.py': '',
    'tests/cli/test_cli.py': PASSING + '# runs tools/wrapper.py\n',
    'tests/docsy/__init__.py': '',
    'tests/docsy/test_docs.py': PASSING + '# reads docs/guide.md\n',
    'tests/shared/__init__.py': '',
    'tests/shared/helpers.py': '# a fixture over kitlib.leaf\n',
    'tests/shared/test_shared.py': PASSING,
    'tests/user/__init__.py': '',
    'tests/user/test_user.py': 'from tests.shared import helpers  # noqa: F401\n' + PASSING,
    # a test that reaches kitlib only as `from kitlib import X` (no `kitlib.X` text)
    'tests/fromimp/__init__.py': '',
    'tests/fromimp/test_fromimp.py': 'from kitlib import mid as m  # noqa: F401\n' + PASSING,
    # kitlib packages that run a tool by path; the shared core's run is not closed over
    'tools/solo.py': 'import os\n',
    'kitlib/runner/__init__.py': "CMD = ['python', 'tools/solo.py']\n",
    'kitlib/runneruser/__init__.py': 'from kitlib import runner\n',
    'tests/runner/__init__.py': '',
    'tests/runner/test_runner.py': PASSING,
    'tests/runneruser/__init__.py': '',
    'tests/runneruser/test_runneruser.py': PASSING,
}
FIXTURE_TEST_PACKAGES = 13


def write_tree(root):
    for rel, text in FILES.items():
        full = os.path.join(root, *rel.split('/'))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, 'w', encoding='utf-8', newline='\n') as fh:
            fh.write(text)


class FixtureTree(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='kit-selftree-')
        self.addCleanup(rmtree, self.root)
        write_tree(self.root)
        self.tree = selftree.scan(self.root)
        self.cfg = selftree.config(self.root)

    def select(self, *paths):
        return selftree.selection(list(paths), self.tree, self.cfg)

    def test_a_change_selects_its_direct_users_not_the_transitive_closure(self):
        picked, why, notes = self.select('kitlib/leaf/x.py')
        # kitlib.leaf.x: leaf's own tests whole, and test_child, which names tools/leaftool.py,
        # which imports kitlib.leaf.x. test_lone and test_user name only the package kitlib.leaf,
        # whose __init__ does not import x.
        self.assertEqual(picked, ['leaf', 'tests.child.test_child'])
        self.assertEqual(why['leaf'], ['own tests of changed kitlib/leaf'])
        self.assertEqual(why['tests.child.test_child'],
                         ['names tools/leaftool.py, which imports kitlib.leaf.x (kitlib/leaf/x.py)'])
        self.assertEqual(notes, [])
        # kitlib.leaf itself: its readers - test_lone names it, test_user imports the
        # tests.shared helper that names it - and one hop: test_fromimp uses kitlib/mid, which
        # imports it, and test_child names leaftool. Not selected: top (kitlib/top imports mid:
        # a second hop), child as a package (kitlib/child runs leaf in a child, but no test
        # reads kitlib/child), cli (it names wrapper.py, which imports leaftool: a second hop),
        # and test_shared (it does not use the helper).
        path = 'kitlib/leaf/__init__.py'
        picked, why, notes = self.select(path)
        self.assertEqual(picked, ['leaf', 'tests.child.test_child', 'tests.fromimp.test_fromimp',
                                  'tests.lone.test_lone', 'tests.user.test_user'])
        self.assertEqual(why['tests.lone.test_lone'], ['uses kitlib.leaf (%s)' % path])
        self.assertEqual(why['tests.user.test_user'], ['uses kitlib.leaf (%s)' % path])
        self.assertEqual(why['tests.fromimp.test_fromimp'],
                         ['uses kitlib.mid, which imports kitlib.leaf (%s)' % path])
        self.assertEqual(why['tests.child.test_child'],
                         ['names tools/leaftool.py, which imports kitlib.leaf (%s)' % path])
        self.assertEqual(notes, [])

    def test_a_change_with_no_importers_selects_only_its_own_tests(self):
        self.assertEqual(self.select('kitlib/top/a.py')[0], ['top'])

    def test_a_git_change_selects_its_readers_not_everything(self):
        # no package is a shared core: kitlib/git/cmd.py is read by tests/git alone here
        picked, why, _ = self.select('kitlib/git/cmd.py')
        self.assertEqual(picked, ['git'])
        self.assertEqual(why, {'git': ['own tests of changed kitlib/git']})

    def test_a_git_change_and_a_leaf_select_both(self):
        self.assertEqual(self.select('kitlib/top/a.py', 'kitlib/git/cmd.py')[0], ['git', 'top'])

    def test_a_path_no_test_reads_selects_nothing_and_is_a_note(self):
        picked, why, notes = self.select('weird/file.txt')
        self.assertEqual((picked, why, notes), ([], {}, ['weird/file.txt']))

    def test_kitlib_folder_that_is_not_a_package_selects_its_readers(self):
        # no test package of its name and nothing imports kitlib.nope.a: a note, not everything
        self.assertEqual(self.select('kitlib/nope/a.py')[0::2], ([], ['kitlib/nope/a.py']))

    def test_the_table_and_the_test_runner_select_everything_a_helper_does_not(self):
        for path in ('tests/selection.json', 'tests/__init__.py'):
            picked, why, _ = self.select(path)
            self.assertIs(picked, ALL, path)
            self.assertEqual(why, ['full suite: %s changed (the selection, the gate config or '
                                   'code every test runs inside)' % path])
        # tests/testutil.py: no test module imports or names it
        self.assertEqual(self.select('tests/testutil.py')[0::2], ([], ['tests/testutil.py']))

    def test_tool_change_selects_the_tests_naming_it_or_a_tool_importing_it(self):
        picked, why, _ = self.select('tools/leaftool.py')
        # not tests/leaf: a tool's change cannot break the kitlib package it calls; not test_mid,
        # whose my_leaftool.py is another name
        self.assertEqual(picked, ['tests.child.test_child', 'tests.cli.test_cli'])
        self.assertEqual(why['tests.child.test_child'], ['names tools/leaftool.py'])
        self.assertEqual(why['tests.cli.test_cli'],
                         ['names tools/wrapper.py, which imports tools/leaftool.py'])

    def test_test_module_from_kitlib_import_counts(self):
        self.assertIn('mid', self.tree.tests['fromimp'].kitlib)
        self.assertIn('mid', self.tree.modules['tests.fromimp.test_fromimp'].kitlib)
        self.assertIn('kitlib.mid', self.tree.modules['tests.fromimp.test_fromimp'].mods)
        self.assertIn('tests.fromimp.test_fromimp', self.select('kitlib/mid/__init__.py')[0])
        # it imports the package, not kitlib.mid.a
        self.assertNotIn('tests.fromimp.test_fromimp', self.select('kitlib/mid/a.py')[0])

    def test_a_tool_only_run_by_path_selects_the_readers_of_its_runners(self):
        # kitlib/runner (and kitlib/git) run tools/solo.py by path and no test names it; no test
        # reads the runners either, so it runs `always` only and is a note
        picked, why, notes = self.select('tools/solo.py')
        self.assertEqual((picked, why, notes), ([], {}, ['tools/solo.py']))

    def test_a_module_s_test_files_are_its_fixture_followed_transitively(self):
        user = self.tree.modules['tests.user.test_user']
        self.assertEqual(user.files, {'tests.user.__init__', 'tests.user.test_user',
                                      'tests.shared.__init__', 'tests.shared.helpers'})
        self.assertNotIn('tests.shared.helpers', self.tree.modules['tests.shared.test_shared'].files)
        # relative imports resolve against the file's package
        nodes = selftree._nodes(selftree._parse('from .w import a\nfrom . import b\n'
                                                'from ..x import c\n'))
        self.assertEqual(selftree._test_imports(nodes, 'tests.p.q.test_m'),
                         {'tests.p.q.w', 'tests.p.q.w.a', 'tests.p.q', 'tests.p.q.b', 'tests.p.x',
                          'tests.p.x.c'})

    def test_tool_no_test_reaches_is_a_note(self):
        with open(os.path.join(self.root, 'tools', 'orphan.py'), 'w') as fh:
            fh.write('import kitlib.git\n')
        tree = selftree.scan(self.root)
        self.assertEqual(selftree.selection(['tools/orphan.py'], tree, self.cfg),
                         ([], {}, ['tools/orphan.py']))

    def test_a_test_file_change_selects_the_test_modules_using_it(self):
        picked, why, _ = self.select('tests/shared/helpers.py')
        self.assertEqual(picked, ['tests.user.test_user'])
        self.assertEqual(why['tests.user.test_user'],
                         ['uses tests.shared.helpers (tests/shared/helpers.py)'])
        self.assertEqual(self.select('tests/leaf/test_leaf.py')[0], ['tests.leaf.test_leaf'])
        # a test file no module uses, and a file the scan did not read, run the whole package;
        # the second also runs the modules naming the package
        self.assertEqual(self.select('tests/leaf/helper.py')[0], ['leaf'])
        self.assertEqual(self.select('tests/shared/data.json')[0],
                         ['shared', 'tests.user.test_user'])

    def test_a_module_whose_package_is_picked_whole_is_dropped(self):
        picked = self.select('kitlib/leaf/x.py', 'tests/lone/data.json')[0]
        self.assertIn('lone', picked)
        self.assertNotIn('tests.lone.test_lone', picked)

    def test_prose_selects_the_tests_naming_it_or_nothing(self):
        self.assertEqual(self.select('docs/guide.md')[0], ['tests.docsy.test_docs'])
        picked, _why, notes = self.select('docs/other.md', 'README.md')
        self.assertEqual(picked, [])
        self.assertEqual(notes, ['README.md', 'docs/other.md'])

    def test_prose_does_not_hide_a_code_change(self):
        self.assertEqual(self.select('docs/other.md', 'kitlib/top/a.py')[0], ['top'])

    def test_always_packages_join_any_change_that_is_not_everything(self):
        cfg = dict(self.cfg, always=['lone'])
        sel = lambda *p: selftree.selection(list(p), self.tree, cfg)
        picked, why, notes = sel('docs/other.md')              # prose no test names
        self.assertEqual(picked, ['lone'])
        self.assertEqual(why['lone'], ['reads every tracked file (always, tests/selection.json)'])
        self.assertEqual(notes, ['docs/other.md'])
        self.assertEqual(sel('kitlib/top/a.py')[0], ['lone', 'top'])
        self.assertEqual(sel('weird/file.txt')[0::2], (['lone'], ['weird/file.txt']))  # no reader
        self.assertIs(sel('tests/selection.json')[0], ALL)     # everything stays everything
        self.assertEqual(sel()[0], [])                         # no change, nothing to scan

    def test_always_must_be_a_list_of_strings(self):
        with self.assertRaises(selftree.ConfigError):
            selftree.config(self.root, dict(TABLE, always='scrub'))

    def test_an_always_module_runs_as_a_single_module(self):
        cfg = dict(self.cfg, always=['tests.leaf.test_x'])
        picked, why, _ = selftree.selection(['kitlib/top/a.py'], self.tree, cfg)
        self.assertEqual(selftree.split_picked(picked), (['top'], ['tests.leaf.test_x']))
        self.assertIn('always', why['tests.leaf.test_x'][0])

    def test_plan_runs_and_prints_an_always_module(self):
        cfg = dict(self.cfg, always=['tests.leaf.test_leaf'])
        out = []
        modules = cli.kitrun.plan(self.root, ['kitlib/top/a.py'], cfg, out.append)
        self.assertEqual(modules, ['tests.top.test_top', 'tests.leaf.test_leaf'])
        self.assertTrue(any(l.strip().startswith('tests/leaf/test_leaf ') for l in out), out)
        self.assertIn('selected 2 of 14 test module(s) (1 of %d test package(s) whole)'
                      % FIXTURE_TEST_PACKAGES, out[-1])

    def test_always_dotted_name_must_be_tests_pkg_module(self):
        for bad in ('a.b', 'tests.proc', 'tests.proc.x.y'):
            with self.assertRaises(selftree.ConfigError, msg=bad):
                selftree.config(self.root, dict(TABLE, always=[bad]))

    def test_a_file_outside_the_code_folders_no_test_names_is_a_note(self):
        self.assertEqual(self.select('hooks/pre.py'), ([], {}, ['hooks/pre.py']))

    def test_paths_table_adds_packages(self):
        picked, why, _ = self.select('data/x.json')
        self.assertEqual(picked, ['leaf'])
        self.assertEqual(why['leaf'], ['reads data/x.json (tests/selection.json)'])

    def test_test_modules_mirror_discover(self):
        self.assertEqual(selftree.test_modules(self.root, ['leaf', 'user']),
                         ['tests.leaf.test_leaf', 'tests.user.test_user'])

    def test_all_test_modules_mirror_discover_over_the_whole_tree(self):
        mods = selftree.all_test_modules(self.root)
        self.assertIn('tests.leaf.test_leaf', mods)
        self.assertIn('tests.testutil', mods)                       # discover's test*.py takes it
        self.assertNotIn('tests.leaf.loose.test_skipped', mods)     # not a package
        self.assertEqual(len(mods), 14)

    def test_string_refs_only_count_when_they_import(self):
        self.assertEqual(selftree.code_refs("X = 'kitlib.leaf'\n")[0], set())
        self.assertEqual(selftree.code_refs("X = '-m kitlib.leaf.run'\n")[0], {'leaf'})
        self.assertEqual(selftree.code_refs('from kitlib import a, b\n')[0], {'a', 'b'})
        self.assertEqual(selftree.code_refs('def f(:\n kitlib.leaf\n')[0], {'leaf'})


# Paths once unmapped or once shared core select the tests that read them
# (FILES plus these).
READER_FILES = {
    # a data file a test names, and one only a kitlib module names
    'conf/settings.ini': '',
    'conf/defaults.cfg': '',
    'conf/nobody.txt': '',
    'kitlib/cfgread/__init__.py': "NAME = 'defaults.cfg'\n",
    # module by module: mid imports leaf, other stands alone; p3's __init__ imports its leaf
    'kitlib/p2/__init__.py': '',
    'kitlib/p2/leaf.py': 'Y = 1\n',
    'kitlib/p2/other.py': 'Z = 1\n',
    'kitlib/p2/side.py': 'S = 1\n',
    'kitlib/p2/mid.py': 'from .leaf import Y  # noqa: F401\n',
    'kitlib/p3/__init__.py': 'from .leaf import Z  # noqa: F401\n',
    'kitlib/p3/leaf.py': 'Z = 1\n',
    # one hop and no second: hop1 imports p2.leaf, hop2 imports hop1
    'kitlib/hop1/__init__.py': 'import kitlib.p2.leaf  # noqa: F401\n',
    'kitlib/hop2/__init__.py': 'import kitlib.hop1  # noqa: F401\n',
    # a kitlib module that runs p2.other in a child
    'kitlib/runs/__init__.py': "CMD = 'python -m kitlib.p2.other'\n",
    # a top-level test helper importing p2.side
    'tests/fixhelp.py': 'import kitlib.p2.side  # noqa: F401\n',
    'tests/readers/__init__.py': '',
    'tests/readers/test_reads.py': PASSING + '# loads conf/settings.ini\n',
    'tests/readers/test_usescfg.py': 'import kitlib.cfgread  # noqa: F401\n' + PASSING,
    'tests/readers/test_usesleaf.py': 'from kitlib.p2 import leaf  # noqa: F401\n' + PASSING,
    'tests/readers/test_usesother.py': 'from kitlib.p2 import other  # noqa: F401\n' + PASSING,
    'tests/readers/test_usesmid.py': 'from kitlib.p2 import mid  # noqa: F401\n' + PASSING,
    'tests/readers/test_usesp3.py': 'import kitlib.p3  # noqa: F401\n' + PASSING,
    'tests/readers/test_useshop1.py': 'import kitlib.hop1  # noqa: F401\n' + PASSING,
    'tests/readers/test_useshop2.py': 'import kitlib.hop2  # noqa: F401\n' + PASSING,
    'tests/readers/test_childarg.py': "ARGS = ['-m', 'kitlib.p2.other']\n" + PASSING,
    'tests/readers/test_pathtext.py': PASSING + '# runs kitlib/p2/other.py\n',
    'tests/readers/test_usesruns.py': 'import kitlib.runs  # noqa: F401\n' + PASSING,
    'tests/readers/test_useshelp.py': 'from tests import fixhelp  # noqa: F401\n' + PASSING,
    # a module directly in kitlib/, its own test package, and a kitlib package importing it
    'kitlib/single.py': 'X = 1\n',
    'kitlib/usesingle/__init__.py': 'from kitlib import single  # noqa: F401\n',
    'tests/single/__init__.py': '',
    'tests/single/test_single.py': PASSING,
    'tests/readers/test_usessingle.py': 'from kitlib import single  # noqa: F401\n' + PASSING,
    'tests/readers/test_usesusesingle.py': 'import kitlib.usesingle  # noqa: F401\n' + PASSING,
    # a kitlib package importing p2.lazy by a literal import_module, and p2.<n> by a computed one
    'kitlib/p2/lazy.py': 'L = 1\n',
    'kitlib/dyn/__init__.py': ("import importlib\n\n\ndef load():\n"
                               "    return importlib.import_module('kitlib.p2.lazy')\n\n\n"
                               "def computed(n):\n"
                               "    return importlib.import_module('kitlib.p2.' + n)\n"),
    'tests/readers/test_usesdyn.py': 'import kitlib.dyn  # noqa: F401\n' + PASSING,
}


class Readers(unittest.TestCase):
    """A changed path selects the tests that read it, module by module, with one hop."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='kit-selftree-')
        self.addCleanup(rmtree, self.root)
        write_tree(self.root)
        for rel, text in READER_FILES.items():
            full = os.path.join(self.root, *rel.split('/'))
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, 'w', encoding='utf-8', newline='\n') as fh:
                fh.write(text)
        self.tree = selftree.scan(self.root)
        self.cfg = selftree.config(self.root)

    def select(self, *paths):
        return selftree.selection(list(paths), self.tree, self.cfg)

    def test_a_file_a_test_names_selects_that_test(self):
        # (a) once unmapped: the full suite
        picked, why, notes = self.select('conf/settings.ini')
        self.assertEqual(picked, ['tests.readers.test_reads'])
        self.assertEqual(why['tests.readers.test_reads'], ['names settings.ini'])
        self.assertEqual(notes, [])

    def test_a_file_a_kitlib_module_names_selects_that_modules_readers(self):
        # (b) kitlib/cfgread names defaults.cfg; test_usescfg imports kitlib.cfgread
        picked, why, notes = self.select('conf/defaults.cfg')
        self.assertEqual(picked, ['tests.readers.test_usescfg'])
        self.assertEqual(why['tests.readers.test_usescfg'],
                         ['uses kitlib.cfgread, which names conf/defaults.cfg'])
        self.assertEqual(notes, [])

    def test_a_file_nothing_reads_runs_always_only_and_is_a_note(self):
        # (c) never ALL
        cfg = selftree.config(self.root, dict(TABLE, always=['lone']))
        picked, _why, notes = selftree.selection(['conf/nobody.txt'], self.tree, cfg)
        self.assertEqual((picked, notes), (['lone'], ['conf/nobody.txt']))

    def test_a_module_change_selects_its_readers_not_its_packages(self):
        # (d) test_usesother imports kitlib.p2.other, which neither leaf nor mid is
        path = 'kitlib/p2/leaf.py'
        picked, why, notes = self.select(path)
        self.assertIn('tests.readers.test_usesleaf', picked)
        self.assertNotIn('tests.readers.test_usesother', picked)
        self.assertEqual(why['tests.readers.test_usesleaf'], ['uses kitlib.p2.leaf (%s)' % path])
        self.assertEqual(notes, [])
        picked = self.select('kitlib/p2/other.py')[0]
        self.assertIn('tests.readers.test_usesother', picked)
        for name in ('test_usesleaf', 'test_usesmid', 'test_useshop1'):
            self.assertNotIn('tests.readers.' + name, picked)

    def test_a_module_importing_the_change_in_its_package_carries_it(self):
        # (e) kitlib/p2/mid.py imports .leaf: importing mid runs leaf
        path = 'kitlib/p2/leaf.py'
        picked, why, _ = self.select(path)
        self.assertIn('tests.readers.test_usesmid', picked)
        self.assertEqual(why['tests.readers.test_usesmid'], ['uses kitlib.p2.mid (%s)' % path])
        # kitlib/p3's __init__ imports its leaf: importing the package runs it
        path = 'kitlib/p3/leaf.py'
        picked, why, _ = self.select(path)
        self.assertEqual(picked, ['tests.readers.test_usesp3'])
        self.assertEqual(why['tests.readers.test_usesp3'], ['uses kitlib.p3 (%s)' % path])

    def test_one_hop_outside_the_package_and_no_second(self):
        # (f) kitlib/hop1 imports kitlib.p2.leaf; kitlib/hop2 imports hop1
        path = 'kitlib/p2/leaf.py'
        picked, why, _ = self.select(path)
        self.assertEqual(picked, ['tests.readers.test_useshop1', 'tests.readers.test_usesleaf',
                                  'tests.readers.test_usesmid'])
        self.assertEqual(why['tests.readers.test_useshop1'],
                         ['uses kitlib.hop1, which imports kitlib.p2.leaf (%s)' % path])
        self.assertNotIn('tests.readers.test_useshop2', picked)

    def test_child_strings_and_path_text_read_a_module(self):
        # (g) a test running `-m kitlib.p2.other`, one naming kitlib/p2/other.py, and the
        # readers of kitlib/runs, which runs it in a child
        path = 'kitlib/p2/other.py'
        picked, why, _ = self.select(path)
        self.assertEqual(picked, ['tests.readers.test_childarg', 'tests.readers.test_pathtext',
                                  'tests.readers.test_usesother', 'tests.readers.test_usesruns'])
        self.assertEqual(why['tests.readers.test_childarg'], ['uses kitlib.p2.other (%s)' % path])
        self.assertEqual(why['tests.readers.test_pathtext'], ['uses kitlib.p2.other (%s)' % path])
        self.assertEqual(why['tests.readers.test_usesruns'],
                         ['uses kitlib.runs, which imports kitlib.p2.other (%s)' % path])

    def test_a_top_level_test_helper_selects_its_users_and_carries_its_imports(self):
        # (h) once a test helper: the full suite
        picked, why, notes = self.select('tests/fixhelp.py')
        self.assertEqual(picked, ['tests.readers.test_useshelp'])
        self.assertEqual(why['tests.readers.test_useshelp'],
                         ['uses tests.fixhelp (tests/fixhelp.py)'])
        self.assertEqual(notes, [])
        # its import of kitlib.p2.side is its users' import
        path = 'kitlib/p2/side.py'
        picked, why, _ = self.select(path)
        self.assertEqual(picked, ['tests.readers.test_useshelp'])
        self.assertEqual(why['tests.readers.test_useshelp'], ['uses kitlib.p2.side (%s)' % path])

    def test_a_module_directly_in_kitlib_selects_its_readers_and_one_hop(self):
        # (i) kitlib/single.py: its own tests/single, its importers, and the readers of
        # kitlib/usesingle, which imports it
        path = 'kitlib/single.py'
        picked, why, notes = self.select(path)
        self.assertEqual(picked, ['single', 'tests.readers.test_usessingle',
                                  'tests.readers.test_usesusesingle'])
        self.assertEqual(why['single'], ['own tests of changed %s' % path])
        self.assertEqual(why['tests.readers.test_usessingle'], ['uses kitlib.single (%s)' % path])
        self.assertEqual(why['tests.readers.test_usesusesingle'],
                         ['uses kitlib.usesingle, which imports kitlib.single (%s)' % path])
        self.assertEqual(notes, [])

    def test_a_literal_import_module_is_an_import_and_a_computed_one_is_not(self):
        # (j) kitlib/dyn imports kitlib.p2.lazy by importlib.import_module('kitlib.p2.lazy')
        path = 'kitlib/p2/lazy.py'
        picked, why, notes = self.select(path)
        self.assertEqual(picked, ['tests.readers.test_usesdyn'])
        self.assertEqual(why['tests.readers.test_usesdyn'],
                         ['uses kitlib.dyn, which imports kitlib.p2.lazy (%s)' % path])
        self.assertEqual(notes, [])
        # its import_module('kitlib.p2.' + n) names no module
        self.assertNotIn('tests.readers.test_usesdyn', self.select('kitlib/p2/side.py')[0])


_ROOT_LINE = 'import os\nROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))\n'
# Tests that load a tool by bare name, and tests that list a repo folder (FILES plus these).
LISTING_FILES = {
    'tools/lonely.py': 'import os\n',
    'docs/sub/b.md': '',
    'tests/loader/__init__.py': '',
    'tests/loader/test_loader.py': ('def load_tool(name):\n    return name\n\n\n'
                                    "WRAPPER = load_tool('wrapper')\n" + PASSING),
    'tests/loader/test_bypath.py': _ROOT_LINE + "SOLO = os.path.join(ROOT, 'tools', 'solo')\n"
                                   + PASSING,
    'tests/walker/__init__.py': '',
    'tests/walker/test_every_tool.py': (_ROOT_LINE + "import glob\nTOOLS = os.path.join(ROOT, "
                                        "'tools')\n\n\ndef tools():\n    return glob.glob("
                                        "os.path.join(TOOLS, '*.py'))\n" + PASSING),
    # a helper that lists docs/sub through a loop variable; its users list it too
    'tests/walker/lister.py': (_ROOT_LINE + "PLACES = [os.path.join(ROOT, 'docs', 'sub')]\n\n\n"
                               'def listing():\n    for place in PLACES:\n'
                               '        yield os.listdir(place)\n'),
    'tests/walker/test_uses_lister.py': 'from tests.walker import lister  # noqa: F401\n' + PASSING,
    # importing another test module does not run its listing here
    'tests/walker/test_imports_walker.py': ('from tests.walker import test_every_tool  # noqa: F401\n'
                                            + PASSING),
    'tests/walker/test_temp.py': ('import os\nimport tempfile\n\n\ndef names():\n'
                                  '    return os.listdir(tempfile.mkdtemp())\n' + PASSING),
}


class LoadsAndListings(unittest.TestCase):
    """A tool loaded by bare name selects its loader; a listed folder selects its listers."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='kit-selftree-')
        self.addCleanup(rmtree, self.root)
        write_tree(self.root)
        for rel, text in LISTING_FILES.items():
            full = os.path.join(self.root, *rel.split('/'))
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, 'w', encoding='utf-8', newline='\n') as fh:
                fh.write(text)
        self.tree = selftree.scan(self.root)
        self.cfg = selftree.config(self.root)

    def select(self, *paths):
        return selftree.selection(list(paths), self.tree, self.cfg)

    def test_a_tool_loaded_by_bare_name_selects_its_loader(self):
        picked, why, _ = self.select('tools/wrapper.py')
        self.assertIn('tests.loader.test_loader', picked)
        self.assertEqual(why['tests.loader.test_loader'], ['names tools/wrapper.py'])
        # and a tool it imports reaches the loader through it
        picked, why, _ = self.select('tools/leaftool.py')
        self.assertEqual(why['tests.loader.test_loader'],
                         ['names tools/wrapper.py, which imports tools/leaftool.py'])

    def test_a_tool_path_built_from_its_bare_name_selects_the_test(self):
        # tools/solo.py: no test names solo.py, but test_bypath joins 'tools', 'solo'
        picked, why, _ = self.select('tools/solo.py')
        self.assertIsNot(picked, ALL)
        self.assertEqual(why['tests.loader.test_bypath'], ['names tools/solo.py'])

    def test_tool_loads_reads_loader_calls_and_tool_paths(self):
        nodes = selftree._nodes(selftree._parse(
            "load_tool('gate')\ninproc.load_tool('backlog')\ntool('commit')\n"
            "os.path.join(ROOT, 'tools', 'land')\nprint('hello')\nload('a b')\n"
            "load_tool(NAME)\n"))
        self.assertEqual(selftree._tool_loads(nodes), {'gate', 'backlog', 'commit', 'land'})

    def test_a_test_listing_a_folder_is_selected_by_a_change_in_it(self):
        # a prose file in a listed folder is not a note: a test reads it
        picked, why, notes = self.select('docs/sub/new.md')
        self.assertEqual(picked, ['tests.walker.test_uses_lister'])
        self.assertEqual(why['tests.walker.test_uses_lister'],
                         ['lists docs/sub/ (docs/sub/new.md)'])
        self.assertEqual(notes, [])
        # docs/ itself is not listed
        self.assertEqual(self.select('docs/other.md')[0], [])
        picked, why, _ = self.select('tools/solo.py')
        self.assertIn('tests.walker.test_every_tool', picked)
        self.assertEqual(why['tests.walker.test_every_tool'], ['lists tools/ (tools/solo.py)'])
        self.assertNotIn('tests.walker.test_imports_walker', picked)

    def test_folders_come_from_the_module_and_its_helpers_only(self):
        mods = self.tree.modules
        self.assertEqual(mods['tests.walker.test_every_tool'].walks, {'tools'})
        self.assertEqual(mods['tests.walker.test_uses_lister'].walks, {'docs/sub'})
        self.assertEqual(mods['tests.walker.test_imports_walker'].walks, set())
        self.assertEqual(mods['tests.walker.test_temp'].walks, set())   # names no repo folder

    def test_a_lister_reads_a_tool_no_test_uses(self):
        # test_every_tool lists tools/: it reads tools/lonely.py, so the path is not a note
        picked, why, notes = self.select('tools/lonely.py')
        self.assertEqual(picked, ['tests.walker.test_every_tool'])
        self.assertEqual(why['tests.walker.test_every_tool'], ['lists tools/ (tools/lonely.py)'])
        self.assertEqual(notes, [])


class Config(unittest.TestCase):
    def test_defaults_without_a_table(self):
        cfg = selftree.config('unused', {})
        self.assertNotIn('shared_core', cfg)        # no package runs everything by name
        self.assertNotIn('by_name', cfg)            # the reader rule covers any path
        self.assertEqual(cfg['full'], [])
        self.assertEqual(cfg['serial'], [])
        self.assertEqual(cfg['long'], [])

    def test_serial_entries_are_module_and_reason_pairs(self):
        cfg = selftree.config('unused', {'serial': [['tests.a.test_a', 'shares a mutex']]})
        self.assertEqual(cfg['serial'], [('tests.a.test_a', 'shares a mutex')])

    def test_bad_tables_are_refused(self):
        for data in ([], {'nope': []}, {'full': 'tests/*.py'}, {'paths': [['a/**', 'leaf']]},
                     {'prose': ['']}, {'shared_core': ['git']}, {'by_name': ['hooks/**']},
                     {'serial': ['tests.a.test_a']},
                     {'serial': [['tests.a.test_a']]}, {'serial': [['tests.a.test_a', ' ']]},
                     {'serial': [['a.test_a', 'why']]}, {'long': 'tests.a.test_a'},
                     {'long': ['a.test_a']}, {'long': [['tests.a.test_a']]}):
            with self.assertRaises(selftree.ConfigError, msg=repr(data)):
                selftree.config('unused', data)

    def test_skip_fails_entries_are_module_and_reason_pairs(self):
        self.assertEqual(selftree.config('unused', {})['skip_fails'], [])
        cfg = selftree.config('unused', {'skip_fails': [['tests.a.test_a', 'the proof']]})
        self.assertEqual(cfg['skip_fails'], [('tests.a.test_a', 'the proof')])
        for bad in (['tests.a.test_a'], [['tests.a.test_a']], [['tests.a.test_a', ' ']],
                    [['a.test_a', 'why']], 'tests.a.test_a'):
            with self.assertRaises(selftree.ConfigError, msg=repr(bad)):
                selftree.config('unused', {'skip_fails': bad})

    def test_stale_skip_fails_names_a_module_the_tree_lacks(self):
        pairs = [('tests.a.test_a', 'x'), ('tests.a.test_gone', 'y')]
        self.assertEqual(selftree.stale_skip_fails(pairs, ['tests.a.test_a']), ['tests.a.test_gone'])
        self.assertEqual(selftree.stale_skip_fails(pairs, ['tests.a.test_a', 'tests.a.test_gone']), [])

    def test_malformed_json_is_refused(self):
        root = tempfile.mkdtemp(prefix='kit-selftree-')
        self.addCleanup(rmtree, root)
        os.makedirs(os.path.join(root, 'tests'))
        with open(os.path.join(root, 'tests', 'selection.json'), 'w') as fh:
            fh.write('{')
        with self.assertRaises(selftree.ConfigError):
            selftree.config(root)


class GitAndCli(TempHomeCase):
    def setUp(self):
        super().setUp()         # TempHomeCase: kit_tests' run folder comes from user.make_temp
        self.repo = TempRepo()
        self.addCleanup(self.repo.cleanup)
        write_tree(self.repo.root)
        self.repo.commit('tree', '.')
        self.repo.git('checkout', '-q', '-b', 'kit/x')

    def run_cli(self, *argv):
        out = io.StringIO()
        rc = cli.main(['-C', self.repo.root] + list(argv), out=out)
        return rc, out.getvalue()

    def test_changed_paths_cover_commits_work_tree_and_untracked(self):
        self.repo.write('kitlib/top/a.py', 'A = 1\n')
        self.repo.commit('top', 'kitlib/top/a.py')
        self.repo.write('kitlib/mid/__init__.py', 'from kitlib import leaf\nB = 2\n')
        self.repo.write('docs/new.md', 'new\n')
        self.repo.git('checkout', '-q', 'main')
        self.repo.write('main-only.txt', 'x\n')
        self.repo.commit('main moves on', 'main-only.txt')
        self.repo.git('checkout', '-q', 'kit/x')
        paths, why = selftree.changed_paths('main', self.repo.root)
        self.assertIsNone(why)
        self.assertEqual(paths, ['docs/new.md', 'kitlib/mid/__init__.py', 'kitlib/top/a.py'])

    def test_changed_paths_list_both_sides_of_a_rename(self):
        self.repo.git('mv', 'tests/leaf/helper.py', 'tests/leaf/helper2.py')
        self.repo.git('commit', '-q', '-m', 'rename')
        paths, why = selftree.changed_paths('main', self.repo.root)
        self.assertIsNone(why)
        self.assertEqual(paths, ['tests/leaf/helper.py', 'tests/leaf/helper2.py'])
        tree, cfg = selftree.scan(self.repo.root), selftree.config(self.repo.root)
        self.assertEqual(selftree.selection(paths, tree, cfg)[0], ['leaf'])

    def test_deleted_tool_selects_its_importers_tests(self):
        self.repo.git('rm', '-q', 'tools/leaftool.py')
        self.repo.git('commit', '-q', '-m', 'delete')
        paths, why = selftree.changed_paths('main', self.repo.root)
        self.assertIsNone(why)
        self.assertEqual(paths, ['tools/leaftool.py'])
        tree, cfg = selftree.scan(self.repo.root), selftree.config(self.repo.root)
        self.assertNotIn('leaftool', tree.tools)
        picked, why, _ = selftree.selection(paths, tree, cfg)
        # tests/child names the deleted tool; tools/wrapper.py still imports it, tests/cli names
        # wrapper.py
        self.assertEqual(picked, ['tests.child.test_child', 'tests.cli.test_cli'])
        self.assertIn('names tools/wrapper.py, which imports tools/leaftool.py',
                      why['tests.cli.test_cli'])

    def test_deleted_tool_no_test_names_is_a_note(self):
        self.repo.git('rm', '-q', 'tools/solo.py')
        self.repo.git('commit', '-q', '-m', 'delete')
        paths, _ = selftree.changed_paths('main', self.repo.root)
        self.assertEqual(paths, ['tools/solo.py'])
        tree, cfg = selftree.scan(self.repo.root), selftree.config(self.repo.root)
        # kitlib/runner still names tools/solo.py, but no test reads kitlib/runner
        self.assertEqual(selftree.selection(paths, tree, cfg), ([], {}, ['tools/solo.py']))

    def test_deleted_kitlib_package_selects_its_own_tests(self):
        self.repo.git('rm', '-q', '-r', 'kitlib/lone')
        self.repo.git('commit', '-q', '-m', 'delete')
        paths, _ = selftree.changed_paths('main', self.repo.root)
        self.assertEqual(paths, ['kitlib/lone/__init__.py'])
        tree, cfg = selftree.scan(self.repo.root), selftree.config(self.repo.root)
        self.assertEqual(selftree.selection(paths, tree, cfg)[0], ['lone'])

    def test_changed_paths_refuse_an_unknown_base(self):
        paths, why = selftree.changed_paths('no-such-branch', self.repo.root)
        self.assertIsNone(paths)
        self.assertIn('merge-base', why)

    def test_plan_prints_each_package_and_its_reason(self):
        self.repo.write('kitlib/mid/__init__.py', 'from kitlib import leaf\nB = 2\n')
        rc, out = self.run_cli('--select', '--plan')
        self.assertEqual(rc, 0)
        self.assertIn('kit_tests: --select against the merge-base with main: 1 changed path(s)', out)
        self.assertIn('tests/mid', out)
        self.assertIn('own tests of changed kitlib/mid', out)
        self.assertIn('tests/fromimp/test_fromimp', out)
        self.assertNotIn('tests/top', out)          # kitlib/top imports mid: not a direct user
        self.assertIn('kit_tests: selected 2 of 14 test module(s) (1 of %d test package(s) '
                      'whole), from 1 changed path(s)' % FIXTURE_TEST_PACKAGES, out)

    def test_plan_names_the_full_suite_and_why(self):
        rc, out = self.run_cli('--select', '--changed', 'tests/selection.json', '--plan')
        self.assertEqual(rc, 0)
        self.assertIn('kit_tests: full suite: tests/selection.json changed', out)

    def test_selected_run_runs_only_the_selection(self):
        rc, out = self.run_cli('--select', '--changed', 'kitlib/top/a.py')
        self.assertEqual(rc, 0, out)
        self.assertRegex(out, r'kit_tests: OK in \d+s \(selected, ')

    def test_failing_selection_exits_1(self):
        self.repo.write('tests/top/test_top.py', PASSING.replace('pass', 'self.fail("red")'))
        rc, out = self.run_cli('--select', '--changed', 'kitlib/top/a.py')
        self.assertEqual(rc, 1)
        self.assertIn('kit_tests: FAILED, exit 1', out)

    def test_nothing_selected_runs_nothing(self):
        rc, out = self.run_cli('--select', '--changed', 'docs/other.md')
        self.assertEqual(rc, 0)
        self.assertIn('(no test)  docs/other.md: prose no test names', out)
        self.assertIn('kit_tests: OK, nothing selected (1 changed path(s))', out)

    def test_a_path_nothing_reads_says_so_not_prose(self):
        rc, out = self.run_cli('--select', '--changed', 'weird/file.txt', '--plan')
        self.assertEqual(rc, 0)
        self.assertIn('(no test)  nothing reads weird/file.txt: always-run set only', out)
        self.assertNotIn('prose', out)
        self.assertIn('kit_tests: OK, nothing selected (1 changed path(s))', out)

    def test_nothing_selected_plan_prints_the_same_ok_line(self):
        rc, out = self.run_cli('--select', '--changed', 'docs/other.md', '--plan')
        self.assertEqual(rc, 0)
        self.assertIn('kit_tests: OK, nothing selected', out)

    def test_git_failure_refuses_and_runs_nothing(self):
        rc, out = self.run_cli('--select', '--base', 'no-such-branch')
        self.assertEqual(rc, 2)
        self.assertIn('no test ran; fix git, or run --full', out)

    def test_bad_table_refuses(self):
        self.repo.write('tests/selection.json', '{"nope": 1}')
        rc, out = self.run_cli('--select', '--changed', 'kitlib/top/a.py', '--plan')
        self.assertEqual(rc, 2)
        self.assertIn('unknown key(s) nope', out)

    def test_full_with_a_bad_table_warns_and_runs_everything(self):
        self.repo.write('tests/selection.json', '{"nope": 1}')
        rc, out = self.run_cli('--full', '--jobs', '2')
        self.assertEqual(rc, 0, out)
        self.assertIn('kit_tests: WARNING: ', out)
        self.assertIn('unknown key(s) nope', out)
        self.assertIn('--full runs every module, none serial', out)
        self.assertRegex(out, r'kit_tests: OK in \d+s \(full, 2 jobs, 0 serial\): 13 tests in 13 '
                              r'module\(s\)')

    def test_full_runs_every_module_in_parallel(self):
        rc, out = self.run_cli('--full', '--jobs', '3')
        self.assertEqual(rc, 0, out)
        self.assertIn('kit_tests: (no tests) tests.testutil defines no test; not run', out)
        self.assertIn('kit_tests: 13 module(s), 3 at a time, then 0 serial', out)
        self.assertRegex(out, r'kit_tests: OK in \d+s \(full, 3 jobs, 0 serial\): 13 tests in 13 '
                              r'module\(s\), 13 package\(s\)')

    def test_jobs_1_runs_one_unittest_process(self):
        rc, out = self.run_cli('--select', '--changed', 'kitlib/top/a.py', '--jobs', '1')
        self.assertEqual(rc, 0, out)
        self.assertRegex(out, r'kit_tests: OK in \d+s \(selected, one process\)')
        self.assertNotIn('at a time', out)
        self.repo.write('tests/top/test_top.py', PASSING.replace('pass', 'self.fail("red")'))
        rc, out = self.run_cli('--select', '--changed', 'kitlib/top/a.py', '--jobs', '1')
        self.assertEqual(rc, 1)
        self.assertIn('kit_tests: FAILED, exit 1 in ', out)

    def test_jobs_must_be_at_least_1(self):
        for bad in ('0', '-2'):
            err = io.StringIO()
            with self.assertRaises(SystemExit) as e, unittest.mock.patch('sys.stderr', err):
                cli.main(['-C', self.repo.root, '--full', '--jobs', bad])
            self.assertEqual(e.exception.code, 2)
            self.assertIn('USAGE: --jobs must be at least 1', err.getvalue())

    def test_full_plan_and_usage(self):
        rc, out = self.run_cli('--full', '--plan')
        want = 'kit_tests: full suite (--full)\n'
        if os.environ.get('KIT_FAKE_GIT', '1') == '1':
            # on the fake, --full diffs for its real-git pass and says against what
            want += ("kit_tests: --full: the real-git pass's change list against the merge-base "
                     "with main: 0 changed path(s)\n")
        self.assertEqual((rc, out), (0, want))
        with self.assertRaises(SystemExit) as e, \
                unittest.mock.patch('sys.stderr', io.StringIO()):
            cli.main(['--full', '--changed', 'a'])
        self.assertEqual(e.exception.code, 2)


class KitTree(unittest.TestCase):
    """The kit's own tree and table: the rules the kit's branches rely on."""

    @classmethod
    def setUpClass(cls):
        cls.tree = selftree.scan(KIT)
        cls.cfg = selftree.config(KIT)

    def select(self, *paths):
        return selftree.selection(list(paths), self.tree, self.cfg)[0]

    def test_only_the_full_list_runs_everything(self):
        # the table, the gate config and the code every test runs inside
        for path in ('tests/selection.json', '.kit.json', 'tests/__init__.py',
                     'kitlib/__init__.py', 'kitlib/git/cmd.py', 'kitlib/git/fake/x.py',
                     'kitlib/tooldeps/pool.py', 'kitlib/tooldeps/selftree.py',
                     'tools/kit_tests.py'):
            self.assertIs(self.select(path), ALL, path)
        # once shared core or test helpers: now their readers
        for path in ('kitlib/config/load.py', 'kitlib/platform/windows/shield.py',
                     'kitlib/lock/api.py', 'tests/testutil.py', 'tests/gitfixture.py'):
            self.assertIsNot(self.select(path), ALL, path)

    def test_paths_once_unmapped_select_their_readers(self):
        # each of these once ran the full suite
        picked = self.select('kitlib/gate/strict_defaults.py')
        self.assertIsNot(picked, ALL)
        self.assertIn('gate', picked)
        picked = self.select('content/default-profile.md')
        self.assertIsNot(picked, ALL)
        self.assertIn('tests.content.test_default_profile', picked)
        self.assertIsNot(self.select('.claude/coordinator-profile.md'), ALL)

    def test_a_path_no_test_reads_runs_always_only(self):
        # built, so no test text (this one included) holds the base name
        path = 'weird/nothing-reads-' + 'this.bin'
        self.assertEqual(set(self.select(path)), set(self.cfg['always']))

    def test_a_leaf_is_selected_without_the_full_suite(self):
        picked = self.select('kitlib/machinesetup/outcome.py')
        self.assertIsNot(picked, ALL)
        self.assertIn('machinesetup', picked)

    def test_skills_test_sees_every_path_a_skill_can_name(self):
        # tests/skills checks every tools/*.py, kitlib/** and optional/** path a SKILL.md names
        # exists, so a rename or delete of one must run it.
        for path in ('tools/backlog.py', 'tools/no_such_tool.py', 'kitlib/machinesetup/outcome.py',
                     'optional/comfy/comfy_gen.py'):
            picked = self.select(path)
            self.assertTrue(picked is ALL or 'skills' in picked, path)

    def test_a_file_directly_in_kitlib_selects_its_rows_not_everything(self):
        # kitlib/** maps to skills, and the listers of kitlib/ read it
        for path in ('kitlib/data.json', 'kitlib/new.py'):
            picked = self.select(path)
            self.assertIsNot(picked, ALL, path)
            self.assertIn('skills', picked, path)
        # a module there selects its readers: tests/kitroot whole, and a test importing it
        picked = self.select('kitlib/kitroot.py')
        self.assertIsNot(picked, ALL)
        self.assertIn('kitroot', picked)
        self.assertIn('tests.guard.test_refusal_log', picked)

    def test_a_literal_import_module_reaches_its_importers_readers(self):
        # kitlib/lock/detach.py loads kitlib.platform.linux.detach by import_module
        picked = self.select('kitlib/platform/linux/detach.py')
        self.assertIsNot(picked, ALL)
        self.assertTrue('lock' in picked or 'tests.lock.test_detach' in picked, picked)

    def test_a_doc_only_change_selects_the_whole_tree_scrubs(self):
        # tests/scrub reads every file `git ls-files` lists: a docs edit with a ruling id in it
        # must reach it though nothing maps docs/** to it.
        for path in ('docs/backlog-store.md', 'README.md', '.gitignore'):
            picked = self.select(path)
            self.assertIsNot(picked, ALL, path)
            self.assertIn('scrub', picked, path)

    def test_every_always_entry_is_a_test_package_or_module(self):
        self.assertIn('scrub', self.cfg['always'])
        mods = set(selftree.all_test_modules(KIT))
        for name in self.cfg['always']:
            if name.startswith('tests.'):
                self.assertIn(name, mods)
            else:
                self.assertIn(name, self.tree.tests)

    def test_repo_wide_policy_tests_follow_any_non_test_path(self):
        # kit/comfy-headless-start: a scoped gate on optional/comfy/** skipped these three and
        # the landing's merged gate failed them.
        policy = {'platform', 'ci', 'tests.proc.test_spawn_sites'}
        for path in ('optional/comfy/blender_run.py', 'tests/optional/comfy/test_blender_run.py',
                     'hosts/codex/adapter/codex_host.py', 'mcp/hub_server.py'):
            picked = self.select(path)
            self.assertIsNot(picked, ALL, path)
            self.assertTrue(policy <= set(picked), path)

    def test_every_serial_entry_names_a_test_module(self):
        with open(os.path.join(KIT, 'tests', 'selection.json'), encoding='utf-8') as fh:
            self.assertIn('serial', json.load(fh))      # the key is written, not defaulted
        mods = set(selftree.all_test_modules(KIT))
        for module, why in self.cfg['serial']:
            self.assertIn(module, mods, why)

    def test_the_table_lists_the_slot_treekill_proof_and_none_is_stale(self):
        modules = set(selftree.all_test_modules(KIT))
        self.assertIn('tests.lock.test_slot_treekill', dict(self.cfg['skip_fails']))
        self.assertEqual(selftree.stale_skip_fails(self.cfg['skip_fails'], modules), [])

    def test_every_long_entry_names_a_test_module(self):
        mods = set(selftree.all_test_modules(KIT))
        self.assertTrue(self.cfg['long'])
        for module in self.cfg['long']:
            self.assertIn(module, mods)

    def test_a_tool_loaded_by_bare_name_selects_its_loader(self):
        # tests/backlog/test_audit.py loads it as inproc.load_tool('backlog_audit')
        self.assertIn('tests.backlog.test_audit', self.select('tools/backlog_audit.py'))

    def test_a_test_listing_tools_follows_any_tool_change(self):
        # tests/preflight/test_preflight.py lists tools/
        self.assertIn('tests.preflight.test_preflight', self.select('tools/backlog_audit.py'))

    def test_the_skill_commands_test_follows_any_tool_change(self):
        # it reads the flags of each tool content/ shows through a computed name
        # (os.path.join(TOOLS, name + '.py')), which no scan can follow
        for path in ('tools/backlog.py', 'tools/backlog_audit.py'):
            picked = self.select(path)
            self.assertIsNot(picked, ALL, path)
            self.assertIn('tests.content.test_skill_commands', picked, path)

    def test_every_test_package_is_selected_by_its_own_change(self):
        for pkg in self.tree.tests:
            picked = self.select('tests/%s/zz.py' % pkg)
            self.assertTrue(picked is ALL or pkg in picked, pkg)


if __name__ == '__main__':
    unittest.main()
