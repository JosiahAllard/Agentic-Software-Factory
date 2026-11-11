"""kitlib.landing.checks and strict paths (docs/gate-landing-policy.md "Strict paths"): a branch that changes a path
matching the list in kitlib/gate/strict_defaults.py, read from the base and never from the branch,
lands only alone, with a queued verdict (kitlib.landing.verdicts) at its tip from a strong-tier
reviewer who is not its author, whoever built it; its merged gate then runs fresh (its
selection, nothing reused) whatever the caller passed. Anything unreadable refuses (fail closed). The landing tests are split
over three modules so each one's real-git run stays well under the test pool's unit timeout: this
one (a verdict's checks, landing alone with a fresh gate, the pure helpers),
test_land_strict_review and test_land_strict_base."""
import json
import os
import unittest

from kitlib import gate as G
from kitlib.gate import strict_defaults
from kitlib.landing import checks, verdicts
from kitlib.git import repo as repomod
from kitlib.records import gate_ok, verdict_entry
from tests.gate import fixtures
from tests.landing import test_land

BRANCH = test_land.BRANCH
LIST_FILE = 'kitlib/gate/strict_defaults.py'    # read at the base (checks.STRICT_FILE)
STRICT = 'kitlib/gate/thing.py'     # matches kitlib/gate/** of the running kit's STRICT_DEFAULTS
REVIEWER = {'verdict': 'LAND', 'risk': 'High', 'reviewer': 'pm-kit:reviewer',
            'reviewer_model': 'opus', 'reviewer_id': 'agent-r1',
            'author': 'Kit Test / strong-worker', 'session': 'coord-1', 'records': []}


class StrictCase(test_land.LandCase):
    REVIEW_TIPS = False     # every verdict here is queued (or not) by the test itself

    def common(self):
        common, why = repomod.common_dir(self.repo.root)
        self.assertIsNotNone(common, why)
        return common

    def queue(self, branch, tip, **over):
        entry = dict(REVIEWER, branch=branch, tip=tip, range='%s..%s' % (self.main[:10], tip))
        entry.update(over)
        verdicts.add(self.common(), entry)

    def queue_by_hand(self, branch, tip, drop):
        """An entry file written past add(), lacking the key `drop`."""
        entry = dict(REVIEWER, branch=branch, tip=tip, range='%s..%s' % (self.main[:10], tip),
                     queued_utc='2026-10-14T00:00:00Z', state='queued', last_result=None)
        del entry[drop]
        os.makedirs(verdict_entry.queue_dir(self.common()), exist_ok=True)
        with open(verdict_entry.entry_path(self.common(), branch), 'w', encoding='utf-8') as f:
            json.dump(entry, f)

    def on_main(self, files, message='main edit'):
        for rel, text in files.items():
            self.repo.write(rel, text)
        self.main = self.repo.commit(message, *files)
        self.repo.git('push', '-q', 'origin', 'main')

    def land_it(self, branch, tip, **kw):
        said = []
        lander = self.lander(branch, self.main, tip, say=said.append, **kw)
        return lander, lander.run(), said

    def assert_refused(self, out, *words):
        self.assertTrue(out.lines[0].startswith('REFUSED: '), out.lines)
        self.assertTrue(out.lines[0].endswith('; nothing was merged'), out.lines)
        for w in words:
            self.assertIn(w, out.lines[0])
        self.assert_untouched(out, G.REFUSED)


class NeedsAVerdict(StrictCase):
    def test_a_strict_change_with_no_queued_verdict_is_refused(self):
        _, tip = self.branch('feature', {STRICT: 'x = 1\n', 'a.py': 'a\n'})
        _, out, _ = self.land_it(BRANCH, tip)
        self.assert_refused(out, '%s changes strict path %s' % (BRANCH, STRICT),
                            'none is queued for it')

    def test_a_verdict_from_a_model_below_the_strong_tier_is_refused(self):
        _, tip = self.branch('feature', {STRICT: 'x = 1\n'})
        for model in ('sonnet', 'haiku', 'account-default', 'claude-sonnet-4-6', 'gpt-5'):
            # Medium: add() refuses a light review of a High-risk branch (test_review_tier)
            self.queue(BRANCH, tip, risk='Medium', reviewer_model=model)
            _, out, _ = self.land_it(BRANCH, tip)
            self.assert_refused(out, 'the reviewer ran on %s, not a strong-tier model' % model)

    def test_a_verdict_whose_reviewer_is_the_author_is_refused(self):
        _, tip = self.branch('feature', {STRICT: 'x = 1\n'})
        self.queue(BRANCH, tip, author='Kit Test / strong-worker agent-r1')
        _, out, _ = self.land_it(BRANCH, tip)
        self.assert_refused(out, 'the reviewer agent-r1 (pm-kit:reviewer) is its author')
        self.queue(BRANCH, tip, reviewer='pm-kit:strong-worker', reviewer_id='agent-r2')
        _, out, _ = self.land_it(BRANCH, tip)
        self.assert_refused(out, 'the reviewer agent-r2 (pm-kit:strong-worker) is its author')

    def test_a_verdict_given_at_another_tip_is_refused(self):
        wt, old = self.branch('feature', {STRICT: 'x = 1\n'})
        self.queue(BRANCH, old)
        self.repo.write('b.py', 'b\n', root=wt)
        tip = self.repo.commit('more', 'b.py', cwd=wt)
        _, out, _ = self.land_it(BRANCH, tip)
        self.assert_refused(out, 'the verdict was given at %s, and the branch is at %s'
                            % (old[:10], tip[:10]))

    def test_an_entry_lacking_the_reviewer_model_or_the_author_is_refused(self):
        _, tip = self.branch('feature', {STRICT: 'x = 1\n'})
        for missing in ('reviewer_model', 'author'):
            self.queue_by_hand(BRANCH, tip, missing)
            _, out, _ = self.land_it(BRANCH, tip)
            self.assert_refused(out, 'its verdict entry cannot be used', 'lacks %s' % missing)

    def test_an_entry_marked_failed_is_refused(self):
        _, tip = self.branch('feature', {STRICT: 'x = 1\n'})
        self.queue(BRANCH, tip)
        verdicts.mark_failed(self.common(), BRANCH, 'red gate', tip=tip)
        _, out, _ = self.land_it(BRANCH, tip)
        self.assert_refused(out, 'its verdict entry is marked failed (red gate)')


class LandsAloneFresh(StrictCase):
    # `unit` always runs and `api` only for src/*: the strict merge selects `unit` alone and
    # runs it fresh, never --full (ARCHITECTURE.md "What a landing runs")
    GATE = {'steps': [{'name': 'unit', 'always': True, 'cmd': fixtures.RED_AWARE,
                       'pass': '^ok$'},
                      {'name': 'api', 'globs': ['src/*'], 'cmd': fixtures.RED_AWARE,
                       'pass': '^ok$'}]}

    def test_a_reviewed_strict_branch_lands_with_a_fresh_merged_gate(self):
        _, tip = self.branch('feature', {STRICT: 'x = 1\n'})
        self.queue(BRANCH, tip)
        lander, out, said = self.land_it(BRANCH, tip)
        self.assertEqual(out.code, G.OK, out.lines)
        self.assertEqual(self.remote_main(), out.merge)
        self.assertTrue(lander.fresh and not lander.full)
        self.assertIn('STRICT: %s changes strict path %s: reviewed by pm-kit:reviewer on opus '
                      '(agent-r1); the merged gate runs fresh (nothing reused)' % (BRANCH, STRICT),
                      said)
        rec, why = gate_ok.verify(self.repo.root, out.merge)
        self.assertIsNone(why)
        self.assertEqual((gate_ok.mode_of(rec), [s['name'] for s in rec['steps']]),
                         ('select', ['unit']))

    def test_a_full_model_id_naming_the_strong_tier_counts(self):
        _, tip = self.branch('feature', {STRICT: 'x = 1\n'})
        self.queue(BRANCH, tip, reviewer_model='claude-opus-5-5')
        lander, out, said = self.land_it(BRANCH, tip)
        self.assertEqual(out.code, G.OK, out.lines)
        self.assertTrue(lander.fresh and not lander.full)
        self.assertIn('STRICT: %s changes strict path %s: reviewed by pm-kit:reviewer on '
                      'claude-opus-5-5 (agent-r1); the merged gate runs fresh (nothing reused)'
                      % (BRANCH, STRICT), said)

    def test_a_reviewed_plain_branch_is_not_escalated(self):
        _, tip = self.branch('feature', {'src/a.py': 'a\n'})
        self.queue(BRANCH, tip)
        lander, out, said = self.land_it(BRANCH, tip)
        self.assertEqual(out.code, G.OK, out.lines)
        self.assertFalse(lander.full or lander.fresh)
        self.assertEqual([l for l in said if l.startswith('STRICT')], [])

    def test_a_batch_holding_a_strict_branch_is_refused(self):
        _, strict_tip = self.branch('strict', {STRICT: 'x = 1\n'})
        _, plain_tip = self.branch('feature', {'src/a.py': 'a\n'})
        self.queue('kit/strict', strict_tip)
        _, out, _ = self.land_it([BRANCH, 'kit/strict'], [plain_tip, strict_tip])
        self.assert_refused(out, 'kit/strict changes strict path %s' % STRICT,
                            'lands alone, never in a batch')


class Pure(unittest.TestCase):
    def test_parse_strict_reads_the_literal_and_runs_nothing(self):
        self.assertEqual(checks.parse_strict("'''doc'''\nSTRICT_DEFAULTS = (\n  'a/**',\n  'b/*',\n)\n"),
                         ('a/**', 'b/*'))
        for text, why in (('STRICT_DEFAULTS = (', 'does not parse'),
                          ('X = 1\n', 'assigns STRICT_DEFAULTS 0 times'),
                          ("STRICT_DEFAULTS = ('a',)\nSTRICT_DEFAULTS = ('b',)\n", '2 times'),
                          ("STRICT_DEFAULTS += ('a',)\n", 'not a literal tuple'),
                          ('STRICT_DEFAULTS = tuple(make())\n', 'not a literal tuple'),
                          ("STRICT_DEFAULTS = 'a/**'\n", 'not a literal tuple'),
                          ('STRICT_DEFAULTS = ()\n', 'non-empty glob strings'),
                          ("STRICT_DEFAULTS = ('a', ' ')\n", 'non-empty glob strings'),
                          ("STRICT_DEFAULTS = ('a', 1)\n", 'non-empty glob strings')):
            with self.assertRaises(ValueError) as cm:
                checks.parse_strict(text)
            self.assertIn(why, str(cm.exception))

    def test_the_kits_own_list_parses_as_it_imports(self):
        self.assertEqual(checks.STRICT_FILE, LIST_FILE)
        path = os.path.join(os.path.dirname(strict_defaults.__file__), 'strict_defaults.py')
        with open(path, encoding='utf-8') as f:
            self.assertEqual(checks.parse_strict(f.read()), tuple(strict_defaults.STRICT_DEFAULTS))

    def test_strong_models_are_the_strong_and_deep_tiers_of_no_weaker_tier(self):
        models, why = verdicts.strong_models()
        self.assertIsNone(why)
        self.assertIn('opus', models)
        for weak in ('sonnet', 'haiku', 'account-default'):
            self.assertNotIn(weak, models)
        models, why = verdicts.strong_models(os.path.join(os.path.dirname(__file__), 'no-such.json'))
        self.assertIsNone(models)
        self.assertIn('hosts.json', why)

    def test_is_author(self):
        def entry(**over):
            return dict(REVIEWER, **over)
        self.assertFalse(verdicts.is_author(entry()))
        self.assertTrue(verdicts.is_author(entry(author='opus / strong-worker Agent-R1')))
        self.assertFalse(verdicts.is_author(entry(author='opus / strong-worker agent-r12')))
        self.assertTrue(verdicts.is_author(entry(reviewer='pm-kit:strong-worker')))
        self.assertTrue(verdicts.is_author(entry(reviewer='Strong-Worker')))
        self.assertFalse(verdicts.is_author(entry(reviewer='pm-kit:worker')))

    def test_is_author_splits_the_author_on_slash_comma_and_semicolon(self):
        for author in ('Kit Test (agent), strong-worker', 'Kit Test (agent); strong-worker',
                       'Kit Test (agent),pm-kit:strong-worker'):
            with self.subTest(author=author):
                self.assertTrue(verdicts.is_author(dict(REVIEWER, reviewer='pm-kit:strong-worker',
                                                    author=author)))
        self.assertFalse(verdicts.is_author(dict(REVIEWER, author='Kit Test (agent), strong-worker')))

    def test_is_author_finds_the_reviewer_type_as_a_word_anywhere_in_the_author(self):
        for author in ('Kit Test (agent) (pm-kit:strong-worker)', 'Kit Test | strong-worker',
                       'Kit Test & strong-worker', 'Kit Test strong-worker',
                       'Kit Test (agent) other-plugin:Strong-Worker.', 'strong-worker'):
            with self.subTest(author=author):
                self.assertTrue(verdicts.is_author(dict(REVIEWER, reviewer='pm-kit:strong-worker',
                                                    author=author)))

    def test_is_author_matches_each_id_of_a_reviewer_id_list(self):
        for rid, author in (('a169fe6a70777751b,a85bb01f9175819d1', 'a85bb01f9175819d1'),
                            ('a169fe6a70777751b,a85bb01f9175819d1',
                             'Kit Test / a85bb01f9175819d1'),
                            ('agent-r9/agent-r1', 'Kit Test / worker agent-r1'),
                            ('agent-r9 agent-r1', 'Kit Test / worker (agent-r1)')):
            with self.subTest(reviewer_id=rid, author=author):
                self.assertTrue(verdicts.is_author(dict(REVIEWER, reviewer_id=rid, author=author)))

    def test_is_author_never_matches_part_of_a_longer_type(self):
        for reviewer, author in (('pm-kit:worker', 'Kit Test | strong-worker'),
                                 ('pm-kit:worker', 'Kit Test (agent) (pm-kit:strong-worker)'),
                                 ('worker', 'Kit Test & weak-worker'),
                                 ('pm-kit:worker', 'Kit Test workers'),
                                 ('pm-kit:strong-worker', 'Kit Test | worker'),
                                 ('pm-kit:strong-worker', 'Kit Test (pm-kit:worker)'),
                                 ('pm-kit:strong-worker', 'Kit Test strong-worker-2'),
                                 ('pm-kit:strong-worker', 'Kit Test strong-worker_x'),
                                 ('pm-kit:reviewer', 'Kit Test | strong-worker')):
            with self.subTest(reviewer=reviewer, author=author):
                self.assertFalse(verdicts.is_author(dict(REVIEWER, reviewer=reviewer,
                                                     author=author)))
        for rid in ('agent-r9,agent-r1', 'agent-r9 / agent-r1', 'agent-r9 agent-r1'):
            with self.subTest(reviewer_id=rid):
                self.assertFalse(verdicts.is_author(dict(REVIEWER, reviewer_id=rid,
                                                     author='Kit Test / worker agent-r12')))

    def test_strong_alias_maps_a_full_model_id_and_refuses_the_rest(self):
        strong, weak = {'opus'}, {'haiku', 'sonnet', 'account-default'}
        for model, alias in (('opus', 'opus'), (' Opus ', 'opus'),
                             ('claude-opus-5-5', 'opus'), ('Claude-Opus-4-1', 'opus'),
                             ('claude-opus-5-5[1m]', 'opus'),
                             ('claude-sonnet-4-6', None), ('claude-haiku-4-5', None),
                             ('sonnet', None), ('account-default', None), ('gpt-5', None),
                             ('opusx-1', None), ('claude-opus5', None),
                             ('claude-opus-sonnet', None), ('', None)):
            with self.subTest(model=model):
                self.assertEqual(verdicts.strong_alias(model, strong, weak), alias)
        # a model in a strong and a weak tier (Codex's account-default) never counts
        self.assertIsNone(verdicts.strong_alias('account-default', {'opus', 'account-default'},
                                            {'account-default'}))



if __name__ == '__main__':
    unittest.main()
