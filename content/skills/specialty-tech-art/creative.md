# Creative work: brief, options first, generation, delivery, limits

One copy, shared by the specialty-3d-assets, specialty-rigging, specialty-tech-art and specialty-sound
skills; the other three read it as `../specialty-tech-art/creative.md`.

## The brief block

Every creative brief carries this block, filled in by the coordinator:

> FEEL: one sentence. REFERENCES: one to three (a game and what specifically, or an image or clip
> path, including any inspiration the owner sent). AVOID: one line. SCOPE OF CHOICE: the agent may
> propose X, may not change Y.

A missing FEEL does not stop the work: propose one, mark it a default, and say in your report that
the block was missing. A coordinator that cannot fill FEEL asks the owner once, through the PM,
using your options as the question.

## Options first

Anything new with a creative choice in it runs options first, one dispatch per step. A fix to
something the owner already approved skips to the fix.

0. **Step 0, cheap and wide.** One labelled sheet of 5-10 very different designs that differ in
   kind (silhouette, concept, source), not in tint or proportion. Built from the owner's
   inspiration where the owner sent some, naming what each takes from which. The families on offer
   are toon or cel bands and flat-shaded low-poly, plus higher-detail ones (stylized PBR,
   hand-painted textures over sculpted forms); each stays inside the frame budget, LODs and
   normal-map bakes carrying the detail. Photoreal is refused. Publish it and stop: the owner
   narrows it.
1. **Intent, before building.** In your scratch note: what it must feel like in play, what reads
   at gameplay distance (or in the mix), and what you take from each reference.
2. **Takes.** From the narrowed set, about three takes that differ in kind, on one page, same
   camera or same playback: each labelled with its intent and the knobs that differ, your pick
   marked with why. Small, background or repeated assets can be one take.
3. **Polish only the take the owner picked.** Never polish one the owner has not chosen.

## Generation: local generators

- Which generator makes which asset is the repo's call (the profile's **Art** or **Audio**
  section). Before using the kit's local generators (`python <kit>/optional/comfy/comfy_gen.py`),
  read `<kit>/knowledge/asset-generation/README.md`, with `MODELS.md` and `BEST_PRACTICES.md`
  beside it, and follow its rules. The tool starts ComfyUI itself (`up` for a session, `down`
  after); never ask the owner to open Comfy Desktop.
- A generator outage stops the run: report it; start and install nothing.
- Your session record goes as keyword lines where the profile's **Art** or **Audio** section
  says, failures included, never in your host's user folder.
- Generate only in the windows your coordinator gives you: never in a measurement window, never
  overlapping an engine, network or render run.
- Check the asset policy the profile's **Art** or **Audio** section gives before relying on any
  generated asset.

## Limits a worker type does not enforce for you

A worker type carries the full tool set; these limits are yours to keep, and the brief may only
narrow them:

- The limits the profile's **Art** or **Audio** section sets (paid generation, shelved sources,
  wiring an asset in, what you may not change): keep each one as written there.
- Never invent an asset, a licence or an attribution; record every import's licence and origin
  where the project records them, public domain included.
- Never change behaviour outside your brief's scope to make something look or sound better:
  report the conflict.
- Needs something no route here produces (a hand-sculpted mesh, a hand-painted texture, a
  recorded performance): stop and report exactly what - subject, format, size or
  budget, where it would live - so the owner can source it. Never ship a placeholder that could
  pass for the real thing.

## Delivering it

- Output ends the way the repo profile's **Art** (or **Audio**) section says. Default when it is
  silent: a self-contained HTML page in your scratch folder, outside the worktree, images inlined
  and clips playable, each item beside its measurements and, for a change, its before.
- Your report leads with that absolute path, or the URL where the profile says published pages
  reach the owner. A render or clip that exists only as a worktree path is one the owner will
  not open. No route at all: say so and give workspace-relative paths to every file.
- **Pages, renders and clips stay where you wrote them**: who deletes them is the profile's
  **Shared resources** section's call; list each path in your report for the PM. Renders are never
  committed.

## Visual loop

For art that ships on screen (scenes, props, characters), once the owner has picked a look and a
style sheet exists (skill `specialty-tech-art`, "Direction mode"):

1. Render from the style sheet's camera under the profile's lock, and build a side-by-side sheet:
   concept, render, difference. Add the `art_score.py` numbers. A character adds an 8-view
   turnaround, a filmstrip per clip and the `motion_check.py` numbers (skill `specialty-rigging`).
2. A critic scores the round (skill `specialty-art-critique`). Your report names the sheet and
   numbers it should read.
3. Loop until the critic's verdict is PASS, fixing the gaps it names, up to the round cap the
   coordinator gives. Never edit the style sheet or the concept to pass.
4. Live Blender: when the kit's Blender MCP bridge exists, you may iterate in
   your own Blender with its full tools. The committed headless script stays the build: before
   review, write every live change back into the script and re-run it headless; the review
   compares that render with the live one, and a `.blend` edited live is never committed.

Generated first: a shipped image or mesh comes from the approved generators and a cleanup script
(`<kit>/knowledge/asset-generation/README.md`); build procedurally only where nothing generated fits.

## Critique before you report

Write the three weakest things you can see (or measure) against the brief - the look or the
sound, not the bookkeeping. Fix the one that costs least; list the rest. If you cannot name three,
you did not look.
