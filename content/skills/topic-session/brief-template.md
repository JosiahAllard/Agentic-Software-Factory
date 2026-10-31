# topic-<slug>: <the topic, in a few words>

You are a plain session the owner drives directly, like a normal session. Do what he says here; he decides the design as you build.

## Scope

Build these records on one branch, `<branch>`:
- `<ID>`: <one line: what it is, and anything the owner already decided about it>
- `<ID>`: <...>

Read each record first: `python <kit>/tools/backlog.py -C <worktree> show <ID>`. <The design or page the owner approved, if any, to verify against.>

## Not clashing with the coordinator

- Work only in your worktree, `<worktree>`. Never edit, check out or commit in the main checkout `<repo>`: landers work there.
- Pull each record before you work on it: `python <kit>/tools/backlog.py -C <worktree> --session <key> pull <ID>` (drop `--session <key>` when the records carry no `session:`). Only the records listed above are yours: each has `batch: <branch>`, and any other record's write is refused. A pull refused for another reason (a live pull on another branch, a scope that folds into another branch's work, a resource, `requires:`): stop that record and tell the PM the refusal.
- Found something new: `add` it as a record, never build it here unless the owner says so and the PM has it batched.
- Stay inside <the files or folders of the topic>. Anything outside them: ask the owner first.
- <What is off limits now: paused runs, shared ports, the machine's heavy work.> Heavy runs book the machine lock, as the kit's tools do (the gate books it itself).

## Finishing

- Commit as you go, small and named, by explicit path: `python <kit>/tools/commit.py -C <worktree> -m "<subject>" --model "<model name>" <path> ...`.
- At the end, gate the tip: `python <kit>/tools/gate.py -C <worktree>`. Add `--full` only when you changed a shared path or the gate's configuration.
- Then message `<PM session name>` with the branch, the tip, the gate result and the records it covers. The coordinator reviews it and lands it. You never merge, land or push.
- Never cite ruling ids in repository text.
