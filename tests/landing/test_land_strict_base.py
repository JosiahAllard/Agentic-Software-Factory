"""kitlib.landing.checks and strict paths (docs/gate-landing-policy.md "Strict paths"), split from test_land_strict so
each module's real-git run stays well under the test pool's unit timeout: a verdict carried over
main merges, the strict list read from the base, and the dry check (`land.py --check-strict`).
Fixtures: test_land_strict.StrictCase."""
import os
import subprocess
import sys
import unittest

from kitlib import gate as G
from kitlib.landing import lander as land
from kitlib.landing import verdicts
from kitlib.records import verdict_entry
from tests import gitfixture
from tests.landing import test_land, test_land_strict

StrictCase = test_land_strict.StrictCase
BRANCH = test_land_strict.BRANCH
STRICT = test_land_strict.STRICT
LIST_FILE = test_land_strict.LIST_FILE
STRICT_GATE = test_land_strict.LandsAloneFresh.GATE     # `unit` always, `api` for src/*


class CarriedVerdict(StrictCase):
    """A verdict carried over main merges (tools/verdict.py carry; docs/gate-landing-policy.md "Carrying a
    verdict forward") lets the merge land, and only when kitlib.landing.carry passes it again here
    against the base, never on the facts stored."""
    GATE = STRICT_GATE

    def synced(self):
        """kit/feature reviewed and queued at a strict change, then main moved and was merged in:
        (wt, reviewed, the merge)."""
        wt, reviewed = self.branch('feature', {STRICT: 'x = 1\n'})
        self.queue(BRANCH, reviewed)
        self.on_main({'m.py': 'm\n'})
        self.repo.git('merge', '-q', '--no-edit', 'main', cwd=wt)
        return wt, reviewed, self.repo.head(wt)

    def test_the_merge_of_a_reviewed_branch_needs_a_carry(self):
        _, reviewed, tip = self.synced()
        _, out, _ = self.land_it(BRANCH, tip)
        self.assert_refused(out, 'the verdict was given at %s, and the branch is at %s: review and '
                            'queue the tip, or carry the verdict (tools/verdict.py carry)'
                            % (reviewed[:10], tip[:10]))

    def test_a_carried_verdict_lands_with_a_fresh_merged_gate(self):
        _, reviewed, tip = self.synced()
        verdicts.carry(self.common(), self.repo.root, BRANCH, self.main)
        lander, out, said = self.land_it(BRANCH, tip)
        self.assertEqual(out.code, G.OK, out.lines)
        self.assertEqual(self.remote_main(), out.merge)
        self.assertTrue(lander.fresh and not lander.full)
        self.assertEqual(lander.strict_carry['from'], reviewed)
        self.assertIn('STRICT: %s changes strict path %s: reviewed by pm-kit:reviewer on opus '
                      '(agent-r1) at %s, carried over 1 main merge; the merged gate runs fresh '
                      '(nothing reused)' % (BRANCH, STRICT, reviewed[:10]), said)

    def test_the_carry_is_checked_against_the_base_landed_on(self):
        _, reviewed, tip = self.synced()
        carried = self.main
        verdicts.carry(self.common(), self.repo.root, BRANCH, carried)
        self.on_main({'n.py': 'n\n'})          # main moves on after the carry: it still holds
        lander, out, _ = self.land_it(BRANCH, tip)
        self.assertEqual(out.code, G.OK, out.lines)
        self.assertEqual((lander.strict_carry['from'], lander.strict_carry['main']),
                         (reviewed, self.main))
        self.assertNotEqual(self.main, carried)

    def test_a_stored_carry_that_does_not_hold_is_refused(self):
        wt, reviewed, _ = self.synced()
        self.repo.write('b.py', 'b\n', root=wt)
        tip = self.repo.commit('own work after the sync', 'b.py', cwd=wt)
        # facts no check would give: a carry record written past kitlib.landing.carry
        fake = {'from': reviewed, 'merges': [tip], 'main': self.main, 'diff': '0' * 64}
        verdicts.carry(self.common(), self.repo.root, BRANCH, self.main,
                       checker=lambda *a: (dict(fake), None))
        _, out, _ = self.land_it(BRANCH, tip)
        self.assert_refused(out, 'the verdict was given at %s and carried to %s, but the carry '
                            'does not hold against main %s' % (reviewed[:10], tip[:10],
                                                               self.main[:10]),
                            'not a merge', 'review and queue the tip')


class ReadFromTheBase(StrictCase):
    # The fixture identity is the owner: the list file is owner-only (kitlib.ownerfiles.rules),
    # and these branches change it only to show the strict check reads the base's copy.
    KIT = dict(StrictCase.KIT, owner={'email': gitfixture.EMAIL})
    LIST = "STRICT_DEFAULTS = (\n    'special/**',\n)\n"

    def test_the_bases_list_is_read(self):
        self.on_main({LIST_FILE: self.LIST})
        _, tip = self.branch('feature', {'special/a.py': 'a\n'})
        _, out, _ = self.land_it(BRANCH, tip)
        self.assert_refused(out, 'changes strict path special/a.py', 'none is queued')

    def test_the_branch_cannot_drop_a_path_from_the_list(self):
        self.on_main({LIST_FILE: self.LIST})
        _, tip = self.branch('feature', {LIST_FILE: 'STRICT_DEFAULTS = ()\n',
                                         'special/a.py': 'a\n'})
        _, out, _ = self.land_it(BRANCH, tip)
        # the base's list has only special/**: the branch's emptied copy is never read
        self.assert_refused(out, '%s changes strict path special/a.py:' % BRANCH, 'none is queued')

    def test_a_plain_branch_lands_beside_the_bases_list(self):
        self.on_main({LIST_FILE: self.LIST})
        _, tip = self.branch('feature', {'a.py': 'a\n'})
        self.queue(BRANCH, tip)
        _, out, _ = self.land_it(BRANCH, tip)
        self.assertEqual(out.code, G.OK, out.lines)

    def test_a_list_that_cannot_be_read_refuses_every_landing(self):
        self.on_main({LIST_FILE: 'STRICT_DEFAULTS = tuple(make())\n'})
        _, tip = self.branch('feature', {'a.py': 'a\n'})
        _, out, _ = self.land_it(BRANCH, tip)
        self.assert_refused(out, 'cannot read the strict paths from main at %s' % self.main[:10],
                            'STRICT_DEFAULTS')


class DryCheck(StrictCase):
    """land.check_strict (`land.py BRANCH --check-strict`): before a reviewer is picked, it says
    whether a branch changes a strict path, by the matcher the landing refuses by, against
    <remote>/<main> as last fetched; it takes no lock, needs no verdict and writes nothing."""
    KIT = dict(StrictCase.KIT, owner={'email': gitfixture.EMAIL})     # the list file: owner-only
    LIST = "STRICT_DEFAULTS = (\n    'special/**',\n)\n"

    def snapshot(self):
        """Every file under the repository (its .git and the linked worktrees included) and the
        bare remote: path -> (size, mtime)."""
        seen = {}
        for top in (self.repo.root, self.repo.remote):
            for d, _, names in os.walk(top):
                for n in names:
                    st = os.stat(os.path.join(d, n))
                    seen[os.path.join(d, n)] = (st.st_size, st.st_mtime_ns)
        return seen

    def test_a_strict_branch_and_a_plain_one_are_told_apart(self):
        _, tip = self.branch('feature', {STRICT: 'x = 1\n', 'a.py': 'a\n'})
        _, plain = self.branch('plain', {'b.py': 'b\n'})
        out = land.check_strict(self.repo.root, [BRANCH, 'kit/plain'])
        self.assertEqual(out.code, G.OK, out.lines)
        self.assertEqual(len(out.lines), 2, out.lines)
        self.assertTrue(out.lines[0].startswith('STRICT %s: %s (tip %s, base ' % (
            BRANCH, STRICT, tip[:10])), out.lines)
        self.assertIn('strong-tier', out.lines[0])
        self.assertTrue(out.lines[1].startswith('NOT STRICT kit/plain: no strict path changed '
                                                '(tip %s, base ' % plain[:10]), out.lines)
        # the landing refuses the strict branch on the very path the dry check named
        _, refused, _ = self.land_it(BRANCH, tip)
        self.assert_refused(refused, '%s changes strict path %s:' % (BRANCH, STRICT))

    def test_the_bases_own_list_decides(self):
        self.on_main({LIST_FILE: self.LIST})
        self.branch('special', {'special/a.py': 'a\n'})
        self.branch('feature', {STRICT: 'x = 1\n'})
        out = land.check_strict(self.repo.root, ['kit/special', BRANCH])
        self.assertEqual(out.code, G.OK, out.lines)
        self.assertTrue(out.lines[0].startswith('STRICT kit/special: special/a.py ('), out.lines)
        # the base's list has only special/**: kitlib/gate/** is not strict in this repository
        self.assertTrue(out.lines[1].startswith('NOT STRICT %s:' % BRANCH), out.lines)

    def test_it_writes_nothing_takes_no_lock_and_needs_no_verdict(self):
        _, tip = self.branch('feature', {STRICT: 'x = 1\n'})
        before = self.snapshot()
        out = land.check_strict(self.repo.root, [BRANCH])
        self.assertEqual(out.code, G.OK, out.lines)
        self.assertTrue(out.lines[0].startswith('STRICT %s:' % BRANCH), out.lines)
        self.assertEqual(self.snapshot(), before)
        self.assertIsNone(self.lock().read())
        self.assertFalse(os.path.exists(verdict_entry.entry_path(self.common(), BRANCH)))
        self.assertEqual(self.book_calls, [])
        self.assertEqual(self.remote_main(), self.main)

    def test_a_branch_or_a_list_that_cannot_be_read_is_refused(self):
        out = land.check_strict(self.repo.root, ['kit/nosuch'])
        self.assertEqual((out.code, out.lines),
                         (G.REFUSED, ['REFUSED: no branch kit/nosuch in this repository']))
        self.on_main({LIST_FILE: 'STRICT_DEFAULTS = tuple(make())\n'})
        self.branch('feature', {'a.py': 'a\n'})
        out = land.check_strict(self.repo.root, [BRANCH])
        self.assertEqual(out.code, G.REFUSED, out.lines)
        self.assertIn('cannot read the strict paths from main at %s' % self.main[:10],
                      out.lines[0])

    def test_the_entry_prints_it_and_takes_only_branches_and_dash_c(self):
        self.branch('feature', {STRICT: 'x = 1\n'})
        tool = [sys.executable, os.path.join(test_land.KIT_ROOT, 'tools', 'land.py'),
                '-C', self.repo.root]

        def run(*args):
            return subprocess.run(tool + list(args), capture_output=True, text=True,
                                  timeout=120, stdin=subprocess.DEVNULL, env=self.env)
        r = run(BRANCH, '--check-strict')
        self.assertEqual(r.returncode, G.OK, (r.stdout, r.stderr))
        self.assertTrue(r.stdout.startswith('STRICT %s: %s (' % (BRANCH, STRICT)), r.stdout)
        for extra in (['--expect-tip', 'b' * 40], ['--fresh'], ['--queue'], ['--submit']):
            r = run(BRANCH, '--check-strict', *extra)
            self.assertEqual(r.returncode, G.USAGE, (extra, r.stdout, r.stderr))
        for args in (['--check-strict'], ['--sweep-landed', '--check-strict']):
            r = run(*args)
            self.assertEqual(r.returncode, G.USAGE, (args, r.stdout, r.stderr))


if __name__ == '__main__':
    unittest.main()
