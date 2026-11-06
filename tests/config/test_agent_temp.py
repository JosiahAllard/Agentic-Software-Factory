"""kitlib.config.user.agent_temp: the agents' temp folder, AGENT_TEMP, else TEMP, else the home
folder; an empty value counts as unset."""
import os
import unittest
from unittest import mock

from kitlib.config import user


class AgentTempTest(unittest.TestCase):
    def test_agent_temp_wins_over_temp(self):
        self.assertEqual(user.agent_temp({'AGENT_TEMP': 'a', 'TEMP': 't'}.get, 'h'), 'a')

    def test_temp_when_agent_temp_is_unset_or_empty(self):
        self.assertEqual(user.agent_temp({'TEMP': 't'}.get, 'h'), 't')
        self.assertEqual(user.agent_temp({'AGENT_TEMP': '', 'TEMP': 't'}.get, 'h'), 't')

    def test_tmp_is_not_read(self):
        self.assertEqual(user.agent_temp({'TMP': 'x'}.get, 'h'), 'h')

    def test_home_argument_when_none_is_set(self):
        self.assertEqual(user.agent_temp({}.get, 'h'), 'h')

    def test_default_home_is_the_users(self):
        self.assertEqual(user.agent_temp({}.get), os.path.expanduser('~'))

    def test_default_reads_this_process_environment(self):
        with mock.patch.dict(os.environ, {'AGENT_TEMP': 'proc-agent'}):
            self.assertEqual(user.agent_temp(), 'proc-agent')


if __name__ == '__main__':
    unittest.main()
