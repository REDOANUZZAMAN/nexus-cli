#!/usr/bin/env bash
# NEXUS CLI installer for macOS / Linux.
#   curl -fsSL https://raw.githubusercontent.com/REDOANUZZAMAN/nexus-cli/main/install.sh | bash
# Installs into ~/.nexus/cli (its own Python environment) and links ~/.local/bin/nexus.
# Options (env vars): NEXUS_VERSION, NEXUS_INSTALL_DIR, NEXUS_BIN_DIR, NEXUS_WHEEL (local file or URL).
set -euo pipefail

main() {
  local version="${NEXUS_VERSION:-0.2.0}"
  local repo="REDOANUZZAMAN/nexus-cli"
  local wheel="${NEXUS_WHEEL:-https://github.com/$repo/releases/download/v$version/nexus_cli-$version-py3-none-any.whl}"
  local install_dir="${NEXUS_INSTALL_DIR:-$HOME/.nexus/cli}"
  local bin_dir="${NEXUS_BIN_DIR:-$HOME/.local/bin}"

  local bold="" dim="" green="" red="" violet="" reset=""
  if [ -t 1 ]; then
    bold=$'\033[1m' dim=$'\033[2m' green=$'\033[32m' red=$'\033[31m' violet=$'\033[38;2;167;139;250m' reset=$'\033[0m'
  fi
  say()  { printf '%s\n' "$*"; }
  fail() { printf '%s✗ %s%s\n' "$red" "$*" "$reset" >&2; exit 1; }

  say "${violet}${bold}✻ Installing NEXUS CLI v$version${reset}"

  # 1. Python 3.9+
  local py=""
  for cand in python3 python; do
    if command -v "$cand" >/dev/null 2>&1 &&
       "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
      py="$cand"; break
    fi
  done
  if [ -z "$py" ]; then
    say "${red}NEXUS CLI needs Python 3.9 or newer.${reset} Install it first:"
    say "  macOS:          brew install python"
    say "  Ubuntu/Debian:  sudo apt install python3 python3-venv"
    say "  Fedora:         sudo dnf install python3"
    exit 1
  fi
  say "${dim}  using $("$py" --version 2>&1)${reset}"

  # 2. A private Python environment, so nothing clashes with system packages
  rm -rf "$install_dir"
  mkdir -p "$(dirname "$install_dir")"
  if ! "$py" -m venv "$install_dir" >/dev/null 2>&1; then
    rm -rf "$install_dir"
    fail "Couldn't create a Python environment. On Ubuntu/Debian run: sudo apt install python3-venv"
  fi
  say "${dim}  downloading…${reset}"
  "$install_dir/bin/python" -m pip install --quiet --disable-pip-version-check --upgrade "$wheel" ||
    fail "Download/install failed. Check your internet connection and try again."

  # 3. Put `nexus` on PATH
  mkdir -p "$bin_dir"
  ln -sf "$install_dir/bin/nexus" "$bin_dir/nexus"

  local path_note=""
  case ":$PATH:" in
    *":$bin_dir:"*) ;;
    *)
      local rc=""
      case "${SHELL:-}" in
        */zsh)  rc="$HOME/.zshrc" ;;
        */bash) rc="$HOME/.bashrc" ;;
        */fish) rc="" ;;
        *)      rc="$HOME/.profile" ;;
      esac
      if [ -n "$rc" ]; then
        if ! grep -qs "$bin_dir" "$rc"; then
          printf '\n# NEXUS CLI\nexport PATH="%s:$PATH"\n' "$bin_dir" >> "$rc"
        fi
        path_note="Open a new terminal (or run: source $rc) so the nexus command is found."
      else
        path_note="Add $bin_dir to your PATH: fish_add_path $bin_dir"
      fi
      ;;
  esac

  say "${green}✓ Installed $("$install_dir/bin/nexus" --version)${reset}"
  [ -n "$path_note" ] && say "${dim}  $path_note${reset}"
  say ""
  say "Next:"
  say "  ${bold}nexus setup${reset}   ${dim}connect to your NEXUS server (address + API key from the app)${reset}"
  say "  ${bold}nexus${reset}         ${dim}start chatting${reset}"
}

main "$@"
