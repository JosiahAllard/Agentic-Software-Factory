"""The refusals a landing makes before its merge (docs/gate-landing.md "Landing", steps 2 to
4; docs/gate-landing-policy.md): the pair and tip checks, the tip's gate record, the selected
gate's advice, the strict paths, the gate config, the queued verdict and its review, the scope
check and the owner-only paths. The mixin Checks of kitlib.landing.lander.Lander: it holds no
state of its own; every attribute is Lander.__init__'s.
"""

import ast
import itertools
import os

from kitlib.backlog import at_landing
from kitlib.backlog import narrowed as nmod
from kitlib.backlog import record as rmod
from kitlib.backlog import scope as scmod
from kitlib.backlog import store as smod
from kitlib.backlog import store_folder, store_open, store_read, store_ref
from kitlib.backlog.errors import Refusal
import kitlib.config as kconfig
from kitlib.config.types import ConfigError
from kitlib.content import oncommit
from kitlib.gate import detect, reuse, runner, strict_defaults
from kitlib.gate import rerun
from kitlib.landing import carry, verdicts
from kitlib.gate import steps as S
from kitlib.git import cmd, refs, repo
from kitlib.ownerfiles import rules as ownerrules
from kitlib.records import gate_ok
from kitlib.records import verdict_entry


FETCH_TIMEOUT = 300
# The scope check (step 5, Lander.scope_check): True refuses a branch that changes a path outside
# the scopes of the records it holds (REFUSED, nothing merged); False lands it with a WARNING
# after the LANDED line. It warns for now; making it refuse is this one line (a follow-up record).
SCOPE_REFUSES = False
# Paths every branch may change whatever records it holds, as scope members: tests, and the doc
# index the doc-budget check makes every new doc add a line to. A folder store's own folder is
# allowed too (Lander._scope_stores). The files tools/render_content.py writes are not: kitlib
# cannot import tools/ to learn them, so a rendering change needs them in a record's scope.
SCOPE_ALWAYS = ('tests/**', 'docs/INDEX.md')
SCOPE_SHOWN = 10        # outside paths a scope-check line names; more are counted
NARROWED_RULES = rmod.Rules('mixed')    # reads every store's `narrowed:` ids (_scope_restore)
# The strict paths (Lander.strict_check; docs/gate-landing-policy.md "Strict paths"): the globs of this file's
# STRICT_DEFAULTS, owner-authored, read from the base with `git show` and parsed, never imported
# from the branch; a base without the file (a repository that is not the kit) has the running
# kit's list. A branch changing a matching path lands only alone, with a queued verdict at its tip
# from a strong-tier reviewer who is not its author, and its merged gate runs fresh.
STRICT_FILE = 'kitlib/gate/strict_defaults.py'
STRICT_NAME = 'STRICT_DEFAULTS'
STRICT_SHOWN = 5        # strict paths a refusal names; more are counted


def _what(e):
    """An exception as a report says it: `Ctrl+C`, or `Type: message` on one line."""
    if isinstance(e, KeyboardInterrupt):
        return 'Ctrl+C'
    return ' '.join(('%s: %s' % (type(e).__name__, e)).split())


# A changed path against a record's scope: one copy, shared with commit.py's narrowed-record check
# (scope.covers, area_lead, in_repo, changed_path_error; all pure).
_covers = scmod.covers
_area_lead = scmod.area_lead
_in_repo = scmod.in_repo
_path_error = scmod.changed_path_error


def _unnamed(path):
    """True when no scope member can name `path` as itself: a comma or a wildcard character is a
    parse error in the scope grammar (kitlib.backlog.scope). A folder member that holds it covers
    it; it is left out of the widening command. Pure."""
    return ',' in path or any(ch in scmod.WILDCARD_CHARS for ch in path)


def parse_strict(text):
    """The globs `text`, a strict_defaults.py, assigns to STRICT_DEFAULTS: read with ast, never
    run, so nothing of the file executes. Raises ValueError unless it assigns STRICT_DEFAULTS
    exactly once, at the top level, a literal tuple or list of non-empty strings. Pure."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError) as e:
        raise ValueError('it does not parse: %s' % ' '.join(str(e).split()))
    values = []
    for node in tree.body:
        targets = (node.targets if isinstance(node, ast.Assign) else
                   [node.target] if isinstance(node, (ast.AnnAssign, ast.AugAssign)) else [])
        if any(isinstance(t, ast.Name) and t.id == STRICT_NAME for t in targets):
            values.append(node.value if isinstance(node, ast.Assign) and len(targets) == 1
                          else None)
    if len(values) != 1:
        raise ValueError('it assigns %s %d times, not once' % (STRICT_NAME, len(values)))
    try:
        globs = ast.literal_eval(values[0]) if values[0] is not None else None
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        globs = None
    if not isinstance(globs, (tuple, list)):
        raise ValueError('%s is not a literal tuple of globs' % STRICT_NAME)
    if not globs or not all(isinstance(g, str) and g.strip() for g in globs):
        raise ValueError('%s is not a tuple of non-empty glob strings' % STRICT_NAME)
    return tuple(globs)


def _closing_reader(root, rev):
    """`history(rid)` of the store whose commits are in `root` at `rev`: `<sha> <subject>` of the
    last one whose subject closes, retires or deletes `rid`, or None (the store's own history
    reader, which `next`'s offer.ended_reader reads too)."""
    return lambda rid: store_read._last_closing(root, rid, rev)


class _Held(object):
    """What the scope check found for one branch: `held`, id -> [scope members, command(*args)
    for its store's write, area or None (_area_lead)]; `read`, a store holding a record was
    read; `unread`, why a store could not be; `folders`, folder stores' own folders (always
    allowed); `narrowed`, id -> its `narrowed:` values, for each id held that carries one
    (scope_check refuses their branch's miss); `states`, id -> the states of the readable records
    of that id in the stores read (_scope_take), and `histories`, each store's `history(rid)`
    (_closing_reader), for `live`."""

    def __init__(self):
        self.held, self.read, self.unread, self.folders = {}, False, [], []
        self.narrowed, self.states, self.histories = {}, {}, []

    def live(self, rid):
        """Whether the other record `rid` of a `narrowed:` line still holds its part, read as
        `next` reads it (offer.ended_reader): a store read holds it and not open (pulled, blocked
        or parked), or none does and no store's history has a commit whose subject closes,
        retires or deletes it (store.history's match). A file removed by any other commit (a
        hand `git rm` or `git mv`, a revert), an id that is not plain, or a history git cannot
        read says nothing: the part is held."""
        if rid in self.states:
            return any(s != 'open' for s in self.states[rid])
        if not store_read.PLAIN_ID.match(rid):
            return True
        for history in self.histories:
            try:
                if history(rid):
                    return False
            except Refusal:
                continue
        return True

    def hold(self, rid, members, command, area=None, narrowed=()):
        if narrowed:
            self.narrowed.setdefault(rid, [])
            self.narrowed[rid].extend(v for v in narrowed if v not in self.narrowed[rid])
        if rid in self.held:
            self.held[rid][0].extend(m for m in members if m not in self.held[rid][0])
        else:
            self.held[rid] = [list(members), command, area]

    def members(self, tops):
        """Every member held, as paths of this repository (_in_repo)."""
        return [m for members, _, area in self.held.values()
                for m in _in_repo(members, area, tops)]


class Checks(object):
    """Lander's refusals before the merge (this module's docstring)."""

    def pair_checks(self):
        """The locked checks of the batch's branches and tips (self.pairs), after the checks of
        the main checkout (locked_checks): each tip still the branch's, none already in main, none
        inside another, the owner and ancestry checks, the tips' gate records, the strict, gate
        config and verdict checks. None, or the first refusal. A fold runs them for the landing's
        own branches with each submission it would fold, then for the whole batch."""
        for b, t in self.pairs:
            tip = self.ref('refs/heads/%s' % b)
            if tip != t:
                return '%s is at %s, and the caller expects %s' % (b, (tip or '?')[:10], t[:10])
        for b, t in self.pairs:
            landed, why = refs.is_ancestor(t, self.expect_main, self.root)
            if landed is not False:
                return why or '%s (%s) is already in %s: nothing to land' % (
                    b, t[:10], self.cfg.main_branch)
        for (b, t), (b2, t2) in itertools.permutations(self.pairs, 2):
            inside, why = refs.is_ancestor(t, t2, self.root)
            if inside is not False:
                return why or '%s (%s) is already in %s (%s), in the same batch: land %s alone' % (
                    b, t[:10], b2, t2[:10], b2)
        for t in self.tips:
            why = self.owner_refusal(t)
            if why:
                return why
        for t in self.tips:
            why = at_landing.ancestry_refusal(self.root, self.cfg, t, env=self.git_env)
            if why:
                return why
        return (self.tip_gate_check() or self.strict_check() or self.config_check()
                or self.verdict_check())

    def tip_gate_check(self):
        """A locked check (docs/gate-landing.md "Landing"): every branch tip has a gate-OK record for
        exactly that commit that git still agrees with (gate_ok.verify), and the record lands it
        (tip_refusal): a `gate.py --full` pass's (gate_ok.full_of), or a default-mode or docs-only
        pass's that the base config takes (select_refusal). A record from before records said
        whether the gate was --full, one whose mode is unknown, and a record of any other commit
        do not count; an --only run writes none. A tip with neither lands on a FLAKE targeted
        rerun of its latest red whole run, --full or default-mode (kitlib.gate.rerun.verify_flake).
        A tip a test failed on, when that test failed twice over the branch's commits since its
        merge base with the base, or whose rerun records cannot be read, never lands, whatever
        record it has (rerun.twice_refusal). None, or the first miss: why, and what gates that tip
        (gate_advice; REFUSED, nothing merged). It only adds to the merged gate. A base gate that
        --full selects no step from refuses first (no_full_step).

        The record's `gate_config` is not compared with any config: the branch gate writes a
        record only for a clean tree, with `config_rev` the tip, so the hash is of the tip's own
        committed config. Re-reading that config here would use the landing kit's defaults and
        schema, which refuse honest records whenever the two kits differ (a kit branch that
        changes the gate's defaults or adds a config key); and the config that decides what
        lands is the base's, which the merged gate reads, and which alone decides whether a
        record that is not --full counts (a branch never loosens its own landing)."""
        why = self.no_full_step()
        if why:
            return why
        for b, t in self.pairs:
            why = rerun.twice_refusal(self.root, t, self.expect_main)
            if why:
                return '%s tip %s does not land: %s' % (b, t[:10], why)
            why = self.tip_refusal(t)
            if why:
                return '%s tip %s has no gate-OK record that lands it: %s; %s' % (
                    b, t[:10], why, self.gate_advice(b, t))
        return None

    def tip_refusal(self, tip):
        """Why `tip`'s gate-OK record does not land it, or None. A --full record (verify_full)
        lands it; a default-mode ("select") or docs-only ("docs") one lands it when select_refusal
        takes it, which says why not; a record of no mode a landing takes (gate_ok.mode_of None)
        and one from before records said `full` are refused as such; with no record, verify's
        reason. Failing those, the tip lands when its latest red whole run has a FLAKE targeted
        rerun (rerun.verify_flake; its reason is added only when the tip has rerun records). The
        record it lands on goes in self.tip_records."""
        self.tip_records.pop(tip, None)
        rec, why = gate_ok.verify_full(self.root, tip)
        if why is None:
            self.tip_records[tip] = rec
            return None
        rec, _ = gate_ok.verify(self.root, tip)
        mode = gate_ok.mode_of(rec) if rec is not None else None
        if mode in ('select', 'docs'):
            why = self.select_refusal(rec, tip, mode)
            if why is None:
                self.tip_records[tip] = rec
                return None
        elif rec is not None and 'full' in rec:
            why = 'the record for %s is of no mode a landing takes (mode %s, full %s)' % (
                rec['commit'], rec.get('mode'), rec.get('full'))
        flake = rerun.verify_flake(self.root, tip)
        if flake is None:
            return None
        if rerun.has_records(self.root, tip):
            why = '%s; its targeted rerun does not land it: %s' % (why, flake)
        return why

    def select_refusal(self, rec, tip, mode):
        """Why a verified record of `tip` that is not --full (`mode` "select" or "docs") does not
        land it, or None. Everything is read from the base's config (self.cfg), never the tip's.
        The landing diff is merge-base(tip, base)..tip. A record counts when the files its own
        diff (its `base`..tip) changes cover the landing diff (the steps it ran were picked for at
        least that change), and diff_refusal's check of the landing diff takes it: a default-mode
        pass lands any change, a docs-only pass only a docs-only one. A change to a strict path or
        a gate config file lands on a default-mode record too: its merged gate then runs fresh
        (strict_escalate) or --full (config_escalate; ARCHITECTURE.md "What a landing runs")."""
        mb, why = refs.merge_base(tip, self.expect_main, self.root)
        if not mb:
            return 'git cannot find the merge base of the tip and %s: %s' % (
                self.expect_main[:10], why or 'none')
        changed, why = runner.changed_between(self.root, mb, tip)
        if changed is None:
            return 'git cannot list what the tip changes: %s' % why
        if not refs.is_sha(rec.get('base') or ''):
            return 'the record names no base, so what its steps were picked for is unknown'
        gated, why = runner.changed_between(self.root, rec['base'], tip)
        if gated is None:
            return 'git cannot list what the record\'s gate saw: %s' % why
        seen = set(reuse.changed_list(gated))
        unseen = [p for p in reuse.changed_list(changed) if p not in seen]
        if unseen:
            return ('its gate ran against %s, whose diff to the tip leaves out %s: gate the tip '
                    'again against %s' % (rec['base'][:10], ', '.join(unseen[:10]),
                                          self.expect_main[:10]))
        passed = {s.get('name') for s in rec.get('steps') or []
                  if isinstance(s, dict) and s.get('result') in ('pass', 'warn', 'reused')}
        return self.diff_refusal(changed, mode, passed)

    def diff_refusal(self, changed, mode, passed):
        """select_refusal's check of the landing diff `changed` for a record of `mode` ("select"
        or "docs") whose passed steps are the names `passed`: None, or why such a record does not
        land it. A change docs-only under the base (reuse.docs_only_change: its "gate".docs_only,
        never a config, hook or strict path) lands when every base docs_only step passed in the
        record (pass, warn or reused; `passed` None, a gate not yet run, skips that check); any
        other change lands on a default-mode record, and never on a docs-only one."""
        gate = self.cfg.section('gate')
        if reuse.docs_only_change(self.cfg, gate, changed):
            if passed is None:
                return None
            missing = [n for n in S.docs_steps(gate) if n not in passed]
            if not missing:
                return None
            return 'the change is docs-only, and the base\'s docs_only step %s did not pass in it' % (
                ', '.join(missing))
        if mode != 'select':
            return 'its gate ran only the docs-only steps, and the change is not docs-only under the base'
        return None

    def no_full_step(self):
        """Why no tip can be gated, or None: the base's `.kit.json` has a "gate" section
        (detect.configured) from which --full selects no step (steps.select with full and no
        changed path): every step is `manual`, there is none, or each is dropped by another
        selected step's skip_if_selected. A branch gate and the merged gate select from those
        same steps, so both would refuse and the tip check's command could never be met."""
        if not detect.configured(self.cfg):
            return None
        _, picked = S.select(self.cfg.section('gate'), [], [], full=True)
        if picked:
            return None
        return ('the "gate" section of .kit.json at %s leaves no step for --full to select (every '
                'step is manual, or is dropped by another selected step\'s skip_if_selected), so '
                'neither `gate.py --full` nor the merged gate would run anything (both refuse: '
                'no gate step selected); no branch can land until %s gives the gate a step that '
                'is not manual and that no other step\'s skip_if_selected names'
                % (self.expect_main[:10], self.cfg.main_branch))

    def gate_advice(self, branch, tip):
        """What gates `branch`'s tip: `gate that tip first: <gate_command>`. When
        the kit lands itself (oncommit.is_kit) the command runs the branch's own gate.py, and one
        from before records said `full` never writes it: a tip whose record lacks the key (the
        record gate_ok.verify takes, without `full`) is told to merge <remote>/<main> in first,
        then gate and land the merge. Any other repository's command runs the running kit's
        gate.py, which writes it whatever the branch holds."""
        command = self.gate_command(branch)
        if oncommit.is_kit(self.root):
            rec, _ = gate_ok.verify(self.root, tip)
            if rec is not None and 'full' not in rec:
                return ('merge %s/%s (%s) into %s first (a branch\'s own gate.py from before '
                        'records said `full` never writes it), then gate the merge and land '
                        'that tip: %s' % (self.cfg.remote, self.cfg.main_branch,
                                          self.expect_main[:10], branch, command))
        return 'gate that tip first: %s' % command

    def gate_command(self, branch):
        """The command that gates `branch`'s worktree in default mode: `python "<gate.py>" -C
        "<worktree>"`. The gate.py is the worktree's own when the repository being landed is the kit
        (its main checkout has the kit's layout, oncommit.is_kit: the branch is gated by its own
        gate code; docs/gate-rerun-checkout.md "The caller's checkout"), whether the running kit is a checkout or
        a plugin export; any other repository's is the running kit's. `<worktree>` stands in when
        no worktree has the branch out."""
        wts, _ = repo.worktrees(self.root)
        wt = next((w.path for w in wts or [] if w.branch == branch), None) or '<worktree>'
        gate_py = os.path.join(wt if oncommit.is_kit(self.root) else S.kit_live(), 'tools', 'gate.py')
        return 'python "%s" -C "%s"' % (gate_py, wt)

    def strict_globs(self):
        """`(globs, None)`: STRICT_DEFAULTS as STRICT_FILE holds it at the base (parse_strict;
        never imported, never read from a branch), or the running kit's list when the base has no
        such file (a repository that is not the kit); `(None, why)` when it cannot be read."""
        base = self.expect_main
        r = self.git('ls-tree', '--name-only', base, '--', STRICT_FILE)
        if r.rc != 0:
            return None, 'git ls-tree %s: %s' % (base[:10], r.err or 'exit %s' % r.rc)
        if not cmd.text(r.out or b'').strip():
            return tuple(strict_defaults.STRICT_DEFAULTS), None
        r = self.git('show', '%s:%s' % (base, STRICT_FILE))
        if r.rc != 0:
            return None, 'git show %s:%s: %s' % (base[:10], STRICT_FILE, r.err or 'exit %s' % r.rc)
        try:
            return parse_strict(cmd.text(r.out or b'')), None
        except ValueError as e:
            return None, '%s: %s' % (STRICT_FILE, e)

    def strict_paths(self):
        """`(hits, None)`: branch -> the strict paths it changes (merge base with the base to its
        tip, matched against strict_globs()), for each branch with any; or `(None, why)` when the
        list or a branch's paths cannot be read. Reads only: strict_check and check_strict (the
        dry check) both decide by it."""
        globs, why = self.strict_globs()
        if globs is None:
            return None, 'cannot read the strict paths from %s at %s: %s' % (
                self.cfg.main_branch, self.expect_main[:10], why)
        hits = {}
        for b, t in self.pairs:
            mb, why = refs.merge_base(self.expect_main, t, self.root)
            changed, why2 = runner.changed_between(self.root, mb, t) if mb else (None, why)
            if changed is None:
                return None, 'git cannot list the paths of %s to check strict paths: %s' % (
                    b, why2 or why or 'no common commit with %s' % self.expect_main[:10])
            found = [p for p in changed if S._match(p, globs)]
            if found:
                hits[b] = found
        return hits, None

    def strict_check(self):
        """A locked check (docs/gate-landing-policy.md "Strict paths"): the paths each branch changes (merge base
        with the base to its tip) against strict_globs(). A branch with a hit lands only alone,
        with a verdict queued for its tip (kitlib.landing.verdicts) whose reviewer ran on a
        strong-tier model (verdicts.strong_alias), whoever built it, and is not its author
        (verdicts.is_author); then
        self.strict_hits and self.strict_review are set, and _locked makes the merged gate run
        fresh (strict_escalate). None, or why not (REFUSED, nothing merged). Every read that fails
        refuses."""
        self.strict_hits, self.strict_review, self.strict_carry = {}, None, None
        hits, why = self.strict_paths()
        if why:
            return why
        self.strict_hits = hits
        if not self.strict_hits:
            return None
        if len(self.pairs) > 1:
            b = next(b for b in self.branches if b in self.strict_hits)
            return ('%s: a branch that changes a strict path lands alone, never in a batch'
                    % self.strict_says(b))
        (b, t), = self.pairs
        why = self.review_refusal(b, t, strict=True)
        if why:
            return '%s: %s' % (self.strict_says(b), why)
        return None

    def branch_paths(self, t):
        """`(the paths the tip t changes from its merge base with the base, None)`, or `(None,
        why)`."""
        mb, why = refs.merge_base(self.expect_main, t, self.root)
        if not mb:
            return None, why or 'no common commit with %s' % self.expect_main[:10]
        return runner.changed_between(self.root, mb, t)

    def config_paths(self):
        """`(hits, None)`: branch -> the gate config files it changes (reuse.config_hits: the
        base's `.kit.json`, a section file, the hook module; merge base with the base to its
        tip), for each branch with any; or `(None, why)` when a branch's paths cannot be read."""
        hits = {}
        for b, t in self.pairs:
            changed, why = self.branch_paths(t)
            if changed is None:
                return None, 'git cannot list the paths of %s to check the gate\'s config files: %s' % (
                    b, why)
            found = reuse.config_hits(self.cfg, changed)
            if found:
                hits[b] = found
        return hits, None

    def config_check(self):
        """A locked check (docs/gate-landing-policy.md "Strict paths"), after strict_check: in a batch, a branch
        that changes a gate config file (config_paths) is refused, as a strict branch is: it lands
        alone, never in a batch. A batch whose paths cannot be read refuses. None, or why not
        (REFUSED, nothing merged)."""
        if len(self.pairs) < 2:
            return None
        hits, why = self.config_paths()
        if why:
            return why
        for b in self.branches:
            if b in hits:
                return ('%s changes the gate\'s config (%s): a branch that changes a gate config '
                        'file lands alone, never in a batch' % (b, ', '.join(hits[b][:STRICT_SHOWN])))
        return None

    def verdict_check(self):
        """A locked check (docs/gate-landing.md "Landing" step 4), after strict_check: every branch that
        changes no strict path lands too only with a verdict queued for its tip by a reviewer who
        is not its author (review_refusal; the tier rule only for a High-risk one), each branch of
        a batch on its own entry. None, or why not for the first branch, in batch order, that
        misses (REFUSED, nothing merged)."""
        for b, t in self.pairs:
            if b in self.strict_hits:       # strict_check passed it, tier rule and all
                continue
            why = self.review_refusal(b, t, strict=False)
            if why:
                return '%s: %s' % (b, why)
        return None

    def strict_says(self, b):
        hits = self.strict_hits[b]
        more = len(hits) - STRICT_SHOWN
        return '%s changes strict path %s%s' % (
            b, ', '.join(hits[:STRICT_SHOWN]), ' and %d more' % more if more > 0 else '')

    def review_refusal(self, b, t, strict):
        """None when the verdict queued for branch `b` lets its tip `t` land; else why not. Every
        landing: an entry that can be used, queued (not marked failed), at `t`, its reviewer not
        the author (verdicts.is_author). A carried entry (verdicts.carry) lands only when
        kitlib.landing.carry.check passes again, from the commit reviewed to `t` against
        self.expect_main. The tier rule (verdicts.tier_why: a strong-tier reviewer, whoever built
        the branch) holds for a High-risk entry and for `strict` (a branch that changes a strict
        path), which also sets self.strict_review to the entry and self.strict_carry to the carry's
        facts, which strict_escalate reports."""
        common, why = repo.common_dir(self.root)
        if not common:
            return 'its verdict queue cannot be found: %s' % (why or 'no git common dir')
        try:
            entry = verdicts._load(verdict_entry.entry_path(common, b), b)
        except verdict_entry.VerdictError as e:
            return 'its verdict entry cannot be used: %s' % e
        if entry is None:
            return ('it lands only with a queued verdict (tools/verdict.py add), and none is '
                    'queued for it')
        if entry['state'] != verdict_entry.QUEUED:
            return ('its verdict entry is marked %s (%s): queue it again with tools/verdict.py '
                    'add' % (entry['state'], entry['last_result'] or 'no reason given'))
        if entry['tip'].lower() != t.lower():
            return ('the verdict was given at %s, and the branch is at %s: review and queue the '
                    'tip, or carry the verdict (tools/verdict.py carry) when a main sync is all '
                    'that changed' % (entry['tip'][:10], t[:10]))
        facts = None
        if verdict_entry.CARRIED in entry:
            # Never the stored facts: the carry is checked again here, against the base this
            # landing merges onto (docs/gate-landing-policy.md "Carrying a verdict forward").
            reviewed = entry[verdict_entry.CARRIED]['from']
            facts, why = carry.check(self.root, reviewed, t, self.expect_main,
                                     git=lambda args: self.git(*args))
            if facts is None:
                return ('the verdict was given at %s and carried to %s, but the carry does not '
                        'hold against %s %s: %s; review and queue the tip'
                        % (reviewed[:10], t[:10], self.cfg.main_branch, self.expect_main[:10], why))
        why = verdicts.tier_why(entry, strict, verdicts.tier_models)
        if why:
            return why
        if verdicts.is_author(entry):
            return ('the reviewer %s (%s) is its author (%s): it lands with a review by an agent '
                    'that did not write it' % (entry['reviewer_id'], entry['reviewer'],
                                              entry['author']))
        if strict:
            self.strict_review, self.strict_carry = entry, facts
        return None

    def scope_check(self):
        """The last locked check: every path each branch changes (merge base to tip, renames as a
        delete and an add) falls in the scope of a record the branch holds - pulled by it, or
        named by a `Closes:` trailer of its own commits while pulled by anyone - or in
        SCOPE_ALWAYS or a folder store's folder. A branch's own commits are `tip --not` the base
        and the batch's earlier tips, so a stacked branch is checked on its own paths. A miss, a
        store it cannot read while some path is outside what it could read, or a check that
        stops: with SCOPE_REFUSES the refusal; else a WARNING kept for after the LANDED line, and
        None. A miss of a branch holding a narrowed record (a `narrowed:` line; docs/gate-landing.md
        "Landing", step 4) is refused whatever SCOPE_REFUSES says; a store it cannot read, or a check
        that stops, cannot tell, so that stays a WARNING. A branch holding no record while a store
        with records was read: a NOTE kept for before the LANDED line."""
        for i, (b, t) in enumerate(self.pairs):
            narrowed = []
            try:
                why = self._scope_of(b, t, [self.expect_main] + self.tips[:i], narrowed)
            except Exception as e:      # noqa: BLE001 - a check that cannot finish is a miss
                why = 'the scope check of %s stopped (%s)' % (b, _what(e))
                narrowed = []
            if not why:
                continue
            if SCOPE_REFUSES or narrowed:
                return why
            self.scope_warnings.append(
                'WARNING: %s; the landing went ahead: the scope check only warns for now '
                '(kitlib.landing.checks SCOPE_REFUSES)' % why)
        return None

    def _scope_base(self, t, bases):
        """`(the merge base of tip t with every base, None)`, or `(None, why)`."""
        if len(bases) == 1:
            mb, why = refs.merge_base(t, bases[0], self.root)
            return (mb or None), (why or 'no common commit with %s' % bases[0][:10])
        r = cmd.call(['merge-base', t] + list(bases), self.root)
        out = cmd.text(r.out).strip() if r.out else ''
        if r.rc == 0 and refs.is_sha(out):
            return out, None
        return None, r.err or 'git merge-base exited %s' % r.rc

    def _scope_of(self, b, t, bases, narrowed=None):
        """None when branch `b` at `t` keeps to its scopes; else why not (scope_check). A miss of
        a branch holding a narrowed record adds those ids to the list `narrowed`."""
        mb, why = self._scope_base(t, bases)
        changed, why2 = runner.changed_between(self.root, mb, t) if mb else (None, why)
        if changed is None:
            return ('git cannot list the paths of %s to check them against the scopes of the '
                    'records it holds: %s' % (b, why2 or why))
        rest = [p for p in changed if not _covers(SCOPE_ALWAYS, p)]
        if not rest:
            return None
        out, why = cmd.run_text(['log', '--format=%B%x00', t, '--not'] + list(bases) + ['--'],
                                self.root, strip=False)
        if out is None:
            return ('git cannot read the Closes trailers of %s to check its paths against the '
                    'scopes of the records it holds: %s' % (b, why))
        values = []
        for message in out.split('\0'):
            values.extend(at_landing.closes_values(message))
        named = set(at_landing.closes_ids(values))
        found = self._scope_stores(b, t, named, changed)
        rest = [p for p in rest if not _covers(found.folders, p)]
        if not rest:
            return None
        if not found.held:
            if found.unread:
                return self._scope_unread(b, found)
            if found.read:
                self.scope_notes.append('NOTE: %s holds no pulled record; its paths were not '
                                        'checked against a scope' % b)
            return None
        tops = set()
        for rev in [mb, t] if any(h[2] for h in found.held.values()) else []:
            out, why = cmd.run_text(['ls-tree', '--name-only', '-z', rev], self.root, strip=False)
            if out is None:
                return ('git cannot list the top-level entries of %s at %s to read its records\' '
                        'scopes as paths of this repository: %s' % (b, rev[:10], why))
            tops.update(p.casefold() for p in out.split('\0') if p)
        members = found.members(tops)
        outside = []
        for p in rest:
            if not _covers(members, p):
                err = _path_error(p)
                outside.append((p, 'undecidable: %s' % err if err else
                                'no scope member can name it' if _unnamed(p) else None))
        if not outside:
            return None
        if found.unread and not found.narrowed:     # a record it could not read may cover them
            return self._scope_unread(b, found)
        if narrowed is not None:
            narrowed.extend(sorted(found.narrowed))
        miss = self._scope_miss(b, t, mb, found, outside, tops)
        if found.unread:            # refused for the narrowed record all the same
            miss += ('; the store also held a record it could not read, so a path in that '
                     'record\'s scope may be counted outside: %s' % '; '.join(found.unread))
        return miss

    def _scope_unread(self, b, found):
        return ('cannot read the backlog store to check %s\'s paths against the scopes of the '
                'records it holds: %s' % (b, '; '.join(found.unread)))

    def _scope_miss(self, b, t, mb, found, outside, tops):
        """The miss line; its `set` command keeps the record's members and adds each path
        outside in the store's form: under the record's area prefix (_area_lead) when its
        members carry it."""
        ids = sorted(found.held)
        shown = ['%s (%s)' % (p, err) if err else p for p, err in outside[:SCOPE_SHOWN]]
        more = len(outside) - len(shown)
        listed = ', '.join(shown) + (', and %d more (git diff --name-only --no-renames %s %s)'
                                     % (more, mb, t) if more else '')
        members, command, area = found.held[ids[0]]
        lead = _area_lead(area, tops) if _in_repo(members, area, tops) != members else ''
        new = [p for p, err in outside if not err]
        if not new:
            how = 'land them on their own branch'
        else:
            if len(outside) > SCOPE_SHOWN:
                new = ['<each path, or a folder/** that covers them>']
            new = [lead + p for p in new]
            args = ['set', ids[0]] + ['--scope "%s"' % m
                                      for m in members + new if m != scmod.NONE]
            how = ('widen a record\'s scope first (set checks the new members against every live '
                   'pull\'s scope): %s; or land them on their own branch' % command(*args))
        if found.narrowed:
            how = '%s; or %s' % (self._scope_restore(found), how)
        return '%s changes %d path(s) outside the scopes of the records it holds (%s): %s; %s' % (
            b, len(outside), ', '.join(ids), listed, how)

    @staticmethod
    def _scope_restore(found):
        """The miss line's clause for the narrowed records held (found.narrowed): for each, the
        original members its `narrowed:` lines name and the `set` that restores them: `next`'s
        RESTORE form (narrowed.restore_set: each current member no original member holds kept, the
        lines cleared, a malformed one kept as written; docs/commit.md "A narrowed record").
        Lines are read with the `mixed` id grammar, which takes every store's ids: they come from
        more than one store, and a record's own store checked them when they were written.
        A record narrowed for several records (a split against several live pulls) gets no
        restore while one of them still holds its part (_Held.live): given back then, a member
        would cover that record's live paths again. The clause names each one still live and
        points at `next`'s RESTORE line, which restores each member once every record it was
        narrowed for has ended (offer._restore)."""
        parts = []
        for rid in sorted(found.narrowed):
            members, command, _area = found.held[rid]
            got = nmod.restore_set(rid, members, found.narrowed[rid], NARROWED_RULES)
            if got is None:
                parts.append('%s carries a narrowed: line that cannot be read: a lead restores its '
                             'original member by hand' % rid)
                continue
            originals, others, args = got
            live = [o for o in others if found.live(o)] if len(others) > 1 else []
            if live:
                parts.append('%s was narrowed off %s for %s: %s still %s, so no restore is given '
                             'here: `next`\'s RESTORE line gives each part back once the records '
                             'it was narrowed for have ended' % (rid, ', '.join(originals), ', '.join(others),
                                        ', '.join(live), 'holds its part' if len(live) == 1
                                        else 'hold their parts'))
                continue
            parts.append('%s was narrowed off %s for %s: a path given to %s waits until it lands, '
                         'then restore the original member with %s' % (
                             rid, ', '.join(originals), ', '.join(others), ', '.join(others),
                             command(*args)))
        return '; '.join(parts)

    def _scope_stores(self, b, t, named, changed):
        """_Held: the records `b` holds in the configured store and the PM data branch's store,
        with `named` the ids its `Closes:` trailers name. A folder store is read at the tip `t`
        (its records travel with the branch), counting a record file the branch deleted
        (`changed`) at its last version; a `ref` or `branch` store as the remote holds it."""
        found = _Held()
        root, cfg = self.root, self.cfg
        try:
            backend = store_open.open_backend(root, cfg, fetch=False)
        except (Refusal, OSError) as e:
            found.unread.append('the configured store: %s' % _what(e))
            backend = None

        def close_cmd(*args):
            return at_landing.close_command(root, *args)

        if isinstance(backend, store_folder.MainBackend):
            rel = backend.rel.replace('\\', '/').strip('/')
            found.folders.append(rel + '/**')
            records, _raw, why = store_read.records_in_tree(root, t, rel)
            if records is None:
                found.unread.append('%s at %s: %s' % (rel, t[:10], why))
            else:
                gone = [p[len(rel) + 1:-3] for p in changed
                        if p.startswith(rel + '/') and p.endswith('.md')
                        and '/' not in p[len(rel) + 1:]]
                self._scope_take(found, '%s at %s' % (rel, t[:10]), records, b, named, gone,
                                 lambda rid: self._scope_last(root, t, rel, rid), close_cmd)
                found.histories.append(_closing_reader(root, t))
        elif backend is not None:
            got = self.scope_reads.get('configured')
            if got is None:
                got = self.scope_reads['configured'] = self._scope_ref_records(root, backend)
            records, tip, why = got
            if records is None:
                found.unread.append(why)
            elif tip:
                self._scope_take(found, backend.describe(), records, b, named, (),
                                 lambda rid: self._scope_last(root, tip, smod.STORE_DIR, rid),
                                 close_cmd)
                found.histories.append(_closing_reader(root, tip))
        configured = backend.describe() if isinstance(backend, store_ref.RefBackend) else None
        got = self.scope_reads.get('pm')
        if got is None:
            got = self.scope_reads['pm'] = self._scope_pm_records(configured)
        pm_root, where, records, last, why, rev = got
        if why:
            found.unread.append('the PM data branch\'s store: %s' % why)
        elif records is not None:
            self._scope_take(found, where, records, b, named, (), last,
                             lambda *args: at_landing.backlog_command(pm_root, *args), True)
            if rev:
                found.histories.append(_closing_reader(pm_root, rev))
        return found

    def _scope_take(self, found, where, records, b, named, gone, last, command, shared=False):
        """Add to `found` the records of one store `b` holds: pulled by it, or pulled and in
        `named`; an id in `named` or `gone` the store no longer holds counts at its last version
        (`last(rid)`: the record before the commit that deleted it, or None). A store holding an
        unreadable record is unread: that record may be the one that covers a path, so of its
        readable records only the narrowed ones are taken (their branch's miss is refused all the
        same: scope_check). `shared`: the store shared by repositories (the PM data branch's),
        whose records keep their area (_area_lead)."""
        bad = sorted(r.stem for r in records if not r.readable)
        if bad:
            found.unread.append('%s: %d record(s) unreadable (%s)' % (
                where, len(bad), ', '.join(bad[:5])))
        if records and not bad:
            found.read = True
        stems = set(r.stem for r in records)
        candidates = [r for r in records if r.readable]
        for r in candidates:
            found.states.setdefault(r.stem, []).append(r.get('state'))
        for rid in sorted((set(named) | set(gone)) - stems):
            rec = last(rid) if store_read.PLAIN_ID.match(rid) else None
            if rec is not None and rec.readable:
                candidates.append(rec)
        for r in candidates:
            if bad and not r.all('narrowed'):
                continue
            if r.get('state') == 'pulled' and (r.get('pulled_by') == b or r.stem in named):
                found.hold(r.stem, r.all('scope'), command, r.get('area') if shared else None,
                           r.all('narrowed'))

    def _scope_last(self, root, rev, rel, rid):
        """The record `rid` as it was before the newest commit reachable from `rev` deleted
        `<rel>/<rid>.md`, or None."""
        gone = store_read.last_removal(root, rev, rel, rid)
        if gone is None:
            return None
        return self._scope_blob(root, '%s^:%s/%s.md' % (gone.sha, rel, rid), rid)

    @staticmethod
    def _scope_blob(root, spec, rid):
        data, _ = cmd.run(['cat-file', '-p', spec], root)
        if data is None:
            return None
        return store_read.parse_blob(rid, data, spec.partition(':')[2])

    def _scope_ref_records(self, root, backend):
        """`(records, tip, None)` of a `ref` or `branch` store as its remote holds it (the local
        ref without a remote), `([], '', None)` when there is none, or `(None, None, why)`. Moves
        no ref: a missing store commit is fetched with an empty --refmap, as ancestry_refusal
        does."""
        if backend.remote:
            tip, why = refs.ls_remote(backend.remote, backend.remote_ref, root,
                                      timeout=FETCH_TIMEOUT)
            if tip is None:
                return None, None, '%s: %s' % (backend.describe(), why)
            if tip and cmd.call(['cat-file', '-e', '%s^{commit}' % tip], root).rc != 0:
                cmd.call(['fetch', '--quiet', '--no-tags', '--no-write-fetch-head', '--refmap=',
                          backend.remote, backend.remote_ref], root, timeout=FETCH_TIMEOUT,
                         env=self.git_env)
                if cmd.call(['cat-file', '-e', '%s^{commit}' % tip], root).rc != 0:
                    return None, None, '%s: cannot fetch %s' % (backend.describe(), tip[:10])
        else:
            tip, why = refs.ref_commit(backend.local_ref, root)
            if tip is None:
                return None, None, '%s: %s' % (backend.describe(), why)
        if not tip:
            return [], '', None
        records, _raw, why = store_read.records_in_tree(root, tip, smod.STORE_DIR)
        if records is None:
            return None, None, '%s at %s: %s' % (backend.describe(), tip[:10], why)
        return records, tip, None

    def _scope_pm_records(self, configured):
        """`(pm root, where, records, last(rid), None, rev)` of the PM data branch's store, with
        `rev` the commit its history is read at (store.history: the ref's tip, a folder store's
        HEAD; None for an empty ref); `(None, None, None, None, None, None)` when there is none, or
        `(..., why, None)`. A folder store there is read in its worktree, as a landing's close
        reads it; a record deleted there and not committed is read from HEAD."""
        pm_root, _cfg, backend, why = at_landing.pm_data_store(self.root, configured)
        if why:
            return None, None, None, None, why, None
        if backend is None:
            return None, None, None, None, None, None
        if isinstance(backend, store_ref.RefBackend):
            records, tip, why = self._scope_ref_records(pm_root, backend)
            if records is None:
                return None, None, None, None, why, None
            return (pm_root, backend.describe(), records,
                    lambda rid: self._scope_last(pm_root, tip, smod.STORE_DIR, rid) if tip
                    else None, None, tip or None)
        try:
            snap = backend.load(rmod.Rules())
        except (Refusal, OSError) as e:
            return None, None, None, None, '%s: %s' % (backend.describe(), _what(e)), None
        rel = backend.rel.replace('\\', '/').strip('/')

        def last(rid):
            if backend.uncommitted_removal(rid):
                return self._scope_blob(pm_root, 'HEAD:%s/%s.md' % (rel, rid), rid)
            return self._scope_last(pm_root, 'HEAD', rel, rid)
        return pm_root, backend.describe(), snap.records, last, None, 'HEAD'

    def owner_refusal(self, tip):
        """None when every branch commit that changes an owner-only path is the owner's (by author
        email), counting only the paths that landing `tip` on the base would change; else why
        not (kitlib.ownerfiles.rules). The rules are the base's merged
        with the tip's (kitlib.config.owner.merge): the branch cannot unlock a path or name its own
        owner; a tip config that does not load adds the default paths."""
        try:
            work = kconfig.load_at(self.root, tip).owner_rules()
        except ConfigError:
            work = kconfig.OwnerRules()
        rules = kconfig.merge_owner_rules(work, self.cfg.owner_rules())
        bad, why = ownerrules.violations(self.root, rules, tip, [self.expect_main],
                                           env=self.git_env)
        if bad is None:
            return 'git cannot list the commits of the branch to check owner-only paths: %s' % why
        return ownerrules.describe(rules, bad) if bad else None
