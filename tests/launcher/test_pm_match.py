"""Finding the PM's process by its session id (reboot.resume_pm, relaunch.pm_running): the id must
be the --resume (-r) or --session-id value, as launch.py stop matches it (stop.runs_as). A topic
session whose brief path, in the PM's scratchpad folder, carries the PM's id is not the PM."""
import unittest

from kitlib.launcher import reboot, relaunch
from tests.launcher.fakes import FakeHost, Home, session, windows_text

SID = 'pm-sid-1'
PM = {'pm_session_id': SID, 'pm_account': 2}
# a topic session: its own name, and a brief in the PM's scratchpad (which holds the PM's id)
TOPIC = ("claude --agent pm-kit:topic --remote-control 'topic-x' -n 'topic-x' "
         "'Read Z:\\AgentTemp\\claude\\proj\\%s\\scratchpad\\topic-x-brief.md'" % SID)
TOPICS = (TOPIC,
          "claude --resume ‘other-sid’ --remote-control 'topic-x' -n 'topic-x' ‘see %s’" % SID,
          "claude --resume 'other-sid' --remote-control 'topic-x' -n 'topic-x' 'see %s'" % SID)
# the PM's real forms: the id as the value of --resume, -r or --session-id, bare or quoted
PM_FORMS = ("claude --resume %s --remote-control 'x' -n 'x'" % SID,
            "claude --resume=%s --remote-control 'x' -n 'x'" % SID,
            "claude -r %s --remote-control 'x' -n 'x'" % SID,
            "claude --session-id %s --remote-control 'x' -n 'x'" % SID,
            "claude --resume '%s' --remote-control 'x' -n 'x'" % SID,
            'claude --resume "%s" --remote-control \'x\' -n \'x\'' % SID,
            "claude --session-id='%s' --remote-control 'x' -n 'x'" % SID,
            "claude --resume ’%s’ --remote-control 'x' -n 'x'" % SID,
            "claude -r ‘%s‘ --remote-control 'x' -n 'x'" % SID)


def proc(pid, cmd):
    return dict(session(pid, 'x'), CommandLine=cmd)


class RebootResumePm(unittest.TestCase):
    def setUp(self):
        windows_text(self)
        self.h = Home(self)

    def resume(self, cmd):
        lines = []
        host = FakeHost(self.h.root, [proc(8, cmd)])
        reboot.resume_pm(self.h.settings, host, dict(PM, pm_folder=self.h.root), lines.append)
        return host, lines

    def test_a_topic_session_that_mentions_the_pm_id_does_not_skip_the_resume(self):
        for cmd in TOPICS:
            host, lines = self.resume(cmd)
            self.assertEqual(len(host.opened), 1, (cmd, lines))
            self.assertEqual(lines, ['started pm-session'], cmd)

    def test_the_pm_forms_still_skip_the_resume(self):
        for cmd in PM_FORMS:
            host, lines = self.resume(cmd)
            self.assertEqual(host.opened, [], cmd)
            self.assertEqual(lines, ['pm-session already running, skipped'], cmd)


class RelaunchPmRunning(unittest.TestCase):
    def test_a_topic_session_that_mentions_the_pm_id_is_not_the_pm(self):
        for cmd in TOPICS:
            self.assertFalse(relaunch.pm_running([proc(8, cmd)], [], SID), cmd)

    def test_the_pm_forms_still_match(self):
        for cmd in PM_FORMS:
            self.assertTrue(relaunch.pm_running([proc(8, cmd)], [], SID), cmd)

    def test_the_pm_name_and_a_registry_record_still_match(self):
        self.assertTrue(relaunch.pm_running([session(7, 'pm-session')], [], SID))
        self.assertTrue(relaunch.pm_running([], [{'sessionId': SID}], SID))


if __name__ == '__main__':
    unittest.main()
