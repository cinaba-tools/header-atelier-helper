"""Unit and protocol tests for the Header Atelier native helper.

Run from the helper folder:  python3 -m unittest discover -s tests -v
Needs Python 3.8+. No dependencies, no network, nothing installed: the tests
only load the two native-host.py files and talk to them over stdin/stdout.
The Windows helper runs on macOS/Linux too, since its Windows-only code is
behind `os.name == "nt"`.
"""

import importlib.util
import json
import os
import struct
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
HELPERS = {
    "macos": os.path.join(HERE, "..", "macos", "native-host.py"),
    "windows": os.path.join(HERE, "..", "windows", "native-host.py"),
}


def load(platform):
    spec = importlib.util.spec_from_file_location(f"native_host_{platform}", HELPERS[platform])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def ask(platform, message):
    """Send one native-messaging frame, return the decoded reply."""
    payload = json.dumps(message).encode("utf-8")
    frame = struct.pack("<I", len(payload)) + payload
    proc = subprocess.run(
        [sys.executable, HELPERS[platform]], input=frame, capture_output=True, timeout=20
    )
    out = proc.stdout
    if len(out) < 4:
        raise AssertionError(f"no reply (stderr: {proc.stderr.decode(errors='replace')})")
    (length,) = struct.unpack("<I", out[:4])
    return json.loads(out[4 : 4 + length].decode("utf-8"))


class SharedLogic(unittest.TestCase):
    """The same rules must hold in both helpers."""

    def for_each(self, check):
        for platform in HELPERS:
            with self.subTest(platform=platform):
                check(load(platform))

    def test_versions_match(self):
        self.assertEqual(load("macos").VERSION, load("windows").VERSION)

    def test_staging_equivalent(self):
        def check(m):
            self.assertEqual(m.staging_equivalent("www.example.com.edgekey.net"),
                             "www.example.com.edgekey-staging.net")
            self.assertEqual(m.staging_equivalent("a1.g.akamai.net"), "a1.g.akamai-staging.net")
            self.assertEqual(m.staging_equivalent("x.edgesuite.net"), "x.edgesuite-staging.net")
            # already a staging edge hostname: kept as it is
            self.assertEqual(m.staging_equivalent("x.edgekey-staging.net"), "x.edgekey-staging.net")
            # not an Akamai edge hostname
            self.assertIsNone(m.staging_equivalent("www.example.com"))
            self.assertIsNone(m.staging_equivalent("notedgekey.net"))
        self.for_each(check)

    def test_ipv4_validation(self):
        def check(m):
            self.assertTrue(m.valid_ipv4("23.61.194.55"))
            self.assertFalse(m.valid_ipv4("256.1.1.1"))
            self.assertFalse(m.valid_ipv4("23.61.194"))
            self.assertFalse(m.valid_ipv4("example.com"))
        self.for_each(check)

    def test_hostname_validation(self):
        def check(m):
            self.assertTrue(m.HOSTNAME_RE.match("www.example.com"))
            self.assertFalse(m.HOSTNAME_RE.match("localhost"))
            self.assertFalse(m.HOSTNAME_RE.match("bad host.com"))
            self.assertFalse(m.HOSTNAME_RE.match("-bad.example.com"))
        self.for_each(check)

    def test_akamai_suffixes(self):
        def check(m):
            self.assertTrue(m.matches_suffix("e123.a.akamaiedge.net", m.AKAMAI_EDGE_SUFFIXES))
            self.assertFalse(m.matches_suffix("example.akadns.net", m.AKAMAI_EDGE_SUFFIXES))
        self.for_each(check)


class Protocol(unittest.TestCase):
    """Native-messaging framing, end to end, with requests that change nothing."""

    def test_rejects_invalid_hostname(self):
        for platform in HELPERS:
            with self.subTest(platform=platform):
                reply = ask(platform, {"action": "set", "hostname": "not a hostname"})
                self.assertFalse(reply["ok"])
                self.assertIn("invalid hostname", reply["error"])

    def test_rejects_unknown_action(self):
        for platform in HELPERS:
            with self.subTest(platform=platform):
                reply = ask(platform, {"action": "no-such-action"})
                self.assertFalse(reply["ok"])


if __name__ == "__main__":
    unittest.main()
