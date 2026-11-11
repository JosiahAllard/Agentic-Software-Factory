"""Owner files: the one home of the owner-only path rules (docs/modules/owner-files-design.md).

  rules.py      which commits and merges change owner-only paths: violations, the net change,
                shrink-only, the clean-merge rule (gate land, pre-push, commit and sync ask it)
  approvals.py  the approval record: the delta rule, verify, consume, spent, its life
  request.py    building an approval request from a checkout (tools/hub.py approvals add, the
                hub MCP tool)
  apply.py      the hub applies an approved change: on the request's branch in its linked
                worktree, checks first, one commit, put back on a refusal

No argparse, no printing, no exits (AGENTS.md).
"""
