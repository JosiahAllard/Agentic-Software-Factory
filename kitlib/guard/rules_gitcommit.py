"""The guard's git commit, interpreter and stash rules (kitlib.guard.rules, which re-exports each name
here): git verbs that make a commit without commit.py (git-plain-commit), git run by python or node
code given on the line (interpreter_git), and stash in a linked worktree (git-stash-shared)."""
import re
from kitlib.git import repo

from .rules_base import agent_session, bare, kept_word, kit_tool, own_worktree, quote, refuse
from .rules_owner import hub_line, inline_code
from .rules_gitcore import _opts, git_call_judged


# git verbs that make a commit, each refused for an agent session in a linked worktree
# (git_plain_commit): plain git writes no Co-Authored-By trailer, so the gate's trailer check fails
# the branch. Known limits, none read here:
# - a git alias from git's config files (an inline -c alias.<name> is expanded);
# - a verb the shell computes or a script runs;
# - commit-tree and rebase;
# - a computed -C, --git-dir or --work-tree: no place is known, so the unplaced handling and
#   git-main-history judge the line, not this rule;
# - a --continue of a revert or cherry-pick sequence started with --no-commit makes no commit, but
#   it is refused like any other: git keeps that mode in the sequence's state, which is not read;
# - a word the shell computes after a hold (`git cherry-pick -n $X`) is read as a commit, though
#   it may be an option that undoes the hold (`--commit`): a computed commit is the common case,
#   so failing closed there would refuse honest lines;
# - commit.py commits only the change in progress of a revert, cherry-pick or am sequence: it does
#   not go on with the commits or patches left, so the refusal says to end the sequence (--quit).
COMMIT_VERBS = ('commit', 'merge', 'revert', 'cherry-pick', 'am', 'pull')
# the words after which a verb makes no commit, spelled exactly
COMMIT_STOPS = {'commit': ('--dry-run',), 'merge': ('--abort', '--quit'), 'revert': ('--abort', '--quit'),
                'cherry-pick': ('--abort', '--quit'),
                'am': ('--abort', '--quit', '--show-current-patch', '--show-current-patch=diff',
                       '--show-current-patch=raw')}
# the options, spelled exactly, that leave the verb's change uncommitted; an abbreviation is none
# (revert's and cherry-pick's -n is read as --no-commit)
COMMIT_HOLDS = {'merge': ('--no-commit', '--squash', '--ff-only'), 'pull': ('--no-commit', '--squash', '--ff-only'),
                'revert': ('--no-commit',), 'cherry-pick': ('--no-commit',)}
# the long options that undo a hold (a prefix of one counts): the last of --commit and --no-commit
# (or -n) wins, of --squash and --no-squash, and of --ff, --no-ff and --ff-only
COMMIT_UNDOES = {'commit': '--no-commit', 'no-squash': '--squash', 'ff': '--ff-only', 'no-ff': '--ff-only'}
# the words that go on with a sequence: the commit is made by commit.py, not by the verb again
COMMIT_STEPS = ('--continue', '--skip', '--resolved')
# the short options that take a value (attached, or the next word), per verb (git 2.5x)
COMMIT_SHORT_VALUES = {'commit': 'mFcCt', 'merge': 'mFsX', 'pull': 'sXj', 'revert': 'mX', 'cherry-pick': 'mX',
                       'am': 'pC'}
# the short options whose value, if any, is attached only (-S<key>, commit's -u<mode>): each ends
# its cluster and takes no next word
COMMIT_SHORT_ATTACHED = {'commit': 'Su', 'merge': 'S', 'pull': 'S', 'revert': 'S', 'cherry-pick': 'S', 'am': 'S'}
# the short options that take no value, per verb: any other letter may take the rest of its cluster
# or the next word, so a hold after it is not counted (commit_scan)
COMMIT_SHORT_FLAGS = {'commit': 'apsnevqzio', 'merge': 'nqve', 'pull': 'nqvrfkpa46t', 'revert': 'ens',
                      'cherry-pick': 'enxs', 'am': 'skcq3imunrb'}
# the long options that take a value in the next word when no `=` gives it
COMMIT_LONG_VALUES = {'message', 'file', 'strategy', 'strategy-option', 'cleanup', 'into-name', 'mainline',
                      'depth', 'deepen', 'shallow-since', 'shallow-exclude', 'upload-pack', 'jobs',
                      'negotiation-tip', 'server-option', 'refmap', 'reuse-message', 'reedit-message',
                      'fixup', 'squash', 'author', 'date', 'template', 'trailer', 'pathspec-from-file',
                      'directory', 'exclude', 'include', 'patch-format', 'quoted-cr', 'whitespace', 'resolvemsg',
                      'empty'}
# the options of a revert or cherry-pick that its --no-commit form keeps (commit_scan's `keep`)
COMMIT_KEPT_LONG = ('strategy', 'strategy-option')
# the long options, spelled in full, that take no next word; any other long option without `=`
# (one git does not know, or an abbreviation, which may be of one that takes a value) may take the
# next word, so a hold after it is not counted (commit_scan)
COMMIT_LONG_FLAGS = {
    'commit', 'no-commit', 'squash', 'no-squash', 'ff', 'no-ff', 'ff-only', 'edit', 'no-edit', 'signoff',
    'no-signoff', 'stat', 'no-stat', 'summary', 'no-summary', 'log', 'no-log', 'verbose', 'quiet', 'progress',
    'no-progress', 'verify', 'no-verify', 'verify-signatures', 'no-verify-signatures', 'gpg-sign', 'no-gpg-sign',
    'allow-unrelated-histories', 'autostash', 'no-autostash', 'rerere-autoupdate', 'no-rerere-autoupdate',
    'overwrite-ignore', 'no-overwrite-ignore', 'rebase', 'no-rebase', 'all', 'append', 'force', 'keep', 'prune',
    'tags', 'no-tags', 'unshallow', 'update-shallow', 'ipv4', 'ipv6', 'recurse-submodules',
    'no-recurse-submodules', 'allow-empty', 'allow-empty-message', 'keep-redundant-commits', 'no-reference',
    'amend', 'no-post-rewrite', 'reset-author', 'short', 'branch', 'porcelain', 'long', 'null', 'only',
    'interactive', 'patch', 'status', 'no-status', '3way', 'no-3way', 'keep-cr', 'no-keep-cr', 'scissors',
    'no-scissors', 'utf8', 'no-utf8', 'keep-non-patch', 'ignore-space-change', 'ignore-whitespace',
    'committer-date-is-author-date', 'ignore-date', 'message-id', 'no-message-id', 'reject', 'retry', 'binary'}


def commit_scan(sub, args):
    """(makes, revs, mainline, keep): whether git `sub` (COMMIT_VERBS) with the Words `args` after
    it makes a commit, the words that are neither options nor their values (a revert's or
    cherry-pick's commits; None after an option the scan cannot place that may take a value, so a
    word after it may be that value),
    the -m/--mainline value given, or None, and the strategy options of a revert or cherry-pick
    (-X, COMMIT_KEPT_LONG) its --no-commit form keeps, as kept_word's (lead, sep, value, dyn). Read left to right as git
    reads them: a stop word (COMMIT_STOPS) makes none; a hold (COMMIT_HOLDS, or -n in a short
    cluster of revert or cherry-pick; merge's and pull's -n is --no-stat) holds until a long option
    that undoes it (COMMIT_UNDOES), or a prefix of one, comes after it; a sequence's next step
    (COMMIT_STEPS) commits. Option values are skipped, so `-m --no-commit` is a message; `--` ends
    the options. It fails closed: after a word it cannot place (a computed word, a long option
    without `=` that COMMIT_LONG_FLAGS and COMMIT_LONG_VALUES do not name, or a short letter
    COMMIT_SHORT_FLAGS does not), no later hold or stop word counts, since that word may have taken
    it as its value."""
    stops, holds = COMMIT_STOPS.get(sub, ()), COMMIT_HOLDS.get(sub, ())
    values, attached = COMMIT_SHORT_VALUES.get(sub, ''), COMMIT_SHORT_ATTACHED.get(sub, '')
    flags = COMMIT_SHORT_FLAGS.get(sub, '')
    held, revs, mainline, unsure, keep, unplaced = set(), [], None, False, [], False
    k = 0
    while k < len(args):
        w = args[k]
        t = w.text
        k += 1
        if w.dyn or not t.startswith('-') or t == '-':
            revs.append(t)
            unsure = unsure or w.dyn
            continue
        if t == '--':
            revs.extend(x.text for x in args[k:])
            break
        if t.startswith('--'):
            body, eq, val = t[2:].partition('=')
            if t in stops and not unsure:
                return False, None if unplaced else revs, mainline, keep
            if t in COMMIT_STEPS:
                return True, None if unplaced else revs, mainline, keep
            if t in holds:
                if not unsure:
                    held.add(t)
                continue
            if len(body) >= 2:
                held -= {h for n, h in COMMIT_UNDOES.items() if n.startswith(body)}
            kept = body in COMMIT_KEPT_LONG and sub in ('revert', 'cherry-pick')
            if eq:
                if body == 'mainline':
                    mainline = val
                if kept:
                    keep.append(('--%s=' % body, '', val, False))
                continue
            if body in COMMIT_LONG_VALUES:
                if body == 'mainline' and k < len(args):
                    mainline = args[k].text
                if kept and k < len(args):
                    keep.append((t, ' ', args[k].text, args[k].dyn))
                k += 1
            elif body not in COMMIT_LONG_FLAGS:
                unsure = True
                # an abbreviation of flags alone takes no value, so the words after it stay commits
                unplaced = unplaced or any(n.startswith(body) for n in COMMIT_LONG_VALUES) or \
                    not any(n.startswith(body) for n in COMMIT_LONG_FLAGS)
            continue
        for j, c in enumerate(t[1:]):
            if c in values:
                rest, dyn = t[j + 2:], False
                if not rest:
                    rest, dyn = (args[k].text, args[k].dyn) if k < len(args) else ('', False)
                    k += 1
                if c == 'm' and sub in ('revert', 'cherry-pick'):
                    mainline = rest
                if c == 'X' and sub in ('revert', 'cherry-pick'):
                    keep.append(('-X', '', rest, dyn))
                break
            if c in attached:
                break
            if c not in flags:
                unsure = unplaced = True
                break
            if c == 'n' and sub in ('revert', 'cherry-pick') and not unsure:
                held.add('--no-commit')
    return sub in ('commit', 'am') or not held, None if unplaced else revs, mainline, keep


def git_plain_commit(event, ctx, cmd, inv, cwd, role):
    """A git verb that makes a commit (COMMIT_VERBS, commit_scan), run by an agent session
    (agent_session) in a linked worktree: plain git writes no Co-Authored-By trailer, so the gate's
    trailer check fails the branch. It runs after every other shell rule, so a refusal another
    rule owns (owner-identity, write-jail and the rest) stands. The alternative is the kit tool
    that does the job with the trailer: commit.py for a commit or a sequence's next step,
    sync_base.py for a merge or pull, the verb's --no-commit form (its -m, -X and --strategy kept) and then commit.py
    for a revert or cherry-pick, git apply --index and then commit.py for am. A --skip drops the
    change in progress and goes on with plain commits: the alternative ends the sequence, drops the
    change and takes what is left one at a time."""
    gc = git_call_judged(inv)
    if gc is None or gc.sub not in COMMIT_VERBS or not gc.dir or gc.unknown or gc.blind:
        return None
    if not agent_session(event, ctx):
        return None
    place = ctx.place(gc.dir)
    if place.kind != repo.LINKED or not place.top:
        return None
    alt = commit_alternative(ctx, gc, place.top)
    if alt is None:
        return None
    why = '`%s` makes a commit with plain git, which writes no Co-Authored-By trailer: the gate\'s trailer check ' \
          'then fails the branch. ' % bare(cmd)
    return refuse('git-plain-commit', role, why + alt[0], alt[1])


# a commit hash as typed: 7 to 40 hex digits (git's default abbreviation up to a full SHA-1)
TYPED_HASH_RE = re.compile(r'[0-9a-fA-F]{7,40}')


def typed_hash(revs):
    """The one commit hash among a merge's or pull's words `revs` (commit_scan), which sync_base.py
    then merges; `<hash>` when there is none, more than one, or the words could not be placed
    (None). A branch name (origin/main) is never resolved: the coordinator names the hash."""
    found = [r for r in revs or () if TYPED_HASH_RE.fullmatch(r)]
    return found[0] if len(found) == 1 else '<hash>'


def commit_alternative(ctx, gc, where):
    """(advice, command) for git `gc` when it makes a commit (COMMIT_VERBS, commit_scan), run in
    the linked worktree `where`: the kit tool that does the job with the trailer (git_plain_commit
    names the forms); None when it makes no commit."""
    if gc.sub not in COMMIT_VERBS:
        return None
    sub = gc.sub
    makes, revs, mainline, kept = commit_scan(sub, gc.rest)
    if not makes:
        return None
    top = quote(where)
    commit = 'python %s -C %s -m "<subject>" --model "<your model>" <each path you changed>' % (
        kit_tool(ctx, 'commit'), top)
    words = {w.text for w in gc.rest}
    one = 'git -C %s apply --index <patch>' % top if sub == 'am' else \
        'git -C %s %s --no-commit <commit>' % (top, sub)
    # --quit forgets the commits or patches still to come: the advice says where to read them first
    left = 'the patches from the one in progress on are in the folder git -C %s rev-parse --git-path ' \
        'rebase-apply names' % top if sub == 'am' else \
        'the commits still to come are listed in the file git -C %s rev-parse --git-path sequencer/todo ' \
        'names' % top
    note = ' --quit forgets what is left, so read it first: %s.' % left
    if sub in ('revert', 'cherry-pick', 'am') and '--skip' in words:
        return ('--skip drops the change in progress and goes on with the rest as plain commits.%s End the sequence '
                'here, then in their own calls drop the change in progress with git -C %s reset --merge and take '
                'each one left with %s, then %s:' % (note, top, one, commit),
                'git -C %s %s --quit' % (top, sub))
    if sub == 'commit' or words & set(COMMIT_STEPS) or (sub == 'am' and '-r' in words):
        rest = ''
        if sub in ('revert', 'cherry-pick', 'am'):
            rest = ' The kit tool commits only the change in progress: it does not go on with the rest of the ' \
                   'sequence. After it, end the sequence with git -C %s %s --quit and take each one left with %s, ' \
                   'then commit.py.%s' % (top, sub, one, note)
        return 'Commit with the kit tool, which stages the paths you name and adds the trailer.%s The commit:' % rest, \
            commit
    if sub in ('merge', 'pull'):
        return ('Sync with the commit your coordinator named; the tool merges exactly it and adds the trailer (to '
                'merge anything else, run git -C %s merge --no-commit <rev>, then in its own call %s):' % (top, commit),
                'python %s -C %s %s --model "<your model>"' % (kit_tool(ctx, 'sync_base'), top, typed_hash(revs)))
    if sub == 'am':
        return ('Apply the patch to the index, then in its own call commit it with %s:' % commit,
                'git -C %s apply --index <patch>' % top)
    keep = ''
    if mainline is not None:
        keep = ' -m %s' % (mainline if mainline.isdigit() else '<n>')
    keep += ''.join(' ' + kept_word(*w) for w in kept)
    return ('Run it with --no-commit, then in its own call commit what it changed with %s:' % commit,
            'git -C %s %s --no-commit%s %s' % (top, sub, keep, ' '.join(revs or ()) or '<commit>'))


# git that python or node code given on the line runs (interpreter_git): the guard reads git only
# as a typed shell command, so every git rule (commit.py's trailer, the shared refs, the main
# checkout) is blind to it. Known limits, none read here:
# - a script file (one in a temp or scratch folder too) and python -m: only code on the line
#   (-c, -e) or on standard input (a heredoc, a here-string, text piped in) is read;
# - a program or a git name the code builds at run time ('g' + 'it', importlib, getattr);
# - other interpreters (ruby, perl, deno) and compiled programs; PowerShell's Start-Process and
#   .NET's Process;
# - a library that writes the repository itself (dulwich, pygit2, open('.git/refs/...'));
# - a line the parser cannot read: the fallback does not run this rule;
# - a git command that is not at the start of its string ('cd x && git commit', shell=True);
# - a git name the code is given from outside its text: an argument (python -c
#   "...subprocess.run(sys.argv[1:])" git commit) or an environment variable (G=git python -c
#   "...os.environ['G']...");
# - python or node run by a launcher (uv run python -c ..., npx node -e ...): the line's program
#   is the launcher, and its arguments are not read as code;
# - an option that takes the next word as its value and is not named (python's
#   --check-hash-based-pycs always, a node option outside NODE_VALUE_OPTS): its value is read as
#   the script, so the code after it is not read.
# It refuses honest code in two ways, both kept: a git read (rev-parse, log) is refused like a
# write, since a read allowance would pass a verb the code computes; and a 'git ...' string in code
# whose launch runs another program is read as git.
# a call that starts a program: subprocess, os.system and its kin, asyncio's, node's child_process
INTERPRETER_LAUNCH_RE = re.compile(r'\b(subprocess|os\.(system|popen|exec\w*|spawn\w*|posix_spawn\w*)|from os import'
                                   r'|pty\.spawn|create_subprocess_\w+|child_process)\b')
# git named as a program: a quoted string that is git or git.exe (a path before it allowed), or
# starts with `git ` (a list's first element, a shell=True string, an f-string, shutil.which('git'))
INTERPRETER_GIT_RE = re.compile(r'''(['"`])(?:[^'"`\s]*[\\/]|[A-Za-z]:[\\/][^'"`\n]*[\\/])?git(?:\.exe)?'''
                                r'''(?:\1|\s)''', re.I)
# a git verb near the git name, which only chooses the alternative's line
INTERPRETER_VERB_RE = re.compile(r'''(?:['"`]|\bgit(?:\.exe)?\s+(?:-C\s+\S+\s+)?)'''
                                 r'''(commit-tree|commit|revert|cherry-pick|am|merge|pull)(?=['"`\s])''')
INTERPRETER_COMMITS = ('commit-tree', 'commit', 'revert', 'cherry-pick', 'am')


def interpreter_git(event, ctx, cmd, inv, cwd, role):
    """git run by python or node code given on the line (rules.inline_code: -c, -e, a heredoc, a
    here-string, text piped in), in an agent session (agent_session), from any directory: one
    text of that code that both starts a program (INTERPRETER_LAUNCH_RE) and names git as one
    (INTERPRETER_GIT_RE). The guard reads git only as a typed shell command, so no git rule judges
    git that code runs. It runs after every other shell rule, so a refusal another rule owns (the
    main checkout's unplaced code, owner-identity and the rest) stands. The alternative is the git
    command typed: commit.py for a commit the code makes, sync_base.py for a merge or pull, else
    `git -C <worktree> <the git command your code ran>`; a git verb in the code only chooses it."""
    if not agent_session(event, ctx):
        return None
    texts = [t for t in inline_code(cmd, inv, skip_pipe=lambda c: True)
             if INTERPRETER_LAUNCH_RE.search(t) and INTERPRETER_GIT_RE.search(t)]
    if not texts:
        return None
    verbs = {m.group(1) for t in texts for m in INTERPRETER_VERB_RE.finditer(t)}
    top = own_worktree(ctx, cwd)
    wt = quote(top) if top else '"<worktree>"'
    commit = 'python %s -C %s -m "<subject>" --model "<your model>" <each path you changed>' % (
        kit_tool(ctx, 'commit'), wt)
    sync = 'python %s -C %s <hash> --model "<your model>"' % (kit_tool(ctx, 'sync_base'), wt)
    which = 'node' if inv.name == 'node' else 'python'
    why = ('`%s` runs git from %s code. The guard reads git only as a typed shell command, so none of its git '
           'rules (the shared refs, the main checkout, the commit trailer) judge git that code runs. Type the git '
           'command itself, one per call; a commit goes through the kit\'s commit.py and a sync through its '
           'sync_base.py. If the guard refuses the typed git command, that refusal stands: do not route it through '
           'a program or a script file.' % (hub_line(bare(cmd)), which))
    if verbs & set(INTERPRETER_COMMITS):
        return refuse('interpreter-git', role, why + ' The commit:', commit)
    if verbs & {'merge', 'pull'}:
        return refuse('interpreter-git', role, why + ' The sync:', sync)
    return refuse('interpreter-git', role, why + ' The git command:',
                  'git -C %s <the git command your code ran>' % wt)


STASH_READS = ('list', 'show', 'create')        # no entry added or taken
STASH_NAMED = ('apply', 'drop')                 # pass with the entry named: `apply <sha>`, `drop stash@{1}`


def git_stash_shared(event, ctx, cmd, gc, role, place):
    """git stash in a linked worktree by an agent session (agent_session), when it adds an entry
    with no tag or takes one it does not name: every worktree of the repository shares the stash
    stack, so a bare `pop` can take another session's entry, and while the stack holds any entry
    the gate's stash guard refuses every gate and landing (docs/gate.md "The stash guard").
    Passes: list, show, create, a tagged push (-m/--message), apply or drop naming the entry,
    branch <name> <stash>. git reads a verb only as the first word after `stash`: a line that
    starts with an option or `--` is a push (`git stash -- show` stashes the path `show`) or one
    git refuses (`git stash -q list`), so only the first word is taken as the verb. An entry or branch name the shell computes
    (`drop $E`) fails closed: empty, it leaves a bare drop of the top entry. A main checkout's
    stash is git-stash's (git_main_rules)."""
    if not agent_session(event, ctx) or not stash_shared(gc):
        return None
    how, instead = stash_alternative(ctx, place.top)
    return refuse('git-stash-shared', role, '`%s` adds to or takes from the stash stack every worktree of this '
                  'repository shares; while it holds an entry every gate and landing refuses (docs/gate.md "The '
                  'stash guard"). %s' % (bare(cmd), how), instead)


def stash_shared(gc):
    """True when git stash `gc` adds an entry with no tag or takes one it does not name
    (git_stash_shared)."""
    first = gc.rest[0] if gc.rest else None
    if first is not None and (first.dyn or not first.text.startswith('-')):
        verb, args = first, gc.rest[1:]
    else:
        verb, args = None, gc.rest              # no verb, or an option or `--` first: a push (or a refused line)
    short, longs, pos = _opts('stash', args)
    if verb is not None and not verb.dyn:
        need = 2 if verb.text == 'branch' else 1
        named = len(pos) >= need and not any(w.dyn for w in pos[:need])
        if verb.text in STASH_READS or (verb.text in STASH_NAMED + ('branch',) and named):
            return False
    if (verb is None or (verb.text == 'push' and not verb.dyn)) and \
            ('m' in short or any(n and 'message'.startswith(n) for n in longs)):
        return False                            # a tagged push: its owner finds it again by the tag
    return True


def stash_alternative(ctx, where):
    """(advice, command): the WIP commit with the kit tool in the linked worktree `where` that
    takes the place of a shared stash entry (git_stash_shared)."""
    return ('Set the work aside as a WIP commit on your branch with the kit tool, which stages each path you name, '
            'a new file too, and adds the trailer:',
            'python %s -C %s -m "WIP: <what>" --model "<your model>" <each path you changed>'
            % (kit_tool(ctx, 'commit'), quote(where)))
