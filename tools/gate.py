"""gate.py: the universal gate, a landing, and the kinds-table proposal (docs/gate.md).

  gate.py [-C DIR] [--base REV] [--full | --only STEP ...] [--plan | --again] [--slug S]
          [--log-dir D]
      gate the checkout under the machine lock; on a pass of a clean tree, the gate-OK record.
      Refused (4) before it books when it would only repeat a failed gate of the same clean
      tree, gate config and kit, unless --again (kitlib.gate.rerun.unchanged_red; docs/gate-records.md
      "An unchanged red tree").
      With no -C or a relative one, refused (4) when this gate.py is in another checkout of the
      gated tree's repository; a relative --log-dir outside the gated checkout is USAGE (2)
      (kitlib.gate.caller). From another directory:
      python <worktree>/tools/gate.py -C <worktree> (docs/gate-rerun-checkout.md "The caller's checkout").
      Refused (4), with --plan and as `land` too, while the repository's stash stack holds an
      entry, whichever checkout made it (kitlib.gate.stash; docs/gate-rerun-checkout.md "The stash guard").
      Refused (4) before its header when its stdout is a file another gate run is writing
      (kitlib.gate.outfile; docs/gate-records.md "Whose run a log is")
  gate.py land BRANCH --expect-tip SHA [--expect-main SHA] [-C DIR] [--summary TEXT]
                      [--fresh] [--wait SECONDS] [--log-dir D] [--keep-branch]
                      [--reviewed-by REVIEWER_ID --submit]
      land BRANCH from the main checkout, as tools/land.py does: merge onto <remote>/<main> as it
      is at the landing's turn, gate the merged tree, push, verify, then delete the landed branch
      (remote and local) and remove its worktree, never forced, unless --keep-branch (docs/gate-landing-policy.md
      "Branch cleanup"). The merged gate runs the steps the merge's diff selects, in their cmd
      form, and every step in full once when the merge changes a gate config file; no flag makes
      it --full. --fresh runs every selected step: nothing is read from or stored in the gate
      cache (docs/gate-landing-policy.md "Gate reuse"). --submit: started detached;
      prints a REVIEW line for each branch whose queued verdict is at the tip submitted, then the
      one SUBMITTED line with its wait-result command, and exits 0; it needs --reviewed-by,
      before --submit: the reviewer id of the verdict queued at the tip (as tools/land.py)
  gate.py [-C DIR] --rerun [--slug S] [--log-dir D]
      after a red whole run (--full or default-mode) of a clean tree: rerun only its failing
      test classes, each paired
      with a control at the merge base, and the steps it never reached, under the machine lock;
      0 FLAKE (the tip lands on that record), 1 REAL (or VOID), 4 nothing to rerun (kitlib.gate.
      rerun; docs/gate-rerun-checkout.md "Targeted rerun")
  gate.py --measure --out PATH [--days N]
      write a proposed kinds table from the lock's run history (never the live table;
      kitlib.gate.measure.write_proposal); any other flag, -C, --slug and --log-dir too, is USAGE
  gate.py wait-result BRANCH --tip SHA [--kind gate|land] [--timeout S] [-C DIR]
      wait for BRANCH's result record at SHA, print it; 0 pass, 1 any other verdict, 7 no record
      at that tip within S seconds. Its one write: a running record at SHA whose process died is
      finished `error` (kitlib.gate.result.finish_dead)
  gate.py allow BRANCH --runs N --why TEXT [-C DIR]
      allow BRANCH N gate runs in all, past the branch gate budget (kitlib.gate.budget; a branch
      gate is refused (4) before booking one more): the coordinator's call, never the builder's
  gate.py stop RUN [-C DIR]
      end gate run RUN (the r-... id on its held line and in machine_lock.py status): only its
      own process tree and lock; 0 stopped or already gone, 1 could not end it, 4 refused: not a
      gate run, another session's, or a landing's (a load node's land gate shows as none on the
      node: kitlib.gate.stop; docs/gate-rerun-checkout.md "Stopping a gate"). The gate it stops exits 16 with a
      `stopped` result record

A `.kit.json` that still writes a retired key gets one WARNING line on stderr per gate or
landing run (kitlib.config.retired_warnings), never in the detached --submitted landing.

A thin entry point (ARCHITECTURE.md, Layers): it parses, calls kitlib.gate, prints and exits.
Exit codes (kitlib.gate): 0 OK, 1 FAILED, 2 USAGE, 3 STUCK, 4 REFUSED, 5 BUSY, 16 STOPPED; 9
LANDED_UNGATED is retired (no landing gives it); a machine lock refusal passes its own code
through (6, 8, 10, 11, 12); 130 Ctrl+C.
"""
import argparse
import os
import re
import sys

# The checkout this gate.py is in: in the kit's own repository, the checkout whose code gates.
KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, KIT)


def safe_stdio():
    """A character the console's encoding has no byte for (cp1252 on a redirected Windows
    stream: a child's log line or a console window's title) is written as a \\u escape, never
    raised: a gate that ran its steps never ends `error` over printing."""
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(errors='backslashreplace')
        except (AttributeError, ValueError, OSError):
            pass


if __name__ == '__main__':      # from the kit's main checkout: run from pushed main (kitlib.gate.pin)
    safe_stdio()
    from kitlib.gate import pin                                     # noqa: E402
    _code, _said = pin.run(__file__, sys.argv[1:])
    if _said:
        sys.stderr.write(_said + '\n')
    if _code is not None:
        sys.exit(_code)

from kitlib import config as kconfig                              # noqa: E402
from kitlib import gate as G                                       # noqa: E402
from kitlib import pluginsource                                    # noqa: E402
from kitlib.coordstate import log as coordlog                       # noqa: E402
from kitlib.gate import land as landmod                            # noqa: E402
from kitlib.gate import budget, caller, measure, outfile, rerun, runner  # noqa: E402
from kitlib.gate import result as resultmod                       # noqa: E402
from kitlib.gate import stop as stopmod                            # noqa: E402
from kitlib.gate import submit as submitmod                        # noqa: E402
from kitlib.lock.store import LockError, Store                     # noqa: E402


def say(line):
    print(line, flush=True)


class Parser(argparse.ArgumentParser):
    """argparse whose errors print `USAGE:` and exit 2 (refusal-log P10: a usage error is never
    worded as a refusal)."""

    def error(self, message):
        self.print_usage(sys.stderr)
        sys.stderr.write('USAGE: %s\n' % message)
        sys.exit(G.USAGE)


def gate_parser():
    p = Parser(prog='gate.py', description='Gate this checkout under the machine lock.')
    p.add_argument('-C', dest='root', help='the checkout (default: here; refused when a relative '
                   'or absent -C names another checkout of this gate.py\'s repository). From '
                   'another directory: python <worktree>/tools/gate.py -C <worktree>')
    p.add_argument('--base', help='compare against this rev (default <remote>/<main>)')
    sel = p.add_mutually_exclusive_group()
    sel.add_argument('--full', action='store_true', help='every step that is not manual; nothing '
                     'read from or stored in the gate cache')
    sel.add_argument('--only', nargs='+', metavar='STEP', help='exactly these steps (no record)')
    sel.add_argument('--rerun', action='store_true',
                     help='rerun only the failing test classes of the red whole run, each '
                     'paired with a control at the merge base, under the machine lock (one per '
                     'red run; counts toward the gate budget; docs/gate-rerun-checkout.md "Targeted rerun")')
    p.add_argument('--plan', action='store_true', help='print what would run; run nothing')
    p.add_argument('--again', action='store_true',
                   help='run a gate that would only repeat a failed gate of this same tree '
                   '(docs/gate-records.md "An unchanged red tree")')
    p.add_argument('--slug', help='the machine-lock slug (default: the branch)')
    p.add_argument('--log-dir', help='where this run\'s logs go')
    p.add_argument('--measure', action='store_true', help='write a proposed kinds table')
    p.add_argument('--out', help='--measure: the proposal file (never the live kinds table)')
    p.add_argument('--days', type=int, default=30, help='--measure: history days (default 30)')
    return p


def land_parser():
    p = Parser(prog='gate.py land', description='Land one branch from the main checkout.')
    p.add_argument('branch')
    p.add_argument('--expect-main', metavar='SHA',
                   help='the main the landing was planned on (optional): main moved on is said, '
                        'never refused')
    p.add_argument('--expect-tip', required=True, metavar='SHA')
    p.add_argument('-C', dest='root', default='.', help='the main checkout (default: here)')
    p.add_argument('--summary', help='the merge title after "Merge <branch>: "')
    p.add_argument('--fresh', action='store_true',
                   help='run every selected merged-gate step: nothing read from or stored in the '
                        'gate cache')
    p.add_argument('--wait', type=int, default=None, metavar='SECONDS',
                   help='wait this long for the landing\'s turn (default 0: BUSY at once; with '
                        '--submit, "landing".wait_s)')
    p.add_argument('--log-dir', help='where the merged gate\'s logs go')
    p.add_argument('--keep-branch', action='store_true',
                   help='after landing, keep the remote branch, the worktree and the local branch')
    p.add_argument('--reviewed-by', dest='reviewed_by', action='append', default=[],
                   metavar='REVIEWER_ID',
                   help='--submit: the reviewer id of the verdict queued at the tip (required)')
    p.add_argument('--submit', action='store_true',
                   help='start the landing detached; print the wait-result command and exit 0')
    p.add_argument(submitmod.FLAG, dest='submitted', metavar='PAYLOAD', help=argparse.SUPPRESS)
    return p


def warn_retired(root):
    """Say on stderr, once per run, each retired key the checkout's `.kit.json` still writes."""
    for line in kconfig.retired_warnings(root):
        sys.stderr.write(line + '\n')


def do_land(argv):
    p = land_parser()
    a = p.parse_args(argv)
    root = os.path.abspath(a.root)
    if not a.submitted:         # the submitting run said it; the detached landing does not
        warn_retired(root)
    if a.wait is not None and a.wait < 0:
        p.error('--wait is 0 or more seconds')
    if a.submit and a.submitted:
        p.error('--submit starts the landing that --submitted runs; not both')
    if a.reviewed_by and not a.submit:
        p.error('%s goes with --submit' % submitmod.REVIEWED_BY)
    branches, tips = [a.branch], [a.expect_tip]
    if a.submit:
        tool = submitmod.GATE_TOOL
        out = submitmod.submit(
            root, __file__, submitmod.child_args(tool, a, root, branches, tips), branches, tips,
            expect_main=a.expect_main, reviewed_by=a.reviewed_by,
            again=lambda ids: submitmod.again_line(
                tool, submitmod.again_args(tool, a, root, branches, tips), ids))
        code = emit(out)
        coordlog.note_submit(out, branches, tips, a.reviewed_by)   # docs/coord-state.md
        return code
    submitted = None
    if a.submitted:
        submitted, said = submitmod.restore_landing(root, a.submitted, branches, tips,
                                                    expect_main=a.expect_main)
        code = emit(said)
        if submitted is None:
            return code
    # A detached landing waits the configured "landing".wait_s for its turn; one run here, 0.
    wait_s = a.wait if a.wait is not None or submitted is not None else 0
    return emit(guarded(landmod.land, root, a.branch, a.expect_main, a.expect_tip,
                        summary=a.summary, wait_s=wait_s, say=say,
                        book_kw={'store': Store(say=say)}, log_dir=a.log_dir,
                        keep_branch=a.keep_branch, fresh=a.fresh, submitted=submitted))


def wait_parser():
    p = Parser(prog='gate.py wait-result',
               description='Wait for the result record of BRANCH at SHA (a running one whose '
                           'process died is finished error).')
    p.add_argument('branch')
    p.add_argument('--tip', required=True, metavar='SHA', help='the tip gated (7+ hex digits)')
    p.add_argument('--kind', choices=resultmod.KINDS, default='gate',
                   help='gate (a branch gate, default) or land (a landing)')
    p.add_argument('--timeout', type=float, metavar='S',
                   help='give up after S seconds, exit 7 (default: wait forever)')
    p.add_argument('--poll', type=float, default=resultmod.POLL_S, metavar='S', help=argparse.SUPPRESS)
    p.add_argument('-C', dest='root', default='.', help='any checkout of the repository')
    return p


def do_wait(argv):
    a = wait_parser().parse_args(argv)
    if not re.fullmatch(r'[0-9a-fA-F]{7,40}', a.tip):
        print('USAGE: --tip is 7 to 40 hex digits, not %r' % a.tip, file=sys.stderr)
        return G.USAGE
    if not resultmod.valid_timeout(a.timeout):
        print('USAGE: --timeout is a finite number of seconds, 0 or more, not %s' % a.timeout,
              file=sys.stderr)
        return G.USAGE
    root = os.path.abspath(a.root)
    try:
        rec, _ = resultmod.wait(root, a.branch, a.tip, kind=a.kind, timeout_s=a.timeout,
                                poll_s=max(0.05, a.poll))
    except resultmod.ResultError as e:
        print('USAGE: -C %s: %s' % (root, e), file=sys.stderr)
        return G.USAGE
    if rec is None:
        print('TIMEOUT: no final %s result record for %s at %s within %s s' % (
            a.kind, a.branch, a.tip, a.timeout))
        return resultmod.TIMEOUT
    for line in resultmod.result_lines(rec, a.kind, a.branch):
        print(line)
    return G.OK if rec.get('verdict') == 'pass' else G.FAILED


def allow_parser():
    p = Parser(prog='gate.py allow',
               description='Allow BRANCH more gate runs than the branch gate budget gives '
                           '(docs/gate-records.md "The branch gate budget"). Its coordinator runs it.')
    p.add_argument('branch')
    p.add_argument('--runs', required=True, type=int, metavar='N',
                   help='the new total of gate runs, a whole number above %d' % budget.RUNS)
    p.add_argument('--why', required=True, help='what the runs found and why more are needed')
    p.add_argument('-C', dest='root', default='.', help='any checkout of the repository')
    return p


def do_allow(argv):
    a = allow_parser().parse_args(argv)
    root = os.path.abspath(a.root)
    try:
        path, used = budget.allow(root, a.branch, a.runs, a.why, Store(say=say))
    except ValueError as e:
        print('USAGE: %s' % e, file=sys.stderr)
        return G.USAGE
    except budget.BudgetError as e:
        print('REFUSED: %s; nothing written' % e, file=sys.stderr)
        return G.REFUSED
    print('ALLOWED %s: %d gate runs (%d used); %s' % (a.branch, a.runs, used, path))
    return G.OK


def stop_parser():
    p = Parser(prog='gate.py stop',
               description='End one gate run: only its own process tree and lock, never a '
                           'landing (docs/gate-rerun-checkout.md "Stopping a gate").')
    p.add_argument('run', metavar='RUN',
                   help='the lock run id: the r-... on the gate\'s held line and in '
                        'machine_lock.py status')
    p.add_argument('-C', dest='root', default='.',
                   help='a checkout whose repository\'s landing lock is read too (default: here)')
    return p


def do_stop(argv):
    a = stop_parser().parse_args(argv)
    return emit(stopmod.stop_run(a.run, Store(say=say), root=os.path.abspath(a.root),
                                 actor={'name': 'gate.py stop'}, say=say))


def emit(outcome):
    """Print the Outcome's lines, to stderr when it has the fact stderr=True; its code."""
    stream = sys.stderr if outcome.facts.get('stderr') else sys.stdout
    for line in outcome.lines:
        print(line, file=stream, flush=True)
    return outcome.code


def guarded(fn, *args, **kw):
    """fn(...)'s Outcome; a SystemExit that escapes the library (the repo's code calling exit) is a
    FAILED gate with a verdict line, never a silent exit 0."""
    try:
        return fn(*args, **kw)
    except SystemExit as e:
        return G.Outcome(G.FAILED, ['GATE FAILED: something the gate ran called exit (SystemExit, '
                                    'code %r) instead of returning; no gate-OK record' % (e.code,)])


def do_measure(a):
    if not a.out:
        print('USAGE: --measure needs --out PATH (a proposal file for PM review)', file=sys.stderr)
        return G.USAGE
    if a.days < 1:
        print('USAGE: --days must be at least 1', file=sys.stderr)
        return G.USAGE
    return emit(measure.write_proposal(a.out, a.days, say=say))


def main(argv):
    safe_stdio()
    book_kw = None
    try:
        if argv[:1] == ['wait-result']:
            return do_wait(argv[1:])
        if argv[:1] == ['land']:
            return do_land(argv[1:])
        if argv[:1] == ['allow']:
            return do_allow(argv[1:])
        if argv[:1] == ['stop']:
            return do_stop(argv[1:])
        a = gate_parser().parse_args(argv)
        if a.measure:
            if (a.only or a.full or a.rerun or a.plan or a.base or a.root is not None
                    or a.slug or a.log_dir or a.again):
                print('USAGE: --measure takes only --out and --days', file=sys.stderr)
                return G.USAGE
            return do_measure(a)
        if a.out:
            print('USAGE: --out goes with --measure', file=sys.stderr)
            return G.USAGE
        if a.rerun and (a.plan or a.base or a.again):
            print('USAGE: --rerun takes only -C, --slug and --log-dir', file=sys.stderr)
            return G.USAGE
        if a.again and a.plan:
            print('USAGE: --again runs a gate; --plan runs nothing', file=sys.stderr)
            return G.USAGE
        root = os.path.abspath(a.root or '.')
        # No -C or a relative one: this gate.py on another checkout of its own repository. An
        # absolute -C names the tree on purpose.
        if not (a.root and os.path.isabs(a.root)):
            refused = caller.own_copy_refusal(root, pluginsource.live_root(KIT), rel=a.root)
            if refused is not None:
                return emit(refused)
        refused = caller.log_dir_refusal(a.log_dir, root, os.getcwd())
        if refused is not None:
            return emit(refused)
        # Before the gate says anything: no second gate's header in another run's log.
        held, refused, warning = outfile.claim(sys.stdout)
        if refused:
            return emit(G.Outcome(G.REFUSED, refused))
        if warning:
            say(warning)
        warn_retired(root)
        try:
            book_kw = {'store': Store(say=say)}
            if a.rerun:
                return emit(guarded(rerun.rerun_gate, root, slug=a.slug, log_dir=a.log_dir,
                                    say=say, book_kw=book_kw))
            return emit(guarded(runner.branch_gate, root, base=a.base, full=a.full,
                                only=a.only, plan=a.plan, slug=a.slug, log_dir=a.log_dir,
                                say=say, book_kw=book_kw, again=a.again))
        finally:
            if held is not None:
                held.release()
    except LockError as e:
        return emit(runner.lock_refusal(e))
    except KeyboardInterrupt:
        print('Ctrl+C: the gate stopped')
        return 130


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
