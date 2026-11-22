#!/usr/bin/env python3
"""Unit tests for repair_linux_wheel.py."""

import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts.repair_linux_wheel import retag_wheel


class TestRepairLinuxWheel(unittest.TestCase):

    def test_retag_wheel_updates_filename_wheel_and_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "cryptography-50.0.2-cp311-abi3-linux_x86_64.whl"
            with zipfile.ZipFile(src, "w") as zf:
                zf.writestr("cryptography/__init__.py", "print('ok')\n")
                zf.writestr(
                    "cryptography-50.0.2.dist-info/WHEEL",
                    "Wheel-Version: 1.0\nGenerator: maturin\nRoot-Is-Purelib: false\nTag: cp311-abi3-linux_x86_64\n",
                )
                zf.writestr(
                    "cryptography-50.0.2.dist-info/RECORD",
                    "cryptography/__init__.py,sha256=abc,12\n"
                    "cryptography-50.0.2.dist-info/WHEEL,sha256=def,80\n"
                    "cryptography-50.0.2.dist-info/RECORD,,\n",
                )

            dest = retag_wheel(src, tmp_path, "manylinux_2_43_x86_64")
            self.assertEqual(dest.name, "cryptography-50.0.2-cp311-abi3-manylinux_2_43_x86_64.whl")

            with zipfile.ZipFile(dest, "r") as zf:
                wheel_txt = zf.read("cryptography-50.0.2.dist-info/WHEEL").decode("utf-8")
                self.assertIn("Tag: cp311-abi3-manylinux_2_43_x86_64", wheel_txt)
                self.assertNotIn("linux_x86_64", wheel_txt)
                record_txt = zf.read("cryptography-50.0.2.dist-info/RECORD").decode("utf-8")
                self.assertIn("cryptography/__init__.py,sha256=", record_txt)


if __name__ == "__main__":
    unittest.main()
