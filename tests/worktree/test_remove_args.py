"""tools/worktree_remove.py's argument split: -C is the tool's own option, never inside a value."""
import unittest

from tools import worktree_remove as tool


class SplitRoot(unittest.TestCase):
    def test_an_abandon_reason_that_starts_with_dash_c_keeps_its_value(self):
        argv = ['slug', '--abandon', '-Cfoo']
        self.assertEqual(tool.split_root(argv), ('.', argv))
        self.assertEqual(tool.parse(argv), ('abandon', 'slug', '-Cfoo', False, False))

    def test_a_tag_and_a_mark_stale_reason_keep_their_values(self):
        self.assertEqual(tool.split_root(['--tag', '-Cx', 'slug']), ('.', ['--tag', '-Cx', 'slug']))
        self.assertEqual(tool.split_root(['b', '--mark-stale', '-C']), ('.', ['b', '--mark-stale', '-C']))

    def test_a_real_c_beside_an_abandon_still_names_the_root(self):
        self.assertEqual(tool.split_root(['-C', 'repo', 's', '--abandon', '-Cfoo']),
                         ('repo', ['s', '--abandon', '-Cfoo']))
        self.assertEqual(tool.split_root(['s', '--abandon', 'why', '-Crepo']), ('repo', ['s', '--abandon', 'why']))

    def test_c_twice_or_without_a_value_is_still_refused(self):
        self.assertEqual(tool.split_root(['-Ca', '-Cb'])[0], None)
        self.assertEqual(tool.split_root(['s', '-C'])[0], None)


if __name__ == '__main__':
    unittest.main()
