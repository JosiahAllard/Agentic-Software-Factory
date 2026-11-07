# Shell guard and git hooks

`boot/hook.py <host> PreToolUse`, the kit's one hook entry (`boot/hook.py claude PreToolUse` on Claude), is the kit's one PreToolUse hook (ARCHITECTURE.md "Layers": Guards). `githooks/pre-push` is the kit's guard git hook. Both are thin entries over `kitlib/guard/` (docs/guard-core.md "Hosts" says how a hook reaches it). `githooks/` also holds `pre-commit`, the ledger check, inert unless a repository opts in (docs/ledger.md "Pre-commit check").

**Live and enforcing:** the guard is registered as the PreToolUse hook (`boot/hook.py claude PreToolUse`; "Go-live", step 1), and subagent calls are refused by it (observed: rules `main-checkout` and `write-jail`). `githooks/pre-push` is live only in a repository whose `core.hooksPath` names `githooks/` ("Go-live", step 2).

## Pages

- `docs/guard-core.md`: Output, Refusal log, Rule modes, Hosts, Parsing, State.
- `docs/guard-rules.md`: Rules, Kit paths in advice.
- `docs/guard-jail-roles.md`: Write jail, Generated files, Role policy, Backlog roles, Gate allow, Batch files and cmd.exe.
- `docs/guard-fallback.md`: Fallback, Lock MCP, Session mark, Agent names.
- `docs/guard-owner.md`: Owner lock tools, Owner machine setup, Owner delegation list, Hub secrets.
- `docs/guard-hooks.md`: Pre-push, Owner-only commits, Self-test, Refusal patterns pre-empted, Go-live (after the joint review).

The git jail (rule `write-jail`, the git half): `git_jail` and `git_jail_unknown` in `kitlib/guard/rules.py`; the section is docs/guard-jail-roles.md "Write jail" in `docs/guard-jail-roles.md`.
