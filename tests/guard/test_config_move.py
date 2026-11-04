"""The guard across the machine config's moves (docs/kit-json.md "Moving the machine config
folder" and "Moving the machine config into the PM repo"; docs/guard.md "Known limits"). A machine
config still in the home folder (kitlib.config.user UNMOVED) fails closed for subagents, and its
ledger_path, with its ledger_dir's rulebooks and digest, stays protected: the old file's while it
is the only one, the new folder's file when both are there; a new file or folder the guard cannot
read fails closed too. The PM repository's config (OK) protects its own ledger, and the pointer's
machine config folder is held."""
import json
import os
import unittest

from kitlib.config import user as kuser
from .claudehost import decide

from . import world as W
from .world import event


class ConfigMove(unittest.TestCase):

    def setUp(self):
        self.w = W.World()
        self.addCleanup(self.w.cleanup)
        os.makedirs(os.path.dirname(self.w.ledger))
        self.new_dir = os.path.join(self.w.home, kuser.DIR_NAME)
        self.target = os.path.join(self.w.wt, 'a.py')

    def put(self, folder, body):
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, kuser.FILE_NAME), 'w', encoding='utf-8') as f:
            f.write(body if isinstance(body, str) else json.dumps(body))

    def edit_ledger(self, ledger, sub=False):
        return decide(event('Write', {'file_path': ledger, 'content': ''}, self.w.plain, sub=sub), self.w.ctx())

    def subagent_write(self):
        return decide(event('Write', {'file_path': self.target, 'content': ''}, self.w.wt, sub=True), self.w.ctx())

    def test_a_broken_new_file_denies_subagent_writes(self):
        self.put(self.new_dir, '{')
        d = self.subagent_write()
        self.assertIsNotNone(d)
        self.assertEqual(d.rule, 'ledger', d.reason)
        self.assertIn(kuser.DIR_NAME, d.reason)

    def test_an_unmoved_config_denies_every_subagent_write_and_keeps_its_ledger(self):
        self.put(self.new_dir, {'ledger_path': self.w.ledger})
        d = self.subagent_write()
        self.assertIsNotNone(d)
        self.assertEqual(d.rule, 'ledger', d.reason)
        self.assertIn('still in the home folder', d.reason)
        d = self.edit_ledger(self.w.ledger)
        self.assertIsNotNone(d)
        self.assertEqual(d.rule, 'ledger', d.reason)

    def test_an_unmoved_config_keeps_its_ledger_dir_books(self):
        split = os.path.join(self.w.base, 'pm', 'split')
        self.put(self.new_dir, {'ledger_dir': split, 'ledger_repos': ['kit']})
        for name in ('STANDING.md', os.path.join('rules', 'kit.md')):
            d = self.edit_ledger(os.path.join(split, name))
            self.assertIsNotNone(d, name)
            self.assertEqual(d.rule, 'ledger', d.reason)

    def test_with_a_pointer_the_pm_ledger_is_protected_and_the_pm_folder_is_held(self):
        # the machine config read from the PM repository (World.user_config): its ledger_path is
        # the ledger rule's, and a write into <pointer>/.pm-kit is machine-config's
        self.w.user_config({'ledger_path': self.w.ledger})
        ctx = lambda: self.w.ctx(pointer=lambda: self.w.data)
        d = decide(event('Write', {'file_path': self.w.ledger, 'content': ''}, self.w.plain, sub=True), ctx())
        self.assertIsNotNone(d)
        self.assertEqual(d.rule, 'ledger', d.reason)
        pm = os.path.join(self.w.data, kuser.DIR_NAME, kuser.FILE_NAME)
        d = decide(event('Write', {'file_path': pm, 'content': ''}, self.w.wt, sub=True), ctx())
        self.assertIsNotNone(d)
        self.assertEqual(d.rule, 'machine-config', d.reason)

    def test_one_hook_call_reads_one_located_file(self):
        self.put(self.new_dir, {'ledger_path': self.w.ledger})
        ctx = self.w.ctx()
        first = ctx.config_location()
        self.assertIs(ctx.config_location(), first)
        self.assertEqual(ctx.user()['ledger_path'], self.w.ledger)


if __name__ == '__main__':
    unittest.main()
