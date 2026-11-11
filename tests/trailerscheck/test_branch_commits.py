"""kitlib.trailerscheck.trailers.branch_commits over the review-pack fixture repository with a bare
`origin` (tests/reviewpack/repo.py WithOrigin): which tracking ref it probes, and a killed probe."""
from unittest import mock

from kitlib.trailerscheck import trailers
from tests.reviewpack.repo import WithOrigin


class BranchCommitsWithAFullRefBase(WithOrigin):
    """trailers.branch_commits probes `refs/remotes/<remote>/<base>` for a short base only."""

    def probes(self, base):
        seen = []
        real = trailers.gitcmd.call

        def call(args, cwd, *a, **k):
            seen.append(list(args))
            return real(args, cwd, *a, **k)
        with mock.patch.object(trailers.gitcmd, 'call', call):
            commits, why = trailers.branch_commits(base, self.root, head='phase/g')
        self.assertIsNone(why)
        return commits, [a[-1] for a in seen if a[:1] == ['rev-parse']]

    def phase(self):
        self.advance_origin()
        self.git('checkout', '-q', '-b', 'phase/g', 'origin/main')
        self.write('f.txt', '1')
        self.commit_all('phase work')

    def test_a_full_ref_base_makes_no_tracking_probe(self):
        self.phase()
        commits, probes = self.probes('refs/remotes/origin/main')
        self.assertEqual([c.subject for c in commits], ['phase work'])
        self.assertEqual(probes, [])

    def test_a_short_base_still_probes_its_tracking_ref(self):
        self.phase()
        commits, probes = self.probes('main')
        self.assertEqual(probes, ['refs/remotes/origin/main'])
        self.assertEqual([c.subject for c in commits], ['phase work'])

    def test_a_killed_tracking_probe_is_no_answer_not_no_tracking_ref(self):
        """`rev-parse --verify --quiet` said "no tracking ref" as exit 1
        with no output, the shape a git ended from outside leaves; the remote's newer base commits
        were then counted as the branch's own."""
        self.phase()
        real = trailers.gitcmd.spawner()

        def run(argv, cwd=None, input=None, timeout=None, env=None):
            if argv[1] == 'rev-parse' and argv[-1] == 'refs/remotes/origin/main':
                return 1, b'', b''
            return real(argv, cwd=cwd, input=input, timeout=timeout, env=env)
        with mock.patch.object(trailers.gitcmd, '_run', side_effect=run):
            commits, why = trailers.branch_commits('main', self.root, head='phase/g')
        self.assertIsNone(commits, 'a killed probe was read as "no tracking ref"')
        self.assertIn('cannot read refs/remotes/origin/main', why)
        self.assertIn('printed nothing', why)
