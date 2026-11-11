"""kitlib.hubsite.serveviews: the Api's `views` object of the hub server (kitlib.hubsite.serve
builds it over a servesources.Sources).

  SiteViews(...)        the views below
  NoRepoList            the repository list could not be read and no earlier answer is held
  NotReadYet            GET snapshot's backlog or completed view before any read (the
                        backlog timer's or a route's), with or without the timer

Views (SiteViews). backlog, completed, repos, agents and skills exist only where their source does, so
the Api answers a missing one `unavailable` by itself. What a view says never holds a path,
git's words or an exception's text: the backlog views carry kitlib.hubsite.backlog's fixed
phrases, and the reasons of the derived views are replaced by fixed phrases here.
  backlog(repo, state, fresh), completed(repo, days)
      [{repo, fetched_at, items, error, stale, fetch_error}]; completed adds `days`. No entry
      names the commit it was read at (backlog.Views does not give it). `days` narrows the
      window backlog.Views was built with and cannot widen it.
      Called with no argument (GET snapshot's call: api.snapshot; the backlog and completed
      routes pass every argument), both answer from what the reads so far left
      (backlog.Views.held), however old, and start no git, whether or not the backlog timer
      runs: the page's one load never waits on a repository's read. A repository the first
      round has not come to yet is an entry with "error" backlog.NOT_READ; with none read yet
      both raise NotReadYet, which the snapshot shows in `unavailable`. Only a source with no
      `held` (a stand-in) reads as the routes do.
  notices() repos() usage() agents() skills() skill(name) skill_source(name) findings()
                                          kitlib.hubsite.derived's answers
  health()    {git, backlog, backlog_at, backlog_error, backlog_timer, backlog_timer_running}:
      backlog_timer is the class of what the timer's last round or pause raised, or null;
      backlog_timer_running is true while its thread is alive (null with no backlog source, or
      one with no timer_running). `git` is GitSync.status()
      without `detail`, or {"running": false, "off": phrase}. `backlog` is null with no backlog
      source, else [{repo, fetched_at, error}] as the last backlog read gave it (`backlog_at`:
      when; null and an empty list before the first read): health starts no git. A read is a
      client's GET of the backlog or completed view (a snapshot's held answer is not one), or the
      end of a round of the backlog
      timer: build() wraps the `wait` of a backlog.Views (its pause between two rounds) so
      SiteViews.after_round() runs on the timer's own thread after each round. That reads the
      view as a client would, mostly from what the round just read; with no timer
      (backlog_refresh_s unset) health knows nothing of the backlog until a client reads it.
      That read is given the timer's stop event (backlog.Views.get(stop=)): once the timer is
      told to stop it reads no further repository, and health says what it said before. A
      read of after_round() that raises (a backlog source whose `get` takes no `stop`, say) is
      one 'error' line in the log (ROUND_FAILED), once for each failure that is new.
"""
from . import backlog, derived, gitsync, recordwrite
from .store import HubError

SYNC_OFF = 'git sync is off on this server'
SYNC_REFUSED = 'git sync was refused at start-up: the server console says why'
NO_USAGE_SOURCE = 'no launcher usage source configured'     # derived.usage's own phrase
USAGE_FAILED = 'the usage source could not be read'
AGENTS_FAILED = 'the agent definitions could not be read'
SKILLS_FAILED = 'the skill sources could not be read'
ROUND_FAILED = ('the backlog view could not be read after a round of the backlog timer, so '
                'health does not follow the timer: %s: %s')


class NoRepoList(Exception):
    """The repository list could not be read and no earlier answer is held. The Api shows the
    class name only."""


class NotReadYet(Exception):
    """GET snapshot's backlog or completed view when no read has answered yet
    (backlog.NOT_READ): the timer's first round is still in git, or no timer ever read. The Api
    shows the class name only."""

    def __init__(self):
        Exception.__init__(self, backlog.NOT_READ)


def _named(entries, repo):
    """The entries of the repository `repo`, compared without regard to case ('' : all)."""
    if not repo:
        return list(entries)
    want = repo.lower()
    return [e for e in entries if str(e.get('repo') or '').lower() == want]


def _entry(e, items):
    return {'repo': e.get('repo'), 'fetched_at': e.get('fetched'), 'items': items,
            'error': e.get('error'), 'stale': bool(e.get('stale')),
            'fetch_error': e.get('fetch_error')}


def _record_call(fn, *args):
    """fn(*args), a record view of kitlib.hubsite.backlog, with what it raises put as the Api
    answers it: a RecordError as its own code (not_found, invalid) and fixed phrase, a ReadError
    as `unavailable` with its fixed phrase (backlog.say). Neither names a path or what was asked."""
    try:
        return fn(*args)
    except backlog.RecordError as e:
        raise HubError(e.code, e.said, e.field)
    except backlog.ReadError as e:
        raise HubError('unavailable', backlog.say(e), 'views')


class SiteViews(object):
    """The Api's `views` (module docstring, "Views"). A view is bound only where its source
    exists: `backlog_views` for backlog, completed, record, record_template and the record
    writes (record_add, record_edit, record_delete, record_unblock, record_answer, the
    filing of a bound question's answer, and record_park, record_unpark and record_asks, the
    hub's park of a record on an ask bound to it (a question, an agent's to-do, a file
    approval), its undoing, and the lines a sweep reads),
    sources.repos, .agents and .skills
    for theirs. notices, findings, lint_runs, usage and health are always there
    (kitlib.hubsite.derived answers a missing notice or findings folder with none and a missing
    usage source with its own phrase). `sync` is the git
    sync or None, `sync_off` the fixed phrase health gives when there is none. `log(level,
    text)`: where after_round says that a read of its own failed."""

    def __init__(self, store, sources, backlog_views=None, sync=None, sync_off=SYNC_OFF,
                 log=None):
        self._log = log
        self._round_said = ''       # the failure after_round last logged
        self._store = store
        self._sources = sources
        self._backlog = backlog_views
        self._sync = sync
        self._sync_off = sync_off
        self._last = None           # what the last backlog answer said, for health
        self.notices = self._notices
        self.findings = self._findings
        self.lint_runs = self._lint_runs
        self.usage = self._usage
        if backlog_views is not None:
            self.backlog = self._open
            self.completed = self._completed
            self.record = self._record
            self.record_template = self._record_template
            self.record_add = self._record_add
            self.record_edit = self._record_edit
            self.record_delete = self._record_delete
            self.record_unblock = self._record_unblock
            self.record_answer = self._record_answer
            self.record_park = self._record_park
            self.record_unpark = self._record_unpark
            self.record_asks = self._record_asks
        if sources.repos is not None:
            self.repos = self._repos
        if sources.agents:
            self.agents = self._agents
        if sources.skills:
            self.skills = self._skills
            self.skill = self._skill
            self.skill_source = self._skill_source

    def _held(self):
        """The backlog source's held answer (backlog.Views.held), whether or not its timer
        runs: GET snapshot never reads git (a timer that ended leaves the views as its last
        reads left them, and health says it is not running). None, to read as the routes do,
        only for a source with no held answer (a stand-in). Raises NotReadYet when no read has
        answered yet."""
        held = getattr(self._backlog, 'held', None)
        if not callable(held):
            return None
        got = held()
        if got is None:
            raise NotReadYet()
        return got

    def _read(self, fresh=False, stop=None, held=False):
        """The backlog source's entries, kept for health. With `stop` (an Event; the timer's
        own read gives its) the source may answer None, a read it cut short: None then, and
        health keeps what it had. `held` (GET snapshot's): what the reads so far left, with no
        git started and health left as it was (_held); for a source with no held answer, a
        read as without it."""
        got = self._held() if held else None
        if got is not None:
            return got.get('repos') or []
        if stop is None:
            got = self._backlog.get(fresh=bool(fresh))
        else:
            got = self._backlog.get(fresh=bool(fresh), stop=stop)
            if got is None:
                return None
        entries = got.get('repos') or []
        # one assignment: health reads it from another thread and takes no lock
        self._last = {'at': got.get('at'), 'error': got.get('error'),
                      'repos': [{'repo': e.get('repo'), 'fetched_at': e.get('fetched'),
                                 'error': e.get('fetch_error') or e.get('error')}
                                for e in entries]}
        if got.get('error') and not entries:
            raise NoRepoList()
        return entries

    def _open(self, repo=None, state=None, fresh=None):
        # no argument at all is GET snapshot's call (api.snapshot); every route passes all three
        held = repo is None and state is None and fresh is None
        out = []
        for e in _named(self._read(fresh, held=held), repo or ''):
            items = e.get('open') or []
            if state:
                items = [i for i in items if i.get('s') == state]
            out.append(_entry(e, items))
        return out

    def _completed(self, repo=None, days=None):
        # no `repo` is GET snapshot's call (api.snapshot); GET completed always passes one
        entries = self._read(held=repo is None)
        limit = self._backlog.days
        since = None
        if days is not None and days < limit:
            since = backlog.window(self._backlog.clock(), days)
        out = []
        for e in _named(entries, repo):
            items = e.get('completed') or []
            if since is not None:
                items = [i for i in items if str(i.get('d') or '')[:10] >= since]
            one = _entry(e, items)
            one['days'] = limit if since is None else days
            out.append(one)
        return out

    def _record(self, repo, id):
        """One record, read now (kitlib.hubsite.backlog Views.record), its failures as HubErrors."""
        return _record_call(self._backlog.record, repo, id)

    def _record_template(self, repo):
        return _record_call(self._backlog.record_template, repo)

    # The owner's record writes (kitlib.hubsite.recordwrite) to the store of the configured
    # repository `repo` names; the root never comes from the caller. After a write the backlog
    # source reads that repository again (Views.reread), so the snapshot the page reads shows
    # it; add and edit also answer the record as read after the write.
    def _record_root(self, repo, rid):
        _record_call(backlog.check_id, rid)
        # Views._named, the record reads' own lookup: its one caller outside backlog.Views
        return _record_call(self._backlog._named, repo)

    def _record_add(self, repo, id, summary, scopes, effort, ref):
        name, root = self._record_root(repo, id)
        out = recordwrite.add(root, id, summary, scopes, effort, ref)
        self._backlog.reread(name, root)
        return dict(out, record=self._record(name, id))

    def _record_edit(self, repo, id, version, changes, unset, body):
        name, root = self._record_root(repo, id)
        out = recordwrite.edit(root, id, version, changes, unset, body)
        self._backlog.reread(name, root)
        return dict(out, record=self._record(name, id))

    def _record_delete(self, repo, id, why):
        name, root = self._record_root(repo, id)
        out = recordwrite.delete(root, id, why)
        self._backlog.reread(name, root)
        return out

    def _record_unblock(self, repo, id, on, reason):
        name, root = self._record_root(repo, id)
        out = recordwrite.unblock(root, id, on, reason)
        self._backlog.reread(name, root)
        return dict(out, record=self._record(name, id))

    def _record_answer(self, repo, id, qid, at, title, label, note, via, other, waiting):
        name, root = self._record_root(repo, id)
        out = recordwrite.answer(root, id, qid, at, title, label, note, via, other, waiting)
        self._backlog.reread(name, root)
        return out

    # The hub's own writes as the owner's proxy (Session: hub-park), from an ask bound to a
    # record (kitlib.hubsite.api park_item, unpark_item); no route calls them.
    def _record_park(self, repo, id, qid, cond, asker):
        name, root = self._record_root(repo, id)
        out = recordwrite.park(root, id, qid, cond, asker)
        if out.get('park') == 'blocked':
            self._backlog.reread(name, root)
        return out

    def _record_unpark(self, repo, id, cond):
        name, root = self._record_root(repo, id)
        out = recordwrite.withdraw(root, id, cond)
        if out.get('dropped'):
            self._backlog.reread(name, root)
        return out

    def _record_asks(self):
        """[{repo, id, q, kind, parked}]: the owner ask lines of every configured repository's
        store (recordwrite.asks), for the sweep at hub start (api questions_file). A repository
        with no checkout is skipped; one whose store cannot be read is skipped with a log line,
        and the next start reads it again."""
        out = []
        for name, root in self._backlog.repos():
            if not root:
                continue
            try:
                got = recordwrite.asks(root)
            except Exception as e:      # noqa: BLE001 - one store must not stop the others
                if self._log is not None:
                    self._log('note', 'reading the asks of %s failed: %s' % (
                        name, getattr(e, 'code', type(e).__name__)))
                continue
            out += [dict(a, repo=name) for a in got]
        return out

    def _notices(self):
        return derived.notices(self._sources.notices)

    def _findings(self):
        return derived.findings(self._sources.findings)

    def _lint_runs(self):
        return derived.lint_runs(self._sources.findings)

    def _repos(self):
        return self._sources.repos(list(self._store.docs('repos').values()))

    def _usage(self):
        out = derived.usage(self._sources.usage, self._store.clock)
        if not out.get('available') and out.get('reason') != NO_USAGE_SOURCE:
            out['reason'] = USAGE_FAILED
        return out

    def _agents(self):
        out = derived.agents(self._sources.agents)
        if not out.get('available'):
            out['reason'] = AGENTS_FAILED
        return out

    def _skills(self):
        out = derived.skills(self._sources.skills)
        if not out.get('available'):
            out['reason'] = SKILLS_FAILED
        return out

    def _skill(self, name):
        out = derived.skill(self._sources.skills, name)
        if out is None:
            raise HubError('not_found', 'there is no skill %s' % name, 'name')
        return out

    def _skill_source(self, name):
        out = derived.skill_source(self._sources.skills, name)
        if out is None:
            raise HubError('not_found', 'there is no skill %s' % name, 'name')
        return out

    def _git(self):
        if self._sync is None:
            return {'running': False, 'off': self._sync_off}
        out = dict(self._sync.status())
        out.pop('detail', None)     # git's own words: they may name a remote URL or a path
        return out

    def git_failure(self):
        """(the step the git sync's last pass failed at, git's reason with scrub() applied), or
        ('', '') with no sync or one that is not failing. The reason may name a remote or a
        folder: it is for the owner and the server log, never for an agent."""
        return ('', '') if self._sync is None else gitsync._failure(self._sync)

    def after_round(self, stop=None):
        """Read the backlog view as a client's read does, so health carries what the timer's
        round just read. `stop`: the timer's event; once it is set no further repository is
        read (backlog.Views.get) and health keeps what it had. Never raises: it runs on the
        timer's thread (build). A read that raised leaves health saying what it said before,
        and is one 'error' line in the log (ROUND_FAILED): one for a failure that is new, not
        one for every round. A repository list that cannot be read is no such failure: health
        carries it (backlog_error)."""
        if self._backlog is None:
            return
        try:
            self._read(stop=stop)
            said = ''
        except NoRepoList:
            said = ''
        except Exception as e:      # noqa: BLE001 - the timer's thread goes on
            said = gitsync.scrub(ROUND_FAILED % (type(e).__name__, e))
        was, self._round_said = self._round_said, said
        if said and said != was and self._log is not None:
            try:
                self._log('error', said)
            except Exception:       # noqa: BLE001 - a log that fails must not end the timer
                pass

    def health(self):
        out = {'git': self._git(), 'backlog': None, 'backlog_at': None, 'backlog_error': None,
               'backlog_timer': None, 'backlog_timer_running': None}
        if self._backlog is not None:
            last = self._last
            out['backlog'] = [dict(r) for r in last['repos']] if last else []
            out['backlog_at'] = last['at'] if last else None
            out['backlog_error'] = last['error'] if last else None
            out['backlog_timer'] = getattr(self._backlog, 'timer_error', None)
            running = getattr(self._backlog, 'timer_running', None)
            out['backlog_timer_running'] = bool(running()) if callable(running) else None
        return out


def _follow_rounds(backlog_views, views):
    """Make each round of the backlog timer reach health: wrap `backlog_views.wait`, the pause
    the timer takes after a round on its own thread, so views.after_round(stop) runs first. Not
    when the timer is stopping (and a timer told to stop during that read ends it after the
    repository it is on), and not for a stand-in with no `wait`."""
    wait = getattr(backlog_views, 'wait', None)
    if not callable(wait):
        return

    def pause(stop, seconds):
        if not stop.is_set():
            views.after_round(stop)
        return wait(stop, seconds)

    backlog_views.wait = pause
