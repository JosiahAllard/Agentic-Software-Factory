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
    return member_base(member).casefold().strip('/').split('/')


def shared_path_error(path):
    """None when `path` may be a `.kit.json` "backlog".shared_paths entry: one literal file, a
    repo-root-relative path with no glob and no wildcard character; else why not. Whether it names
    a folder in the tree is the caller's to ask (opener.repo_shared_paths). Pure."""
    if not isinstance(path, str):
        return 'not a string'
    if path.endswith(GLOB_SUFFIX) or any(ch in WILDCARD_CHARS for ch in path):
        return '`%s` is a glob: a shared path is one literal file' % path
    if path.endswith('/'):
        return '`%s` is a folder: a shared path is one literal file' % path
    if path.casefold() == NONE:
        return '`%s` reads as the `none` sentinel' % path
    return path_shape_error(path, 'shared path')


def is_shared(member, shared):
    """True when scope member `member` IS one of the shared paths `shared` (casefolded, as members
    compare): a literal member, never a glob and never `none`."""
    if not shared or member == NONE or member.endswith(GLOB_SUFFIX):
        return False
    folded = member.casefold()
    return any(folded == s.casefold() for s in shared)


def members_collide(a, b, shared=()):
    """True iff one member's component list is a prefix of the other's. Component-wise, so
    `Foo.java` and `Foobar.java` are clear; over-approximate by design. A tab a member already
    holds is an ordinary character here. `shared` (`.kit.json` "backlog".shared_paths): a pair
    where either member IS a shared path does not collide (`x/**` against a shared `x/f`
    included); both members are still validated first, and `shared` is only asked of a pair that
    collides without it (a lazy `shared` is read only then)."""
    for m in (a, b):
        err = scope_member_error(m, tab_ok=True)
        if err is not None:
            raise UndecidableScope('%s: %s' % (m, err))
    if a == NONE or b == NONE:
        return False
    ca, cb = scope_components(a), scope_components(b)
    n = min(len(ca), len(cb))
    if ca[:n] != cb[:n]:
        return False
    return not (is_shared(a, shared) or is_shared(b, shared))


def scopes_collide(scopes_a, scopes_b, shared=()):
    """True iff any member of one set collides with any member of the other. Every member is
    validated, even after a hit; an empty set raises. `shared`: as members_collide."""
    for name, scopes in (('a', scopes_a), ('b', scopes_b)):
        if not scopes:
            raise UndecidableScope('scope set %s is empty; an item that touches no tracked file '
                                   'says `scope: none`' % name)
    hit = False
    for a in scopes_a:
        for b in scopes_b:
            if members_collide(a, b, shared):
                hit = True
    return hit


def colliding_pairs(scopes_a, scopes_b, shared=()):
    return [(a, b) for a in scopes_a for b in scopes_b if members_collide(a, b, shared)]


def shared_hits(scopes_a, scopes_b, shared):
    """The shared paths a pair exempted by `shared` meets on: for each `(a, b)` that collides
    without `shared` and not with it, the member of the two that is a shared path (`a` when both
    are), each once, in order. A member the grammar cannot read raises UndecidableScope. `shared`
    is asked only of a pair that collides without it."""
    out = []
    for a in scopes_a:
        for b in scopes_b:
            if members_collide(a, b) and (is_shared(a, shared) or is_shared(b, shared)):
                path = a if is_shared(a, shared) else b
                if path not in out:
                    out.append(path)
    return out


COMPLEMENT_CAP = 30     # members `complement` returns at most; more is "cannot auto-narrow"


def member_base(member):
    """`member` without its `/**` glob suffix: the folder a glob covers, else the member."""
    return member[:-len(GLOB_SUFFIX)] if member.endswith(GLOB_SUFFIX) else member


def paths_related(a, b):
    """`members_collide` on two paths without validating them: a tracked path such as
    `pages/[id].js` or `a,b.txt` is not a legal scope member but is a real file."""
    ca, cb = a.casefold().split('/'), b.casefold().split('/')
    n = min(len(ca), len(cb))
    return ca[:n] == cb[:n]


def path_within(base, path):
    """`path` is `base` or lies under it, spelled exactly (letter case included): what a tracked
    path must be to count as inside a member's base."""
    return path == base or path.startswith(base + '/')


def strictly_inside(inner, outer):
    """Member `inner` lies inside member `outer`'s `/**` glob and is not `outer` itself.
    UndecidableScope when either is not a legal member."""
    ci, co = scope_components(inner), scope_components(outer)
    return outer.endswith(GLOB_SUFFIX) and len(ci) > len(co) and ci[:len(co)] == co


def inside_bare_folder(inner, outer):
    """Member `outer` is a bare path (no `/**`) and member `inner` lies strictly below it, so
    `outer` is a folder that covers `inner` (members collide by component prefix). Narrowing
    reads such an `outer` as broad (offer._split, for NARROWS only; NARROW's offer.is_broad
    does not); `strictly_inside`, which the commit side reads, stays the glob's alone.
    UndecidableScope when either is not a legal member."""
    ci, co = scope_components(inner), scope_components(outer)
    return not outer.endswith(GLOB_SUFFIX) and len(ci) > len(co) and ci[:len(co)] == co


def deeper_member(a, b):
    """Of two colliding members, the one inside the other (`a` when they are as deep)."""
    return a if len(scope_components(a)) >= len(scope_components(b)) else b


def complement(member, removed, tree, cap=COMPLEMENT_CAP):
    """The sorted sibling members that cover `member` minus the repo paths in `removed`, over
    `tree` (the tracked paths at the code main; the caller supplies it): `dir/**` for a sibling
    folder with nothing removed under it, a file by name for a sibling file, descent into a
    folder that holds a removed path. `member` is a folder, a `dir/**` glob or a file. It is
    returned as it is (a one-member list) when no removed path collides with it. None when the
    result would exceed `cap` members, or when `member` is a single file holding a removed path
    (a literal .md included), or when `tree` has nothing for it (empty tree, letter-case
    mismatch): the caller cannot auto-narrow. `[]` means every tracked file under the member
    was removed; test `is None`, never `not out`. A malformed `member`, or a `removed` entry that
    is not a plain repo path, raises UndecidableScope; `tree` is not checked."""
    err = scope_member_error(member, tab_ok=True)
    if err is not None:
        raise UndecidableScope('%s: %s' % (member, err))
    for r in removed:
        err = ('a glob, not a path' if r.endswith(GLOB_SUFFIX)
               else path_shape_error(r, 'removed path', tab_ok=True, comma_ok=True))
        if err is not None:
            raise UndecidableScope('%s: %s' % (r, err))
    base = member_base(member)
    if member == NONE or not any(paths_related(base, r) for r in removed):
        return [member]
    below = base + '/'
    under = [p for p in tree if paths_related(base, p)]
    if not under or any(not path_within(base, p) for p in under):
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
                if any(paths_related(path, r) for r in removed):
                    descend(path)
                else:
                    out.append(path + GLOB_SUFFIX)
            elif not any(paths_related(path, r) for r in removed):
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


# --- a changed path against a record's scope (the landing scope check and commit.py's
# narrowed-record check share these: kitlib.gate.land, kitlib.backlog.narrowed) ----------------

def changed_path_error(path):
    """None when a scope can be said to cover the literal changed path `path`, else why not:
    such a path is covered by no member. A comma or a wildcard character is an ordinary
    character in a changed path. Pure."""
    why = path_shape_error(path, 'path', tab_ok=True, comma_ok=True)
    if why:
        return why
    if path.casefold() == NONE:
        return 'a path that reads as the `none` sentinel'
    return None


def covers(members, path):
    """True when a scope member in `members` covers the literal changed path `path`: one's
    components a prefix of the other's, casefolded (members_collide's comparison). Only the
    member is read in the scope grammar. A member the grammar cannot read, `none`, and a path
    changed_path_error refuses cover nothing. Pure."""
    if changed_path_error(path):
        return False
    have = path.casefold().split('/')
    for m in members:
        try:
            want = scope_components(m)
        except UndecidableScope:
            continue
        n = min(len(want), len(have))
        if want[:n] == have[:n]:
            return True
    return False


def area_lead(area, tops):
    """The area prefix a member of a record carries, `<area>/`, or '' when it carries none: the
    store shared by repositories (the PM data branch's) writes a member `<area>/<path>`; `area`
    is None for a record of any other store, and an area that names a top-level entry of the
    repository (`tops`, casefolded) is a path. Pure."""
    return area + '/' if area and area.casefold() not in tops else ''


def in_repo(members, area, tops):
    """`members`, a record's scope, as paths of this repository: under an area prefix
    (area_lead) a member loses that first component; every other member is as it is. Pure."""
    if not area_lead(area, tops):
        return list(members)
    out = []
    for m in members:
        head, _, rest = m.partition('/')
        out.append(rest if rest and head.casefold() == area.casefold() else m)
    return out
