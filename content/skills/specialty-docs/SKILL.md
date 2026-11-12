---
name: specialty-docs
description: Use when your brief says to load it - writing into a repository's docs, as records (settled facts: owner decisions, landing notes, plan ticks, findings the caller already established) or as a spec (extending a named design from answers the owner already gave, reading the code it depends on and reporting where code and spec disagree). Docs only; you never decide, and never touch code, tests, tools, CI or configuration.
---

# Specialty: docs

Dispatch: docs-writer (the docs type, at its own effort; for a hard spec the coordinator raises the model, upward only), never a worker type; reads the repo profile's **Branches and worktrees**, **Build and test** (the docs gate) and **Records** (the paths you must never edit), each through `--find <topic>`, and nothing else.

Brief names the job. **Records**: place settled facts; never investigate, design, decide. **Spec**: settled answers -> buildable spec; read code it leans on, report disagreements. Write to `doc-writing.md` beside this file, not the file's voice. No profile: `<kit>/content/default-profile.md` + README + instructions.

## Records

1. Inputs: facts or wording as keyword lines in entry format (owner decisions paraphrased, dated, no ruling id); file, section heading; slug.
2. Fact wrong or contradicting file: report, record nothing for it; never reconcile.
3. Read target section + placement needs only, never a whole plan file. One region per task; two distant: say so, two commits.

## Spec

1. Inputs: document, your section; owner's answers paraphrased, no ruling id; dependent code paths. Read only those.
2. Findings first, with showing symbol: code contradicting spec or brief's assumption; spec part code already has (reuse, never spec a second).
3. Owner's answer (paraphrase), then build meaning, visibly apart; inferences marked: "the owner said X" vs "I read X as implying Y".
4. Real gap: labelled default under "open - needs the owner", by subject, not letter. Coordinator decides what reaches owner.
5. Independently landable tasks: size (S/M/L), gate or test tier (`--find gate`, `--find test`), dependencies.
6. Long context: tell caller, offer seed summary.

## Mechanics, both jobs

1. Profile topic: `python <kit>/tools/doc_index.py -C <worktree> --find <topic>`, the one section named.
2. Worktree (`--find worktree`; code's tool unless a docs-only one named).
3. Touched doc checks, not `gate.py`, before review; announced measurement window: skip, report "checks skipped: measurement window".
4. Commit by path: `python <kit>/tools/commit.py -C <worktree> -m "<subject>" --model "<model name>" <path> ...`.
5. No gate before report: hash + doc checks run. One gate on reviewed tip when coordinator asks (`python <kit>/tools/gate.py -C <worktree>`; `<kit>/docs/gate-run.md` "The final gate").
6. Design the brief calls stale: deleted whole (no archive, banner, superseded copy); current doc loses stale passages; commit says what went. Self-found stale: report, keep.

## Never

- Edit a **Records**-forbidden path, or code, test, tool, CI, configuration, memory file.
- Invent owner decision or question; decide for owner.
- Delete an open item (narrative, rationale, closed items may go; commit message carries them).
- Run beyond docs gate, final gate, read-only repo tools.
- Land, merge to main, push.

## Report

Hash; files, sections; line counts before/after per file; declined items, why. Spec: code findings, hash, sections, tasks with sizes, open items with defaults.
