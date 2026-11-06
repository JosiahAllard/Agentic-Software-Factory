"""kit_tests.py: run the kit's own test suite, whole or only the tests that directly use what a
branch changed (docs/test-tools.md "kit_tests").

  kit_tests.py [-C DIR] --select [--smoke] [--base B] [--changed PATH ...] [--plan] [--jobs N]
  kit_tests.py [-C DIR] --full [--base B] [--plan] [--jobs N]
  kit_tests.py [-C DIR] --scrub [--plan] [--jobs N]: only tests/scrub, one pass (a cmd for a
  repo's docs_only gate step; the kit's gate sets no docs_only, so no kit gate step runs it)
  --smoke adds tests/selection.json's "smoke" modules to the selection unless every changed
  path is prose (kitlib.tooldeps.selftree.smoke; the gate's smoke_steps, docs/gate.md); a
  smoke entry naming no test module refuses
  kit_tests.py [-C DIR] --list: every test id sorted, then a summary and the decorator skips
  (kitlib.tooldeps.selflist; nothing runs; runtime skips are not counted)
  add --real-git to either: every test on real git, not the fake
  add --both-tiers to either: the fake-git pass, then the real-git pass (kitlib.tooldeps.tiers)
  add --pid-hold to either: hold pids for the run (opt-in, agent runs only; the gate refuses it;
  docs/test-tools.md "The pid shield")

--select diffs the checkout against its merge-base with B (default: origin/main when it resolves,
else main - docs/gate.md "Default base" - and a line says when the local main differs), work
tree and untracked files included (or takes the --changed list), maps the paths through
kitlib.tooldeps.selftree and the table tests/selection.json to the change's direct users (the
test modules that import, name or load what changed, or list a folder it is in; never the
transitive closure), prints each selected test package or single module with the reasons it was
picked, and runs those test modules. A shared-core, test-helper, selector or unmapped change, or
a tool no test uses, runs the full suite instead, and says why. --full runs `python -m unittest
discover -s tests -t .`'s modules; the landing's merged gate ("landing".gate_once), the daily
full run and go-live use it, and catch what a direct-users selection leaves out. --plan prints
and runs nothing.
The modules run in parallel worker processes (kitlib.tooldeps.selfrun), --jobs at a time - by
default the logical processors the machine lock run holds - longest first by the times recorded
in the git common dir (kit-tests-times.json), long modules split by class - then the table's
`serial` modules one at a time. As the pool drains, the cpu slots its last units do not use go
back to the lock (kitlib.lock.giveback.drainer) when the lock run offers it. --jobs 1 runs the
one `python -m unittest` process the tool always ran.
Tests run on the fake git (docs/test-tools.md "Fake git"): the units get KIT_FAKE_GIT=1 unless it
is set; --real-git gives them 0, so the unit tier also runs on real git. On the fake, --select
and --full also run the test modules the change adds or changes (tiers.changed_test_modules: the
--select change list, or for --full the same diff against --base or the default base, said in a
line) as a second pass on real git, with no flag (the gate's cmd and full_cmd); when there is
none, one pass and a line `kit_tests: real-git pass: no added or changed test module`.
--both-tiers runs the selection on KIT_FAKE_GIT 1, then tiers.REAL_GIT_MODULES of it and the
added or changed test modules on 0. Two passes print `kit_tests: OK` only when both passed; each
pass's own lines read `kit_tests [fake git]: ` / `kit_tests [real git]: `. The fake pass gives cpu
slots back as it drains, down to what the real pass can use at once (tiers.keep_for_next); one
drainer serves both passes.
The units also get the home fence's marker (docs/test-tools.md "Home fence"): KIT_HOME_FENCE, the
home folder this process resolves, unless the environment already carries one. The tools a test
starts inherit it and refuse that home.
And the time fence's marker (docs/flaky-tests.md): KIT_TIME_FENCE=1 unless it is set, so a degrade
budget (kitlib.clock.budget) is the ceiling in every unit; kit_tests' own process stays unmarked.
No test child gets the session's kit binding: PM_KIT_ROOT, PM_KIT_RELEASE_ID, KIT_LIVE_ROOT and
KIT_PINNED, PM_KIT_ROLE, PM_KIT_HOST and PM_KIT_SESSION_NAME (KIT_SESSION_ENV), each registered
host's session_env variable and its PM_KIT_<HOST>_ variables (hosts.json; session_env_names()) are
dropped from its environment, so a session's env cannot steer the suite
(kitlib/tooldeps/childenv.py child_env).
A thin entry point (ARCHITECTURE.md, Layers): it parses, calls the library, prints, runs, exits.
Exit codes: the unittest run's (0 passed or nothing selected, 1 failed); 2 USAGE or a refusal
(git could not list the change - for --full too, unless --real-git - or --select with a
malformed table): no test ran; 130 Ctrl+C.
--full never refuses a malformed table: it warns and runs every module, none serial.
"""
import argparse
import os
import subprocess          # noqa: F401  (kept as a handle for test patches: cli.subprocess.call)
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kitlib.git import repo as gitrepo                              # noqa: E402
from kitlib.lock import giveback                                    # noqa: E402,F401
from kitlib.pytests import runner as unit_runner                    # noqa: E402
from kitlib.tooldeps import kitrun, selflist, selfrun, selftree, tiers  # noqa: E402,F401
# The test children's environment (kitlib/tooldeps/childenv.py): the session's kit binding
# (KIT_SESSION_ENV, session_env_names) dropped, the time and home fences marked.
from kitlib.tooldeps.childenv import KIT_SESSION_ENV, child_env, session_env_names  # noqa: E402,F401

USAGE = kitrun.USAGE
FULL = kitrun.FULL
units_env = kitrun.units_env                                        # tests/clock read it here


class Parser(argparse.ArgumentParser):
    """argparse whose errors print `USAGE:` and exit 2."""

    def error(self, message):
        self.print_usage(sys.stderr)
        sys.stderr.write('USAGE: %s\n' % message)
        sys.exit(USAGE)


def build_parser():
    p = Parser(prog='kit_tests.py', allow_abbrev=False,
               description="Run the kit's own tests, whole or selected (docs/test-tools.md).")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument('--select', action='store_true', help='only what the change can reach')
    g.add_argument('--full', action='store_true', help='the whole suite')
    g.add_argument('--list', dest='list_tests', action='store_true',
                   help='print every test id, sorted, and the decorator skips; run nothing')
    g.add_argument('--scrub', action='store_true',
                   help='only the scrub test package, one pass (no kit gate step runs it: '
                        'the kit sets no docs_only)')
    p.add_argument('--smoke', action='store_true',
                   help='--select: add the table\'s "smoke" modules unless every changed path '
                   'is prose')
    p.add_argument('--base', help='--select and --full: diff against the merge-base with this '
                   '(default: origin/main when it resolves, else main); --select picks from the '
                   'change, --full takes only the added or changed test modules for its real-git '
                   'pass (no diff under --real-git or with --changed)')
    p.add_argument('--changed', nargs='+', metavar='PATH', help='--select: these paths, not git')
    p.add_argument('--plan', action='store_true', help='print the selection, run nothing')
    p.add_argument('--jobs', type=int, metavar='N',
                   help='modules run at once (default: the cores the lock run holds; 1: one process)')
    p.add_argument('--real-git', action='store_true',
                   help='every test on real git, not the fake (KIT_FAKE_GIT=0)')
    p.add_argument('--both-tiers', action='store_true',
                   help='run twice: on the fake git, then on real git; OK only when both pass '
                   '(the daily run\'s daily_cmd)')
    p.add_argument(unit_runner.PID_HOLD_FLAG, dest='pid_hold', action='store_true',
                   help='hold pids for the run (opt-in for agent runs; off by default; the gate '
                   'refuses it)')
    p.add_argument('-C', dest='root', default='.', help='the checkout (default: here)')
    return p


def main(argv=None, out=None):
    out = sys.stdout if out is None else out

    def say(line):
        out.write(line + '\n')
        out.flush()
    parser = build_parser()
    a = parser.parse_args(argv)
    if (a.full or a.scrub) and a.changed:
        parser.error('--changed goes with --select')
    if a.smoke and not a.select:
        parser.error('--smoke goes with --select (--full already runs every module)')
    if a.scrub:
        for flag, used in (('--both-tiers', a.both_tiers), ('--base', a.base is not None)):
            if used:
                parser.error('%s does not go with --scrub (one pass, no change list)' % flag)
    if a.jobs is not None and a.jobs < 1:
        parser.error('--jobs must be at least 1')
    if a.both_tiers and a.real_git:
        parser.error('--both-tiers already runs the real-git tier; drop --real-git')
    if a.both_tiers and a.pid_hold:
        parser.error('--both-tiers takes no --pid-hold (one hold cannot span two passes)')
    if a.list_tests:
        for flag, used in (('--jobs', a.jobs is not None), ('--plan', a.plan),
                           ('--real-git', a.real_git), ('--both-tiers', a.both_tiers),
                           (unit_runner.PID_HOLD_FLAG, a.pid_hold), ('--changed', a.changed),
                           ('--base', a.base is not None)):
            if used:
                parser.error('%s does not go with --list (it runs nothing)' % flag)
    try:
        top, why = gitrepo.toplevel(os.path.abspath(a.root))
        if not top:
            say('kit_tests: refused: %s' % why)
            return USAGE
        if a.list_tests:
            modules = selftree.all_test_modules(top)
            env = child_env()
            env.setdefault('KIT_FAKE_GIT', '1')
            entries = selflist.collect(top, modules, environ=env)
            if entries is None:
                say('kit_tests: refused: the child that loads the tests failed - nothing listed')
                return USAGE
            for line in selflist.render(entries, modules):
                say(line)
            return 0
        return kitrun.run(a, top, out)
    except KeyboardInterrupt:
        say('kit_tests: INTERRUPTED, exit %d' % tiers.INTERRUPTED)
        return tiers.INTERRUPTED


if __name__ == '__main__':
    sys.exit(main())
