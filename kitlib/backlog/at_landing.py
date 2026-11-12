"""The backlog store at a landing (docs/backlog-store.md "Lifecycle changes", "Merge safety";
called by the landing, tools/land.py). The configured store takes part when it is a `ref` or
`branch` store; a main store's records travel with the branch, so a landing leaves them alone.
The PM data branch's store (kitlib.config.pmdata: a folder store in the worktree that has the
recorded data branch checked out) takes part too: the PM's records of this repository live there.

  ancestry_refusal  before any merge: why the branch may not land when its history holds the
                    store's root commit (a store never merges into main), or the PM data
                    branch's recorded root (pmdata_refusal; docs/pm-data-branch.md), else None
  close_landed      after the push is verified: close every record the landed branch still holds
                    pulled, in the configured store (one commit each, "closes <ID>: landed <merge
                    sha>") and in the PM data store (the record file deleted in that worktree and
                    the deletion committed on the PM data branch); and every record a `Closes:
                    <ID>[, <ID>]` line in the messages of the branch's commits names
                    (closes_trailers), while pulled, whoever pulled it (a holder that is a live
                    branch other than main and outside the batch is a WARNING: tell its builder).
                    A branch with a `Part-of: <ID>` line (part_trailers) lands one part of a
                    record: its pulls no Closes line names are released ("releases <ID>: part
                    landed <merge sha> ..."), not closed. A live-proof record (a `requires:
                    live-run` line, is_live_proof) closes only when a `Live-pass: <ID> <evidence>`
                    line of the branch cites a passing live run (live_pass_trailers): else it is
                    released when the branch holds it, left pulled when another branch does, and
                    a NOTE says how to close it. What cannot be closed or released is a
                    WARNING line naming the command, and the landing stands; trailer ids no store
                    holds, and Part-of ids the branch holds nowhere, are NOTE lines. The implicit
                    default store (refs/backlog/main while .kit.json names no "backlog".store;
                    store_live.default_store_refusal) is only read, the local ref too when the
                    remote has none: a record that only it holds is a WARNING that it is
                    stranded there
  landable_commands the `backlog.py landable` command of each store that takes part

Neither creates a store or adds the store's fetch refspec when the remote has no store, and
ancestry_refusal moves no ref: it fetches the store's objects with an empty --refmap.
"""
import os

from kitlib import kitroot
import kitlib.config as kconfig
from kitlib.pluginsource import layout as plugin_layout
from kitlib.config import pmdata
from kitlib.git import cmd, refs
from kitlib.git import repo as gitrepo

from . import offer
from . import plan as pmod
from . import plan_claim, plan_end
from . import record as rmod
from . import store as smod
from . import store_folder, store_open, store_read
from .errors import Dirty, IndexStale, Overtaken, Refusal, Uncommitted

SESSION = 'gate-land'
KIT_ROOT = kitroot.root()
FETCH_TIMEOUT = 300
# What a printed write command names for -C: a backlog write refuses in the main checkout (the
# landers'), so the command never names the main checkout a landing runs in.
WORKTREE = '<a worktree of this repository>'


def _one_line(text):
    return ' '.join(str(text).split())


def backlog_py():
    """The kit's tools/backlog.py, in the live kit checkout: KIT_LIVE_ROOT when this code runs
    from a pinned export of pushed main (kitlib.pluginsource.layout.live_root), which a later prune may
    remove, so a printed command never names it."""
    return os.path.join(plugin_layout.live_root(KIT_ROOT), 'tools', 'backlog.py')


def backlog_command(root, *args):
    """The backlog.py command line that runs `args` against `root`."""
    return 'python "%s" -C "%s" %s' % (backlog_py(), root, ' '.join(args))


def close_command(root, *args):
    """The command line that runs the backlog write `args` for the repository whose main checkout
    is `root`: -C names a worktree placeholder (WORKTREE), never `root`. The repository's own
    tools/backlog.py when it has one (the kit's own repository), else the kit's."""
    if os.path.isfile(os.path.join(root, 'tools', 'backlog.py')):
        tool = 'tools/backlog.py'
    else:
        tool = '"%s"' % backlog_py()
    return 'python %s -C "%s" %s' % (tool, WORKTREE, ' '.join(args))


def ref_store(root, cfg, fetch=False):
    """The configured store's backend when it is a `ref` or `branch` store, else None."""
    backend = store_open.open_backend(root, cfg, fetch=fetch)
    return None if isinstance(backend, store_folder.MainBackend) else backend


def _has_commit(root, sha):
    return cmd.call(['cat-file', '-e', '%s^{commit}' % sha], root).rc == 0


def _local_tip(ref, root):
    """`(commit, None)` of the local `ref`, `('', None)` when it does not exist, or `(None, why)`
    when it cannot be read (a git ended from outside included: refs.ref_commit)."""
    return refs.ref_commit(ref, root)


def pmdata_refusal(root, tip):
    """None when the repository has no PM data record (kit-pm-data.json in its git common dir,
    kitlib.config.pmdata) or `tip`'s history does not hold the recorded root; else why `tip` may not
    land. A record that cannot be read, a root this clone lacks, or an ancestry git cannot tell
    refuses (fail closed)."""
    common, why = gitrepo.common_dir(root)
    if common is None:
        return 'cannot find the git directory to look for the PM data record: %s' % why
    try:
        record = pmdata.read(common)
    except pmdata.PmDataError as exc:
        return 'cannot read the PM data record to check the branch\'s history: %s' % exc
    if record is None:
        return None
    if not _has_commit(root, record.root):
        return ('the PM data root commit %s that %s names is not in this clone, so the branch\'s '
                'history cannot be checked against it' % (record.root[:10], pmdata.record_path(common)))
    held, why = refs.is_ancestor(record.root, tip, root)
    if held is None:
        return ('cannot tell whether the branch holds the PM data root commit %s: %s'
                % (record.root[:10], why))
    if held:
        return ('the branch\'s history holds the PM data root commit %s (branch %s, private remote '
                '%s): PM data never merges into main - rebuild the branch without the data '
                'branch\'s commits' % (record.root[:10], record.branch, record.remote))
    return None


def ancestry_refusal(root, cfg, tip, env=None):
    """None when `tip` may land; else why not: its history holds the PM data branch's recorded
    root (pmdata_refusal) or a root commit of the backlog store (read from the remote, and the
    local copy too), or either cannot be read to tell."""
    why = pmdata_refusal(root, tip)
    if why:
        return why
    try:
        backend = ref_store(root, cfg)
    except Refusal as exc:
        return 'cannot read the backlog store to check the branch\'s history: %s' % exc
    if backend is None:
        return None
    tips = []
    if backend.remote:
        remote_tip, why = refs.ls_remote(backend.remote, backend.remote_ref, root,
                                         timeout=FETCH_TIMEOUT)
        if remote_tip is None:
            return ('cannot read the backlog store %s on %s to check the branch\'s history: %s'
                    % (backend.remote_ref, backend.remote, why))
        if remote_tip and not _has_commit(root, remote_tip):
            # Objects only: the empty --refmap stops git updating a remote-tracking ref (or the
            # local store ref) through a configured refspec, and no refspec is added.
            cmd.call(['fetch', '--quiet', '--no-tags', '--no-write-fetch-head', '--refmap=',
                      backend.remote, backend.remote_ref], root, timeout=FETCH_TIMEOUT, env=env)
            if not _has_commit(root, remote_tip):
                return ('cannot fetch the backlog store %s from %s (%s) to check the branch\'s '
                        'history' % (backend.remote_ref, backend.remote, remote_tip[:10]))
        if remote_tip:
            tips.append(remote_tip)
    local, why = _local_tip(backend.local_ref, root)
    if local is None:
        return ("cannot read the local backlog store %s to check the branch's history: %s"
                % (backend.local_ref, why))
    if local and local not in tips:
        tips.append(local)
    if not tips:
        return None
    out, why = cmd.run_text(['rev-list', '--max-parents=0'] + tips, root)
    if out is None:
        return 'cannot list the backlog store\'s root commit: %s' % why
    for store_root in out.split():
        held, why = refs.is_ancestor(store_root, tip, root)
        if held is None:
            return ('cannot tell whether the branch holds the backlog store\'s root commit %s: %s'
                    % (store_root[:10], why))
        if held:
            return ('the branch\'s history holds the backlog store\'s root commit %s (%s): the '
                    'store has no history shared with main and never merges into it - rebuild '
                    'the branch without the store\'s commits'
                    % (store_root[:10], backend.describe()))
    return None


def closes_ids(values):
    """The record ids in `Closes:` trailer values: each value split on commas, the first word of
    each part, kept when it is a plain id (store_read.PLAIN_ID), so `A, B (both done)` is A and B
    and `#12` is nothing. Pure."""
    out = []
    for value in values:
        for part in value.split(','):
            words = part.split()
            if words and store_read.PLAIN_ID.match(words[0]) and words[0] not in out:
                out.append(words[0])
    return out


CLOSES_KEY = 'Closes:'
# The line that makes a landing one part of a record (close_landed): the branch then closes only
# what its Closes lines name and releases every other record it holds pulled.
PART_KEY = 'Part-of:'


def trailer_values(message, key):
    """The values of the `key` lines (CLOSES_KEY, PART_KEY) of one commit message, anywhere in
    it: a line that starts with `key` (case-sensitive, at the line's start), its value unfolded,
    so each following line that starts with a space or tab joins it. Prose (`closes D2`, or
    `Closes: D2` inside a sentence) is not such a line. Not git's trailer parser, which reads only
    the last paragraph: tools/commit.py writes the attribution as a paragraph of its own. Pure."""
    out = []
    for line in message.splitlines():
        if out and out[-1] is not None and line[:1] in (' ', '\t'):
            out[-1] += ' ' + line.strip()
        elif line.startswith(key):
            out.append(line[len(key):].strip())
        else:
            out.append(None)
    return [v for v in out if v is not None]


def closes_values(message):
    """The values of the `Closes:` lines of one commit message (trailer_values). Pure."""
    return trailer_values(message, CLOSES_KEY)


def _branch_values(root, tip, base, key):
    """`(values, None)`: the `key` line values in the messages of the commits `tip` has and
    `base` does not, merges included; or `(None, why)`."""
    out, why = cmd.run_text(['log', '--format=%B%x00', tip, '--not', base, '--'], root,
                            strip=False)
    if out is None:
        return None, why
    values = []
    for message in out.split('\0'):
        values.extend(trailer_values(message, key))
    return values, None


def closes_trailers(root, tip, base):
    """`(ids, None)`: the ids the `Closes:` lines name (closes_values, closes_ids) in the messages
    of the commits `tip` has and `base` does not, merges included; or `(None, why)`."""
    values, why = _branch_values(root, tip, base, CLOSES_KEY)
    return (None, why) if values is None else (closes_ids(values), None)


def part_trailers(root, tip, base):
    """`((partial, ids), None)` from the `Part-of:` lines of the commits `tip` has and `base` does
    not: `partial` is True when there is any such line, even one whose value holds no plain id (a
    malformed id still makes the landing one part: it releases, never closes); `ids` are the ids
    they name (closes_ids). Or `(None, why)`."""
    values, why = _branch_values(root, tip, base, PART_KEY)
    return (None, why) if values is None else ((bool(values), closes_ids(values)), None)


# A live-proof record carries a `requires: live-run` line (LIVE_PROOF; offer.unmet_requires reads
# it as a note, so it gates no pull): a landing closes it only when a `Live-pass:` line (LIVE_KEY)
# of the branch cites a passing live run of it (close_landed).
LIVE_PROOF = 'live-run'
LIVE_KEY = 'Live-pass:'


def is_live_proof(rec):
    """Whether record `rec` is a live proof: one of its `requires:` lines is LIVE_PROOF. Pure."""
    return LIVE_PROOF in rec.all('requires')


def live_passes(values):
    """The ids `Live-pass:` values cite a passing live run for: a value whose first word is a plain
    id (store_read.PLAIN_ID) and that has at least one more word, the evidence (a run, a hub id, a
    log). `FOO` alone cites nothing. Pure."""
    out = []
    for value in values:
        words = value.split()
        if len(words) > 1 and store_read.PLAIN_ID.match(words[0]) and words[0] not in out:
            out.append(words[0])
    return out


def live_pass_trailers(root, tip, base):
    """`(ids, None)`: the ids the `Live-pass:` lines of the commits `tip` has and `base` does not
    cite (live_passes); or `(None, why)`."""
    values, why = _branch_values(root, tip, base, LIVE_KEY)
    return (None, why) if values is None else (live_passes(values), None)


def live_reason(merge, branch):
    """The reason a landing releases a live-proof record its branch holds with (close_landed)."""
    return ('landed %s (%s); not closed: a live-proof record closes only when a Live-pass line '
            'cites a passing live run' % (merge, branch))


def _live_note(branch, rid, how, command):
    """The NOTE that `rid`, a live-proof record, was not closed and `how` it was left."""
    return ('NOTE: %s landed, and %s is a live-proof record (requires: live-run) with no '
            '`Live-pass: %s <run>` line in its commits to cite a passing live run, so it was not '
            'closed: %s; once a live run passes, close it with: %s'
            % (branch, rid, rid, how,
               command('close', rid, '--reason', '"live run <run> passed"')))


def _close_plan(ctx, rid, branch, reason, named=False):
    """plan_close, run only while `rid` is still pulled by `branch` in the fresh snapshot; or, when
    a `Closes:` trailer of the branch names it (`named`), while it is still pulled by anyone."""
    def plan(snap):
        rec = snap.get(rid)
        if rec is None or rec.get('state') != 'pulled' or (
                not named and rec.get('pulled_by') != branch):
            return smod.Change({}, '', ['%s is no longer pulled%s; left as it is'
                                        % (rid, '' if named else ' by %s' % branch)])
        return plan_end.plan_close(snap, ctx, rid, reason)
    return plan


def release_reason(merge, branch):
    """The reason a partial landing releases a record with (close_landed): also the subject of
    its commit, `releases <ID>: <reason>`."""
    return 'part landed %s (a Part-of line of %s); the rest is not done' % (merge, branch)


def _release_plan(ctx, rid, branch, reason):
    """plan_release as `branch` (ctx.branch; its own pull, so no liveness probe), run only while
    `rid` is still pulled by `branch` in the fresh snapshot; its commit subject is `releases
    <ID>: <reason>`, as a close's is `closes <ID>: <reason>`. `kept=False`: the landing's cleanup
    deletes the branch, so no `Stopped on branch ... branch kept.` line, and no branch read."""
    def plan(snap):
        rec = snap.get(rid)
        if rec is None or rec.get('state') != 'pulled' or rec.get('pulled_by') != branch:
            return smod.Change({}, '', ['%s is no longer pulled by %s; left as it is'
                                        % (rid, branch)])
        change = plan_claim.plan_release(snap, ctx, rid, reason, kept=False)
        change.subject = 'releases %s: %s' % (rid, ' '.join(reason.split()))
        return change
    return plan


def _store_unread(branch, what, how):
    return ('WARNING: %s landed, and the backlog store could not be read (%s), so no record it '
            'holds pulled was closed; see which with: %s' % (branch, _one_line(what), how))


def _same_path(a, b):
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def pm_data_root(root, *, worktrees=None):
    """`(path, None)` of the worktree of `root`'s repository that has the PM data branch checked
    out (the branch kit-pm-data.json records; kitlib.config.pmdata), `(None, None)` when the repository
    has no PM data record, or `(None, why)` when there is one and that worktree cannot be found.
    `worktrees` is a gitrepo.Worktrees, when the caller has one: it holds the list and the common
    dir (`common`) of the repository, each asked of git once."""
    common, why = (worktrees.common if worktrees is not None else None), None
    if common is None:
        common, why = gitrepo.common_dir(root)
    if common is None:
        return None, 'cannot find the git directory to look for the PM data record: %s' % why
    try:
        record = pmdata.read(common)
    except pmdata.PmDataError as exc:
        return None, 'cannot read the PM data record: %s' % exc
    if record is None:
        return None, None
    wts, why = (worktrees or gitrepo.worktrees)(root)
    if wts is None:
        return None, 'cannot list the worktrees: %s' % why
    for wt in wts:
        if wt.branch == record.branch and not wt.bare:
            return wt.path, None
    return None, 'no worktree has the PM data branch %s checked out' % record.branch


def pm_data_store(root, configured):
    """`(store root, Config, backend, None)` of the PM data branch's store, `(None,) * 4` when
    there is none or it is the configured store (`configured`: its describe(), or None), or
    `(None, None, None, why)` when it cannot be opened. Public: kitlib/landing/checks.py calls
    it."""
    pm_root, why = pm_data_root(root)
    if pm_root is None:
        return None, None, None, why
    if _same_path(pm_root, root):
        return None, None, None, None
    try:
        pm_cfg = kconfig.load(pm_root)
        backend = store_open.open_backend(pm_root, pm_cfg, fetch=True, clean=True)
    except (kconfig.ConfigError, Refusal, OSError) as exc:
        return None, None, None, 'cannot open the backlog store in %s: %s' % (pm_root, exc)
    if configured is not None and backend.describe() == configured:
        return None, None, None, None
    return pm_root, pm_cfg, backend, None


class _Seen(object):
    """What the stores close_landed reads say about the ids the trailers name: `held`, the
    Closes ids some store holds; `part_held`, the Part-of ids the landed branch holds pulled in
    some store; `read`, how many stores were read; `unread`, whether one could not be. And what
    the landing did, so its NOTE lines claim only that: `closed` and `released`, the ids whose
    close or release was written (committed or not: a WARNING says which); `unreleased`, the ids
    it set out to release and did not; `held_back`, the live-proof ids it released, not closed,
    for want of a Live-pass line (in neither list). `default`: id -> _Stranded, the records the
    implicit default store holds that the landing would close or release (it writes none there);
    `live`, the snapshots of the other stores read, whose records are not stranded."""

    def __init__(self):
        self.held, self.part_held, self.read, self.unread = set(), set(), 0, False
        self.closed, self.released, self.unreleased = set(), [], []
        self.held_back = set()
        self.default, self.live = {}, []

    def done(self, rid, act):
        if act.live:
            self.held_back.add(rid)
        elif act.release:
            self.released.append(rid)
        else:
            self.closed.add(rid)

    def not_done(self, rid, act):
        if act.release and not act.live:
            self.unreleased.append(rid)


class _Part(object):
    """A branch's `Part-of:` lines (part_trailers): `partial`, whether it has any, so the landing
    is one part of a record; `ids`, the ids they name."""

    def __init__(self, partial=False, ids=()):
        self.partial, self.ids = partial, list(ids)


def close_landed(root, cfg, branch, merge, session=SESSION, tip=None, base=None, batch=()):
    """Close every record `branch`, which landed as `merge`, holds pulled: in the configured store
    when it is a `ref` or `branch` store, and in the PM data branch's store (pm_data_store). With
    `tip` and `base` (the branch tip merged and the main it was merged onto), every record a
    `Closes:` trailer in `tip --not base` names (closes_trailers) is closed too while it is
    pulled, whoever pulled it: a pull made in the main checkout records the main branch, which no
    landing matches. The lines to print (`closed ...`, and a `WARNING: ...` naming the command for
    each record or store that could not be closed, or trailers that could not be read; one
    `NOTE: ...` naming the trailer ids no store read holds, when every store taking part was read).
    `batch`: the branches landing with `branch`, whose builders a trailer close needs no telling.
    The code repository's mains (`cfg`'s guarded_mains) are main in the PM data store too, whose
    own configured main is its data branch.
    A `Part-of: <ID>` line in those messages (part_trailers) makes the landing one part of a
    record: then only the records a Closes line names are closed, and every other record the
    branch holds pulled is released (`state: open`, its pull cleared, so `next` offers it again;
    `released ...` lines, and the WARNINGs name the release command). Without one the branch's
    pulls close. NOTE lines name a Part-of id the branch holds pulled in no store read (a typo,
    or another branch's record: left as it is) with what was released, and an id both a Closes and
    a Part-of line name (closed, never released); each says only what this landing did. When the lines cannot be read, the branch's pulls close (the WARNING says so).
    A live-proof record (is_live_proof) that would close is closed only when a `Live-pass:` line
    of those messages cites a passing live run of it (live_pass_trailers); else one NOTE says why
    and how to close it, and the record is released when the branch holds it, left pulled when
    another branch does. Lines that cannot be read cite nothing.
    [] when neither store takes part and no trailer names a record, or nothing is pulled."""
    lines, named, seen, part, passed = [], [], _Seen(), _Part(), []
    quiet = tuple(cfg.guarded_mains()) + tuple(batch)    # holders nobody needs to tell
    if tip and base:
        named, why = closes_trailers(root, tip, base)
        if named is None:
            named = []
        else:
            found, why = part_trailers(root, tip, base)
            if found is not None:
                part = _Part(*found)
                passed, live_why = live_pass_trailers(root, tip, base)
                if passed is None:
                    passed = []
                    lines.append('WARNING: %s landed, and the Live-pass lines of its commits '
                                 'could not be read (%s), so no live-proof record was closed'
                                 % (branch, _one_line(live_why)))
        if why:
            lines.append('WARNING: %s landed, and the Closes trailers of its commits could not be '
                         'read (%s), so only the records it holds pulled are closed (its Part-of '
                         'lines were not read either, so none of them was released instead; and '
                         'its Live-pass lines were not read either, so each live-proof record it '
                         'holds pulled was released, not closed); see the Closes, Part-of and '
                         'Live-pass lines in: git log --format=%%B %s --not %s'
                         % (branch, _one_line(why), tip, base))
            # As the WARNING says: only the branch's pulls, and no live-proof one is cited.
            named, part, passed = [], _Part(), []
    backend = ref_store(root, cfg, fetch=True)
    if backend is not None:
        lines.extend(_close_in(backend, root, cfg, branch, merge, session,
                               lambda *args: close_command(root, *args), named, seen, quiet,
                               part, passed))
    pm_root, pm_cfg, pm_backend, why = pm_data_store(
        root, backend.describe() if backend is not None else None)
    if why:
        seen.unread = True
        lines.append('WARNING: %s landed, and the PM data branch\'s backlog store was not read '
                     '(%s), so no record it holds pulled there was closed; see which from that '
                     'worktree with: %s' % (branch, _one_line(why),
                                            backlog_command('<the PM data worktree>', 'landable',
                                                            branch)))
    elif pm_backend is not None:
        lines.extend(_close_in(pm_backend, pm_root, pm_cfg, branch, merge, session,
                               lambda *args: backlog_command(pm_root, *args), named, seen,
                               quiet, part, passed))
    if not seen.unread:
        for rid in sorted(seen.default):
            if not any(snap.get(rid) is not None for snap in seen.live):
                lines.append(seen.default[rid].warning(branch, root, pm_root))
    unknown = [rid for rid in named if rid not in seen.held and rid not in seen.default]
    if unknown and seen.read and not seen.unread:
        lines.append('NOTE: %s landed, and a Closes trailer of its commits names %s, which no '
                     'backlog store this landing reads holds (a typo, or a case that differs from '
                     'the record\'s file name?), so nothing was closed for %s'
                     % (branch, ', '.join(unknown), 'it' if len(unknown) == 1 else 'them'))
    for rid in (r for r in part.ids if r in named):
        lines.append('NOTE: %s landed, and %s is named by both a Closes line and a Part-of line '
                     'of its commits; the Closes line wins, so %s'
                     % (branch, rid, 'it was closed, not released' if rid in seen.closed else
                        'it was released, not closed, as a live-proof record (see the NOTE '
                        'above)' if rid in seen.held_back else
                        'it was not released, and it was not closed either (see the lines '
                        'above)'))
    stray = [rid for rid in part.ids
             if rid not in named and rid not in seen.part_held and rid not in seen.default]
    if stray and seen.read and not seen.unread:
        did = []
        if seen.released:
            did.append('it released %s, which no Closes line names' % ', '.join(seen.released))
        if seen.unreleased:
            did.append('%s, which it held pulled, %s not released (see the lines above)'
                       % (', '.join(seen.unreleased),
                          'was' if len(seen.unreleased) == 1 else 'were'))
        lines.append('NOTE: %s landed, and a Part-of line of its commits names %s, which it does '
                     'not hold pulled in any backlog store this landing reads (a typo, or another '
                     'branch\'s record?), so the landing left %s as %s; %s'
                     % (branch, ', '.join(stray), 'it' if len(stray) == 1 else 'them',
                        'it is' if len(stray) == 1 else 'they are',
                        '; '.join(did) or 'it held no record pulled that no Closes line names'))
    return lines


class _Act(object):
    """What the landing does to one record: close it (`landed <merge>`), or release it, for a
    landing that is one part of it (release_reason), or for a live-proof record no Live-pass line
    cites a passing live run for (`live`: live_reason). `verb`, `past` and `noun` word the lines;
    `args` is the backlog.py command that does it by hand."""

    def __init__(self, rid, branch, merge, release=False, live=False):
        self.rid, self.release, self.live = rid, release or live, live
        if self.release:
            self.verb, self.past, self.noun = 'release', 'released', 'release'
            self.reason = live_reason(merge, branch) if live else release_reason(merge, branch)
            # `--as`: the person who runs it is not on the landed branch, whose own pull it is.
            self.args = ['--as', branch, 'release', rid, '--reason', '"%s"' % self.reason]
        else:
            self.verb, self.past, self.noun = 'close', 'closed', 'close'
            self.reason = 'landed %s' % merge
            self.args = ['close', rid, '--reason', '"%s"' % self.reason]


def _after_put_back(store, rid, branch, command, reason, named=False, act=None):
    """What is true of `rid` once a refused close (or release: `act`, an _Act) was put back, read
    from the store again: the other commit may have closed or removed the record, released it,
    or left its file unreadable. The close command is given only while `branch` still holds the
    record pulled; or, when a `Closes:` trailer of the branch names it (`named`), while anyone
    does, as _close_plan would close it. A release's command, only while `branch` holds it."""
    past = act.past if act else 'closed'
    try:
        now = store.load().get(rid)
    except (Refusal, OSError) as exc:
        return ('the store could not be read again (%s), so see whether %s is still open with: %s'
                % (_one_line(str(exc)), rid, command('show', rid)))
    if now is None:
        return ('the store no longer holds %s (that commit closed or removed it), so there is '
                'nothing to %s' % (rid, act.verb if act else 'close'))
    if not now.readable:
        return ('%s cannot be read as that commit left it, so it was not %s; see it with: %s'
                % (rid, past, command('show', rid)))
    holder = now.get('pulled_by')
    if now.get('state') == 'pulled' and (named or holder == branch):
        if act is not None and act.release:
            return ('%s is still pulled by %s and was not released; release it with: %s'
                    % (rid, holder or '?', command(*act.args)))
        return ('%s is still pulled by %s and was not closed; close it with: %s'
                % (rid, holder or '?', command('close', rid, '--reason', '"%s"' % reason)))
    return ('%s is now %s and no longer pulled%s, so it was not %s; see it with: %s'
            % (rid, now.get('state') or 'in an unreadable state',
               '' if named else ' by %s' % branch, past, command('show', rid)))


class _Stranded(object):
    """A record the implicit default store (`where`: refs/backlog/main, read only) holds that the
    landing would close or release: `rec`, the record; `named`, whether a Closes line names it
    (else the branch holds it pulled); `act`, the _Act the landing would make. `proof`: a
    live-proof record no Live-pass line cites that another branch holds or none does, which the
    landing would not close (as _close_in leaves it), so the WARNING names a close only for once
    a live run passes."""

    def __init__(self, where, rec, named, act, proof=False):
        self.where, self.rec, self.named, self.act = where, rec, named, act
        self.proof = proof and not act.release

    def warning(self, branch, root, pm_root):
        """The WARNING that the record is stranded in the default store: close_landed prints it
        when no other store read holds the id."""
        act, rid = self.act, self.act.rid
        args = list(act.args)
        why, once = '', ''
        if self.proof:
            args = ['close', rid, '--reason', '"live run <run> passed"']
            why = (' (a live-proof record: no `Live-pass: %s <run>` line in its commits cites a '
                   'passing live run)' % rid)
            once = 'once a live run passes, '
        state = self.rec.get('state') or '?'
        if state == 'pulled':
            state = 'pulled by %s' % (self.rec.get('pulled_by') or '?')
        what = ('a Closes line of its commits names %s, which only %s holds (%s)'
                % (rid, self.where, state) if self.named
                else 'it holds %s pulled only in %s' % (rid, self.where))
        live = ('the PM data worktree %s' % pm_root.replace('\\', '/') if pm_root
                else 'this repository\'s live backlog store')
        return ('WARNING: %s landed, and %s: the default backlog store, read only while .kit.json '
                'names no "backlog".store, so %s was not %s%s; the record is stranded there: %sadd '
                'it to %s and %s it there, or, if %s is this repository\'s live store, name it in '
                '.kit.json ("backlog": {"store": "ref"}) and %s it with: %s'
                % (branch, what, rid, act.past, why, once, live, act.verb, smod.STORE_REF,
                   act.verb, close_command(root, *args)))


def _refused_advice(exc, backend, root, act, args, command):
    """How to make a write the store refused: `command(*args)`. The implicit default store is
    never written (_close_in reads it only), so no refusal of its comes here."""
    return '%s it with: %s' % (act.verb, command(*args))


def _read_default(snap, where, branch, merge, named, seen, part, passed=()):
    """Record in `seen.default` what the implicit default store's `snap` holds that the landing
    would close (an id a Closes line names) or close or release (the branch's own pulls); a
    live-proof record no Live-pass line in `passed` cites: release it when the branch holds it,
    else close it not at all (_Stranded.proof)."""
    def proof(rec):
        return rec.readable and is_live_proof(rec) and rec.stem not in passed

    def unproven(rec):
        return (proof(rec) and rec.get('state') == 'pulled'
                and rec.get('pulled_by') == branch)
    for rid in named:
        rec = snap.get(rid)
        if rec is not None:
            seen.default.setdefault(rid, _Stranded(where, rec, True, _Act(
                rid, branch, merge, live=unproven(rec)), proof=proof(rec)))
    for rec in snap.readable:
        if (rec.get('state') == 'pulled' and rec.get('pulled_by') == branch
                and rec.stem not in named):
            seen.default.setdefault(rec.stem, _Stranded(where, rec, False, _Act(
                rec.stem, branch, merge, release=part.partial,
                live=not part.partial and unproven(rec))))


def _close_in(backend, root, cfg, branch, merge, session, command, named=(), seen=None,
              quiet=(), part=None, passed=()):
    """close_landed for one store: `backend` of the repository or worktree `root` with `cfg`;
    `command(*args)` is the printed command line for a write the landing could not make. `named`:
    the ids the branch's `Closes:` trailers name; one this store does not hold is another store's
    or none (close_landed's NOTE, from `seen`, a _Seen); one it holds and not pulled is a WARNING
    naming the command; one pulled by a branch that is neither main nor a dead pull (no local,
    remote-tracking or worktree branch by that name: offer.live_branches) is closed, with a
    WARNING that the holder's builder must be told, unless the holder is in `quiet` (the code
    repository's mains and the batch's branches: _live_holder_warning). `part` (a _Part): when
    the branch has a `Part-of:` line, a record it holds pulled that no Closes line names is
    released (_release_plan), not closed. A live-proof record it would close (is_live_proof) whose
    id is not in `passed` (live_pass_trailers) is not closed: released when the branch holds it,
    left pulled when another branch does, with one NOTE each (_live_note).
    The implicit default store (`backend.implicit`) is only read, into `seen.default`
    (_read_default), for close_landed's stranded-record WARNING: the local ref when the remote has
    no store, as the landing's own store commands would find it."""
    seen = seen if seen is not None else _Seen()
    part = part if part is not None else _Part()
    reason = 'landed %s' % merge
    unread = backlog_command(root, 'landable', branch)
    implicit = getattr(backend, 'implicit', False)
    where = getattr(backend, 'remote_ref', None) or smod.STORE_REF
    if getattr(backend, 'remote', None):
        remote_tip, why = refs.ls_remote(backend.remote, backend.remote_ref, root,
                                         timeout=FETCH_TIMEOUT)
        if remote_tip is None:
            seen.unread = True
            return [_store_unread(branch, '%s on %s: %s' % (backend.remote_ref, backend.remote,
                                                            why), unread)]
        if not remote_tip and not implicit:
            seen.read += 1                  # no store, so it holds none of them
            return []                       # no store: create none, add no refspec
        if not remote_tip:                  # the default store: read the local ref, no fetch
            try:
                backend = store_open.open_backend(root, cfg, fetch=False)
            except (kconfig.ConfigError, Refusal, OSError) as exc:
                seen.unread = True
                return [_store_unread(branch, 'the local %s: %s' % (smod.STORE_REF, exc),
                                      unread)]
            where = 'the local %s (%s has none)' % (backend.local_ref, backend.remote)
    section = cfg.section('backlog')
    store = smod.Store(backend, rmod.Rules(section['id_grammar'], section['phases']),
                       session=session)
    try:
        snap = store.load()
        store_read.require_readable(snap)
    except Refusal as exc:
        seen.unread = True
        return [_store_unread(branch, '%s: %s' % (exc.label, exc), unread)]
    seen.read += 1
    if implicit:
        _read_default(snap, where, branch, merge, named, seen, part, passed)
        return []
    seen.live.append(snap)
    seen.held.update(rid for rid in named if snap.get(rid) is not None)
    held = set(r.stem for r in snap.readable               # the store's lookup key (Snapshot.by_id)
               if r.get('state') == 'pulled' and r.get('pulled_by') == branch)
    release = set()
    if part.partial:                        # one part of a record: close only what Closes names
        release = held - set(named)
        held = held & set(named)
        seen.part_held.update(rid for rid in part.ids if rid in release)

    def close_args(rid):
        # An owner-held record's close is `close-held` (kitlib/backlog/roles.py): the printed
        # form is the same, and the role check of whoever runs it decides.
        return ['close', rid, '--reason', '"%s"' % reason]

    def act_args(act, rid):
        return act.args if act.release else close_args(rid)

    lines = []
    by_trailer = {}                         # id -> its pulled_by, for a record held by another
    for rid in named:
        rec = snap.get(rid)
        if rec is None or rid in held:
            continue
        if rec.get('state') != 'pulled':
            lines.append('WARNING: %s landed, and a Closes trailer of its commits names %s, which '
                         'is %s, not pulled, so it was not closed; %sclose it with: %s'
                         % (branch, rid, rec.get('state') or '?',
                            'it is a live-proof record: once a live run passes, '
                            if is_live_proof(rec) and rid not in passed else '',
                            command(*close_args(rid))))
            continue
        by_trailer[rid] = rec.get('pulled_by') or '?'
    # Live-proof records no Live-pass line cites: the branch's own pulls are released, another
    # holder's are left pulled; each gets one NOTE (below).
    unproven = set(rid for rid in held | set(by_trailer)
                   if is_live_proof(snap.get(rid)) and rid not in passed)
    left = dict((rid, by_trailer.pop(rid)) for rid in unproven if rid in by_trailer)
    held -= unproven
    release |= unproven - set(left)
    ctx = pmod.Context(store, root, remote=cfg.remote, main_branch=cfg.main_branch,
                       code_main=kconfig.work_base(cfg), branch=branch)
    folder = isinstance(backend, store_folder.MainBackend)
    live = []                               # offer.live_branches' answer, once it is needed
    gone = set()                            # ids the fresh snapshot no longer had as planned
    for rid in sorted(held | set(by_trailer) | release):
        act = _Act(rid, branch, merge, release=rid in release, live=rid in unproven)
        how = (', which a Closes trailer of its commits names' if rid in by_trailer
               else ', which it holds pulled')
        try:
            if act.release:
                change, tip = store.write(_release_plan(ctx, rid, branch, act.reason))
            else:
                change, tip = store.write(_close_plan(ctx, rid, branch, reason,
                                                      named=rid in by_trailer))
        except Overtaken as exc:            # another commit changed the record: never blind
            if exc.left:                    # a file the write could not put back to HEAD
                lines.append('WARNING: %s landed, and the %s of %s in the backlog store %s was '
                             'refused (%s); do not commit it over that commit: compare each path '
                             'left with HEAD first: %s'
                             % (branch, act.noun, rid, backend.describe(), _one_line(str(exc)),
                                exc.compare))
            else:                           # put back: the record is as that commit left it
                lines.append('WARNING: %s landed, and the %s of %s in the backlog store %s was '
                             'refused and its files were put back (%s: %s); %s'
                             % (branch, act.noun, rid, backend.describe(), exc.label,
                                _one_line(str(exc)),
                                _after_put_back(store, rid, branch, command, reason,
                                                named=rid in by_trailer, act=act)))
            seen.not_done(rid, act)
            continue
        except Uncommitted as exc:          # written in the PM data worktree, its commit failed
            lines.append('WARNING: %s landed, and %s was %s in the backlog store %s but not '
                         'committed (%s); commit it with: %s'
                         % (branch, rid, act.past, backend.describe(), _one_line(str(exc)),
                            command('catch-up', '--apply',
                                    store_folder.quoted(exc.paths or [backend.path_of(rid)]))))
            if rid in by_trailer:           # closed all the same: its holder must be told
                lines.extend(_live_holder_warning(branch, rid, by_trailer[rid], cfg, live, root,
                                                  quiet))
            seen.done(rid, act)
            continue
        except IndexStale as exc:           # committed; only that worktree's index is behind
            lines.append('WARNING: %s landed, and %s was %s in the backlog store %s (%s); '
                         'repair the index with: %s'
                         % (branch, rid, act.past, backend.describe(), _one_line(str(exc)),
                            exc.repair))
            if rid in by_trailer:
                lines.extend(_live_holder_warning(branch, rid, by_trailer[rid], cfg, live, root,
                                                  quiet))
            seen.done(rid, act)
            continue
        except Dirty as exc:                # another session's uncommitted change: never over it
            first = ('repair the index with: %s, then' % exc.repair if exc.repair
                     else 'once that change is committed or put back,')
            lines.append('WARNING: %s landed, and %s%s, was not %s (%s: %s); %s %s it '
                         'with: %s'
                         % (branch, rid, how, act.past, exc.label, _one_line(str(exc)), first,
                            act.verb, command(*act_args(act, rid))))
            seen.not_done(rid, act)
            continue
        except Exception as exc:            # one record's failure never stops the others
            what = ('%s: %s' % (exc.label, exc) if isinstance(exc, Refusal)
                    else '%s: %s' % (type(exc).__name__, exc))
            lines.append('WARNING: %s landed, and %s%s, was not %s (%s); %s'
                         % (branch, rid, how, act.past, _one_line(what),
                            _refused_advice(exc, backend, root, act, act_args(act, rid),
                                            command)))
            seen.not_done(rid, act)
            continue
        if not change.writes:
            lines.extend(change.lines)
            seen.not_done(rid, act)
            gone.add(rid)
            continue
        seen.done(rid, act)
        whose = ('; named by a Closes trailer of %s; it was pulled by %s' % (branch, by_trailer[rid])
                 if rid in by_trailer else '')
        if folder:
            lines.append('%s %s in the backlog store %s: %s (%s)%s'
                         % (act.past, rid, backend.describe(), act.reason,
                            'committed %s' % tip[:10] if tip
                            else 'the record file is %s, not committed'
                            % ('written' if act.release else 'deleted'), whose))
        else:
            lines.append('%s %s in the backlog store: %s (store at %s)%s'
                         % (act.past, rid, act.reason, (tip or '?')[:10], whose))
        lines.extend('  ' + line for line in change.lines      # the summary line is said above
                     if not (line.startswith('RELEASED %s ' % rid) if act.release
                             else line == 'CLOSED %s' % rid))
        if rid in by_trailer:
            lines.extend(_live_holder_warning(branch, rid, by_trailer[rid], cfg, live, root,
                                              quiet))
    for rid in sorted(unproven):
        how = ('it was left pulled by %s' % left[rid] if rid in left else
               'its pull was released' if rid in seen.held_back else
               'its pull was not released either: it is no longer pulled by %s' % branch
               if rid in gone else
               'its pull was not released either (see the WARNING above)')
        lines.append(_live_note(branch, rid, how, command))
    return lines


def _live_holder_warning(branch, rid, holder, cfg, live, root, quiet=()):
    """[] when `holder`, who had `rid` pulled before a Closes trailer of `branch` closed it, is
    the landed branch, the store's main branch or one in `quiet` (the code repository's mains: a
    pull made in the main checkout; a branch of the same batch), unnamed, or a dead pull (git
    answered and no branch has that name); else the WARNING that the holder's builder must be
    told. `live`: offer.live_branches' answer, filled on first use; a probe that fails counts the
    holder live (fail toward telling)."""
    if holder in (branch, cfg.main_branch, '?', '') or holder in quiet:
        return []
    if not live:
        live.extend(offer.live_branches(root, cfg.remote))
    names, why = live
    if names is not None and holder not in names:
        return []
    probe = ('' if names is not None else
             ' (git could not tell whether that branch is still live: %s)' % _one_line(why))
    return ['WARNING: %s landed, and its Closes trailer closed %s while %s holds it pulled%s; '
            'tell the builder of %s that %s is closed' % (branch, rid, holder, probe, holder, rid)]


def landable_commands(root, cfg, branch):
    """The `backlog.py landable` command of each store a landing closes records in (close_landed):
    the configured store's when it is a `ref` or `branch` store, the PM data store's when there is
    one. A store that cannot be opened names its command anyway."""
    out = []
    try:
        configured = ref_store(root, cfg)
    except Exception:                       # noqa: BLE001 - cannot tell: name it anyway
        configured, out = None, [backlog_command(root, 'landable', branch)]
    if configured is not None:
        out.append(backlog_command(root, 'landable', branch))
    try:
        pm_root, _why = pm_data_root(root)
    except Exception:                       # noqa: BLE001
        pm_root = None
    if pm_root is not None and not _same_path(pm_root, root):
        out.append(backlog_command(pm_root, 'landable', branch))
    return out
