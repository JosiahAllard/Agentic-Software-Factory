"""tools/hub.py pages: a card's kind and folder, given at publish (--kind, --folder) and set later
with `pages place`. The cases of tests/hubsite/test_hub_cli.py's helpers: a temp home and a server
on port 0, or localask.ask stood in for."""
import os
from unittest import mock

from kitlib.hubclient import localask
from kitlib.hubsite import auth
from tests.hubsite import test_hub_cli as cli

HUB = cli.HUB


class PagesPlaceCase(cli.VerbCase):
    def publish(self, *extra):
        page = self.file('plan.html', '<h1>Plan</h1>\n')
        return self.ok('pages', 'publish', 'plan', '--title', 'The plan', '--html-file', page,
                       *extra)

    def test_a_publish_carries_kind_and_folder(self):
        self.assertEqual(self.publish('--kind', 'plan', '--folder', 'hub/docs'),
                         ['PUBLISHED plan'])
        card = self.direct('/pages')['items'][0]
        self.assertEqual((card['kind'], card['folder']), ('plan', 'hub/docs'))

    def test_a_publish_without_them_still_works(self):
        self.assertEqual(self.publish(), ['PUBLISHED plan'])
        self.assertNotIn('kind', self.direct('/pages')['items'][0])

    def test_place_sets_kind_folder_and_repo_pinned_to_the_version(self):
        self.publish('--repo', 'old')
        self.assertEqual(self.ok('pages', 'place', 'plan', '--kind', 'spec', '--folder', 'hub/docs',
                                 '--repo', 'kit', '--if-version', '1'), ['PLACED plan v2'])
        card = self.direct('/pages')['items'][0]
        self.assertEqual((card['kind'], card['folder'], card['repo'], card['version']),
                         ('spec', 'hub/docs', 'kit', 2))
        self.assertEqual(self.direct('/pages/plan')['html'], '<h1>Plan</h1>\n')
        err = self.refused('pages', 'place', 'plan', '--kind', 'plan', '--if-version', '1')
        self.assertIn('conflict', err)
        self.assertEqual(self.direct('/pages')['items'][0]['kind'], 'spec')

    def test_place_needs_its_pin_and_one_field(self):
        self.publish()
        code, out, err = self.run_cli('pages', 'place', 'plan', '--kind', 'spec', '--port',
                                      self.port)
        self.assertEqual((code, out), (2, ''))
        self.assertIn('--if-version', err)
        err = self.refused('pages', 'place', 'plan', '--if-version', '1')
        self.assertIn('give at least one of', err)


class PagesRouteCase(cli.CliCase):
    def test_publish_and_place_go_to_their_routes_with_the_agents_token(self):
        self.assertEqual(self.run_cli('setup')[0], 0)
        agent = auth.read_token(self.tokens, auth.AGENT)
        page = os.path.join(self.home, 'page.html')
        with open(page, 'w', encoding='utf-8') as f:
            f.write('<h1>plan</h1>')
        seen = []

        def ask(port, method, path, token=None, body=None, timeout=None):
            seen.append((method, path, token == agent, body))
            return 200, {'ok': True, 'id': 'plan', 'version': 2}

        base = HUB.api.BASE
        with mock.patch.object(localask, 'ask', ask):
            code, out, err = self.run_cli('pages', 'publish', 'plan', '--title', 't',
                                          '--html-file', page, '--kind', 'plan', '--folder', 'f',
                                          '--port', '8791')
            self.assertEqual((code, err), (0, ''))
            method, path, by_agent, body = seen[-1]
            self.assertEqual((method, path, by_agent, body['kind'], body['folder']),
                             ('POST', base + '/pages', True, 'plan', 'f'))
            code, out, err = self.run_cli('pages', 'place', 'a/b', '--kind', 'spec', '--folder',
                                          'x', '--repo', 'kit', '--if-version', '1', '--port',
                                          '8791')
            self.assertEqual((code, err), (0, ''))
            self.assertEqual(seen[-1], ('POST', base + '/pages/a%2Fb/place', True,
                                        {'kind': 'spec', 'folder': 'x', 'repo': 'kit',
                                         'if_version': 1}))
