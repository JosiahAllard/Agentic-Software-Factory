"""The fake git's pack reader: a REF_DELTA whose base is in the same pack."""
import os
import shutil
import subprocess
import tempfile
import threading
import unittest

from kitlib.git.fake import objects
from tests.realgit import real_git

GIT = shutil.which('git')


def _git(cwd, *args):
    return subprocess.run(
        ('git', '-c', 'user.name=t', '-c', 'user.email=t@example.invalid') + args,
        cwd=cwd, check=True, capture_output=True, text=True).stdout


@real_git('real git writes the pack with REF_DELTA objects')
@unittest.skipUnless(GIT, 'real git needed to write the pack')
class RefDeltaSamePackTest(unittest.TestCase):
    def test_ref_delta_with_base_in_same_pack_reads(self):
        with tempfile.TemporaryDirectory() as d:
            _git(d, 'init', '-q')
            body = ''.join('line %d of a file long enough to delta well\n' % i for i in range(400))
            for n in range(4):
                with open(os.path.join(d, 'f.txt'), 'w', newline='\n') as f:
                    f.write(body + 'edit %d\n' % n)
                _git(d, 'add', 'f.txt')
                _git(d, 'commit', '-q', '-m', 'c%d' % n)
            # no --delta-base-offset: deltas are REF_DELTA, bases in the same pack
            _git(d, '-c', 'repack.useDeltaBaseOffset=false', 'repack', '-adf', '-q')
            packdir = os.path.join(d, '.git', 'objects', 'pack')
            idx = [n for n in os.listdir(packdir) if n.endswith('.idx')]
            self.assertEqual(len(idx), 1)
            verify = _git(d, 'verify-pack', '-v', os.path.join(packdir, idx[0]))
            deltas = [ln for ln in verify.splitlines() if len(ln.split()) == 7]
            self.assertTrue(deltas, 'the pack holds no delta')
            oids = _git(d, 'rev-list', '--objects', '--all').split()[::2]

            store = objects.ObjectStore(os.path.join(d, '.git', 'objects'), 'sha1')
            result = {}

            def work():
                result['read'] = [store.read(o) for o in oids]

            t = threading.Thread(target=work, daemon=True)
            t.start()
            t.join(20)
            self.assertFalse(t.is_alive(), 'reading a REF_DELTA deadlocked')
            self.assertTrue(all(r is not None for r in result['read']))
            for o, (kind, data) in zip(oids, result['read']):
                self.assertEqual(objects.hash_object('sha1', kind, data), o)


if __name__ == '__main__':
    unittest.main()
