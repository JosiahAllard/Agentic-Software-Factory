"""Landing: a reviewed branch merged onto main, gated as merged, pushed and cleaned up
(ARCHITECTURE.md "Contracts": Landing; docs/gate-landing.md, docs/gate-landing-policy.md).

  lander      one branch (or a batch) landed: the order of steps, the landing's turn and its
              state (Lander, built from the step mixins below), the queue and check_strict
  checks      the refusals before the merge: tips, records, strict paths, verdicts, scope,
              owner-only paths
  merge       main synced, the landing tree prepared and the branches merged in it
  mergedgate  where the merged gate runs (main or a load node) and its run
  push        the land record, the push and its verification, STUCK and its recovery text
  after       after the push: feed, approvals, mirrors, records closed, branch cleaned up
  submit      a landing started as a detached process and followed (land.py --submit)
  verdicts    the verdict queue: reviewed branches waiting to land (tools/verdict.py)
  carry       whether a reviewed tip's verdict holds for a later tip that only synced main
  cleanup     a landed branch's remote branch, worktree and local branch removed
  feed        the landing feed's events, posted to the hub after a landing

tools/land.py and tools/gate.py parse, print and exit; everything else is here. Nothing here
prints: progress goes to a `say` callable the caller passes. The exit codes are the gate's
(kitlib.gate).
"""
