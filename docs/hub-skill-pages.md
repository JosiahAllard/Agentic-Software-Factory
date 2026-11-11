# Action Hub: skill edits and pages

## skill_edits/E-NNN
The owner's new text for one kit skill's source, `content/skills/<name>/SKILL.md`, which an agent
commits: the hub writes no repository. Only the source is edited; the rendered copies come from
`tools/commit.py` on the commit (docs/content.md). `E` is the store's id prefix for it
(`kitlib.hubvalidate.REF_COLLECTIONS`); an `E-` id is not a ref, and a page id holding one is refused
("pages/<id> and page_html/<id>").

| Field | Written by | Meaning |
|---|---|---|
| status | owner, agent | `draft` or `submitted` (owner); `committed` or `failed` (agent); `superseded` (the hub, on a source that moved on); `withdrawn` (owner) |
| skill | owner | the skill's folder name, one the hub lists (`GET /api/v1/skills`) |
| text | owner | the whole new SKILL.md, CRLF stored as LF |
| base_blob | server | the git blob id of the source the owner edited |
| created_at, submitted_at | server | when it was written and submitted |
| source_blob | server | for a superseded edit, the blob of the source it no longer fits |
| branch, blob, done_at | agent | the branch the commit is on and the blob of the committed text |
| why, closed_at | agent, owner | why it failed; when it failed or was withdrawn |

base_blob is the blob of the copy of the skills this hub serves (`GET /api/v1/skills/<name>/source`),
taken when the edit is written: the git blob of the text as served (CRLF read as LF, as git stores a
text file), not of the file's bytes, so a copy checked out with CRLF has its blob on main. That copy can lag the kit's main (a plugin export not yet
refreshed, a main checkout behind its remote), so the worker's own check against main is the one that
counts, and the page says so beside the editor.

The routes ("Skill edits" in the `kitlib/hubsite/apiskills.py` docstring):

| Route | Who | What |
|---|---|---|
| `GET /api/v1/skills/<name>/source` | anyone | {name, text, blob}: the whole SKILL.md the hub serves and its blob |
| `POST /api/v1/skill_edits` | owner | {skill, text, submit?}: a draft, or submitted with `submit` |
| `POST /api/v1/skill_edits/<id>/edit` | owner | {text?, submit?, if_version}: a draft, superseded or failed edit only |
| `POST /api/v1/skill_edits/<id>/withdraw` | owner | {if_version?}: any edit not committed |
| `POST /api/v1/skill_edits/<id>/done` | agent | {branch, blob, if_version}: a submitted edit is committed |
| `POST /api/v1/skill_edits/<id>/fail` | agent | {why, current_blob?, if_version}: a submitted edit could not be committed |
| `GET /api/v1/skill_edits` | anyone | the edits, filtered by `status` |
| `GET /api/v1/skill_edits/<id>` | anyone | {item, source}: one edit, and for a superseded or failed one the source text at its `source_blob` (null when the server cannot read it) |

- Who writes: the text is the owner's alone. The three owner writes are OWNER routes, as `POST stories`,
  `stories/<id>/withdraw`, `to_pm` and `finding_marks` are, so an agent's key gets `forbidden` (field
  `role`) before the body is read, and nothing is written. An agent only reports: done and fail are
  AGENT routes.
- The text: at most 65536 bytes of UTF-8 (`kitlib.hubsite.skilledits.MAX_BYTES`), no NUL, and front
  matter whose `name:` is the skill's own: an edit never renames a skill. Anything else is `invalid`
  (field `text`) with nothing written.
- Superseded: submitting when the served source's blob is no longer base_blob makes the edit
  `superseded` with `source_blob`, and so does a fail whose `current_blob` (the blob on main the worker
  read) is not base_blob. The owner re-applies it: an edit of a superseded or failed edit is put on the
  source at its `source_blob` when the server can read that blob's text in the kit checkout (`git
  cat-file`, a read; `kitlib.hubsite.serverepos._kit_text`) and the page shows that text, else on the source
  as the server serves it now; base_blob becomes that blob and the edit goes back to draft, or to
  submitted. So a re-apply onto the blob on main is not superseded again while the served copy lags:
  submitting is superseded only when base_blob is neither the served blob nor the blob of the source on
  main in the kit checkout (`_kit_blob`), and its `source_blob` is then main's blob (the served blob when
  the server cannot read main).
- Done: the blob must be the git blob of the edit's text as UTF-8, else `invalid` (field `blob`). The
  server also reads the blob of the source on `branch` in this machine's kit checkout with `git
  rev-parse` (a read; `kitlib.hubsite.serverepos._kit_blob`) and refuses a branch it cannot read or whose
  blob differs (field `branch`), so an agent cannot mark committed a text it never wrote.
- Pending and push: a submitted edit is a pending item of kind `skill_edit` with no `from`: it is for the
  kit's coordinator (`Api.kit_session_key`: the sessions.json key of the agent session on the kit
  repository, else the repository's configured name when none matches)
  while that session can be reached, else the PM's. Its push (`submitted`) goes to that key and the PM;
  done, fail or withdraw acknowledges it.

Worker steps (skill hub-ops "Skill edits"):
1. `hub.py skill-edit show E-<n> --out <F>` writes the owner's text byte for byte; note its version.
2. In a worktree from main, read `git rev-parse main:content/skills/<name>/SKILL.md`. When it is not the
   edit's base blob, stop: `hub.py skill-edit fail E-<n> --why "the skill changed on main" --at main
   --checkout <worktree> --if-version V` marks it superseded for the owner.
3. Copy F over the source and commit it with `tools/commit.py -C <worktree> -m "Skill <name>: owner
   edit" -b "Text by the owner, hub edit E-<n>" --model "<model name>" content/skills/<name>/SKILL.md`,
   which renders the copies.
4. `hub.py skill-edit done E-<n> --branch <branch> --checkout <worktree> --if-version V`. The branch
   lands as any other.

Tests: tests/hubsite/test_skill_edits.py.

## pages/<id> and page_html/<id>
An agent's document: `pages/<id>` is its card, `page_html/<id>` = {html} its body. In `pages`,
`kitlib/hubsite/schema.py` requires only `title` (a string); the page also reads `repo`, `from`,
`summary`, `refs` (a list of ids), `at` and the store's own `updated_at`, each as text, and orders the
list by `updated_at`, else `at`, newest first. The ids have no prefix: the store assigns none (`Store.add`
refuses `pages`); the publisher names one, and an id that holds a hub item's or a ruling's id anywhere, as
`kitlib.hubvalidate`'s ref pattern finds one (`Q-12`, `R-3`, `plan-Q-1`, `Q-1.v2`), or an approval's
or a skill edit's id (`A-2`, `E-4`), is refused (`_REF_SHAPED`), so a ref that names a page is never
read as an item.

The routes ("Pages" in the `kitlib/hubsite/apipages.py` docstring):

| Route | Who | What |
|---|---|---|
| `POST /api/v1/pages` | agent | {id, title, html, repo?, from?, summary?, refs?, if_version?}: writes the body, then the card |
| `GET /api/v1/pages` | anyone | the cards, filtered by `status` and `repo` like every list |
| `GET /api/v1/pages/<id>` | anyone | {id, html, version} of the body; `not_found` when there is none |
| `POST /api/v1/pages/<id>/delete` | owner | deletes the card, then the body; `if_version` optional |

An agent publishes with `hub.py pages publish ID --title T --html-file F` or the hub MCP tool
`hub_page_publish` (docs/hub-mcp.md "Agent write tools"), and reads one back with `hub.py pages show ID
[--out F]` or `hub_page`. Both run `kitlib.hubvalidate.pagecheck.check` on the file before sending, so a body
the server would refuse is never sent. Publishing over an existing page needs the card's version as
`if_version` (`--if-version`); without it the write is `conflict`. One lock in the hub process
(`PAGES_LOCK` in `kitlib/hubsite/store.py`, which `apipages.py` takes) is held from a publish's reads to its
last write, and over a delete, so of two publishers with the same pin the second is `conflict` with
nothing written. The body is put first and the card second, so a card never names a missing body. Deleting
is the owner's alone: the Docs tab's Delete, with no CLI verb and no MCP tool. It deletes the card, then
the body; when the body's delete fails the card stays deleted and the body is an orphan, which
`kitlib.hubvalidate` warns of and the next publish of that id writes over.

`pagecheck.warnings` also warns, never refuses, of a `video`, `audio` or `source` whose `src` the page policy blocks (`MEDIA_SRC_ALLOWED`, kept equal to `DOC_CSP`'s `media-src`, `data:` today, so `data:` media plays and never warns): the publish succeeds and the reply's `warnings` carry a `media-blocked` finding naming the element and the workaround, an animated GIF or WebP in `<img>`.

What a body may hold (`kitlib/hubvalidate/pagecheck.py`, whose docstring has the full list): at most 512 KiB
of UTF-8 (`MAX_BYTES`); no `script`, `iframe`, `object`, `embed`, `form`, `link`, `base` or other element
of `TAGS`; a `meta` only as exactly `<meta charset="utf-8">` or `<meta name content>` with a name of
`META_NAMES` (viewport, color-scheme, description, author, generator), each attribute once; a `<title>`
only as `<title>text</title>`, with no attribute and no `<` in its text; no `on*` attribute; no `href`
that is not a `#fragment` (a `data:` image on an svg `image` is the one exception); `src` and CSS `url()`
only as `data:`; no `@import`, CSS backslash escape or function of `CSS_FUNCTIONS` (`image-set()`,
`image()`, `src()`). Markup that the check's parser and a browser could read differently is refused too:
`<` or `&` in a style element's text, a self-closing `<style/>`, and the words of `RAW` anywhere in the
text (`<textarea`, `<xmp`, `<noembed`, `<noframes`, `<plaintext`, and the comment and CDATA endings). A
whole document (doctype, `html`, `head`, `body`) passes. A body that breaks a rule is refused whole,
never stripped (`invalid`, field `html`, nothing written). Mermaid is not rendered by the kit: a body with
`class="mermaid"` or a ` ```mermaid ` fence is refused with "render it to svg first", and the publisher
draws the diagram as inline `<svg>`. The listener refuses any request body over 1 MiB (`MAX_BODY` in
`kitlib/hubsite/httpd.py`) with 413 before the check runs, so a body near the 512 KiB cap whose JSON
encoding about doubles it (mostly quotes, backslashes or escaped non-ASCII) is refused there.

`GET /api/v1/snapshot` carries the cards and leaves every body out. Every card has a live Read full
screen: the reader asks `GET /api/v1/pages/<id>` when it opens and says "This document has no body on the
hub server." on a 404.

The body is agent-written html. The page draws it nowhere but in the reader's one frame, made in script
with a `sandbox` attribute that grants nothing (no scripts, forms, same-origin, popups or navigation of
the hub page) and given the body through `srcdoc` behind a meta policy, `default-src 'none'; style-src
'unsafe-inline'; img-src data:; media-src data:; form-action 'none'; base-uri 'none'` (`DOC_CSP` in
`kitlib/hubsite/web/index.html`). The policy blocks what the body loads by itself: images and video or
audio other than `data:`, CSS files, fonts, scripts, frames and form posts. A diagram therefore shows only as an inline
`<svg>` or a `data:` image. Links are not blocked: a link clicked inside the reader moves the frame itself
to that address, where the policy no longer applies (the sandbox still blocks scripts and forms there).
The reader rewrites a bare `href="#x"` to `href="about:srcdoc#x"` (`docFragments`), so an in-page anchor scrolls and sets `:target` (a srcdoc frame's base URL is the hub page's, so the bare form left the frame); inferred from the HTML spec, the owner checks it in a real browser.
Some browsers may also look up the host names of links in advance (DNS prefetch). The page check refuses
such a link, `<link>` and every `<meta>` but the ones above at publish, so a body published through the
check has none; a body placed in the store by hand is not checked. A whole document goes in unchanged,
the policy still first. That a browser still applies it there is inferred from the HTML spec (an iframe
srcdoc document is always no-quirks, and the policy meta opens the head, so the body's later doctype and
`<head>` are ignored), not tested: the owner checks it in a real browser.

Esc closes the reader only while the focus is outside the frame: keys typed inside the sandboxed document
never reach the hub page. The Close button always works and gives the focus back to the button that
opened the reader. An open reader keeps the body it was opened with until it is closed, even when a later
read of the snapshot changes or removes the page; a body that arrives after its reader was closed is
dropped.

`python tools/hub.py validate` (`_check_pages` in `kitlib/hubvalidate/__init__.py`) warns when a page's
title is missing or empty, when a `page_html` has no string `html`, and when a `page_html` has no `pages`
document (only when the `pages` collection exists). It does not warn about a page with no body.

