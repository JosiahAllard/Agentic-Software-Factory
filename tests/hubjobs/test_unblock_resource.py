"""The Action Hub's unblock-conditions job on `resource:<name> free`: a hold stays while every unit
of the repo's declared resource is taken by a live pull, clears when one frees, and stays (with the
reason said) when the capacity cannot be read."""
from unittest import mock

from kitlib.backlog import offer, opener
from kitlib.hubjobs import unblock_conditions as U
from tests.backlog.helpers import kit_json_ref, rec
from tests.hubjobs.test_unblock_conditions import Case, Ctx

UNTIL = 'live-client resource:dev-clone free'
HOLDER = 'kit/holder'


def waiting():
    return rec('WAIT', state='blocked', blocked_on='live-client', until=UNTIL)


def holder(state='pulled', rid='HOLD', by=HOLDER, uses='dev-clone'):
    over = {} if uses is None else dict(uses=uses)
    if state == 'pulled':
        over.update(state='pulled', pulled_at='2026-11-04', pulled_by=by)
    return rec(rid, **over)


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

    def test_two_conditions_on_one_repo_read_its_resources_once(self):
        kit_json_ref(self.repo, {'backlog': {'resources': {'dev-clone': 1, 'gpu': 1}}})
        self.seed(waiting(),
                  rec('GPU', state='blocked', blocked_on='live-client',
                      until='live-client resource:gpu free'),
                  holder())
        with mock.patch('kitlib.backlog.opener.repo_resources',
                        wraps=opener.repo_resources) as caps, \
                mock.patch('kitlib.backlog.offer.in_flight_split',
                           wraps=offer.in_flight_split) as split:
            self.job()(Ctx())
        # (kitlib.config.load also runs for the store open and the write, so it is not counted)
        self.assertEqual((caps.call_count, split.call_count), (1, 1))
        self.assertEqual(self.get('WAIT').get('state'), 'blocked')      # dev-clone is taken
        self.assertEqual(self.get('GPU').get('state'), 'open')          # gpu is free

    def test_an_unreadable_config_is_read_once_per_pass_and_says_the_same_why(self):
        readers = U.Readers(self.hub, self.plugins, lambda repo, rid: None)
        readers.at(self.repo.root, None)
        with mock.patch('kitlib.backlog.opener.repo_resources',
                        side_effect=ValueError('x')) as caps:
            self.assertIsNone(readers.resource_free('dev-clone'))
            first = readers.why
            self.assertIsNone(readers.resource_free('gpu'))
            self.assertEqual(readers.why, first)
        self.assertIn('.kit.json cannot be read', first)
        self.assertEqual(caps.call_count, 1)

    def test_each_at_reads_afresh(self):
        readers = U.Readers(self.hub, self.plugins, lambda repo, rid: None)
        readers.at(self.repo.root, None)
        with mock.patch('kitlib.backlog.offer.in_flight_split',
                        return_value=([], [], None)) as split:
            readers.resource_free('dev-clone')
            readers.resource_free('dev-clone')
            self.assertEqual(split.call_count, 1)
            readers.at(self.repo.root, None)
            readers.resource_free('dev-clone')
            self.assertEqual(split.call_count, 2)

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

    def test_pulls_on_other_or_no_resource_take_no_unit(self):
        self.repo.git('branch', 'kit/other')
        self.repo.git('branch', 'kit/none')
        self.seed(waiting(), holder('pulled', 'OTH', 'kit/other', 'gpu'),
                  holder('pulled', 'NON', 'kit/none', None))
        self.job()(Ctx())
        self.assertEqual(self.get('WAIT').get('state'), 'open')

    def test_capacity_two_is_free_with_one_holder_and_taken_with_two(self):
        kit_json_ref(self.repo, {'backlog': {'resources': {'dev-clone': 2}}})
        self.repo.git('branch', 'kit/second')
        self.seed(waiting(), holder())
        self.job()(Ctx())
        self.assertEqual(self.get('WAIT').get('state'), 'open')
        self.seed(waiting(), holder(), holder('pulled', 'HOL2', 'kit/second'))
        self.job()(Ctx())
        self.assertEqual(self.get('WAIT').get('state'), 'blocked')

    def test_the_job_says_why_an_unreadable_capacity_holds_the_record(self):
        self.seed(waiting())
        with mock.patch('kitlib.backlog.opener.repo_resources', side_effect=ValueError('x')):
            note = self.job()(Ctx())
        self.assertEqual(self.get('WAIT').get('state'), 'blocked')
        self.assertIn('.kit.json cannot be read', note.note)

    def test_the_job_says_why_git_that_cannot_tell_holds_the_record(self):
        self.seed(waiting(), holder())
        with mock.patch('kitlib.backlog.offer.live_branches', return_value=(None, 'boom')):
            note = self.job()(Ctx())
        self.assertEqual(self.get('WAIT').get('state'), 'blocked')
        self.assertIn('git cannot tell which pulls are live (boom)', note.note)

    def test_the_reasons_survive_the_note_cut(self):
        self.seed(waiting())
        long_note = 'x' * (U.NOTE_MAX + 50)
        job = U.UnblockJob(lambda: ([('kit', self.repo.root)], [long_note]),
                           lambda: U.Readers(self.hub, self.plugins, lambda repo, rid: None),
                           write=self.write)
        with mock.patch('kitlib.backlog.opener.repo_resources', side_effect=ValueError('x')):
            note = job(Ctx())
        self.assertIn('.kit.json cannot be read', note.note)

