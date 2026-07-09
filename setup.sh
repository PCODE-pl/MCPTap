#!/usr/bin/env sh
set -eu

PRODUCT_NAME="MCPTap"
SERVICE_NAME="mcptap"
GITHUB_REPO="PCODE-pl/MCPTap"
DEFAULT_RELEASE_URL="https://github.com/${GITHUB_REPO}/releases/latest/download/mcptap-release.tar.gz"

INSTALL_DIR="${MCPTAP_INSTALL_DIR:-$HOME/.local/share/mcptap}"
BIN_DIR="${MCPTAP_BIN_DIR:-$HOME/.local/bin}"
CONFIG_DIR="${MCPTAP_CONFIG_DIR:-$HOME/.config/mcptap}"
VENV_DIR="${MCPTAP_VENV_DIR:-$INSTALL_DIR/.venv}"
RELEASE_URL="${MCPTAP_RELEASE_URL:-$DEFAULT_RELEASE_URL}"
PYTHON_BIN="${PYTHON:-}"
TMP_DIR=""
SOURCE_DIR=""

usage() {
    cat <<EOF
${PRODUCT_NAME} installer

Usage:
  sh setup.sh [options]

Options:
  --release-url URL       Download this GitHub Release asset instead of latest.
  --install-dir PATH      Install application files here. Default: $INSTALL_DIR
  --config-dir PATH       Install example configuration here. Default: $CONFIG_DIR
  --venv-dir PATH         Create Python virtualenv here. Default: $VENV_DIR
  --python PATH           Python 3 interpreter to use.
  --no-service            Install files only, do not install user service.
  --force-config          Overwrite existing config files.
  --help                  Show this help.

Environment variables matching the options are also supported:
  MCPTAP_RELEASE_URL, MCPTAP_INSTALL_DIR, MCPTAP_CONFIG_DIR, MCPTAP_VENV_DIR, PYTHON

Notes:
  A Python virtualenv cannot be created without an existing Python interpreter.
  This installer requires Python 3.10+ already available on the system. If Python
  is missing, install it first with your OS package manager or Homebrew.
EOF
}

NO_SERVICE=0
FORCE_CONFIG=0

while [ "$#" -gt 0 ]; do
    case "$1" in
        --release-url)
            RELEASE_URL="$2"
            shift 2
            ;;
        --install-dir)
            INSTALL_DIR="$2"
            VENV_DIR="${MCPTAP_VENV_DIR:-$INSTALL_DIR/.venv}"
            shift 2
            ;;
        --config-dir)
            CONFIG_DIR="$2"
            shift 2
            ;;
        --venv-dir)
            VENV_DIR="$2"
            shift 2
            ;;
        --python)
            PYTHON_BIN="$2"
            shift 2
            ;;
        --no-service)
            NO_SERVICE=1
            shift
            ;;
        --force-config)
            FORCE_CONFIG=1
            shift
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

log() {
    printf '%s\n' "[$PRODUCT_NAME] $*"
}

die() {
    printf '%s\n' "[$PRODUCT_NAME] ERROR: $*" >&2
    exit 1
}

cleanup() {
    if [ -n "$TMP_DIR" ] && [ -d "$TMP_DIR" ]; then
        rm -rf "$TMP_DIR"
    fi
}
trap cleanup EXIT INT TERM

command_exists() {
    command -v "$1" >/dev/null 2>&1
}

detect_os() {
    uname_s=$(uname -s 2>/dev/null || echo unknown)
    case "$uname_s" in
        Linux) echo linux ;;
        Darwin) echo macos ;;
        *) echo unknown ;;
    esac
}

download_file() {
    url="$1"
    output="$2"
    if command_exists curl; then
        curl -fsSL "$url" -o "$output"
    elif command_exists wget; then
        wget -q "$url" -O "$output"
    else
        die "curl or wget is required to download release assets"
    fi
}

find_python() {
    if [ -n "$PYTHON_BIN" ]; then
        [ -x "$PYTHON_BIN" ] || die "Python executable not found or not executable: $PYTHON_BIN"
        printf '%s\n' "$PYTHON_BIN"
        return
    fi

    for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
        if command_exists "$candidate"; then
            printf '%s\n' "$candidate"
            return
        fi
    done

    cat >&2 <<EOF
[$PRODUCT_NAME] Python 3.10+ was not found.

A virtualenv is not a standalone Python distribution. It is a directory built
from an existing Python interpreter, so it cannot be created when Python is not
installed at all.

Install Python first, for example:
  macOS/Homebrew:  brew install python
  Debian/Ubuntu:   sudo apt install python3 python3-venv python3-pip
  Fedora:          sudo dnf install python3 python3-pip
  Arch:            sudo pacman -S python

Then run this installer again.
EOF
    exit 1
}

verify_python_version() {
    py="$1"
    "$py" - <<'PY' || exit 1
import sys
if sys.version_info < (3, 10):
    raise SystemExit("Python 3.10+ is required; found %s" % sys.version.split()[0])
PY
}

create_venv() {
    py="$1"
    mkdir -p "$(dirname "$VENV_DIR")"

    if [ ! -x "$VENV_DIR/bin/python" ]; then
        log "Creating Python virtualenv: $VENV_DIR"
        if ! "$py" -m venv "$VENV_DIR"; then
            cat >&2 <<EOF
[$PRODUCT_NAME] Failed to create virtualenv.

On Debian/Ubuntu this usually means python3-venv is missing:
  sudo apt install python3-venv
EOF
            exit 1
        fi
    else
        log "Using existing Python virtualenv: $VENV_DIR"
    fi

    "$VENV_DIR/bin/python" -m pip install --upgrade pip setuptools wheel
    "$VENV_DIR/bin/python" -m pip install --upgrade aiohttp python-dotenv mcp yaml
}

use_local_source_if_available() {
    script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
    if [ -f "$script_dir/proxy.py" ] && [ -d "$script_dir/examples" ]; then
        SOURCE_DIR="$script_dir"
        return 0
    fi
    return 1
}

download_release_source() {
    TMP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/mcptap.XXXXXX")
    archive="$TMP_DIR/mcptap-release.tar.gz"
    src="$TMP_DIR/src"
    mkdir -p "$src"

    log "Downloading release asset: $RELEASE_URL"
    download_file "$RELEASE_URL" "$archive"
    tar -xzf "$archive" -C "$src"

    if [ -f "$src/proxy.py" ] && [ -d "$src/examples" ]; then
        SOURCE_DIR="$src"
        return
    fi

    nested=$(find "$src" -maxdepth 2 -type f -name proxy.py -print | head -n 1 | xargs dirname 2>/dev/null || true)
    if [ -n "$nested" ] && [ -d "$nested/examples" ]; then
        SOURCE_DIR="$nested"
        return
    fi

    die "Release asset does not contain proxy.py and examples/"
}

install_files() {
    source_dir="$1"
    mkdir -p "$INSTALL_DIR" "$BIN_DIR" "$CONFIG_DIR"

    log "Installing application files into: $INSTALL_DIR"
    cp "$source_dir/proxy.py" "$INSTALL_DIR/proxy.py"
    chmod 0644 "$INSTALL_DIR/proxy.py"

    cat >"$BIN_DIR/mcptap" <<EOF
#!/usr/bin/env sh
exec "$VENV_DIR/bin/python" "$INSTALL_DIR/proxy.py" "\$@"
EOF
    chmod 0755 "$BIN_DIR/mcptap"

    for file in proxy.env openrouter.env requesty.env mcp-intercept.yaml; do
        src_file="$source_dir/examples/$file"
        dst_file="$CONFIG_DIR/$file"
        [ -f "$src_file" ] || continue
        if [ -f "$dst_file" ] && [ "$FORCE_CONFIG" -ne 1 ]; then
            log "Keeping existing config: $dst_file"
        else
            log "Installing example config: $dst_file"
            cp "$src_file" "$dst_file"
            chmod 0600 "$dst_file"
        fi
    done
}

install_systemd_user_service() {
    user_systemd_dir="$HOME/.config/systemd/user"
    service_file="$user_systemd_dir/${SERVICE_NAME}.service"
    mkdir -p "$user_systemd_dir"

    cat >"$service_file" <<EOF
[Unit]
Description=MCPTap LLM proxy
Documentation=https://github.com/${GITHUB_REPO}
After=network-online.target

[Service]
Type=simple
EnvironmentFile=$CONFIG_DIR/proxy.env
ExecStart=$VENV_DIR/bin/python $INSTALL_DIR/proxy.py
Restart=on-failure
RestartSec=5
WorkingDirectory=$INSTALL_DIR

[Install]
WantedBy=default.target
EOF

    systemctl --user daemon-reload
    systemctl --user enable --now "${SERVICE_NAME}.service"
    log "Installed and started systemd user service: ${SERVICE_NAME}.service"
    log "Logs: journalctl --user -u ${SERVICE_NAME}.service -f"
}

install_launchd_user_service() {
    launch_agents_dir="$HOME/Library/LaunchAgents"
    plist_file="$launch_agents_dir/pl.pcode.mcptap.plist"
    wrapper="$INSTALL_DIR/run-launchd.sh"
    mkdir -p "$launch_agents_dir" "$INSTALL_DIR"

    cat >"$wrapper" <<EOF
#!/usr/bin/env sh
set -a
[ -f "$CONFIG_DIR/proxy.env" ] && . "$CONFIG_DIR/proxy.env"
set +a
exec "$VENV_DIR/bin/python" "$INSTALL_DIR/proxy.py"
EOF
    chmod 0755 "$wrapper"

    cat >"$plist_file" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>pl.pcode.mcptap</string>
    <key>ProgramArguments</key>
    <array>
        <string>$wrapper</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>WorkingDirectory</key>
    <string>$INSTALL_DIR</string>
    <key>StandardOutPath</key>
    <string>$HOME/Library/Logs/mcptap.log</string>
    <key>StandardErrorPath</key>
    <string>$HOME/Library/Logs/mcptap.error.log</string>
</dict>
</plist>
EOF

    launchctl bootout "gui/$(id -u)" "$plist_file" >/dev/null 2>&1 || true
    launchctl bootstrap "gui/$(id -u)" "$plist_file"
    launchctl enable "gui/$(id -u)/pl.pcode.mcptap" >/dev/null 2>&1 || true
    launchctl kickstart -k "gui/$(id -u)/pl.pcode.mcptap" >/dev/null 2>&1 || true
    log "Installed and started launchd user service: pl.pcode.mcptap"
    log "Logs: $HOME/Library/Logs/mcptap.log and mcptap.error.log"
}

install_user_service() {
    os_name=$(detect_os)
    case "$os_name" in
        linux)
            if command_exists systemctl && systemctl --user show-environment >/dev/null 2>&1; then
                install_systemd_user_service
            else
                cat >&2 <<EOF
[$PRODUCT_NAME] systemd --user is not available in this session.
Files were installed, but the service was not enabled.

Start manually with:
  $BIN_DIR/mcptap

Or install a user service manually after enabling systemd user sessions.
EOF
            fi
            ;;
        macos)
            install_launchd_user_service
            ;;
        *)
            cat >&2 <<EOF
[$PRODUCT_NAME] Unsupported OS for automatic service installation: $(uname -s 2>/dev/null || echo unknown)
Files were installed, but the service was not enabled.

Start manually with:
  $BIN_DIR/mcptap
EOF
            ;;
    esac
}

main() {
    log "Installing $PRODUCT_NAME"
    py=$(find_python)
    verify_python_version "$py"

    if use_local_source_if_available; then
        log "Using local checkout: $SOURCE_DIR"
    else
        download_release_source
    fi

    create_venv "$py"
    install_files "$SOURCE_DIR"

    if [ "$NO_SERVICE" -eq 0 ]; then
        install_user_service
    else
        log "Skipping service installation because --no-service was used"
    fi

    cat <<EOF

[$PRODUCT_NAME] Installation complete.

Application: $INSTALL_DIR/proxy.py
Command:     $BIN_DIR/mcptap
Config:      $CONFIG_DIR
Virtualenv:  $VENV_DIR

Next steps:
  1. Edit $CONFIG_DIR/proxy.env
  2. Edit $CONFIG_DIR/openrouter.env or $CONFIG_DIR/requesty.env
  3. If using MCP interception, edit $CONFIG_DIR/mcp-intercept.yaml and set:
       MCP_TAP_INTERCEPT_YAML=@$CONFIG_DIR/mcp-intercept.yaml

Health check after service start:
  curl http://127.0.0.1:8787/health
EOF
}

main "$@"