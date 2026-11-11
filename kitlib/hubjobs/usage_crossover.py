"""kitlib.hubjobs.usage_crossover: the hub moves a session to another account as its account nears
the weekly limit (docs/launcher.md "Usage crossover", docs/hub-push.md "Timed jobs").

What a session's next step is, and the states a crossing goes through, are decided in
kitlib.hubjobs._crossover_decide (its docstring: the trigger, the states of a crossing, the
holds, the target, the PM, unlisted sessions); this module reads the files and processes it
is given, acts on the step and keeps the state file.

  make(sources) -> jobs.Job | None   the job; None when launcher.state_dir is not set or the host
                                     has no watcher readers
  claims(settings, now) -> {key}     the sessions the job is moving now (kitlib.hubjobs.coord_restart
                                     holds them); LaunchError for a bad state file
  CrossoverJob(load, ...)            its run(ctx)

launcher.usage_crossover, read again at each run, sets what the job may do. `notify` (the default,
and unset): decide, log, run `launch.py handoff --what-if` and `stop --dry-run` on each change and
push `would-cross`; nothing is stopped or started. `normal`: crossings that ask first. `full`:
those and the stop-first path for a stalled session.

One acting step per run across all keys, stop-stalled first, then stop-old, finish, start, ask,
would-cross; the rest wait for the next run. Every key is decided on each run.

Pushes (ctx.event, push.JOB_EACH: owed once per detail, each with its own text): crossing,
would-cross, crossed, cross-cleared, cross-failed, stalled, no-ready, no-checkin, no-room, pm-limit,
unmanaged. A word goes out once per change, kept in the state file as `told`.

State in <launcher.state_dir>/usage-crossover.json: {crossings: {key: {phase, phase_at, ...}},
starts: {key: [epoch]}, asks: {key: [epoch]}, landed: {account: epoch of the last start there},
told: {key: mark}, windows: {name: reset of the window already told}, readings: {account: text},
cleared: {key: {account, reset}}, weeks: {account: [[updated epoch, week %]] of the last
BURN_SPAN_S}, pm_old: {name, pid, created} of the PM's old process}. The same file is the
claim: coord-restart holds a key whose crossing moved phase less than CLAIM_TTL_S ago. A bad file
is a failure, here and in coord-restart, never read as empty.

usage-crossover.log, same folder: one line per reading change and one per changed decision.
"""
import collections
import datetime
import os
import subprocess
import sys
import time

from kitlib import pmstart, pmwatch
from kitlib.config import user as kuser
from kitlib.hubjobs import _crossover_decide as D
from kitlib.hubjobs import coord_restart as C
from kitlib.hubjobs import session_watch
from kitlib.hubsite import jobs
from kitlib.launcher import accounts as A
from kitlib.launcher import handoff, lookup, pipes, relaunch, state
from kitlib.launcher import stop as lstop
from kitlib.launcher import unlisted as UL
from kitlib.launcher.settings import LaunchError

NAME = 'usage-crossover'
EVERY_S = 300
FIRST_S = 60                   # before coord-restart's 120: a claim is written before it reads one
CLAIM_TTL_S = 2 * 3600         # after the last phase change
STATE_FILE = 'usage-crossover.json'
LOG = 'usage-crossover.log'
PM_FROM = 'pm'                 # the `from` of its hand-off records (push.PM_KEY)
HANDOFF_FOLDER = 'usage-crossover'   # <agent temp>/usage-crossover/<key>/: an unlisted session's hand-off files
READY_FILE, CHECKIN_FILE, DEEP_STATE = 'ready', 'checkin', 'state.md'
PREV = '.prev'                 # an earlier crossing's hand-off file, set aside when the next one opens


class StepFailed(Exception):
    """A launch.py child that exited non-zero or did not end in time."""


def claims(settings, now):
    """The keys of the crossings whose last phase change is under CLAIM_TTL_S old. LaunchError for
    a bad file: read as empty it would drop the hold."""
    data = state.read_json(state.path(settings, STATE_FILE)) or {}
    crossings = data.get('crossings')
    out = set()
    for key, c in (crossings if isinstance(crossings, dict) else {}).items():
        if isinstance(c, dict) and D._number(c.get('phase_at')) and now - c['phase_at'] < CLAIM_TTL_S:
            out.add(key)
    return out


PM_CHECKPOINT = ("The hub's usage crossover: the PM's account is near its weekly limit, and the hub will start "
                 'a fresh PM on account %s from your hand-off file (a conversation cannot move between '
                 'accounts). Checkpoint now, as role pm "Hand-off" says: update HANDOFF.md in place, post it '
                 'with hub_handoff_ready (kind handoff, from pm, state_path its absolute path), then send '
                 'nothing more and start nothing. Once the new PM is confirmed you are told to stand down. '
                 'If the hub drops the crossing instead (a cross-cleared, cross-failed, no-room or stalled '
                 'push with detail pm), resume your normal work.')
CHECKPOINT = ("The hub's usage crossover: this session's account is near its weekly limit, and "
              'the hub will move you to account %s. Checkpoint and stand down now with skill '
              'session-handoff: write your state note, post it with hub_handoff_ready (kind '
              'handoff, from %s), reply ready as the skill says and go idle. The hub runs the '
              'next step; do not wait for the PM.')

# An unlisted session has no hand-off record to post: it writes its ready file instead
# (kitlib.hubjobs._crossover_decide docstring "Unlisted sessions"), and is told to resume (RESUME) when the crossing is dropped before its stop.
DEEP_CHECKPOINT = ("The hub's usage crossover: this session's account is near its weekly limit, and the hub will "
                   'start your successor on account %(target)s, in your worktree and on your branch, from your '
                   'state note. Now: commit your work in progress on your branch, start no new heavy run (let a '
                   'running gate finish, or note its log), write your state note to %(state)s (your worktree, '
                   'branch and tip, what is done, what is next, what is still running), then write any text to '
                   'the file %(ready)s and go idle; send no report. The hub stops this session once your '
                   'successor has checked in. If the hub drops the crossing, it tells you to resume.')
CONSULTANT_CHECKPOINT = ("The hub's usage crossover: this session's account is near its weekly limit, and the hub "
                         'will stop this session and start %(name)s again on account %(target)s, from your notes '
                         'file. Now: finish the answer in hand and take no new question, append a dated line to '
                         'your notes file %(state)s saying where you stopped, then write any text to the file '
                         '%(ready)s and go idle. If the hub drops the crossing, it tells you to resume.')
RESUME = ("The hub's usage crossover was dropped: this session stays on its account. Resume your normal work.")


def checkpoint_text(key, target):
    return CHECKPOINT % (target, key)


def unlisted_checkpoint_text(c, target):
    """The checkpoint message of the unlisted crossing `c` (DEEP_CHECKPOINT, CONSULTANT_CHECKPOINT)."""
    text = CONSULTANT_CHECKPOINT if c.get('kind') == UL.CONSULTANT else DEEP_CHECKPOINT
    return text % {'target': target, 'name': c.get('old_name'), 'state': c.get('state_path'), 'ready': c.get('ready')}


def unlisted_argv(kit, c, target, plan=False):
    """`launch.py consultant` or `launch.py deep` for the unlisted crossing `c` to account `target`,
    from its launch (kitlib.launcher.unlisted.Session). --force: pick_target's tier 2 lies over the
    launcher's headroom limit. `plan`: --plan, and for a consultant --replace (the old one runs)."""
    L = c.get('launch') or {}
    if c.get('kind') == UL.CONSULTANT:
        args = ['consultant', '--repo-path', L.get('repo_path'), '--repo', L.get('slug'), '--account', target]
    else:
        args = ['deep', '--repo-path', L.get('repo_path'), '--brief', L.get('brief'), '--requester',
                L.get('requester'), '--slug', L.get('slug'), '--account', target]
    for flag, k in (('--model', 'model'), ('--effort', 'effort'), ('--autocompact', 'autocompact')):
        if L.get(k):
            args += [flag, L[k]]
    if c.get('kind') == UL.CONSULTANT:
        args += ['--notes-path', L.get('notes')]
    else:
        args += ['--predecessor', c.get('old_name'), '--state', c.get('state_path')]
    args += ['--checkin', c.get('checkin'), '--force'] + (['--home-worktree'] if L.get('home') else [])
    if plan:
        args += ['--plan'] + (['--replace'] if c.get('kind') == UL.CONSULTANT else [])
    return launch_argv(kit, *args)


def pm_checkpoint_text(target):
    return PM_CHECKPOINT % target


def launch_argv(kit, verb, *args):
    return [sys.executable, os.path.join(kit, 'tools', 'launch.py'), verb] + [str(a) for a in args]


def handoff_argv(kit, key, target, state_path=None, what_if=False):
    return (launch_argv(kit, 'handoff', '--session', key, '--account', target)
            + (['--state', state_path] if state_path else [])
            + (['--what-if'] if what_if else []))


def stop_argv(kit, name, pid, successor=None, dry_run=False):
    return (launch_argv(kit, 'stop', '--name', name)
            + (['--successor', successor] if successor else [])
            + ['--pid', str(pid)]
            + (['--dry-run'] if dry_run else []))


def pm_crossover_argv(kit, target, name, state_path=None, what_if=False):
    return (launch_argv(kit, 'pm-crossover', '--account', target, '--name', name)
            + (['--state', state_path] if state_path else [])
            + (['--what-if'] if what_if else []))


def make(sources):
    from kitlib.hubclient import push
    from kitlib.launcher import settings as lsettings
    home = getattr(sources, 'home', None)
    try:
        lsettings.load(home).state_dir()
    except LaunchError:
        return None                     # no launcher state on this machine: nothing to move
    try:
        host = session_watch.host_module()
    except (ImportError, AttributeError, OSError, ValueError):     # registry.RegistryError
        return None

    def deliver(name, text):            # sources.push and .handoffs are read at each run
        if sources.push is None:
            return False
        return sources.push.deliver(name, text).state in (push.WRITTEN, push.UNCERTAIN)

    def handoffs():
        read = getattr(sources, 'handoffs', None)
        return None if read is None else read()

    job = CrossoverJob(lambda: lsettings.load(home), deliver, handoffs, host)
    return jobs.Job(NAME, EVERY_S, job, first_s=FIRST_S)


class CrossoverJob(object):
    """run(ctx) is the job. `load()` the launcher Settings, read at each run so `usage_crossover`
    takes effect with no hub restart; `deliver(name, text)` True when the message was written or
    may have been; `handoffs()` {key: newest hand-off record}, None when the hub has no read yet;
    `host` the watcher readers (session_watch.host_module). What a test also replaces: `procs()`
    the process rows, `child(argv, cwd)` -> (exit code, lines), `clock` epoch seconds, `kit` the
    kit checkout whose tools/launch.py runs, `temp` the agent temp folder an unlisted session's
    hand-off files go under when its window sets none (AGENT_TEMP, else TEMP, else home)."""

    def __init__(self, load, deliver, handoffs, host, procs=None, child=None, clock=None, kit=None, temp=None):
        self.load, self.deliver, self.handoffs, self.host = load, deliver, handoffs, host
        self.procs = procs or C.list_processes
        self.child = child or C.run_child
        self.clock = clock or time.time
        self.kit = kit or pmstart.KIT_ROOT
        self.temp = temp or kuser.agent_temp()
        self._noted = {}                # key: the decision note last logged

    def __call__(self, ctx):
        settings = self.load()
        newest = self.handoffs()
        if newest is None:
            return jobs.Skip('the hub gives the job no hand-off read yet')
        now = self.clock()
        doc = self._read(settings, now)
        sessions = state.read_sessions(settings)
        coordinators = state.read_coordinators(settings) or {}
        try:
            procs = list(self.procs())      # every row: an unlisted session's window is a powershell.exe
        except subprocess.TimeoutExpired as e:     # before any state is written: the next run decides
            return C.skip_timed_out(e)
        rows = [(lookup.rc_name(p.get('CommandLine')), p) for p in lookup.launched(procs)]
        lives = [D.Live(n, p.get('ProcessId'), p.get('Created')) for n, p in rows if n]
        had_old = 'pm_old' in doc
        pm_old = _old_pm(doc, lives)
        pm = {n for n, p in rows if n and _pm_row(n, p, coordinators)} | ({pm_old.name} if pm_old else set())
        pms = _pm_processes(rows, coordinators, pm_old)
        accounts = self._accounts(settings)
        run = _Run(self, ctx, settings, doc, now)
        run.dirty = had_old and pm_old is None
        run.log_readings(accounts)
        stalls = self._stalls(settings, lives)
        olds = [c.get('old_name') for c in doc['crossings'].values() if isinstance(c, dict)]
        quiet = self._quiet(set(stalls) | {n for n in olds if n}, now)
        running = _running(lives, coordinators)
        entries = dict(state.entries(sessions))

        def step(key, entry, live):
            crossing = doc['crossings'].get(key)
            name = (crossing or {}).get('old_name') or (live[0].name if live else None)
            return D.decide(key, entry, crossing, live, stalls.get(name), quiet.get(name), accounts,
                          newest.get(key), doc['starts'], running, doc['landed'],
                          settings.usage_crossover, now,
                          note_at=_mtime((crossing or {}).get('state_path')),
                          cleared=doc['cleared'].get(key), asks=doc['asks'].get(key),
                          weeks=doc['weeks'])

        steps, known = [], set()
        for key in sorted(set(entries) | set(doc['crossings'])):
            if key == D.PM_DOC or _is_unlisted(doc['crossings'].get(key)):
                continue
            entry = entries.get(key)
            stem = (entry or {}).get('stem')
            live = [l for l in lives if C.of_stem(l.name, stem)]
            known.update(l.name for l in live)
            if _pm_entry(key, entry, coordinators) or any(l.name in pm for l in live):
                continue
            d = step(key, entry, live)
            for _ in range(3):          # state changes, no acting step; the longest chain:
                # ready (successor up and moved) -> starting -> started -> stall-stop or stopping
                if d.action != D.ADVANCE:
                    break
                run.advance(key, d)
                d = step(key, entry, live)
            run.settle(key, d)
            steps.append((key, entry, live, d))
        never = self._unlisted(run, procs, settings, lives, known, pm, entries, stalls, quiet, accounts, running,
                               steps)
        run.names = {l.name for l in lives} | {e.get('name') for e in entries.values() if isinstance(e, dict)}
        if pms or doc['crossings'].get(D.PM_DOC):

            def pm_step():
                c = doc['crossings'].get(D.PM_DOC)
                name = (c or {}).get('old_name') or (pms[0].name if len(pms) == 1 else None)
                acct = (c or {}).get('account') or (pms[0].account if pms else None)
                return D.decide_pm(pms, c, stalls.get(name), quiet.get(name), accounts, newest.get(PM_FROM),
                                 doc['starts'], running, doc['landed'], settings.usage_crossover, now,
                                 note_at=_mtime((c or {}).get('state_path')), cleared=doc['cleared'].get(D.PM_DOC),
                                 asks=doc['asks'].get(D.PM_DOC), points=doc['weeks'].get(str(acct)),
                                 recorded=_recorded(coordinators), names={l.name for l in lives})
            steps.append(run.pm_settle(pm_step, pm, pms))
        self._others(run, lives, known, pm, accounts, coordinators, stalls, never)
        run.act(steps, newest, sessions, coordinators)
        return run.finish()

    def _unlisted(self, run, procs, settings, lives, known, pm, entries, stalls, quiet, accounts, running, steps):
        """Decide each launched consultant and deep-worker with no sessions.json entry, and each
        open crossing of one (kitlib.hubjobs._crossover_decide docstring "Unlisted sessions"), into
        `steps`; their names go into `known` and their accounts into `running`. Returns
        {name: (reason, account)} of the live sessions that are never crossed."""
        doc, now = run.doc, run.now
        found, never = UL.classify(procs, settings, skip=set(known) | set(pm), keys=set(entries))
        never = {n: (why, acct or C.name_account(n)) for n, (why, acct) in never.items()}
        run.unlisted.update(found)
        run.unlisted.update(k for k, c in doc['crossings'].items() if _is_unlisted(c))
        for group in found.values():
            for s in group:
                known.add(s.name)
                if C.name_account(s.name) is None:
                    running[s.account] += 1
        keys = set(found) | {k for k, c in doc['crossings'].items() if _is_unlisted(c)}
        for key in sorted(keys):
            c = doc['crossings'].get(key)
            group = found.get(key, [])
            if c is None and len(group) > 1:    # which one would cross is unknown: none does
                for s in group:
                    known.discard(s.name)
                    never[s.name] = (UL.SEVERAL, s.account)
                continue
            sess = group[0] if len(group) == 1 else None
            live = [l for l in lives if C.of_stem(l.name, key)]

            def step():
                cur = doc['crossings'].get(key)
                name = (cur or {}).get('old_name') or (sess.name if sess else None)
                return D.decide_unlisted(key, sess, cur, live, stalls.get(name), quiet.get(name), accounts,
                                       doc['starts'], running, doc['landed'], settings.usage_crossover, now,
                                       files=_files(cur), cleared=doc['cleared'].get(key),
                                       asks=doc['asks'].get(key), weeks=doc['weeks'])
            d = step()
            for _ in range(2):          # asked -> ready -> stop-first: state changes, no acting step
                if d.action != D.ADVANCE:
                    break
                run.advance(key, d)
                d = step()
            run.unlisted_settle(key, d, live)
            steps.append((key, sess, live, d))
        if never:
            run.notes.append('never crossed: %s' % ', '.join('%s (%s)' % (n, never[n][0]) for n in sorted(never)))
        return never

    def _others(self, run, lives, known, pm, accounts, coordinators, stalls=None, never=None):
        """The `unmanaged` pushes, detail <name>:<reason> (never: {name: (reason, account)}), and
        `pm-limit` for a PM that is not a launched process (the desktop app; a launched one is
        moved, decide_pm), once per week window."""
        now = run.now
        stalls = stalls or {}
        never = never or {}
        for l in lives:
            if l.name not in pm and l.name not in known:
                why, acct = never.get(l.name, (UL.NO_WINDOW, C.name_account(l.name)))
                hot = _hot_reset(acct, stalls.get(l.name), accounts, now)
                if hot is not None:
                    run.once_a_window(l.name, hot, 'unmanaged', unmanaged_detail(l.name, why))
        if pm:
            return
        acct = coordinators.get('pm_account')
        reset = _hot_reset(None if acct in (None, '') else str(acct), None, accounts, now)
        if reset is not None:
            run.once_a_window('pm', reset, 'pm-limit', 'pm')

    def _read(self, settings, now):
        """The state file, each part an object; missing is empty, a bad file a failure. A cleared
        mark whose week has reset is dropped."""
        data = state.read_json(state.path(settings, STATE_FILE)) or {}
        for part in ('crossings', 'starts', 'landed', 'told', 'windows', 'readings', 'cleared',
                     'asks', 'weeks'):
            if not isinstance(data.get(part), dict):
                data[part] = {}
        if not isinstance(data.get('pm_old'), dict):
            data.pop('pm_old', None)
        data['cleared'] = {k: v for k, v in data['cleared'].items()
                           if isinstance(v, dict) and D._number(v.get('reset')) and v['reset'] > now}
        return data

    def _accounts(self, settings):
        out = {}
        for n in settings.account_numbers():
            _, data = A.newest_usage(settings, n, by='updated')
            out[str(n)] = C.AccountState(A.account_signed_in(settings, n), data)
        return out

    def _stalls(self, settings, lives):
        """{name: (account or None, reset epoch)} of the live sessions whose transcript ends on a
        weekly-limit error. The record must be the live process's: the registry keeps dead ones."""
        pids = {l.pid: l.name for l in lives}
        folders = pmwatch.account_folders(settings)
        out = {}
        for r in self.host.rc_records(pipes.registry_dirs(settings)):
            read = r.get('weekly_stop')
            if read is None or pids.get(r.get('pid')) != r.get('name'):
                continue
            stop = read(list(folders))
            if stop is not None:
                out[r['name']] = (folders.get(stop[0]), stop[1])
        return out

    def _quiet(self, names, now):
        """{name: seconds since its transcript was written} of these sessions (the stalled ones
        and those of open crossings). An unreadable one is left out: unknown is not quiet."""
        finder = self.host.newest_finder()
        out = {}
        for name in sorted(names):
            try:
                t = finder(name)
            except OSError:
                continue
            if t is not None:
                out[name] = now - t
        return out


def _mtime(path):
    """The mtime of a state note, or None: no path, or no file."""
    if not isinstance(path, str) or not path:
        return None
    try:
        return os.path.getmtime(path)
    except (OSError, ValueError):
        return None


def _is_unlisted(c):
    """True for the crossing of an unlisted session (it carries its kind)."""
    return isinstance(c, dict) and c.get('kind') in (UL.CONSULTANT, UL.DEEP)


def _files(c):
    """decide_unlisted's `files` of the crossing `c`: the mtimes of its ready, check-in and note files."""
    c = c or {}
    return {'ready': _mtime(c.get('ready')), 'checkin': _mtime(c.get('checkin')), 'note': _mtime(c.get('state_path'))}


def unmanaged_detail(name, reason):
    """An `unmanaged` push's detail, <name>:<reason>, cut to push.field's 64 characters with the reason whole."""
    return '%s:%s' % (name[:max(1, 64 - len(reason) - 1)], reason)


def _pm_name(name, coordinators):
    return lstop._pm_named(name) or name == coordinators.get('pm_name')


def _pm_entry(key, entry, coordinators):
    """True for a sessions.json entry that names the PM session."""
    entry = entry or {}
    return any(n and _pm_name(n, coordinators) for n in (key, entry.get('name'), entry.get('stem')))


def _pm_row(name, row, coordinators):
    """True for the PM's process: by name, or by pm_session_id as its --resume or --session-id value
    (launcher.stop.runs_as; the id elsewhere on the line, as in a topic session's brief path, is not
    the PM; a `<` value is the example file's placeholder)."""
    pm_id = coordinators.get('pm_session_id')
    return _pm_name(name, coordinators) or (isinstance(pm_id, str) and not pm_id.startswith('<')
                                            and lstop.runs_as(row.get('CommandLine'), pm_id))


def _recorded(coordinators):
    return D.Recorded(coordinators.get('pm_session_id'), coordinators.get('pm_name'),
                    coordinators.get('pm_account'))


def _old_pm(doc, lives):
    """The PM's old process after a crossover (doc pm_old) while it runs, else None; dropped from
    the document once it has ended."""
    old = doc.get('pm_old')
    if not isinstance(old, dict):
        return None
    for l in lives:
        if l.name == old.get('name') and l.pid == old.get('pid') and l.created == old.get('created'):
            return l
    doc.pop('pm_old', None)
    return None


def _pm_processes(rows, coordinators, pm_old=None):
    """[PmLive] of the launched processes of coordinators.json's PM: the one named pm_name when it
    is set, else _pm_row's; never the old process after a crossover."""
    want = coordinators.get('pm_name')
    acct = coordinators.get('pm_account')
    out = []
    for n, p in rows:
        if not n or (pm_old is not None and p.get('ProcessId') == pm_old.pid and n == pm_old.name):
            continue
        if (n == want) if want else _pm_row(n, p, coordinators):
            out.append(D.PmLive(n, p.get('ProcessId'), p.get('Created'),
                              None if acct in (None, '') else str(acct)))
    return out


def _running(lives, coordinators):
    """{account: live sessions on it}: by each session's name, and the PM's account."""
    out = collections.Counter(a for a in (C.name_account(l.name) for l in lives) if a is not None)
    pm = coordinators.get('pm_account')
    if pm not in (None, ''):
        out[str(pm)] += 1
    return out


def _hot_reset(account, stall, accounts, now):
    """The week's reset when this session or account is at the trigger (a stall read ahead, or a
    fresh reading at TRIGGER_WEEK%), else None."""
    if stall is not None and stall[1] > now:
        return stall[1]
    st = accounts.get(account)
    data = st.usage if st is not None else None
    week, reset = D.week_window(data, now)
    if week is not None and week >= D.TRIGGER_WEEK and D.is_fresh(data, now):
        return reset
    return None


class _Run(object):
    """One run's work on the state document: every write goes through save(), before the action
    it covers."""

    def __init__(self, job, ctx, settings, doc, now):
        self.job, self.ctx, self.settings, self.doc, self.now = job, ctx, settings, doc, now
        self.notes = []
        self.acted = False
        self.dirty = False
        self.names = set()              # the names a new PM may not take (the live and the entries')
        self.unlisted = set()           # the keys of unlisted sessions (decide_unlisted)

    def save(self):
        state.write_json(state.path(self.settings, STATE_FILE), self.doc)
        self.dirty = False

    def log(self, line):
        state.log(self.settings, LOG, line, datetime.datetime.fromtimestamp(self.now))

    def log_readings(self, accounts):
        """Log each account's reading on a change, and keep its week history (burn_eta)."""
        for acct, st in sorted(accounts.items(), key=lambda t: int(t[0])):
            week, _, passed = D.week_state(st.usage, self.now)
            if week is None:
                continue
            self._history(acct, D._epoch((st.usage or {}).get('updated_at')), week)
            text = 'week=%d%s' % (int(week), ' computed' if passed else '')
            if self.doc['readings'].get(acct) != text:
                self.doc['readings'][acct] = text
                self.dirty = True
                self.log('reading account %s %s updated=%s' % (acct, text,
                                                               (st.usage or {}).get('updated_at')))

    def _history(self, acct, updated, week):
        pts = [pt for pt in (self.doc['weeks'].get(acct) or [])
               if isinstance(pt, list) and len(pt) == 2 and D._number(pt[0]) and self.now - pt[0] <= D.BURN_SPAN_S]
        if updated is not None and self.now - updated <= D.BURN_SPAN_S and (not pts or updated > pts[-1][0]):
            pts.append([updated, week])
        if pts != self.doc['weeks'].get(acct):
            if pts:
                self.doc['weeks'][acct] = pts
            else:
                self.doc['weeks'].pop(acct, None)
            self.dirty = True

    def tell(self, key, d):
        """Push d.word once per change of its mark: told[key] keeps the last one."""
        if not d.word:
            if self.doc['told'].pop(key, None) is not None:
                self.dirty = True
            return
        if self.doc['told'].get(key) == d.mark:
            return
        self.doc['told'][key] = d.mark
        self.dirty = True
        self.ctx.event(d.word, d.detail)

    def once_a_window(self, name, reset, word, detail):
        self.doc['windows'] = {n: t for n, t in self.doc['windows'].items()
                               if D._number(t) and t > self.now}
        told = self.doc['windows'].get(name)
        if told is None or abs(told - reset) >= D.RESET_NEAR_S:   # the sources differ by seconds
            self.doc['windows'][name] = reset
            self.dirty = True
            self.ctx.event(word, detail)

    def advance(self, key, d):
        c = self.doc['crossings'][key]
        c.update(d.fields, phase=d.phase, phase_at=self.now)
        self.dirty = True
        self.log('%s: %s: now %s' % (key, d.note, d.phase))

    def settle(self, key, d):
        """One key's note, and a hold's or a clear's push; a clear drops the crossing."""
        self.notes.append('%s: %s' % (key, d.note))
        if self.job._noted.get(key) != d.note:
            self.job._noted[key] = d.note
            self.log('%s: %s' % (key, d.note))
        if d.action == D.CLEAR:
            self.doc['crossings'].pop(key, None)
            if d.fields.get('reset') is not None:
                self.doc['cleared'][key] = d.fields
            self.dirty = True
            self.tell(key, d)
        elif d.action == D.HOLD:
            self.tell(key, d)

    def pm_settle(self, decide_now, pm, pms):
        """The PM's step, decided again after a state change; `crossed` keeps its old process as
        pm_old (and in `pm`, so this run pushes it nothing): its window stays until the owner closes
        it."""
        d = decide_now()
        for _ in range(2):          # asked -> ready -> ready stalled: state changes, no acting step
            if d.action != D.ADVANCE:
                break
            self.advance(D.PM_DOC, d)
            d = decide_now()
        if d.action == D.CLEAR and d.word == 'crossed':
            c = self.doc['crossings'][D.PM_DOC]
            self.doc['pm_old'] = {'name': c.get('old_name'), 'pid': c.get('old_pid'),
                                  'created': c.get('old_created')}
            pm.add(c.get('old_name'))
        self.settle(D.PM_DOC, d)
        return D.PM_DOC, None, pms, d

    def act(self, steps, newest, sessions, coordinators):
        """The first step in ACTS order: this run's one acting step. A would-cross already told
        is no step: it would keep the others out."""
        chosen = [s for s in steps if s[3].action in D.ACTS
                  and not (s[3].action == D.WOULD and self.doc['told'].get(s[0]) == s[3].mark)]
        if not chosen:
            return
        key, entry, live, d = min(chosen, key=lambda s: D.ACTS.index(s[3].action))
        if self.ctx.stop.is_set():
            self.notes.append('%s: left for the next run (the hub is closing)' % key)
            return
        self.acted = True
        if key == D.PM_DOC:
            getattr(self, '_pm_' + d.action)(live, d, newest.get(PM_FROM), coordinators)
            return
        if key in self.unlisted:
            getattr(self, '_u_' + d.action.replace('-', '_'))(key, entry, live, d)
            return
        step = getattr(self, '_' + d.action.replace('-', '_'))
        step(key, entry, live, d, newest.get(key), sessions, coordinators)

    def _new_crossing(self, key, entry, live, target, high, phase):
        old = live[0]
        c = {'phase': phase, 'phase_at': self.now, 'asked_at': self.now, 'target': target,
             'account': C.name_account(old.name) or str(entry.get('account')), 'old_name': old.name,
             'old_pid': old.pid, 'old_created': old.created, 'entry_name': entry.get('name'),
             'high_c': high, 'delivered': False}
        self.doc['crossings'][key] = c
        return c

    def _ask(self, key, entry, live, d, newest, sessions, coordinators):
        c = self.doc['crossings'].get(key)
        if c is None:
            c = self._new_crossing(key, entry, live, d.target, D.c_number(newest), D.ASKED)
            kept = {k: D.recent(v, self.now) for k, v in self.doc['asks'].items()}
            self.doc['asks'] = {k: v for k, v in kept.items() if v}
            self.doc['asks'].setdefault(key, []).append(self.now)
        self.save()
        c['delivered'] = bool(self.job.deliver(c['old_name'], checkpoint_text(key, d.target)))
        self.dirty = True
        if d.word:
            self.tell(key, d)
        self.notes.insert(0, '%s: asked to checkpoint for account %s' % (key, d.target))

    def _start(self, key, entry, live, d, newest, sessions, coordinators):
        c = self.doc['crossings'][key]
        c.update(phase=D.STARTING, phase_at=self.now, target=d.target, high_c=D.c_number(newest),
                 new_name=handoff.successor_name(entry, d.target, False))
        kept = {k: D.recent(v, self.now) for k, v in self.doc['starts'].items()}
        self.doc['starts'] = {k: v for k, v in kept.items() if v}
        self.doc['starts'].setdefault(key, []).append(self.now)
        self.doc['landed'][d.target] = self.now
        self.save()                     # before the child: a crash mid-start still counts
        path = None if c.get('stalled') else c.get('state_path')
        self.notes.insert(0, '%s: handing off to account %s' % (key, d.target))
        self._child(handoff_argv(self.job.kit, key, d.target, path))

    def _finish(self, key, entry, live, d, newest, sessions, coordinators):
        c = self.doc['crossings'][key]
        handoff.record_move(self.settings, sessions, coordinators or None, key, entry,
                            c['target'], c['new_name'])
        c.update(phase=D.STARTED, phase_at=self.now)
        self.dirty = True
        self.notes.insert(0, '%s: sessions.json moved to %s' % (key, c['new_name']))

    def _count_stop(self, c):
        """A stop is counted, and its backoff set, before the child: a refusal waits it out."""
        tries = int(c.get('stop_tries') or 0) + 1
        c.update(stop_tries=tries, stop_retry_at=self.now + D.BACKOFF_S[min(tries, len(D.BACKOFF_S)) - 1])

    def _stop_old(self, key, entry, live, d, newest, sessions, coordinators):
        c = self.doc['crossings'][key]
        self._count_stop(c)
        self.save()
        self.notes.insert(0, '%s: stopping %s' % (key, c['old_name']))
        self._child(stop_argv(self.job.kit, c['old_name'], c['old_pid'], c['new_name']))

    def _stop_stalled(self, key, entry, live, d, newest, sessions, coordinators):
        c = self.doc['crossings'].get(key)
        if c is None:
            c = self._new_crossing(key, entry, live, d.target, D.c_number(newest), D.STALL_STOP)
            c['stalled'] = True
        if d.word:
            self.tell(key, d)
        self._count_stop(c)
        self.save()
        up = c.get('new_name') in [l.name for l in live]
        self.notes.insert(0, '%s: stopping stalled %s' % (key, c['old_name']))
        self._child(stop_argv(self.job.kit, c['old_name'], c['old_pid'],
                              c['new_name'] if up else None))

    def _would(self, key, entry, live, d, newest, sessions, coordinators):
        """A dry run on a change: nothing starts or stops, and a refusal is only logged."""
        name = live[0].name
        for argv in (handoff_argv(self.job.kit, key, d.target, what_if=True),
                     stop_argv(self.job.kit, name, live[0].pid, dry_run=True)):
            try:
                code, lines = self.job.child(argv, self.job.kit)
            except (subprocess.TimeoutExpired, OSError) as e:
                code, lines = -1, [type(e).__name__]
            self.log('%s: would run %s: exit %s: %s' % (key, argv[2], code,
                                                        (lines or ['no output'])[-1]))
        self.tell(key, d)

    def unlisted_settle(self, key, d, live):
        """An unlisted key's settle: a crossing that clears with its old session still running and
        told to checkpoint (not crossed: it stopped or its successor runs) tells it to resume."""
        c = self.doc['crossings'].get(key)
        if (d.action == D.CLEAR and d.word != 'crossed' and isinstance(c, dict) and c.get('delivered')
                and any((l.pid, l.created) == (c.get('old_pid'), c.get('old_created')) for l in live)):
            if self.job.deliver(c.get('old_name'), RESUME):
                self.notes.append('%s: %s told to resume' % (key, c.get('old_name')))
            else:
                self.notes.append('%s: %s could not be told to resume' % (key, c.get('old_name')))
        self.settle(key, d)

    def _u_crossing(self, key, sess, target, phase):
        """A new crossing of the unlisted session `sess`; its hand-off files go in
        <agent temp>/usage-crossover/<key>/ (kitlib.hubjobs._crossover_decide docstring "Unlisted
        sessions")."""
        folder = os.path.join(sess.agent_temp or self.job.temp, HANDOFF_FOLDER, key)
        state_path = sess.notes if sess.kind == UL.CONSULTANT else os.path.join(folder, DEEP_STATE)
        return {'kind': sess.kind, 'phase': phase, 'phase_at': self.now, 'asked_at': self.now, 'target': target,
                'account': sess.account, 'old_name': sess.name, 'old_pid': sess.pid, 'old_created': sess.created,
                'delivered': False, 'folder': folder, 'ready': os.path.join(folder, READY_FILE),
                'checkin': os.path.join(folder, CHECKIN_FILE), 'state_path': state_path,
                'launch': {'repo_path': sess.repo_path, 'home': sess.home, 'model': sess.model,
                           'effort': sess.effort, 'autocompact': sess.autocompact, 'requester': sess.requester,
                           'brief': sess.brief, 'notes': sess.notes, 'slug': sess.slug}}

    def _u_open(self, key, sess, target, phase):
        """A new crossing of `sess` (_u_crossing), kept in the state. Its folder is the key's for
        every crossing, so an earlier crossing's ready, check-in and state.md files are set aside
        first as <file>.prev: none of them counts for this one, and a stall-path start, which
        waits for no note, never hands the successor an old state note. A consultant's notes file
        lies outside the folder and stays."""
        c = self.doc['crossings'][key] = self._u_crossing(key, sess, target, phase)
        for name in (READY_FILE, CHECKIN_FILE, DEEP_STATE):
            path = os.path.join(c['folder'], name)
            try:
                os.replace(path, path + PREV)
            except FileNotFoundError:
                continue
            except OSError as e:
                self.log('%s: could not set aside %s: %s' % (key, path, e))
            else:
                self.notes.append('%s: set aside %s from an earlier crossing' % (key, path))
        return c

    def _u_ask(self, key, sess, live, d):
        c = self.doc['crossings'].get(key)
        if c is None:
            c = self._u_open(key, sess, d.target, D.ASKED)
            kept = {k: D.recent(v, self.now) for k, v in self.doc['asks'].items()}
            self.doc['asks'] = {k: v for k, v in kept.items() if v}
            self.doc['asks'].setdefault(key, []).append(self.now)
        self.save()
        try:
            os.makedirs(c['folder'], exist_ok=True)
        except OSError as e:            # the session is told the path; it may make the folder itself
            self.log('%s: could not make %s: %s' % (key, c['folder'], e))
        c['delivered'] = bool(self.job.deliver(c['old_name'], unlisted_checkpoint_text(c, d.target)))
        self.dirty = True
        if d.word:
            self.tell(key, d)
        self.notes.insert(0, '%s: asked to checkpoint for account %s' % (key, d.target))

    def _u_start(self, key, sess, live, d):
        c = self.doc['crossings'][key]
        new = key if c.get('kind') == UL.CONSULTANT else '%s-a%s' % (key, d.target)
        c.update(phase=D.STARTING, phase_at=self.now, start_at=self.now, target=d.target, new_name=new)
        kept = {k: D.recent(v, self.now) for k, v in self.doc['starts'].items()}
        self.doc['starts'] = {k: v for k, v in kept.items() if v}
        self.doc['starts'].setdefault(key, []).append(self.now)
        self.doc['landed'][d.target] = self.now
        self.save()                     # before the child: a crash mid-start still counts
        self.notes.insert(0, '%s: starting %s on account %s' % (key, new, d.target))
        self._child(unlisted_argv(self.job.kit, c, d.target))

    def _u_stop_old(self, key, sess, live, d):
        """The old session's stop: a consultant's before its start (stop-first, no successor), a
        deep-worker's once its successor checked in."""
        c = self.doc['crossings'][key]
        self._count_stop(c)
        self.save()
        self.notes.insert(0, '%s: stopping %s' % (key, c['old_name']))
        self._child(stop_argv(self.job.kit, c['old_name'], c['old_pid'],
                              c.get('new_name') if c.get('phase') == D.STOPPING else None))

    def _u_stop_stalled(self, key, sess, live, d):
        c = self.doc['crossings'].get(key)
        if c is None:
            c = self._u_open(key, sess, d.target, D.STALL_STOP)
            c['stalled'] = True
        if d.word:
            self.tell(key, d)
        self._count_stop(c)
        self.save()
        mine = (c.get('old_pid'), c.get('old_created'))
        up = c.get('kind') == UL.DEEP and any(l.name == c.get('new_name') and (l.pid, l.created) != mine
                                              for l in live)
        self.notes.insert(0, '%s: stopping stalled %s' % (key, c['old_name']))
        self._child(stop_argv(self.job.kit, c['old_name'], c['old_pid'], c['new_name'] if up else None))

    def _u_would(self, key, sess, live, d):
        """A dry run on a change: nothing starts or stops, and a refusal is only logged."""
        c = self._u_crossing(key, sess, d.target, D.ASKED)
        for argv in (unlisted_argv(self.job.kit, c, d.target, plan=True),
                     stop_argv(self.job.kit, sess.name, sess.pid, dry_run=True)):
            try:
                code, lines = self.job.child(argv, self.job.kit)
            except (subprocess.TimeoutExpired, OSError) as e:
                code, lines = -1, [type(e).__name__]
            self.log('%s: would run %s: exit %s: %s' % (key, argv[2], code, (lines or ['no output'])[-1]))
        self.tell(key, d)

    def _pm_crossing(self, pm, target, high, phase, coordinators):
        c = {'phase': phase, 'phase_at': self.now, 'asked_at': self.now, 'target': target,
             'account': pm.account, 'old_name': pm.name, 'old_pid': pm.pid, 'old_created': pm.created,
             'old_sid': coordinators.get('pm_session_id'), 'high_c': high, 'delivered': False}
        self.doc['crossings'][D.PM_DOC] = c
        return c

    def _pm_ask(self, pms, d, newest, coordinators):
        c = self.doc['crossings'].get(D.PM_DOC)
        if c is None:
            c = self._pm_crossing(pms[0], d.target, D.c_number(newest), D.ASKED, coordinators)
            kept = {k: D.recent(v, self.now) for k, v in self.doc['asks'].items()}
            self.doc['asks'] = {k: v for k, v in kept.items() if v}
            self.doc['asks'].setdefault(D.PM_DOC, []).append(self.now)
        self.save()
        c['delivered'] = bool(self.job.deliver(PM_FROM, pm_checkpoint_text(d.target)))
        self.dirty = True
        if d.word:
            self.tell(D.PM_DOC, d)
        self.notes.insert(0, '%s: the PM asked to checkpoint for account %s' % (D.PM_DOC, d.target))

    def _pm_start(self, pms, d, newest, coordinators):
        """`launch.py pm-crossover`; a new crossing (a stall on full) starts here with no ask."""
        c = self.doc['crossings'].get(D.PM_DOC)
        if c is None:
            c = self._pm_crossing(pms[0], d.target, D.c_number(newest), D.STARTING, coordinators)
            c['stalled'] = True
            if d.word:
                self.tell(D.PM_DOC, d)
        if not c.get('new_name') or c.get('target') != d.target:
            c['new_name'] = relaunch.crossover_name(d.target, self.names)
        c.update(phase=D.STARTING, phase_at=self.now, target=d.target, high_c=D.c_number(newest))
        kept = {k: D.recent(v, self.now) for k, v in self.doc['starts'].items()}
        self.doc['starts'] = {k: v for k, v in kept.items() if v}
        self.doc['starts'].setdefault(D.PM_DOC, []).append(self.now)
        self.doc['landed'][d.target] = self.now
        self.save()                     # before the child: a crash mid-start still counts
        path = None if c.get('stalled') else c.get('state_path')
        self.notes.insert(0, '%s: starting the PM %s on account %s' % (D.PM_DOC, c['new_name'], d.target))
        self._child(pm_crossover_argv(self.job.kit, d.target, c['new_name'], path))

    def _pm_would(self, pms, d, newest, coordinators):
        """A dry run on a change: nothing starts."""
        argv = pm_crossover_argv(self.job.kit, d.target, relaunch.crossover_name(d.target, set(self.names)),
                                 what_if=True)
        try:
            code, lines = self.job.child(argv, self.job.kit)
        except (subprocess.TimeoutExpired, OSError) as e:
            code, lines = -1, [type(e).__name__]
        self.log('%s: would run %s: exit %s: %s' % (D.PM_DOC, argv[2], code, (lines or ['no output'])[-1]))
        self.tell(D.PM_DOC, d)

    def _child(self, argv):
        what = ' '.join(argv[2:])
        try:
            code, lines = self.job.child(argv, self.job.kit)
        except subprocess.TimeoutExpired:
            raise StepFailed('%s did not end in %d s' % (what, C.START_TIMEOUT_S))
        except OSError as e:
            raise StepFailed('%s could not start: %s' % (what, e))
        self.log('ran %s: exit %s: %s' % (what, code, (lines or ['no output'])[-1]))
        if code != 0:
            raise StepFailed('%s exited %s: %s' % (what, code, (lines or ['no output'])[-1]))

    def finish(self):
        if self.dirty:
            self.save()
        text = '; '.join(self.notes) or 'no session in the sessions file'
        return text if self.acted else jobs.Skip(text)
