"""The backlog check and the writes on a store the size of a large repository's: 154 pulled
records, ~3,000 scope members with broad `brain/**`, `node/**` globs, a ~3,000-path tree
(BACKLOG-PULL-CHECK-QUADRATIC: the pairwise check took minutes a write)."""
import os
import random
import time

from kitlib.backlog import check as checkmod
from tests.backlog.helpers import TODAY, StoreCase, priced, rec

TOPS = ('brain', 'node', 'kitlib', 'tools', 'docs', 'web', 'game', 'net')
TREE = ['%s/s%d/t%d/f%d.py' % (t, i, j, k)
        for t in TOPS for i in range(8) for j in range(6) for k in range(8)]
BRAIN = ['kit/brain%d' % i for i in range(3)]       # live branches with a worktree each
OTHER = ['kit/b%02d' % i for i in range(27)]
CHECK_BOUND = 5.0       # seconds: the record's Done-when for the check
# Seconds for one write. A write also runs git (the store's read and publish, the tree, each live
# branch's diff and worktree status), which a loaded machine slows many times over; before the
# fix one took minutes, so this still catches the pairwise check coming back.
WRITE_BOUND = 60.0


def members(rnd, n):
    out, tops = [], [t for t in TOPS if t != 'brain']
    while len(out) < n:
        t, i, j, x = rnd.choice(tops), rnd.randrange(8), rnd.randrange(6), rnd.random()
        m = ('%s/s%d/t%d/f%d.py' % (t, i, j, rnd.randrange(8)) if x < 0.6 else
             '%s/s%d/t%d/**' % (t, i, j) if x < 0.85 else
             '%s/s%d' % (t, i) if x < 0.95 else '%s/s%d/**' % (t, i))
        if m not in out:
            out.append(m)
    return out


def records():
    rnd, out = random.Random(7), []
    for n in range(154):
        if n < 6:
            scope, by = ['brain/s%d/**' % n], BRAIN[n % 3]
        else:
            scope, by = members(rnd, 20), OTHER[n % 27]
            if n < 9:
                scope[0] = ('node/**', 'web/**', 'game/**')[n - 6]
        out.append(priced('R%03d' % n, state='pulled', pulled_at=TODAY, pulled_by=by,
                          scope=scope))
    out.append(priced('CAND', priority='1', scope='brain/**'))     # narrowed against brain
    out.append(priced('PULLME', priority='2', scope='extra/new.py'))
    out.append(rec('BLK', state='blocked', blocked_on='item:PULLME', scope='extra/blk.py'))
    return out


class LargeStore(StoreCase):

    def setUp(self):
        super().setUp()
        repo = self.repo
        for p in TREE:
            repo.write(p, 'x\n')
        repo._own('add', '-A')
        repo._own('commit', '-q', '-m', 'tree')
        repo._own('push', '-q', 'origin', 'main')
        repo._own('fetch', '-q', 'origin')
        for b in OTHER:
            repo._own('branch', b)
        for n, b in enumerate(BRAIN):
            wt = os.path.join(repo.base, 'wt%d' % n)
            repo._own('worktree', 'add', '-q', '-b', b, wt, 'main')
            repo.write('brain/s%d/t0/f0.py' % n, 'changed\n', root=wt)
            repo._own('commit', '-q', '-am', 'brain %d' % n, cwd=wt)
        self.seed(*records())

    def timed(self, run):
        t = time.perf_counter()
        out = run()
        return out, time.perf_counter() - t

    def test_check_and_writes_finish_in_seconds(self):
        snap = self.store.load()
        pulled = [r for r in snap.readable if r.get('state') == 'pulled']
        self.assertEqual(len(pulled), 154)
        self.assertGreater(sum(len(r.all('scope')) for r in pulled), 2900)
        results, took = self.timed(lambda: checkmod.check_store(snap))
        warned = {r.name: r for r in results}[checkmod.PULL_RECORDS].warnings
        self.assertTrue(warned)                     # the broad globs collide
        with self.subTest('check'):
            self.assertLess(took, CHECK_BOUND, 'check_store took %.2f s' % took)
        writes = (('set', ('PULLME', [('summary', ['a renamed thing that is wrong and must be '
                                                   'fixed'])])),
                  ('unblock', ('BLK',), dict(reason='PULLME is not needed')),
                  ('pull', ('PULLME',)))
        for verb in writes:
            name, args, kw = verb if len(verb) == 3 else verb + ({},)
            _change, took = self.timed(lambda: self.verb(name, *args, **kw))
            with self.subTest(name):
                self.assertLess(took, WRITE_BOUND, '%s took %.2f s' % (name, took))
        self.assertEqual(self.get('PULLME').get('state'), 'pulled')
        self.assertEqual(self.get('BLK').get('state'), 'open')
