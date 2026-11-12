---
name: backlog-pricing
description: Use when you price a backlog record (PM or coordinator) - the checks that close or narrow it first, what each priority 1-5 means, tie-breaks, and who may close what.
---

# Backlog pricing

`python <kit>/tools/backlog.py -C <worktree> set <ID> --priority <n> --priority-by <who>` (skill backlog-ops, step 8). Coordinator: first price 2-5 on unpriced (`--priority-by agent`); never over PM/owner price, never 1.

## A. Before pricing: close or narrow

Check against code, docs; close or narrow when:

- done (`close`, naming what did it) or obsolete (`retire`, citing check);
- duplicate;
- nitpick or overengineering (skill backlog-ops, step 2, "The bar");
- owner-dropped;
- no observed failure, concrete defect, cleanup (file:line) or owner request.

Part fails: narrow, not close.

## B. What each priority means

- p1: owner or PM.
- p2: blocks a p1, red gate, live failure in focus area.
- p3: real defect or owner feature blocking nothing.
- p4: cleanup, docs, dead code, tooling comfort.
- p5: nice-to-have; never closed for sitting at p5.

Ties: focus area, then smaller. Never raise for ease.

## C. Who closes what

- Coordinator retires unpriced record failing A, citing check (`retire <ID> --why "<check>"`; done: `close`, not retire).
- Owner/PM-priced failing A: PM decides, never coordinator.
- Speed record closes only with before/after seconds from next daily or full run (local agent timing not enough), every named untaken lever filed as own record.
