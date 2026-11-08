"""quality_scorecard.py: review_pack's quality signals over landed branches (docs/model-ab.md
"Branch quality scorecard"). Read-only on the repository.

  quality_scorecard.py <repo> [--base REF] (--since DATE [--until DATE] | --branches FILE |
                       --merge SHA ...) [--out FILE.json]
  quality_scorecard.py --compare A.json [B.json]

--out adds to FILE, replacing earlier records of the same repo and cohort, so one file holds a whole
baseline. --compare prints the tables of one file, or of two side by side with a delta. Dates take a
full timestamp (2026-10-25T00:00:00): a bare date means that date at the current time of day.
--branches reads `name[:mergesha]` lines; a name alone is measured as unlanded.

A thin entry point (ARCHITECTURE.md, Layers): it parses, calls kitlib.reviewpack.scorecard and
prints. Exit codes: 0, 2 usage.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kitlib.reviewpack import scorecard                             # noqa: E402


def build_parser():
    p = argparse.ArgumentParser(prog='quality_scorecard.py', allow_abbrev=False,
                                description='Quality signals over landed branches. Read-only.')
    p.add_argument('repo', nargs='?')
    p.add_argument('--base', default='HEAD')
    p.add_argument('--since')
    p.add_argument('--until')
    p.add_argument('--branches')
    p.add_argument('--merge', action='append')
    p.add_argument('--out')
    p.add_argument('--compare', nargs='+', metavar='FILE')
    return p


def main(argv=None):
    p = build_parser()
    a = p.parse_args(sys.argv[1:] if argv is None else argv)
    if a.compare:
        sets = scorecard.load(a.compare)
    elif a.repo and (a.since or a.branches or a.merge):
        recs = scorecard.collect(a.repo, a.base, a.since, a.until, a.branches, a.merge)
        if a.out:
            scorecard.save(a.out, recs)
        sets = {'now': recs}
    else:
        p.error('give <repo> with --since, --branches or --merge, or --compare A [B]')
    print(scorecard.table(sets))
    return 0


if __name__ == '__main__':
    sys.exit(main())
