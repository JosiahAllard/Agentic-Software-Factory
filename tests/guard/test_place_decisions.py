"""Reading places from the filesystem (kitlib.guard.place) changes no guard decision: every row
of the rule table (tests/guard/test_rules.py ROWS, root searches and main-checkout edits
included) is decided twice on a real repository laid out as world.World lays out its fake one,
once with git's classify and once with the filesystem's, and the two decisions must be the same
(kind, rule and the full reason). The filesystem side must also have answered every place
itself: a run that quietly fell back to git would prove nothing."""
import os
import shutil
import subprocess
import unittest

from kitlib.git import repo
from kitlib.guard import place
from tests import gitfixture
from tests.realgit import real_git

from . import world as W
from .claudehost import decide
from .test_fsplace import MINE
from .test_rules import ROWS

NO_CD = 'no Instead that runs from the worktree'


class RealWorld(W.World):
    """world.World with base/repo a real main checkout and .claude/worktrees/wt its real linked
    worktree; the fake refs, gate records and lock stay as they are."""

    def __init__(self):
        W.World.__init__(self)
        env = gitfixture.fixture_env()

        def git(*args, cwd=None):
            subprocess.run(('git',) + args, cwd=cwd or self.main, env=env, check=True, capture_output=True)
        git('init', '-q', '-b', 'main')
        for k, v in (('user.name', gitfixture.NAME), ('user.email', gitfixture.EMAIL),
                     ('commit.gpgsign', 'false')):
            git('config', k, v)
        git('add', '.kit.json')
        git('commit', '-q', '-m', 'kit config')
        os.rmdir(self.wt)
        git('worktree', 'add', '-q', self.wt, '-b', 'kit/wt')
        os.makedirs(os.path.join(self.wt, 'src'), exist_ok=True)


class Memo(object):
    def __init__(self, fn):
        self.fn, self.seen, self.calls = fn, {}, 0

    def __call__(self, path):
        self.calls += 1
        key = os.path.normcase(os.path.abspath(path))
        if key not in self.seen:
            self.seen[key] = self.fn(path)
        return self.seen[key]


@real_git("checks the filesystem's places against git's own rev-parse")
class SameDecisions(unittest.TestCase):
    # Appended to the common config, the main checkout's config.worktree and the linked
    # worktree's (None: not written). Subclasses lay out other configs.
    COMMON = MAIN_WT = LINKED_WT = None

    def setUp(self):
        self.w = RealWorld()
        self.addCleanup(lambda: shutil.rmtree(self.w.base, True) if os.path.isdir(self.w.base) else None)
        # The rules place absolute paths only (the `<main checkout>` placeholder is text, never a
        # path: test_place_placeholder.py), so the rows run from whatever folder the suite runs in.
        W.deny_softened()                     # the rows are test_rules.py's, judged with the softened rules at deny
        self.addCleanup(W.allow_unplaced)
        self.w.git.ancestors.add((W.SHA_WT, 'main'))
        common = os.path.join(self.w.main, '.git')
        for path, text in ((os.path.join(common, 'config'), self.COMMON),
                           (os.path.join(common, 'config.worktree'), self.MAIN_WT),
                           (os.path.join(common, 'worktrees', os.path.basename(self.w.wt), 'config.worktree'),
                            self.LINKED_WT)):
            if text:
                with open(path, 'a', encoding='utf-8', newline='\n') as f:
                    f.write(text)

    def test_every_row_decides_the_same(self):
        by_git = Memo(repo.classify)
        fell_back = Memo(repo.classify)
        by_fs = lambda path: place.classify(path, fallback=fell_back)   # noqa: E731
        rules_seen = set()
        for name, make, _kind, _rule, _instead in ROWS:
            with self.subTest(name):
                a = decide(make(self.w), self.w.ctx(classify=by_git))
                b = decide(make(self.w), self.w.ctx(classify=by_fs))
                want = a and (a.verdict, a.rule, a.reason)
                self.assertEqual(b and (b.verdict, b.rule, b.reason), want)
                if a:
                    rules_seen.add(a.rule)
        if MINE:     # an elevated run's files are Administrators': the owner check hands them to git
            # A path below <main>/.git/worktrees (the sync-base marker rows) is git's to place, by
            # design (kitlib.guard.place: "a folder on the way is itself a git directory").
            gitdirs = os.path.normcase(os.path.abspath(os.path.join(self.w.main, '.git', 'worktrees'))) + os.sep
            handed = sorted(p for p in fell_back.seen if not p.startswith(gitdirs))
            self.assertEqual(handed, [], 'the filesystem reader handed %d place(s) to git: %s'
                             % (len(handed), ', '.join(handed)))
        self.assertGreater(by_git.calls, 0)
        for rule in ('root-search', 'main-checkout', 'heavy', 'git-checkout'):
            self.assertIn(rule, rules_seen)


@real_git("checks the filesystem's places against git's own rev-parse")
class SameDecisionsUnderWorktreeConfig(SameDecisions):
    """The state the Claude desktop app leaves a repository in when it makes a worktree:
    extensions.worktreeConfig on in the common config, and a config.worktree in the linked
    worktree's git dir setting core.longpaths and core.hooksPath."""
    COMMON = '[extensions]\n\tworktreeConfig = true\n'
    LINKED_WT = '[core]\n\tlongpaths = true\n\thooksPath = C:/somewhere/githooks\n'


@real_git("checks the filesystem's places against git's own rev-parse")
class SameDecisionsWhenConfigWorktreeWins(SameDecisions):
    """The common config says core.bare = true and each work tree's config.worktree says false:
    git reads config.worktree last, so both are work trees (a reader that ignored config.worktree
    would hand every place to git, one that ignored the common config would never see bare)."""
    COMMON = '[extensions]\n\tworktreeConfig = true\n[core]\n\tbare = true\n'
    MAIN_WT = '[core]\n\tbare = false\n'
    LINKED_WT = '[core]\n\tbare = false\n\tlongpaths = true\n'


@real_git('a path in a git dir is UNKNOWN to git and the filesystem reader alike; the fake World answers MAIN')
class LinkedWorktreeIndexLock(unittest.TestCase):
    """A subagent deletes the index.lock a git that died left in its linked worktree's git dir,
    <common>/worktrees/<name>/index.lock (rules.own_index_lock, docs/guard-rules.md main-checkout row):
    rm, unlink or Remove-Item, from that worktree; from the main checkout the Instead is that run, the
    cd in the same call. Every other write into a git dir stays refused (KIT-GUARD-LINKED-WT-LOCK)."""

    def setUp(self):
        self.w = w = RealWorld()
        self.addCleanup(shutil.rmtree, w.base, True)
        W.deny_softened()
        self.addCleanup(W.allow_unplaced)
        self.wt2 = os.path.join(w.main, '.claude', 'worktrees', 'wt2')
        self.git('worktree', 'add', '-q', self.wt2, '-b', 'kit/wt2')
        self.other, self.owt = os.path.join(w.base, 'other'), os.path.join(w.base, 'owt')
        os.makedirs(self.other)
        for args in (('init', '-q', '-b', 'main'), ('-c', 'user.name=t', '-c', 'user.email=t@example.invalid',
                                                     'commit', '-q', '--allow-empty', '-m', 'x'),
                     ('worktree', 'add', '-q', self.owt, '-b', 'kit/owt')):
            self.git(*args, cwd=self.other)
        self.gd = os.path.join(w.main, '.git', 'worktrees', 'wt')
        self.lock = W.fwd(os.path.join(self.gd, 'index.lock'))
        for path in (self.lock, os.path.join(self.other, '.git', 'worktrees', 'owt', 'index.lock')):
            open(path, 'w').close()

    def git(self, *args, cwd=None):
        subprocess.run(('git',) + args, cwd=cwd or self.w.main, env=gitfixture.fixture_env(), check=True,
                       capture_output=True)

    def judge(self, payload, untold=None):
        ctx = self.w.ctx(classify=place.classify)
        if untold:   # pm_data_error answers for this folder alone
            told = ctx.pm_data_error
            ctx.pm_data_error = lambda path=None: 'no record' if path and repo.real(path) == repo.real(untold) else \
                told(path)
        return decide(payload, ctx)

    def refused(self, rows, untold=None, rule='main-checkout'):
        """Each row (name, payload, Instead): denied by `rule`, with that Instead when it is a string, with
        none that runs from the worktree when it is NO_CD, and any when None."""
        for name, payload, instead in rows:
            with self.subTest(name):
                d = self.judge(payload, untold)
                self.assertIsNotNone(d)
                self.assertEqual((d.verdict, d.rule), ('deny', rule))
                if instead is NO_CD:
                    self.assertFalse((d.instead or '').startswith('cd '), d.instead)
                elif instead is not None:
                    self.assertEqual(d.instead, instead)

    def test_pm_data_untold_or_the_pm_data_worktree(self):
        w, lock = self.w, self.lock
        pmd = os.path.join(w.base, 'pmd')
        self.git('worktree', 'add', '-q', pmd, '-b', 'pm-data')
        pmd_lock = W.fwd(os.path.join(w.main, '.git', 'worktrees', 'pmd', 'index.lock'))
        open(pmd_lock, 'w').close()
        # the PM data worktree untold in the folder the command runs in, then in the call's folder
        self.refused([('runs in an untold folder', W.bash('rm -f %s' % lock, w.wt, sub=True), None)], w.wt, 'pm-data')
        self.refused([('called from an untold folder', W.bash('cd "%s" && rm -f %s' % (W.fwd(w.wt), lock), w.main,
                                                               sub=True), None)], w.main, 'pm-data')
        w.user_config({'pm_data': {'path': pmd}})     # place answers the PM data worktree MAIN
        self.refused([('the PM data worktree\'s own lock', W.bash('rm -f %s' % pmd_lock, pmd, sub=True), None)])
        self.assertIsNone(self.judge(W.bash('rm -f %s' % lock, w.wt, sub=True)))

    def test_own_lock_deleted_from_its_worktree(self):
        w, lock = self.w, self.lock
        typed = '/' + lock[0].lower() + lock[2:] if lock[1:2] == ':' else lock     # Git Bash's /z/... as observed
        bash_instead = 'cd "<worktree>" && rm "%s"' % lock
        ps_instead = 'cd "<worktree>"; if ($?) { Remove-Item "%s" }' % lock
        refused = [  # (name, payload, Instead: see refused)
            ('the observed replay, from the main checkout', W.bash('rm %s' % typed, w.main, sub=True), bash_instead),
            ('relative, from the main checkout', W.bash('rm .git/worktrees/wt/index.lock', w.main, sub=True),
             bash_instead),
            ('PowerShell, from the main checkout', W.ps('Remove-Item "%s"' % lock, w.main, sub=True), ps_instead),
            ('wt2\'s lock from wt', W.bash('rm %s/.git/worktrees/wt2/index.lock' % W.fwd(w.main), w.wt, sub=True),
             'cd "<worktree>" && rm "%s/.git/worktrees/wt2/index.lock"' % W.fwd(w.main)),
            ('another repository\'s worktree', W.bash('cd "%s" && rm "%s/.git/worktrees/owt/index.lock"' % (
                W.fwd(self.owt), W.fwd(self.other)), w.main, sub=True), None),
            ('its config.worktree', W.bash('rm %s/config.worktree' % W.fwd(self.gd), w.wt, sub=True), None),
            ('its sync-base marker', W.bash('rm %s/kit-sync-base' % W.fwd(self.gd), w.wt, sub=True), None),
            ('the main checkout\'s index.lock', W.bash('rm %s/.git/index.lock' % W.fwd(w.main), w.wt, sub=True), None),
            ('a lock outside a worktree git dir', W.bash('rm %s/.git/worktrees/x/index.lock' % W.fwd(w.main), w.wt,
                                                         sub=True), None),
            ('its whole git dir', W.bash('rm -r %s' % W.fwd(self.gd), w.wt, sub=True), None),
            ('a redirect onto it', W.bash('echo x > %s' % lock, w.wt, sub=True), NO_CD),
            ('rm with a redirect onto it', W.bash('rm -f nothing 2> %s' % lock, w.wt, sub=True), NO_CD),
            ('cp onto it', W.bash('cp %s/src/x %s' % (W.fwd(w.wt), lock), w.wt, sub=True), NO_CD),
            ('cp onto it, from the main checkout', W.bash('cp x %s' % lock, w.main, sub=True), NO_CD),
            ('a hard link onto it', W.bash('ln %s/.git/config %s' % (W.fwd(w.main), lock), w.wt, sub=True), NO_CD),
            ('a hard link onto it, from the main checkout', W.bash('ln .git/config %s' % lock, w.main, sub=True),
             NO_CD),
            ('a PowerShell hard link onto it', W.ps('New-Item -ItemType HardLink -Path "%s" -Target "%s/.git/config"'
                                                    % (lock, W.fwd(w.main)), w.main, sub=True), NO_CD),
            ('Remove-Item -Include under its git dir', W.ps('Remove-Item -Path "%s" -Include index.lock -Recurse'
                                                            % W.fwd(self.gd), w.wt, sub=True), None),
            ('the Write tool', W.event('Write', {'file_path': lock, 'content': ''}, w.wt, sub=True), NO_CD),
        ]
        self.refused(refused)
        allowed = [
            ('rm -f from wt', W.bash('rm -f %s' % lock, w.wt, sub=True)),
            ('/bin/rm from wt', W.bash('/bin/rm %s' % lock, w.wt, sub=True)),
            ('unlink from wt', W.bash('unlink %s' % lock, w.wt, sub=True)),
            ('the bash Instead, filled', W.bash(bash_instead.replace('<worktree>', W.fwd(w.wt)), w.main, sub=True)),
            ('the PowerShell Instead, filled', W.ps(ps_instead.replace('<worktree>', W.fwd(w.wt)), w.main, sub=True)),
            ('Remove-Item from wt', W.ps('Remove-Item -Force "%s"' % lock, w.wt, sub=True)),
            ('module-qualified Remove-Item', W.ps('Microsoft.PowerShell.Management\\Remove-Item "%s"' % lock, w.wt,
                                                  sub=True)),
            ('del from wt', W.ps('del "%s"' % lock, w.wt, sub=True)),
        ]
        for name, payload in allowed:
            with self.subTest(name):
                d = self.judge(payload)
                self.assertIsNone(d, d and d.reason)


if __name__ == '__main__':
    unittest.main()
