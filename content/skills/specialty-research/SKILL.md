---
name: specialty-research
description: Use when your brief says to load it - investigating code, infrastructure or an audit question, how a change would fit this codebase, prior art in other tools, or (one case among them) game concepts. Proposes options with evidence, seams and rough costs for the owner to choose from; builds nothing and writes nothing into the repository.
---

# Specialty: research and investigation

Dispatch: analyst (the read type), never a worker type; reads only what the brief names, plus the repo's instructions and the docs they or the repo profile point to.

Short answer or few constraint-fitting options, each checked against carrying code, config, logs; failure reason beside success reason. Chosen option: worker, or docs-writer with skill `specialty-docs`.

1. Inputs: narrow question ("which callers break if this signature changes", not "look at the gate"); settled constraints; reading; rejected options. No constraints: ask caller.
2. Docs: `python <kit>/tools/doc_index.py -C <worktree> --find <topic>`, one section; never whole index.
3. Seam first: function, tool, config key, registry touched, current behaviour; callers, failure paths, not just happy path; additive or shared logic; reused or removed piece.
4. Audits: claim, falsifying check, command or path:line, result. Counts say what counted, excluded. Producing mechanism before items: design breeding items is the finding.
5. Prior art: tool/game, what it does; cite only sources you read. Range, then cut: kinds, not flavours; one-line why per cut.
6. Against your favourite: each option's strongest objection (wrong fit, excess machinery, existing copy, regression, cost; concept: unfun, unbalanced).
7. Label evidence (`../specialty-docs/doc-writing.md`, "Findings and doc edits"): "the code does X" (cited) vs "I expect Y" (run settles); recalled marked; preference never a finding.
8. Fork: labelled proposed default; never invent owner questions.
9. Long notes: scratch file outside checkout, absolute path in report. Owner page: skill `hub-ops`, step 4.

## Never

- Edit, create, delete in repo; run builds, tests, game.
- Decide what gets built.

## Report

Answer first. Per option, by recommendation: pitch; concrete change; seams, additive/shared; size (S/M/L), riskiest part; strongest objection; what must be true, settleable by test/run/playtest. Then cuts; prior art, sources; forks with defaults.
