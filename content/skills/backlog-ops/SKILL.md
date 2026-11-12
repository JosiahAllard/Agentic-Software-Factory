---
name: backlog-ops
description: Use when you need to read or change a repo's backlog store with <kit>/tools/backlog.py - next, show, add, pull, close, block, set (including narrowing a record's scope). Gives the forms that pass the safety checks, the bar a new record must meet, and the kit's one statement of the refusal rule.
---

# Backlog operations

Only tracker, only `python <kit>/tools/backlog.py -C <worktree> <verb> ...` (no verb: usage; store: `.kit.json` `"backlog".store`). Bare, one per call; `-C`, `--session`, `--as` before verb. Linked worktree: live store (PM data too) as your branch; own writes, no `--as` (`<kit>/docs/backlog.md` "The store a worktree opens").

1. **Read**: `next` (offer for `--session`; `--all` uncapped); `show <ID>` (ended: `ENDED: ...` + `reason:`, exit 0; `NOT FOUND:`: never existed); `find <text>` (closed too; never grep PM repo); `list --blocked`, `list --needs-priority`, `list --status <open|blocked|parked|pulled>` (in flight = `pulled`); `stale --days <N>`.
2. **Add**: `add <ID> --summary "<title>" --scope <path> [--scope <path>] --found-on <branch|none> --why-not-folded "<reason>" [--body-file <path>]`. Summary: title, max 120 chars; failure, place, evidence in body; `## Found`: where, why not folded. Unpriced. `## Done when` items checkable (test, path, grep). Scope: real files, one `--scope` per path; `none`: landed, removed branch (named in reason). Overlap with finding branch's diff or pulled record: WARNING, note in `## Found`; folding finding still fixed there.
   **The bar**: observed failure (refusal row, failed run, incident, live finding), concrete file:line, doc/dead-code cleanup, owner request. Nitpick (wording, rename, one doc line, test name or strength, tidy-up), overengineering (unobserved case, misuse/evasion hardening, extra machinery): fix on branch or note. Landed/follow-up branch: note, unless observed failure or owner request. Guard, landing-tool record: real agent mistake only, never probe, evasion form, forgery. Hypothetical: Known-limits line or nothing. Flake: only its targeted rerun's printed `add` line.
3. **Pull** `next`'s offer before work: `pull <ID>` (session-owned: `--session <key> pull <ID>`; retry only adding `--session`, no `--reason`).
4. Refused pulls; builder stops, reports each. Unpriced: builder never prices. Outside offer: pre-emption, PM's call, coordinator asks; on yes `pull <ID> --reason "<why>"`. `pull-collision` (live pull, other branch), `set-widen-collision` (widening onto a pulled record: `set <ID> --scope <path> ...`, whole new list, no `--reason`): lead's (coordinator, PM).
5. **Narrow**, never override a collision: `set <ID> --scope <path> [--scope <path>]`, then `show <ID>`. Builder: own record, design step.
6. **End**: done: `Closes: <ID>` at landing, or `close <ID> --reason "<what was done>"`; dropped: `retire <ID> --why "<why>"`; given back: `release <ID> --reason "<why>"`. Owner-held; retiring/deleting owner- or PM-priced: theirs.
7. **Block**: `block <ID> --on <member> [--on <member>] [--reason "<why>"] [--owner-ask "<Q-nnn: question>"]`; members incl. `owner`, `item:<ID>`. `deliberate`: `--reason`/`--owner-ask` cites R-, Q-, T-, A- id or `item:<ID>`. `owner`: cites its ending hub ask (Q-, T-, A-), posted first, bound (`hub.py questions add ... --record <repo>:<ID>`, `todos add`, `approvals add`): parks. `unblock <ID>`: lone blocker; `unblock <ID> --on <member> --reason "<why>"`: one of several (`owner`: owner/PM). Program-checked hold (`--until`): `rare-cases.md` "block --until".
8. **Price and edit**: `set <ID> --priority <1-5> --priority-by <who>` (skill backlog-pricing; owner/PM price, its reprice, owner-blocked, 1: owner/PM only; `-1` refused); `set <ID> --body-file <path>`. Owner wait: bound hub ask (skill hub-ops); kit records `<pm-folder>:<ID>` (`hub_view` view `repos`). Filed answer (`answer: <Q-id> <at>`, `## Owner answer`) unparks; `next` offers it once no bound question open.
9. `check` exits 0 before commit.

Store kinds (which writes touch the checkout): `rare-cases.md` "Store kinds".

Exit 0 OK (`ENDED:`, empty `next`/`list`); 1 `NOT FOUND:`, `ERROR:`, red check; 2 `USAGE:`, `REFUSED:`. `USAGE:`, `NOT FOUND:`, `ERROR:`: your mistake, fix once. Only `REFUSED:` is a policy guard.

## Refusals

The kit's one statement of the refusal rule; the kit header and every role cite it.
- A classifier refusal or a permission denial is final: it ends that action. Send your caller the exact command, directory and verbatim message (a coordinator to the PM, the PM to the owner) and go on with other work. Never retry, reword, add a pre-emption flag, or route it through another tool, agent, shell or session.
- A tool's `REFUSED:` on substance is final the same way. A refusal of the form only, whose message names the literal form, may be retried once, by a fresh agent, on the PM's word.
- A kit-guard (hook) refusal is neither: fix the form its `Instead:` line gives, then retry.

## Never

- Hand-edit records, store git objects.
- Machine paths in fields: placeholder (`%REPO_DATA%/...`).
- Invent ruling ids; role or model label ≠ owner price or authorisation.
- `--reason` to fill a slot, (builder) re-run outside-offer pull, or over a live-pull collision.

Details: `<kit>/docs/backlog.md`.
