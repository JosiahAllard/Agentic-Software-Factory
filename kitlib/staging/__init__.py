"""kitlib.staging: the promotion of a staging branch to the main branch
(docs/modules/staging-design.md).

  changes   what is on the staging branch and not on main, and whether a promotion is due
  record    the phase record of one attempt to move main (tested, pushing, pushed, the ends)
  advance   an attempt's phases after its test: the landing lock, the push, the sync
  promote   one promotion run: the promote lock, recovery, the gate, the record, advance

tools/promote.py runs one; the hub job kitlib.hubjobs.staging_promote starts one when it is due.
kitlib.dailyfull never imports this package.
"""
