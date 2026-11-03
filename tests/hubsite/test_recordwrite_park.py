"""kitlib.hubsite.recordwrite park and withdraw: the hub as the owner's proxy blocks a record on
the owner until the ask bound to it ends (a question answered, a to-do done, an approval
decided; Session: hub-park), by the record's state,
and drops that line when the ask is withdrawn; several asks are several `owner` lines, read as
AND."""
import unittest

from kitlib.backlog import store as smod
from kitlib.hubsite import recordwrite
from tests.backlog.helpers import rec, record_text
from tests.hubsite.test_record_write import Case

AT, LATER = ('2026-10-02T09:00:%s+00:00' % s for s in ('10', '20'))


def cond(qid):
    return 'question:%s answered' % qid


class Park(Case):

    def setUp(self):
        Case.setUp(self)
        self.named()

    def park(self, qid='Q-101', rid='FOO'):
        return recordwrite.park(self.repo.root, rid, qid, cond(qid), 'kit')

    def tip(self):
        return self.repo.git('rev-parse', smod.STORE_REF).strip()

    def test_an_open_record_is_blocked_on_the_owner_under_its_own_session(self):
        self.seed(**rec('FOO'))
        out = self.park()
        self.assertEqual(out['park'], 'blocked')
        self.assertEqual((self.field('FOO', 'state'), self.field('FOO', 'blocked_on')),
                         (['blocked'], ['owner']))
        self.assertEqual(self.field('FOO', 'until'), ['owner question:Q-101 answered'])
        self.assertEqual(self.field('FOO', 'owner_ask'), ['Q-101 on the Action Hub (asked by kit)'])
        self.assertIn('asked as Q-101 on the Action Hub by kit', self.read('FOO')['body'])
        log = self.repo.git('log', '-1', '--format=%B', smod.STORE_REF)
        self.assertIn('Session: hub-park', log)
        tip = self.tip()
        self.assertEqual(self.park()['park'], 'already')      # a retry writes nothing
        self.assertEqual(self.tip(), tip)

    def test_a_pulled_record_has_its_pull_stopped_and_back_when_the_ask_is_withdrawn(self):
        wt = self.repo.worktree('work')         # kit/work, one commit past main
        self.repo.write('work.txt', 'work\n', root=wt)
        self.repo.commit('work', 'work.txt', cwd=wt)
        self.seed(FOO=record_text(state='pulled', pulled_by='kit/work', pulled_at='2026-10-01',
                                  priority='2', priority_by='agent'))
        self.assertEqual(self.park()['park'], 'blocked')
        self.assertEqual(self.field('FOO', 'state'), ['blocked'])
        self.assertEqual(self.field('FOO', 'pulled_by'), [])
        out = recordwrite.withdraw(self.repo.root, 'FOO', cond('Q-101'))
        self.assertEqual((out['dropped'], out['unblocked']), (True, True))
        self.assertEqual((self.field('FOO', 'state'), self.field('FOO', 'pulled_by')),
                         (['pulled'], ['kit/work']))
        self.assertIn('ask withdrawn', self.repo.git('log', '-1', '--format=%B', smod.STORE_REF))

    def test_a_blocked_record_gains_the_owner_beside_its_blockers(self):
        self.seed(**rec('FOO', state='blocked', blocked_on='live-client'))
        self.assertEqual(self.park()['park'], 'blocked')
        self.assertEqual(self.field('FOO', 'blocked_on'), ['live-client', 'owner'])
        self.assertEqual(self.field('FOO', 'until'), ['owner question:Q-101 answered'])

    def test_a_park_a_manual_owner_hold_and_a_gone_record_are_left_as_they_are(self):
        self.seed(**dict(rec('FOO', state='parked', q='Q-9', parked_at='2026-10-01'),
                         **rec('BAR', state='blocked', blocked_on='owner', owner_ask='which?')))
        tip = self.tip()
        self.assertEqual(self.park()['park'], 'parked')
        self.assertEqual(self.park(rid='BAR')['park'], 'held')
        self.assertEqual(self.park(rid='NOPE')['park'], 'gone')
        self.assertEqual(self.tip(), tip)
        self.assertEqual(self.field('BAR', 'until'), [])

    def test_several_asks_hold_until_the_last_is_withdrawn(self):
        self.seed(**rec('FOO'))
        self.park('Q-101')
        self.park('Q-102')
        self.assertEqual(self.field('FOO', 'until'),
                         ['owner question:Q-101 answered', 'owner question:Q-102 answered'])
        out = recordwrite.withdraw(self.repo.root, 'FOO', cond('Q-101'))
        self.assertEqual((out['dropped'], out['unblocked']), (True, False))
        self.assertEqual((self.field('FOO', 'state'), self.field('FOO', 'until')),
                         (['blocked'], ['owner question:Q-102 answered']))
        out = recordwrite.withdraw(self.repo.root, 'FOO', cond('Q-102'))
        self.assertEqual((out['dropped'], out['unblocked']), (True, True))
        self.assertEqual((self.field('FOO', 'state'), self.field('FOO', 'until')), (['open'], []))
        tip = self.tip()
        again = recordwrite.withdraw(self.repo.root, 'FOO', cond('Q-102'))
        self.assertEqual((again['dropped'], self.tip()), (False, tip))

    def test_answers_end_the_hold_on_the_last_ask_only(self):
        self.seed(**rec('FOO'))
        self.park('Q-101')
        self.park('Q-102')
        out = recordwrite.answer(self.repo.root, 'FOO', 'Q-101', AT, 'Which one?', 'first')
        self.assertTrue(any('still waits on' in line for line in out['lines']), out['lines'])
        self.assertEqual((self.field('FOO', 'state'), self.field('FOO', 'until')),
                         (['blocked'], ['owner question:Q-102 answered']))
        recordwrite.answer(self.repo.root, 'FOO', 'Q-102', LATER, 'And this?', 'second')
        self.assertEqual((self.field('FOO', 'state'), self.field('FOO', 'until')), (['open'], []))
        self.assertEqual(self.field('FOO', 'owner_ask'), [])

    def test_an_ask_whose_park_has_not_run_does_not_keep_a_hold_with_no_line(self):
        # Q-102 is bound to FOO and open, but its park failed or has not run: the answer to
        # Q-101 ends the hold on its last line, and Q-102's own park blocks FOO again on its line
        self.seed(**rec('FOO'))
        self.park('Q-101')
        recordwrite.answer(self.repo.root, 'FOO', 'Q-101', AT, 'Which one?', 'first',
                           waiting=['Q-102'])
        self.assertEqual((self.field('FOO', 'state'), self.field('FOO', 'until')), (['open'], []))
        self.assertEqual(self.park('Q-102')['park'], 'blocked')
        self.assertEqual((self.field('FOO', 'state'), self.field('FOO', 'until')),
                         (['blocked'], ['owner question:Q-102 answered']))

    def test_a_manual_owner_hold_with_no_line_still_waits_on_the_open_questions(self):
        self.seed(**rec('FOO', state='blocked', blocked_on='owner', owner_ask='Q-101 and Q-102'))
        recordwrite.answer(self.repo.root, 'FOO', 'Q-101', AT, 'Which one?', 'first',
                           waiting=['Q-102'])
        self.assertEqual(self.field('FOO', 'state'), ['blocked'])

    def test_asks_lists_the_owner_ask_lines_and_which_the_hub_wrote(self):
        self.seed(**dict(rec('FOO'), **dict(
            rec('BAR', state='blocked', blocked_on='owner',
                until='owner question:Q-7 answered', owner_ask='by hand'),
            **dict(rec('BAZ', state='blocked', blocked_on='live-client',
                       until='live-client question:Q-8 answered'), **rec('QUX')))))
        self.park('Q-101')
        recordwrite.park(self.repo.root, 'QUX', 'T-5', 'todo:T-5 done', 'kit')
        recordwrite.park(self.repo.root, 'QUX', 'A-6', 'approval:A-6 decided', 'pm')
        got = sorted(recordwrite.asks(self.repo.root), key=lambda a: (a['id'], a['q']))
        self.assertEqual(got, [{'id': 'BAR', 'q': 'Q-7', 'kind': 'question', 'parked': False},
                               {'id': 'FOO', 'q': 'Q-101', 'kind': 'question', 'parked': True},
                               {'id': 'QUX', 'q': 'A-6', 'kind': 'approval', 'parked': True},
                               {'id': 'QUX', 'q': 'T-5', 'kind': 'todo', 'parked': True}])

    def test_a_to_do_and_an_approval_park_as_a_question_does(self):
        self.seed(**rec('FOO'))
        out = recordwrite.park(self.repo.root, 'FOO', 'T-5', 'todo:T-5 done', 'kit')
        self.assertEqual(out['park'], 'blocked')
        out = recordwrite.park(self.repo.root, 'FOO', 'A-6', 'approval:A-6 decided', 'pm')
        self.assertEqual(out['park'], 'blocked')
        self.assertEqual(self.field('FOO', 'until'),
                         ['owner todo:T-5 done', 'owner approval:A-6 decided'])
        body = self.read('FOO')['body']
        self.assertIn('asked as T-5 on the Action Hub by kit', body)
        self.assertIn('asked as A-6 on the Action Hub by pm', body)
        out = recordwrite.withdraw(self.repo.root, 'FOO', 'approval:A-6 decided')
        self.assertEqual((out['dropped'], out['unblocked']), (True, False))
        self.assertEqual(self.field('FOO', 'until'), ['owner todo:T-5 done'])


if __name__ == '__main__':
    unittest.main()
