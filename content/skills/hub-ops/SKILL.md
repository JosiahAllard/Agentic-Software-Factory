---
name: hub-ops
description: Use when you post on the Action Hub - a question or a to-do for the owner, how its answer comes back (read pending once at start, then push; no background wait), or a page (a research or design write-up) for the owner to read. Gives the commands and what each needs; who may ask the owner what stays your role's rule.
---

# Action Hub operations

Writes, except `todos ack`, `questions done`, `approvals add`, `skill-edit`: `python <kit>/tools/hub.py <verb> ... --from <your session key>`, one per call. MCP `hub_ask`, `hub_todo_add`, `hub_todo_ack`, `hub_page_publish`, `hub_approval_request`: same; `from` where verb takes `--from` (not `hub_todo_ack`, `hub_approval_request`). Key: start's `hub key:` line; others refused; unknown: ask PM.

1. Question: `python <kit>/tools/hub.py questions add --title "<restates its subject>" --context-file <F> --option "label::description" [--option ...] --recommended <N> [--refs <ID> ...] --from <key> (--record <repo>:<ID> | --class <C>)`.
   - `N` from 0. Proposing to build (tool, guard, check, audit, design, record kind): observed failures, count; what exists or goes (none: say so).
   - Record or class, never neither (`hub_ask`: `record` or `class`). `C`: `rule` (standing rule, delegation run line), `machine`, `cross-repo`, `go` (one-off landing/archive go).
   - `--record <repo>:<ID>`; kit records: PM repo folder name (`hub_view` view `repos`), `<pm-folder>:<ID>`, not `pm:<ID>`/`kit:<ID>`. Blocks record on owner until answered or withdrawn. Move on: no PM relay, no wait.
   - `python <kit>/tools/hub.py questions show <Q-id>`: options, answer, note (`questions`: all). Withdraw own open: `questions withdraw <Q-id> --from <key> --if-version <V>`.
2. Pending, push: `python <kit>/tools/hub.py pending --for <key>` once at start; act. No background wait: answers, done to-dos push `[hub push] <kind> <id> <status> ...` + action line (<kit>/docs/hub-push.md "Push notifications").
   - Push: read `pending --for <key>` (takes it, ends push), act. Pending until logged/done (<kit>/docs/hub-pm.md "For the PM"): after compaction/restart, check before acting on re-listed id.
   - Bound answer filed in record (`## Owner answer`; park ends once no bound question open; status `filed`, <kit>/docs/hub-questions.md "Filing the answer"), named in push: `backlog.py show <ID>`, resume. Hub marks done (`done_by` `hub`, <kit>/docs/hub-questions.md "Done by the hub"): no `questions log`, `questions done`.
   - Still listed after read: standing rule (PM logs; your done refused); delegation run line (waits for log step); filing failed (stays `answered`, PM's); unbound classed (PM logs); ` (filed)` alone: `python <kit>/tools/hub.py questions done <Q-id> --why "<what you did>" --if-version <V>`, V from `pending`.
3. To-do (owner runs a command): `python <kit>/tools/hub.py todos add --title "<T>" --command "<C>" [--command ...] [--detail "<D>"] [--steps <F.json>] [--repo <repo>] [--record <repo>:<ID>] --from <key>` (`hub_todo_add` takes `record`); bound: parks record until `todo:T-n done`. Withdraw: `todos withdraw <T-id> --from <key> --if-version <V>`. Pushed done: act, `python <kit>/tools/hub.py todos ack <id>`; unacked 30 min: PM's (<kit>/docs/hub-pm.md "Who handles a pending item").
4. Page: one UTF-8 HTML file; no script, `on*` attribute, remote URL; <meta> only charset utf-8 or name viewport/color-scheme/description/author/generator; <title> plain text; max 512 KiB (<kit>/docs/hub-skill-pages.md "pages/<id> and page_html/<id>"). Diagrams inline `<svg>`; Mermaid refused ("render it to svg first").
   - `python <kit>/tools/hub.py pages publish <page-id> --title "<T>" --html-file <F> [--summary "<S>"] --from <key>`; republish: `--if-version <V>` from card.
   - Id-shaped id refused (Q M T P S A R E letter, dash, digits, any case: `design-T-3`; `H-<name>-<digits>`). Question or to-do: page id in `--refs`.
   - No shell: file in scratch, named in report; caller publishes.
5. Owner approval (role rule needs one): each file's whole new content in a file outside checkout; `python <kit>/tools/hub.py approvals add --checkout <checkout> --title "<T>" --file <repo path> <content file> [--file ...] [--reason "<R>"] [--record <repo>:<ID>]` (`hub_approval_request` takes `record`; bound: parks until `approval:A-n decided`, denied too; expired undecided: hold kept, `approval-expired` pushed to PM).
   - Base: checkout HEAD file (else `--head <commit>`); content exact bytes; prints `REQUESTED <id>`; no `--from`; no deletes (<kit>/docs/hub-approvals.md "approvals/A-NNN"). Owner decides on hub.
   - Withdraw pending (superseded): `approvals withdraw <A-id> --from pm --if-version <V>` (no stored `from`: key `pm`; V from `approvals --json`); prints `WITHDRAWN <A-id>`. Decided: refused; expired undecided: allowed, drops hold.
   - Ask from checkout on your branch; files at base. Approved: hub commits on that branch in your worktree, pushes `applied` + commit; never commit yourself. Unapplied (`approvals show <A-id>` reason, push `unapplied`): fix worktree; owner reapplies (<kit>/docs/hub-approvals.md "Applying").
6. Skill edits: owner writes skill text on hub (agents' writes refused); push `skill_edit <E-id> submitted` to kit coordinator; worker commits it unchanged.
   - `python <kit>/tools/hub.py skill-edit show <E-id>`: skill, base blob, version V; `skill-edit show <E-id> --out <F>`: exact text.
   - Worktree from main. `git rev-parse main:content/skills/<name>/SKILL.md` not base blob: `skill-edit fail <E-id> --why "the skill changed on main" --at main --checkout <worktree> --if-version <V>` (owner re-applies); stop.
   - Else F over `content/skills/<name>/SKILL.md` (source only; commit.py renders); `python <kit>/tools/commit.py -C <worktree> -m "Skill <name>: owner edit" -b "Text by the owner, hub edit <E-id>" --model "<model name>" content/skills/<name>/SKILL.md`; `skill-edit done <E-id> --branch <branch> --checkout <worktree> --if-version <V>` (hub checks branch blob = text).
   - Cannot commit: `skill-edit fail <E-id> --why "<W>" --if-version <V>`. Review, land as usual (<kit>/docs/hub-skill-pages.md "skill_edits/E-NNN").
