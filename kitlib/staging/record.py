"""kitlib.staging.record: the phase record of one attempt to move the main branch
(docs/modules/staging-design.md "The phase record").

One folder per attempt, `<gate log base>/promote/<stamp>-<sha10>/` (stamp_now), holding
`promote.json` (FILE), never in the daily full run's results folder: a promotion's green is never
a daily green.
The record (SCHEMA 1):

  schema      1
  via         "promotion" (the diff-selected run, kitlib.staging.promote)
  sha         the commit the attempt moves main to (the staging tip it tested)
  base        `<remote>/<main>` the attempt tested against (its full hash), or None
  phase       one of LIVE (not finished: the next run recovers it) or ENDS
  started, updated   local times, ISO seconds
  pid         the process that wrote the record last
  log_dir     the gate's log folder, or None
  why         the reason of an end other than done, or a note
  tries       the pushes tried
  announced   True once the hub job has pushed its hub item (ANNOUNCE)
  history     [[phase, time], ...], oldest first
  culprit     a red attempt's culprit hunt (kitlib.staging.culprit after_red): {verdict, red,
              pid} while it runs (verdict HUNTING; `stopped` once the hub job's look found that
              run gone), then {end, verdict, merge, by, suspects, probes, red[, mode]}
  revert      for a culprit found: {branch, base, commit, note} (the local branch prepared), or
              {why[, base]} when none could be

Phases: tested (green, its record written) -> pushing -> pushed (main is S on the remote) -> done
(the main checkout's local main synced as far as it safely can). The ends: red (a step failed),
behind (main moved since the test: tested again next time), refused (the pre-push guard refused
the push), diverged (main holds commits the staging branch lacks), failed (the push could not be
made or verified in PUSH_TRIES tries, or the tested record could not be written), error (the gate
refused to run on the tip). HOLDS never test the same tip again (failed among them, so a tip
whose push or record keeps failing is not gated again on every hub tick): a new staging tip is a
fresh attempt, and a refused one waits for the owner's action as well. ANNOUNCE get one hub item
each (Attempt.event: a red one whose culprit's revert branch was prepared is word `revert`).

The records are written atomically (kitlib.fs.atomic) and only by the run holding the promote
lock (kitlib.staging.promote.lock_for), the hub job's `announced` mark included.
"""
import datetime
import json
import os

from kitlib.fs import atomic

SCHEMA = 1
FILE = 'promote.json'
DIR = 'promote'
LIVE = ('tested', 'pushing', 'pushed')
ENDS = ('done', 'red', 'behind', 'refused', 'diverged', 'failed', 'error')
HOLDS = ('red', 'refused', 'diverged', 'failed', 'error')
ANNOUNCE = ('red', 'refused', 'diverged', 'failed', 'error')
HUNTING = 'hunting'     # a culprit hunt's verdict while it runs (kitlib.staging.culprit)


class RecordError(Exception):
    """A record that could not be written, or a phase that is not one."""


def now_text():
    return datetime.datetime.now().isoformat(timespec='seconds')


def stamp_now():
    """The folder stamp: local time to the microsecond, so two attempts of one second still sort
    in the order they started (attempts() reads them by folder name)."""
    return datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')


class Attempt(object):
    """One attempt's record at `path` (its `rec`, a dict)."""

    def __init__(self, path, rec):
        self.path, self.rec = path, rec

    @property
    def phase(self):
        return self.rec.get('phase')

    @property
    def sha(self):
        return self.rec.get('sha')

    @property
    def folder(self):
        return os.path.dirname(self.path)

    def live(self):
        return self.phase in LIVE

    def event(self):
        """`(word, detail id)` of this attempt's hub item: ('revert', the culprit's sha10) when a
        revert branch was prepared, else (its phase, its sha10)."""
        if (self.rec.get('revert') or {}).get('branch'):
            return 'revert', ((self.rec.get('culprit') or {}).get('merge') or '?')[:10]
        return self.phase, (self.sha or '?')[:10]

    def set(self, phase, why=None, **more):
        """Move to `phase` (LIVE or ENDS) and write the record; `why` replaces the reason when
        given; `more` sets other keys. RecordError for a phase that is not one, or a failed
        write."""
        if phase not in LIVE + ENDS:
            raise RecordError('no phase %r: the phases are %s' % (phase, ', '.join(LIVE + ENDS)))
        when = now_text()
        self.rec.update(more)
        self.rec.update(phase=phase, updated=when, pid=os.getpid())
        if why is not None:
            self.rec['why'] = why
        self.rec.setdefault('history', []).append([phase, when])
        self.write()
        return self

    def write(self):
        try:
            atomic.write_text(self.path, json.dumps(self.rec, indent=1, sort_keys=True) + '\n',
                              makedirs=True)
        except OSError as e:
            raise RecordError('the phase record %s could not be written: %s' % (self.path, e))


def start(folder, sha, base, phase, via='promotion', why=None, log_dir=None, stamp=None):
    """A new attempt's record under `folder`, written at `phase`."""
    path = os.path.join(folder, '%s-%s' % (stamp or stamp_now(), sha[:10]), FILE)
    rec = {'schema': SCHEMA, 'via': via, 'sha': sha, 'base': base, 'phase': None,
           'started': now_text(), 'updated': None, 'pid': None, 'log_dir': log_dir,
           'why': why, 'tries': 0, 'announced': False, 'history': []}
    return Attempt(path, rec).set(phase)


def attempts(folder):
    """Every readable attempt under `folder`, oldest first (by folder name)."""
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return []
    found = []
    for name in names:
        path = os.path.join(folder, name, FILE)
        try:
            with open(path, encoding='utf-8') as f:
                rec = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(rec, dict) and rec.get('schema') == SCHEMA:
            found.append(Attempt(path, rec))
    return found


def unfinished(found):
    """The latest attempt of `found` (attempts()) that is not finished, or None."""
    return next((a for a in reversed(found) if a.live()), None)


def held(found, sha):
    """The latest attempt of `found` at `sha` that ended in one of HOLDS, or None: that tip is
    never tested again."""
    return next((a for a in reversed(found) if a.sha == sha and a.phase in HOLDS), None)


def unannounced(found):
    """The attempts of `found` that ended in one of ANNOUNCE and have no hub item yet."""
    return [a for a in found if a.phase in ANNOUNCE and not a.rec.get('announced')]


def hunting(a):
    """Whether attempt `a`'s culprit hunt is marked as running (kitlib.staging.culprit HUNTING)."""
    return (a.rec.get('culprit') or {}).get('verdict') == HUNTING


def line(a):
    """The status line of attempt `a`, with its hunt's verdict and revert branch when it has
    them."""
    r = a.rec
    hunt, rev = r.get('culprit') or {}, r.get('revert') or {}
    more = ''
    if hunt.get('verdict'):
        more += '; hunt: %s' % hunt['verdict']
    if rev.get('branch'):
        more += '; revert branch %s' % rev['branch']
    elif rev.get('why'):
        more += '; no revert: %s' % rev['why']
    return 'promote   %s %s %s at %s%s%s' % (
        (r.get('phase') or '?').upper(), (r.get('sha') or '?')[:10],
        'on main %s' % (r.get('base') or '?')[:10], r.get('updated') or r.get('started') or '?',
        ' - %s' % r['why'] if r.get('why') else '', more)
