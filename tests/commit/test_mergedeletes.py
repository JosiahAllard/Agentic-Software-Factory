"""kitlib.commit.mergedeletes: the rule of docs/commit.md "A merge that only deletes". The merge
commits it judges are in tests/guard/test_ownercommits.py (DeletionMerges) and commit.py's merge in
progress in tests/commit/test_commit.py (AMergeThatOnlyDeletes)."""
import unittest

from kitlib.commit import mergedeletes


class KeepsOnlyDeletions(unittest.TestCase):
    """The rule on contents alone: (ours, theirs, result, bases) and whether the merge may hold
    the result."""

    T = b'1\n2\n3\n4\n5\n'

    def test_the_table(self):
        T = self.T
        crlf = T.replace(b'\n', b'\r\n')
        table = [
            ('both sides\' deletions', (b'1\n2\n4\n5\n', b'1\n2\n3\n5\n', b'1\n2\n5\n', [T]), True),
            ('the same deletion on both', (b'1\n2\n4\n5\n', b'1\n2\n4\n5\n', b'1\n2\n4\n5\n', [T]),
             True),
            ('deletions in CRLF', (crlf.replace(b'3\r\n', b''), crlf.replace(b'4\r\n', b''),
                                   b'1\r\n2\r\n5\r\n', [crlf]), True),
            ('a duplicate one side deleted once', (b'a\nb\n', b'a\na\n', b'a\n', [b'a\na\nb\n']),
             True),
            ('a line one side deleted, kept', (b'1\n2\n4\n5\n', b'1\n2\n3\n5\n', b'1\n2\n3\n5\n',
                                               [T]), False),
            ('a new line', (b'1\n2\n4\n5\n', b'1\n2\n3\n5\n', b'1\n2\n5\nnew\n', [T]), False),
            ('a line moved', (T, T, b'2\n1\n3\n4\n5\n', [T]), False),
            ('a line both kept, dropped', (b'1\n2\n4\n5\n', b'1\n2\n3\n5\n', b'1\n5\n', [T]),
             False),
            ('both copies of a duplicate dropped', (b'a\nb\n', b'a\na\n', b'', [b'a\na\nb\n']),
             False),
            ('one side\'s addition dropped', (T + b'6\n', b'1\n2\n3\n5\n', b'1\n2\n3\n5\n', [T]),
             False),
            ('line endings changed', (T, T, crlf, [T]), False),
            ('the last newline dropped', (b'1\n2\n4\n5\n', b'1\n2\n3\n5\n', b'1\n2\n5', [T]),
             False),
            ('a NUL byte', (b'1\n\0\n4\n', b'1\n\0\n3\n', b'1\n\0\n', [b'1\n\0\n3\n4\n']), False),
            ('no merge base', (b'1\n2\n4\n5\n', b'1\n2\n3\n5\n', b'1\n2\n5\n', []), False),
            ('a base the difference adds to', (b'1\n2\n4\n5\n', b'1\n2\n3\n5\n', b'1\n2\n5\n',
                                               [T, b'1\n2\n4\n5\n']), False),
        ]
        for name, args, want in table:
            with self.subTest(name):
                self.assertIs(mergedeletes.keeps_only_deletions(*args), want)


if __name__ == '__main__':
    unittest.main()
