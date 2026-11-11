"""The trailers step through a real landing (kitlib.landing.land): the merge is made and gated in the
detached landing tree, and the step runs there as a repository's gate runs it, `{here}/trailers.py
-C {root} --base {base}` with merged_args `--merged` (docs/repo-checks.md "trailers"). The
lander's trailerless merge is exempt and the branch lands; a trailerless branch commit is a
warning in the step's log and the branch lands all the same."""
import os
import unittest

from kitlib import gate as G
from tests.landing.test_land import BRANCH, LandCase
from tests.reviewpack.repo import TRAILERED_MSG

TRAILERS = {'steps': [{'name': 'trailers', 'always': True,
                       'cmd': ['{python}', '{here}/trailers.py', '-C', '{root}', '--base', '{base}'],
                       'merged_args': ['--merged']}]}


class TrailersLanding(LandCase):
    GATE = TRAILERS

    def branch_with(self, message):
        wt = self.repo.worktree('feature')
        self.repo.write('a.py', 'x = 1\n', root=wt)
        return self.repo.commit(message, 'a.py', cwd=wt)

    def trailers_log(self, lander):
        with open(os.path.join(lander.gate_logs, 'trailers.log'), encoding='utf-8') as f:
            return f.read()

    def test_a_trailered_branch_lands_through_the_trailers_step(self):
        tip = self.branch_with('work\n\n' + TRAILERED_MSG)
        lander = self.lander(BRANCH, self.main, tip)
        out = lander.run()
        log = self.trailers_log(lander)
        self.assertEqual(out.code, G.OK, out.lines + [log])
        self.assertIn('1 lander merge(s) exempt', log)
        self.assertEqual(self.remote_main(), out.merge)
        self.assertEqual(self.repo.git('rev-parse', out.merge + '^2'), tip)

    def test_a_trailerless_branch_commit_lands_with_a_warning(self):
        tip = self.branch_with('bare work')
        lander = self.lander(BRANCH, self.main, tip)
        out = lander.run()
        log = self.trailers_log(lander)
        self.assertEqual(out.code, G.OK, out.lines + [log])
        self.assertIn('WARNING: %s commit "bare work" has no Co-Authored-By trailer' % tip[:10],
                      log)
        self.assertEqual(self.remote_main(), out.merge)
        self.assertEqual(self.repo.git('rev-parse', out.merge + '^2'), tip)


if __name__ == '__main__':
    unittest.main()
