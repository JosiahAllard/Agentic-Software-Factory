"""A Codex chat is never the owner (an owner answer; docs/guard-fallback.md "Session mark"): owner
actions are done only from a Claude chat or by hand. rules.is_owner_session is false for every
Codex chat, the unmarked main chat too, so each owner-only check refuses one; the owner's unmarked
Claude chat still passes. Codex's hooks carry no subagent identity (hosts.json capabilities
subagent_identity_in_hooks false), the same signal rules.agent_session reads.

Each call goes through kitlib.host.hookmain._judge with the host's own adapter, as the real hook
does (test_host_agent_session's way)."""
import os
import unittest
from types import SimpleNamespace

from hosts.claude.adapter.host import ADAPTER as CLAUDE
from hosts.codex.adapter import ADAPTER as CODEX
from kitlib import kitroot, lockmcp
from kitlib.guard import decide, gaterole, rules
from kitlib.host import Action, hookmain, registry

from . import world as W

OWNER_EMAIL = 'owner@example.com'


class Base(unittest.TestCase):
    def setUp(self):
        self.w = W.World(kit={'repo': {'name': 'demo'}, 'owner': {'email': OWNER_EMAIL}})
        self.addCleanup(self.w.cleanup)
        self.w.git.ancestors.add((W.SHA_WT, 'main'))
        self.w.user_config({'pm_data': {'path': self.w.data}})
        self.kit = kitroot.root()

    def judge(self, adapter, payload, role=None):
        """The Decision hookmain reaches on `payload` on `adapter`'s host, in a session marked
        `role` (None: unmarked, the main chat no launcher started)."""
        env = {'PM_KIT_ROLE': role} if role else {}
        guard = SimpleNamespace(decide=lambda a, environ=None, profile=None:
                                decide(a, self.w.ctx(environ=environ, profile=profile, kit=self.kit)))
        actions = adapter.parse('PreToolUse', payload)
        return hookmain._judge(adapter, guard, actions if isinstance(actions, tuple) else (actions,), env, None)

    def payload(self, tool, data):
        return {'hook_event_name': 'PreToolUse', 'tool_name': tool, 'tool_input': data, 'cwd': self.w.wt}

    # one call per host for the same operation
    def write(self, adapter, path):
        if adapter is CODEX:
            return self.payload('apply_patch', {'command': '*** Begin Patch\n*** Add File: %s\n+x\n*** End Patch'
                                                           % path})
        return self.payload('Write', {'file_path': path, 'content': 'x'})

    def shell(self, adapter, command):
        if adapter is CODEX:
            return self.payload('Bash', {'command': command, 'shell': 'powershell.exe'})
        return self.payload('PowerShell', {'command': command})

    def mcp(self, adapter, tool):
        return self.payload(adapter.profile.mcp_tool_format.format(
            plugin=registry.PLUGIN_NAME, server=lockmcp.SERVER_NAME, tool=tool), {})

    def refused_on_codex_only(self, make, rule):
        """`make(adapter)` builds the payload: an unmarked Codex chat is refused by `rule`, the
        owner's unmarked Claude chat is not held by it."""
        d = self.judge(CODEX, make(CODEX))
        self.assertEqual((d.verdict, d.rule), ('deny', rule), d.reason)
        self.assertIn(' Instead: ', d.reason)
        d = self.judge(CLAUDE, make(CLAUDE))
        self.assertTrue(d.verdict == 'allow' or d.rule != rule, d.reason)
        return d


class OwnerOnlyChecksRefuseACodexChat(Base):

    def test_a_machine_config_write(self):
        target = os.path.join(self.w.wt, '.pm-kit', 'settings.json')
        self.refused_on_codex_only(lambda a: self.write(a, target), 'machine-config')

    def test_a_commit_under_the_owners_identity(self):
        cmd = 'git commit --author "Owner <%s>" -m x' % OWNER_EMAIL
        self.refused_on_codex_only(lambda a: self.shell(a, cmd), 'owner-identity')

    def test_an_owner_tool(self):
        cmd = 'python "%s" hold whole --for 1h' % W.fwd(os.path.join(self.w.kit_dir, 'tools', 'machine_lock.py'))
        self.refused_on_codex_only(lambda a: self.shell(a, cmd), 'owner-tool')

    def test_the_owners_delegation_list(self):
        target = os.path.join(self.w.home, '.pm-kit', 'owner-delegations.json')
        self.refused_on_codex_only(lambda a: self.write(a, target), 'owner-delegations')

    def test_a_hub_owner_verb(self):
        cmd = 'python "%s" answer EX-1 --label yes' % W.fwd(os.path.join(self.w.kit_dir, 'tools', 'hub.py'))
        self.refused_on_codex_only(lambda a: self.shell(a, cmd), 'hub-owner-verb')

    def test_a_hub_secret_write(self):
        target = os.path.join(self.w.home, '.pm-kit', 'hub', 'owner.token')
        self.refused_on_codex_only(lambda a: self.write(a, target), 'hub-secrets')

    def test_a_backlog_owner_write(self):
        cmd = 'python -c "from kitlib.hubsite import recordwrite"'
        self.refused_on_codex_only(lambda a: self.shell(a, cmd), 'backlog-raw-write')

    def test_a_lock_mcp_owner_tool(self):
        self.refused_on_codex_only(lambda a: self.mcp(a, lockmcp.OWNER_ONLY[0]), 'lock-mcp')

    def test_the_refusal_says_a_codex_chat_is_never_the_owner(self):
        d = self.judge(CODEX, self.write(CODEX, os.path.join(self.w.wt, '.pm-kit', 'settings.json')))
        self.assertIn('an unmarked Codex chat, never the owner', d.reason)
        self.assertNotIn('marked owner', d.reason)

    def test_the_gate_allow_refusal_calls_the_codex_chat_unmarked(self):
        cmd = 'python "%s" allow kit/x --runs 8 --why y' % W.fwd(os.path.join(self.w.kit_dir, 'tools', 'gate.py'))
        self.refused_on_codex_only(lambda a: self.shell(a, cmd), 'gate-allow')
        d = self.judge(CODEX, self.shell(CODEX, cmd))
        self.assertIn('This session is an unmarked Codex chat, never the owner', d.reason)
        self.assertNotIn('a session marked', d.reason)


class TheOwnerTests(Base):
    """The predicates every owner-only check reads, on each host's profile."""

    def ctx(self, adapter, role=None):
        return self.w.ctx(environ={'PM_KIT_ROLE': role} if role else {}, profile=adapter.profile)

    def act(self):
        return Action('shell', 'Bash', command='git status', cwd=self.w.wt)

    def test_is_owner_session_is_false_for_every_codex_chat(self):
        for role in (None, 'pm', 'coordinator', 'worker'):
            with self.subTest(role=role):
                self.assertFalse(rules.is_owner_session(self.act(), self.ctx(CODEX, role)))

    def test_is_owner_session_holds_for_the_claude_owner_alone(self):
        self.assertTrue(rules.is_owner_session(self.act(), self.ctx(CLAUDE)))
        self.assertTrue(rules.is_owner_session(self.act(), self.w.ctx(environ={})))   # the default host
        self.assertFalse(rules.is_owner_session(self.act(), self.ctx(CLAUDE, 'coordinator')))

    def test_a_profile_no_registered_host_has_is_never_the_owner(self):
        import dataclasses
        odd = dataclasses.replace(CLAUDE.profile, state_directory='.elsewhere')
        self.assertFalse(rules.is_owner_session(self.act(), self.w.ctx(environ={}, profile=odd)))

    def test_the_ledgers_wider_refusals_bind_an_unmarked_codex_chat(self):
        self.assertTrue(rules.ledger_wide_binds(self.act(), self.ctx(CODEX)))
        self.assertFalse(rules.ledger_wide_binds(self.act(), self.ctx(CODEX, 'pm')))
        self.assertFalse(rules.ledger_wide_binds(self.act(), self.ctx(CLAUDE)))

    def test_gate_allow_is_not_the_codex_chats(self):
        self.assertFalse(gaterole.may_allow(self.act(), self.ctx(CODEX)))
        self.assertTrue(gaterole.may_allow(self.act(), self.ctx(CLAUDE)))


class EveryCodexCallReachesTheGuard(Base):
    """No fast path on Codex (hosts.json codex hook.fast_path "none"): boot/hook.py hands even a
    tool guarded_tools leaves out to hookmain, in the unmarked chat as in a marked one; on the
    default host (fast_path "all") that call still returns before the kit boots."""

    def entry(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            'never_owner_hook', os.path.join(os.path.dirname(rules.__file__), '..', '..', 'boot', 'hook.py'))
        hook = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(hook)
        return hook

    def dispatched(self, host, tool, environ):
        """Whether boot/hook.py <host> PreToolUse reaches hookmain.dispatch for a call to `tool`."""
        import io
        import sys
        import types
        from unittest import mock
        hook, seen = self.entry(), []
        boot = types.ModuleType('kitboot')
        boot.boot = lambda here=None: self.kit
        with mock.patch.dict(sys.modules, {'kitboot': boot}),                 mock.patch('kitlib.host.hookmain.dispatch', lambda *a, **k: seen.append(a[1]) or 0):
            rc = hook.main([host, 'PreToolUse'], io.StringIO('{"tool_name": "%s"}' % tool), io.StringIO(), environ)
        self.assertEqual(rc, 0)
        return seen == ['PreToolUse']

    def test_an_unlisted_codex_tool_reaches_the_guard_in_any_session(self):
        for tool in ('Read', 'update_plan', 'some_new_tool'):
            for environ in ({}, {'PM_KIT_ROLE': ''}, {'PM_KIT_ROLE': 'worker'}):
                with self.subTest(tool=tool, environ=environ):
                    self.assertTrue(self.dispatched('codex', tool, environ))
        self.assertFalse(self.dispatched('claude', 'TodoWrite', {}), 'the default host keeps its fast path')

    def test_the_guard_allows_a_read_from_the_unmarked_chat(self):
        d = self.judge(CODEX, self.payload('Read', {'file_path': os.path.join(self.w.wt, 'a.txt')}))
        self.assertEqual(d.verdict, 'allow', d.reason)


if __name__ == '__main__':
    unittest.main()
