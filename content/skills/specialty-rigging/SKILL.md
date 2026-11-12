---
name: specialty-rigging
description: Use when your brief says to load it - rigging and weighting a Blender character as a script, rendering test poses, and sharing one rig across skins.
---

# Specialty: scripted character rigs

Dispatch: senior-worker at least, never weak-worker; reads the repo profile's **Branches and worktrees**, **Build and test**, **Shared resources** and **Art**.

`creative.md` (`../specialty-tech-art/creative.md` from this folder) first. Props: specialty-3d-assets; characters, rigs, skins: yours. Steps: rules; Advice: expectations.

1. Inputs: body contract (document, section: skeleton, bone map, rest pose, scale, facing axis, clips, per-skin scenes: first-person body, ragdoll); mesh; export path. None: stop, say so.
2. Bone map = contract. Armature by script: metarig renamed to map, or bone list at landmarks measured on mesh. Before weighting: one root, contract's parent chain, rest pose, consistent roll, exact names, joints inside mesh.
3. One continuous mesh, joint edge loops. Capped tube per limb segment cannot bend: rebuild, never weight around.
4. Rigid plate within one bone segment: 100% to it; spanning a joint: split.
5. Weights by script, never painted. Transfer from reference skin only within a few mm (measure gap). Print: normalised, influences per vertex within contract (silent: 4), none unweighted, every deforming bone used.
6. Before animation: scripted pose sweep, bend test per joint (arm raised, elbow, knee, shoulder, hip, squat, reach across, head); per pose worst skin-to-body gap, part overlap (distance query).
7. Sweep + test animations (walk, or repo's shared clips) to contact sheet: fixed camera, light; labelled frame per pose, worst gap; cycle at fixed steps. Read every image back, say what you see; numbers do not prove shape.
8. Skins share one rig (skeleton, bone map, rest pose; clips reused); steps 5-7 again vs base body's worst gaps; per-skin scenes.
9. Blender, renders: machine lock render kind (skill machine-lock-run). No random seeds, timestamps.
10. Validate export re-imported from disk vs contract (names, rest pose, scale, facing, influences); critique (`creative.md`); sheet; commit (skill builder).

Advice: rigid plates gap at elbows, shoulders, hands; test raised arm, head, neck first. Metarig script API may fail in installed Blender: try early, fall back to bone list. Motion keyed, retargeted or mocap per repo; mocap cleanup cost unknown.

## Character route

Generated character; rules above hold. Caps (faces, textures): `<kit>/knowledge/asset-generation/BEST_PRACTICES.md`; never restate.

1. Owner picks A-pose sheet (front, side, plain ground); TRELLIS.2 meshes it.
2. Cleanup script: one continuous mesh, joint loops (rule 3); bake texture, normal map; LODs by scripted Decimate. No hand sculpting.
3. Rig: scripted Rigify metarig renamed to bone map, automatic weights, rules 5, 6. Fallback: bone list.
4. Locomotion: mocap, CMU BVH first (Mixamo only owner-downloaded), bundled BVH add-on, retargeted by bone-map constraints, baked. Text-to-motion optional: only owner-approved MODELS.md model.
5. Keyed: Bezier easing, never linear; arcs, holds; overlap, follow-through: scripted phase offsets per chain; tails, cloth: scripted damped springs, baked.
6. Motion loop (`creative.md`, "Visual loop"): 8-view turnaround, filmstrip per clip (fixed camera, sampled frames, onion skin), `motion_check.py` numbers. Critic reads filmstrips; owner gets playable clips.

## Never

- Hand-edit exported rig/mesh; change shared skeleton or bone map for one skin.

## Report

Sheet path/URL first; what you saw; worst gap per pose, skin; validator table; Advice lines held; files, branch, commit; rig's remaining needs.
