"""_hub_args.py: the argument parser of tools/hub.py; not a command. The work of each verb is in
kitlib/hubsite/cli/ (its package docstring lists the verbs; `hub.py <verb> --help` gives each
one's options); the verbs' names, their usage examples and the checks of option values are in
kitlib/hubsite/cli/usage.py. The argparse types below (_port, _folder, _count, _seconds,
_choice, _option, _record) are those checks: a bad value is a usage error, exit 2, before any
verb runs.
"""
import argparse
import sys

from kitlib import hubvalidate
from kitlib.hubclient import localask
from kitlib.hubsite.cli import server, task, usage
from kitlib.ownerfiles import approvals

# the stop and restart timeouts the help names: the cli's own imports of them
control, hub_autostart = server.control, task.hub_autostart


class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        sys.stderr.write('USAGE: %s\n' % message)
        sys.stderr.write(usage.example_line(self.prog))
        sys.exit(2)


def _typed(check):
    """An argparse type of `check`: its ValueError is the option's usage error, in its words."""
    def typed(text):
        try:
            return check(text)
        except ValueError as e:
            raise argparse.ArgumentTypeError(str(e))
    return typed


def _port(low):
    return _typed(usage.port(low))


_folder, _count, _seconds, _choice, _option, _record = (
    _typed(check) for check in (usage.folder, usage.count, usage.seconds, usage.choice,
                                usage.option, usage.record))


def _question_class(x, what):
    """--class on questions add and edit (docs/hub-questions.md "A question's class")."""
    x.add_argument('--class', dest='question_class', choices=hubvalidate.QUESTION_CLASSES,
                   help=what + ': rule (a standing rule or a delegation run line), machine, '
                        'cross-repo or go (a one-off landing or archive go)')


def _unfiled(x):
    """--unfiled on questions log and done (docs/hub-questions.md "Filing the answer")."""
    x.add_argument('--unfiled', metavar='WHY',
                   help='why an answer whose filing into its record failed for good (not '
                        'found, invalid, no store) is handled without it')


def _asks(p):
    """The two arguments of every parser whose verb asks the server. No default is set: a
    sub-verb's parser would put its own over what the verb before it was given."""
    p.add_argument('--port', type=_port(1), default=argparse.SUPPRESS,
                   help='the port (default: hub.port)')
    p.add_argument('--json', action='store_true', default=argparse.SUPPRESS,
                   help="print the server's answer as one JSON line")
    return p


def _pins(p, required=False):
    p.add_argument('--if-version', dest='if_version', type=_count, default=None, metavar='V',
                   required=required, help='refuse unless the document has this version')


def _creator(p, metavar):
    """The id, --from and --if-version of a creator's edit or withdraw: the hub checks --from
    against the item's own (kitlib.hubsite.api _creator)."""
    p.add_argument('id')
    p.add_argument('--from', dest='who', required=True, metavar=metavar,
                   help='the from the item was added with (pm when it was added without one)')
    _pins(p, required=True)


def _repo(p):
    """--repo of an `add` whose verb lists by --repo too. No default is set, as in _asks: the
    sub-verb's own would put None over a --repo given before `add`. Read it with getattr."""
    p.add_argument('--repo', default=argparse.SUPPRESS)


def _listed(sub, name, what, statuses=None, repo=True, since=False):
    """The parser of a verb that lists its collection when no sub-verb follows; its
    sub-parsers. `since`: the list takes --since, an ISO time with its offset (the route keeps
    the documents written at or after it)."""
    p = _asks(sub.add_parser(name, help=what))
    if statuses:
        p.add_argument('--status', choices=statuses + ('all',), default=argparse.SUPPRESS)
    if repo:
        p.add_argument('--repo', default=argparse.SUPPRESS)
    if since:
        p.add_argument('--since', default=argparse.SUPPRESS, metavar='ISO',
                       help='only those written at or after this time (ISO, with its offset)')
    return p.add_subparsers(dest='sub')


def _leaf(subs, name, what):
    return _asks(subs.add_parser(name, help=what))


def _client_verbs(sub):
    S = hubvalidate.STATUSES
    q = _listed(sub, 'questions', 'list the questions; add: ask one; log: mark it logged; '
                'done: handled with no ruling; edit, withdraw: change or remove one you asked; '
                'file: file answers into their records again', S['questions'], since=True)
    x = _leaf(q, 'show', 'one question with its options, context and answer')
    x.add_argument('id')
    x = _leaf(q, 'add', 'ask the owner a question')
    x.add_argument('--title', required=True)
    _repo(x)
    x.add_argument('--context-file', dest='context_file', metavar='F',
                   help='a UTF-8 file with the context of the question')
    x.add_argument('--option', dest='options', action='append', type=_option, default=[],
                   metavar='"label::description"', help='an answer to choose (repeatable)')
    x.add_argument('--recommended', type=_count, metavar='N',
                   help='the place of the recommended option (counts from 0)')
    x.add_argument('--refs', nargs='+', metavar='ID')
    x.add_argument('--urgent', action='store_true')
    x.add_argument('--story', metavar='S-n')
    x.add_argument('--record', type=_record, metavar='REPO:ID',
                   help='the backlog record the question is about (checked by the hub)')
    _question_class(x, 'with no --record: what kind of question it is')
    x.add_argument('--from', dest='who', metavar='KEY',
                   help='your launcher session key: the answer comes back to it (default: pm)')
    x = _leaf(q, 'log', 'mark an answered question logged, its answer being in the ledger')
    x.add_argument('id')
    _unfiled(x)
    _pins(x, required=True)
    x = _leaf(q, 'done', 'mark an answered question handled with no ruling (a one-off go)')
    x.add_argument('id')
    x.add_argument('--why', required=True, help='why the answer needs no ruling')
    _unfiled(x)
    _pins(x, required=True)
    x = _leaf(q, 'file', 'file each answer to a question bound to a record that is not filed '
              'yet into its record (the hub does at once and at start)')
    x.add_argument('id', nargs='?', help='only this question')
    x = _leaf(q, 'bind', 'bind open or answered questions to the records that cite them, '
              'filing an answered one at once (the PM)')
    mode = x.add_mutually_exclusive_group(required=True)
    mode.add_argument('--dry-run', dest='dry_run', action='store_true',
                      help='list what would be bound, and what cannot be')
    mode.add_argument('--apply', action='store_true', help='bind them')
    x = _leaf(q, 'edit','change an open question you asked')
    _creator(x, 'KEY')
    x.add_argument('--title')
    x.add_argument('--context-file', dest='context_file', metavar='F',
                   help='a UTF-8 file with the new context')
    x.add_argument('--option', dest='options', action='append', type=_option, default=[],
                   metavar='"label::description"',
                   help='an answer to choose (repeatable; the list given replaces the old one)')
    x.add_argument('--recommended', type=_count, metavar='N',
                   help='the place of the recommended option (counts from 0)')
    x.add_argument('--refs', nargs='+', metavar='ID')
    x.add_argument('--urgent', action=argparse.BooleanOptionalAction, default=None)
    bind = x.add_mutually_exclusive_group()
    bind.add_argument('--record', type=_record, metavar='REPO:ID',
                      help='bind the question to this backlog record (checked by the hub)')
    bind.add_argument('--no-record', dest='no_record', action='store_true',
                      help='bind the question to no record')
    _question_class(x, 'the class of a question bound to no record')
    _creator(_leaf(q, 'withdraw', 'remove an open question you asked'), 'KEY')

    x = _asks(sub.add_parser('answer', help='answer a question (the owner)'))
    x.add_argument('id')
    x.add_argument('--choice', required=True, type=_choice, metavar='N|other',
                   help='the place of the chosen option (counts from 0), or other (which needs '
                        '--note)')
    x.add_argument('--note')
    _pins(x)

    t = _listed(sub, 'todos', 'list the to-dos; add, done, ack, edit, withdraw', S['todos'])
    x = _leaf(t, 'add', 'add a to-do')
    x.add_argument('--title', required=True)
    x.add_argument('--detail')
    x.add_argument('--command', dest='commands', action='append', metavar='C',
                   help='a command the to-do names (repeatable)')
    x.add_argument('--steps', dest='steps_file', metavar='F',
                   help='a UTF-8 JSON file holding a list of steps: {edit, old, new}, '
                        '{open, line} or {run}; the hub makes them commands after --command')
    _repo(x)
    x.add_argument('--refs', nargs='+', metavar='ID')
    x.add_argument('--urgent', action='store_true')
    who = x.add_mutually_exclusive_group()
    who.add_argument('--from', dest='who', metavar='NAME')
    who.add_argument('--owner', action='store_true', help="the owner's own to-do")
    x.add_argument('--record', type=_record, metavar='REPO:ID',
                   help='the backlog record that waits on this to-do: the hub parks it until '
                        "the to-do is done (an agent's to-do only; checked by the hub)")
    x = _leaf(t, 'done', 'mark a to-do done (the owner)')
    x.add_argument('id')
    x.add_argument('--undo', action='store_true')
    x.add_argument('--note', help='the owner\'s note on it, shown in todos show and pushed to '
                                  'the asker (at most 2000 characters; not with --undo)')
    _pins(x)
    _leaf(t, 'show', 'one to-do with its detail, commands and the owner\'s note').add_argument('id')
    _leaf(t, 'ack', 'say that the PM has seen a to-do').add_argument('id')
    x = _leaf(t, 'edit', 'change an open to-do you added')
    _creator(x, 'NAME')
    x.add_argument('--title')
    x.add_argument('--detail')
    x.add_argument('--command', dest='commands', action='append', metavar='C',
                   help='a command the to-do names (repeatable; the list given replaces the old '
                        'one)')
    x.add_argument('--repo', dest='edit_repo', metavar='R')
    x.add_argument('--refs', nargs='+', metavar='ID')
    x.add_argument('--urgent', action=argparse.BooleanOptionalAction, default=None)
    _creator(_leaf(t, 'withdraw', 'remove an open to-do you added'), 'NAME')

    s = _listed(sub, 'stories', 'list the stories; add, set, withdraw', S['stories'])
    x = _leaf(s, 'add', 'submit a story (the owner)')
    x.add_argument('--repo', required=True)
    x.add_argument('--title', required=True)
    x.add_argument('--story-file', dest='story_file', required=True, metavar='F',
                   help='a UTF-8 file with the story')
    x.add_argument('--urgent', action='store_true')
    x = _leaf(s, 'set', "set a story's status")
    x.add_argument('id')
    x.add_argument('--status', required=True, choices=hubvalidate.AGENT_STORY_STATUSES)
    x.add_argument('--reviewer')
    x.add_argument('--review-note', dest='review_note')
    x.add_argument('--backlog-id', dest='backlog_id')
    x.add_argument('--closed-note', dest='closed_note')
    _pins(x, required=True)
    _leaf(s, 'withdraw', 'withdraw a story (the owner)').add_argument('id')

    m = _listed(sub, 'to-pm', 'list the messages to the PM; add, send, reply', S['to_pm'])
    x = _leaf(m, 'add', 'queue a message to the PM (the owner)')
    x.add_argument('--text', required=True)
    x.add_argument('--kind')
    _repo(x)
    x.add_argument('--urgent', action='store_true')
    _leaf(m, 'send', 'send every queued message (the owner)')
    x = _leaf(m, 'reply', 'answer a message the owner sent')
    x.add_argument('id')
    x.add_argument('reply', metavar='TEXT')
    _pins(x)

    i = _listed(sub, 'inbox', 'list the inbox; add, read', repo=False)
    x = _leaf(i, 'add', 'post a message for the owner')
    x.add_argument('--text', required=True)
    x.add_argument('--title')
    x.add_argument('--repo')
    x.add_argument('--ref', metavar='ID')
    x.add_argument('--asked')
    x.add_argument('--refs', nargs='+', metavar='ID')
    x.add_argument('--from', dest='who', metavar='NAME')
    x = _leaf(i, 'read', 'mark an inbox message read (the owner)')
    x.add_argument('id')
    x.add_argument('--undo', action='store_true')
    _pins(x)

    x = _asks(sub.add_parser('handoffs', help="each session's newest hand-off record; add: "
                                               "record yours"))
    x.add_argument('--from', dest='who', default=argparse.SUPPRESS, metavar='KEY',
                   help="only this session key's")
    h = x.add_subparsers(dest='sub')
    x = _leaf(h, 'add', 'record your hand-off, beside the ready reply to the PM')
    x.add_argument('--kind', required=True, choices=hubvalidate.HANDOFF_KINDS)
    x.add_argument('--state', required=True, metavar='PATH',
                   help='the absolute path of your state note')
    x.add_argument('--from', dest='who', required=True, metavar='KEY',
                   help='your launcher session key')

    g = _listed(sub, 'pages', 'list the published pages; publish, show, place')
    x = _leaf(g, 'publish', 'publish an HTML page to the hub')
    x.add_argument('id')
    x.add_argument('--title', required=True)
    x.add_argument('--html-file', dest='html_file', required=True, metavar='F',
                   help='a UTF-8 HTML file: no script, no remote URL, Mermaid rendered to svg')
    _repo(x)
    x.add_argument('--summary')
    x.add_argument('--refs', nargs='+', metavar='ID')
    x.add_argument('--kind', help='what kind of document it is (plan, spec, ...)')
    x.add_argument('--folder', help='the folder the Docs tab files it under')
    x.add_argument('--from', dest='who', metavar='NAME')
    _pins(x)
    x = _leaf(g, 'show', "a page's HTML")
    x.add_argument('id')
    x.add_argument('--out', metavar='F', help='write the HTML to this file instead')
    x = _leaf(g, 'place', "set a page card's kind, folder and repo; the body is unchanged")
    x.add_argument('id')
    x.add_argument('--kind')
    x.add_argument('--folder')
    _repo(x)
    _pins(x, required=True)

    e = _listed(sub, 'skill-edit', "list the owner's edits of kit skills; show; done: the "
                "text is committed on a branch; fail: it could not be", S['skill_edits'],
                repo=False)
    x = _leaf(e, 'show', "one edit; --out writes the owner's text to a file, byte for byte")
    x.add_argument('id')
    x.add_argument('--out', metavar='F', help="write the owner's text to this file")
    x = _leaf(e, 'done', 'mark a submitted edit committed: the hub checks the blob of the '
              "skill's SKILL.md on --branch against the owner's text")
    x.add_argument('id')
    x.add_argument('--branch', required=True, help='the branch the commit is on')
    x.add_argument('--checkout', default='.', metavar='DIR',
                   help='the checkout to read the branch in (default: .)')
    _pins(x, required=True)
    x = _leaf(e, 'fail', 'mark a submitted edit failed; with --at, superseded when the skill '
              'changed since the owner wrote it')
    x.add_argument('id')
    x.add_argument('--why', required=True, help='what stopped the commit')
    x.add_argument('--at', metavar='REV',
                   help="send the blob of the skill's SKILL.md at this revision (main): the "
                        'hub marks the edit superseded when it is not the one the owner edited')
    x.add_argument('--checkout', default='.', metavar='DIR',
                   help='the checkout to read --at in (default: .)')
    _pins(x, required=True)

    x = _listed(sub, 'approvals', "list the requests for the owner's approval; show; add: ask "
                "for one; apply: the owner has the hub apply an approved one again; withdraw: "
                "take back a pending one; supersede: mark a decided one replaced",
                tuple(S['approvals']) + (approvals.SUPERSEDED,))
    _leaf(x, 'show', 'one request and its files').add_argument('id')
    y = _leaf(x, 'add', "ask the owner to approve writing these files")
    y.add_argument('--checkout', default='.', metavar='DIR',
                   help='the git checkout the change is for (default: .)')
    y.add_argument('--title', required=True)
    y.add_argument('--file', dest='files', action='append', nargs=2, required=True,
                   metavar=('PATH', 'CONTENT_FILE'),
                   help='a path relative to the repo top and the UTF-8 file holding its new '
                        'text (repeatable)')
    _repo(y)
    y.add_argument('--head', metavar='SHA', help="the base commit (default: the checkout's HEAD)")
    y.add_argument('--reason')
    y.add_argument('--record', type=_record, metavar='REPO:ID',
                   help='the backlog record that waits on this request: the hub parks it until '
                        'the owner decides (checked by the hub)')
    y = _leaf(x, 'apply', 'the owner has the hub apply an approved change again')
    y.add_argument('id')
    _pins(y)
    _creator(_leaf(x, 'withdraw', 'take back a request still undecided'), 'KEY')
    y = _leaf(x, 'supersede', 'mark a decided approval nobody consumed superseded')
    y.add_argument('id')
    y.add_argument('--by', required=True, metavar='A-n|SHA',
                   help='the approval that replaces it, or a commit on main (40 hex)')
    y.add_argument('--from', dest='who', required=True, metavar='KEY',
                   help="pm, or a coordinator session of the approval's repo")
    _pins(y, required=True)

    x = _asks(sub.add_parser('pending', help='the owner input the PM has not handled'))
    x.add_argument('--for', dest='for_key', metavar='KEY',
                   help="only the items for this session key (default: every item)")
    w = _asks(sub.add_parser('wait', help='wait for a change of the store or a pending item'))
    w.add_argument('--since', type=_count, metavar='REV',
                   help='changes after this revision (default: the revision at the call)')
    w.add_argument('--timeout', type=_seconds, metavar='S',
                   help='exit 4 after this many seconds with nothing (default: no limit)')
    w.add_argument('--pending', action='store_true', help='pending items end the wait too')
    w.add_argument('--for', dest='for_key', metavar='KEY',
                   help="with --pending: only this session key's items end the wait, and no "
                        "other change does")
    w.add_argument('--except', dest='except_ids', nargs='+', metavar='ID',
                   help='with --pending: leave out these ids (ones already acted on); only '
                        'another pending item ends the wait')
    v = sub.add_parser('validate', help='check the store folder; reads it, no server needed')
    v.add_argument('--store', type=_folder, help='the store folder (default: the configured one)')


def parser():
    p = Parser(prog='hub.py', description='The hub server of this machine.')
    sub = p.add_subparsers(dest='verb')
    s = sub.add_parser('serve', help='serve the hub until Ctrl+C or hub.py stop')
    s.add_argument('--port', type=_port(0), help='the port (default: hub.port; 0: a free one)')
    s.add_argument('--store', type=_folder,
                   help='serve this folder as a scratch copy: git sync is off for the run, so '
                        'nothing in it is committed or pushed')
    t = sub.add_parser('status', help="the running server's health")
    t.add_argument('--port', type=_port(1), help='the port (default: hub.port)')
    u = sub.add_parser('setup', help='make the token files where they are absent; the machine '
                                     'config is not written')
    u.add_argument('--autostart', action='store_true',
                   help='also register the scheduled task that starts the server at sign-in')
    u.add_argument('--whatif', action='store_true',
                   help='with --autostart: print the task, change nothing (no token file either)')
    u.add_argument('--check', action='store_true',
                   help='write nothing: say whether the Access settings serve and which kit '
                        'copies would refuse the hub key')
    o = sub.add_parser('open', help='open the hub page, signed in as the owner')
    o.add_argument('--port', type=_port(1), help='the port (default: hub.port)')
    o.add_argument('--print', dest='show', action='store_true',
                   help='print the one-time link instead of opening it')
    n = sub.add_parser('unpair', help='end the session of every paired browser (the owner)')
    n.add_argument('--port', type=_port(1), help='the port (default: hub.port)')
    for name, what in (('stop', 'ask the running server to close cleanly, and wait (the owner)'),
                       ('restart', 'stop the server and start its scheduled task again (the '
                                   'owner)')):
        r = sub.add_parser(name, help=what)
        r.add_argument('--port', type=_port(1), help='the port (default: hub.port)')
        r.add_argument('--timeout', type=_seconds, default=control.DEFAULT_TIMEOUT_S, metavar='S',
                       help='give up after about this many seconds, ending nothing (default: '
                            '%d); a request in flight may run past it by up to %d s, a read of '
                            "the task's state by up to %d s"
                            % (control.DEFAULT_TIMEOUT_S, localask.ASK_TIMEOUT_S,
                               hub_autostart.TASK_TIMEOUT_S))
    _client_verbs(sub)
    return p
