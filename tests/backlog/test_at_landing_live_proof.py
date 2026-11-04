"""A live-proof record (a `requires: live-run` line) closes at landing only when a commit of the
branch cites a passing live run: a `Live-pass: <ID> <evidence>` line. Without one the landing
does not close it: a record the branch holds pulled is released (as a part landed is), one another
branch holds is left pulled, and one NOTE line says why and how to close it once a live run passes
(kitlib.backlog.at_landing.close_landed). `backlog.py landable` says the same ahead of the landing
(kitlib.backlog.landable). A record with no such line closes as before. docs/backlog.md
"Records"; docs/backlog-store.md "Lifecycle changes". Scratch repositories with a ref store,
landed by kitlib.gate.land.Lander."""
import unittest

from kitlib.backlog import at_landing
from kitlib.backlog import landable as L
from kitlib.backlog import record as rmod
from tests.backlog import test_at_landing_part as part   # not its classes: their tests run there
from tests.gate.test_land_store import BRANCH, StoreLandCase, pulled

TRAILERS = part.TRAILERS
LIVE = dict(requires='live-run')


class LiveProofLanding(StoreLandCase):
    branch_with = part.PartLanding.branch_with
    land = part.PartLanding.land
    warnings = part.PartLanding.warnings
    notes = part.PartLanding.notes
    started = part.PartLanding.started
    get = part.PartLanding.get
    assert_open = part.PartLanding.assert_open

    def ids(self, out, word):
        return [l.split()[1] for l in self.started(out, word)]

    def assert_held_back(self, notes, rid, how):
        """`notes` holds one NOTE, the live-proof one for `rid`, saying `how` it was left."""
        self.assertEqual(len(notes), 1, notes)
        self.assertIn('%s is a live-proof record (requires: live-run)' % rid, notes[0])
        self.assertIn('no `Live-pass: %s <run>` line' % rid, notes[0])
        self.assertIn(how, notes[0])
        self.assertIn('once a live run passes, close it with: ', notes[0])
        self.assertIn('close %s --reason' % rid, notes[0])

    def test_a_closes_line_alone_releases_a_live_proof_record(self):
        self.seed(pulled('FOO', **LIVE))
        out = self.land(self.branch_with('fix\n\nCloses: FOO\n%s\n' % TRAILERS))
        self.assertEqual(self.stems(), ['FOO'])
        self.assert_open('FOO')
        self.assertEqual(self.ids(out, 'closed'), [], out.lines)
        self.assertEqual(self.ids(out, 'released'), ['FOO'], out.lines)
        self.assertTrue(self.subjects()[0].startswith('releases FOO: landed %s' % out.merge),
                        self.subjects()[0])
        self.assert_held_back(self.notes(out), 'FOO', 'so it was not closed: its pull was released')
        self.assertEqual(self.warnings(out), [])

    def test_a_cited_passing_live_run_closes_it(self):
        self.seed(pulled('FOO', **LIVE), pulled('BAR', **LIVE))
        out = self.land(self.branch_with(
            'fix\n\nCloses: FOO, BAR\nLive-pass: FOO run 412 passed\n%s\n' % TRAILERS))
        self.assertEqual(self.stems(), ['BAR'])
        self.assertEqual(self.ids(out, 'closed'), ['FOO'], out.lines)
        self.assertEqual(self.ids(out, 'released'), ['BAR'], out.lines)
        self.assert_open('BAR')
        self.assert_held_back(self.notes(out), 'BAR', 'its pull was released')
        self.assertEqual(self.warnings(out), [])

    def test_a_record_not_marked_live_proof_closes_as_before(self):
        # No Closes line: the branch's pulls close, as they always have, but the live-proof one.
        self.seed(pulled('FOO', requires='item:NOPE'), pulled('BAR', **LIVE))
        out = self.land(self.branch_with('all of it\n\n%s\n' % TRAILERS))
        self.assertEqual(self.stems(), ['BAR'])
        self.assertIn('closes FOO: landed %s' % out.merge, self.subjects())
        self.assertEqual(self.ids(out, 'closed'), ['FOO'], out.lines)
        self.assertEqual(self.ids(out, 'released'), ['BAR'], out.lines)
        self.assert_held_back(self.notes(out), 'BAR', 'its pull was released')

    def test_a_live_proof_record_another_branch_holds_is_left_pulled(self):
        self.seed(pulled('FOO', by='kit/other', **LIVE))
        out = self.land(self.branch_with('fix\n\nCloses: FOO\n%s\n' % TRAILERS))
        self.assertEqual(self.stems(), ['FOO'])
        rec = self.get('FOO')
        self.assertEqual((rec.get('state'), rec.get('pulled_by')), ('pulled', 'kit/other'))
        self.assertEqual(self.ids(out, 'closed') + self.ids(out, 'released'), [], out.lines)
        self.assert_held_back(self.notes(out), 'FOO', 'it was left pulled by kit/other')
        self.assertEqual(self.warnings(out), [])

    def test_a_live_pass_line_with_no_evidence_cites_nothing(self):
        self.assertEqual(at_landing.live_passes(['FOO', 'BAR run 9', '#1 run 2', 'BAR again', '']),
                         ['BAR'])
        self.seed(pulled('FOO', **LIVE))
        out = self.land(self.branch_with('fix\n\nCloses: FOO\nLive-pass: FOO\n%s\n' % TRAILERS))
        self.assert_open('FOO')
        self.assertEqual(self.ids(out, 'closed'), [], out.lines)
        self.assert_held_back(self.notes(out), 'FOO', 'its pull was released')

    def test_landable_says_the_landing_releases_an_uncited_live_proof_record(self):
        self.seed(pulled('FOO', **LIVE), pulled('BAR', **LIVE), pulled('BAZ'))
        self.branch_with('fix\n\nCloses: FOO, BAR, BAZ\nLive-pass: FOO run 412\n%s\n' % TRAILERS)
        backend = self.backend()
        got = L.check(backend, backend.load(rmod.Rules()), self.repo.root, [BRANCH],
                      base=self.main)
        self.assertEqual(len(got), 1, got)
        v = got[0]
        self.assertEqual((v.verdict, v.closes, v.releases), (L.LANDABLE, ('BAZ', 'FOO'), ('BAR',)))
        self.assertEqual(L.lines(v, 'python backlog.py')[1:], [
            '  closes BAZ, which it holds pulled (a slice branch needs a `Part-of: BAZ` line)',
            '  closes FOO, which it holds pulled (a slice branch needs a `Part-of: FOO` line)',
            '  releases BAR, which it holds pulled (a live-proof record: no `Live-pass: BAR <run>` '
            'line in its commits cites a passing live run)'])
        self.assertEqual(L.exit_code([v]), 0)


if __name__ == '__main__':
    unittest.main()
