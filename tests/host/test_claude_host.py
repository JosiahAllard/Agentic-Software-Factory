"""hosts.claude.adapter.claude_host's credentials reads: login_of and LOGIN (kitlib.launcher.accounts
reaches them through kitlib.host.registry.host_module); its transcript lookup by session name."""
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

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


def title(name):
    return json.dumps({'type': 'custom-title', 'customTitle': name}) + '\n'


FILLER = json.dumps({'type': 'user', 'text': 'x' * 40}) + '\n'


class SessionTitles(unittest.TestCase):
    """A main transcript in a temporary project folder; the title cache cleared around each test."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        os.makedirs(os.path.join(self.root, 'proj'))
        self.path = os.path.normcase(os.path.realpath(os.path.join(self.root, 'proj', 'sid.jsonl')))
        patch = mock.patch.dict(H._TITLES, clear=True)
        patch.start()
        self.addCleanup(patch.stop)

    def write(self, text, mode='a'):
        with open(self.path, mode, encoding='utf-8', newline='\n') as f:
            f.write(text)

    def test_a_resumed_renamed_transcript_is_found_under_each_name_it_recorded(self):
        self.write(title('pm-a1') + FILLER * 5 + title('pm-session') + FILLER + title('Project Manager'))
        os.utime(self.path, (1000, 1000))
        for name in ('pm-a1', 'pm-session', 'Project Manager'):
            self.assertEqual(H.newest_transcript(name, [self.root]), 1000, name)
        self.assertIsNone(H.newest_transcript('pm-a2', [self.root]))

    def test_a_title_split_across_reads_and_one_reads_before_the_end_are_found(self):
        self.write(FILLER + title('far') + FILLER * 20 + title('split') + FILLER * 20)
        with mock.patch.object(H, 'CHUNK', len(FILLER) + 7):     # cuts every line, 'far' many reads back
            self.assertEqual(H.session_titles(self.path), {'far', 'split'})

    def test_a_line_not_yet_ended_counts_once_it_is_and_an_appended_title_is_seen(self):
        line = title('late')
        self.write(title('first') + line[:10])
        self.assertEqual(H.session_titles(self.path), {'first'})
        self.write(line[10:])
        self.assertEqual(H.session_titles(self.path), {'first', 'late'})
        self.write(FILLER + title('renamed'))
        self.assertEqual(H.session_titles(self.path), {'first', 'late', 'renamed'})

    def test_a_shrunk_file_is_read_again_from_its_start(self):
        self.write(title('old') + FILLER * 3)
        self.assertEqual(H.session_titles(self.path), {'old'})
        self.write(title('new'), mode='w')
        self.assertEqual(H.session_titles(self.path), {'new'})

    def test_an_unreadable_file_has_no_titles_and_caches_nothing(self):
        self.assertEqual(H.session_titles(self.path), frozenset())
        self.assertNotIn(self.path, H._TITLES)


if __name__ == '__main__':
    unittest.main()
