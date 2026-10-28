#!/usr/bin/env python3
"""Header Atelier helper: Windows installer logic.

Run through install.bat (it locates Python 3 and launches this script).
Everything is per-user - files under %LOCALAPPDATA%, registration under HKCU -
so NO administrator rights are needed.

Installs:
  %LOCALAPPDATA%\\header-atelier-helper\\native-host.py
  %LOCALAPPDATA%\\header-atelier-helper\\native-host.bat      (Chrome's entry point)
  %LOCALAPPDATA%\\header-atelier-helper\\com.cinaba.header_atelier.json
  HKCU\\Software\\<browser>\\NativeMessagingHosts\\com.cinaba.header_atelier
  (one key per Chromium browser - see BROWSER_REG_SUBKEYS)
"""

import json
import os
import shutil
import subprocess
import sys
import time

NATIVE_HOST_NAME = "com.cinaba.header_atelier"
# Internal build and public build (no Control Center features) of Header
# Atelier: one helper serves both, each pinned to its own ID by its manifest key.
EXTENSION_IDS = (
    "jckelahhlphngdmllbfjmeapdcailnii",  # internal build
    "fmhlfkkokgidhamnfnejlinbjgpmgejh",  # public build, loaded unpacked
    "aecomcdlbbnegglengjcflgdodncmljj",  # public build from the Chrome Web Store
)
LOCAL_APPDATA = os.environ.get("LOCALAPPDATA", os.path.expanduser(r"~\AppData\Local"))
INSTALL_DIR = os.path.join(LOCAL_APPDATA, "header-atelier-helper")
# Name and folder used up to helper 1.4.x: an install over one of those moves
# its hostnames here and removes it.
OLD_HOST_NAME = "top.abasiletools.tcportal.staging"
OLD_INSTALL_DIR = os.path.join(LOCAL_APPDATA, "tcportal-staging")
# Every Chromium browser reads its own HKCU hive. Registering only Chrome is
# what left Edge/Vivaldi users having to copy the key by hand. Writing a key for
# a browser that is not installed is harmless (nothing reads it) and means the
# helper just works if that browser is installed later.
BROWSER_REG_SUBKEYS = [
    ("Google Chrome", r"Software\Google\Chrome\NativeMessagingHosts"),
    ("Microsoft Edge", r"Software\Microsoft\Edge\NativeMessagingHosts"),
    ("Vivaldi", r"Software\Vivaldi\NativeMessagingHosts"),
    ("Brave", r"Software\BraveSoftware\Brave-Browser\NativeMessagingHosts"),
    ("Chromium", r"Software\Chromium\NativeMessagingHosts"),
]
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def bat_safe_path(path):
    """cmd.exe parses .bat files in the OEM code page, so a path with
    characters outside it (e.g. an accented username) would come out garbled.
    Fall back to the DOS 8.3 short form, which is plain ASCII."""
    try:
        path.encode("ascii")
        return path
    except UnicodeEncodeError:
        pass
    import ctypes
    buf = ctypes.create_unicode_buffer(1024)
    if ctypes.windll.kernel32.GetShortPathNameW(path, buf, 1024):
        return buf.value
    return path  # short names disabled on the volume: keep the long path


def move_from_old_helper(winreg):
    if not os.path.isdir(OLD_INSTALL_DIR):
        return
    print("Moving over from the previous helper (1.4.x) ...")
    old_state = os.path.join(OLD_INSTALL_DIR, "state.json")
    new_state = os.path.join(INSTALL_DIR, "state.json")
    if os.path.isfile(old_state) and not os.path.isfile(new_state):
        shutil.copy2(old_state, new_state)
    # Its proxy is detached and would keep running; stop it, but only if the
    # pid really is a Python process and not one that reused the number.
    try:
        with open(os.path.join(OLD_INSTALL_DIR, "proxy.pid")) as f:
            pid = int(f.read().strip())
        found = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid, "/FO", "CSV", "/NH"],
                               capture_output=True, text=True, timeout=15).stdout
        if "python" in found.lower():
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, timeout=15)
            time.sleep(1)
    except Exception:  # noqa: BLE001 - no pid file, or nothing to stop
        pass
    for _label, subkey in BROWSER_REG_SUBKEYS:
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, subkey + "\\" + OLD_HOST_NAME)
        except OSError:
            pass
    shutil.rmtree(OLD_INSTALL_DIR, ignore_errors=True)


def main():
    if os.name != "nt":
        sys.exit("This installer is for Windows. On macOS use header_atelier_native_host/install.sh.")
    if sys.version_info < (3, 8):
        sys.exit("Python 3.8 or newer is required (found %d.%d). Install it from "
                 "https://www.python.org/downloads/ - no admin rights needed." % sys.version_info[:2])
    import winreg

    print("Installing native host to %s ..." % INSTALL_DIR)
    os.makedirs(INSTALL_DIR, exist_ok=True)
    shutil.copy2(os.path.join(SCRIPT_DIR, "native-host.py"),
                 os.path.join(INSTALL_DIR, "native-host.py"))

    # Chrome on Windows can only launch .exe/.bat native hosts, so a wrapper
    # .bat starts the script with the same Python that ran this installer.
    # The absolute path matters: Chrome must not depend on PATH (or hit the
    # WindowsApps "python" stub that opens the Microsoft Store).
    bat_path = os.path.join(INSTALL_DIR, "native-host.bat")
    python_exe = bat_safe_path(sys.executable)
    with open(bat_path, "w", encoding="ascii", errors="replace", newline="\r\n") as f:
        f.write('@echo off\n"%s" "%%~dp0native-host.py" %%*\n' % python_exe)

    move_from_old_helper(winreg)

    # The proxy is a detached process: nothing else would ever stop it, so an
    # upgrade used to leave the previous version serving every staged hostname.
    print("Replacing any staging proxy left running by a previous version ...")
    try:
        subprocess.run([sys.executable, os.path.join(INSTALL_DIR, "native-host.py"), "restart-daemon"],
                       timeout=30, check=False)
    except Exception as e:  # noqa: BLE001 - never block the install on this
        print("  (could not restart the proxy: %s)" % e)

    print("Registering the native messaging host (per-user, HKCU) ...")
    manifest_path = os.path.join(INSTALL_DIR, NATIVE_HOST_NAME + ".json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump({
            "name": NATIVE_HOST_NAME,
            "description": "Header Atelier helper (localhost proxy)",
            "path": bat_path,
            "type": "stdio",
            "allowed_origins": ["chrome-extension://%s/" % ext_id for ext_id in EXTENSION_IDS],
        }, f, indent=2)
    for label, subkey in BROWSER_REG_SUBKEYS:
        full_key = subkey + "\\" + NATIVE_HOST_NAME
        try:
            winreg.SetValue(winreg.HKEY_CURRENT_USER, full_key, winreg.REG_SZ, manifest_path)
            print("  - %s" % label)
        except OSError as e:  # one locked-down hive must not abort the install
            print("  - %s: SKIPPED (%s)" % (label, e))

    print()
    print("Done. Now exit the browser COMPLETELY and reopen it: use the Chrome menu")
    print("(three dots) > Exit, or right-click the Chrome icon in the system tray")
    print("and choose Exit - just closing the windows is not enough, Chrome keeps")
    print("running in the background. Then open the Header Atelier popup: the")
    print("network tab will be active.")
    print("No administrator rights or password are ever required.")


if __name__ == "__main__":
    main()
