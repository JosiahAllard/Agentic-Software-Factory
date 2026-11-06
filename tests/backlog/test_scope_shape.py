"""A scope path holds no control character (kitlib/backlog/scope.py `path_shape_error`): a line
break in `add --scope` (or `retarget`'s OLD/NEW) would split the record's `scope:` line into two
header lines, so it is refused with USAGE and nothing is written. `set` checks no field shape
itself, so kitlib/backlog/record.py `render` refuses a control character other than a tab in any
header key or value (never in the body) for every verb that writes a record. A tab splits no
line, so a record that already holds one in its summary still takes writes."""
import unittest

from kitlib.backlog import offer as omod
from kitlib.backlog import record as rmod
from kitlib.backlog import scope as smod
from kitlib.backlog import store as stmod
from kitlib.backlog.errors import Usage

from tests.backlog.ownertree import setUpModule, tearDownModule  # noqa: F401 - the owner's tree
from tests.backlog import test_cli
from tests.backlog.helpers import ADD_FLAGS, StoreCase, priced, rec

BROKEN = ('a/x\npin: 1', 'a/x\rb', 'a\tb', 'a/\x7fb')


class Shape(unittest.TestCase):

    def test_a_control_character_is_a_shape_error(self):
        for p in BROKEN:
            self.assertIn('control character', smod.path_shape_error(p) or '', repr(p))
            self.assertIn('control character', smod.scope_member_error(p) or '', repr(p))

    def test_a_plain_path_is_still_fine(self):
        self.assertIsNone(smod.path_shape_error('docs/a b.md'))

    def test_the_collision_test_decides_a_member_that_already_holds_a_tab(self):
        self.assertTrue(smod.members_collide('a/b\tc/**', 'a/b\tc/d.py'))
        self.assertTrue(smod.members_collide('a/**', 'a/b\tc'))
        self.assertFalse(smod.members_collide('a/b\tc', 'a/b c'))
        self.assertFalse(smod.members_collide('x/y', 'a/b\tc/**'))
        self.assertIsNone(smod.scope_member_error('a/b\tc', tab_ok=True))
        self.assertIn('control character', smod.scope_member_error('a/b\nc', tab_ok=True))

    def test_render_refuses_a_control_character_in_the_header_only(self):
        for pairs in ([('summary', 'x\npin: 1')], [('summary', 'a\x0cb')], [('pin\n', '1')]):
            with self.assertRaises(Usage, msg=pairs):
                rmod.render(pairs, '')
        text = rmod.render([('id', 'FOO')], 'line one\n\tindented\r\nline two\n')
        self.assertTrue(text.startswith('id: FOO\n\nline one\n\tindented'), text)

    def test_render_passes_a_tab_through_unchanged(self):
        # A tab splits no line; a scope or files path with one is still refused, as a new FAIL
        # finding at check_change (ExistingTab below, ScopeShapeCli `set --scope`).
        self.assertEqual(rmod.render([('summary', 'a\tb')], ''), 'summary: a\tb\n\n')


class ExistingTab(StoreCase):
    """A record another store already holds with a TAB in a free-text header value (a pasted
    summary) still takes every write: render does not refuse a tab, so no unrelated write is
    blocked by bytes it did not add."""

    def test_set_pull_and_a_dependent_close_still_write_and_keep_the_tab(self):
        self.seed(priced('FOO', summary='a thing\twith a tab'),
                  rec('BAR', state='blocked', blocked_on='item:FOO', summary='another\tone'))
        self.verb('set', 'FOO', [('effort', ['s'])])
        self.assertEqual(self.get('FOO').get('effort'), 's')
        self.verb('pull', 'FOO')
        self.assertEqual(self.get('FOO').get('state'), 'pulled')
        self.assertEqual(self.get('FOO').get('summary'), 'a thing\twith a tab')
        self.verb('close', 'FOO', 'the work is finished')     # rewrites BAR, its dependent
        self.assertIsNone(self.get('FOO'))
        bar = self.get('BAR')
        self.assertEqual((bar.get('state'), bar.get('blocked_on')), ('open', '-'))
        self.assertEqual(bar.get('summary'), 'another\tone')

    def test_a_new_tab_in_a_files_path_is_still_refused(self):
        self.seed(rec('FOO'))
        before = self.remote_tip()
        with self.assertRaises(Usage) as cm:
            self.verb('set', 'FOO', [('files', ['a\tb']), ('files_at', ['abc1234'])])
        self.assertIn('control character', str(cm.exception))
        self.assertIn('files', str(cm.exception))
        self.assertEqual(self.remote_tip(), before)

    def test_a_pulled_record_with_a_tab_in_its_scope_blocks_no_offer(self):
        self.seed(priced('HELD', scope='held/a\tb/**', state='pulled', pulled_at='2026-10-01',
                         pulled_by='kit/held'),
                  priced('TABBED', scope='tabbed/x\ty'), priced('OTHER', scope='other/**'),
                  priced('UNDER', scope='held/a\tb/c.py'))
        snap = self.store.load()
        (offers, passed), refusals, _ranks = omod.next_offer(snap, [snap.get('HELD')])
        self.assertEqual(refusals, [])
        self.assertEqual(sorted(o.rec.id for o in offers), ['OTHER', 'TABBED'])
        self.assertEqual([(p.rec.id, 'collides' in p.why) for p in passed], [('UNDER', True)])

    def test_a_new_tab_in_a_path_is_still_refused(self):
        self.seed(rec('FOO'))
        before = self.remote_tip()
        with self.assertRaises(Usage) as cm:
            self.verb('set', 'FOO', [('scope', ['a\tb'])])
        self.assertIn('control character', str(cm.exception))
        self.assertEqual(self.remote_tip(), before)


class ScopeShapeCli(unittest.TestCase):
    setUp = test_cli.Cli.setUp
    run_tool = test_cli.Cli.run_tool
    ok = test_cli.Cli.ok

    def tip(self):
        return self.repo.git('ls-remote', 'origin', stmod.STORE_REF)

    def test_add_refuses_a_line_break_in_a_scope(self):
        before = self.tip()
        for p in BROKEN:
            rc, out = self.run_tool('add', 'FOO', '--summary', 'a thing to do', '--scope', p,
                                    *ADD_FLAGS)
            self.assertEqual(rc, 2, (p, out))
            self.assertRegex(out, r'^USAGE: ')
        self.assertEqual(self.tip(), before)

    def test_set_refuses_a_line_break_in_a_header_value(self):
        # record.render's guard: `set` does not run the field validators itself.
        self.ok('add', 'FOO', '--summary', 'a thing to do', '--scope', 'a/x', *ADD_FLAGS)
        before = self.tip()
        for flag, value in (('--scope', 'a/x\npin: 1'), ('--summary', 'a thing\npin: 1'),
                            ('--summary', 'a thing\rto do'), ('--scope', 'a\tb')):
            rc, out = self.run_tool('set', 'FOO', flag, value)
            self.assertEqual(rc, 2, (flag, value, out))
            self.assertRegex(out, r'^USAGE: ')
        self.assertEqual(self.tip(), before)
        self.assertNotIn('pin:', self.ok('show', 'FOO'))

    def test_a_plain_set_still_writes(self):
        self.ok('add', 'FOO', '--summary', 'a thing to do', '--scope', 'a/x', *ADD_FLAGS)
        before = self.tip()
        self.ok('set', 'FOO', '--summary', 'a thing to do, reworded', '--scope', 'a/y')
        self.assertNotEqual(self.tip(), before)
        shown = self.ok('show', 'FOO')
        self.assertIn('summary: a thing to do, reworded', shown)
        self.assertIn('scope: a/y', shown)

    def test_add_warns_on_a_bare_folder_and_still_writes(self):
        before = self.tip()
        out = self.ok('add', 'FOO', '--summary', 'a thing to do', '--scope', 'kitlib/backlog',
                      *ADD_FLAGS)
        self.assertRegex(out, r'WARNING: scope `kitlib/backlog` collides with everything below')
        self.assertIn('narrow it to the files', out)
        self.assertNotEqual(self.tip(), before)
        self.assertIn('scope: kitlib/backlog', self.ok('show', 'FOO'))

    def test_add_warns_on_a_glob(self):
        out = self.ok('add', 'FOO', '--summary', 'a thing to do', '--scope', 'kitlib/backlog/**',
                      *ADD_FLAGS)
        self.assertIn('WARNING: scope `kitlib/backlog/**` collides', out)
        self.assertEqual(out.count('WARNING'), 1)

    def test_add_of_a_single_file_scope_prints_no_warning(self):
        out = self.ok('add', 'FOO', '--summary', 'a thing to do', '--scope', 'a/x.py', *ADD_FLAGS)
        self.assertNotIn('WARNING', out)

    def test_set_scope_warns_on_a_bare_folder_and_still_writes(self):
        self.ok('add', 'FOO', '--summary', 'a thing to do', '--scope', 'a/x.py', *ADD_FLAGS)
        out = self.ok('set', 'FOO', '--scope', 'a/dir')
        self.assertIn('WARNING: scope `a/dir` collides with everything below', out)
        self.assertIn('scope: a/dir', self.ok('show', 'FOO'))
        self.assertNotIn('WARNING', self.ok('set', 'FOO', '--scope', 'a/y.py'))

    def test_the_shape_test_alone(self):
        self.assertEqual(smod.broad_scope_warning(['a/b.py', 'none']), [])
        self.assertTrue(all(smod.is_folder_scope(m) for m in ('a', 'a/b', 'a/**', 'a/b*/**')))


if __name__ == '__main__':
    unittest.main()
