#!/usr/bin/env bash
# NEXUS CLI installer for macOS / Linux / Android (Termux).
#   curl -fsSL https://raw.githubusercontent.com/REDOANUZZAMAN/nexus-cli/main/install.sh | bash
# One command does everything: finds Python (or downloads a private copy with uv if there is
# none / it is unusable), installs into ~/.nexus/cli, links ~/.local/bin/nexus, then runs
# `nexus setup` the first time.
# Options (env vars): NEXUS_VERSION, NEXUS_INSTALL_DIR, NEXUS_BIN_DIR, NEXUS_WHEEL (local file or URL),
#                     NEXUS_NO_SETUP=1 (skip the setup step).
set -euo pipefail

main() {
  local version="${NEXUS_VERSION:-0.2.0}"
  local repo="REDOANUZZAMAN/nexus-cli"
  local wheel="${NEXUS_WHEEL:-https://github.com/$repo/releases/download/v$version/nexus_cli-$version-py3-none-any.whl}"
  local root="$HOME/.nexus"
  local install_dir="${NEXUS_INSTALL_DIR:-$root/cli}"
  local bin_dir="${NEXUS_BIN_DIR:-$HOME/.local/bin}"
  local uv_dir="$root/uv"

  local bold="" dim="" green="" red="" violet="" reset=""
  if [ -t 1 ]; then
    bold=$'\033[1m' dim=$'\033[2m' green=$'\033[32m' red=$'\033[31m' violet=$'\033[38;2;167;139;250m' reset=$'\033[0m'
  fi
  say()  { printf '%s\n' "$*"; }
  step() { printf '%s  %s%s\n' "$dim" "$*" "$reset"; }
  fail() { printf '%s✗ %s%s\n' "$red" "$*" "$reset" >&2; exit 1; }
  have() { command -v "$1" >/dev/null 2>&1; }
  fetch() {  # fetch URL → stdout
    if have curl; then curl -fsSL "$1"; elif have wget; then wget -qO- "$1"; else return 1; fi
  }
  # spin LABEL CMD... → runs CMD quietly with an animated line, then ✓ LABEL (or returns its error)
  spin() {
    local label="$1"; shift
    if [ ! -t 1 ]; then step "$label…"; "$@" >/dev/null 2>&1; return; fi
    "$@" >/dev/null 2>&1 &
    local pid=$! i=0 rc=0 start=$SECONDS
    local frames=(⠋ ⠙ ⠹ ⠸ ⠼ ⠴ ⠦ ⠧ ⠇ ⠏)
    printf '\033[?25l'
    while kill -0 "$pid" 2>/dev/null; do
      printf '\r  %s%s%s %s %s%ss%s' "$violet" "${frames[i % 10]}" "$reset" "$label" "$dim" $((SECONDS - start)) "$reset"
      i=$((i + 1)); sleep 0.1
    done
    wait "$pid" || rc=$?
    printf '\r\033[K\033[?25h'
    [ "$rc" -eq 0 ] && printf '  %s✓%s %s\n' "$green" "$reset" "$label"
    return "$rc"
  }
  install_uv() {
    fetch https://astral.sh/uv/install.sh |
      env UV_INSTALL_DIR="$uv_dir" UV_NO_MODIFY_PATH=1 INSTALLER_NO_MODIFY_PATH=1 sh
  }
  trap 'printf "\033[?25h"' EXIT

  say "${violet}${bold}✻ Installing NEXUS CLI v$version${reset}"
  mkdir -p "$root"

  # Termux (Android): its own package manager, no uv builds — just make sure python is there.
  if [ -n "${TERMUX_VERSION:-}" ] || [[ "${PREFIX:-}" == *com.termux* ]]; then
    if ! have python; then
      spin "installing Python (Termux)" pkg install -y python || fail "Couldn't install Python. Run: pkg update && pkg install python, then try again."
    fi
  fi

  # 1. Try the Python that's already on this machine.
  local ok=""
  for cand in python3 python; do
    if have "$cand" &&
       "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
      step "using $("$cand" --version 2>&1)"
      rm -rf "$install_dir"
      if "$cand" -m venv "$install_dir" >/dev/null 2>&1 && [ -x "$install_dir/bin/python" ] &&
         "$install_dir/bin/python" -m pip --version >/dev/null 2>&1; then
        ok=1; break
      fi
      rm -rf "$install_dir"
    fi
  done

  # 2. No usable Python (missing, too old, or no venv/pip like stock Ubuntu) → let uv fetch one.
  if [ -z "$ok" ]; then
    step "no ready-to-use Python here, downloading a private copy (one time, ~40 MB)"
    local uv="$uv_dir/uv"
    if [ ! -x "$uv" ]; then
      spin "downloading Python installer" install_uv ||
        fail "Couldn't download the Python installer. Check your internet connection and try again."
    fi
    [ -x "$uv" ] || fail "Couldn't set up Python on this machine. Install Python 3.9+ yourself, then run this again."
    rm -rf "$install_dir"
    spin "downloading Python 3.12" "$uv" venv --quiet --seed --python 3.12 "$install_dir" ||
      fail "Couldn't create a Python environment. Check your internet connection and try again."
  fi

  # 3. Install NEXUS CLI into that private environment.
  spin "downloading NEXUS CLI" "$install_dir/bin/python" -m pip install --quiet --disable-pip-version-check --upgrade "$wheel" ||
    fail "Download/install failed. Check your internet connection and try again."

  # 4. Put `nexus` on PATH.
  mkdir -p "$bin_dir"
  ln -sf "$install_dir/bin/nexus" "$bin_dir/nexus"
  local on_path=1
  case ":$PATH:" in
    *":$bin_dir:"*) ;;
    *)
      on_path=""
      local rc rcs=("$HOME/.profile")
      case "${SHELL:-}" in
        */zsh)  rcs+=("$HOME/.zshrc") ;;
        */bash) rcs+=("$HOME/.bashrc") ;;
      esac
      for rc in "${rcs[@]}"; do
        grep -qs "$bin_dir" "$rc" || printf '\n# NEXUS CLI\nexport PATH="%s:$PATH"\n' "$bin_dir" >> "$rc"
      done
      if [[ "${SHELL:-}" == */fish ]] && have fish; then
        fish -c "fish_add_path -U $bin_dir" >/dev/null 2>&1 || true
      fi
      ;;
  esac

  say "${green}✓ Installed $("$install_dir/bin/nexus" --version)${reset}"

  # 5. First run: connect to the server right away (reads answers from the keyboard, not the pipe).
  if [ -z "${NEXUS_NO_SETUP:-}" ] && [ ! -f "$root/config.json" ] && { : </dev/tty; } 2>/dev/null; then
    say ""
    "$install_dir/bin/nexus" setup </dev/tty || true
  fi

  say ""
  if [ -n "$on_path" ]; then
    say "Start chatting:  ${bold}nexus${reset}"
  else
    say "Start chatting:  ${bold}$bin_dir/nexus${reset}"
    say "${dim}  (in a new terminal window just type: nexus)${reset}"
  fi
  say "${dim}  Change server later with: nexus setup${reset}"
}

main "$@"
