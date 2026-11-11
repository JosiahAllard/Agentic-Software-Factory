"""What a parsed git invocation acts on (docs/guard-core.md "Parsing").

git_call(inv) reads git's global options into a GitCall: the directory it acts on, its
subcommand and the Words after it, and what points it at a place the text does not show.
git_dyn_word names a computed word where the git rules read one.
"""
import re

from . import shell as sh
from .commands import (_git_word, run_dir)


# git's global options read with the next word as their value (-C, --git-dir and --work-tree are
# read apart): one missing here makes its value look like the subcommand. --attr-source is in
# git's manual; --shallow-file is git's own, undocumented. Listing an option git does not take is
# safe: git stops at an option it does not know, so nothing after it runs.
GIT_VALUE_OPTS = {'-c', '--namespace', '--super-prefix', '--config-env', '--exec-path', '--attr-source',
                  '--shallow-file'}
# subcommands whose options the git rules read, and per subcommand the options whose value is a
# message, a name or a path (a variable there is only a value)
GIT_READ_SUBS = {'add', 'stage', 'commit', 'checkout', 'switch', 'reset', 'clean', 'stash', 'restore',
                 'branch', 'push', 'worktree', 'rm', 'mv', 'checkout-index', 'read-tree', 'update-ref',
                 'merge', 'rebase', 'cherry-pick', 'revert', 'am', 'pull', 'apply'}
GIT_SUB_VALUES = {
    'commit': ('-m', '--message', '-F', '--file', '-C', '-c', '--reuse-message', '--reedit-message',
               '--author', '--date', '--fixup', '--squash', '-t', '--template', '--trailer'),
    'merge': ('-m', '--message', '-F', '--file', '-s', '--strategy', '-X', '--strategy-option'),
    'stash': ('-m', '--message'),
    'checkout': ('-b', '-B', '--orphan'),
    'switch': ('-c', '-C', '--create', '--force-create', '--orphan'),
    'worktree': ('-b', '-B', '--reason'),
    'push': ('-o', '--push-option', '--repo', '--receive-pack', '--exec'),
}
CORE_WORKTREE_RE = re.compile(r'(?i)^core\.worktree=')


def git_dyn_word(inv):
    """A reason when a git invocation has a word the shell computes where the git rules read it
    (the subcommand, or an option of a subcommand they check), else None."""
    if inv is None or inv.name != 'git':
        return None
    gc = git_call(inv)
    if gc is None:
        return None
    if gc.sub_dyn:
        return 'a git subcommand that is not literal'
    if gc.sub not in GIT_READ_SUBS:
        return None
    values = GIT_SUB_VALUES.get(gc.sub, ())
    rest = gc.rest
    k = 0
    while k < len(rest):
        w = rest[k]
        if w.text == '--' and not w.dyn:
            break
        if not w.dyn and w.text in values:
            k += 2
            continue
        if w.dyn:
            return 'a git %s word that is not literal' % gc.sub
        k += 1
    return None


class GitCall(object):
    """A git invocation: `dir` the directory it acts on (git -C applied to the directory git
    starts in, run_dir: the command's cwd, or a Start-Process -WorkingDirectory; None when the
    text cannot tell), `sub` its subcommand, `rest` the Words after it, `alias`
    (name, value) when the line defines a git alias (-c alias.name=value). Every word is the
    text the shell passes (a backslash bash drops is dropped); `sub_dyn` the subcommand is not
    literal; `blind` what points git at a place the text does not show, which a -C cannot undo (a
    GIT_ENV_NAMES variable, --git-dir, --work-tree, -c core.worktree, --config-env, a computed
    -c), else None. `synth`: how many Words at the start of `rest` a git alias's value gave (the
    guard's expansion of one), not words of the line."""
    __slots__ = ('dir', 'sub', 'rest', 'inv', 'unknown', 'alias', 'sub_dyn', 'blind', 'synth')

    def __init__(self, dir_, sub, rest, inv, unknown, alias=None, sub_dyn=False, blind=None, synth=0):
        self.dir, self.sub, self.rest, self.inv, self.unknown = dir_, sub, rest, inv, unknown
        self.alias, self.sub_dyn, self.blind, self.synth = alias, sub_dyn, blind, synth

    @property
    def argv(self):
        return [w.text for w in self.rest]

    def before(self):
        """The invocation's Words before the word that names the subcommand (git's own options)."""
        a = list(self.inv.args)
        return a[:len(a) - (len(self.rest) - self.synth) - 1]

    def after(self):
        """The invocation's Words after the word that names the subcommand (`rest` without the
        words an alias's value gave)."""
        a = list(self.inv.args)
        n = len(self.rest) - self.synth
        return a[len(a) - n:] if n else []


def git_call(inv):
    """GitCall for a git invocation, or None when inv is not git or names no subcommand. git
    that xargs runs with no subcommand gets one with sub '' and blind 'xargs': the input xargs
    appends may hold --git-dir and the subcommand."""
    if inv is None or inv.name != 'git':
        return None
    d = run_dir(inv)                            # Start-Process -WorkingDirectory: git starts there
    git_env = inv.cmd.git_env
    unknown = d is None or bool(git_env)
    # what sets a git dir or work tree the text cannot place: -C never undoes it
    blind = (git_env if isinstance(git_env, str) else 'GIT_DIR or the like') if git_env else None
    if blind:
        d = None
    a = [_git_word(w) if inv.cmd.shell == sh.BASH else w for w in inv.args]
    alias = None
    k = 0
    while k < len(a):
        t = a[k].text
        cfg = None
        if t == '-c' or t == '--config-env':
            cfg = a[k + 1].text if k + 1 < len(a) else ''
        elif t.startswith('-c') and not t.startswith('--'):
            cfg = t[2:]
        elif t.startswith('--config-env='):
            cfg = t.split('=', 1)[1]
        if cfg is not None and cfg.lower().startswith('alias.'):
            nm, _eq, val = cfg[len('alias.'):].partition('=')
            alias = (nm, val)
        why = None
        if cfg is not None:
            if t.startswith('--config-env'):
                why = '--config-env'
            elif CORE_WORKTREE_RE.match(cfg):
                why = '-c core.worktree'
            elif t == '-c' and k + 1 < len(a) and a[k + 1].dyn or t != '-c' and t.startswith('-c') and a[k].dyn:
                why = 'the computed -c'
        if why:                                 # core.worktree, or a config from the environment or computed
            d, unknown, blind = None, True, blind or why
        if t == '-C':
            if k + 1 >= len(a) or a[k + 1].dyn:
                d, unknown = None, True
            elif not blind:                     # after --git-dir and the like, -C moves only the cwd
                d = sh.resolve_path(a[k + 1].text, None if unknown else d, home=inv.cmd.home)
                unknown = d is None
            k += 2
            continue
        if t.startswith('--git-dir') or t.startswith('--work-tree'):
            d, unknown, blind = None, True, blind or ('--git-dir' if t.startswith('--git-dir') else '--work-tree')
            k += 1 if '=' in t else 2
            continue
        if t in GIT_VALUE_OPTS:
            k += 2
            continue
        if t.startswith('-'):
            k += 1
            continue
        break
    if k >= len(a):
        if inv.via == 'xargs':                  # xargs adds the subcommand, and options before it
            return GitCall(None, '', [], inv, True, alias, False, blind or 'xargs')
        return None
    return GitCall(d, a[k].text, a[k + 1:], inv, unknown or a[k].dyn, alias, a[k].dyn, blind)
