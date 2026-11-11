"""A landing's merged gate (docs/gate-landing.md "Landing", steps 2b and 7): where it runs (main
or a load node) and the run, fresh or full when the change needs it. The mixin MergedGate of
kitlib.landing.lander.Lander: it holds no state of its own; every attribute is
Lander.__init__'s.
"""

from kitlib import gate as G
from kitlib.gate import reuse, runner
from kitlib.lock import kinds as lockkinds
from kitlib.lock.store import LockError
from kitlib.records import gate_ok
from kitlib.records import place
from kitlib.landing.checks import _what


def _not_landed(lines):
    """A green merged gate's lines in a landing that is not landed: its `GATE OK` verdict line is
    reworded, so a reader keying on `^GATE (OK|FAILED)` never takes a NOT LANDED for a pass."""
    return ['merged gate passed' + l[len('GATE OK'):] if l.startswith('GATE OK') else l
            for l in lines]


class MergedGate(object):
    """Lander's merged gate, routed and run (this module's docstring)."""

    def routes(self):
        """The load-node routing: Lander(node_route=), or kitlib.loadnode.landroute."""
        if self.node_route is None:
            from kitlib.loadnode import landroute
            self.node_route = landroute
        return self.node_route

    def plan_route(self):
        """Step 2b, before the booking: where the merged gate runs (landroute.plan), its line said
        unless it is quiet. A test's merged gate always gates here. A plan that raises gates here
        too, said as `gating here: the load-node plan failed: <error>`."""
        lr = self.routes()
        try:
            self.route = lr.plan(self.root, place.repo_name(self.cfg, self.root),
                                 self.cfg.section('gate')['kind'], store=self.queue_store(),
                                 merged_gate_fn=self.merged_gate_fn)
        except Exception as e:      # noqa: BLE001 - nothing booked or changed yet: gate on main
            self.route = None
            self.say('gating here: the load-node plan failed: %s' % _what(e))
            return
        line = lr.route_line(self.route)
        if line:
            self.say(line)

    def on_node(self):
        """True while the merged gate is routed to a load node."""
        return self.route is not None and self.route.node is not None

    def gate_facts(self):
        """{gate_once, escalated} for the land records (kitlib.gate.result build): gate_once is
        retired and always None; escalated is whether config_escalate made the merged gate --full,
        None when the base's config was never read (a submitted landing that ended before it)."""
        return {'gate_once': None, 'escalated': None if self.cfg is None else self.escalated}

    def strict_escalate(self):
        """A strict branch passed strict_check: its merged gate runs fresh, the steps its merge
        selects with nothing reused (docs/gate-landing-policy.md "Strict paths"), whatever the
        caller passed (it only ever tightens), and one line says so."""
        (b, _), = self.pairs
        e, c = self.strict_review, self.strict_carry
        self.fresh = True
        carried = ''
        if c:
            n = len(c['merges'])
            carried = ' at %s, carried over %d main merge%s' % (c['from'][:10], n,
                                                                 '' if n == 1 else 's')
        self.say('STRICT: %s: reviewed by %s on %s (%s)%s; the merged gate runs fresh (nothing '
                 'reused)' % (self.strict_says(b), e['reviewer'], e['reviewer_model'],
                              e['reviewer_id'], carried))

    def config_escalate(self):
        """After the merge, before the merged gate (ARCHITECTURE.md "What a landing runs"): a
        merge whose diff to the base changes a gate config file (reuse.config_hits: `.kit.json`, a
        config section file, the hook module) runs the merged gate --full once, every step that
        is not manual in its full_cmd form with nothing reused, said once; a diff git cannot list
        escalates too. Nothing else makes a landing --full."""
        changed, why = runner.changed_between(self.root, self.expect_main, self.merge)
        hit = reuse.config_hits(self.cfg, changed) if changed is not None else None
        if changed is not None and not hit:
            return
        self.full = self.escalated = True
        self.say('GATE CONFIG: %s: the merged gate runs every step in full on the merged tree' % (
            'the merge changes the gate\'s config (%s)' % ', '.join(hit[:5]) if hit else
            'git cannot list what the merge changes (%s)' % (why or 'no reason given')))

    def gate(self):
        self.lock.set_phase(self.token, 'gate')
        self.phase = 'gate'
        refusal = self.lock.gate_refusal()
        if refusal:
            return G.Outcome(G.REFUSED, ['REFUSED: %s' % refusal])
        if self.merged_gate_fn is not None:
            return self.merged_gate_fn(self)
        if self.on_node():
            out = self.node_gate()
            if out is not None:
                return out
        out = self.rebook_kind()
        if out is not None:
            return out
        return self.main_gate()

    def rebook_kind(self):
        """Step 7 on main, just before main's merged gate: None when the slot held is of the
        base config's "gate".kind. Main moved on before the turn to a base whose config (read
        again by follow, step 4) names another kind than book_turn booked (the config read at
        preconditions): it says so, releases the slot first, then books the base's kind at its
        own size with no wait limit, as rebook_here does and for the same reason (booked while
        the old run is held, a kind of every slot, an owner whole hold or a gpu pool would wait
        on itself). The landing lock file stays held, so no other landing merges meanwhile.
        None once booked; the machine lock not giving it: a FAILED Outcome (NOT LANDED, the
        merge undone). After a node-routed fallback rebook_here booked the base's kind already:
        nothing to do."""
        kind, booked = self.cfg.section('gate')['kind'], self.booked_kind
        if booked is None or self.same_kind(booked, kind):
            return None
        self.say('the base %s books "gate".kind %s, not %s as this landing did: releasing the '
                 "landing's %s run and booking a %s run for the merged gate, waiting with no "
                 'limit' % (self.expect_main[:10], kind, booked, booked, kind))
        self.drop_slot(0, 'done')
        try:
            self.book_slot(None)
        except LockError as e:
            line = ('the machine lock did not give the merged gate its %s run: %s'
                    % (kind, e.message))
            return G.Outcome(G.FAILED, [line], verdict=line, results=[])
        return None

    def same_kind(self, a, b):
        """True when kind names a and b book the same kind: equal once a retired name (an alias
        in the lock's kinds table) is read as the kind it names now, so a base whose config
        still names the old name, or already the new one, is not a different kind."""
        path = self.queue_store().kinds_path
        al = lockkinds.aliases(path)
        return lockkinds.canonical(a, table_aliases=al) == lockkinds.canonical(b, table_aliases=al)

    def route_need(self, lr, kind):
        """The slots the merged gate's kind `kind` needs on a node, for recheck: `(need, None)`,
        the plan's when the base's config names the kind booked (book_turn); else `kind` sized
        again (lr.need_of), or `(None, why)` when it cannot be, and the landing gates here."""
        if self.booked_kind is None or self.same_kind(self.booked_kind, kind):
            return self.route.need, None
        try:
            return lr.need_of(kind, self.queue_store()), None
        except Exception as e:      # noqa: BLE001 - gate here, at the base's kind
            return None, ('the base %s books "gate".kind %s, not %s, and %s cannot be sized '
                          'here (%s)' % (self.expect_main[:10], kind, self.booked_kind, kind,
                                         _what(e)))

    def node_gate(self):
        """Step 7 for a node-routed landing: the land job's Outcome (landroute.run_on_node), or
        None once it fell back to main (rebook_here booked main's full run): the tip's --full
        record has the merge's tree (same_tree_why), no node qualifies now (recheck, for the
        slots of the base's kind: route_need; another node that does takes it), or nothing ran
        on the node (Fallback). Only those fall back:
        any other exception goes on up (recover: NOT LANDED). The machine lock not giving the
        full run: a FAILED Outcome."""
        lr, kind = self.routes(), self.cfg.section('gate')['kind']
        repo_name = place.repo_name(self.cfg, self.root)
        why = lr.same_tree_why(self.tree, self.cfg, self.branches[0], self.tips[0],
                               self.expect_main, self.merge, batch=len(self.branches),
                               fresh=self.fresh, full=self.full)
        node = need = None
        if not why:
            need, why = self.route_need(lr, kind)
        if not why:
            c = lr.recheck(self.route.nodes, repo_name, need)
            if c.node is None:
                why = c.why
            else:
                node = c.node
                if c.node.name != self.route.node.name:
                    self.say('gating on load node %s: %s' % (c.node.name, c.why))
        if node is not None:
            try:
                return lr.run_on_node(node, self.root, self.merge, self.expect_main, self.cfg,
                                      repo_name, place.slug_of('+'.join(self.branches)),
                                      full=self.full, docs=True, tree=self.tree, kind=kind,
                                      say=self.say, store=self.queue_store())
            except lr.Fallback as e:
                why = '%s (nothing ran on load node %s)' % (e.why, node.name)
        try:
            self.rebook_here(why)
        except LockError as e:
            line = ("the machine lock did not give main's merged gate its full %s run: %s"
                    % (kind, e.message))
            return G.Outcome(G.FAILED, [line], verdict=line, results=[])
        return None

    def main_gate(self):
        """Step 7 on main: the merged gate in the landing's slot (runner.merged_gate)."""
        plan = reuse.planner(self.tree, self.cfg, self.branches[0], self.tips[0], self.expect_main,
                             self.merge, self.prior, fresh=self.fresh, full=self.full,
                             batch=len(self.branches),
                             record=self.tip_records.get(self.tips[0]))
        out = runner.merged_gate(self.root, self.cfg, self.expect_main, full=self.full,
                                 slug='+'.join(self.branches), log_dir=self.log_dir, say=self.say,
                                 book_kw=self.book_kw, reuse=plan,
                                 slot=self.slot, tree=self.tree, docs=True)
        self.gate_logs = out.facts.get('log_dir')
        return out

    def merged_mode(self):
        """The mode of the merged gate's gate-OK record on the new main (gate_ok.MODES), or None."""
        try:
            rec, _ = gate_ok.read(self.root, self.merge)
            return gate_ok.mode_of(rec) if isinstance(rec, dict) else None
        except Exception:   # noqa: BLE001 - an event without it still says the landing
            return None
