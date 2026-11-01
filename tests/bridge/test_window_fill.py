"""The owner window's connection list: only a full refresh runs live checks (ui/window.py _fill)."""
import unittest

from optional.bridge.ui.window import Window
from tests.bridge import test_owner  # the module, so its tests are not collected twice


class FakeList:
    """The Listbox calls _fill makes, with no Tk."""
    def __init__(self): self.rows=[]
    def curselection(self): return ()
    def delete(self,first,last=None): self.rows=[]
    def insert(self,index,text): self.rows.append(text)
    def size(self): return len(self.rows)
    def selection_set(self,index): pass


class WindowFillTests(unittest.TestCase):
    setUp=test_owner.WriterLeaseTests.setUp
    tearDown=test_owner.WriterLeaseTests.tearDown

    def window(self):
        window=object.__new__(Window)  # no Tk root: _fill reads only these
        window.owner,window.choices,window.routes,window._rows=self.owner,{},FakeList(),{}
        return window

    def test_the_timed_reread_runs_no_live_check_and_a_full_refresh_marks_a_stale_lease(self):
        self.owner.lease(self.a.reference,self.b.reference,enabled=True)
        del self.live['b']  # b stays listed, stale
        checks=[]
        class Counted(dict):  # the fixture's live check reads self.live.get at every call
            def get(self,key,default=None):
                checks.append(key)
                return dict.get(self,key,default)
        self.live=Counted(self.live)
        window=self.window()
        window._fill(check=False)
        self.assertEqual(checks,[])
        self.assertNotIn('cannot deliver',window.routes.rows[0])
        window._fill(check=True)
        self.assertTrue(checks)
        self.assertIn('cannot deliver',window.routes.rows[0])


if __name__=='__main__':
    unittest.main()
