"""An agent session's PowerShell .NET write whose path the line computes ($p, an expression):
refused under write-jail at the kit's default modes, not only warned under unplaced; so is a
literal relative one after a move to a location the line computes (cd $d). A docs-writer
ran `cd <worktree>; $p='docs\\guard.md'; ...; [IO.File]::WriteAllText($p,$t)`: .NET read the
relative path against the process's working directory, the main checkout, and wrote there, while
the guard only warned (rules.ps_drive_jail under unplaced, whose default mode is warn). The owner's
own session is not refused. A literal relative path is rules.net_process_dir's
(test_dotnet_relative_write.py)."""
import unittest

from kitlib import guard
from kitlib.guard import context

from . import world as W
from .claudehost import action

COORDINATOR = {context.ROLE_ENV: 'coordinator'}     # a marked agent session, not a subagent
# the observed command, its replacement list cut short
OBSERVED = ("cd {wt}; $p='docs\\guard.md'; $t=[IO.File]::ReadAllText($p); $r=@(,@('a','b'))\n"
            "foreach($x in $r){{ $t=$t.Replace($x[0],$x[1]) }}\n"
            "[IO.File]::WriteAllText($p,$t)")


class Base(unittest.TestCase):
    def setUp(self):
        self.w = W.World()
        self.addCleanup(self.w.cleanup)

    def decide(self, t, cwd=None, env=None, sub=True, agent_type='docs-writer'):
        payload = W.event('PowerShell', {'command': t.format(main=self.w.main, wt=self.w.wt), 'description': 'test'},
                          cwd or self.w.wt, sub, agent_type=agent_type)
        return guard.decide(action(payload), self.w.ctx(environ=env or {}))

    def denied(self, d):
        self.assertEqual((d.verdict, d.rule), ('deny', 'write-jail'), d.reason)
        return d


class Observed(Base):
    def test_from_a_subagent_is_refused(self):
        for cwd in (self.w.main, self.w.wt):
            with self.subTest(cwd=cwd):
                d = self.denied(self.decide(OBSERVED, cwd=cwd))
                self.assertIn('[Environment]::CurrentDirectory', d.reason)
                self.assertEqual(d.instead, '[IO.File]::WriteAllText("%s/<file>",$t)' % W.fwd(self.w.wt))

    def test_the_alternative_with_a_file_passes(self):
        d = self.denied(self.decide(OBSERVED))
        filled = d.instead.replace('<file>', 'docs/guard.md')
        self.assertEqual(self.decide(filled).verdict, 'allow')
        self.assertNotIn('kit guard', self.decide(filled).reason or '')

    def test_from_a_marked_agent_session_is_refused(self):
        self.denied(self.decide(OBSERVED, env=COORDINATOR, sub=False))

    def test_the_owners_own_session_is_not_refused(self):
        # the owner may edit the main checkout: not judged before, and not now
        for cwd in (self.w.main, self.w.wt):
            with self.subTest(cwd=cwd):
                self.assertEqual(self.decide(OBSERVED, cwd=cwd, sub=False).verdict, 'allow')


class OtherComputedPaths(Base):
    TEXTS = ("$p = 'x.txt'; [System.IO.File]::AppendAllText($p, 'y')",
             "$p = '{wt}\\x.txt'; [IO.File]::WriteAllText($p, 'y')",
             "[IO.File]::WriteAllText((Join-Path $d 'x.txt'), 'y')",
             "[IO.File]::WriteAllText([IO.Path]::Combine('a', 'x.txt'), 'y')",
             "$s = [IO.FileStream]::new($p, 'Create')", "[IO.Directory]::CreateDirectory($d)",
             "[IO.File]::Copy('{wt}\\a.txt', $p)", "cd $d; [IO.File]::WriteAllText('x.txt', 'y')")

    def test_each_is_refused_for_an_agent_session(self):
        for t in self.TEXTS:
            with self.subTest(text=t):
                d = self.denied(self.decide(t))
                self.assertIn(W.fwd(self.w.wt) + '/<file>', d.instead)

    def test_none_is_refused_for_the_owner(self):
        for t in self.TEXTS:
            with self.subTest(text=t):
                self.assertNotEqual(self.decide(t, cwd=self.w.main, sub=False).verdict, 'deny')

    def test_a_computed_source_with_a_literal_destination_passes(self):
        self.assertEqual(self.decide("[IO.File]::Copy($p, '{wt}\\b.txt')").verdict, 'allow')


if __name__ == '__main__':
    unittest.main()
