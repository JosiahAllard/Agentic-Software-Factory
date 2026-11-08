"""Quality scorecard: review_pack's quality signals over landed branches. Read-only on the repo.

usage: python <kit>/optional/usage/quality_scorecard.py <repo> [--base REF]
           (--since DATE [--until DATE] | --branches FILE | --merge SHA ...) [--out FILE.json]
       python <kit>/optional/usage/quality_scorecard.py --compare A.json B.json

A landed branch is a first-parent merge on --base (default HEAD); its tip is the merge's second
parent, so a deleted branch still counts. "Merge X into Y" sync merges are skipped. --branches reads
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
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from kitlib.reviewpack import quality  # noqa: E402

TIMEOUT = 120
SYNC = re.compile(r' into \S|^Merge [0-9a-f]{7,40}\b')
_SIZE = re.compile(r'size\s+(\d+) non-test file\(s\) \| lines (\d+) -> (\d+) \| net ([+-]\d+)')
_NEW = re.compile(r'SIGNAL new file over \d+ non-test lines: (\S+)')
_GREW = re.compile(r'SIGNAL grew over .*?: (\S+) \d+ -> \d+')
_GONE = re.compile(r'SIGNAL deleted test, .*?: (\S+)')
SIGNALS = (('new file over %d lines' % quality.NEW_FILE_LINE, 'new'),
           ('grew over %d%% past %d lines' % (quality.GROWTH_PCT, quality.GROWTH_FLOOR), 'grew'),
           ('deleted test, no "covered by"', 'deleted_tests'))


def git(repo, *args):
    p = subprocess.run(['git', '-C', repo] + list(args), capture_output=True, text=True,
                       encoding='utf-8', errors='replace', timeout=TIMEOUT)
    if p.returncode:
        raise RuntimeError((p.stderr.strip().splitlines() or ['git ' + args[0] + ' failed'])[0])
    return p.stdout.strip()


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
    rec = {'repo': os.path.basename(os.path.abspath(repo)), 'cohort': cohort, 'branch': name,
           'merge': merge or '', 'date': date, 'landed': bool(merge)}
    try:
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
    return [measure(repo, base, branch_name(s), since + ('..' + until if until else ''), m, d)
            for m, d, s in landings(repo, base, since, until)]


def cell(recs, key):
    return sum(1 for r in recs if r[key])


def table(sets):
    """Text tables, one per signal, over {label: records}; `sets` has one label, or two to compare."""
    cohorts = sorted({(r['repo'], r['cohort']) for rs in sets.values() for r in rs})
    labels = list(sets)
    head = ['  %-34s' % 'cohort'] + ['%16s' % l for l in labels]
    if len(labels) == 2:
        head.append('%8s' % 'delta')
    out = []
    for title, key in SIGNALS + (('median net lines', 'net'), ('p90 net lines', 'p90'),
                                 ('NOT CHECKED', 'not_checked')):
        out += ['', title, ''.join(head)]
        for repo, cohort in cohorts:
            vals = []
            for rs in sets.values():
                rs = [r for r in rs if (r['repo'], r['cohort']) == (repo, cohort)]
                nets = sorted(r['size']['net'] for r in rs if r['size'])
                if key in ('net', 'p90'):
                    v = ('%d' % (statistics.median(nets) if key == 'net' else nets[int(.9 * (len(nets) - 1))])
                         if nets else '-')
                else:
                    v = '%d of %d' % (cell(rs, key), len(rs))
                vals.append(v)
            row = '  %-34s' % ('%s %s' % (repo, cohort))[:34] + ''.join('%16s' % v for v in vals)
            if len(vals) == 2:
                a, b = (re.match(r'-?\d+', v) for v in vals)
                row += '%8s' % ('%+d' % (int(b.group()) - int(a.group())) if a and b else '-')
            out.append(row)
    return '\n'.join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('repo', nargs='?')
    ap.add_argument('--base', default='HEAD')
    ap.add_argument('--since')
    ap.add_argument('--until')
    ap.add_argument('--branches')
    ap.add_argument('--merge', action='append')
    ap.add_argument('--out')
    ap.add_argument('--compare', nargs=2, metavar=('A', 'B'))
    a = ap.parse_args(argv)
    if a.compare:
        sets = collections.OrderedDict((os.path.basename(p), json.load(open(p, encoding='utf-8')))
                                       for p in a.compare)
    elif a.repo and (a.since or a.branches or a.merge):
        recs = collect(a.repo, a.base, a.since, a.until, a.branches, a.merge)
        if a.out:
            with open(a.out, 'w', encoding='utf-8') as f:
                json.dump(recs, f, indent=1)
        sets = {'now': recs}
    else:
        ap.error('give <repo> with --since, --branches or --merge, or --compare A B')
    print(table(sets))
    return 0


if __name__ == '__main__':
    sys.exit(main())
