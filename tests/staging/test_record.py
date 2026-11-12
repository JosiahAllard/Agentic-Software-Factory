"""kitlib.staging.record: one attempt's phase record, in a scratch folder."""
import json
import os
import shutil
import tempfile
import unittest

from kitlib.staging import record as R

S1, S2 = 'a' * 40, 'b' * 40
BASE = 'c' * 40


class Case(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='staging-record-')
        self.addCleanup(shutil.rmtree, self.dir, True)


class Phases(Case):
    def test_a_new_attempt_is_written_at_its_phase(self):
        a = R.start(self.dir, S1, BASE, 'tested', log_dir='L', stamp='20261112-010203')
        self.assertEqual(a.path, os.path.join(self.dir, '20261112-010203-' + S1[:10], R.FILE))
        with open(a.path, encoding='utf-8') as f:
            rec = json.load(f)
        self.assertEqual((rec['schema'], rec['via'], rec['sha'], rec['base'], rec['phase']),
                         (R.SCHEMA, 'promotion', S1, BASE, 'tested'))
        self.assertEqual((rec['log_dir'], rec['tries'], rec['announced']), ('L', 0, False))
        self.assertEqual([p for p, _ in rec['history']], ['tested'])
        self.assertEqual(rec['pid'], os.getpid())
        self.assertTrue(a.live())

    def test_each_set_is_written_and_kept_in_the_history(self):
        a = R.start(self.dir, S1, BASE, 'tested')
        a.set('pushing', tries=1)
        a.set('pushed', why='promoted')
        a.set('done')
        b = R.attempts(self.dir)[0]
        self.assertEqual([p for p, _ in b.rec['history']], ['tested', 'pushing', 'pushed', 'done'])
        self.assertEqual((b.phase, b.rec['tries'], b.rec['why']), ('done', 1, 'promoted'))
        self.assertFalse(b.live())

    def test_a_phase_that_is_not_one_is_refused(self):
        a = R.start(self.dir, S1, BASE, 'tested')
        with self.assertRaises(R.RecordError):
            a.set('synced')
        self.assertEqual(R.attempts(self.dir)[0].phase, 'tested')

    def test_the_live_phases_and_the_ends_are_apart(self):
        self.assertEqual(set(R.LIVE) & set(R.ENDS), set())
        self.assertIn('refused', R.HOLDS)
        self.assertIn('red', R.HOLDS)
        self.assertNotIn('refused', R.LIVE)
        self.assertNotIn('behind', R.HOLDS)
        self.assertNotIn('failed', R.HOLDS)
        self.assertTrue(set(R.HOLDS) <= set(R.ANNOUNCE))

    def test_a_record_that_cannot_be_written_is_a_record_error(self):
        blocker = os.path.join(self.dir, 'file')
        with open(blocker, 'w') as f:
            f.write('x')
        with self.assertRaises(R.RecordError):
            R.start(blocker, S1, BASE, 'tested')


class Reading(Case):
    def test_oldest_first_unreadable_and_other_schemas_skipped(self):
        R.start(self.dir, S1, BASE, 'red', stamp='20261112-000001')
        R.start(self.dir, S2, BASE, 'tested', stamp='20261112-000002')
        bad = os.path.join(self.dir, '20261112-000003-x')
        os.makedirs(bad)
        with open(os.path.join(bad, R.FILE), 'w') as f:
            f.write('not json')
        other = os.path.join(self.dir, '20261112-000004-y')
        os.makedirs(other)
        with open(os.path.join(other, R.FILE), 'w') as f:
            json.dump({'schema': 99, 'phase': 'tested'}, f)
        found = R.attempts(self.dir)
        self.assertEqual([a.sha for a in found], [S1, S2])

    def test_attempts_of_one_second_keep_their_order(self):
        for sha in (S2, S1, S2, S1):            # S1 sorts first by name; the order is by start
            R.start(self.dir, sha, BASE, 'done')
        self.assertEqual([a.sha for a in R.attempts(self.dir)], [S2, S1, S2, S1])

    def test_no_folder_is_no_attempts(self):
        self.assertEqual(R.attempts(os.path.join(self.dir, 'none')), [])

    def test_unfinished_is_the_latest_live_attempt(self):
        R.start(self.dir, S1, BASE, 'pushing', stamp='20261112-000001')
        R.start(self.dir, S2, BASE, 'done', stamp='20261112-000002')
        self.assertEqual(R.unfinished(R.attempts(self.dir)).sha, S1)
        R.start(self.dir, S2, BASE, 'pushed', stamp='20261112-000003')
        self.assertEqual(R.unfinished(R.attempts(self.dir)).phase, 'pushed')

    def test_held_is_a_hold_at_that_sha_only(self):
        R.start(self.dir, S1, BASE, 'behind', stamp='20261112-000001')
        R.start(self.dir, S2, BASE, 'failed', stamp='20261112-000002')
        found = R.attempts(self.dir)
        self.assertIsNone(R.held(found, S1))
        self.assertIsNone(R.held(found, S2))
        for i, end in enumerate(R.HOLDS):
            R.start(self.dir, S1, BASE, end, stamp='20261112-00010%d' % i)
            self.assertEqual(R.held(R.attempts(self.dir), S1).phase, end)
        self.assertIsNone(R.held(R.attempts(self.dir), S2))

    def test_unannounced_until_marked(self):
        R.start(self.dir, S1, BASE, 'refused', stamp='20261112-000001')
        R.start(self.dir, S2, BASE, 'done', stamp='20261112-000002')
        owed = R.unannounced(R.attempts(self.dir))
        self.assertEqual([a.sha for a in owed], [S1])
        owed[0].rec['announced'] = True
        owed[0].write()
        self.assertEqual(R.unannounced(R.attempts(self.dir)), [])

    def test_the_status_line(self):
        a = R.start(self.dir, S1, BASE, 'red', why='unit failed')
        line = R.line(a)
        self.assertTrue(line.startswith('promote   RED %s on main %s at ' % (S1[:10], BASE[:10])),
                        line)
        self.assertTrue(line.endswith(' - unit failed'), line)


if __name__ == '__main__':
    unittest.main()
