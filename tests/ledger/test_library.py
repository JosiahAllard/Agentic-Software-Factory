"""The kitlib.ledger functions tools/ledger.py used to hold: files.under and read_views,
migrate.report_for and check_out, audit.check_all (the CLI tests pin their printed output)."""
import os
import tempfile
import unittest
from unittest import mock

from kitlib.ledger import audit, files, migrate, vcs
from kitlib.ledger.errors import Failed, Usage

ENTRY = ('## R-0001 | 2026-10-01 08:00Z | alpha | selected\n'
         'Q: q\nA: a\nDecides: Rule one.\nSource: s\nSupersedes: -\n')
DIGEST = '# Standing rulings\n\n## alpha\n\n### Landing\n- R-0001: land one branch at a time.\n'


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        self.log = os.path.join(self.dir, 'LEDGER.md')
        self.digest = os.path.join(self.dir, 'STANDING.md')
        with open(self.log, 'w', encoding='utf-8', newline='\n') as f:
            f.write('# Rulings ledger\n\n' + ENTRY)
        with open(self.digest, 'w', encoding='utf-8', newline='\n') as f:
            f.write(DIGEST)
        self.found = files.of_config({'ledger_path': self.log})


class FilesTest(Base):
    def test_under_is_the_folder_or_anything_below_it(self):
        self.assertTrue(files.under(os.path.join(self.dir, 'a', '..', 'b'), self.dir))
        self.assertTrue(files.under(self.dir, self.dir))
        self.assertFalse(files.under(self.dir + 'x', self.dir))

    def test_read_views_reads_the_ledger_and_names_an_unreadable_one(self):
        texts, why = files.read_views(self.found, None)
        self.assertIsNone(why)
        self.assertIn('R-0001', texts[0])
        os.remove(self.log)
        texts, why = files.read_views(self.found, None)
        self.assertIsNone(texts)
        self.assertIn(self.log, why)

    def test_an_unlisted_repo_is_a_usage_error_in_the_split_layout(self):
        found = files.of_config({'ledger_dir': self.dir, 'ledger_repos': ['alpha']})
        with self.assertRaises(Usage):
            files.views(found, 'beta')


class MigrateTest(Base):
    def test_report_for_proposes_each_rule_and_fails_without_a_digest(self):
        self.assertIn('R-0001', migrate.report_for(self.found))
        os.remove(self.digest)
        with self.assertRaises(Failed):
            migrate.report_for(self.found)

    def test_check_out_refuses_a_ledger_file_and_the_digest_only(self):
        for bad in (self.log, self.digest, os.path.join(self.dir, 'rules', 'x.md')):
            with self.assertRaises(Usage):
                migrate.check_out(self.found, bad)
        migrate.check_out(self.found, os.path.join(self.dir, 'report.txt'))


class AuditTest(Base):
    def test_check_all_counts_entries_and_a_cut_digest_citation_is_a_problem(self):
        with mock.patch.object(vcs, 'base_text', return_value=('', None)), \
                mock.patch.object(vcs, 'default_base', return_value='origin/main'):
            got = audit.check_all(self.found, ['alpha'])
            self.assertEqual(got.problems, 0, got.lines)
            self.assertEqual(got.lines[-1], 'ledger: 1 entries, OK')
            with open(self.digest, 'a', encoding='utf-8', newline='\n') as f:
                f.write('- R-0009: cites nothing live.\n')
            bad = audit.check_all(self.found, ['alpha'])
        self.assertGreaterEqual(bad.problems, 1)
        self.assertTrue(bad.lines[0].startswith('FAIL '))


if __name__ == '__main__':
    unittest.main()
