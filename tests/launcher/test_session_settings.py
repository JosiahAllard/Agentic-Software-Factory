"""The settings file a launched session starts with (docs/launcher.md "Session settings"):
hosts.claude.adapter.content.session_settings, kitlib.launcher.sessionsettings, start's --settings
and --mcp, and unlisted reading the path back. The shipped cut list waits on the owner, so every test
here runs on a cut list of its own."""
import json
import os
import shutil
import tempfile
import types
import unittest
from unittest import mock

from hosts.claude.adapter import content as claude_content
from kitlib.launcher import sessionsettings, start, unlisted, window
from kitlib.launcher.settings import LaunchError
from tests.launcher.test_unlisted import Base, rows

CUT = ('Read', 'NotebookEdit', 'mcp__plugin_pm-kit_hub', 'mcp__plugin_pm-kit_blender')
HUB, BLENDER = 'mcp__plugin_pm-kit_hub', 'mcp__plugin_pm-kit_blender'


def role(capabilities=(), all_tools=False):
    return types.SimpleNamespace(capabilities=list(capabilities), all_tools=all_tools)


def kit_with(root, data):
    """A kit folder under `root` whose rendered session settings are `data`; its Spec."""
    kit = os.path.join(root, 'kit')
    path = os.path.join(kit, *claude_content.SESSION_SETTINGS_REL.split('/'))
    os.makedirs(os.path.dirname(path))
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f)
    return sessionsettings.Spec(claude_content.SESSION_SETTINGS_REL, claude_content.mcp_deny_names(), kit)


class Render(unittest.TestCase):
    def test_a_capability_a_role_names_is_taken_back_and_all_takes_nothing(self):
        c = types.SimpleNamespace(roles=[role(['read', 'search']), role(all_tools=True)])
        got = claude_content.session_settings(c, CUT)
        self.assertEqual(got, {'permissions': {'deny': ['NotebookEdit', HUB, BLENDER]}})
        c = types.SimpleNamespace(roles=[role(all_tools=True)])
        self.assertEqual(claude_content.session_settings(c, CUT)['permissions']['deny'], list(CUT))

    def test_the_shipped_file_is_rendered_from_the_one_cut_list(self):
        from kitlib import content
        kit = content.load(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        out = claude_content.renderer().render(kit)
        self.assertEqual(json.loads(out[claude_content.SESSION_SETTINGS_REL].decode('utf-8')),
                         claude_content.session_settings(kit, claude_content.SESSION_CUT))

    def test_each_kit_server_has_its_plugin_deny_entry(self):
        self.assertEqual(claude_content.mcp_deny_names(),
                         {n: 'mcp__plugin_pm-kit_%s' % n for n in claude_content.MCP_SERVERS})


class Files(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.spec = kit_with(self.root, {'permissions': {'deny': list(CUT)}, 'other': 1})

    def test_resolve_names_the_rendered_file_or_says_it_is_missing(self):
        path, missing = sessionsettings.resolve(self.spec)
        self.assertIsNone(missing)
        self.assertTrue(os.path.isfile(path))
        os.remove(path)
        self.assertEqual(sessionsettings.resolve(self.spec), (None, sessionsettings.MISSING % path))
        unknown = self.spec._replace(kit=None)
        self.assertEqual(sessionsettings.resolve(unknown)[1],
                         sessionsettings.MISSING % ('the kit checkout: ' + self.spec.rel))

    def test_plan_without_mcp_is_the_rendered_file_and_writes_nothing(self):
        path, _ = sessionsettings.resolve(self.spec)
        self.assertEqual(sessionsettings.plan(self.spec, path, (), self.root, 'n'), (path, None))

    def test_plan_with_mcp_drops_that_servers_deny_entry_only(self):
        path, _ = sessionsettings.resolve(self.spec)
        got, data = sessionsettings.plan(self.spec, path, ['hub'], self.root, 'deep-x')
        self.assertEqual(got, os.path.join(self.root, 'launcher', 'settings-deep-x.json'))
        self.assertEqual(data, {'permissions': {'deny': ['Read', 'NotebookEdit', BLENDER]}, 'other': 1})
        self.assertFalse(os.path.exists(got))

    def test_an_unknown_server_is_refused_naming_the_known_ones(self):
        path, _ = sessionsettings.resolve(self.spec)
        with self.assertRaisesRegex(LaunchError, r'--mcp nope: no such MCP server \(known: blender, hub'):
            sessionsettings.plan(self.spec, path, ['nope'], self.root, 'n')

    def test_write_goes_through_a_temp_name_then_replace(self):
        target = os.path.join(self.root, 'launcher', 'settings-n.json')
        seen = []
        real = os.replace

        def replace(src, dst):
            seen.append((src, dst, os.path.exists(dst)))
            real(src, dst)
        with mock.patch('os.replace', replace):
            sessionsettings.write(target, {'a': 1})
        self.assertEqual(seen, [(target + '.tmp', target, False)])
        with open(target, encoding='utf-8') as f:
            self.assertEqual(json.load(f), {'a': 1})

    def test_a_failed_write_leaves_no_temp_file(self):
        target = os.path.join(self.root, 'launcher', 'settings-n.json')
        with mock.patch('os.replace', side_effect=OSError('no')), self.assertRaises(OSError):
            sessionsettings.write(target, {'a': 1})
        self.assertEqual(os.listdir(os.path.dirname(target)), [])


class Command(unittest.TestCase):
    def test_settings_sits_after_the_permission_mode_and_before_remote_control(self):
        cmd = window.session_command('n', 'p', 'a', settings='C:/k/s.json')
        self.assertIn(" --permission-mode auto --settings 'C:/k/s.json' --remote-control 'n'", cmd)
        self.assertNotIn('--settings', window.session_command('n', 'p', 'a'))


class Launch(Base):
    def setUp(self):
        super().setUp()
        self.spec = kit_with(os.path.join(self.h.root, 'my kit'), {'permissions': {'deny': list(CUT)}})
        self.rendered = os.path.join(self.spec.kit, *self.spec.rel.split('/'))
        self.host.session_spec = self.spec
        self.own = os.path.join(self.temp, 'launcher', 'settings-deep-net-race.json')

    def test_a_path_with_a_space_reads_back_through_unlisted(self):
        self.assertIn(' ', self.rendered)
        text = self.deep()
        self.assertIn(' --settings %s --remote-control ' % window.q(self.rendered), text)
        self.assertEqual(unlisted.read_window(text, 'deep-net-race')['settings'], self.rendered)
        found, never = self.classify(rows(text, 700, 'deep-net-race'))
        self.assertEqual((never, found['deep-net-race'][0].brief), ({}, self.brief))

    def test_a_path_with_a_space_reads_back_from_the_bash_window(self):
        with mock.patch('kitlib.platform.NAME', 'linux'):
            text = self.deep()
            self.assertEqual(unlisted.read_window(text, 'deep-net-race')['settings'], self.rendered)

    def test_mcp_starts_with_a_file_of_its_own(self):
        text = self.deep(mcp=['hub'])
        self.assertEqual(unlisted.read_window(text, 'deep-net-race')['settings'], self.own)
        with open(self.own, encoding='utf-8') as f:
            self.assertEqual(json.load(f)['permissions']['deny'], ['Read', 'NotebookEdit', BLENDER])
        self.assertFalse(os.path.exists(self.own + '.tmp'))

    def test_what_if_and_plan_write_no_file(self):
        out = start.deep(self.h.settings, self.host, repo_path=self.repo, requester='r', slug='net-race',
                         brief=self.brief, account='1', plan=True, mcp=['hub'])
        self.assertIn('Would write the session settings to %s' % self.own, out)
        self.assertFalse(os.path.exists(os.path.dirname(self.own)))
        self.assertEqual(self.host.opened, [])
        out = start.start(self.h.settings, self.host, repo='alpha', account='1', whatif=True, mcp=['hub'])
        self.assertTrue(any(l.startswith('Would write the session settings to ') for l in out))
        self.assertFalse(os.path.exists(os.path.dirname(self.own)))

    def test_a_failed_launch_removes_the_file_it_wrote(self):
        seen = []

        def fail(text, **kw):
            seen.append(os.path.isfile(self.own))
            raise OSError('no window')
        self.host.open_window = fail
        with self.assertRaisesRegex(LaunchError, 'no window'):          # deep says the fault
            self.deep(mcp=['hub'])
        self.assertEqual(seen, [True])
        self.assertFalse(os.path.exists(self.own))

    def test_a_missing_file_says_so_and_passes_no_settings(self):
        os.remove(self.rendered)
        out = start.deep(self.h.settings, self.host, repo_path=self.repo, requester='r', slug='net-race',
                         brief=self.brief, account='1', plan=False)
        self.assertIn(sessionsettings.MISSING % self.rendered, out)
        self.assertNotIn('--settings', self.opened())

    def test_a_host_without_a_spec_passes_none_and_refuses_mcp(self):
        del self.host.session_spec
        self.assertNotIn('--settings', self.deep())
        with self.assertRaisesRegex(LaunchError, '--mcp needs the session settings'):
            self.deep(mcp=['hub'])


class Parsing(unittest.TestCase):
    def setUp(self):
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))), 'tools'))
        import launch
        self.launch = launch

    def test_mcp_repeats_on_the_four_starting_verbs_and_defaults_to_none(self):
        for verb in (['start', '--repo', 'a'], ['deep', '--repo-path', 'r', '--requester', 'q', '--slug', 's'],
                     ['consultant', '--repo-path', 'r'], ['topic', '--worktree', 'w', '--brief', 'b', '--slug', 's']):
            self.assertEqual(self.launch.parser().parse_args(verb).mcp, [], verb)
            self.assertEqual(self.launch.parser().parse_args(verb + ['--mcp', 'hub', '--mcp', 'blender']).mcp,
                             ['hub', 'blender'], verb)

    def test_session_refuses_mcp(self):
        a = self.launch.parser().parse_args(['start', '--session', 'k', '--mcp', 'hub'])
        with self.assertRaisesRegex(LaunchError, 'drop --mcp'):
            self.launch.run(a, None, None)

    def test_the_spec_is_the_claude_renderers(self):
        spec = self.launch.session_spec()
        self.assertEqual((spec.rel, spec.mcp_deny), (claude_content.SESSION_SETTINGS_REL,
                                                     claude_content.mcp_deny_names()))
        with mock.patch.object(self.launch.layout, 'default_kit', return_value=(None, 'an export')):
            self.assertIsNone(self.launch.session_spec().kit)
