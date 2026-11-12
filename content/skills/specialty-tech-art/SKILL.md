---
name: specialty-tech-art
description: Use when your brief says to load it - art that is code: shaders, materials, palettes, UI themes, procedural meshes and textures, lighting, camera, post-processing.
---

# Specialty: art that is code

Dispatch: senior-worker at least, never weak-worker; reads the repo profile's **Branches and worktrees**, **Build and test**, **Shared resources** and **Art**.

`creative.md` beside this file first. Within art direction; silent: propose, never stall. Never change owner-approved look or direction doc.

1. Inputs: direction (document, section), FEEL block, owned vs others' files, lock + port to render. Open direction: owner question, asked by showing answers.
2. Propose mode (no direction): strongly different options, pick none; overrides step 1.
3. No hand modelling, animating, recording. Images, edits, cut-outs, upscales: profile **Art** generators only (`creative.md`); meshes: specialty-3d-assets; audio: specialty-sound. Else: stop, report need.
4. Design question (new look, skin, silent direction): options first (`creative.md`), Step 0 as images or shader/palette variants, same scene, camera. Fix: step 5.
5. Existing shaders, materials, themes first: one product look.
6. Art data in resources where project does, editable without rebuild.
7. Shaders are code: meaning-named uniforms; no per-pixel work on full-screen draws; odd terms commented. Direction's budgets (overdraw, transparency, viewports, lights); unmeasured: state cost.
8. Headless, server paths work: window-only code guarded; headless runs check.
9. Visual loop to critic pass (`creative.md`, "Visual loop"): render per project, locked, after orphan sweep. `<kit>/optional/comfy/render_glb.py`: kit lock wrapper, run directly (skill machine-lock-run). Read image back vs direction, before image. Animated: first, peak, last frames.
10. No render: say so, every visual claim unverified; never describe unseen.
11. Critique (`creative.md`); page (options side by side, or before/after, with cost); commit (skill builder).

## Direction mode

Art director: after owner's concept pick, write style sheet beside it; builders never edit. Flow: Step 0 sheet, owner narrows, takes, owner picks, style sheet, asset sheet. Template:

- Palette: 6-10 hex, each with role.
- Camera: FOV, height, pitch, framing.
- Lighting rig: sun azimuth, elevation, ambient, fog, tonemap, exposure.
- Materials: roughness, rim, toon bands or PBR notes.
- Asset sheet: elements alone, centred, plain mid-grey, shippable generator (`<kit>/knowledge/asset-generation/MODELS.md`); only composed reference concept from reference-only model. Owner picks whole sheet once per theme, before image-to-3D.

## Never

- Claim an asset unseen on disk.

## Report

Page path/URL first; frame, what you saw; pick, why; critique's three weaknesses; files; unverified; cost, how known; needed assets, described; out-of-scope.
