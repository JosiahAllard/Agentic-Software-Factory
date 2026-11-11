"""kitlib.schedtask.line: the pieces of a PowerShell registration line. Nothing is run here."""
import unittest

from kitlib.schedtask import line as L


class Quoting(unittest.TestCase):

    def test_every_single_quote_is_doubled(self):
        for q in L.SINGLE_QUOTES:
            self.assertEqual(L.quoted('a%sb' % q), "'a%s%sb'" % (q, q))


if __name__ == '__main__':
    unittest.main()
