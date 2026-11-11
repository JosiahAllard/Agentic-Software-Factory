"""A commit's message (kitlib.commit, whose docstring holds the rules): the body from -b or
--body-file, the trailers (the owner trailer, Co-Authored-By or Owner-Authored, the session line)
and the message built from them. Imports nothing from kitlib.commit."""
import os
import re

from kitlib import attribution


OWNER_AUTHORED_TRAILER = 'Owner-Authored: yes'


def canonical_approved(rules, owner_approved, paths):
    """Each --owner-approved value in the one spelling a trailer is written in
    (kitlib.config.owner, Rules.canonical): the committed path it names, or the blanket entry as
    configured; anything else as typed."""
    return [rules.canonical(value, paths) for value in owner_approved]


def build_trailers(rules, owner_approved, owner_authored, model, families=(), provider=None,
                   session=()):
    """`<owner trailer>: <value>` lines (the configured "owner".trailer), then Co-Authored-By on
    an agent's commit, or `Owner-Authored: yes` on the owner's own, then the `session` lines."""
    trailers = ['%s: %s' % (rules.trailer, o) for o in owner_approved]
    trailers.append(OWNER_AUTHORED_TRAILER if owner_authored else attribution.coauthor(model, provider, families))
    trailers.extend(session)
    return trailers


def lift_session_lines(body, keys):
    """`(body, lines)`: the `<key>: <value>` lines (a key of `keys`, any case; one value with no
    space) that END the body, taken off it. They go in the trailer block: left in the body, the
    blank line before the trailers keeps git from reading them as trailers (a `-b "...~~
    Claude-Session: <url>"`). A line anywhere else in the body stays where it is."""
    if not body or not keys:
        return body, []
    line_re = re.compile(r'(?:%s): \S+\Z' % '|'.join(re.escape(k) for k in keys), re.IGNORECASE)
    rows = body.rstrip().split('\n')
    lifted = []
    while rows and line_re.match(rows[-1].strip()):
        lifted.insert(0, rows.pop().strip())
    return '\n'.join(rows).rstrip(), lifted


def session_lines(body, owner_authored, env=None, provider=None):
    """`(body, session trailer lines)`: a body-final session line is lifted out of the body
    (lift_session_lines) and wins; otherwise an agent's commit gets the host's own
    (kitlib.attribution.session_trailer, from `env`, default os.environ). The owner's own commit
    gets none from the environment."""
    body, lifted = lift_session_lines(body, attribution.session_keys())
    if lifted or owner_authored:
        return body, lifted
    line = attribution.session_trailer(os.environ if env is None else env, provider)
    return body, [line] if line else []


def build_message(subject, body, trailers):
    parts = [subject]
    if body:
        parts.append(body)
    parts.append('\n'.join(trailers))
    return '\n\n'.join(parts) + '\n'


# The whole -b value is a path: drive-rooted, UNC or `/`-rooted with no whitespace, or
# drive-rooted and ending in a file extension (a Windows path may hold spaces), or a `%VAR%\` path
# with no whitespace. A drive-rooted value with spaces and no extension is prose and passes.
PATH_SHAPED = re.compile(r'(?:[A-Za-z]:[\\/]|\\\\|/|%[A-Za-z_][A-Za-z0-9_]*%[\\/])\S*'
                         r'|[A-Za-z]:[\\/].*\.[A-Za-z0-9]{1,8}')


def path_shaped_body(value, root, cwd):
    """The usage error when `-b`'s whole value is one line that is a path rather than a body, else
    None. Judged on the value as typed, before a typed `\\n` becomes a newline."""
    v = (value or '').strip()
    if not v or '\n' in v or '\r' in v:
        return None
    exists = os.path.isfile(os.path.join(cwd, v)) or os.path.isfile(os.path.join(root, v))
    if not exists and not PATH_SHAPED.fullmatch(v):
        return None
    return ('-b is the body TEXT, and %r %s. To take the body from a file, pass --body-file %s '
            'instead.' % (v, 'is an existing file' if exists else 'looks like an absolute path', v))


def read_body_file(path):
    """`(body, None)` from a --body-file, or `(None, usage error)`: UTF-8 with a BOM dropped, CRLF
    made LF, trailing whitespace and leading blank lines trimmed."""
    try:
        with open(path, 'rb') as fh:
            data = fh.read()
    except OSError as exc:
        return None, '--body-file %s could not be read: %s' % (path, exc.strerror or exc)
    try:
        text = data.decode('utf-8-sig')
    except UnicodeDecodeError as exc:
        return None, '--body-file %s is not UTF-8 (%s)' % (path, exc.reason)
    text = text.replace('\r\n', '\n').rstrip().lstrip('\n')
    if not text.strip():
        return None, '--body-file %s is empty' % path
    return text, None


def message_body(b, body_file, root, cwd):
    """`(body, None)` from -b or --body-file, or `(None, usage error)`. In -b a typed `\\n` and a
    `~~` each become a newline (`~~` survives shells that eat a backslash); a single `~` is kept."""
    if body_file is not None:
        return read_body_file(os.path.join(cwd, body_file))
    if not b:
        return '', None
    err = path_shaped_body(b, root, cwd)
    if err:
        return None, err
    return b.replace('\\n', '\n').replace('~~', '\n'), None
