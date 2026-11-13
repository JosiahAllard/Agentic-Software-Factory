"""kitlib.staging.promote: one promotion run, in a scratch repository with a bare remote and a
staging branch (tests/dailyfull/test_promote.StagingCase), the merged gate run for real on the
scratch machine-lock store. Stood in: the promotion record (write_record's own call is WriteRecord's),
the landing lock (a stand-in that logs its takes) and the main checkout's sync (the
landing's sync_checkout: tests/landing/test_sync_checkout.py)."""
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
from kitlib.staging import culprit as CU
from kitlib.staging import promote as SP
from kitlib.staging import revert as V
from kitlib.staging import record as R
from kitlib.gate import runner
from kitlib.records import gate_ok, place
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
        kw.setdefault('mover', self.mover)
        return SP.run(self.main_dir, self.cfg(), force=force, book_kw=self.book_kw,
                      record=self.record, **kw)

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

    def test_a_failed_tip_is_held_and_never_gated_again_until_a_new_tip(self):
        tip = self.land()
        self.record_error = 'the store is read-only'
        self.run_promote()
        self.assertEqual(self.gates, 1)
        self.record_error = None
        out = self.run_promote(gate=self.no_gate)
        self.assertEqual(out.code, G.REFUSED, out.lines)
        self.assertIn('HELD - %s ended failed' % tip[:10], out.lines[0])
        self.assertEqual(len(self.attempts()), 1)
        self.assertEqual(SP.look(self.main_dir, self.cfg(), lambda a: None,
                                 clock=lambda: time.time() + 7 * HOUR)[0], 'idle')
        self.land()                             # a new staging tip is a fresh attempt
        out = self.run_promote()
        self.assertEqual(out.code, G.OK, out.lines)
        self.assertEqual((self.gates, self.attempts()[-1].phase), (2, 'done'))

    def test_a_push_that_keeps_failing_is_held_and_not_pushed_again(self):
        tip = self.land()

        def mover(main, cfg, staging, git, say):
            return A.Mover(main, cfg, staging, git, say=say, lock=self.lock, sync=self.sync,
                           sleep=lambda s: None,
                           promote=lambda *a, **k: {'status': 'not-promoted', 'why': 'offline',
                                                    'main': 'main', 'from': None, 'to': tip})
        out = self.run_promote(mover=mover)
        self.assertEqual(out.code, G.REFUSED, out.lines)
        self.assertEqual(self.attempts()[-1].phase, 'failed')
        out = self.run_promote(gate=self.no_gate, mover=mover)
        self.assertIn('HELD - %s ended failed' % tip[:10], out.lines[0])
        self.assertEqual((self.gates, len(self.attempts())), (1, 1))

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
        with mock.patch.object(P, 'run_push', side_effect=AssertionError('pushed')):
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


class WriteRecord(Case):
    def test_the_promotion_record_is_asked_of_record_tested_pass_with_its_base(self):
        base, tip = self.remote_main(), self.land()
        cfg = self.cfg()
        out = G.Outcome(G.OK, [], source='config', results=[
            {'name': 'unit', 'status': 'PASS', 'seconds': 1.5},
            {'name': 'lint', 'status': 'REUSED', 'seconds': 0.0}])
        seen = []

        def stub(main, sha, kind, gate_config, kit, repo, started, steps, **kw):
            seen.append(dict(main=main, sha=sha, kind=kind, gate_config=gate_config, kit=kit,
                             repo=repo, started=started, steps=steps, **kw))
        with mock.patch.object(gate_ok, 'record_tested_pass', new=stub):
            for full in (False, True):
                SP.write_record(self.main_dir, tip, base, cfg, '2026-11-12T04:00:00Z', out, full)
        self.assertEqual([s['full'] for s in seen], [False, True])
        s = seen[0]
        self.assertEqual((s['main'], s['sha'], s['kind']), (self.main_dir, tip, 'promotion'))
        self.assertEqual((s['base'], s['config_rev']), (base, base))
        self.assertEqual(s['gate_config'], gate_ok.gate_config(cfg))
        self.assertEqual(s['kit'], runner.kit_identity())
        self.assertEqual(s['repo'], place.repo_name(cfg, self.main_dir))
        self.assertEqual((s['started'], s['source']), ('2026-11-12T04:00:00Z', 'config'))
        self.assertEqual(s['steps'], [{'name': 'unit', 'result': 'pass', 'seconds': 1.5},
                                      {'name': 'lint', 'result': 'reused', 'seconds': 0.0}])

    def test_a_raising_record_tested_pass_is_record_not_written(self):
        out = G.Outcome(G.OK, [], results=[])

        def boom(*a, **k):
            raise OSError('read-only')
        with mock.patch.object(gate_ok, 'record_tested_pass', new=boom):
            with self.assertRaisesRegex(SP.RecordNotWritten, '^OSError: read-only$'):
                SP.write_record(self.main_dir, 'a' * 40, 'b' * 40, self.cfg(), 'x', out, False)


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


class Hunt(Case):
    """The culprit hunt after a red, run for real: its probes in the promotion's tree on the
    scratch store, its revert as a local branch of the main checkout (kitlib.staging.culprit)."""

    def branch(self, merge):
        return self.repo.git('rev-parse', '--verify', '-q', 'refs/heads/' + V.branch_of(merge),
                             check=False)

    def test_one_red_landing_is_confirmed_by_its_revert_and_the_branch_prepared(self):
        tip = self.land_red()
        out = self.run_promote()
        self.assertEqual(out.code, G.FAILED, out.lines)
        said = '\n'.join(out.lines)
        self.assertIn('promote: hunt: culprit %s (confirmed by revert)' % tip[:10], said)
        self.assertIn('promote: REVERT PREPARED - local branch revert/%s' % tip[:10], said)
        self.assertIn('nothing was pushed', said)
        a = self.attempts()[-1]
        found = a.rec['culprit']
        self.assertEqual((found['end'], found['merge'], found['by']),
                         (CU.FOUND, tip, CU.BY_REVERT))
        self.assertEqual([p['what'] for p in found['probes']], ['S', 'revert of %s' % tip[:10]])
        self.assertEqual(found['red'], ['unit'])
        revert = self.branch(tip)
        self.assertEqual(a.rec['revert']['branch'], V.branch_of(tip))
        self.assertEqual((a.rec['revert']['commit'], a.rec['revert']['base']), (revert, tip))
        self.assertEqual(self.repo.git('rev-parse', revert + '^1'), tip)
        self.assertNotIn('RED', self.repo.git('ls-tree', '--name-only', revert).split())
        self.assertEqual(self.repo.git('log', '-1', '--format=%an|%cn', revert),
                         '%s|%s' % (V.AUTHOR[0], V.AUTHOR[0]))
        self.assertEqual(a.event(), ('revert', tip[:10]))
        self.assertIn('; revert branch revert/%s' % tip[:10], R.line(a))
        self.assertEqual(self.repo.git('ls-remote', 'origin', 'refs/heads/' + V.branch_of(tip)),
                         '')                        # nothing pushed

    def test_the_red_landing_among_green_ones_is_found_by_bisect(self):
        self.land()
        culprit = self.land_red()
        tip = self.land()
        out = self.run_promote()
        found = self.attempts()[-1].rec['culprit']
        self.assertEqual((found['end'], found['merge'], found['by']),
                         (CU.FOUND, culprit, CU.BY_BISECT), out.lines)
        self.assertEqual(len(found['suspects']), 3)         # 'unit' has no globs: every landing
        revert = self.branch(culprit)
        self.assertEqual(self.repo.git('rev-parse', revert + '^1'), tip)
        self.assertNotIn('RED', self.repo.git('ls-tree', '--name-only', revert).split())
        self.assertIn('landed-3.txt', self.repo.git('ls-tree', '--name-only', revert).split())

    def test_main_red_already_is_on_main_after_the_revert_probed_red_and_no_branch(self):
        self.land_red()
        self.repo.git('push', '-q', 'origin', 'HEAD:refs/heads/main', cwd=self.stg)
        base = self.remote_main()
        tip = self.land()
        out = self.run_promote()
        found = self.attempts()[-1].rec['culprit']
        self.assertEqual(found['end'], CU.ON_MAIN, out.lines)
        self.assertEqual([p['what'] for p in found['probes']],
                         ['S', 'revert of %s' % tip[:10], 'bisect'])     # the probe at M ran
        self.assertEqual([p['sha'] for p in found['probes']][2], base)
        self.assertNotIn('revert', self.attempts()[-1].rec)
        self.assertEqual(self.branch(tip), '')

    def test_a_staging_tip_moved_since_the_test_gets_the_revert_on_its_new_tip(self):
        tip = self.land_red()
        moved = []

        def gate(*a, **k):
            out = self.gate(*a, **k)
            moved.append(self.land())           # a landing while the promotion ran
            return out
        self.run_promote(gate=gate)
        a, later = self.attempts()[-1], moved[0]
        self.assertEqual((a.rec['culprit']['merge'], a.rec['culprit']['by']), (tip, CU.BY_REVERT))
        revert = self.branch(tip)
        self.assertEqual((a.rec['revert']['base'], a.rec['revert']['commit']), (later, revert))
        self.assertEqual(self.repo.git('rev-parse', revert + '^1'), later)
        names = self.repo.git('ls-tree', '--name-only', revert).split()
        self.assertNotIn('RED', names)
        self.assertIn('landed-2.txt', names)

    def test_a_hunt_that_raises_leaves_no_slot_held_and_the_look_stops_it(self):
        self.land_red()
        real, calls = runner.run_steps, []

        def run_steps(*a, **k):
            calls.append(1)
            if len(calls) > 1:              # the gate's own run is the first: a probe raises
                raise RuntimeError('boom')
            return real(*a, **k)
        with mock.patch.object(runner, 'run_steps', side_effect=run_steps):
            with self.assertRaises(RuntimeError):
                self.run_promote()
        self.assertTrue(R.hunting(self.attempts()[-1]))
        self.assertEqual(self.store.live_runs(), [])
        SP.look(self.main_dir, self.cfg(), lambda a: None)
        self.assertEqual(self.attempts()[-1].rec['culprit']['verdict'], CU.STOPPED)

    def test_the_probes_wait_for_their_slot_within_the_budget_and_the_gate_never_waits(self):
        self.land_red()
        self.run_promote()
        hunts = [c for c in self.book_calls if c['slug'] == CU.SLUG]
        self.assertTrue(hunts, self.book_calls)
        others = [c for c in self.book_calls if c['slug'] != CU.SLUG]
        self.assertTrue(others[0].get('no_wait'), others)
        for c in hunts:
            self.assertNotIn('no_wait', c)
            self.assertTrue(0 < c['wait_s'] <= CU.HUNT_BUDGET_S, c)
            self.assertEqual(c.get('gate_mode'), 'bisect')

    def test_a_hunt_left_marked_hunting_is_marked_stopped_by_the_look(self):
        tip = self.land()
        a = R.start(SP.folder(self.cfg(), self.main_dir), tip, self.remote_main(), 'red',
                    why='the gate failed')
        a.rec['culprit'] = {'verdict': R.HUNTING, 'pid': 1, 'red': ['unit']}
        a.write()
        announced = []
        SP.look(self.main_dir, self.cfg(), announced.append)
        self.assertEqual([x.rec['culprit']['verdict'] for x in announced], [CU.STOPPED])
        self.assertEqual(self.attempts()[-1].rec['culprit']['verdict'], CU.STOPPED)
        self.assertEqual(self.attempts()[-1].event(), ('red', tip[:10]))


# A step whose cmd selects by the change, as `kit_tests.py --select` does: it runs (and sees RED)
# only where a landed-*.txt shows a landing to select; its daily_cmd runs everything.
SELECTING = ['{python}', '-c', 'import glob, os, sys; print("ok"); sys.exit(1 if os.path.exists('
             '"RED") and ("--daily" in sys.argv or glob.glob("landed-*.txt")) else 0)']


class HuntDailyForm(Case):
    """A whole-step probe runs the daily form (kitlib.dailyfull.plan), never the promotion's
    change-selecting cmd: that selects nothing at main or at a revert, and would probe green."""
    GATE = {'steps': [{'name': 'unit', 'always': True, 'cmd': SELECTING,
                       'daily_cmd': SELECTING + ['--daily'], 'pass': '^ok$'}]}

    def test_main_red_already_is_on_main_and_never_a_revert_confirmed_by_selecting_nothing(self):
        self.land_red()
        self.repo.git('push', '-q', 'origin', 'HEAD:refs/heads/main', cwd=self.stg)
        base = self.remote_main()
        tip = self.land()
        out = self.run_promote()
        self.assertEqual(out.code, G.FAILED, out.lines)
        found = self.attempts()[-1].rec['culprit']
        self.assertEqual(found['end'], CU.ON_MAIN, found)
        self.assertEqual([(p['what'], p['verdict']) for p in found['probes']],
                         [('S', 'red'), ('revert of %s' % tip[:10], 'red'), ('bisect', 'red')])
        self.assertEqual(found['probes'][2]['sha'], base)
        self.assertNotIn('revert', self.attempts()[-1].rec)


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
        real = P.run_push
        with mock.patch.object(P, 'run_push', side_effect=real) as push:
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
