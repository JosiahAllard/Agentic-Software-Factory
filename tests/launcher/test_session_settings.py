"""The settings file a launched session starts with (docs/launcher.md "Session settings"):
hosts.claude.adapter.content.session_settings, kitlib.launcher.sessionsettings, start's --settings
and --mcp, and unlisted still parsing a command line that carries --settings. The launch tests run on a cut list of their own, so the
shipped one can change without them."""
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
from tests.launcher.fakes import NOW
from tests.launcher.test_unlisted import Base, rows

CUT = ('Read', 'NotebookEdit', 'mcp__plugin_pm-kit_hub', 'mcp__plugin_pm-kit_blender')
HUB, BLENDER = 'mcp__plugin_pm-kit_hub', 'mcp__plugin_pm-kit_blender'


def role(capabilities=(), all_tools=False):
    return types.SimpleNamespace(capabilities=list(capabilities), all_tools=all_tools)


def write_json(path, data):
    """A settings file at `path`, as start writes one."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f)


def kit_with(root, data):
    """A kit folder under `root` whose rendered session settings are `data`; its Spec."""
    kit = os.path.join(root, 'kit')
    write_json(os.path.join(kit, *claude_content.SESSION_SETTINGS_REL.split('/')), data)
    return sessionsettings.Spec(claude_content.SESSION_SETTINGS_REL, claude_content.mcp_deny_names(), kit)


class Render(unittest.TestCase):
    def test_a_capability_a_role_names_is_taken_back_and_all_takes_nothing(self):
        c = types.SimpleNamespace(roles=[role(['read', 'search']), role(all_tools=True)])
        got = claude_content.session_settings(c, CUT)
        self.assertEqual(got, dict(claude_content.SESSION_KEYS,
                                   permissions={'deny': ['NotebookEdit', HUB, BLENDER]}))
        c = types.SimpleNamespace(roles=[role(all_tools=True)])
        self.assertEqual(claude_content.session_settings(c, CUT)['permissions']['deny'], list(CUT))

    def test_the_shipped_file_is_rendered_from_the_one_cut_list(self):
        from kitlib import content
        kit = content.load(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        out = claude_content.renderer().render(kit)
        self.assertEqual(json.loads(out[claude_content.SESSION_SETTINGS_REL].decode('utf-8')),
                         claude_content.session_settings(kit, claude_content.SESSION_CUT))

    def test_each_kit_server_has_its_plugin_deny_entry_and_each_user_server_its_own(self):
        self.assertEqual(claude_content.mcp_deny_names(),
                         dict({n: 'mcp__plugin_pm-kit_%s' % n for n in claude_content.MCP_SERVERS},
                              suno='mcp__suno'))

    def test_the_owners_skills_are_hidden_sync_left_on_and_artifact_kept(self):
        got = claude_content.session_settings(types.SimpleNamespace(roles=[]))
        self.assertNotIn('syncClaudeAiSkills', got)        # false would move the account's synced skills away
        bundled = ('claude-api', 'claude-in-chrome', 'code-review', 'dataviz', 'fewer-permission-prompts',
                   'init', 'keybindings-help', 'loop', 'plugin-authoring', 'run', 'schedule',
                   'security-review', 'simplify', 'update-config', 'workflow-authoring')
        synced = ['anthropic-skills:' + n for n in (
            'artifact-emulator', 'built-in-browser', 'chrome-browser', 'computer-use', 'deep-research',
            'docs', 'docx', 'google-workspace', 'import-memory', 'morning', 'pdf', 'pptx', 'skill-creator',
            'xlsx')]
        self.assertEqual(got['skillOverrides'], {n: 'user-invocable-only' for n in list(bundled) + synced})
        for kept in ('artifact-design', 'artifact-diagramming', 'artifact-capabilities'):
            self.assertNotIn(kept, got['skillOverrides'])
        self.assertNotIn('Artifact', claude_content.SESSION_CUT)
        self.assertNotIn('Artifact', got['permissions']['deny'])

    def test_every_server_the_cut_denies_is_one_mcp_can_take_back(self):
        servers = [e for e in claude_content.SESSION_CUT if e.startswith('mcp__')]
        self.assertTrue(servers)
        self.assertLessEqual(set(servers), set(claude_content.mcp_deny_names().values()))


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
        self.assertEqual(sessionsettings.plan(self.spec, path, (), self.root, 'n', 7), (path, None))

    def test_plan_with_mcp_drops_that_servers_deny_entry_only(self):
        path, _ = sessionsettings.resolve(self.spec)
        got, data = sessionsettings.plan(self.spec, path, ['hub'], self.root, 'deep-x', 7)
        self.assertEqual(got, os.path.join(self.root, 'launcher', 'settings-deep-x-7.json'))
        self.assertEqual(data, {'permissions': {'deny': ['Read', 'NotebookEdit', BLENDER]}, 'other': 1})
        self.assertFalse(os.path.exists(got))

    def test_an_unknown_server_is_refused_naming_the_known_ones(self):
        path, _ = sessionsettings.resolve(self.spec)
        with self.assertRaisesRegex(LaunchError, r'--mcp nope: no such MCP server \(known: blender, hub'):
            sessionsettings.plan(self.spec, path, ['nope'], self.root, 'n', 7)

    def test_a_file_already_there_is_never_this_launchs(self):
        path, _ = sessionsettings.resolve(self.spec)
        own, data = sessionsettings.plan(self.spec, path, ['hub'], self.root, 'n', 7)
        write_json(own, data)
        with self.assertRaisesRegex(LaunchError, 'already there'):
            sessionsettings.plan(self.spec, path, ['hub'], self.root, 'n', 7)


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
        self.own = os.path.join(self.temp, 'launcher', 'settings-deep-net-race-%d.json' % (NOW.timestamp() * 1000))

    def test_a_window_with_a_spaced_settings_path_is_still_found_by_unlisted(self):
        self.assertIn(' ', self.rendered)
        text = self.deep()
        self.assertIn(' --settings %s --remote-control ' % window.q(self.rendered), text)
        found, never = self.classify(rows(text, 700, 'deep-net-race'))
        self.assertEqual((never, found['deep-net-race'][0].brief), ({}, self.brief))

    def test_the_bash_window_with_a_spaced_settings_path_still_parses(self):
        with mock.patch('kitlib.platform.NAME', 'linux'):
            text = self.deep()
            self.assertIn(' --settings %s --remote-control ' % window.q(self.rendered), text)
            self.assertIsNotNone(unlisted.read_window(text, 'deep-net-race'))

    def test_mcp_starts_with_a_file_of_its_own(self):
        text = self.deep(mcp=['hub'])
        self.assertIn(' --settings %s --remote-control ' % window.q(self.own), text)
        with open(self.own, encoding='utf-8') as f:
            self.assertEqual(json.load(f)['permissions']['deny'], ['Read', 'NotebookEdit', BLENDER])
        self.assertEqual(os.listdir(os.path.dirname(self.own)), [os.path.basename(self.own)])   # no temp file

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

    def test_a_failed_launch_leaves_a_running_sessions_file_of_that_name(self):
        live = os.path.join(self.temp, 'launcher', 'settings-deep-net-race-1.json')     # an earlier launch's
        write_json(live, {'permissions': {'deny': []}})
        self.host.open_window = mock.Mock(side_effect=OSError('no window'))
        with self.assertRaisesRegex(LaunchError, 'no window'):
            self.deep(mcp=['hub'])
        self.assertEqual(os.listdir(os.path.dirname(live)), [os.path.basename(live)])
        with open(live, encoding='utf-8') as f:
            self.assertEqual(json.load(f), {'permissions': {'deny': []}})

    def test_a_missing_file_says_so_and_passes_no_settings(self):
        os.remove(self.rendered)
        out = start.deep(self.h.settings, self.host, repo_path=self.repo, requester='r', slug='net-race',
                         brief=self.brief, account='1', plan=False)
        self.assertIn(sessionsettings.MISSING % self.rendered, out)
        self.assertNotIn('--settings', self.opened())

    def test_a_host_without_a_spec_passes_none_and_refuses_mcp(self):
        self.host.session_spec = None
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

    def test_a_renderer_without_the_settings_members_gives_no_spec(self):
        for missing in ('session_settings_rel', 'mcp_deny_names'):
            members = {'session_settings_rel': lambda: 'x.json', 'mcp_deny_names': dict}
            del members[missing]
            renderer = types.SimpleNamespace(**members)
            adapter = types.SimpleNamespace(ADAPTER=types.SimpleNamespace(
                content=lambda: types.SimpleNamespace(renderer=lambda: renderer)))
            with mock.patch('_host.adapter_module', return_value=adapter):
                self.assertIsNone(self.launch.session_spec(), missing)
