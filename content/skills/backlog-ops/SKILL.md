---
name: backlog-ops
description: Use when you need to read or change a repo's backlog store with <kit>/tools/backlog.py - next, show, add, pull, close, block, set (including narrowing a record's scope). Gives the forms that pass the safety checks, the bar a new record must meet, and the kit's one statement of the refusal rule.
---

# Backlog operations

The store is the only tracker, read and written only through `python <kit>/tools/backlog.py -C <worktree> <verb> ...` (no verb: usage; its place: `.kit.json` `"backlog".store`). Every call is bare, one per shell call; global options (`-C`, `--session`, `--as`) go before the verb. From a linked worktree it opens the repository's live store, PM data included, as your branch: a builder's own writes need no `--as` (`<kit>/docs/backlog.md` "The store a worktree opens").

1. **Read**: `next` (the ranked offer for `--session`; `--all` uncapped); `show <ID>` (a closed, retired or deleted one prints `ENDED: ...` and a `reason:`, exit 0; `NOT FOUND:` means no commit ever removed that id); `find <text>` (closed ones too; never grep the PM repo); `list --blocked`, `list --needs-priority`, `list --status <open|blocked|parked|pulled>` (in flight is `pulled`); `stale --days <N>`.
2. **Add**: `add <ID> --summary "<title>" --scope <path> [--scope <path>] --found-on <branch|none> --why-not-folded "<reason>" [--body-file <path>]`. The summary is a title of at most 120 characters; what fails, where and the evidence go in the body file, where `## Found` takes where it was found and why it did not fold. Always unpriced; scope = the real files, one `--scope` per path; `none` for a branch since landed and removed (name it in the reason). A scope overlapping the finding branch's diff or a pulled record is REFUSED: fold it into that branch, or file it after it lands.
   **The bar**: a record cites an observed failure (a refusal row, a failed run, an incident, a live finding), a concrete file:line, doc or dead-code cleanup, or an owner request. No record for a nitpick (wording, a rename, one doc line, a test name or strength, a tidy-up) or an overengineered fix (a hypothetical or unobserved case, hardening against misuse or evasion, extra machinery): fix it on the branch or leave a note. A finding on an already-landed branch, or on a follow-up's branch, is a note unless it cites an observed failure or an owner request; a guard or landing-tool record cites an honest mistake an agent actually made, never a probe, a reviewer's evasion form or a forging case; a hypothetical gets a Known-limits line or nothing; a flake is only the `add` line its targeted rerun printed.
3. **Pull** what `next` offers, before work starts: `pull <ID>` (session-owned: `--session <key> pull <ID>`; a retry that only adds `--session` needs no `--reason`).
4. An unpriced record refuses the pull: a builder never prices; it stops and reports. A pull outside the offer is a pre-emption, the owner's decision: a coordinator asks it through the PM and, on the owner's yes, runs `pull <ID> --reason "<why>"`. A pull colliding with a live pull on another branch (`pull-collision`), and widening a pulled record into one (`set-widen-collision`: `set <ID> --scope <path> ...`, the whole new list, no `--reason`), are a lead's: a coordinator or the PM; a builder stops and reports. `--reason` never fills a free slot and never overrides a collision.
5. **Narrow** an over-broad scope instead of overriding a collision: `set <ID> --scope <path> [--scope <path>]`, then `show <ID>`. A builder narrows its own record at its design step.
6. **End**: finished work closes with the work (the branch's `Closes: <ID>` at landing, or `close <ID> --reason "<what was done>"`); dropped: `retire <ID> --why "<why>"`; a pull given back: `release <ID> --reason "<why>"`. An owner-held record, and a retire or delete of an owner- or PM-priced one, are the owner's or the PM's.
7. **Block**: `block <ID> --on <member> [--on <member>] [--reason "<why>"] [--owner-ask "<Q-nnn: question>"]`; members include `owner` and `item:<ID>`. `deliberate` needs a `--reason` or `--owner-ask` citing an R-, Q-, T- or A- id or `item:<ID>`; `owner` cites the hub ask that ends it (Q-, T- or A-): post it first bound to the record (`hub.py questions add ... --record <repo>:<ID>`, or `todos add`, `approvals add`), which parks the record itself. `unblock <ID>` clears a lone blocker; `unblock <ID> --on <member> --reason "<why>"` one of several (`owner`: the owner or the PM). A hold a program can check: one `--on <member>` with `--until "<condition>"`: `todo:T-n done`, `question:Q-n answered`, `approval:A-n decided`, `record:<repo>:<ID> closed`, `path:<path> exists|absent`, `plugin:<folder> absent`; the hub clears it when met (on `owner`, only the first three, and a coordinator may set one only when its block adds `owner` or the hold already ends on such lines; `<kit>/docs/backlog.md` "block --until").
8. **Price and edit**: `set <ID> --priority <1-5> --priority-by <who>` (skill backlog-pricing; an owner or PM price, a reprice of one, one blocked on the owner, and 1 are the owner's and the PM's; `-1` is refused); `set <ID> --body-file <path>`. A record awaiting the owner is asked bound to it on the hub (skill hub-ops), which parks it; the kit's own records bind under the PM repository's folder name, `<pm-folder>:<ID>` (`hub_view` view `repos`). The answer comes back in the record (an `answer: <Q-id> <at>` line and a `## Owner answer` section) and the park ends when no other bound question is open, so `next` offers it again.
9. `check` exits 0 before you commit.

Store kinds: `main` writes only the files in your worktree (nothing staged; it refuses the main checkout); the PM data worktree commits by itself; `ref` and `branch` land on the store at once and never touch the checkout.

Exit 0 OK (`ENDED:`, an empty `next` or `list` too); 1 `NOT FOUND:`, `ERROR:` or a red check; 2 `USAGE:` or `REFUSED:`. `USAGE:`, `NOT FOUND:` and `ERROR:` are your own mistake: fix the command once. Only `REFUSED:` is a policy guard.

## Refusals

The kit's one statement of the refusal rule; the kit header and every role cite it.
- A classifier refusal or a permission denial is final: it ends that action. Send your caller the exact command, directory and verbatim message (a coordinator to the PM, the PM to the owner) and go on with other work. Never retry, reword, add a pre-emption flag, or route it through another tool, agent, shell or session.
- A tool's `REFUSED:` on substance is final the same way. A refusal of the form only, whose message names the literal form, may be retried once, by a fresh agent, on the PM's word.
- A kit-guard (hook) refusal is neither: fix the form its `Instead:` line gives, then retry.

## Never

- Edit a record file or the store's git objects by hand.
- Write a machine path in any field: use the placeholder the repo documents (`%REPO_DATA%/...`).
- Invent a ruling id, or take a role or model label as an owner price or authorisation.

Details: `<kit>/docs/backlog.md` ("Commands", "The fold-in guard", "Owner gates").
