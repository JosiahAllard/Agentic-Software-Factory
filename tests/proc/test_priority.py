"""kitlib.proc.process.raise_to_normal: a process below normal (idle or below normal, as a
scheduled task's default priority 7 gives) is raised to normal; one at normal or above is left
as it is; off Windows nothing is called. Every kernel32 call is a fake: no real process's
priority is read or changed."""
import types
import unittest
from unittest import mock

import kitlib.platform
from kitlib.proc import process, win32

ME = 0xFFFF                     # the fake pseudo handle of this process
ABOVE_NORMAL, HIGH, REALTIME = 0x8000, 0x80, 0x100


def fake_k32(now, set_ok=True):
    """A K32 whose GetPriorityClass answers `now` (0: failed) and records each SetPriorityClass."""
    calls = []

    def set_class(h, cls):
        calls.append((h, cls))
        return 1 if set_ok else 0
    k32 = types.SimpleNamespace(GetCurrentProcess=lambda: ME,
                                GetPriorityClass=lambda h: now if h == ME else 0,
                                SetPriorityClass=set_class)
    return k32, calls


class RaiseToNormal(unittest.TestCase):
    def run_with(self, now, set_ok=True, platform="windows"):
        k32, calls = fake_k32(now, set_ok)
        with mock.patch.object(process, "K32", k32), \
                mock.patch.object(kitlib.platform, "NAME", platform):
            return process.raise_to_normal(), calls

    def test_below_normal_and_idle_are_raised_to_normal(self):
        for cls in (win32.BELOW_NORMAL_PRIORITY_CLASS, win32.IDLE_PRIORITY_CLASS):
            with self.subTest(cls=hex(cls)):
                self.assertEqual(self.run_with(cls),
                                 ("raised", [(ME, win32.NORMAL_PRIORITY_CLASS)]))

    def test_normal_and_above_are_never_changed(self):
        for cls in (win32.NORMAL_PRIORITY_CLASS, ABOVE_NORMAL, HIGH, REALTIME):
            with self.subTest(cls=hex(cls)):
                self.assertEqual(self.run_with(cls), ("unchanged", []))

    def test_a_class_that_cannot_be_read_or_set_is_failed(self):
        self.assertEqual(self.run_with(0), ("failed", []))
        self.assertEqual(self.run_with(win32.BELOW_NORMAL_PRIORITY_CLASS, set_ok=False),
                         ("failed", [(ME, win32.NORMAL_PRIORITY_CLASS)]))

    def test_off_windows_it_does_nothing(self):
        def never(*_a):
            raise AssertionError("kernel32 called off Windows")
        k32 = types.SimpleNamespace(GetCurrentProcess=never, GetPriorityClass=never,
                                    SetPriorityClass=never)
        for name in ("linux", "unsupported"):
            with self.subTest(platform=name), mock.patch.object(process, "K32", k32), \
                    mock.patch.object(kitlib.platform, "NAME", name):
                self.assertIsNone(process.raise_to_normal())


if __name__ == "__main__":
    unittest.main()
