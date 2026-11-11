"""The checks a commit makes before anything is written (kitlib.commit, whose docstring holds the
rules): the paths, the owner-only rules, the author identity, what is already staged, a merge in
progress (merge_heads, landed_main, merge_owner_refusal), a machine path in the message or the
staged copy, and the main checkout. Each returns a refusal or raises _Stop; the exit codes are
here. Imports nothing from kitlib.commit."""
import os
import posixpath

from kitlib import config as kconfig
from kitlib import machinepaths
from kitlib.config import owner as ownermod
from kitlib.git import checkout, refs, repo

from kitlib.ownerfiles import rules as ownerrules


OK, REFUSED, USAGE = 0, 1, 2


TOOL = 'commit.py'


norm = ownermod.norm


class _Stop(Exception):
    def __init__(self, code, *lines):
        Exception.__init__(self, code)
        self.code = code
        self.lines = [l for l in lines if l is not None]


def repo_path(path, root):
    """A path as typed, in the repo-relative, forward-slash form `git diff --cached` reports: an
    absolute path is made relative to `root`, None when outside it (or on another drive). A
    relative path has its slashes flipped and its `./` and `..` segments folded, so
    `./CLAUDE.md` and `tools/../CLAUDE.md` reach the owner-only check as the `CLAUDE.md` that
    `git add` stages; one that climbs out of the work tree is None too. Case is left alone."""
    p = path.replace('\\', '/')
    if not os.path.isabs(p):
        p = posixpath.normpath(p)
        return None if p == '..' or p.startswith('../') else p
    try:
        rel = os.path.relpath(os.path.abspath(p), root).replace('\\', '/')
    except ValueError:
        return None
    if rel == '..' or rel.startswith('../'):
        return None
    return rel


def locked_paths(rules, paths):
    """The owner-only paths among `paths`."""
    return [p for p in paths if rules.is_locked(p)]


def shrunk_paths(root, rules, paths, revs=('HEAD',)):
    """The `"owner".shrink_only` paths among `paths` whose change `git diff <revs>` shows only
    deletes lines (kitlib.ownerfiles.rules.deletes_only, the one predicate gate land and
    pre-push judge the commit by): `HEAD`, the work tree's, before staging; `--cached <head>`, the
    staged copy _stage commits. A git failure shrinks nothing."""
    return {p for p in paths if rules.is_shrink_only(p)
            and ownerrules.deletes_only(root, p, list(revs))[0]}


def owner_only_refusal(locked, carried=()):
    """The refusal of an owner-only path committed without the owner (`carried`: the paths a merge
    in progress would carry unnamed, named in a hint)."""
    return ('refused: %s is owner-only, and only a commit the owner authors (their own git '
            'identity, --owner-authored) can carry its approval. Propose the change to the owner.%s '
            'Or ask the owner on the hub (hub.py approvals add): the hub commits it once approved.' % (
                ', '.join(locked), (' (A merge in progress carries the merged branch\'s own copy '
                                    'of %s without it being named: leave it out of your list.)'
                                    % ', '.join(carried)) if carried else ''))


def unapproved_locked(rules, locked, owner_approved):
    """The locked paths no --owner-approved value covers (the exact path, or its blanket)."""
    return [p for p in locked if not rules.approved_by(p, owner_approved)]


def model_requirement_error(rules, owner_authored, owner_approved, paths):
    """None unless an --owner-authored commit's --owner-approved coverage fails: every value must
    name a blanket entry or one of the paths this call commits (an empty or unrelated value must
    not silently pass). No --owner-approved at all is refused only when an owner-only path is in
    the commit."""
    if not owner_authored:
        return None
    named = [norm(p) for p in paths]
    unrelated = [o for o in owner_approved
                 if not o.strip() or (not rules.is_blanket(o) and norm(o) not in named)]
    if (not owner_approved and locked_paths(rules, paths)) or unrelated:
        return ('--owner-authored requires every --owner-approved value to be a blanket '
                'directory entry or one of the paths committed in this call%s' % (
                    ' (not: %s)' % ', '.join(repr(o) for o in unrelated) if unrelated else ''))
    return None


def author_email(root):
    """The author email git will record for a commit in `root`, or None when git cannot say.
    `git var GIT_AUTHOR_IDENT` honours the GIT_AUTHOR_* environment."""
    rc, out, _ = ownerrules.run_git(root, 'var', 'GIT_AUTHOR_IDENT')
    email = ownermod.email_of(out.strip()) if rc == 0 else ''
    return email or None


def owner_identity_error(rules, email):
    """None when `email` is provably the owner's under `rules`, else the refusal message."""
    problem = rules.identity_problem()
    if problem:
        return 'refused: --owner-authored, but %s. Nothing was committed.' % problem
    if email is None:
        return ('refused: --owner-authored, but git could not say which author identity it would '
                'record (git var GIT_AUTHOR_IDENT failed). Nothing was committed.')
    if email in rules.agent_emails:
        return ('refused: --owner-authored, but the author identity %s is an agent\'s '
                '("owner".agent_emails). The owner commits from their own terminal. Nothing was '
                'committed.' % email)
    if email != rules.owner_email:
        return ('refused: --owner-authored, but the author identity %s is not the owner\'s (%s). '
                'Nothing was committed.' % (email, rules.owner_email))
    return None


def staged_status(root):
    """`{path: status letter}` of every staged change (renames off: A, M or D only); raises _Stop
    when git cannot list them."""
    rc, out, err = ownerrules.run_git(root, 'diff', '--cached', '--name-status', '--no-renames',
                                      '-z')
    if rc != 0:
        raise _Stop(REFUSED, 'git diff --cached failed: %s' % (err or 'exit %s' % rc))
    cols = [c for c in out.split('\0') if c]
    return {cols[i + 1]: cols[i][:1] for i in range(0, len(cols) - 1, 2)}


def merge_heads(root):
    """The commits a merge in progress is bringing in (MERGE_HEAD, one per line), or []."""
    path, _why = repo.git_path(root, 'MERGE_HEAD')
    if not path:
        return []
    try:
        with open(path, encoding='utf-8') as fh:
            return [ln.strip() for ln in fh.read().splitlines() if ln.strip()]
    except OSError:
        return []


def landed_main(root, remote, main):
    """`[the commit of <remote>/<main> as last fetched]` when it resolves, else `[]`: the commits
    already on main, which `gate land` and pre-push judged when they landed. The same ref pre-push
    trusts (kitlib.guard.prepush); no network."""
    if not remote or refs.bad_rev(remote) or not main:
        return []
    sha = refs.resolve('refs/remotes/%s/%s' % (remote, main), root, known=True)[0]
    return [sha] if sha else []


def merge_owner_refusal(root, rules, landed=(), fold=True):
    """`(paths, why)` for a merge in progress about to be committed with everything it staged
    (kitlib.syncbase): the staged owner-only paths merge_owned does not carry, sorted ([]: none),
    with `why` None. When git could not tell, `why` says so and the paths are those merge_owned
    had not carried when it stopped (`(None, why)` when the staged paths could not be read); a
    caller refuses on any `why`. `fold` False (`"commit".merge_fold` off) carries no path both
    sides changed (clean_owner_merge is not asked), but still a path only the merged side changed
    (merge_owned's merged-copy test); an octopus merge carries none."""
    try:
        staged = staged_status(root)
        locked = {p for p in staged if rules.is_locked(p)}
        heads = merge_heads(root) if locked else []
    except _Stop as stop:
        return None, ' '.join(stop.lines)
    if not locked:
        return [], None
    owned, why = ownerrules.merge_owned(root, rules, locked, heads, landed, join=fold)
    return sorted(locked - owned), why


def foreign_refusal(foreign, merge_why=None, in_merge=False):
    """The refusal text for files staged that the caller did not name."""
    notes = ''
    if merge_why is not None:
        notes += (' (a merge is in progress, but its own staged files could not be told apart: %s)'
                  % merge_why)
    elif in_merge:
        notes += (' (a merge is in progress; these differ from the clean merge result, are its '
                  'conflicts, or are owner-only and not the merged branch\'s own copy: name the '
                  'ones that are yours)')
    return ('refused: already staged and not in your list (another session?): %s%s'
            % (', '.join(sorted(foreign)), notes))


def machine_path_error(subject, body, typed='', folders=None):
    """`(refusal or None, warning lines)`: the refusal when the subject or body carries a machine
    path. `typed` is -b as typed, scanned too: the `\\n` conversion cuts a
    `<drive>:\\Users\\nick` path short. `folders` are the drive-rooted folder names refused
    besides a user profile (None: the machine config's, machinepaths.folders(); one it cannot
    read refuses user-profile paths only, and its WARNING is the one warning line)."""
    note = None
    if folders is None:
        folders, note = machinepaths.folders()
    warnings = [note] if note else []
    found = []
    for text in (subject, typed, body):
        found += [f for f in machinepaths.find(text, folders) if f not in found]
    if not found:
        return None, warnings
    return ('refused: the commit message carries a machine path: %s. '
            'tools/machine_paths.py fails a file with one, and history cannot be fixed once it '
            'lands. Write %%VAR%%\\... (the variable that applies), or leave it out.'
            % ', '.join(found)), warnings


def unstage_added(root, rels, status_before, lines):
    """`lines` plus how the index was left, as one refusal text, after resetting the paths among
    `rels` this call staged that were not staged before it."""
    added = sorted(set(rels) - set(status_before))
    rc, _out, gerr = ownerrules.run_git(root, 'reset', '-q', '--', *added) if added else (0, '', '')
    return lines + ['Nothing was committed; %s' % (
        'fix it and run this again.' if rc == 0 else
        'the files this call staged are still staged (git reset failed: %s).' % gerr)]


def staged_machine_path_error(root, status_after, status_before):
    """`(refusal lines or None, warning lines)`: kitlib.machinepaths.scan_staged over the STAGED
    copy of every staged file but a deletion. Fail-closed: an index that cannot be read, a staged
    path with no blob, or a refused config is a refusal too. A machine config the kit cannot read
    is not: its WARNING leads the warning lines. Every refusal unstages the paths this call staged
    that were not staged before it."""
    rels = sorted(p for p, s in status_after.items() if s != 'D')
    staged = sorted(status_after)
    note = []
    try:
        cfg = machinepaths.load_config(root)
        note = [cfg.note] if cfg.note else []
        got = machinepaths.scan_staged(root, rels, cfg)
    except kconfig.ConfigError as e:
        got = (None, 'its .kit.json "machine_paths" config is refused: %s' % e)
    if got[0] is None:
        return unstage_added(root, staged, status_before, note + [
            'refused: the staged files could not be checked for a machine path: %s.'
            % got[1]]), []
    hits, _used, read, skipped = got
    warnings = note + machinepaths.skipped_lines(skipped)
    if not hits:
        return None, warnings
    lines = warnings + machinepaths.hit_lines(hits, len(read))
    lines.append('refused: a staged file carries a machine path, and the '
                 'machine-paths check would fail the branch.')
    return unstage_added(root, staged, status_before, lines), []


def main_checkout_error(lander_main_checkout, root, main_branch=None, rule_pointer=None):
    """None if this commit may go ahead in `root`, else the refusal or usage error (exit 2).
    Without the flag: refused in the main checkout, and wherever git cannot tell. With it: a usage
    error everywhere EXCEPT the main checkout, so it cannot be carried by habit from a worktree."""
    if not lander_main_checkout:
        return checkout.main_refusal(root, 'a commit', TOOL, rule_pointer)
    place = repo.classify(root)
    if place.kind != repo.MAIN:
        return ('USAGE: --lander-main-checkout was given but %s is %s, not the main checkout%s. '
                'The flag is for a commit on %s in the main checkout only; drop it.'
                % (root, place.kind, ' (%s)' % place.detail if place.detail else '',
                   main_branch or 'the main branch'))
    return None
