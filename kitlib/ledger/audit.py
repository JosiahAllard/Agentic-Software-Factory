"""The whole-ledger check `ledger.py check` prints (docs/ledger.md "Check"): every readable file
validated and checked append-only, the standing digest against all of them, and the rulebooks.

Returns the lines to print and the exit code; it prints nothing itself.
"""
import collections
import os

from . import files, read, rulebook, standing, vcs, write

# lines: what to print, in order. problems: the failure count (exit 1 when not 0).
Checked = collections.namedtuple('Checked', 'lines problems')


def rulebook_findings(found, texts):
    """`(lines for check to print, error count, rule count, book count)`: every rulebook present
    parsed, then checked against the ids of the log `texts`."""
    if not found.rules:
        return [], 0, 0, 0
    books, errs = rulebook.load(found.rules)
    ids = {e.id for text in texts for e in read.parse(text)}
    got = rulebook.check(books, ids)
    errs = errs + got.errors + rulebook.applied(books, texts)
    out = ['FAIL ' + e for e in errs] + ['note: ' + n for n in got.notes]
    return out, len(errs), sum(len(b.rules) for b in books.values()), len(books)


def check_one(label, text, old, repos, repo, others):
    """The check's findings for one file, each prefixed with `label` when it is set."""
    errs = write.check(text, old, repos, repo, others)
    return ['%s: %s' % (label, e) for e in errs] if label else errs


def check_all(found, repos, base=None):
    """Every readable file validated and checked append-only since `base` (default: each file's
    repository's remote main); the digest against all of them. A Checked."""
    texts, errs, unchecked = [], [], []
    present = []
    for r, path in files.readable(found):
        if found.split and not os.path.exists(path):
            if r is None:                    # a repo's file is new until its first entry
                errs.append('the frozen ledger %s (ledger_path) is not there' % path)
            continue
        present.append((r, path, write.read_text(path)))
    for r, path, text in present:
        texts.append(text)
        at = base or vcs.default_base(path)
        old, why = vcs.base_text(path, at, new_ok=r is not None)
        if old is None:
            unchecked.append((path, at, why))
        label = os.path.basename(path) if found.split else None
        others = [t for _, p, t in present if p != path] if r is not None else ()
        # The frozen (or one-file) ledger keeps its own repo columns (`all` among them); a repo's
        # file holds that repo only.
        errs += check_one(label, text, old, None if (found.split and r is None) else repos, r, others)
    digest, dwhy = standing.read_digest(found.digest)
    errs += standing.problems(texts, digest)
    if dwhy:
        errs.append('cannot read the standing digest: %s' % dwhy)
    lines = ['FAIL ' + e for e in errs]
    book_lines, book_errs, rules, books = rulebook_findings(found, texts)
    lines += book_lines
    for path, at, why in unchecked:
        lines.append('append-only NOT CHECKED: cannot read %s at %s (%s)'
                     % (os.path.basename(path) if found.split else 'the ledger', at,
                        ' '.join((why or '').split())))
    entries = sum(write.count(t) for t in texts)
    problems = len(errs) + book_errs
    verdict = 'OK' if not problems else '%d problem(s)' % problems
    if found.split:
        lines.append('ledger: %d entries in %d files, %s' % (entries, len(texts), verdict))
    else:
        lines.append('ledger: %d entries, %s' % (entries, verdict))
    if books:
        lines.append('rulebooks: %d rules in %d files' % (rules, books))
    return Checked(lines, problems)
