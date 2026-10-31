"""The topic session (content/roles/topic.md, docs/backlog.md "Roles"): a launched plain session
marked `topic`, a builder whose writes name only the records batched for its branch
(`batch: <branch>`, set by a lead), and which files no records (`add` is refused). Its pull of a record batched for its own branch takes the
whole batch first (plan.batch_members as current_offer's seeds), so rank order is no refusal and
no `preempt` is written; every other refusal stands. A record batched for one branch is held from
every other branch's pull and `next`."""
import tempfile
import unittest

from kitlib.backlog import plan as pmod
from kitlib.backlog import policy
from kitlib.backlog import record as rmod
from kitlib.backlog import roles as R
from kitlib.backlog.errors import Refusal
from tests import inproc
from tests.backlog.helpers import ADD_FLAGS, BRANCH, TODAY, StoreCase, kit_json_ref, priced
from tests.gitfixture import TempRepo, fixture_env, rmtree

HOLDER = 'kit/holder'
OTHER = 'kit/other-topic'


class TopicMark(unittest.TestCase):
    def test_the_topic_mark_is_the_topic_role_and_never_a_subagents(self):
        """(a) The session mark `topic` is the role topic; a subagent named topic is unlisted."""
        self.assertEqual(policy.cli_role({'PM_KIT_ROLE': 'topic'}), policy.TOPIC)
        self.assertIn('topic', R.BUILDERS)
        self.assertIsNone(R.role_of('topic', True))


class TopicPull(StoreCase):
    remote = False

    def setUp(self):
        super().setUp()
        self.repo.git('branch', HOLDER)

    def test_a_batch_member_below_a_colliding_higher_record_pulls_with_no_preempt(self):
        """(b) The rank-3 member's scope collides with a higher-ranked open record, so `next`
        passes it over; the topic pull takes it plainly."""
        self.seed(priced('TOP', priority='1', scope='web/app.css'),
                  priced('MID', priority='2', scope='web/other.css'),
                  priced('MINE', priority='3', scope='web/app.css', batch=BRANCH))
        change = self.verb('pull', 'MINE', batch=True)
        r = self.get('MINE')
        self.assertEqual((r.get('state'), r.get('pulled_by')), ('pulled', BRANCH))
        for key in rmod.PREEMPT_KEYS:
            self.assertFalse(r.has(key), key)
        self.assertNotIn('pre-empting', change.subject)

    def test_a_worker_pull_of_the_same_member_is_still_out_of_order(self):
        """Pin: only the topic pull skips the rank order; anyone else's is not in the offer."""
        self.seed(priced('TOP', priority='1', scope='web/app.css'),
                  priced('MINE', priority='3', scope='web/app.css', batch=BRANCH))
        with self.assertRaises(Refusal) as cm:
            self.verb('pull', 'MINE')
        self.assertIn("is not in `next`'s offer", str(cm.exception))
        self.assertEqual(self.get('MINE').get('state'), 'open')

    def test_a_batch_of_seven_pulls_its_last_member_over_a_cap_of_five(self):
        """(c) The cap grows to hold the whole batch."""
        self.seed(*[priced('M%d' % n, scope='area/m%d/**' % n, batch=BRANCH) for n in range(1, 8)])
        self.assertEqual(self.ctx.cap, 5)
        with self.assertRaises(Refusal):
            self.verb('pull', 'M7')
        self.verb('pull', 'M7', batch=True)
        r = self.get('M7')
        self.assertEqual(r.get('state'), 'pulled')
        self.assertFalse(r.has('preempt'))

    def test_a_topic_pull_still_collides_with_another_branchs_live_pull(self):
        """Pin: the batch skips the order, never a live pull on another branch."""
        self.seed(priced('HOLD', state='pulled', pulled_at=TODAY, pulled_by=HOLDER,
                         scope='web/**'),
                  priced('MINE', scope='web/app.css', batch=BRANCH))
        with self.assertRaises(Refusal) as cm:
            self.verb('pull', 'MINE', batch=True)
        self.assertIn('`pull-collision`', str(cm.exception))
        self.assertEqual(self.get('MINE').get('state'), 'open')

    def test_a_record_batched_for_another_branch_is_held_from_every_pull(self):
        """(f) A lead's pull (any role's: the plan refuses it) onto another branch is refused,
        with or without a reason."""
        self.seed(priced('HELD', batch=OTHER))
        for kw in ({}, {'reason': 'the owner wants it on this branch', 'may_collide': True},
                   {'batch': True}):
            with self.subTest(**kw):
                with self.assertRaises(Refusal) as cm:
                    self.verb('pull', 'HELD', **kw)
                self.assertIn('held for the batch on %s' % OTHER, str(cm.exception))
                self.assertIn('--unset batch', str(cm.exception))
        self.assertEqual(self.get('HELD').get('state'), 'open')

    def test_next_elsewhere_passes_a_held_record_over(self):
        """(g) `next` on another branch, and in the main checkout, passes it over by name; on its
        own branch it is offered."""
        self.seed(priced('HELD', priority='1', batch=OTHER), priced('FREE', priority='2'))
        snap = self.store.load()
        for branch in (BRANCH, 'main'):
            with self.subTest(branch=branch):
                offers, passed, _holding, _own = pmod.offer_for(snap, self.ctx, branch=branch)
                self.assertEqual([o.rec.id for o in offers], ['FREE'])
                self.assertEqual([(p.rec.id, p.why) for p in passed],
                                 [('HELD', 'held for the batch on %s' % OTHER)])
        self.repo.git('branch', OTHER)
        offers, _passed, _holding, _own = pmod.offer_for(snap, self.ctx, branch=OTHER)
        self.assertEqual([o.rec.id for o in offers], ['HELD', 'FREE'])


class TopicCli(unittest.TestCase):
    """The topic mark through tools/backlog.py: its writes name only its branch's batch."""
    TOPIC_BRANCH = 'kit/topic-branch'

    def setUp(self):
        self.repo = TempRepo(bare_remote=True, prepare=kit_json_ref,
                             prepare_key='test_cli.Cli.kit_json_ref')
        self.addCleanup(self.repo.cleanup)
        home = tempfile.mkdtemp(prefix='kit-home-')
        self.addCleanup(rmtree, home)
        self.env = fixture_env({'USERPROFILE': home, 'HOME': home})
        self.env.pop('PM_KIT_ROLE', None)
        for rid in ('MINE', 'LOOSE', 'ELSE'):
            self.ok(None, 'add', rid, '--summary', 'a thing to do', '--scope',
                    'tools/%s.py' % rid.lower(), *ADD_FLAGS)
            self.ok(None, 'set', rid, '--priority', '3', '--priority-by', 'agent')

    def run_tool(self, mark, *args):
        env = dict(self.env)
        if mark is not None:
            env['PM_KIT_ROLE'] = mark
        return inproc.run_main(inproc.load_tool('backlog').main, ['-C', self.repo.root] + list(args),
                               env, self.repo.root)

    def ok(self, mark, *args):
        rc, out, err = self.run_tool(mark, *args)
        self.assertEqual(rc, 0, out + err)
        return out + err

    def refused(self, mark, *args):
        rc, out, err = self.run_tool(mark, *args)
        self.assertEqual(rc, 2, out + err)
        self.assertIn('REFUSED', out + err)
        return out + err

    def topic(self, *args):
        return ('--as', self.TOPIC_BRANCH) + args

    def test_a_coordinator_sets_the_batch_and_a_worker_may_not(self):
        """(h) `set --batch` is set-other: a lead's, audited; a builder's is refused."""
        out = self.refused('worker', 'set', 'MINE', '--batch', self.TOPIC_BRANCH)
        self.assertIn('`set-other`', out)
        self.ok('coordinator', 'set', 'MINE', '--batch', self.TOPIC_BRANCH)
        self.assertIn('batch: %s\n' % self.TOPIC_BRANCH, self.ok(None, 'show', 'MINE'))

    def test_a_topic_pull_of_its_batch_passes_and_any_other_record_is_refused_naming_batch(self):
        """(d) Not batched, or batched for another branch: refused, naming `batch`."""
        self.ok('coordinator', 'set', 'MINE', '--batch', self.TOPIC_BRANCH)
        self.ok('coordinator', 'set', 'ELSE', '--batch', OTHER)
        for rid, has in (('LOOSE', 'no `batch:`'), ('ELSE', '`batch: %s`' % OTHER)):
            with self.subTest(rid=rid):
                out = self.refused('topic', *self.topic('pull', rid))
                self.assertIn('a topic session writes only the records batched for its branch', out)
                self.assertIn(has, out)
        self.ok('topic', *self.topic('pull', 'MINE'))
        self.assertIn('pulled_by: %s\n' % self.TOPIC_BRANCH, self.ok(None, 'show', 'MINE'))

    def test_a_topic_pull_through_the_tool_takes_a_batch_member_the_plain_offer_passes_over(self):
        """(a) Through tools/backlog.py (opener.planner passes batch=True for the topic mark): a
        member below a higher record whose scope collides with it is not in the plain offer, yet the
        topic pull takes it with no `--reason` and writes no `preempt`; a worker's pull is refused."""
        self.ok(None, 'add', 'HIGH', '--summary', 'a higher thing', '--scope', 'tools/mine.py',
                *ADD_FLAGS)
        self.ok(None, 'set', 'HIGH', '--priority', '1', '--priority-by', 'agent')
        self.ok('coordinator', 'set', 'MINE', '--batch', self.TOPIC_BRANCH)
        out = self.refused('worker', *self.topic('pull', 'MINE'))
        self.assertIn("`MINE` is not in `next`'s offer", out)
        self.assertIn('already taken by HIGH', out)
        self.ok('topic', *self.topic('pull', 'MINE'))
        shown = self.ok(None, 'show', 'MINE')
        self.assertIn('pulled_by: %s\n' % self.TOPIC_BRANCH, shown)
        self.assertNotIn('preempt', shown)

    def test_a_topic_session_sets_scope_on_its_own_pull_and_a_widen_into_a_live_pull_is_refused(self):
        """(b) `set --scope` on a record it holds pulled is a builder's own-pull text edit; a
        widen into another branch's live pull stays a lead's (`set-widen-collision`)."""
        self.ok('coordinator', 'set', 'MINE', '--batch', self.TOPIC_BRANCH)
        self.ok('topic', *self.topic('pull', 'MINE'))
        self.ok('topic', *self.topic('set', 'MINE', '--scope', 'tools/mine2.py'))
        self.assertIn('tools/mine2.py', self.ok(None, 'show', 'MINE'))
        self.repo.git('branch', OTHER)  # a live pull is one whose branch exists
        self.ok('coordinator', '--as', OTHER, 'pull', 'ELSE')
        out = self.refused('topic', *self.topic('set', 'MINE', '--scope', 'tools/else.py'))
        self.assertIn('widens its scope into a live pull', out)
        self.assertIn('`set-widen-collision`', out)
        self.assertNotIn('tools/else.py', self.ok(None, 'show', 'MINE'))

    def test_a_topic_session_never_prices_closes_or_adds(self):
        """(e) Its role's rows stay a builder's: no price, no close, even on its own batch. It
        files no records: its `add` is refused, pointing at the owner."""
        self.ok('coordinator', 'set', 'MINE', '--batch', self.TOPIC_BRANCH)
        self.ok('topic', *self.topic('pull', 'MINE'))
        out = self.refused('topic', *self.topic('set', 'MINE', '--priority', '1', '--priority-by',
                                                'agent'))
        self.assertIn("this session's role is topic", out)
        out = self.refused('topic', *self.topic('close', 'MINE', '--reason',
                                                'built on the topic branch'))
        self.assertIn("this session's role is topic", out)
        out = self.refused('topic', 'add', 'NEWONE', '--summary', 'found while building',
                           '--scope', 'tools/newone.py', *ADD_FLAGS)
        self.assertIn('a topic session files no records: the owner files the records', out)
        self.assertIn('NOT FOUND', self.run_tool(None, 'show', 'NEWONE')[1] +
                      self.run_tool(None, 'show', 'NEWONE')[2])
        self.ok('worker', 'add', 'NEWONE', '--summary', 'found while building', '--scope',
                'tools/newone.py', *ADD_FLAGS)


if __name__ == '__main__':
    unittest.main()
