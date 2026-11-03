"""A command line the parser cannot read cleanly (docs/guard.md "Fallback").

kitlib.guard.shell marks a parse unclean when the text holds something it does not model (an
unclosed quote, an encoded command, a program named by a variable, a word the shell rewrites).
The rules have already run on what it did read; check() then looks at the raw text, so an
unreadable line is never a way around a rule, and a plain line is never denied by it:

  - the owner's tools by name (NAMED), first, in every session but the owner's own: text naming
    the hub tool (hub.py, tools.hub, -m hub, kit hub) is refused as `hub-owner-verb`, text naming
    the lock CLI (machine_lock.py, tools.machine_lock, kit machine_lock) as `owner-tool`, read
    verbs included, and text
    naming the owner's delegation list (owner-delegations) as `owner-delegations`, reads included.
    The part of the line holding the name is read again first (named_reread): a hub owner verb, a
    lock owner-only write or a write of the list there gives that rule's own refusal; otherwise the reason says the line
    names the tool and the alternative is plainly()'s.
  - the roles' tools by name, for a subagent only (named_runs): text naming tools/backlog.py, or
    gate.py allow, that the rules have not read (_read_at: not a literal word of a literal,
    non-runner command, so a commit message naming it is a mention, unless its output is piped
    into a runner: _pipe_runner, _piped_text) has its part of the line read again and judged by
    backlogrole.runs or gaterole.shell; a part that still does not read, or whose program is
    computed, is refused as backlog-role or gate-allow, its alternative the tool's literal form.
  - a git or root-search pattern that matches: the text from the match to the next ; | & or
    newline is read again as a command of its own and the rules run on it. A rule that denies it
    gives the deny, its real alternative included; none: the `unreadable` deny, whose advice is
    to write the names literally or run a script file (plainly(): advice only, the line is never
    rewritten).
    The location-dependent git patterns are checked when the call's directory is a main checkout or one git cannot place; the push --no-verify pattern when that
    directory's repository has a PM data record (or git cannot place it), in every session but
    the owner's own. The git-shared-ref patterns (SHARED_REF_PATTERNS: update-ref, a push into a
    repository named by a path or one the shell computes, a fetch or pull that may write a ref
    (its option abbreviations included), symbolic-ref with two names or -d, replace other than a
    listing, a notes write, branch -f/-m/-M/-C, tag -f/-d, checkout -B, switch -C,
    --ignore-other-worktrees, worktree add -B/-f, rebase --update-refs or rebase.updateRefs, remote
    remove/rm/rename/set-head and add --mirror, fast-import and filter-branch) are checked
    wherever the call runs, in a session rules.shared_ref_held holds. The push-to-main pattern in a repository that does not set
    repo.push_block (one with no .kit.json among them) gives only the re-read's own deny (a delete
    or a forced push of main); no deny lets the line run.
  - a heavy program's name: the same, and a program named by a variable is denied as heavy with
    the lock line that runs it.
  - anything else: a subagent is denied (`unreadable`); another jailed session (a mark in
    guard_agent_marks) is denied (`write-jail`) when the line holds the word git, as the unread
    part may move git to a checkout the jail cannot place; a session the jail does not hold only
    in a main checkout or a place git cannot tell, and only when the line runs a program the text does not name, runs
    a shell, eval, Invoke-Expression, source or an encoded command, pipes into a shell, or names
    a git subcommand or option by a variable. Such a line in a repository with a PM data record
    is denied too (`git-push-pmdata`), wherever in it, in every session but the owner's own: the
    hidden program may be git push --no-verify. PowerShell's `& (<subexpression>)` leaves a line
    clean (commands.call_subexpr); rules.call_subexpr_pmdata judges it, on a clean line or one
    unclean for another reason: that PM data check, and in any repository an agent session is
    denied, as here (`unreadable` for a subagent, `write-jail` for another jailed session).

The `unreadable` rule is a warning by default (kitlib.guard.modes): where it would
deny, the warning is kept and the check goes on to the next pattern and the checks after it, so
only a kept rule's own deny (a re-read one, git-push-pmdata) stops the line. A pattern whose rule
is off (wrapped-gate by default) is not looked for. The owner can set each back to deny.

Where a pattern matches, the directory is the one the command holding the match runs in (a
literal cd before it is followed), not only the call's.

The git patterns are an earlier per-repository git guard's ($g and its four rules) plus the
kit's own git rules.
"""
import fnmatch
import os
import re

from kitlib import config as kconfig
from kitlib.git import repo

from . import commands as cm
from . import modes
from . import rules
from . import shell as sh

G = r'\bgit(?:\.exe)?\s+(?:-[Cc]\s+(?:"[^"]*"|\'[^\']*\'|\S+)\s+|--?\S+\s+)*'
GIT_WORD_RE = re.compile(r'(?i)(?<![\w.-])git(?:\.exe)?(?![\w-])')  # git as a word, not .git or GIT_DIR
# programs that run text as commands: on an unclean line their commands may be what it hides
RUNNERS = cm.BASH_SHELLS | cm.PS_SHELLS | {'cmd', 'eval', 'iex', 'invoke-expression', 'source', '.'}

# git with no subcommand run by xargs: the input names the repository and the command, so it is
# judged machine-wide, not by the record of the directory it runs in
XARGS_BARE_GIT = r'\bxargs\b[^|;&\n]*\bgit(?:\.exe)?[ \t]*(?=[|;&\n]|$)'

# every worktree shares the refs, so a held session's ref write is judged wherever it runs: the
# forms git-shared-ref reads (rules.git_shared_ref, git_shared_push, git_shared_ref_forms). A match
# is only a reason to read the part again; the re-read rules decide.
REST = r'[^|;&\n]*'                                   # the rest of the command
# the kit command before the name of the tool it runs (kit, tools/kit.py, -m tools.kit, and kit
# with each suffix commands.base_name takes off a program: kit.cmd, kit.com; docs/guard.md "The
# kit command"), the name maybe quoted, after quotes or empty words (commands.kit_tool skips them)
KIT_WORD = (r'(?:(?<![\w.-])kit(?:\.py|%s)?|\btools\.kit)["\']*(?:\s+["\']+)*\s+["\']*'
            % '|'.join(re.escape(s) for s in cm.EXE_SUFFIXES))
# the ANYWHERE rules re-read from the start of the part of the line holding the match, so the
# words before the tool's name are read too, and then from the match itself when that gave
# nothing: a computed program or an open quote at the part's start leaves the run unreadable there
SEGMENT_RULES = ('owner-tool', 'wrapped-gate')
SHARED_REF_PATTERNS = (
    G + r'update-ref\b',
    # a push whose repository may be this one: `.`, a file: URL, a path, --repo
    G + r'push\b' + REST + r'?\s(?:--repo\b|["\']?(?:\.(?=["\']?(?:\s|$|[|;&\n]))|\.\.?[\\/]|file:|[A-Za-z]:[\\/]|/))',
    # ... or one the shell computes, or under the home folder: the first word after the options
    G + r'push\s+(?:-\S+\s+)*["\']?[$`~]',
    # a fetch or pull with a refspec (a `:` not of a URL), or an option that writes refs of its own
    # (--tags, --stdin, --prune-tags, --update-head-ok, or an abbreviation of one git takes)
    G + r'(?:fetch|pull)\b' + REST + r'(?::(?!//)|\s--(?:prune-t|t(?:a(?:gs?)?)?\b|update-h|st(?:d(?:in?)?)?\b)'
        r'|\s-[a-zA-Z0-9]*[Ptu])',
    # a forced fetch or pull of `tag <name>`: -f in a cluster, or --force down to --fo (--f is
    # ambiguous to git: --filter, and pull's --ff)
    G + r'(?:fetch|pull)\b(?=' + REST + r'\stag\s)' + REST + r'\s(?:-[a-zA-Z0-9]*f|--fo(?:r(?:ce?)?)?(?![^\s|;&]))',
    # git -c remote.<name>.fetch=... or a pruneTags (config keys ignore case), rebase.updateRefs.
    # The key starts a word (or follows -c): a start inside one, as at each `remote.` of
    # remote.remote.remote..., would scan the rest of the word again from each
    r'\bgit(?:\.exe)?\s' + REST + r'(?i:(?:(?<![\w.-])|(?<=-c))remote\.[^\s=]*\.(?:fetch|prunetags)|\bfetch\.prunetags)\s*=',
    r'\bgit(?:\.exe)?\s' + REST + r'(?i:\brebase\.updaterefs)\b',
    # branch -f, -m, -M, -C (in a cluster too), --force, --move; tag -f, -d, --force, --delete
    G + r'branch\b' + REST + r'\s(?:-[a-zA-Z]*[fmMC]|--forc|--mo)',
    G + r'tag\b' + REST + r'\s(?:-[a-zA-Z]*[fd]|--forc|--d)',
    # checkout -B, switch -C or --force-create, --ignore-other-worktrees; worktree add -B or -f
    G + r'checkout\b' + REST + r'\s(?:-[a-zA-Z0-9]*B|--ignore-o)',
    G + r'switch\b' + REST + r'\s(?:-[a-zA-Z]*C|--force-|--i)',
    G + r'worktree\s+add\b' + REST + r'\s(?:-[a-zA-Z]*[Bf]|--f)',
    # rebase --update-refs (any abbreviation: no other rebase option starts with u)
    G + r'rebase\b' + REST + r'\s--u',
    # remote remove, rm, rename, set-head, add --mirror
    G + r'remote\s+(?:-\S+\s+)*(?:remove|rm|rename|set-head)\b',
    G + r'remote\s+(?:-\S+\s+)*add\b' + REST + r'\s--mi',
    G + r'(?:fast-import|filter-branch)\b',
    # symbolic-ref -d (in a cluster too, -dq), or with two names (a write); one name alone reads. An
    # option word is -m and its value or another option, never both (`-mx` either way would
    # backtrack exponentially on a line of them)
    G + r'symbolic-ref\b(?:' + REST + r'\s(?:-[a-zA-Z]*d|--d)|(?:\s+(?:-m\s*(?:"[^"]*"|\'[^\']*\'|\S+)|-(?!m)\S+))*'
        r'\s+[^\s|;&-]\S*\s+[^\s|;&])',
    # replace with a word, unless it lists
    G + r'replace\b(?!' + REST + r'\s(?:-l\b|--l))(?=' + REST + r'\s\S)',
    # a notes verb that writes
    G + r'notes\b' + REST + r'?\s(?:add|append|copy|edit|merge|prune|remove)\b',
)

MAIN_ONLY = (
    ('git-bulk-add', G + r'(?:add|stage)\b[^|;&]*(\s-A\b|\s--all\b|\s-u\b|\s--update\b|\s\.(\s|$))'),
    ('git-commit-all', G + r'commit\b[^|;&]*\s(-a|-am|-a[a-zA-Z]+|--all)\b'),
    ('git-checkout', G + r'(?:checkout|switch)\b'),
    ('git-reset-hard', G + r'reset\b[^|;&]*\s--hard\b'),
    ('git-clean', G + r'clean\b(?![^|;&]*\s(-[a-zA-Z]*n|--dry-run)\b)'),
    ('git-stash', G + r'stash\b(?!\s+(list|show)\b)'),
    ('git-restore', G + r'restore\b(?:(?![^|;&]*\s(?:--staged|-[a-zA-Z]*S[a-zA-Z]*)\b)'
                        r'|(?=[^|;&]*\s(?:--worktree|-[a-zA-Z]*W[a-zA-Z]*)\b))'),
)
SUBAGENT_MAIN = (
    ('git-main-history', G + r'(?:commit|merge|rebase|cherry-pick|revert|am|pull|apply|update-ref)\b'),
)
ANYWHERE = (
    ('git-branch-force-delete', G + r'branch\s+[^|;&]*(-D|--delete\s+--force|-[a-zA-Z]*D[a-zA-Z]*)\b'),
    ('git-worktree-remove', G + r'worktree\s+remove\b'),
    # every worktree shares the refs: a held session's ref write is judged wherever it runs
    *(('git-shared-ref', p) for p in SHARED_REF_PATTERNS),
    ('git-push-main', G + r'push\b[^|;&]*(\s|:|/)(?:main|master)\b'),
    ('git-push-pmdata', G + r'push\b[^|;&]*\s--no(?:-v|-?(?![\w-]))'),
    ('git-push-pmdata', G + r'send-pack\b'),
    # a computed push word or subcommand ($X, `...`, $(...), %X%, @(...)): may be --no-verify or push
    ('git-push-pmdata', G + r'(?:push\b[^|;&\n]*)?(?:\$|`|%[A-Za-z_]\w*%|@\()'),
    # git push, or git with no subcommand, run by xargs: the input it appends may be --no-verify or push
    ('git-push-pmdata', r'\bxargs\b[^|;&\n]*\bgit(?:\.exe)?\b[^|;&\n]*\bpush\b'),
    ('git-push-pmdata', XARGS_BARE_GIT),
    # git http-push and the remote helpers, run by hand, talk to a remote without the hook too
    ('git-push-pmdata', G + r'(?:http-push|remote-[\w-]+)\b'),
    ('git-push-pmdata', r'(?i)(?<![\w.-])git-(?:send-pack|http-push|remote-[\w-]+)(?:\.exe)?\b'),
    # machine setup run to act (rules.owner_tool): any session but the owner's own. This and
    # wrapped-gate re-read from the start of the part of the line holding the match (SEGMENT_RULES),
    # so the kit command before the tool's name (`kit machine_setup --apply`) is read too, and then
    # from the match (`$PY tools/machine_setup.py --apply`)
    ('owner-tool', r'(?i)(?<![A-Za-z0-9_])(?:machine[_-]setup(?:\.py|\.ps1)?|new-repo\.ps1)(?![A-Za-z0-9_])'
                   r'[^|;&\n]*\s-{1,2}a'),
    ('wrapped-gate', r'(?:\bgate\.py\b|\bmachine_lock\.py\s+run\b|' + KIT_WORD
                     + r'(?:gate|machine_lock\s+run)(?![\w.-]))[^;&]*?(?<!\|)\|(?!\|)'),
    ('root-search', r'\bfind\s+(?:-[HLP]\s+)*(?:/|/[a-zA-Z]/?|[A-Za-z]:[\\/]?)(?=\s|$|["\'])'),
    ('root-search', r'(?i)\b(?:get-childitem|gci|ls|dir)\b[^|;&]*\s["\']?[A-Za-z]:[\\/]?["\']?(?=\s|$)[^|;&]*\s-r'),
    ('root-search', r'(?i)\b(?:get-childitem|gci|ls|dir)\b[^|;&]*\s-r[a-z]*\b[^|;&]*\s["\']?[A-Za-z]:[\\/]?["\']?(?=\s|$)'),
    ('root-search', r'(?i)\bdir\s+(?:/\w+\s+)*["\']?[A-Za-z]:\\?["\']?\s*(?:/\w+\s+)*/s\b'),
    ('root-search', r'(?i)\bdir\b[^|;&]*/s\b[^|;&]*\s["\']?[A-Za-z]:\\?["\']?(?=\s|$)'),
)
# the owner's tools by name (docs/guard.md "Fallback"): a line the guard cannot read that names one
# is refused whole under that tool's rule, in every session but the owner's own, read verbs included
# python's -m, alone or ending a cluster of its no-value flags (-Im) as rules.PY_FLAG_RE reads it, the
# module joined or not, and maybe quoted
PY_M = r'(?<![\w-])-[bBdEiIOPqsSuvx]*m\s*[\'"]?'
NAMED = (
    ('hub-owner-verb', re.compile(r'(?i)(?<![\w.-])hub\.py\b|\btools\.hub(?![\w.])|' + PY_M + r'hub(?![\w.])|'
                                  + KIT_WORD + r'hub(?![\w.-])'),
     'names the hub tool, and a part of it the guard cannot read may run one of its owner verbs: %s'
     % rules.HUB_OWNER_WHY),
    ('owner-tool', re.compile(r'(?i)(?<![\w.-])machine_lock\.py\b|\btools\.machine_lock(?![\w.])|' + PY_M
                              + r'machine_lock(?![\w.])|' + KIT_WORD + r'machine_lock(?![\w.-])'),
     'names the lock CLI, and a part of it the guard cannot read may run one of its owner-only writes, which '
     'the owner runs, not agents.'),
    ('owner-delegations', re.compile(r'(?i)owner-delegations'),
     "names the owner's delegation list, and a part of it the guard cannot read may write, move or delete it, "
     'which only the owner does.'),
)
# the tools a subagent's role decides by name (named_runs): tools/backlog.py (backlog-role) and
# gate.py allow (gate-allow), as the script, -m tools.X or the kit command names them
BACKLOG_NAME = re.compile(r'(?i)(?<![\w.-])backlog\.py\b|\btools\.backlog(?![\w.])|' + KIT_WORD + r'backlog(?![\w.-])')
GATE_ALLOW_NAME = re.compile(r'(?i)(?:(?<![\w.-])gate\.py\b|\btools\.gate(?![\w.])|' + KIT_WORD + r'gate(?![\w.-]))'
                             r'[^;&|\n]*?(?<![\w.-])allow(?![\w.-])')
PARSED_RULES = ('owner_tool', 'wrapped_gate', 'root_search', 'heavy', 'git_rules', 'shell_writes')
WHY = 'The guard could not read this command; write the program name literally. Why: %s.'
# the alternative is a script file, never the line again nor a rewrite of it
SCRIPT = (' Make every program name and nested shell text literal (a nested bash -c in single quotes; a '
          'variable it needs exported), or write the commands to a script file with the Write tool, then run it:')
SCRIPT_RUNS = {'bash': ('script.sh', 'bash "%s"'),
               'powershell': ('script.ps1', 'powershell -NoProfile -File "%s"')}


def heavy_patterns(cfg):
    """(entry, regex) per heavy program; a match starts at the program word."""
    out = []
    for e in kconfig.heavy_programs(cfg):
        glob = fnmatch.translate(e['program'].lower())
        # fnmatch ends the pattern with \Z (\z from Python 3.14): the match here goes on
        body = re.sub(r'\\[Zz]', '', glob).replace('(?s:', '(?:')
        body = body.replace('.*', r'[^\s"\'/\\|;&]*')
        out.append((e, re.compile(r'(?i)(?<![^\s;&|("\'/\\])%s(?:\.exe)?(?=$|[\s"\';&|)])' % body)))
    return out


def segment(text, start):
    """The text from `start` to the next ; | & or newline, an unbalanced closing quote dropped."""
    m = re.compile(r'[;|&\n]').search(text, start)
    seg = text[start:m.start() if m else len(text)].rstrip()
    while seg and seg[-1] in '\'"' and seg.count(seg[-1]) % 2:
        seg = seg[:-1].rstrip()
    return seg


def segment_start(text, pos):
    """Where the part of `text` holding position `pos` starts: after the last ; | & or newline before it."""
    return max(text.rfind(c, 0, pos) for c in ';|&\n') + 1


def _dyn_commands(parsed):
    out = []
    for c in parsed.commands:
        inv = cm.invocation(c)
        if inv is not None and inv.dyn:
            out.append((c, inv))
    return out


def named_run(cmd, inv):
    """`<the program $X names> args` for a command whose program is a variable."""
    return ' '.join(['<the program %s names>' % inv.path] + [w.text for w in inv.args])


def plainly(ctx, parsed):
    """(the reason's last sentence, the alternative): advice only, a run of a script file in the
    temp folder for the line's shell; the guard never rewrites the refused line."""
    name, run = SCRIPT_RUNS['powershell' if parsed.shell == sh.PS else 'bash']
    return SCRIPT, run % rules.fwd(os.path.join(ctx.tempdir, name))


def cwd_at(parsed, pos, cwd):
    """The directory the top-level command holding text position `pos` runs in (a literal cd
    before it followed; None when a cd the text cannot place came first)."""
    best = None
    for c in parsed.commands:
        if c.depth == 0 and c.start is not None and c.start <= pos and (best is None or c.start >= best.start):
            best = c
    return cwd if best is None else best.cwd


def main_like(ctx, where):
    place = ctx.place(where) if where else None
    return place is None or place.kind in (repo.MAIN, repo.UNKNOWN)


def runs_hidden(inv):
    """Why a command may run what the text does not show, or None."""
    if inv is None:
        return None
    if inv.dyn:
        return 'a program the text does not name'
    name = inv.name.lower()
    if name in RUNNERS:
        return 'a command that runs text (%s)' % inv.name
    if name == 'git' and cm.git_dyn_word(inv):
        return 'a git subcommand or option the text does not name'
    return None


def named_reread(event, ctx, text, shell, parsed, pos, cwd, role, rule):
    """The NAMED rule's own deny of the part of the line holding text position `pos` (from the
    last ; | & or newline before it to the next), read again as a command of its own: a hub tool
    run there that runs an owner verb (rules.hub_owner_run), a lock CLI owner-only write
    (rules.owner_tool), or a write of the owner's delegation list (rules.owner_delegations); None
    when the part runs none of them."""
    start = segment_start(text, pos)
    where = cwd_at(parsed, pos, cwd) or cwd
    p = cm.read(segment(text, start), shell, where, home=getattr(ctx, 'home', None))
    if rule == 'hub-owner-verb':
        verb = rules.hub_owner_run(p)
        if not verb:
            return None
        return rules.refuse(rule, role, 'Its text names the hub tool and runs its owner verb (%s): %s This session '
                            'is %s. Ask the owner through a question:' % (verb, rules.HUB_OWNER_WHY,
                                                                         rules.hub_session(event, ctx)),
                            rules.hub_ask_alternative(ctx))
    named = rules.owner_delegations if rule == 'owner-delegations' else rules.owner_tool
    for c in p.commands:
        d = named(event, ctx, c, cm.invocation(c), cwd, role)
        if d:
            return d
    return None


def _read_at(parsed, text, pos):
    """The top-level command holding text position `pos` in a part of the line the rules have
    already judged, or None: a literal word (or a literal heredoc or redirect target) of a command
    whose program is literal and runs no text (RUNNERS). A name there is a mention (a commit
    message) or a run the rules read, unless the command's output is piped into a runner
    (_pipe_runner); a name in a computed word, a runner's text or a part no command holds is not."""
    for c in parsed.commands:
        if c.depth != 0 or c.source != text:
            continue
        inv = cm.invocation(c)
        if inv is None or inv.dyn or (inv.name or '').lower() in RUNNERS:
            continue
        for w in c.words:
            if w.start is not None and w.end is not None and w.start <= pos < w.end and not w.dyn:
                return c
        for r in c.redirects:
            w = r.target
            if w is not None and w.start is not None and w.end is not None and w.start <= pos < w.end:
                return c                        # a file or heredoc body the program reads as data
    return None


def _pipe_runner(parsed, c):
    """(the runner, the kind it reads its code in: shell.BASH, shell.PS, shell.CMD or 'py') when
    `c`'s output reaches, through its pipeline, a command that runs text (RUNNERS) or python with
    no script, -c or -m of its own (backlogrole.reads_stdin: its own code reads standard input as
    data); else None."""
    from . import backlogrole
    seen = {id(c)}
    cur = c
    while True:
        nxt = next((x for x in parsed.commands if x.pipe_from is cur and id(x) not in seen), None)
        if nxt is None:
            return None
        seen.add(id(nxt))
        inv = cm.invocation(nxt)
        name = (inv.name or '').lower() if inv is not None and not inv.dyn else ''
        if inv is not None and inv.dyn:
            return nxt, nxt.shell               # a program the text does not name: fail closed
        if name in cm.BASH_SHELLS or name == 'eval' or name in ('source', '.'):
            return nxt, sh.BASH if name in cm.BASH_SHELLS else nxt.shell
        if name in cm.PS_SHELLS or name in ('iex', 'invoke-expression'):
            return nxt, sh.PS
        if name == 'cmd':
            return nxt, sh.CMD
        if rules.PY_RUN_RE.match(name) and backlogrole.reads_stdin(inv) == 'py':
            return nxt, 'py'
        cur = nxt


def _piped_text(c, level):
    """The code `c` pipes into a runner, as written, or None when the text cannot be seen: its
    heredoc or here-string, or, at level 0, the files `cat F` or `Get-Content F` reads (at most
    SCRIPT_CAP of each, regular files only), joined by newlines."""
    from . import backlogrole
    for r in c.redirects:
        if r.op in ('<<', '<<<') and r.target is not None:
            return r.target.text
    files = None if level else backlogrole.reader_files(c)
    if not files:
        return None
    texts = []
    for f in files:
        _path, t = backlogrole._script_text(c, f)
        if not isinstance(t, str):
            return None
        texts.append(t)
    return '\n'.join(texts) if texts else None


def named_runs(event, ctx, text, parsed, cwd, role, level=0):
    """A subagent's line the guard cannot read that names tools/backlog.py (BACKLOG_NAME), or runs
    gate.py allow (GATE_ALLOW_NAME), where the rules have not read it (_read_at): the part of the
    line holding the name (from the last ; | & or newline before it to the next) is read again and
    judged by that rule (backlogrole.runs, gaterole.shell); a part that reads no more cleanly, or
    whose program is computed, is refused under that rule, as the guard cannot judge the run for the
    role. A name in what a command pipes into a runner (_pipe_runner) is that runner's code: its
    heredoc, here-string or the files cat or Get-Content reads (_piped_text, files at `level` 0
    only) are judged as a script file is (backlogrole.judge_text), and text it cannot see is
    refused. A main session, and a host's unidentified subagent for the backlog, are not judged
    here (the tool's own check is theirs). None when nothing is refused."""
    from . import backlogrole, gaterole
    checks = []
    if backlogrole._judged_subagent(event):
        checks.append((backlogrole.ROLE_RULE, BACKLOG_NAME, backlogrole.runs,
                       'names tools/backlog.py where the guard cannot read the run (%s), so it cannot judge the run '
                       'for your role (kitlib/backlog/roles.py). Run the tool\'s verb itself, its words literal:',
                       backlogrole.literal_form(ctx)))
    if rules.subagent(event) and not gaterole.may_allow(event, ctx):
        checks.append((gaterole.RULE, GATE_ALLOW_NAME, gaterole.shell,
                       'names gate.py allow where the guard cannot read the run (%s): ' + gaterole.WHY,
                       gaterole.INSTEAD))
    if not checks:
        return None
    why = WHY % ('; '.join(parsed.unclean) or 'the parser gave no reason')
    for rule, pat, judge, what, instead in checks:
        if modes.mode(rule) == modes.OFF:
            continue
        for m in pat.finditer(text):
            held = _read_at(parsed, text, m.start())
            pipe = held is not None and _pipe_runner(parsed, held)
            if pipe:
                runner, kind = pipe
                code = _piped_text(held, level)
                head = '`%s` pipes into %s' % (rules.hub_line(rules.bare(held)), rules.bare(runner))
                if code is None:
                    d = rules.refuse(rule, role, '%s %s, text the guard cannot see; its text %s' % (
                        why, head, what % 'a command piped into a runner'), instead)
                else:
                    d = backlogrole.judge_text(event, ctx, held, cwd, role, kind, code, head, level=level + 1)
                    d = d and rules.reworded(d, '%s %s' % (why, d.reason))
                if d:
                    return d
                continue
            if held is not None:
                continue
            where = cwd_at(parsed, m.start(), cwd) or cwd
            p = cm.read(segment(text, segment_start(text, m.start())), parsed.shell, where,
                        home=getattr(ctx, 'home', None))
            unread = list(p.unclean)
            for c in p.commands:
                inv = cm.invocation(c)
                d = judge(event, ctx, c, inv, where, role)
                if d:
                    return rules.reworded(d, '%s %s' % (why, d.reason))
                if inv is not None and inv.dyn:
                    unread.append('a program the text does not name (%s)' % inv.path)
            if not unread:
                continue                        # read whole, and the rule let it run
            # the tool's own literal form, never a script file: a script file it runs is read too
            d = rules.refuse(rule, role, '%s Its text %s' % (why, what % '; '.join(unread)), instead)
            if d:
                return d
    return None


def check(event, ctx, text, cwd, role, parsed):
    why = WHY % ('; '.join(parsed.unclean) or 'the parser gave no reason')
    shell = parsed.shell

    def reread(start, where):
        p = cm.read(segment(text, start), shell, where, home=getattr(ctx, 'home', None))
        for cmd in p.commands:
            inv = cm.invocation(cmd)
            for name in PARSED_RULES:
                d = getattr(rules, name)(event, ctx, cmd, inv, cwd, role)
                if d:
                    return d
        return None

    def answer(d, rule):
        if d is not None:
            return rules.reworded(d, '%s %s' % (why, d.reason))
        last, alt = plainly(ctx, parsed)
        return rules.refuse('unreadable', role, '%s Its text matches the %s rule.%s' % (why, rule, last), alt)

    owner = rules.is_owner_session(event, ctx)
    for rule, pat, what in () if owner else NAMED:
        if modes.mode(rule) == modes.OFF:
            continue
        m = pat.search(text)
        if not m:
            continue
        d = named_reread(event, ctx, text, shell, parsed, m.start(), cwd, role, rule)
        if d is not None:
            return rules.reworded(d, '%s %s' % (why, d.reason))
        last, alt = plainly(ctx, parsed)
        d = rules.refuse(rule, role, '%s Its text %s This session is %s.%s' % (
            why, what, rules.hub_session(event, ctx), last), alt)
        if d:
            return d                            # set to warn by the owner: the checks below still run
    d = named_runs(event, ctx, text, parsed, cwd, role)
    if d:
        return d

    main_rules = set(r for r, _p in MAIN_ONLY + SUBAGENT_MAIN)
    for rule, pat in list(ANYWHERE) + list(MAIN_ONLY) + list(SUBAGENT_MAIN):
        if modes.mode(rule) == modes.OFF:
            continue                            # an off rule: its text is no reason to warn either
        ungated = False
        if rule == 'git-push-main':
            cfg = ctx.config(cwd) if cwd else None
            if cwd and ctx.is_pm_data(cwd):      # the kit's .kit.json decides; unloadable: read it again
                kit, _why = rules.kit_judged(ctx, cwd)
                cfg = kit.config(cwd) if kit is not None else None
            ungated = cfg is not None and not cfg.push_block
        m = re.search(pat, text)
        if not m:
            continue
        where = cwd_at(parsed, m.start(), cwd)
        if rule in main_rules and not main_like(ctx, where):
            continue
        if rule == 'owner-tool' and rules.is_owner_session(event, ctx):
            continue
        if rule == 'git-shared-ref' and not rules.shared_ref_held(event, ctx):
            continue
        if rule == 'git-push-pmdata' and (rules.is_owner_session(event, ctx) or (
                pat != XARGS_BARE_GIT and not rules.pmdata_recorded(ctx, where or cwd))):
            continue
        d, first = None, segment_start(text, m.start()) if rule in SEGMENT_RULES else m.start()
        if first != m.start():                  # the words before the name too (a kit command's)
            d = reread(first, where or cwd)
        if d is None:                           # from the match too: an unreadable start may hide the run
            d = reread(m.start(), where or cwd)
        if ungated and d is None:
            continue                            # no push_block: only a delete or a forced push of main is refused
        d = answer(d, rule)
        if d:
            return d                            # unreadable warns by default: the patterns after it still run
    held = None
    cfg = ctx.config(cwd) if cwd else None
    for entry, pat in heavy_patterns(cfg):
        m = pat.search(text)
        if not m or (entry.get('when_args') and not any(a in text for a in entry['when_args'])) or any(
                not any(a in text for a in wanted) for wanted in entry.get('also_args', ())):
            continue
        if held is None:
            held = ctx.lock_held()[0]
        if held:
            break
        dyn = _dyn_commands(parsed)
        if dyn:
            c, inv = dyn[0]
            d = rules.refuse('heavy', role, '%s Its text names %s, a heavy program (kind %s), and this session '
                             'holds no machine lock.%s' % (why, entry['program'], entry['kind'],
                                                           rules.run_from(c, cwd)),
                             rules.lock_alternative(ctx, c, entry, cwd, named_run(c, inv)))
            if d:
                return d
            continue                            # heavy warns by default: the checks below still run
        d = reread(m.start(), cwd_at(parsed, m.start(), cwd) or cwd)
        if d is not None:
            return answer(d, 'heavy')
    last, alt = plainly(ctx, parsed)
    if role == 'subagent':
        d = rules.refuse('unreadable', role, why + last, alt)
        if d:
            return d                            # warned by default: judged below as a jailed session is
    if GIT_WORD_RE.search(text) and rules.agent_session(event, ctx):
        d = rules.refuse(rules.UNPLACED, role, '%s git on a line the guard cannot read may write a checkout it '
                         'cannot place; run the git in its own call.%s' % (why, last), alt)
        if d:
            return d                            # warned: the PM data and main-checkout checks below still run
    for c in parsed.commands:
        inv = cm.invocation(c)
        hidden = runs_hidden(inv)
        if not hidden:
            continue
        if cm.call_subexpr(inv):
            # `& (...)`: judged by rules.call_subexpr_pmdata (PM data, then agent sessions), never unreadable
            d = rules.call_subexpr_pmdata(event, ctx, c, inv, cwd, role)
            if d:
                return rules.reworded(d, '%s %s' % (why, d.reason))
            continue
        if main_like(ctx, c.cwd):
            d = rules.refuse('unreadable', role, '%s It runs %s in a main checkout or a place git cannot '
                             'tell.%s' % (why, hidden, last), alt)
            if d:
                return d                        # warned by default: the PM data check below still runs
        if not owner and rules.pmdata_recorded(ctx, c.cwd):
            return rules.refuse('git-push-pmdata', role, '%s It runs %s in a repository with a PM data record (%s): '
                                'it may be git push --no-verify, which skips the pre-push hook that keeps PM data '
                                'off public remotes.%s' % (why, hidden, rules.pmdata.FILE_NAME, last), alt)
    return None
