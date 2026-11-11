r"""Stage explicit paths and commit with the right trailers, in one call (from the PM kit's
tools/commit.py; docs/commit.md).

commit(Request) -> Outcome(code, lines). Nothing here prints or exits; tools/commit.py does.

- The work tree is Request.root (tools/commit.py resolves `-C DIR`, default `.`, with
  kitlib.git.repo.toplevel). A path is relative to that work tree's root, or absolute inside it
  (outside: a usage error); a relative --body-file is the caller's (Request.cwd).
- Explicit paths only: a directory, `.`, `-A` or a glob is a usage error. A named path already
  staged as a deletion is committed as that deletion; nothing unnamed is added.
- --allow-empty (Request.allow_empty) is the one commit of no paths: HEAD's tree unchanged, only
  the message and its trailers (a `Closes:` line). It refuses anything staged, a merge in
  progress and an unborn branch (no HEAD to keep); a hook that changes the commit's tree has it
  undone and refused (commit_only); with a path, --owner-approval or --consume it is a usage
  error. Every other check runs as for any commit. No paths without it is never an empty commit.
- The body: `-b` is the TEXT (a typed `\n` becomes a newline); --body-file reads it from a file
  (UTF-8, a BOM dropped, CRLF made LF; empty, unreadable or not UTF-8 is a usage error). A `-b`
  whose whole value is one line naming an existing file, or shaped like an absolute path, is a
  usage error: pass --body-file.
- A machine path in the subject or body is refused (kitlib.machinepaths.find, over the machine
  config's `machine_path_folders` and a user profile); an --owner-authored commit is exempt. A
  machine path in the STAGED copy of a file the commit would record is refused by
  kitlib.machinepaths.scan_staged under the repository's own "machine_paths" config, with no
  --owner-authored exemption; the paths this call staged are unstaged again. A machine config the
  kit cannot read is a WARNING, printed once (by the message check, or by the staged check of an
  --owner-authored commit), and user-profile paths are still refused.
- Anything ELSE already staged is refused (it may be another session's), with one exception: with
  .kit.json "commit".merge_fold, during a merge in progress (one MERGE_HEAD), the files the merge
  staged itself (merge_owned, in kitlib.ownerfiles.rules with the other merge checks named
  here), so only the resolved conflicts are named. An owner-only path is the
  merge's only when its staged state is exactly the merged branch's copy and that branch changed
  it (a deletion included; a conflict resolved to that copy too), or when it is exactly the clean
  merge of both sides' changes and every one of those is the owner's or approved
  (clean_owner_merge); named, it is refused as below.
  An owner-only path the merged branch changed but the merge commits as HEAD's copy (a
  `git checkout HEAD -- <path>`) is one WARNING line (dropped_owner_changes); nothing is refused.
- The commit records only the paths this call named, rendered or let a merge carry (commit_only):
  it is made from a private index (GIT_INDEX_FILE), HEAD's tree plus those paths' staged entries,
  so a file another writer stages after the check above is not swept in. The real index is not
  written; what is still staged after the commit is named in a WARNING line. HEAD and those
  entries are read once, before the machine-path check; an entry staged again after it, a HEAD
  that cannot be read (other than an unborn branch), and a HEAD another writer moves before the
  commit refuse it. This call's commit, made on a HEAD moved after the last check, is undone
  (update-ref back to its parent; the merge state written back for a merge commit) and refused;
  a commit at HEAD that is not this call's is never undone. A hook may change the commit; it is
  told for this call's by its parent, or by the reflog entry its GIT_REFLOG_ACTION tags
  (commit_only).
- Owner-only paths (.kit.json "owner", kitlib.config.owner) are committed only by the owner:
  --owner-authored, which refuses unless the author identity git will record (`git var
  GIT_AUTHOR_IDENT`) is "owner".email (unset, an agent_emails address or any other: refused).
  --owner-approved is required only when an owner-only path is in the commit, and
  every value given must be a blanket entry or a path committed in this call. An --owner-authored
  commit carries `Owner-Authored: yes` in place of Co-Authored-By.
- Or an agent commits the change the owner approved on the hub: --owner-approval hub:A-<n>
  (kitlib.ownerfiles.apply; docs/commit.md "Owner approval"). The paths named must be exactly the
  approval's, HEAD its base and each file its approved base; the hub verifies it, then the approved
  content is written, staged and checked blob for blob, committed with `Owner-Approval: hub:A-<n>`
  before Co-Authored-By, and the approval consumed, recording that commit (a rebuild of it needs
  no new approval: docs/commit.md "Owner approval"). A refusal after the write
  puts the work tree back and unstages what this call staged. A consume that fails leaves the
  commit (REFUSED, naming the retry): --owner-approval hub:A-<n> --consume <commit> consumes it.
- Otherwise the trailer is `Co-Authored-By: Claude <model> <noreply@anthropic.com>`, "Claude "
  added when the name lacks it - unless the name is a family in .kit.json
  "commit".coauthor_families or starts with one followed by a space, '.' or '-'.
  The model is required: --model, else CLAUDE_AGENT_MODEL (blank is unset).
- Last, the session line (session_lines): `<key>: <value>` lines that end the body (the hosts'
  attribution.session_trailer keys, `Claude-Session`) move into the trailer block, where git
  reads them; with none, an agent's commit gets its host's own, `Claude-Session:
  https://claude.ai/code/<id>` from CLAUDE_CODE_BRIDGE_SESSION_ID when that is set to a plain id.
- The main checkout is refused (kitlib.git.checkout.main_refusal); --lander-main-checkout is the
  one way past it, and a usage error anywhere else.
- Render on commit (kitlib.content.oncommit; docs/content.md "Render on commit"): in a work tree
  with the kit's layout, when a named path is under `content/` or is `kit.json` or `hosts.json`,
  the work tree's own tools/render_content.py --write runs first, and every rendered file it wrote
  or removed is staged into the same commit (listed on a `rendered` line). A render that fails
  refuses the commit, naming the command to run; what it wrote stays in the work tree.
- After the commit: the hash, the files, and the doc-budget verdict when a .md or owner-only file
  was committed (the kit's tools/doc_budget.py run with `-C <root>`, judged by kitlib.verdict), then
  the doc-index verdict (tools/doc_index.py, same call; nothing when "doc_index".enabled is false).

Codes: OK 0 committed, REFUSED 1 refused or git failed, USAGE 2 a usage error, the main-checkout
refusal or a refused .kit.json.
"""
import collections
import hashlib
import os
import posixpath
import re
import shutil
import subprocess
import sys
import uuid

from kitlib import config as kconfig
from kitlib import attribution, machinepaths, verdict
from kitlib.docs import callers as doc_callers
from kitlib.config import owner as ownermod
from kitlib.config import user as kuser
from kitlib.content import oncommit
from kitlib.git import checkout, cmd, refs, repo
from kitlib.proc import spawn

from kitlib.ownerfiles import apply as approval
from kitlib.ownerfiles import rules as ownerrules

OK, REFUSED, USAGE = 0, 1, 2
TOOL = 'commit.py'
OWNER_AUTHORED_TRAILER = 'Owner-Authored: yes'
KIT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DOC_BUDGET_TIMEOUT = 600     # seconds

norm = ownermod.norm

# root: the work tree's top level; cwd: the caller's directory (a relative --body-file); env: the
# environment the host's model variable is read from (None: os.environ); provider: the host name
# (None: the hosts.json default); owner_approval: `hub:A-<n>` (--owner-approval); consume: the
# commit --consume names; hub: the hub client to ask (None: the machine's hub; tests pass one);
# allow_empty: --allow-empty, a commit of no paths (module docstring).
Request = collections.namedtuple(
    'Request', 'root subject b body_file model owner_authored owner_approved '
               'lander_main_checkout paths cwd env provider owner_approval consume hub '
               'allow_empty',
    defaults=(None, None, None, None, False))
Outcome = collections.namedtuple('Outcome', 'code lines')


def request(root, subject, paths, b='', body_file=None, model=None, owner_authored=False,
            owner_approved=(), lander_main_checkout=False, cwd=None, env=None, provider=None,
            owner_approval=None, consume=None, hub=None, allow_empty=False):
    return Request(root, subject, b, body_file, model, owner_authored, list(owner_approved),
                   lander_main_checkout, list(paths), cwd or os.getcwd(), env, provider,
                   owner_approval, consume, hub, allow_empty)


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
            'Or ask the owner on the hub and commit with --owner-approval hub:A-<n>.' % (
                ', '.join(locked), (' (A merge in progress carries the merged branch\'s own copy '
                                    'of %s without it being named: leave it out of your list.)'
                                    % ', '.join(carried)) if carried else ''))


def unapproved_locked(rules, locked, owner_approved):
    """The locked paths no --owner-approved value covers (the exact path, or its blanket)."""
    return [p for p in locked if not rules.approved_by(p, owner_approved)]


def canonical_approved(rules, owner_approved, paths):
    """Each --owner-approved value in the one spelling a trailer is written in
    (kitlib.config.owner, Rules.canonical): the committed path it names, or the blanket entry as
    configured; anything else as typed."""
    return [rules.canonical(value, paths) for value in owner_approved]


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


def build_trailers(rules, owner_approved, owner_authored, model, families=(), provider=None,
                   session=()):
    """`<owner trailer>: <value>` lines (the configured "owner".trailer), then Co-Authored-By on
    an agent's commit, or `Owner-Authored: yes` on the owner's own, then the `session` lines."""
    trailers = ['%s: %s' % (rules.trailer, o) for o in owner_approved]
    trailers.append(OWNER_AUTHORED_TRAILER if owner_authored else attribution.coauthor(model, provider, families))
    trailers.extend(session)
    return trailers


def lift_session_lines(body, keys):
    """`(body, lines)`: the `<key>: <value>` lines (a key of `keys`, any case; one value with no
    space) that END the body, taken off it. They go in the trailer block: left in the body, the
    blank line before the trailers keeps git from reading them as trailers (a `-b "...~~
    Claude-Session: <url>"`). A line anywhere else in the body stays where it is."""
    if not body or not keys:
        return body, []
    line_re = re.compile(r'(?:%s): \S+\Z' % '|'.join(re.escape(k) for k in keys), re.IGNORECASE)
    rows = body.rstrip().split('\n')
    lifted = []
    while rows and line_re.match(rows[-1].strip()):
        lifted.insert(0, rows.pop().strip())
    return '\n'.join(rows).rstrip(), lifted


def session_lines(body, owner_authored, env=None, provider=None):
    """`(body, session trailer lines)`: a body-final session line is lifted out of the body
    (lift_session_lines) and wins; otherwise an agent's commit gets the host's own
    (kitlib.attribution.session_trailer, from `env`, default os.environ). The owner's own commit
    gets none from the environment."""
    body, lifted = lift_session_lines(body, attribution.session_keys())
    if lifted or owner_authored:
        return body, lifted
    line = attribution.session_trailer(os.environ if env is None else env, provider)
    return body, [line] if line else []


def build_message(subject, body, trailers):
    parts = [subject]
    if body:
        parts.append(body)
    parts.append('\n'.join(trailers))
    return '\n\n'.join(parts) + '\n'


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


def doc_budget_check(root, kit_root=None):
    """`(verdict line, ok)` from the kit's tools/doc_budget.py run with `-C root`, or a NOT
    CHECKED line when the kit has none. Judged conjunctively (kitlib.verdict): exit 0 AND the OK
    line AND no FAILED line. `kit_root` defaults to KIT_ROOT, read at call time."""
    tool = os.path.join(kit_root or KIT_ROOT, 'tools', 'doc_budget.py')
    if not os.path.isfile(tool):
        return 'docs      NOT CHECKED - no tools/doc_budget.py in the kit, so nothing was measured', True
    try:
        r = spawn.run([sys.executable, tool, '-C', root], capture_output=True, text=True,
                      encoding='utf-8', errors='replace', timeout=DOC_BUDGET_TIMEOUT)
    except subprocess.TimeoutExpired:
        return 'docs      FAILED (doc_budget.py did not finish in %d s)' % DOC_BUDGET_TIMEOUT, False
    except OSError as e:
        return 'docs      FAILED (doc_budget.py could not start: %s)' % e, False
    out = (r.stdout or '') + (r.stderr or '')
    return verdict.line('docs', r.returncode, out, keep=4), verdict.judge(r.returncode, out)[0]


def doc_index_check(root, kit_root=None):
    """`(verdict line, ok)` from the kit's tools/doc_index.py run with `-C root` (the index
    checks doc_budget no longer makes), `(None, True)` when `.kit.json` "doc_index".enabled is
    false, or a NOT CHECKED line when the kit has no tool. Judged as doc_budget_check judges."""
    try:
        if doc_callers.switched_off(kconfig.load(root)):
            return None, True
    except kconfig.ConfigError:
        pass                                  # the tool reports a bad config itself
    tool = doc_callers.tool(kit_root or KIT_ROOT)
    if not os.path.isfile(tool):
        return doc_callers.not_checked(), True
    try:
        r = spawn.run([sys.executable, tool, '-C', root], capture_output=True, text=True,
                      encoding='utf-8', errors='replace', timeout=doc_callers.TIMEOUT)
    except subprocess.TimeoutExpired:
        return 'doc index FAILED (doc_index.py did not finish in %d s)' % doc_callers.TIMEOUT, False
    except OSError as e:
        return 'doc index FAILED (doc_index.py could not start: %s)' % e, False
    line, failed = doc_callers.line(r.returncode, (r.stdout or '') + (r.stderr or ''))
    return line, not failed


# The whole -b value is a path: drive-rooted, UNC or `/`-rooted with no whitespace, or
# drive-rooted and ending in a file extension (a Windows path may hold spaces), or a `%VAR%\` path
# with no whitespace. A drive-rooted value with spaces and no extension is prose and passes.
PATH_SHAPED = re.compile(r'(?:[A-Za-z]:[\\/]|\\\\|/|%[A-Za-z_][A-Za-z0-9_]*%[\\/])\S*'
                         r'|[A-Za-z]:[\\/].*\.[A-Za-z0-9]{1,8}')


def path_shaped_body(value, root, cwd):
    """The usage error when `-b`'s whole value is one line that is a path rather than a body, else
    None. Judged on the value as typed, before a typed `\\n` becomes a newline."""
    v = (value or '').strip()
    if not v or '\n' in v or '\r' in v:
        return None
    exists = os.path.isfile(os.path.join(cwd, v)) or os.path.isfile(os.path.join(root, v))
    if not exists and not PATH_SHAPED.fullmatch(v):
        return None
    return ('-b is the body TEXT, and %r %s. To take the body from a file, pass --body-file %s '
            'instead.' % (v, 'is an existing file' if exists else 'looks like an absolute path', v))


def read_body_file(path):
    """`(body, None)` from a --body-file, or `(None, usage error)`: UTF-8 with a BOM dropped, CRLF
    made LF, trailing whitespace and leading blank lines trimmed."""
    try:
        with open(path, 'rb') as fh:
            data = fh.read()
    except OSError as exc:
        return None, '--body-file %s could not be read: %s' % (path, exc.strerror or exc)
    try:
        text = data.decode('utf-8-sig')
    except UnicodeDecodeError as exc:
        return None, '--body-file %s is not UTF-8 (%s)' % (path, exc.reason)
    text = text.replace('\r\n', '\n').rstrip().lstrip('\n')
    if not text.strip():
        return None, '--body-file %s is empty' % path
    return text, None


def message_body(b, body_file, root, cwd):
    """`(body, None)` from -b or --body-file, or `(None, usage error)`. In -b a typed `\\n` and a
    `~~` each become a newline (`~~` survives shells that eat a backslash); a single `~` is kept."""
    if body_file is not None:
        return read_body_file(os.path.join(cwd, body_file))
    if not b:
        return '', None
    err = path_shaped_body(b, root, cwd)
    if err:
        return None, err
    return b.replace('\\n', '\n').replace('~~', '\n'), None


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


def left_staged_line(others):
    """The warning for files another writer staged during this call, which it left staged."""
    return ('WARNING: staged by another writer during this call, left staged and not committed: '
            '%s' % ', '.join(sorted(others)))


def head_commit(root):
    """HEAD's commit id, or '' on an unborn branch (HEAD names a branch that does not exist yet).
    Any other failure to read HEAD raises _Stop: an empty tree is never guessed."""
    rc, out, err = ownerrules.run_git(root, 'rev-parse', '-q', '--verify', 'HEAD^{commit}')
    if rc == 0 and out.strip():
        return out.strip()
    src, ref, _e = ownerrules.run_git(root, 'symbolic-ref', '-q', 'HEAD')
    if src == 0 and ref.strip():
        rrc, rout, _e = ownerrules.run_git(root, 'rev-parse', '-q', '--verify', ref.strip())
        if rrc == 1 and not rout.strip():
            return ''
    raise _Stop(REFUSED, 'refused: git could not read HEAD (rev-parse HEAD: %s). Nothing was '
                'committed.' % (err.strip() or 'exit %s' % rc))


def index_snapshot(root, keep):
    """`{path: (mode, oid), or None for a deletion}`: the real index's entry for each path of
    `keep` ({path: status letter}), read in one `git ls-files --stage`. Raises _Stop when git cannot
    list the index or a kept path is not one stage-0 entry (unmerged, or changed meanwhile)."""
    rc, out, err = ownerrules.run_git(root, 'ls-files', '--stage', '-z')
    if rc != 0:
        raise _Stop(REFUSED, 'git ls-files --stage failed: %s. Nothing was committed.'
                    % (err or 'exit %s' % rc))
    entries = {}
    for row in out.split('\0'):
        meta, tab, name = row.partition('\t')
        if tab and name in keep:
            entries.setdefault(name, []).append(meta.split())
    snap = {}
    for path in sorted(keep):
        metas = entries.get(path, [])
        if keep[path] == 'D' and not metas:
            snap[path] = None
        elif len(metas) == 1 and len(metas[0]) == 3 and metas[0][2] == '0':
            snap[path] = (metas[0][0], metas[0][1])
        else:
            raise _Stop(REFUSED, 'refused: %s is not one staged entry in the index (unmerged, or '
                        'changed during this call). Nothing was committed; run this again.' % path)
    return snap


def private_index_file(tmp):
    """The private index's path in the folder `tmp`. Its name starts `next-index-`, the name
    git gives the index of a partial commit, so the kit's pre-commit hook
    (kitlib.githooks.render.partial) treats this commit as one."""
    return os.path.join(tmp, 'next-index-%d' % os.getpid())


def private_index(root, tmp, head, snap):
    """`(env, None)` for a `git commit` that records exactly `snap` (index_snapshot): a private
    index (private_index_file) holding `head`'s tree (empty for '', an unborn branch) with each
    snapshot entry set, a deletion (None) removed; or `(None, why)`. Every other entry of the real
    index, staged by anyone, is left out of the commit and left as it is.

    The private index starts as a copy of the real index (_copied_index), with every other path
    that differs from `head` (staged, unmerged, or absent) put back to `head`'s entry, so the
    entries `git commit` refreshes keep their stat data. A read-tree index has none, and `git
    commit` then hashes every tracked work-tree file, past cmd.GIT_TIMEOUT in a large tree.
    When the copy cannot be used, and on an unborn branch, the
    private index is read-tree'd (`read-tree -m` would refuse an unmerged real index)."""
    env = dict(cmd.scrubbed_env(), GIT_INDEX_FILE=private_index_file(tmp))
    zero = '0' * (len(head) or 40)
    back = _copied_index(root, env, head, snap) if head else None
    if back is None:
        r = cmd.call(['read-tree', head] if head else ['read-tree', '--empty'], root, env=env)
        if r.rc != 0:
            return None, 'git read-tree into a private index failed: %s' % (
                r.err or 'exit %s' % r.rc)
        back = []
    lines = back + ['0 %s\t%s' % (zero, p) if e is None else '%s %s\t%s' % (e[0], e[1], p)
                    for p, e in sorted(snap.items())]
    if lines:
        r = cmd.call(['update-index', '--index-info'], root, env=env,
                     stdin=('\n'.join(lines) + '\n').encode('utf-8'))
        if r.rc != 0:
            return None, 'git update-index on the private index failed: %s' % (
                r.err or 'exit %s' % r.rc)
    return env, None


EMPTY_BLOB_SHA1 = hashlib.sha1(b'blob 0\0').hexdigest()
EMPTY_BLOB_SHA256 = hashlib.sha256(b'blob 0\0').hexdigest()


def _copied_index(root, env, head, snap):
    """The real index copied to env's GIT_INDEX_FILE, and the --index-info lines that put every
    path outside `snap` back to `head`'s entry there: removed first (every stage), then `head`'s
    mode and blob. None, with the copy removed, when the copy cannot be used: no real index, a
    copy whose trailing checksum does not match (torn), git cannot read it or `head`, or a path
    this cannot carry exactly (not UTF-8, so it decodes with U+FFFD; a newline; a leading quote).
    An entry holding the empty blob is always put back: `ls-files --stage` does not show an
    intent-to-add entry's flag, and one would match a file empty at `head`, then be left out of
    the commit's tree. The copy keeps the real index's mtime (copy2), which git's racy-clean
    check compares entries against."""
    dest = env['GIT_INDEX_FILE']
    real, _why = repo.git_path(root, 'index')
    try:
        if not real:
            return None
        shutil.copy2(real, dest)
        if _torn(dest, len(head)):
            raise OSError('torn copy')
    except OSError:
        _remove(dest)
        return None
    r = cmd.call(['ls-files', '--stage', '-z'], root, env=env)
    t = cmd.call(['ls-tree', '-r', '-z', '--full-tree', head], root, env=env) if r.rc == 0 else r
    if r.rc != 0 or t.rc != 0:
        _remove(dest)
        return None
    ours, theirs = {}, {}
    for out, into in ((r.out, ours), (t.out, theirs)):
        for row in cmd.text(out).split('\0'):
            meta, tab, path = row.partition('\t')
            f = meta.split()
            if not (tab and len(f) == 3):
                continue
            if '�' in path or '\n' in path or path.startswith('"'):
                _remove(dest)
                return None
            if into is ours:
                ours.setdefault(path, set()).add((f[0], f[1], f[2]))
            else:
                theirs[path] = (f[0], f[2])
    empty = EMPTY_BLOB_SHA256 if len(head) == 64 else EMPTY_BLOB_SHA1
    drop, put = [], []
    for path in sorted((set(ours) | set(theirs)) - set(snap)):
        want = theirs.get(path)
        if (want is not None and want[1] != empty
                and ours.get(path) == {(want[0], want[1], '0')}):
            continue
        if path in ours:
            drop.append('0 %s\t%s' % ('0' * len(head), path))
        if want is not None:
            put.append('%s %s\t%s' % (want[0], want[1], path))
    return drop + put


def _torn(path, oid_len):
    """True when the index file at `path` does not end in the checksum of what precedes it
    (an all-zero trailer, index.skipHash, is not checked)."""
    algo, size = ('sha256', 32) if oid_len == 64 else ('sha1', 20)
    with open(path, 'rb') as fh:
        data = fh.read()
    if len(data) < 12 + size or data[:4] != b'DIRC':
        return True
    body, tail = data[:-size], data[-size:]
    return tail != bytes(size) and hashlib.new(algo, body).digest() != tail


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


MERGE_STATE = ('MERGE_HEAD', 'MERGE_MSG', 'MERGE_MODE')


def merge_state(root):
    """`{path: bytes}` of the merge-state files that exist now (MERGE_STATE), to put back when a
    commit is undone."""
    saved = {}
    for name in MERGE_STATE:
        path, _why = repo.git_path(root, name)
        try:
            with open(path, 'rb') as fh:
                saved[path] = fh.read()
        except (OSError, TypeError):
            pass
    return saved


def head_moved_line(head, now, made=False):
    """The refusal's first line; `made`: this call's commit was made on `now` and still exists."""
    return ('refused: HEAD moved during this call (another writer committed %s on top of %s); '
            '%s' % (now[:12] or 'nothing', head[:12] or 'an unborn branch',
                    'this call\'s commit was made on theirs.' if made
                    else 'this commit would have reverted their change.'))


def undo_commit(root, new, parent, saved, merged):
    """Point HEAD back at `parent` if it is still `new` (update-ref with the old value), and, when
    `new` is a merge commit (`merged`: it consumed the merge state), write back the merge-state
    files `saved` that it removed. The refusal lines either way."""
    rc, _out, err = ownerrules.run_git(root, 'update-ref', '-m',
                                       'commit.py: undo a commit made on a moved HEAD',
                                       'HEAD', parent, new)
    if rc != 0:
        return ['refused, and NOT undone: commit %s was made on the moved HEAD and lacks the other '
                'writer\'s change; git update-ref failed: %s. Undo it by hand: git update-ref HEAD '
                '%s %s' % (new[:12], err.strip() or 'exit %s' % rc, parent, new)]
    for path, data in (saved.items() if merged else ()):
        if not os.path.exists(path):
            try:
                with open(path, 'wb') as fh:
                    fh.write(data)
            except OSError as e:
                return ['Undone: HEAD is back at %s, but %s could not be written back (%s).'
                        % (parent[:12], path, e.strerror)]
    return ['Undone: HEAD is back at %s; nothing was committed, and the files this call named '
            'are still staged. Run this again.' % parent[:12]]


READ_BACK_FORMAT = '--format=%H %T %P%n%B'     # the read-back of HEAD after `git commit`
REFLOG_TAIL = 1 << 20      # bytes of logs/HEAD read for this call's entry: it is among the last


def message_lines(text):
    """`text` as git's whitespace cleanup leaves it, near enough to compare: each line's trailing
    whitespace dropped, and blank lines dropped."""
    return [l.rstrip() for l in text.splitlines() if l.strip()]


def reflog_tag():
    """This call's GIT_REFLOG_ACTION for `git commit`: git writes it, then ': <subject>', as the
    message of the reflog entry for the commit, so the entry is this call's alone."""
    return 'commit (kit %d %s)' % (os.getpid(), uuid.uuid4().hex)


def tagged_entry(root, tag):
    """`(old, new)` of the one entry in HEAD's reflog (logs/HEAD of the work tree at `root`) whose
    message starts with `tag`: the HEAD this call's `git commit` read and the commit it made; old is
    '' on an unborn branch. None when there is no reflog, no such entry, or more than one (the fake
    git writes no reflog)."""
    path, _why = repo.git_path(root, 'logs/HEAD')
    if not path:
        return None
    try:
        with open(path, 'rb') as fh:
            fh.seek(max(0, os.path.getsize(path) - REFLOG_TAIL))
            data = fh.read()
    except OSError:
        return None
    want, hits = (tag + ':').encode('utf-8'), []
    for line in data.split(b'\n'):
        meta, tab, message = line.partition(b'\t')
        fields = meta.split()
        if tab and message.startswith(want) and len(fields) >= 2:
            old, new = (f.decode('ascii', 'replace') for f in fields[:2])
            hits.append(('' if not old.strip('0') else old, new))
    return hits[0] if len(hits) == 1 else None


def commit_only(root, head, snap, msg, allow_empty=False):
    """`git commit` of exactly `snap` (index_snapshot) on `head` (head_commit) through
    private_index; `(rc, out, err, commit id, note lines, trees)`. `allow_empty` (--allow-empty):
    git commits `head`'s tree unchanged; this call's commit whose tree a hook changed is undone
    (undo_commit) and refused. `trees` is None when the commit
    holds exactly the tree built, else `(tree built, tree committed)`: a hook changed it, and
    align_index brings the real index's entries for the paths that differ into line. Every other
    staged entry stays staged.
    HEAD must not move under the call: checked just before `git commit` (refused), and read back
    after it. A HEAD read back whose first parent is `head` and that holds the tree and message
    built is this call's commit. When a hook may have changed it, the reflog entry tagged with this
    call's GIT_REFLOG_ACTION (tagged_entry) decides: with no entry, or one naming that HEAD, it is
    this call's (git commit moves HEAD only from the HEAD it read). Otherwise this call's commit
    and the HEAD it was made on come from that entry:
    - this call's commit at HEAD, made on a HEAD another writer moved: undone (undo_commit) and
      refused;
    - this call's commit made on `head`, another writer's on top of it: it stands (a NOTE line);
      refused, NOT undone, when HEAD no longer contains it;
    - this call's commit made on a moved HEAD, another writer's on top: refused, NOT undone, naming
      the commit it may revert.
    With no such entry (no reflog, the fake git), the commit at HEAD is this call's only when its
    tree is the private index's and its message is `msg`; then it is undone as above. Anything
    else is refused without undoing anything: a commit that is not this call's is never undone."""
    tag = reflog_tag()
    tmp = kuser.mkdtemp('kit-commit-')
    try:
        env, why = private_index(root, tmp, head, snap)
        if env is None:
            return 1, '', why, '', [], None
        r = cmd.call(['write-tree'], root, env=env)
        if r.rc != 0:
            return r.rc, '', 'git write-tree of the private index failed: %s' % (
                r.err or 'exit %s' % r.rc), '', [], None
        tree = r.out.decode('utf-8', 'replace').strip()
        now = head_commit(root)
        if now != head:
            raise _Stop(REFUSED, head_moved_line(head, now), 'Nothing was committed; the files '
                        'this call named are still staged. Run this again.')
        saved = merge_state(root)
        r = cmd.call(['commit', '-q'] + (['--allow-empty'] if allow_empty else []) + ['-m', msg],
                     root, env=dict(env, GIT_REFLOG_ACTION=tag))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    gout = r.out.decode('utf-8', 'replace')
    if r.rc != 0:
        return r.rc, gout, r.err, '', [], None
    rc, out, err = ownerrules.run_git(root, 'log', '-1', READ_BACK_FORMAT, 'HEAD')
    first, _nl, body = out.partition('\n')
    ids = first.split()
    if rc != 0 or len(ids) < 2:
        raise _Stop(REFUSED, 'git commit ran, but HEAD could not be read back to check it was '
                    'made on %s: %s. Check `git log -3` by hand.'
                    % (head[:12] or 'the unborn branch', err.strip() or 'exit %s' % rc))
    now, now_tree, parents = ids[0], ids[1], ids[2:]
    base = parents[0] if parents else ''
    as_built = now_tree == tree and message_lines(body) == message_lines(msg)
    if base == head and as_built:
        return 0, gout, r.err, now, [], None
    entry = tagged_entry(root, tag)
    if base == head and (entry is None or entry[1] == now):
        if allow_empty and now_tree != tree:
            raise _Stop(REFUSED, 'refused: --allow-empty, but a hook changed the commit\'s tree '
                        '(it staged or removed a file), so it is not an empty commit.',
                        *undo_commit(root, now, base, saved, len(parents) > 1))
        return 0, gout, r.err, now, [], (tree, now_tree)
    if not parents:
        raise _Stop(REFUSED, 'refused: after git commit, HEAD is %s, a commit with no parent, '
                    'which this call cannot tell for its own commit on %s.'
                    % (now[:12], head[:12] or 'an unborn branch'),
                    'NOT undone: check `git log` and `git reflog` by hand.')
    if entry is None:
        if not as_built:
            raise _Stop(REFUSED, 'refused, and NOT undone: git commit ran, but HEAD is now %s, '
                        'which this call cannot tell for its own commit (another writer committed '
                        'after it, or a hook changed it). Check `git log -3` by hand: a commit of '
                        'this call made on another writer\'s commit lacks their change.' % now[:12])
        return _undo_moved(root, head, now, base, saved, len(parents) > 1)
    ours_base, ours = entry
    if ours == now:
        if ours_base != base:
            raise _Stop(REFUSED, 'refused, and NOT undone: HEAD %s has parent %s, but git logged '
                        'this call\'s commit on %s. Check `git log -3` by hand.'
                        % (now[:12], base[:12], ours_base[:12] or 'an unborn branch'))
        return _undo_moved(root, head, now, base, saved, len(parents) > 1)
    if ours_base == head:
        rc, _out, _err = ownerrules.run_git(root, 'merge-base', '--is-ancestor', ours, now)
        if rc == 0:
            trc, made, _err = ownerrules.run_git(root, 'rev-parse', '%s^{tree}' % ours)
            return 0, gout, r.err, ours, [
                'NOTE: another writer has since committed %s on top of this commit.' % now[:12]], (
                    tree, made.strip() if trc == 0 else '')
        raise _Stop(REFUSED, 'refused, and NOT undone: this call committed %s on %s, but HEAD is '
                    'now %s, which does not contain it. Check `git log -3` and `git reflog` by '
                    'hand.' % (ours[:12], head[:12] or 'an unborn branch', now[:12]))
    raise _Stop(REFUSED, head_moved_line(head, ours_base, made=True),
                'NOT undone: this call\'s commit %s was made on %s, so it may undo changes from %s; '
                'another writer has since committed %s on top of it. Check `git log -3` and '
                'restore their change by hand.'
                % (ours[:12], ours_base[:12] or 'an unborn branch',
                   ours_base[:12] or 'that writer', now[:12]))


def _undo_moved(root, head, new, parent, saved, merged):
    """Refuse this call's commit `new`, made on `parent` after HEAD moved from `head`, undoing it
    (undo_commit)."""
    raise _Stop(REFUSED, head_moved_line(head, parent),
                *undo_commit(root, new, parent, saved, merged))


def _tree_entry(mode, oid):
    return None if not mode.strip('0') else (mode, oid)


def align_index(root, trees, ours):
    """`(note lines, refusal lines)` after bringing the real index into line with this call's
    commit `ours` for each path a hook changed in it; `trees` is commit_only's `(tree built, tree
    committed)`. A hook's `git add` reaches only the private index, so without this the real index
    keeps the copy from before the hook and the next commit reverts the hook's change. A path whose
    real-index entry is still the built tree's is set to the committed entry. One holding another
    copy (staged before or during the call) is left as staged, and refused (the commit stands),
    naming the command that takes the commit's copy. The read and the write of the index are two git calls:
    a path staged between them is overwritten."""
    built, made = trees
    if made == built:
        return [], []
    rc, out, err = ownerrules.run_git(root, 'diff-tree', '-r', '-z', '--no-renames', built,
                                      made) if made else (
        None, '', 'its tree could not be read')
    if rc != 0:
        return [], ['refused: commit %s stands, but a hook may have changed it, and it could not be '
                    'compared with what this call built (%s). Check `git diff --cached` against it '
                    'by hand.' % (ours[:12], err.strip() or 'exit %s' % rc)]
    cols, changed = out.split('\0'), {}
    for i in range(0, len(cols) - 1, 2):
        meta = cols[i].lstrip(':').split()
        if len(meta) >= 4:
            changed[cols[i + 1]] = (_tree_entry(meta[0], meta[2]), _tree_entry(meta[1], meta[3]))
    rc, out, err = ownerrules.run_git(root, 'ls-files', '--stage', '-z')
    if rc != 0:
        return [], ['refused: commit %s stands, but a hook changed %s in it, and the index could '
                    'not be read to bring it into line (%s). Run: git restore --staged --source=%s '
                    '-- %s' % (ours[:12], ', '.join(sorted(changed)), err.strip() or 'exit %s' % rc,
                               ours[:12], ' '.join(sorted(changed)))]
    staged = {}
    for row in out.split('\0'):
        meta, tab, name = row.partition('\t')
        if tab and name in changed:
            staged.setdefault(name, []).append(tuple(meta.split()))
    fit, kept = [], []
    for path in sorted(changed):
        old, new = changed[path]
        rows = staged.get(path, [])
        same = (not rows and old is None) or (
            len(rows) == 1 and len(rows[0]) == 3 and rows[0][2] == '0' and rows[0][:2] == old)
        (fit if same else kept).append(path)
    if fit:
        zero = '0' * len(ours)
        info = ['0 %s\t%s' % (zero, p) if changed[p][1] is None
                else '%s %s\t%s' % (changed[p][1][0], changed[p][1][1], p) for p in fit]
        r = cmd.call(['update-index', '--index-info'], root,
                     stdin=('\n'.join(info) + '\n').encode('utf-8'))
        if r.rc != 0:
            kept, fit = sorted(kept + fit), []
    notes = ['index     set to the commit\'s copy of what a hook changed: %s' % ', '.join(fit)
             ] if fit else []
    if not kept:
        return notes, []
    return notes, ['refused: commit %s stands, but a hook changed %s in it, and the index holds '
                   'a copy other than the one this call committed (someone staged it before or '
                   'during this call, or the index could not be written): the next commit of it '
                   'would replace the hook\'s change. To '
                   'take the commit\'s copy: git restore --staged --source=%s -- %s'
                   % (ours[:12], ', '.join(kept), ours[:12], ' '.join(kept))]


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


def render_sources(root, paths):
    """The repo-relative rendered paths the kit's renderer wrote or removed in `root`, when `root`
    has the kit's layout and a path in `paths` is a render source (kitlib.content.oncommit); else
    None (no render ran). A render that fails raises _Stop, naming the command to run."""
    if not (oncommit.wants_render(paths) and oncommit.is_kit(root)):
        return None
    got, why = oncommit.render(root)
    if got is None:
        raise _Stop(REFUSED, 'refused: a render source is in this commit, and the render failed: %s. '
                    'Fix it and render by hand, then commit again: %s. Nothing was committed.'
                    % (why, oncommit.command(root)))
    return got


# The approval an --owner-approval commit carries: the hub client, its id (A-<n>), its document,
# the HEAD the steps before the write judged it on and what they found to write there
# (approval.prewrite; both None until _approval_before_write).
_Hub = collections.namedtuple('_Hub', 'client did doc head plan', defaults=(None, None))


def _approval_first(req, root, paths):
    """_Hub for --owner-approval once the hub has the approval, approved and not consumed; raises
    _Stop for what refuses it before the index is read: no hub, no such approval, its state, a
    render source among `paths` (the commit would carry rendered files the owner did not approve)
    or a merge in progress (the commit would have two parents)."""
    did = approval.doc_id(req.owner_approval)
    client, why = approval.hub_client(req.hub)
    doc = None
    if client is not None:
        doc, why = approval.fetch(client, did)
    why = why if doc is None else approval.state_error(did, doc)
    if why:
        raise _Stop(REFUSED, 'refused: %s. Nothing was written or committed.' % why)
    if oncommit.wants_render(paths) and oncommit.is_kit(root):
        raise _Stop(REFUSED, 'refused: a render source is named, and an approved commit carries '
                    'the approved files only. Nothing was written or committed.')
    heads = merge_heads(root)
    if heads:
        raise _Stop(REFUSED, 'refused: a merge in progress (MERGE_HEAD %s): an approved commit has '
                    'one parent, its base. Nothing was written or committed.'
                    % ', '.join(h[:12] for h in heads))
    return _Hub(client, did, doc)


def _approval_before_write(root, hub, paths, status_before):
    """`hub`, with the HEAD it was judged on and what to write there, once the approved change may
    be written: none of its paths staged already, the paths its own and the change applying on
    HEAD (approval.prewrite: the approved content, or its clean merge onto HEAD's copy), and the
    hub verifying it for its base; else _Stop."""
    staged = sorted(set(status_before) & set(approval.approved_paths(hub.doc)))
    why = None
    if staged:
        why = ('%s is staged already: an approved commit records the approved content only. '
               'Unstage it first' % ', '.join(staged))
    head = plan = None
    if not why:
        head = head_commit(root)
        plan, why = approval.prewrite(root, hub.did, hub.doc, paths, head)
    why = why or approval.preverify_error(root, hub.client, hub.did, hub.doc, head)
    if why:
        raise _Stop(REFUSED, 'refused: %s. Nothing was written or committed.' % why)
    return hub._replace(head=head, plan=plan)


def _stage(req, root, paths, rendered, named, merged, status_before, msg_warnings, body, trailers,
           lines, hub, shrink=(None, ())):
    """`(head, snap, status_after, others, message)`: the named paths staged and checked, for
    commit_only; raises _Stop on a refusal. `shrink`: (rules, the owner-only paths exempt as
    work-tree shrinks), each judged again here on its staged copy against `head`."""
    # A listed path is always added again. The one skip: a listed path staged as a deletion and
    # absent from the work tree (`git add` would fail "pathspec did not match"); its deletion is
    # committed because it was named. Nothing unnamed is ever added.
    to_add = [p for p in paths
              if not (status_before.get(p) == 'D' and not os.path.lexists(os.path.join(root, p)))]
    if to_add:
        rc, _out, err = ownerrules.run_git(root, 'add', '--', *to_add, timeout=None)
        if rc != 0:
            raise _Stop(REFUSED, 'git add failed: %s' % (err or 'exit %s' % rc))
    err = oncommit.stage(root, rendered) if rendered else None
    if err:
        raise _Stop(REFUSED, *unstage_added(root, sorted(named), status_before, [
            'refused: the rendered files could not be staged: %s.' % err]))
    # Only what this call named, rendered or the merge staged is committed: anything another
    # writer staged since the check above stays staged, out of this commit.
    staged_now = staged_status(root)
    status_after = {p: s for p, s in staged_now.items() if p in named or p in merged}
    others = set(staged_now) - set(status_after)
    if not status_after and not req.allow_empty:
        raise _Stop(REFUSED, 'nothing to commit in: %s' % ', '.join(paths))
    # What is committed is this one read of HEAD and of the index; the index must still hold it
    # after the machine-path check, so the blobs checked are the blobs committed.
    head = head_commit(root)
    snap = index_snapshot(root, status_after)
    why = hub and approval.staged_error(hub.doc, head, status_after, snap, hub.head, hub.plan)
    if why:
        raise _Stop(REFUSED, 'refused: %s. Nothing was committed.' % why)
    rules, shrunk = shrink
    grown = sorted(set(shrunk) - (shrunk_paths(root, rules, shrunk, ['--cached', head])
                                  if shrunk and head else set()))
    if grown:           # the staged copy, the one committed, is what counts
        raise _Stop(REFUSED, *unstage_added(root, sorted(named), status_before,
                                            [owner_only_refusal(grown)]))
    refusal, warnings = staged_machine_path_error(root, status_after, status_before)
    # The message check printed the same machine-config WARNING already: once is enough.
    if refusal:
        raise _Stop(REFUSED, *[l for l in refusal if l not in msg_warnings])
    changed = sorted(p for p, e in index_snapshot(root, status_after).items() if snap[p] != e)
    if changed:
        raise _Stop(REFUSED, 'refused: %s was staged again during this call, after its staged copy '
                    'was checked. Nothing was committed; run this again.' % ', '.join(changed))
    lines.extend(l for l in warnings if l not in msg_warnings)
    return head, snap, status_after, others, build_message(req.subject, body, trailers)


def _put_back(root, written, paths, stop_lines):
    """`stop_lines` and how the work tree and index were left, after an --owner-approval commit
    was refused once the approved content was written: the files put back, the paths unstaged
    (none was staged before the call: _approval_before_write)."""
    failed = approval.restore(written)
    rc, _out, gerr = ownerrules.run_git(root, 'reset', '-q', '--', *paths)
    if not failed and rc == 0:
        return stop_lines + ['The work tree is as it was, and nothing this call staged is staged.']
    return stop_lines + ['The work tree and index could not be put back: %s. Check `git status` by '
                         'hand.' % '; '.join(failed + (
                             ['git reset failed: %s' % (gerr or 'exit %s' % rc)] if rc else []))]


def _consume(req, lines):
    """--owner-approval hub:A-<n> --consume COMMIT: consume the approval for COMMIT, made already
    (approval.consume_commit)."""
    did = approval.doc_id(req.owner_approval)
    value = req.consume.strip()
    rc, out, _err = ((1, '', '') if not value or value.startswith('-')
                     else ownerrules.run_git(req.root, 'rev-parse', '-q', '--verify',
                                             value + '^{commit}'))
    sha = out.strip()
    if rc != 0 or not sha:
        raise _Stop(REFUSED, 'refused: --consume %s is not a commit here. Nothing was consumed.'
                    % req.consume)
    client, why = approval.hub_client(req.hub)
    line, consumed = None, False
    if client is not None:
        line, why, consumed = approval.consume_commit(req.root, client, did, req.owner_approval,
                                                      sha)
    if line is None and consumed:
        raise _Stop(REFUSED, approval.consume_refusal(req.owner_approval, sha, why, True),
                    'Run this again to ask the hub again: python %s' % approval.retry_command(
                        os.path.join(KIT_ROOT, 'tools', 'commit.py'), req.owner_approval, sha))
    if line is None:
        raise _Stop(REFUSED, 'refused: %s. %s was not consumed.' % (why, req.owner_approval))
    lines.append(line)


def commit(req):
    """Outcome(code, lines) for one commit request (module docstring)."""
    lines = []
    try:
        _commit(req, lines)
    except _Stop as stop:
        return Outcome(stop.code, lines + stop.lines)
    return Outcome(OK, lines)


def _usage(text):
    return _Stop(USAGE, 'USAGE: %s' % text)


def _commit(req, lines):
    root = req.root
    try:
        cfg = kconfig.load(root)
        opts = kconfig.commit_settings(cfg)
        rules = cfg.owner_rules()
    except kconfig.ConfigError as e:
        raise _Stop(USAGE, 'REFUSED: the kit configuration is refused: %s. Nothing was committed.'
                    % e)
    err = approval.usage_error(req)
    if err:
        raise _usage(err)
    if req.allow_empty and (req.paths or req.owner_approval or req.consume is not None):
        raise _usage('--allow-empty takes no paths and no --owner-approval: it commits only the '
                     'message and its trailers')
    if req.consume is not None:
        return _consume(req, lines)
    model, source, err = attribution.resolve_model(req.owner_authored, req.model, req.env, req.provider)
    if err:
        raise _usage(err)
    if source not in (None, '--model'):
        lines.append('model     %s (from %s)' % (model, source))

    body, err = message_body(req.b, req.body_file, root, req.cwd)
    if err:
        raise _usage(err)
    # The owner's own commit is exempt: the owner may keep a path literal on purpose.
    err, msg_warnings = ((None, []) if req.owner_authored
                         else machine_path_error(req.subject, body, req.b))
    if err:
        raise _Stop(REFUSED, err, *msg_warnings)
    lines.extend(msg_warnings)

    err = main_checkout_error(req.lander_main_checkout, root, opts['main_branch'],
                              opts['rule_pointer'])
    if err:
        raise _Stop(USAGE, err)

    paths = []
    for p in req.paths:
        rel = repo_path(p, root)
        if rel is None:
            raise _usage('"%s" is outside this worktree (%s); name a file inside it' % (p, root))
        paths.append(rel)
    for p in paths:
        if p in ('.', '-A', '--all') or '*' in p or os.path.isdir(os.path.join(root, p)):
            raise _usage('"%s" is not an explicit file path; name each file' % p)

    err = model_requirement_error(rules, req.owner_authored, req.owner_approved, paths)
    if err:
        raise _usage(err)

    hub = _approval_first(req, root, paths) if req.owner_approval else None
    ran = render_sources(root, paths)
    rendered = ran or []
    named = set(paths) | set(rendered)
    status_before = staged_status(root)
    heads = merge_heads(root) if opts['merge_fold'] else []
    landed = landed_main(root, opts['remote'], opts['main_branch']) if heads else []
    merged, merge_why = ownerrules.merge_owned(root, rules, set(status_before) - named, heads,
                                               landed)
    foreign = set(status_before) - named - merged
    for p in ownerrules.dropped_owner_changes(root, rules, heads or merge_heads(root)):
        lines.append("WARNING: this merge drops the merged branch's change to owner-only %s (it "
                     "commits HEAD's copy); if that is not meant, restore it from the merged "
                     "branch" % p)
    if req.allow_empty and status_before:
        raise _Stop(REFUSED, 'refused: --allow-empty commits nothing staged, but this is staged: '
                    '%s. Commit it by name, or unstage it. Nothing was committed.'
                    % ', '.join(sorted(status_before)))
    if req.allow_empty and (heads or merge_heads(root)):
        raise _Stop(REFUSED, 'refused: --allow-empty during a merge in progress; commit the merge '
                    'by naming its resolved files. Nothing was committed.')
    if req.allow_empty and not head_commit(root):
        raise _Stop(REFUSED, 'refused: --allow-empty on an unborn branch: there is no HEAD to keep, '
                    'so it would make an empty root commit. Nothing was committed.')
    if foreign:
        raise _Stop(REFUSED, foreign_refusal(foreign, merge_why, len(heads) == 1))
    # A path given to another record by a narrowing pull (docs/commit.md "A narrowed record"):
    # every named path, both sides of a rename included; merge-owned paths are main's, unchecked.
    # Imported here: kitlib.backlog is heavy, and most commits hold no narrowed record.
    from kitlib.backlog import narrowed as narrowmod
    refusal, warnings = narrowmod.check(root, sorted(named))
    lines.extend(warnings)
    if refusal:
        raise _Stop(REFUSED, *(refusal + ['Nothing was committed.']))
    if hub:
        hub = _approval_before_write(root, hub, paths, status_before)
    approved = canonical_approved(rules, req.owner_approved, paths)
    locked = locked_paths(rules, paths + [p for p in rendered if p not in paths])
    shrunk = set()      # judged again on the staged copy, in _stage
    if locked and not req.owner_authored and not hub and not merge_heads(root):  # a merge: never
        shrunk = shrunk_paths(root, rules, locked)
        locked = [p for p in locked if p not in shrunk]
    if locked and not req.owner_authored and not hub:
        # The hint only for a path the merge would carry unnamed, as it is staged now.
        carried = sorted(ownerrules.merge_owned(root, rules, set(locked) & set(status_before),
                                                heads, landed)[0])
        raise _Stop(REFUSED, owner_only_refusal(locked, carried))
    for p in ([] if hub else unapproved_locked(rules, locked, approved)):
        raise _Stop(REFUSED, 'refused: %s is owner-only and no --owner-approved value names it.' % p)
    if req.owner_authored:
        err = owner_identity_error(rules, author_email(root))
        if err:
            raise _Stop(REFUSED, err)
    body, session = session_lines(body, req.owner_authored, req.env, req.provider)
    trailers = build_trailers(rules, approved, req.owner_authored, model, opts['coauthor_families'],
                              req.provider, session)
    written = None
    if hub:
        trailers.insert(0, approval.trailer(req.owner_approval))
        written, why = approval.write(root, hub.doc, hub.plan)
        if written is None:
            raise _Stop(REFUSED, 'refused: %s. Nothing was committed.' % why)
    try:
        head, snap, status_after, others, msg = _stage(
            req, root, paths, rendered, named, merged, status_before, msg_warnings, body,
            trailers, lines, hub, (rules, shrunk))
        try:
            rc, out, err, new, notes, trees = commit_only(root, head, snap, msg,
                                                          allow_empty=req.allow_empty)
        except _Stop as stop:
            if hub:                 # the commit may stand: the work tree is left as committed
                stop.lines.append('%s was not consumed; the approved content is left in the work '
                                  'tree.' % req.owner_approval)
            written = None
            raise
        if rc != 0:
            raise _Stop(REFUSED, 'commit failed: %s' % (err or out.strip() or 'exit %s' % rc))
    except _Stop as stop:
        if written is None:
            raise
        raise _Stop(stop.code, *_put_back(root, written, paths, stop.lines))
    _rc, short, _err = ownerrules.run_git(root, 'rev-parse', '--short', new)
    lines.append('committed %s  %s' % (short.strip() or new[:12], req.subject))
    lines.append('files     %s' % (', '.join(
        paths + sorted((merged & set(status_after)) - set(paths))) or 'none (--allow-empty)'))
    lines.extend(notes)
    aligned, misaligned = align_index(root, trees, new) if trees else ([], [])
    lines.extend(aligned)
    try:
        others = set(staged_status(root))    # against the new HEAD: what is still staged
    except _Stop:
        pass                                 # the commit stands; the earlier read says enough
    if others:
        lines.append(left_staged_line(others))
    if ran is not None:
        lines.append('rendered  %s' % (', '.join(p for p in rendered if p not in paths)
                                       or 'no rendered file changed'))
    if any(p.endswith('.md') for p in status_after) or locked:
        line, ok = doc_budget_check(root)
        lines.append(line)
        iline, iok = doc_index_check(root)
        if iline:
            lines.append(iline)
        if not (ok and iok):
            lines.append('          fix it in a follow-up commit before this branch can land')
    if hub:
        line, why, consumed = approval.consume_commit(root, hub.client, hub.did,
                                                      req.owner_approval, new)
        if line:
            lines.append(line)
        else:
            misaligned = [
                approval.consume_refusal(req.owner_approval, new, why, consumed),
                'The commit %s stands; gate land and the pre-push hook refuse it until the hub '
                'verifies the approval as consumed by it. Run in this work tree%s: python %s' % (
                    new[:12], ' to ask the hub again' if consumed else '',
                    approval.retry_command(os.path.join(KIT_ROOT, 'tools', 'commit.py'),
                                           req.owner_approval, new))
            ] + list(misaligned)
    if misaligned:
        raise _Stop(REFUSED, *misaligned)
