"""hosts.claude.adapter.claude_host's credentials reads: login_of and LOGIN (kitlib.launcher.accounts
reaches them through kitlib.host.registry.host_module)."""
import unittest

from hosts.claude.adapter import claude_host as H

LATER_MS = 2000086400000


def oauth(**fields):
    login = {'accessToken': 'x', 'refreshToken': 'y', 'expiresAt': 1, 'refreshTokenExpiresAt': LATER_MS}
    login.update(fields)
    return {'claudeAiOauth': login}


class LoginOf(unittest.TestCase):
    def test_no_login_object_is_none(self):
        for data in (None, [], {}, {'mcpOAuth': {}}, {'claudeAiOauth': 'x'}):
            self.assertIsNone(H.login_of(data), data)

    def test_a_live_login_is_held_with_its_end_as_stored_even_with_its_access_token_expired(self):
        self.assertEqual(H.login_of(oauth()), (True, LATER_MS))
        self.assertEqual(H.login_of(oauth(refreshTokenExpiresAt=None)), (True, None))

    def test_the_file_written_at_the_time_limit_and_login_ended_are_not_held(self):
        for fields in ({'refreshToken': ''}, {'expiresAt': 0}, {'loginEnded': True},
                       {'refreshToken': '', 'expiresAt': 0}):
            self.assertEqual(H.login_of(oauth(**fields))[0], False, fields)

    def test_the_sign_in_command(self):
        self.assertEqual(H.LOGIN, 'claude auth login')


if __name__ == '__main__':
    unittest.main()
