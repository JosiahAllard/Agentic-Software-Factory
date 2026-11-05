"""The hub client is light: importing it does not import the hub server (kitlib.hubsite.serve), so a
plain client call does not pay for the server's imports; serve re-exports what moved to
kitlib.hubsite.localask, so every older importer of serve.ask and its errors reaches the same ones."""
import os
import sys
import unittest

from kitlib.hubsite import client, localask, serve
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
        self.assertFalse(_imported('kitlib.hubsite.client', 'kitlib.hubsite.serve'))

    def test_probe_sees_serve_when_it_is_imported(self):
        self.assertTrue(_imported('kitlib.hubsite.serve', 'kitlib.hubsite.serve'))


class ServeReExports(unittest.TestCase):

    def test_names_are_the_same_objects(self):
        for name in ('ask', 'ServeError', 'NotRunning', 'NoAnswer', 'ASK_TIMEOUT_S', 'MAX_ANSWER',
                     'VIA_CLI', 'VIA_MCP', 'USER_AGENT'):
            self.assertIs(getattr(serve, name), getattr(localask, name), name)

    def test_client_refusal_is_a_serve_error(self):
        self.assertTrue(issubclass(client.Refused, serve.ServeError))


if __name__ == '__main__':
    unittest.main()
