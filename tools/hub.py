"""hub.py: the hub server of this machine (kitlib.hubsite) and the verbs that ask it.

  hub.py serve | status | setup | open | unpair | stop | restart
  hub.py questions | answer | todos | stories | to-pm | inbox | pages | approvals | handoffs
         | skill-edit | pending | wait | validate

What each verb does and prints: kitlib/hubsite/cli/ (its package docstring lists the verbs by
module, with the shared --port, --json and --if-version rules and the exit codes; each module's
docstring has its verbs); `hub.py <verb> --help` gives each verb's options. The parser is
tools/_hub_args.py.

A thin entry point (ARCHITECTURE.md, Layers): it parses, calls kitlib.hubsite.cli with `say`,
the one writer of its lines, and exits with the code the verb returns. Exit codes: 0 ok;
1 refused; 2 usage error or a machine config that cannot be read; 3 the server is not running
(the start command is printed), or `restart` started nothing; 4 a wait, stop or restart ran out
of time (kitlib/hubsite/cli/__init__.py).
"""
import functools
import json
import os
import sys

TOOLS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(TOOLS))
if TOOLS not in sys.path:       # loaded by path (a test, the server's self-check): _hub_args, _host
    sys.path.insert(1, TOOLS)

import _hub_args                                                   # noqa: E402
from kitlib import hubjobs                                         # noqa: E402
from kitlib.config.types import ConfigError                        # noqa: E402
from kitlib.hubclient import client                                # noqa: E402
from kitlib.hubclient import errors as huberrors                   # noqa: E402
from kitlib.hubsite.cli import approvals, asks, common, pages, server, setup   # noqa: E402

INTERRUPTED = 'interrupted'             # Ctrl+C anywhere else (a verb, or serve before it served)


def say(text, err=False):
    """One line to stdout, or stderr with `err`; a character the console cannot show is written
    as its escape. A console that was closed while the server served must not change how the
    server ends: the line is lost, nothing else."""
    stream = sys.stderr if err else sys.stdout
    try:
        try:
            print(text, file=stream, flush=True)
        except UnicodeEncodeError:
            enc = getattr(stream, 'encoding', None) or 'ascii'
            print(str(text).encode(enc, 'backslashreplace').decode(enc), file=stream, flush=True)
    except (OSError, ValueError):
        pass


def _adapter_module():
    """The host adapter's module (tools/_host.py), for the server's push notifications."""
    import _host
    return _host.adapter_module()


VERBS = {'serve': functools.partial(server.serve, machine_sources=hubjobs.machine_sources,
                                    adapter_module=_adapter_module),
         'status': server.status, 'setup': setup.setup, 'open': server.open_page,
         'unpair': server.unpair, 'stop': server.stop, 'restart': server.restart,
         'questions': asks.questions, 'answer': asks.answer, 'todos': asks.todos,
         'stories': asks.stories, 'to-pm': asks.to_pm, 'inbox': asks.inbox, 'pages': pages.pages,
         'approvals': approvals.approvals, 'handoffs': asks.handoffs,
         'skill-edit': pages.skill_edit, 'pending': asks.pending,
         'wait': asks.wait, 'validate': setup.validate}


def main(argv=None, stop=None):
    """`stop`: a threading.Event that ends `serve` when set (default: Ctrl+C only). The
    `machine config:` and `REFUSED` lines go through gitsync.scrub, as the server's log lines
    do: a reason may quote a remote URL."""
    p = _hub_args.parser()
    a = p.parse_args(argv)
    if not a.verb:
        p.error(_hub_args.NO_VERB)
    if a.verb == 'setup' and a.check and (a.autostart or a.whatif):
        p.error('--check takes no other option: it writes nothing')
    if a.verb == 'setup' and a.whatif and not a.autostart:
        p.error('--whatif needs --autostart')
    if a.verb == 'wait' and a.for_key and not a.pending:
        p.error('--for picks among the pending items: give --pending with it')
    if a.verb == 'wait' and a.except_ids and not a.pending:
        p.error('--except leaves out pending items: give --pending with it')
    try:
        return VERBS[a.verb](a, say, stop)
    except ConfigError as e:
        say(common.scrub('machine config: %s' % e), err=True)
        return client.USAGE
    except huberrors.NotRunning as e:
        say('%s; start it with: %s' % (e, common.START), err=True)
        return client.exit_code(e)
    except client.Refused as e:         # the server's own refusal: its code, then its words
        if getattr(a, 'json', False):
            say(common.scrub(json.dumps(e.body, sort_keys=True)))
        say(common.scrub('REFUSED %s: %s' % (e.code, ' '.join(e.message.split()))), err=True)
        if e.field == 'record':         # a --record the hub would not take: the form that it does
            sys.stderr.write(_hub_args.example_line('hub.py %s %s'
                                                    % (a.verb, getattr(a, 'sub', None) or '')))
        return client.exit_code(e)
    except common.REFUSED_ERRORS as e:
        say(common.scrub('REFUSED: %s' % e), err=True)
        return client.REFUSED
    except KeyboardInterrupt:           # not in a server's close: server.serve answers that one
        say(INTERRUPTED, err=True)
        return 1


if __name__ == '__main__':
    sys.exit(main())
