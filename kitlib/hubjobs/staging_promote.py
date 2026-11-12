"""kitlib.hubjobs.staging_promote: the hub server's trigger of a staging branch's promotion
(docs/modules/staging-design.md "The trigger"; docs/hub-push.md "Timed jobs").

  make(sources) -> jobs.Job | None   the job; None when launcher.repos names no repository
  PromoteJob(roots, ...)             its run(ctx): one pass

Every EVERY_S the job looks at each repository launcher.repos names (kitlib.staging.promote.look:
no fetch, the promote lock probed and let go again). Staging off there: nothing, and nothing
said. A promotion run holding the promote lock: nothing. Owed (an attempt a run left unfinished,
or "staging".promote_after_landings untested landings, or the oldest of them
promote_after_hours old, at a tip that has not ended red, refused, diverged or error): one
detached `tools/promote.py -C <repo>` (kitlib.lock.detach), which takes the promote lock itself
before anything else, so two starts never run twice.

An attempt that ended red, refused, diverged, failed or error gets one job push to the PM, word
`promote-<end>` (EVENTS), detail `<repo>:<sha10>`, and is marked announced in its record; the PM
raises it with the owner. A newer push of this job replaces one the hub still owes
(push.PushWorker.job_event: these words are not in JOB_EACH), a known limit.

Outcomes: a start is a success whose note names it; nothing to start is a skip; a repository
whose config or git cannot be read, or a start that failed, is a note on a success when
another repository started, else a failure (health shows it).
"""
import os
import sys

from kitlib.hubsite import jobs

NAME = 'staging-promote'
EVERY_S = 600
EVENTS = ('red', 'refused', 'diverged', 'failed', 'error')     # promote-<end>


class PromoteFailed(Exception):
    """A repository could not be looked at, or its run could not be started."""


def start_run(root):
    """Start `tools/promote.py -C root` detached: `(pid, None)` or `(None, why)`."""
    from kitlib import kitroot
    from kitlib.lock import detach
    argv = detach.tool_command(sys.executable, kitroot.tool('promote'), ['-C', root])
    why = detach.missing(argv)
    if why:
        return None, why
    return detach.create(argv, root, dict(os.environ))


def _look(root, announce):
    from kitlib import config as kconfig
    from kitlib.staging import promote
    try:
        cfg = kconfig.load(root)
    except Exception as e:          # noqa: BLE001 - one repository's config never stops the pass
        return 'error', 'its .kit.json: %s: %s' % (type(e).__name__, e)
    return promote.look(root, cfg, announce)


class PromoteJob(object):
    """`roots()` -> {name: main checkout}; `look(root, announce)` and `start(root)` are
    _look's and start_run's signatures (tests pass their own)."""

    def __init__(self, roots, look=_look, start=start_run):
        self.roots, self.look, self.start = roots, look, start

    def __call__(self, ctx):
        started, notes, bad = [], [], []
        for name, root in sorted(self.roots().items()):
            if ctx.stop.is_set():
                break

            def announce(a, name=name):
                ctx.event('promote-%s' % a.phase, '%s:%s' % (name, (a.sha or '?')[:10]))
            verdict, note = self.look(root, announce)
            if verdict == 'start':
                pid, why = self.start(root)
                if pid is None:
                    bad.append('%s: the run did not start: %s' % (name, why))
                else:
                    started.append('%s (%s, pid %s)' % (name, note, pid))
            elif verdict == 'error':
                bad.append('%s: %s' % (name, note))
            elif verdict in ('busy', 'idle'):
                notes.append('%s: %s' % (name, note))
        if bad and not started:
            raise PromoteFailed('; '.join(bad))
        if started:
            return 'started ' + ', '.join(started) + ('; ' + '; '.join(bad) if bad else '')
        return jobs.Skip('; '.join(notes) or 'staging is off in every repository')


def repo_roots(s):
    return dict(s.repos)


def make(sources):
    from kitlib.launcher import settings as lsettings
    s = lsettings.load(getattr(sources, 'home', None))
    if not s.repos:
        return None
    return jobs.Job(NAME, EVERY_S, PromoteJob(lambda: repo_roots(s)))
