"""Choose exact already-open chats and their communication directions.

Controller discovery and native liveness checks are supplied by service assembly.
This UI changes only bridge connections, never native permissions or chat state.
It owns no agent-facing server and exposes no owner write through MCP. It displays
and its buttons act through OwnerControls; every check on who may connect, hold a
lease or deliver stays there and in the store.
"""
import time
import tkinter as tk
from tkinter import ttk

from optional.bridge.core.hostpairs import REFUSED as HOST_PAIR_REFUSED
from optional.bridge.core.owner import PARTLY_DONE,OwnerControls
from optional.bridge.core.pairing import PairingError

REREAD_MS=5000
# How a chat that is not listed, or not answering, comes back (README "Connections window").
COME_BACK='have it make a bridge call (any bridge tool), then click Refresh'
NOT_LISTED_HELP=('Not listed? Most chats are listed only after they make a bridge call since the bridge '
    'started (an app that reports its open chats has them listed on Refresh). A chat that is '
    'running but not calling the bridge is not seen. To bring a chat here, '+COME_BACK+'.')
NOT_ANSWERING=('A chat marked "not answering" is between turns or closed: it stays listed but cannot '
    'deliver now. To bring it back, '+COME_BACK+'.')
# PairingError codes for a selected chat that is gone or replaced, and for one that failed its live check.
GONE=('visible-chat-changed','chat-not-observed','selection-chat-changed')
STALE=('chat-live-binding-unverified',)
# Other codes a Connect, Stop or lease click raises before anything is written (a store write that
# fails is rolled back, and a lease whose log line fails is undone).
BEFORE_WRITE=('invalid-selection-options','invalid-owner-selection','same-chat-selection',
    'owner-control-unverified','owner-decision-capacity','route-capacity','pairing-store-closed',
    'writer-lease-unavailable','writer-lease-capacity','writer-lease-log-failed','pm-routes-unavailable')
UNKNOWN=' Click Refresh to see what is connected now.'


def refusal(error):
    """The status line for a Connect, Stop or lease click that failed."""
    code=getattr(error,'code',None)
    if code==HOST_PAIR_REFUSED: return 'The bridge does not connect these two kinds of chat. Nothing was changed.'
    if code in GONE:
        return ('A selected chat is no longer listed as you chose it: it closed, restarted, changed its '
            'permissions, or the bridge restarted. Nothing was changed. To bring it back, '+COME_BACK
            +', then select the chats again.')
    if code in STALE:
        return ('A selected chat did not answer its live check: it is between turns or closed. Nothing was '
            'changed. To bring it back, '+COME_BACK+', then try again.')
    if type(error) is not PairingError:
        return 'Something failed ('+type(error).__name__+'); what was changed is not known.'+UNKNOWN
    reason=' (reason: '+code+')' if type(code) is str and code.replace('-','').isalnum() and len(code)<=64 else ''
    if code==PARTLY_DONE:
        return 'The connection is stopped, but a PM connection on it may not be.'+UNKNOWN
    if code in BEFORE_WRITE:
        return ('That was refused'+reason+'. Nothing was changed. Click Refresh, select the chats again and '
            'retry; a chat that is not listed must first make a bridge call.')
    return 'That failed'+reason+'; what was changed is not known.'+UNKNOWN


class Window:
    def __init__(self,root,owner, *, refresh=None,preview=False):
        if type(owner) is not OwnerControls: raise ValueError('invalid owner controller')
        self.root,self.owner,self.refresh_callback,self.preview=root,owner,refresh,preview
        self.choices={}
        self._names={}  # reference -> title, every chat this window has listed, for rows of chats gone since
        root.title('Agent bridge — chat connections'+(' — preview' if preview else ''))
        root.geometry('1020x720')
        root.minsize(780,560)
        style=ttk.Style(root)
        style.configure('Act.TButton',font=('Segoe UI',11,'bold'),padding=(14,6))
        frame=ttk.Frame(root,padding=20)
        frame.pack(fill='both',expand=True)
        ttk.Label(frame,text='Choose which chats can talk to each other',font=('Segoe UI',17,'bold')).pack(anchor='w')
        ttk.Label(frame,text='Select two chats, then click Connect or Stop. Each chat keeps its current permissions.'
            ).pack(anchor='w',pady=(8,12))
        self.table=ttk.Treeview(frame,columns=('chat','state','runtime','workspace'),show='headings',
            selectmode='extended',height=8)
        for key,label,width in (('chat','Chat name',260),('state','State',130),('runtime','App or terminal',140),
                ('workspace','Working folder',400)):
            self.table.heading(key,text=label)
            self.table.column(key,width=width,minwidth=90)
        self.table.pack(fill='both',expand=True)
        self.table.bind('<<TreeviewSelect>>',self._selection)
        self.hint=tk.StringVar(value=NOT_LISTED_HELP)
        ttk.Label(frame,textvariable=self.hint,wraplength=960,foreground='#444').pack(anchor='w',pady=(6,0))
        controls=ttk.Frame(frame)
        controls.pack(fill='x',pady=12)
        self.direction=tk.StringVar(value='Both directions')
        ttk.Combobox(controls,textvariable=self.direction,state='readonly',width=28,
            values=('Both directions','First selected chat → second')).pack(side='left')
        self.connect=ttk.Button(controls,text='Connect',style='Act.TButton',command=lambda:self._change(True))
        self.connect.pack(side='left',padx=8)
        self.disconnect=ttk.Button(controls,text='Stop',style='Act.TButton',command=lambda:self._change(False))
        self.disconnect.pack(side='left')
        ttk.Button(controls,text='Refresh',command=self.refresh).pack(side='right')
        writers=ttk.Frame(frame)
        writers.pack(fill='x',pady=(0,12))
        # A writer lease: the first chat (an app chat with an active turn) carries
        # delivery to the second for a chat of another app. It lasts until Stop, a PM revoke, a
        # service restart, or either chat coming back as a different chat binding (a permission change).
        self.lend=ttk.Button(writers,text='Grant lease: first chat delivers to second for other apps',
            command=lambda:self._lease(True))
        self.lend.pack(side='left')
        self.unlend=ttk.Button(writers,text='Stop lease',command=lambda:self._lease(False))
        self.unlend.pack(side='left',padx=8)
        self.selected=tk.StringVar(value='Select two chats to choose a connection.')
        ttk.Label(frame,textvariable=self.selected).pack(anchor='w',pady=(0,12))
        ttk.Label(frame,text='Current connections and leases',font=('Segoe UI',11,'bold')).pack(anchor='w')
        self.routes=tk.Listbox(frame,height=6,activestyle='none',font=('Segoe UI',10),borderwidth=1)
        self.routes.pack(fill='x',pady=6)
        rows=ttk.Frame(frame)
        rows.pack(fill='x')
        # Stops the connection, lease or PM route selected in the list by its references; a lease or
        # PM route stops when a chat is no longer listed (closed, between turns or not yet rediscovered).
        self.stop_row=ttk.Button(rows,text='Stop selected connection',style='Act.TButton',
            command=self._stop_selected,state='disabled' if preview else 'normal')
        self.stop_row.pack(side='left')
        self.waiting=tk.StringVar(value='')
        ttk.Label(frame,textvariable=self.waiting,wraplength=960).pack(anchor='w',pady=(8,0))
        self.status=tk.StringVar(value='Preview only. Connections are not changed.' if preview else 'No chat is connected just because it is listed.')
        ttk.Label(frame,textvariable=self.status,wraplength=960).pack(anchor='w',pady=(8,0))
        self._order=[]
        self._rows={}  # list index -> ('owner'|'lease'|'pm', first reference, second reference)
        self._checked={}  # row -> deliverable, from the last full refresh's live checks
        self.refresh()
        # The PM makes routes while this window is open: re-read the list, without discovery.
        root.after(REREAD_MS,self._reread)

    def _selection(self,event=None):
        chosen=list(self.table.selection())
        self._order=[item for item in self._order if item in chosen]+[item for item in chosen if item not in self._order]
        if len(self._order)==2:
            titles=[self.choices[ref].title for ref in self._order]
            self.selected.set('First: '+titles[0]+'    Second: '+titles[1])
        else: self.selected.set('Select exactly two chats. The first selection is the sender for a one-way connection.')
        state='normal' if len(self._order)==2 and not self.preview else 'disabled'
        for button in (self.connect,self.disconnect,self.lend,self.unlend): button.configure(state=state)

    def refresh(self):
        try:
            if self.refresh_callback is not None: self.refresh_callback()
            live={}  # each chat's live check runs once in this refresh
            choices=self.owner.chats(check=True,live=live)
            self.choices={choice.reference:choice for choice in choices}
            self._names.update((choice.reference,choice.title) for choice in choices)
            old=list(self._order)
            self.table.delete(*self.table.get_children())
            for choice in choices:
                self.table.insert('',tk.END,iid=choice.reference,values=(choice.title,
                    'ready' if choice.live is not False else 'not answering',choice.runtime,choice.workspace))
            self.table.selection_set([ref for ref in old if ref in self.choices])
            self.hint.set(hints(choices))
            self._fill(check=True,live=live)
            self.waiting.set(waiting_line(self.owner.waiting()))
            if not self.preview and not choices: self.status.set('No chats are listed yet. '+NOT_LISTED_HELP)
            self._selection()
        except Exception:
            self.status.set('Chat discovery is unavailable. Existing connections are not changed by this refresh.')
            for button in (self.connect,self.disconnect,self.lend,self.unlend): button.configure(state='disabled')

    def _fill(self, *, check,live=None):
        """Redraw the connection list, keeping the selected row. check: run the routes' and leases'
        live checks (a full refresh). The timed re-read runs none: a row keeps the
        result of the last full refresh, and a chat that is not listed now still marks it.
        A failed live check keeps a chat listed, so listing alone cannot clear a mark. live: the
        refresh's shared live-check results (OwnerControls._live)."""
        if check and live is None: live={}
        selected=[self._rows.get(index) for index in self.routes.curselection()]
        self.routes.delete(0,tk.END)
        self._rows={}  # reset before any row, so a failed refresh leaves no stale index map
        previous,checked=self._checked,{}  # only rows shown now are kept
        def can_deliver(row,deliverable):
            checked[row]=deliverable if check else previous.get(row,True)
            return deliverable and checked[row]
        leases=tuple(self.owner.writer_grants(check=check,live=live))
        through={target:writer for writer,target,_granted,_deliverable in leases}
        def via(source,target):
            writer=through.get(target)
            return '' if writer is None or writer==source else '; delivered through '+self._title(writer)+"'s lease"
        for source,target,deliverable in self.owner.owner_rows(check=check,live=live):
            row=('owner',source,target)
            self._rows[self.routes.size()]=row
            self.routes.insert(tk.END,self._title(source)+' → '+self._title(target)+', connected by you'
                +via(source,target)+('' if can_deliver(row,deliverable) else
                ' (a chat is not answering: between turns or closed; it cannot deliver now)'))
        for writer,target,granted,deliverable in leases:
            row=('lease',writer,target)
            self._rows[self.routes.size()]=row
            self.routes.insert(tk.END,'Lease: '+self._title(writer)+' delivers to '+self._title(target)
                +' for other apps, granted by you since '+_when(granted)
                +('' if can_deliver(row,deliverable) else ' (a chat is closed, between turns or not listed; it cannot deliver)'))
        for source,target,made,deliverable in self.owner.pm_routes(check=check):
            row=('pm',source,target)
            self._rows[self.routes.size()]=row
            self.routes.insert(tk.END,self._title(source)+' → '+self._title(target)+', connected by the PM since '
                +_when(made)+via(source,target)+('' if can_deliver(row,deliverable) else ' (cannot deliver now)'))
        self._checked=checked
        for index,row in tuple(self._rows.items()):
            if row in selected: self.routes.selection_set(index)

    def _reread(self):
        try: self._fill(check=False)
        except Exception: pass
        try: self.root.after(REREAD_MS,self._reread)
        except Exception: pass  # the window is gone

    def _title(self,reference):
        choice=self.choices.get(reference)
        if choice is not None: return choice.title
        name=getattr(self,'_names',{}).get(reference)
        return (name if name is not None else 'unnamed chat '+reference[:8])+' (not listed: it needs a bridge call, then Refresh)'

    def _stop_selected(self):
        if self.preview: return
        rows=[self._rows[index] for index in self.routes.curselection() if index in self._rows]
        if len(rows)!=1:
            self.status.set('Select one connection, lease or PM connection in the list to stop it.')
            return
        kind,first,second=rows[0]
        try:
            if kind=='owner':
                # This one direction; the other direction, if connected, is its own row.
                self.owner.change(first,second,enabled=False,bidirectional=False)
                self.status.set('That connection is stopped. The chats remain open.')
            elif kind=='pm':
                # Also keeps the PM from making it again; only Connect undoes that.
                stopped=self.owner.stop_pm(first,second)
                if not stopped.removed: self.status.set('That PM connection was already gone. Nothing was stopped.')
                elif stopped.unblocked is None:
                    self.status.set('That PM connection is stopped. The PM cannot make it again; Connect can.')
                else: self.status.set('That PM connection is stopped, but the PM could make it again: '
                    'the block was not saved ('+stopped.unblocked+').')
            elif self.owner.lease(first,second,enabled=False): self.status.set('That lease is stopped.')
            else: self.status.set('That lease was already gone or replaced. Nothing was stopped.')
        except Exception as error:
            self.status.set('Stop selected connection: '+refusal(error))
        self.refresh()

    def _change(self,enabled):
        if self.preview or len(self._order)!=2: return
        try:
            self.owner.change(*self._order,enabled=enabled,bidirectional=self.direction.get()=='Both directions')
            self.status.set('Chats connected. Each keeps its current permissions.' if enabled else 'Connection stopped. The chats remain open.')
        except Exception as error:
            self.status.set(refusal(error))
        self.refresh()

    def _lease(self,enabled):
        if self.preview or len(self._order)!=2: return
        try:
            granted=self.owner.lease(*self._order,enabled=enabled)
            self.status.set('Since '+_when(granted)+' the first chat may deliver to the second for other apps, '
                'until you click Stop lease. It delivers only while it is in an active turn.'
                if enabled else 'The first chat no longer delivers to the second.')
        except Exception as error:
            self.status.set(refusal(error))
        self.refresh()


def hints(choices):
    """The help line under the chat list: how a chat gets listed, and what 'not answering' means."""
    if any(choice.live is False for choice in choices): return NOT_ANSWERING+' '+NOT_LISTED_HELP
    return NOT_LISTED_HELP


def waiting_line(count):
    """The line for remembered connections whose chats are not listed now (after a restart, say)."""
    if not count: return ''
    return ('Remembered connections waiting for their chats: %d (each direction counts once). A chat of each is closed or has not been seen '
        'since the bridge started. Each connection comes back by itself once both its chats are listed: for each '
        'chat, %s. Leases end when the bridge restarts: grant them again.') % (count,COME_BACK)


def _when(granted):
    return time.strftime('%Y-%m-%d %H:%M',time.localtime(granted))
