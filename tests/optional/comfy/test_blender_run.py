"""optional/comfy/blender_run.py: finding Blender, the lock command, and the committed-script,
--out and stray-write checks. Blender never starts here: subprocess.call is replaced.

usage: python -m unittest tests.optional.comfy.test_blender_run -v
"""
import io
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
CODE = os.path.join(HERE, '..', '..', '..', 'optional', 'comfy')
sys.path.insert(0, CODE)
import blender_run as B  # noqa: E402


def touch(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        f.write('x')
    return path


class Finding(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_newest_reads_the_version_from_the_folder(self):
        paths = [r'C:\PF\Blender Foundation\Blender 4.2\blender.exe',
                 r'C:\PF\Blender Foundation\Blender 5.2\blender.exe',
                 r'C:\PF\Blender Foundation\Blender 4.10\blender.exe']
        self.assertEqual(B.newest(paths), paths[1])
        self.assertIsNone(B.newest([]))

    def test_install_candidates_on_windows(self):
        exe = touch(os.path.join(self.tmp, 'Blender Foundation', 'Blender 5.2', 'blender.exe'))
        got = B.install_candidates({'ProgramFiles': self.tmp, 'ProgramW6432': self.tmp}, 'win32')
        self.assertEqual(got, [exe])

    def test_order_given_then_env_then_path_then_install(self):
        given = touch(os.path.join(self.tmp, 'given', 'blender.exe'))
        envb = touch(os.path.join(self.tmp, 'env', 'blender.exe'))
        inst = touch(os.path.join(self.tmp, 'Blender Foundation', 'Blender 5.2', 'blender.exe'))
        env = {'KIT_BLENDER': envb, 'ProgramFiles': self.tmp}
        with mock.patch('shutil.which', return_value=None):
            self.assertEqual(B.find_blender(given, env, 'win32'), given)
            self.assertEqual(B.find_blender(None, env, 'win32'), envb)
            self.assertEqual(B.find_blender(None, {'ProgramFiles': self.tmp}, 'win32'), inst)
            with self.assertRaises(B.Refused):
                B.find_blender(None, {}, 'win32')
        with mock.patch('shutil.which', return_value='/usr/bin/blender'):
            self.assertEqual(B.find_blender(None, {'ProgramFiles': self.tmp}, 'win32'), '/usr/bin/blender')

    def test_a_named_blender_that_is_missing_refuses_rather_than_searching(self):
        with mock.patch('shutil.which', return_value='/usr/bin/blender'):
            with self.assertRaises(B.Refused) as e:
                B.find_blender(os.path.join(self.tmp, 'nope.exe'), {}, 'win32')
        self.assertIn('no Blender at', str(e.exception))


class Commands(unittest.TestCase):
    def test_blender_argv(self):
        got = B.blender_argv('blender', 'job.py', ['--out', 'o'], factory=True)
        self.assertEqual(got[:3], ['blender', '--background', '--factory-startup'])
        self.assertEqual(got[3:7], ['--python-exit-code', '1', '--python', os.path.abspath('job.py')])
        self.assertEqual(got[7:], ['--', '--out', 'o'])
        self.assertNotIn('--factory-startup', B.blender_argv('blender', 'job.py', []))

    def test_lock_cmd_books_render_on_this_machine(self):
        got = B.lock_cmd('py', 'ml.py', 'blender-job', ['blender', 'x'])
        self.assertEqual(got, ['py', '-B', 'ml.py', 'run', '--repo', 'comfy', '--slug', 'blender-job',
                               '--kind', 'render', '--node', 'main', '--max-wait', '540s', '--',
                               'blender', 'x'])

    def test_slug_and_script_args(self):
        self.assertEqual(B.slug('render', 'out/My Sheet.png'), 'render-My-Sheet')
        self.assertEqual(B.slug('motion', '...'), 'motion-run')
        self.assertEqual(B.script_args(['blender', '--python', 'x', '--', 'a', 'b']), ['a', 'b'])
        self.assertEqual(B.script_args(['blender']), [])


class Checks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = os.path.join(self.tmp, 'repo')
        os.makedirs(os.path.join(self.repo, '.git'))

    def test_out_must_be_in_a_checkout_and_not_in_its_git_folder(self):
        self.assertIsNone(B.out_refusal(os.path.join(self.repo, 'art', 'new')))
        self.assertIn('not inside a git checkout', B.out_refusal(os.path.join(self.tmp, 'loose')))
        self.assertIn('.git folder', B.out_refusal(os.path.join(self.repo, '.git', 'x')))

    def test_a_script_must_be_tracked_and_clean(self):
        script = touch(os.path.join(self.repo, 'tools', 'job.py'))
        calls = []

        def git(answers):
            def run(root, *args):
                calls.append(args)
                return answers[args[0]]
            return run
        self.assertIsNone(B.script_refusal(script, git({'ls-files': (0, ''), 'status': (0, '')})))
        self.assertEqual(calls[0], ('ls-files', '--error-unmatch', '--', 'tools/job.py'))
        self.assertIn('not tracked', B.script_refusal(script, git({'ls-files': (1, '')})))
        self.assertIn('uncommitted', B.script_refusal(
            script, git({'ls-files': (0, ''), 'status': (0, ' M tools/job.py\n')})))
        self.assertIn('no script', B.script_refusal(os.path.join(self.repo, 'gone.py'), git({})))
        loose = touch(os.path.join(self.tmp, 'loose.py'))
        self.assertIn('not in a git checkout', B.script_refusal(loose, git({})))

    def test_stray_writes_are_new_or_changed_paths_outside_out(self):
        before = ' M kept.txt\n?? old/\n'
        after = (' M kept.txt\n?? old/\n?? art/round1/sheet.png\n M src/model.py\n'
                 '?? art/\n?? "with space.txt"\nR  a.txt -> b.txt\n')
        self.assertEqual(B.stray_writes(before, after, 'art/round1'),
                         ['b.txt', 'src/model.py', 'with space.txt'])
        self.assertEqual(B.stray_writes('', ' M kept.txt\n', 'art'), ['kept.txt'])
        self.assertEqual(B.stray_writes(' M kept.txt\n', ' M kept.txt\n', 'art'), [])
        self.assertEqual(B.stray_writes('', 'MM kept.txt\n', ''), [])


class Main(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = os.path.join(self.tmp, 'repo')
        os.makedirs(os.path.join(self.repo, '.git'))
        self.script = touch(os.path.join(self.repo, 'job.py'))
        self.out = os.path.join(self.repo, 'art', 'o')
        self.after = ''
        for name, value in (('find_blender', 'BLENDER'), ('need_tool', None), ('lock_python', 'PY')):
            p = mock.patch.object(B, name, return_value=value)
            p.start()
            self.addCleanup(p.stop)
        self.call = mock.patch('subprocess.call', return_value=0).start()
        self.addCleanup(mock.patch.stopall)
        self.statuses = []

    def git(self, root, *args):
        if args[0] == 'ls-files':
            return 0, ''
        if args[:2] == ('status', '--porcelain') and args[2] == '--':
            return 0, ''
        self.statuses.append(args)
        return 0, '' if len(self.statuses) == 1 else self.after

    def run_main(self, *args):
        with mock.patch('sys.stdout', new_callable=io.StringIO) as out, \
                mock.patch('sys.stderr', new_callable=io.StringIO) as err:
            code = B.main(list(args), git=self.git)
        return code, out.getvalue(), err.getvalue()

    def test_runs_the_script_under_the_lock_in_out(self):
        code, text, _ = self.run_main(self.script, '--out', self.out, '--', '--size', '2')
        self.assertEqual(code, 0)
        cmd = self.call.call_args.args[0]
        kw = self.call.call_args.kwargs
        self.assertEqual(cmd[:4], ['PY', '-B', B.MACHINE_LOCK_TOOL, 'run'])
        self.assertIn('render', cmd)
        tail = cmd[cmd.index('BLENDER'):]
        self.assertIn('--factory-startup', tail)
        self.assertEqual(tail[-5:], ['--', '--out', os.path.abspath(self.out), '--size', '2'])
        self.assertEqual(kw['cwd'], os.path.abspath(self.out))
        self.assertEqual(kw['env']['BLENDER_RUN_OUT'], os.path.abspath(self.out))
        self.assertTrue(os.path.isdir(self.out))
        self.assertIn('OUT ', text)

    def test_the_run_code_is_returned(self):
        self.call.return_value = 1
        self.assertEqual(self.run_main(self.script, '--out', self.out)[0], 1)

    def test_a_write_outside_out_fails_the_run(self):
        self.after = '?? art/o/a.png\n?? stray.glb\n'
        code, _, err = self.run_main(self.script, '--out', self.out)
        self.assertEqual(code, 3)
        self.assertIn('OUTSIDE --out: stray.glb', err)
        self.assertNotIn('a.png', err)

    def test_refusals_start_nothing(self):
        cases = [((self.script, '--out', os.path.join(self.tmp, 'loose')), 'not inside a git checkout'),
                 ((os.path.join(self.repo, 'gone.py'), '--out', self.out), 'no script')]
        for args, why in cases:
            with self.subTest(why=why):
                code, _, err = self.run_main(*args)
                self.assertEqual(code, 2)
                self.assertIn(why, err)
        with mock.patch.object(B, 'find_blender', side_effect=B.Refused('no Blender found')):
            self.assertEqual(self.run_main(self.script, '--out', self.out)[0], 2)
        self.assertEqual(self.call.call_count, 0)
        self.assertEqual(self.run_main()[0], 2)


class EnsureLocked(unittest.TestCase):
    def setUp(self):
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(B, 'need_tool').start()
        mock.patch.object(B, 'lock_python', return_value='PY').start()
        self.call = mock.patch('subprocess.call', return_value=4).start()

    def test_the_locked_rerun_returns_and_consumes_its_marker(self):
        with mock.patch.dict(os.environ, {'X_LOCKED': '1', 'MACHINE_LOCK_RUN': 'lease'}):
            B.ensure_locked('m.py', [], 'motion-x', 'X_LOCKED', 'BL')
            self.assertNotIn('X_LOCKED', os.environ)
        self.assertEqual(self.call.call_count, 0)

    def test_otherwise_it_relaunches_under_the_lock_and_exits_with_the_code(self):
        with mock.patch.dict(os.environ, {'X_LOCKED': '1'}):
            os.environ.pop('MACHINE_LOCK_RUN', None)
            with self.assertRaises(SystemExit) as e:
                B.ensure_locked('m.py', ['a.glb'], 'motion-x', 'X_LOCKED', 'BL')
        self.assertEqual(e.exception.code, 4)
        cmd = self.call.call_args.args[0]
        self.assertEqual(cmd[cmd.index('--slug') + 1], 'motion-x')
        self.assertEqual(cmd[-2:], ['--', 'a.glb'])
        self.assertEqual(self.call.call_args.kwargs['env']['X_LOCKED'], '1')

    def test_no_tool_refuses(self):
        with mock.patch.object(B, 'need_tool', side_effect=B.Refused('no machine lock tool')), \
                mock.patch('sys.stderr', new_callable=io.StringIO), \
                mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop('X_LOCKED', None)
            with self.assertRaises(SystemExit) as e:
                B.ensure_locked('m.py', [], 'motion-x', 'X_LOCKED', 'BL')
        self.assertEqual(e.exception.code, 2)
        self.assertEqual(self.call.call_count, 0)


if __name__ == '__main__':
    unittest.main()
