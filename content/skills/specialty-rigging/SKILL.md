---
name: specialty-rigging
description: Use when your brief says to load it - rigging and weighting a Blender character as a script, rendering test poses, and sharing one rig across skins.
---

# Specialty: scripted character rigs

Dispatch: senior-worker at least, never weak-worker; reads the repo profile's **Branches and worktrees**, **Build and test**, **Shared resources** and **Art**.

Read `creative.md` (`../specialty-tech-art/creative.md` from this folder) first. Props are specialty-3d-assets work; characters, rigs and skins are yours. Numbered steps are rules; the Advice lines are expectations, not measured facts.

1. Inputs: the body contract by document and section - skeleton, bone names (a bone map), rest pose, scale, facing axis, the clips, any per-skin scenes the engine needs (a first-person body, a ragdoll); the mesh; the export path. No contract: stop and say so.
2. Fit the repo's skeleton by bone names: the bone map is the contract. Script the armature (a generated metarig renamed to the map, or a bone list placed at landmarks measured on the mesh). Before weighting: one root, the contract's parent chain and rest pose, consistent bone roll, exact names, every joint inside the mesh it drives.
3. One continuous mesh with edge loops at every joint. Separate capped tubes per limb segment cannot deform at a bend: rebuild, never weight around it.
4. Each rigid plate sits within one bone's segment, weighted 100% to it; a plate spanning a joint is split there.
5. Weights by script, never hand-painted. Transfer from a reference skin only when the new skin sits within a few mm of that body (measure the gap first). Print the checks: weights normalised, influences per vertex within the contract (4 where silent), no unweighted vertex, every deforming bone used.
6. Before any animation, a scripted pose sweep with a bend test at each joint (arm raised, elbow, knee, shoulder forward and back, hip, squat, reach across, head turned): per pose, the worst skin-to-body gap and part-to-part overlap by distance query.
7. Render the sweep and test animations (a walk, or the repo's shared clips) to a contact sheet: fixed camera and light, one labelled frame per pose with its worst gap, a cycle sampled at fixed steps. Read every image back and say what you see; numbers do not prove shape.
8. Skins share one rig: each binds to the same skeleton, bone map and rest pose so clips are reused, repeats steps 5-7 against the base body's worst gaps, and builds its per-skin scenes.
9. Blender and render runs go under the machine lock's render kind (skill machine-lock-run). No random seeds or timestamps.
10. Validate the export re-imported from disk against the contract (bone names, rest pose, scale, facing, influences), then critique (`creative.md`), deliver the sheet, commit (skill builder).

Advice: rigid plates gap or cut through at elbows, shoulders and hands; test the raised arm and the head and neck first. A metarig generator's script API may not run in the installed Blender: try it early, fall back to a bone list. Motion is keyed, retargeted clips or mocap, as the repo says; mocap cleanup cost is the unknown.

## Character route

For a generated character; the numbered rules above still hold. Caps (faces, textures) are in `<kit>/knowledge/asset-generation/BEST_PRACTICES.md`; never restate them.

1. The owner picks an A-pose sheet (front and side, plain ground). TRELLIS.2 makes the mesh.
2. A cleanup script remeshes to one continuous mesh with loops at the joints (rule 3), bakes the texture and a normal map onto it, and makes LODs by scripted Decimate. Never sculpt by hand.
3. Rig: a Rigify metarig built by script and renamed to the bone map, automatic weights, then rules 5 and 6 (normalised, capped, none unweighted, pose sweep for gaps). Fall back to a bone list.
4. Locomotion: mocap, CMU BVH first (Mixamo only from clips the owner downloads), imported with the bundled BVH add-on and retargeted through the bone map by constraints, then baked. A text-to-motion model is an optional route, only once its licence is cleared.
5. Keyed motion: Bezier easing, never linear; arcs and holds; overlap and follow-through by scripted phase offsets along each chain; tails, cloth and antennae by scripted damped springs, baked.
6. Motion loop (`creative.md`, "Visual loop"): an 8-view turnaround and a filmstrip per clip (fixed camera, sampled frames, onion skin), plus the motion numbers: foot slide per contact, jerk spikes, share of linear keys, worst skin gap per pose. The critic reads stills, so the filmstrips are its input; the owner gets playable clips.

## Never

- Hand-edit an exported rig or mesh, or change the shared skeleton or bone map to fit one skin.

## Report

The contact sheet path or URL first. Then what you saw, in your own words; the worst gap per pose and skin; the validator table; which Advice lines held; files, branch and commit; what the rig still needs.
