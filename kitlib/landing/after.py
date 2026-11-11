"""After a landing pushed (docs/gate-landing.md "Landing", steps 11 and 12): the feed post and
deploy note, the approvals it carried, the load-node mirrors, the records it closes and the
branch cleanup. The mixin After of kitlib.landing.lander.Lander: it holds no state of its own;
every attribute is Lander.__init__'s.
"""

import re

from kitlib.backlog import at_landing
from kitlib.landing import cleanup
from kitlib.git import cmd
from kitlib.records import place
from kitlib.landing.checks import _what


# a record close_pulled closed: at_landing.close_landed's line, `closed <id> in the backlog store`
_CLOSED = re.compile(r'closed (\S+) in the backlog store\b')


class After(object):
    """Lander's steps after the push (this module's docstring)."""

    def post_landing(self):
        """Landed, the records finished and the lock released: one landing feed event per branch
        posted to the hub (kitlib.landing.feed), built from the land record the ledger line
        was (rr.last), with the hub approvals its commits put on main (landed_approvals).
        Best-effort with a short timeout: each event the hub did not take is a
        WARNING said as progress and dropped, and the outcome, its lines and its code stand
        whatever happens here. Never raises."""
        try:
            from kitlib.landing import feed as landings
            repo_name = place.repo_name(self.cfg, self.root)
            mode = self.merged_mode()
            events = []
            for rr in self.result_runs:
                if rr.last is None:
                    continue            # ledger() said so; there is nothing to build it from
                events.append(landings.event(
                    rr.last, repo_name, self.cfg.main_branch, self.merge,
                    records=self.closed.get(rr.branch, ()), mode=mode,
                    deploy=self.deploy_note(rr.last.get('tip')),
                    approvals=self.landed_approvals(rr.last.get('tip'))))
            lines = landings.post(events, client=self.hub)
        except BaseException as e:  # noqa: BLE001 - landed: only the feed misses it
            lines = ['WARNING: the landing was not posted to the hub feed (%s); the landing '
                     'stands' % _what(e)]
        for line in lines:
            try:
                self.say(line)
            except BaseException:   # noqa: BLE001 - the outcome stands
                pass

    def deploy_note(self, tip):
        """The `Deploy:` note of the commit `tip` (kitlib.landing.feed.deploy_note), or None."""
        from kitlib.landing import feed as landings
        if not tip:
            return None
        r = self.git('log', '-1', '--format=%B', tip)
        return landings.deploy_note(cmd.text(r.out)) if r.rc == 0 else None

    def landed_approvals(self, tip):
        """{A-<n>: sha} of the hub approvals the commits main (before the landing) .. `tip`
        carry (kitlib.ownerfiles.approvals.landed), for the hub page's "on main"; {} when git cannot
        tell. Display only: whether an approval is spent is read from main itself."""
        from kitlib.ownerfiles import approvals    # here: as deploy_note
        if not (tip and self.expect_main):
            return {}
        got, _why = approvals.landed(self.root, self.expect_main, tip)
        return got or {}

    def refresh_mirrors(self, out):
        """Step 13, landed and nothing held: the merge pushed to the load nodes' mirrors of main
        (kitlib.loadnode.mirror.refresh), its lines put before the LANDED line of `out`. The
        landing stands whatever happens here: an exception or Ctrl+C is a WARNING line."""
        lines = []
        try:
            from kitlib.loadnode import mirror
            mirror.refresh(self.root, place.repo_name(self.cfg, self.root), self.cfg.main_branch,
                           out.facts['merge'], store=self.queue_store(), say=lines.append)
        except BaseException as e:  # noqa: BLE001 - landed: only the nodes' mirrors lag
            lines.append("WARNING: refreshing the load nodes' mirrors of %s stopped (%s); the "
                         'landing stands' % (self.cfg.main_branch, _what(e)))
        at = next((i for i, x in enumerate(out.lines) if x.startswith('LANDED ')), len(out.lines))
        out.lines[at:at] = lines

    def close_pulled(self):
        """After the verified push and the release: close what the branch holds pulled in the
        backlog store (kitlib.backlog.at_landing). The landing stands whatever happens here, so
        every failure, Ctrl+C included, is a WARNING line and never reaches recover().
        Each branch's `Closes:` lines are read from its tip down to the main it was merged onto
        (the batch's previous merge, else the base), so a stacked branch never re-reads the
        lines of the branch merged before it; the batch's other branches are no holder to tell.
        The ids of each branch's `closed <id> in the backlog store` lines go in self.closed, for
        the landing feed (post_landing).
        A batch not merged as a stack (fewer merges than branches) reads every branch's lines down
        to the base, which takes in an earlier batch branch's lines when the tip carries them: a
        WARNING says so for each branch after the first (its Part-of line can release what this
        branch would close), and nothing else changes."""
        self.phase = 'landed'
        lines = []
        stacked = len(self.merges) == len(self.pairs)
        for i, (b, t) in enumerate(self.pairs):
            base = self.merges[i - 1] if stacked and i else self.expect_main
            if i and not stacked:
                lines.append(
                    'WARNING: the batch was not merged as a stack (%d merge(s) for %d branches), '
                    'so %s\'s Closes and Part-of lines are read from its tip down to the base %s, '
                    'which takes in the lines of a batch branch before it that its tip carries: a '
                    'Part-of line of that branch can release records %s holds instead of closing '
                    'them; see the lines read with: git log --format=%%B %s --not %s'
                    % (len(self.merges), len(self.pairs), b, str(base)[:10], b, t, base))
            try:
                said = at_landing.close_landed(
                    self.root, self.cfg, b, self.merge, tip=t, base=base,
                    batch=[o for o in self.branches if o != b])
                lines.extend(said)
                self.closed[b] = [m.group(1) for m in map(_CLOSED.match, said) if m]
            except BaseException as e:
                what = 'Ctrl+C' if isinstance(e, KeyboardInterrupt) else '%s: %s' % (
                    type(e).__name__, e)
                try:
                    hows = at_landing.landable_commands(self.root, self.cfg, b) or [
                        at_landing.backlog_command(self.root, 'landable', b)]
                except BaseException:   # noqa: BLE001 - still say what stopped
                    hows = ['the backlog tool']
                lines.append('WARNING: %s landed, and closing the records it holds pulled in the '
                             'backlog store stopped (%s); see what is left with: %s' % (
                                 b, ' '.join(what.split()), ' and '.join(hows)))
        return lines

    def clean_up(self):
        """After close_pulled: the branch cleanup (kitlib.landing.cleanup.after_landing); its lines,
        which go before the LANDED line, and self.cleanup. The landing stands whatever happens
        here, so an exception or Ctrl+C is a WARNING naming the sweep and never reaches recover()."""
        self.cleaned = True
        try:
            self.cleanup, lines = cleanup.after_landing(self.root, self.branches, self.merge,
                                                        keep_branch=self.keep_branch,
                                                        git_env=self.git_env)
        except BaseException as e:
            self.cleanup = {b: None for b in self.branches}
            lines = ['WARNING: %s landed, and its branch cleanup stopped (%s); finish it with: %s'
                     % (self.branch, ' '.join(_what(e).split()),
                        cleanup.sweep_command(self.root))]
        return lines
