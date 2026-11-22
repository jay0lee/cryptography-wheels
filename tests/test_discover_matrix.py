#!/usr/bin/env python3
"""Unit tests for discover_matrix.py."""

import unittest
from datetime import date
from unittest.mock import patch

from scripts.discover_matrix import (
    check_latest_wheel_release_matches,
    get_supported_runners,
    parse_available_runners_from_readme,
    parse_brownout_from_issue,
    parse_cryptography_version_from_pyproject,
)


class TestDiscoverMatrix(unittest.TestCase):

    def test_parse_cryptography_version_from_pyproject(self):
        sample_toml = """
[project]
name = "gam"
dependencies = [
    "google-auth>=2.0.0",
    "cryptography==50.0.2",
    "urllib3>=2.0.0",
]
"""
        self.assertEqual(parse_cryptography_version_from_pyproject(sample_toml), "50.0.2")

    def test_parse_cryptography_version_missing_raises(self):
        sample_toml = """
[project]
name = "gam"
dependencies = ["urllib3>=2.0.0"]
"""
        with self.assertRaises(RuntimeError):
            parse_cryptography_version_from_pyproject(sample_toml)

    def test_parse_brownout_dates(self):
        body = "macos-14 brownouts will begin October 6th, 2026."
        fb, cutoff = parse_brownout_from_issue(body, reference_year=2026)
        self.assertEqual(fb, date(2026, 10, 6))
        self.assertEqual(cutoff, date(2026, 10, 5))

    def test_parse_available_runners_filters_paid_slim_vs2026(self):
        sample_readme = """
## Available Images

| Image | Architecture | YAML Label | Included Software |
|-------|--------------|------------|-------------------|
| Ubuntu 24.04 | x64 | `ubuntu-24.04`, or `ubuntu-latest` | ... |
| Ubuntu 24.04 Arm64 | arm64 | `ubuntu-24.04-arm` | ... |
| Ubuntu Slim | x64 | `ubuntu-slim` | ... |
| macOS 15 | x64 | `macos-15-large`, or `macos-15-intel` | ... |
| macOS 26 Arm64 | arm64 | `macos-26`, or `macos-26-xlarge` | ... |
| Windows 2025 VS 2026 | x64 | `windows-2025-vs2026` | ... |
| Windows Server 2025 | x64 | `windows-2025`, or `windows-latest` | ... |
| Windows 11 Arm64 | arm64 | `windows-11-arm` | ... |
| macOS 14 | x64 | `macos-14-large` | ... |
"""
        runners = parse_available_runners_from_readme(sample_readme, free_only=True)
        by_os = {r["os"]: r for r in runners}

        self.assertTrue(by_os["ubuntu-24.04"]["active"])
        self.assertEqual(by_os["ubuntu-24.04"]["rust_target"], "x86_64-unknown-linux-gnu")

        self.assertTrue(by_os["ubuntu-24.04-arm"]["active"])
        self.assertEqual(by_os["ubuntu-24.04-arm"]["rust_target"], "aarch64-unknown-linux-gnu")

        self.assertFalse(by_os["ubuntu-slim"]["active"])
        self.assertFalse(by_os["windows-2025-vs2026"]["active"])
        self.assertFalse(by_os["macos-14-large"]["active"])

        self.assertTrue(by_os["macos-15-intel"]["active"])
        self.assertEqual(by_os["macos-15-intel"]["arch"], "x64")

        self.assertTrue(by_os["macos-26"]["active"])
        self.assertEqual(by_os["macos-26"]["arch"], "arm64")

        self.assertTrue(by_os["windows-2025"]["active"])
        self.assertTrue(by_os["windows-11-arm"]["active"])
        self.assertEqual(by_os["windows-11-arm"]["rust_target"], "aarch64-pc-windows-msvc")

    def test_get_supported_runners_excludes_brownout_cutoff(self):
        sample_readme = """
## Available Images
| Image | Architecture | YAML Label | Included Software |
|-------|--------------|------------|-------------------|
| Ubuntu 22.04 | x64 | `ubuntu-22.04` | ... |
| Ubuntu 24.04 | x64 | `ubuntu-24.04` | ... |
"""
        mock_issues = [
            {
                "title": "Ubuntu 22.04 deprecation",
                "body": "ubuntu-22.04 brownouts start November 23, 2026.",
            }
        ]
        with patch("scripts.discover_matrix.http_get_text", return_value=sample_readme):
            with patch("scripts.discover_matrix.github_api_get", return_value=mock_issues):
                active = get_supported_runners(today=date(2026, 11, 22))
        self.assertEqual([r["os"] for r in active], ["ubuntu-24.04"])

    def test_get_supported_runners_deduplicates_windows_per_arch(self):
        sample_readme = """
## Available Images
| Image | Architecture | YAML Label | Included Software |
|-------|--------------|------------|-------------------|
| Windows Server 2025 | x64 | `windows-2025`, or `windows-latest` | ... |
| Windows Server 2022 | x64 | `windows-2022` | ... |
| Windows 11 Arm64 | arm64 | `windows-11-arm` | ... |
"""
        with patch("scripts.discover_matrix.http_get_text", return_value=sample_readme):
            with patch("scripts.discover_matrix.github_api_get", return_value=[]):
                active = get_supported_runners(today=date(2026, 11, 22))
        self.assertEqual([r["os"] for r in active], ["windows-2025", "windows-11-arm"])

    def test_check_latest_wheel_release_matches(self):
        mock_release = {
            "assets": [
                {"name": "manifest.json", "browser_download_url": "https://example.com/manifest.json"}
            ]
        }
        mock_manifest = '{"cryptography_version": "50.0.2", "python_boringssl_tag": "v1.2026.11.22.0400"}'
        with patch("scripts.discover_matrix.github_api_get", return_value=mock_release):
            with patch("scripts.discover_matrix.http_get_text", return_value=mock_manifest):
                self.assertTrue(check_latest_wheel_release_matches("jay0lee/cryptography-wheels", "50.0.2", "v1.2026.11.22.0400"))
                self.assertFalse(check_latest_wheel_release_matches("jay0lee/cryptography-wheels", "50.0.3", "v1.2026.11.22.0400"))


if __name__ == "__main__":
    unittest.main()
