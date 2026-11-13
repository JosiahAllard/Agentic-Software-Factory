"""kitlib.staging.changes: when a promotion is due (moved here from kitlib.dailyfull.promote), with
a stand-in git."""
import unittest

from kitlib.staging import changes as C

HOUR = 3600
NOW = 1_800_000_000


class FakeGit:
    """A stand-in for kitlib.dailyfull.git: `answers` maps an args tuple prefix to (rc, out, err)."""

    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def __call__(self, cwd, *args):
        self.calls.append(args)
        for prefix, answer in self.answers.items():
            if args[:len(prefix)] == prefix:
                return answer
        return 1, '', 'not answered: %s' % ' '.join(args)


class Cfg:
    remote, main_branch = 'origin', 'main'

    def __init__(self, staging=None):
        self.staging = staging or {}

    def section(self, name):
        assert name == 'staging', name
        return self.staging


class Due(unittest.TestCase):
    def test_the_count_boundary(self):
        self.assertFalse(C.due(C.DUE_COUNT - 1, NOW - HOUR, NOW))
        self.assertTrue(C.due(C.DUE_COUNT, NOW - HOUR, NOW))

    def test_the_age_boundary(self):
        self.assertFalse(C.due(1, NOW - int(5.9 * HOUR), NOW))
        self.assertTrue(C.due(1, NOW - 6 * HOUR, NOW))

    def test_nothing_untested_is_never_due(self):
        self.assertFalse(C.due(0, None, NOW))
        self.assertFalse(C.due(0, None, NOW, landings=0))

    def test_the_settings_move_both_boundaries(self):
        self.assertTrue(C.due(2, NOW - HOUR, NOW, landings=2, hours=6))
        self.assertFalse(C.due(1, NOW - HOUR, NOW, landings=2, hours=2))
        self.assertTrue(C.due(1, NOW - 2 * HOUR, NOW, landings=2, hours=2))


class Thresholds(unittest.TestCase):
    def test_the_defaults_are_4_landings_and_6_hours(self):
        self.assertEqual(C.thresholds(Cfg()), (4, 6))
        self.assertEqual((C.DUE_COUNT, C.DUE_HOURS), (4, 6))

    def test_the_settings_are_read(self):
        cfg = Cfg({'promote_after_landings': 2, 'promote_after_hours': 1})
        self.assertEqual(C.thresholds(cfg), (2, 1))

    def test_the_schema_defaults_are_these(self):
        from kitlib.config import schema
        rows = schema.SECTIONS['staging']
        self.assertEqual((rows['promote_after_landings'].default,
                          rows['promote_after_hours'].default), (C.DUE_COUNT, C.DUE_HOURS))


class Untested(unittest.TestCase):
    def test_the_first_parent_commits_main_lacks_and_the_oldest(self):
        git = FakeGit({('log',): (0, '%d\n%d\n' % (NOW, NOW - 9), '')})
        self.assertEqual(C.untested('.', Cfg(), 'staging', git), ((2, NOW - 9), None))
        self.assertEqual(git.calls, [('log', '--first-parent', '--format=%ct',
                                      'origin/main..origin/staging')])

    def test_another_main_ref(self):
        git = FakeGit({('log',): (0, '', '')})
        self.assertEqual(C.untested('.', Cfg(), 'staging', git, main_ref='abc'),
                         ((0, None), None))
        self.assertEqual(git.calls[0][-1], 'abc..origin/staging')

    def test_a_log_that_fails_is_why(self):
        got, why = C.untested('.', Cfg(), 'staging', FakeGit({('log',): (128, '', 'bad')}))
        self.assertIsNone(got)
        self.assertIn('bad', why)


class NotDueLine(unittest.TestCase):
    def test_it_names_the_count_age_and_thresholds(self):
        line = C.not_due_line(2, NOW - 3 * HOUR, NOW, 4, 6)
        self.assertEqual(line, 'promote: NOT DUE (2 untested, oldest 3.0 h; due at 4, or at 6 h)')


if __name__ == '__main__':
    unittest.main()
