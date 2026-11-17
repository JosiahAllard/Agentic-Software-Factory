"""What each record holds up: its transitive dependents through `item:` blockers, the count and
the best price among them. `offer.rank_key` ranks by it (docs/backlog.md "next: the rank");
the stored priority is never changed.
"""
from collections import deque

from . import record as rmod
from . import store_read


class Unblocks(object):
    """Built once from a snapshot. `count[stem]`: the records it unblocks, directly or through a
    chain, each once. `best[stem]`: `(priority, id)` of its best-priced dependent (a tie goes to
    the lower id), absent when none is priced. `cycle`: one `item:` cycle as ids, or None. Every
    record with a dependent has an entry, priced or not. A walk puts its start in its visited set
    first, so a cycle ends it: each record in one counts the others."""

    def __init__(self, snap):
        edges = store_read.item_edges(snap.records)
        held_by = {}                        # blocker -> the records blocked on it
        for stem in sorted(edges):
            for target in edges[stem]:
                held_by.setdefault(target, []).append(stem)
        self.count, self.best = {}, {}
        for root in held_by:
            seen, queue = {root}, deque([root])
            while queue:
                for dep in held_by.get(queue.popleft(), ()):
                    if dep not in seen:
                        seen.add(dep)
                        queue.append(dep)
            seen.discard(root)
            self.count[root] = len(seen)
            priced = [(int(snap.get(d).get('priority')), d) for d in seen
                      if rmod.is_band(snap.get(d).get('priority'))]
            if priced:
                self.best[root] = min(priced)
        self.cycle = store_read.find_cycle(edges)

    def effective(self, rec):
        """The best of `rec`'s own price and its dependents' best; None when `rec` is unpriced
        (it has no rank, whatever it holds)."""
        if not rmod.is_band(rec.get('priority')):
            return None
        own = int(rec.get('priority'))
        best = self.best.get(rec.stem)
        return min(own, best[0]) if best else own

    def why(self, rec):
        """`unblocks N, best pK: ID`, or `unblocks N` when no dependent is priced; None when it
        unblocks nothing."""
        n = self.count.get(rec.stem)
        if not n:
            return None
        best = self.best.get(rec.stem)
        return 'unblocks %d%s' % (n, ', best p%d: %s' % best if best else '')

    def cycle_line(self):
        """`next`'s CYCLE line, or None. `check` fails on the same cycle (store_read)."""
        if not self.cycle:
            return None
        return ('CYCLE: item: blockers %s; nothing in it can be pulled (check fails on it)'
                % ' -> '.join(self.cycle + [self.cycle[0]]))
