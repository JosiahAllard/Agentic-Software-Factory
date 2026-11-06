"""kitlib.dailyfull.bisect: after a red daily full run, the first-parent commit that turned its red
steps red (docs/daily-full.md "Bisect").

G is the commit of the latest earlier green run that is an ancestor of the red run's commit S. The
commits searched are `git rev-list --first-parent G..S`. Probes go in this order:

- First, S itself is probed. If it is green again, the verdict is FLAKE and nothing more runs.
- Then a binary search runs to the first red commit: about 1 + log2(n) probes for n commits.

At most `max_probes` probes run. If the cap is reached first, the verdict names the range the
culprit lies in.

Each probe switches the daily worktree to the commit (the caller holds the tree's lock
throughout) and plans that commit's own gate (`plan`, daily argv). Then it runs one of two ways,
logging to `<log dir>/bisect-<n>-<sha10>`:

- Failing tests only, when every red step's log at S names its failing tests and nothing in it
  is uncovered (kitlib.gate.rerun cover_step: kit_tests and unittest logs). In one machine-lock
  slot of that commit's gate kind (rerun.run_classes, the targeted rerun's) it runs `setup` when
  the gate has one, then each failing class with `python -m unittest -v <class>`, in the mode it
  failed in, for at most rerun.class_timeout seconds. The probe is red when one of S's failing
  ids, or a fixture of its class or module, fails or errors again, or when the class cannot be
  loaded though the tree defines it. A test the commit does not have counts as green (judge).
- The whole red steps otherwise (and setup), through kitlib.gate.runner.gated, in a slot of the
  run's own kind. Red when a red step FAILs, green when every red step it has passed or warned.

When the failing tests pass alone at S, one whole-step probe runs at S before a flake is said; if
that is red, the rest of the search runs whole steps (`mode` says so). A commit whose gate lacks a
red step counts as green for that step. Anything else (a switch, config or plan failure, a lock
refusal, a stop, a failed setup, a red step or class that did not run or ran past its time
limit) stops the bisect as inconclusive.
Afterwards the worktree is put back at S.

Nothing here prints or exits; the caller says the lines.
"""
import os
import re

from kitlib.config import load as load_config
from kitlib.config.types import ConfigError
from kitlib.gate import detect
from kitlib.gate import rerun
from kitlib.gate import runner
from kitlib.gate import steps as S

FLAKE = 'did not reproduce: possible flake'
RED, GREEN = 'red', 'green'
# Result names that are not steps a probe can run: the gate's own and the window report's.
NOT_STEPS = ('gate', 'windows')
DEFAULT_PROBES = 5
WHOLE = 'whole step: '
NARROWED_GREEN = WHOLE + 'narrowed green at S'
SETUPS = ('setUpClass', 'setUpModule')     # the fixtures whose error means no test ran


def bisectable(result):
    """The red step names a probe can run again. Empty when the run's red is `infra` (an error,
    or nothing ran)."""
    red = result.get('red') or []
    if 'infra' in red or result.get('error'):
        return []
    return [n for n in red if n not in NOT_STEPS]


def last_green(results, sha, wt, git):
    """The commit of the latest green run among `results` (oldest first) that is a strict ancestor
    of `sha`, or None."""
    for r in reversed(results):
        g = r.get('sha')
        if r.get('verdict') != 'green' or not g or g == sha:
            continue
        rc, _, _ = git(wt, 'merge-base', '--is-ancestor', g, sha)
        if rc == 0:
            return g
    return None


def targets(red, steps):
    """`(covers, mode)`. covers: {red step name: its kitlib.gate.rerun cover_step entry} when every
    red step's log names its failing tests and nothing in it is uncovered, else None. mode: what
    a probe runs, `tests: <n> class(es)` or `whole step: <why>`."""
    if steps is None:
        return None, WHOLE + 'no step logs were given'
    by = {s.get('name'): s for s in steps}
    covers = {}
    for n in red:
        if n not in by:
            return None, WHOLE + '%s has no step entry' % n
        entry = rerun.cover_step(dict(by[n]), '')
        why = entry.get('uncovered') or (
            [] if entry.get('failures') else ['%s: no failing test parsed' % n])
        if why:
            return None, WHOLE + why[0]
        covers[n] = entry
    return covers, 'tests: %d class(es)' % len(rerun.classes_of({'steps': list(covers.values())}))


def present(top, cls):
    """Whether the tree at `top` defines the test class (or module) `cls`: its file is there and,
    for a class, holds `class <Name>`."""
    # Not rerun.ABSENT: that reads the log of a class that would not load (an import error, a
    # missing attribute), which a commit whose test module is broken writes as well as one that
    # lacks the class. This reads the commit's source, so a defined class that fails to load
    # stays red. A class bound by import or assignment, not `class`, reads as absent here.
    rel = rerun.scope_of(top, cls)
    path = os.path.join(top, *rel.split('/'))
    if not rel.endswith('.py') or not os.path.isfile(path):
        return False
    rest = cls[len(rel[:-len('.py')]) + 1:]
    if not rest:
        return True
    try:
        with open(path, encoding='utf-8', errors='replace') as f:
            text = f.read()
    except OSError:
        return False
    return re.search(r'^\s*class\s+%s\b' % re.escape(rest.split('.')[0]), text, re.M) is not None


def judge(text, rc, ids, defined):
    """RED, GREEN or None (inconclusive) of one class run's log `text` (rc None: it did not start
    or ran past its timeout) for S's failing `ids`. RED: one of them shows FAIL or ERROR; or a
    set-up fixture (setUpClass, setUpModule) of a class or module one of them is in does, so its
    tests never ran (a tearDownClass or tearDownModule error, after they ran, does not count); or the class could not be loaded and `defined()` (the commit defines
    it). GREEN: the class could not be loaded and the commit does not define it, or the run
    reached its `Ran N` line (the ids passed, or the commit lacks them). None: anything else (no
    `Ran` line: a crash)."""
    if rc is None:
        return None
    lines = (text or '').splitlines()
    want, not_loaded = set(ids), False
    for line in lines:
        m = rerun.TEST_LINE.match(line)
        if not m:
            continue
        paren = m.group(3)
        if rerun.unloaded(paren):
            not_loaded = True
            continue
        tid = rerun.test_id(m.group(2), paren)[0]
        if tid in want:
            return RED
        owner, method = tid.rsplit('.', 1)
        if method in SETUPS and any(w.startswith(owner + '.') for w in want):
            return RED              # a tear-down's error comes after its tests ran: not this
    if not_loaded:
        return RED if defined() else GREEN
    if any(rerun.RAN.match(line) for line in lines):
        return GREEN
    return None


class Prober(object):
    """Runs the red steps `red` at one commit of the daily worktree `wt`. `plan(cfg, wt, sha)`:
    the daily steps of that commit's gate (kitlib.dailyfull.plan). `steps`: the red run's step
    entries ({name, status, reasons, log}), whose logs choose the probe (targets; `mode`)."""

    def __init__(self, wt, red, kind, name, slug, log_dir, say, book_kw, git, plan, steps=None):
        self.wt, self.red, self.kind, self.name, self.slug = wt, list(red), kind, name, slug
        self.log_dir, self.say, self.book_kw, self.git, self.plan = log_dir, say, book_kw, git, plan
        self.count = 0
        self.covers, self.mode = targets(self.red, steps)

    def __call__(self, sha):
        """`(RED | GREEN | None, failed red step names, why)`; None is inconclusive."""
        self.count += 1
        rc, _, err = self.git(self.wt, 'switch', '--detach', sha)
        if rc != 0:
            return None, [], 'could not put %s at %s: %s' % (self.wt, sha[:10], err or 'exit %d' % rc)
        try:
            cfg = load_config(self.wt)
            chosen = self.plan(cfg, self.wt, sha)
        except (ConfigError, S.GateConfigError) as e:
            return None, [], 'the gate at %s: %s' % (sha[:10], e)
        names = [s.name for s in chosen if s.name in self.red]
        if not names:
            return GREEN, [], None
        chosen = [s for s in chosen if s.name in self.red or s.name == 'setup']
        logs = os.path.join(self.log_dir, 'bisect-%d-%s' % (self.count, sha[:10]))
        os.makedirs(logs, exist_ok=True)
        if self.covers is None:
            return self._whole(sha, cfg, chosen, names, logs)
        verdict, failed, why = self._tests(sha, cfg, chosen, names, logs)
        if verdict != GREEN or self.count > 1:
            return verdict, failed, why
        # The first probe is S: its failing tests passed alone there. One whole-step probe at S
        # before a flake is said; the rest of the search, if any, runs whole steps too.
        self.covers, self.mode = None, NARROWED_GREEN
        self.say('daily: bisect: the failing tests passed alone at %s; the whole step runs there'
                 % sha[:10])
        os.makedirs(logs + '-step', exist_ok=True)
        return self._whole(sha, cfg, chosen, names, logs + '-step')

    def _tests(self, sha, cfg, chosen, names, logs):
        """The probe of S's failing tests at `sha` (the module docstring)."""
        by = {s.name: s for s in chosen}
        runs = []                           # (red step name, class entry)
        for n in names:
            rel = rerun.cwd_rel(self.wt, by[n].cwd)
            if rel is None:
                return None, [], 'red step %s at %s runs outside the tree' % (n, sha[:10])
            entry = dict(self.covers[n],
                         failures=[dict(f, cwd=rel) for f in self.covers[n]['failures']])
            runs += [(n, c) for c in rerun.classes_of({'steps': [entry]})]
        setup = [s for s in chosen if s.name == 'setup']
        try:
            gate = detect.effective(cfg, self.wt).gate
        except (ConfigError, detect.DetectError, S.GateConfigError) as e:
            return None, [], 'the gate at %s: %s' % (sha[:10], e)
        # A setup Step carries no check (steps.build), so this loads nothing today; it is the
        # rerun's load all the same, which refuses a SystemExit as runner.gated does.
        checks, options, why = rerun.load_checks(cfg, setup)
        if why:
            return None, [], 'the gate at %s: %s' % (sha[:10], why)
        timeout = rerun.class_timeout(self.wt, cfg)
        self.say('daily: bisect probe %d at %s: %s, its failing tests only (%d class(es))'
                 % (self.count, sha[:10], ', '.join(names), len(runs)))
        failed, idle = [], []

        def each(i, c, rc, text, log, slot):
            top = os.path.join(self.wt, *c['cwd'].split('/')) if c['cwd'] else self.wt
            v = judge(text, rc, c['ids'], lambda: present(top, c['cls']))
            if v == RED and runs[i][0] not in failed:
                failed.append(runs[i][0])
            elif v is None:
                idle.append(c['cls'] + (' (still running after %s s)' % timeout
                                        if rerun.TIMED_OUT in (text or '') else ''))

        def broke(out):
            return any(r['status'] == 'FAIL' for r in out['setup'])
        ctx = {'root': self.wt, 'base': sha, 'merged': False, 'changed': [], 'options': options}
        out, refused = rerun.run_classes(
            gate['kind'], self.name, self.slug, self.book_kw, runner.tested_tree(self.wt),
            self.wt, [c for _n, c in runs], gate, ctx, logs, sha, self.say, setup=setup,
            checks=checks, timeout=timeout, each=each,
            code=lambda o: 1 if failed or idle or broke(o) else 0)
        if refused is not None:
            return None, [], 'the machine lock refused a probe: %s' % '; '.join(refused.lines)
        if out['stopped'] is not None:
            return None, [], 'probe %d at %s was stopped' % (self.count, sha[:10])
        if broke(out):
            return None, [], 'setup failed at %s' % sha[:10]
        if failed:
            return RED, failed, None
        if idle:
            return None, [], 'class(es) %s did not run at %s' % (', '.join(idle), sha[:10])
        return GREEN, [], None

    def _whole(self, sha, cfg, chosen, names, logs):
        """The probe of the whole red steps (and setup) at `sha`."""
        self.say('daily: bisect probe %d at %s: %s' % (self.count, sha[:10], ', '.join(names)))
        results, refused = runner.gated(cfg, self.wt, sha, [], chosen, self.kind, self.name,
                                        self.slug, logs, self.say, self.book_kw)
        if refused is not None:
            return None, [], 'the machine lock refused a probe: %s' % '; '.join(refused.lines)
        status = {r['name']: r['status'] for r in results}
        failed = [n for n in names if status.get(n) == 'FAIL']
        if failed:
            return RED, failed, None
        if status.get('setup') == 'FAIL':
            return None, [], 'setup failed at %s' % sha[:10]
        idle = [n for n in names if status.get(n) not in ('PASS', 'WARN')]
        if idle:
            return None, [], 'red step(s) %s did not run at %s' % (', '.join(idle), sha[:10])
        return GREEN, [], None


def search(commits, probe, max_probes):
    """Bisects `commits` (oldest first, the last is the red run's commit; the commit before the
    first is green) with `probe(sha) -> (verdict, failed, why)`, at most `max_probes` probes.
    Returns {bad, culprit, probes, verdict}: `probes` lists {sha, verdict, red}; `bad` is the
    oldest commit seen red; `culprit` is set only when the commit before it was seen green."""
    probes = []
    good, bad = -1, None

    def run(i):
        verdict, failed, why = probe(commits[i])
        probes.append({'sha': commits[i], 'verdict': verdict or 'inconclusive', 'red': failed,
                       **({'why': why} if why else {})})
        return verdict, why

    out = {'bad': None, 'culprit': None, 'probes': probes}
    if max_probes < 1 or not commits:
        out['verdict'] = 'not run: no probe allowed' if commits else 'not run: no commit to probe'
        return out
    verdict, why = run(len(commits) - 1)
    if verdict is None:
        out['verdict'] = 'inconclusive: %s' % why
        return out
    if verdict == GREEN:
        out['verdict'] = FLAKE
        return out
    bad = len(commits) - 1
    while bad - good > 1:
        if len(probes) >= max_probes:
            out['bad'] = commits[bad]
            out['verdict'] = ('probe cap %d reached: the culprit is one of the %d commit(s) '
                              'after %s up to %s' % (max_probes, bad - good,
                                                     commits[good][:10] if good >= 0 else 'the green run',
                                                     commits[bad][:10]))
            return out
        mid = (good + bad) // 2
        verdict, why = run(mid)
        if verdict is None:
            out['bad'] = commits[bad]
            out['verdict'] = 'inconclusive: %s' % why
            return out
        if verdict == RED:
            bad = mid
        else:
            good = mid
    out['bad'] = out['culprit'] = commits[bad]
    out['verdict'] = 'culprit %s' % commits[bad][:10]
    return out


def bisect(result, out_results, prober, wt, git, max_probes):
    """The bisect dict for the red `result` (its `sha` is S), or None with no bisect to run
    (no bisectable red step). `out_results`: every earlier result, oldest first. The worktree is
    put back at S afterwards."""
    red = bisectable(result)
    if not red:
        return None
    sha = result['sha']
    good = last_green(out_results, sha, wt, git)
    if good is None:
        return {'good': None, 'bad': sha, 'culprit': None, 'probes': [],
                'verdict': 'not run: no earlier green run is an ancestor of %s' % sha[:10]}
    rc, text, err = git(wt, 'rev-list', '--first-parent', '--reverse', '%s..%s' % (good, sha))
    if rc != 0:
        return {'good': good, 'bad': sha, 'culprit': None, 'probes': [],
                'verdict': 'inconclusive: git rev-list failed: %s' % (err or 'exit %d' % rc)}
    commits = text.split()
    try:
        found = search(commits, prober, max_probes)
    finally:
        rc, _, err = git(wt, 'switch', '--detach', sha)
    if rc != 0:
        found['back'] = 'could not put %s back at %s: %s' % (wt, sha[:10], err or 'exit %d' % rc)
    return dict(found, good=good)
