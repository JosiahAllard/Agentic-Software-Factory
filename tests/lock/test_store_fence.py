"""The home fence on the machine's lock store (docs/test-tools.md "Home fence"): in a process under
the kit's tests the machine's store gives no file path and names no mutex (HomeFenced), so nothing
under it can be read, written or locked; a scratch store is untouched; and a real run gets the
store it always got.

On the machine's store these tests compute paths and names only: Store(), .root, .testing,
path(), mutex_name(), real_root(), profile_dir() and machine_home(). None reads, writes or locks
it; the release check's look at it (kitlib.lock.release.live_runs) is given a stand-in for `os`.

On a scratch store, a booking moves the records of runs whose wrapper is surely gone out of
active/, after the queue's prune has said which dead runs it dropped, and removes what has
outlived its readers (DeadRecordsReaped; what Store.prune_files keeps and removes: FilesPruned)."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from unittest import mock

from kitlib.config import user
from kitlib.lock import release
from kitlib.lock import run as rm
from kitlib.lock import store as lockstore
from kitlib.lock.store import EXIT_BUSY, LockError, Settings, Store, iso, real_root
from kitlib.proc import process
from tests.home import unmarked_env
from tests.lock.helpers import StoreCase, clean_env
from tests.platforms import windows_only

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# The marker, spelled here so that a renamed constant fails a test instead of following it.
MARK = 'KIT_HOME_FENCE'
# The prefix of the temp folder a child gets as USERPROFILE and HOME.
CHILD_HOME = 'kit-store-fence-home-'

# A process that imports kitlib and not the tests package, and only computes: the machine's store,
# one line of JSON with its root, one with file paths and a mutex name, one with the store's home.
COMPUTE = '''\
    import json, sys
    sys.path.insert(0, sys.argv[1])
    from kitlib.lock import store as lockstore
    s = lockstore.Store()
    print('built', json.dumps({'root': s.root, 'testing': s.testing,
                               'tests': 'tests' in sys.modules}))
    print('used', json.dumps({'bare': s.path(), 'queue': s.path('queue.json'),
                              'run': s.path('active', 'r.json'),
                              'mutex': s.mutex_name('queue')}))
    print('home', json.dumps(lockstore.machine_home()))
    '''


class Case(unittest.TestCase):
    def folder(self, prefix='kit-store-fence-'):
        d = tempfile.mkdtemp(prefix=prefix)
        self.addCleanup(shutil.rmtree, d, True)
        return d

    def child(self, env):
        """COMPUTE in a child whose USERPROFILE and HOME are a temp folder, over `env`:
        (exit code, {first word: the rest of the line}, all its output)."""
        home = self.folder(CHILD_HOME)
        script = os.path.join(self.folder('kit-store-fence-child-'), 'child.py')
        with open(script, 'w', encoding='utf-8') as fh:
            fh.write(textwrap.dedent(COMPUTE))
        r = subprocess.run([sys.executable, '-I', script, ROOT],
                           env=dict(env, USERPROFILE=home, HOME=home),
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120)
        out = r.stdout.decode(errors='replace')
        lines = {}
        for ln in out.splitlines():
            word, _, rest = ln.partition(' ')
            if word in ('built', 'used', 'home'):
                lines[word] = rest
        return r.returncode, lines, out


@windows_only('the lock is Windows only')
class MachineStoreFenced(Case):
    def refused(self, call, *args):
        with self.assertRaises(user.HomeFenced) as cm:
            call(*args)
        text = str(cm.exception)
        for part in ("machine's lock store", MARK, 'Store(root=', 'Home fence'):
            self.assertIn(part, text)

    def test_the_machines_store_gives_no_path_under_the_tests(self):
        s = Store()                                 # building it is legal: nothing is opened
        self.assertFalse(s.testing)
        self.assertEqual(os.path.normcase(s.root), os.path.normcase(os.path.abspath(real_root())))
        for parts in (('queue.json',), ('active', 'r.json'), ('revision',), ()):
            with self.subTest(parts=parts):
                self.refused(s.path, *parts)

    def test_the_machines_store_names_no_mutex_under_the_tests(self):
        s = Store()
        for lock in ('queue', 'revision', 'run.r-20200101T000000Z-abcd'):
            with self.subTest(lock=lock):
                self.refused(s.mutex_name, lock)

    def test_a_root_that_spells_the_machines_store_is_fenced_too(self):
        real = real_root()
        for root in (real, real.upper(), real.lower(), real + os.sep,
                     os.path.join(real, 'active', os.pardir)):
            with self.subTest(root=root):
                s = Store(root=root)
                self.assertFalse(s.testing)
                self.refused(s.path, 'queue.json')
                self.refused(s.mutex_name, 'queue')

    def test_a_tool_a_test_starts_is_refused_the_machines_store(self):
        # the marker reaches the child by inheritance: this process's environment carries it
        self.assertTrue(os.environ.get(MARK))
        code, lines, out = self.child(dict(os.environ))
        self.assertEqual(json.loads(lines['built']), {
            'root': os.path.abspath(real_root()), 'testing': False, 'tests': False}, out)
        self.assertNotEqual(code, 0, out)
        self.assertNotIn('used', lines, out)
        self.assertNotIn('home', lines, out)
        self.assertIn('HomeFenced', out)
        self.assertIn('Home fence', out)

    def test_a_release_check_is_refused_before_it_looks_at_the_machines_folder(self):
        # the stand-in for `os` says "no such folder": a look at the folder that does not go
        # through Store.path would report the store missing instead of refusing it
        s = Store()
        fake = mock.Mock()
        fake.path.isdir.return_value = False
        with mock.patch.object(release, 'os', fake):
            with self.assertRaises(user.HomeFenced):
                release.live_runs(s)
        self.assertEqual(fake.mock_calls, [])       # nothing was asked of the file system


@windows_only('the lock is Windows only')
class ScratchStore(Case):
    def test_a_scratch_store_is_untouched_by_the_fence(self):
        self.assertTrue(os.environ.get(MARK))
        root = os.path.join(self.folder(), 'store')
        s = Store(root=root)
        self.assertTrue(s.testing)
        self.assertEqual(s.root, os.path.abspath(root))
        self.assertEqual(s.path(), os.path.abspath(root))
        self.assertEqual(s.path('queue.json'), os.path.join(os.path.abspath(root), 'queue.json'))
        self.assertEqual(s.path('active', 'r.json'),
                         os.path.join(os.path.abspath(root), 'active', 'r.json'))
        h = hashlib.sha1(os.path.normcase(os.path.abspath(root)).encode('utf-8')).hexdigest()[:12]
        self.assertEqual(s.mutex_name('queue'), 'Global\\MachineLock.test-%s.queue' % h)
        s.write('holds/h.json', {'id': 'h'})        # and it reads, writes and locks as before
        self.assertEqual(s.read('holds/h.json'), {'id': 'h'})
        self.assertEqual(s.revision(), 1)
        with s.lock('queue'):
            pass

    def test_every_read_write_and_mutex_of_a_store_goes_through_path_or_mutex_name(self):
        class Reached(BaseException):
            """Not an Exception: the store's own `except OSError` cannot swallow it."""

        def reached(*a, **kw):
            raise Reached()

        def store(method):
            s = Store(root=os.path.join(self.folder(), 'store'), settings=Settings(store_lock_s=2.0))
            setattr(s, method, reached)
            return s

        def hold(s, name):
            with s.lock(name):
                pass

        me = {'id': 'r-20200101T000000Z-abcd', 'pid': os.getpid(), 'ctime': process.own_ctime()}
        ended = {'id': me['id'], 'since': iso(), 'held_since': iso(), 'ended': iso()}
        uses = (
            ('path', 'read', lambda s: s.read('queue.json')),
            ('path', 'write', lambda s: s.write('queue.json', {})),
            ('path', 'drop', lambda s: s.drop('queue.json')),
            ('path', 'revision', lambda s: s.revision()),
            ('path', 'bump', lambda s: s.bump()),
            ('path', 'live_runs', lambda s: s.live_runs()),
            ('path', 'live_runs(prune)', lambda s: s.live_runs(prune=True)),
            ('path', 'prune_files', lambda s: s.prune_files()),
            ('path', 'history_append', lambda s: s.history_append(ended)),
            ('path', 'history_records', lambda s: s.history_records()),
            ('mutex_name', 'bump', lambda s: s.bump()),
            ('mutex_name', 'lock', lambda s: hold(s, 'queue')),
            ('mutex_name', 'run_mutex', lambda s: s.run_mutex(me['id'])),
            ('mutex_name', 'run_dead', lambda s: s.run_dead(me)),
        )
        for method, name, use in uses:
            with self.subTest(through=method, use=name):
                with self.assertRaises(Reached):
                    use(store(method))


@windows_only('the lock is Windows only')
class RealRun(Case):
    def test_an_unfenced_process_gets_the_machines_store_as_before(self):
        # the child has no marker and a temp USERPROFILE and HOME; it only computes
        code, lines, out = self.child(unmarked_env())
        self.assertEqual(code, 0, out)
        root = os.path.abspath(real_root())
        self.assertEqual(json.loads(lines['built']),
                         {'root': root, 'testing': False, 'tests': False}, out)
        self.assertEqual(json.loads(lines['used']), {
            'bare': root, 'queue': os.path.join(root, 'queue.json'),
            'run': os.path.join(root, 'active', 'r.json'),
            'mutex': 'Global\\MachineLock.queue'}, out)
        # the store's home is the profile folder Windows reports, not the child's USERPROFILE
        self.assertEqual(json.loads(lines['home']), lockstore.profile_dir(), out)
        self.assertNotIn(CHILD_HOME, lines['home'])

    def test_the_stores_profile_is_the_fences(self):
        self.assertEqual(user.real_profile(), lockstore.profile_dir())


@windows_only('the lock is Windows only')
class DeadRecordsReaped(StoreCase):
    """A booking moves a record whose run is surely gone (Store.run_live) from active/ to ended/,
    after its first look at the queue (so the queue's prune still says which dead runs it
    dropped) and whether or not it gets its slots; every record whose run may be live stays."""
    DEAD_PID = 'r-20261010T000000Z-dead01'      # its pid and creation time name no process
    DEAD_MUTEX = 'r-20261010T000000Z-dead02'    # no creation time, and no run mutex
    SETTLED = 'r-20261010T000000Z-dead03'       # dead, and its ended record was written
    LIVE = 'r-20261010T000000Z-a11ve1'          # this process, with its creation time
    LIVE_MUTEX = 'r-20261010T000000Z-a11ve2'    # no creation time, its run mutex held

    def exited(self):
        """(pid, creation time) of a process that has exited."""
        p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
        ct = process.proc_ctime(p.pid)
        p.kill()
        p.wait()
        return p.pid, ct

    def active(self, rid):
        return os.path.exists(self.store.path('active', rid + '.json'))

    def test_a_booking_moves_dead_records_to_ended_and_keeps_live_ones(self):
        self.confine(1)
        pid, ct = self.exited()
        self.live_record(self.DEAD_PID, pid=pid, ctime=ct, cores=[0])
        self.live_record(self.DEAD_MUTEX, ctime=None)
        self.live_record(self.SETTLED, pid=pid, ctime=ct)
        self.store.write('ended/%s.json' % self.SETTLED, {'id': self.SETTLED, 'how': 'done',
                                                          'code': 0})
        self.live_record(self.LIVE)
        self.live_record(self.LIVE_MUTEX, ctime=None)
        held = self.store.run_mutex(self.LIVE_MUTEX)
        self.addCleanup(held.release)
        stop = self.store.path('active', self.DEAD_PID + '.stop')
        with open(stop, 'w', encoding='utf-8') as f:
            json.dump({'by': 'owner'}, f)
        dead = (self.DEAD_PID, self.DEAD_MUTEX, self.SETTLED)
        self.assertEqual(sorted(d['id'] for d in self.store.live_runs()),
                         [self.LIVE, self.LIVE_MUTEX])
        for rid in dead:                    # a read moves nothing
            self.assertTrue(self.active(rid), rid)

        s = rm.book('engine-test', 'kit', 'reaper', cores=1, store=self.store, no_wait=True,
                    env=clean_env())
        s.release()

        for rid in dead:
            self.assertFalse(self.active(rid), rid)
        self.assertFalse(os.path.exists(stop))
        for rid in (self.LIVE, self.LIVE_MUTEX):
            self.assertTrue(self.active(rid), rid)
            self.assertIsNone(self.store.read('ended/%s.json' % rid), rid)
        by_pid = self.store.read('ended/%s.json' % self.DEAD_PID)
        self.assertEqual({k: by_pid[k] for k in ('id', 'state', 'how', 'code', 'reaped', 'cores')},
                         {'id': self.DEAD_PID, 'state': 'ended', 'how': 'reaped', 'code': None,
                          'reaped': 'its wrapper process is gone', 'cores': [0]})
        self.assertIsNotNone(lockstore.epoch(by_pid['ended']))
        by_mutex = self.store.read('ended/%s.json' % self.DEAD_MUTEX)
        self.assertEqual((by_mutex['how'], by_mutex['reaped']),
                         ('reaped', 'its run mutex is not held'))
        self.assertEqual(self.store.read('ended/%s.json' % self.SETTLED),
                         {'id': self.SETTLED, 'how': 'done', 'code': 0})
        # no history line for a reaped run: only the booking's own
        self.assertEqual([d['slug'] for d in self.store.history_records()], ['reaper'])

    def queue_entry(self, rid, cores):
        """A queue entry for rid holding cores, its wrapper this process: live while rid's run
        mutex is held (Store.run_dead)."""
        return {'id': rid, 'ticket': 1, 'state': 'held', 'pid': os.getpid(),
                'ctime': process.own_ctime(), 'repo': 'kit', 'slug': 'gone',
                'kind': 'engine-test', 'need': {'cpu': len(cores), 'gpu': 0}, 'expect_s': 600,
                'cores': cores, 'gpus': [], 'held_at': time.time()}

    def test_a_dead_run_in_active_and_the_queue_is_still_said_to_have_died(self):
        # The reap writes its ended record, which would make the queue's prune drop its entry
        # quietly as settled (queue.prune); the prune looks first.
        core = self.confine(1)[0]
        self.live_record(self.DEAD_MUTEX, ctime=None, cores=[core])
        self.store.write('queue.json', {'ticket': 1, 'entries': [
            self.queue_entry(self.DEAD_MUTEX, [core])]})
        said = []
        self.store.say = said.append

        s = rm.book('engine-test', 'kit', 'next', cores=1, store=self.store, no_wait=True,
                    env=clean_env())
        s.release()

        [line] = [t for t in said if self.DEAD_MUTEX in t]
        self.assertIn('died holding it; it is free', line)
        self.assertFalse(self.active(self.DEAD_MUTEX))
        self.assertEqual(self.store.read('ended/%s.json' % self.DEAD_MUTEX)['how'], 'reaped')

    def test_a_refused_booking_still_moves_dead_records(self):
        cores = self.confine(1)                 # the live run holds every slot it may use
        self.live_record(self.DEAD_MUTEX, ctime=None)
        self.live_record(self.LIVE_MUTEX, ctime=None, cores=cores)
        held = self.store.run_mutex(self.LIVE_MUTEX)
        self.addCleanup(held.release)
        self.store.write('queue.json', {'ticket': 1, 'entries': [
            self.queue_entry(self.LIVE_MUTEX, cores)]})

        with self.assertRaises(LockError) as cm:
            rm.book('engine-test', 'kit', 'busy', cores=1, store=self.store, no_wait=True,
                    env=clean_env())

        self.assertEqual(cm.exception.code, EXIT_BUSY)
        self.assertFalse(self.active(self.DEAD_MUTEX))
        self.assertEqual(self.store.read('ended/%s.json' % self.DEAD_MUTEX)['how'], 'reaped')
        self.assertTrue(self.active(self.LIVE_MUTEX))

    def test_a_booking_removes_ended_records_and_logs_past_their_keep(self):
        self.confine(1)
        old, fresh = 'r-20261001T000000Z-01d0a1', 'r-20261010T000000Z-f7e5a1'
        aged(self.store.path('ended', old + '.json'), DAY * 4)
        aged(self.store.path('logs', old + '.log'), DAY * 4)
        aged(self.store.path('ended', fresh + '.json'), DAY * 2)

        s = rm.book('engine-test', 'kit', 'pruner', cores=1, store=self.store, no_wait=True,
                    env=clean_env())
        s.release()

        self.assertFalse(os.path.exists(self.store.path('ended', old + '.json')))
        self.assertFalse(os.path.exists(self.store.path('logs', old + '.log')))
        self.assertTrue(os.path.exists(self.store.path('ended', fresh + '.json')))


DAY = 86400


def aged(path, age_s, folder=False):
    """A file (folder: a folder holding control.json) at path, last changed age_s ago."""
    os.makedirs(path if folder else os.path.dirname(path), exist_ok=True)
    if folder:
        with open(os.path.join(path, 'control.json'), 'w', encoding='utf-8') as f:
            f.write('{}')
    else:
        with open(path, 'w', encoding='utf-8') as f:
            f.write('{}')
    t = time.time() - age_s
    os.utime(path, (t, t))
    return path


class FilesPruned(Case):
    """Store.prune_files removes what has outlived its readers and nothing a live run or the
    owner still has: on Linux too, so called directly (the booking's call: DeadRecordsReaped)."""
    LIVE = 'r-20261010T000000Z-a11ve1'
    UNREAD = 'r-20261010T000000Z-a11ve2'
    DEAD = 'r-20261010T000000Z-dead01'
    FRESH = 'r-20261010T000000Z-dead02'

    def setUp(self):
        self.store = Store(root=os.path.join(self.folder(), 'store'))

    def there(self, *parts):
        return os.path.exists(self.store.path(*parts))

    def test_ended_records_and_logs_go_past_their_keep_and_corrupt_stays(self):
        aged(self.store.path('ended', self.DEAD + '.json'), DAY * 4)
        aged(self.store.path('logs', self.DEAD + '.log'), DAY * 4)
        aged(self.store.path('ended', self.FRESH + '.json'), DAY * 2)
        aged(self.store.path('_corrupt', 'active', self.DEAD + '.json'), DAY * 400)
        aged(self.store.path('_corrupt', 'queue.json'), DAY * 400)

        self.store.prune_files()

        self.assertFalse(self.there('ended', self.DEAD + '.json'))
        self.assertFalse(self.there('logs', self.DEAD + '.log'))
        self.assertTrue(self.there('ended', self.FRESH + '.json'))
        self.assertEqual(self.store.corrupt(), ['active/%s.json' % self.DEAD, 'queue.json'])

    def test_a_background_folder_goes_only_when_no_active_file_names_its_run(self):
        self.store.write('active/%s.json' % self.LIVE, {'id': self.LIVE})
        with open(self.store.path('active', self.UNREAD + '.json'), 'w') as f:
            f.write('{"id": ')                      # a record that does not parse
        for rid, age in ((self.LIVE, DAY), (self.UNREAD, DAY), (self.DEAD, DAY), (self.FRESH, 60)):
            aged(self.store.path('background', rid + '.d'), age, folder=True)

        self.store.prune_files()

        self.assertFalse(self.there('background', self.DEAD + '.d'))
        for rid in (self.LIVE, self.UNREAD, self.FRESH):
            self.assertTrue(self.there('background', rid + '.d'), rid)

    def test_a_history_month_goes_history_keep_days_after_it_ended(self):
        now = lockstore.utc_now()
        this = aged(self.store.path('history', now.strftime('%Y-%m') + '.jsonl'), 0)
        old = aged(self.store.path('history', '%04d-%02d.jsonl' % (now.year - 1, now.month)), 0)

        self.store.prune_files()

        self.assertFalse(os.path.exists(old))
        self.assertTrue(os.path.exists(this))

    def test_one_call_removes_at_most_prune_max(self):
        for i in range(lockstore.PRUNE_MAX + 3):
            aged(self.store.path('ended', 'r-20261001T000000Z-%06x.json' % i), DAY * 4)

        self.store.prune_files()
        self.assertEqual(len(os.listdir(self.store.path('ended'))), 3)
        self.store.prune_files()
        self.assertEqual(os.listdir(self.store.path('ended')), [])


if __name__ == '__main__':
    unittest.main()
