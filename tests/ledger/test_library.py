"""The kitlib.ledger functions tools/ledger.py used to hold: files.read_views and
audit.check_all (the CLI tests pin their printed output)."""
import os
import tempfile
import unittest
from unittest import mock

from kitlib.ledger import audit, files, vcs
from kitlib.ledger.errors import Usage

def rid(n):
    return 'R-%04d' % n


ENTRY = ('## ' + rid(1) + ' | 2026-10-01 08:00Z | alpha | selected\n'
         'Q: q\nA: a\nDecides: Rule one.\nSource: s\nSupersedes: -\n')
DIGEST = '# Standing rulings\n\n## alpha\n\n### Landing\n- %s: land one branch at a time.\n' % rid(1)


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
    def test_read_views_reads_the_ledger_and_names_an_unreadable_one(self):
        texts, why = files.read_views(self.found, None)
        self.assertIsNone(why)
        self.assertIn(rid(1), texts[0])
        os.remove(self.log)
        texts, why = files.read_views(self.found, None)
        self.assertIsNone(texts)
        self.assertIn(self.log, why)

    def test_an_unlisted_repo_is_a_usage_error_in_the_split_layout(self):
        found = files.of_config({'ledger_dir': self.dir, 'ledger_repos': ['alpha']})
        with self.assertRaises(Usage):
            files.views(found, 'beta')


class AuditTest(Base):
    def test_check_all_counts_entries_and_a_cut_digest_citation_is_a_problem(self):
        with mock.patch.object(vcs, 'base_text', return_value=('', None)), \
                mock.patch.object(vcs, 'default_base', return_value='origin/main'):
            got = audit.check_all(self.found, ['alpha'])
            self.assertEqual(got.problems, 0, got.lines)
            self.assertEqual(got.lines[-1], 'ledger: 1 entries, OK')
            with open(self.digest, 'a', encoding='utf-8', newline='\n') as f:
                f.write('- %s: cites nothing live.\n' % rid(9))
            bad = audit.check_all(self.found, ['alpha'])
        self.assertGreaterEqual(bad.problems, 1)
        self.assertTrue(bad.lines[0].startswith('FAIL '))


if __name__ == '__main__':
    unittest.main()
