"""kitlib.staging.culprit: the landings, the suspects and the hunt's order, with stand-in probes, a
stand-in revert and a stand-in git (the hunt run for real: tests/staging/test_promote.py Hunt)."""
import os
import shutil
import tempfile
import unittest
from unittest import mock

from kitlib import gate as G
from kitlib.dailyfull import bisect as B
from kitlib.gate import rerun
from kitlib.staging import culprit as CU
from kitlib.staging import record as R
from kitlib.staging import revert as V
from tests.staging.test_changes import FakeGit

M = 'b' * 40                                    # main
L1, L2, L3 = ('1' * 40), ('2' * 40), ('3' * 40)  # first-parent M..S, oldest first; S is L3
LANDS = [CU.Landing(L1, ['src/a.py']), CU.Landing(L2, ['docs/x.md']), CU.Landing(L3, ['c.txt'])]
REVERT = 'e' * 40


class Probe(object):
    """`probe(sha, wait_s)`: `verdicts` {sha: RED, GREEN or None}; every call in `calls`."""

    def __init__(self, verdicts):
        self.verdicts, self.calls = verdicts, []

    def __call__(self, sha, wait_s):
        self.calls.append((sha, wait_s))
        v = self.verdicts[sha]
        return v, (['unit'] if v == B.RED else []), (None if v else 'the lock refused')


class Make(object):
    def __init__(self, status=V.MADE):
        self.status, self.calls = status, []

    def __call__(self, merge, onto):
        self.calls.append((merge, onto))
        return V.Made(self.status, REVERT if self.status == V.MADE else None,
                      ['a.txt'] if self.status == V.CONFLICT else []), None


class Clock(object):
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


def hunt(verdicts, sus, make=None, clock=None, deadline=None, **kw):
    probe, make, clock = Probe(verdicts), make or Make(), clock or Clock()
    found = CU.hunt(LANDS, M, L3, sus, probe, make, deadline or clock() + CU.HUNT_BUDGET_S,
                    clock, **kw)
    return found, probe, make


class Hunt(unittest.TestCase):
    def test_s_green_again_is_a_flake_and_nothing_more_runs(self):
        found, probe, make = hunt({L3: B.GREEN}, LANDS[:1])
        self.assertEqual((found['end'], found['verdict'], found['merge']), (CU.FLAKE, B.FLAKE, None))
        self.assertEqual(([s for s, _ in probe.calls], make.calls), ([L3], []))

    def test_s_inconclusive_stops_the_hunt(self):
        found, probe, _ = hunt({L3: None}, LANDS[:1])
        self.assertEqual(found['end'], CU.INCONCLUSIVE)
        self.assertEqual(found['verdict'], 'inconclusive: the lock refused')
        self.assertEqual(len(probe.calls), 1)

    def test_one_suspect_whose_revert_turns_s_green_is_confirmed_by_revert(self):
        found, probe, make = hunt({L3: B.RED, REVERT: B.GREEN}, [LANDS[1]])
        self.assertEqual((found['end'], found['merge'], found['by']), (CU.FOUND, L2, CU.BY_REVERT))
        self.assertEqual(found['verdict'], 'culprit %s (confirmed by revert)' % L2[:10])
        self.assertEqual([s for s, _ in probe.calls], [L3, REVERT])
        self.assertEqual(make.calls, [(L2, L3)])
        self.assertEqual(found['made'].commit, REVERT)
        self.assertEqual([(p['sha'], p['what'], p['verdict']) for p in found['probes']],
                         [(L3, 'S', B.RED), (REVERT, 'revert of %s' % L2[:10], B.GREEN)])
        self.assertEqual(found['suspects'], [L2])

    def test_a_revert_still_red_goes_on_to_the_bisect(self):
        found, probe, make = hunt({L3: B.RED, REVERT: B.RED, L1: B.GREEN, L2: B.RED}, [LANDS[1]])
        self.assertEqual((found['end'], found['merge'], found['by']), (CU.FOUND, L2, CU.BY_BISECT))
        self.assertIn('its revert at S probed red too', found['verdict'])
        self.assertEqual([s for s, _ in probe.calls], [L3, REVERT, L1, L2])   # S is not run twice
        self.assertEqual(make.calls, [(L2, L3), (L2, L3)])

    def test_a_revert_probe_inconclusive_stops_the_hunt(self):
        found, probe, _ = hunt({L3: B.RED, REVERT: None}, [LANDS[1]])
        self.assertEqual(found['end'], CU.INCONCLUSIVE)
        self.assertEqual(len(probe.calls), 2)

    def test_a_revert_that_conflicts_is_not_probed_and_the_bisect_runs(self):
        found, probe, _ = hunt({L3: B.RED, L1: B.RED, M: B.GREEN}, [LANDS[1]],
                               make=Make(V.CONFLICT))
        self.assertEqual((found['merge'], found['by']), (L1, CU.BY_BISECT))
        self.assertNotIn(REVERT, [s for s, _ in probe.calls])

    def test_several_suspects_go_straight_to_the_bisect(self):
        found, probe, make = hunt({L3: B.RED, L1: B.GREEN, L2: B.RED}, LANDS[:2])
        self.assertEqual((found['merge'], found['by']), (L2, CU.BY_BISECT))
        self.assertEqual(found['verdict'], 'culprit %s (by bisect)' % L2[:10])
        self.assertEqual([s for s, _ in probe.calls], [L3, L1, L2])
        self.assertEqual(make.calls, [(L2, L3)])        # the culprit's revert, on S

    def test_no_suspect_bisects_too(self):
        found, _, _ = hunt({L3: B.RED, L1: B.GREEN, L2: B.GREEN}, [])
        self.assertEqual((found['merge'], found['by']), (L3, CU.BY_BISECT))

    def test_main_red_too_is_on_main_and_no_revert(self):
        found, probe, make = hunt({L3: B.RED, L1: B.RED, M: B.RED}, LANDS)
        self.assertEqual((found['end'], found['merge']), (CU.ON_MAIN, None))
        self.assertIn('main (%s) is red too' % M[:10], found['verdict'])
        self.assertEqual([s for s, _ in probe.calls], [L3, L1, M])
        self.assertEqual(make.calls, [])

    def test_the_probe_cap_ends_capped_naming_the_range(self):
        found, probe, _ = hunt({L3: B.RED, L1: B.GREEN}, LANDS, max_probes=2)
        self.assertEqual(found['end'], CU.CAPPED)
        self.assertIn('probe cap 2 reached', found['verdict'])
        self.assertEqual(len(probe.calls), 2)

    def test_each_probe_waits_at_most_the_time_left_and_none_left_is_budget(self):
        clock = Clock()
        deadline = clock() + 100

        def probe(sha, wait_s):
            seen.append(wait_s)
            clock.now += 60
            return B.RED, ['unit'], None
        seen = []
        found = CU.hunt(LANDS, M, L3, LANDS, probe, Make(), deadline, clock, budget=100)
        self.assertEqual(seen, [100, 40])
        self.assertEqual(found['end'], CU.BUDGET)
        self.assertTrue(found['verdict'].startswith('inconclusive (budget): '), found['verdict'])
        self.assertEqual(len(found['probes']), 2)

    def test_a_probe_that_gave_up_once_the_budget_ran_out_is_budget(self):
        clock = Clock()

        def probe(sha, wait_s):
            clock.now += wait_s         # the slot wait ran to its end: the lock's timeout
            return None, [], 'the machine lock timed out'
        found = CU.hunt(LANDS, M, L3, LANDS, probe, Make(), clock() + 50, clock, budget=50)
        self.assertEqual(found['end'], CU.BUDGET)
        self.assertEqual(found['verdict'], 'inconclusive (budget): the machine lock timed out')

    def test_the_budget_is_about_four_hours(self):
        self.assertEqual(CU.HUNT_BUDGET_S, 4 * 3600)


class Suspects(unittest.TestCase):
    LANDS = [CU.Landing(L1, ['src/a.py']), CU.Landing(L2, ['docs/x.md']),
             CU.Landing(L3, ['tests/test_a.py'])]

    def read(self, rel):
        return {'docs/x.md': 'calls unit\n'}.get(rel)

    def shas(self, found):
        return [x.sha for x in found]

    def test_a_step_with_globs_takes_the_landings_it_selects(self):
        specs = {'unit': {'name': 'unit', 'globs': ['src/*', 'tests/*'], 'always': True}}
        self.assertEqual(self.shas(CU.suspects(specs, ['unit'], self.LANDS, set(), self.read)),
                         [L1, L3])

    def test_a_step_with_no_globs_or_one_the_gate_lacks_takes_every_landing(self):
        for specs in ({'unit': {'name': 'unit', 'always': True}}, {}):
            self.assertEqual(self.shas(CU.suspects(specs, ['unit'], self.LANDS, set(), self.read)),
                             [L1, L2, L3])

    def test_a_content_rule_reads_the_tree_at_s(self):
        specs = {'unit': {'name': 'unit', 'globs': ['src/*'],
                          'content': {'globs': ['docs/*'], 'regex': 'calls unit'}}}
        self.assertEqual(self.shas(CU.suspects(specs, ['unit'], self.LANDS, set(), self.read)),
                         [L1, L2])

    def test_a_manual_step_still_takes_what_its_globs_select(self):
        specs = {'unit': {'name': 'unit', 'globs': ['src/*'], 'manual': True}}
        self.assertEqual(self.shas(CU.suspects(specs, ['unit'], self.LANDS, set(), self.read)),
                         [L1])

    def test_a_landing_that_changed_a_failing_test_s_file_is_the_one(self):
        specs = {'unit': {'name': 'unit'}}
        self.assertEqual(self.shas(CU.suspects(specs, ['unit'], self.LANDS, {'tests/test_a.py'},
                                               self.read)), [L3])

    def test_a_path_under_a_failing_runner_scope_is_a_hit(self):
        specs = {'unit': {'name': 'unit'}}
        self.assertEqual(self.shas(CU.suspects(specs, ['unit'], self.LANDS, {'src/'},
                                               self.read)), [L1])

    def test_one_suspect_is_one_across_every_red_step(self):
        specs = {'unit': {'name': 'unit', 'globs': ['src/*']},
                 'docs': {'name': 'docs', 'globs': ['docs/*']}}
        self.assertEqual(self.shas(CU.suspects(specs, ['unit'], self.LANDS, set(), self.read)),
                         [L1])
        self.assertEqual(self.shas(CU.suspects(specs, ['unit', 'docs'], self.LANDS, set(),
                                               self.read)), [L1, L2])


class FailingPaths(unittest.TestCase):
    def setUp(self):
        self.top = tempfile.mkdtemp(prefix='kit-culprit-')
        self.addCleanup(shutil.rmtree, self.top, True)
        for rel in ('tests/test_a.py', 'sub/tests/test_b.py'):
            path = os.path.join(self.top, *rel.split('/'))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            open(path, 'w').close()

    def test_a_class_s_file_in_its_step_s_folder_and_a_runner_s_scope(self):
        failures = {'unit': [{'cls': 'tests.test_a.Case', 'id': 'tests.test_a.Case.t'}],
                    'sub': [{'cls': 'tests.test_b.Case', 'id': 'tests.test_b.Case.t'}],
                    'jvm': [{'cls': 'x.Y', 'id': 'x.Y.t', 'runner': 'gradle', 'scope': 'mod'}],
                    'green': [{'cls': 'tests.test_a.Case', 'id': 'tests.test_a.Case.t'}]}
        seen = []

        def cover(res, rel, top, since):
            seen.append((res['name'], rel, since))
            return {'failures': [dict(f) for f in failures[res['name']]]}
        results = [{'name': n, 'status': 'FAIL'} for n in failures]
        specs = {'sub': {'name': 'sub', 'cwd': 'sub'}}
        with mock.patch.object(rerun, 'cover_step', side_effect=cover):
            got = CU.failing_paths(self.top, results, ['unit', 'sub', 'jvm'], specs, 99.0)
        self.assertEqual(got, {'tests/test_a.py', 'sub/tests/test_b.py', 'mod/'})
        self.assertEqual(seen, [('unit', '', 99.0), ('sub', 'sub', 99.0), ('jvm', '', 99.0)])


class Landings(unittest.TestCase):
    def test_every_first_parent_commit_with_the_paths_it_changed(self):
        git = FakeGit({('log',): (0, '%s 1\n%s 2' % (L1, L2), ''),
                       ('diff', '--name-only', '--no-renames', '-z', L1 + '^1'):
                           (0, 'a.txt\0b/c.txt\0', ''),
                       ('diff', '--name-only', '--no-renames', '-z', L2 + '^1'): (0, '', '')})
        got, why = CU.landings('.', git, M, L2)
        self.assertIsNone(why)
        self.assertEqual(got, [CU.Landing(L1, ['a.txt', 'b/c.txt']), CU.Landing(L2, [])])
        self.assertEqual(git.calls[0][-1], '%s..%s' % (M, L2))
        self.assertEqual(len([c for c in git.calls if c[0] == 'log']), 1)     # one listing

    def test_a_diff_that_fails_is_why(self):
        git = FakeGit({('log',): (0, '%s 1' % L1, ''), ('diff',): (128, '', 'bad object')})
        got, why = CU.landings('.', git, M, L1)
        self.assertIsNone(got)
        self.assertIn('bad object', why)


class Pieces(unittest.TestCase):
    def test_the_bisect_s_lines_are_said_as_the_hunt_s(self):
        said = []
        say = CU.say_as_hunt(said.append)
        say('daily: bisect probe 1 at abc: unit')
        say('other line')
        self.assertEqual(said, ['promote: hunt probe 1 at abc: unit', 'other line'])

    def test_a_probe_s_slot_waits_the_time_left_never_now_or_never(self):
        class Prober(object):
            book_kw = {'no_wait': True, 'cores': 1, 'gate_mode': 'bisect'}

            def __call__(self, sha):
                return B.GREEN, [], None
        p = Prober()
        CU.bound(p)('a' * 40, 99.7)
        self.assertEqual(p.book_kw, {'cores': 1, 'gate_mode': 'bisect', 'wait_s': 99})
        CU.bound(p)('a' * 40, 0.2)
        self.assertEqual(p.book_kw['wait_s'], 1)

    def test_the_red_steps_drop_the_gate_s_own_and_a_green_one(self):
        out = G.Outcome(G.FAILED, [], results=[
            {'name': 'unit', 'status': 'FAIL'}, {'name': 'gate', 'status': 'FAIL'},
            {'name': 'windows', 'status': 'FAIL'}, {'name': 'lint', 'status': 'PASS'}])
        self.assertEqual(CU.red_steps(out), ['unit'])

    def test_a_red_with_no_step_to_probe_is_not_hunted_and_writes_nothing(self):
        tmp = tempfile.mkdtemp(prefix='kit-culprit-')
        self.addCleanup(shutil.rmtree, tmp, True)
        a = R.start(tmp, 'f' * 40, M, 'red', why='the gate failed')
        out = G.Outcome(G.FAILED, [], results=[{'name': 'gate', 'status': 'FAIL'}])
        self.assertEqual(CU.after_red({}, a, out, lambda line: None), [])
        self.assertNotIn('culprit', R.attempts(tmp)[0].rec)


if __name__ == '__main__':
    unittest.main()
