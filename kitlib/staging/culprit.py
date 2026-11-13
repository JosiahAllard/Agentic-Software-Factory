"""kitlib.staging.culprit: after a promotion ends red, the landing that turned the staging tip red,
and its revert prepared for the coordinator (docs/modules/staging-design.md "Culprit and revert").

  landings(root, git, base, tip)          ([Landing], None) or (None, why): every first-parent
                                          commit of base..tip (kitlib.staging.changes
                                          first_parent), oldest first, with the paths it changed
  failures(top, results, red, specs, since)   S's failing tests, each with its step
  failing_paths(top, found)               the test files (and runner scope folders) they live in
  suspects(specs, red, lands, failing, read)   the landings that may have turned the red red
  cmd_suspects(argv, values, folder, tests, lands, timeout)   the repository's own suspects
                                          ("staging".suspects_cmd), or why they cannot be had
  hunt(lands, base, tip, sus, probe, make, deadline, clock)   the hunt (below): a dict
  after_red(...)                          the whole, in the promotion's tree, for promote._test

Suspects. A landing is a candidate when it selects one of the red steps by the paths it changed
(kitlib.gate.steps.selects on its own paths, `always` and `manual` set aside, content read in the
tree at S); a step with no globs and no content, or one the gate at M lacks, takes every landing.
A candidate that changed a failing test's file, or a path under a failing gradle or dotnet
test's scope, is a hit; the suspects are the hits, else the candidates. "One suspect" is one
across every red step together (bisect.bisectable names them: `gate`, `windows` and an `infra`
red are never hunted).

A repository with `"staging".suspects_cmd` (read at M, as `<remote>/<main>` writes it) names its
own instead (cmd_suspects): its `suspect <sha40>` lines, exit 0. A command that cannot start,
exits non-zero, outlives its timeout or names a commit that is not a landing of M..S gives the
kit's suspects above, and a line says why.

The hunt, in this order, every probe a kitlib.dailyfull.bisect Prober's (failing tests only when
S's logs name them, else the whole red steps; a whole-step probe runs that commit's own gate in
the daily form, kitlib.dailyfull.plan, as the daily bisect does: a change-selecting cmd such as
`--select` against M would select nothing at M or at a revert, and probe green there):

  1. S. Green: FLAKE, and nothing more. Inconclusive: the hunt stops there.
  2. One suspect: its revert on S (kitlib.staging.revert make) is probed. Green: the culprit,
     confirmed by revert. Red, a conflict, an empty revert or git failing: on to 3.
     Inconclusive: the hunt stops.
  3. A bisect (bisect.search) over M and every first-parent commit of M..S, at most
     bisect.DEFAULT_PROBES probes in it (S's, already run, counts as one): a landing found is the
     culprit by bisect; M itself red is on-main (no landing turned it red: no revert).

HUNT_BUDGET_S bounds the whole hunt, the machine-lock waits included: each probe's slot waits at
most the time left (the lock's wait_s), and a hunt with none left ends `inconclusive (budget)`.
A detached run waits inside the promote lock, so the hub job sees it busy and starts nothing; a
hand-run `tools/promote.py --force` runs the hunt in the foreground, within the same budget.

The tree is put back at S afterwards. For a culprit, after_red fetches, reads the staging tip T
again and prepares `revert/<merge10>` on T (revert.prepare: local only, never pushed); the
coordinator reviews and lands it. Nothing here prints; the caller says the lines.
"""
import collections
import json
import os
import posixpath
import re
import subprocess
import time

from kitlib import dailyfull as D
from kitlib.config.types import ConfigError
from kitlib.dailyfull import bisect as B
from kitlib.gate import detect
from kitlib.gate import reports
from kitlib.gate import rerun
from kitlib.gate import steps as S
from kitlib.git import cmd as gitcmd
from kitlib.records import inflight
from kitlib.records import place
from kitlib.staging import changes as C
from kitlib.staging import record as R
from kitlib.staging import revert as V

HUNT_BUDGET_S = 4 * 3600        # the whole hunt, its slot waits included
SLUG = 'promote-hunt'           # the probes' booking
HUNTING, STOPPED = R.HUNTING, 'stopped'     # the record's culprit verdict while a run hunts, and
#                                             once look() found that run gone (promote.look)
FOUND, FLAKE, ON_MAIN, CAPPED, INCONCLUSIVE, BUDGET = (
    'found', 'flake', 'on-main', 'capped', 'inconclusive', 'budget')     # a hunt's `end`
BY_REVERT, BY_BISECT = 'confirmed by revert', 'by bisect'
SUSPECTS_TIMEOUT_S = 600        # suspects_cmd's run, or the hunt's budget left when that is less
TESTS_FILE, LANDINGS_FILE, SUSPECTS_LOG = (
    'suspects-tests.json', 'suspects-landings.json', 'suspects.log')    # in the attempt's folder
FROM_CMD, FROM_KIT = 'suspects_cmd', 'kit'          # the record's culprit.suspects_from
_SUSPECT = re.compile(r'^suspect ([0-9a-f]{40})$')
_PREFIX = 'daily: bisect'

Landing = collections.namedtuple('Landing', 'sha paths')


def landings(root, git, base, tip):
    """`([Landing(sha, paths)], None)`: every first-parent commit of `base..tip`, oldest first,
    each with the paths it changed against its first parent (renames as a delete and an add), or
    `(None, why)`."""
    found, why = C.first_parent(root, git, base, tip)
    if found is None:
        return None, why
    out = []
    for sha, _t in found:
        rc, text, err = git(root, 'diff', '--name-only', '--no-renames', '-z', sha + '^1', sha)
        if rc != 0:
            return None, 'git diff %s^1 %s failed: %s' % (sha[:10], sha[:10], err or 'exit %d' % rc)
        out.append(Landing(sha, [p for p in text.split('\0') if p.strip()]))
    return out, None


def _rel(top, spec):
    """The step's folder in the tree, '/'-separated ('' the top), or None outside it."""
    cwd = (spec or {}).get('cwd')
    return reports.inside(top, os.path.join(top, cwd)) if cwd else ''


def failures(top, results, red, specs, since):
    """S's failing tests in the red steps `red`, in step order: rerun.cover_step's failures
    ({id, cls, cwd[, runner, scope]}: `cwd` the step's folder, '/'-separated, '' the top), each
    with its `step`. `results`: the red gate's step results; `since`: its start, epoch seconds.
    A step whose folder is outside `top` gives none."""
    out = []
    for res in results:
        if res.get('name') not in red:
            continue
        rel = _rel(top, specs.get(res['name']))
        if rel is None:
            continue
        for f in rerun.cover_step(dict(res), rel, top, since).get('failures') or []:
            out.append(dict(f, step=res['name'], cwd=rel))
    return out


def failing_paths(top, found):
    """The repository paths the failures `found` (failures()) live in: a unittest class's file
    (kitlib.gate.rerun scope_of, in its step's folder), and a gradle or dotnet failure's scope
    folder ending '/'."""
    out = set()
    for f in found:
        if f.get('runner'):
            if f.get('scope'):
                out.add(f['scope'].strip('/') + '/')
            continue
        rel = f['cwd']
        file = rerun.scope_of(os.path.join(top, *rel.split('/')) if rel else top, f['cls'])
        if file.endswith('.py'):
            out.add(posixpath.join(rel, file) if rel else file)
    return out


def _selects(spec, paths, read):
    if spec is None or not (spec.get('globs') or spec.get('content')):
        return True
    return S.selects(dict(spec, always=False, manual=False), paths, read) is not None


def _hits(paths, failing):
    return any(p in failing or any(f.endswith('/') and p.startswith(f) for f in failing)
               for p in paths)


def suspects(specs, red, lands, failing, read):
    """The suspects among `lands` (the module docstring): `specs` {step name: its config},
    `red` the red step names, `failing` failing_paths(), `read(rel)` a file's text at S."""
    candidates = [x for x in lands if any(_selects(specs.get(n), x.paths, read) for n in red)]
    hits = [x for x in candidates if _hits(x.paths, failing)]
    return hits or candidates


def _write_json(path, value):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(value, f, indent=1, sort_keys=True)


def cmd_suspects(argv, values, folder, tests, lands, timeout, run=None):
    """`([sha], None)`: the suspects the repository's `argv` ("staging".suspects_cmd) names, or
    `(None, why)` when it gives none to trust. `values`: the step placeholders (steps.mapping of
    the promotion's tree at S, which is also its working folder); `tests` the tests file's
    content; `lands` the landings of M..S. Writes TESTS_FILE and LANDINGS_FILE ({tests_file},
    {landings_file}) and the command's output, SUSPECTS_LOG, into `folder`. The command runs with
    no input in a job of its own (inflight.run), its whole tree ended after `timeout` s. Its
    stdout lines `suspect <sha40>` are the suspects, in order; a `suspect` line of another form
    gives none to trust, other lines are ignored; exit 0 with none is no suspect. `run`: a
    stand-in for inflight.run."""
    tests_file, landings_file = os.path.join(folder, TESTS_FILE), os.path.join(folder, LANDINGS_FILE)
    try:
        _write_json(tests_file, tests)
        _write_json(landings_file, [{'sha': x.sha, 'paths': list(x.paths)} for x in lands])
    except OSError as e:
        return None, 'its files could not be written in %s: %s' % (folder, e)
    cmd = S.substitute(argv, dict(values, tests_file=tests_file, landings_file=landings_file))
    try:
        rc, out, err = (run or inflight.run)(cmd, values['root'], timeout)
    except OSError as e:
        return None, 'could not start (%s)' % e
    except subprocess.TimeoutExpired:
        return None, 'was still running after %d s and was ended' % timeout
    text, errs = gitcmd.text(out or b''), gitcmd.text(err or b'')
    try:
        with open(os.path.join(folder, SUSPECTS_LOG), 'w', encoding='utf-8') as f:
            f.write('$ %s\nexit %d\n--- stdout\n%s\n--- stderr\n%s\n' % (
                ' '.join(cmd), rc, text, errs))
    except OSError:
        pass                        # the log is evidence only
    if rc != 0:
        return None, 'exited %d (%s)' % (rc, os.path.join(folder, SUSPECTS_LOG))
    lines = [line.strip() for line in text.splitlines()]
    said = [line for line in lines if line.split(None, 1)[:1] == ['suspect']]
    bad = [line for line in said if not _SUSPECT.match(line)]
    if bad:
        return None, 'printed a suspect line that is not a 40-hex sha: %r' % bad[0][:60]
    named = [_SUSPECT.match(line).group(1) for line in said]
    known = {x.sha for x in lands}
    foreign = [sha for sha in named if sha not in known]
    if foreign:
        return None, 'named %s, which is not a landing of M..S' % foreign[0][:10]
    return list(dict.fromkeys(named)), None


def hunt(lands, base, tip, sus, probe, make, deadline, clock, max_probes=B.DEFAULT_PROBES,
         budget=HUNT_BUDGET_S):
    """The hunt (the module docstring). `probe(sha, wait_s) -> (verdict, failed, why)` (a
    bisect.Prober's, its slot waiting at most `wait_s`); `make(merge, onto) -> (Made, why)`
    (revert.make). Returns {end, verdict, merge, by, suspects, probes, made}: `probes` lists
    {sha, what, verdict, red[, why]} in the order they ran; `made` is the culprit's revert on
    `tip` (revert.Made) or None."""
    probes, seen, spent = [], {}, []

    def run(sha, what):
        if sha in seen:
            return seen[sha]
        left = deadline - clock()
        if left <= 0:
            spent.append(sha)
            return None, [], 'the hunt\'s %d s budget is spent' % budget
        got = probe(sha, left)
        if got[0] is None and clock() >= deadline:
            spent.append(sha)
        probes.append(dict({'sha': sha, 'what': what, 'verdict': got[0] or 'inconclusive',
                            'red': got[1]}, **({'why': got[2]} if got[2] else {})))
        seen[sha] = got
        return got

    out = {'end': None, 'verdict': None, 'merge': None, 'by': None,
           'suspects': [x.sha for x in sus], 'probes': probes, 'made': None}

    def stop(why):
        if spent:
            return dict(out, end=BUDGET, verdict='inconclusive (budget): %s' % why)
        return dict(out, end=INCONCLUSIVE, verdict='inconclusive: %s' % why)

    verdict, _failed, why = run(tip, 'S')
    if verdict is None:
        return stop(why)
    if verdict == B.GREEN:
        return dict(out, end=FLAKE, verdict=B.FLAKE)
    tried = None
    if len(sus) == 1:
        made, why = make(sus[0].sha, tip)
        if made is not None and made.commit:
            verdict, _failed, why = run(made.commit, 'revert of %s' % sus[0].sha[:10])
            if verdict is None:
                return stop(why)
            if verdict == B.GREEN:
                return dict(out, end=FOUND, merge=sus[0].sha, by=BY_REVERT, made=made,
                            verdict='culprit %s (%s)' % (sus[0].sha[:10], BY_REVERT))
            tried = sus[0].sha
    found = B.search([base] + [x.sha for x in lands], lambda sha: run(sha, 'bisect'),
                     max_probes)
    if found['culprit'] is None:
        if found['verdict'].startswith('inconclusive'):
            return stop(found['verdict'][len('inconclusive: '):])
        return dict(out, end=CAPPED if found['bad'] else INCONCLUSIVE, verdict=found['verdict'])
    if found['culprit'] == base:
        return dict(out, end=ON_MAIN, verdict='on-main: main (%s) is red too: no landing of '
                    'M..S turned it red' % base[:10])
    merge = found['culprit']
    made, _why = make(merge, tip)      # None: after_red's prepare makes it again, and says why
    note = ' (its revert at S probed red too: another landing may be red as well)' if (
        tried == merge) else ''
    return dict(out, end=FOUND, merge=merge, by=BY_BISECT, made=made,
                verdict='culprit %s (%s)%s' % (merge[:10], BY_BISECT, note))


def say_as_hunt(say):
    """`say`, with the bisect's own `daily: bisect` prefix read as `promote: hunt`."""
    def said(line):
        say('promote: hunt' + line[len(_PREFIX):] if line.startswith(_PREFIX) else line)
    return said


def bound(prober):
    """`probe(sha, wait_s)` of a Prober: its slot waits at most `wait_s` (never now-or-never)."""
    def probe(sha, wait_s):
        kw = {k: v for k, v in prober.book_kw.items() if k != 'no_wait'}
        prober.book_kw = dict(kw, wait_s=max(1, int(wait_s)))
        return prober(sha)
    return probe


def red_steps(out):
    """The red step names of the gate outcome `out` a hunt can probe (bisect.bisectable)."""
    results = out.facts.get('results') or []
    return B.bisectable({'red': [r['name'] for r in results if r.get('status') == 'FAIL']})


def after_red(ctx, attempt, out, say, clock=time.time, budget=HUNT_BUDGET_S, make=V.make):
    """The hunt after the promotion of `attempt` (record.Attempt, ended red) failed with the gate
    outcome `out`, and the revert branch of its culprit: the lines to say. `ctx`: {main, cfg (the
    main checkout's), cfg_base (M's), staging, wt (the promotion's tree), tip, base, book
    (the gate's booking), git, since (the gate's start, epoch seconds), log_dir}. Writes the
    attempt's `culprit` (and, for a culprit, `revert`); nothing when no red step can be hunted
    (red_steps). RecordError passes through."""
    red = red_steps(out)
    if not red:
        return []
    attempt.rec['culprit'] = {'verdict': HUNTING, 'pid': os.getpid(), 'red': red}
    attempt.write()
    found, mode = _hunt(dict(ctx, log_dir=ctx.get('log_dir') or attempt.folder,
                             folder=attempt.folder), red,
                        out.facts.get('results') or [], say, clock, clock() + budget, budget,
                        make)
    lines = ['promote: hunt: %s (%d probe(s))' % (found['verdict'], len(found['probes']))]
    rec = {k: found[k] for k in ('end', 'verdict', 'merge', 'by', 'suspects', 'probes')}
    rec.update((k, found[k]) for k in ('suspects_from', 'suspects_why') if found.get(k))
    attempt.rec['culprit'] = dict(rec, red=red, **({'mode': mode} if mode else {}))
    if found.get('back'):
        lines.append('promote: hunt: %s' % found['back'])
    if found['merge']:
        rev, line = _prepare(ctx, found)
        attempt.rec['revert'] = rev
        lines.append(line)
    attempt.write()
    return lines


def _hunt(ctx, red, results, say, clock, deadline, budget, make):
    """`(the hunt dict, the probes' mode)` in the promotion's tree, put back at S after."""
    main, wt, tip, base, git = ctx['main'], ctx['wt'], ctx['tip'], ctx['base'], ctx['git']
    lands, why = landings(main, git, base, tip)
    if lands is None:
        return _none('inconclusive: %s' % why), None
    try:
        gate = detect.effective(ctx['cfg_base'], wt, base=base).gate
    except (ConfigError, detect.DetectError, S.GateConfigError) as e:
        return _none('inconclusive: the gate at %s: %s' % (base[:10], e)), None
    specs = {s['name']: s for s in gate.get('steps') or []}
    found_f = failures(wt, results, red, specs, ctx.get('since'))
    failing = failing_paths(wt, found_f)
    sus, origin, why = _suspects_of(ctx, red, found_f, failing, lands, specs, say, clock,
                                    deadline)
    prober = B.Prober(wt, red, gate['kind'], place.repo_name(ctx['cfg_base'], main), SLUG,
                      ctx['log_dir'], say_as_hunt(say), ctx.get('book') or {}, git,
                      D.plan, steps=results)
    probe, chain = bound(prober), {x.sha for x in lands} | {base}

    def probe_at(sha, wait_s):
        try:
            return probe(sha, wait_s)
        finally:
            if sha not in chain:    # a revert commit no ref reaches: leave it quietly, for S
                git(wt, 'switch', '-q', '--detach', tip)
    try:
        found = hunt(lands, base, tip, sus, probe_at,
                     lambda merge, onto: make(wt, merge, onto), deadline, clock, budget=budget)
    finally:
        rc, _, err = git(wt, 'switch', '-q', '--detach', tip)
    found['suspects_from'] = origin
    if why:
        found['suspects_why'] = why
    if rc != 0:
        found['back'] = 'could not put %s back at %s: %s' % (wt, tip[:10], err or 'exit %d' % rc)
    return found, (prober.mode if found['probes'] else None)


def _suspects_of(ctx, red, found, failing, lands, specs, say, clock, deadline):
    """`([Landing], suspects_from, why)`: the suspects_cmd M writes names, else the kit's
    (`why`: why the command's were not used; None when none is set)."""
    wt, base = ctx['wt'], ctx['base']
    kit = suspects(specs, red, lands, failing, S.working_tree_reader(wt))
    argv = ctx['cfg_base'].written('staging', 'suspects_cmd')
    if not argv:
        return kit, FROM_KIT, None
    keep = ('step', 'id', 'cls', 'runner', 'scope')
    tests = {'base': base, 'tip': ctx['tip'], 'red': list(red), 'failing': sorted(failing),
             'failures': [{k: f[k] for k in keep if f.get(k) is not None} for f in found]}
    timeout = max(1, int(min(SUSPECTS_TIMEOUT_S, deadline - clock())))
    shas, why = cmd_suspects(argv, S.mapping(wt, base, ctx['cfg_base'].tools_dir), ctx['folder'],
                             tests, lands, timeout)
    if shas is None:
        say('promote: hunt: suspects_cmd %s; the kit\'s suspects are used' % why)
        return kit, FROM_KIT, 'suspects_cmd ' + why
    by = {x.sha: x for x in lands}
    say('promote: hunt: suspects_cmd named %d suspect(s)' % len(shas))
    return [by[sha] for sha in shas], FROM_CMD, None


def _none(verdict):
    return {'end': INCONCLUSIVE, 'verdict': verdict, 'merge': None, 'by': None, 'suspects': [],
            'probes': [], 'made': None}


def _prepare(ctx, found):
    """`(the record's revert, the line)`: revert/<merge10> on the staging tip as it is now."""
    main, git, merge, tip = ctx['main'], ctx['git'], found['merge'], ctx['tip']
    staging = '%s/%s' % (ctx['cfg'].remote, ctx['staging'])
    why = D.fetch(main, ctx['cfg'].remote, git)
    rc, now, err = git(main, 'rev-parse', '--verify', '-q', 'refs/remotes/%s' % staging)
    if why or rc != 0 or not now:
        why = why or '%s cannot be read: %s' % (staging, err or 'exit %d' % rc)
        return {'why': why}, 'promote: NO REVERT - %s' % why
    made = found['made'] if now == tip else None
    got, why = V.prepare(main, merge, now, made=made)
    if got is None:
        return {'why': why, 'base': now}, 'promote: NO REVERT - %s' % why
    rec = {'branch': got.branch, 'base': now, 'commit': got.commit, 'note': got.note}
    return rec, ('promote: REVERT PREPARED - local branch %s (%s) reverts %s on %s at %s%s; the '
                 'coordinator reviews and lands it, nothing was pushed' % (
                     got.branch, got.commit[:10], merge[:10], staging, now[:10],
                     ' (%s)' % got.note if got.note else ''))
