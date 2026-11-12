"""Claude Code's ContentRenderer (kitlib.host.content; docs/content.md "Claude").

Each role becomes `hosts/claude/shell/agents/<name>.md`: front matter opening with the generated-file
line as a YAML comment (kitlib.host.content.banner; the front matter stays the file's first thing),
then `name`, `description`, `tools` (the
role's capabilities as Claude tool names, in order; no line for `capabilities: all`), `model`
(hosts.json's Claude `role_models` model for the role, else its `tiers` model for its tier:
role_models, tier_models), `effort`
when the role has one, and for `cache: long` the `experimental` map with `cacheTtl: 1h`; then the
body with its tokens filled from tokens(): TOKENS, and PROFILE_TOKENS read from hosts.json's Claude
entry, so a value hosts.json holds is written once (`{{long_context_model}}` names the role's own
`model` line's model: long_context_phrase); `{{model:<tier>}}` from tier_models() and
`{{tool:<capability>}}` from TOOLS, the map the `tools` line uses. Lines end as the role file's do.
Skills are not copied: the plugin manifest points at `content/skills/` itself (docs/content.md
"Manifests").

manifests(kit, content) writes the files that carry the kit's one version (kit.json; ARCHITECTURE.md
"Packaging"), each JSON with indent 2 and a final newline:
- `.claude-plugin/plugin.json`: kit.json's name and version, every agent file, SKILLS_REL (the
  kit's own `content/skills/`), HOOKS_REL and the MCP servers (MCP_SERVERS: the machine lock's, the hub's and the Blender bridge's). It stays at the kit
  root: the plugin root is the whole kit, since its hooks and MCP server run kitlib and tools beside them (${CLAUDE_PLUGIN_ROOT}).
  That is the Windows file; each other platform hosts.json gives Claude gets the same manifest with
  that platform's MCP interpreter (mcp_servers) in manifest_rel(platform),
  `.claude-plugin/plugin.<platform>.json`, which the plugin export writes in its place.
- `.claude-plugin/marketplace.json`: the checkout as a local marketplace of that one plugin
  (`"source": "./"`), its version as `metadata.version`.
- HOOKS_REL: the plugin's hooks (HOOKS), each command line from kitlib.hookrun.main.manifest_command
  for Windows: the kit's one hook entry, ${CLAUDE_PLUGIN_ROOT}/ENTRY, with `claude`, the event and,
  for the coordinator rules, its part (boot/hook.py). Each other platform hosts.json gives Claude
  gets the same hooks with that platform's command line in hooks_rel(platform),
  `hooks/hooks.<platform>.json`; plugin.json names HOOKS_REL alone, and the plugin export
  (kitlib.pluginsource) writes the platform's file in its place.
JSON holds no comment, and these keep to the keys Claude Code documents, so they carry no
generated-file line; plugin.json's description says it is rendered.

render(content) also writes SESSION_SETTINGS_REL, the one settings file every launched session
starts with (`claude --settings`, kitlib.launcher.sessionsettings; docs/launcher.md "Session
settings"): SESSION_KEYS (claude.ai connectors off, the HIDDEN_SKILLS bundled and SYNCED_SKILLS
claude.ai-synced skills hidden from the model) and `permissions.deny`, SESSION_CUT minus the tool of every capability a role lists
(session_settings). `capabilities: all` takes nothing back, or no tool would ever be cut. One file,
not one per role, until a role needs a different one. mcp_deny_names() maps each MCP_SERVERS server,
and each of the owner's USER_MCP servers, to its deny entry, which `launch.py --mcp <server>` takes
back for one session.
"""
import json

from kitlib.host import agentname
from kitlib.hookrun import main as hookrun
from kitlib.host import registry
from kitlib.host.content import ContentError, banner, capped, quote, role_source, substitute

HOST = 'claude'
SHELL_REL = 'hosts/claude/shell'
AGENTS_REL = SHELL_REL + '/agents'
SKILLS_REL = 'content/skills'                  # the plugin's skills: the content folder itself
# the skills' copy before they moved to content/skills: any file there is stale
RETIRED_SKILLS_REL = SHELL_REL + '/skills'
HOOKS_REL = SHELL_REL + '/hooks/hooks.json'
MANIFEST_REL = '.claude-plugin/plugin.json'
MARKETPLACE_REL = '.claude-plugin/marketplace.json'
SESSION_SETTINGS_REL = SHELL_REL + '/session-settings.json'
PLUGIN_ROOT = '${CLAUDE_PLUGIN_ROOT}'
DESCRIPTION = ('The PM kit as a Claude Code plugin (ARCHITECTURE.md, Packaging): the agents rendered into '
               'hosts/claude/shell/agents/, the skills in content/skills/, its hooks and the machine lock and hub '
               'MCP servers; templates, optional tools and knowledge ship beside them (docs/shipping.md). '
               'Rendered from kit.json and content/ by tools/render_content.py; never edit it by hand.')
ENTRY = 'boot/hook.py'
# The plugin's hooks: (event, matcher or None, the part boot/hook.py runs or None). The kit header
# runs at every session and subagent start; the coordinator rules at a session's start, resume or
# compact. The guard (PreToolUse) and the PM checklist are in settings (kitlib.machinesetup).
HOOKS = (
    ('SessionStart', 'startup|resume|clear|compact', None),
    ('SessionStart', 'startup|resume|compact', 'coordinator'),
    ('SubagentStart', None, None),
)
MCP_SERVERS = {'machine-lock': {'command': 'python', 'args': [PLUGIN_ROOT + '/mcp/lock_server.py']},
               'hub': {'command': 'python', 'args': [PLUGIN_ROOT + '/mcp/hub_server.py']},
               'blender': {'command': 'python', 'args': [PLUGIN_ROOT + '/mcp/blender_server.py']}}


def _plugin_deny(server):
    """The permissions.deny entry of the kit's MCP server `server`: the plugin's tools are
    mcp__plugin_<plugin>_<server>__<tool>, so the bare mcp__plugin_<plugin>_<server> covers them all."""
    return 'mcp__plugin_%s_%s' % (agentname.PREFIX.rstrip(':'), server)


# What every launched session starts with (session_settings; the owner's answers, 2026-11-12).
# USER_MCP: the owner's user-scope MCP servers (not the kit's) that --mcp can turn on: {server: deny entry}.
USER_MCP = {'suno': 'mcp__suno'}
# SESSION_CUT: the tools and MCP servers it starts without, each a permissions.deny entry, a bare
# tool name or an MCP server's entry from _plugin_deny or USER_MCP, so mcp_deny_names() gives it
# and `launch.py --mcp <server>` can take it back. Artifact stays.
SESSION_CUT = ('NotebookEdit', 'ReportFindings', 'ScheduleWakeup', 'SendFeedback', 'Workflow',
               _plugin_deny('blender'), USER_MCP['suno'])
# The bundled skills hidden from the model (still /name to the owner). Not artifact-design,
# artifact-diagramming or artifact-capabilities: the Artifact tool, kept, says to load them.
HIDDEN_SKILLS = ('claude-api', 'claude-in-chrome', 'code-review', 'dataviz', 'fewer-permission-prompts',
                 'init', 'keybindings-help', 'loop', 'plugin-authoring', 'run', 'schedule',
                 'security-review', 'simplify', 'update-config', 'workflow-authoring')
# The claude.ai-synced skills, hidden the same way, by the name a session lists them under (sync
# stays on: syncClaudeAiSkills false would move the account's synced skills away).
SYNCED_SKILLS = tuple('anthropic-skills:' + name for name in (
    'artifact-emulator', 'built-in-browser', 'chrome-browser', 'computer-use', 'deep-research', 'docs',
    'docx', 'google-workspace', 'import-memory', 'morning', 'pdf', 'pptx', 'skill-creator', 'xlsx'))
# The settings keys beside permissions.deny.
SESSION_KEYS = {'disableClaudeAiConnectors': True,
                'skillOverrides': {name: 'user-invocable-only' for name in HIDDEN_SKILLS + SYNCED_SKILLS}}


def _json(data):
    """`data` as the rendered JSON files are written: indent 2, a final newline, UTF-8."""
    return (json.dumps(data, indent=2) + '\n').encode('utf-8')


def mcp_deny_names():
    """{server: its permissions.deny entry} of each MCP_SERVERS server (_plugin_deny) and each
    USER_MCP server (a user-scope server's tools are mcp__<server>__<tool>)."""
    names = {name: _plugin_deny(name) for name in sorted(MCP_SERVERS)}
    names.update(USER_MCP)
    return names


def session_settings(content, cut=SESSION_CUT):
    """The launched sessions' settings for `content`: SESSION_KEYS and {"permissions": {"deny":
    [...]}}, the entries of `cut` in order, less the Claude tool of every capability a role lists."""
    kept = {TOOLS[c] for role in content.roles if not role.all_tools for c in role.capabilities if c in TOOLS}
    return dict(SESSION_KEYS, permissions={'deny': [e for e in cut if e not in kept]})


def mcp_servers(platform='windows'):
    """MCP_SERVERS, each command `platform`'s MCP interpreter (hookrun.mcp_interpreter)."""
    command = hookrun.mcp_interpreter(platform)
    return {name: dict(server, command=command) for name, server in MCP_SERVERS.items()}
TOOLS = {
    'read': 'Read', 'edit': 'Edit', 'write': 'Write', 'search': 'Grep', 'find': 'Glob',
    'bash': 'Bash', 'powershell': 'PowerShell', 'worktree': 'EnterWorktree', 'enter-worktree': 'EnterWorktree', 'artifact': 'Artifact',
    'dispatch': 'Agent', 'message': 'SendMessage', 'list-agents': 'ListAgents', 'stop-task': 'TaskStop',
    'monitor': 'Monitor', 'tool-search': 'ToolSearch', 'skill': 'Skill', 'ask-owner': 'AskUserQuestion',
    'cron-create': 'CronCreate', 'cron-list': 'CronList', 'cron-delete': 'CronDelete',
    'web-search': 'WebSearch', 'web-fetch': 'WebFetch',
}
# The tokens whose Claude value no hosts.json field holds.
TOKENS = {
    'profile_path': '.claude/agent-profile.md',
    'coordinator_profile_path': '.claude/coordinator-profile.md',
    'instructions_file': 'CLAUDE.md',
    'config_dir_env': 'CLAUDE_CONFIG_DIR',
    'launch_agent': 'claude --agent',
}
# The tokens read from hosts.json's Claude entry (a registry.Host): token -> its value from it.
PROFILE_TOKENS = {
    'worktrees_dir': lambda host: host.profile.worktrees_directory,
    'owner_folder': lambda host: next(p for p in host.profile.owner_paths if p.endswith('/')),
    'user_folder': lambda host: '~/' + host.profile.state_directory,
    'coauthor_email': lambda host: host.profile.attribution.email,
    'long_context_model': lambda host: long_context_phrase(dict(host.tiers)['deep']),
}


def long_context_phrase(model):
    """How role prose names the model a launched long-context session runs on."""
    return 'the 1M-context %s model' % model


CACHE_TTL = {'long': '1h'}


def tokens(registry_path=None):
    """Every one-value token's Claude value: TOKENS, and PROFILE_TOKENS from hosts.json's Claude
    entry."""
    host = registry.host(HOST, registry_path)
    out = dict(TOKENS)
    out.update((name, value(host)) for name, value in PROFILE_TOKENS.items())
    return out


def agent_name(role):
    """The name Claude dispatches a kit role by: the plugin's prefix and the role."""
    return agentname.qualified(role)


class ClaudeContent:
    host = HOST

    def tier_models(self):
        """hosts.json's Claude `tiers`: the one tier -> model map."""
        return dict(registry.host(HOST).tiers)

    def role_models(self):
        """hosts.json's Claude `role_models`: the roles it runs on a model other than their tier's."""
        return dict(registry.host(HOST).role_models)

    def owned(self):
        return (AGENTS_REL,)

    def retired(self):
        """Folders this renderer once wrote and no longer does (kitlib.content: every file in
        one is stale)."""
        return (RETIRED_SKILLS_REL,)

    @staticmethod
    def tokens():
        return tokens()

    @staticmethod
    def tools():
        return dict(TOOLS)

    @staticmethod
    def role_path(name):
        return '%s/%s.md' % (AGENTS_REL, name)

    def settings(self, role, tiers=None, role_models=None):
        """[(key, value)] of the front matter `role`'s agent file runs it with, in the file's order:
        `tools` (no line for `capabilities: all`), `model` (Role.model_for: `role_models`, else
        `tiers`; default: hosts.json's), `effort`, `autoCompactWindow` (the role's `compact`; the
        CLI holds a subagent's window at or under it), and `cacheTtl` (under `experimental`)."""
        tiers = self.tier_models() if tiers is None else tiers
        role_models = self.role_models() if role_models is None else role_models
        out = []
        if not role.all_tools:
            missing = [c for c in role.capabilities if c not in TOOLS]
            if missing:
                raise ContentError('%s: no Claude tool for %s' % (self.role_path(role.name), ', '.join(missing)))
            out.append(('tools', ', '.join(TOOLS[c] for c in role.capabilities)))
        out.append(('model', role.model_for(tiers, role_models)))
        if role.effort:
            out.append(('effort', role.effort))
        if role.compact:
            out.append(('autoCompactWindow', str(role.compact)))
        if role.cache:
            out.append(('cacheTtl', CACHE_TTL[role.cache]))
        return out

    def agent_file(self, role, names, values=None, tiers=None, role_models=None):
        """The bytes of `role`'s agent file; `names` are every role's name (for agent tokens),
        `values` the tokens' values, `tiers` the tier -> model map and `role_models` the role ->
        model map (default: hosts.json's)."""
        src = self.role_path(role.name)
        values = tokens() if values is None else values
        tiers = self.tier_models() if tiers is None else tiers
        role_models = self.role_models() if role_models is None else role_models
        # {{long_context_model}} names the model this role runs on: its role_models entry, else its tier's
        values = dict(values, long_context_model=long_context_phrase(role.model_for(tiers, role_models)))
        fill = lambda text: substitute(text, values, agent_name, names, src, models=tiers,    # noqa: E731
                                       tools=TOOLS)
        lines = ['---', '# ' + banner(role_source(role.name)), 'name: %s' % role.name,
                 'description: %s' % quote(capped(fill(role.description), role_source(role.name)))]
        for key, value in self.settings(role, tiers, role_models):
            lines += ['experimental:', '  cacheTtl: %s' % value] if key == 'cacheTtl' else ['%s: %s' % (key, value)]
        lines.append('---')
        nl = role.newline
        return (nl.join(lines) + nl + fill(role.body)).encode('utf-8')

    def render(self, content):
        names = content.role_names()
        values, tiers, role_models = tokens(), self.tier_models(), self.role_models()
        out = {}
        for role in content.roles:
            out[self.role_path(role.name)] = self.agent_file(role, names, values, tiers, role_models)
        out[SESSION_SETTINGS_REL] = _json(session_settings(content))
        return out

    @staticmethod
    def session_settings_rel():
        """The kit-relative path of the launched sessions' settings file (render)."""
        return SESSION_SETTINGS_REL

    @staticmethod
    def mcp_deny_names():
        return mcp_deny_names()

    def hooks(self, registry_path=None, platform='windows'):
        tool_event = registry.host(HOST, registry_path).hook.tool_event
        events = {}
        for event, matcher, part in HOOKS:
            hook = {'type': 'command'}
            hook.update(hookrun.manifest_command('%s/%s' % (PLUGIN_ROOT, ENTRY), platform, HOST, event,
                                                  *([part] if part else []), blocking=event == tool_event))
            group = {'matcher': matcher} if matcher else {}
            group['hooks'] = [hook]
            events.setdefault(event, []).append(group)
        return {'hooks': events}

    def manifests(self, kit, content, registry_path=None):
        """{path: bytes} of the plugin manifest, the marketplace and the hooks file, for `kit`
        ({"name", "version"}, kitlib.content.load_kit) and `content`, the manifest and the hooks
        file once for each platform hosts.json gives Claude."""
        plugin = {
            'name': kit['name'], 'version': kit['version'], 'description': DESCRIPTION,
            'agents': ['./%s/%s.md' % (AGENTS_REL, n) for n in sorted(content.role_names())],
            'skills': ['./%s/' % SKILLS_REL], 'hooks': './' + HOOKS_REL,
        }
        market = {
            'name': kit['name'],
            'description': "The PM kit's plugin, loaded in place from a kit checkout (docs/install.md, "
                           'Plugin install).',
            'owner': {'name': 'PM kit'}, 'metadata': {'version': kit['version']},
            'plugins': [{'name': kit['name'], 'source': './',
                         'description': 'The PM kit: agents, skills and the machine lock and hub MCP servers.'}],
        }
        out = {MARKETPLACE_REL: _json(market)}
        for platform in registry.host(HOST, registry_path).platforms:
            out[manifest_rel(platform)] = _json(dict(plugin, mcpServers=mcp_servers(platform)))
            out[hooks_rel(platform)] = _json(self.hooks(registry_path, platform))
        return out


def manifest_rel(platform):
    """The plugin manifest for `platform`: MANIFEST_REL on Windows, the one Claude Code reads;
    beside it `plugin.<platform>.json` elsewhere, which kitlib.pluginsource.export.export writes in its
    place."""
    return MANIFEST_REL if platform == 'windows' else MANIFEST_REL[:-len('.json')] + '.%s.json' % platform


def hooks_rel(platform):
    """The hooks file for `platform`: HOOKS_REL on Windows, the one plugin.json names; beside it
    `hooks.<platform>.json` elsewhere, which kitlib.pluginsource.export.export writes in its place."""
    return HOOKS_REL if platform == 'windows' else HOOKS_REL[:-len('.json')] + '.%s.json' % platform


def renderer():
    return ClaudeContent()
