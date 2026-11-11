"""A verdict-queue entry: where it is, whether it is well formed, and whether it has expired. The
queue itself, and every write to it, is landing.verdicts'; this module only reads.

Where: `<git common dir>/kit-verdicts/<branch>.json`, the branch name percent-encoded
(urllib.parse.quote with no safe characters: '/' is '%2F', '%' is '%25'), so the file name gives
the branch back and holds no character Windows refuses. The common dir is shared by every work
tree of the repository and never tracked.

An entry (every key always present):

  branch          the branch name, as `git branch` names it (kit/x)
  tip             the full commit hash reviewed; add() checks it is `refs/heads/<branch>` now
  verdict         LAND | LAND WITH FOLLOW-UP
  risk            Low | Medium | High
  reviewer        the reviewer's agent type
  reviewer_model  the model the reviewer ran on, one for every id reviewer_id lists (a second,
                  architecture reviewer runs on the same model: skill branch-review);
                  tools/verdict.py add checks it against each reviewer's transcript where the
                  host has a reader (model_mismatch_why)
  reviewer_id     the reviewer's agent or session id, or several (`a,b`); tools/verdict.py add
                  reads each agent id's final `Verdict:` line where the host has a reader
                  (reviewer_verdict_why)
  author          the change's author: commit author and agent type (`Kit Test / worker`); it
                  picks no reviewer model: a High-risk or strict branch needs a strong-tier
                  reviewer whoever built it, and a Low or Medium branch off the strict paths has
                  no tier rule (tier_why: add() and landing.land, Lander.review_refusal)
  range           the range reviewed, `<base>..<tip>`; its right side is the tip or a prefix of
                  it (7 or more hex digits), so the review covered the commit queued
  session         the coordinator session that queued it
  records         [backlog record ids] the branch carries
  queued_utc      set by add(): YYYY-MM-DDTHH:MM:SSZ
  state           queued | failed (mark_failed)
  last_result     None, or why it was marked failed

and, only in an entry carry() moved (a re-add drops it):

  carried         {from, merges, main, diff, utc}: the verdict was given at `from` (the commit the
                  range ends at), and tip carries it over `merges` (main merges, oldest first, the
                  last the tip) as landing.carry checked against `main` at `utc`; `diff` is the
                  sha256 of the branch's normalized patch. A record of the check, never proof: a
                  landing checks it again (docs/gate-landing-policy.md "Carrying a verdict forward").

add(), carry() and mark_failed() are landing.verdicts'. Nothing here prints or writes.
"""
import os
import re
import urllib.parse

from kitlib.git import refs

DIR_NAME = 'kit-verdicts'
SUFFIX = '.json'
VERDICTS = ('LAND', 'LAND WITH FOLLOW-UP')
RISKS = ('Low', 'Medium', 'High')
QUEUED, FAILED = 'queued', 'failed'
STATES = (QUEUED, FAILED)
# What the coordinator states; add() sets the rest.
GIVEN_KEYS = ('branch', 'tip', 'verdict', 'risk', 'reviewer', 'reviewer_model', 'reviewer_id',
              'author', 'range', 'session', 'records')
SET_KEYS = ('queued_utc', 'state', 'last_result')
KEYS = GIVEN_KEYS + SET_KEYS
CARRIED = 'carried'         # set only by carry(): an entry may lack it
CARRIED_KEYS = ('from', 'merges', 'main', 'diff', 'utc')
DIGEST_RE = re.compile(r'^[0-9a-f]{64}$')
TEXT_KEYS = ('reviewer', 'reviewer_model', 'reviewer_id', 'author', 'session')
UTC_RE = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$')
RANGE_TIP_RE = re.compile(r'^[0-9a-f]{7,64}$')
# DOS device names: a file called CON.json or nul.x.json is the device, not a file.
DEVICES = frozenset(['CON', 'PRN', 'AUX', 'NUL'] + ['COM%d' % i for i in range(1, 10)]
                    + ['LPT%d' % i for i in range(1, 10)])


class VerdictError(Exception):
    """An entry is malformed, its file cannot be read or written, or git gave no answer."""


class BadEntry(VerdictError):
    """The entry (or branch name) given is malformed: nothing was read or written."""


def _branch_why(branch):
    if not isinstance(branch, str) or not branch.strip():
        return 'the branch is a non-empty name, not %r' % (branch,)
    why = refs.bad_rev(branch)
    if why:
        return 'the branch %s' % why
    if branch != branch.strip() or any(c.isspace() for c in branch):
        return 'the branch name %r holds white space' % branch
    if branch.startswith('refs/'):
        return 'name the branch as git branch does (kit/x), not the ref %s' % branch
    return None


def file_name(branch):
    """The queue file name for `branch`: percent-encoded, then .json."""
    why = _branch_why(branch)
    if why:
        raise BadEntry(why)
    name = urllib.parse.quote(branch, safe='') + SUFFIX
    if name.split('.')[0].upper() in DEVICES:
        raise BadEntry('the branch %s would be the file %s, a Windows device name' % (branch, name))
    return name


def branch_of(name):
    """The branch a queue file name holds, or None when `name` is not one file_name() makes."""
    if not name.endswith(SUFFIX) or name.startswith('.'):
        return None
    branch = urllib.parse.unquote(name[:-len(SUFFIX)])
    try:
        return branch if file_name(branch) == name else None
    except VerdictError:
        return None


def queue_dir(common_dir):
    return os.path.join(common_dir, DIR_NAME)


def entry_path(common_dir, branch):
    return os.path.join(queue_dir(common_dir), file_name(branch))


def reviewer_types(text):
    """The agent types a verdict's `reviewer` names, casefolded: each part split on '/', ',' or
    ';', stripped, a `plugin:` prefix dropped, empty ones left out (`pm-kit:` names none). Pure."""
    return set(p.strip().rpartition(':')[2].strip().casefold()
               for p in re.split(r'[/,;]', text)) - {''}


def check(entry, stored=True):
    """Why `entry` is not a well-formed entry, or None. `stored`: it must carry add()'s keys too
    (an entry read back; it may hold `carried`); False: only the keys the coordinator states (an
    entry given to add)."""
    if not isinstance(entry, dict):
        return 'an entry is a JSON object, not %s' % type(entry).__name__
    keys = KEYS if stored else GIVEN_KEYS
    missing = [k for k in keys if k not in entry]
    if missing:
        return 'the entry lacks %s' % ', '.join(missing)
    extra = sorted(k for k in entry if k not in keys and not (stored and k == CARRIED))
    if extra:
        return 'the entry has keys it may not set: %s' % ', '.join(extra)
    why = _branch_why(entry['branch'])
    if why:
        return why
    tip = entry['tip']
    if not refs.is_sha(tip):
        return 'tip is a full commit hash, not %r' % (tip,)
    reviewed = tip
    if CARRIED in entry:
        why = _carried_why(entry[CARRIED], tip)
        if why:
            return why
        reviewed = entry[CARRIED]['from']
    if entry['verdict'] not in VERDICTS:
        return 'verdict is one of %s, not %r' % (' | '.join(VERDICTS), entry['verdict'])
    if entry['risk'] not in RISKS:
        return 'risk is one of %s, not %r' % (' | '.join(RISKS), entry['risk'])
    for k in TEXT_KEYS:
        v = entry[k]
        if not isinstance(v, str) or not v.strip() or '\n' in v or '\r' in v:
            return '%s is a non-empty one-line text, not %r' % (k, v)
    if not reviewer_types(entry['reviewer']):
        # Fail closed: with no type, verdicts.is_author could not tell the reviewer from the author.
        return ('reviewer names no agent type: it is the reviewer\'s agent type '
                '(pm-kit:analyst), not %r' % entry['reviewer'])
    rng = entry['range']
    base, sep, right = rng.partition('..') if isinstance(rng, str) else ('', '', '')
    if (not sep or not base or base.startswith('-') or any(c.isspace() for c in rng)
            or not RANGE_TIP_RE.match(right) or not reviewed.startswith(right)):
        return ('range is <base>..<tip> with the %s (or 7+ digits of it) on the right, not %r'
                % ('reviewed commit the verdict was carried from' if reviewed != tip
                   else 'queued tip', rng))
    recs = entry['records']
    if not isinstance(recs, list) or any(not isinstance(r, str) or not r or any(c.isspace() for c in r)
                                         for r in recs):
        return 'records is a list of record ids, not %r' % (recs,)
    if stored:
        if not isinstance(entry['queued_utc'], str) or not UTC_RE.match(entry['queued_utc']):
            return 'queued_utc is YYYY-MM-DDTHH:MM:SSZ, not %r' % (entry['queued_utc'],)
        if entry['state'] not in STATES:
            return 'state is one of %s, not %r' % (' | '.join(STATES), entry['state'])
        if entry['last_result'] is not None and not isinstance(entry['last_result'], str):
            return 'last_result is null or a text, not %r' % (entry['last_result'],)
    return None


def _carried_why(c, tip):
    """Why `c` is not the `carried` record of an entry queued at `tip`, or None."""
    if not isinstance(c, dict) or sorted(c) != sorted(CARRIED_KEYS):
        return 'carried is an object of %s, not %r' % (', '.join(CARRIED_KEYS), c)
    for k in ('from', 'main'):
        if not refs.is_sha(c[k]):
            return 'carried.%s is a full commit hash, not %r' % (k, c[k])
    if c['from'] == tip:
        return 'carried.from is the commit reviewed, not the tip %s it was carried to' % tip[:10]
    merges = c['merges']
    if (not isinstance(merges, list) or not merges or any(not refs.is_sha(m) for m in merges)
            or merges[-1] != tip):
        return 'carried.merges is the main merges carried over, the last the tip, not %r' % (merges,)
    if not isinstance(c['diff'], str) or not DIGEST_RE.match(c['diff']):
        return 'carried.diff is a sha256 in hex, not %r' % (c['diff'],)
    if not isinstance(c['utc'], str) or not UTC_RE.match(c['utc']):
        return 'carried.utc is YYYY-MM-DDTHH:MM:SSZ, not %r' % (c['utc'],)
    return None


def expired(common_dir, entry, main):
    """Why `entry` waits to land no more, or None: `landed` when its tip is an ancestor of `main`
    (main as fetched, a commit), `superseded` when it is not and `refs/heads/<branch>` is gone.
    None, the entry kept, whenever that cannot be told: no `main`, or git gives no answer."""
    if not main:
        return None
    landed, _ = refs.is_ancestor(entry['tip'], main, common_dir)
    if landed:
        return 'landed: %s is in main %s' % (entry['tip'][:10], main[:10])
    if landed is None:
        return None
    head, _ = refs.ref_commit('refs/heads/%s' % entry['branch'], common_dir)
    if head == '':
        return 'superseded: there is no branch %s' % entry['branch']
    return None
