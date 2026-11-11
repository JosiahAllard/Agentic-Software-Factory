"""boot/kitboot.py, the check every host entry runs before it imports kitlib (docs/kitboot.md): the
folder beside boot/ or PM_KIT_ROOT, and every refusal; no git."""
import importlib.util
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location('kitboot_under_test', ROOT / 'boot' / 'kitboot.py')
K = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(K)


class Source(unittest.TestCase):
    def test_nothing_set_is_the_checkout_beside_boot(self):
        kit = K.locate({})
        self.assertEqual(Path(kit.root), ROOT)
        self.assertEqual(K.locate({}, here=str(ROOT / 'boot' / 'kitboot.py')), kit)

    def test_an_absolute_root_is_used(self):
        self.assertEqual(Path(K.locate({K.ROOT_ENV: str(ROOT)}).root), ROOT.resolve())

    def test_refusals(self):
        scratch = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, scratch, True)
        for env, why in (({K.ROOT_ENV: ''}, 'non-blank'), ({K.ROOT_ENV: '  '}, 'non-blank'),
                         ({K.ROOT_ENV: 'relative/kit'}, 'absolute'),
                         ({K.ROOT_ENV: scratch}, 'no kit at')):
            with self.subTest(env=env), self.assertRaises(K.BootError) as caught:
                K.locate(env)
            self.assertIn(why, str(caught.exception))


class Bind(unittest.TestCase):
    def setUp(self):
        saved = list(sys.path)
        self.addCleanup(sys.path.__setitem__, slice(None), saved)

    def test_this_kit_goes_first_on_the_path(self):
        self.assertEqual(Path(K.boot({})), ROOT)
        self.assertEqual(Path(sys.path[0]), ROOT)
        before = list(sys.path)
        K.boot({})
        self.assertEqual(sys.path, before)                  # not inserted twice

    def test_a_kitlib_loaded_from_elsewhere_is_refused(self):
        other = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, other, True)
        os.makedirs(os.path.join(other, 'kitlib'))
        with self.assertRaises(K.BootError) as caught:
            K.bind(K.Kit(other))
        self.assertIn('another kitlib is already loaded', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
