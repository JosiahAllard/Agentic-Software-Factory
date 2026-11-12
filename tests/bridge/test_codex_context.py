"""Active Codex caller metadata over real stable files and a retained process."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import uuid

from optional.bridge.hosts.codex.context import CodexObserver, ContextError, _sha
from kitlib.proc.process import own_ctime

NATIVE_SOURCE = 'optional/bridge/hosts/codex/context.py'


def counting():
    """(patcher, reads, walks): within the patcher every observer lists each rollout read and thread walked."""
    from unittest.mock import patch
    reads,walks=[],[]
    read,walk=CodexObserver._read_records,CodexObserver._walk
    def counted_read(observer,path,contexts):
        reads.append(path)
        return read(observer,path,contexts)
    def counted_walk(observer,thread):
        walks.append(thread)
        return walk(observer,thread)
    return patch.multiple(CodexObserver,_read_records=counted_read,_walk=counted_walk),reads,walks


class CodexContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='bridge-codex-context-')
        self.root = Path(self.temp.name)
        self.sessions = self.root/'sessions'/'2026'/'10'/'12'
        self.sessions.mkdir(parents=True)
        self.workspace = self.root/'workspace'
        self.workspace.mkdir()
        self.thread, self.turn = str(uuid.uuid4()), str(uuid.uuid4())
        self.meta = {'x-codex-turn-metadata':{'thread_id':self.thread,'turn_id':self.turn,
            'thread_source':'user','auto_review_enabled':True,'node_repl_auto_review_required':True}}
        self.session = {'id':self.thread,'cwd':str(self.workspace),'originator':'Codex Desktop',
            'source':'vscode','thread_source':'user','creator_account_id':'test-account',
            'creator_user_id':'test-user'}
        self.context = {'turn_id':self.turn,'cwd':str(self.workspace),'approval_policy':'never',
            'approvals_reviewer':'auto_review','sandbox_policy':{'type':'workspace-write'},
            'permission_profile':{'type':'managed','network':'restricted','file_system':{
                'type':'restricted','entries':[{'access':'write','path':{'type':'path','path':str(self.workspace)}}]}}}
        self.lifecycle = {'type':'task_started','turn_id':self.turn}
        self.path = self.sessions / ('rollout-test-'+self.thread+'.jsonl')
        self.write()
        self.observer = CodexObserver(self.root,os.getpid(),own_ctime(),sys.executable)

    def tearDown(self):
        self.observer.close()
        self.temp.cleanup()

    def record(self, kind, payload):
        return json.dumps({'timestamp':'2026-10-12T12:00:00Z','type':kind,'payload':payload},
            separators=(',',':')).encode()+b'\n'

    def write(self, *, extra=b''):
        self.path.write_bytes(self.record('session_meta',self.session)+extra+
            self.record('turn_context',self.context)+self.record('event_msg',self.lifecycle))

    def test_host_context_matches_original_active_chat_and_retained_native_process(self):
        facts=self.observer.observe(self.meta)
        self.assertEqual(facts.thread_id,self.thread)
        self.assertEqual(facts.turn_id,self.turn)
        self.assertEqual(facts.kind,'desktop')
        self.assertEqual(facts.mode,'prompting')
        self.assertEqual(facts.workspace_identity,(self.workspace.stat().st_dev,self.workspace.stat().st_ino))
        binding=facts.binding('test-host')
        self.assertEqual(binding.identity.native_session_id,self.thread)
        self.assertEqual(binding.identity.adapter_id,'codex-desktop')
        self.assertEqual(binding.account_ref,facts.account_ref)

    def test_terminal_native_identity_is_supported_without_desktop_originator_filter(self):
        self.session.update(originator='codex_cli_rs',source='cli')
        self.write()
        facts=self.observer.observe(self.meta)
        self.assertEqual(facts.kind,'terminal')
        self.assertEqual(facts.binding('test-host').identity.adapter_id,'codex-terminal')

    def test_full_access_never_is_bypass_while_sandboxed_never_is_prompting(self):
        self.assertEqual(self.observer.observe(self.meta).mode,'prompting')
        self.context.update(permission_profile={'type':'disabled'},sandbox_policy={'type':'danger-full-access'},
            approvals_reviewer='user')
        self.write()
        self.assertEqual(self.observer.observe(self.meta).mode,'bypass')

    def test_completed_or_replaced_active_turn_and_forged_thread_metadata_are_rejected(self):
        for mutation in ('completed','changed-turn','wrong-thread'):
            with self.subTest(mutation=mutation):
                if mutation=='completed': self.lifecycle['type']='task_completed'
                elif mutation=='changed-turn': self.lifecycle.update(type='task_started',turn_id=str(uuid.uuid4()))
                else: self.meta['x-codex-turn-metadata']['thread_id']=str(uuid.uuid4())
                self.write()
                with self.assertRaises(ContextError): self.observer.observe(self.meta)

    def test_permission_account_and_writer_changes_change_selection_binding(self):
        original=self.observer.observe(self.meta).binding('test-host')
        self.context['approval_policy']='on-request'
        self.write()
        self.assertNotEqual(original,self.observer.observe(self.meta).binding('test-host'))
        self.session['creator_account_id']='different-account'
        self.write()
        self.assertNotEqual(original,self.observer.observe(self.meta).binding('test-host'))
        with self.assertRaises(ContextError):
            CodexObserver(self.root,os.getpid(),own_ctime()+1,sys.executable)

    def test_missing_review_evidence_unknown_permissions_and_account_are_unavailable(self):
        self.meta['x-codex-turn-metadata'].pop('auto_review_enabled')
        with self.assertRaises(ContextError): self.observer.observe(self.meta)
        self.meta['x-codex-turn-metadata']['auto_review_enabled']=True
        self.context['permission_profile']['file_system']['type']='unrestricted'
        self.write()
        with self.assertRaises(ContextError): self.observer.observe(self.meta)
        self.context['permission_profile']['file_system']['type']='restricted'
        self.session.pop('creator_user_id')
        self.write()
        with self.assertRaises(ContextError): self.observer.observe(self.meta)

    def test_workspace_drift_duplicate_rollout_and_repeated_session_identity_are_rejected(self):
        self.context['cwd']=str(self.root)
        self.write()
        with self.assertRaises(ContextError): self.observer.observe(self.meta)
        self.context['cwd']=str(self.workspace)
        self.write(extra=self.record('session_meta',self.session))
        with self.assertRaises(ContextError): self.observer.observe(self.meta)
        self.write()
        (self.sessions/('rollout-duplicate-'+self.thread+'.jsonl')).write_bytes(self.path.read_bytes())
        with self.assertRaises(ContextError): self.observer.observe(self.meta)

    def test_large_unrelated_native_record_is_streamed_and_does_not_hide_later_context(self):
        large=self.record('response_item',{'type':'function_call_output','output':'private large output'*100000})
        self.write(extra=large)
        facts=self.observer.observe(self.meta)
        self.assertEqual(facts.turn_id,self.turn)
        self.assertNotIn('private large output',repr(facts))

    def test_oversize_authority_record_and_incomplete_final_line_never_attest(self):
        self.context['unexpected']='x'*140000
        self.write()
        with self.assertRaises(ContextError): self.observer.observe(self.meta)
        self.context.pop('unexpected')
        self.write()
        self.path.write_bytes(self.path.read_bytes()[:-1])
        with self.assertRaises(ContextError): self.observer.observe(self.meta)

    def test_directory_scan_and_file_limits_fail_closed(self):
        from unittest.mock import patch
        with patch('optional.bridge.hosts.codex.context.MAX_ENTRIES',1):
            with self.assertRaises(ContextError): self.observer.observe(self.meta)
        with patch('optional.bridge.hosts.codex.context.MAX_ROLLOUT_BYTES',1):
            with self.assertRaises(ContextError): self.observer.observe(self.meta)

    def test_a_scope_finds_and_reads_each_rollout_once_and_a_call_outside_one_reads_fresh(self):
        patcher,reads,walks=counting()
        with patcher:
            facts=self.observer.observe(self.meta)
            sha=(_sha(self.session),_sha(self.context))
            self.assertEqual((len(reads),len(walks)),(1,1))
            with self.observer.scope():
                for _ in range(3): self.observer.observe(self.meta)
                self.observer.observe_existing(facts,self.meta,session_sha256=sha[0],context_sha256=sha[1])
            self.assertEqual((len(reads),len(walks)),(2,2))  # the scope: one more of each
            self.observer.observe(self.meta)
            self.assertEqual((len(reads),len(walks)),(3,3))  # the memo is gone with the scope

    def test_an_inner_scope_exit_keeps_the_outer_scopes_memo(self):
        patcher,reads,_walks=counting()
        with patcher, self.observer.scope():
            with self.observer.scope(): self.observer.observe(self.meta)
            self.observer.observe(self.meta)
        self.assertEqual(len(reads),1)

    def test_an_error_inside_a_scope_clears_its_memo(self):
        patcher,reads,_walks=counting()
        with patcher:
            with self.assertRaises(RuntimeError), self.observer.scope():
                self.observer.observe(self.meta)
                raise RuntimeError('boom')
            self.observer.observe(self.meta)
        self.assertEqual(len(reads),2)

    def test_scoped_contexts_are_copied_to_each_caller(self):
        with self.observer.scope():
            first=[]
            self.observer._records(self.path,contexts=first)
            first.append('caller edit')
            second=[]
            self.observer._records(self.path,contexts=second)
        self.assertEqual(second,[(self.turn,_sha(self.context))])

    def test_thread_name_is_the_last_index_entry_for_the_thread_and_never_identity(self):
        # Codex keeps chat titles in session_index.jsonl, one JSON line per rename (display only).
        index=self.root/'session_index.jsonl'
        self.assertIsNone(self.observer.thread_name(self.thread))  # no index yet
        other=str(uuid.uuid4())
        lines=[{'id':self.thread,'thread_name':'first title','updated_at':'x'},
            {'id':other,'thread_name':'another chat','updated_at':'x'},
            {'id':self.thread,'thread_name':'kit coordinator','updated_at':'y'}]
        index.write_bytes(b''.join(json.dumps(line).encode()+b'\n' for line in lines)+b'{broken\n')
        self.assertEqual(self.observer.thread_name(self.thread),'kit coordinator')
        self.assertIsNone(self.observer.thread_name(str(uuid.uuid4())))
        before=self.observer.observe(self.meta).binding('test-host')
        index.write_bytes(json.dumps({'id':self.thread,'thread_name':'renamed'}).encode()+b'\n')
        self.assertEqual(self.observer.observe(self.meta).binding('test-host'),before)  # a rename changes no binding
        from unittest.mock import patch
        with patch('optional.bridge.hosts.codex.context.MAX_INDEX_BYTES',4):
            self.assertIsNone(self.observer.thread_name(self.thread))
