"""tools/verdict.py add reads the reviewer's final `Verdict:` line from its subagent transcript
(hosts.claude.adapter.agent_models.verdict, reached through ADAPTER.agent_verdict();
kitlib.gate.verdicts.seen_verdicts and reviewer_verdict_why) and refuses LAND over a SEND BACK,
on the scratch repository of tests/gate/fixtures.py GateRepoCase and transcripts planted under a
temp home: no real config folder is read."""
import json
import os
import unittest
from unittest import mock

from hosts.claude.adapter import agent_models as AM
from kitlib.gate import verdicts as V
from tests.gate.fixtures import GateRepoCase
from tests.gate import test_verdict_cli as VC
from tests.home import temp_home

RID = 'a13560c0143b859e7'           # the shape of every Claude subagent id
RID2 = 'a082f4af73b3c82f2'
MODEL = 'claude-opus-5-5'


def text_turn(text):
    return {'type': 'assistant', 'message': {'role': 'assistant', 'model': MODEL,
                                             'content': [{'type': 'text', 'text': text}]}}


def handback_turn(text):
    """The final report as Claude Code records it for a subagent: a SubagentHandback call."""
    return {'type': 'assistant', 'message': {'role': 'assistant', 'model': MODEL, 'content': [
        {'type': 'tool_use', 'id': 'toolu_1', 'name': 'SubagentHandback',
         'input': {'message': text}}]}}


def tool_turn():
    return {'type': 'assistant', 'message': {'role': 'assistant', 'model': MODEL, 'content': [
        {'type': 'tool_use', 'id': 'toolu_2', 'name': 'Read', 'input': {'file_path': 'x'}}]}}


def report(verdict):
    return 'Findings, most severe first:\n1. none blocking\n\nVerdict: %s' % verdict


class ReviewerVerdict(GateRepoCase):
    run_tool = VC.VerdictCli.run_tool
    add_args = VC.VerdictCli.add_args

    def setUp(self):
        VC.VerdictCli.setUp(self)
        self.home = temp_home(self, prefix='kit-reviewer-verdict-')
        env = mock.patch.dict(os.environ, {'CLAUDE_CONFIG_DIR': os.path.join(self.home, '.claude')})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop('PM_KIT_HOST', None)

    def plant(self, rid, *turns):
        d = os.path.join(self.home, '.claude', 'projects', 'Q--repo', 'sess-1', 'subagents')
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, 'agent-%s.jsonl' % rid), 'w', encoding='utf-8') as f:
            f.write(json.dumps({'type': 'user', 'message': {'role': 'user', 'content': 'review'}}) + '\n')
            for t in turns:
                f.write(json.dumps(t) + '\n')

    def add(self, **over):
        a = {'--risk': 'High', '--reviewer': 'pm-kit:analyst', '--reviewer-id': RID,
             '--reviewer-model': 'opus'}
        a.update(over)
        return self.run_tool(*self.add_args(**a))

    def assert_refused(self, **over):
        before = V.scan(self.repo.common_dir())
        code, out, err = self.add(**over)
        self.assertEqual(code, 2, (out, err))
        self.assertTrue(out.startswith('REFUSED: '), out)
        self.assertNotIn('QUEUED', out)
        self.assertEqual(V.scan(self.repo.common_dir()), before)
        return out

    def test_land_over_a_send_back_is_refused(self):
        """The observed gap: the coordinator queued LAND over its reviewer's SEND BACK."""
        self.plant(RID, tool_turn(), handback_turn(report('SEND BACK')))
        for verdict in ('LAND', 'LAND WITH FOLLOW-UP'):
            out = self.assert_refused(**{'--verdict': verdict})
            self.assertIn('the reviewer %s said SEND BACK (its final Verdict: line)' % RID, out)
            self.assertIn('\nInstead: dispatch a fresh review of the tip %s' % self.tip, out)
        self.assertEqual(V.scan(self.repo.common_dir()), ([], []))

    def test_land_is_queued_and_the_reviewer_verdict_printed(self):
        self.plant(RID, handback_turn(report('LAND')))
        code, out, err = self.add()
        self.assertEqual(code, 0, (out, err))
        self.assertEqual(out.strip().splitlines(),
                         ['reviewer %s said LAND at %s' % (RID, self.tip),
                          'QUEUED kit/a %s' % self.tip])

    def test_a_markdown_line_in_a_text_report_is_read(self):
        self.plant(RID, text_turn('Review.\n\n**Verdict:** land with follow-up\n'))
        code, out, err = self.add(**{'--verdict': 'LAND WITH FOLLOW-UP'})
        self.assertEqual(code, 0, (out, err))
        self.assertIn('reviewer %s said LAND WITH FOLLOW-UP at %s\n' % (RID, self.tip), out)

    def test_a_transcript_with_no_verdict_line_is_refused(self):
        self.plant(RID, handback_turn('Findings: none.'))
        out = self.assert_refused()
        self.assertIn('the reviewer %s gave no final Verdict: line' % RID, out)
        self.assertIn('\nInstead: resume the reviewer %s (SendMessage to its id), ask for its final '
                      'Verdict: line, then queue again' % RID, out)

    def test_only_the_final_turn_counts(self):
        # an earlier turn's line is not the final verdict ...
        self.plant(RID, handback_turn(report('SEND BACK')), text_turn('Re-read: fixed.\nVerdict: LAND'))
        self.assertEqual(self.add()[0], 0)
        # ... and a resumed reviewer appends its new final turn to the same transcript (inference)
        self.plant(RID, handback_turn(report('LAND')), tool_turn(), handback_turn('Still open.'))
        self.assertIn('gave no final Verdict: line', self.assert_refused())
        self.plant(RID, handback_turn('No line yet.'), handback_turn(report('SEND BACK')))
        self.assertIn('said SEND BACK', self.assert_refused())

    def test_one_of_two_reviewers_sending_back_is_refused(self):
        self.plant(RID, handback_turn(report('LAND')))
        self.plant(RID2, handback_turn(report('SEND BACK')))
        out = self.assert_refused(**{'--reviewer-id': '%s/%s' % (RID, RID2)})
        self.assertIn('the reviewer %s said SEND BACK' % RID2, out)
        self.assertNotIn('reviewer %s said' % RID, out)

    def test_no_transcript_queues_with_a_note(self):
        code, out, err = self.add()
        self.assertEqual(code, 0, (out, err))
        self.assertIn('NOTE: the reviewer\'s verdict is not checked: no transcript found for the '
                      'reviewer id %s' % RID, out)
        self.assertNotIn(' said ', out)

    def test_a_host_with_no_reader_skips_the_check(self):
        self.plant(RID, handback_turn(report('SEND BACK')))
        with mock.patch.dict(os.environ, {'PM_KIT_HOST': 'codex'}):
            self.assertIsNone(self.tool.verdict_reader())
            code, out, err = self.add()
            self.assertEqual((code, out.strip()), (0, 'QUEUED kit/a %s' % self.tip), err)
        self.assertIs(self.tool.verdict_reader(), AM.verdict)

    def test_a_self_review_or_a_session_name_is_not_looked_up(self):
        self.plant('a' + '0' * 16, handback_turn(report('SEND BACK')))
        code, out, _ = self.add(**{'--risk': 'Low', '--reviewer': 'pm-kit:coordinator',
                                   '--reviewer-id': 'a' + '0' * 16})
        self.assertEqual((code, out.strip()), (0, 'QUEUED kit/a %s' % self.tip))
        code, out, _ = self.add(**{'--reviewer-id': 'demo-coordinator-b7-r1'})
        self.assertEqual((code, out.strip()), (0, 'QUEUED kit/a %s' % self.tip))


class ReadVerdict(unittest.TestCase):
    def test_the_last_anchored_line_of_the_final_report(self):
        cases = [
            ('Verdict: LAND', 'LAND'),
            ('Verdict: LAND WITH FOLLOW-UP', 'LAND WITH FOLLOW-UP'),
            ('verdict: send back - two defects', 'SEND BACK'),
            ('- **Verdict**: LAND', 'LAND'),
            ('Verdict: SEND BACK\nlater text\nVerdict: LAND', 'LAND'),
            ('The verdict: LAND is wrong here', None),      # not anchored at the line start
            ('Verdict: maybe', None),
            ('Verdict: LANDED', None),
            ('', None),
        ]
        for text, want in cases:
            self.assertEqual(AM.report_verdict(text), want, text)


if __name__ == '__main__':
    unittest.main()
