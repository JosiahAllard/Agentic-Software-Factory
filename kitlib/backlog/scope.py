"""The scope language and collision (ported in behaviour from an earlier tools/backlog.py). A
scope member is `none`, a literal repo-relative path, or a path with a `/**` suffix. A comparison
that cannot be decided raises UndecidableScope and is a collision to every caller; nothing here
returns "clear" for an input it did not understand."""
import re

NONE = 'none'
WILDCARD_CHARS = '*?[]{}'
GLOB_SUFFIX = '/**'


class UndecidableScope(Exception):
    """A comparison against a member this grammar cannot parse, or an empty scope set."""


def path_shape_error(p, what='path', tab_ok=False, comma_ok=False):
    """None if `p` is a repo-root-relative forward-slash path, else why not. `tab_ok`: a tab inside
    the path is an ordinary character - for reading members a record already holds (collision,
    the offer), so an old tab blocks no session; a write's new tab is still a `check` finding.
    `comma_ok`: a comma is an ordinary character - for a literal changed path that is never
    written as a record line (kitlib.gate.land's scope check)."""
    if not p:
        return 'an empty %s' % what
    if p != p.strip():
        return 'a %s with leading or trailing whitespace' % what
    if any((ord(ch) < 32 and not (tab_ok and ch == '\t')) or ord(ch) == 127 for ch in p):
        return ('a control character in the %s %r - a line break would split a record line'
                % (what, p))
    if ',' in p and not comma_ok:
        return 'a comma in a %s - a set is one `%s:` line per member' % (what, what)
    if '\\' in p:
        return 'a backslash in a %s - paths are forward-slash, repo-root relative' % what
    if p.startswith('/'):
        return 'an absolute %s - paths are repo-root relative' % what
    if re.match(r'^[A-Za-z]:', p):
        return 'a drive letter in a %s - paths are repo-root relative' % what
    for part in p.split('/'):
        if part == '':
            return 'an empty path component in `%s` (a doubled or trailing slash)' % p
        if part in ('.', '..'):
            return 'a `%s` component in `%s` - paths do not walk upwards' % (part, p)
    return None


def scope_member_error(member, tab_ok=False):
    """None if `member` is a legal scope member, else why not (`tab_ok`: see path_shape_error)."""
    if member == NONE:
        return None
    if not member:
        return 'an empty scope'
    if member.casefold() == NONE:
        return '`%s` casefolds into the `none` sentinel but is not it; write `none`' % member
    body = member
    if body.endswith(GLOB_SUFFIX):
        body = body[:-len(GLOB_SUFFIX)]
        if not body:
            return '`%s` has no prefix - a prefix glob is `<path>/**`' % member
    if any(ch in WILDCARD_CHARS for ch in body):
        return ('`%s` is not a scope: the only wildcard is a trailing `/**`; other globs are '
                'parse errors, not patterns' % member)
    return path_shape_error(body, 'scope', tab_ok)


def scope_components(member):
    """The casefolded component list `members_collide` compares."""
    err = scope_member_error(member, tab_ok=True)
    if err is not None:
        raise UndecidableScope('%s: %s' % (member, err))
    if member == NONE:
        raise UndecidableScope('`none` has no components; it collides with nothing')
    body = member[:-len(GLOB_SUFFIX)] if member.endswith(GLOB_SUFFIX) else member
    return body.casefold().strip('/').split('/')


def members_collide(a, b):
    """True iff one member's component list is a prefix of the other's. Component-wise, so
    `Foo.java` and `Foobar.java` are clear; over-approximate by design. A tab a member already
    holds is an ordinary character here."""
    for m in (a, b):
        err = scope_member_error(m, tab_ok=True)
        if err is not None:
            raise UndecidableScope('%s: %s' % (m, err))
    if a == NONE or b == NONE:
        return False
    ca, cb = scope_components(a), scope_components(b)
    n = min(len(ca), len(cb))
    return ca[:n] == cb[:n]


def scopes_collide(scopes_a, scopes_b):
    """True iff any member of one set collides with any member of the other. Every member is
    validated, even after a hit; an empty set raises."""
    for name, scopes in (('a', scopes_a), ('b', scopes_b)):
        if not scopes:
            raise UndecidableScope('scope set %s is empty; an item that touches no tracked file '
                                   'says `scope: none`' % name)
    hit = False
    for a in scopes_a:
        for b in scopes_b:
            if members_collide(a, b):
                hit = True
    return hit


def colliding_pairs(scopes_a, scopes_b):
    return [(a, b) for a in scopes_a for b in scopes_b if members_collide(a, b)]


COMPLEMENT_CAP = 30     # members `complement` returns at most; more is "cannot auto-narrow"


def _related(a, b):
    """`members_collide` on two paths without validating them: a tracked path such as
    `pages/[id].js` or `a,b.txt` is not a legal scope member but is a real file."""
    ca, cb = a.casefold().split('/'), b.casefold().split('/')
    n = min(len(ca), len(cb))
    return ca[:n] == cb[:n]


def complement(member, removed, tree, cap=COMPLEMENT_CAP):
    """The sorted sibling members that cover `member` minus the repo paths in `removed`, over
    `tree` (the tracked paths at the code main; the caller supplies it): `dir/**` for a sibling
    folder with nothing removed under it, a file by name for a sibling file, descent into a
    folder that holds a removed path. `member` is a folder, a `dir/**` glob or a file. It is
    returned as it is (a one-member list) when no removed path collides with it. None when the
    result would exceed `cap` members, or when `member` is a single file holding a removed path
    (a literal .md included), or when `tree` has nothing for it (empty tree, letter-case
    mismatch): the caller cannot auto-narrow. `[]` means every tracked file under the member
    was removed; test `is None`, never `not out`. Collision is `members_collide`."""
    base = member[:-len(GLOB_SUFFIX)] if member.endswith(GLOB_SUFFIX) else member
    if member == NONE or not any(_related(base, r) for r in removed):
        return [member]
    below = base + '/'
    under = [p for p in tree if _related(base, p)]
    if not under or any(not (p == base or p.startswith(below)) for p in under):
        return None         # no tracked data for it, or a letter-case mismatch: do not guess
    if not member.endswith(GLOB_SUFFIX) and not any(p.startswith(below) for p in tree):
        return None         # a single file
    out = []

    def descend(prefix):
        names = {}
        for p in tree:
            if p.startswith(prefix + '/'):
                head, _, rest = p[len(prefix) + 1:].partition('/')
                names[head] = names.get(head, False) or bool(rest)
        for name in sorted(names):
            path = prefix + '/' + name
            if names[name]:
                if any(_related(path, r) for r in removed):
                    descend(path)
                else:
                    out.append(path + GLOB_SUFFIX)
            elif not any(_related(path, r) for r in removed):
                out.append(path)
            if len(out) > cap:
                return

    descend(base)
    return None if len(out) > cap else sorted(out)


def area_from_scope(scopes):
    """`area` is the first component of the first scope that is not `none`, else `none`."""
    for s in scopes:
        if s != NONE:
            return s.split('/')[0]
    return NONE
