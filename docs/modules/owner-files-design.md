# Owner files design

Low-level design of `kitlib/ownerfiles` and `kitlib/delegations`. The high-level design is ARCHITECTURE.md ("Contracts": Owner files; "Security") and wins on a conflict. Where today's tree differs: docs/redesign-work.md (W-015, W-016, W-028).

## Contract

- **An owner-only path changes only by the owner's word.** `config.owner` says which paths are owner-only and who the owner is. A commit that changes one reaches main only when the owner authored it, or it carries one approval the hub verifies.
- **One home for the rule.** `ownerfiles` judges a commit, a merge and a range. `commit`, `syncbase`, `landing`, the pre-push check, the review pack and the hub all ask it; none carries a copy.
- **What a commit changes** is judged per commit, never by the net blob: a one-parent commit by its diff, a two-parent merge by what differs from the clean merge of its parents. A path counts only when landing the tip would change it on the target main.
- **An approval** is a hub record: the repository, the signed base, each file's base and approved content. An approved commit carries exactly one `Owner-Approval: hub:A-<n>`, has one parent, changes only the approval's files, and makes the approved change on its parent (the approved blob, or its clean merge onto the parent's copy).
- **The hub applies an approved change.** When the owner approves, the hub server makes the approval's commit itself, in the worktree the request names, with the owner's approval trailer; the owner can also run the apply from an outside terminal. No agent writes, stages or commits an owner-only path, approved or not: an agent's part ends with the request and resumes with the push that says the commit is on its branch. A change that cannot be applied (the worktree moved on, a path is not HEAD's copy) shows on the approval with the reason, and stays approved until it is applied, spent or expired.
- **An approval holds until its change is on main**, with a 30-day backstop from the decision. A rebuild, rebase, merge or re-land that carries the same change needs no second approval. Only lines added or removed beyond what was approved need a new one. Once a commit main reaches carries the trailer, the approval is spent and any other commit carrying it is refused.
- **A sync merge carries an owner-only path** with no new approval when the staged copy equals `git merge-tree`'s result, the path is not conflicted, and every commit on either side that changed it is the owner's or approved. A rename, a mode change, a non-default merge attribute or an octopus merge refuses. Merge-tree only, never merge-file.
- **Shrink-only paths** (`owner.shrink_only`): a one-parent commit that only deletes lines of such a path needs no approval.
- **Everything fails closed**: a git failure, a hub that cannot be asked, a merge or root commit carrying an approval, any doubt.
- **The delegation list** (`delegations`) is the owner's own file naming the operations the PM may run on the owner's behalf. It is read, never written, by the kit; the guard closes it to agents. A run checks its preconditions, places itself, and where the list says so takes a one-run hub approval. Checks that read an app (the PM start checklist) are handed in by the entry point.

## Packages and files

| Path | Holds |
|---|---|
| `ownerfiles/rules.py` | Which commits and merges touch owner-only paths: `violations`, the net change, shrink-only, the clean-merge rule |
| `ownerfiles/approvals.py` | The approval record: the delta rule, verify, consume, spent, the 30-day life |
| `ownerfiles/request.py` | Building a request from a checkout (the CLI and the hub MCP tool call it) |
| `ownerfiles/apply.py` | Making the approved commit in a worktree: pre-write checks, write, stage, commit, consume, put back on a refusal |
| `delegations/load.py`, `schema.py`, `place.py`, `run.py` | The list, its checks, where a run may act, running one operation |

## State

Approvals live in the hub store; `ownerfiles` reads them through `hubclient` and owns what they mean. The delegation list is an owner-only file. Nothing is cached.

## Errors

Each check returns the reason as text; the caller prints it under its own label (`refused:` for a commit, `REFUSED:` for a landing, a deny for a push). An apply that fails after it wrote puts every file back and unstages its paths.

## Adding to it

A new way for an owner-only path to change is a new rule here, with its test on both a commit and a landing; nothing else may special-case owner paths. A new delegated operation: an entry in the owner's list and its checks in `delegations`.

## Tests

`tests/ownerfiles` (commit rules, the delta rule, the life and spent rules, the merge rule, apply and put-back), `tests/delegations`, and the landing's and pre-push's owner tests.

## Reference

docs/commit.md ("Owner approval"), docs/guard-hooks.md ("Owner-only commits"), docs/hub-approvals.md, docs/delegations.md, docs/guard-owner.md.
