#!/bin/bash
# Header Atelier helper: macOS uninstaller (per-user, no sudo).
# Run:  ./uninstall.sh
# Stops the local proxy, restores production for every staged hostname (by
# forgetting them and clearing the proxy state), then removes installed files
# from every Chromium browser the installer may have registered.

set -euo pipefail

NATIVE_HOST_NAME="com.cinaba.header_atelier"
INSTALL_DIR="$HOME/Library/Application Support/header-atelier-helper"
APP_SUPPORT="$HOME/Library/Application Support"
# Name and folder used up to helper 1.4.x.
OLD_HOST_NAME="top.abasiletools.tcportal.staging"
OLD_INSTALL_DIR="$APP_SUPPORT/tcportal-staging"

# Keep in step with install.sh.
BROWSER_ROOTS=(
  "Google Chrome|Google/Chrome"
  "Google Chrome Beta|Google/Chrome Beta"
  "Google Chrome Canary|Google/Chrome Canary"
  "Chromium|Chromium"
  "Vivaldi|Vivaldi"
  "Brave|BraveSoftware/Brave-Browser"
  "Microsoft Edge|Microsoft Edge"
)

LEGACY_SYSTEM_MANIFEST="/Library/Google/Chrome/NativeMessagingHosts/$OLD_HOST_NAME.json"
LEGACY_SYSTEM_DIR="/usr/local/libexec/tcportal-staging"
LEGACY_SUDOERS="/etc/sudoers.d/tcportal-staging"

if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
  echo "Do NOT run this with sudo - it only touches your own home folder." >&2
  echo "Run:  ./uninstall.sh" >&2
  exit 1
fi

for dir in "$INSTALL_DIR" "$OLD_INSTALL_DIR"; do
  if [[ -x "$dir/native-host.py" ]]; then
    echo "Stopping the staging proxy and clearing all hostnames ..."
    /usr/bin/env python3 "$dir/native-host.py" clear-all || true
  fi
done

echo "Removing the native messaging manifests ..."
for entry in "${BROWSER_ROOTS[@]}"; do
  label="${entry%%|*}"
  rel="${entry#*|}"
  for name in "$NATIVE_HOST_NAME" "$OLD_HOST_NAME"; do
    manifest="$APP_SUPPORT/$rel/NativeMessagingHosts/$name.json"
    if [[ -e "$manifest" ]]; then
      rm -f "$manifest"
      echo "  - $label"
    fi
  done
done

echo "Removing installed files ..."
rm -rf "$INSTALL_DIR" "$OLD_INSTALL_DIR"

echo
echo "Done. Also open the browser and set every hostname back to Production in"
echo "the Header Atelier popup (or just remove them) so the proxy setting is cleared."

if [[ -e "$LEGACY_SYSTEM_MANIFEST" || -e "$LEGACY_SYSTEM_DIR" || -e "$LEGACY_SUDOERS" ]]; then
  echo
  echo "NOTE: leftovers from the retired pre-1.2.0 helper are still on this Mac." >&2
  echo "They live outside your home folder, so this uninstaller cannot remove them:" >&2
  echo >&2
  if [[ -e "$LEGACY_SYSTEM_MANIFEST" ]]; then echo "  sudo rm -f '$LEGACY_SYSTEM_MANIFEST'" >&2; fi
  if [[ -e "$LEGACY_SUDOERS" ]]; then echo "  sudo rm -f '$LEGACY_SUDOERS'" >&2; fi
  if [[ -e "$LEGACY_SYSTEM_DIR" ]]; then echo "  sudo rm -rf '$LEGACY_SYSTEM_DIR'" >&2; fi
fi
