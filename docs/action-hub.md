# Action Hub

One private web page, served on your own PC, where you, the owner, answer the PM's questions, work
through hand steps and write new stories when it suits you. Nobody waits on it. The page is
`kitlib/hubsite/web/index.html`; the server is `tools/hub.py serve` (`kitlib.hubsite`), which keeps
the hub's documents in a store folder in git and answers an HTTP API on the loopback address. The
PM uses the same API through the `tools/hub.py` verbs; agents can also reach it through the hub MCP
server (docs/hub-mcp.md), which carries the agent's token and none of the owner's verbs.

The text is split by topic into the files below. A cite of the form `docs/action-hub.md "<section>"`
resolves through this table: read the section in the file named against its title.

| File | What it holds | Section titles in it |
|---|---|---|
| docs/hub-using.md | the owner's page: its tabs and sections | "Using the hub" |
| docs/hub-serving.md | running the server: settings, health, restart, stale code, the store folder | "Serving it", "Stale code" |
| docs/hub-pm.md | the PM's side: the `hub.py` verbs, who handles a pending item | "For the PM", "Who handles a pending item" |
| docs/hub-push.md | push notifications, the timed jobs, store upkeep | "Push notifications", "Timed jobs", "Filed answers" (the push bullet), "Store upkeep" |
| docs/hub-questions.md | questions: fields, class, binding to a record, filing the answer, done by the hub, standing rules | "questions/Q-NNN", "A question's class", "A question bound to a record", "Binding the open questions", "Filing the answer", "Done by the hub", "A standing rule" |
| docs/hub-records.md | inbox, PM notes, to-dos, stories, repos, schedules, finding marks | "inbox/M-NNN", "Lock-window notices", "to_pm/P-NNN", "todos/T-NNN", "The owner's note on a done to-do", "stories/S-NNN", "repos/<name>", "schedules/<task>-<repo>", "finding_marks/<fingerprint>" |
| docs/hub-skill-pages.md | skill edits and published pages | "skill_edits/E-NNN", "pages/<id> and page_html/<id>" |
| docs/hub-approvals.md | approvals, landing events, hand-offs | "approvals/A-NNN", "landings/L-NNN", "handoffs/C-NNN" |
| docs/hub-views.md | the machine lock routes, the live views, the record page | "Machine lock routes", "Views read live", "Documents named in a question", "The record page" |
