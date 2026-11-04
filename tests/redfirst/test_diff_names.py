"""A python test that errors on a name the base lacks is RED only when the base..head diff adds
that name (kitlib.redfirst.verdict missing_name and in_diff, run.py added_lines; docs/redfirst.md
"What counts as red"). End to end in a throwaway repository: each form once with the name the
change adds (RED) and once with a name it does not add (INCONCLUSIVE). The test file's own lines
name both, so they must not count as the diff.
"""
import unittest

from kitlib.redfirst import run as R
from kitlib.redfirst import verdict as V

from .test_run import Case

CALC = ('def add(a, b):\n    return a + b\n\n\nclass Calc(object):\n'
        '    def add(self, a, b):\n        return a + b\n')
# The change: a new function, a new method, a new keyword argument and a new module.
CALC_NEW = ('def add(a, b, scale=1):\n    return (a + b) * scale\n\n\ndef mul(a, b):\n'
            '    return a * b\n\n\nclass Calc(object):\n    def add(self, a, b):\n'
            '        return a + b\n\n    def mul(self, a, b):\n        return a * b\n')
NEWMOD = 'X = 1\n'
TESTS = '''import unittest
from unittest import mock

import calc


class T(unittest.TestCase):
    def test_module_attr_in(self):
        self.assertEqual(calc.mul(2, 3), 6)

    def test_module_attr_out(self):
        self.assertEqual(calc.div(6, 3), 2)

    def test_import_name_in(self):
        from calc import mul
        self.assertEqual(mul(2, 3), 6)

    def test_import_name_out(self):
        from calc import div
        self.assertEqual(div(6, 3), 2)

    def test_module_in(self):
        import calc_new
        self.assertEqual(calc_new.X, 1)

    def test_module_out(self):
        import calc_gone
        self.assertEqual(calc_gone.X, 1)

    def test_patch_in(self):
        with mock.patch('calc.mul', return_value=7):
            self.assertEqual(calc.mul(1, 1), 7)

    def test_patch_out(self):
        with mock.patch('calc.div', return_value=7):
            self.assertEqual(calc.div(1, 1), 7)

    def test_object_attr_in(self):
        self.assertEqual(calc.Calc().mul(2, 3), 6)

    def test_object_attr_out(self):
        self.assertEqual(calc.Calc().div(6, 3), 2)

    def test_type_attr_in(self):
        self.assertEqual(calc.Calc.mul(None, 2, 3), 6)

    def test_type_attr_out(self):
        self.assertEqual(calc.Calc.div(None, 6, 3), 2)

    def test_kwarg_in(self):
        self.assertEqual(calc.add(1, 2, scale=2), 6)

    def test_kwarg_out(self):
        self.assertEqual(calc.add(1, 2, offset=2), 5)
'''
PATH = 'tests/test_forms.py'


class DiffNames(Case):
    CALC_AT = ('tests/calc.py',)

    def setUp(self):
        super().setUp()
        self.before = self.commit({'tests/calc.py': CALC})      # the base the runs name
        self.commit({'tests/calc.py': CALC_NEW, 'tests/calc_new.py': NEWMOD, PATH: TESTS})

    def verdicts(self, form):
        """{in, out}: the verdict line of each of the form's two tests, run in one tool run."""
        rc, out = self.run_tool('--base', self.before,
                                '--tests', '%s::T.test_%s_in' % (PATH, form),
                                '--tests', '%s::T.test_%s_out' % (PATH, form))
        got = {}
        for way in ('in', 'out'):
            line = [l for l in out.splitlines() if 'T.test_%s_%s ' % (form, way) in l]
            self.assertEqual(len(line), 1, out)
            got[way] = line[0]
        return got

    def check(self, form, said):
        got = self.verdicts(form)
        self.assertTrue(got['in'].startswith(V.RED + ' '), got['in'])
        self.assertIn(said, got['in'])
        self.assertTrue(got['out'].startswith(V.INCONCLUSIVE + ' '), got['out'])
        self.assertIn('the base..head diff adds no', got['out'])
        self.assertEqual(self.throwaways(), [])

    def test_a_module_attribute(self):
        self.check('module_attr', "module 'calc' has no attribute 'mul'")

    def test_an_imported_name(self):
        self.check('import_name', "cannot import name 'mul'")

    def test_a_module(self):
        self.check('module', "No module named 'calc_new'")

    def test_a_mock_patch_target(self):
        self.check('patch', "does not have the attribute 'mul'")

    def test_an_object_attribute(self):
        self.check('object_attr', "'Calc' object has no attribute 'mul'")

    def test_a_type_attribute(self):
        self.check('type_attr', "type object 'Calc' has no attribute 'mul'")

    def test_an_unexpected_keyword_argument(self):
        self.check('kwarg', "got an unexpected keyword argument 'scale'")

    def test_a_copied_file_s_lines_are_left_out_however_its_path_is_spelled(self):
        """An `--also` helper given as ./tests\\calc_new.py is still a copied file: its lines and
        path are not the diff's; the change's other files are."""
        head = self.fx.head(self.wt)
        added = R.added_lines(self.wt, self.before, head, [PATH, '.\\tests\\calc_new.py'])
        self.assertIn('+def mul(a, b):', added)
        self.assertNotIn('calc_new', added)
        self.assertNotIn('calc_gone', added)


if __name__ == '__main__':
    unittest.main()
