#!/usr/bin/env bash
# Rewrites patches/skater-voice.patch from the changes in the TonyWasAProSkater checkout (its
# edited files and its new, not ignored, files). Edit the engine there, run this, commit the patch.
#
#   patches/refresh.sh [OUT]     default: patches/skater-voice.patch
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
GAME="$ROOT/TonyWasAProSkater"
OUT="${1:-$ROOT/patches/skater-voice.patch}"

{
    git -C "$GAME" diff --no-color --no-ext-diff HEAD     # staged or not (apply --3way stages)
    git -C "$GAME" ls-files --others --exclude-standard -z |
        while IFS= read -r -d '' f; do
            # exits 1 when the files differ, which they always do here
            git -C "$GAME" diff --no-color --no-ext-diff --no-index -- /dev/null "$f" || [ $? -eq 1 ]
        done
} >"$OUT.tmp"
mv "$OUT.tmp" "$OUT"
echo "[patches] wrote $OUT ($(grep -c '^diff --git' "$OUT") files)"
