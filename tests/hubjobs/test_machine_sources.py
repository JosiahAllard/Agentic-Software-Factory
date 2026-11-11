"""kitlib.hubjobs.machine_sources: the machine's Sources that tools/hub.py serve builds the hub
from, handed this package's makers as its timed jobs (none for a scratch store)."""
import os
import shutil
import tempfile
import unittest
from unittest import mock

from kitlib import hubjobs
from tests.home import pm_repo


class MachineSourcesTest(unittest.TestCase):
    """Under a temp home (as tests/hubsite/test_jobs.py MachineJobsTest)."""

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix='kit-hubjobs-src-')
        self.addCleanup(shutil.rmtree, self.home, True)
        env = mock.patch.dict(os.environ, {'USERPROFILE': self.home, 'HOME': self.home})
        env.start()
        self.addCleanup(env.stop)
        pm_repo(self, record=False)

    def test_the_machines_sources_carry_every_job_of_the_package(self):
        src = hubjobs.machine_sources(self.home)
        self.assertEqual(src.home, self.home)
        self.assertEqual([m.module for m in src.jobs], [m.module for m in hubjobs.makers()])
        self.assertIn('kitlib.hubjobs.plugin_refresh', [m.module for m in src.jobs])
        self.assertIsNotNone(src.sync)

    def test_a_scratch_stores_sources_carry_no_job_and_no_sync(self):
        src = hubjobs.machine_sources(self.home, sync=False)
        self.assertEqual(src.jobs, [])
        self.assertIsNone(src.sync)


if __name__ == '__main__':
    unittest.main()
