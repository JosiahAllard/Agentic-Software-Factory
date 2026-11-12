"""The hub applies an approved change (kitlib.ownerfiles.apply, docs/hub-approvals.md "Applying"):
the owner's approve on a real kitlib.hubsite.api commits the approved files on the branch the
request named, in its linked worktree, and consumes the approval with that commit; what cannot be
applied stays approved with the reason; a failure after the branch moved puts everything back."""
import datetime
import os
import shutil
import tempfile
import unittest
from unittest import mock

from kitlib import config as kconfig
from kitlib.hubsite.api import AGENT, BASE, OWNER
from kitlib.ownerfiles import apply as consumer
from kitlib.ownerfiles import approvals as ap
from kitlib.ownerfiles import request as rq
from kitlib.ownerfiles import rules as oc
from tests.gitfixture import TempRepo
from tests.home import temp_home
from tests.realgit import real_git
from tests.ownerfiles.test_approvals_add import KEY, NOW, OTHER, URL, Hub, own

BRANCH = 'kit/w'
norm = lambda p: os.path.normcase(os.path.normpath(p))      # noqa: E731
PATH = 'docs/a.txt'
WHEN = datetime.datetime.fromisoformat(NOW)
DID = 'A-100'


class Case(unittest.TestCase):
    """A checkout of URL whose committed `.kit.json` makes `docs/a.txt` owner-only, a linked
    worktree on kit/w, and a hub holding a pending request to change the file there."""

    def setUp(self):
        temp_home(self)
        self.repo = TempRepo()
        self.addCleanup(self.repo.cleanup)
        self.repo.git('remote', 'add', 'origin', URL)
        self.repo.write(PATH, 'one\n')
        own(self.repo.root)
        self.head = self.repo.commit('a file', PATH, '.kit.json')
        self.wt = self.repo.worktree('w')
        self.hub = Hub(self, self.repo.root)
        status, obj = self.hub.api.call('POST', BASE + '/approvals', AGENT, {
            'repo': URL, 'title': 'Change a', 'head': self.head, 'branch': BRANCH,
            'files': [{'path': PATH, 'base_blob': ap.blob_id('one\n'), 'content': 'two\n'}]},
            None)
        self.assertEqual(status, 200, obj)
        self.did = obj['id']

    def doc(self):
        return self.hub.approvals()[self.did]

    def tip(self, branch=BRANCH):
        return self.repo.git('rev-parse', 'refs/heads/' + branch)

    def write(self, data, path=PATH):
        full = os.path.join(self.wt, *path.split('/'))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, 'wb') as f:
            f.write(data)

    def read(self, path=PATH):
        with open(os.path.join(self.wt, *path.split('/')), 'rb') as f:
            return f.read()

    def commit_wt(self, path, data):
        """Commit `data` at `path` on kit/w, in its worktree; the new tip."""
        self.write(data, path)
        return self.repo.commit('work on ' + path, path, cwd=self.wt)

    def unappliable(self):
        """The request decided while the work tree drifts, so it stays approved; then put back."""
        self.write(b'drift\n')
        self.hub.decide(self.did)
        self.write(b'one\n')
        self.assertEqual(self.doc()['status'], ap.APPROVED)

    def apply_route(self, did=None):
        return self.hub.api.call('POST', BASE + '/approvals/%s/apply' % (did or self.did), OWNER,
                                 {}, None, strong=True)


class Approve(Case):

    def test_approve_commits_in_named_worktree(self):
        self.hub.decide(self.did)
        doc, tip = self.doc(), self.tip()
        self.assertEqual((doc['status'], doc['commit']), (ap.CONSUMED, tip))
        self.assertEqual(norm(doc['applied']['worktree']), norm(self.wt))
        self.assertEqual(self.repo.git('rev-parse', tip + '^'), self.head)
        self.assertIn('Owner-Approval: hub:%s' % self.did,
                      self.repo.git('log', '-1', '--format=%B', tip))
        self.assertEqual(self.read(), b'two\n')
        self.assertEqual(self.repo.git('-C', self.wt, 'status', '--porcelain', '--', PATH), '')

    def test_commit_passes_owner_rules(self):
        self.hub.decide(self.did)
        rules = kconfig.load(self.wt).owner_rules()
        bad, why = oc.violations(self.wt, rules, self.tip(), (self.head,),
                                 approvals=oc.HubApprovals(client=self.hub.client))
        self.assertEqual((bad, why), ([], None))
        self.assertEqual(self.repo.git('log', '-1', '--format=%ae', self.tip()),
                         consumer.AUTHOR[1])
        self.assertFalse(rules.is_owner(consumer.AUTHOR[1]))

    def test_unappliable_stays_approved_with_reason(self):
        self.write(b'drift\n')
        self.hub.decide(self.did)
        doc = self.doc()
        self.assertEqual(doc['status'], ap.APPROVED)
        self.assertNotIn('commit', doc)
        self.assertIn("is not HEAD's copy", doc['apply']['error'])
        self.assertEqual((self.tip(), self.read()), (self.head, b'drift\n'))

    def test_head_moved_on_with_other_work_is_committed_and_consumed(self):
        moved = self.commit_wt('other.txt', b'other\n')
        self.hub.decide(self.did)
        doc, tip = self.doc(), self.tip()
        self.assertEqual((doc['status'], doc['commit']), (ap.CONSUMED, tip))
        self.assertEqual(self.repo.git('rev-parse', tip + '^'), moved)
        self.assertEqual(self.read(), b'two\n')

    def test_no_such_approval(self):
        status, obj = self.apply_route('A-999')
        self.assertEqual(status, 404, obj)


class TheSignedBranch(Case):
    """The branch an apply commits on is the one the owner approved: the signature covers it."""

    def test_branch_changed_after_the_decision_is_refused_at_apply(self):
        self.unappliable()
        other = self.repo.worktree('x')
        before = self.tip('kit/x')
        self.hub.store.update(ap.COLLECTION, self.did, {'branch': 'kit/x'})
        status, obj = self.apply_route()
        self.assertEqual(status, 200, obj)
        self.assertFalse(obj['applied'])
        self.assertIn(ap.WHY_UNSIGNED, obj['why'])
        self.assertEqual(self.doc()['status'], ap.APPROVED)
        self.assertEqual(self.tip('kit/x'), before)
        self.assertEqual(self.repo.git('-C', other, 'status', '--porcelain'), '')

    def test_branch_other_than_the_approved_one_is_refused(self):
        self.unappliable()
        done, why = consumer.apply(self.wt, 'kit/x', self.did, self.doc(), KEY, WHEN)
        self.assertIsNone(done)
        self.assertIn('was approved for branch %s, not kit/x' % BRANCH, why)

    def test_an_approved_request_with_no_branch_is_not_applied(self):
        files = [{'path': PATH, 'base_blob': ONE, 'content': 'two\n'}]
        doc = ap.new_doc(URL, 't', self.head, files, WHEN)
        doc.update(ap.decide_fields(KEY, self.did, doc, ap.APPROVE, WHEN))
        self.hub.store.put(ap.COLLECTION, self.did, doc)
        status, obj = self.apply_route()
        self.assertEqual(status, 200, obj)
        self.assertFalse(obj['applied'])
        doc = self.doc()
        self.assertEqual(doc['status'], ap.APPROVED)
        self.assertTrue(doc['apply']['error'])
        self.assertEqual((self.tip(), self.read()), (self.head, b'one\n'))

    def test_an_approval_signed_without_a_branch_still_verifies(self):
        files = [{'path': PATH, 'base_blob': ONE, 'content': 'two\n'}]
        doc = ap.new_doc(URL, 't', self.head, files, WHEN)
        self.assertNotIn('branch', doc)
        doc.update(ap.decide_fields(KEY, DID, doc, ap.APPROVE, WHEN))
        blobs = {f['path']: f['blob'] for f in doc['files']}
        self.assertEqual(ap.check(KEY, DID, doc, URL, self.head, blobs, WHEN), (True, ''))
        self.assertEqual(ap.check(KEY, DID, dict(doc, branch=BRANCH), URL, self.head, blobs,
                                  WHEN), (False, ap.WHY_UNSIGNED))

    def test_signature_covers_branch_only_when_present(self):
        doc = ap.new_doc(URL, 't', self.head, [{'path': PATH, 'base_blob': None,
                                                'content': 'x'}], WHEN)
        self.assertNotIn(b'"branch"', ap.canonical(DID, doc))
        self.assertIn(b'"branch":"kit/w"', ap.canonical(DID, dict(doc, branch=BRANCH)))


class PutBack(Case):

    def setUp(self):
        super(PutBack, self).setUp()
        self.unappliable()

    def test_put_back_on_refusal(self):
        with mock.patch.object(consumer, '_set_index',
                               side_effect=['index.lock is held', None]) as set_index:
            status, obj = self.apply_route()
        self.assertEqual(status, 200, obj)
        self.assertFalse(obj['applied'])
        self.assertIn('index.lock is held', obj['why'])
        self.assertIn('as they were', obj['why'])
        self.assertEqual(set_index.call_count, 2)
        self.assertEqual((self.tip(), self.read()), (self.head, b'one\n'))
        doc = self.doc()
        self.assertEqual(doc['status'], ap.APPROVED)
        self.assertNotIn('commit', doc)
        self.assertIn('index.lock is held', doc['apply']['error'])

    def test_a_probe_refusal_writes_nothing(self):
        held = lambda root, paths: 'needs %s, held by L-ONE on kit/l' % paths[0]  # noqa: E731
        done, why = consumer.apply(self.wt, BRANCH, self.did, self.doc(), KEY, WHEN, checks=(held,))
        self.assertIsNone(done)
        self.assertEqual(why, 'needs %s, held by L-ONE on kit/l' % PATH)
        self.assertEqual((self.tip(), self.read()), (self.head, b'one\n'))
        self.assertEqual(self.repo.git('-C', self.wt, 'status', '--porcelain', '-uno'), '')


class TheOwnerRules(Case):

    def test_the_work_trees_uncommitted_config_does_not_loosen_them(self):
        own(self.wt, ['README.md'])         # uncommitted: loose.txt and docs/ unlocked on disk
        self.assertFalse(kconfig.load(self.wt).owner_rules().is_locked('loose.txt'))
        rules, why = consumer._owner_rules(self.wt, self.head)
        self.assertIsNone(why)
        self.assertTrue(rules.is_locked('loose.txt'))
        self.assertTrue(rules.is_locked(PATH))

    def test_a_main_checkout_off_its_main_branch_gives_no_rules(self):
        # no <remote>/<main> to read: the main checkout's HEAD is another branch, whose
        # .kit.json would unlock docs/; land refuses then, and so does apply
        self.unappliable()
        self.repo.git('checkout', '-q', '-b', 'side')
        own(self.repo.root, ['README.md'])
        self.repo.commit('side config', '.kit.json')
        rules, _why = consumer._owner_rules(self.wt, self.head)
        self.assertIsNone(rules)
        status, obj = self.apply_route()
        self.assertEqual(status, 200, obj)
        self.assertFalse(obj['applied'])
        self.assertEqual(self.doc()['status'], ap.APPROVED)
        self.assertEqual((self.tip(), self.read()), (self.head, b'one\n'))


# The rows: (name, arrange, files, tweak, expect). arrange(case) changes the worktree and returns
# the head the approval is made on (None: the fixture's); files are the request's; tweak(doc)
# changes the signed document; expect is the step that refuses (a name of STEPS, or (approvals'
# check, its WHY_ phrase)), or a check(case, done) of an applied change. A refusal leaves the
# branch, the work tree and the index as they were.

ONE = ap.blob_id('one\n')
M_BASE = 'a\nb\nc\n'
M = 'docs/m.txt'


def _merge_base(head_copy):
    def arrange(case):
        base = case.commit_wt(M, M_BASE.encode())
        case.commit_wt(M, head_copy.encode())
        return base
    return arrange


def _staged(path, data, back=None):
    def arrange(case):
        case.write(data, path)
        case.repo.git('add', '--', path, cwd=case.wt)
        if back is not None:
            case.write(back, path)
    return arrange


def _write(path, data):
    return lambda case: case.write(data, path)


def _commit(path, data):
    def arrange(case):
        case.commit_wt(path, data)
    return arrange


def _symlink(path, outside=False):
    def arrange(case):
        full = os.path.join(case.wt, *path.split('/'))
        target = full + '.real'
        if outside:
            target = tempfile.mkdtemp(prefix='kit-apply-out-')
            case.addCleanup(shutil.rmtree, target, True)
        if os.path.lexists(full):
            os.rename(full, target)
        try:
            os.symlink(target, full, target_is_directory=outside)
        except (OSError, NotImplementedError) as e:
            raise unittest.SkipTest('no symbolic links here: %s' % e)
    return arrange


def _kit_checkout(case):
    os.makedirs(os.path.join(case.wt, 'content', 'roles'))
    case.write(b'# render\n', 'tools/render_content.py')


def _new_and_deleted(case, done):
    tree = case.repo.git('ls-tree', '-r', '--name-only', done.commit)
    case.assertIn('docs/new.txt', tree.split('\n'))
    case.assertNotIn(PATH, tree.split('\n'))
    case.assertEqual(case.read('docs/new.txt'), b'n\n')
    case.assertFalse(os.path.lexists(os.path.join(case.wt, *PATH.split('/'))))


def _moved_on(case, done):
    case.assertEqual(case.repo.git('rev-parse', done.commit + '^'), done.old)
    case.assertEqual(case.read(), b'two\n')


def _merged(case, done):
    case.assertEqual(case.read(M), b'A\nb\nC\n')
    case.assertEqual(case.repo.git('show', '%s:%s' % (done.commit, M)), 'A\nb\nC')


CHANGE = [{'path': PATH, 'base_blob': ONE, 'content': 'two\n'}]
NEW = [{'path': 'docs/new.txt', 'base_blob': None, 'content': 'n\n'}]
MERGE = [{'path': M, 'base_blob': ap.blob_id(M_BASE), 'content': 'a\nb\nC\n'}]

ROWS = [
    ('new_file_and_delete', None, NEW + [{'path': PATH, 'base_blob': ONE, 'content': None}],
     None, _new_and_deleted),
    ('head_moved_on_with_other_work', _commit('other.txt', b'x\n'), CHANGE, None, _moved_on),
    ('approved_path_already_staged', _staged(PATH, b'zzz\n', b'one\n'), CHANGE, None,
     '_at_head'),
    ('something_else_staged', _staged('other.txt', b'x\n'), CHANGE, None, '_staged_error'),
    ('head_already_holds_the_change', _commit(PATH, b'two\n'), CHANGE, None, 'prewrite'),
    ('new_file_the_later_head_holds_with_other_content', _commit('docs/new.txt', b'other\n'),
     NEW, None, 'delta_targets'),
    ('not_decided', None, CHANGE, 'pending', 'state_error'),
    ('signature_does_not_verify', None, CHANGE, lambda d: d.update(sig='0' * 64),
     ('check', ap.WHY_UNSIGNED)),
    ('content_not_the_signed_blob', None, CHANGE,
     lambda d: d['files'][0].update(content='three\n'), 'prewrite'),
    ('other_repository', None, CHANGE, 'other', ('check', ap.WHY_REPO)),
    ('render_source_among_the_paths', _kit_checkout,
     [{'path': 'content/x.md', 'base_blob': None, 'content': 'x\n'}], None,
     'owner_paths_error'),
    ('path_the_owner_rules_do_not_lock', None,
     [{'path': 'free.txt', 'base_blob': None, 'content': 'x\n'}], None, 'owner_paths_error'),
]
REAL_ROWS = [
    ('clean_merge_on_a_newer_base', _merge_base('A\nb\nc\n'), MERGE, None, _merged),
    ('overlap_refused_with_nothing_written', _merge_base('a\nb\nX\n'), MERGE, None,
     'delta_targets'),
    ('symlink_at_an_approved_path', _symlink(PATH), CHANGE, None, '_path_error'),
    ('link_out_of_the_work_tree', _symlink('docs/lnk', outside=True),
     [{'path': 'docs/lnk/x.txt', 'base_blob': None, 'content': 'x\n'}], None, '_path_error'),
    ('filters_would_store_other_bytes', _write('.gitattributes', b'*.txt text\n'),
     [{'path': PATH, 'base_blob': ONE, 'content': 'two\r\n'}], None, '_filters_error'),
]

# The steps of apply's checks a row may name, each (module, name, refused(result)): the first one
# to refuse, in call order (an inner step returns before the one that calls it), is the row's.
_why = lambda got: got is not None             # noqa: E731
_first_none = lambda got: got[0] is None       # noqa: E731
STEPS = [(consumer, 'state_error', _why), (ap, 'check', lambda got: got[0] is False),
         (consumer, '_head_error', _why), (rq, 'owner_paths_error', _why),
         (consumer, '_path_error', _why), (ap, 'delta_targets', _first_none),
         (consumer, 'prewrite', _first_none), (consumer, '_filters_error', _why),
         (consumer, '_at_head', _first_none), (consumer, '_staged_error', _why)]


class _Rows(Case):
    """consumer.apply on a document signed here (DID), one row each."""

    def signed(self, files, head, tweak):
        doc = ap.new_doc(OTHER if tweak == 'other' else URL, 'Change', head, files, WHEN,
                         branch=BRANCH)
        if tweak != 'pending':
            doc.update(ap.decide_fields(KEY, DID, doc, ap.APPROVE, WHEN))
        if callable(tweak):
            tweak(doc)
        return doc

    def steps(self):
        """Spy on STEPS; the list of (name, refused, result) each call appends, in return order."""
        calls = []
        for mod, name, refused in STEPS:
            def spy(*a, _real=getattr(mod, name), _name=name, _refused=refused, **k):
                got = _real(*a, **k)
                calls.append((_name, _refused(got), got))
                return got
            patcher = mock.patch.object(mod, name, spy)
            patcher.start()
            self.addCleanup(patcher.stop)
        return calls

    def check_row(self, arrange, files, tweak, expect):
        head = (arrange(self) if arrange else None) or self.head
        doc = self.signed(files, head, tweak)
        before = (self.tip(), self.repo.git('-C', self.wt, 'status', '--porcelain'),
                  self.repo.git('-C', self.wt, 'diff', '--cached', '--name-only'))
        calls = self.steps()
        done, why = consumer.apply(self.wt, BRANCH, DID, doc, KEY, WHEN)
        if callable(expect):
            self.assertIsNotNone(done, why)
            self.assertEqual(self.tip(), done.commit)
            self.assertEqual(self.repo.git('-C', self.wt, 'status', '--porcelain', '-uno'), '')
            expect(self, done)
            return
        self.assertIsNone(done)
        refusing = [(name, got) for name, refused, got in calls if refused]
        self.assertTrue(refusing, why)
        step, phrase = expect if isinstance(expect, tuple) else (expect, None)
        self.assertEqual(refusing[0][0], step, why)
        if phrase is not None:
            self.assertEqual(refusing[0][1], (False, phrase))
        self.assertEqual((self.tip(), self.repo.git('-C', self.wt, 'status', '--porcelain'),
                          self.repo.git('-C', self.wt, 'diff', '--cached', '--name-only')),
                         before)


def _row_test(row):
    _name, arrange, files, tweak, expect = row
    return lambda self: self.check_row(arrange, files, tweak, expect)


class Rows(_Rows):
    """ROWS, on the fake git."""


@real_git('git merge-file, symbolic links and the text attribute with no eol: the fake has none')
class RealRows(_Rows):
    """REAL_ROWS, on real git."""


for _cls, _rows in ((Rows, ROWS), (RealRows, REAL_ROWS)):
    for _row in _rows:
        setattr(_cls, 'test_' + _row[0], _row_test(_row))
del _cls, _rows, _row     # a module-level TestCase name is loaded as a test class


class Where(unittest.TestCase):

    def test_branch_not_a_linked_worktree_refuses(self):
        repo = TempRepo()
        self.addCleanup(repo.cleanup)
        repo.worktree('w')
        self.assertIn("main checkout's branch", consumer.worktree_for(repo.root, 'main')[1])
        self.assertIn('no linked worktree', consumer.worktree_for(repo.root, 'kit/x')[1])
        self.assertIsNone(consumer.worktree_for(repo.root, BRANCH)[1])
        self.assertIsNone(consumer.worktree_for(repo.root, None)[0])


if __name__ == '__main__':
    unittest.main()
