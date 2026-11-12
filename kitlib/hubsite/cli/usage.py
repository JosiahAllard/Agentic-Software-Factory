"""kitlib.hubsite.cli.usage: tools/hub.py's verbs by name, one correct command for each (printed
under a usage error, and under the hub's refusal of a --record), and the checks of the values its
options take (--port, --store, the counts, --timeout, --choice, --option, --record). A check
returns the value the verb reads or raises ValueError with the words of the refusal; the tool's
parser (tools/_hub_args.py) turns that into its usage error, exit 2, before any verb runs.
Imports nothing from cli.
"""


# every verb, in the order the usage error names them
VERB_NAMES = ('serve', 'status', 'setup', 'open', 'unpair', 'stop', 'restart',
              'questions', 'answer', 'todos', 'stories', 'to-pm', 'inbox', 'pages', 'approvals',
              'handoffs', 'skill-edit', 'pending', 'wait', 'validate')


NO_VERB = 'give a verb: %s or %s' % (', '.join(VERB_NAMES[:-1]), VERB_NAMES[-1])


# One correct command per verb, printed under a usage error (and under the hub's refusal of a
# --record), keyed by the parser's prog. Each one parses: tests/hubsite/test_usage_examples.py.
EXAMPLES = {
    'hub.py serve': 'hub.py serve --port 0',
    'hub.py status': 'hub.py status',
    'hub.py setup': 'hub.py setup --check',
    'hub.py open': 'hub.py open --print',
    'hub.py unpair': 'hub.py unpair',
    'hub.py stop': 'hub.py stop',
    'hub.py restart': 'hub.py restart',
    'hub.py questions': 'hub.py questions --status open',
    'hub.py questions show': 'hub.py questions show Q-1000',
    'hub.py questions add': ('hub.py questions add --title "<title>" --context-file <file> '
                             '--option "<label>::<description>" --record <pm-folder>:<ID>'),
    'hub.py questions log': 'hub.py questions log Q-1000 --if-version 3',
    'hub.py questions done': 'hub.py questions done Q-1000 --why "<why>" --if-version 3',
    'hub.py questions file': 'hub.py questions file Q-1000',
    'hub.py questions bind': 'hub.py questions bind --dry-run',
    'hub.py questions edit': ('hub.py questions edit Q-1000 --from <key> --if-version 3 '
                              '--record <pm-folder>:<ID>'),
    'hub.py questions withdraw': 'hub.py questions withdraw Q-1000 --from <key> --if-version 3',
    'hub.py answer': 'hub.py answer Q-1000 --choice 0',
    'hub.py todos': 'hub.py todos --status open',
    'hub.py todos add': 'hub.py todos add --title "<title>"',
    'hub.py todos done': 'hub.py todos done T-1000',
    'hub.py todos show': 'hub.py todos show T-1000',
    'hub.py todos ack': 'hub.py todos ack T-1000',
    'hub.py todos edit': 'hub.py todos edit T-1000 --from <key> --if-version 3',
    'hub.py todos withdraw': 'hub.py todos withdraw T-1000 --from <key> --if-version 3',
    'hub.py stories': 'hub.py stories',
    'hub.py stories add': 'hub.py stories add --repo <repo> --title "<title>" --story-file <file>',
    'hub.py stories set': 'hub.py stories set S-1 --status review --if-version 3',
    'hub.py stories withdraw': 'hub.py stories withdraw S-1',
    'hub.py to-pm': 'hub.py to-pm',
    'hub.py to-pm add': 'hub.py to-pm add --text "<text>"',
    'hub.py to-pm send': 'hub.py to-pm send',
    'hub.py to-pm reply': 'hub.py to-pm reply P-1 "<reply>"',
    'hub.py handoffs': 'hub.py handoffs --from kit',
    'hub.py handoffs add': 'hub.py handoffs add --kind restart --state "<state file path>" '
                           '--from kit',
    'hub.py inbox': 'hub.py inbox',
    'hub.py inbox add': 'hub.py inbox add --text "<text>"',
    'hub.py inbox read': 'hub.py inbox read M-1',
    'hub.py pages': 'hub.py pages',
    'hub.py pages publish': 'hub.py pages publish <id> --title "<title>" --html-file <file>',
    'hub.py pages show': 'hub.py pages show <id>',
    'hub.py pages place': 'hub.py pages place <id> --kind plan --folder <folder> --if-version 2',
    'hub.py skill-edit': 'hub.py skill-edit --status submitted',
    'hub.py skill-edit show': 'hub.py skill-edit show E-1',
    'hub.py skill-edit done': 'hub.py skill-edit done E-1 --branch <branch> --if-version 3',
    'hub.py skill-edit fail': 'hub.py skill-edit fail E-1 --why "<why>" --if-version 3',
    'hub.py approvals': 'hub.py approvals',
    'hub.py approvals show': 'hub.py approvals show A-1',
    'hub.py approvals add': 'hub.py approvals add --title "<title>" --file <path> <content-file>',
    'hub.py approvals apply': 'hub.py approvals apply A-1',
    'hub.py approvals withdraw': 'hub.py approvals withdraw A-1 --from <key> --if-version 3',
    'hub.py approvals supersede': 'hub.py approvals supersede A-1 --by A-2 --from <key> '
                                  '--if-version 3',
    'hub.py pending': 'hub.py pending',
    'hub.py wait': 'hub.py wait --pending --timeout 600',
    'hub.py validate': 'hub.py validate',
}


def example_for(prog):
    """The example of the parser `prog`, or of the nearest verb above it; None when none."""
    words = prog.split()
    while words:
        found = EXAMPLES.get(' '.join(words))
        if found:
            return found
        words.pop()
    return None


def example_line(prog):
    found = example_for(prog)
    return '  example: %s\n' % found if found and len(prog.split()) > 1 else ''


def port(low):
    def in_range(text):
        try:
            n = int(text)
        except ValueError:
            n = -1
        if not low <= n <= 65535:
            raise ValueError('a port is a number from %d to 65535' % low)
        return n
    return in_range


def folder(text):
    """--store's value. An empty one is refused here: it must never mean "no --store", which
    serves the configured store with git sync on."""
    if not text.strip():
        raise ValueError('--store needs a folder, not an empty value')
    return text


def count(text):
    try:
        n = int(text)
    except ValueError:
        n = -1
    if n < 0:
        raise ValueError('a whole number from 0, not %r' % text)
    return n


def seconds(text):
    try:
        n = float(text)
    except ValueError:
        n = -1.0
    if not 0 <= n < float('inf'):
        raise ValueError('a number of seconds from 0, not %r' % text)
    return n


def choice(text):
    return text if text == 'other' else count(text)


def option(text):
    """questions add's --option value: "label::description"."""
    label, sep, description = text.partition('::')
    if not sep or not label.strip() or not description.strip():
        raise ValueError('an option is "label::description", not %r' % text)
    return {'label': label.strip(), 'description': description.strip()}


def record(text):
    """--record's value: "REPO:ID", split at the first ':' (a repository name holds none), as
    the {repo, id} the server checks against its configured repositories."""
    repo, sep, rid = text.partition(':')
    if not sep or not repo.strip() or not rid.strip():
        raise ValueError('a record is REPO:ID (a configured repository and one of its record '
                         'ids), not %r' % text)
    return {'repo': repo.strip(), 'id': rid.strip()}
