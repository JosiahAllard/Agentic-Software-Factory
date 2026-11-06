"""Context.unmet resolves the code main (refs.default_base, 2-3 git spawns) at most once per Context
and only when a record names a `commit:` requirement: a `next` or `pull` over a store with none
spawns nothing for it (docs/backlog.md, `next`)."""
import sys
from unittest import mock

from kitlib.backlog import pull_branch
from kitlib.backlog import plan as pmod
from kitlib.git import refs

from tests.backlog.helpers import BRANCH, StoreCase, priced


class UnmetBaseLazy(StoreCase):
    remote = False

    def count_unmet_bases(self):
        """A patch of refs.default_base that counts the calls made from a Context method."""
        real = refs.default_base
        calls = []

        def spy(*a, **kw):
            if isinstance(sys._getframe(1).f_locals.get('self'), pmod.Context):
                calls.append(a)
            return real(*a, **kw)
        return mock.patch.object(refs, 'default_base', spy), calls

    def run_next_and_pull(self, rid):
        patch, calls = self.count_unmet_bases()
        with patch:
            pmod.offer_for(self.store.load(), self.ctx)
            self.store.write(lambda s: pull_branch.plan_pull(s, self.ctx, rid))
        return calls

    def test_no_commit_requires_resolves_no_base(self):
        self.seed(priced('A', requires='item:GONE'), priced('B'))
        self.assertEqual(self.run_next_and_pull('B'), [])

    def test_one_commit_requires_resolves_the_base_once_per_pull(self):
        sha = self.repo.git('rev-parse', 'main')
        self.seed(priced('A', requires=['commit:%s' % sha]), priced('B'))
        self.assertEqual(len(self.run_next_and_pull('A')), 1)

    def test_a_context_remembers_the_base(self):
        sha = self.repo.git('rev-parse', 'main')
        self.seed(priced('A', requires=['commit:%s' % sha]))
        snap = self.store.load()
        patch, calls = self.count_unmet_bases()
        with patch:
            for _ in range(3):
                self.assertEqual(self.ctx.unmet(snap)(snap.get('A')), [])
        self.assertEqual(len(calls), 1)
