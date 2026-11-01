"""The owner window names chats, says who made each connection and lease, and says how to bring a
chat back (ui/window.py, core/owner.py). It displays; only OwnerControls acts."""
from pathlib import Path
import unittest
from unittest.mock import patch

from optional.bridge.core.owner import PARTLY_DONE,OwnerControls
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
        # Core knows no host's id format: the host may give a short label, else the first 8 characters.
        desktop=chat('local_8a23be98-0000-4000-8000-000000000000')
        self.live[desktop.identity.session_id]=desktop
        self.pairs.observe(desktop)
        titles={choice.reference:choice.title for choice in self.owner.chats()}
        self.assertEqual(titles[desktop.reference],'unnamed chat local_8a')
        self.assertEqual(titles[self.a.reference],'unnamed chat a')
        named=OwnerControls(self.pairs,self.decisions,describe=lambda peer:{'title':'kit-coordinator'}
            if peer==self.a else {'short':'8a23be98'} if peer==desktop else {})
        titles={choice.reference:choice.title for choice in named.chats()}
        self.assertEqual(titles[self.a.reference],'kit-coordinator')
        self.assertEqual(titles[desktop.reference],'unnamed chat 8a23be98')

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

    def test_a_connection_carried_by_a_lease_cannot_deliver_while_the_writer_does_not_answer(self):
        # The delivery checks the writer too (hosts/codex/runtime.py), so the row must not say ready.
        owner,choices=self.named()
        owner.change(self.a.reference,self.c.reference,enabled=True,bidirectional=False)
        owner.lease(self.b.reference,self.c.reference,enabled=True)
        self.live.pop('b')  # the writer closed or its turn ended; a and c still answer
        self.assertEqual(owner.owner_rows(),((self.a.reference,self.c.reference,False),))
        window=shown(owner,choices)
        window._fill(check=True)
        self.assertIn("delivered through codex writer's lease (a chat is not answering",window.routes.rows[0])
        self.live['b']=self.b
        self.assertEqual(owner.owner_rows(),((self.a.reference,self.c.reference,True),))
        owner.change(self.b.reference,self.c.reference,enabled=True,bidirectional=False)
        self.live.pop('a')  # the writer's own row is its own delivery and does not depend on a
        rows={(sender,recipient):ok for sender,recipient,ok in owner.owner_rows()}
        self.assertEqual(rows,{(self.a.reference,self.c.reference):False,(self.b.reference,self.c.reference):True})

    def test_a_refresh_checks_each_chat_once(self):
        owner,_choices=self.named()
        owner.change(self.a.reference,self.c.reference,enabled=True,bidirectional=True)
        owner.lease(self.b.reference,self.c.reference,enabled=True)
        checks=[]
        class Counted(dict):  # the fixture's live check reads self.live.get at every call
            def get(self,key,default=None):
                checks.append(key)
                return dict.get(self,key,default)
        self.live=Counted(self.live)
        live={}
        choices=owner.chats(check=True,live=live)  # what Window.refresh does
        window=shown(owner,choices)
        window._fill(check=True,live=live)
        self.assertEqual(sorted(checks),['a','b','c'])
        self.assertEqual(len(window.routes.rows),3)

    def test_a_chat_observed_while_the_rows_are_read_gives_no_error(self):
        owner,_choices=self.named()
        owner.change(self.a.reference,self.c.reference,enabled=True,bidirectional=False)
        full=self.pairs.registered()
        before=tuple(peer for peer in full if peer!=self.c)  # c is observed just after the first read
        with patch.object(self.pairs,'registered',side_effect=[before]+[full]*5):
            self.assertEqual(owner.owner_rows(),())

    def test_a_lease_whose_chat_left_the_list_keeps_its_name_and_says_how_to_bring_it_back(self):
        owner,choices=self.named()
        owner.lease(self.b.reference,self.c.reference,enabled=True)
        window=shown(owner,choices)
        self.pairs.withdraw('c')  # c is no longer listed
        window.choices.pop(self.c.reference)
        window._fill(check=True)
        self.assertIn('reviewer (not listed: it needs a bridge call, then Refresh)',window.routes.rows[0])
        unknown='f'*64  # a chat this window never listed is named like any unnamed chat, not by a bare id
        self.assertEqual(window._title(unknown),'unnamed chat ffffffff (not listed: it needs a bridge call, then Refresh)')

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

    def test_only_a_refusal_before_any_write_says_nothing_was_changed(self):
        self.assertIn('Nothing was changed',ui.refusal(PairingError('route-capacity')))
        for error in (RuntimeError('boom'),PairingError('some-later-failure'),PairingError(PARTLY_DONE)):
            line=ui.refusal(error)
            self.assertNotIn('Nothing was changed',line)
            self.assertNotIn('chats changed',line)
            self.assertIn('Click Refresh to see what is connected now',line)
        # A Stop whose PM-route cleanup fails after the owner's row was written: partly done.
        self.owner.change(self.a.reference,self.b.reference,enabled=True,bidirectional=False)
        class FailingPm:
            def stop(self,*args,**kwargs): raise RuntimeError('pm log unavailable')
        self.owner.pm=FailingPm()
        with self.assertRaises(PairingError) as raised:
            self.owner.change(self.a.reference,self.b.reference,enabled=False,bidirectional=False)
        self.assertEqual(raised.exception.code,PARTLY_DONE)
        with self.assertRaises(PairingError): self.pairs.require_selected(self.a,self.b)  # it is stopped
        self.assertIn('The connection is stopped',ui.refusal(raised.exception))


class RealWindowTests(unittest.TestCase):
    """The real Tk window, withdrawn: names, buttons and help lines."""
    setUp=test_owner.WriterLeaseTests.setUp
    tearDown=test_owner.WriterLeaseTests.tearDown

    def test_a_chat_that_fails_its_check_shows_not_answering_in_the_real_window(self):
        import tkinter as tk
        try: root=tk.Tk()
        except tk.TclError as error: self.skipTest('no Tk: '+str(error))
        try:
            root.withdraw()
            self.live.pop('b')
            window=ui.Window(root,self.owner)
            states={item:window.table.item(item,'values')[1] for item in window.table.get_children()}
            self.assertEqual(states,{self.a.reference:'ready',self.b.reference:'not answering',self.c.reference:'ready'})
            self.assertIn('not answering',window.hint.get())
        finally: root.destroy()

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
