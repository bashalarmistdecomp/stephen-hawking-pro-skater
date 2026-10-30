# Stephen Hawking skater: build pipeline

Turns the two Meshy models in `assets/models/` (the professor and the powered wheelchair) into a
skater mod for the THPS1 PC port in `TonyWasAProSkater`. He rides the chair in place of a
board, with his right hand on the joystick.

It works in two stages:

1. **The bake** (Blender) turns the source models into the *prepared model*, `assets/baked/`:
   posed, joined, decimated and textured for the game.
2. **The mod build** (plain Python) turns the prepared model into the game's skater files, with
   Tony Hawk's animations rewritten for the chair. It also adds his voice clips (`assets/voice/`).

The prepared model and the voice clips are part of the repository. Building the mod therefore
needs neither Blender nor espeak-ng: only Python and the game's data.

To play, run `./play.sh` from the project root. It rebuilds the mod first if anything here
changed. `./play.sh --record` does the same and records the game window and its sound to
`ref/recordings/<date>_<time>.mkv`, from when the window appears until you quit (details in the
script's header). `./play.sh --install-desktop` adds it to the app menu, with "Play and record"
on its right-click menu and the bake's `icon.png` as its icon. To build without playing:

```sh
pipeline/build.sh              # build the mod from the prepared model and install it
pipeline/build.sh --bake       # first make the prepared model again in Blender (then commit it)
pipeline/build.sh --no-install # leave the mod in build/mod/
```

The game's data must be there (`./play.sh` copies it off your disc first): the mod build reads
Tony Hawk's animations from `TonyWasAProSkater/build/pc64-linux/Data/Models/hawk.psx`. Nothing
from the disc goes into the repository. The mod it builds (`build/mod/`, and the installed copy)
carries those animations, so it stays on your machine.

It installs to `~/.local/share/Neversoft/Tony Hawk's Pro Skater/Mods/HawkingMod`. In the game,
"Stephen Hawking" is the last skater on the select screen: press Left from Tony Hawk.

Environment overrides: `BLENDER`, `THPS_DATA_DIR`, `THPS_MODS_DIR`.

## Files

| File | What it is |
|---|---|
| `finish.json` | Every hand-picked decision, as data: what to delete, the pose, the IK targets, where the chair goes, budgets, units, and which animations follow the pelvis. Change a number here and rerun. |
| `blender_bake.py` | Stage 1 (headless Blender, `build.sh --bake`). Builds a posed, static, decimated model and writes `baked.json`, the textures and the launcher's `icon.png` (copied to `assets/baked/`), plus `hawking_review.glb` and the check renders `review_*.png` with a 1.75 m reference box (left in `build/bake/`). |
| `build_mod.py` | Stage 2 (plain Python). Reads `assets/baked/` and writes the version 6 skater `.psx`, the front-end `.psx`, `mod.ini` and `Skaters/hawking.ini`. |
| `make_voice.py` | His voice clips, from `finish.json`'s lines (espeak-ng and ffmpeg). Makes only the new or changed lines; `--say "…"` tries any line. |
| `thps_anim.py` | Reads and writes THPS1 skater animation packets (type 44), plus the part transforms. |

## What the bake does to the source models

- **Professor.** Deletes the stray `Icosphere` and the box under his feet: six loose panels at
  floor level. Takes frame 0 of the `Sitting_Clap` action and pitches the hips back 10°. IK puts
  the right wrist on the joystick and the ankles on the footplate, with feet kept flat. Welds the
  pieces Meshy splits apart, then decimates to 3,600 triangles.
- **Wheelchair.** Turns it to face the same way as him and scales it ×1.45. Places the seat under
  his thighs, drops loose fragments under 12 mm, welds, and decimates to 4,400 triangles.
- Textures go down to 1024² RGB565. Normals are smooth, because hard edges split vertices and
  the part budget below is in vertices.

Rigging fixes are kept as data so they can be repeated: `bone_rotate_deg` for extra rotation per
bone, `ik` for targets in the chair's own coordinates, and `keep_rest_rotation` for bones that
stay as they were in the rest pose. The bake log prints how close each IK chain gets and where
the key bones land.

## How it fits the game

- Skater models are a hierarchy of rigid parts with no skinning. The mesh is spread over 21 of
  the 30 part slots, at most 250 vertices each, since faces index vertices with a byte. Slots
  0–2 (board and trucks, hidden during bails) and 18 (the head, scaled by the big-head cheat)
  stay empty.
- The animations are Hawk's 78, rewritten so that every part follows one anchor per frame:
  - **Riding:** the anchor is the board, so flips, grabs and grinds move the chair and rider
    together. A kickflip rolls the whole chair over.
  - **Bails** (`pelvis_anims`): the anchor is the pelvis, with the board's riding offset. The
    chair falls over with him instead of flying off.
- The model's front goes to the game's −z (`front_z`). With +z he rode backwards.
- `FrontModel` (`hking_fe.psx`) is the riding pose turned −25° so that the select screen shows
  him three-quarter on.
- Base stats, trick set and boards are Tony Hawk's (`Base = Hawk`).

## His voice lines

`finish.json`'s `voice` section holds the lines, the voice settings and when each line plays.

`make_voice.py` speaks each line with espeak-ng's Klatt formant voice. ffmpeg then band-limits
it and converts it to 44.1 kHz mono, and the peak is set to −3 dB. The clips are kept in
`assets/voice/`, with `voice.json` recording what each was made from. The script makes only the
lines that are new or changed, so espeak-ng is needed only for those. Without it, a new line is
left out of the mod and the others still play. `build_mod.py` copies the clips to the mod's
`Data/Voice/` and writes a `[Voice]` section in the skater's ini.

To try a line before adding it: `python3 pipeline/make_voice.py --say "Any words."` plays it
and saves it in `build/voice/`.

| Event | Lines | When |
|---|---|---|
| `Start` | "However difficult life may seem…" | once per run, when he first moves |
| `Trick` | "One step closer to the stars." / "Intelligence is the ability to adapt to change." | when a trick scores: `TrickChance` 30%, at least `Gap` 6 s after the last line |
| `Bail` | "Ow." | every bail, cutting off anything playing |
| `AfterBail` | "Without imperfection, we would not exist." | after the "Ow", `AfterBailChance` 50% |

To tune the voice, change `voice.espeak.args` (voice variant `en-us+klatt4`, speed, pitch, word
gap) or `voice.filter`. ACAT can't make the voice: it hands speech to Windows' SAPI.
The emulator of Hawking's own synthesizer (hawkingsvoice.com) isn't distributed, and its authors
ask that the voice not be reused.

**Engine change.** The stock port has no per-skater voice, so `patches/skater-voice.patch` adds
one:
- `[Voice]` parsing in the mod loader;
- `pc_voice.cpp`, which loads the WAVs and decides what plays when;
- a voice-clip track in the mixer, at the sound-effects level;
- four one-line hooks in the game code (`CreateSkatersAndCameras`, `CBruce::DoPhysics`,
  `Trick_Trick`, `Bail`).

`./play.sh` applies it to `TonyWasAProSkater` if it isn't applied yet and rebuilds the game.
The `[Voice]` format is documented in the port's `platform/pc/include/pc/voice.h`.
`THPS_TRACE_SOUND=1` logs each line as it plays.

## Checking a change

1. After `build.sh --bake`, open `build/bake/review_*.png`. Six views, with a 1.75 m box for
   scale. When it looks right, commit `assets/baked/`.
2. Run the game. Or, headless, script the menus with `THPS_KEYS` and take shots with
   `THPS_SHOT`/`THPS_SHOTDIR` (see `TonyWasAProSkater/docs`). From a fresh boot, these
   presses reach his select screen: Return at frame 700, 950 and 1150, then Left at 1420.

## Known limits / next steps

- The tricks are Hawk's board animations applied to the whole chair. There are no
  wheelchair-specific tricks or rider movement yet: the rider is welded to the chair.
- Hook points (grind sparks, the board's contact points) come from Hawk's board, so they land
  approximately.
- Two-player split screen uses the same model (`Model2P` defaults to `Model`).
