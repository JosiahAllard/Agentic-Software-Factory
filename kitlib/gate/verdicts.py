"""The verdict queue: reviewed branches waiting to land, one file per branch.

A coordinator queues a branch after reading its review (tools/verdict.py add); a reviewer never
does, so no reviewer can queue its own verdict. After a main sync of a queued branch the
coordinator carries the verdict to the new head (tools/verdict.py carry) when the sync is all that
changed (kitlib.gate.carry). A landing reads its branch's entry and never removes it;
tools/verdict.py list drops each entry that landed or whose branch is gone (expire()).

An entry's place and keys, its check and its expiry are kitlib.records.verdict_entry's (ve), and
so are VerdictError and BadEntry. Every write is a temp file in the same folder, then os.replace; a
re-add for the same branch replaces its entry.

The queue lock: add, drop, mark_failed and scan each run whole (read, check, then write or
remove) holding `<queue folder>/.queue.lock`, a file made with O_CREAT|O_EXCL and removed when
the call ends, so none of them acts on an entry another replaced between its read and its write.
carry() runs its git check between two holds and writes only when the entry and the branch's head
are, in the second, what they were in the first. A call waits up to LOCK_WAIT_S for the lock, then raises VerdictError and does nothing. A
PermissionError making the lock file is waited out the same way (on Windows a lock file being
removed refuses a new one for a moment), the VerdictError then naming it; any other OSError is a
VerdictError at once, naming the cause. Stale-lock
rule: a holder keeps the lock for milliseconds, so a lock file last written more than
LOCK_STALE_S ago was left by a holder that died (a killed process skips its finally) and the
next caller removes it and takes the lock. A lock file that cannot be removed when a call ends is
left to that rule. Temp files are made only under the lock, so one that scan (holding it) sees
was left by a write that died: scan reports it among the files that are not entries, which stops
entries(); nothing here deletes it.

Errors (all VerdictError): BadEntry for a malformed entry or branch name given, nothing read or
written; SelfReview (a BadEntry) when add() is given a Medium or High-risk verdict the coordinator queueing
it reviewed itself (self_review_why), nothing read or written, or, with "landing".
self_review_max_lines given, a self-read of a branch over that many changed lines or one whose
size cannot be told (self_review_size_why), nothing written; ModelMismatch (a BadEntry) when
add() is given the models the reviewer's transcript shows (seen_models, the host's reader) and one
is not the reviewer_model stated (model_mismatch_why), nothing written; ReviewerSaid (a BadEntry)
when add() is given what the reviewer's transcript says (seen_verdicts, the host's reader) and its
final report says SEND BACK or has no `Verdict:` line, or a transcript found cannot be read
(reviewer_verdict_why), or the report's `Checks:` and `Done-when:` lines, the follow-ups or the
design review named for a new source file or class fail (checks_why, done_when_why,
follow_up_why, design_why), nothing written;
LightReview (a BadEntry)
when add() is given a High-risk verdict whose reviewer_model is not a strong-tier model (tier_why),
nothing written; TipMismatch when the tip is not the branch's head, or there is no such branch;
CarryRefused when carry() finds the head does not carry the verdict, nothing written;
OtherBranch when the file for the branch named holds another branch's entry (names that differ
only in case share one file on Windows); VerdictError itself for a queue file that cannot be read
or written, or is not an entry, the queue lock not had in time, git giving no answer, or (add())
hosts.json "tiers" unread when the tier rule needs them, nothing written. Nothing
here prints.

Residual race (inference, not tested): two callers that find the same stale lock at once can
both remove it, the second removing the lock the first just made; that needs a holder to have
died first. A wall-clock jump of more than LOCK_STALE_S can make a held lock look stale.
"""
import contextlib
import datetime
import json
import os
import re
import secrets
import time

from kitlib.fs import replace as fsreplace
from kitlib.gate import carry as carrymod
from kitlib.git import batch as gitbatch
from kitlib.git import cmd as gitcmd
from kitlib.git import refs
from kitlib.records import verdict_entry as ve

# The check ids a reviewer's `Checks:` line gives (skill specialty-review "Report"), each PASS,
# FAIL or N/A: a code review lists every CODE_CHECKS id (home, reuse, size and layering N/A when an
# approved design came), a design review every DESIGN_CHECKS id, its checks 1-7 in order.
CODE_CHECKS = ('scope', 'done-when', 'defects', 'tests', 'copies', 'home', 'reuse', 'size',
               'layering')
DESIGN_CHECKS = ('home', 'reuse', 'cause', 'scope', 'size', 'contract', 'layering')
CHECK_STATES = ('PASS', 'FAIL', 'N/A')
RETRIES = 5                 # tries of a read, replace or remove while it fails with PermissionError
RETRY_SLEEP = 0.2           # seconds between them
# RETRIES as a wait for kitlib.fs.replace: try k starts at least k-1 pauses in, so the last (at
# 0.8 s or later) is past this bound and the one before it (at 0.6 s) is not; the half pause is
# the margin for a slow try.
REPLACE_WAIT = (RETRIES - 1.5) * RETRY_SLEEP
LOCK_NAME = '.queue.lock'
LOCK_WAIT_S = 10.0          # how long add, drop and mark_failed wait for the queue lock
LOCK_STALE_S = 60.0         # a lock file older than this was left by a crash
LOCK_POLL_S = 0.05
BY_HAND = 'remove it by hand'


class SelfReview(ve.BadEntry):
    """A Medium or High-risk verdict whose reviewer is the coordinator queueing it (self_review_why): its
    text ends with the Instead: line (SELF_REVIEW_INSTEAD). Nothing was read or written."""


class ModelMismatch(ve.BadEntry):
    """The reviewer's transcript shows a model other than the reviewer_model given
    (model_mismatch_why): its text ends with an Instead: line. Nothing was read or written."""


class ReviewerSaid(ve.BadEntry):
    """The reviewer's final report says SEND BACK, has no `Verdict:` line, or its transcript was
    found and cannot be read (reviewer_verdict_why); it has no full `Checks:` line (checks_why) or
    reports a Done-when item UNMET under a plain LAND (done_when_why); the follow-ups are wrong
    (follow_up_why); or a new source file or class has no approved design review (design_why).
    Its text ends with an Instead: line. Nothing was read or written."""


class LightReview(ve.BadEntry):
    """A High-risk verdict whose reviewer_model is not a strong-tier model (tier_why): its text
    ends with the Instead: line (TIER_INSTEAD). Nothing was read or written."""


class TipMismatch(ve.VerdictError):
    """The tip to queue is not the branch's head (or the branch does not exist)."""


class OtherBranch(ve.VerdictError):
    """The file for the branch named holds another branch's entry."""


class CarryRefused(ve.VerdictError):
    """The branch's head does not carry its queued verdict (carry()): nothing was written."""


def utc(when=None):
    return (when or datetime.datetime.now(datetime.timezone.utc)).strftime('%Y-%m-%dT%H:%M:%SZ')


SELF_REVIEW_INSTEAD = ('Instead: dispatch a fresh analyst to review the branch (skill branch-review, '
                       'step 3), then queue its verdict with --reviewer pm-kit:analyst '
                       '--reviewer-id <its agent id>')
COORDINATOR_RE = re.compile(r'(?<![a-z0-9])coordinator(?![a-z0-9])')


def self_reader(entry):
    """How `entry`'s reviewer is the coordinator queueing it, or None: an id `reviewer_id` lists
    (split as land.is_author splits it) is `session`, case and outer space aside, or an agent type
    the reviewer names (reviewer_types) holds `coordinator` as a word (`pm-kit:coordinator`,
    `kit-coordinator`). `entry` is well formed (check). Pure."""
    session = entry['session'].strip().casefold()
    ids = [i for i in re.split(r'[/,;\s]+', entry['reviewer_id'].casefold()) if i]
    if session in ids:
        return 'its reviewer id %s is the session queueing it' % entry['reviewer_id'].strip()
    if any(COORDINATOR_RE.search(t) for t in ve.reviewer_types(entry['reviewer'])):
        return 'its reviewer %s is the coordinator role' % entry['reviewer'].strip()
    return None


def self_review_why(entry):
    """Why add() refuses `entry` as a self-review, or None. Refused: a Medium or High-risk
    verdict whose reviewer is the coordinator queueing it (self_reader). A Low one is not: a
    coordinator may read a small Low branch itself (skill branch-review, step 3), up to
    "landing".self_review_max_lines when that is set (self_review_size_why). `entry` is well
    formed (check). Pure."""
    if entry['risk'] not in ('Medium', 'High'):
        return None
    who = self_reader(entry)
    if not who:
        return None
    return ('a %s-risk verdict needs a reviewer other than the coordinator queueing it, and %s '
            '(skill branch-review, step 3); nothing was queued\n%s'
            % (entry['risk'], who, SELF_REVIEW_INSTEAD))


def diff_lines(cwd, rng):
    """`(added, removed, files)` the range `rng` (`<base>..<tip>`) changes, counted as
    `git diff --numstat --find-renames <base>...<tip>` counts them: from the merge base of the two
    to the tip, so commits the base gained after the branch left it are not counted. A binary file
    is one file and no lines. Git runs in `cwd` (a git dir does). VerdictError when git gives no
    answer or prints a line that is not a numstat row."""
    base, _, tip = rng.partition('..')
    for rev in (base, tip):
        why = refs.bad_rev(rev)
        if why:
            raise ve.VerdictError('the range %s: %s' % (rng, why))
    out, why = gitcmd.run_text(['diff', '--numstat', '--find-renames', '%s...%s' % (base, tip),
                                '--'], cwd)
    if out is None:
        raise ve.VerdictError('git diff --numstat %s...%s: %s' % (base, tip, (why or '').strip()))
    added = removed = files = 0
    for row in out.splitlines():
        a, sep, rest = row.partition('\t')
        d, sep2, _ = rest.partition('\t')
        if not (sep and sep2):
            raise ve.VerdictError('git diff --numstat printed %r, not a numstat row' % row[:80])
        if (a, d) != ('-', '-'):
            if not (a.isdigit() and d.isdigit()):
                raise ve.VerdictError('git diff --numstat printed %r, not a numstat row' % row[:80])
            added, removed = added + int(a), removed + int(d)
        files += 1
    return added, removed, files


def self_review_size_why(entry, max_lines, cwd, main, main_why=None):
    """Why add() refuses `entry` as a self-read too large for the coordinator to give, or None.
    None whenever `max_lines` ("landing".self_review_max_lines) is None or the reviewer is not the
    coordinator queueing it (self_reader). Else the size is the larger of two counts (diff_lines,
    git run in `cwd`): the range given, and `main...<tip>` with `main` main as fetched, so a base
    stated past most of the branch (`<tip>~1..<tip>`) does not shrink it. Refused when that is
    more than `max_lines`, or when it cannot be told (`main` None, `main_why` saying why, or git
    gives no answer): with a cap set, a size that cannot be told is not a small branch. `entry` is
    well formed (check)."""
    if max_lines is None:
        return None
    who = self_reader(entry)
    if not who:
        return None
    head = ('a verdict the coordinator queueing it reviewed itself covers at most %d changed lines '
            '("landing".self_review_max_lines), and ' % max_lines)
    tail = '; %s; nothing was queued\n%s' % (who, SELF_REVIEW_INSTEAD)
    if not main:
        return head + ('main as fetched cannot be read (%s), so the size of %s cannot be told'
                       % (main_why or 'not given', entry['range'])) + tail
    tip = entry['tip'][:10]
    counts = []
    for rng in (entry['range'], '%s..%s' % (main, entry['tip'])):
        try:
            counts.append(diff_lines(cwd, rng))
        except ve.VerdictError as e:
            return head + 'git cannot tell how many lines %s changes (%s)' % (rng, e) + tail
    added, removed, files = max(counts, key=lambda c: c[0] + c[1])
    if added + removed <= max_lines:
        return None
    return head + ('%s changes %d (+%d -%d in %d file%s; the larger of the range given and %s...%s '
                   'from main as fetched)' % (entry['range'], added + removed, added, removed,
                                              files, '' if files == 1 else 's', main[:10],
                                              tip)) + tail


def _reviewer_ids(text):
    """The ids a verdict's `reviewer_id` lists, split as land.is_author splits them. Pure."""
    return [i for i in re.split(r'[/,;\s]+', text) if i]


def seen_models(entry, reader):
    """`{id: [models]}`: for each id `entry`'s reviewer_id lists that `reader` takes as an agent
    id, the models its transcript shows. `reader` is a host's transcript reader (on Claude
    hosts.claude.adapter.agent_models.models): id -> sorted models, [] for an agent id with no
    transcript, None for an id that is not an agent id (a session name), which is left out. {}
    when there is no reader (a host with none, Codex) or the reviewer is the coordinator queueing
    it (self_reader): a self-review is no subagent. `entry` is well formed (check)."""
    if reader is None or self_reader(entry):
        return {}
    out = {}
    for rid in _reviewer_ids(entry['reviewer_id']):
        got = reader(rid)
        if got is not None:
            out[rid] = list(got)
    return out


def model_mismatch_why(entry, seen):
    """Why add() refuses `entry` because its reviewer ran on another model than its reviewer_model
    says, or None. `seen` is seen_models(): a model seen matches when it is reviewer_model (case
    and outer space aside) or names the same tier model of hosts.json (land.named_model:
    `claude-haiku-5-5` names haiku). Refused when any model seen for any id does not match; an id
    with none seen (no transcript) is not checked. The Instead: line names the one model seen, or,
    when the review ran on more than one, asks for a fresh review on reviewer_model (in place of
    each reviewer off it, when several reviewers were seen): no one reviewer_model could name it. Two reviewers (a review and an
    architecture check) on one model pass. `entry` is well formed (check)."""
    from kitlib.gate import land          # land imports this module: a late import
    strong, weak, _ = land.tier_models()
    strong, weak = strong or set(), weak or set()
    typed = entry['reviewer_model'].strip()
    want = land.named_model(typed, strong, weak)

    def same(model):
        return (model.casefold() == typed.casefold()
                or (want is not None and land.named_model(model, strong, weak) == want))
    off = [(rid, models) for rid, models in sorted(seen.items())
           if models and not all(same(m) for m in models)]
    if not off:
        return None
    said = '; '.join('the reviewer %s ran on %s' % (rid, ' and '.join(models))
                     for rid, models in off)
    tier = ' or '.join(sorted(strong - weak)) or 'a strong-tier model'
    head = ('--reviewer-model %s, but %s (its transcript): an agent dispatched with no model '
            'override runs on its role\'s default tier; nothing was queued\nInstead: '
            % (typed, said))
    every = sorted(set(m for models in seen.values() for m in models))
    if len(every) > 1:
        # the review ran on more than one model: no one --reviewer-model can name it. With two
        # reviewers (an architecture check beside the review), only the one off is redone.
        which = ''
        if sum(1 for models in seen.values() if models) > 1:
            which = (' in place of the reviewer %s (one --reviewer-model covers every '
                     '--reviewer-id)' % ' and '.join(rid for rid, _ in off))
        return head + ('the review ran on more than one model (%s), which no --reviewer-model can '
                       'name: dispatch a fresh analyst with model %s%s, the model the verdict '
                       'needs (%s for a High-risk branch or one land.py --check-strict calls '
                       'STRICT; skill branch-review)' % (', '.join(every), typed, which, tier))
    use = 'queue it with --reviewer-model %s, the model it ran on' % (
        land.named_model(every[0], strong, weak) or every[0])
    return head + ('%s; a High-risk branch, or one land.py --check-strict calls STRICT, needs a '
                   'review on %s: dispatch a fresh analyst with model %s (skill branch-review)'
                   % (use, tier, tier))


SEND_BACK = 'SEND BACK'
UNREAD = 'unread'           # a reviewer verdict reader's verdict: a transcript found cannot be read


def seen_reports(ids, reader):
    """`{id: (found, verdict, report)}`: for each id the text `ids` lists (split as reviewer_id is)
    that `reader` takes as an agent id, what its transcript says. `reader` is a host's reviewer
    verdict reader (on Claude hosts.claude.adapter.agent_models.verdict): id -> `(found, verdict,
    report)`, `found` whether a transcript was found, `verdict` its final report's `Verdict:` line
    (LAND, LAND WITH FOLLOW-UP, SEND BACK) or None, or UNREAD when a transcript found cannot be
    read, `report` that final report's text (None when none was read); None for an id that is not
    an agent id, which is left out."""
    out = {}
    for rid in _reviewer_ids(ids):
        got = reader(rid)
        if got is not None:
            out[rid] = (bool(got[0]), got[1], got[2])
    return out


def seen_verdicts(entry, reader):
    """seen_reports() for `entry`'s reviewer_id. {} when there is no reader (a host with none,
    Codex) or the reviewer is the coordinator queueing it (self_reader), as seen_models. `entry`
    is well formed (check)."""
    if reader is None or self_reader(entry):
        return {}
    return seen_reports(entry['reviewer_id'], reader)


def reviewer_verdict_why(entry, said):
    """Why add() refuses `entry` because of what its reviewer said, or None. `said` is
    seen_verdicts(). Every verdict queued lands (VERDICTS), so a reviewer whose final `Verdict:`
    line is SEND BACK refuses it: the Instead: line asks for a fresh review of the tip, never the
    coordinator's own reading over it. A reviewer whose transcript was found but whose final
    report has no `Verdict:` line refuses it too (docs/gate-landing-policy.md "Strict paths"): the Instead:
    line asks to resume that reviewer for its line. So does one whose transcript was found but
    cannot be read (UNREAD): fail closed, the Instead: line asks to retry or re-ask the reviewer.
    An id with no transcript is not checked. `entry` is well formed (check). Pure."""
    back = [rid for rid, (found, v, _) in sorted(said.items()) if found and v == SEND_BACK]
    mute = [rid for rid, (found, v, _) in sorted(said.items()) if found and v is None]
    unread = [rid for rid, (found, v, _) in sorted(said.items()) if found and v == UNREAD]
    if not back and not mute and not unread:
        return None
    parts = (['the reviewer %s said SEND BACK (its final Verdict: line)' % rid for rid in back]
             + ['the reviewer %s gave no final Verdict: line (its transcript)' % rid for rid in mute]
             + ['a transcript of the reviewer %s was found but cannot be read' % rid
                for rid in unread])
    head = '--verdict %s, but %s; nothing was queued\nInstead: ' % (entry['verdict'], '; '.join(parts))
    if back:
        return head + ('dispatch a fresh review of the tip %s (skill branch-review) and queue its '
                       'verdict: a disputed blocking claim is settled by a fresh strong-tier review, '
                       'not by the coordinator\'s own reading' % entry['tip'])
    if mute:
        return head + ('resume the reviewer %s (SendMessage to its id), ask for its final Verdict: '
                       'line, then queue again' % ', '.join(mute))
    return head + ('run verdict.py add again once the transcript can be read, or resume the '
                   'reviewer %s (SendMessage to its id) and ask for its final Verdict: line again'
                   % ', '.join(unread))


def said_lines(entry, said):
    """`reviewer <id> said <verdict> at <tip>`, one per id of `said` (seen_verdicts) whose final
    report has a `Verdict:` line, sorted by id. Pure."""
    return ['reviewer %s said %s at %s' % (rid, v, entry['tip'])
            for rid, (found, v, _) in sorted(said.items()) if found and v and v != UNREAD]


def report_head(word):
    """The pattern of a report line headed `<word>:` at the line start, markdown decoration
    allowed (`**Verdict:** LAND`, a list dash, backticks), case aside: the one rule for the
    `Verdict:`, `Checks:`, `Done-when:` and `Design:` lines of a reviewer's final report."""
    return re.compile(r'^[\s>*_#`-]*' + re.escape(word) + r'[*_`]*\s*:', re.IGNORECASE)


CHECKS_HEAD = report_head('checks')
DONE_HEAD = report_head('done-when')
DESIGN_HEAD = report_head('design')
CHECK_RE = re.compile(r'^([a-z][\w-]*)\s*[:=]?\s*(pass|fail|n/a)$', re.IGNORECASE)
STATUS_RE = re.compile(r'(?<![\w-])(met|unmet)(?![\w-])', re.IGNORECASE)
DESIGN_RE = re.compile(DESIGN_HEAD.pattern + r'[*_`\s]*(approve|revise)(?![\w-])', re.IGNORECASE)


def _last_line(head, text):
    """The last line of `text` that `head` matches, or None. Pure."""
    found = None
    for line in (text or '').splitlines():
        if head.match(line):
            found = line
    return found


def checks_of(text):
    """`{id: PASS | FAIL | N/A}` from the last `Checks:` line of `text`: its parts split on `,` or
    `;`, each `<id> <state>` (decoration aside); a part that is not one is left out, so its id
    counts as missing. None when `text` has no `Checks:` line. Pure."""
    line = _last_line(CHECKS_HEAD, text)
    if line is None:
        return None
    out = {}
    for part in re.split(r'[,;]', line[CHECKS_HEAD.match(line).end():]):
        m = CHECK_RE.match(part.strip(' \t*_`.'))
        if m:
            out[m.group(1).casefold()] = m.group(2).upper()
    return out


def unmet_lines(text):
    """The `Done-when:` lines of `text` whose status, the first MET or UNMET word after the item
    (`Done-when: <ID> (<x>) UNMET - <evidence>`), is UNMET, stripped: the evidence may name the
    word (`MET - was UNMET`) without counting. Pure."""
    out = []
    for line in (text or '').splitlines():
        head = DONE_HEAD.match(line)
        m = STATUS_RE.search(line, head.end()) if head else None
        if m and m.group(1).upper() == 'UNMET':
            out.append(line.strip())
    return out


def design_of(text):
    """APPROVE or REVISE from the last `Design:` line of `text`; None when there is none or it
    names neither. Pure."""
    line = _last_line(DESIGN_HEAD, text)
    m = DESIGN_RE.match(line) if line is not None else None
    return m.group(1).upper() if m else None


def _lacking(checks, ids):
    return [c for c in ids if c not in (checks or {})]


def checks_why(entry, said):
    """Why add() refuses `entry` because a reviewer's final report has no `Checks:` line, or one
    that lacks a CODE_CHECKS id, or None. `said` is seen_verdicts(); an id with no transcript, or
    one whose transcript cannot be read (reviewer_verdict_why refuses it first), is not checked.
    Pure."""
    parts, ids = [], []
    for rid, (found, v, report) in sorted(said.items()):
        if not found or v == UNREAD:
            continue
        checks = checks_of(report)
        if checks is None:
            parts.append('the reviewer %s gave no Checks: line (its final report)' % rid)
        elif _lacking(checks, CODE_CHECKS):
            parts.append('the reviewer %s\'s Checks: line lacks %s'
                         % (rid, ', '.join(_lacking(checks, CODE_CHECKS))))
        else:
            continue
        ids.append(rid)
    if not parts:
        return None
    return ('--verdict %s, but %s; nothing was queued\nInstead: resume the reviewer %s (SendMessage '
            'to its id) and ask it to give its full final lines again: each Done-when: line, the '
            'Checks: line giving each of %s as PASS, FAIL or N/A, and the Verdict: line (skill '
            'specialty-review "Report"), then queue again'
            % (entry['verdict'], '; '.join(parts), ', '.join(ids), ', '.join(CODE_CHECKS)))


def done_when_why(entry, said):
    """Why add() refuses a plain LAND whose reviewer reports a Done-when item UNMET (a
    `Done-when:` line saying UNMET, or `done-when FAIL` on its Checks: line), or None. `said` is
    seen_verdicts(). Pure."""
    if entry['verdict'] != 'LAND':
        return None
    parts = []
    for rid, (found, v, report) in sorted(said.items()):
        if not found or v == UNREAD:
            continue
        lines = unmet_lines(report)
        if lines:
            parts.append('the reviewer %s reports %s' % (rid, ' | '.join(l[:120] for l in lines)))
        elif (checks_of(report) or {}).get('done-when') == 'FAIL':
            parts.append('the reviewer %s gives done-when FAIL' % rid)
    if not parts:
        return None
    return ('--verdict LAND, but %s; nothing was queued\nInstead: queue it with --verdict "LAND WITH '
            'FOLLOW-UP" --follow-up <ID>, <ID> an open record for the unmet part (backlog.py add; '
            'skill branch-review, step 7)' % '; '.join(parts))


FOLLOW_UP_INSTEAD = ('Instead: file the follow-up (backlog.py add) and queue again with --follow-up '
                     '<its ID>')


def follow_up_why(entry, follow_ups):
    """Why add() refuses `entry`'s follow-ups, or None. `follow_ups`: `{id: (state, open)}` for
    each --follow-up given, `state` the record's `state:` in the store, or None when the store holds
    no record of that id (a closed or retired record has none), and `open` True when that state is
    one of kitlib.backlog.record.STATES (open, blocked, parked, pulled; the caller reads the store).
    Refused: LAND WITH FOLLOW-UP naming none; a plain LAND naming one; an id with no open record, or
    one of the records the branch carries (its landing closes them). Pure."""
    if entry['verdict'] == 'LAND':
        if not follow_ups:
            return None
        return ('--follow-up goes with --verdict "LAND WITH FOLLOW-UP", not LAND; nothing was '
                'queued\nInstead: drop --follow-up, or queue it with --verdict "LAND WITH FOLLOW-UP"')
    if not follow_ups:
        return ('--verdict LAND WITH FOLLOW-UP names no --follow-up; nothing was queued\n%s'
                % FOLLOW_UP_INSTEAD)
    parts = []
    for rid, (state, is_open) in sorted(follow_ups.items()):
        if rid in entry['records']:
            parts.append('--follow-up %s is a record this branch carries, which its landing closes'
                         % rid)
        elif not is_open:
            parts.append('--follow-up %s is no open record in the store (%s)'
                         % (rid, 'none of that id' if state is None else 'state: %s' % state))
    if not parts:
        return None
    return '%s; nothing was queued\n%s' % ('; '.join(parts), FOLLOW_UP_INSTEAD)


DESIGN_INSTEAD = ('Instead: run the design review: dispatch a fresh analyst with skill '
                  'specialty-review, design review (skill branch-review, A.2), and queue again with '
                  '--design-reviewer-id <its agent id>')


def design_why(entry, new, design_id, design_said):
    """Why add() refuses `entry` because the diff adds a new non-test source file or class (`new`,
    new_sources()) and no approved design review is named, or None. `design_id`: the
    --design-reviewer-id given, or None. `design_said`: seen_reports() for it, or None when the
    host has no reader (not checked). Refused: no design_id; one that is the session queueing it;
    with a reader, an id that is not an agent id, a transcript found that cannot be read, or a
    final report whose last `Design:` line is not APPROVE or whose Checks: line lacks a
    DESIGN_CHECKS id. An id with no transcript is not checked. Pure."""
    if not new:
        return None
    shown = ', '.join(new[:5]) + (' and %d more' % (len(new) - 5) if len(new) > 5 else '')
    head = 'the diff adds %s' % shown
    if not design_id or not design_id.strip():
        return '%s, and no --design-reviewer-id names its design review; nothing was queued\n%s' % (
            head, DESIGN_INSTEAD)
    ids = _reviewer_ids(design_id)
    session = entry['session'].strip().casefold()
    parts = ['the design reviewer %s is the session queueing it' % i
             for i in ids if i.casefold() == session]
    if design_said is not None:
        parts += ['the design reviewer %s is no agent id' % i
                  for i in ids if i not in design_said and i.casefold() != session]
        for rid, (found, v, report) in sorted(design_said.items()):
            if not found:
                continue
            if v == UNREAD:
                parts.append('a transcript of the design reviewer %s cannot be read' % rid)
                continue
            said = design_of(report)
            if said != 'APPROVE':
                parts.append('the design reviewer %s %s' % (
                    rid, 'said REVISE' if said else 'gave no Design: line'))
            lacking = _lacking(checks_of(report), DESIGN_CHECKS)
            if lacking:
                parts.append('the design reviewer %s\'s Checks: line lacks %s'
                             % (rid, ', '.join(lacking)))
    if not parts:
        return None
    return '%s, but %s; nothing was queued\n%s' % (head, '; '.join(parts), DESIGN_INSTEAD)


def _git_diff(cwd, args, base, tip):
    out, why = gitcmd.run_text(['-c', 'core.quotepath=off', 'diff', '--no-color', '--no-ext-diff',
                                '--src-prefix=a/', '--dst-prefix=b/']
                               + args + ['%s...%s' % (base, tip), '--'], cwd)
    if out is None:
        raise ve.VerdictError('git diff %s...%s: %s' % (base, tip, (why or '').strip()))
    return out


def _patch_path(row, side):
    """The path of a patch's `--- a/<path>` or `+++ b/<path>` line (`side` 'a/' or 'b/'), git's
    C-quoting undone and the tab git ends a path holding a space with dropped; None for /dev/null
    or any other shape."""
    name = gitbatch.unquote_path(row[4:].rstrip('\t'))
    return name[2:] if name.startswith(side) else None


def new_sources(cwd, rng, exts, is_test, types):
    """The new non-test source files and classes the range `rng` (`<base>..<tip>`) adds, sorted:
    `<path>` for a file the diff adds (status A of `git diff --name-status --find-renames
    <base>...<tip>`; a rename is not one) ending in one of `exts`, `<path>:<Name>` for a type an
    added line of such a file declares (`git diff --no-renames`) whose name no removed line of the
    diff declares (a moved class is not new). A path `is_test(path)` is true of is left out (the
    caller passes the repo module map's ModuleMap.is_test_source). `types`: `(suffix, regex)`
    pairs, the first whose suffix the path ends in (None: any) gives the regex whose group 2 is the
    type's name (kitlib.docs.codemap's). The prefixes are given, so diff.noprefix and the like
    change nothing. Git runs in `cwd`. VerdictError when git gives no answer."""
    base, _, tip = rng.partition('..')
    for rev in (base, tip):
        why = refs.bad_rev(rev)
        if why:
            raise ve.VerdictError('the range %s: %s' % (rng, why))

    def source(p):
        return p if p and p.endswith(tuple(exts)) and not is_test(p) else None

    def type_name(path, line):
        for suffix, rx in types:
            if suffix is None or path.endswith(suffix):
                m = rx.match(line)
                return m.group(2) if m else None
        return None
    fields = _git_diff(cwd, ['--name-status', '-z', '--find-renames'], base, tip).split('\0')
    files, i = set(), 0
    while i < len(fields) and fields[i]:
        status = fields[i]
        width = 3 if status[:1] in 'RC' else 2
        if status == 'A' and source(fields[i + 1]):
            files.add(fields[i + 1])
        i += width
    added, removed = set(), set()
    old = new = None
    hunk = False
    for row in _git_diff(cwd, ['--unified=0', '--no-renames'], base, tip).splitlines():
        if row.startswith('diff --git '):
            old = new = None
            hunk = False
        elif not hunk and row.startswith('--- '):
            old = source(_patch_path(row, 'a/'))
        elif not hunk and row.startswith('+++ '):
            new = source(_patch_path(row, 'b/'))
        elif row.startswith('@@'):
            hunk = True
        elif hunk and row.startswith('+') and new:
            name = type_name(new, row[1:])
            if name:
                added.add((new, name))
        elif hunk and row.startswith('-') and old:
            name = type_name(old, row[1:])
            if name:
                removed.add(name)
    return sorted(files | set('%s:%s' % (p, n) for p, n in added if n not in removed))


TIER_INSTEAD = ('Instead: dispatch a fresh analyst with a strong-tier model (hosts.json "tiers") '
                'to review the branch (skill branch-review), then queue its verdict')


def tier_why(entry, strict, tiers):
    """Why `entry`'s reviewer is too light for its branch, or None: the one tier rule add() (at
    queue time, `strict` False: strict comes from the diff, so the landing checks it) and
    kitlib.gate.land (Lander.review_refusal, at landing) share. A branch that is High risk or
    `strict` (changes a strict path) lands only on a review whose reviewer_model names a
    strong-tier model (land.strong_alias), whoever built it; a Low or Medium one off the strict
    paths has no tier rule. `tiers`: called with no arguments only when the rule applies, it gives
    land.tier_models()'s `(strong, weak, why)`; models it cannot give refuse (fail closed), and
    an exception it raises passes through (add() raises VerdictError for unread tiers).
    `entry` is well formed (check). Pure."""
    if not (strict or entry['risk'] == 'High'):
        return None
    from kitlib.gate import land          # land imports this module: a late import
    strong, weak, why = tiers()
    if strong is None:
        return 'the strong-tier models cannot be read: %s' % why
    model = entry['reviewer_model']
    if land.strong_alias(model, strong, weak) is not None:
        return None
    return ('the reviewer ran on %s, not a strong-tier model (%s, or a full model id naming it; '
            'hosts.json "tiers"): %s lands only on a strong-tier review, whoever built it (its '
            'author is %s)' % (model, ', '.join(sorted(strong - weak)) or 'none',
                               'a branch that changes a strict path' if strict
                               else 'a High-risk branch', entry['author']))


def _retry(fn, *args):
    """fn(*args), tried RETRIES times RETRY_SLEEP apart while it fails with PermissionError. Kept
    for the entry read (kitlib.fs.replace has no read) and drop()'s remove (a file already gone
    is False there, where kitlib.fs.replace.remove returns as on success)."""
    for i in range(RETRIES):
        try:
            return fn(*args)
        except PermissionError:          # Windows: another process holds the file a moment
            if i == RETRIES - 1:
                raise
            time.sleep(RETRY_SLEEP)


def _read_text(path):
    with open(path, encoding='utf-8') as f:
        return f.read()


def _load(path, branch):
    """`entry` read from `path`; None when there is no file. Raises VerdictError when the file
    cannot be read or is not a well-formed entry for `branch`."""
    try:
        text = _retry(_read_text, path)
    except FileNotFoundError:
        return None
    except OSError as e:
        raise ve.VerdictError('cannot read %s: %s' % (path, e))
    try:
        entry = json.loads(text)
    except ValueError as e:
        raise ve.VerdictError('%s is not a verdict entry: %s' % (path, e))
    why = ve.check(entry)
    if why:
        raise ve.VerdictError('%s is not a verdict entry: %s' % (path, why))
    if entry['branch'] != branch:
        raise OtherBranch('%s holds the entry of %s, not %s (names that differ only in case share '
                          'one file on Windows)' % (path, entry['branch'], branch))
    return entry


def _clear_stale(path):
    """Remove the lock file `path` when it was last written more than LOCK_STALE_S ago."""
    try:
        if time.time() - os.path.getmtime(path) > LOCK_STALE_S:
            os.remove(path)
    except OSError:                      # gone already, or its holder is removing it
        pass


@contextlib.contextmanager
def _locked(common_dir):
    """Hold the queue lock (the module docstring says how) for the `with` body."""
    folder = ve.queue_dir(common_dir)
    lock = os.path.join(folder, LOCK_NAME)
    try:
        os.makedirs(folder, exist_ok=True)
    except OSError as e:
        raise ve.VerdictError('cannot make %s: %s' % (folder, e))
    deadline = time.monotonic() + LOCK_WAIT_S
    while True:
        denied = None
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            _clear_stale(lock)
        except PermissionError as e:     # Windows: a lock file being removed, for a moment
            denied = e
        except OSError as e:
            raise ve.VerdictError('cannot make the queue lock %s: %s' % (lock, e))
        if time.monotonic() >= deadline:
            if denied is not None:
                raise ve.VerdictError('cannot make the queue lock %s in %.0f s: %s'
                                   % (lock, LOCK_WAIT_S, denied))
            raise ve.VerdictError('the verdict queue stayed locked for %.0f s (%s); it is removed by '
                               'the next call once %.0f s old' % (LOCK_WAIT_S, lock, LOCK_STALE_S))
        time.sleep(LOCK_POLL_S)
    try:
        try:
            os.write(fd, ('pid %d at %s\n' % (os.getpid(), utc())).encode('ascii'))
        except OSError:                  # the note is for a reader; the file is the lock
            pass
        finally:
            os.close(fd)
        yield
    finally:
        try:
            fsreplace.remove(lock, wait=REPLACE_WAIT, pause=RETRY_SLEEP)
        except OSError:                  # left to the stale-lock rule
            pass


def _write(path, entry):
    folder = os.path.dirname(path)
    try:
        os.makedirs(folder, exist_ok=True)
        tmp = os.path.join(folder, '.%s.%s.tmp' % (os.path.basename(path), secrets.token_hex(4)))
        try:
            with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
                f.write(json.dumps(entry, sort_keys=True, indent=1) + '\n')
            fsreplace.replace(tmp, path, wait=REPLACE_WAIT, pause=RETRY_SLEEP)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
    except OSError as e:
        raise ve.VerdictError('cannot write %s: %s' % (path, e))
    return path


def head_of(common_dir, branch):
    """`refs/heads/<branch>`'s commit; '' when there is no such branch. Raises VerdictError when
    git gives no answer."""
    sha, why = refs.ref_commit('refs/heads/%s' % branch, common_dir)
    if sha is None:
        raise ve.VerdictError('git cannot say where %s is: %s' % (branch, why))
    return sha


def add(common_dir, entry, now=None, self_review_max_lines=None, main=None, main_why=None,
        seen=None, said=None, follow_ups=None, new=None, design_id=None, design_said=None):
    """Queue `entry` (the GIVEN_KEYS; nothing else) for its branch, replacing an entry already
    queued for it: `(path, stored entry)`. The tip must be `refs/heads/<branch>` in the repository
    whose common dir is `common_dir`, else TipMismatch. A Medium or High-risk self-review is SelfReview
    (self_review_why); so is a self-read of a branch over `self_review_max_lines` changed lines, or
    one whose size cannot be told, when that cap ("landing".self_review_max_lines) is given
    (self_review_size_why; `main`: main as fetched, a commit, or None with `main_why`). `seen`:
    seen_models() for the entry; a model seen that is not its reviewer_model is ModelMismatch
    (model_mismatch_why). `said`: seen_verdicts() for the entry; a reviewer whose final report
    says SEND BACK, or has no `Verdict:` line, is ReviewerSaid (reviewer_verdict_why); so is one
    with no `Checks:` line or one lacking a CODE_CHECKS id (checks_why), and a plain LAND over a
    Done-when item it reports UNMET (done_when_why). `follow_ups`: `{id: state}` of each
    --follow-up (follow_up_why), None not checked. `new`: new_sources() of the range, None not
    checked; when it lists any, `design_id` and `design_said` (seen_reports() of it, None with no
    reader) must name an approved design review (design_why). Each refusal is ReviewerSaid. A High-risk
    verdict whose reviewer_model is not a strong-tier model is
    LightReview (tier_why); when it needs hosts.json "tiers" and they cannot be read, VerdictError
    (a configuration fault: nothing queued). Nothing seen or said is stored. `now`: a datetime for
    queued_utc."""
    why = ve.check(entry, stored=False)
    if why:
        raise ve.BadEntry(why)
    why = self_review_why(entry)
    if why:
        raise SelfReview(why)
    why = model_mismatch_why(entry, seen) if seen else None
    if why:
        raise ModelMismatch(why)
    for why in ((reviewer_verdict_why(entry, said) or checks_why(entry, said)
                 or done_when_why(entry, said)) if said else None,
                follow_up_why(entry, follow_ups) if follow_ups is not None else None,
                design_why(entry, new, design_id, design_said) if new is not None else None):
        if why:
            raise ReviewerSaid(why)
    from kitlib.gate import land          # land imports this module: a late import

    def tiers():
        got = land.tier_models()
        if got[0] is None:                # a configuration fault, not a light review
            raise ve.VerdictError('the kit configuration cannot be read: hosts.json "tiers": %s; '
                               'nothing was queued' % got[2])
        return got
    why = tier_why(entry, False, tiers)
    if why:
        raise LightReview('%s; nothing was queued\n%s' % (why, TIER_INSTEAD))
    branch, tip = entry['branch'], entry['tip']
    path = ve.entry_path(common_dir, branch)
    head = head_of(common_dir, branch)
    if not head:
        raise TipMismatch('there is no branch %s' % branch)
    if head != tip:
        raise TipMismatch('%s is at %s, not the tip given %s' % (branch, head, tip))
    why = self_review_size_why(entry, self_review_max_lines, common_dir, main, main_why)
    if why:
        raise SelfReview(why)
    stored = dict(entry, records=list(entry['records']), queued_utc=utc(now), state=ve.QUEUED,
                  last_result=None)
    with _locked(common_dir):
        _load(path, branch)              # an unreadable entry, or another branch's, is not replaced
        return _write(path, stored), stored


def carry(common_dir, root, branch, base, now=None, checker=None):
    """Move `branch`'s queued verdict to the branch's head when the head carries it
    (docs/gate-landing-policy.md "Carrying a verdict forward"): `(path, stored entry)`, with tip the head
    and `carried` the facts kitlib.gate.carry.check gave for the commit first reviewed (the
    entry's carried.from, else its tip), the head and `base` (main as fetched, a full hash).
    `root`: a path in the repository, where git runs. None when nothing is queued for `branch`.

    CarryRefused, nothing written, when the entry is marked failed, is already at the head, or the
    head does not carry it; TipMismatch when there is no such branch; VerdictError when the entry
    or the branch moved while the check ran (outside the queue lock: git may be slow). `checker`:
    the check to run (default kitlib.gate.carry.check); `now`: a datetime for carried.utc."""
    path = ve.entry_path(common_dir, branch)
    with _locked(common_dir):
        entry = _load(path, branch)
    if entry is None:
        return None
    if entry['state'] != ve.QUEUED:
        raise CarryRefused('the verdict of %s is marked %s (%s): review and queue it again'
                           % (branch, entry['state'], entry['last_result'] or 'no reason given'))
    head = head_of(common_dir, branch)
    if not head:
        raise TipMismatch('there is no branch %s' % branch)
    if head == entry['tip']:
        raise CarryRefused('the verdict of %s is queued at its head %s already: nothing to carry'
                           % (branch, head[:10]))
    reviewed = entry[ve.CARRIED]['from'] if ve.CARRIED in entry else entry['tip']
    facts, why = (checker or carrymod.check)(root, reviewed, head, base)
    if facts is None:
        raise CarryRefused('%s at %s does not carry the verdict given at %s: %s'
                           % (branch, head[:10], reviewed[:10], why))
    stored = dict(entry, tip=head, carried=dict(facts, utc=utc(now)))
    why = ve.check(stored)
    if not why and (stored[ve.CARRIED]['from'] != reviewed or stored[ve.CARRIED]['main'] != base):
        why = 'carried.from or carried.main is not the commit asked about'
    if why:
        raise CarryRefused('the carry check gave facts that are not a carried record: %s' % why)
    with _locked(common_dir):
        if _load(path, branch) != entry:
            raise ve.VerdictError('the verdict of %s changed while its carry was checked: run carry '
                               'again' % branch)
        if head_of(common_dir, branch) != head:
            raise ve.VerdictError('%s moved while its carry was checked: run carry again' % branch)
        return _write(path, stored), stored


def scan(common_dir):
    """`(entries, bad)`: every well-formed entry, oldest first, and `[(path, why)]` for each file
    in the queue folder that is not one, a temp file a dead write left among them; each why ends
    with how to remove that file (tools/verdict.py drop only where drop removes it)."""
    folder = ve.queue_dir(common_dir)
    if not os.path.isdir(folder):
        return [], []
    with _locked(common_dir):
        try:
            names = sorted(os.listdir(folder))
        except OSError as e:
            raise ve.VerdictError('cannot list %s: %s' % (folder, e))
        return _scan(folder, names)


def _scan(folder, names):
    good, bad = [], []
    for name in names:
        path = os.path.join(folder, name)
        if name == LOCK_NAME:                # ours: scan holds it
            continue
        if name.startswith('.') and name.endswith('.tmp'):
            bad.append((path, 'a temp file a write that died left behind; %s' % BY_HAND))
            continue
        branch = ve.branch_of(name)
        if branch is None:
            bad.append((path, 'not a name a verdict entry file has; %s' % BY_HAND))
            continue
        try:
            entry = _load(path, branch)
        except OtherBranch as e:             # drop refuses it
            bad.append((path, '%s; %s' % (e, BY_HAND)))
            continue
        except ve.VerdictError as e:
            bad.append((path, '%s; remove it with tools/verdict.py drop %s, or by hand'
                        % (e, branch)))
            continue
        if entry is not None:
            good.append(entry)
    return order(good), bad


def entries(common_dir):
    """Every queued entry, oldest first. Raises VerdictError naming each file in the queue folder
    that is not a well-formed entry: a landing takes nothing from a queue it cannot read whole."""
    good, bad = scan(common_dir)
    if bad:
        raise ve.VerdictError('the verdict queue holds files that are not entries: %s'
                           % ' | '.join('%s: %s' % pb for pb in bad))
    return good


def order(items):
    """Oldest first; the branch name settles a tie. A malformed item sorts as text, never raises."""
    def key(e):
        e = e if isinstance(e, dict) else {}
        return str(e.get('queued_utc') or ''), str(e.get('branch') or '')
    return sorted(items, key=key)


def drop(common_dir, branch, tip=None):
    """Remove `branch`'s entry: True when one was removed, False when none is queued (or, with
    `tip`, the entry queued is for another tip and is kept). Without `tip` an unreadable entry
    file is removed too. OtherBranch, and nothing removed, when the file holds another branch's
    entry or, unreadable, has another branch's name in another case."""
    path = ve.entry_path(common_dir, branch)
    with _locked(common_dir):
        try:
            entry = _load(path, branch)
        except OtherBranch:
            raise
        except ve.VerdictError:
            if tip is not None:
                raise
            try:
                names = os.listdir(os.path.dirname(path))
            except OSError as e:
                raise ve.VerdictError('cannot list %s: %s' % (os.path.dirname(path), e))
            if os.path.basename(path) not in names:
                raise OtherBranch('%s is not named for %s (names that differ only in case share '
                                  'one file on Windows): nothing removed' % (path, branch))
        else:
            if entry is None or (tip is not None and entry['tip'] != tip):
                return False
        try:
            _retry(os.remove, path)
        except FileNotFoundError:
            return False
        except OSError as e:
            raise ve.VerdictError('cannot remove %s: %s' % (path, e))
        return True


def mark_failed(common_dir, branch, why, tip=None):
    """Mark `branch`'s entry failed with `why` (kept in last_result): True when marked, False when
    none is queued (or, with `tip`, the entry queued is for another tip and is left as is). A
    failed entry is never in a batch; a re-add queues it again."""
    if not isinstance(why, str) or not why.strip():
        raise ve.VerdictError('say why the landing failed')
    path = ve.entry_path(common_dir, branch)
    with _locked(common_dir):
        entry = _load(path, branch)
        if entry is None or (tip is not None and entry['tip'] != tip):
            return False
        entry.update(state=ve.FAILED, last_result=why.strip())
        _write(path, entry)
        return True


def expire(common_dir, items, main):
    """Drop each of the entries `items` that expired(): `[(entry, why)]` for those dropped. Git runs
    outside the queue lock; each drop is drop(tip=the entry's tip), so an entry re-added at another
    tip meanwhile is kept, and one that cannot be dropped (VerdictError) is kept too."""
    gone = []
    for e in items:
        why = ve.expired(common_dir, e, main)
        if not why:
            continue
        try:
            if drop(common_dir, e['branch'], tip=e['tip']):
                gone.append((e, why))
        except ve.VerdictError:
            continue
    return gone
