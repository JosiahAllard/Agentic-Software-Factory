"""The owner window's connection list: only a full refresh runs live checks (ui/window.py _fill)."""
import unittest

from optional.bridge.ui.window import Window
from tests.bridge import test_owner,test_pm_routes  # the modules, so their tests are not collected twice


class FakeList:
    """The Listbox calls _fill makes, with no Tk."""
    def __init__(self): self.rows=[]
    def curselection(self): return ()
    def delete(self,first,last=None): self.rows=[]
    def insert(self,index,text): self.rows.append(text)
    def size(self): return len(self.rows)
    def selection_set(self,index): pass


def window(owner):
    window=object.__new__(Window)  # no Tk root: _fill reads only these
    window.owner,window.choices,window.routes,window._rows,window._checked=owner,{},FakeList(),{},{}
    return window


class WindowFillTests(unittest.TestCase):
    setUp=test_owner.WriterLeaseTests.setUp
    tearDown=test_owner.WriterLeaseTests.tearDown

    def window(self): return window(self.owner)

    def test_the_timed_reread_runs_no_live_check_and_keeps_a_full_refreshs_mark(self):
        self.owner.lease(self.a.reference,self.b.reference,enabled=True)
        self.live.pop('b')  # b stays listed, stale
        checks=[]
        class Counted(dict):  # the fixture's live check reads self.live.get at every call
            def get(self,key,default=None):
                checks.append(key)
                return dict.get(self,key,default)
        self.live=Counted(self.live)
        window=self.window()
        window._fill(check=True)
        self.assertTrue(checks)
        self.assertIn('cannot deliver',window.routes.rows[0])
        del checks[:]
        window._fill(check=False)  # the timed re-read: no live call, the mark stays
        self.assertEqual(checks,[])
        self.assertIn('cannot deliver',window.routes.rows[0])
        self.live['b']=self.b  # b passes again: only the next full refresh clears the mark
        window._fill(check=False)
        self.assertIn('cannot deliver',window.routes.rows[0])
        window._fill(check=True)
        self.assertNotIn('cannot deliver',window.routes.rows[0])
        window._fill(check=False)
        self.assertNotIn('cannot deliver',window.routes.rows[0])


class WindowPmFillTests(unittest.TestCase):
    setUp=test_pm_routes.PmRouteTests.setUp
    tearDown=test_pm_routes.PmRouteTests.tearDown
    enroll,frontend,call,connect=(getattr(test_pm_routes.PmRouteTests,name)
        for name in ('enroll','frontend','call','connect'))

    def test_the_timed_reread_keeps_a_pm_rows_mark_from_the_full_refresh(self):
        self.connect(self.a,self.b,bidirectional=False)
        shown=window(self.controller.owner)
        self.live.pop('b')  # b stays listed, not kit-launched now
        shown._fill(check=True)
        self.assertEqual(len(shown.routes.rows),1)
        self.assertIn('cannot deliver',shown.routes.rows[0])
        shown._fill(check=False)
        self.assertIn('cannot deliver',shown.routes.rows[0])


if __name__=='__main__':
    unittest.main()
