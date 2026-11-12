"""The guard's shared-ref rules (kitlib.guard.rules, which re-exports each name here): git update-ref
by an agent session (git-shared-ref) with the ref helpers it reads, the other git forms it judges
the same way, and git that deletes a branch or removes a worktree."""
import os
import re
from kitlib.git import defaults as gitdefaults
from kitlib.git import repo

from . import jail
from . import paths
from . import shell as sh
from .rules_base import UNPLACED, agent_session, bare, fwd, kit_tool, own_worktree, quote, refuse, spelt
from .rules_gitcore import GIT_LONGS, _canon_long, _opts, _pos, computed_word, git_prefix_configs
from .rules_push import _guarded_mains, kit_judged
from .rules_edit import session_mark


def ref_delete_hint(ctx, gc, top, cwd, short, longs, pos, cleanup=False):
    """(sentence, command) for `git update-ref -d <ref>`, or None. Every worktree of a repository
    shares its refs, so neither a worktree nor the lander carries a ref delete to main: a branch
    goes with git branch -d, which refuses an unmerged one; a stray ref (stray_ref) is shown, and
    a coordinator deletes it in its own repository (`cleanup`: the session is one); any other ref
    is shown, and the PM deletes it; a guarded main is never offered to branch -d. Both run in
    the session's own worktree of this main checkout when it has one. None too for a ref
    git_shared_ref lets any session delete (scratch_ref): only where it runs is wrong, and
    git_in_worktree says so."""
    if gc.sub != 'update-ref' or 'd' not in short or 'stdin' in longs or gc.blind:
        return None
    ref = pos[0] if pos else None
    name = ref.text if ref is not None and not ref.dyn else None
    if name and scratch_ref(name):
        return None
    at = quote(own_worktree(ctx, cwd, top) or top)
    shared = 'Every worktree shares this repository\'s refs, so no worktree or landing carries a ref delete to main.'
    if name and name.startswith('refs/heads/') and len(name) > len('refs/heads/'):
        branch = name[len('refs/heads/'):]
        if branch.lower() in main_names(ctx, top):
            return ('%s %s is the main branch: only a landing moves it, and no session deletes it. To show it:' %
                    (shared, name), 'git -C %s show-ref %s' % (at, name))
        return ('%s Delete a branch with git branch -d, which refuses an unmerged one; unmerged work goes to '
                'the PM, never deleted:' % shared, 'git -C %s branch -d %s' % (at, branch))
    if name and stray_ref(name):
        who = 'you delete it in your own repository, naming the ref literally' if cleanup else \
            'your coordinator deletes it from its own session in its own repository, so send it the ref'
        return ('%s A ref outside refs/heads, refs/tags, refs/stash and refs/backlog is a coordinator\'s '
                'to delete: %s. To show it:' %
                (shared, who), 'git -C %s show-ref %s' % (at, name))
    return ('%s A ref other than a branch is the PM\'s to delete; to show it:' % shared,
            'git -C %s show-ref %s' % (at, name or '<ref>'))


# git_shared_ref: the update-ref options it reads (GIT_LONGS['update-ref'] spells the long ones),
# the refs any agent session may write (scratch_ref), and the names git reads as one loose ref file
UPDATE_REF_SHORTS = set('mdz')
PER_WORKTREE_REFS = ('refs/bisect/', 'refs/worktree/', 'refs/rewritten/')
PSEUDOREF_RE = re.compile(r'^[A-Z][A-Z0-9_]*_HEAD$')
REF_NAME_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._/-]*$')
SCRATCH_WALK_MAX = 4096     # entries of a temp repository's git directory read for a link, at most
REPARSE_POINT = 0x400       # FILE_ATTRIBUTE_REPARSE_POINT: a symlink, a junction or another link


def ref_name_ok(name):
    """True for a ref name the guard reads as one file under the repository's git directory: plain
    characters only, no `..`, empty or dot-led component, and no component ending in .lock or a dot."""
    if not name or not REF_NAME_RE.match(name):
        return False
    parts = name.split('/')
    return all(p and not p.startswith('.') and not p.endswith('.') and not p.endswith('.lock') for p in parts)


def per_worktree_ref(name):
    """True for a ref git keeps in each worktree's own git directory: HEAD, a <NAME>_HEAD
    pseudoref, refs/bisect/, refs/worktree/ and refs/rewritten/."""
    return name == 'HEAD' or bool(PSEUDOREF_RE.match(name)) or name.startswith(PER_WORKTREE_REFS)


def scratch_ref(name):
    """True for a ref any agent session may write or delete from any checkout: refs/tmp/<name>
    and the per-worktree refs but HEAD."""
    return ref_name_ok(name) and name != 'HEAD' and (name.startswith('refs/tmp/') or per_worktree_ref(name))


# the main sessions that may delete a stray ref in their own repository (the coordinator only),
# and the refs that are no stray ref, compared without case: NTFS reads refs/HEADS/main as the
# loose file of refs/heads/main
REF_CLEANUP_MARKS = ('coordinator',)
NOT_STRAY = ('refs/heads', 'refs/tags', 'refs/stash', 'refs/backlog')


def stray_ref(name):
    """True for a ref a coordinator may delete in its own repository: a ref_name_ok name under
    refs/, outside refs/heads, refs/tags, refs/stash (every worktree's stash entries) and
    refs/backlog (the backlog store, in any case)."""
    low = (name or '').lower()
    return ref_name_ok(name) and low.startswith('refs/') and not low.startswith(NOT_STRAY)


def cleanup_session(event, ctx):
    """True for a main session whose mark (session_mark: None for every subagent, a call a host
    cannot tell apart included) is one of REF_CLEANUP_MARKS, case ignored."""
    return str(session_mark(event, ctx) or '').lower() in REF_CLEANUP_MARKS


def _deref_unsure(place, name):
    """Why `git update-ref -d <name>` without --no-deref may delete another ref (git follows a
    symbolic ref and deletes where it points), or None: a reftable repository, where the guard
    reads no ref file, a loose ref file that does not read, or one that is a symbolic ref."""
    if os.path.isdir(os.path.join(place.common, 'reftable')):
        return 'its refs are in a reftable, where the guard cannot tell a symbolic ref'
    text, why = _read_ref(os.path.join(place.common, *name.split('/')))
    if text is None:
        return why
    if text.startswith('ref:'):
        return 'it is a symbolic ref to %s, and git deletes that ref instead' % text[4:].strip()
    return None


def main_names(ctx, where):
    """The guarded mains of the repository at `where`, lower-cased, main and master always among
    them (a missing or unreadable .kit.json guards those two)."""
    names = {'main', 'master'}
    cfg = ctx.config(where) if where and os.path.isabs(where) else None
    if cfg is not None:
        names.update(str(b).lower() for b in _guarded_mains(cfg) if b)
    return names


def shared_ref_held(event, ctx):
    """True for a session git_shared_ref holds: an agent session (agent_session) but the PM's."""
    return agent_session(event, ctx) and str(session_mark(event, ctx) or '').lower() != 'pm'


def _in_temp(ctx, path):
    """True when `path` lies in a temp root (jail.temp_roots). It lifts checks, so it compares with
    the jail's strict fold (jail.real strict: on Linux a mount in doubt does not fold)."""
    strict = paths.posix()
    real = jail.real(path, strict)
    return any(jail.inside(real, jail.real(r, strict)) for r in jail.temp_roots(ctx))


def _scratch_repo(ctx, place):
    """True when `place` is a scratch repository: its git directories lie in a temp root
    (jail.temp_roots) and no link inside them may carry a ref write out. Every entry but what
    objects/ holds is read, at most SCRATCH_WALK_MAX; any symlink, junction or other reparse
    point, an entry that does not read, or a longer walk answers False, and the rule judges the
    repository as any other. A hard link is not seen: git writes a ref file and packed-refs to a
    lock file it renames over the old one, so a write does not go through a hard link."""
    if place is None or not place.common:
        return False
    roots = [place.common]
    if place.git_dir and not jail.inside(jail.real(place.git_dir), jail.real(place.common)):
        roots.append(place.git_dir)
    if not all(_in_temp(ctx, r) for r in roots):
        return False
    seen, stack = 0, list(roots)
    while stack:
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                entries = list(it)
            for e in entries:
                seen += 1
                if seen > SCRATCH_WALK_MAX or e.is_symlink() or \
                        getattr(e.stat(follow_symlinks=False), 'st_file_attributes', 0) & REPARSE_POINT:
                    return False
                if e.is_dir(follow_symlinks=False) and not (d in roots and e.name.lower() == 'objects'):
                    stack.append(e.path)
        except OSError:
            return False
    return True


def _read_ref(path):
    """(text of the loose ref file, None); ('', None) when there is none; (None, why) when it
    cannot be read."""
    try:
        with open(path, 'rb') as f:
            return f.read(4096).decode('utf-8', 'replace').strip(), None
    except FileNotFoundError:
        return '', None
    except OSError as e:
        return None, 'its file cannot be read (%s)' % e


def _head_ref(place):
    """The ref the HEAD file of `place`'s git directory names, '' when HEAD is detached, None when
    there is no such file or it cannot be read."""
    if not place.git_dir:
        return None
    text, _ = _read_ref(os.path.join(place.git_dir, 'HEAD'))
    if not text:
        return None
    return text[4:].strip() if text.startswith('ref:') else ''


def _own_branch(ctx, place, at):
    """The branch checked out in the linked worktree `place` (refs/heads/<name>), or None: a
    detached or unreadable HEAD, a guarded main, the PM data worktree or one the guard cannot tell."""
    if place.kind != repo.LINKED or not place.git_dir or ctx.is_pm_data(at) or ctx.pm_data_error(at):
        return None
    own = _head_ref(place)
    if not own or not own.startswith('refs/heads/') or not ref_name_ok(own):
        return None
    if own[len('refs/heads/'):].lower() in main_names(ctx, at):
        return None
    return own


def _ref_ok(name, delete, linked, own):
    if scratch_ref(name):
        return True
    if delete or not linked:
        return False
    return name == 'HEAD' or (own is not None and name == own)


def _update_ref_unread(gc, short, longs, pos):
    """Why the guard cannot tell which ref `git update-ref` writes, or None."""
    if gc.inv is not None and gc.inv.via == 'xargs':
        return 'xargs adds words the text does not show'
    if 'stdin' in longs:
        return 'it reads the refs to write from its input'
    unknown = sorted(longs - set(GIT_LONGS['update-ref']))
    if unknown:
        return 'it gives an option the guard does not read (--%s)' % unknown[0]
    odd = sorted(short - UPDATE_REF_SHORTS)
    if odd:
        return 'it gives an option the guard does not read (-%s)' % odd[0]
    if not pos:
        return 'it names no ref'
    for w in gc.rest:
        if w.dyn or w.alt is not None or w.synth:
            return 'a word before the ref holds something only the shell knows (%s)' % w.text
        if w is pos[0]:
            break
    if not ref_name_ok(pos[0].text):
        return '%r is not a ref name the guard reads' % pos[0].text
    return None


def _stray_delete(ctx, gc, cwd, place, name, longs, text, top, role):
    """A coordinator's `git update-ref -d <stray_ref>`: None (allowed) in its own repository (the
    main checkout `top` of the place git runs in is the one of its session's folder `cwd`) when
    git deletes that ref itself (--no-deref, or _deref_unsure finds no other ref it may reach);
    else the refusal."""
    mine = ctx.main_top(cwd) if cwd else None
    at = quote(top or gc.dir)
    if not top or not mine or paths.norm(mine) != paths.norm(top):
        return refuse('git-shared-ref', role, '`%s` deletes %s in a repository other than the one your session '
                      'runs in (%s): a coordinator deletes a stray ref only in its own repository, and that '
                      'repository\'s coordinator deletes this one. To show it:' % (text, name, fwd(mine or cwd or '?')),
                      'git -C %s show-ref %s' % (at, name))
    why = None if 'no-deref' in longs else _deref_unsure(place, name)
    if why is None:
        return None
    return refuse('git-shared-ref', role, '`%s` may delete another ref than %s: %s. Delete the ref itself with '
                  '--no-deref:' % (text, name, why), 'git -C %s update-ref --no-deref -d %s' % (at, name))


SHARED_REFS = 'every worktree of this repository shares its refs, so a change made from any of them is made for all'
SHARED_REF_ALLOWED = 'An agent session writes refs/tmp/<name> (its scratch space), the per-worktree refs ' \
                     '(refs/bisect/, refs/worktree/, refs/rewritten/, <NAME>_HEAD) and, in its own worktree, HEAD ' \
                     'and the branch checked out there; a coordinator deletes a ref outside refs/heads, refs/tags, ' \
                     'refs/stash and refs/backlog in its own repository; any other ref is the PM\'s.'


def _shared_scene(ctx, gc, cwd):
    """(place, top, at, why) of a ref write by git `gc`: the Place git runs in (None when the text
    does not name it), its main checkout, the checkout a refusal's Instead names (the session's own
    worktree of it when it has one), and why the guard cannot judge that place (None when it can)."""
    why = None
    if gc.blind or gc.unknown or not gc.dir:
        why = 'the repository it runs in is unknown (%s)' % (gc.blind or 'a directory the text does not name')
    place = ctx.place(gc.dir) if gc.dir and not gc.unknown and not gc.blind else None
    if not why and place.kind not in (repo.MAIN, repo.LINKED):
        why = 'git cannot place %s (%s)' % (fwd(gc.dir), place.detail or place.kind)
    top = (ctx.main_top(gc.dir) if gc.dir and not gc.unknown else None) or (place.top if place else None)
    at = own_worktree(ctx, cwd, top) if top else own_worktree(ctx, cwd)
    at = at or top or (ctx.main_top(cwd) if cwd else None) or cwd
    return place, top, at, why


def git_shared_ref(event, ctx, cmd, gc, cwd, role):
    """`git update-ref` by a held session (shared_ref_held), from any checkout: every worktree of a
    repository shares its refs, so a worktree reaches main as surely as the main checkout. It may
    write scratch_ref names, and in a linked worktree its HEAD and the branch checked out there
    (never a guarded main); it deletes only scratch_ref names, and a coordinator's main session
    (cleanup_session) a stray_ref in its own repository too (_stray_delete). A ref is judged by its name; HEAD
    (unless --no-deref) by the branch it is on too, so a worktree on a guarded main does not move
    it. A scratch repository (_scratch_repo: in a temp root, no
    link out of its git directory) is not judged. Neither the PM data worktree nor, while the
    guard cannot tell it (ctx.pm_data_error), any linked worktree gets the HEAD allowance."""
    if not shared_ref_held(event, ctx):
        return None
    text = bare(cmd)
    short, longs, pos = _opts('update-ref', gc.rest)
    delete = 'd' in short
    unread = _update_ref_unread(gc, short, longs, pos)
    after = False
    for w in gc.rest:
        if after and (w.dyn or w.alt is not None or w.synth):
            delete = True                       # a word only the shell knows may be -d
        after = after or (bool(pos) and w is pos[0])
    place, top, at, where = _shared_scene(ctx, gc, cwd)
    if _scratch_repo(ctx, place):
        return None
    unread = unread or where
    shared, allowed = SHARED_REFS, SHARED_REF_ALLOWED
    cleanup = cleanup_session(event, ctx)
    if unread:
        hint = ref_delete_hint(ctx, gc, top or at, cwd, short, longs, pos, cleanup) if 'd' in short else None
        if hint:
            return refuse('git-shared-ref', role, '`%s`: %s, and %s. %s' % (text, unread, shared, hint[0]), hint[1])
        return refuse('git-shared-ref', role, '`%s`: the guard cannot tell which ref it writes: %s; %s. Name one '
                      'ref literally. %s To keep a commit in your scratch space:' % (text, unread, shared, allowed),
                      'git -C %s update-ref refs/tmp/<name> <new-oid>' % quote(at))
    name = pos[0].text
    # the PM data worktree answers as MAIN; while the guard cannot tell it, any linked worktree may be it
    linked = place.kind == repo.LINKED and not ctx.pm_data_error(gc.dir)
    own = _own_branch(ctx, place, gc.dir)
    chain, why = [name], None
    if name == 'HEAD' and 'no-deref' not in longs:
        head = _head_ref(place)
        if head is None:
            chain, why = None, 'the HEAD file of %s cannot be read' % fwd(gc.dir)
        elif head:
            chain.append(head)
    if chain is not None:
        bad = [r for r in chain if not _ref_ok(r, delete, linked, own)]
        if not bad:
            return None
        if cleanup and 'd' in short and stray_ref(name):
            return _stray_delete(ctx, gc, cwd, place, name, longs, text, top, role)
        why = '%s is not one it may %s' % (bad[0] if bad[0] == name else '%s, where %s points,' % (bad[0], name),
                                           'delete' if delete else 'write')
    hint = ref_delete_hint(ctx, gc, top or at, cwd, short, longs, pos, cleanup) if 'd' in short else None
    if hint:
        return refuse('git-shared-ref', role, '`%s` deletes %s: %s. %s' % (text, name, why, hint[0]), hint[1])
    return refuse('git-shared-ref', role, '`%s` %s %s: %s; %s. %s To show it:' % (
        text, 'deletes' if delete else 'moves', name, why, shared, allowed), 'git -C %s show-ref %s' % (quote(at), name))


# ---------------------------------------------------------------- other git forms that write refs
# git-shared-ref judges these as it judges update-ref (_judge_refs): a push into the repository
# itself (git_shared_push), a fetch or pull refspec's destination (a forced `tag <name>` too),
# symbolic-ref with two names or -d, replace, a notes write, a forced or moving branch, a forced
# or deleted tag, checkout -B and switch -C, --ignore-other-worktrees, worktree add -B or -f,
# rebase --update-refs, a remote's remove, rename, set-head or mirror add, fast-import and
# filter-branch (git_shared_ref_forms; branch after git_branch_delete, worktree after
# git_worktree_remove). Known limits (docs/guard.md "What git-shared-ref does not read"): refspecs, remotes and
# rebase.updateRefs git takes from its config files, a named remote whose URL is this repository,
# a fetch or pull refspec the shell computes whole, a push into another local repository, rebase
# <upstream> <branch> (it moves that branch), git replay and filter-repo.
SHARED_REF_FORMS = ('fetch', 'pull', 'symbolic-ref', 'replace', 'notes', 'tag', 'checkout', 'switch', 'rebase',
                    'remote', 'fast-import', 'filter-branch')
# per form: (long flags, long options that take a value, short flags, short options that take a
# value, short options whose value may only be attached)
FETCH_FLAGS = ('all', 'append', 'atomic', 'dry-run', 'write-fetch-head', 'force', 'keep', 'multiple',
               'auto-maintenance', 'auto-gc', 'write-commit-graph', 'prefetch', 'prune', 'prune-tags', 'tags',
               'recurse-submodules', 'quiet', 'verbose', 'progress', 'update-head-ok', 'update-shallow',
               'unshallow', 'ipv4', 'ipv6', 'show-forced-updates', 'set-upstream', 'stdin', 'porcelain',
               'negotiate-only')
FETCH_VALUES = ('upload-pack', 'jobs', 'depth', 'deepen', 'shallow-since', 'shallow-exclude', 'refmap',
                'server-option', 'negotiation-tip', 'filter', 'submodule-prefix', 'recurse-submodules-default')
PULL_FLAGS = FETCH_FLAGS + ('commit', 'edit', 'ff', 'ff-only', 'squash', 'stat', 'summary', 'compact-summary',
                            'verify', 'verify-signatures', 'autostash', 'allow-unrelated-histories', 'rebase',
                            'signoff', 'log', 'gpg-sign')
PULL_VALUES = FETCH_VALUES + ('strategy', 'strategy-option', 'cleanup')
BRANCH_FLAGS = ('delete', 'move', 'copy', 'force', 'list', 'all', 'remotes', 'verbose', 'quiet', 'track',
                'unset-upstream', 'color', 'abbrev', 'column', 'omit-empty', 'show-current', 'create-reflog',
                'edit-description', 'ignore-case', 'recurse-submodules')
BRANCH_VALUES = ('set-upstream-to', 'contains', 'no-contains', 'merged', 'no-merged', 'sort', 'points-at', 'format')
TAG_FLAGS = ('list', 'delete', 'verify', 'annotate', 'sign', 'edit', 'force', 'create-reflog', 'color', 'column',
             'omit-empty', 'ignore-case')
TAG_VALUES = ('message', 'file', 'trailer', 'cleanup', 'local-user', 'contains', 'no-contains', 'merged',
              'no-merged', 'sort', 'points-at', 'format')
CHECKOUT_FLAGS = ('quiet', 'progress', 'merge', 'detach', 'force', 'ours', 'theirs', 'patch', 'guess', 'overlay',
                  'overwrite-ignore', 'ignore-other-worktrees', 'ignore-skip-worktree-bits', 'pathspec-file-nul',
                  'recurse-submodules', 'track', 'auto-advance')
CHECKOUT_VALUES = ('conflict', 'orphan', 'unified', 'inter-hunk-context', 'pathspec-from-file')
SWITCH_FLAGS = ('guess', 'discard-changes', 'quiet', 'recurse-submodules', 'progress', 'merge', 'detach', 'force',
                'overwrite-ignore', 'ignore-other-worktrees', 'track', 'auto-advance')
SWITCH_VALUES = ('create', 'force-create', 'conflict', 'orphan')
REBASE_FLAGS = ('keep-base', 'verify', 'quiet', 'verbose', 'stat', 'signoff', 'committer-date-is-author-date',
                'reset-author-date', 'ignore-whitespace', 'force-rebase', 'ff', 'continue', 'skip', 'abort', 'quit',
                'edit-todo', 'show-current-patch', 'apply', 'merge', 'interactive', 'rerere-autoupdate',
                'autosquash', 'update-refs', 'gpg-sign', 'autostash', 'rebase-merges', 'fork-point', 'root',
                'reschedule-failed-exec', 'reapply-cherry-picks', 'keep-empty')
REBASE_VALUES = ('onto', 'trailer', 'whitespace', 'empty', 'exec', 'strategy', 'strategy-option')
WORKTREE_ADD_FLAGS = ('force', 'detach', 'checkout', 'lock', 'orphan', 'quiet', 'track', 'guess-remote',
                      'relative-paths')
REF_FORM_OPTS = {'fetch': (FETCH_FLAGS, FETCH_VALUES, 'afkmnPpqtuv46', 'jo', ''),
                 'pull': (PULL_FLAGS, PULL_VALUES, 'aefknpqrtuv46', 'joXs', 'S'),
                 'symbolic-ref': (('quiet', 'delete', 'short', 'recurse', 'no-recurse'), (), 'qd', 'm', ''),
                 'branch': (BRANCH_FLAGS, BRANCH_VALUES, 'vqrladDmMcCfi', 'u', 't'),
                 'tag': (TAG_FLAGS, TAG_VALUES, 'ldvasefi', 'mFu', 'n'),
                 'checkout': (CHECKOUT_FLAGS, CHECKOUT_VALUES, 'qmdf23plt', 'bBU', ''),
                 'switch': (SWITCH_FLAGS, SWITCH_VALUES, 'qmdft', 'cC', ''),
                 'rebase': (REBASE_FLAGS, REBASE_VALUES, 'qvnfmik', 'xsXC', 'Sr'),
                 'worktree add': (WORKTREE_ADD_FLAGS, ('reason',), 'fdq', 'bB', ''),
                 'remote add': (('fetch', 'tags', 'mirror'), ('track', 'master'), 'f', 'tm', '')}
# git replace that only lists: these options (and --format=<f>), with a pattern only after -l
REPLACE_LIST_OPTS = ('-l', '--list')
NOTES_READ_VERBS = (None, 'list', 'show', 'get-ref')
REMOTE_FETCH_KEY_RE = re.compile(r'^remote\.(.+)\.fetch$')
PRUNE_TAGS_KEY_RE = re.compile(r'^(?:fetch|remote\..+)\.prunetags$')
FALSE_WORDS = ('false', 'no', 'off', '0')


def _long_hit(known, body):
    """(name, negated) of the long option `body` (the text after `--`, before any `=`) among the
    names `known`: its exact name, a `no-` one, or the one name it abbreviates; None when it names
    none or more than one."""
    neg = body.startswith('no-') and body not in known
    stem = body[3:] if neg else body
    hits = [stem] if stem in known else [n for n in known if n.startswith(stem)] if stem else []
    return (hits[0], neg) if len(hits) == 1 else None


def _form_words(sub, args):
    """(short flags, {long name: [(value text, computed)]}, positional Words, unread option texts)
    of git `sub`'s arguments by REF_FORM_OPTS: a long option is its exact name, a `no-` one, or the
    one name it abbreviates; one the table does not name, or an ambiguous prefix, is unread."""
    return _form_scan(sub, args)[:4]


def _form_scan(sub, args):
    """_form_words, and the option names in the order given (a long one by name, a negated one as
    no-<name>, a short flag as -<letter>) and how many positional words come before a `--`."""
    flags, values, s_flags, s_values, s_attached = REF_FORM_OPTS[sub]
    known = set(flags) | set(values)
    shorts, longs, pos, unread, seq = set(), {}, [], [], []
    k = 0
    npre = None
    while k < len(args):
        w = args[k]
        t = w.text
        k += 1
        if t == '--':
            npre = len(pos)
            pos.extend(args[k:])
            break
        if t.startswith('--'):
            body, eq, v = t[2:].partition('=')
            hit = _long_hit(known, body)
            if hit is None:
                unread.append(t)
                continue
            name, neg = hit
            seq.append('no-' + name if neg else name)
            if name in values and not neg:
                if not eq and k < len(args):
                    v, computed = args[k].text, computed_word(args[k])
                    k += 1
                else:
                    computed = computed_word(w)
                longs.setdefault(name, []).append((v, computed))
            else:
                longs.setdefault('no-' + name if neg else name, []).append((v, computed_word(w)))
            continue
        if t.startswith('-') and len(t) > 1:
            body = t[1:]
            for j, c in enumerate(body):
                if c in s_values:
                    if not body[j + 1:] and k < len(args):
                        longs.setdefault('-' + c, []).append((args[k].text, computed_word(args[k])))
                        k += 1
                    else:
                        longs.setdefault('-' + c, []).append((body[j + 1:], computed_word(w)))
                    break
                if c in s_attached:
                    break
                if c in s_flags:
                    shorts.add(c)
                    seq.append('-' + c)
                else:
                    unread.append('-' + c)
            continue
        pos.append(w)
    return shorts, longs, pos, unread, seq, len(pos) if npre is None else npre


def fetch_dst_ref(dst):
    """The local ref a fetch refspec's destination names (git's get_local_ref): a refs/ name as
    it is, heads/, tags/ and remotes/ under refs/, any other name a branch."""
    if dst.startswith('refs/'):
        return dst
    if dst.startswith(('heads/', 'tags/', 'remotes/')):
        return 'refs/' + dst
    return 'refs/heads/' + dst


def _tracking_ref(src, dst, remote):
    """True for the remote-tracking map a fetch makes: `dst` refs/remotes/<remote>/<x> (remote and
    folder compared without case) from `src` <x> or refs/heads/<x>, one `*` in both allowed; <x>
    never HEAD."""
    if not remote or not ref_name_ok(remote):
        return False
    prefix = 'refs/remotes/%s/' % remote
    if dst[:len(prefix)].lower() != prefix.lower():
        return False
    x = dst[len(prefix):]
    if not x or x == 'HEAD' or x.count('*') > 1 or not ref_name_ok(x.replace('*', 'x')):
        return False
    return src in (x, 'refs/heads/' + x)


def fetch_refs(gc, place=None):
    """([(ref, delete, what, instead)], why unread or None): the refs git fetch or pull `gc` writes
    beyond FETCH_HEAD and the remote-tracking map (_tracking_ref): each refspec destination after
    the remote (fetch_dst_ref; a `^` exclusion and an empty destination write nothing), each
    --refmap and inline -c remote.<name>.fetch mapping, local tags under --prune-tags (or an inline
    *.pruneTags), a forced --tags and a forced `tag <name>`. Unread: --stdin, a refspec the shell
    computes with a literal `:` or `+`, a forced `tag` whose name it computes, an option the guard
    does not read on a line with a `:` word."""
    shorts, longs, pos, unknown = _form_words(gc.sub, gc.rest)
    remote = pos[0] if pos else None
    rname = remote.text if remote is not None and not computed_word(remote) else None
    # with --multiple every word names a remote (and --all takes none)
    many = 'm' in shorts or 'multiple' in longs
    specs, tagged = [], []          # tagged: (name text, computed) of each `tag <name>` (refs/tags/<name>)
    words = [] if many else pos[1:]
    j = 0
    while j < len(words):
        w = words[j]
        if w.text == 'tag' and not computed_word(w) and j + 1 < len(words):
            tagged.append((words[j + 1].text, computed_word(words[j + 1])))
            j += 2
            continue
        specs.append((w.text, computed_word(w), rname))
        j += 1
    specs += [(v, c, rname) for v, c in longs.get('refmap', ())]
    if gc.inv is not None:
        for key, val, first, last in git_prefix_configs(gc):
            # --config-env names a variable that holds the value: the text is not the value
            computed = computed_word(last) or first.text.startswith('--config-env')
            m = REMOTE_FETCH_KEY_RE.match(key)
            if m:
                specs.append((val, computed, m.group(1)))
            elif PRUNE_TAGS_KEY_RE.match(key) and (computed or val.strip().lower() not in FALSE_WORDS):
                longs.setdefault('prune-tags', []).append((val, computed))
    if 'stdin' in longs:
        return [], 'it reads refspecs from its input'
    if unknown and (any(':' in w.text for w in pos) or any(':' in t for t in unknown)):
        return [], 'it gives an option the guard does not read (%s)' % unknown[0]
    instead = 'Fetch into a remote-tracking ref (refs/remotes/<remote>/<branch>) or your scratch space:'
    out = []
    for text, computed, rem in specs:
        spec = text.strip('"\'') if computed else text
        if computed and (':' in spec or spec.startswith('+')):
            return [], 'a refspec holds something only the shell knows (%s)' % text
        if computed:
            continue                            # a refspec computed whole: a known limit
        spec = spec[1:] if spec.startswith('+') else spec
        src, colon, dst = spec.partition(':')
        if spec.startswith('^') or not colon or not dst:
            continue
        ref = fetch_dst_ref(dst)
        if _tracking_ref(src, ref, rem):
            continue
        out.append((ref, False, None, (instead, 'git -C <at> fetch %s %s:refs/tmp/<name>' % (rem or '<remote>',
                                                                                             src or 'HEAD'))))
    forced = 'f' in shorts or 'force' in longs
    for name, computed in tagged if forced else ():
        # unforced, git refuses to move a tag that is there; forced, it moves it
        if computed:
            return [], 'a tag name holds something only the shell knows (%s)' % name
        out.append(('refs/tags/' + name, False, 'moves the tag %s to the remote\'s' % name,
                    ('Fetch it into your scratch space:', 'git -C <at> fetch %s %s' % (
                        rname or '<remote>', spelt('refs/tags/%s:refs/tmp/%s' % (name, name))))))
    tags = ('without --prune-tags or a forced --tags (a fetch never moves a tag you have):',
            'git -C <at> fetch %s' % (rname or '<remote>'))
    if 'P' in shorts or 'prune-tags' in longs:
        out.append(('refs/tags/*', True, 'deletes every tag the remote does not have', ('Fetch ' + tags[0], tags[1])))
    if ('t' in shorts or 'tags' in longs) and ('f' in shorts or 'force' in longs):
        out.append(('refs/tags/*', False, 'moves every tag to the remote\'s', ('Fetch ' + tags[0], tags[1])))
    return out, None


def symbolic_ref_refs(gc, place=None):
    """([(ref, delete, what, instead)], why unread or None) of git symbolic-ref `gc`: none for a
    read (one name, no -d); with -d the name, deleted; with two names the name, written (HEAD
    itself: symbolic-ref does not follow it), and the ref it then points at, since later writes go
    through it. A computed word or an option the guard does not read is unread."""
    shorts, longs, pos, unknown = _form_words('symbolic-ref', gc.rest)
    delete = 'd' in shorts or 'delete' in longs
    if any(computed_word(w) for w in gc.rest):
        return [], ('a word holds something only the shell knows (%s)'
                    % next(w.text for w in gc.rest if computed_word(w)))
    if unknown:
        return [], 'it gives an option the guard does not read (%s)' % unknown[0]
    if not pos or (len(pos) < 2 and not delete):
        return [], None
    name = pos[0].text
    switch = ('To put your worktree on another branch:', 'git -C <at> switch <branch>') if name == 'HEAD' else None
    if delete:
        return [(name, True, None, switch)], None
    target = pos[1].text
    return [(name, False, None, switch), (target, False, 'points %s at %s, so later writes through it move it' % (
        name, target), switch)], None


def replace_refs(gc, place=None):
    """([(ref, delete, what, instead)], why unread or None) of git replace `gc`: none when it only
    lists (no word, or REPLACE_LIST_OPTS and --format=<f> with a pattern only after -l); else the
    refs/replace/<object> ref it writes or deletes (-d)."""
    words = [w.text for w in gc.rest]
    if any(computed_word(w) for w in gc.rest):
        return [], ('a word holds something only the shell knows (%s)'
                    % next(w.text for w in gc.rest if computed_word(w)))
    opts = [t for t in words if t.startswith('-')]
    objs = [t for t in words if not t.startswith('-')]
    if all(t in REPLACE_LIST_OPTS or t.startswith('--format=') for t in opts) and \
            (not objs or any(t in REPLACE_LIST_OPTS for t in opts)):
        return [], None
    delete = '-d' in opts or any(len(t) > 2 and '--delete'.startswith(t) for t in opts)
    ref = 'refs/replace/%s' % (objs[0] if objs else '<object>')
    return [(ref, delete, None, ('To list the replace refs:', 'git -C <at> replace -l'))], None


def notes_refs(gc, place=None):
    """([(ref, delete, what, instead)], why unread or None) of git notes `gc`: none for a verb in
    NOTES_READ_VERBS; any other writes the notes ref (--ref <ref> under refs/notes/, else
    refs/notes/commits). A computed verb, or an option before it other than --ref, is unread."""
    args, k, ref = gc.rest, 0, 'refs/notes/commits'
    while k < len(args) and args[k].text.startswith('-'):
        w = args[k]
        body, eq, v = w.text[2:].partition('=')
        if w.text == '--' or not w.text.startswith('--') or not body or not 'ref'.startswith(body):
            return [], 'it gives an option the guard does not read (%s)' % w.text
        if not eq:
            k += 1
            v = args[k].text if k < len(args) else ''
        ref = v if v.startswith('refs/notes/') else 'refs/' + v if v.startswith('notes/') else 'refs/notes/' + v
        k += 1
    verb = args[k] if k < len(args) else None
    if verb is not None and computed_word(verb):
        return [], 'its verb holds something only the shell knows (%s)' % verb.text
    if (verb.text if verb is not None else None) in NOTES_READ_VERBS:
        return [], None
    return [(ref, verb.text == 'remove', None, ('To show the notes:', 'git -C <at> notes list'))], None


def _first_computed(words):
    """'a word holds something only the shell knows (<text>)' for the first computed Word of
    `words`, or None."""
    for w in words:
        if computed_word(w):
            return 'a word holds something only the shell knows (%s)' % w.text
    return None


def _last_of(seq, names):
    """The last of `names` in `seq` (_form_scan's option names in order), or None."""
    for n in reversed(seq):
        if n in names:
            return n
    return None


def branch_refs(gc, place=None):
    """([(ref, delete, what, instead)], why unread or None) of git branch `gc`: a move (-m, -M,
    --move) deletes the old name (with one name, the branch HEAD names where git runs: none when it
    is detached, unread when the guard cannot read it) and writes the new; a forced copy (-C, or
    -c with -f) writes the new name; -f with a name writes it. A
    plain create, an unforced copy, -d and -D (git_branch_delete judges -D), a listing and the
    other modes write no ref here. A computed word, or an option the guard does not read, in a
    form that writes is unread."""
    shorts, longs, pos, unknown = _form_words('branch', gc.rest)
    move = bool(shorts & set('mM')) or 'move' in longs
    copy = bool(shorts & set('cC')) or 'copy' in longs
    force = bool(shorts & set('fMC')) or 'force' in longs
    if not pos or not (move or force):
        return [], None
    others = {'delete', 'list', 'edit-description', 'show-current', 'set-upstream-to', 'unset-upstream',
              'contains', 'no-contains', 'merged', 'no-merged', 'points-at'}
    if shorts & set('dDlu') or others & set(longs) or '-u' in longs:
        return [], None
    why = _first_computed(gc.rest)
    if why or unknown:
        return [], why or 'it gives an option the guard does not read (%s)' % unknown[0]
    names = [w.text for w in pos]
    if move or copy:
        old, new = (names[0], names[1]) if len(names) > 1 else (None, names[0])
        new_ref = 'refs/heads/' + new
        if move:
            if old is None:
                # one name: git renames the branch checked out where it runs (HEAD's), and a
                # detached HEAD has none to rename
                cur = _head_ref(place) if place is not None else None
                if cur == '':
                    return [], None
                if not cur or not cur.startswith('refs/heads/') or not ref_name_ok(cur):
                    return [], 'it renames the branch checked out where git runs, and the guard cannot read its HEAD'
                old = cur[len('refs/heads/'):]
            make = ('To make a branch of the new name and leave %s as it is:' % old,
                    'git -C <at> branch %s %s' % (spelt(new), spelt(old)))
            return [('refs/heads/' + old, True, 'renames refs/heads/%s to %s, deleting the old name' % (old, new_ref),
                     make),
                    (new_ref, False, None, make)], None
        copy_to = ('To copy it to a new name:', 'git -C <at> branch -c %s<new-name>' % (spelt(old) + ' ' if old else ''))
        return [(new_ref, False, 'writes over %s with a copy' % new_ref, copy_to)], None
    start = ' ' + spelt(names[1]) if len(names) > 1 else ''
    return [('refs/heads/' + names[0], False, 'moves refs/heads/%s' % names[0],
             ('To make a new branch there:', 'git -C <at> branch <new-name>%s' % start))], None


def tag_refs(gc, place=None):
    """([(ref, delete, what, instead)], why unread or None) of git tag `gc`: -d deletes each tag
    named; -f with a name writes it. A plain create, a listing and -v write no ref here."""
    shorts, longs, pos, unknown = _form_words('tag', gc.rest)
    delete = 'd' in shorts or 'delete' in longs
    force = 'f' in shorts or 'force' in longs
    if not pos or not (delete or force) or 'l' in shorts or 'list' in longs or 'v' in shorts or 'verify' in longs:
        return [], None
    why = _first_computed(gc.rest)
    if why or unknown:
        return [], why or 'it gives an option the guard does not read (%s)' % unknown[0]
    if delete:
        return [('refs/tags/' + w.text, True, None, None) for w in pos], None
    start = ' ' + spelt(pos[1].text) if len(pos) > 1 else ''
    return [('refs/tags/' + pos[0].text, False, 'moves refs/tags/%s' % pos[0].text,
             ('To make a new tag there:', 'git -C <at> tag <new-name>%s' % start))], None


def _branch_seen(place, name):
    """True when the repository of `place` (the Place git runs in) has branch `name`: a loose
    refs/heads/<name>, a packed-refs line, or a worktree's HEAD naming it; False when it has none
    of them; None when the guard cannot tell (no place or common dir, or a reftable store)."""
    common = place.common if place is not None else None
    if not common or os.path.isdir(os.path.join(common, 'reftable')):
        return None
    ref = 'refs/heads/' + name
    if os.path.isfile(os.path.join(common, *ref.split('/'))) or ref in (gitdefaults._packed(common) or ()):
        return True
    heads = [os.path.join(common, 'HEAD')]
    try:
        heads += [os.path.join(common, 'worktrees', n, 'HEAD') for n in os.listdir(os.path.join(common, 'worktrees'))]
    except OSError:
        pass
    return any((gitdefaults._read(h, 4096) or '').strip() == 'ref: ' + ref for h in heads)


def _checked_out(word, place):
    """([(ref, ...)], why) for the branch `word` that --ignore-other-worktrees (or worktree add
    -f) checks out though another worktree has it, git running in `place`: refs/heads/<name> when the
    repository has that branch (_branch_seen; one the guard cannot tell counts); none for a word
    that names no branch there (a tag, a commit, a new branch git makes from a remote's), HEAD or a
    commit expression; `-` and @{...} name a branch the text does not show."""
    t = word.text
    if computed_word(word):
        return None, 'a word holds something only the shell knows (%s)' % t
    detach = ('To check it out detached, so no branch moves with your commits:', 'git -C <at> switch --detach %s'
              % spelt(t))
    if t == '-' or t.startswith('@{'):
        return ('the branch %s names' % t, detach), None
    if t == 'HEAD' or not ref_name_ok(t) or _branch_seen(place, t) is False:
        return None, None
    return ('refs/heads/' + t, detach), None


def checkout_refs(gc, place=None):
    """([(ref, delete, what, instead)], why unread or None) of git checkout or switch `gc`: -B
    (checkout) or -C/--force-create (switch) writes the branch it names; the last of
    --ignore-other-worktrees and its no- form, with no branch option or --detach, checks out the
    branch named (_checked_out) though another worktree has it, so commits here move it."""
    shorts, longs, pos, unknown, seq, npre = _form_scan(gc.sub, gc.rest)
    if gc.sub == 'checkout':
        forced = longs.get('-B') or []
    else:
        forced = (longs.get('-C') or []) + (longs.get('force-create') or [])
    start = ' ' + spelt(pos[0].text) if pos and not computed_word(pos[0]) else ''
    if forced:
        v, computed = forced[-1]
        if computed:
            return [], 'a branch name holds something only the shell knows (%s)' % v
        return [('refs/heads/' + v, False, 'resets refs/heads/%s' % v,
                 ('To make a new branch and switch to it:', 'git -C <at> switch -c <new-name>%s' % start))], None
    if _last_of(seq, ('ignore-other-worktrees', 'no-ignore-other-worktrees')) != 'ignore-other-worktrees':
        return [], None
    if set(longs) & {'-b', '-c', 'create', 'orphan', 'detach'} or 'd' in shorts or not npre:
        return [], None
    hit, why = _checked_out(pos[0], place)
    if why or not hit:
        return [], why
    return [(hit[0], False, 'checks out %s though another worktree may have it, so commits here move it' % hit[0],
             hit[1])], None


def worktree_add_refs(gc, place=None):
    """([(ref, delete, what, instead)], why unread or None) of git worktree add `gc`: -B writes
    the branch it names; -f with no -b, -B, --detach or --orphan checks out the branch named (the
    commit-ish, else the path's last part) though another worktree may have it. Any other worktree
    verb, and a plain add (-b <new>, or an existing branch git refuses when it is out elsewhere),
    write no ref here."""
    args = gc.rest
    if not args or args[0].text != 'add' or computed_word(args[0]):
        return [], None
    shorts, longs, pos, unknown, seq, npre = _form_scan('worktree add', args[1:])
    force = 'f' in shorts or 'force' in longs
    path = spelt(pos[0].text) if pos and not computed_word(pos[0]) else '<path>'
    if '-B' in longs:
        v, computed = longs['-B'][-1]
        if computed:
            return [], 'a branch name holds something only the shell knows (%s)' % v
        start = ' ' + spelt(pos[1].text) if len(pos) > 1 and not computed_word(pos[1]) else ''
        return [('refs/heads/' + v, False, 'resets refs/heads/%s' % v,
                 ('To make a new branch for it:', 'git -C <at> worktree add -b <new-name> %s%s' % (path, start)))], None
    if not force or not pos or '-b' in longs or 'd' in shorts or set(longs) & {'detach', 'orphan'}:
        return [], None
    if unknown:
        return [], 'it gives an option the guard does not read (%s)' % unknown[0]
    if len(pos) > 1:
        hit, why = _checked_out(pos[1], place)
    elif computed_word(pos[0]):
        hit, why = None, 'a word holds something only the shell knows (%s)' % pos[0].text
    else:
        base = re.split(r'[\\/]', pos[0].text.rstrip('\\/'))[-1]
        named = ref_name_ok(base) and base != 'HEAD' and _branch_seen(place, base) is not False
        hit, why = (('refs/heads/' + base, None), None) if named else (None, None)
    if why or not hit:
        return [], why
    commit = spelt(pos[1].text) if len(pos) > 1 else '<commit-ish>'
    return [(hit[0], False, 'checks out %s though another worktree may have it, so commits there move it' % hit[0],
             ('To check the commit out detached in the new worktree:',
              'git -C <at> worktree add --detach %s %s' % (path, commit)))], None


def _inline_on(gc, name):
    """(on, why unread or None) of the last inline -c `name` (a boolean setting) before git's
    subcommand: on unless its value is false (a bare key is true); None when the line sets none.
    A value the shell computes, or one --config-env names, is unread."""
    on = None
    if gc.inv is not None:
        for key, val, first, final in git_prefix_configs(gc):
            if key == name.lower():
                if computed_word(final) or first.text.startswith('--config-env'):
                    return None, 'its -c %s holds something only the shell knows' % name
                on = val.strip().lower() not in FALSE_WORDS
    return on, None


def rebase_refs(gc, place=None):
    """([(ref, delete, what, instead)], why unread or None) of git rebase `gc`: with
    --update-refs (the last of it and its no- form, else an inline -c rebase.updateRefs not
    false) it moves every branch that points into the commits it rebases."""
    shorts, longs, pos, unknown, seq, npre = _form_scan('rebase', gc.rest)
    last = _last_of(seq, ('update-refs', 'no-update-refs'))
    on = last == 'update-refs'
    if last is None:
        on, why = _inline_on(gc, 'rebase.updateRefs')
        if why:
            return [], why
    if not on:
        return [], None
    keep = []
    for w in gc.rest:
        body = w.text[2:].partition('=')[0] if w.text.startswith('--') else None
        hit = _long_hit(set(REBASE_FLAGS) | set(REBASE_VALUES), body) if body else None
        if not (hit and hit[0] == 'update-refs'):
            keep.append(spelt(w.text))
    return [('refs/heads/*', False, 'moves every branch that points into the commits it rebases',
             ('Rebase without moving other branches:', ('git -C <at> rebase --no-update-refs %s' % ' '.join(keep)).rstrip()))], None


def pull_refs(gc, place=None):
    """([(ref, delete, what, instead)], why unread or None) of git pull `gc`: what its fetch
    writes (fetch_refs), and, when an inline -c rebase.updateRefs is on and the pull may rebase,
    every branch that points into the commits it rebases. It may rebase unless the line says it
    merges: the last of --rebase[=<v>], -r and --no-rebase, else an inline -c pull.rebase (the
    guard does not read the repository's config). git pull takes no --update-refs of its own."""
    refs, why = fetch_refs(gc, place)
    on, cwhy = _inline_on(gc, 'rebase.updateRefs')
    if not on or why:
        return refs, why or cwhy
    shorts, longs, pos, unknown, seq, npre = _form_scan('pull', gc.rest)
    last = _last_of(seq, ('rebase', 'no-rebase', '-r'))
    if last == 'no-rebase':
        return refs, None
    if last == 'rebase':
        v, computed = longs['rebase'][-1]
        if computed:
            return refs, 'its --rebase holds something only the shell knows'
        if v.strip().lower() in FALSE_WORDS:
            return refs, None
    elif last is None:
        rebases, cwhy = _inline_on(gc, 'pull.rebase')
        if cwhy:
            return refs, cwhy
        if rebases is False:
            return refs, None
    rest = ' '.join(spelt(w.text) for w in gc.rest)
    return refs + [('refs/heads/*', False, 'may rebase with rebase.updateRefs on, which moves every branch that '
                    'points into the commits it rebases',
                    ('Pull without moving other branches:',
                     ('git -C <at> -c rebase.updateRefs=false pull %s' % rest).rstrip()))], None


def remote_refs(gc, place=None):
    """([(ref, delete, what, instead)], why unread or None) of git remote `gc`: remove (rm) and
    rename delete the remote's remote-tracking refs, set-head writes or deletes <remote>/HEAD, and
    add --mirror (=fetch) makes later fetches write every ref. Its other verbs (prune and update
    as fetch --prune, show, get-url and the like) write no ref here."""
    args, k = gc.rest, 0
    while k < len(args) and args[k].text.startswith('-'):
        t = args[k].text
        if t == '-h':
            return [], None
        if computed_word(args[k]) or not (t == '-v' or (len(t) > 2 and '--verbose'.startswith(t))):
            return [], 'it gives an option the guard does not read (%s)' % t
        k += 1
    if k >= len(args):
        return [], None
    verb, rest = args[k], args[k + 1:]
    if computed_word(verb):
        return [], 'its verb holds something only the shell knows (%s)' % verb.text
    names = [w for w in rest if not w.text.startswith('-')]
    listing = ('To list the remotes:', 'git -C <at> remote -v')
    if verb.text in ('remove', 'rm', 'rename', 'set-head'):
        why = _first_computed(rest)
        if why:
            return [], why
        if not names:
            return [], None
        n = names[0].text
        if verb.text == 'set-head':
            delete = any(w.text in ('-d', '--delete') for w in rest)
            return [('refs/remotes/%s/HEAD' % n, delete, None,
                     ('To see which branch the remote\'s HEAD names:', 'git -C <at> remote show %s' % spelt(n)))], None
        if verb.text == 'rename':
            new = names[1].text if len(names) > 1 else '<new-name>'
            return [('refs/remotes/%s/*' % n, True, 'renames every remote-tracking ref of %s to %s' % (n, new),
                     ('To add the same URL under the new name and leave %s as it is:' % n,
                      'git -C <at> remote add %s <url>' % spelt(new)))], None
        return [('refs/remotes/%s/*' % n, True, 'deletes every remote-tracking ref of %s' % n, listing)], None
    if verb.text != 'add':
        return [], None
    shorts, longs, pos, unknown, seq, npre = _form_scan('remote add', rest)
    if _last_of(seq, ('mirror', 'no-mirror')) != 'mirror':
        return [], None
    v, computed = longs['mirror'][-1]
    if computed:
        return [], 'its --mirror holds something only the shell knows'
    if v not in ('', 'fetch'):
        return [], None
    plain = ' '.join(spelt(w.text) for w in pos[:2]) or '<name> <url>'
    return [('refs/*', False, 'sets the remote up as a mirror, so a fetch from it writes over every ref',
             ('To add it with its own remote-tracking refs (refs/remotes/<name>/):', 'git -C <at> remote add %s' % plain))], None


HISTORY_TOOLS = {'fast-import': 'writes the refs its input names',
                 'filter-branch': 'rewrites the branches it names and keeps their old tips under refs/original/'}


def history_tool_refs(gc, place=None):
    """([(ref, delete, what, instead)], None) of git fast-import or filter-branch `gc`: every ref
    (refs/*), unless it only asks for help (-h)."""
    if [w.text for w in gc.rest] in (['-h'], ['--help']):
        return [], None
    return [('refs/*', False, HISTORY_TOOLS[gc.sub],
             ('Run it in a clone under the temp folder, where the guard does not judge refs:',
              'git clone <at> "<temp>/<name>"'))], None


REF_FORM_READERS = {'fetch': fetch_refs, 'pull': pull_refs, 'symbolic-ref': symbolic_ref_refs,
                    'replace': replace_refs, 'notes': notes_refs, 'branch': branch_refs, 'tag': tag_refs,
                    'checkout': checkout_refs, 'switch': checkout_refs, 'rebase': rebase_refs,
                    'remote': remote_refs, 'worktree': worktree_add_refs, 'fast-import': history_tool_refs,
                    'filter-branch': history_tool_refs}


INJECT_SUBS = ('fetch', 'pull')                 # and `remote update|prune` (_injected_config)
INJECT_REMOTE_VERBS = ('update', 'prune')
INJECT_KEY_PREFIXES = ('include.', 'includeif.', 'url.', 'remote.')


def _injected_config(gc):
    """Why git fetch, pull or `remote update|prune` `gc` runs on config the guard does not read
    (a text), or None: a line that moves the home folder (another user config), or a -c setting
    of include.*, includeIf.*, url.* or remote.* other than the remote.<name>.fetch and
    *.pruneTags that fetch_refs reads. Each can change where the fetch reads from or which refs
    it writes; per git call, so a fetch --all is held as one that names its remote. What
    re-points git from the environment or a variable (gc.blind: GIT_DIR, GIT_CONFIG_COUNT and
    the like, --config-env) is not judged here: every session this rule holds is an agent
    session, and the write jail refuses that git for each of them (git_jail_unknown)."""
    if gc.sub == 'remote':
        verb = next((w for w in gc.rest if not w.text.startswith('-')), None)
        if verb is None or verb.text not in INJECT_REMOTE_VERBS:
            return None
    elif gc.sub not in INJECT_SUBS:
        return None
    if gc.blind or gc.inv is None:
        return None                             # gc.blind: the write jail's (git_jail_unknown)
    home = getattr(gc.inv.cmd, 'home_env', False)
    if home:
        return 'the line sets %s, so git reads another user config' % home
    for key, _val, _first, _last in git_prefix_configs(gc):
        if REMOTE_FETCH_KEY_RE.match(key) or PRUNE_TAGS_KEY_RE.match(key):
            continue                            # fetch_refs reads it
        if key.startswith(INJECT_KEY_PREFIXES):
            return 'its -c %s changes where it fetches from or what config git reads' % key
    return None


def _judge_refs(event, ctx, cmd, gc, cwd, role, refs, unread):
    """git-shared-ref for a git form other than update-ref: `refs` [(ref, delete, what, instead)]
    the refs it writes or deletes (`what` a phrase for the refusal, None for "writes/deletes
    <ref>"; `instead` (sentence, command with <at> and maybe <temp>, the temp folder), None for show-ref), `unread` why the guard
    cannot tell them (None when it can). As git_shared_ref: only a held session
    (shared_ref_held), not in a scratch repository (_scratch_repo), each ref by _ref_ok; no form
    here writes through HEAD. A fetch, pull or `remote update|prune` on config the guard does not
    read (_injected_config) is refused whatever refs it names."""
    injected = _injected_config(gc)
    if (not refs and not unread and not injected) or not shared_ref_held(event, ctx):
        return None
    place, _top, at, where = _shared_scene(ctx, gc, cwd)
    if _scratch_repo(ctx, place):
        return None
    text = bare(cmd)
    if injected:
        temp = jail.temp_roots(ctx)
        return refuse('git-shared-ref', role, '`%s`: %s, so the guard cannot tell which refs it writes; %s. '
                      'Fetch with the repository\'s own config, or in a clone under the temp folder, where the '
                      'guard does not judge refs:' % (text, injected, SHARED_REFS),
                      'git clone %s "%s/<name>"' % (quote(at), fwd(temp[0]) if temp else '<temp folder>'))
    unread = unread or where
    if unread:
        return refuse('git-shared-ref', role, '`%s`: the guard cannot tell which refs it writes: %s; %s. Name each '
                      'ref literally. %s To keep a commit in your scratch space:' % (
                          text, unread, SHARED_REFS, SHARED_REF_ALLOWED),
                      'git -C %s update-ref refs/tmp/<name> <new-oid>' % quote(at))
    linked = place.kind == repo.LINKED and not ctx.pm_data_error(gc.dir)
    own = _own_branch(ctx, place, gc.dir)
    for name, delete, what, instead in refs:
        if _ref_ok(name, delete, linked, own):
            continue
        what = what or '%s %s' % ('deletes' if delete else 'writes', name)
        how, command = instead or ('To show it:', 'git -C <at> show-ref %s' % name)
        temp = jail.temp_roots(ctx)
        return refuse('git-shared-ref', role, '`%s` %s: %s is not one it may %s; %s. %s %s' % (
            text, what, name, 'delete' if delete else 'write', SHARED_REFS, SHARED_REF_ALLOWED, how),
            command.replace('<at>', quote(at)).replace('<temp>', fwd(temp[0]) if temp else '<temp folder>'))
    return None


def git_shared_ref_forms(event, ctx, cmd, gc, cwd, role):
    """git-shared-ref for the forms REF_FORM_READERS reads (SHARED_REF_FORMS, branch and worktree),
    each given the Place git runs in (None when the text does not name it)."""
    if not shared_ref_held(event, ctx):
        return None
    place = ctx.place(gc.dir) if gc.dir and not gc.unknown and not gc.blind else None
    refs, unread = REF_FORM_READERS[gc.sub](gc, place)
    return _judge_refs(event, ctx, cmd, gc, cwd, role, refs, unread)


def _repo_common(path):
    """The git common dir of the repository git opens at `path` (a work tree, its .git, a git dir,
    or <path>.git, as git's enter_repo tries them), or None."""
    for p in (path, path + '.git'):
        common = gitdefaults.common_dir(p)
        if common:
            return common
        if os.path.isfile(os.path.join(p, 'HEAD')):
            named = gitdefaults._read(os.path.join(p, 'commondir'), 4096)
            return paths.norm(os.path.join(p, named.strip()), fold=False) if named and named.strip() else p
    return None


def _push_self(gc, place, t, computed, home=None):
    """True when git push's repository `word` is the repository git runs in: `.`, or a local path
    or file:// URL (resolved against where git runs and its work tree's top) that opens this
    repository's common dir; False for a URL, scp-like host:path, or a name that opens no such
    repository; None when the guard cannot tell (a computed word, or a path with no place to
    compare). A leading ~ is the home folder `home`, as git reads it. A named remote is taken as
    a remote, whatever its URL (a known limit)."""
    if computed:
        return None
    if t == '.':
        return True
    if t == '~' or t.startswith(('~/', '~\\')):
        if not home:
            return None
        t = os.path.join(home, t[2:]) if len(t) > 2 else home
    elif t.startswith('~'):
        return None                             # ~user: another user's home
    if t.lower().startswith('file://'):
        t = t[len('file://'):]
        t = t[1:] if re.match(r'^/[A-Za-z]:', t) else t
    elif '://' in t or (not re.match(r'^[A-Za-z]:', t) and re.match(r'^[^/\\]*:', t)):
        return False
    absolute = os.path.isabs(t) or bool(re.match(r'^[A-Za-z]:[\\/]', t))
    if place is None or not place.common:
        return None if absolute or '/' in t or '\\' in t or t.startswith('.') else False
    bases = [t] if absolute else [os.path.join(b, t) for b in (gc.dir, place.top) if b]
    mine = paths.norm(jail.real(place.common))
    for p in bases:
        common = _repo_common(paths.norm(p, fold=False))
        if common and paths.norm(jail.real(common)) == mine:
            return True
    return False


def _long_value(sub, args, name):
    """(value text, computed) of git `sub`'s long option `name` (or an abbreviation of it), the
    last one given, or None."""
    out, k = None, 0
    while k < len(args):
        t = args[k].text
        if t == '--':
            break
        if t.startswith('--'):
            body, eq, v = t[2:].partition('=')
            if _canon_long(sub, body) == [name]:
                if eq:
                    out = (v, computed_word(args[k]))
                elif k + 1 < len(args):
                    out = (args[k + 1].text, computed_word(args[k + 1]))
                    k += 1
        k += 1
    return out


def git_shared_push(event, ctx, cmd, gc, cwd, role):
    """git-shared-ref for a push into the repository itself (_push_self): it writes this
    repository's refs, so each refspec destination is judged as update-ref's ref. A destination
    must be a full refs/ name (push matches a short one against the refs there); `:dst` and -d
    delete; a refspec with no `:` is a no-op. --all, --mirror, --branches, --tags, --prune, no
    refspec, a computed refspec, or a repository the guard cannot tell with a refspec that writes,
    are unread."""
    if not shared_ref_held(event, ctx):
        return None
    short, longs, pos = _opts('push', gc.rest)
    remote = (pos[0].text, computed_word(pos[0])) if pos else _long_value('push', gc.rest, 'repo')
    if remote is None:
        return None
    specs = pos[1:]
    delete = 'd' in short or 'delete' in longs
    every = sorted({'all', 'mirror', 'branches', 'tags', 'prune'} & longs)
    place = ctx.place(gc.dir) if gc.dir and not gc.unknown and not gc.blind else None
    self_ = _push_self(gc, place, *remote, home=getattr(ctx, 'home', None))
    if self_ is False:
        return None
    writes = delete or every or any(':' in w.text or computed_word(w) for w in specs)
    if self_ is None:
        why = 'the guard cannot tell whether %s is this repository' % remote[0]
        return _judge_refs(event, ctx, cmd, gc, cwd, role, [], why) if writes else None
    refs, unread = [], None
    if every:
        unread = 'with --%s it pushes refs it does not name into this repository' % every[0]
    elif not specs:
        unread = 'it names no refspec, so git takes them from its config'
    for w in specs:
        if unread:
            break
        if computed_word(w):
            unread = 'a refspec holds something only the shell knows (%s)' % w.text
            break
        spec = w.text[1:] if w.text.startswith('+') else w.text
        src, colon, dst = spec.partition(':')
        if spec.startswith('^') or (not colon and not delete):
            continue                            # an exclusion, or a ref pushed onto itself
        dst = dst if colon else src
        if not dst or not dst.startswith('refs/'):
            unread = '%r names no full ref (refs/...); push into this repository names it in full' % spec
            break
        refs.append((dst, delete or not src, None, None))
    return _judge_refs(event, ctx, cmd, gc, cwd, role, refs, unread)


def git_branch_delete(ctx, gc, role):
    short, longs, pos = _opts('branch', gc.rest, wide=True)
    force = 'D' in short or (('delete' in longs or 'd' in short) and ('force' in longs or 'f' in short))
    if not force:
        return None
    names = ' '.join(w.text for w in pos) or '<branch>'
    from . import backlogrole
    store = backlogrole.store_kind(ctx, gc.dir) if gc.dir and not gc.unknown else None
    held = [w.text for w in pos if not w.dyn and '*' not in w.text and
            backlogrole.is_store_ref(w.text, store, short=True)]
    if held:
        # -d of it is a raw write of the store's ref (backlog-raw-write): no delete form passes
        return refuse('git-branch-force-delete', role, '`git branch %s` deletes a branch whether or not it is '
                      'merged, and %s is the backlog store\'s branch, which only tools/backlog.py writes; no '
                      'session deletes it. To see it:' % (' '.join(w.text for w in gc.rest), held[0]),
                      'git log --oneline -1 %s' % held[0])
    return refuse('git-branch-force-delete', role, '`git branch %s` deletes a branch whether or not it is merged. '
                  'Delete it with -d, which refuses an unmerged branch; unmerged work goes to the PM, never '
                  'deleted:' % ' '.join(w.text for w in gc.rest), 'git branch -d %s' % names)


def git_worktree_remove(ctx, gc, role, text):
    args = gc.rest
    if not args or args[0].text != 'remove':
        return None
    if ctx.is_pm_data(gc.dir):
        ctx, why = kit_judged(ctx, gc.dir)
        if ctx is None:
            return refuse('git-worktree-remove', role, '`%s` from the PM data worktree, where the kit\'s rules for the '
                          'shared repository cannot be read: %s. To see the worktrees:' % (text, why),
                          'git -C %s worktree list' % quote(gc.dir))
    pos = _pos(args[1:])
    if not pos or pos[0].dyn or gc.dir is None:
        target = None
    else:
        target = sh.resolve_path(pos[0].text, gc.dir, home=getattr(ctx, 'home', None))
    main_top =(ctx.main_top(gc.dir) if gc.dir else None) or gc.dir
    if target is None:
        # a worktree the guard cannot place (warn by default); git still refuses a dirty one without --force
        return refuse(UNPLACED, role, '`%s`: the guard cannot tell which worktree it removes. Name it '
                      'by a literal path:' % text,
                      'git -C %s worktree remove "<worktree path>"' % (quote(main_top) if main_top else '"<main checkout>"'))
    if _in_temp(ctx, target):
        return None                             # a scratch worktree under a temp root
    cfg = ctx.config(gc.dir)
    main = cfg.main_branch if cfg is not None else 'main'
    clean, why = ctx.git.is_clean(target)
    if clean is not True:
        return refuse('git-worktree-remove', role, '`%s` removes a worktree that is not clean (%s). Commit or drop '
                      'what status lists, then remove it with git -C %s worktree remove %s. To see it:' %
                      (text, why or 'uncommitted or untracked files', quote(main_top), quote(target)),
                      'git -C %s status' % quote(target))
    head, why = ctx.git.resolve('HEAD', target)
    merged, why2 = ctx.git.is_ancestor(head, main, gc.dir) if head else (None, why)
    if merged is not True:
        word, mark_why = stale_mark_word(ctx, target, head) if merged is False and head else (False, None)
        if word:
            return None                         # a stale mark that holds: the PM's word
        how = ('Merge it first; unmerged work goes to the PM, who decides (python %s -C %s %s --abandon "<why>" '
               'only on the PM\'s word).' % (kit_tool(ctx, 'worktree_remove'),
                                             quote(main_top) if main_top else '"<main checkout>"',
                                             os.path.basename(target)))
        if mark_why:
            how += ' A stale mark does not count: %s.' % mark_why
        return refuse('git-worktree-remove', role, '`%s` removes a worktree whose commit is not merged into %s (%s). '
                      '%s Its unmerged commits:' % (text, main, why2 or 'not an ancestor', how),
                      'git -C %s log --oneline %s..HEAD' % (quote(target), main))
    return None


def stale_mark_word(ctx, target, head):
    """`(True, None)` when the clean worktree at `target`, HEAD `head`, carries a stale mark that
    holds (kitlib.worktree.marks: a mark recorded by `worktree_remove.py --mark-stale`, read from
    `<git common dir>/kit-stale-marks.json`): the PM's word to remove it unmerged. It holds on the
    nightly sweep's void checks (sweep.sweep_skip): the mark names this path and this tip, and
    nothing in the tree, its git dir (but index, index.lock and the folder's own time), its
    branch's reflog or the tip's commit date is newer than the mark. The sweep's other checks are
    not made here: the idle bar, `git worktree lock`, exemption, worktree-folder placement, a branch
    checked out elsewhere, holders, open claims, machine lock and landing hold. marks.mark makes
    them all when it marks (with classify.live_reason) but the DELETE-access holder probe
    (holders.blockers): of holders it checks only processes' current folders, through the L
    rule. So what is open is that probe and a change after the mark: the mark is the decision that
    the tree is idle and later activity voids it; a later `git worktree lock` writes `locked` into
    the walked git dir and voids it while it is held (no `--force` is read); a mark naming another
    path is void. Known limits: a file held open in the
    tree other than as a current folder (neither the mark nor this runs holders.blockers), and a
    later exemption, second checkout, holder (no process probe runs here), claim, machine-lock
    run or landing that writes nothing there, are not seen (docs/guard.md, the
    git-worktree-remove row). Else `(False, why)`: why is None when the tree's branch has no
    mark (detached included), else why no mark counts. Fails closed: a marks file, branch, git
    common dir or last activity that cannot be read is no word.
    The tree walk (sweep.last_activity) runs only here, on an unmerged remove that found a mark."""
    from kitlib.worktree import marks, sweep  # the walk and its git reads: only on this path
    place = ctx.place(target)
    if not place.common:
        return False, 'git cannot place its repository (%s)' % (place.detail or 'no git common dir')
    branch, why = ctx.git.current_branch(target)
    if branch is None:
        return False, 'its branch cannot be read (%s)' % why
    if not branch:
        return False, None                    # a detached HEAD: a mark names a branch
    got, why = marks.load(os.path.join(place.common, marks.FILE_NAME))
    if got is None:
        return False, 'the stale marks cannot be read (%s)' % why
    mark = marks.find(got, branch)
    if mark is None:
        return False, None
    void = marks.void_reason(mark, target, head)
    if void is None:
        seen, why = sweep.last_activity(ctx.main_top(target) or target, target, branch)
        if seen is None:
            return False, 'its stale mark cannot be checked: its last activity cannot be read (%s)' % why
        void = marks.void_reason(mark, target, head, seen)
    if void:
        return False, 'its stale mark is void: %s' % void
    return True, None
