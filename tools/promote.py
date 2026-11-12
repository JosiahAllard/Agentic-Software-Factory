"""promote.py: one promotion of the staging branch to the main branch
(docs/modules/staging-design.md).

  promote.py [-C DIR] [--force]     test the staging tip against main with the merged gate and,
                                    on a green, move main to it; an attempt a run left unfinished
                                    is moved on first, and then the run ends
  promote.py [-C DIR] --status      print the latest attempts; run nothing

A thin entry point: it parses, calls kitlib.staging.promote, prints and exits. The hub job
staging-promote starts it when a promotion is due; --force runs it when it is not.
Exit: 0 done, behind, not due, or skipped by the machine lock (nothing recorded); 1 the gate was
red; 2 usage, or staging off at <remote>/<main>; 4 held, diverged, refused by the pre-push
guard, failed or the gate refused; 5 another promotion run holds the promote lock (nothing
recorded).
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kitlib import gate as G                                        # noqa: E402
from kitlib.config import load as load_config                       # noqa: E402
from kitlib.config.types import ConfigError                         # noqa: E402
from kitlib.staging import promote                                  # noqa: E402
from kitlib.worktree import remove_checks                           # noqa: E402


def say(line):
    print(line, flush=True)


def parse_args(argv):
    p = argparse.ArgumentParser(prog='promote.py', description=__doc__.split('\n\n')[0])
    p.add_argument('-C', dest='root', default='.', help='any checkout of the repository')
    p.add_argument('--force', action='store_true',
                   help='test the staging tip even when no promotion is due')
    p.add_argument('--status', action='store_true', help='print the latest attempts; run nothing')
    return p.parse_args(argv)


def main(argv=None):
    try:
        args = parse_args(list(sys.argv[1:] if argv is None else argv))
    except SystemExit:                       # a usage error, or --help: 0 would mean done
        return G.USAGE
    try:
        root = remove_checks.main_checkout(os.path.abspath(args.root))
        cfg = load_config(root)
    except (ConfigError, remove_checks.GitReadError) as e:
        say('REFUSED: %s' % e)
        return G.USAGE
    if args.status:
        for line in promote.status_lines(root, cfg):
            say(line)
        return G.OK
    out = promote.run(root, cfg, say=say, force=args.force)
    for line in out.lines:
        say(line)
    return out.code


if __name__ == '__main__':
    sys.exit(main())
