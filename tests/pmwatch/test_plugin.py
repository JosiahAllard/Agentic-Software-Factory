"""The PLUGIN event: pm_watch's check of the pm-kit plugin export against the kit's
refs/remotes/<remote>/main (kitlib.pmwatch.plugin_check over kitlib.pluginsource.refresh.poll). A temp kit
with a bare remote, a temp export root; spawn and the liveness probe are fakes."""
import json
import os
import unittest
from unittest import mock

from kitlib.pluginsource import (
    layout as plugin_layout, state as plugin_state, refresh as plugin_refresh)
from kitlib import pmwatch as W
from tests.pluginsource import Kit


NOW = 10000.0


def block(failures=0, ok_ago=60, every=300):
    """A plugin-refresh health block as the hub's GET health gives it."""
    return {"every_s": every, "runs": 5, "failures": failures, "running": False, "last_at": NOW - 5,
            "ok_at": None if ok_ago is None else NOW - ok_ago, "note": None, "error": None}


class PluginEventTest(unittest.TestCase):
    def setUp(self):
        self.k = Kit(self)
        self.spawned = []
        self.live = set()

    def watcher(self, auto, state=None, folders=(), hub_job=lambda: None):
        s = plugin_layout.Settings(self.k.root, auto, "origin")
        check = W.plugin_check(kit=self.k.kit, settings=s,
                               spawn=lambda kit, root: self.spawned.append((kit, root)),
                               alive=lambda pid, ct: pid in self.live, folders=list(folders),
                               hub_job=hub_job, now=lambda: NOW)
        w = W.Watcher(state if state is not None else {}, [], lambda: {}, os.path.join(self.k.base, "alerts"),
                      os.path.join(self.k.base, "sessions.json"), lambda n: None, branch_poll=0,
                      plugin=check)
        w.start(0.0)
        return w

    def main(self):
        return self.k.repo.git("rev-parse", "refs/remotes/origin/main")

    def export_at(self, sha):
        plugin_state.write_state(self.k.root, {"sha": sha, "folders": {}})

    def advance(self):
        r, self.n = self.k.repo, getattr(self, "n", 0) + 1
        name = "later-%d.txt" % self.n
        r.write(name, "z\n")
        r.commit("later", name)
        r.git("push", "-q", "origin", "main")
        return self.main()

    def test_current_is_silent(self):
        os.makedirs(self.k.root)
        self.export_at(self.main())
        w = self.watcher(auto=True)
        self.assertEqual(w.poll(1.0), [])
        self.assertEqual(self.spawned, [])

    def test_behind_reports_once_per_sha_and_auto_off_spawns_nothing(self):
        os.makedirs(self.k.root)
        self.export_at(self.main())
        new = self.advance()
        w = self.watcher(auto=False)
        self.assertEqual(w.poll(1.0), ["PLUGIN pm-kit plugin behind kit main %s: %s"
                                       % (new[:12], plugin_layout.refresh_cmd())])
        self.assertEqual(w.poll(2.0), [])
        w2 = self.watcher(auto=False, state=json.loads(json.dumps(w.s)))   # a relaunch: saved state
        self.assertEqual(w2.poll(3.0), [])
        newer = self.advance()
        self.assertEqual(len(w2.poll(4.0)), 1)
        self.assertIn(newer[:12], w2.s["plugin_main"])
        self.assertEqual(self.spawned, [])
        self.export_at(newer)                       # refreshed: silent, and the memory clears
        self.assertEqual(w2.poll(5.0), [])
        self.assertIsNone(w2.s["plugin_main"])

    def test_auto_refresh_spawns_once_per_sha_and_not_while_a_refresh_runs(self):
        os.makedirs(self.k.root)
        self.export_at(self.main())
        self.advance()
        w = self.watcher(auto=True)
        ev = w.poll(1.0)
        self.assertEqual(len(ev), 1)
        self.assertTrue(ev[0].endswith("(auto_refresh: started)"), ev[0])
        self.assertEqual(self.spawned, [(self.k.kit, self.k.root)])
        self.assertEqual(w.poll(2.0), [])
        self.assertEqual(len(self.spawned), 1)
        with open(os.path.join(self.k.root, plugin_refresh.PIDFILE), "w", encoding="utf-8") as fh:
            json.dump({"pid": 4242, "ctime": 1.0}, fh)
        self.live.add(4242)
        self.advance()
        ev = w.poll(3.0)
        self.assertEqual(len(ev), 1)
        self.assertTrue(ev[0].endswith("(auto_refresh: a refresh is already running)"), ev[0])
        self.assertEqual(len(self.spawned), 1)

    def test_the_hub_plugin_job_up_owns_the_refresh_and_hub_down_falls_back(self):
        os.makedirs(self.k.root)
        self.export_at(self.main())
        new = self.advance()
        w = self.watcher(auto=True, hub_job=lambda: block(failures=1))
        self.assertEqual(w.poll(1.0), ["PLUGIN pm-kit plugin behind kit main %s: %s"
                                       % (new[:12], plugin_layout.refresh_cmd())])
        self.assertEqual(self.spawned, [])
        w = self.watcher(auto=True, hub_job=lambda: None)
        self.assertTrue(w.poll(1.0)[0].endswith("(auto_refresh: started)"))
        self.assertEqual(len(self.spawned), 1)

    def test_a_healthy_hub_job_makes_a_plain_behind_export_silent_until_it_stops_being_healthy(self):
        os.makedirs(self.k.root)
        self.export_at(self.main())
        new = self.advance()
        cur = [block()]
        w = self.watcher(auto=True, hub_job=lambda: cur[0])
        self.assertEqual(w.poll(1.0), [])
        self.assertEqual(w.poll(400.0), [])
        self.assertEqual(self.spawned, [])
        cur[0] = block(failures=1)
        self.assertEqual(w.poll(800.0), ["PLUGIN pm-kit plugin behind kit main %s: %s"
                                         % (new[:12], plugin_layout.refresh_cmd())])
        self.assertEqual(self.spawned, [])

    def test_a_stale_or_unreadable_hub_job_or_auto_off_still_reports(self):
        os.makedirs(self.k.root)
        self.export_at(self.main())
        new = self.advance()
        line = ["PLUGIN pm-kit plugin behind kit main %s: %s" % (new[:12], plugin_layout.refresh_cmd())]
        self.assertEqual(self.watcher(auto=True, hub_job=lambda: block(ok_ago=1000)).poll(1.0), line)
        self.assertEqual(self.watcher(auto=True, hub_job=lambda: block(ok_ago=None)).poll(1.0), line)
        self.assertEqual(self.watcher(auto=False, hub_job=lambda: block()).poll(1.0), line)
        ev = self.watcher(auto=True, hub_job=lambda: None).poll(1.0)     # hub down or unreadable
        self.assertEqual(ev, [line[0] + " (auto_refresh: started)"])

    def test_a_healthy_hub_job_does_not_silence_no_export_or_an_unmigrated_folder(self):
        w = self.watcher(auto=True, hub_job=lambda: block())
        self.assertEqual(len(w.poll(1.0)), 1)
        os.makedirs(self.k.root)
        (two,) = self.k.folders(".claude-two")
        self.export_at(self.main())
        self.advance()
        w = self.watcher(auto=True, folders=[two], hub_job=lambda: block())
        self.assertIn("--migrate", w.poll(2.0)[0])

    def test_plugin_job_healthy(self):
        for b, want in ((block(), True), (block(ok_ago=900), True), (block(ok_ago=901), False),
                        (block(failures=1), False), (block(ok_ago=None), False),
                        ({"failures": 0}, False), ({}, False), (None, False),
                        (dict(block(), failures=True), False), (dict(block(), every_s=0), False)):
            self.assertEqual(W.plugin_job_healthy(b, NOW), want, b)

    def test_hub_plugin_job_up_reads_health_jobs(self):
        class Client:
            def __init__(self, health):
                self.health = health

            def get(self, path, timeout=None):
                if isinstance(self.health, Exception):
                    raise self.health
                return self.health

        def up(health):
            return W.hub_plugin_job_up(load=lambda: mock.Mock(port=1), make_client=lambda port: Client(health))
        self.assertTrue(up({"jobs": {"plugin-refresh": {}}}))
        self.assertFalse(up({"jobs": {}}))
        self.assertFalse(up({}))
        self.assertFalse(up(OSError("down")))

    def test_a_failing_check_is_an_error_on_the_third(self):
        def boom(last):
            raise plugin_layout.RefreshError("LOCALAPPDATA is not set")
        w = W.Watcher({}, [], lambda: {}, os.path.join(self.k.base, "alerts"),
                      os.path.join(self.k.base, "sessions.json"), lambda n: None, branch_poll=0,
                      plugin=boom)
        w.start(0.0)
        got = [w.poll(1.0 + i) for i in range(4)]
        self.assertEqual([g for g in got if g], [["ERROR plugin check: LOCALAPPDATA is not set"]])


    def test_no_export_or_a_folder_not_migrated_names_migrate_and_starts_nothing(self):
        w = self.watcher(auto=True)
        ev = w.poll(1.0)
        self.assertEqual(ev, ["PLUGIN pm-kit plugin behind kit main %s: %s (auto_refresh: not started: "
                              "no export yet)" % (self.main()[:12], plugin_layout.refresh_cmd(True))])
        os.makedirs(self.k.root)
        (two,) = self.k.folders(".claude-two")
        self.export_at(self.main())
        new = self.advance()
        w = self.watcher(auto=True, folders=[two])
        self.assertEqual(w.poll(2.0), ["PLUGIN pm-kit plugin behind kit main %s: %s (auto_refresh: not "
                                       "started: not migrated in %s)" % (new[:12], plugin_layout.refresh_cmd(True), two)])
        self.assertEqual(self.spawned, [])

    def test_an_unreadable_ref_or_state_is_an_error_on_the_third(self):
        for broken in ("ref", "state"):
            w = self.watcher(auto=False)
            if broken == "state":
                os.makedirs(self.k.root, exist_ok=True)
                with open(os.path.join(self.k.root, plugin_state.STATE), "w", encoding="utf-8") as fh:
                    fh.write("{not json")
                got = [w.poll(1.0 + i) for i in range(4)]
            else:
                with mock.patch.object(plugin_layout, "tracking_main", lambda kit, remote: (None, "git broke")):
                    got = [w.poll(1.0 + i) for i in range(4)]
            errs = [g for g in got if g]
            self.assertEqual(len(errs), 1, got)
            self.assertTrue(errs[0][0].startswith("ERROR plugin check: "), errs)
            self.assertIn("git broke" if broken == "ref" else plugin_state.STATE, errs[0][0])

    def export_kit(self, checkout):
        """A folder shaped like the plugin export the watcher runs from: a marker, no .git."""
        folder = os.path.join(self.k.base, "export-kit")
        os.makedirs(folder)
        with open(os.path.join(folder, plugin_layout.MARKER), "w", encoding="utf-8") as fh:
            json.dump({"checkout": checkout}, fh)
        return folder

    def test_a_kit_that_is_an_export_reads_the_ref_from_its_source_checkout(self):
        os.makedirs(self.k.root)
        self.export_at(self.main())
        new = self.advance()
        s = plugin_layout.Settings(self.k.root, False, "origin")
        got, _ = W.plugin_check(kit=self.export_kit(self.k.kit), settings=s, folders=[])(None)
        self.assertEqual(got, ["pm-kit plugin behind kit main %s: %s" % (new[:12], plugin_layout.refresh_cmd())])

    def test_an_export_with_no_source_checkout_is_a_failed_check_with_a_clear_line(self):
        s = plugin_layout.Settings(self.k.root, False, "origin")
        check = W.plugin_check(kit=self.export_kit(os.path.join(self.k.base, "gone")), settings=s,
                               folders=[])
        with self.assertRaises(plugin_layout.RefreshError) as cm:
            check(None)
        self.assertIn("export", str(cm.exception))
        self.assertIn("source checkout", str(cm.exception))

    def test_no_tracking_ref_is_silent(self):
        with mock.patch.object(plugin_layout, "tracking_main", lambda kit, remote: ("", None)):
            w = self.watcher(auto=True)
            self.assertEqual([w.poll(1.0 + i) for i in range(4)], [[], [], [], []])


if __name__ == "__main__":
    unittest.main()
