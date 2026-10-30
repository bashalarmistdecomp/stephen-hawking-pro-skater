# Notices

Stephen Hawking Pro Skater is a fan-made mod for a PC port of *Tony Hawk's Pro Skater*. The work in
this repository is MIT-licensed (`LICENSE`), © 2026 Bash Alarmist: the scripts, the engine patch,
the docs, the models (source and prepared), the voice clips and the logo.

## The game

*Tony Hawk's Pro Skater* is © 1999 Activision and Neversoft. This project isn't affiliated with or
endorsed by them.

This repository holds none of the game's files: no disc image, no file extracted from the disc,
and no recording or render of the game. To play, you need your own copy of the game (the
PlayStation disc, USA). The mod reads Tony Hawk's animations from the data the port extracts from
that disc. It does this at build time, and what it builds stays on your machine (`build/` and the
game's `Mods` folder). What ships here, the prepared model in `assets/baked/`, is made only from
this project's own models.

## The port

[TonyWasAProSkater](https://github.com/LANCommander/TonyWasAProSkater) by LANCommander is the PC
port this mod runs on. It is MIT-licensed. This repository includes it as a git submodule, pinned
to one upstream commit, and changes it only through `patches/skater-voice.patch`, which is
applied at build time.

## Stephen Hawking

He is shown here as a tribute, with affection. The project isn't affiliated with or endorsed by
his estate.

- **Voice.** His lines are synthesized with espeak-ng's Klatt voice, which approximates the sound
  of his synthesizer; the clips are in `assets/voice/`. It is not his voice. Some emulators
  recreate his synthesizer's voice. This project doesn't use them, because their authors ask
  that the voice not be reused.
- **Lines.** The words are quotes widely attributed to him.

## Models

The A-pose of Stephen Hawking, `assets/models/source-hawking.png`, was made with MiniMax H3
Ref2VA in ComfyUI. `assets/models/professor.glb` was generated from it, and
`assets/models/wheelchair.glb` alongside it, with [Meshy](https://www.meshy.ai) Image to 3D.
`assets/baked/` is prepared from those two models in Blender (`pipeline/build.sh --bake`).
