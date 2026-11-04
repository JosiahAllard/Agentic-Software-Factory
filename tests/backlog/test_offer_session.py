"""plan.Context.offer_session: a library caller (the hub's fill watch) offers and pulls for one
session while the store commits under another. Unset, `ctx.session` is the store's session, as
before, for every caller."""
import unittest

from kitlib.backlog import plan as pmod


class Store(object):
    def __init__(self, session):
        self.session = session


class OfferSession(unittest.TestCase):

    def test_unset_it_is_the_stores_session_read_each_time(self):
        store = Store('kit')
        ctx = pmod.Context(store, '/root')
        self.assertEqual(ctx.session, 'kit')
        store.session = None                # read live, as the property always was
        self.assertIsNone(ctx.session)
        store.session = 'lane'
        self.assertEqual(ctx.session, 'lane')

    def test_set_it_is_the_offers_session_and_the_store_keeps_its_own(self):
        store = Store('hub-fill')
        ctx = pmod.Context(store, '/root')
        ctx.offer_session = 'kit'
        self.assertEqual((ctx.session, store.session), ('kit', 'hub-fill'))

    def test_set_to_none_it_offers_the_untagged_records(self):
        ctx = pmod.Context(Store('hub-fill'), '/root')
        ctx.offer_session = None            # the main coordinator's records with no `session:`
        self.assertIsNone(ctx.session)


if __name__ == '__main__':
    unittest.main()
