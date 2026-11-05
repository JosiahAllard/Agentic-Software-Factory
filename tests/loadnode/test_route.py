"""kitlib.loadnode.route: `node.py run --node auto` picks the machine with the most cpu slots free
now among main and the nodes that opted in for the repository; a tie goes to main; none free
leaves the job on main, whose lock queues it."""
import unittest

from kitlib.loadnode import box, remote, route
from kitlib.loadnode.registry import Node
from tests.loadnode.test_registry import KEY


def node(name, slots=2, memory_mb=4096, repos=('my-repo',)):
    return Node(name, '10.0.0.%d' % (len(name) + 1), 'pmnode', KEY, slots, memory_mb, 22, None,
                tuple(repos))


def lock(cpu_free=4, gpu_free=0, cpu_slots=4, gpu_slots=0, waiting=0, **kw):
    d = {'cpu_free': cpu_free, 'gpu_free': gpu_free, 'cpu_slots': cpu_slots,
         'gpu_slots': gpu_slots, 'waiting': waiting, 'unreadable': False, 'pending': False}
    d.update(kw)
    return d


def ok(name, jobs=0, mem_free=8192, verbs=None, **lk):
    reply = {'verbs': list(box.RUN_VERBS) + ['status'] if verbs is None else verbs,
             'jobs': jobs, 'lock': lock(**lk), 'hardware': {'memory_mb': {'free': mem_free}}}
    return remote.Result(name, remote.OK, reply, '')


NEED = {'cpu': 2, 'gpu': 0}


class Fits(unittest.TestCase):

    def test_free_slots_and_no_one_waiting(self):
        self.assertEqual(route.fits(lock(cpu_free=2), NEED), '')
        self.assertIn('1 of 2 cpu', route.fits(lock(cpu_free=1), NEED))
        self.assertIn('0 of 1 gpu', route.fits(lock(), {'cpu': 1, 'gpu': 1}))
        self.assertIn('1 run waiting', route.fits(lock(waiting=1), NEED))

    def test_a_node_need_is_capped_at_its_slots(self):
        self.assertEqual(route.node_need({'cpu': 12, 'gpu': 0}, lock(cpu_slots=4)),
                         {'cpu': 4, 'gpu': 0})
        self.assertIsNone(route.node_need({'cpu': 1, 'gpu': 1}, lock()))
        self.assertEqual(route.node_need({'cpu': 1, 'gpu': 2}, lock(gpu_slots=1)),
                         {'cpu': 1, 'gpu': 1})


class NodeFits(unittest.TestCase):

    def test_each_reason_a_node_is_passed_over(self):
        n = node('n1')
        cases = (
            (node('n1', repos=()), ok('n1'), 'not opted in'),
            (n, None, 'not asked'),
            (n, remote.Result('n1', 'unreachable', None, 'x'), 'unreachable'),
            (n, ok('n1', verbs=['status']), 'no run verbs'),
            (n, ok('n1', unreadable=True), 'not fully readable'),
            (n, ok('n1', pending=True), 'pending'),
            (n, ok('n1', jobs=2), 'runs 2 jobs'),
            (n, ok('n1', mem_free=1000), '1000 MB memory free of 2048'),
            (n, ok('n1', cpu_free=1), '1 of 2 cpu'),
        )
        for nd, res, why in cases:
            with self.subTest(why=why):
                self.assertIn(why, route.node_fits(nd, res, 'my-repo', NEED))
        self.assertEqual(route.node_fits(n, ok('n1'), 'my-repo', NEED), '')
        self.assertEqual(route.node_fits(n, ok('n1', mem_free=None), 'my-repo', NEED), '')
        self.assertIn('no gpu', route.node_fits(n, ok('n1'), 'my-repo', {'cpu': 1, 'gpu': 1}))

    def test_verbs_names_the_ones_the_job_needs_and_none_keeps_the_run_verbs(self):
        n, land = node('n1'), (box.LAND_VERB,)
        self.assertEqual(route.node_fits(n, ok('n1'), 'my-repo', NEED, None),
                         route.node_fits(n, ok('n1'), 'my-repo', NEED))
        self.assertEqual(route.node_fits(n, ok('n1', verbs=['status']), 'my-repo', NEED),
                         'its kit serves no run verbs (update it)')
        self.assertEqual(route.node_fits(n, ok('n1'), 'my-repo', NEED, land),
                         'its kit serves no land verb (update it)')
        self.assertEqual(route.node_fits(n, ok('n1', verbs=['land']), 'my-repo', NEED, land), '')

    def test_a_heavy_kind_takes_the_whole_node(self):
        self.assertEqual(route.node_fits(node('n1'), ok('n1', cpu_free=4), 'my-repo',
                                         {'cpu': 12, 'gpu': 0}), '')
        self.assertIn('3 of 4 cpu', route.node_fits(node('n1'), ok('n1', cpu_free=3), 'my-repo',
                                                    {'cpu': 12, 'gpu': 0}))


class Choose(unittest.TestCase):

    def test_the_most_free_wins_and_a_tie_goes_to_main(self):
        nodes = [node('n1'), node('n2')]
        res = [ok('n2', cpu_free=4), ok('n1', cpu_free=3)]
        c = route.choose(nodes, res, 'my-repo', lock(cpu_free=2, cpu_slots=12), NEED)
        self.assertEqual((c.where, c.node.name), ('n2', 'n2'))
        c = route.choose(nodes, res, 'my-repo', lock(cpu_free=4, cpu_slots=12), NEED)
        self.assertEqual((c.where, c.node), (route.MAIN, None))

    def test_a_tie_between_nodes_goes_to_the_first_in_the_registry(self):
        nodes = [node('n1'), node('n2')]
        res = [ok('n2', cpu_free=4), ok('n1', cpu_free=4)]
        c = route.choose(nodes, res, 'my-repo', lock(cpu_free=0, cpu_slots=12), NEED)
        self.assertEqual(c.where, 'n1')
        self.assertIn('main: 0 of 2 cpu', c.why)

    def test_none_free_leaves_it_on_main_and_says_why(self):
        nodes = [node('n1'), node('n2', repos=())]
        res = [ok('n1', cpu_free=0), ok('n2')]
        c = route.choose(nodes, res, 'my-repo', lock(cpu_free=1, cpu_slots=12), NEED)
        self.assertEqual((c.where, c.node), (route.MAIN, None))
        self.assertIn('main queues it', c.why)
        self.assertIn('n1: 0 of 2 cpu', c.why)
        self.assertIn('n2: not opted in', c.why)

    def test_main_busy_and_a_node_free(self):
        c = route.choose([node('n1')], [ok('n1')], 'my-repo',
                         lock(cpu_free=12, cpu_slots=12, waiting=2), NEED)
        self.assertEqual(c.where, 'n1')

    def test_verbs_passes_over_a_node_that_does_not_serve_them(self):
        nodes, land = [node('n1'), node('n2')], (box.LAND_VERB,)
        res = [ok('n1', cpu_free=4), ok('n2', cpu_free=3, verbs=list(box.RUN_VERBS) + ['land'])]
        main = lock(cpu_free=0, cpu_slots=12)
        self.assertEqual(route.choose(nodes, res, 'my-repo', main, NEED).where, 'n1')
        c = route.choose(nodes, res, 'my-repo', main, NEED, verbs=land)
        self.assertEqual(c.where, 'n2')
        self.assertIn('n1: its kit serves no land verb', c.why)

    def test_no_nodes_is_main(self):
        self.assertEqual(route.choose([], [], 'my-repo', lock(), NEED).where, route.MAIN)


class Decide(unittest.TestCase):

    def go(self, name='auto', nodes=None, dirty=False, need=lambda: NEED, main=None, results=None,
           action='run', **kw):
        said = []
        nodes = [node('n1')] if nodes is None else nodes
        d = route.decide(action, name, nodes, 'my-repo', dirty, '/top', need,
                         main or (lambda: lock()), lambda takers: results or [ok(n.name) for n in takers],
                         said.append, **kw)
        return d, said

    def test_a_named_node_is_refused_or_taken_without_a_fallback(self):
        d, said = self.go('nope')
        self.assertEqual((d.action, d.err), ('usage', 'USAGE: no node named nope in the registry'))
        d, _ = self.go('n1', nodes=[node('n1', repos=())])
        self.assertIn('n1 has not opted in my-repo', d.err)
        d, _ = self.go('n1', dirty=True)
        self.assertIn('/top has tracked changes not committed; a node runs HEAD', d.err)
        d, _ = self.go('n1', dirty=True, action='gate')
        self.assertIn('a node gates HEAD', d.err)
        d, said = self.go('n1')
        self.assertEqual((d.action, d.node.name, d.named, said), ('node', 'n1', True, []))

    def test_dirty_or_no_taker_is_main(self):
        d, said = self.go(dirty=True)
        self.assertEqual((d.action, d.named), ('main', False))
        self.assertEqual(said, ['running on main: tracked changes are not committed'])
        d, said = self.go(nodes=[node('n1', repos=())], action='gate')
        self.assertEqual((d.action, said), ('main', ['gating on main: no node has opted in my-repo']))

    def test_a_kind_that_cannot_be_sized(self):
        def bad():
            raise ValueError('boom')
        d, said = self.go(need=bad, size_fail='cannot size (%s)')
        self.assertEqual((d.action, said), ('main', ['cannot size (boom)']))
        with self.assertRaises(ValueError):
            self.go(need=bad)
        # cmd_gate's real template
        tmpl = 'the gate\'s kind %s cannot be sized here (%%s); gating on main' % 'k1'
        d, said = self.go(need=bad, action='gate', size_fail=tmpl)
        self.assertEqual((d.action, said),
                         ('main', ['the gate\'s kind k1 cannot be sized here (boom); gating on main']))

    def test_a_node_without_the_verb_is_left_out_and_said(self):
        res = [ok('n1', verbs=list(box.RUN_VERBS))]
        d, said = self.go(results=res, serves=box.GATE_VERB, action='gate',
                          main=lambda: lock(cpu_free=0))
        self.assertEqual(d.action, 'main')
        self.assertEqual(said[0], 'n1: its kit serves no gate verb (update it)')
        self.assertTrue(said[1].startswith('gating on main: '))

    def test_an_unreadable_main_lock_is_main(self):
        def bad():
            raise OSError('x')
        d, said = self.go(main=bad)
        self.assertEqual((d.action, said),
                         ('main', ['the main machine\'s lock cannot be read (OSError); running on main']))
        d, said = self.go(main=bad, action='gate')
        self.assertEqual((d.action, said),
                         ('main', ['the main machine\'s lock cannot be read (OSError); gating on main']))

    def test_the_choice_decides_and_is_said(self):
        d, said = self.go(main=lambda: lock(cpu_free=0), results=[ok('n1', cpu_free=4)])
        self.assertEqual((d.action, d.node.name, d.named), ('node', 'n1', False))
        self.assertTrue(said[0].startswith('running on n1: n1 has 4 cpu slots free'))
        d, said = self.go()
        self.assertEqual(d.action, 'main')
        self.assertTrue(said[0].startswith('running on main: main has 4 cpu slots free'))


class NeedOf(unittest.TestCase):

    def test_the_kind_sized_by_main_layout(self):
        from kitlib.lock import layout as lay
        table = {'engine-test': object()}
        calls = []
        orig = lay.need_for
        lay.need_for = lambda layout, spec: calls.append((layout, spec)) or {'cpu': 3, 'gpu': 0}
        self.addCleanup(setattr, lay, 'need_for', orig)
        self.assertEqual(route.need_of('engine-test', table=table, layout='L'), {'cpu': 3, 'gpu': 0})
        self.assertEqual(calls, [('L', table['engine-test'])])
        with self.assertRaises(KeyError):
            route.need_of('no-such-kind', table=table, layout='L')

    def test_a_retired_name_sizes_as_the_kind_it_names(self):
        # the kit's kinds.json aliases engine-test to test: a --kind engine-test job or a base
        # gate.kind "engine-test" is sized, not declined as an unknown kind
        from kitlib.lock import layout as lay
        table = {'test': object(), 'app-run': object()}
        calls = []
        orig = lay.need_for
        lay.need_for = lambda layout, spec: calls.append(spec) or {'cpu': 2, 'gpu': 0}
        self.addCleanup(setattr, lay, 'need_for', orig)
        self.assertEqual(route.need_of('engine-test', table=table, layout='L'), {'cpu': 2, 'gpu': 0})
        route.need_of('engine-run', table=table, layout='L')
        self.assertEqual(calls, [table['test'], table['app-run']])


if __name__ == '__main__':
    unittest.main()
