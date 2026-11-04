"""The Action Hub's unblock-conditions job on `resource:<name> free`: a hold stays while every unit
of the repo's declared resource is taken by a live pull, clears when one frees, and stays (with the
reason said) when the capacity cannot be read."""
from kitlib.hubjobs import unblock_conditions as U
from tests.backlog.helpers import kit_json_ref, rec
from tests.hubjobs.test_unblock_conditions import Case, Ctx

UNTIL = 'live-client resource:dev-clone free'
HOLDER = 'kit/holder'


def waiting():
    return rec('WAIT', state='blocked', blocked_on='live-client', until=UNTIL)


def holder(state='pulled'):
    over = dict(uses='dev-clone')
    if state == 'pulled':
        over.update(state='pulled', pulled_at='2026-11-04', pulled_by=HOLDER)
    return rec('HOLD', **over)


class ResourceFree(Case):

    def setUp(self):
        Case.setUp(self)
        kit_json_ref(self.repo, {'backlog': {'resources': {'dev-clone': 1}}})
        self.repo.git('branch', HOLDER)

    def test_it_stays_while_taken_and_clears_when_the_pull_ends(self):
        self.seed(waiting(), holder())
        ctx = Ctx()
        self.job()(ctx)
        self.assertEqual(self.get('WAIT').get('state'), 'blocked')
        self.assertEqual(ctx.events, [])
        self.seed(holder('open'))       # the holder gave its pull back
        self.job()(ctx)
        self.assertEqual(self.get('WAIT').get('state'), 'open')
        self.assertEqual(ctx.events, [(U.EVENT,'kit:WAIT')])

    def test_a_dead_pull_takes_no_unit(self):
        self.seed(waiting(), holder())
        self.repo.git('branch', '-D', HOLDER)
        self.job()(Ctx())
        self.assertEqual(self.get('WAIT').get('state'), 'open')

    def test_an_unreadable_capacity_is_not_met_and_says_why(self):
        # a .kit.json the store cannot be opened with fails the pass already ("the store could not
        # be read"); the reader's own cases are a store it was not pointed at and a config that
        # breaks after the store opened
        readers = U.Readers(self.hub, self.plugins, lambda repo, rid: None)
        self.assertIsNone(readers.resource_free('dev-clone'))
        self.assertIn('no store', readers.why)
        readers.at(self.repo.root, None)
        self.repo.write('.kit.json', '{ not json')
        self.assertIsNone(readers.resource_free('dev-clone'))
        self.assertIn('.kit.json cannot be read', readers.why)

    def test_a_name_the_repo_does_not_declare_is_not_met(self):
        self.seed(rec('ODD', state='blocked', blocked_on='live-client',
                      until='live-client resource:gpu free'))
        note = self.job()(Ctx())
        self.assertEqual(self.get('ODD').get('state'), 'blocked')
        self.assertIn('gpu', note.note)

