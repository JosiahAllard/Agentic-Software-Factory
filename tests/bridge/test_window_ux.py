"""The owner window names chats, says who made each connection and lease, and says how to bring a
chat back (ui/window.py, core/owner.py). It displays; only OwnerControls acts."""
from pathlib import Path
import unittest

from optional.bridge.core.owner import OwnerControls
from optional.bridge.core.pairing import PairingError,PairingStore
from optional.bridge.ui import window as ui
from tests.bridge import test_owner  # the module, so its tests are not collected twice
from tests.bridge.test_pairing import chat
from tests.bridge.test_window_fill import FakeList


class Selected(FakeList):
    """FakeList with one selected row."""
    def __init__(self,index=None):
        super().__init__()
        self.index=index
    def curselection(self): return () if self.index is None else (self.index,)


class FakeVar:
    def __init__(self): self.value=None
    def set(self,value): self.value=value


def shown(owner,choices=()):
    window=object.__new__(ui.Window)  # no Tk root
    window.owner,window.routes,window._rows,window._checked=owner,Selected(),{},{}
    window.choices={choice.reference:choice for choice in choices}
    window._names={choice.reference:choice.title for choice in choices}
    window.preview,window.status=False,FakeVar()
    window.refresh=lambda:None
    return window


class ChatNameTests(unittest.TestCase):
    setUp=test_owner.WriterLeaseTests.setUp
    tearDown=test_owner.WriterLeaseTests.tearDown

    def test_a_chat_without_a_host_name_is_listed_by_a_short_id_not_its_whole_id(self):
        desktop=chat('local_8a23be98-0000-4000-8000-000000000000')
        self.live[desktop.identity.session_id]=desktop
        self.pairs.observe(desktop)
        titles={choice.reference:choice.title for choice in self.owner.chats()}
        self.assertEqual(titles[desktop.reference],'unnamed chat 8a23be98')
        self.assertEqual(titles[self.a.reference],'unnamed chat a')
        named=OwnerControls(self.pairs,self.decisions,describe=lambda peer:{'title':'kit-coordinator'}
            if peer==self.a else {})
        titles={choice.reference:choice.title for choice in named.chats()}
        self.assertEqual(titles[self.a.reference],'kit-coordinator')

    def test_a_full_refresh_marks_a_chat_that_does_not_answer_and_the_hint_says_how_to_bring_it_back(self):
        self.assertEqual({choice.live for choice in self.owner.chats()},{None})  # no check, no live call
        self.live.pop('b')  # b stays listed, stale (between turns or closed)
        choices=self.owner.chats(check=True)
        self.assertEqual({choice.reference:choice.live for choice in choices},
            {self.a.reference:True,self.b.reference:False,self.c.reference:True})
        hint=ui.hints(choices)
        self.assertIn('not answering',hint)
        self.assertIn('make a bridge call',hint)
        self.assertIn('Refresh',hint)
        self.assertNotIn('not answering',ui.hints(self.owner.chats()))
        # A chat that is not listed at all: the line says chats appear after a bridge call.
        self.assertIn('since the bridge started',ui.hints(()))
        self.assertIn('bridge call',ui.hints(()))


class ConnectionListTests(unittest.TestCase):
    setUp=test_owner.WriterLeaseTests.setUp
    tearDown=test_owner.WriterLeaseTests.tearDown

    def named(self):
        owner=OwnerControls(self.pairs,self.decisions,leases=self.leases,
            describe=lambda peer:{'title':{'a':'coordinator','b':'codex writer','c':'reviewer'}[peer.identity.session_id]})
        return owner,owner.chats()

    def test_each_connection_says_who_made_it_and_the_lease_it_is_delivered_through(self):
        owner,choices=self.named()
        owner.change(self.a.reference,self.c.reference,enabled=True,bidirectional=False)
        owner.lease(self.b.reference,self.c.reference,enabled=True)
        window=shown(owner,choices)
        window._fill(check=True)
        rows=window.routes.rows
        self.assertEqual(len(rows),2)
        self.assertIn('coordinator → reviewer, connected by you',rows[0])
        self.assertIn("delivered through codex writer's lease",rows[0])
        self.assertIn('Lease: codex writer delivers to reviewer',rows[1])
        self.assertIn('granted by you',rows[1])
        self.assertEqual(window._rows,{0:('owner',self.a.reference,self.c.reference),
            1:('lease',self.b.reference,self.c.reference)})

    def test_a_connection_whose_chat_does_not_answer_says_it_cannot_deliver_until_the_next_refresh(self):
        owner,choices=self.named()
        owner.change(self.a.reference,self.c.reference,enabled=True,bidirectional=False)
        window=shown(owner,choices)
        self.live.pop('c')
        window._fill(check=True)
        self.assertIn('not answering',window.routes.rows[0])
        window._fill(check=False)  # the timed re-read keeps the mark
        self.assertIn('not answering',window.routes.rows[0])
        self.live['c']=self.c
        window._fill(check=True)
        self.assertNotIn('not answering',window.routes.rows[0])

    def test_a_lease_whose_chat_left_the_list_keeps_its_name_and_says_how_to_bring_it_back(self):
        owner,choices=self.named()
        owner.lease(self.b.reference,self.c.reference,enabled=True)
        window=shown(owner,choices)
        self.pairs.withdraw('c')  # c is no longer listed
        window.choices.pop(self.c.reference)
        window._fill(check=True)
        self.assertIn('reviewer (not listed: it needs a bridge call, then Refresh)',window.routes.rows[0])

    def test_stop_selected_stops_the_owners_connection_in_that_direction_only(self):
        owner,choices=self.named()
        owner.change(self.a.reference,self.c.reference,enabled=True,bidirectional=True)
        window=shown(owner,choices)
        window._fill(check=True)
        index=next(i for i,row in window._rows.items() if row==('owner',self.a.reference,self.c.reference))
        window.routes.index=index
        window._stop_selected()
        self.assertEqual(window.status.value,'That connection is stopped. The chats remain open.')
        with self.assertRaises(PairingError): self.pairs.require_selected(self.a,self.c)
        self.pairs.require_selected(self.c,self.a)  # the other direction is its own row


class RestartTests(unittest.TestCase):
    setUp=test_owner.OwnerTests.setUp
    tearDown=test_owner.OwnerTests.tearDown

    def test_after_a_restart_remembered_connections_are_counted_and_the_line_says_what_to_redo(self):
        self.owner.change(self.a.reference,self.b.reference,enabled=True,bidirectional=True)
        self.assertEqual(self.owner.waiting(),0)
        self.pairs.close()
        # The bridge restarted: the store is reopened and no chat has made a bridge call yet.
        self.pairs=PairingStore(Path(self.temp.name)/'pairs.sqlite',owner_check=self.decisions.authorize,
            live_check=lambda peer:self.live.get(peer.identity.session_id)==peer)
        owner=OwnerControls(self.pairs,self.decisions)
        self.assertEqual(owner.connections(),())
        self.assertEqual(owner.waiting(),2)
        line=ui.waiting_line(owner.waiting())
        self.assertIn('Remembered connections waiting for their chats: 2',line)
        self.assertIn('make a bridge call',line)
        self.assertIn('Leases end when the bridge restarts',line)
        self.pairs.observe(self.a)
        self.assertEqual(owner.waiting(),2)
        self.pairs.observe(self.b)  # both chats are back: the connection is listed by itself
        self.assertEqual(owner.waiting(),0)
        self.assertEqual(len(owner.connections()),2)
        self.assertEqual(ui.waiting_line(0),'')

    def test_a_connect_on_chats_gone_since_the_list_was_read_says_why_and_how_to_bring_them_back(self):
        gone=self.a.reference
        self.pairs.withdraw('a')
        with self.assertRaises(PairingError) as raised:
            self.owner.change(gone,self.b.reference,enabled=True)
        line=ui.refusal(raised.exception)
        self.assertIn('no longer listed',line)
        self.assertIn('make a bridge call',line)
        self.assertIn('Refresh',line)
        self.pairs.observe(self.a)
        self.live.pop('a')  # listed, but between turns
        with self.assertRaises(PairingError) as raised:
            self.owner.change(self.a.reference,self.b.reference,enabled=True)
        line=ui.refusal(raised.exception)
        self.assertIn('between turns or closed',line)
        self.assertIn('make a bridge call',line)
        self.assertIn('(reason: owner-decision-capacity)',ui.refusal(PairingError('owner-decision-capacity')))
        self.assertIn('does not connect these two kinds of chat',ui.refusal(PairingError('host-pair-refused')))


class RealWindowTests(unittest.TestCase):
    """The real Tk window, withdrawn: names, buttons and help lines."""
    setUp=test_owner.WriterLeaseTests.setUp
    tearDown=test_owner.WriterLeaseTests.tearDown

    def test_the_window_shows_names_connect_and_stop_buttons_and_the_help_line(self):
        import tkinter as tk
        try: root=tk.Tk()
        except tk.TclError as error: self.skipTest('no Tk: '+str(error))
        try:
            root.withdraw()
            owner=OwnerControls(self.pairs,self.decisions,leases=self.leases,
                describe=lambda peer:{'title':'chat named '+peer.identity.session_id})
            window=ui.Window(root,owner)
            self.assertEqual([window.connect.cget('text'),window.disconnect.cget('text')],['Connect','Stop'])
            self.assertEqual(window.stop_row.cget('text'),'Stop selected connection')
            self.assertEqual(window.unlend.cget('text'),'Stop lease')
            titles=sorted(window.table.item(item,'values')[0] for item in window.table.get_children())
            self.assertEqual(titles,['chat named a','chat named b','chat named c'])
            self.assertEqual({window.table.item(item,'values')[1] for item in window.table.get_children()},{'ready'})
            self.assertIn('bridge call',window.hint.get())
            self.assertEqual(window.waiting.get(),'')
        finally: root.destroy()


if __name__=='__main__':
    unittest.main()
