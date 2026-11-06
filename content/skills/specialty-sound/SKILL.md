---
name: specialty-sound
description: Use when your brief says to load it - designing or upgrading game sound effects (synthesis, free libraries, text-to-speech) or generating songs and other audio clips, measured since you cannot hear.
---

# Specialty: sound

Dispatch: worker at least, senior-worker for design (a new sound or family with no picked direction), never weak-worker; reads the repo profile's **Branches and worktrees**, **Build and test**, **Shared resources** and **Audio**.

Read `creative.md` (`../specialty-tech-art/creative.md` from this folder) and `measuring.md` beside this file first.

**You cannot hear.** Never claim to have heard something, never call a clip punchy, muddy or satisfying, and never claim an improvement from the strength of its source. Describe a clip only by what you measured or what its source says; the owner is the ear. A listening model's verdict is evidence you cite, never a claim that you heard the clip.

1. Inputs: which sound (or family) by the game's id, and the event it must convey ("a hammer killing a player", not "a meaty thud"); the FEEL block; replace or propose only (default propose); which sources are live. No audio direction in the profile's **Audio** section (the default profile has none): stop and say so.
2. Sound brief, before any source, per sound in your scratch note: the event, what the player must understand from it, how often it plays, what it sits near in the mix.
3. A new sound or family with no picked direction: Step 0 (`creative.md`) as 5-10 directions of a few lines each (source; layers - transient, body, tail; envelope), with a rough procedural sketch where cheap.
4. Read, don't listen: library tags and descriptions, and game-audio design notes, are your references; cite what you used.
5. Three named options per sound (`creative.md` "Takes"), each testing a different answer to the brief: intent, layers and envelope, numbers, a drop-in path, what to listen for.
6. Take sources in the order `measuring.md` gives.
7. Prepare and measure every clip, before and after, against the whole existing set (`measuring.md`); look at each spectrogram.
8. Critique from the numbers and spectrograms (`creative.md`), deliver the page (each clip playable, before and after, spectrogram and numbers beside the ones it replaces), commit (skill builder).

## Generated audio

Songs and other audio clips from the hosted generator on the profile's **Audio** "Generated audio:" line, which names it, its models, its style and variety controls and its analysis and listen tools; a hosted generator not named there: stop and say so. Local generation stays `measuring.md` source 4.
1. Take budget in clips (one submission can return several), from the brief or the owner's approval for paid generation; spent: stop and report.
2. Make takes differ on purpose, with the generator's own options: model, style prompt, variety or weirdness controls, negative style; one deliberate change per take, never the same settings resubmitted for more.
3. Check every take with the analysis or listen tools against the brief and for robustness: the file complete and its length against the requested one; the ending (a real ending or fade, not a cut mid-phrase); flat or repeated sections; a seamless loop point for anything that loops; after the repo's prepare step, the `measuring.md` numbers by name.
4. Drop failed takes and near-duplicates, judged by analysis numbers or spectrogram plus the listening verdict, never by file name or prompt.
5. Record each take's exact settings where the profile's record rule says, so the owner's pick can be made again.
6. The owner gets a short list (three by default) of distinct, checked takes, each saying what was varied and what the check found.

## Report

Page path or URL first. Then the sound and its sound brief; the options, each with intent, source and why a cheaper source could not; measurements before and after as a table; what you saw in the spectrograms; the critique's three weaknesses; **what the owner should listen for, as a question the owner can answer** ("does the impact still read at distance?"); licence and where you recorded it; branch and commit. For a shortlist, end with "Which of these three, or none?"
