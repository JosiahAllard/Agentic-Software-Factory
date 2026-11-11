"""kitlib.hubsite.servesources: where the hub server's parts come from (Sources), and this
machine's, read from its machine config (machine_sources). kitlib.hubsite.serve builds the server
from a Sources; a test hands it one of stand-ins.

  Sources(...)                                  where each part comes from
  machine_sources(home=None, sync=True, jobs=())
                                                this machine's Sources, from its machine config,
                                                with the timed jobs it is handed
  agents_dir(), skills_dir()                    the kit's agent definitions and skill sources
"""
import json
import os
import time

from kitlib import hubbacklog, kitroot, pmwatch
from kitlib.config import pm as pmconfig, pmdata
from kitlib.config import user
from kitlib.config.types import ConfigError
from kitlib.hubclient import token as _token

from . import backlog, derived, gitsync, repoweb

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web')


class Sources(object):
    """Where the server's parts come from. A part that is None is a view the server does not
    have (or, for `sync`, a server that commits nothing).

      tokens       the credential folder (kitlib.hubclient.token.folder)
      static_dir   the page's folder; a missing one answers 404
      pm_data      kitlib.config.pmdata's {path, branch, remote}, or None
      notices      the machine lock's notice folder
      findings     the maintenance lint's results folder, <pm_data>/maint-lint (one
                   <repo>/result.json each: kitlib.maintlint.result, and its skip marker);
                   the findings view and the lint_runs view (GET findings' runs) read it;
                   None: no findings and no runs
      repos        callable(the store's repos documents) -> [{name, aliases}]
      known_repos  callable() -> the PM's configured repo names, which the store's repo-alias
                   check accepts without a repos/ document; None: none
      usage        callable() -> the lines kitlib.launcher.usage prints
      agents       the folder of agent definitions
      skills       the folder of skill sources (content/skills: <name>/SKILL.md each)
      backlog      callable(refresh_s) -> a kitlib.hubsite.backlog.Views (or its like: get, start,
                   stop, days, clock, timer_error; a callable `wait`, when it has one, is
                   wrapped by build(), and its get() is then also called with `stop=`, the
                   timer's event: kitlib.hubsite.serveviews, "Views")
      sync         callable(store_dir, commit_every_s, remote=, branch=, push=) -> a
                   kitlib.hubsite.gitsync.GitSync (or its like: start, stop, sync_once, status)
      access_keys  callable(team) -> the tunnel gate's key source (get(kid)); None:
                   kitlib.hubsite.access.KeyCache, which asks Cloudflare for the team's keys
                   when a token first needs one. Called only when the tunnel listener starts.
                   What it raises turns the tunnel off (TUNNEL_FAILED). A key source may also
                   have ready() (are there keys now: health's `tunnel`; one without it never
                   counts as having them, so health says TUNNEL_BOUND while its tokens are
                   checked all the same), `error` and stop() (called first at the close)
      access_now   callable() -> the Unix time the tunnel gate checks a token's times against;
                   None: time.time
      push         push notifications (not the store's git push): the host's delivery object,
                   deliver(recipient, text) and is_pm(recipient) (kitlib.hubsite.push); None:
                   no push notifications
      push_dir     the folder push notifications keep their outbox and undelivered log in (the
                   launcher's state folder); push notifications run only when both are set
      lock         the machine lock's door for the Api's lock routes (kitlib.hubsite.lockdoor
                   LockDoor, or its like: revision, view, stats); None: those routes answer
                   `unavailable`
      jobs         the timed jobs (kitlib.hubsite.jobs): [make(sources) -> jobs.Job or None],
                   one per job, each with its `folder` (serve.code_folders) and `module` (the
                   hubjobs package's makers: hubjobs.machine_sources passes them to
                   machine_sources; a scratch store has none); [] or None: none
      home         the home folder machine_sources read the machine config under (None: the
                   user's), for a job that reads it again
      pending      callable(for_key) -> the pending items for a session key (Api.pending: each
                   with kind, id, from, at). Set by build() once the Api exists, before the
                   jobs are made; None before. Read only: unlike GET pending it sets no handed/seen
                   marks (the routing's own clean-up of marks for items gone is not one).
      handoffs     callable() -> {session key: its newest hand-off record} (api.newest_handoffs).
                   Set by build() as `pending` is; None before. Read only.
    """

    def __init__(self, tokens=None, static_dir=STATIC_DIR, pm_data=None, notices=None, repos=None,
                 usage=None, agents=None, backlog=None, sync=None, access_keys=None,
                 access_now=None, known_repos=None, push=None, push_dir=None, lock=None,
                 findings=None, skills=None, jobs=None, home=None):
        self.jobs = list(jobs or [])
        self.home = home
        self.pending = None
        self.handoffs = None
        self.findings = findings
        self.lock = lock
        self.push = push
        self.push_dir = push_dir
        self.known_repos = known_repos
        self.tokens = tokens
        self.static_dir = static_dir
        self.pm_data = pm_data
        self.notices = notices
        self.repos = repos
        self.usage = usage
        self.agents = agents
        self.skills = skills
        self.backlog = backlog
        self.sync = sync
        self.access_keys = access_keys
        self.access_now = access_now


TOPLEVEL_MISS_S = 60.0      # seconds a folder git found no top level for is not asked again


def _toplevels(clock=time.monotonic):
    """pmconfig._toplevel with what it finds kept: a callable(folder) -> the basename of its git
    top level, git asked once per folder for the process's life. A miss (no git, not a work
    tree, a timeout) is kept for TOPLEVEL_MISS_S only, so a failed run neither starts git on
    every read nor leaves the name out for good. Called from several threads (requests, the
    backlog timer, waits): a dict get and set each, and two callers that both miss each run git
    once, nothing worse."""
    tops = {}

    def toplevel(folder):
        kept = tops.get(folder)
        if kept is not None and (kept[0] or clock() - kept[1] < TOPLEVEL_MISS_S):
            return kept[0]
        name = pmconfig._toplevel(folder)
        tops[folder] = (name, clock())
        return name
    return toplevel


def _repo_pairs(cfg, toplevel=None):
    """A callable giving backlog.Views its (name, root) pairs from the pm_start config `cfg`,
    each root as kitlib.hubbacklog.repo_root finds it. The PM repository's root is asked of git
    once, and so is each folder pmconfig.known_repos names a repository by (`toplevel`, a
    _toplevels(); default a new one); sessions_file is read on each call."""
    found = []
    toplevel = toplevel or _toplevels()

    def pairs():
        if not found:
            top = hubbacklog.git(os.path.dirname(os.path.abspath(cfg['sessions_file'])),
                                 'rev-parse', '--show-toplevel')
            found.append(top.decode('utf-8').strip())
        return backlog.repo_list(pmconfig.known_repos(cfg, toplevel=toplevel), found[0],
                                 cfg.get('repo_paths'))
    return pairs


def agents_dir(environ=None):
    """The rendered agents folder of the host this server runs for (PM_KIT_HOST, else the
    registry's default: kitlib.host.registry.selected): <kit>/<its shell>/agents. None when
    hosts.json cannot name one, so the agents view is simply not there."""
    from kitlib.host import registry
    try:
        shell = registry.host(registry.selected(environ)).shell
    except registry.RegistryError:
        return None
    return os.path.join(kitroot.root(), os.path.normpath(shell), 'agents')


def skills_dir():
    """The kit's skill sources, <kit>/content/skills: never a host's rendered copy."""
    return os.path.join(kitroot.root(), 'content', 'skills')


def _configured_repos(cfg, toplevel=None):
    """A callable giving pmconfig.known_repos of the pm_start config `cfg`, the store's repo-alias
    check's names. As in _repo_pairs, git is asked once: each folder's git top level (the PM's
    repository and the kit's, two git runs) is kept (`toplevel`, a _toplevels(); default a new
    one), while sessions_file is read on each call, so
    a new coordinator's repository counts at once, as on the repo menu (derived.repos). () when
    the names cannot be read, since a store write must never fail on them: KeyError no
    sessions_file, ValueError bad "repos" or bad JSON, OSError an unreadable sessions file,
    AttributeError a sessions file whose JSON is not an object, TypeError a sessions_file that is
    not a path. No git is not an error: pmconfig._toplevel gives None and the name is left out.
    Anything else is a bug in this code and is left to raise."""
    toplevel = toplevel or _toplevels()

    def names():
        try:
            return list(pmconfig.known_repos(cfg, toplevel=toplevel)) + sorted(
                {k for ks in _session_aliases(cfg).values() for k in ks}, key=str.lower)
        except (KeyError, ValueError, OSError, AttributeError, TypeError):
            return ()
    names.session_keys = lambda: _session_aliases(cfg)     # api._schedule_repo maps keys to repos
    return names


def _session_aliases(cfg):
    """{repo: [hub keys]} from the sessions file of `cfg`: each session's key (what the hub's
    repo tags use, such as `kit` for the kit's repository) is an alias of that session's "repo".
    {} when the file cannot be read, is not an object or names no repo, so the hub falls back to
    the repo names alone."""
    try:
        sessions = json.loads(pmconfig._read(cfg['sessions_file']))
        out = {}
        for key, e in sessions.items():
            if (isinstance(e, dict) and isinstance(e.get('repo'), str) and e['repo']
                    and isinstance(key, str) and key and key.lower() != e['repo'].lower()):
                out.setdefault(e['repo'], []).append(key)
        return out
    except (KeyError, ValueError, OSError, AttributeError, TypeError):
        return {}


def _repos_with_session_keys(cfg, stored, toplevel):
    """derived.repos of `cfg` with each repository's session keys added to its aliases."""
    found = derived.repos(cfg, stored, toplevel=toplevel)
    keys = {r.lower(): ks for r, ks in _session_aliases(cfg).items()}
    for r in found:
        r['aliases'] = r['aliases'] + [k for k in keys.get(r['name'].lower(), ())
                                       if k not in r['aliases']]
    return found


def machine_sources(home=None, sync=True, jobs=()):
    """This machine's Sources, read from the machine config under `home` (default the user's).
    It writes nothing and starts no git. sync=False: no git sync and none of `jobs` (a scratch
    store). `jobs`: the timed jobs' makers (Sources.jobs), handed by the caller. The lock
    door reads the machine lock (its own fixed folder, not `home`) on its first call, never
    here. ConfigError when the machine config cannot be used."""
    path = user.readable_file(home)
    cfg = pmconfig.load_config(path)
    try:
        pm = pmdata.machine_settings(home)
    except pmdata.PmDataError as e:
        raise ConfigError(str(e))
    src = Sources(tokens=_token.folder(home), pm_data=pm,
                  notices=pmwatch.configured_notice_dir(path),
                  agents=agents_dir(), skills=skills_dir(),
                  sync=gitsync.GitSync if sync else None, home=home)
    # The timed jobs, as handed (no line here per job). `sync` is false only for a scratch store
    # (tools/hub.py serve --store), which runs no machine job.
    if sync:
        src.jobs.extend(jobs)
    if pm and pm.get('path'):
        from kitlib.maintlint import result as lint_result
        src.findings = os.path.join(pm['path'], lint_result.FOLDER)
    from . import lockdoor          # here: only the machine's server reads the machine lock
    src.lock = lockdoor.LockDoor()
    if pmwatch.launcher_configured(home):
        def report():
            from kitlib.launcher import settings as launcher_settings   # here: only this needs it
            from kitlib.launcher import usage as launcher_usage
            return launcher_usage.report(launcher_settings.load(home))
        src.usage = report
    if cfg and cfg.get('sessions_file'):
        tops = _toplevels()         # one memo: the repo menu, the alias check, the backlog
        webs = repoweb.Webs(_repo_pairs(cfg, tops))     # each repo's web address, for repo-path links
        src.repos = lambda stored: webs.annotate(_repos_with_session_keys(cfg, stored, tops))
        src.known_repos = _configured_repos(cfg, tops)
        src.backlog = lambda refresh_s: backlog.Views(_repo_pairs(cfg, tops), refresh_s)
    return src
