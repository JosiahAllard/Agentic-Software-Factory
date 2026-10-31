"""usage_crossover._pm_row: the PM's process by name, or by pm_session_id as its --resume (-r) or
--session-id value (launcher.stop.runs_as). A topic session whose brief path, in the PM's
scratchpad folder, carries the PM's id is not the PM."""
import unittest

from kitlib.hubjobs import usage_crossover as U

SID = 'pm-sid-1'
COORDS = {'pm_session_id': SID, 'pm_name': 'pm-fork-1'}
TOPICS = ("claude --agent pm-kit:topic --remote-control 'topic-x' -n 'topic-x' "
          "'Read Z:\\AgentTemp\\claude\\proj\\%s\\scratchpad\\topic-x-brief.md'" % SID,
          "claude --resume 'other-sid' --remote-control 'topic-x' -n 'topic-x' 'see %s'" % SID)
PM_FORMS = ("claude --resume %s --remote-control 'x' -n 'x'" % SID,
            "claude --resume=%s --remote-control 'x' -n 'x'" % SID,
            "claude -r %s --remote-control 'x' -n 'x'" % SID,
            "claude --session-id %s --remote-control 'x' -n 'x'" % SID,
            "claude --resume '%s' --remote-control 'x' -n 'x'" % SID,
            'claude --resume "%s" --remote-control \'x\' -n \'x\'' % SID)


class PmRow(unittest.TestCase):
    def test_a_topic_session_that_mentions_the_pm_id_is_not_the_pm(self):
        for cmd in TOPICS:
            self.assertFalse(U._pm_row('topic-x', {'CommandLine': cmd}, COORDS), cmd)

    def test_the_pm_forms_still_match(self):
        for cmd in PM_FORMS:
            self.assertTrue(U._pm_row('x', {'CommandLine': cmd}, COORDS), cmd)

    def test_the_pm_names_still_match_and_a_placeholder_id_never_does(self):
        self.assertTrue(U._pm_row('pm-session', {'CommandLine': 'claude'}, COORDS))
        self.assertTrue(U._pm_row('pm-fork-1', {'CommandLine': 'claude'}, COORDS))
        placeholder = {'pm_session_id': '<pm session id>'}
        self.assertFalse(U._pm_row('x', {'CommandLine': "claude --resume '<pm session id>'"}, placeholder))


if __name__ == '__main__':
    unittest.main()
