import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from hosts.codex.adapter import plugin_install
from hosts.codex.adapter.sessions import native_codex


class NativeExecutable(unittest.TestCase):
    def test_explicit_exe_and_environment_binding(self):
        with tempfile.TemporaryDirectory() as scratch:
            binary = Path(scratch) / "codex.exe"
            binary.touch()
            self.assertEqual(native_codex(binary), str(binary.resolve()))
            self.assertEqual(native_codex(environ={"PM_KIT_CODEX_EXE": str(binary)}), str(binary.resolve()))

    def test_shims_missing_and_relative_paths_are_refused(self):
        with tempfile.TemporaryDirectory() as scratch:
            shim = Path(scratch) / "codex.cmd"
            shim.touch()
            for candidate in (shim, Path(scratch) / "absent.exe", "codex.exe"):
                with self.subTest(candidate=candidate), self.assertRaises(ValueError):
                    native_codex(candidate)
        with mock.patch("shutil.which", return_value=None), self.assertRaises(ValueError):
            native_codex(environ={})

    def test_off_path_the_desktop_apps_newest_codex_exe_is_found(self):
        """No PM_KIT_CODEX_EXE, none on PATH: the newest by mtime of the desktop app's
        LOCALAPPDATA/OpenAI/Codex/bin/<hash>/codex.exe; a hash folder without one is skipped."""
        with tempfile.TemporaryDirectory() as local:
            bin_dir = Path(local) / "OpenAI" / "Codex" / "bin"
            old, new, other = bin_dir / "aaa" / "codex.exe", bin_dir / "bbb" / "codex.exe", bin_dir / "ccc" / "rg.exe"
            for exe, when in ((old, 1000), (new, 2000), (other, 3000)):
                exe.parent.mkdir(parents=True)
                exe.touch()
                os.utime(exe, (when, when))
            env = {"LOCALAPPDATA": local, "PATH": ""}
            self.assertEqual(native_codex(environ=env), str(new.resolve()))
            os.utime(old, (4000, 4000))
            self.assertEqual(native_codex(environ=env), str(old.resolve()))
            self.assertEqual(native_codex(environ=dict(env, PM_KIT_CODEX_EXE=str(new))), str(new.resolve()))


class LinuxExecutable(unittest.TestCase):
    """Platform NAME 'linux': the binary is `codex`, with no suffix."""

    def setUp(self):
        patch = mock.patch("kitlib.platform.NAME", "linux")
        patch.start()
        self.addCleanup(patch.stop)

    def test_codex_resolves_from_path_and_from_the_environment(self):
        with tempfile.TemporaryDirectory() as scratch:
            binary = Path(scratch) / "codex"
            binary.touch()
            # shutil.which on a Windows test machine would append PATHEXT; the lookup name is the point
            with mock.patch("shutil.which", return_value=str(binary)) as which:
                self.assertEqual(native_codex(environ={"PATH": scratch}), str(binary.resolve()))
            which.assert_called_once_with("codex", path=scratch)
            self.assertEqual(native_codex(environ={"PM_KIT_CODEX_EXE": str(binary), "PATH": ""}),
                             str(binary.resolve()))
            self.assertEqual(native_codex(binary), str(binary.resolve()))

    def test_shims_missing_and_relative_paths_are_still_refused(self):
        with tempfile.TemporaryDirectory() as scratch:
            shim = Path(scratch) / "codex.cmd"
            shim.touch()
            for candidate in (shim, Path(scratch) / "absent", "codex"):
                with self.subTest(candidate=candidate), self.assertRaises(ValueError):
                    native_codex(candidate)

    def test_codex_running_matches_codex(self):
        for names, want in ((["bash", "codex"], True), (["bash", "codex.exe"], False)):
            with self.subTest(names=names), mock.patch(
                    "kitlib.proc.process.snapshot", return_value=[(i, 0, n) for i, n in enumerate(names)]):
                self.assertEqual(plugin_install.codex_running(), want)
