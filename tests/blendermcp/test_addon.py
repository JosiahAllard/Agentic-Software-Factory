"""kitlib.blendermcp.addon: finding Blender, its version, and the add-on pin."""
import hashlib
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from kitlib.blendermcp import addon


class Addon(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='blendermcp-')
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def write(self, rel, data):
        path = os.path.join(self.tmp, *rel.split('/'))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as f:
            f.write(data)
        return path

    def test_the_tree_hash_is_paths_and_file_hashes_in_order_without_pycache(self):
        self.write('mcp/b.py', b'B')
        self.write('mcp/a/c.txt', b'C')
        self.write('mcp/__pycache__/x.pyc', b'junk')
        want = hashlib.sha256()
        # os.walk order: the folder's own files first, then its sorted subfolders
        want.update(b'b.py\0' + hashlib.sha256(b'B').hexdigest().encode() + b'\n')
        want.update(b'a/c.txt\0' + hashlib.sha256(b'C').hexdigest().encode() + b'\n')
        folder = os.path.join(self.tmp, 'mcp')
        self.assertEqual(addon.tree_hash(folder), want.hexdigest())
        self.write('mcp/b.py', b'B2')
        self.assertNotEqual(addon.tree_hash(folder), want.hexdigest())

    def test_check_refuses_a_missing_or_unpinned_add_on(self):
        with self.assertRaises(addon.SetupError) as cm:
            addon.check(os.path.join(self.tmp, 'none'))
        self.assertIn('not installed', str(cm.exception))
        self.write('mcp/x.py', b'x')
        with self.assertRaises(addon.SetupError) as cm:
            addon.check(os.path.join(self.tmp, 'mcp'))
        self.assertIn('not the reviewed', str(cm.exception))
        with mock.patch.object(addon, 'TREE_SHA256', addon.tree_hash(os.path.join(self.tmp, 'mcp'))):
            addon.check(os.path.join(self.tmp, 'mcp'))

    def test_the_add_on_folder_per_platform(self):
        self.assertEqual(addon.addon_dir('5.2', {'APPDATA': r'C:\A'}, 'windows'),
                         os.path.join(r'C:\A', 'Blender Foundation', 'Blender', '5.2', 'extensions',
                                      'user_default', 'mcp'))
        self.assertEqual(addon.addon_dir('5.2', {'HOME': '/h'}, 'linux'),
                         os.path.join('/h', '.config', 'blender', '5.2', 'extensions',
                                      'user_default', 'mcp'))
        self.assertEqual(addon.addon_dir('5.2', {'XDG_CONFIG_HOME': '/x'}, 'linux'),
                         os.path.join('/x', 'blender', '5.2', 'extensions', 'user_default', 'mcp'))
        self.assertEqual(addon.addon_dir('5.2', {'BLENDER_USER_EXTENSIONS': '/e', 'APPDATA': 'a'},
                                         'windows'), os.path.join('/e', 'user_default', 'mcp'))
        with self.assertRaises(addon.SetupError):
            addon.addon_dir('5.2', {}, 'windows')

    def test_find_blender_takes_the_env_then_path_then_the_newest_install(self):
        exe = self.write('Blender 5.1/blender.exe', b'')
        self.assertEqual(addon.find_blender({addon.BLENDER_ENV: exe}, lambda n: 'on-path'), exe)
        with self.assertRaises(addon.SetupError):
            addon.find_blender({addon.BLENDER_ENV: exe + '.missing'}, lambda n: 'on-path')
        self.assertEqual(addon.find_blender({}, lambda n: 'on-path', platform='windows'), 'on-path')
        pf = os.path.join(self.tmp, 'pf')
        hits = [os.path.join(pf, 'Blender Foundation', 'Blender %s' % v, 'blender.exe')
                for v in ('4.9', '5.10', '5.2', 'x')]
        env = {'ProgramFiles': pf}
        got = addon.find_blender(env, lambda n: None, lambda pattern: list(hits), 'windows')
        self.assertEqual(got, hits[1])
        with self.assertRaises(addon.SetupError) as cm:
            addon.find_blender(env, lambda n: None, lambda pattern: [], 'windows')
        self.assertIn(addon.BLENDER_ENV, str(cm.exception))
        with self.assertRaises(addon.SetupError):
            addon.find_blender({}, lambda n: None, lambda pattern: list(hits), 'linux')

    def test_the_version_comes_from_the_folder_else_from_blender(self):
        self.assertEqual(addon.version_of(r'C:\P\Blender 5.2\blender.exe', run=None), (5, 2))
        calls = []

        def run(argv, **kw):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, 'Blender 5.1.3\nbuild date', '')
        self.assertEqual(addon.version_of('/usr/bin/blender', run), (5, 1))
        self.assertEqual(calls, [['/usr/bin/blender', '--version']])
        with self.assertRaises(addon.SetupError):
            addon.version_of('/b', lambda argv, **kw: subprocess.CompletedProcess(argv, 0, 'no', ''))
        with self.assertRaises(addon.SetupError):
            addon.version_of('/b', mock.Mock(side_effect=OSError('gone')))

    def test_ready_refuses_an_old_blender_and_checks_the_add_on_of_its_version(self):
        old = self.write('Blender 5.0/blender.exe', b'')
        with self.assertRaises(addon.SetupError) as cm:
            addon.ready({addon.BLENDER_ENV: old})
        self.assertIn('older', str(cm.exception))
        new = self.write('Blender 5.2/blender.exe', b'')
        ext = os.path.join(self.tmp, 'ext')
        env = {addon.BLENDER_ENV: new, 'BLENDER_USER_EXTENSIONS': ext}
        with self.assertRaises(addon.SetupError):
            addon.ready(env)
        self.write('ext/user_default/mcp/__init__.py', b'pinned')
        with mock.patch.object(addon, 'TREE_SHA256',
                               addon.tree_hash(os.path.join(ext, 'user_default', 'mcp'))):
            self.assertEqual(addon.ready(env), new)

    def test_the_launch_command(self):
        # --online-mode is Blender's own option: after --command the add-on's parser gets it
        # and refuses it (exit 2), as the live run found
        # --python-use-system-env lets Blender's Python read launch_env's PYTHONPYCACHEPREFIX
        self.assertEqual(addon.launch_argv('b', 9877),
                         ['b', '--background', '--python-use-system-env', '--online-mode', '--command',
                          'blender_mcp', '--host', '127.0.0.1', '--port', '9877'])

    def test_the_launch_env_keeps_python_off_the_add_ons_own_pycache(self):
        # a .pyc planted in the add-on's __pycache__ (outside the tree hash) is never read: Python
        # reads and writes its caches only under the prefix, a fresh empty folder per Blender
        got = addon.launch_env({'PATH': 'p', 'PYTHONPATH': 'x', 'pythonhome': 'y', 'PYTHONPYCACHEPREFIX': 'old',
                                'MACHINE_LOCK_RUN': 'r'}, 'C:/cache')
        self.assertEqual(got, {'PATH': 'p', 'MACHINE_LOCK_RUN': 'r', 'PYTHONPYCACHEPREFIX': 'C:/cache',
                               'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONNOUSERSITE': '1'})


if __name__ == '__main__':
    unittest.main()
