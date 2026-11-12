"""kitlib.staging.promote: one promotion run, in a scratch repository with a bare remote and a
staging branch (tests/dailyfull/test_promote.StagingCase), the merged gate run for real on the
scratch machine-lock store. Stood in: the promotion record (gate_ok.record_tested_pass is the gaps
branch's), the landing lock (a stand-in that logs its takes) and the main checkout's sync (the
landing's sync_checkout, not extracted yet)."""
import os
import stat
import time
import unittest
from unittest import mock

from kitlib import gate as G
from kitlib.config import load as load_config
from kitlib.dailyfull import promote as P
from kitlib.lock import run as lockrun
from kitlib.lock import store as lockstore
from kitlib.staging import advance as A
from kitlib.staging import promote as SP
from kitlib.staging import record as R
from kitlib.gate import runner
from tests.dailyfull.test_promote import StagingCase
from tests.realgit import real_git
from tests.staging.test_advance import FakeLock

HOUR = 3600
# The gate's logs and the attempts' folder inside the scratch repository (kitlib.records.place
# log_base), never the machine's kit temp folder; `.claude/` is ignored there.
LOGS = {'landing': {'log_dir': '.claude/gate-logs'}}


class Case(StagingCase):
    KIT = dict(StagingCase.KIT, **LOGS)

    def setUp(self):
        StagingCase.setUp(self)
        self.log = []               # gate, slot, landing-lock, push and sync events, in order
        self.records = []
        self.record_error = None
        self.gates = 0
        self.lock = FakeLock(self.log)

    # the stand-ins ------------------------------------------------------------------
    def record(self, main, sha, base, cfg_base, started, out, full):
        self.log.append(('record', sha))
        if self.record_error:
            raise SP.RecordNotWritten(self.record_error)
        self.records.append({'sha': sha, 'base': base, 'full': full,
                             'steps': [r['name'] for r in out.facts['results']],
                             'main_then': self.remote_main()})

    def sync(self, main, cfg, sha):
        self.log.append(('sync', sha))
        return []

    def mover(self, main, cfg, staging, git, say):
        return A.Mover(main, cfg, staging, git, say=say, lock=self.lock, sync=self.sync,
                       sleep=lambda s: None)

    def gate(self, *args, **kw):
        self.gates += 1
        self.log.append(('gate',))
        return runner.merged_gate(*args, **kw)

    def run_promote(self, force=True, **kw):
        kw.setdefault('gate', self.gate)
        return SP.run(self.main_dir, self.cfg(), force=force, book_kw=self.book_kw,
                      record=self.record, mover=self.mover, **kw)

    def attempts(self):
        where = SP.folder(self.cfg(), self.main_dir)
        self.assertTrue(where.startswith(self.main_dir), where)
        return R.attempts(where)

    def land_red(self):
        self.n += 1
        self.repo.write('RED', 'x\n', root=self.stg)
        sha = self.repo.commit('red landing', 'RED', cwd=self.stg)
        self.repo.git('push', '-q', 'origin', 'HEAD:refs/heads/staging', cwd=self.stg)
        return sha

    def no_gate(self, *a, **k):
        raise AssertionError('the gate ran')


class Promotes(Case):
    def test_the_gate_s_slot_is_booked_now_or_never(self):
        seen = []

        def gate(*a, **k):
            seen.append(k['book_kw'])
            return self.gate(*a, **k)
        self.land()
        out = self.run_promote(gate=gate)          # the scratch store's book_kw, with no no_wait
        self.assertEqual(out.code, G.OK, out.lines)
        self.assertTrue(seen[0].get('no_wait'), seen)
        self.assertNotIn('wait_s', seen[0])

    def test_green_moves_main_to_exactly_the_tested_tip_after_its_record(self):
        base = self.remote_main()
        tip = self.land()
        out = self.run_promote()
        self.assertEqual(out.code, G.OK, out.lines)
        self.assertEqual(self.remote_main(), tip)
        self.assertEqual(self.records, [{'sha': tip, 'base': base, 'full': False,
                                         'steps': ['unit'], 'main_then': base}])
        a = self.attempts()[-1]
        self.assertEqual((a.phase, a.sha, a.rec['base']), ('done', tip, base))
        self.assertEqual([p for p, _ in a.rec['history']], ['tested', 'pushing', 'pushed', 'done'])
        self.assertEqual([e[0] for e in self.log],
                         ['gate', 'record', 'take', 'phase', 'sync', 'release'])
        self.assertIn(('sync', tip), self.log)

    def test_main_moves_only_to_the_tested_sha_when_staging_moves_during_the_gate(self):
        tip = self.land()
        later = []

        def gate(*a, **k):
            out = self.gate(*a, **k)
            later.append(self.land())           # a landing while the gate ran
            return out
        out = self.run_promote(gate=gate)
        self.assertEqual(out.code, G.OK, out.lines)
        self.assertEqual(self.remote_main(), tip)
        self.assertNotEqual(self.remote_main(), later[0])

    def test_the_gate_s_slot_is_released_before_the_landing_lock_is_taken(self):
        book = lockrun.book
        log = self.log

        class Logged(object):
            def __init__(self, slot):
                self._slot = slot

            def __getattr__(self, name):
                return getattr(self._slot, name)

            def __enter__(self):
                self._slot.__enter__()
                return self

            def __exit__(self, *exc):
                r = self._slot.__exit__(*exc)
                log.append(('slot-exit',))
                return r

            def release(self, *a, **k):
                log.append(('slot-release',))
                return self._slot.release(*a, **k)

        def logged_book(*a, **k):
            log.append(('slot-book',))
            return Logged(book(*a, **k))
        self.land()
        with mock.patch.object(lockrun, 'book', side_effect=logged_book):
            out = self.run_promote()
        self.assertEqual(out.code, G.OK, out.lines)
        names = [e[0] for e in self.log]
        self.assertIn('slot-release', names)
        last_slot = max(i for i, n in enumerate(names) if n.startswith('slot-'))
        self.assertLess(last_slot, names.index('take'), names)


class Due(Case):
    def test_three_fresh_landings_are_not_due_and_four_are(self):
        for _ in range(3):
            self.land()
        out = self.run_promote(force=False, gate=self.no_gate)
        self.assertEqual(out.code, G.OK)
        self.assertTrue(out.lines[0].startswith('promote: NOT DUE (3 untested'), out.lines)
        self.assertEqual(self.attempts(), [])
        tip = self.land()
        out = self.run_promote(force=False)
        self.assertEqual(out.code, G.OK, out.lines)
        self.assertEqual(self.remote_main(), tip)

    def test_one_landing_six_hours_old_is_due(self):
        tip = self.land()
        out = self.run_promote(force=False, gate=self.no_gate,
                               clock=lambda: time.time() + 5 * HOUR)
        self.assertIn('NOT DUE', out.lines[0])
        out = self.run_promote(force=False, clock=lambda: time.time() + 6 * HOUR + 60)
        self.assertEqual(out.code, G.OK, out.lines)
        self.assertEqual(self.remote_main(), tip)

    def test_nothing_on_staging_is_nothing_to_promote_even_forced(self):
        out = self.run_promote(gate=self.no_gate)
        self.assertEqual(out.code, G.OK)
        self.assertIn('nothing to promote', out.lines[0])


class EndsAndHolds(Case):
    def test_a_red_tip_ends_red_and_is_never_tested_again(self):
        before = self.remote_main()
        tip = self.land_red()
        out = self.run_promote()
        self.assertEqual(out.code, G.FAILED, out.lines)
        self.assertEqual(self.remote_main(), before)
        self.assertEqual((self.attempts()[-1].phase, self.records), ('red', []))
        out = self.run_promote(gate=self.no_gate)
        self.assertEqual(out.code, G.REFUSED)
        self.assertIn('HELD - %s ended red' % tip[:10], out.lines[0])
        self.assertEqual(len(self.attempts()), 1)

    def test_a_new_landing_on_a_held_tip_is_tested(self):
        self.land_red()
        self.run_promote()
        self.repo.git('rm', '-q', 'RED', cwd=self.stg)
        self.repo.git('commit', '-q', '-m', 'green again', cwd=self.stg)
        self.repo.git('push', '-q', 'origin', 'HEAD:refs/heads/staging', cwd=self.stg)
        out = self.run_promote()
        self.assertEqual(out.code, G.OK, out.lines)
        self.assertEqual(self.attempts()[-1].phase, 'done')

    def test_a_record_not_written_ends_failed_and_main_stays(self):
        before = self.remote_main()
        self.land()
        self.record_error = 'the store is read-only'
        out = self.run_promote()
        self.assertEqual(out.code, G.REFUSED, out.lines)
        self.assertEqual(self.remote_main(), before)
        a = self.attempts()[-1]
        self.assertEqual(a.phase, 'failed')
        self.assertIn('the store is read-only', a.rec['why'])
        self.assertNotIn('take', [e[0] for e in self.log])

    def test_a_machine_lock_refusal_is_a_skip_that_records_nothing(self):
        self.land()

        def refused(*a, **k):
            return G.Outcome(lockstore.EXIT_BUSY, ['the machine lock is busy'])
        out = self.run_promote(gate=refused)
        self.assertEqual(out.code, G.OK, out.lines)
        self.assertIn('SKIPPED (busy)', out.lines[0])
        self.assertEqual((self.attempts(), self.records), ([], []))

    def test_main_with_commits_staging_lacks_ends_diverged_untested(self):
        self.land()
        self.push_to_main_directly()
        out = self.run_promote(gate=self.no_gate)
        self.assertEqual(out.code, G.REFUSED, out.lines)
        self.assertEqual(self.attempts()[-1].phase, 'diverged')


class MainMoves(Case):
    def test_main_moving_during_the_gate_to_an_ancestor_ends_behind_then_retests(self):
        first = self.land()
        tip = self.land()

        def gate(*a, **k):
            out = self.gate(*a, **k)
            self.repo.git('push', '-q', 'origin', '%s:refs/heads/main' % first, cwd=self.stg)
            return out
        out = self.run_promote(gate=gate)
        self.assertEqual(out.code, G.OK, out.lines)
        self.assertEqual(self.attempts()[-1].phase, 'behind')
        self.assertEqual(self.remote_main(), first)
        self.assertNotIn('take', [e[0] for e in self.log])
        out = self.run_promote()                # tested again, on the moved main
        self.assertEqual(out.code, G.OK, out.lines)
        self.assertEqual(self.remote_main(), tip)
        self.assertEqual([r['base'] for r in self.records], [self.records[0]['base'], first])
        self.assertEqual(self.gates, 2)


class Recovery(Case):
    def test_from_tested_with_main_still_the_base_it_is_moved_on_without_a_gate(self):
        base = self.remote_main()
        tip = self.land()
        R.start(SP.folder(self.cfg(), self.main_dir), tip, base, 'tested')
        out = self.run_promote(gate=self.no_gate)
        self.assertEqual(out.code, G.OK, out.lines)
        self.assertIn('left %s at tested' % tip[:10], out.lines[0])
        self.assertEqual(self.remote_main(), tip)
        self.assertEqual(self.attempts()[-1].phase, 'done')

    def test_from_tested_with_main_moved_it_ends_behind(self):
        first = self.land()
        tip = self.land()
        base = self.remote_main()
        R.start(SP.folder(self.cfg(), self.main_dir), tip, base, 'tested')
        self.repo.git('push', '-q', 'origin', '%s:refs/heads/main' % first, cwd=self.stg)
        out = self.run_promote(gate=self.no_gate)
        self.assertEqual(out.code, G.OK, out.lines)
        self.assertEqual(self.attempts()[-1].phase, 'behind')
        self.assertEqual(self.remote_main(), first)

    def test_from_pushing_the_push_is_made_through_promote(self):
        base = self.remote_main()
        tip = self.land()
        R.start(SP.folder(self.cfg(), self.main_dir), tip, base, 'pushing').set('pushing', tries=1)
        out = self.run_promote(gate=self.no_gate)
        self.assertEqual(out.code, G.OK, out.lines)
        self.assertEqual(self.remote_main(), tip)
        self.assertEqual(self.attempts()[-1].rec['tries'], 2)

    def test_from_pushing_when_main_already_holds_the_tip_it_is_current(self):
        base = self.remote_main()
        tip = self.land()
        self.repo.git('push', '-q', 'origin', '%s:refs/heads/main' % tip, cwd=self.stg)
        R.start(SP.folder(self.cfg(), self.main_dir), tip, base, 'pushing')
        with mock.patch.object(P, '_push', side_effect=AssertionError('pushed')):
            out = self.run_promote(gate=self.no_gate)
        self.assertEqual(out.code, G.OK, out.lines)
        self.assertEqual(self.attempts()[-1].phase, 'done')

    def test_from_pushed_only_the_sync_is_left(self):
        base = self.remote_main()
        tip = self.land()
        self.repo.git('push', '-q', 'origin', '%s:refs/heads/main' % tip, cwd=self.stg)
        R.start(SP.folder(self.cfg(), self.main_dir), tip, base, 'pushed')
        out = self.run_promote(gate=self.no_gate)
        self.assertEqual(out.code, G.OK, out.lines)
        self.assertEqual([e[0] for e in self.log], ['take', 'sync', 'release'])

    def test_a_recovery_ends_the_run(self):
        base = self.remote_main()
        tip = self.land()
        R.start(SP.folder(self.cfg(), self.main_dir), tip, base, 'tested')
        self.land()
        self.run_promote(gate=self.no_gate)
        self.assertEqual(self.gates, 0)


class PromoteLock(Case):
    def test_a_second_run_finds_the_lock_busy_and_reads_and_records_nothing(self):
        self.land()
        lock, why = SP.lock_for(self.main_dir)
        self.assertIsNone(why)
        lock.acquire()
        self.addCleanup(lock.release)

        def no_git(*a, **k):
            raise AssertionError('git ran: %s' % (a,))
        out = self.run_promote(gate=self.no_gate, git=no_git)
        self.assertEqual(out.code, G.BUSY, out.lines)
        self.assertIn('read and recorded nothing', out.lines[0])
        self.assertEqual(self.attempts(), [])
        self.assertEqual(SP.look(self.main_dir, self.cfg(), self.fail_announce)[0], 'busy')

    def fail_announce(self, a):
        raise AssertionError('announced %s' % a.phase)

    def test_the_lock_is_one_per_repository_whichever_checkout_asks(self):
        a, _ = SP.lock_for(self.main_dir)
        b, _ = SP.lock_for(self.stg)
        self.assertEqual(os.path.normcase(os.path.realpath(a.path)),
                         os.path.normcase(os.path.realpath(b.path)))

    def test_the_lock_is_let_go_after_a_run(self):
        self.run_promote(gate=self.no_gate)
        lock, _ = SP.lock_for(self.main_dir)
        lock.acquire()
        lock.release()


class Look(Case):
    def setUp(self):
        Case.setUp(self)
        self.announced = []

    def look(self, **kw):
        return SP.look(self.main_dir, self.cfg(), self.announced.append, **kw)

    def test_nothing_due_is_idle_and_due_is_start(self):
        self.land()
        self.repo.git('fetch', '-q', 'origin')
        self.assertEqual(self.look()[0], 'idle')
        self.assertEqual(self.look(clock=lambda: time.time() + 7 * HOUR)[0], 'start')

    def test_a_held_tip_is_idle_and_its_end_is_announced_once(self):
        self.land_red()
        self.run_promote()
        self.assertEqual(self.look(clock=lambda: time.time() + 7 * HOUR)[0], 'idle')
        self.assertEqual([a.phase for a in self.announced], ['red'])
        self.look()
        self.assertEqual(len(self.announced), 1)
        self.assertTrue(self.attempts()[-1].rec['announced'])

    def test_an_unfinished_attempt_is_start(self):
        tip = self.land()
        R.start(SP.folder(self.cfg(), self.main_dir), tip, self.remote_main(), 'pushing')
        self.repo.git('fetch', '-q', 'origin')
        verdict, note = self.look()
        self.assertEqual(verdict, 'start')
        self.assertIn('left at pushing', note)


class Off(StagingCase):
    KIT = dict({'daily_full': {'enabled': True, 'kind': 'engine-test'}}, **LOGS)

    def setUp(self):
        StagingCase.setUp(self)
        self.assertTrue(SP.folder(self.cfg(), self.main_dir).startswith(self.main_dir))

    def test_with_the_setting_absent_the_run_reports_off_and_records_nothing(self):
        out = SP.run(self.main_dir, self.cfg(), force=True,
                     gate=lambda *a, **k: self.fail('the gate ran'))
        self.assertEqual(out.code, G.USAGE)
        self.assertIn('promote: OFF', out.lines[0])
        self.assertEqual(R.attempts(SP.folder(self.cfg(), self.main_dir)), [])

    def test_with_the_setting_absent_the_look_is_off(self):
        self.assertEqual(SP.look(self.main_dir, self.cfg(), self.fail), ('off', None))

    def test_with_the_setting_absent_the_hub_job_does_nothing(self):
        from kitlib.hubjobs import staging_promote as hubjob
        from kitlib.hubsite import jobs
        ctx = mock.Mock(stop=mock.Mock(is_set=lambda: False))
        job = hubjob.PromoteJob(lambda: {'r': self.main_dir},
                                start=lambda root: self.fail('a run was started'))
        out = job(ctx)
        self.assertIsInstance(out, jobs.Skip)
        ctx.event.assert_not_called()

    def test_the_tool_reports_off(self):
        from tools import promote as tool
        with mock.patch('builtins.print') as said:
            code = tool.main(['-C', self.main_dir])
        self.assertEqual(code, G.USAGE)
        self.assertIn('promote: OFF', said.call_args_list[0][0][0])


@real_git('the pre-push hook: the fake git runs no hook')
class PrePushRefusal(Case):
    def test_a_pre_push_refusal_ends_refused_and_is_never_pushed_again(self):
        before = self.remote_main()
        tip = self.land()
        hooks = os.path.join(self.tmp, 'hooks')
        os.makedirs(hooks)
        hook = os.path.join(hooks, 'pre-push')
        with open(hook, 'w', newline='\n') as f:
            f.write('#!/bin/sh\necho "pre-push: refused, the test guard says no" >&2\nexit 1\n')
        os.chmod(hook, os.stat(hook).st_mode | stat.S_IEXEC)
        self.repo.git('config', 'core.hooksPath', hooks)
        real = P._push
        with mock.patch.object(P, '_push', side_effect=real) as push:
            out = self.run_promote()
            self.assertEqual(out.code, G.REFUSED, out.lines)
            self.assertEqual(push.call_count, 1)
            a = self.attempts()[-1]
            self.assertEqual(a.phase, 'refused')
            self.assertIn('the test guard says no', a.rec['why'])
            self.assertEqual(self.remote_main(), before)
            out = self.run_promote(gate=self.no_gate)
            self.assertEqual(push.call_count, 1)
        self.assertIn('HELD - %s ended refused' % tip[:10], out.lines[0])


if __name__ == '__main__':
    unittest.main()
