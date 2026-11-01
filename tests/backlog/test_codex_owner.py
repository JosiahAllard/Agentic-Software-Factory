"""A Codex chat is never the owner, for tools/backlog.py too (an owner answer; docs/guard.md
"Session mark"). The tool's role comes from its session mark, and an unmarked process was the
owner; now an unmarked process with a Codex host above it (kitlib.host.registry.unowned_caller,
read from the process tree as the machine lock's caller reads it) is unlisted, and so is one whose
tree or hosts.json cannot be read (fail closed). The owner's own shell and the default host's
unmarked chat stay the owner. Each test hands in the process tree, so none depends on the host
the suite runs under."""
import unittest
from types import SimpleNamespace

from kitlib.backlog import audit_check, policy, roles
from kitlib.backlog.errors import Refusal
from kitlib.host import registry

CODEX_TREE = lambda: ['python.exe', 'powershell.exe', 'codex.exe', 'explorer.exe']    # noqa: E731
CLAUDE_TREE = lambda: ['python.exe', 'powershell.exe', 'claude.exe', 'explorer.exe']  # noqa: E731
OWNER_TREE = lambda: ['python.exe', 'powershell.exe', 'windowsterminal.exe', 'explorer.exe']  # noqa: E731
# Codex started from the owner's Claude chat, and Claude started from a Codex chat: any Codex
# ancestor makes the process not the owner's, the nearest host or not.
CLAUDE_UNDER_CODEX = lambda: ['python.exe', 'claude.exe', 'pwsh.exe', 'codex.exe']  # noqa: E731


def unreadable():
    raise OSError('no snapshot')


class UnownedCaller(unittest.TestCase):

    def test_a_codex_ancestor_anywhere_is_never_the_owner(self):
        for tree in (CODEX_TREE, CLAUDE_UNDER_CODEX):
            with self.subTest(tree=tree()):
                why = registry.unowned_caller(tree)
                self.assertIn('codex.exe', why)
                self.assertIn('never the owner', why)

    def test_the_owners_shell_and_claude_chat_are_not_held(self):
        self.assertIsNone(registry.unowned_caller(OWNER_TREE))
        self.assertIsNone(registry.unowned_caller(CLAUDE_TREE))

    def test_a_tree_that_cannot_be_read_fails_closed(self):
        self.assertIn('could not be read', registry.unowned_caller(unreadable))

    def test_the_images_are_the_hosts_with_no_subagent_identity(self):
        images = registry.unowned_images()
        self.assertIn('codex.exe', images)
        self.assertNotIn('claude.exe', images)
        self.assertNotIn('node.exe', images)


class CliRole(unittest.TestCase):

    def test_an_unmarked_codex_process_is_unlisted(self):
        self.assertIsNone(policy.cli_role({}, tree=CODEX_TREE))
        self.assertIsNone(policy.cli_role({}, tree=CLAUDE_UNDER_CODEX))
        self.assertIsNone(policy.cli_role({}, tree=unreadable))

    def test_the_owner_stays_the_owner(self):
        self.assertEqual(policy.cli_role({}, tree=OWNER_TREE), roles.OWNER)
        self.assertEqual(policy.cli_role({}, tree=CLAUDE_TREE), roles.OWNER)

    def test_a_mark_stands_on_codex_as_before(self):
        self.assertEqual(policy.cli_role({'PM_KIT_ROLE': 'coordinator'}, tree=CODEX_TREE), 'coordinator')
        self.assertIsNone(policy.cli_role({'PM_KIT_ROLE': 'owner'}, tree=CLAUDE_TREE))   # marked-owner: unlisted

    def test_an_owner_row_is_refused_and_the_refusal_says_why(self):
        args = SimpleNamespace(verb='retire', id='EX-1')
        with self.assertRaises(Refusal) as cm:
            policy.check(args, None, None, {}, tree=CODEX_TREE)
        self.assertIn('codex.exe', str(cm.exception))
        self.assertIn('never the owner', str(cm.exception))
        policy.check(args, None, None, {}, tree=OWNER_TREE)            # the owner may

    def test_a_collision_row_is_not_the_codex_processs(self):
        self.assertFalse(policy.may('pull-collision', {}, tree=CODEX_TREE))
        self.assertTrue(policy.may('pull-collision', {}, tree=OWNER_TREE))


class ByDesignSession(unittest.TestCase):
    """tools/backlog.py --session <a kit writer's name>: a marked session may not use one, and
    neither may an unmarked Codex process; the owner's own shell may."""

    def test_an_unmarked_codex_process_may_not_take_a_writers_name(self):
        name = audit_check.BY_DESIGN[0]
        with self.assertRaises(Refusal) as cm:
            policy.by_design_refusal(name, {}, tree=CODEX_TREE)
        self.assertIn('never the owner', str(cm.exception))
        with self.assertRaises(Refusal):
            policy.by_design_refusal(name, {}, tree=unreadable)
        with self.assertRaises(Refusal):
            policy.by_design_refusal(name, {'PM_KIT_ROLE': 'worker'}, tree=OWNER_TREE)

    def test_the_owner_and_other_names_pass(self):
        policy.by_design_refusal(audit_check.BY_DESIGN[0], {}, tree=OWNER_TREE)
        policy.by_design_refusal(audit_check.BY_DESIGN[0], {}, tree=CLAUDE_TREE)
        policy.by_design_refusal('kit-x', {}, tree=CODEX_TREE)
        policy.by_design_refusal(None, {}, tree=unreadable)


if __name__ == '__main__':
    unittest.main()
