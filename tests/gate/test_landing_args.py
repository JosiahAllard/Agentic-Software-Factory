"""kitlib.gate.submit's one copy of the submitted landing's arguments and restore path, shared by
`gate.py land` and tools/land.py: again_args (the Instead: command), child_args (the detached
landing's) and restore_landing; and kitlib.gate.measure.write_proposal (`gate.py --measure`).

The expected lists are what the tools' own copies (tools/gate.py land_again_args and
land_child_args, tools/land.py again_args and child_args) gave before they moved here."""
import json
import os
import shutil
import tempfile
import types
import unittest
from unittest import mock

from kitlib import gate as G
from kitlib.gate import measure, submit

ROOT = 'Z:\\repo\\main'
FWD = 'Z:/repo/main'
TIP, TIP2, MAIN = 'c' * 40, 'd' * 40, 'b' * 40


def ns(**over):
    a = dict(expect_main=None, summary=None, wait=None, log_dir=None, fresh=False,
             keep_branch=False, wait_result=False)
    a.update(over)
    return types.SimpleNamespace(**a)


EVERYTHING = dict(expect_main=MAIN, summary='a title', wait=30, log_dir='logs', fresh=True,
                  keep_branch=True)


class GateLandArgs(unittest.TestCase):
    """gate.py land: `land BRANCH -C ROOT --expect-tip SHA`, then --expect-main and the rest."""

    def test_again_args_bare(self):
        self.assertEqual(submit.again_args(submit.GATE_TOOL, ns(), ROOT, ['kit/x'], [TIP]),
                         ['land', 'kit/x', '-C', FWD, '--expect-tip', TIP])

    def test_again_args_everything_keeps_log_dir_as_given(self):
        self.assertEqual(
            submit.again_args(submit.GATE_TOOL, ns(**EVERYTHING), ROOT, ['kit/x'], [TIP]),
            ['land', 'kit/x', '-C', FWD, '--expect-tip', TIP, '--expect-main', MAIN,
             '--summary', 'a title', '--wait', '30', '--log-dir', 'logs',
             '--fresh', '--keep-branch'])

    def test_child_args_everything_root_and_log_dir_absolute(self):
        self.assertEqual(
            submit.child_args(submit.GATE_TOOL, ns(**EVERYTHING), ROOT, ['kit/x'], [TIP]),
            ['land', 'kit/x', '-C', ROOT, '--expect-tip', TIP, '--expect-main', MAIN,
             '--summary', 'a title', '--wait', '30', '--log-dir', os.path.abspath('logs'),
             '--fresh', '--keep-branch'])

    def test_wait_zero_is_written(self):
        self.assertEqual(submit.child_args(submit.GATE_TOOL, ns(wait=0), ROOT, ['kit/x'], [TIP]),
                         ['land', 'kit/x', '-C', ROOT, '--expect-tip', TIP, '--wait', '0'])


class LandToolArgs(unittest.TestCase):
    """land.py: -C first; again keeps the permission rule's order and --wait-result, the child
    puts --expect-main before the tips and the branches last."""

    def test_again_args_everything(self):
        a = ns(wait_result=True, **EVERYTHING)
        self.assertEqual(
            submit.again_args(submit.LAND_TOOL, a, ROOT, ['kit/x', 'kit/y'], [TIP, TIP2]),
            ['-C', FWD, 'kit/x', 'kit/y', '--expect-tip', TIP, '--expect-tip', TIP2,
             '--expect-main', MAIN, '--summary', 'a title', '--wait', '30', '--log-dir', 'logs',
             '--fresh', '--keep-branch', '--wait-result'])

    def test_child_args_everything_never_wait_result(self):
        a = ns(wait_result=True, **EVERYTHING)
        self.assertEqual(
            submit.child_args(submit.LAND_TOOL, a, ROOT, ['kit/x', 'kit/y'], [TIP, TIP2]),
            ['-C', ROOT, '--expect-main', MAIN, '--expect-tip', TIP, '--expect-tip', TIP2,
             '--summary', 'a title', '--wait', '30', '--log-dir', os.path.abspath('logs'),
             '--fresh', '--keep-branch', 'kit/x', 'kit/y'])

    def test_bare(self):
        self.assertEqual(submit.again_args(submit.LAND_TOOL, ns(), ROOT, ['kit/x'], [TIP]),
                         ['-C', FWD, 'kit/x', '--expect-tip', TIP])
        self.assertEqual(submit.child_args(submit.LAND_TOOL, ns(), ROOT, ['kit/x'], [TIP]),
                         ['-C', ROOT, '--expect-tip', TIP, 'kit/x'])

    def test_the_flags_a_fold_reads_are_the_same_from_either_tool(self):
        """flags_of reads the child's arguments in any order: both tools' give the same flags."""
        a = ns(**EVERYTHING)
        self.assertEqual(
            submit.flags_of(submit.child_args(submit.GATE_TOOL, a, ROOT, ['kit/x'], [TIP])),
            submit.flags_of(submit.child_args(submit.LAND_TOOL, a, ROOT, ['kit/x'], [TIP])))


class RestoreLanding(unittest.TestCase):
    """restore_landing: what each tool says and exits with on its --submitted run."""

    def test_an_untrusted_payload_is_one_refusal_line_for_stderr(self):
        with mock.patch.object(submit, 'restore', side_effect=submit.Untrusted('forged')), \
                mock.patch.object(submit, 'abandon') as abandon:
            sub, out = submit.restore_landing('R', 'p.land.submit', ['kit/x'], [TIP])
        self.assertIsNone(sub)
        self.assertEqual((out.code, out.facts.get('stderr')), (G.REFUSED, True))
        self.assertEqual(out.lines, [submit.untrusted_line('p.land.submit', 'forged')])
        abandon.assert_not_called()

    def test_an_unreadable_payload_is_abandoned(self):
        gave = G.Outcome(G.REFUSED, ['REFUSED: bad; nothing was changed'])
        with mock.patch.object(submit, 'restore', side_effect=submit.SubmitError('bad')), \
                mock.patch.object(submit, 'abandon', return_value=gave) as abandon:
            sub, out = submit.restore_landing('R', 'p', ['kit/x'], [TIP], expect_main=MAIN)
        self.assertIsNone(sub)
        self.assertIs(out, gave)
        abandon.assert_called_once_with('R', 'p', 'bad')

    def test_restored_redirects_its_output(self):
        with mock.patch.object(submit, 'restore', return_value={'output': 'o.log'}) as restore, \
                mock.patch.object(submit, 'redirect_output') as redirect:
            sub, out = submit.restore_landing('R', 'p', ['kit/x'], [TIP], expect_main=MAIN)
        restore.assert_called_once_with('R', 'p', ['kit/x'], [TIP], expect_main=MAIN)
        redirect.assert_called_once_with('o.log')
        self.assertEqual((sub, out.code, out.lines), ({'output': 'o.log'}, G.OK, []))

    def test_an_output_that_cannot_be_opened_is_a_warning(self):
        with mock.patch.object(submit, 'restore', return_value={'output': 'o.log'}), \
                mock.patch.object(submit, 'redirect_output', side_effect=OSError('denied')):
            sub, out = submit.restore_landing('R', 'p', ['kit/x'], [TIP])
        self.assertEqual(out.code, G.OK)
        self.assertEqual(out.lines, ['WARNING: the output stays on the hidden console: denied'])
        self.assertFalse(out.facts.get('stderr'))


class WriteProposal(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='measure-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.live = os.path.join(self.tmp, 'kinds.json')
        with open(self.live, 'w', encoding='utf-8') as f:
            json.dump({'kinds': {'unit': {'cpu': 2, 'warn_min': 5}}}, f)

    def store(self):
        return types.SimpleNamespace(history_records=lambda days: [],
                                     settings=types.SimpleNamespace(history_min_runs=3))

    def test_the_live_table_is_refused_for_stderr_and_kept(self):
        with open(self.live, 'rb') as f:
            before = f.read()
        out = measure.write_proposal(self.live, 30, store=self.store(), live=self.live)
        self.assertEqual((out.code, out.facts.get('stderr')), (G.REFUSED, True))
        self.assertTrue(out.lines[0].startswith('REFUSED: '), out.lines)
        with open(self.live, 'rb') as f:
            self.assertEqual(f.read(), before)

    def test_an_unreadable_live_table_is_refused_with_nothing_written(self):
        path = os.path.join(self.tmp, 'p.json')
        out = measure.write_proposal(path, 30, store=self.store(),
                                     live=os.path.join(self.tmp, 'none.json'))
        self.assertEqual((out.code, out.facts.get('stderr')), (G.REFUSED, True))
        self.assertFalse(os.path.exists(path))

    def test_it_writes_the_proposal(self):
        path = os.path.join(self.tmp, 'p.json')
        layout = {'layout': {'medium_cores': 4, 'agent_cap': 8}}
        with mock.patch.object(measure.lay, 'in_force', return_value=layout):
            out = measure.write_proposal(path, 7, store=self.store(), live=self.live)
        self.assertEqual(out.code, G.OK, out.lines)
        self.assertFalse(out.facts.get('stderr'))
        self.assertEqual(out.lines[-1], 'proposal written: %s' % os.path.abspath(path))
        with open(path, encoding='utf-8') as f:
            doc = json.load(f)
        self.assertEqual(doc['kinds']['unit']['cpu'], 2)
        self.assertIn('from 7 day(s) of history', doc['_note'])


if __name__ == '__main__':
    unittest.main()
