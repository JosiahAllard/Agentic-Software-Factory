# Creative dispatches

Read from skill `coordinator-dispatch`, "Creative dispatches".

- Art that is code (skill `specialty-tech-art`), scripted 3D props (skill `specialty-3d-assets`)
  and scripted character rigs (skill `specialty-rigging`) go to senior-worker at least; sound
  (skill `specialty-sound`; the agent cannot hear, so its report ends in a question you put to the
  owner) to worker at least, senior-worker for design; never weak-worker. They work via the
  local generators, against an art direction the owner has decided or, where none exists, by
  proposing strongly different looks for the owner to pick; none hand-sculpts; rigs and their test
  animations are scripted (skill `specialty-rigging`). An asset generation cannot supply -> the
  owner, not another agent.
- Launched sessions start without Blender or Suno, and their agents too: before a 3D or Blender
  (or Suno sound) dispatch, a coordinator launched without `--mcp blender` (or `--mcp suno`) asks
  the PM to relaunch it with that flag.
- Every creative brief carries this block, filled in:

  > FEEL: one sentence. REFERENCES: one to three (a game and what specifically, or an image path,
  > including any inspiration images the owner sent). AVOID: one line. SCOPE OF CHOICE: the agent may
  > propose X, may not change Y.

  Cannot fill FEEL: ask the owner once, through the PM, using the agent's options as the question.
- Anything new with a creative choice runs options first, one dispatch per step: Step 0, a cheap
  sheet of 5-10 very different designs (or designs from the owner's inspiration images) -> the
  owner narrows -> about three takes -> the owner picks, through the PM -> a polish dispatch on
  the pick only. A fix to something the owner already approved skips to the fix.
- Visual output: as the repo profile says; default a self-contained HTML (images inlined base64)
  in the agent's scratch folder, report leading with its absolute path. Never relay a bare
  worktree image path; an image nobody opened has not been reviewed.
- Art team flow (art that ships on screen). The art director (senior-worker + `specialty-tech-art`,
  direction mode) takes the owner-picked concept to a style sheet and asset sheet; the builders
  (senior-worker + `specialty-tech-art` for scenes, shaders and lighting, `specialty-3d-assets` for
  props, `specialty-rigging` for rigs and animation), one per area, build from them and never edit
  the sheet; the critic (skill `specialty-art-critique`; tier in the roster file) scores each round,
  a fresh dispatch each time. You re-dispatch the
  builder with the critic's top 3 gaps until the verdict is PASS.
- Round cap: 4 rounds per area. Then the owner gets the best round, with its scores and open gaps,
  through the PM. The builder's brief carries the cap and the "Visual loop" section of `specialty-tech-art/creative.md`.
