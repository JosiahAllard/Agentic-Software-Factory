"""Landing: a reviewed branch merged onto main, gated as merged, pushed and cleaned up
(ARCHITECTURE.md "Contracts": Landing; docs/gate-landing.md, docs/gate-landing-policy.md).

  land      one branch (or a batch) landed: landing lock, expected refs, merge, merged gate,
            push, verify, records closed, the feed posted, the branch cleaned up
  submit    a landing started as a detached process and followed (land.py --submit)
  verdicts  the verdict queue: reviewed branches waiting to land (tools/verdict.py)
  carry     whether a reviewed tip's verdict holds for a later tip that only synced main
  cleanup   a landed branch's remote branch, worktree and local branch removed
  feed      the landing feed's events, posted to the hub after a landing

tools/land.py and tools/gate.py parse, print and exit; everything else is here. Nothing here
prints: progress goes to a `say` callable the caller passes. The exit codes are the gate's
(kitlib.gate).
"""
