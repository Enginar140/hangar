#!/usr/bin/env bash
# Hangar installer.
#
#   curl -fsSL https://raw.githubusercontent.com/Enginar140/hangar/main/install.sh | bash
#
# Options (pass after `bash -s --` when piping):
#   --yes            answer yes to every question (CI, unattended installs)
#   --gazebo         also install Gazebo Harmonic, the ArduPilot Gazebo plugin and example models
#   --no-ardupilot   skip ArduPilot (use `hangar config set ardupilot.dir ...` for your own build)
#   --build-ardupilot   build ArduPilot from source instead of using the official prebuilt SITL
#   --mavproxy       also install MAVProxy for the shared map (about 900 MB)
#   --from PATH      install Hangar from a local checkout instead of GitHub
#
# Every step says what it will do and asks first. uv provides Python 3.12, so
# the system Python is not touched. Third-party projects are
# downloaded from their own repositories after their licenses are shown.

set -euo pipefail

REPO="${HANGAR_REPO:-https://github.com/Enginar140/hangar}"
REF="${HANGAR_REF:-main}"
ASSUME_YES=0
WITH_GAZEBO=ask
WITH_ARDUPILOT=1
BUILD_ARDUPILOT=0
WITH_MAVPROXY=ask
FROM=""

while [ $# -gt 0 ]; do
    case "$1" in
        -y|--yes) ASSUME_YES=1 ;;
        --gazebo) WITH_GAZEBO=1 ;;
        --no-gazebo) WITH_GAZEBO=0 ;;
        --no-ardupilot) WITH_ARDUPILOT=0 ;;
        --build-ardupilot) BUILD_ARDUPILOT=1 ;;
        --mavproxy) WITH_MAVPROXY=1 ;;
        --from) FROM="$2"; shift ;;
        -h|--help) sed -n '2,17p' "$0" 2>/dev/null || true; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
    shift
done

if [ -t 1 ]; then
    B=$'\e[1m'; G=$'\e[32m'; Y=$'\e[33m'; R=$'\e[31m'; D=$'\e[2m'; N=$'\e[0m'
else
    B=""; G=""; Y=""; R=""; D=""; N=""
fi
step() { printf '\n%s==> %s%s\n' "$B" "$*" "$N"; }
info() { printf '    %s\n' "$*"; }
ok()   { printf '    %s✓%s %s\n' "$G" "$N" "$*"; }
warn() { printf '    %s!%s %s\n' "$Y" "$N" "$*"; }
die()  { printf '%serror:%s %s\n' "$R" "$N" "$*" >&2; exit 1; }

# When piped from curl, stdin is this script: questions must use the terminal.
TTY=""
if [ -r /dev/tty ] && { : </dev/tty; } 2>/dev/null; then TTY=/dev/tty; fi

ask() { # ask "question" -> 0 for yes
    [ "$ASSUME_YES" = 1 ] && return 0
    [ -n "$TTY" ] || die "no terminal for questions; rerun with --yes"
    local reply
    printf '    %s [y/N] ' "$1" >/dev/tty
    read -r reply </dev/tty || true
    case "$reply" in [yYeE]*) return 0 ;; *) return 1 ;; esac
}

hangar_cmd() { # run hangar with the terminal as stdin so its own prompts work
    local extra=()
    [ "$ASSUME_YES" = 1 ] && extra=(--yes)
    if [ -n "$TTY" ]; then "$HANGAR" "$@" "${extra[@]}" </dev/tty; else "$HANGAR" "$@" "${extra[@]}"; fi
}

need_sudo() {
    if [ "$(id -u)" -eq 0 ]; then SUDO=""; return; fi
    command -v sudo >/dev/null || die "sudo is required to install system packages"
    SUDO="sudo"
}

apt_install() {
    need_sudo
    info "${D}sudo apt-get install -y $*${N}"
    $SUDO apt-get update -qq
    DEBIAN_FRONTEND=noninteractive $SUDO apt-get install -y "$@"
}

# --- 1. system ------------------------------------------------------------------

step "Checking the system"
[ "$(uname -s)" = Linux ] || die "Hangar runs on Linux (Ubuntu 22.04/24.04 recommended)"
DISTRO="unknown"; CODENAME=""
if [ -r /etc/os-release ]; then
    . /etc/os-release
    DISTRO="${ID:-unknown} ${VERSION_ID:-}"
    CODENAME="${UBUNTU_CODENAME:-${VERSION_CODENAME:-}}"
fi
case "$CODENAME" in
    jammy|noble) ok "$PRETTY_NAME" ;;
    *) warn "$DISTRO is not tested; Ubuntu 22.04 or 24.04 is recommended"
       ask "Continue anyway?" || exit 1 ;;
esac

ensure_cmds() { # ensure_cmds cmd... ; offers to apt-install whatever is missing
    local missing=() cmd
    for cmd in "$@"; do command -v "$cmd" >/dev/null || missing+=("$cmd"); done
    [ ${#missing[@]} -eq 0 ] && return 0
    warn "missing: ${missing[*]}"
    ask "Install them with apt?" || die "please install: ${missing[*]}"
    apt_install "${missing[@]}"
}

ensure_cmds curl
ok "curl is available"

# --- 2. uv + hangar ----------------------------------------------------------------

step "Installing Hangar"
export PATH="$HOME/.local/bin:$PATH"
if ! command -v uv >/dev/null; then
    info "Hangar is installed with uv (a fast Python package manager, user-level, no sudo)."
    ask "Install uv to ~/.local/bin?" || die "uv is required: https://docs.astral.sh/uv/"
    curl -LsSf https://astral.sh/uv/install.sh | sh
fi
ok "uv $(uv --version | cut -d' ' -f2)"

if [ -n "$FROM" ]; then
    uv tool install --force --python 3.12 "$FROM"
else
    ensure_cmds git
    uv tool install --force --python 3.12 "git+${REPO}@${REF}"
fi
HANGAR="$(command -v hangar || echo "$HOME/.local/bin/hangar")"
[ -x "$HANGAR" ] || die "hangar was not installed"
ok "$("$HANGAR" --version) at $HANGAR"
uv tool update-shell >/dev/null 2>&1 || true

# --- 2b. clipboard ---------------------------------------------------------------

step "Clipboard tool"
if command -v wl-copy >/dev/null || command -v xclip >/dev/null || command -v xsel >/dev/null; then
    ok "available"
else
    info "Hangar's Copy buttons (camera code, logs) need a clipboard tool:"
    info "wl-clipboard for Wayland and xclip for X11 (both tiny, from Ubuntu's repositories)."
    if ask "Install wl-clipboard and xclip?"; then
        apt_install wl-clipboard xclip
        ok "installed"
    else
        warn "skipped; Copy falls back to saving files. Later: sudo apt install wl-clipboard xclip"
    fi
fi

# --- 3. ArduPilot -----------------------------------------------------------------

if [ "$WITH_ARDUPILOT" = 1 ]; then
    step "ArduPilot SITL"
    if "$HANGAR" doctor 2>/dev/null | grep -q "✓ SITL arduplane"; then
        ok "ArduPilot SITL binaries already configured"
    elif [ "$BUILD_ARDUPILOT" = 1 ]; then
        info "Building ArduPilot from source (10-30 minutes, installs its prerequisites with sudo)."
        ensure_cmds git
        hangar_cmd sources fetch ardupilot && hangar_cmd sources build ardupilot \
            || warn "skipped; later: hangar sources fetch ardupilot && hangar sources build ardupilot"
    else
        info "Hangar uses ArduPilot's official prebuilt SITL binaries (about 21 MB, seconds)."
        info "Already have your own build? Answer no and run: hangar config set ardupilot.dir PATH"
        # hangar shows the license and asks itself, so there is a single question
        hangar_cmd sources fetch ardupilot_sitl || warn "skipped; later: hangar sources fetch ardupilot_sitl"
    fi
fi

# --- 3b. MAVProxy map (optional) -----------------------------------------------------

step "MAVProxy map (optional)"
if "$HANGAR" doctor 2>/dev/null | grep -q "✓ MAVProxy"; then
    ok "already installed"
elif [ "$WITH_MAVPROXY" = 1 ]; then
    hangar_cmd sources fetch mavproxy || warn "skipped; later: hangar sources fetch mavproxy"
elif [ "$ASSUME_YES" = 1 ]; then
    info "skipped (about 900 MB); add --mavproxy, or later: hangar sources fetch mavproxy"
else
    info "The Map button shows every vehicle on one MAVProxy map with its console."
    info "It needs MAVProxy with wxPython and OpenCV: about 900 MB, no sudo. You can also"
    info "install it later from the Map button or with: hangar sources fetch mavproxy"
    hangar_cmd sources fetch mavproxy || warn "skipped; later: hangar sources fetch mavproxy"
fi

# --- 4. Gazebo (optional) -------------------------------------------------------------

if [ "$WITH_GAZEBO" = ask ]; then
    step "Gazebo (optional)"
    info "Built-in physics vehicles work without Gazebo. Gazebo adds 3D worlds, real models"
    info "and cameras. It installs Gazebo Harmonic from packages.osrfoundation.org."
    if ask "Set up Gazebo too?"; then WITH_GAZEBO=1; else WITH_GAZEBO=0; fi
fi

if [ "$WITH_GAZEBO" = 1 ]; then
    step "Gazebo Harmonic"
    if command -v gz >/dev/null; then
        ok "Gazebo already installed: $(gz sim --versions 2>/dev/null | head -1)"
    else
        case "$CODENAME" in jammy|noble) ;; *) die "Gazebo packages exist for Ubuntu 22.04/24.04 only" ;; esac
        need_sudo
        $SUDO install -d /usr/share/keyrings
        curl -fsSL https://packages.osrfoundation.org/gazebo.gpg \
            | $SUDO tee /usr/share/keyrings/pkgs-osrf-archive-keyring.gpg >/dev/null
        echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/pkgs-osrf-archive-keyring.gpg] http://packages.osrfoundation.org/gazebo/ubuntu-stable $CODENAME main" \
            | $SUDO tee /etc/apt/sources.list.d/gazebo-stable.list >/dev/null
        apt_install gz-harmonic
        ok "Gazebo installed"
    fi
    ensure_cmds git
    hangar_cmd sources fetch ardupilot_gazebo sitl_models hangar_worlds && hangar_cmd sources build ardupilot_gazebo \
        || warn "skipped; later: hangar sources fetch ardupilot_gazebo sitl_models hangar_worlds && hangar sources build ardupilot_gazebo"
fi

# --- 5. done -------------------------------------------------------------------------

step "Checking the installation"
"$HANGAR" doctor || true

cat <<EOF

${G}${B}Hangar is installed.${N}

  ${B}hangar${N}                      open the terminal UI
  ${B}hangar --help${N}               all commands
  ${B}hangar init && hangar quickstart.yaml${N}   try two vehicles with built-in physics
  ${B}hangar doctor${N}               check the setup again

If 'hangar' is not found, open a new terminal (uv added ~/.local/bin to your PATH).
EOF
