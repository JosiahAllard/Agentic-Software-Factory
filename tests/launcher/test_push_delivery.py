"""kitlib.launcher.pushdelivery, and the launch.py entry's use of it."""
import io
import types
import unittest
from unittest import mock

from kitlib.launcher import pushdelivery, stop


class Settings:
    def state_dir(self):
        return 'state'


def adapter_with(push):
    return lambda: types.SimpleNamespace(ADAPTER=types.SimpleNamespace(**({'push': push} if push else {})))


def raising(err):
    def adapter_module():
        raise err
    return adapter_module


class PushDelivery(unittest.TestCase):
    def test_the_adapters_push_and_the_state_folder(self):
        got = pushdelivery.push_delivery(adapter_with(lambda s: ('delivery', s)), Settings())
        self.assertEqual((got[0][0], got[1]), ('delivery', 'state'))

    def test_an_adapter_without_push_is_none_and_says_so(self):
        notes = []
        self.assertEqual(pushdelivery.push_delivery(adapter_with(None), Settings(), notes.append), (None, None))
        self.assertEqual(notes, ['push notifications off: this host has no delivery'])

    def test_any_fault_is_none_with_its_reason(self):
        notes = []
        for err in (ImportError('no adapter'), RuntimeError('broken')):
            self.assertEqual(pushdelivery.push_delivery(raising(err), Settings(), notes.append), (None, None))
        self.assertEqual(notes, ['push notifications off: ImportError: no adapter',
                                 'push notifications off: RuntimeError: broken'])


class LaunchEntry(unittest.TestCase):
    def test_launch_uses_the_library_and_drops_a_broken_adapter(self):
        import sys
        from tools import launch
        with mock.patch.dict(sys.modules, {'_host': types.SimpleNamespace(adapter_module=raising(RuntimeError('x')))}):
            self.assertIsNone(launch.push_delivery(Settings()))

    def test_a_refusal_goes_to_stderr(self):
        from tools import launch
        err, out = io.StringIO(), io.StringIO()
        with mock.patch.object(launch, 'run', side_effect=stop.Refused('no way')), \
                mock.patch.object(launch.S, 'load', return_value=None), \
                mock.patch('sys.stderr', err), mock.patch('sys.stdout', out):
            self.assertEqual(launch.main(['usage'], host=types.SimpleNamespace(home='h')), 2)
        self.assertIn('REFUSED: no way', err.getvalue())
        self.assertEqual(out.getvalue(), '')


if __name__ == '__main__':
    unittest.main()
