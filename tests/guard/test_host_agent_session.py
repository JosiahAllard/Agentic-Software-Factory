"""Who the guard counts as an agent session on each host (rules.agent_session; docs/guard-fallback.md
"Session mark"). Codex's hooks carry no subagent identity (hosts.json capabilities), so on Codex
every chat is an agent session, the owner's unmarked main chat too: the rules only agents are held
to (kit-json among them) refuse it there. On Claude an unmarked main chat is still the owner's.

Each call goes through kitlib.host.hookmain._judge with the host's own adapter, which hands the
guard the adapter's profile as the real hook does (seen live: an unmarked Codex desktop chat wrote
a worktree's .kit.json and the guard let it through as the owner's)."""
import os
import unittest
from types import SimpleNamespace

from hosts.claude.adapter.host import ADAPTER as CLAUDE
from hosts.codex.adapter import ADAPTER as CODEX
from kitlib import kitroot
from kitlib.guard import decide, rules
from kitlib.host import Action, hookmain

from . import world as W


class Base(unittest.TestCase):
    def setUp(self):
        self.w = W.World()
        self.addCleanup(self.w.cleanup)
        self.target = os.path.join(self.w.wt, '.kit.json')

    def judge(self, adapter, payload, role=None):
        """The Decision hookmain reaches on `payload` on `adapter`'s host, in a session marked
        `role` (None: unmarked, the main chat no launcher started)."""
        env = {'PM_KIT_ROLE': role} if role else {}
        guard = SimpleNamespace(decide=lambda a, environ=None, profile=None:
                                decide(a, self.w.ctx(environ=environ, profile=profile, kit=kitroot.root())))
        actions = adapter.parse('PreToolUse', payload)
        return hookmain._judge(adapter, guard, actions if isinstance(actions, tuple) else (actions,), env, None)

    def payload(self, tool, data):
        return {'hook_event_name': 'PreToolUse', 'tool_name': tool, 'tool_input': data, 'cwd': self.w.wt}

    def codex_patch(self):
        return self.payload('apply_patch', {'command': '*** Begin Patch\n*** Update File: %s\n@@\n-{}\n+{}\n+\n'
                                                       '*** End Patch' % self.target})

    def codex_shell(self):
        return self.payload('Bash', {'command': 'Add-Content .kit.json ""', 'shell': 'powershell.exe'})

    def claude_edit(self):
        return self.payload('Edit', {'file_path': self.target, 'old_string': '{', 'new_string': '{\n'})

    def kit_json_refused(self, d):
        self.assertEqual((d.verdict, d.rule), ('deny', 'kit-json'), d.reason)
        self.assertIn('only the owner changes it', d.reason)
        self.assertIn('Instead: python -m json.tool', d.reason)


class CodexEveryChatIsAnAgent(Base):

    def test_an_unmarked_codex_chat_may_not_edit_kit_json(self):
        for name, payload in (('apply_patch', self.codex_patch()), ('shell', self.codex_shell())):
            with self.subTest(name):
                self.kit_json_refused(self.judge(CODEX, payload))

    def test_a_marked_codex_session_is_refused_as_before(self):
        for role in ('coordinator', 'pm', 'worker'):
            with self.subTest(role):
                self.kit_json_refused(self.judge(CODEX, self.codex_patch(), role=role))

    def test_agent_session_is_true_for_every_codex_call(self):
        ctx = self.w.ctx(profile=CODEX.profile)
        self.assertTrue(rules.agent_session(Action('edit', 'apply_patch', path=self.target), ctx))


class ClaudeUnmarkedChatIsTheOwner(Base):

    def test_an_unmarked_claude_chat_may_edit_kit_json(self):
        d = self.judge(CLAUDE, self.claude_edit())
        self.assertEqual(d.verdict, 'allow', d.reason)

    def test_a_marked_claude_session_is_refused(self):
        self.kit_json_refused(self.judge(CLAUDE, self.claude_edit(), role='coordinator'))

    def test_agent_session_is_false_for_the_claude_owner(self):
        for profile in (CLAUDE.profile, None):       # None: the default host hosts.json names
            with self.subTest(profile=profile and 'claude'):
                ctx = self.w.ctx(profile=profile)
                self.assertFalse(rules.agent_session(Action('edit', 'Edit', path=self.target), ctx))


class UnknownHost(Base):

    def test_a_profile_no_registered_host_has_is_held_as_an_agent(self):
        import dataclasses
        odd = dataclasses.replace(CLAUDE.profile, state_directory='.elsewhere')
        ctx = self.w.ctx(profile=odd)
        self.assertTrue(rules.agent_session(Action('edit', 'Edit', path=self.target), ctx))


if __name__ == '__main__':
    unittest.main()
