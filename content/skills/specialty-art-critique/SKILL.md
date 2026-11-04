---
name: specialty-art-critique
description: Use when your brief says to load it - scoring one round of art (a scene, prop or character) against its style sheet and concept from the builder's sheet and measured numbers, naming the top 3 gaps and a PASS or ITERATE verdict. Read-only.
---

# Specialty: art critique

Dispatch: analyst at the strong tier, high effort, never a worker type and never the builder of the area; a fresh dispatch each round; reads the repo profile's **Art** section. Read-only: you edit, build and render nothing.

This skill is the one home of the rubric. Score only what the inputs show; a claim the sheet cannot show is unverified, not a point.

1. Inputs: the style sheet and concept; the builder's side-by-side sheet (concept, render, difference); the `art_score.py` numbers (palette distance, value-map difference, silhouette overlap); the round number and the last round's gaps. A character adds the turnaround, the filmstrips and the `motion_check.py` numbers (foot slide, jerk spikes, share of linear keys, worst skin gap). Missing input: say which and score only the axes you can.
2. Still axes, each scored 0-4 (0 absent or wrong, 1 weak, 2 partial, 3 good, 4 matches the concept): palette; lighting and value; shape and silhouette; materials; composition and read.
3. Motion axes for characters, same scale: timing and easing; weight and contact; overlap and secondary motion; pose clarity; deformation.
4. Each score cites what you saw or the number it rests on. The numbers inform, they do not replace looking: read every image.
5. Closed-gap check: for each gap named last round, say closed, partly closed or open, with evidence.
6. Top 3 gaps, most costly first, each with where (area, view, frame) and what to change. Change the build, never the style sheet or the concept.
7. Pass bar: every axis at least 3 and a mean of at least 3.2. The inputs give the round number; on the last round name the best round so far.
8. Format: the scores table (axis, score, evidence), the mean, the closed-gap check, the top 3 gaps. The last line is exactly `Verdict: PASS` or `Verdict: ITERATE`.

## Never

- Score your own work, edit anything, or soften a score to end the loop.
