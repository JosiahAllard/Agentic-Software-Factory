"""kitlib.hubsite.repoweb: a repository's web address, main branch and root names for the page's repo-path links."""
import os
import shutil
import tempfile
import unittest
from unittest import mock

from kitlib.git import cmd
from kitlib.hubsite import repoweb
from tests.realgit import real_git


class WebUrlTests(unittest.TestCase):
    def test_github_and_gitlab_remotes_become_one_https_address(self):
        for remote in ('https://github.com/o/r.git', 'https://github.com/o/r', 'git@github.com:o/r.git',
                       'ssh://git@github.com/o/r.git', 'ssh://git@github.com:22/o/r',
                       'https://user:tok@github.com/o/r.git?x=1#f', ' https://GitHub.com/o/r/ '):
            with self.subTest(remote=remote):
                self.assertEqual(repoweb.web_url(remote), 'https://github.com/o/r')
        self.assertEqual(repoweb.web_url('git@gitlab.com:g/sub/r.git'), 'https://gitlab.com/g/sub/r')
        self.assertEqual(repoweb.web_url('https://gitlab.example.org/g/r'), 'https://gitlab.example.org/g/r')

    def test_gitlab_keeps_subgroups_but_stops_at_its_own_routes(self):
        self.assertEqual(repoweb.web_url('https://gitlab.com/g/s/r/-/tree/main'), 'https://gitlab.com/g/s/r')
        self.assertIsNone(repoweb.web_url('https://gitlab.com/-/x'))

    def test_another_host_a_local_path_or_junk_has_no_address(self):
        for remote in ('https://bitbucket.org/o/r', 'git@example.org:o/r', 'Z:/repos/r', '/srv/git/r', 'file:///x/r',
                       'https://github.com/onlyowner', 'https://github.com/o/../r', 'https://github.com/o/r r',
                       '', None, 5, 'ftp://github.com/o/r', 'https://evilgitlab.com/o/r',
                       'https://github.com/o/r/x', 'https://github.com/o/r/tree/main', 'https://github.com/o/r/-/x'):
            with self.subTest(remote=remote):
                self.assertIsNone(repoweb.web_url(remote))


def git(root, *args):
    out, why = cmd.run(list(args), root)
    assert out is not None, why


@real_git('remote get-url, ls-tree and a ref store read by the code under test: a real repository')
class WebsTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix='kit-repoweb-')
        self.addCleanup(shutil.rmtree, self.d, True)

    def repo(self, name, origin=None, branch='main'):
        root = os.path.join(self.d, name)
        os.makedirs(os.path.join(root, 'docs'))
        git(root, 'init', '-q', '-b', branch)
        if origin:
            git(root, 'remote', 'add', 'origin', origin)
        with open(os.path.join(root, 'docs', 'gate.md'), 'w') as f:
            f.write('x')
        with open(os.path.join(root, 'README.md'), 'w') as f:
            f.write('x')
        git(root, 'add', '-A')
        git(root, '-c', 'user.name=t', '-c', 'user.email=t@t.test', 'commit', '-q', '-m', 'i')
        return root

    def test_a_repo_with_an_origin_gets_web_branch_and_root_names(self):
        root = self.repo('a', 'git@github.com:o/r.git')
        got = repoweb.Webs(lambda: [('A', root)]).annotate([{'name': 'a', 'aliases': []}])
        self.assertEqual(got, [{'name': 'a', 'aliases': [], 'web': 'https://github.com/o/r',
                                'branch': 'main', 'top': ['README.md', 'docs']}])

    def test_with_a_staging_branch_the_links_name_it_and_its_root_names(self):
        # docs/gate-landing.md "Staging branch": landed files are on the staging branch as fetched
        root = self.repo('s', 'git@github.com:o/r.git')
        git(root, 'checkout', '-q', '--detach')
        with open(os.path.join(root, 'NEW.md'), 'w') as f:
            f.write('x')
        git(root, 'add', 'NEW.md')
        git(root, '-c', 'user.name=t', '-c', 'user.email=t@t.test', 'commit', '-q', '-m', 's')
        git(root, 'update-ref', 'refs/remotes/origin/staging', 'HEAD')
        git(root, 'checkout', '-q', 'main')
        with open(os.path.join(root, '.kit.json'), 'w') as f:
            f.write('{"staging": {"branch": "staging"}}')
        got = repoweb.Webs(lambda: [('S', root)]).annotate([{'name': 's', 'aliases': []}])
        self.assertEqual(got[0]['branch'], 'staging')
        self.assertEqual(got[0]['top'], ['NEW.md', 'README.md', 'docs'])

    def test_a_repo_with_no_origin_a_foreign_host_or_no_checkout_is_unchanged(self):
        none, other = self.repo('n'), self.repo('o', 'https://bitbucket.org/o/r')
        repos = [{'name': n, 'aliases': []} for n in ('n', 'o', 'x', 'y')]
        got = repoweb.Webs(lambda: [('n', none), ('o', other), ('x', None)]).annotate(repos)
        self.assertEqual(got, [{'name': n, 'aliases': []} for n in ('n', 'o', 'x', 'y')])

    def test_an_unreadable_list_leaves_the_repos_alone(self):
        def boom():
            raise OSError('no')
        self.assertEqual(repoweb.Webs(boom).annotate([{'name': 'a', 'aliases': []}]), [{'name': 'a', 'aliases': []}])

    def test_git_runs_once_until_the_config_changes_or_the_time_is_up(self):
        root = self.repo('c', 'https://github.com/o/r')
        now, reads = [0.0], []

        def read(r):
            reads.append(r)
            return {'web': 'https://github.com/o/r', 'branch': 'main', 'top': []}
        webs = repoweb.Webs(lambda: [('c', root)], clock=lambda: now[0], read=read)
        for _ in range(3):
            webs.annotate([{'name': 'c', 'aliases': []}])
        self.assertEqual(len(reads), 1)
        cfg = os.path.join(root, '.git', 'config')
        st = os.stat(cfg)
        os.utime(cfg, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
        webs.annotate([{'name': 'c', 'aliases': []}])
        self.assertEqual(len(reads), 2)
        now[0] = repoweb.REFRESH_S + 1
        webs.annotate([{'name': 'c', 'aliases': []}])
        self.assertEqual(len(reads), 3)

    def test_a_linked_worktree_refreshes_when_the_common_config_changes(self):
        main = self.repo('w', 'https://github.com/o/r')
        tree = os.path.join(self.d, 'w-tree')
        git(main, 'worktree', 'add', '-q', '-b', 'side', tree)
        self.assertTrue(os.path.isfile(os.path.join(tree, '.git')))
        now, reads = [0.0], []

        def read(r):
            reads.append(r)
            return {}
        webs = repoweb.Webs(lambda: [('w', tree)], clock=lambda: now[0], read=read)
        for _ in range(2):
            webs.annotate([{'name': 'w', 'aliases': []}])
        self.assertEqual(len(reads), 1)
        cfg = os.path.join(main, '.git', 'config')
        st = os.stat(cfg)
        os.utime(cfg, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
        webs.annotate([{'name': 'w', 'aliases': []}])
        self.assertEqual(len(reads), 2)

    def test_a_failed_config_lookup_is_asked_again_only_after_the_window(self):
        main = self.repo('f', 'https://github.com/o/r')
        tree = os.path.join(self.d, 'f-tree')
        git(main, 'worktree', 'add', '-q', '-b', 'side', tree)
        reads, asked, now = [], [], [0.0]
        real = repoweb._config_path

        def flaky(root):
            asked.append(root)
            return None if len(asked) <= 2 else real(root)
        webs = repoweb.Webs(lambda: [('f', tree)], clock=lambda: now[0], read=lambda r: reads.append(r) or {})
        with mock.patch.object(repoweb, '_config_path', flaky):
            for _ in range(5):
                webs.annotate([{'name': 'f', 'aliases': []}])
            self.assertEqual((len(asked), len(reads)), (1, 1))      # a persistent failure: one call in the window
            now[0] = repoweb.REFRESH_S + 1
            for _ in range(3):
                webs.annotate([{'name': 'f', 'aliases': []}])
            self.assertEqual(len(asked), 2)                          # one more after it, still failing
            now[0] = 2 * repoweb.REFRESH_S + 2
            for _ in range(3):
                webs.annotate([{'name': 'f', 'aliases': []}])
            self.assertEqual(len(asked), 3)                          # it works now: kept from then on
            self.assertEqual(len(reads), 3)

    def test_a_named_main_branch_and_the_cap_on_root_names(self):
        root = self.repo('m', 'https://github.com/o/r', branch='trunk')
        self.assertEqual(repoweb.Webs(lambda: [('m', root)]).annotate([{'name': 'm', 'aliases': []}])[0].get('branch'), None)
        os.makedirs(os.path.join(root, 'kit'))
        with open(os.path.join(root, 'kit', 'x'), 'w') as f:
            f.write('{"repo": {"main_branch": "trunk"}}')
        os.replace(os.path.join(root, 'kit', 'x'), os.path.join(root, '.kit.json'))
        got = repoweb.Webs(lambda: [('m', root)]).annotate([{'name': 'm', 'aliases': []}])[0]
        self.assertEqual(got['branch'], 'trunk')
        self.assertLessEqual(len(got['top']), repoweb.MAX_TOP)


if __name__ == '__main__':
    unittest.main()
