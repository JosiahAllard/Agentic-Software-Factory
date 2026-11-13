# commit.py and machine_paths.py

Two tools. `tools/commit.py` is the entry over `kitlib/commit`. `tools/machine_paths.py` is the entry over `kitlib/machinepaths`. Each library's module docstring holds the full rules, and this page holds the contract.

## commit.py

`commit.py [-C DIR] -m SUBJECT [-b BODY | --body-file FILE] [--model NAME] [--host NAME] [--owner-authored] [--owner-approved VALUE ...] [--lander-main-checkout] PATH ...`

`--owner-approval` and `--consume` are usage errors (exit 2): a change the owner approved on the hub is committed by the hub, never by an agent ("Owner approval" below).

`commit.py [-C DIR] -m SUBJECT [-b BODY | --body-file FILE] [--model NAME] [--host NAME] [--owner-authored] --allow-empty` commits no paths: HEAD's tree unchanged, only the message and its trailers, for a branch that needs only a `Closes:` or `Part-of:` line. It is refused (exit 1) when anything is staged, a merge is in progress, or the branch is unborn ("no HEAD to keep"); a hook that changes the commit's tree (a pre-commit hook staging a file) has the commit undone and refused (`commit_only`, `undo_commit`); a PATH with it is a usage error (exit 2). Every other check below runs as for any commit, and the host trailer is added as usual (`Request.allow_empty`, `commit_only`). Without the flag, no PATH is still a usage error: no paths never means an empty commit.

`--host NAME` names the agent host for the Co-Authored-By trailer: a host in `hosts.json`, default `PM_KIT_HOST`, else the registry's default host (Claude). `--provider` is its old name, still accepted. A name `hosts.json` lacks is a usage error (exit 2) before any work. The trailer comes from `kitlib.attribution`: Claude's is `Claude <model>` as below; Codex's needs the actual explicit model id, emits `Codex <model-id> <noreply@openai.com>` and never uses CLAUDE_AGENT_MODEL. Explicit paths, owner identity/approval, staged-file and document checks keep their rules. The shared trailer classifier recognizes both forms.

It stages exactly the PATHs named and commits them in the work tree that DIR is in (default: here). `-C` is resolved by `kitlib.git.repo.toplevel`.
- A path is relative to that work tree's root, or absolute inside it.
- `./` and `..` are folded before the owner-only check.
- A relative `--body-file` is relative to the caller's directory.
- No flag may be abbreviated.

What it refuses (`kitlib/commit/run.py`, `_commit`; the checks are `kitlib/commit/checks.py`):
- **Explicit paths only.** A directory, `.`, `-A`, `--all` or a path with a `*` is a usage error (a `?` or `[` pattern is not tested). A path outside the work tree is a usage error too.
- **A merge of a base sync_base.py would refuse.** During a merge, every MERGE_HEAD must be on `<remote>/<base>` as last fetched (`on_main`, the predicate `sync_base.py` asks right after its fetch); one that is not, or one git cannot place, is refused (`unsynced_merge_refusal`), and the refusal points to `sync_base.py` and the coordinator. `<base>` is `<main>` unless sync_base.py stopped on a conflict: it then writes `kit-sync-base` in the work tree's git dir (`git rev-parse --git-path`; `checks.record_sync_base`) naming its `--base` and the commit it merged, and a MERGE_HEAD that is that commit is judged against that base. The remote is always `.kit.json`'s. A successful commit removes the marker. A refused sync is never redone as `git merge --no-commit` finished with commit.py.
- **Foreign staged files.** Anything already staged and not named is refused, because it may belong to another session.
  - With `"commit".merge_fold` on, during a merge with one MERGE_HEAD, the files the merge staged itself are exempt (`kitlib.ownerfiles.rules.merge_owned`).
  - An owner-only file is exempt only when its staged state is exactly the merged branch's copy: the same mode and blob as `MERGE_HEAD`'s, or a deletion of a path `MERGE_HEAD` deleted (`carries_merged_copy`). That branch must have changed it from the merge base, so the merge is carrying the owner's change in, not undoing this branch's. Every commit the merge brings in (reachable from `MERGE_HEAD`, not from `HEAD`) that changes it must be authored by the owner or carry an owner approval the hub verifies, judged as `gate land` judges a branch (`non_owner_paths`, through `kitlib.ownerfiles.rules.violations` against `HEAD`); when git cannot tell, it stays refused. A commit reachable from `<remote>/<main>` as last fetched is not judged again (`landed_main`, the ref pre-push trusts): it was judged when it landed, and an approved one's approval is spent once it is there (and stops verifying 30 days after the owner's decision, or whenever the hub is down). A conflict resolved to that copy counts too, and so does the clean merge of both sides' approved changes ("Owner approval", "A merge of approved changes"); any other staged state, an unresolved conflict, or an octopus merge stays refused.
  - Naming such a file is still refused as owner-only. The refusal says to leave it out of the list only for a path the merge would carry unnamed.
  - A file another writer stages after that check is not committed: the commit is built from a private index holding only the named, rendered and merge-carried paths (`commit_only`). Whatever is still staged after the commit is named on a `WARNING: staged by another writer during this call` line.
- **A narrowed record.** A path a narrowing pull gave to another live record is refused, the whole commit with it ("A narrowed record" below).
- **HEAD moved.** A HEAD another writer moves before `git commit` refuses the commit (`commit_only`). HEAD is read back once after `git commit`.
  - A HEAD read back whose first parent is the HEAD checked, holding the tree and message this call built, is this call's commit. When a hook changed it (a commit-msg hook adding a trailer, a pre-commit hook staging a file into the private index), it is still this call's unless the tagged reflog entry below names another commit.
  - A hook's `git add` reaches only the private index. For each path a hook changed, the real index is set to the commit's copy when it still holds the copy from before the hook (`align_index`, an `index` line), so neither the next commit nor a `WARNING` takes it back. A path removed by the hook is removed from the real index. If the real index holds another copy of that path (someone staged it before or during the call), or the index cannot be written, the commit stands, that copy stays staged, and the call is refused (exit 1) with the `git restore --staged --source=<commit>` command that takes the commit's copy. The index is read (`ls-files`) and written (`update-index`) in two git calls: a path staged between them is overwritten with the commit's copy.
  - Otherwise `git commit` ran with `GIT_REFLOG_ACTION` set to a tag of this call's own (`reflog_tag`), and the one HEAD reflog entry carrying it names this call's commit and the HEAD git made it on (`tagged_entry`).
  - If the other writer committed after the check but before git read HEAD, this call's commit lands on top of theirs and lacks their change. While it is still HEAD it is undone (`update-ref` back to its parent) and refused, and the other writer's commit stays as it is. MERGE_HEAD, MERGE_MSG and MERGE_MODE come back only when the undone commit was a merge; CHERRY_PICK_HEAD, REVERT_HEAD and SQUASH_MSG are not restored.
  - If a third commit already sits on top of it, nothing is undone: the refusal (exit 1, `NOT undone`) says this call's commit was made on the other writer's, names both, and says to restore the changes it may undo by hand.
  - A commit made on the HEAD checked, with another writer's commit on top of it, stands: exit 0, the `committed` line names this call's commit, and a `NOTE:` line names theirs. If HEAD no longer contains it (another writer moved HEAD elsewhere), the call is refused, `NOT undone`.
  - With no tagged entry (no reflog, or the fake git, which writes none), a commit at HEAD whose first parent is the HEAD checked is this call's, as above; any other counts as this call's only when its tree and message are this call's, and anything else is refused, `NOT undone`, with a `git log -3` hint.
  - A HEAD read back with no parent, other than this call's first commit on an unborn branch, is refused, `NOT undone`.
  - A HEAD git cannot read is refused too, except on an unborn branch.
- **Machine paths.** A machine path in the subject or body is refused (`kitlib.machinepaths.find`), unless the commit is `--owner-authored`.
  - A machine path in the staged copy of any file being committed is refused too, under the repository's own `"machine_paths"` config. There is no owner exemption here. This check fails closed.
  - The message check runs before anything is staged; the staged-copy refusal unstages what this call staged.
- **Owner-only paths** (the `owner` section; the rule is in `kitlib/config/owner.py`) are committed only by the owner, with `--owner-authored`.
  - `--owner-authored` requires the author identity git will record (`git var GIT_AUTHOR_IDENT`) to be `"owner".email`. It must not be in `"owner".agent_emails`.
  - `--owner-approved` is required only when an owner-only path is in the commit.
  - Every `--owner-approved` value must be a blanket entry or a committed path. It is written in its canonical spelling under `"owner".trailer`.
  - Or the owner approves the change on the hub, and the hub commits it ("Owner approval" below).
  - Or the path is in `"owner".shrink_only` and its change to HEAD only deletes lines, in the work tree and again in the staged copy after staging (`shrunk_paths`, with the predicate `gate land` and pre-push use: docs/guard-hooks.md "Owner-only commits"). Any other change to it, or one in a merge in progress, stays refused; a git failure refuses.
- **The main checkout** (`kitlib.git.checkout.main_refusal`), unless `--lander-main-checkout` is given. That flag is a usage error anywhere else.

The message is the subject, then the body, then the trailers:
- The trailers are the `--owner-approved` lines, then `Co-Authored-By: Claude <model> <noreply@anthropic.com>`.
- An `--owner-authored` commit carries `Owner-Authored: yes` instead of `Co-Authored-By`.
- The model comes from `--model`, else `CLAUDE_AGENT_MODEL`. A blank value counts as unset; with none, or with `--model` and `--owner-authored` together, the call is a usage error.
- `"Claude "` is added unless the name is a `"commit".coauthor_families` family, or starts with one followed by a space, `.` or `-`.
- The session line comes last (`kitlib.commit.message.session_lines`). Lines of the form `<key>: <value>` that end the body, `<key>` being a host's `attribution.session_trailer` key in `hosts.json` (`Claude-Session`), are taken out of the body and written in the trailer block, where git reads them as trailers. This holds with or without the environment variable below, so a `-b "...~~Claude-Session: <url>"` is a trailer, not a body line. The same line anywhere else in the body stays in the body.
  - With no such line, an agent's commit on Claude gets `Claude-Session: https://claude.ai/code/<id>`, `<id>` being `CLAUDE_CODE_BRIDGE_SESSION_ID`. Nothing is written when that variable is unset, blank, or not a plain id (letters, digits, `_`, `-`).
  - The URL prefix and the variable name are harness facts observed on 2026-10-14, not a documented contract. A host change to either is a `hosts.json` edit.
  - An `--owner-authored` commit gets no session line from the environment. Codex has no session trailer (`"session_trailer": null`).
- The body comes from `-b TEXT`, where a typed `\n` becomes a newline (`~~` also becomes a newline; for a literal `~~` use --body-file), or from `--body-file FILE`, read as UTF-8 with a BOM dropped, CRLF changed to LF and trailing whitespace and leading blank lines trimmed; an unreadable, non-UTF-8 or empty file is a usage error.
  - `-m` takes the subject once; a second `-m` is a usage error (exit 2) pointing at `-b` / `--body-file`, and nothing is committed.
  - A `-b` whose whole value names an existing file, or looks like an absolute path, is a usage error.

**Render on commit** (docs/content.md "Render on commit"; `kitlib.content.oncommit`): in a work tree with the kit's layout, a named path under `content/`, or `kit.json` or `hosts.json`, makes it run that work tree's `tools/render_content.py --write` before staging. Every rendered file the render wrote or removed is staged into the same commit, under the same foreign-file and owner-only checks, and a `rendered  <paths>` line follows `files` (`no rendered file changed` when the render changed nothing). A render that fails is refused (exit 1) with the command to run; what it wrote stays in the work tree. Any other repository, or a commit naming no render source, starts no render.

After a commit that includes a `.md` file or an owner-only file, it prints the doc-budget verdict.
- The kit's `tools/doc_budget.py -C <root>` produces it, and `kitlib.verdict` judges it.
- A `doc index` verdict follows (`kitlib.commit.run.doc_index_check`: `tools/doc_index.py -C <root>`, judged on `DOC INDEX OK` / `DOC INDEX FAILED`). Either failing prints the follow-up-commit notice. `.kit.json` "doc_index".enabled false prints nothing.
- With no such tool, the verdict is `docs      NOT CHECKED - ...`; with no `doc_index.py`, the doc index line is a NOT CHECKED line too.
- A failing verdict leaves the commit in place. It only asks for a follow-up commit.

| Code | Meaning |
|---|---|
| 0 | committed (`committed <hash>  <subject>`, `files     ...`, and `rendered  ...` after a render) |
| 1 | refused (`refused: ...`), or git failed (`git add failed`, `commit failed`, `nothing to commit in`) |
| 2 | `USAGE: ...`, the main-checkout refusal, or a refused `.kit.json` |

**Known limits** of the merge check:
- The owner's own commit of a merge through commit.py is refused too; `--owner-authored` is no exemption.
- A session with no agent mark (a topic session, the owner's chat) can write the `kit-sync-base` marker or move `refs/remotes/origin/main`, so the check binds only marked sessions and subagents, the same as gate results.

## A narrowed record

A narrowing of two colliding records apart writes a `narrowed: <original member> <other id> <other branch>` line on each, one per (original member, other id) (`kitlib/backlog/record.py` FIELDS; split at the last two spaces, so a member may hold a space). `pull` of a record `next` offers by narrowing (NARROWS) writes it on both records in one store write; a lead writes it by hand with `set --narrowed` and clears it with `set --unset narrowed`, as `next`'s RESTORE line names once the other record holds its part no more: landed, retired, deleted or released (docs/backlog.md "next: a short offer's causes").

Before it commits, commit.py reads the store `backlog.py -C <root>` opens (the PM data worktree's live store, else the configured one), once and without a fetch (`kitlib.backlog.narrowed.check`). For each record pulled by the committing branch that carries such lines, every named path is checked: both sides of a rename, which git records as a delete and an add; paths a merge carries are not checked. Who commits does not matter: an `--owner-authored` commit is checked like any other, and so is the hub's apply of an approval (docs/hub-approvals.md "Applying"), since a narrowed-away path collides whoever commits it. A path is refused when it falls in an original member, outside the record's scope, while that line's other record is still `pulled`; a path in the split member that neither record holds is refused the same way. One hit refuses the whole commit, with the fixed text: `REFUSED: needs <path>, held by <other id> on <other branch> (narrowed for <id>). Stop and report "needs <path>, held by <other id>". Instead: backlog.py block <id> --on item:<other id> --reason "needs <path>"`. The one variable part is a rename suffix after the first `<path>`, `(rename of <old>)` or `(renamed to <new>)`, added only when the two sides are a rename with unchanged content (the same blob); an edited move names only the refused side. Members of a PM data store shared by repositories (`<area>/<path>`) are compared without their area prefix, unless the area names a top-level entry of the repository (`kitlib.backlog.scope.in_repo`), as the landing scope check compares them. Once the other record is no longer pulled the path commits, and the landing scope check refuses it until the original member is restored with `set --scope` (docs/gate.md, Landing step 4).

A `narrowed:` line the check cannot read refuses every commit on that branch until a lead corrects it. A store that cannot be read gives a `WARNING: the narrowed-record check could not read the backlog store ...` line and the commit goes ahead: every commit in every repository goes through this check. An unreadable record gives that warning too, but the readable records are still checked, so a narrowed record's hit beside it is refused; the landing scope check likewise refuses a narrowed record's miss when another record of its store is unreadable (docs/gate.md, Landing step 4).

Known limit: with the store itself unreadable both at commit and at landing, no narrowed record is read: the landing scope check only warns (an unread store never refuses there), so such a path can land.

## Owner approval

A change to owner-only paths that an agent asked for and the owner approved on the Action Hub (docs/hub-approvals.md "approvals/A-NNN") is committed by the hub, on the branch the request names, in that branch's linked worktree, as the owner approves it (docs/hub-approvals.md "Applying"; `kitlib/ownerfiles/apply.py`). An agent never commits it: `--owner-approval` and `--consume` are kept only as usage errors (exit 2, `kitlib.commit.run.HUB_APPLIES`) that say so and name `hub.py approvals apply A-<n>`, the owner's verb for an approval the hub could not apply. The agent asks with `hub.py approvals add` or `hub_approval_request` from a checkout on its branch, leaves the approved paths at HEAD's copy, and reads the commit from `hub.py approvals show A-<n>` or the `applied` push.

Landing it: `gate land` and the pre-push hook (`kitlib.ownerfiles.rules.violations`) pass a non-owner commit that changes an owner-only path only when it carries exactly one `Owner-Approval: hub:A-<n>`, has one parent, changes no path outside the approval, and makes the approved change on that parent (`approvals.delta_error`). At the approval's `head` each approved path must be its base blob. At the commit each path must be what the approved change gives on the parent: the approved blob when the parent holds the base or the approved blob, else a clean `git merge-file` of parent, base and approved content (`approvals.delta_result`), as a regular file in the parent's mode (100644 or 100755: a symlink or gitlink entry refuses, `ownerfiles.rules.irregular`). A delete must leave no file. The hub must then verify the approval for its signed base as consumed (by any commit, so a rebuilt branch keeps it). A file approval, used or not, holds until it is spent or 30 days after the owner's decision (`APPROVAL_LIFE_S`, `kitlib/ownerfiles/approvals.py`). It is spent once a commit that main as last fetched reaches carries its trailer (`approvals.spent`); any other commit carrying it is then refused. A merge, a root commit, a conflict or any doubt of the hub keeps it refused.

A merge of approved changes: a sync merge of two branches that each changed the same owner-only file in an approved (or owner-authored) commit needs no new approval. With `"commit".merge_fold` on and one MERGE_HEAD, the merge carries the file unnamed (`merge_owned`, `clean_owner_merge`) when:
- its staged copy is exactly the clean merge of HEAD and MERGE_HEAD (`git merge-tree --write-tree`, the test every merge-staged path passes) and merge-tree does not list it as conflicted, so an extra edit or a hand-resolved conflict refuses;
- it is a regular file (100644 or 100755) of the same mode, staged and under the same name at HEAD, MERGE_HEAD and every merge base (`git merge-base --all`), so a rename, an add, a delete or a mode change refuses (a symlink on either side is a merge conflict: "distinct types");
- `git check-attr merge` for it (work tree and index) is `unspecified` and `git config --get merge.default` finds none (exit 1; git applies that driver to every unspecified path), so a merge driver (`merge=union`, `-merge`, `merge.default`) refuses, as does a git that cannot say;
- every commit on either side that changes it is the owner's or carries an approval the hub verifies, judged by `kitlib.ownerfiles.rules.violations`: MERGE_HEAD against HEAD (`non_owner_paths`), and HEAD against MERGE_HEAD (each approval is judged on the commit that carries it, so each side's are checked, never the result's blob). Commits on `<remote>/<main>` as last fetched are not judged again.
An octopus merge carries nothing. The merge commit itself then changes nothing beyond the clean merge, so `gate land` judges only the two approved commits, as before. The hub's apply refuses while a merge is in progress in the worktree. `sync_base.py` applies the same rule through `merge_owner_refusal` before it commits a clean merge (docs/sync-base.md).

A merge that joins both sides: when both sides changed lines of the same owner-only file and the merge conflicts on it, the agent may resolve it and commit it, named, with no new approval, when the staged copy only joins the two sides' changes. That is the base lines both sides kept, in order, and every line either side added, each once and in its side's order. The two sides' added lines may sit in any order between the kept lines around them, and a run both sides added alike in one place is held once. So a union of both sides' additions joins them, as does a deletion beside the other side's addition, or both sides' deletions. This is one more way to accept a merge, beside the merge-tree rule above, never in place of it. Each of the following refuses it:
- a line neither side had;
- a second copy of an added line;
- a line either side deleted;
- a line both kept, dropped;
- a line one side added, dropped;
- a line moved, or a side's added lines reordered.

Lines are compared as bytes split after each LF, so a changed line end (CRLF for LF) or a dropped last line end is a change and refuses. Which lines a side kept and added is what Python's `difflib` matches between it and each merge base, a run of added lines moved down past a kept line equal to its first, so that JSON-shaped rows two sides add in one place are placed alike (`kitlib.ownerfiles.rules.combines_changes`). A resolution that `difflib` reads otherwise, a check past its cost bounds (`JOIN_PAIRS_MAX`, 8 million equal line pairs between a base and a side, about eight times docs/module-map.json's; `JOIN_STATES_MAX`, 1,000 readings of the result at once) refuse, and need an approval. With `"commit".merge_fold` on and one MERGE_HEAD, `tools/commit.py` takes such a path when every commit on either side that changes it is the owner's or approved (as above: `non_owner_paths`, `ours_unapproved`), so a side with an unapproved commit refuses however the copy joins. It then judges the staged copy (`staged_unjoined`, `kitlib.ownerfiles.rules.joined_merge`). The file must be a regular file of one mode at HEAD, MERGE_HEAD, every merge base and the staged copy; a NUL byte, no merge base, an octopus merge and any git failure refuse. `gate land` and the pre-push hook pass the merge commit by the same predicate (`violations`) and still judge every commit on either side on its own. Any other resolution needs an approval.

Known limit: the signature binds each path's blob, not its mode. The hub's commit keeps HEAD's mode for a path HEAD holds and writes a new file as 100644 (`apply._target`). The landing check (`approvals.delta_error`) refuses a mode change whenever the parent holds the file; it accepts either regular mode only for a new file (no file at the parent).

Elsewhere the trailer is shown and claims nothing. The review pack's co-author section shows `| owner approval hub:A-<n>` on the commit's row (`kitlib.reviewpack.coauthor_section`). Neither it nor `tools/trailers.py` reads the trailer as an owner claim (`kitlib.trailerscheck.trailers`), and `"owner".trailer` may not be `Owner-Approval` in any case (`kitlib.config.owner.trailer_problem`).

## machine_paths.py

`machine_paths.py [-C DIR]` (`--root` is an alias of `-C`) searches every file git tracks in the work tree (`kitlib.machinepaths.tracked_files`), never an untracked one. It looks for a drive-rooted machine folder, or a drive-rooted user profile, or a posix home (`/home/<name>`; a placeholder, `/home` alone and a path that merely contains `home` do not match). The machine folders are the machine config's `machine_path_folders` (docs/kit-json.md "Machine config keys"); the kit codes no folder name. Unset, only drive-rooted user-profile paths are searched, and the tree sweep prints a `NOTE:` line naming the key (`kitlib.machinepaths.UNSET_NOTE`). The tree sweep, the staged-file check of commit.py (`kitlib.machinepaths.scan_staged`), the message check of commit.py (`machine_path_error`) and the free-text check of backlog.py (`kitlib.backlog.plan.refuse_machine_paths`) all read it. A machine config that cannot be read does not stop any of them: user-profile paths are still searched, and each prints a `WARNING: machine config ...` line that names no value from the file: commit.py prints it once (the message check, or the staged-file check of an --owner-authored commit), and backlog.py adds it to a refusal or to the lines of the write (`kitlib.backlog.store.add_plan_note`). The `kitlib/machinepaths` docstring gives the pattern and the allow-list rules.

| Code | Meaning |
|---|---|
| 0 | `MACHINE PATHS OK (...)`; WARNING lines (a skipped file, a stale allow entry) do not fail |
| 1 | `MACHINE PATHS FAILED: ...`, a hit off the allow-list |
| 2 | `MACHINE PATHS NOT CHECKED: ...`: git could not list the tree, nothing was read, the config was refused, or `-C` is not in a work tree; or `USAGE:` |

## `.kit.json` keys read

docs/kit-json.md lists every key with its default.
- `commit.merge_fold` and `commit.coauthor_families` are read by commit.py, and also by sync_base.py (`merge_fold`) and the ledger's commits (`coauthor_families`).
- `owner.*` (paths, paths_except, email, agent_emails, trailer, shrink_only) is read by commit.py. It comes from the working tree's `.kit.json`.
- `checkout_guard.rule_pointer` is the refusal's pointer to where the rule is written down.
- `repo.main_branch` is named in the `--lander-main-checkout` usage error.
- `machine_paths.*` (allow, fixtures, known_binary, scan, fallback_vars) is read by both tools. commit.py reads it through `scan_staged`.
