"""The checks of a worktree removal (docs/worktree-remove.md): the context every verb opens
(`open_ctx`), git reads, and every check that refuses before anything is removed. Nothing here
writes; kitlib.worktree.remove removes and kitlib.worktree.remove_abandon abandons.

`check(top)` acts on the MAIN checkout of the repository `top` is in (the main checkout or any
linked worktree) and returns Outcome(code, lines). Nothing here prints. The checks, in order, are
the PM tool's:

- the target: under `"repo".worktrees_dir` or another registered host's worktree folder
  (kitlib.worktree.hostdirs; homes_of: a folder reached through a junction is not one), not
  `"worktree_remove".exempt`, present, registered (a stump is named and never deleted), and on
  one of `branch_template`'s branches when one is set, unless it is detached; or, named
  by its PATH, a registered DETACHED tree outside those folders (`outside_target`: not the main
  checkout nor in it, no link on the way, not locked, holding no other registered tree); the plain
  verb (`remove`) alone also takes such a tree on a branch (the owner's rule: only that deliberate
  by-path call clears one), every later check still running. A slug never names an outside tree:
  one that only matches such a tree is refused with its path-form command (`slug_redirect`);
- landed: the branch (or detached HEAD) is an ancestor of `<remote>/<main_branch>` AS IT STANDS,
  never fetched (`merge_check`); UNLANDED work is the owner's to remove and the refusal prints the
  owner's commands and the `--abandon` verb, running none of them;
- on a tree outside the worktree folders or a DETACHED one anywhere, every commit in its HEAD
  reflog is reached by a ref (`kept_reflog`; a commit left there by a checkout lives only in that
  log, which `git worktree remove` deletes), and HEAD and the log are read again just before git
  (`unmoved`);
- holders (`holders.blockers`), then work (`dirty`: anything uncommitted or untracked, and any
  git-IGNORED file outside `build_output` or a keeper inside it; a `{piece, path}` entry is the
  folder the pieces table puts there, and one that cannot be resolved holds nothing), then links
  or junctions inside.

Exit codes: OK 0; REFUSED 1 (refused, a leftover exists, git failed, or `git branch -d` refused
after the removal); CONFIG 2 (the kit configuration was refused).
"""
import collections
import fnmatch
import json
import os

from kitlib import kitroot
from kitlib import pieces
import kitlib.config as kconfig
from kitlib.config import ConfigError, load
from kitlib.git import cmd, refs, repo
from kitlib.host.registry import RegistryError

from . import holders, hostdirs
from .holders import canon


OK, REFUSED, CONFIG = 0, 1, 2
Outcome = collections.namedtuple('Outcome', 'code lines')

TOOL = 'worktree_remove'
ARCHIVE = 'archive/'
# Files that refuse a removal even inside build output (a dev database under bin/, SQLite's
# journals, keys, model weights, lowercase *.local.*). `"worktree_remove".keepers` adds to these.
KEEPERS = ('*.db', '*.db-wal', '*.db-shm', '*.sqlite', '*.key', '*.key.*', '*.pem', '*.pfx',
           '*.npz', '*.pt', '*.ckpt', '*.safetensors', '*.onnx', '*.local.*')
# --no-optional-locks: a status may otherwise rewrite the worktree's index (a write by a read),
# and the sweep's idle check reads that git dir.
STATUS_ARGS = ('--no-optional-locks', 'status', '--porcelain', '--branch', '--untracked-files=all',
               '--ignored=matching')


class GitReadError(RuntimeError):
    """A git read that failed, or printed nothing where git always answers: never an empty answer."""


def git_env():
    """Every git here: the scrubbed environment (cmd.scrubbed_env) with GCM_INTERACTIVE=never, so
    Git Credential Manager never opens a prompt the nightly task could not answer."""
    return dict(cmd.scrubbed_env(), GCM_INTERACTIVE='never')


def git(cwd, *args, timeout=cmd.GIT_TIMEOUT, env=None, stdin=None):
    """`(rc, stdout stripped, stderr)`; rc None when git did not run."""
    r = cmd.call(list(args), cwd, stdin=stdin, timeout=timeout, env=env or git_env())
    return r.rc, cmd.text(r.out or b'').strip(), (r.err or '').strip()


def failure(args, rc, err):
    """How a git read went wrong: the command, its exit code (or that it did not run), its stderr."""
    err = '; '.join(line.strip() for line in (err or '').splitlines() if line.strip())
    if rc is None:
        return 'git %s did not run: %s' % (' '.join(args), err or '?')
    return 'git %s exited %d%s' % (' '.join(args), rc, ': ' + err if err else ' and printed nothing')


def read(cwd, *args):
    """git's stdout for a read whose answer is never empty; GitReadError otherwise."""
    rc, out, err = git(cwd, *args)
    if rc != 0 or not out:
        raise GitReadError(failure(args, rc, err))
    return out


class Ctx(object):
    """One run: the main checkout, its kit Config, the settings below, the caller's directory and
    the output lines."""

    def __init__(self, main, cfg, cwd=None):
        self.main, self.cfg = main, cfg
        self.cwd = cwd or os.getcwd()
        self.s = settings(main, cfg)
        self.out = []

    def say(self, *lines):
        self.out.extend(lines)

    def describe(self, path, why):
        self.say(*holders.describe(path, why, cwd=self.cwd))

    def outcome(self, code):
        return Outcome(code, list(self.out))


def piece_dirs(cfg, entries):
    """`(dirs, None)`: each `{piece, path}` build_output entry as the literal repo-relative folder
    the pieces table puts it at (no trailing `/`), or `([], why)` when any of them cannot be
    resolved - no table, a malformed one (of any shape: the section is opaque, so anything the
    table load raises counts), an unknown piece, a piece or path that is not a string (the schema
    lets a null through), a path that is empty, absolute, `..`-climbing, backslashed or a glob,
    and a piece dir with an empty or `.` part. Never raises: a caller refuses on `why` (fail
    closed)."""
    if not entries:
        return [], None
    try:
        table = pieces.load_repo(None, cfg)
    except Exception as e:      # an opaque section: any shape can fail any way; refuse, never crash
        return [], 'the pieces table cannot be read (%s: %s)' % (type(e).__name__, e)
    dirs = []
    for ent in entries:
        pid, rel = ent.get('piece'), ent.get('path')
        if not isinstance(pid, str) or not isinstance(rel, str):
            return [], ('build_output entry %s: "piece" and "path" must both be strings'
                        % json.dumps(ent))
        parts = rel.rstrip('/').split('/')
        if (not rel.rstrip('/') or rel.startswith('/') or '\\' in rel or ':' in rel
                or any(c in rel for c in '*?[]') or '..' in parts or '' in parts or '.' in parts):
            return [], ('build_output entry %s: "path" must be a folder relative to the piece,'
                        ' `/`-separated, with no glob, `..` or empty part' % json.dumps(ent))
        try:
            folder = table.path(pid, rel).rstrip('/')
        except KeyError as k:
            return [], 'build_output entry %s: %s' % (json.dumps(ent), k.args[0])
        except Exception as e:
            return [], 'build_output entry %s: the pieces table cannot resolve it (%s: %s)' % (
                json.dumps(ent), type(e).__name__, e)
        if any(p in ('', '.', '..') for p in folder.split('/')):
            return [], ('build_output entry %s: piece %r has dir %r, which has an empty or `.`'
                        ' part, so it names no folder git prints' % (json.dumps(ent), pid,
                                                                       table.dir(pid)))
        dirs.append(folder)
    return dirs, None


def host_folders(cfg):
    """Every host's worktree folder (kitlib.worktree.hostdirs). A hosts.json that cannot be read
    leaves only "repo".worktrees_dir: removal then finds fewer worktrees, never more."""
    try:
        return hostdirs.folders(cfg)
    except RegistryError:
        return (os.path.normpath(cfg.repo['worktrees_dir']),)


def templates(value):
    """`branch_template` as a list of {slug} templates (the schema allows one string or a list)."""
    if not value:
        return []
    return [value] if isinstance(value, str) else list(value)


def settings(main, cfg):
    """This repository's values (the schema has already checked their shapes)."""
    wr = cfg.section(TOOL)
    build = list(wr.get('build_output') or [])
    dirs, dirs_error = piece_dirs(cfg, [b for b in build if isinstance(b, dict)])
    return {
        'base': kconfig.work_base(cfg), 'main': cfg.main_branch, 'remote': cfg.remote,
        'upstream': '%s/%s' % (cfg.remote, kconfig.work_base(cfg)),
        'worktrees_dir': os.path.normpath(cfg.repo['worktrees_dir']),
        'worktrees_dirs': list(host_folders(cfg)),
        'tool': kitroot.command('worktree_remove', main),     # the kit's own tool (no repository copy)
        'name': cfg.repo.get('name') or os.path.basename(os.path.normpath(main)),
        'exempt': dict(wr.get('exempt') or {}),
        'disposable_untracked': list(wr.get('disposable_untracked') or []),
        'build_output': [b for b in build if isinstance(b, str)],
        'build_dirs': dirs, 'build_dirs_error': dirs_error,
        'keepers': list(KEEPERS) + list(wr.get('keepers') or []),
        'archive_next_free': bool(wr.get('archive_next_free')),
        'branch_template': templates(wr.get('branch_template')),
    }


def main_checkout(start):
    """The checkout that owns the worktrees dir, from any directory of the repository, in its one
    real spelling. GitReadError when git cannot say (never a fallback to `start`)."""
    main = repo.main_checkout_toplevel(start)
    if main is None:
        raise GitReadError('git cannot place %s in a main checkout' % start)
    return os.path.realpath(os.path.abspath(main))


def open_ctx(top, cwd=None):
    """`(Ctx, None)` for the repository `top` is in, or `(None, Outcome)` refusing."""
    try:
        main = main_checkout(top)
    except GitReadError as e:
        return None, Outcome(REFUSED, ['failed    cannot find the main checkout (%s); nothing was'
                                       ' checked or removed' % e])
    try:
        cfg = load(main)
        ctx = Ctx(main, cfg, cwd)
    except ConfigError as e:
        return None, Outcome(CONFIG, ['%s: refused: kit configuration: %s' % (TOOL, e)])
    if not holders.WINDOWS:
        ctx.say('note      the destructive failure this guards is a Windows one; checking anyway')
    return ctx, None


def home_of(ctx):
    return os.path.normpath(os.path.join(ctx.main, ctx.s['worktrees_dir']))


def homes_of(ctx):
    """Primary root, plus non-redirected roots of other supported hosts."""
    primary = home_of(ctx)
    homes = [primary]
    for folder in ctx.s.get('worktrees_dirs', ()):
        candidate = os.path.abspath(os.path.join(ctx.main, folder))
        if canon(candidate) == canon(primary):
            continue
        # Additional host roots cannot escape the checkout through a junction.
        if os.path.normcase(os.path.realpath(candidate)) != os.path.normcase(candidate):
            continue
        if canon(candidate).startswith(canon(ctx.main) + os.sep):
            homes.append(candidate)
    return homes


def is_workstream(ctx, path):
    return any(canon(path).startswith(canon(home) + os.sep) for home in homes_of(ctx))


def worktree_listing(main):
    """`git worktree list --porcelain`'s text; GitReadError when it failed or printed nothing."""
    return read(main, 'worktree', 'list', '--porcelain')


def registrations(main, listing=None):
    """canon(path) -> (path as git registered it, branch or '(detached)')."""
    out, path = {}, None
    for line in (worktree_listing(main) if listing is None else listing).splitlines():
        if line.startswith('worktree '):
            path = line[len('worktree '):]
            out[canon(path)] = (path, '(detached)')
        elif line.startswith('branch ') and path:
            out[canon(path)] = (path, line[len('branch refs/heads/'):])
    return out


def registered(main, listing=None):
    return {key: branch for key, (_, branch) in registrations(main, listing).items()}


def merge_base_ref(s):
    """`refs/remotes/<remote>/<base>` (the work base, kitlib.config.work_base): only the PUSHED
    base counts."""
    return 'refs/remotes/%s/%s' % (s['remote'], s['base'])


def base_commit(ctx):
    """`(full hash, None)` of the base as it stands, or `(None, why)`."""
    args = ('rev-parse', '--verify', merge_base_ref(ctx.s) + '^{commit}')
    rc, base, err = git(ctx.main, *args)
    if rc != 0 or not base:
        return None, failure(args, rc, err)
    return base, None


def checked_out(main, key):
    """(branch or None, HEAD or None) of the registered worktree whose canon(path) is `key`."""
    branch = head = path = None
    for line in worktree_listing(main).splitlines():
        if line.startswith('worktree '):
            path = canon(line[len('worktree '):])
        elif path == key and line.startswith('HEAD '):
            head = line[len('HEAD '):].strip()
        elif path == key and line.startswith('branch '):
            branch = line[len('branch refs/heads/'):].strip()
    return branch, head


def merge_check(ctx, key, registered_path, seen=None):
    """None when the worktree's work is already in the base, else the refusal lines. The base is
    read as it stands, never fetched; a failure of the check itself refuses with no commands.
    `seen` (a dict), when given, gets the HEAD this check judged under 'head'."""
    s, main = ctx.s, ctx.main
    up = s['upstream']
    registered_path = os.path.normpath(registered_path)
    try:
        branch, head = checked_out(main, key)
    except GitReadError as e:
        return ['refused   %s: the worktree list could not be read (%s), so whether its work has'
                ' landed is unknown; nothing was removed' % (registered_path, e)]
    if seen is not None:
        seen['head'] = head
    tip = ('refs/heads/' + branch) if branch else head
    name = branch or ('detached HEAD %s' % (head or '?'))
    if not tip:
        return ['refused   %s: git reported no HEAD for it, so whether its work has landed is'
                ' unknown; nothing was removed' % registered_path]
    base, why = base_commit(ctx)
    if base is None:
        return ['refused   %s: cannot read %s (%s), so whether %s has landed is'
                ' unknown; nothing was removed' % (registered_path, up, why, name)]
    merged, why = refs.is_ancestor(tip, base, main)
    if merged:
        ctx.say('merged    %s is in %s %s' % (name, up, base[:10]))
        return None
    if merged is None:
        return ['refused   %s: the merge check failed (%s); nothing was removed'
                % (registered_path, why)]
    rc, count, err = git(main, 'rev-list', '--count', '%s..%s' % (base, tip))
    count = count if rc == 0 and count else 'an unknown number of'
    slug = os.path.basename(registered_path)
    named = handle(ctx, registered_path)
    lines = ['refused   %s holds UNLANDED work: %s has %s commit(s) not in %s %s;'
             ' nothing was removed' % (registered_path, name, count, up, base[:10]),
             '  abandon  the supported route, run on the owner\'s or the PM\'s ruling: it keeps the'
             ' commits under the tag %s%s (--tag %s<name> for the tag a ruling names), then'
             ' removes the tree:' % (ARCHIVE, slug, ARCHIVE),
             '             %s %s --abandon "<why>"' % (s['tool'], named),
             '  owner    removing unlanded work outright is the owner\'s standing rule that'
             ' deleting data is the owner\'s, and this verb has no override. To discard it, the'
             ' owner runs, in the owner\'s own terminal only (the Action Hub refuses these as a'
             ' to-do),',
             '           from %s:' % main,
             '             git worktree remove "%s"' % registered_path]
    if branch:
        lines.append('             git branch -D %s' % branch)
    lines.append('  note     %s was read as it stands, not fetched: if the branch has'
                 ' landed since, `git fetch %s` and run this again.' % (up, s['remote']))
    return lines


def exemption(s, path):
    """The ruling exempting the folder at `path` (`"worktree_remove".exempt`), or None."""
    name = os.path.normcase(os.path.basename(os.path.normpath(path)))
    for slug, ruling in s['exempt'].items():
        if os.path.normcase(slug) == name:
            return ruling
    return None


def leftovers(ctx, listing):
    """(stumps, orphans): directories git does not know about, and registered entries under the
    worktrees dir whose directory is gone. Exempt folders are neither."""
    known = registered(ctx.main, listing)
    stumps = []
    for home in homes_of(ctx):
        for name in sorted(os.listdir(home)) if os.path.isdir(home) else []:
            path = os.path.join(home, name)
            if os.path.isdir(path) and canon(path) not in known and not exemption(ctx.s, path):
                stumps.append(path)
    orphans = [p for p in sorted(known) if is_workstream(ctx, p)
               and not os.path.isdir(p) and not exemption(ctx.s, p)]
    return stumps, orphans


def ancestry(ctx, base):
    """`((first-parent chain, ancestors), None)` of `base` from one `git rev-list --parents`, or
    `(None, why)`."""
    from kitlib.git import batch
    return batch.ancestry(base, ctx.main, timeout=cmd.GIT_TIMEOUT, env=git_env())


def merged_worktrees(ctx, listing, skip=()):
    """`([(path, branch or None)], None)`: registered worktrees in the worktrees dir whose own work
    has LANDED: the tip is in the base AND off its first-parent chain (a fresh worktree, tip ==
    a base tip, is never one); or `(None, why)`. Paths in `skip` are left out (preflight leaves
    out the worktree it runs in)."""
    base, why = base_commit(ctx)
    if base is None:
        return None, why
    skipped = {canon(p) for p in skip}
    candidates = []
    for e in worktree_entries(listing):
        path = e['path']
        if not is_workstream(ctx, path) or canon(path) in skipped \
                or exemption(ctx.s, path) or not os.path.isdir(path):
            continue
        if e['head']:
            candidates.append(e)
    if not candidates:
        return [], None
    got, why = ancestry(ctx, base)
    if got is None:
        return None, 'git rev-list --parents %s: %s' % (base, why)
    chain, ancestors = got
    out = []
    for e in candidates:
        if e['head'] not in chain and e['head'] in ancestors:
            out.append((os.path.normpath(e['path']), e['branch']))
    return out, None


def worktree_entries(listing):
    """Each registered worktree as a dict: path, head, branch (None detached), locked (None, or
    git's reason, '' for none)."""
    out = []
    for line in listing.splitlines():
        if line.startswith('worktree '):
            out.append({'path': line[len('worktree '):], 'head': None, 'branch': None,
                        'locked': None})
        elif not out:
            continue
        elif line.startswith('HEAD '):
            out[-1]['head'] = line[len('HEAD '):].strip()
        elif line.startswith('branch '):
            out[-1]['branch'] = line[len('branch refs/heads/'):].strip()
        elif line == 'locked' or line.startswith('locked '):
            out[-1]['locked'] = line[len('locked'):].strip()
    return out


def check(top, cwd=None):
    """`--check`: leftovers (stumps, orphans) and, for information, landed worktrees. Exit 1 when a
    stump or orphan exists."""
    ctx, refused = open_ctx(top, cwd)
    if refused:
        return refused
    try:
        listing = worktree_listing(ctx.main)
    except GitReadError as e:
        ctx.say('failed    %s; nothing is listed: which directories git knows is unknown' % e)
        return ctx.outcome(REFUSED)
    stumps, orphans = leftovers(ctx, listing)
    for path in stumps:
        why = holders.blockers(path)
        ctx.say('leftover  %s' % path)
        ctx.describe(path, why)
        ctx.say('           the slug stays unusable until whatever holds it lets go.' if why else
                '           the slug is free again as soon as a human deletes this directory.')
    for path in orphans:
        ctx.say('orphan    %s registered with no directory on disk; run `git worktree repair'
                ' <path>` first, and prune only a single entry proven empty and merged' % path)
    merged, why = merged_worktrees(ctx, listing)
    if merged is None:
        ctx.say('merged    not checked: %s' % why)
    for path, branch in merged or []:
        ctx.say('merged    %s [%s] landed: its own commits are in %s; `%s %s` removes it%s'
                % (path, branch or 'detached', ctx.s['upstream'], ctx.s['tool'],
                   os.path.basename(path) if canon(path).startswith(canon(home_of(ctx)) + os.sep) else '"%s"' % path,
                   ' and its branch' if branch else ''))
    if not stumps and not orphans:
        ctx.say('clean     every directory under %s is a registered worktree'
                % ctx.s['worktrees_dir'])
        return ctx.outcome(OK)
    return ctx.outcome(REFUSED)


def target_path(ctx, argument):
    """The absolute path `argument` names: a path (holding a separator) against the caller's
    directory, a slug under the worktrees dir."""
    if os.path.sep in argument or '/' in argument:
        return os.path.abspath(os.path.join(ctx.cwd, argument))
    return os.path.abspath(os.path.join(home_of(ctx), argument))


def handle(ctx, registered_path):
    """How a printed command names a tree: its slug in a worktree folder, else its quoted path
    (a slug always resolves under the worktree folder)."""
    if is_workstream(ctx, registered_path):
        return os.path.basename(os.path.normpath(registered_path))
    return '"%s"' % os.path.normpath(registered_path)


def _under(path, root):
    return canon(path).startswith(canon(root).rstrip('\\/') + os.sep)


def link_on_the_way(path):
    """The first folder of `path`, itself or an ancestor, that is a link, junction or other reparse
    point (holders.linked), or None. Another spelling of the same folders - an 8.3 short name, a
    subst drive - is no link, so it is not refused: canon resolves it to the tree git registered."""
    p = os.path.abspath(path)
    while True:
        if holders.linked(p):
            return p
        parent = os.path.dirname(p)
        if parent == p:
            return None
        p = parent


def kept_reflog(ctx, path, head, tagged):
    """The HEAD reflog (kitlib.worktree.scratch.reflog) of a tree outside every worktree folder or
    detached anywhere, when every commit in it is kept: reached by a branch, remote-tracking
    branch or tag, and with `tagged` (--abandon) also by `head`, which the tag is about to hold. Else None after saying why: a commit made in the tree
    and left by a checkout lives only in `logs/HEAD`, which `git worktree remove` deletes, and a
    commit no ref reaches is the owner's call (the --scratch verb's rule). Reads only."""
    from . import scratch           # scratch imports this module
    logged, why = scratch.reflog(path)
    if logged is None:
        ctx.say('refused   %s: %s; nothing was %s' % (path, why, 'tagged or removed' if tagged
                                                         else 'removed'))
        return None
    if not tagged:
        return logged if scratch.all_reached(ctx, path, head, logged) else None
    # The tag on HEAD keeps HEAD's history: HEAD counts as one more ref. One rev-list for them all.
    lost, why = scratch._reaches(ctx, [o for o in logged if o != head], also=(head,))
    if lost is None:
        ctx.say('refused   %s: whether a ref reaches its HEAD reflog is unknown (%s); nothing was'
                ' tagged or removed' % (path, why))
        return None
    if lost:
        ctx.say('refused   %s: its HEAD reflog or a per-worktree ref holds %s, which no ref'
                ' reaches and the tag on HEAD'
                ' %s would not keep (a commit made there and left behind); whether to drop it is'
                ' the owner\'s call; nothing was tagged or removed'
                % (path, ', '.join(o[:10] for o in lost), head[:10]))
        return None
    return logged


def unmoved(ctx, path, head, logged):
    """kitlib.worktree.scratch.unmoved: HEAD and its reflog read once more just before git, so a
    commit made while the verb ran is not removed with an outside or detached tree."""
    from . import scratch
    return scratch.unmoved(ctx, path, head, logged)


def outside_target(ctx, path, branch_ok=False):
    """`(path, registrations)` for a registered DETACHED worktree outside every worktree folder,
    named by its path (an agent's red-run tree in a scratchpad), or the exit status after saying
    why not (a missing folder: OK). The scope is git's own registration list. With `branch_ok`
    (only the plain verb, `remove`, by the owner's rule) a tree on a branch is taken too, and
    `merge_check` then judges that branch; `--abandon`, the landing cleanup and classify (which
    call `_remove` directly) take only a detached one."""
    main, nothing = ctx.main, 'nothing was removed'
    where = ('is the main checkout' if canon(path) == canon(main) else
             'is inside the main checkout' if _under(path, main) else
             'holds the main checkout' if _under(main, path) else None)
    if where:
        ctx.say('refused   %s is not under %s: it %s %s, and this tool never removes the main'
                ' checkout or a tree inside it outside the worktree folders; %s'
                % (path, home_of(ctx), where, main, nothing))
        return REFUSED
    link = link_on_the_way(path)
    if link:
        ctx.say('refused   %s: a link or junction is on the way to it (%s is one; it resolves to'
                ' %s); %s' % (path, link, os.path.realpath(path), nothing))
        return REFUSED
    if exemption(ctx.s, path):
        ctx.say('refused   %s is exempt (%s): this tool never removes it; nothing was touched'
                % (path, exemption(ctx.s, path)))
        return REFUSED
    if not os.path.isdir(path):
        ctx.say('nothing   %s does not exist' % path)
        return OK
    try:
        listing = worktree_listing(main)
    except GitReadError as e:
        ctx.say('refused   %s: the worktree list could not be read (%s), so whether it is a'
                ' registered worktree is unknown; %s' % (path, e, nothing))
        return REFUSED
    entries = worktree_entries(listing)
    entry = next((e for e in entries if canon(e['path']) == canon(path)), None)
    if entry is None:
        ctx.say('refused   %s is not a registered worktree of %s; this tool never deletes a'
                ' folder git does not know; %s' % (path, main, nothing))
        return REFUSED
    inner = [os.path.normpath(e['path']) for e in entries if _under(e['path'], path)]
    if inner:
        ctx.say('refused   %s holds the registered worktree %s; %s'
                % (path, '; '.join(inner), nothing))
        return REFUSED
    if entry['locked'] is not None:
        ctx.say('refused   %s is locked (%s); %s' % (path, entry['locked'] or 'no reason', nothing))
        return REFUSED
    if entry['branch'] and not branch_ok:
        ctx.say('refused   %s is on branch %s: outside the worktree folders only a detached tree'
                ' is removed here (a branch\'s worktree is a workstream\'s, under %s; the plain'
                ' verb named by its path removes one whose branch has landed); %s'
                % (path, entry['branch'], home_of(ctx), nothing))
        return REFUSED
    if entry['branch'] and is_base(entry['branch'], ctx.s):
        ctx.say('refused   %s is on the main branch %s: this tool never removes a tree on it nor'
                ' deletes it; %s' % (path, entry['branch'], nothing))
        return REFUSED
    if not entry['head']:
        ctx.say('refused   %s: git reported no HEAD for it; %s' % (path, nothing))
        return REFUSED
    return path, registrations(main, listing)


def outside_matches(ctx, slug):
    """The registered worktrees outside every worktree folder whose folder is named `slug`, as
    git spells them; GitReadError when the list cannot be read."""
    return [os.path.normpath(e['path']) for e in worktree_entries(worktree_listing(ctx.main))
            if os.path.normcase(os.path.basename(os.path.normpath(e['path'])))
            == os.path.normcase(slug) and not is_workstream(ctx, e['path'])
            and canon(e['path']) != canon(ctx.main)]


def slug_redirect(ctx, argument, path):
    """For a slug with no folder under the worktree folder: REFUSED after naming the path-form
    command of each outside tree of that name (never removed by its name), else None."""
    try:
        found = outside_matches(ctx, argument)
    except GitReadError as e:
        # A slug with no folder was `nothing` (exit 0) before outside trees existed; a list that
        # cannot be read removes nothing either, so it stays `nothing`, with the doubt said.
        ctx.say('note      the worktree list could not be read (%s), so whether a worktree outside'
                ' the worktree folders is named %s is unknown' % (e, argument))
        return None
    if not found:
        return None
    ctx.say('refused   %s is not under %s: a worktree named %s is registered outside the worktree'
            ' folders, and a slug never names one; nothing was removed. Name it by its path:'
            ' --scratch for a red-run tree under a temp folder (its HEAD and reflog need only be in'
            ' some ref; uncommitted files git already holds are restored), the plain verb when its'
            ' HEAD is in %s, --abandon to keep unlanded work under a tag:'
            % (argument, home_of(ctx), argument, ctx.s['upstream']))
    for p in found:
        ctx.say('             %s "%s" --scratch' % (ctx.s['tool'], p),
                '             %s "%s"' % (ctx.s['tool'], p),
                '             %s "%s" --abandon "<why>"' % (ctx.s['tool'], p))
    return REFUSED


def registered_target(ctx, argument, branch_ok=False):
    """`(path, registrations)` for the registered workstream worktree `argument` names, or, named
    by its path, a detached one outside the worktree folders (`outside_target`; also one on a
    branch with `branch_ok`); else the exit status after saying why there is none (absent: 0)."""
    s = ctx.s
    path = target_path(ctx, argument)
    by_path = os.path.sep in argument or '/' in argument
    if not is_workstream(ctx, path):
        if by_path:
            return outside_target(ctx, path, branch_ok)
        ctx.say('refused   %s is not under %s; this tool removes workstream worktrees only'
                % (path, home_of(ctx)))
        return REFUSED
    if exemption(s, path):
        ctx.say('refused   %s is exempt (%s): this tool never removes it; nothing was touched'
                % (path, exemption(s, path)))
        return REFUSED
    if not os.path.isdir(path):
        if not by_path:
            redirected = slug_redirect(ctx, argument, path)
            if redirected is not None:
                return redirected
        ctx.say('nothing   %s does not exist' % path)
        return OK
    try:
        known = registrations(ctx.main)
    except GitReadError as e:
        ctx.say('refused   %s: the worktree list could not be read (%s), so whether it is a'
                ' registered worktree is unknown; nothing was removed' % (path, e))
        return REFUSED
    if canon(path) not in known:
        ctx.say('leftover  %s is not a registered worktree - the stump of a removal that already'
                ' failed' % path)
        ctx.describe(path, holders.blockers(path))
        ctx.say('           git cannot remove it (`not a working tree`) and neither will this tool.',
                '           Clearing it is a human decision; this tool never deletes a leftover.')
        return REFUSED
    registered_path, branch = known[canon(path)]
    # A detached tree has no branch to hold to a template; the HEAD reflog check
    # (kept_reflog) still guards what it made.
    if s['branch_template'] and branch != '(detached)':
        slug = os.path.basename(os.path.normpath(registered_path))
        wants = [t.replace('{slug}', slug) for t in s['branch_template']]
        if branch not in wants:
            ctx.say('refused   %s is on %s, not %s: this repository removes only worktrees on'
                    ' their own %s branch (.kit.json worktree_remove.branch_template); nothing was'
                    ' removed' % (path, branch, ' or '.join(wants), ' or '.join(s['branch_template'])))
            return REFUSED
    return path, known


def dirty(ctx, path):
    """`(work, disposable, ignored, None)`: status lines that are work, the untracked files
    `disposable_untracked` names, and `!! ` lines of ignored files outside build output (or keepers
    inside it); `(None, None, None, why)` when the status cannot be read (never a clean tree).
    When a `{piece, path}` build_output entry cannot be resolved, no file counts as inside it and
    the first `!! ` line outside the globs says why (fail closed: the tree stays)."""
    s = ctx.s
    globs, build, dirs = s['disposable_untracked'], s['build_output'], s['build_dirs']
    unresolved = s['build_dirs_error']
    # --branch puts a `## ` header first, so the strip cannot eat the first entry's leading space.
    rc, out, err = git(path, *STATUS_ARGS)
    if rc != 0 or not out:
        return None, None, None, failure(STATUS_ARGS, rc, err)
    work, disposable, ignored, budget = [], [], [], [holders.WALK_MAX]
    for line in out.splitlines():
        if line.startswith('## '):
            continue
        if line.startswith('!! '):
            rel = line[3:]
            # A quoted path is never matched: it is named in the refusal instead (fail closed).
            if rel.startswith('"') or not holders.is_build_output(rel, build, dirs):
                if unresolved:
                    line, unresolved = '%s (%s)' % (line, unresolved), None
                ignored.append(line)
            else:
                ignored.extend(holders.keepers_in(path, rel, s['keepers'], budget))
            continue
        rel = line[3:] if line.startswith('?? ') and not line[3:].startswith('"') else None
        if rel and any(fnmatch.fnmatch(rel, g) or fnmatch.fnmatch(rel.rsplit('/', 1)[-1], g)
                       for g in globs):
            disposable.append(rel)
        else:
            work.append(line)
    return work, disposable, ignored, None


def ignored_reason(ignored, path):
    """The words for a tree holding ignored files that are not build output."""
    return ('ignored files that are not build output (%d): %s - `git worktree remove` would'
            ' delete them without a word; list them: git -C "%s" status --ignored --porcelain (a'
            " file inside a build-output folder shows there only as its folder); deleting them is"
            " the owner's decision, not an agent's. Move each one worth keeping out of the tree, or"
            ' name them in .kit.json worktree_remove.build_output if they are build output (a'
            ' keeper - KEEPERS or worktree_remove.keepers - refuses even there)'
            % (len(ignored), holders.listed(ignored), os.path.normpath(path)))


def dirty_refusal(ctx, path, what, rerun):
    """None when `path` holds no work and no ignored file outside build output, else says why it
    is refused and returns REFUSED. `what` ends the refusal; `rerun` follows an ignored refusal."""
    work, _, ignored, why = dirty(ctx, path)
    end = what[0].upper() + what[1:]
    if work is None:
        ctx.say('refused   %s: its status could not be read (%s); %s' % (path, why, what))
        return REFUSED
    if work:
        ctx.say('refused   %s has uncommitted or untracked files (%d): %s%s - an agent may still'
                ' be working there%s. %s.'
                % (path, len(work), '; '.join(work[:5]), ' ...' if len(work) > 5 else '',
                   ', and the tag would keep commits only' if 'tagged' in what else '', end))
        return REFUSED
    if ignored:
        ctx.say('refused   %s has %s. %s.' % (path, ignored_reason(ignored, path), end),
                '  rerun    %s' % rerun)
        return REFUSED
    return None


def is_base(branch, s):
    """`branch` is the base (s['base']) or the main branch (s['main']; a staging work base leaves
    the main branch protected too), by same_name."""
    return any(same_name(branch, b) for b in (s['base'], s.get('main', s['base'])))


def same_name(a, b):
    """Branch names git may treat as one: on a case-insensitive file system (core.ignorecase)
    `refs/heads/Main` opens the file of `refs/heads/main`."""
    return a.casefold() == b.casefold()
