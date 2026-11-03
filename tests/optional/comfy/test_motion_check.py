"""optional/comfy/motion_check.py: the motion maths (contacts, foot slide, jerk, spikes, the share
of linear keys, the skin gap, LODs), without Blender.

usage: python -m unittest tests.optional.comfy.test_motion_check -v
"""
import io
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
CODE = os.path.join(HERE, '..', '..', '..', 'optional', 'comfy')
sys.path.insert(0, CODE)
import motion_check as M  # noqa: E402


class Contacts(unittest.TestCase):
    def test_runs_near_the_lowest_point_of_two_frames_or_more(self):
        h = [0.0, 0.01, 0.3, 0.5, 0.0, 0.3, 0.02, 0.0, 0.015]
        self.assertEqual(M.contacts(h, 0.02), [(0, 1), (6, 8)])
        self.assertEqual(M.contacts(h, 0.02, min_len=1), [(0, 1), (4, 4), (6, 8)])
        self.assertEqual(M.contacts([], 0.02), [])
        self.assertEqual(M.contacts([1.0, 1.0], 0.0), [(0, 1)])

    def test_the_ground_is_the_clip_lowest_point(self):
        self.assertEqual(M.contacts([5.0, 5.01, 6.0], 0.02), [(0, 1)])


class FootSlide(unittest.TestCase):
    def test_a_planted_foot_does_not_slide(self):
        pts = [(0, 0, 0)] * 4 + [(0.2, 0, 0.3), (0.4, 0, 0)]
        self.assertEqual(M.foot_slide(pts, 0.02, 100), [{'first': 0, 'last': 3, 'slide_cm': 0.0}])

    def test_slide_is_the_horizontal_path_in_cm(self):
        # 3 cm then 4 cm sideways while planted (height wobble ignored), then a lift
        pts = [(0, 0, 0), (0.03, 0, 0.005), (0.03, 0.04, 0), (0.5, 0.5, 0.4)]
        self.assertEqual(M.foot_slide(pts, 0.02, 100), [{'first': 0, 'last': 2, 'slide_cm': 7.0}])
        # scene units of centimetres: unit_cm 1
        self.assertEqual(M.foot_slide([(0, 0, 0), (3, 4, 0)], 2, 1)[0]['slide_cm'], 5.0)

    def test_the_path_is_its_length_not_the_end_distance(self):
        self.assertAlmostEqual(M.horizontal_path([(0, 0), (1, 0), (0, 0)], 0, 2), 2.0)


class Jerk(unittest.TestCase):
    def test_constant_acceleration_has_no_jerk(self):
        pts = [(0.5 * 9.8 * (t / 30) ** 2, 0, 0) for t in range(10)]
        for v in M.jerk(pts, 1 / 30):
            self.assertAlmostEqual(v, 0.0, places=6)

    def test_a_cubic_has_constant_jerk(self):
        # x = t^3 has jerk 6 everywhere
        pts = [((t / 10) ** 3, 0, 0) for t in range(8)]
        got = M.jerk(pts, 0.1)
        self.assertEqual(len(got), 5)
        for v in got:
            self.assertAlmostEqual(v, 6.0, places=6)

    def test_a_pop_shows_in_the_windows_around_it(self):
        pts = [(0, 0, 0)] * 10
        pts[5] = (0, 0.01, 0)
        got = M.jerk(pts, 1 / 30)
        self.assertEqual([i for i, v in enumerate(got) if v > 0], [2, 3, 4, 5])
        self.assertAlmostEqual(got[3], 0.03 * 27000)
        self.assertEqual(M.jerk(pts[:3], 1 / 30), [])

    def test_spikes_are_over_five_medians_and_the_floor(self):
        self.assertEqual(M.spikes([1, 1, 1, 2, 30, 1]), [4])
        self.assertEqual(M.spikes([0.1, 0.1, 0.9]), [])          # under the 1 m/s^3 floor
        self.assertEqual(M.spikes([0, 0, 0, 3]), [3])
        self.assertEqual(M.spikes([]), [])
        self.assertEqual(M.spikes([10, 10, 60], factor=5, floor=1), [2])


class LinearKeys(unittest.TestCase):
    def test_the_last_key_of_a_curve_leads_no_segment(self):
        self.assertEqual(M.linear_share([['LINEAR', 'BEZIER'], ['BEZIER', 'LINEAR', 'LINEAR']]),
                         (2 / 3, 3))

    def test_constant_counts_as_not_linear_and_no_segments_is_none(self):
        self.assertEqual(M.linear_share([['CONSTANT', 'LINEAR', 'LINEAR']]), (0.5, 2))
        self.assertEqual(M.linear_share([['LINEAR'], []]), (None, 0))
        self.assertEqual(M.linear_share([]), (None, 0))


class SkinGap(unittest.TestCase):
    def test_seams_in_one_mesh_and_contacts_across_meshes(self):
        pts = [(0, 0, 0), (0, 0, 0), (0.01, 0, 0), (1, 1, 1), (1, 1, 1.004)]
        owners = [0, 0, 0, 1, 2]
        pairs = M.touching_pairs(pts, owners, 1e-5, 0.005)
        self.assertEqual([(i, j) for i, j, _ in sorted(pairs)], [(0, 1), (3, 4)])
        self.assertAlmostEqual(sorted(pairs)[1][2], 0.004)

    def test_pairs_across_a_cell_boundary_are_found(self):
        pts = [(0.0049, 0, 0), (0.0051, 0, 0)]
        self.assertEqual(len(M.touching_pairs(pts, [0, 1], 1e-5, 0.005)), 1)

    def test_the_worst_gap_is_the_largest_opening(self):
        pairs = [(0, 1, 0.0), (2, 3, 0.004)]
        posed = [(0, 0, 0), (0.02, 0, 0), (0, 0, 0), (0.001, 0, 0)]
        g, which = M.worst_gap(pairs, posed)
        self.assertAlmostEqual(g, 0.02)
        self.assertEqual(which, (0, 1))
        self.assertEqual(M.worst_gap(pairs, [(0, 0, 0)] * 4)[1], None)   # pairs only closing


class Names(unittest.TestCase):
    def test_lod_of(self):
        for name, want in (('Body_LOD1', 'LOD1'), ('body.lod02', 'LOD2'), ('Body LOD 3', 'LOD3'),
                           ('Body', 'LOD0'), ('Lod-4_hair', 'LOD4')):
            self.assertEqual(M.lod_of(name), want, name)

    def test_triangles(self):
        self.assertEqual(M.triangles([3, 4, 5, 2]), 1 + 2 + 3)

    def test_foot_bones_prefer_deforming_and_skip_controls(self):
        names = ['foot.L', 'foot.R', 'foot_ik.L', 'MCH-foot.R', 'Foot_ctrl', 'toe.L', 'DEF-foot.L']
        self.assertEqual(M.foot_bones(names, {'DEF-foot.L'}), ['DEF-foot.L'])
        self.assertEqual(M.foot_bones(names, set()), ['foot.L', 'foot.R', 'DEF-foot.L'])


class Cli(unittest.TestCase):
    def test_out_outside_a_checkout_refuses_before_any_blender(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        with mock.patch.object(M.B, 'find_blender') as find, \
                mock.patch('sys.stderr', new_callable=io.StringIO) as err:
            with self.assertRaises(SystemExit) as e:
                M.main(['walker.glb', '--out', os.path.join(tmp, 'o')])
        self.assertEqual(e.exception.code, 2)
        self.assertIn('not inside a git checkout', err.getvalue())
        self.assertEqual(find.call_count, 0)

    def test_from_plain_python_it_relaunches_under_the_lock(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        os.makedirs(os.path.join(tmp, '.git'))
        with mock.patch.object(M.B, 'find_blender', return_value='BL'), \
                mock.patch.object(M.B, 'ensure_locked', side_effect=SystemExit(0)) as lock:
            with self.assertRaises(SystemExit):
                M.main(['walker.glb', '--out', os.path.join(tmp, 'o'), '--step', '3'])
        script, argv, run_slug, env, binary = lock.call_args.args
        self.assertEqual((os.path.basename(script), run_slug, env, binary),
                         ('motion_check.py', 'motion-walker', 'MOTION_CHECK_LOCKED', 'BL'))
        self.assertEqual(argv[-2:], ['--step', '3'])

    def test_bad_step_or_contact_is_an_argument_error(self):
        with mock.patch('sys.stderr', new_callable=io.StringIO):
            for args in (['--step', '0'], ['--contact', '0']):
                with self.assertRaises(SystemExit) as e:
                    M.parse_args(['m.glb', '--out', 'o'] + args)
                self.assertEqual(e.exception.code, 2)


if __name__ == '__main__':
    unittest.main()
