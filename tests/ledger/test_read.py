"""kitlib.ledger.read: the one reader of the rulings ledger's entries."""
import os
import tempfile
import unittest

from kitlib import ledger
from kitlib.backlog import ledger as backlog_ledger
from kitlib.ledger import read

TEXT = """# Rulings ledger

## R-9001 | 2026-10-01 08:00Z | alpha | selected
Q: q
A: a
Decides: First.
Decides: Second wins.
Source: s
Supersedes: -
Items: FOO, BAR

## R-9002 | 2026-10-02 08:00Z | ALPHA | Typed
Q: q
Items: FOO, not an id

## R-9003 | 2026-10-03 | all | delegated
Decides: Header without a time.

## R-9004 | 2026-10-04 08:00Z | alpha | selected
Retired: spent - done (see R-9001)

## R-9005 | 2026-10-05 08:00Z | alpha | selected
Q: q
Retired: spent - not alone (see -)
Items: FOO

not a header ## R-9006 | x | y | z
## R-9006
"""

IDS = ['R-9001', 'R-9002', 'R-9003', 'R-9004', 'R-9005']


class ParseTest(unittest.TestCase):
    def setUp(self):
        self.by = {e.id: e for e in read.parse(TEXT)}

    def test_every_header_is_read_in_order(self):
        self.assertEqual([e.id for e in read.parse(TEXT)], IDS)

    def test_fields_of_a_full_entry(self):
        e = self.by['R-9001']
        self.assertEqual((e.date, e.repo, e.kind, e.decides, e.items, e.malformed, e.retired,
                          e.strict, e.dead),
                         ('2026-10-01', 'alpha', 'selected', 'Second wins.', ('FOO', 'BAR'), None,
                          None, True, False))

    def test_repo_and_kind_are_lower_cased_and_a_loose_header_is_not_strict(self):
        e = self.by['R-9002']
        self.assertEqual((e.repo, e.kind, e.strict), ('alpha', 'typed', False))
        self.assertEqual((e.items, e.malformed), (None, 'Items: FOO, not an id'))

    def test_a_header_without_a_time_is_read_but_not_strict(self):
        e = self.by['R-9003']
        self.assertEqual((e.date, e.strict, e.decides), ('2026-10-03', False, 'Header without a time.'))

    def test_tombstone_is_the_whole_body_only(self):
        self.assertEqual(self.by['R-9004'].retired, ('spent', 'R-9001'))
        self.assertIsNone(self.by['R-9005'].retired)
        self.assertTrue(self.by['R-9005'].dead)

    def test_read_entries_needs_a_header_and_a_readable_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'L.md')
            with open(path, 'w', encoding='utf-8') as f:
                f.write('nothing here\n')
            got, why = read.read_entries(path)
            self.assertIsNone(got)
            self.assertIn('holds no `## R-nnnn', why)
            got, why = read.read_entries(os.path.join(d, 'absent.md'))
            self.assertIsNone(got)
            self.assertIn('cannot read', why)
            with open(path, 'wb') as f:
                f.write(b'\xef\xbb\xbf' + TEXT.encode('utf-8'))
            got, why = read.read_entries(path)
            self.assertEqual((sorted(got), why), (IDS, None))


class SharedTest(unittest.TestCase):
    def test_the_backlog_reads_through_the_same_reader(self):
        self.assertIs(backlog_ledger.read_entries, read.read_entries)
        self.assertIs(backlog_ledger.Entry, read.Entry)

    def test_the_digest_feed_is_a_view_of_the_same_parse(self):
        self.assertEqual(list(ledger.entries(TEXT)),
                         [('R-9001', '2026-10-01', 'selected', 'Second wins.')])
        self.assertEqual(backlog_ledger.successor({e.id: e for e in read.parse(TEXT)}, 'R-9004'),
                         'R-9001')


SUPERSEDING = """## R-9010 | 2026-10-10 08:00Z | alpha | selected
Q: q
A: a
Decides: Later.
Source: s
Supersedes: R-9001,
  R-9003
Items: FOO

## H-alpha-9001 | 2026-10-11 08:00Z | alpha | selected
Q: q
Supersedes: R-9010
"""


class SupersedesTest(unittest.TestCase):
    """What ends a ruling a hold cites (`next`'s STALE HOLD): a tombstone, or a `Supersedes:` line
    of any entry naming it; nothing else."""

    def setUp(self):
        self.entries = {e.id: e for e in read.parse(TEXT + '\n' + SUPERSEDING)}
        self.by = backlog_ledger.superseded_by(self.entries)

    def test_supersedes_reads_every_id_over_continuation_lines(self):
        self.assertEqual(self.entries['R-9010'].supersedes, ('R-9001', 'R-9003'))
        self.assertEqual(self.entries['R-9001'].supersedes, ())       # `Supersedes: -`
        self.assertEqual(self.entries['R-9002'].supersedes, ())       # no line

    def test_ended_is_a_tombstone_or_a_supersedes_line_of_any_file(self):
        ended = [backlog_ledger.ended(self.entries, self.by, rid)
                 for rid in ('R-9001', 'R-9004', 'R-9010', 'H-alpha-9001', 'R-9002', 'R-9404')]
        self.assertEqual(ended, ['superseded by R-9010', 'retired: spent',
                                 'superseded by H-alpha-9001', None, None, None])

    def test_later_follows_supersedes_and_tombstones(self):
        self.assertEqual(backlog_ledger.later(self.entries, self.by, 'R-9001'),
                         {'R-9010', 'H-alpha-9001'})
        self.assertEqual(backlog_ledger.later(self.entries, self.by, 'R-9004'),
                         {'R-9001', 'R-9010', 'H-alpha-9001'})

    def test_read_all_skips_a_repo_file_not_written_yet(self):
        with tempfile.TemporaryDirectory() as d:
            frozen = os.path.join(d, 'rulings.md')
            with open(frozen, 'w', encoding='utf-8') as f:
                f.write(TEXT)
            with open(os.path.join(d, 'alpha.md'), 'w', encoding='utf-8') as f:
                f.write('# no header yet\n')
            cfg = {'ledger_dir': d, 'ledger_repos': ['alpha', 'beta'], 'ledger_path': frozen}
            got, why = backlog_ledger.read_all(lambda: cfg)
            self.assertEqual((sorted(got), why), (IDS, None))
            got, why = backlog_ledger.read_all(lambda: {})
            self.assertEqual((got, why), (None, 'no ledger_path or ledger_dir'))

    def test_read_all_needs_the_frozen_or_one_file_ledger_read_with_a_header(self):
        with tempfile.TemporaryDirectory() as d:
            frozen = os.path.join(d, 'rulings.md')
            with open(frozen, 'w', encoding='utf-8') as f:
                f.write('# a ledger with no entry header\n')
            got, why = backlog_ledger.read_all(lambda: {'ledger_path': frozen})
            self.assertEqual((got, why), (None, '%s holds no `## R-nnnn` header' % frozen))
            got, why = backlog_ledger.read_all(
                lambda: {'ledger_path': os.path.join(d, 'absent.md')})
            self.assertIsNone(got)
            self.assertIn('cannot read', why)


if __name__ == '__main__':
    unittest.main()
