#!/usr/bin/env python3
"""Unit tests for prepare_release.py."""

import json
import tempfile
import unittest
from pathlib import Path

from scripts.prepare_release import prepare_release_assets


class TestPrepareRelease(unittest.TestCase):

    def test_deduplication_prefers_lower_os_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            inp = Path(tmp) / "all_wheels"
            out = Path(tmp) / "release_assets"

            for runner, content in [
                ("wheel-macos-26", b"macos26-bytes"),
                ("wheel-xcode-27", b"xcode27-bytes"),
                ("wheel-ubuntu-24.04", b"ubuntu24-bytes"),
                ("wheel-ubuntu-22.04", b"ubuntu22-bytes"),
            ]:
                d = inp / runner
                d.mkdir(parents=True)
                whl_name = (
                    "cryptography-50.0.2-cp311-abi3-macosx_26_0_arm64.whl"
                    if "macos" in runner or "xcode" in runner
                    else "cryptography-50.0.2-cp311-abi3-manylinux_2_34_x86_64.whl"
                )
                (d / whl_name).write_bytes(content)

            manifest = prepare_release_assets(
                input_dir=inp,
                output_dir=out,
                release_tag="release-2026-11-22-1",
                cryptography_version="50.0.2",
                python_boringssl_tag="v1.2026.11.21.0148",
                python_version="3.15.2",
                boringssl_version="20261120-289645a45072",
            )

            self.assertEqual(
                (out / "cryptography-50.0.2-cp311-abi3-macosx_26_0_arm64.whl").read_bytes(),
                b"macos26-bytes",
            )
            self.assertEqual(
                (out / "cryptography-50.0.2-cp311-abi3-manylinux_2_34_x86_64.whl").read_bytes(),
                b"ubuntu22-bytes",
            )
            self.assertTrue((out / "SHA256SUMS.txt").is_file())
            parsed = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(len(parsed["wheels"]), 2)
            self.assertEqual(manifest["cryptography_version"], "50.0.2")


if __name__ == "__main__":
    unittest.main()
