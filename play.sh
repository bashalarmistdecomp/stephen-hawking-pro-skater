#!/usr/bin/env bash
# Stephen Hawking Pro Skater: the one command to set up, build and play.
#
#   ./play.sh                    set up what's missing, build what's out of date, then play
#   ./play.sh --check            only look: say what's ready and what's missing (changes nothing)
#   ./play.sh --disc FILE        use FILE as your disc image (a .bin, its .cue, or a .zip of them)
#   ./play.sh --record           play, recording the game's window and sound to ref/recordings/
#   ./play.sh --install-desktop  add "Stephen Hawking Pro Skater" to the desktop's app menu
#   ./play.sh --remove-desktop   take it out again
#
# In order, stopping to say what to do when something is missing:
#   1. The port (the TonyWasAProSkater submodule): fetched if it's empty, and patches/*.patch
#      applied to it once.
#   2. The game: built when there's no binary or it's older than the patches. That needs GCC
#      with 32-bit support, pkg-config, SDL2, and Ghidra's GNU demangler (GNU_DEMANGLER,
#      GHIDRA_INSTALL_DIR, or TonyWasAProSkater/extern/ghidra_demangler/; it offers to fetch it).
#   3. The game's data: copied off your disc, once. That's Tony Hawk's Pro Skater for the
#      PlayStation, USA: a raw .bin image, its .cue, or a .zip of them. Put it in ref/, or pass
#      --disc.
#   4. The mod: built from assets/ and installed when it's missing or older than its sources
#      (pipeline/build.sh; no Blender needed).
#   5. The game, with the mod.
# Extra arguments go to the game, and THPS_* variables pass through
# (TonyWasAProSkater/platform/pc/README.md). Launched from the app menu (no terminal), it logs
# to build/play.log and shows problems as a notification.
#
# Recording: the game opens as an X11 (XWayland) window, which ffmpeg can grab. Its sound plays
# as usual and is also linked (PipeWire) into a private virtual sink, so only the game is
# recorded, not other sounds. One ffmpeg process records the window and that sink, so picture
# and sound stay in sync, encoding on the GPU (NVENC). It runs from the window appearing until
# the game quits. Choose window or fullscreen (Alt+Enter) before playing: the capture is the
# size the window had when it began. RECORD_FPS (60) and RECORD_QUALITY (NVENC -cq, 20; lower
# is better) tune it.
set -euo pipefail

ROOT="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
GAME="$ROOT/TonyWasAProSkater"
EXE="$GAME/build/pc64-linux/thps"
DATA="$GAME/build/pc64-linux/Data"
DISC="$GAME/extern/thps1_disc/thps1.bin"
DEMANGLER="$GAME/extern/ghidra_demangler/demangler_gnu_v2_24"
GHIDRA_ZIP="https://github.com/NationalSecurityAgency/ghidra/releases/download/Ghidra_12.0.3_build/ghidra_12.0.3_PUBLIC_20260210.zip"
DEFAULT_MODS="$HOME/.local/share/Neversoft/Tony Hawk's Pro Skater/Mods"
MOD="${THPS_MODS_DIR:-$DEFAULT_MODS}/HawkingMod/Data/Models/hawking.psx"
DESKTOP="$HOME/.local/share/applications/stephen-hawking-pro-skater.desktop"
ICON="$HOME/.local/share/icons/stephen-hawking-pro-skater.png"
LOG="$ROOT/build/play.log"
REPO="https://github.com/bashalarmistdecomp/stephen-hawking-pro-skater.git"

record=0
check=0
disc_arg="${THPS_DISC:-}"
while [ $# -gt 0 ]; do
    case "$1" in
        --record) record=1; shift ;;
        --check) check=1; shift ;;
        --disc) [ $# -ge 2 ] || { echo "--disc needs a file" >&2; exit 2; }; disc_arg="$2"; shift 2 ;;
        --disc=*) disc_arg="${1#--disc=}"; shift ;;
        -h|--help) sed -n '2,/^set -euo/p' "$0" | sed '$d; s/^# \{0,1\}//'; exit 0 ;;
        --install-desktop)
            mkdir -p "$(dirname "$ICON")" "$(dirname "$DESKTOP")"
            cp "$ROOT/assets/baked/icon.png" "$ICON"
            cat >"$DESKTOP" <<EOF
[Desktop Entry]
Type=Application
Name=Stephen Hawking Pro Skater
Comment=THPS1 with the wheelchair skater mod
Exec="$ROOT/play.sh"
Path=$ROOT
Icon=$ICON
Terminal=false
Categories=Game;
Actions=Record;

[Desktop Action Record]
Name=Play and record
Exec="$ROOT/play.sh" --record
EOF
            command -v update-desktop-database >/dev/null && update-desktop-database "$(dirname "$DESKTOP")" || true
            echo "[play] installed $DESKTOP"
            exit 0 ;;
        --remove-desktop)
            rm -f "$DESKTOP" "$ICON"
            echo "[play] removed $DESKTOP"
            exit 0 ;;
        --) shift; break ;;
        *) break ;;     # the rest is the game's
    esac
done

# launched from the app menu: no terminal, so keep a log, and show problems as a notification
tty=1
if [ ! -t 1 ] && [ "$check" = 0 ]; then
    tty=0
    mkdir -p "$ROOT/build"
    exec >"$LOG" 2>&1
fi
interactive() { [ "$tty" = 1 ] && [ -t 0 ] && [ "$check" = 0 ]; }

if [ -t 1 ]; then
    YES=$'\e[32m✓\e[0m' NO=$'\e[31m✗\e[0m' TODO=$'\e[33m•\e[0m' BOLD=$'\e[1m' PLAIN=$'\e[0m'
else
    YES='✓' NO='✗' TODO='•' BOLD='' PLAIN=''
fi
say() { echo "[play] $*"; }
ok() { echo "[play] $YES $*"; }
todo() { echo "[play] $TODO $*"; }
indent() { sed 's/^/         /'; }

# Something that stops the way to the game: say what, and what to do about it (the rest of the
# arguments, a line each). With --check, count it and go on; otherwise stop here.
problems=0
stop() {
    local what="$1"
    shift
    echo "[play] $NO $what"
    [ $# -eq 0 ] || printf '%s\n' "$@" | indent
    if [ "$check" = 1 ]; then
        problems=$((problems + 1))
        return 0
    fi
    if [ "$tty" = 0 ] && command -v notify-send >/dev/null; then
        notify-send -i "$ICON" "Stephen Hawking Pro Skater" "$what (log: $LOG)"
    fi
    exit 1
}
fail() { stop "$@"; exit 1; }

ask() {     # a yes/no question, yes by default
    local reply
    printf '[play]   %s [Y/n] ' "$1"
    IFS= read -r reply || return 1
    case "$reply" in [nN]*) return 1 ;; esac
}

# ---- what this system needs -------------------------------------------------

distro() {
    local ids
    ids=" $( (. /etc/os-release 2>/dev/null && echo "${ID:-} ${ID_LIKE:-}") ) "
    case "$ids" in
        *" arch "*) echo arch ;;
        *" debian "*|*" ubuntu "*) echo debian ;;
        *" fedora "*|*" rhel "*) echo fedora ;;
        *) echo other ;;
    esac
}

# the packages for a set of needs (build, record), as a command for this distribution
install_hint() {
    case "$(distro)-$1" in
        arch-build)
            echo "Install them:  sudo pacman -S --needed git python gcc lib32-gcc-libs pkgconf sdl2-compat"
            echo "(lib32-gcc-libs is in the [multilib] repository: enable it in /etc/pacman.conf.)" ;;
        debian-build)
            echo "Install them:  sudo apt install git python3 gcc-multilib g++-multilib pkg-config libsdl2-dev" ;;
        fedora-build)
            echo "Install them:  sudo dnf install git python3 gcc gcc-c++ glibc-devel.i686 libstdc++-devel.i686 pkgconf-pkg-config SDL2-devel" ;;
        *-build)
            echo "Install git, Python 3, GCC and G++ with 32-bit (multilib) support, pkg-config and the"
            echo "SDL2 development files from your distribution's packages." ;;
        arch-record)
            echo "Install them:  sudo pacman -S --needed ffmpeg libpulse pipewire-pulse xorg-xwininfo" ;;
        debian-record)
            echo "Install them:  sudo apt install ffmpeg pulseaudio-utils pipewire-bin x11-utils" ;;
        fedora-record)
            echo "Install them:  sudo dnf install ffmpeg pulseaudio-utils pipewire-utils xwininfo" ;;
        *-record)
            echo "Install ffmpeg, pactl (PulseAudio utilities), PipeWire's tools and xwininfo." ;;
    esac
}

# what the game build needs that isn't here, comma-separated (nothing when all is well)
missing_build_tools() {
    local miss=() t
    t="$(mktemp -d)"
    command -v git >/dev/null || miss+=("git")
    command -v python3 >/dev/null || miss+=("Python 3")
    if ! command -v g++ >/dev/null; then
        miss+=("GCC (g++)")
    elif ! echo 'int main() { return 0; }' | g++ -m32 -x c++ - -o "$t/a" 2>/dev/null; then
        miss+=("GCC's 32-bit (multilib) support")
    fi
    if ! command -v pkg-config >/dev/null; then
        miss+=("pkg-config")
    elif ! pkg-config --exists sdl2; then
        miss+=("SDL2 (its development files)")
    fi
    rm -rf -- "${t:?}"
    local IFS=,
    echo "${miss[*]}" | sed 's/,/, /g'
}

missing_record_tools() {
    local miss=() t
    for t in ffmpeg pactl pw-dump pw-link xwininfo; do
        command -v "$t" >/dev/null || miss+=("$t")
    done
    if command -v ffmpeg >/dev/null && ! ffmpeg -hide_banner -encoders 2>/dev/null | grep h264_nvenc >/dev/null; then
        miss+=("ffmpeg's NVENC encoder (an NVIDIA GPU)")
    fi
    [ -n "${DISPLAY:-}" ] || miss+=("an X11 display (XWayland)")
    local IFS=,
    echo "${miss[*]}" | sed 's/,/, /g'
}

# ---- 1. the port ---------------------------------------------------------------

patched=0
step_port() {
    if [ ! -f "$GAME/platform/pc/tools/build.py" ]; then
        if ! git -C "$ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
            stop "the port (TonyWasAProSkater/) is missing, and this folder isn't a git clone" \
                 "A .zip of the repository doesn't include it. Clone the repository instead:" \
                 "    git clone --recursive $REPO"
            return 1
        fi
        if [ "$check" = 1 ]; then
            todo "port: not fetched yet (./play.sh runs git submodule update --init)"
            return 0
        fi
        say "fetching the port (git submodule update --init)"
        git -C "$ROOT" submodule update --init || { stop "couldn't fetch the port (see above)"; return 1; }
    fi
    local p state=patched
    for p in "$ROOT"/patches/*.patch; do
        [ -f "$p" ] || continue
        git -C "$GAME" apply --reverse --check "$p" 2>/dev/null && continue
        if ! git -C "$GAME" apply --check "$p" 2>/dev/null; then
            stop "patches/$(basename "$p") doesn't apply to TonyWasAProSkater/" \
                 "The port has to be at the commit this repository pins, without other changes" \
                 "to the same files. 'git submodule status' shows where it is. To set your" \
                 "changes there aside: git -C TonyWasAProSkater stash -u"
            return 1
        fi
        if [ "$check" = 1 ]; then
            todo "port: patches/$(basename "$p") isn't applied yet (./play.sh applies it)"
            state="not patched yet"
            continue
        fi
        git -C "$GAME" apply "$p"
        patched=1
        say "patched the port: $(basename "$p")"
    done
    ok "port: TonyWasAProSkater $(git -C "$GAME" rev-parse --short HEAD 2>/dev/null), $state"
}

# ---- 2. the game -------------------------------------------------------------

game_why=""     # why the game needs building ("" when it doesn't)
game_stale() {
    local p
    game_why=""
    [ -x "$EXE" ] || { game_why="it isn't built yet"; return 0; }
    [ "$patched" = 0 ] || { game_why="the port was just patched"; return 0; }
    for p in "$ROOT"/patches/*.patch; do
        [ "$p" -nt "$EXE" ] && { game_why="patches/$(basename "$p") changed"; return 0; }
    done
    return 1
}

find_demangler() {
    if [ -n "${GNU_DEMANGLER:-}" ]; then
        [ -x "$GNU_DEMANGLER" ]
        return
    fi
    if [ -n "${GHIDRA_INSTALL_DIR:-}" ]; then
        [ -x "$GHIDRA_INSTALL_DIR/GPL/DemanglerGnu/os/linux_x86_64/demangler_gnu_v2_24" ]
        return
    fi
    [ -x "$DEMANGLER" ] && export GNU_DEMANGLER="$DEMANGLER"
}

DEMANGLER_HELP=(
    "The game build runs Ghidra's GNU demangler, one small program out of Ghidra (GPL)."
    "Set GHIDRA_INSTALL_DIR to a Ghidra install, or GNU_DEMANGLER to the program, or put"
    "demangler_gnu_v2_24 (Ghidra's GPL/DemanglerGnu/os/linux_x86_64/) in"
    "TonyWasAProSkater/extern/ghidra_demangler/."
)

# Reads just the demangler out of Ghidra's release zip on GitHub (HTTP ranges: the zip's index
# and that one file, a few MB of its 500).
fetch_demangler() {
    mkdir -p "$(dirname "$DEMANGLER")"
    python3 -I - "$GHIDRA_ZIP" "$DEMANGLER" <<'PY'
import io, os, sys, urllib.request, zipfile
url, out = sys.argv[1:3]
MEMBER = 'GPL/DemanglerGnu/os/linux_x86_64/demangler_gnu_v2_24'


class Remote(io.RawIOBase):
    def __init__(self, url):
        with urllib.request.urlopen(urllib.request.Request(url, headers={'Range': 'bytes=0-0'})) as r:
            self.url, span = r.geturl(), r.headers.get('Content-Range', '')
            if r.status != 206 or '/' not in span:
                raise SystemExit('[play] the download server does not serve parts of files')
        self.size, self.pos, self.got = int(span.rsplit('/', 1)[1]), 0, 0

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self, off, whence=0):
        self.pos = (off, self.pos + off, self.size + off)[whence]
        return self.pos

    def readinto(self, b):
        n = min(len(b), self.size - self.pos)
        if n <= 0:
            return 0
        rq = urllib.request.Request(self.url, headers={'Range': 'bytes=%d-%d' % (self.pos, self.pos + n - 1)})
        with urllib.request.urlopen(rq) as r:
            data = r.read()
        b[:len(data)] = data
        self.pos += len(data)
        self.got += len(data)
        return len(data)


remote = Remote(url)
z = zipfile.ZipFile(io.BufferedReader(remote, 1 << 20))
data = z.read(next(n for n in z.namelist() if n.endswith(MEMBER)))
with open(out + '.part', 'wb') as f:
    f.write(data)
os.chmod(out + '.part', 0o755)
os.replace(out + '.part', out)
print('[play]   got it (%d KB, %.1f MB read of the %d MB zip)' % (len(data) >> 10, remote.got / 1e6, remote.size >> 20))
PY
}

# whether the game can be built here; offers to fetch the demangler
game_ready() {
    local miss
    miss="$(missing_build_tools)"
    if [ -n "$miss" ]; then
        stop "the game needs building ($game_why), and that needs: $miss" \
             "$(install_hint build)" "Then run ./play.sh again."
        return 1
    fi
    find_demangler && return 0
    if interactive; then
        echo "[play] $TODO the game build needs Ghidra's GNU demangler, and it isn't here"
        printf '%s\n' "${DEMANGLER_HELP[@]}" | indent
        if ask "Fetch just that file from Ghidra 12.0.3's release on GitHub now?" && fetch_demangler; then
            export GNU_DEMANGLER="$DEMANGLER"
            return 0
        fi
    fi
    stop "the game needs building ($game_why), and that needs Ghidra's GNU demangler" \
         "${DEMANGLER_HELP[@]}" "Run in a terminal, ./play.sh offers to fetch it."
    return 1
}

build_game() {
    say "building the game ($game_why; this takes a few minutes)"
    (cd "$GAME" && THPS_ARCH=x64 python3 platform/pc/tools/build.py) \
        || { stop "the game build failed (its output is above)"; return 1; }
    ok "game: built"
}

# ---- 3. the game's data, off your disc -------------------------------------------

have_data() { [ -f "$DATA/Manifest.txt" ] && [ -f "$DATA/Models/hawk.psx" ]; }

# Checks disc images: `probe FILE...` or `find DIR`. A line per file: "bin<TAB>file<TAB>its .bin",
# "zip<TAB>file<TAB>the .bin in it", or "bad<TAB>file<TAB>why".
DISC_PY='
import os, posixpath, re, sys, zipfile
RAW, HEAD = 2352, 4 << 20
SYNC = b"\x00" + b"\xff" * 10 + b"\x00"
EXTS = (".bin", ".img", ".cue", ".zip", ".iso", ".chd", ".gdi")


def verdict(head, size):
    if size % RAW or not head.startswith(SYNC):
        return "it is not a raw disc image (a MODE2/2352 .bin)"
    if b"SLUS_008.60" in head:
        return None
    m = re.search(rb"S[CL][UEPK][SMP]_\d{3}\.\d{2}", head)
    if m:
        return "it is %s, not Tony Hawk\x27s Pro Skater (USA), SLUS_008.60" % m.group().decode()
    return "it is not Tony Hawk\x27s Pro Skater (USA): no SLUS_008.60 on it"


def cue_files(text):
    return re.findall(r"^\s*FILE\s+\"?(.*?)\"?\s+BINARY\s*$", text, re.M | re.I)


def probe(p):
    ext = os.path.splitext(p)[1].lower()
    if not os.path.isfile(p):
        return "bad", "there is no such file"
    if ext == ".cue":
        files = cue_files(open(p, encoding="latin-1").read())
        if not files:
            return "bad", "its .cue names no .bin"
        p, ext = os.path.join(os.path.dirname(p), files[0]), ".bin"
        if not os.path.isfile(p):
            return "bad", "the .bin it names (%s) is missing" % files[0]
    if ext in (".bin", ".img"):
        with open(p, "rb") as f:
            why = verdict(f.read(HEAD), os.path.getsize(p))
        return ("bad", why) if why else ("bin", os.path.abspath(p))
    if ext == ".zip":
        try:
            z = zipfile.ZipFile(p)
        except zipfile.BadZipFile:
            return "bad", "it is not a readable .zip"
        lower = {n.lower(): n for n in z.namelist()}
        member = None
        for n in z.namelist():
            if n.lower().endswith(".cue"):
                files = cue_files(z.read(n).decode("latin-1"))
                if files:
                    member = lower.get(posixpath.join(posixpath.dirname(n), files[0]).lower())
                break
        if member is None:
            bins = [i for i in z.infolist() if i.filename.lower().endswith((".bin", ".img"))]
            if not bins:
                return "bad", "there is no .bin in it"
            member = max(bins, key=lambda i: i.file_size).filename
        with z.open(member) as f:
            why = verdict(f.read(HEAD), z.getinfo(member).file_size)
        return ("bad", why) if why else ("zip", member)
    return "bad", {
        ".iso": "an .iso has 2048-byte sectors, and the game needs the raw ones: rip the disc as .bin/.cue",
        ".chd": "unpack the .chd first: chdman extractcd -i GAME.chd -o GAME.cue",
        ".gdi": "that is the Dreamcast disc; this mod is made for the PlayStation (USA) one",
    }.get(ext, "it is not a disc image (a .bin, its .cue, or a .zip of them)")


def candidates(top):
    found = []
    for d, dirs, files in os.walk(top):
        dirs.sort()
        if d[len(top):].count(os.sep) >= 3:
            dirs[:] = []
        cued = set()
        for f in files:
            if f.lower().endswith(".cue"):
                cued.update(x.lower() for x in cue_files(open(os.path.join(d, f), encoding="latin-1").read()))
        found += [os.path.join(d, f) for f in sorted(files)
                  if f.lower().endswith(EXTS) and f.lower() not in cued]
    return found


paths = candidates(sys.argv[2]) if sys.argv[1] == "find" else sys.argv[2:]
for p in paths:
    kind, what = probe(p)
    print("%s\t%s\t%s" % (kind, p, what))
'
disc_probe() { python3 -I -c "$DISC_PY" "$@"; }

disc_kind="" disc_file="" disc_bin=""
disc_take() {   # "kind<TAB>file<TAB>what": remember a good one
    IFS=$'\t' read -r disc_kind disc_file disc_bin <<<"$1"
    [ -n "$disc_kind" ] && [ "$disc_kind" != bad ]
}

DISC_HELP=(
    "The game's data comes off your own copy of Tony Hawk's Pro Skater for the PlayStation"
    "(USA, SLUS-00860), as a raw disc image: a .bin (MODE2/2352) and its .cue, or a .zip of"
    "them. Put it in ref/ (any name) and run ./play.sh again, or: ./play.sh --disc FILE"
)

# a path typed or dropped into the terminal (quoted, escaped or a file:// URL), as a plain path
unquote() {
    python3 -I -c '
import os, shlex, sys, urllib.parse
s = sys.argv[1].strip()
if not os.path.exists(s):
    try:
        s = shlex.split(s)[0]
    except (ValueError, IndexError):
        pass
if s.startswith("file://"):
    s = urllib.parse.unquote(s[7:])
print(os.path.expanduser(s))' "$1"
}

# Finds your disc image: --disc, the one already in place, or one in ref/; asks when there's
# none. Sets disc_kind (bin | zip), disc_file and disc_bin.
find_disc() {
    local r line
    if [ -n "$disc_arg" ]; then
        r="$(disc_probe probe "$disc_arg")"
        disc_take "$r" && return 0
        stop "$disc_arg can't be used: $disc_bin" "${DISC_HELP[@]}"
        return 1
    fi
    if [ -e "$DISC" ]; then
        r="$(disc_probe probe "$DISC")"
        disc_take "$r" && return 0
        stop "TonyWasAProSkater/extern/thps1_disc/thps1.bin can't be used: $disc_bin" \
             "Remove it, then put your disc in ref/ or pass --disc FILE."
        return 1
    fi
    r="$(disc_probe find "$ROOT/ref" 2>/dev/null || true)"
    line="$(grep -v '^bad' <<<"$r" | head -1 || true)"
    if [ -n "$line" ]; then
        disc_take "$line"
        return 0
    fi
    local tried=()
    while IFS=$'\t' read -r _ f why; do
        [ -n "$f" ] && tried+=("${f#"$ROOT"/}: $why")
    done <<<"$r"
    if interactive; then
        echo "[play] $TODO the game's data has to come off your disc once, and there's no disc image here"
        printf '%s\n' "${DISC_HELP[@]}" "${tried[@]/#/not usable: }" | indent
        local reply
        while :; do
            printf '[play]   Drag the file here (or type its path) and press Enter, or just Enter to stop: '
            IFS= read -r reply || break
            [ -n "${reply//[[:space:]]/}" ] || break
            r="$(disc_probe probe "$(unquote "$reply")")"
            disc_take "$r" && return 0
            echo "[play]   that can't be used: $disc_bin"
        done
    fi
    stop "the game's data has to come off your disc once, and there's no disc image here" \
         "${DISC_HELP[@]}" "${tried[@]/#/Not usable: }"
    return 1
}

# your disc where the game looks for it: a link to the .bin, or unpacked from the .zip
place_disc() {
    mkdir -p "$(dirname "$DISC")"
    if [ "$disc_kind" = zip ]; then
        say "unpacking your disc from $(basename "$disc_file") (about 630 MB)"
        python3 -I -c '
import os, shutil, sys, zipfile
src, member, out = sys.argv[1:4]
with zipfile.ZipFile(src).open(member) as f, open(out + ".part", "wb") as o:
    shutil.copyfileobj(f, o, 1 << 20)
os.replace(out + ".part", out)' "$disc_file" "$disc_bin" "$DISC" || { stop "couldn't unpack it"; return 1; }
    elif [ "$(readlink -f "$DISC" 2>/dev/null)" != "$(readlink -f "$disc_bin")" ]; then
        if [ -e "$DISC" ] && [ ! -L "$DISC" ]; then
            mv "$DISC" "$DISC.old"
            say "moved the disc image that was there to $(basename "$DISC").old"
        fi
        ln -sfn "$(readlink -f "$disc_bin")" "$DISC"
    fi
    unset THPS_DISC
}

# the game copies its data off the disc on its first start: run it once, with no window, for that
extract_data() {
    say "copying the game's data off your disc (once; about 630 MB)"
    mkdir -p "$ROOT/build"
    (cd "$(dirname "$EXE")" && THPS_HEADLESS=1 THPS_AUDIO=0 THPS_MOVIES=0 THPS_NOGAMEPAD=1 \
        THPS_FRAMES=1 "$EXE" >"$ROOT/build/extract.log" 2>&1) || true
    have_data || { stop "the game couldn't copy its data off the disc" "Its log: build/extract.log"; return 1; }
    ok "game data: copied off your disc"
}

# ---- 4. the mod ----------------------------------------------------------------

mod_why=""
mod_stale() {
    mod_why=""
    if [ ! -f "$MOD" ]; then
        mod_why="it isn't installed yet"
    elif [ -n "$(find "$ROOT/pipeline" "$ROOT/assets/baked" "$ROOT/assets/voice" -type f \
                  \( -name '*.py' -o -name '*.json' -o -name '*.sh' -o -name '*.rgba' -o -name '*.wav' \) \
                  -newer "$MOD" -print -quit)" ]; then
        mod_why="its sources changed"
    fi
    [ -n "$mod_why" ]
}

build_mod() {
    say "building the mod ($mod_why)"
    "$ROOT/pipeline/build.sh" || { stop "the mod build failed (its output is above)"; return 1; }
    ok "mod: built and installed"
}

# ---- in order ----------------------------------------------------------------

say "${BOLD}Stephen Hawking Pro Skater${PLAIN}"
if [ "$(uname -s)" != Linux ] || [ "$(uname -m)" != x86_64 ]; then
    stop "this launcher is for Linux on x86-64, the only system it's tested on" \
         "The port itself also builds on Windows: TonyWasAProSkater/platform/pc/README.md."
fi
if ! command -v git >/dev/null || ! command -v python3 >/dev/null; then
    stop "this needs git and Python 3" "$(install_hint build)"
fi

if [ "$check" = 1 ]; then
    step_port || true
    if game_stale; then
        if game_ready; then todo "game: $game_why; ./play.sh builds it (a few minutes)"; fi
    else
        ok "game: built"
    fi
    if have_data; then
        ok "game data: copied off your disc"
    elif find_disc; then
        todo "game data: not copied yet; ./play.sh copies it off ${disc_file#"$ROOT"/}"
    fi
    if mod_stale; then todo "mod: $mod_why; ./play.sh builds it"; else ok "mod: installed and up to date"; fi
    # only for changing things, or recording
    if command -v espeak-ng >/dev/null && command -v ffmpeg >/dev/null; then
        ok "voice tools (espeak-ng, ffmpeg): there, for making new lines"
    else
        todo "voice tools: no espeak-ng or ffmpeg; only needed to make new lines (pipeline/make_voice.py)"
    fi
    if command -v "${BLENDER:-blender}" >/dev/null; then
        ok "Blender: there, for remaking the model (pipeline/build.sh --bake)"
    else
        todo "Blender: not found; only needed to remake the model (pipeline/build.sh --bake)"
    fi
    miss="$(missing_record_tools)"
    if [ -z "$miss" ]; then ok "recording (--record): ready"; else todo "recording (--record) would need: $miss"; fi
    if [ "$problems" = 0 ]; then
        say "ready: ./play.sh does the rest and starts the game"
        exit 0
    fi
    say "$problems thing(s) to sort out first (above)"
    exit 1
fi

step_port
if game_stale; then
    game_ready
fi
if ! have_data; then
    find_disc           # before the build, so a missing disc doesn't wait for it
fi
if [ -n "$game_why" ]; then
    build_game
else
    ok "game: built"
fi
if have_data; then
    ok "game data: copied off your disc"
else
    place_disc
    extract_data
fi
if mod_stale; then
    build_mod
else
    ok "mod: installed and up to date"
fi

if [ "$record" = 0 ]; then
    say "starting the game"
    exec "$EXE" "$@"
fi

# ---- recording ---------------------------------------------------------------

miss="$(missing_record_tools)"
[ -z "$miss" ] || stop "recording needs: $miss" "$(install_hint record)"

SINK=thps_record
OUT="$ROOT/ref/recordings/$(date +%Y-%m-%d_%H-%M-%S).mkv"
mkdir -p "$ROOT/ref/recordings"
module=""
game=""
ff=""
linker=""
cleanup() {
    [ -n "$linker" ] && kill "$linker" 2>/dev/null || true
    [ -n "$ff" ] && kill -INT "$ff" 2>/dev/null && wait "$ff" 2>/dev/null || true
    [ -n "$game" ] && kill "$game" 2>/dev/null || true
    [ -n "$module" ] && pactl unload-module "$module" 2>/dev/null || true
}
trap cleanup EXIT
trap 'exit 130' INT TERM

# a sink left over by a run that was killed outright
for m in $(pactl list short modules | grep "sink_name=$SINK" | cut -f1); do
    pactl unload-module "$m" || true
done
module="$(pactl load-module module-null-sink sink_name="$SINK" rate=48000 channels=2 \
    channel_map=front-left,front-right sink_properties=device.description=THPS-recording)"

# "<port id> <channel>" for each output port of the game's playback stream(s)
game_ports() {
    pw-dump | python3 -I -c '
import json, sys
objs = json.load(sys.stdin)
props = {o["id"]: (o.get("info") or {}).get("props") or {} for o in objs}
nodes = {i for i, p in props.items()
         if p.get("media.class") == "Stream/Output/Audio"
         and str(props.get(p.get("client.id"), {}).get("application.process.id")) == sys.argv[1]}
for i, p in props.items():
    if p.get("node.id") in nodes and p.get("port.direction") == "out":
        print(i, p.get("audio.channel"))
' "$1"
}

# keep the game's sound linked into the recording sink as well as the speakers (re-linking if
# the game reopens its audio)
link_audio() {
    while kill -0 "$1" 2>/dev/null; do
        game_ports "$1" | while read -r port ch; do
            case "$ch" in FL|FR) pw-link "$port" "$SINK:playback_$ch" 2>/dev/null || true ;; esac
        done
        sleep 2
    done
}

say "starting the game (recording)"
SDL_VIDEODRIVER=x11 SDL_VIDEO_DRIVER=x11 "$EXE" "$@" &
game=$!
link_audio "$game" &
linker=$!

# the game's window, once it's on screen
wid=""
for _ in $(seq 600); do
    kill -0 "$game" 2>/dev/null || fail "the game quit before its window appeared"
    wid="$(xwininfo -root -tree | awk '/"Tony Hawk.s Pro Skater"/ {print $1; exit}')"
    if [ -n "$wid" ] && xwininfo -id "$wid" | grep -q 'IsViewable'; then
        break
    fi
    wid=""
    sleep 0.1
done
[ -n "$wid" ] || fail "no game window after 60 s"
sleep 0.5
geom="$(xwininfo -id "$wid" | awk '/Width:/ {w=$2} /Height:/ {h=$2} END {print w "x" h}')"
say "recording window $wid ($geom) and the game's sound to $OUT"

ffmpeg -hide_banner -loglevel warning -nostdin \
    -thread_queue_size 1024 -f x11grab -framerate "${RECORD_FPS:-60}" -draw_mouse 0 \
        -window_id "$((wid))" -i "$DISPLAY" \
    -thread_queue_size 1024 -f pulse -sample_rate 48000 -channels 2 -i "$SINK.monitor" \
    -map 0:v -map 1:a \
    -c:v h264_nvenc -preset p5 -rc vbr -cq "${RECORD_QUALITY:-20}" -b:v 0 -pix_fmt yuv420p \
    -c:a aac -b:a 192k "$OUT" &
ff=$!

status=0
wait "$game" || status=$?
game=""
wait "$linker" 2>/dev/null || true
linker=""
kill -INT "$ff" 2>/dev/null || true
wait "$ff" 2>/dev/null || true
ff=""
say "saved $OUT"
exit "$status"
