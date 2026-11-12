---
name: specialty-3d-assets
description: Use when your brief says to load it - building a game-ready 3D prop as a Blender Python script, cleaning generated meshes, validating budgets and delivering a contact sheet.
---

# Specialty: scripted 3D assets

Dispatch: senior-worker at least, never weak-worker; reads the repo profile's **Branches and worktrees**, **Build and test**, **Shared resources** and **Art**.

`creative.md` (`../specialty-tech-art/creative.md` from this folder) first. Props: yours; characters, rigs, skins: skill `specialty-rigging`. Sheet reader names object, user, feel, untold.

1. Inputs: what, art direction, FEEL block, real size in project units, neighbours, asset path, using scene/material. No size: ask. No **Art** direction (default profile has none): stop, say so.
2. First report names category once: parametric hard-surface (props, housings, panels, blocky weapons, architecture) scripts well; faces, cloth, creatures, hand-painted detail badly. Then best possible.
3. Creative choice: options first (`creative.md`), Step 0 as images or blockouts.
4. Script, not mesh: named constants on top; load-bearing numbers commented; primitives, explicit vertices, modifiers. Shape change = number change.
5. Generated mesh (`to3d`): raw material; scripted cleanup to scale, pivot, triangle budget, separate glow/tint materials.
6. Split by material: separately tinted, lit, swapped part = own object, material.
7. Engine export conventions (up/forward axis, scale, origin): no scene correction. Deterministic: no seeds, timestamps, interactive state.
8. Render after first run: several angles, one proving relied-on feature (profile, bevel, cutout); read every image back, say what you see. `<kit>/optional/comfy/render_glb.py`: kit lock wrapper, run directly (skill machine-lock-run); engine-side render (profile's lock) ships.
9. Validate re-imported export, never source scene: names, triangle budget, dimensions vs size, one material per object, UVs; each measurement beside budget. Wrong shape passes at right size: never report unseen.
10. Critique (`creative.md`); sheet (renders, measurements vs budgets, shape constants, rebuild command); commit (skill builder).

## Generated first

Prop with picked asset sheet (skill `specialty-tech-art`, "Direction mode"): prefer over primitives (step 4):

1. Owner-picked sheet image.
2. Image-to-3D, TRELLIS.2 (`comfy_gen.py to3d --engine trellis2`); face, texture caps: `<kit>/knowledge/asset-generation/BEST_PRACTICES.md`.
3. Cleanup script (step 5): scale, pivot, budget, emissive parts own materials.
4. Over-budget detail: scripted normal-map bake onto decimated mesh, LODs; validate (step 9).

## Never

- Hand-edit exported mesh; commit one without your script.
- Hand sculpt: report need.

## Report

Sheet path/URL first; what renders showed, fixes between rounds; pick, why; critique's three weaknesses; files; validator pass/fail table; engine render; branch, commit; unmakeable needs.
