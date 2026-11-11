"""The hub client is light: importing it does not import the hub server (kitlib/hubsite/serve.py),
so a plain client call does not pay for the server's imports."""
import os
import sys
import unittest

from kitlib.proc import spawn

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _imported(module, probe):
    """Whether `probe` is in sys.modules after a fresh interpreter imports `module`."""
    code = 'import sys; import %s; print(%r in sys.modules)' % (module, probe)
    out = spawn.run([sys.executable, '-c', code], cwd=ROOT, capture_output=True,
                    text=True, timeout=120, env=dict(os.environ, PYTHONPATH=ROOT))
    assert out.returncode == 0, out.stderr
    return out.stdout.strip() == 'True'


class ClientIsLight(unittest.TestCase):

    def test_client_does_not_import_serve(self):
        self.assertFalse(_imported('kitlib.hubclient.client', 'kitlib.hubsite.serve'))

    def test_no_hubclient_module_imports_the_server(self):
        for name in ('client', 'codestale', 'errors', 'localask', 'push', 'settings', 'token'):
            self.assertFalse(_imported('kitlib.hubclient.' + name, 'kitlib.hubsite'), name)

    def test_probe_sees_serve_when_it_is_imported(self):
        self.assertTrue(_imported('kitlib.hubsite.serve', 'kitlib.hubsite.serve'))


if __name__ == '__main__':
    unittest.main()
