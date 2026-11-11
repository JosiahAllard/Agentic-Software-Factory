r"""Stage explicit paths and commit with the right trailers, in one call (from the PM kit's
tools/commit.py; docs/commit.md).

commit(Request) -> Outcome(code, lines). Nothing here prints or exits; tools/commit.py does.

The package holds no code here. kitlib.commit.run is the call (commit, request, Request,
Outcome): the order of the checks, the stage, the commit and the put-back on a refusal.
kitlib.commit.checks holds the checks made before anything is written, the merge rule among
them, and the exit codes. kitlib.commit.message holds the message: the body and the trailers.
run imports checks and message; neither imports the other or run.

- The work tree is Request.root (tools/commit.py resolves `-C DIR`, default `.`, with
  kitlib.git.repo.toplevel). A path is relative to that work tree's root, or absolute inside it
  (outside: a usage error); a relative --body-file is the caller's (Request.cwd).
- Explicit paths only: a directory, `.`, `-A` or a glob is a usage error. A named path already
  staged as a deletion is committed as that deletion; nothing unnamed is added.
- --allow-empty (Request.allow_empty) is the one commit of no paths: HEAD's tree unchanged, only
  the message and its trailers (a `Closes:` line). It refuses anything staged, a merge in
  progress and an unborn branch (no HEAD to keep); a hook that changes the commit's tree has it
  undone and refused (commit_only); with a path it is a usage error. Every other check runs as
  for any commit. No paths without it is never an empty commit.
- The body: `-b` is the TEXT (a typed `\n` becomes a newline); --body-file reads it from a file
  (UTF-8, a BOM dropped, CRLF made LF; empty, unreadable or not UTF-8 is a usage error). A `-b`
  whose whole value is one line naming an existing file, or shaped like an absolute path, is a
  usage error: pass --body-file.
- A machine path in the subject or body is refused (kitlib.machinepaths.find, over the machine
  config's `machine_path_folders` and a user profile); an --owner-authored commit is exempt. A
  machine path in the STAGED copy of a file the commit would record is refused by
  kitlib.machinepaths.scan_staged under the repository's own "machine_paths" config, with no
  --owner-authored exemption; the paths this call staged are unstaged again. A machine config the
  kit cannot read is a WARNING, printed once (by the message check, or by the staged check of an
  --owner-authored commit), and user-profile paths are still refused.
- A merge in progress whose MERGE_HEAD is not on <remote>/<main> as last fetched (on_main, the
  rule sync_base.py applies after its fetch) is refused (unsynced_merge_refusal): a base
  sync_base.py would refuse is never committed here instead.
- Anything ELSE already staged is refused (it may be another session's), with one exception: with
  .kit.json "commit".merge_fold, during a merge in progress (one MERGE_HEAD), the files the merge
  staged itself (merge_owned, in kitlib.ownerfiles.rules with the other merge checks named
  here), so only the resolved conflicts are named. An owner-only path is the
  merge's only when its staged state is exactly the merged branch's copy and that branch changed
  it (a deletion included; a conflict resolved to that copy too), or when it is exactly the clean
  merge of both sides' changes and every one of those is the owner's or approved
  (clean_owner_merge); named, it is refused as below.
  An owner-only path the merged branch changed but the merge commits as HEAD's copy (a
  `git checkout HEAD -- <path>`) is one WARNING line (dropped_owner_changes); nothing is refused.
- The commit records only the paths this call named, rendered or let a merge carry (commit_only):
  it is made from a private index (GIT_INDEX_FILE), HEAD's tree plus those paths' staged entries,
  so a file another writer stages after the check above is not swept in. The real index is not
  written; what is still staged after the commit is named in a WARNING line. HEAD and those
  entries are read once, before the machine-path check; an entry staged again after it, a HEAD
  that cannot be read (other than an unborn branch), and a HEAD another writer moves before the
  commit refuse it. This call's commit, made on a HEAD moved after the last check, is undone
  (update-ref back to its parent; the merge state written back for a merge commit) and refused;
  a commit at HEAD that is not this call's is never undone. A hook may change the commit; it is
  told for this call's by its parent, or by the reflog entry its GIT_REFLOG_ACTION tags
  (commit_only).
- Owner-only paths (.kit.json "owner", kitlib.config.owner) are committed only by the owner:
  --owner-authored, which refuses unless the author identity git will record (`git var
  GIT_AUTHOR_IDENT`) is "owner".email (unset, an agent_emails address or any other: refused).
  --owner-approved is required only when an owner-only path is in the commit, and
  every value given must be a blanket entry or a path committed in this call. An --owner-authored
  commit carries `Owner-Authored: yes` in place of Co-Authored-By.
- A change the owner approves on the hub is not committed here: the hub applies it, on the
  request's branch in its linked worktree (kitlib.ownerfiles.apply; docs/commit.md "Owner
  approval"). tools/commit.py's --owner-approval and --consume are usage errors saying so
  (run.HUB_APPLIES).
- Otherwise the trailer is `Co-Authored-By: Claude <model> <noreply@anthropic.com>`, "Claude "
  added when the name lacks it - unless the name is a family in .kit.json
  "commit".coauthor_families or starts with one followed by a space, '.' or '-'.
  The model is required: --model, else CLAUDE_AGENT_MODEL (blank is unset).
- Last, the session line (session_lines): `<key>: <value>` lines that end the body (the hosts'
  attribution.session_trailer keys, `Claude-Session`) move into the trailer block, where git
  reads them; with none, an agent's commit gets its host's own, `Claude-Session:
  https://claude.ai/code/<id>` from CLAUDE_CODE_BRIDGE_SESSION_ID when that is set to a plain id.
- The main checkout is refused (kitlib.git.checkout.main_refusal); --lander-main-checkout is the
  one way past it, and a usage error anywhere else.
- Render on commit (kitlib.content.oncommit; docs/content.md "Render on commit"): in a work tree
  with the kit's layout, when a named path is under `content/` or is `kit.json` or `hosts.json`,
  the work tree's own tools/render_content.py --write runs first, and every rendered file it wrote
  or removed is staged into the same commit (listed on a `rendered` line). A render that fails
  refuses the commit, naming the command to run; what it wrote stays in the work tree.
- After the commit: the hash, the files, and the doc-budget verdict when a .md or owner-only file
  was committed (the kit's tools/doc_budget.py run with `-C <root>`, judged by kitlib.verdict), then
  the doc-index verdict (tools/doc_index.py, same call; nothing when "doc_index".enabled is false).

Codes: OK 0 committed, REFUSED 1 refused or git failed, USAGE 2 a usage error, the main-checkout
refusal or a refused .kit.json.
"""
