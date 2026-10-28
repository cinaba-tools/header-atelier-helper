#!/bin/bash
# Header Atelier helper: macOS installer (per-user, no sudo).
# Run from the unzipped folder, WITHOUT sudo:  ./install.sh
#
# Everything lives under your own account - no root, no sudoers, no /etc/hosts.
# The staging switch works by running a tiny proxy on 127.0.0.1 (as you) and
# routing only the hostnames you stage through it via the browser's proxy
# setting.
#
# Installs:
#   ~/Library/Application Support/header-atelier-helper/native-host.py
#   a native messaging manifest in EVERY Chromium browser found in your account
#   (Chrome, Chromium, Vivaldi, Brave, Edge - see BROWSER_ROOTS below)

set -euo pipefail

NATIVE_HOST_NAME="com.cinaba.header_atelier"
# Internal build and public build (no Control Center features) of Header
# Atelier: one helper serves both, each pinned to its own ID by its manifest key.
EXTENSION_ID="jckelahhlphngdmllbfjmeapdcailnii"
PUBLIC_EXTENSION_ID="fmhlfkkokgidhamnfnejlinbjgpmgejh"
INSTALL_DIR="$HOME/Library/Application Support/header-atelier-helper"
APP_SUPPORT="$HOME/Library/Application Support"
# Name and folder used up to helper 1.4.x: an install over one of those moves
# its hostnames here and removes it.
OLD_HOST_NAME="top.abasiletools.tcportal.staging"
OLD_INSTALL_DIR="$APP_SUPPORT/tcportal-staging"

# Chromium browsers keep NativeMessagingHosts at their data root, one per
# browser. Registering only Chrome is why Vivaldi used to fall back to an old
# system-wide registration and fail. Format: "Label|path under Application Support".
BROWSER_ROOTS=(
  "Google Chrome|Google/Chrome"
  "Google Chrome Beta|Google/Chrome Beta"
  "Google Chrome Canary|Google/Chrome Canary"
  "Chromium|Chromium"
  "Vivaldi|Vivaldi"
  "Brave|BraveSoftware/Brave-Browser"
  "Microsoft Edge|Microsoft Edge"
)

# Retired pre-1.2.0 build: installed as root, outside the home folder, so
# ./uninstall.sh cannot remove it and it can still shadow this install.
LEGACY_SYSTEM_MANIFEST="/Library/Google/Chrome/NativeMessagingHosts/$OLD_HOST_NAME.json"
LEGACY_SYSTEM_DIR="/usr/local/libexec/tcportal-staging"
LEGACY_SUDOERS="/etc/sudoers.d/tcportal-staging"

if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
  echo "Do NOT run this with sudo - it installs into your own home folder." >&2
  echo "Run:  ./install.sh" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! /usr/bin/env python3 --version >/dev/null 2>&1; then
  echo "WARNING: python3 not found. Install the Xcode Command Line Tools first:" >&2
  echo "         xcode-select --install" >&2
fi

echo "Installing native host to $INSTALL_DIR ..."
mkdir -p "$INSTALL_DIR"
install -m 755 "$SCRIPT_DIR/native-host.py" "$INSTALL_DIR/native-host.py"

if [[ -d "$OLD_INSTALL_DIR" ]]; then
  echo "Moving over from the previous helper (1.4.x) ..."
  if [[ -f "$OLD_INSTALL_DIR/state.json" && ! -f "$INSTALL_DIR/state.json" ]]; then
    cp "$OLD_INSTALL_DIR/state.json" "$INSTALL_DIR/state.json"
  fi
  # Its proxy is detached and would keep running; stop it, but only if the
  # pid really is that proxy and not a process that reused the number.
  old_pid="$(cat "$OLD_INSTALL_DIR/proxy.pid" 2>/dev/null || true)"
  if [[ "$old_pid" =~ ^[0-9]+$ ]] && ps -p "$old_pid" -o command= 2>/dev/null | grep -q "native-host.py"; then
    kill "$old_pid" 2>/dev/null || true
    sleep 1
  fi
  for entry in "${BROWSER_ROOTS[@]}"; do
    rm -f "$APP_SUPPORT/${entry#*|}/NativeMessagingHosts/$OLD_HOST_NAME.json"
  done
  rm -rf "$OLD_INSTALL_DIR"
fi

# The proxy is a detached process: nothing else would ever stop it, so an
# upgrade used to leave the previous version serving every staged hostname.
echo "Replacing any staging proxy left running by a previous version ..."
/usr/bin/env python3 "$INSTALL_DIR/native-host.py" restart-daemon || true

write_manifest() {
  local hosts_dir="$1"
  mkdir -p "$hosts_dir"
  cat > "$hosts_dir/$NATIVE_HOST_NAME.json" <<EOF
{
  "name": "$NATIVE_HOST_NAME",
  "description": "Header Atelier helper (localhost proxy)",
  "path": "$INSTALL_DIR/native-host.py",
  "type": "stdio",
  "allowed_origins": ["chrome-extension://$EXTENSION_ID/", "chrome-extension://$PUBLIC_EXTENSION_ID/"]
}
EOF
}

echo "Registering the native messaging host (per-user) ..."
registered=0
for entry in "${BROWSER_ROOTS[@]}"; do
  label="${entry%%|*}"
  rel="${entry#*|}"
  root="$APP_SUPPORT/$rel"
  # Only browsers actually present in this account: creating the data root of a
  # browser that is not installed would be litter.
  [[ -d "$root" ]] || continue
  write_manifest "$root/NativeMessagingHosts"
  echo "  - $label"
  registered=$((registered + 1))
done

if [[ "$registered" -eq 0 ]]; then
  # No Chromium browser profile yet (fresh account, or never launched).
  write_manifest "$APP_SUPPORT/Google/Chrome/NativeMessagingHosts"
  echo "  - Google Chrome (no browser profile found yet; registered anyway)"
  registered=1
fi

echo
echo "Done - registered for $registered browser(s). Quit the browser COMPLETELY"
echo "(Cmd+Q) and reopen it, then open the Header Atelier popup: the network"
echo "tab will be active."
echo "No admin rights or password are ever required."

# Warn last so it is the final thing on screen.
if [[ -e "$LEGACY_SYSTEM_MANIFEST" || -e "$LEGACY_SYSTEM_DIR" || -e "$LEGACY_SUDOERS" ]]; then
  echo
  echo "WARNING: leftovers from the retired pre-1.2.0 helper are still on this Mac." >&2
  echo "That build ran as root and edited /etc/hosts; its system-wide registration" >&2
  echo "can still win in some browsers, producing errors about a missing sudo rule." >&2
  echo "It is not used any more - remove it once (this is the only step needing sudo):" >&2
  echo >&2
  # Plain `[[ ... ]] && echo` would make a false test the script's exit status,
  # and `set -e` would turn that into a failed install.
  if [[ -e "$LEGACY_SYSTEM_MANIFEST" ]]; then echo "  sudo rm -f '$LEGACY_SYSTEM_MANIFEST'" >&2; fi
  if [[ -e "$LEGACY_SUDOERS" ]]; then echo "  sudo rm -f '$LEGACY_SUDOERS'" >&2; fi
  if [[ -e "$LEGACY_SYSTEM_DIR" ]]; then echo "  sudo rm -rf '$LEGACY_SYSTEM_DIR'" >&2; fi
fi
