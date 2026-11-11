"""kitlib.gate.land: a branch tip lands only with a gate-OK record for exactly that commit that the
landing takes (docs/gate-landing.md "Landing", the locked checks): a `gate.py --full` or a
default-mode pass's, or a docs-only pass's of a change docs-only under the base. No branch needs a
--full run (ARCHITECTURE.md "What a landing runs"). No record, a record of no mode a landing
takes, one from before records said whether the gate was --full, a record for another commit and
one git no longer agrees with are each REFUSED before any merge. The refusal says what gates the
tip (merge the base first, in the kit, when the tip's record lacks the `full` key); a base gate
--full selects no step from is refused first; the record's gate_config is not compared. A merge
that changes a gate config file, or whose diff git cannot list, runs every step in full once
(Lander.config_escalate)."""
import json
import os
import re
from unittest import mock

from kitlib import gate as G
from kitlib.gate import runner
from kitlib.gate import steps as S
from kitlib.records import gate_ok
from tests.gate import fixtures, test_land

BRANCH = test_land.BRANCH
COMMAND = re.compile(r'python "([^"]+)" -C "([^"]+)"')


class TipCase(test_land.LandCase):
    GATE_TIPS = False       # every tip here is gated (or not) by the test itself

    def gate(self, wt, **kw):
        out = runner.branch_gate(wt, book_kw=self.book_kw,
                                 log_dir=os.path.join(self.tmp, 'own'), **kw)
        self.assertEqual(out.code, G.OK, out.lines)
        return out

    def assert_refused(self, out, branch, tip, wt, *says):
        """REFUSED before any merge: main and the remote unmoved, no merge record, and the first
        line names the tip, each of `says` and the default-mode command that gates the tip."""
        self.assertEqual(out.code, G.REFUSED, out.lines)
        first = out.lines[0]
        self.assertIn('%s tip %s has no gate-OK record that lands it' % (branch, tip[:10]), first)
        for s in says:
            self.assertIn(s, first)
        self.assertIn('nothing was merged', first)
        m = COMMAND.search(first)
        self.assertIsNotNone(m, first)
        self.assertTrue(m.group(1).endswith(os.path.join('tools', 'gate.py')), first)
        self.assertTrue(os.path.samefile(m.group(2), wt), first)
        self.assertNotIn('--full', first[m.end():m.end() + 8])
        self.assertEqual(self.main_now(), self.main)
        self.assertEqual(self.remote_main(), self.main)
        self.assertIsNone(self.lock().read())
        self.assertEqual([r for r in self.records() if r != tip + '.json'], [])


GATE_FIRST = 'gate that tip first: '
MERGE_FIRST = 'merge origin/main (%s) into %s first'


class Refused(TipCase):
    def test_a_tip_with_no_record_is_refused(self):
        wt, tip = self.branch('feature', {'a.py': 'x = 1\n'})
        out = self.lander(BRANCH, self.main, tip).run()
        self.assert_refused(out, BRANCH, tip, wt, 'no gate-OK record for %s' % tip)

    def test_a_record_from_before_the_full_key_does_not_count(self):
        wt, tip = self.branch('feature', {'a.py': 'x = 1\n'})
        self.gate(wt, full=True)
        rec, _ = gate_ok.read(self.repo.root, tip)
        del rec['full']                             # a --full pass written by an older kit
        gate_ok.write(self.repo.root, rec)
        out = self.lander(BRANCH, self.main, tip).run()
        self.assert_refused(out, BRANCH, tip, wt, 'does not say whether the gate ran --full')
        # not the kit: the running kit's gate.py writes `full`, so no merge is asked for
        self.assertIn(GATE_FIRST, out.lines[0])
        self.assertNotIn('merge origin/main', out.lines[0])

    def test_a_record_of_the_commit_before_the_tip_does_not_count(self):
        wt, first = self.branch('feature', {'a.py': 'x = 1\n'})
        self.gate(wt)
        self.repo.write('b.py', 'y = 2\n', root=wt)
        tip = self.repo.commit('more', 'b.py', cwd=wt)
        out = self.lander(BRANCH, self.main, tip).run()
        self.assertEqual(out.code, G.REFUSED, out.lines)
        self.assertIn('no gate-OK record for %s' % tip, out.lines[0])
        os.remove(gate_ok.record_path(self.repo.root, first))
        self.assert_refused(out, BRANCH, tip, wt)

    def test_a_record_git_no_longer_agrees_with_does_not_count(self):
        wt, tip = self.branch('feature', {'a.py': 'x = 1\n'})
        self.gate(wt)
        rec, _ = gate_ok.read(self.repo.root, tip)
        rec['tree'] = '0' * 40
        gate_ok.write(self.repo.root, rec)
        out = self.lander(BRANCH, self.main, tip).run()
        self.assert_refused(out, BRANCH, tip, wt, 'names tree %s' % ('0' * 40))

    def test_a_record_of_no_mode_a_landing_takes_is_refused(self):
        wt, tip = self.branch('feature', {'a.py': 'x = 1\n'})
        self.gate(wt)
        rec, _ = gate_ok.read(self.repo.root, tip)
        rec['mode'] = 'partial'
        gate_ok.write(self.repo.root, rec)
        out = self.lander(BRANCH, self.main, tip).run()
        self.assert_refused(out, BRANCH, tip, wt, 'the record for %s is of no mode a landing '
                            'takes (mode partial, full False)' % tip)

    def test_a_batch_with_one_ungated_tip_lands_nothing(self):
        wt, tip = self.branch('feature', {'a.py': 'x = 1\n'})
        self.gate(wt)
        wt2, tip2 = self.branch('other', {'o.py': 'o = 1\n'})
        out = self.lander([BRANCH, 'kit/other'], self.main, [tip, tip2]).run()
        self.assertEqual(out.code, G.REFUSED, out.lines)
        self.assertIn('kit/other tip %s has no gate-OK record that lands it' % tip2[:10],
                      out.lines[0])
        self.assertTrue(os.path.samefile(COMMAND.search(out.lines[0]).group(2), wt2))
        os.remove(gate_ok.record_path(self.repo.root, tip))
        self.assert_refused(out, 'kit/other', tip2, wt2)


class GitCopy(TipCase):
    """A tip whose record is only in the git common dir (gate_ok.legacy_dir): with a dev_drive it
    does not count; with none (a load node set up without one) it is the gate's one record."""

    def test_with_a_dev_drive_the_gate_writes_no_git_copy_and_a_git_copy_alone_does_not_land(self):
        wt, tip = self.branch('feature', {'a.py': 'x = 1\n'})
        self.gate(wt)
        self.assertFalse(os.path.exists(gate_ok.legacy_path(self.repo.root, tip)))
        path = gate_ok.record_path(self.repo.root, tip)
        with open(path, encoding='utf-8') as f:
            body = f.read()
        os.remove(path)
        os.makedirs(gate_ok.legacy_dir(self.repo.root), exist_ok=True)
        with open(gate_ok.legacy_path(self.repo.root, tip), 'w', encoding='utf-8') as f:
            f.write(body)
        out = self.lander(BRANCH, self.main, tip).run()
        self.assert_refused(out, BRANCH, tip, wt, 'no gate-OK record for %s' % tip)

    def test_with_no_dev_drive_the_branch_gate_passes_with_only_the_git_copy(self):
        # Kept for load nodes set up without a dev_drive: a warning, not a fail.
        wt, tip = self.branch('feature', {'a.py': 'x = 1\n'})
        with mock.patch('kitlib.config.user.dev_drive', return_value=None):
            out = self.gate(wt, full=True)
        self.assertTrue(out.lines[0].startswith('GATE OK'), out.lines)
        warned = [l for l in out.lines if l.startswith(
            'warning: the gate-OK record could not be written to the record folder: ')]
        self.assertEqual(len(warned), 1, out.lines)
        self.assertIn('dev_drive', warned[0])
        self.assertTrue(os.path.samefile(out.record, gate_ok.legacy_path(self.repo.root, tip)))
        self.assertFalse(os.path.exists(gate_ok.record_path(self.repo.root, tip)))
        with mock.patch('kitlib.config.user.dev_drive', return_value=None):
            self.assertEqual(gate_ok.verify_full(self.repo.root, tip)[0]['commit'], tip)
        self.assertIsNone(gate_ok.verify_full(self.repo.root, tip)[0])


def same_path(a, b):
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


class GateCommand(TipCase):
    """The refusal's command names the worktree's own tools/gate.py when the repository being
    landed is the kit (its main checkout has the kit's layout, kitlib.content.oncommit.is_kit),
    wherever the running kit came from; any other repository is gated by the running kit's. It
    is the default-mode gate: no tip needs --full. In the kit, a tip whose record lacks the
    `full` key is told to merge the base in first: the branch's own gate.py is from before
    records said `full` (Lander.gate_advice). Being behind the base alone does not ask for a
    merge."""

    def kit_layout(self):
        self.repo.write('content/roles/worker.md', 'role\n')
        self.repo.write('tools/render_content.py', '')
        self.main = self.repo.commit('kit layout', 'content/roles/worker.md', 'tools/render_content.py')
        self.repo.git('push', '-q', 'origin', 'main')

    def main_moves(self):
        self.repo.write('later.txt', 'later\n')
        self.main = self.repo.commit('main moves on', 'later.txt')
        self.repo.git('push', '-q', 'origin', 'main')

    def refusal(self, behind=False, old_record=False):
        """The refused landing of an ungated branch (`behind`: main moves on after it branched;
        `old_record`: the tip has a --full record without the `full` key, as a gate.py from
        before the key wrote it): (gate.py it names, the worktree, the first line)."""
        wt, tip = self.branch('feature', {'a.py': 'x = 1\n'})
        if old_record:
            self.gate(wt, full=True)
            rec, _ = gate_ok.read(self.repo.root, tip)
            del rec['full']
            gate_ok.write(self.repo.root, rec)
        if behind:
            self.main_moves()
        out = self.lander(BRANCH, self.main, tip).run()
        self.assert_refused(out, BRANCH, tip, wt)
        return COMMAND.search(out.lines[0]).group(1), wt, out.lines[0]

    def test_another_repository_is_gated_by_the_running_kit(self):
        gate_py, _, first = self.refusal()
        self.assertTrue(same_path(gate_py, os.path.join(S.kit_live(), 'tools', 'gate.py')), gate_py)
        self.assertIn(GATE_FIRST, first)

    def test_another_repository_behind_its_base_is_not_told_to_merge(self):
        # the running kit's gate.py writes `full` whatever the branch holds
        gate_py, _, first = self.refusal(behind=True)
        self.assertTrue(same_path(gate_py, os.path.join(S.kit_live(), 'tools', 'gate.py')), gate_py)
        self.assertIn(GATE_FIRST, first)
        self.assertNotIn('merge origin/main', first)

    def test_the_kit_is_gated_by_the_worktrees_own_gate(self):
        self.kit_layout()
        gate_py, wt, first = self.refusal()
        self.assertTrue(same_path(gate_py, os.path.join(wt, 'tools', 'gate.py')), gate_py)
        self.assertIn(GATE_FIRST, first)            # the tip holds the base: no merge to make
        self.assertNotIn('merge origin/main', first)

    def test_a_kit_branch_behind_its_base_with_no_record_is_told_to_gate_it(self):
        # behind says nothing about the age of its gate.py: no record is asked for, no merge
        self.kit_layout()
        gate_py, wt, first = self.refusal(behind=True)
        self.assertTrue(same_path(gate_py, os.path.join(wt, 'tools', 'gate.py')), gate_py)
        self.assertIn(GATE_FIRST, first)
        self.assertNotIn('merge origin/main', first)

    def test_a_kit_tip_whose_record_lacks_full_is_told_to_merge_the_base_first(self):
        # it holds the base, yet its record shows its own gate.py never writes `full`
        self.kit_layout()
        gate_py, wt, first = self.refusal(old_record=True)
        self.assertTrue(same_path(gate_py, os.path.join(wt, 'tools', 'gate.py')), gate_py)
        self.assertIn('does not say whether the gate ran --full', first)
        self.assertIn(MERGE_FIRST % (self.main[:10], BRANCH), first)
        self.assertIn('then gate the merge and land that tip: python', first)
        self.assertNotIn(GATE_FIRST, first)


class NoFullStep(TipCase):
    """A base whose "gate" section leaves no step for --full to select (every step manual, none,
    or each dropped by another's skip_if_selected) is refused before any tip is read
    (Lander.no_full_step): neither `gate.py --full` nor the merged gate would select a step, so
    the tip check's command could never be met. One runnable step among manual ones lands."""

    def set_gate(self, gate):
        self.repo.write('.kit.json', json.dumps(dict(test_land.LandCase.KIT, gate=gate), indent=1))
        self.main = self.repo.commit('gate config', '.kit.json')
        self.repo.git('push', '-q', 'origin', 'main')

    def assert_manual_refusal(self, gate):
        self.set_gate(gate)
        _, tip = self.branch('feature', {'a.py': 'x = 1\n'})
        self.gate_tips(tip)                         # a full record: not what refuses it
        out = self.lander(BRANCH, self.main, tip).run()
        self.assertEqual(out.code, G.REFUSED, out.lines)
        first = out.lines[0]
        self.assertIn('the "gate" section of .kit.json at %s leaves no step for --full to select'
                      % self.main[:10], first)
        self.assertIn('nothing was merged', first)
        self.assertFalse(out.facts.get('undone'))      # refused before the merge, not undone
        self.assertEqual(self.main_now(), self.main)
        self.assertEqual(self.remote_main(), self.main)
        self.assertIsNone(self.lock().read())

    def test_a_gate_of_manual_steps_only_is_refused(self):
        self.assert_manual_refusal({'steps': [
            {'name': 'unit', 'manual': True, 'cmd': fixtures.RED_AWARE, 'pass': '^ok$'}]})

    def test_a_gate_with_no_step_is_refused(self):
        self.assert_manual_refusal({'steps': []})

    def test_two_steps_that_drop_each_other_are_refused(self):
        self.assert_manual_refusal({'steps': [
            dict(fixtures.GATE['steps'][0], name='unit', skip_if_selected=['other']),
            dict(fixtures.GATE['steps'][0], name='other', skip_if_selected=['unit'])]})

    def test_one_step_that_is_not_manual_lands(self):
        self.set_gate({'steps': [
            {'name': 'slow', 'manual': True, 'cmd': fixtures.RED_AWARE, 'pass': '^ok$'},
            dict(fixtures.GATE['steps'][0])]})
        wt, tip = self.branch('feature', {'a.py': 'x = 1\n'})
        self.gate(wt)
        out = self.lander(BRANCH, self.main, tip).run()
        self.assertEqual(out.code, G.OK, out.lines)


class TipConfig(TipCase):
    """The tip record's `gate_config` is not compared with any config (Lander.tip_gate_check):
    a branch that changes its own gate is gated by it and lands; the merged gate runs the base's,
    every step in full once, as the merge changes a gate config file (Lander.config_escalate).
    .kit.json is owner-only, so the base names an owner and the owner commits the change
    (tests/gate/test_land_owner.py)."""
    OWNER = 'owner@example.invalid'
    KIT = dict(test_land.LandCase.KIT, owner={'email': OWNER})

    def test_a_branch_that_changes_its_gate_lands_on_its_own_record(self):
        gate = {'steps': [dict(fixtures.GATE['steps'][0]),
                          dict(fixtures.GATE['steps'][0], name='lint')]}
        wt = self.repo.worktree('feature')
        self.repo.write('.kit.json', json.dumps(dict(self.KIT, gate=gate), indent=1), root=wt)
        self.repo.git('add', '.kit.json', cwd=wt)
        self.repo.git('-c', 'user.email=%s' % self.OWNER, 'commit', '-q', '-m', 'gate', cwd=wt)
        tip = self.repo.git('rev-parse', 'HEAD', cwd=wt)
        self.gate(wt)
        rec, why = gate_ok.verify(self.repo.root, tip)
        self.assertIsNone(why)
        self.assertNotEqual(rec['gate_config'],
                            gate_ok.config_hash(gate_ok.gate_config_at(self.repo.root, self.main)))
        self.assertEqual([s['name'] for s in rec['steps']], ['unit', 'lint'])
        out = self.lander(BRANCH, self.main, tip).run()
        self.assertEqual(out.code, G.OK, out.lines)
        merged = gate_ok.read(self.repo.root, out.merge)[0]
        self.assertEqual((gate_ok.mode_of(merged), [s['name'] for s in merged['steps']]),
                         ('full', ['unit']))         # the base's gate, run in full


class Lands(TipCase):
    def test_a_full_gate_record_of_the_tip_lands(self):
        wt, tip = self.branch('feature', {'a.py': 'x = 1\n'})
        out = self.gate(wt, full=True)
        self.assertIs(gate_ok.read(self.repo.root, tip)[0]['full'], True, out.lines)
        out = self.lander(BRANCH, self.main, tip).run()
        self.assertEqual(out.code, G.OK, out.lines)
        rec, why = gate_ok.verify(self.repo.root, out.merge)
        self.assertIsNone(why)
        self.assertIs(rec['full'], False)           # the merged gate ran in its default mode

    def test_a_default_mode_record_of_the_tip_lands(self):
        wt, tip = self.branch('feature', {'a.py': 'x = 1\n'})
        self.gate(wt)
        self.assertEqual(gate_ok.mode_of(gate_ok.read(self.repo.root, tip)[0]), 'select')
        out = self.lander(BRANCH, self.main, tip).run()
        self.assertEqual(out.code, G.OK, out.lines)
        rec, why = gate_ok.verify(self.repo.root, out.merge)
        self.assertIsNone(why)
        self.assertEqual((rec['full'], gate_ok.mode_of(rec)), (False, 'select'))


def _step(name, **kw):
    return dict(fixtures.GATE['steps'][0], name=name, **kw)


SELECT_GATE = {
    'smoke_steps': ['boot'],
    'docs_only': {'globs': ['docs/*.md'], 'steps': ['scrub']},
    'steps': [_step('unit'), _step('boot', always=False, manual=True),
              _step('scrub', always=False, globs=['*.md'])]}


class SelectRecords(TipCase):
    """A record that is not --full lands a tip when the BASE's config takes it
    (Lander.select_refusal): its diff covers the landing diff, and either it is a default-mode
    pass, or the change is docs-only under the base's "gate".docs_only and its steps passed. The
    merged gate selects by the merge's diff, the smoke steps included; a docs-only merge runs
    only the docs_only steps and is recorded with mode "docs"."""
    GATE = SELECT_GATE

    def gated(self, files, **kw):
        wt, tip = self.branch('feature', files)
        self.gate(wt, **kw)
        rec, why = gate_ok.verify(self.repo.root, tip)
        self.assertIsNone(why)
        return wt, tip, rec

    def edit_record(self, tip, **changes):
        rec, _ = gate_ok.read(self.repo.root, tip)
        rec.update(changes)
        gate_ok.write(self.repo.root, rec)

    def refused(self, wt, tip, *says):
        out = self.lander(BRANCH, self.main, tip).run()
        self.assert_refused(out, BRANCH, tip, wt, *says)
        return out

    def test_a_default_mode_record_lands_and_the_merge_runs_its_selection(self):
        wt, tip, rec = self.gated({'a.py': 'x = 1\n'})
        self.assertEqual((gate_ok.mode_of(rec), [s['name'] for s in rec['steps']]),
                         ('select', ['unit', 'boot']))
        out = self.lander(BRANCH, self.main, tip).run()
        self.assertEqual(out.code, G.OK, out.lines)
        merged = gate_ok.read(self.repo.root, out.merge)[0]
        self.assertEqual((gate_ok.mode_of(merged), [s['name'] for s in merged['steps']]),
                         ('select', ['unit', 'boot']))     # the smoke step, not the *.md one

    def test_a_record_missing_a_smoke_step_lands_and_the_merge_runs_it(self):
        wt, tip, rec = self.gated({'a.py': 'x = 1\n'})
        self.edit_record(tip, steps=[s for s in rec['steps'] if s['name'] != 'boot'])
        out = self.lander(BRANCH, self.main, tip).run()
        self.assertEqual(out.code, G.OK, out.lines)
        merged = gate_ok.read(self.repo.root, out.merge)[0]
        self.assertIn('boot', [s['name'] for s in merged['steps']])

    def test_a_record_whose_diff_does_not_cover_the_landing_is_refused(self):
        wt, first = self.branch('feature', {'a.py': 'x = 1\n'})
        self.repo.write('b.py', 'y = 2\n', root=wt)
        tip = self.repo.commit('more', 'b.py', cwd=wt)
        self.gate(wt)
        self.edit_record(tip, base=first)           # its steps were picked for b.py alone
        out = self.refused(wt, tip, 'whose diff to the tip leaves out a.py')
        self.assertNotIn('without --full', out.lines[0])

    def test_a_docs_only_record_lands_and_the_merge_runs_only_the_docs_steps(self):
        wt, tip, rec = self.gated({'docs/guide.md': 'words\n'})
        self.assertEqual((gate_ok.mode_of(rec), [s['name'] for s in rec['steps']]),
                         ('docs', ['scrub']))
        said = []
        out = self.lander(BRANCH, self.main, tip, say=said.append).run()
        self.assertEqual(out.code, G.OK, out.lines)
        self.assertTrue(any('a docs-only merge' in line for line in said), said)
        merged = gate_ok.read(self.repo.root, out.merge)[0]
        self.assertEqual((gate_ok.mode_of(merged), [s['name'] for s in merged['steps']]),
                         ('docs', ['scrub']))

    def test_a_docs_only_record_needs_the_bases_docs_only(self):
        wt, tip, rec = self.gated({'docs/guide.md': 'words\n'})
        self.assertEqual(gate_ok.mode_of(rec), 'docs')
        gate = {k: v for k, v in SELECT_GATE.items() if k != 'docs_only'}
        self.repo.write('.kit.json', json.dumps(dict(self.KIT, gate=gate), indent=1))
        self.main = self.repo.commit('no docs-only', '.kit.json')
        self.repo.git('push', '-q', 'origin', 'main')
        self.refused(wt, tip, 'its gate ran only the docs-only steps, and the change is not '
                              'docs-only under the base')

    def test_a_docs_only_record_missing_the_docs_step_is_refused(self):
        wt, tip, _ = self.gated({'docs/guide.md': 'words\n'})
        self.edit_record(tip, steps=[])
        self.refused(wt, tip, "the base's docs_only step scrub did not pass in it")


class BranchGateRecord(TipCase):
    def test_full_says_whether_the_branch_gate_ran_full(self):
        wt, tip = self.branch('feature', {'a.py': 'x = 1\n'})
        self.gate(wt)
        self.assertIs(gate_ok.read(self.repo.root, tip)[0]['full'], False)
        self.gate(wt, full=True)
        self.assertIs(gate_ok.read(self.repo.root, tip)[0]['full'], True)
        out = runner.branch_gate(wt, only=['unit'], book_kw=self.book_kw,
                                 log_dir=os.path.join(self.tmp, 'only'))
        self.assertEqual(out.code, G.OK, out.lines)
        self.assertIsNone(out.facts['record'])      # --only writes no record at all
        self.assertIs(gate_ok.read(self.repo.root, tip)[0]['full'], True)


# ---------------------------------------------------------------- a gate config change

FAILS = ['{python}', '-c', 'import sys; print("ok"); sys.exit(1)']
CONFIG = 'GATE CONFIG: '


class ConfigCase(TipCase):
    """A base whose steps are `unit` (always) and `api` (api/*); the owner may commit .kit.json."""
    GATE = {'steps': [_step('unit'), _step('api', always=False, globs=['api/*'])]}
    OWNER = 'owner@example.invalid'
    KIT = dict(test_land.LandCase.KIT, owner={'email': OWNER})

    def config_branch(self):
        """A branch whose one change is to .kit.json (a gate config file however little of the
        gate it touches), committed by the owner and gated in default mode: (worktree, tip)."""
        wt = self.repo.worktree('feature')
        kit = dict(self.KIT, gate=self.GATE, landing={'self_review_max_lines': 99})
        self.repo.write('.kit.json', json.dumps(kit, indent=1), root=wt)
        self.repo.git('add', '.kit.json', cwd=wt)
        self.repo.git('-c', 'user.email=%s' % self.OWNER, 'commit', '-q', '-m', 'config', cwd=wt)
        self.gate(wt)
        return wt, self.repo.git('rev-parse', 'HEAD', cwd=wt)


class ConfigEscalation(ConfigCase):
    def test_a_merge_diff_git_cannot_list_escalates_to_full(self):
        # Lander.config_escalate lists base..merge: the first such call, failed here
        wt, tip = self.branch('feature', {'a.py': 'x = 1\n'})
        self.gate(wt)
        real, failed = runner.changed_between, []

        def changed_between(root, a, b, *rest, **kw):
            if a == self.main and b != tip and not failed:
                failed.append(b)
                return None, 'git broke'
            return real(root, a, b, *rest, **kw)
        said = []
        lander = self.lander(BRANCH, self.main, tip, say=said.append)
        with mock.patch.object(runner, 'changed_between', changed_between):
            out = lander.run()
        self.assertEqual(out.code, G.OK, out.lines)
        self.assertEqual(failed, [out.merge])
        self.assertTrue(lander.full and lander.escalated)
        merged = gate_ok.read(self.repo.root, out.merge)[0]
        self.assertEqual((gate_ok.mode_of(merged), [s['name'] for s in merged['steps']]),
                         ('full', ['unit', 'api']))
        self.assertIn(CONFIG + 'git cannot list what the merge changes (git broke): the merged '
                      'gate runs every step in full on the merged tree', said)

    def test_a_plain_merge_is_not_escalated(self):
        wt, tip = self.branch('feature', {'a.py': 'x = 1\n'})
        self.gate(wt)
        said = []
        lander = self.lander(BRANCH, self.main, tip, say=said.append)
        out = lander.run()
        self.assertEqual(out.code, G.OK, out.lines)
        self.assertFalse(lander.full or lander.escalated)
        self.assertEqual([l for l in said if l.startswith(CONFIG)], [])
        merged = gate_ok.read(self.repo.root, out.merge)[0]
        self.assertEqual((gate_ok.mode_of(merged), [s['name'] for s in merged['steps']]),
                         ('select', ['unit']))


class ConfigRed(ConfigCase):
    """The escalated merged gate runs each step's full_cmd: here it fails where the cmd the
    branch gate ran passes, so the landing of a config change is red and nothing moves; a plain
    change's merged gate runs cmd and lands."""
    GATE = {'steps': [dict(fixtures.GATE['steps'][0], full_cmd=FAILS)]}

    def test_the_escalated_merged_gate_runs_the_full_cmd(self):
        _, tip = self.config_branch()
        out = self.lander(BRANCH, self.main, tip).run()
        self.assertEqual(out.code, G.FAILED, out.lines)
        self.assertTrue(out.verdict.startswith('GATE FAILED: unit'), out.verdict)
        self.assertEqual(self.main_now(), self.main)
        self.assertEqual(self.remote_main(), self.main)

    def test_a_plain_merged_gate_runs_the_cmd(self):
        wt, tip = self.branch('feature', {'a.py': 'x = 1\n'})
        self.gate(wt)
        out = self.lander(BRANCH, self.main, tip).run()
        self.assertEqual(out.code, G.OK, out.lines)
