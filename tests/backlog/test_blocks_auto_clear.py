"""KIT-BLOCKS-AUTO-CLEAR: an `owner` block cites the hub ask that ends it (plan.plan_block,
record.owner_citation_error); `next` prints one REVIEW line per record to review
(offer.Banner.review_lines) and, under `--session`, a NOTE naming the pullable records that key
never offers (offer.Banner.session_note); a lead may end an `owner` hold on the owner's own
to-do, question or approval and on nothing else (policy.lead_until). docs/backlog.md "Commands", "next: a short offer's
causes", "Records"."""
import unittest

from kitlib.backlog import policy
from kitlib.backlog import record as rmod
from kitlib.backlog import store as smod
from kitlib.backlog.errors import Refusal, Usage

from tests.backlog.ownertree import setUpModule, tearDownModule  # noqa: F401 - the owner's tree
from tests.backlog import test_cli, test_policy
from tests.backlog.helpers import StoreCase, rec, record_text


class OwnerCitesAHubId(StoreCase):
    remote = False

    def refused(self, rid, says, **kw):
        tip = self.remote_tip()
        with self.assertRaises(Usage) as ctx:
            self.verb('block', rid, ['owner'], **kw)
        self.assertIn(says, str(ctx.exception))
        self.assertIn('questions add --record <REPO>:%s' % rid, str(ctx.exception))
        self.assertEqual(self.remote_tip(), tip)        # nothing written

    def test_a_self_cite_is_refused(self):
        # Retribution TEST-SUPPORT-ARTIFACT: only `item:<itself>`, so nothing ever cleared it
        self.seed(rec('FOO'))
        self.refused('FOO', 'cites the record itself', owner_ask='item:FOO until the owner '
                     'approves the grant lines')
        self.refused('FOO', 'cites the record itself', owner_ask='Q-123 first',
                     reason='item:FOO waits on Q-123')

    def test_an_item_or_ruling_alone_is_refused(self):
        self.seed(rec('FOO'), rec('BAR'))
        self.refused('FOO', 'cites no Action Hub question', owner_ask='item:BAR first')
        self.refused('FOO', 'cites no Action Hub question', owner_ask='which one?',
                     reason='R-1234 says the owner decides')
        self.refused('FOO', 'cites no Action Hub question', owner_ask='which colour?')

    def test_a_hub_id_passes_in_the_ask_the_reason_or_the_stored_ask(self):
        self.seed(rec('FOO'), rec('BAR'), rec('BAZ', state='blocked', blocked_on='owner',
                                              owner_ask='Q-77: which one?'))
        self.verb('block', 'FOO', ['owner'], owner_ask='item:BAR and T-101 first')
        self.verb('block', 'BAR', ['owner'], owner_ask='which one?',
                  reason='asked as A-120 on the hub')
        self.verb('block', 'BAZ', ['owner', 'deliberate'], reason='held for Q-77 too')
        self.assertEqual([self.get(r).all('blocked_on') for r in ('FOO', 'BAR', 'BAZ')],
                         [['owner'], ['owner'], ['owner', 'deliberate']])

    def test_other_members_are_not_held_to_it(self):
        self.seed(rec('FOO'), rec('BAR'))
        self.verb('block', 'FOO', ['item:BAR'])
        self.verb('block', 'FOO', ['deliberate'], reason='R-1234: until the rework')
        self.assertEqual(self.get('FOO').all('blocked_on'), ['item:BAR', 'deliberate'])

    def test_the_error_helper(self):
        self.assertIsNone(rmod.owner_citation_error('FOO', None, 'Q-123?'))
        self.assertIsNone(rmod.owner_citation_error('FOO', 'asked as T-5 on the hub', None))
        self.assertIsNotNone(rmod.owner_citation_error('FOO', 'see KIT-T-5 and Q-12x', None))
        self.assertIn('itself', rmod.owner_citation_error('FOO', 'item:FOO', 'Q-123?'))
        self.assertIn('(only R-1234, item:BAR)',
                      rmod.owner_citation_error('FOO', 'R-1234', 'item:BAR'))


class NextLines(unittest.TestCase):
    """Through tools/backlog.py `next`."""
    setUp = test_cli.Cli.setUp
    run_tool = test_cli.Cli.run_tool
    ok = test_cli.Cli.ok

    def publish(self, texts):
        backend = smod.RefBackend(self.repo.root, 'origin', smod.STORE_REF, smod.STORE_REF)
        backend.publish(smod.Store(backend, rmod.Rules()).load(), texts, 'seed\n')

    def test_each_record_to_review_gets_a_line_and_keeps_its_mark(self):
        # Retribution DRAIN-INVENTORY: `review: owner-answer` sat 57 h behind a bare count
        self.publish({
            'AAA': record_text(id='AAA', priority='2', priority_by='agent', scope='a/aaa.py',
                               review='owner-answer 2026-11-01',
                               answer=['Q-1600 2026-10-20T09:00:00-05:00',
                                       'Q-1673 2026-11-01T10:00:00-05:00']),
            'BBB': record_text(id='BBB', priority='-', scope='a/bbb.py',
                               review='hub 2026-11-02'),
            'CCC': record_text(id='CCC', priority='3', priority_by='agent', scope='a/ccc.py')})
        out = self.ok('next')
        self.assertIn('2 to review', out)
        self.assertIn("REVIEW AAA: owner answer Q-1673 filed 2026-11-01 -> read '## Owner "
                      "answer', then clear-review AAA --reason \"<what you read>\"", out)
        self.assertIn('REVIEW BBB: marked by hub 2026-11-02 -> read the record, then '
                      'clear-review BBB --reason "<what you read>"', out)
        self.assertEqual(out.count('REVIEW '), 2, out)
        self.assertIn('review:', self.ok('show', 'AAA'))         # `next` clears nothing

    def test_a_session_short_offer_names_what_other_keys_hold(self):
        # Retribution: `next --session <key>` offered 0 while plain `next` offered 8
        texts = {}
        for rid, session in (('AAA', None), ('BBB', None), ('CCC', 'retri'), ('DDD', 'kit')):
            over = {'session': session} if session else {}
            texts[rid] = record_text(id=rid, priority='2', priority_by='agent',
                                     scope='a/%s.py' % rid.lower(), **over)
        self.publish(texts)
        out = self.ok('--session', 'kit', 'next')
        self.assertIn('NEXT (1 offered, session kit)', out)
        self.assertIn('NOTE: 3 pullable carry other sessions (retri: 1) or none (2); this key '
                      'offers 1', out)
        self.assertNotIn('NOTE: 3 pullable', self.ok('next'))      # no key, no note
        out = self.ok('--session', 'kit', 'next', '--cap', '1')
        self.assertNotIn('pullable carry other sessions', out)   # a full offer is not short


# The owner's Go (Q-1821): a lead may end an `owner` hold on the owner's own act, and nothing else.
LEAD_FORMS = ('todo:T-101 done', 'question:Q-102 answered', 'approval:A-103 decided')
TOP_FORMS = ('when the owner says so', 'todo:T-104 started', 'path:%TEMP%/flag exists',
             'record:Kit:BAR closed')


class LeadUntilOnOwner(unittest.TestCase):
    """policy.lead_until, _until_rows, given_of, read_argv: the three owner-event forms are a
    lead's `block-owner`; every other `--until` on `owner` stays the `unblock-owner` row."""

    setUp = test_policy.CliRole.setUp
    run_tool = test_policy.CliRole.run_tool
    ok = test_policy.CliRole.ok
    refused = test_policy.CliRole.refused
    seed = test_policy.CliRole.seed

    def test_the_rows_from_the_text_the_guard_reads(self):
        for form in LEAD_FORMS:
            a = policy.read_argv(test_policy.words('block', 'FOO', '--on', 'owner', '--until', form))
            self.assertEqual(a.given['until'], form)
            self.assertEqual(policy.sort('block', a.given), ['block-owner'], form)
            a = policy.read_argv(test_policy.words('block', 'FOO', '--on', 'owner',
                                                   '--until=' + form))
            self.assertEqual(policy.sort('block', a.given), ['block-owner'], form)
        for form in TOP_FORMS:
            a = policy.read_argv(test_policy.words('block', 'FOO', '--on', 'owner', '--until', form))
            self.assertIn('unblock-owner', policy.sort('block', a.given), form)
        # a value the line does not show is never a lead's
        a = policy.read_argv([('block', True), ('FOO', True), ('--on', True), ('owner', True),
                              ('--until', True), ('$cond', policy.ONE)])
        self.assertIn('unblock-owner', policy.sort('block', a.given))
        self.assertIn('unblock-owner', policy.sort('block', {'on': ['owner'], 'until': True}))
        self.assertEqual(policy.sort('block', {'on': ['BAR'], 'until': 'free text'}), ['block'])

    def test_a_lead_may_set_the_three_forms_and_no_other(self):
        self.seed()
        self.ok('coordinator', 'block', 'FOO', '--on', 'owner', '--owner-ask', 'which one, Q-101?')
        self.ok('coordinator', 'block', 'FOO', '--on', 'owner', '--reason',
                'until T-101 is done', '--until', LEAD_FORMS[0])
        self.ok('coordinator', 'block', 'FOO', '--on', 'owner', '--owner-ask',
                'which one now, Q-102?', '--until', LEAD_FORMS[1])
        self.ok('coordinator', 'block', 'FOO', '--on', 'owner', '--reason',
                'until A-103 is decided', '--until', LEAD_FORMS[2])
        shown = self.ok(None, 'show', 'FOO')
        for form in LEAD_FORMS:
            self.assertIn('until: owner %s' % form, shown)
        for form in TOP_FORMS:
            out = self.refused('coordinator', 'block', 'FOO', '--on', 'owner', '--reason',
                               'until A-103 is decided', '--until', form)
            self.assertIn('`unblock-owner`', out)
        self.assertEqual(self.ok(None, 'show', 'FOO'), shown)       # nothing written

    def test_the_owner_and_the_pm_are_unchanged(self):
        self.seed()
        for mark in ('pm', None):
            self.ok(mark, 'block', 'FOO', '--on', 'owner', '--owner-ask', 'which one, Q-101?',
                    '--until', LEAD_FORMS[0])
            rc, out = self.run_tool(mark, 'block', 'FOO', '--on', 'owner', '--reason',
                                    'until A-103 is decided', '--until', 'record:Kit:BAR closed')
            self.assertEqual(rc, 2, out)
            self.assertIn('`owner` ends only on an owner event', out)   # USAGE, not the role
        self.assertIn('until: owner todo:T-101 done', self.ok(None, 'show', 'FOO'))


class LeadUntilWithoutTheRoleCheck(StoreCase):
    remote = False

    def test_a_library_caller_follows_the_same_split(self):
        self.seed(rec('FOO', state='blocked', blocked_on='owner', owner_ask='which one, Q-101?'))
        self.verb('block', 'FOO', ['owner'], until='todo:T-101 done')
        self.assertEqual(self.get('FOO').all('until'), ['owner todo:T-101 done'])
        tip = self.remote_tip()
        with self.assertRaises(Refusal) as ctx:
            self.verb('block', 'FOO', ['owner'], until='todo:T-101 started')
        self.assertIn('unblock-owner', str(ctx.exception))
        self.assertEqual(self.remote_tip(), tip)


if __name__ == '__main__':
    unittest.main()
