"""`--until "resource:<name> free"` (docs/backlog.md "block --until"): the form, how it is
evaluated against a reader, and the refusal of an undeclared name at block time."""
import unittest

from kitlib.backlog import conditions as condmod
from kitlib.backlog import plan as pmod
from kitlib.backlog import store as smod
from kitlib.backlog.errors import Usage

from tests.backlog.ownertree import setUpModule, tearDownModule  # noqa: F401 - the owner's tree
from tests.backlog.helpers import BRANCH, TODAY, StoreCase, rec

FORM = 'resource:dev-clone free'


class Reader(object):
    def __init__(self, free):
        self.free = free

    def resource_free(self, name):
        return self.free


class Form(unittest.TestCase):

    def test_the_form_parses(self):
        self.assertEqual(condmod.parse(FORM), condmod.Condition('resource', 'dev-clone', 'free'))
        self.assertEqual(condmod.kind_of(FORM), 'resource')

    def test_other_text_is_not_the_form(self):
        for text in ('resource:Dev_Clone free', 'resource:dev-clone busy', 'resource: free',
                     'resource:dev-clone', 'resource:dev-clone free soon'):
            self.assertIsNone(condmod.parse(text), text)

    def test_met_unmet_and_unreadable(self):
        cond = condmod.parse(FORM)
        self.assertIs(condmod.evaluate(cond, Reader(True)), True)
        self.assertIs(condmod.evaluate(cond, Reader(False)), False)
        self.assertIsNone(condmod.evaluate(cond, Reader(None)))
        self.assertIsNone(condmod.evaluate(cond, object()), 'a reader without it cannot tell')

    def test_owner_cannot_end_on_a_resource(self):
        self.assertIn('owner', condmod.owner_error('owner', FORM))
        self.assertIsNone(condmod.owner_error('live-client', FORM))

    def test_resource_error(self):
        self.assertIsNone(condmod.resource_error(FORM, {'dev-clone': 1}))
        self.assertIn('does not declare', condmod.resource_error(FORM, {'gpu': 1}))
        self.assertIsNone(condmod.resource_error('todo:T-1 done', {}))
        self.assertIsNone(condmod.resource_error('resource:dev-clone busy', {}))


class BlockTime(StoreCase):
    remote = False

    def setUp(self):
        super().setUp()
        self.store = smod.Store(self.backend, self.rules, session='kit')
        self.ctx = pmod.Context(self.store, self.repo.root, branch=BRANCH, today=TODAY,
                                resources={'dev-clone': 1})

    def test_a_declared_name_is_written(self):
        self.seed(rec('FOO'))
        self.verb('block', 'FOO', ['live-client'], until=FORM)
        self.assertEqual(self.get('FOO').all('until'), ['live-client ' + FORM])

    def test_an_unknown_name_is_usage_and_writes_nothing(self):
        self.seed(rec('FOO'))
        with self.assertRaises(Usage) as cm:
            self.verb('block', 'FOO', ['live-client'], until='resource:nope free')
        self.assertIn('does not declare', str(cm.exception))
        self.assertEqual(self.get('FOO').get('state'), 'open')

    def test_a_resource_text_that_does_not_parse_is_usage(self):
        self.seed(rec('FOO'))
        for text in ('resource:Dev-Clone free', 'resource:dev-clone busy'):
            with self.assertRaises(Usage, msg=text):
                self.verb('block', 'FOO', ['live-client'], until=text)
        self.assertEqual(self.get('FOO').get('state'), 'open')

    def test_owner_is_refused(self):
        self.seed(rec('FOO'))
        with self.assertRaises(Usage):
            self.verb('block', 'FOO', ['owner'], owner_ask='which build should ship?', until=FORM)


if __name__ == '__main__':
    unittest.main()
