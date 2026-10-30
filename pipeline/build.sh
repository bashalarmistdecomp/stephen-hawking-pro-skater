#!/usr/bin/env bash
# Builds the Stephen Hawking skater mod and installs it. ./play.sh runs this when the mod is
# missing or older than its sources.
#
#   pipeline/build.sh              build the mod from the prepared model and install it
#   pipeline/build.sh --bake       first make the prepared model again, in Blender
#   pipeline/build.sh --no-install leave the mod in build/mod/
#
# The prepared model (assets/baked: the posed, decimated mesh and its textures) is part of the
# repository, so Blender is only needed for --bake: after changing the source models
# (assets/models) or finish.json's bake settings. His voice clips (assets/voice) are too:
# espeak-ng is only needed for new or changed lines (make_voice.py). The mod itself is built on
# your machine because it carries Tony Hawk's animations, read from your copy of the game.
#
# Every decision lives in pipeline/finish.json; change it there and run this again.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
GAME="$ROOT/TonyWasAProSkater"
DATA="${THPS_DATA_DIR:-$GAME/build/pc64-linux/Data}"
DEFAULT_MODS="$HOME/.local/share/Neversoft/Tony Hawk's Pro Skater/Mods"
MODS="${THPS_MODS_DIR:-$DEFAULT_MODS}"
BLENDER="${BLENDER:-blender}"
BAKED="$ROOT/assets/baked"

bake=0
install=1
for a in "$@"; do
    case "$a" in
        --bake) bake=1 ;;
        --no-bake) ;;   # the default now
        --no-install) install=0 ;;
        *) echo "unknown option $a" >&2; exit 2 ;;
    esac
done

if [ ! -f "$DATA/Models/hawk.psx" ]; then
    echo "no $DATA/Models/hawk.psx: run ./play.sh first (the game copies its data off your disc)" >&2
    exit 1
fi

if [ "$bake" = 1 ]; then
    command -v "$BLENDER" >/dev/null || { echo "--bake needs Blender (or set BLENDER)" >&2; exit 1; }
    mkdir -p "$ROOT/build/bake"
    echo "baking in Blender (log: build/bake/blender.log)"
    "$BLENDER" -b --python "$ROOT/pipeline/blender_bake.py" -- \
        --root "$ROOT" --finish "$ROOT/pipeline/finish.json" --out "$ROOT/build/bake" \
        > "$ROOT/build/bake/blender.log" 2>&1 || { tail -30 "$ROOT/build/bake/blender.log"; exit 1; }
    grep '^\[bake\]' "$ROOT/build/bake/blender.log"
    grep -q '^\[bake\] done' "$ROOT/build/bake/blender.log" || { echo "bake did not finish" >&2; exit 1; }
    # the prepared model: what build_mod.py reads (the review renders stay in build/bake)
    mkdir -p "$BAKED"
    cp "$ROOT/build/bake/baked.json" "$ROOT/build/bake/icon.png" "$BAKED/"
    python3 -I -c 'import json, sys; print("\n".join(m["texture"] for m in json.load(open(sys.argv[1]))["meshes"]))' \
        "$ROOT/build/bake/baked.json" | while read -r tex; do cp "$ROOT/build/bake/$tex" "$BAKED/"; done
    echo "updated the prepared model in assets/baked (check build/bake/review_*.png, then commit it)"
fi

python3 -I "$ROOT/pipeline/make_voice.py"

args=(--root "$ROOT" --finish "$ROOT/pipeline/finish.json" --bake "$BAKED"
      --data "$DATA" --out "$ROOT/build/mod" --voice "$ROOT/assets/voice")
[ "$install" = 1 ] && args+=(--install "$MODS")
python3 -I "$ROOT/pipeline/build_mod.py" "${args[@]}"
