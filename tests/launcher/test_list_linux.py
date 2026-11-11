"""kitlib.launcher.lookup on Linux: session_images is the running
platform's process_names over every host. kitlib.platform.NAME is patched to 'linux', so this runs
on Windows."""
import unittest
from unittest import mock

from kitlib.launcher import lookup

ROW = {'Name': 'claude', 'CommandLine': 'claude -n x', 'ExecutablePath': '/usr/bin/claude',
       'ProcessId': 7, 'ParentProcessId': 1, 'Created': '2026-10-27T10:00:00+00:00'}


class Linux(unittest.TestCase):
    def setUp(self):
        patch = mock.patch('kitlib.platform.NAME', 'linux')
        patch.start()
        self.addCleanup(patch.stop)

    def test_live_session_names_skip_a_node_that_runs_no_hosts_cli(self):
        rows = [dict(ROW, Name='node', CommandLine='node app.js --remote-control X'),
                dict(ROW, Name='node', CommandLine='node /srv/codex/app.js --remote-control V'),
                dict(ROW, Name='node', CommandLine='node /usr/local/bin/claude --remote-control Y'),
                dict(ROW, CommandLine='claude --remote-control Z'),
                dict(ROW, Name='node', CommandLine='node /usr/lib/node_modules/@openai/codex/bin/codex.js '
                                                   '--remote-control W')]
        self.assertEqual(lookup.live_session_names(rows), {'Y', 'Z', 'W'})

    def test_session_images_are_the_linux_process_names(self):
        self.assertEqual(lookup.session_images(), frozenset({'claude', 'node', 'codex'}))


class Windows(unittest.TestCase):
    def test_session_images_on_windows_are_the_session_processes(self):
        with mock.patch('kitlib.platform.NAME', 'windows'):
            self.assertEqual(lookup.session_images(), frozenset({'claude.exe', 'codex.exe'}))


if __name__ == '__main__':
    unittest.main()
