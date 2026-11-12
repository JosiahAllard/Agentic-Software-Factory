"""tools/verdict.py add reads the reviewer's final `Verdict:` line from its subagent transcript
(hosts.claude.adapter.agent_models.verdict, reached through ADAPTER.agent_verdict();
kitlib.landing.verdicts.seen_verdicts and reviewer_verdict_why) and refuses LAND over a SEND BACK,
on the scratch repository of tests/gate/fixtures.py GateRepoCase and transcripts planted under a
temp home: no real config folder is read."""
import json
import os
import re
import unittest
from unittest import mock

from hosts.claude.adapter import agent_models as AM
from kitlib.backlog import record as RECORD
from kitlib.landing import verdicts as V
from kitlib.records import verdict_entry as VE
from tests.gate.fixtures import GateRepoCase
from tests.landing import test_verdict_cli as VC
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


CHECKS = 'Checks: ' + ', '.join('%s PASS' % c for c in V.CODE_CHECKS)
DESIGN = 'Checks: ' + ', '.join('%s PASS' % c for c in V.DESIGN_CHECKS) + '\nDesign: APPROVE'


def report(verdict, extra=''):
    return ('Findings, most severe first:\n1. none blocking\n%s\n%s\nVerdict: %s'
            % (extra, CHECKS, verdict))


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
        self.plant(RID, text_turn('Review.\n\n%s\n**Verdict:** land with follow-up\n' % CHECKS))
        with self.backlog({'EX-9': 'open'}):
            code, out, err = self.add(**{'--verdict': 'LAND WITH FOLLOW-UP', '--follow-up': 'EX-9'})
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
        self.plant(RID, handback_turn(report('SEND BACK')),
                   text_turn('Re-read: fixed.\n%s\nVerdict: LAND' % CHECKS))
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

    def test_a_transcript_that_cannot_be_read_is_refused(self):
        """Fail closed: a transcript found but unreadable may be the newest, so it is not skipped.
        A folder where the transcript file should be makes open() raise an OSError."""
        d = os.path.join(self.home, '.claude', 'projects', 'Q--repo', 'sess-1', 'subagents')
        os.makedirs(os.path.join(d, 'agent-%s.jsonl' % RID))
        self.assertEqual(AM.verdict(RID, home=self.home), (True, 'unread', None))
        out = self.assert_refused()
        self.assertIn('a transcript of the reviewer %s was found but cannot be read' % RID, out)
        self.assertIn('\nInstead: run verdict.py add again once the transcript can be read, or resume '
                      'the reviewer %s (SendMessage to its id) and ask for its final Verdict: line '
                      'again' % RID, out)
        self.assertNotIn(' said ', out)

    def test_a_transcript_whose_time_cannot_be_read_is_refused(self):
        self.plant(RID, handback_turn(report('LAND')))
        with mock.patch.object(AM.os.path, 'getmtime', side_effect=OSError('denied')):
            self.assertEqual(AM.verdict(RID, home=self.home), (True, 'unread', None))

    def test_the_newest_transcript_wins(self):
        """Two config folders (two accounts) both hold a transcript of the id: the one last
        written gives the verdict, whichever folder is listed first."""
        paths = []
        for cfg in ('.claude', '.claude-account1'):
            d = os.path.join(self.home, cfg, 'projects', 'Q--repo', 'sess-1', 'subagents')
            os.makedirs(d, exist_ok=True)
            paths.append(os.path.join(d, 'agent-%s.jsonl' % RID))

        def write(path, verdict, when):
            with open(path, 'w', encoding='utf-8') as f:
                f.write(json.dumps(handback_turn(report(verdict))) + '\n')
            os.utime(path, (when, when))

        first, second = paths          # CLAUDE_CONFIG_DIR's folder is listed first
        for old, new in ((first, second), (second, first)):
            write(old, 'LAND', 1700000000)
            write(new, 'SEND BACK', 1700000600)
            self.assertEqual(AM.verdict(RID, home=self.home)[:2], (True, 'SEND BACK'), new)
            write(old, 'SEND BACK', 1700000000)
            write(new, 'LAND', 1700000600)
            self.assertEqual(AM.verdict(RID, home=self.home), (True, 'LAND', report('LAND')), new)

    def test_a_self_review_or_a_session_name_is_not_looked_up(self):
        self.plant('a' + '0' * 16, handback_turn(report('SEND BACK')))
        code, out, _ = self.add(**{'--risk': 'Low', '--reviewer': 'pm-kit:coordinator',
                                   '--reviewer-id': 'a' + '0' * 16})
        self.assertEqual((code, out.strip()), (0, 'QUEUED kit/a %s' % self.tip))
        code, out, _ = self.add(**{'--reviewer-id': 'demo-coordinator-b7-r1'})
        self.assertEqual((code, out.strip()), (0, 'QUEUED kit/a %s' % self.tip))

    def backlog(self, states):
        """verdict.py's backlog store read, as `states` ({id: state}; an id missing: no record)."""
        return mock.patch.object(self.tool, 'follow_up_states',
                                 side_effect=lambda root, ids: {
                                     i: (states.get(i), states.get(i) in RECORD.STATES)
                                     for i in ids})

    def test_a_report_with_no_full_checks_line_is_refused(self):
        self.plant(RID, handback_turn('Findings: none.\nVerdict: LAND'))
        out = self.assert_refused()
        self.assertIn('the reviewer %s gave no Checks: line (its final report)' % RID, out)
        self.assertIn('\nInstead: resume the reviewer %s (SendMessage to its id) and ask it to give '
                      'its full final lines again: each Done-when: line, the Checks: line' % RID,
                      out)
        self.assertIn('and the Verdict: line', out)
        self.plant(RID, handback_turn(report('LAND').replace(', copies PASS', '')))
        self.assertIn("the reviewer %s's Checks: line lacks copies" % RID, self.assert_refused())

    def test_a_plain_land_over_an_unmet_done_when_is_refused(self):
        unmet = 'Done-when: EX-1 (c) UNMET - no test names it'
        self.plant(RID, handback_turn(report('LAND WITH FOLLOW-UP', unmet)))
        out = self.assert_refused(**{'--verdict': 'LAND'})
        self.assertIn('--verdict LAND, but the reviewer %s reports %s' % (RID, unmet), out)
        self.assertIn('\nInstead: queue it with --verdict "LAND WITH FOLLOW-UP" --follow-up <ID>', out)
        with self.backlog({'EX-9': 'pulled'}):
            code, out, err = self.add(**{'--verdict': 'LAND WITH FOLLOW-UP', '--follow-up': 'EX-9'})
        self.assertEqual(code, 0, (out, err))

    def test_land_with_follow_up_needs_an_open_follow_up(self):
        """The CLI wiring; verdicts.follow_up_why's cases are test_verdicts'."""
        self.plant(RID, handback_turn(report('LAND WITH FOLLOW-UP')))
        with self.backlog({'EX-9': 'open'}):
            out = self.assert_refused(**{'--verdict': 'LAND WITH FOLLOW-UP',
                                         '--follow-up': 'EX-CLOSED'})
        self.assertIn('--follow-up EX-CLOSED is no open record in the store', out)

    def test_a_store_that_cannot_be_read_is_not_done(self):
        """opener.follow_up_states's `why` is NOT DONE (exit 1), not a refusal."""
        self.plant(RID, handback_turn(report('LAND WITH FOLLOW-UP')))
        with mock.patch('kitlib.backlog.opener.follow_up_states',
                        return_value=(None, 'the PM data worktree cannot be read')) as got:
            code, out, err = self.add(**{'--verdict': 'LAND WITH FOLLOW-UP', '--follow-up': 'EX-9'})
        self.assertEqual(code, 1, (out, err))
        self.assertIn('NOT DONE', out + err)
        self.assertIn('the backlog store cannot be read for --follow-up: the PM data worktree '
                      'cannot be read', out + err)
        self.assertEqual(got.call_args[0][1], ['EX-9'])
        self.assertEqual(self.tool.follow_up_states('/r', []), {})

    def new_class_branch(self):
        wt, tip = self.branch('b', {'pkg/mod.py': 'class Fresh:\n    pass\n',
                                    'tests/test_mod.py': 'class TestFresh:\n    pass\n'})
        return wt, tip, {'--branch': 'kit/b', '--tip': tip,
                         '--range': '%s..%s' % (self.main[:10], tip[:10])}

    def test_a_new_source_needs_an_approved_design_review(self):
        wt, tip, over = self.new_class_branch()
        self.plant(RID, handback_turn(report('LAND')))
        out = self.assert_refused(**over)
        self.assertIn('the diff adds pkg/mod.py, pkg/mod.py:Fresh, and no --design-reviewer-id', out)
        self.assertTrue(out.strip().endswith(V.DESIGN_INSTEAD), out)
        over['--design-reviewer-id'] = RID2
        self.plant(RID2, handback_turn(DESIGN.replace('APPROVE', 'REVISE')))
        self.assertIn('the design reviewer %s said REVISE' % RID2, self.assert_refused(**over))
        self.plant(RID2, handback_turn(DESIGN))
        code, out, err = self.add(**over)
        self.assertEqual((code, out.strip().splitlines()[-1]), (0, 'QUEUED kit/b %s' % tip), err)
        # a class added to a file that already exists needs one too: this range adds no file
        self.repo.write('pkg/mod.py', 'class Fresh:\n    pass\n\n\nclass Second:\n    pass\n',
                        root=wt)
        tip2 = self.repo.commit('second class', 'pkg/mod.py', cwd=wt)
        over = {'--branch': 'kit/b', '--tip': tip2, '--range': '%s..%s' % (tip[:10], tip2[:10])}
        out = self.assert_refused(**over)
        self.assertIn('the diff adds pkg/mod.py:Second, and no --design-reviewer-id', out)
        self.assertTrue(out.strip().endswith(V.DESIGN_INSTEAD), out)
        over['--design-reviewer-id'] = RID2
        code, out, err = self.add(**over)
        self.assertEqual((code, out.strip().splitlines()[-1]), (0, 'QUEUED kit/b %s' % tip2), err)

    def test_a_design_review_with_no_transcript_or_reader_queues_with_a_note(self):
        _, tip, over = self.new_class_branch()
        over['--design-reviewer-id'] = RID2
        code, out, err = self.add(**over)
        self.assertEqual(code, 0, (out, err))
        self.assertIn('NOTE: the design review is not checked: no transcript found for the design '
                      'reviewer id %s' % RID2, out)
        with mock.patch.dict(os.environ, {'PM_KIT_HOST': 'codex'}):
            code, out, err = self.add(**over)
            self.assertEqual(code, 0, (out, err))
            self.assertIn('NOTE: the design review is not checked: this host has no transcript '
                          'reader', out)
            del over['--design-reviewer-id']
            self.assertIn('no --design-reviewer-id', self.assert_refused(**over))

    def test_new_sources_leaves_out_tests_renames_and_moved_classes(self):
        body = 'class Fresh:\n' + ''.join('    x%d = %d\n' % (i, i) for i in range(12))
        wt, t1 = self.branch('c', {'pkg/mod.py': body, 'notes.txt': 'x\n'})
        self.assertEqual(self.tool.new_sources(self.repo.root, '%s..%s' % (self.main, t1)),
                         ['pkg/mod.py', 'pkg/mod.py:Fresh'])
        os.makedirs(os.path.join(wt, 'lib'))
        self.repo.git('mv', 'pkg/mod.py', 'lib/mod.py', cwd=wt)
        self.repo.write('lib/mod.py', body + '\n\nclass Second:\n    pass\n', root=wt)
        t2 = self.repo.commit('move', 'lib/mod.py', cwd=wt)
        self.assertEqual(self.tool.new_sources(self.repo.root, '%s..%s' % (t1, t2)),
                         ['lib/mod.py:Second'])
        # diff.noprefix changes nothing (the prefixes are given), and a path holding a space,
        # which git ends with a tab on its ---/+++ lines, is read whole
        self.repo.git('config', 'diff.noprefix', 'true')
        self.repo.write('lib/my mod.py', 'class Spaced:\n    pass\n', root=wt)
        t3 = self.repo.commit('spaced', 'lib/my mod.py', cwd=wt)
        self.assertEqual(self.tool.new_sources(self.repo.root, '%s..%s' % (t2, t3)),
                         ['lib/my mod.py', 'lib/my mod.py:Spaced'])
        with self.assertRaises(VE.VerdictError):
            self.tool.new_sources(self.repo.root, 'nosuch..%s' % t2)

    def test_new_sources_leaves_out_what_a_sync_merge_brought_from_main(self):
        """The observed refusal: a range from the pre-sync tip counted main's files as added."""
        wt, t1 = self.branch('s', {'pkg/own.py': 'class Own:\n    pass\n'})
        self.repo.write('pkg/from_main.py', 'class FromMain:\n    pass\n')
        landed = self.repo.commit('main moves', 'pkg/from_main.py')
        self.repo.git('push', '-q', 'origin', 'main')
        self.repo.git('merge', '-q', '--no-edit', landed, cwd=wt)
        t2 = self.repo.git('rev-parse', 'HEAD', cwd=wt).strip()
        rng = '%s..%s' % (t1, t2)
        self.assertEqual(self.tool.new_sources(self.repo.root, rng), [])
        # the branch's own file is still new
        self.assertEqual(self.tool.new_sources(self.repo.root, '%s..%s' % (self.main, t2)),
                         ['pkg/own.py', 'pkg/own.py:Own'])
        # main unreadable keeps today's answer: nothing is loosened
        with mock.patch.object(self.tool, 'main_as_fetched', return_value=(None, 'no ref')):
            self.assertEqual(self.tool.new_sources(self.repo.root, rng),
                             ['pkg/from_main.py', 'pkg/from_main.py:FromMain'])
        # a class main adds to a file that already exists is left out the same way
        self.repo.write('pkg/from_main.py', 'class FromMain:\n    pass\n\n\nclass Later:\n    pass\n')
        landed = self.repo.commit('main adds a class', 'pkg/from_main.py')
        self.repo.git('push', '-q', 'origin', 'main')
        self.repo.git('merge', '-q', '--no-edit', landed, cwd=wt)
        t3 = self.repo.git('rev-parse', 'HEAD', cwd=wt).strip()
        rng = '%s..%s' % (t2, t3)
        self.assertEqual(self.tool.new_sources(self.repo.root, rng), [])
        with mock.patch.object(self.tool, 'main_as_fetched', return_value=(None, 'no ref')):
            self.assertEqual(self.tool.new_sources(self.repo.root, rng), ['pkg/from_main.py:Later'])


class SkillIds(unittest.TestCase):
    """The ids skill specialty-review gives are the ones verdict.py add checks."""

    def test_the_skill_lists_both_constants_in_order(self):
        from kitlib.gate import steps
        path = os.path.join(os.path.dirname(steps.KIT_TOOLS), 'content', 'skills',
                            'specialty-review', 'SKILL.md')
        with open(path, encoding='utf-8') as f:
            text = f.read()
        design, code = text.split('## Code review', 1)
        ids = lambda part: re.findall(r'^\d+\. \*\*[^*]+\*\* \(`([a-z-]+)`\)', part, re.M)
        self.assertEqual(tuple(ids(design)), V.DESIGN_CHECKS)
        listed = ids(code) + list(re.findall(r'as `(\w+)`, `(\w+)`, `(\w+)` and `(\w+)`', code)[0])
        self.assertEqual(sorted(listed), sorted(V.CODE_CHECKS))
        for part, want in ((design, V.DESIGN_CHECKS), (text.split('## Report', 1)[1], V.CODE_CHECKS)):
            line = re.search(r'`(Checks: [^`]+)`', part).group(1)     # the line template
            self.assertEqual(tuple(V.checks_of(line)), want)


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
            ('`Verdict: SEND BACK`', 'SEND BACK'),          # backticks around the line ...
            ('Verdict: `LAND`', 'LAND'),                    # ... or around the verdict
            ('**Verdict:** `LAND WITH FOLLOW-UP`', 'LAND WITH FOLLOW-UP'),
            ('`Verdict`: send back', 'SEND BACK'),
            ('Verdict: LAND or SEND BACK', None),            # two verdicts named: neither is read
            ('Verdict: `LAND` / `SEND BACK`', None),
            ('Verdict: LAND\nVerdict: LAND or SEND BACK', None),   # an earlier line never stands in
            ('Verdict: SEND BACK\nVerdict: pending', None),
            ('', None),
        ]
        for text, want in cases:
            self.assertEqual(AM.report_verdict(text), want, text)


if __name__ == '__main__':
    unittest.main()
