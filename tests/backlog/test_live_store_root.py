"""kitlib.backlog.store.live_store_root: the checkout whose configured store holds a repo's
records, and the `why` a caller that must not read a wrong store refuses on."""
import unittest
from unittest import mock

from kitlib.backlog import store as smod
from kitlib.config import ConfigError


class Cfg:
    def __init__(self, named):
        self.named = named

    def written(self, section, key):
        return self.named


def live(pm, cfg=None):
    return mock.patch.object(smod, 'pm_data_live_store', return_value=pm)


class LiveStoreRootTest(unittest.TestCase):
    def test_a_named_store_in_the_repo_is_the_repo(self):
        with live(('P', 'x', None)) as pm:
            self.assertEqual(smod.live_store_root('R', Cfg('s')), ('R', None))
        pm.assert_not_called()

    def test_the_pm_data_worktree_when_it_names_one(self):
        with live(('P', 'kit/backlog', None)):
            self.assertEqual(smod.live_store_root('R', Cfg(None)), ('P', None))

    def test_the_repo_when_the_pm_data_worktree_names_none(self):
        with live(('P', None, None)):
            self.assertEqual(smod.live_store_root('R', Cfg(None)), ('R', None))

    def test_no_pm_data_worktree_is_the_repo(self):
        with live((None, None, None)):
            self.assertEqual(smod.live_store_root('R', Cfg(None)), ('R', None))

    def test_why_comes_back_with_the_repo(self):
        with live((None, None, 'not found')):
            self.assertEqual(smod.live_store_root('R', Cfg(None)), ('R', 'not found'))

    def test_cfg_is_loaded_when_not_given(self):
        with live((None, None, None)), mock.patch('kitlib.config.load', return_value=Cfg('s')) as ld:
            self.assertEqual(smod.live_store_root('R'), ('R', None))
        ld.assert_called_once_with('R')

    def test_an_unreadable_kit_json_raises(self):
        with mock.patch('kitlib.config.load', side_effect=ConfigError('bad')):
            with self.assertRaises(ConfigError):
                smod.live_store_root('R')


if __name__ == '__main__':
    unittest.main()
