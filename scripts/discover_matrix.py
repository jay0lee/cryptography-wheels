#!/usr/bin/env python3
"""
discover_matrix.py - Dynamically discovers active free GitHub-hosted runners
and resolves upstream dependency versions for cryptography-wheels.

Outputs (written to GITHUB_OUTPUT when running in GitHub Actions, or printed):
  - matrix: JSON object {"include": [...]} of active runners
  - cryptography_version: Pinned cryptography version from GAM pyproject.toml
  - python_boringssl_tag: Latest release tag from jay0lee/python-boringssl-latest
  - python_version: Python version string from latest python-boringssl-latest release
  - boringssl_version: BoringSSL version string from latest python-boringssl-latest release
  - should_build: "true" or "false"
"""

from __future__ import annotations

import argparse
import calendar
import json
import os
import re
import sys
import tomllib
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from typing import Any

RUNNER_IMAGES_REPO = "actions/runner-images"
RUNNER_IMAGES_README_URL = f"https://raw.githubusercontent.com/{RUNNER_IMAGES_REPO}/main/README.md"
GAM_PYPROJECT_URL = "https://raw.githubusercontent.com/GAM-team/GAM/main/pyproject.toml"
PYTHON_BORINGSSL_REPO = "jay0lee/python-boringssl-latest"

RUST_TARGET_MAP = {
    ("linux", "x64"): "x86_64-unknown-linux-gnu",
    ("linux", "arm64"): "aarch64-unknown-linux-gnu",
    ("macos", "x64"): "x86_64-apple-darwin",
    ("macos", "arm64"): "aarch64-apple-darwin",
    ("windows", "x64"): "x86_64-pc-windows-msvc",
    ("windows", "arm64"): "aarch64-pc-windows-msvc",
}

MONTHS = {
    **{m.lower(): i for i, m in enumerate(calendar.month_name) if m},
    **{m.lower(): i for i, m in enumerate(calendar.month_abbr) if m},
}


def http_get_text(url: str, headers: dict[str, str] | None = None, timeout: int = 20) -> str | None:
    """Fetch URL content as UTF-8 text, returning None on HTTP/network failure."""
    req_headers = {"User-Agent": "cryptography-wheels-discover/1.0"}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, headers=req_headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8")
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError):
        return None


def github_api_get(path: str, token: str | None = None) -> Any:
    """Call GitHub REST API v3 and parse JSON response."""
    url = path if path.startswith("http") else f"https://api.github.com/{path.lstrip('/')}"
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    text = http_get_text(url, headers=headers)
    if text is None:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _extract_os_family(label: str) -> str | None:
    low = label.lower()
    if low.startswith("ubuntu-"):
        return "linux"
    if low.startswith("macos-") or low.startswith("xcode-"):
        return "macos"
    if low.startswith("windows-"):
        return "windows"
    return None


def _normalize_arch(arch_cell: str, label: str) -> str:
    low_arch = arch_cell.lower()
    low_label = label.lower()
    if "arm64" in low_arch or "aarch64" in low_arch or "-arm" in low_label:
        return "arm64"
    if "arm64" not in low_arch and "x64" not in low_arch and "intel" not in low_label:
        # Recent Apple Silicon macOS runners without -intel default to arm64.
        if low_label.startswith("macos-") or low_label.startswith("xcode-"):
            return "arm64"
    return "x64"


def parse_available_runners_from_readme(readme_text: str, free_only: bool = True) -> list[dict[str, Any]]:
    """Parse Available Images tables from actions/runner-images README."""
    runners: list[dict[str, Any]] = []
    seen_labels: set[str] = set()

    in_available = False
    for raw_line in readme_text.splitlines():
        line = raw_line.strip()
        if line.startswith("## "):
            in_available = "available images" in line.lower()
            continue
        if not in_available or not line.startswith("|"):
            continue

        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 3:
            continue
        image_cell, arch_cell, label_cell = cells[0], cells[1], cells[2]
        if image_cell.lower() in {"image", ""} or set(image_cell) <= {"-", " ", ":"}:
            continue

        backticked = re.findall(r"`([^`]+)`", label_cell)
        if not backticked:
            continue

        # Identify paid/excluded labels within this row.
        free_labels: list[str] = []
        paid_labels: list[str] = []
        for lbl in backticked:
            low = lbl.lower()
            if "-large" in low or "-xlarge" in low:
                paid_labels.append(lbl)
            else:
                free_labels.append(lbl)

        # Pick the primary free label (preferring non-'latest' explicit version if present).
        chosen_label: str | None = None
        if free_labels:
            explicit = [l for l in free_labels if not l.endswith("-latest")]
            chosen_label = explicit[0] if explicit else free_labels[0]
        elif not free_only and paid_labels:
            chosen_label = paid_labels[0]
        else:
            # Row has only paid labels (e.g. macos-14-large)
            chosen_label = paid_labels[0]

        if not chosen_label or chosen_label in seen_labels:
            continue

        os_family = _extract_os_family(chosen_label)
        if not os_family:
            continue

        low_chosen = chosen_label.lower()
        is_paid = any(k in low_chosen for k in ("-large", "-xlarge"))
        is_slim = "-slim" in low_chosen
        is_vs2026 = "-vs2026" in low_chosen
        is_preview = "preview" in image_cell.lower() or "beta" in image_cell.lower()
        is_deprecated = "deprecated" in image_cell.lower()

        # Normalize windows-latest alias if row contains both windows-2025 and windows-latest or similar
        arch = _normalize_arch(arch_cell, chosen_label)
        aliases = [l for l in backticked if l != chosen_label]

        active = not (
            (free_only and is_paid)
            or is_slim
            or is_vs2026
            or is_deprecated
        )

        seen_labels.add(chosen_label)
        runners.append(
            {
                "os": chosen_label,
                "os_family": os_family,
                "arch": arch,
                "name": f"{chosen_label} ({arch})",
                "rust_target": RUST_TARGET_MAP[(os_family, arch)],
                "aliases": aliases,
                "preview": is_preview,
                "deprecated": is_deprecated,
                "paid": is_paid,
                "active": active,
            }
        )

    return runners


def parse_brownout_from_issue(body: str, reference_year: int | None = None) -> tuple[date | None, date | None]:
    """Extract first brownout date and cutoff_date (first_brownout - 1 day) from issue text."""
    if not body:
        return None, None
    year_default = reference_year or datetime.now(timezone.utc).year

    date_pattern = re.compile(
        r"\b(" + "|".join(re.escape(m) for m in MONTHS) + r")\.?\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(\d{4}))?\b",
        re.IGNORECASE,
    )

    candidate_dates: list[date] = []
    # Prefer dates found near brownout lines/sentences
    lines = body.splitlines()
    brownout_sections: list[str] = []
    in_brownout_block = False
    for line in lines:
        low = line.lower()
        if "brownout" in low or "brown-out" in low:
            in_brownout_block = True
            brownout_sections.append(line)
        elif in_brownout_block:
            if line.strip().startswith("-") or line.strip().startswith("*") or re.search(r"\d{1,2}:\d{2}", line):
                brownout_sections.append(line)
            elif line.strip() == "":
                continue
            else:
                in_brownout_block = False

    search_text = "\n".join(brownout_sections) if brownout_sections else body
    for match in date_pattern.finditer(search_text):
        month_str, day_str, year_str = match.group(1).lower(), match.group(2), match.group(3)
        month = MONTHS.get(month_str)
        if not month:
            continue
        day = int(day_str)
        year = int(year_str) if year_str else year_default
        try:
            candidate_dates.append(date(year, month, day))
        except ValueError:
            continue

    if not candidate_dates:
        return None, None

    first_brownout = min(candidate_dates)
    cutoff_date = first_brownout - timedelta(days=1)
    return first_brownout, cutoff_date


def fetch_runner_brownouts(labels: list[str], token: str | None = None) -> dict[str, tuple[date | None, date | None]]:
    """Query open actions/runner-images announcement issues for brownout schedules."""
    issues = github_api_get(f"repos/{RUNNER_IMAGES_REPO}/issues?state=open&per_page=50", token=token)
    result: dict[str, tuple[date | None, date | None]] = {}
    if not isinstance(issues, list):
        return result

    for issue in issues:
        title = (issue.get("title") or "").lower()
        body = issue.get("body") or ""
        if "deprecat" not in title and "brownout" not in title and "remov" not in title:
            continue
        text_low = f"{title}\n{body.lower()}"
        for lbl in labels:
            if re.search(rf"(?<![a-z0-9-]){re.escape(lbl.lower())}(?![a-z0-9-])", text_low):
                fb, cutoff = parse_brownout_from_issue(body)
                if fb is not None:
                    prev = result.get(lbl)
                    if prev is None or (prev[0] is not None and fb < prev[0]):
                        result[lbl] = (fb, cutoff)
    return result


def _runner_version_key(label: str) -> tuple[int, ...]:
    """Extract numeric version components from a runner label for OS version comparison."""
    nums = re.findall(r"\d+", label)
    return tuple(int(n) for n in nums)


def _is_preview_runner(runner: dict[str, Any]) -> bool:
    return bool(runner.get("preview")) or runner["os"].lower().startswith("xcode-")


def get_supported_runners(token: str | None = None, today: date | None = None) -> list[dict[str, Any]]:
    """Discover one optimal active free GitHub-hosted runner per (os_family, arch).

    For Linux and macOS, selects the oldest active non-preview OS version per architecture so
    built wheels achieve maximum forward compatibility across newer OS/glibc releases.
    For Windows, selects the newest active non-preview runner per architecture to match
    jay0lee/python-boringssl-latest.
    """
    readme_text = http_get_text(RUNNER_IMAGES_README_URL)
    if readme_text is None:
        # Fallback through GitHub Contents API if raw host fails
        meta = github_api_get(f"repos/{RUNNER_IMAGES_REPO}/readme", token=token)
        if isinstance(meta, dict) and meta.get("download_url"):
            readme_text = http_get_text(meta["download_url"])
    if not readme_text:
        raise RuntimeError(f"Failed to fetch {RUNNER_IMAGES_REPO} README")

    runners = parse_available_runners_from_readme(readme_text, free_only=True)
    if not runners:
        raise RuntimeError(f"Parsed zero runners from {RUNNER_IMAGES_REPO} README")

    check_date = today or datetime.now(timezone.utc).date()
    brownouts = fetch_runner_brownouts([r["os"] for r in runners], token=token)

    by_target: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for r in runners:
        fb, cutoff = brownouts.get(r["os"], (None, None))
        if fb and cutoff:
            r["first_brownout"] = fb.isoformat()
            r["cutoff_date"] = cutoff.isoformat()
            if check_date >= cutoff:
                r["active"] = False
        if r["active"]:
            by_target.setdefault((r["os_family"], r["arch"]), []).append(r)

    selected: list[dict[str, Any]] = []
    for (os_family, _arch), candidates in by_target.items():
        non_preview = [c for c in candidates if not _is_preview_runner(c)]
        pool = non_preview if non_preview else candidates
        if os_family == "windows":
            chosen = max(pool, key=lambda c: (_runner_version_key(c["os"]), c["os"]))
        else:
            chosen = min(pool, key=lambda c: (_runner_version_key(c["os"]), c["os"]))
        selected.append(
            {
                "os": chosen["os"],
                "os_family": chosen["os_family"],
                "arch": chosen["arch"],
                "name": chosen["name"],
                "rust_target": chosen["rust_target"],
            }
        )

    return selected


def parse_cryptography_version_from_pyproject(pyproject_text: str) -> str:
    """Extract pinned cryptography version (==X.Y.Z) from GAM pyproject.toml."""
    data = tomllib.loads(pyproject_text)
    deps = data.get("project", {}).get("dependencies", [])
    for dep in deps:
        if not isinstance(dep, str):
            continue
        match = re.match(r"^\s*cryptography\s*==\s*([A-Za-z0-9._+-]+)", dep)
        if match:
            return match.group(1)
    raise RuntimeError("Could not find pinned cryptography==X.Y.Z in GAM pyproject.toml")


def resolve_cryptography_version(override: str | None = None) -> str:
    """Resolve target cryptography version from GAM main pyproject.toml (unless overridden)."""
    if override and override.strip():
        return override.strip()
    pyproject_text = http_get_text(GAM_PYPROJECT_URL)
    if not pyproject_text:
        raise RuntimeError(f"Failed to fetch {GAM_PYPROJECT_URL}")
    return parse_cryptography_version_from_pyproject(pyproject_text)


def get_python_boringssl_release_info(token: str | None = None) -> dict[str, str]:
    """Fetch latest release tag and versions from jay0lee/python-boringssl-latest."""
    release = github_api_get(f"repos/{PYTHON_BORINGSSL_REPO}/releases/latest", token=token)
    if not isinstance(release, dict) or not release.get("tag_name"):
        return {
            "tag": "unknown",
            "python_version": "unknown",
            "boringssl_version": "unknown",
        }

    tag = str(release["tag_name"])
    py_ver = "unknown"
    bssl_ver = "unknown"

    # Look for manifest.json in release assets for exact versions
    for asset in release.get("assets", []):
        if asset.get("name") == "manifest.json" and asset.get("browser_download_url"):
            text = http_get_text(asset["browser_download_url"])
            if text:
                try:
                    manifest = json.loads(text)
                    py_ver = str(manifest.get("python_version", py_ver))
                    bssl_ver = str(manifest.get("boringssl_version", bssl_ver))
                except json.JSONDecodeError:
                    pass
            break

    return {
        "tag": tag,
        "python_version": py_ver,
        "boringssl_version": bssl_ver,
    }


def check_latest_wheel_release_matches(
    repo: str,
    cryptography_version: str,
    python_boringssl_tag: str,
    token: str | None = None,
) -> bool:
    """Return True if the latest release already built this exact cryptography + BoringSSL combo."""
    release = github_api_get(f"repos/{repo}/releases/latest", token=token)
    if not isinstance(release, dict):
        return False

    for asset in release.get("assets", []):
        if asset.get("name") == "manifest.json" and asset.get("browser_download_url"):
            text = http_get_text(asset["browser_download_url"])
            if not text:
                return False
            try:
                manifest = json.loads(text)
            except json.JSONDecodeError:
                return False
            return (
                manifest.get("cryptography_version") == cryptography_version
                and manifest.get("python_boringssl_tag") == python_boringssl_tag
            )
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Discover runners and dependency versions")
    parser.add_argument("--cryptography-version", default="", help="Override cryptography version")
    parser.add_argument("--event-name", default=os.environ.get("GITHUB_EVENT_NAME", ""), help="GitHub event name")
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""), help="Target GitHub repository")
    parser.add_argument("--force-rebuild", action="store_true", help="Force rebuild even on schedule match")
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")

    runners = get_supported_runners(token=token)
    crypto_ver = resolve_cryptography_version(args.cryptography_version or None)
    bssl_info = get_python_boringssl_release_info(token=token)

    should_build = True
    if args.event_name == "schedule" and not args.force_rebuild and args.repo:
        if check_latest_wheel_release_matches(args.repo, crypto_ver, bssl_info["tag"], token=token):
            should_build = False

    matrix_json = json.dumps({"include": runners}, separators=(",", ":"))
    outputs = {
        "matrix": matrix_json,
        "cryptography_version": crypto_ver,
        "python_boringssl_tag": bssl_info["tag"],
        "python_version": bssl_info["python_version"],
        "boringssl_version": bssl_info["boringssl_version"],
        "should_build": "true" if should_build else "false",
    }

    print(f"Discovered {len(runners)} active runners:")
    for r in runners:
        print(f"  - {r['os']} [{r['os_family']}/{r['arch']}] ({r['rust_target']})")
    print(f"Pinned cryptography version: {crypto_ver}")
    print(f"python-boringssl-latest tag: {bssl_info['tag']} (Python {bssl_info['python_version']}, BoringSSL {bssl_info['boringssl_version']})")
    print(f"should_build: {outputs['should_build']}")

    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a", encoding="utf-8") as f:
            for k, v in outputs.items():
                f.write(f"{k}={v}\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
