"""Quality scorecard: review_pack's quality signals over landed branches. Read-only on the repo.

usage: python <kit>/optional/usage/quality_scorecard.py <repo> [--base REF]
           (--since DATE [--until DATE] | --branches FILE | --merge SHA ...) [--out FILE.json]
       python <kit>/optional/usage/quality_scorecard.py --compare A.json [B.json]

--out adds to FILE, replacing earlier records of the same repo and cohort, so one file holds a whole
baseline. --compare prints the tables of one file, or of two side by side with a delta.
A landed branch is a first-parent merge on --base (default HEAD); its tip is the merge's second
parent, so a deleted branch still counts. Sync merges (SYNC) are skipped. Dates take a full
timestamp (2026-10-25T00:00:00); a bare date means that date at the current time of day. --branches reads
`name[:mergesha]` lines: a name alone is measured as unlanded, tip against its merge-base with --base.
Each branch is measured by kitlib.reviewpack.quality.section(), the lines review_pack prints. A git
call that fails or times out gives a NOT CHECKED row, never a pass. docs/model-ab.md "Branch quality
scorecard" has the plan.
"""
import argparse
import collections
import json
import os
import re
import statistics
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from kitlib.git import cmd as gitcmd  # noqa: E402
from kitlib.reviewpack import quality  # noqa: E402

TIMEOUT = 120
# A sync merge: "Merge X into Y" (`into` before any parenthesis, so a landing whose note says "into"
# stays one), "Merge <sha> ..." (a batch or the base merged in) and "Merge master|main ...".
SYNC = re.compile(r'^Merge [^(]*\binto \S|^Merge (?:[0-9a-f]{7,40}|master|main)\b')
KNOWN = ('size', '    ', '  SIGNAL', '  ... ', 'quality  NOT CHECKED')
_SIZE = re.compile(r'size\s+(\d+) non-test file\(s\) \| lines (\d+) -> (\d+) \| net ([+-]\d+)')
_NEW = re.compile(r'SIGNAL new file over \d+ non-test lines: (\S+)')
_GREW = re.compile(r'SIGNAL grew over .*?: (\S+) \d+ -> \d+')
_GONE = re.compile(r'SIGNAL deleted test, .*?: (\S+)')
SIGNALS = (('new file over %d lines' % quality.NEW_FILE_LINE, 'new'),
           ('grew over %d%% past %d lines' % (quality.GROWTH_PCT, quality.GROWTH_FLOOR), 'grew'),
           ('deleted test, no "covered by"', 'deleted_tests'))


def git(repo, *args):
    r = gitcmd.call(list(args), repo, timeout=TIMEOUT)
    if r.rc != 0:               # a failed git, or None: could not start or timed out
        raise RuntimeError((r.err.splitlines() or ['git %s failed' % args[0]])[0])
    return r.out.decode('utf-8', 'replace').strip()


def repo_name(repo):
    """The repository's folder name, never a worktree's."""
    common = os.path.abspath(os.path.join(repo, git(repo, 'rev-parse', '--git-common-dir')))
    return os.path.basename(os.path.dirname(common) if os.path.basename(common) == '.git' else common)


def landings(repo, base, since, until=None):
    """[(merge sha, date, subject)] of first-parent landings on `base`, newest first."""
    args = ['log', '--first-parent', '--merges', '--since=' + since, '--format=%H%x01%cs%x01%s']
    if until:
        args.append('--until=' + until)
    rows = (r.split('\x01') for r in git(repo, *args, base).splitlines())
    return [tuple(r) for r in rows if len(r) == 3 and not SYNC.search(r[2])]


def branch_name(subject):
    m = re.match(r"Merge (?:branch )?'?([^\s']+)", subject)
    return m.group(1) if m else subject


def parse(lines):
    """The record fields a section()'s lines give."""
    rec = {'size': None, 'new': [], 'grew': [], 'deleted_tests': [], 'not_checked': []}
    for l in lines:
        if not l.startswith(KNOWN) or (l.startswith('  SIGNAL') and not any(
                rx.search(l) for rx in (_NEW, _GREW, _GONE))):
            rec['not_checked'].append('unrecognised: ' + l.strip())      # never a silent pass
            continue
        m = _SIZE.search(l)
        if m:
            rec['size'] = dict(zip(('files', 'before', 'after', 'net'), map(int, m.groups())))
        for key, rx in (('new', _NEW), ('grew', _GREW), ('deleted_tests', _GONE)):
            m = rx.search(l)
            if m:
                rec[key].append(m.group(1))
        if 'NOT CHECKED' in l:
            rec['not_checked'].append(l.strip())
    return rec


def measure(repo, base, name, cohort, merge=None, date=''):
    """One branch's record. A landed branch gives `merge`; otherwise `name` is a ref."""
    rec = {'repo': '', 'cohort': cohort, 'branch': name, 'merge': merge or '', 'date': date,
           'landed': bool(merge)}
    try:
        rec['repo'] = repo_name(repo)
        if merge:
            parent, tip = git(repo, 'rev-parse', merge + '^1', merge + '^2').split()
        else:
            parent, tip = base, name
        mb = git(repo, 'merge-base', parent, tip)
        lines = quality.section(lambda *a: git(repo, *a), repo, mb, tip)
    except Exception as e:      # noqa: BLE001 - a row, never a pass
        lines = [quality.not_checked('branch', e)]
    rec.update(parse(lines))
    return rec


def collect(repo, base, since, until, branches, merges):
    if branches:
        out = []
        for l in open(branches, encoding='utf-8'):
            name, _, sha = l.strip().partition(':')
            if name and not name.startswith('#'):
                out.append(measure(repo, base, name, 'spot', sha or None))
        return out
    if merges:
        return [measure(repo, base, branch_name(git(repo, 'log', '-1', '--format=%s', m)), 'merge',
                        git(repo, 'rev-parse', m), git(repo, 'log', '-1', '--format=%cs', m)) for m in merges]
    return [measure(repo, base, branch_name(s), 'window', m, d)
            for m, d, s in landings(repo, base, since, until)]


def value(recs, key):
    """(number, text) of one signal over `recs`: a share of branches in percent, or net lines."""
    nets = sorted(r['size']['net'] for r in recs if r['size'])
    if key in ('net', 'p90'):
        if not nets:
            return None, '-'
        v = statistics.median(nets) if key == 'net' else nets[int(.9 * (len(nets) - 1))]
        return v, '%d' % v
    n = sum(1 for r in recs if r[key])
    return (100.0 * n / len(recs) if recs else None), '%d of %d (%.1f%%)' % (n, len(recs), 100.0 * n / max(len(recs), 1))


def table(sets):
    """Text tables, one per signal, over {label: records}. Two labels add a delta column: percentage
    points for a share, lines for a net (counts are not compared, the branch counts differ)."""
    cohorts = sorted({(r['repo'], r['cohort']) for rs in sets.values() for r in rs})
    head = '  %-38s' % 'cohort' + ''.join('%26s' % l[:25] for l in sets) + ('%10s' % 'delta' if len(sets) == 2 else '')
    out = []
    for title, key in SIGNALS + (('median net lines', 'net'), ('p90 net lines', 'p90'),
                                 ('NOT CHECKED', 'not_checked')):
        out += ['', title, head]
        for repo, cohort in cohorts:
            vals = [value([r for r in rs if (r['repo'], r['cohort']) == (repo, cohort)], key)
                    for rs in sets.values()]
            row = '  %-38s' % ('%s %s' % (repo, cohort)) +''.join('%26s' % t for _, t in vals)
            if len(vals) == 2:
                (a, _), (b, _) = vals
                row += '%10s' % ('%+.0f' % (b - a) if a is not None and b is not None else '-')
            out.append(row)
    return '\n'.join(out)


def save(path, recs):
    """Write `recs` to `path`, replacing earlier records of the same repo and cohort there, so one
    file can hold several repos and cohorts."""
    old = json.load(open(path, encoding='utf-8')) if os.path.exists(path) else []
    mine = {(r['repo'], r['cohort']) for r in recs}
    with open(path, 'w', encoding='utf-8') as f:
        json.dump([r for r in old if (r['repo'], r['cohort']) not in mine] + recs, f, indent=1)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('repo', nargs='?')
    ap.add_argument('--base', default='HEAD')
    ap.add_argument('--since')
    ap.add_argument('--until')
    ap.add_argument('--branches')
    ap.add_argument('--merge', action='append')
    ap.add_argument('--out')
    ap.add_argument('--compare', nargs='+', metavar='FILE')
    a = ap.parse_args(argv)
    if a.compare:
        sets = collections.OrderedDict((os.path.basename(p), json.load(open(p, encoding='utf-8')))
                                       for p in a.compare)
    elif a.repo and (a.since or a.branches or a.merge):
        recs = collect(a.repo, a.base, a.since, a.until, a.branches, a.merge)
        if a.out:
            save(a.out, recs)
        sets = {'now': recs}
    else:
        ap.error('give <repo> with --since, --branches or --merge, or --compare A B')
    print(table(sets))
    return 0


if __name__ == '__main__':
    sys.exit(main())
