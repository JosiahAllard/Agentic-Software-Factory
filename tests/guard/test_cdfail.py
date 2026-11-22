"""A cd that may fail (kitlib.guard.cdfail, shell.track; docs/guard-core.md "Parsing"): the folders
the shell may be in after it, and the git rules that judge a git write in each of them, the folder
before the cd included; a subagent's checkout or switch whose folder is computed is refused
(docs/guard-rules.md, git-checkout). More rows: shell_rows.py, "a cd that may fail"."""
import os
import tempfile
import unittest

from kitlib import guard
from kitlib.guard import cdfail, rules
from kitlib.guard import shell as s

from . import world as W
from .claudehost import action

CWD = 'D:\\w'


def gits(text, sh, cwd=CWD):
    p, _ = s.parse(text, sh, cwd)
    return [c for c in p.commands if c.argv[:1] == ['git']]


def alts(text, sh, cwd=CWD):
    """{git subcommand: the other folders it may run in (Command.cwd_alt)}."""
    return {c.argv[1]: c.cwd_alt for c in gits(text, sh, cwd)}


class Track(unittest.TestCase):
    def test_the_shells_are_named_as_in_shell(self):
        self.assertEqual((cdfail.BASH, cdfail.PS), (s.BASH, s.PS))

    def test_a_run_of_and_runs_only_after_the_cd(self):
        got = alts('cd "$S" && git a && { git b; git c; } && git d | git e; git f', s.BASH)
        self.assertEqual(got, {'a': (), 'b': (), 'c': (), 'd': (), 'e': (), 'f': (CWD,)})

    def test_what_follows_or_after_the_run_runs_in_the_folder_before(self):
        self.assertEqual(alts('cd "$S" && git a || git b; git c', s.BASH), {'a': (), 'b': (CWD,), 'c': (CWD,)})

    def test_or_exit_guards_the_rest_of_its_subshell(self):
        self.assertEqual(alts('cd "$S" || exit 1; git a; { git b; }', s.BASH), {'a': (), 'b': ()})
        a, b = gits('(cd "$S" || exit; git a); git b', s.BASH)
        self.assertEqual((a.cwd, a.cwd_alt, b.cwd, b.cwd_alt), (None, (), CWD, ()))

    def test_or_return_guards_nothing(self):
        self.assertEqual(alts('cd "$S" || return; git a', s.BASH), {'a': (CWD,)})

    def test_powershell_if_ok_guards_its_own_block_only(self):
        got = alts('cd $env:S; if ($?) { git a; git b } else { git c }; git d', s.PS)
        self.assertEqual(got, {'a': (), 'b': (), 'c': (CWD,), 'd': (CWD,)})
        for line in ('cd $env:S; if (!$?) { git a }', 'cd $env:S; if ($? -and $x) { git a }', 'cd $env:S; git a'):
            with self.subTest(line):
                self.assertEqual(alts(line, s.PS), {'a': (CWD,)})

    def test_a_missing_literal_folder_may_fail(self):
        a, = gits('cd /d/w/missing; git a', s.BASH)
        self.assertEqual((a.cwd, a.cwd_alt), (CWD + '\\missing', (CWD,)))

    def test_a_cd_that_cannot_fail_clears_the_other_folders(self):
        t = tempfile.mkdtemp()
        self.addCleanup(os.rmdir, t)
        a, b = gits('cd "$S"; git a; cd %s; git b' % t.replace('\\', '/'), s.BASH)
        self.assertEqual((a.cwd_alt, b.cwd_alt), ((CWD,), ()))
        self.assertIsNotNone(b.cwd)

    def test_a_relative_cd_moves_each_other_folder(self):
        a, = gits('cd "$S"; cd sub; git a', s.BASH)
        self.assertIsNone(a.cwd)
        self.assertEqual(sorted(a.cwd_alt), sorted([CWD, CWD + '\\sub']))

    def test_a_subshell_drops_what_a_cd_inside_it_held(self):
        a, b = gits('(cd "$S"; git a); git b', s.BASH)
        self.assertEqual((a.cwd_alt, b.cwd, b.cwd_alt), ((CWD,), CWD, ()))

    def test_a_substitution_takes_its_holders_folders(self):
        self.assertEqual(alts('cd "$S"; echo $(git a)', s.BASH), {'a': (CWD,)})


class Rules(unittest.TestCase):
    """At the kit's own modes: unplaced and git-alias warn."""

    def setUp(self):
        self.w = W.World()
        self.addCleanup(self.w.cleanup)

    def decide(self, payload):
        return guard.decide(action(payload), self.w.ctx())

    def denied(self, payload, rule):
        d = self.decide(payload)
        self.assertEqual((d.verdict, d.rule), ('deny', rule), d.reason)
        return d

    def test_the_refusal_names_the_failed_cd_and_a_literal_folder(self):
        line = ('S="%s/pl"; cd "$S" && git rev-parse --git-dir >/dev/null 2>&1 || git init -q; timeout 280 git fetch -q '
                '--depth 1 https://example.com/x.git 6ffa5b1 && git -c core.autocrlf=false checkout -q FETCH_HEAD && '
                'ls o | head -60' % W.fwd(self.w.temp))   # the line a subagent checked out with in a main checkout
        d = self.denied(W.bash(line, self.w.main, sub=True), 'git-checkout')
        self.assertIn('if the cd before it fails', d.reason)
        self.assertIn(W.fwd(self.w.main), d.reason)
        self.assertEqual(d.instead, 'git -C "<folder>" -c core.autocrlf=false checkout -q FETCH_HEAD')

    def test_an_alias_the_line_defines_keeps_the_other_folders(self):
        d = self.denied(W.bash('cd "$S"; git -c alias.co=checkout co FETCH_HEAD', self.w.main), 'git-checkout')
        self.assertEqual(d.instead, 'git -C "<folder>" checkout FETCH_HEAD')

    def test_a_nested_shell_takes_the_other_folders(self):
        self.denied(W.bash('cd "$S"; bash -c "git checkout x"', self.w.main), 'git-checkout')

    def test_a_subagent_checkout_in_a_computed_folder_is_refused(self):
        for line, instead in (('cd "$WT" && git checkout -b kit/x', 'git -C "<folder>" checkout -b kit/x'),
                              ('git -C "$WT" switch -c kit/x', 'git -C "<folder>" switch -c kit/x')):
            with self.subTest(line):
                d = self.denied(W.bash(line, self.w.wt, sub=True), 'git-checkout')
                self.assertIn('computed', d.reason)
                self.assertEqual(d.instead, instead)
                warned = self.decide(W.bash(line, self.w.wt))         # the main session: a warning
                self.assertEqual((warned.verdict, warned.rule), ('allow', rules.WARNED), warned.reason)
                self.assertIn('rule=unplaced', warned.reason)

    def test_reads_in_a_computed_folder_stay_allowed(self):
        for line in ('cd "$S" && git status', 'cd "$S"; git status', 'cd "$S"; git log -1'):
            with self.subTest(line):
                self.assertEqual(self.decide(W.bash(line, self.w.main, sub=True)).verdict, 'allow')


if __name__ == '__main__':
    unittest.main()
