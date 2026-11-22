#!/usr/bin/env python3
"""
prepare_release.py - Deduplicates built wheels across runners and writes
SHA256SUMS.txt and manifest.json into release_assets/.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path


def runner_sort_key(artifact_dir_name: str) -> tuple[int, tuple[int, ...], str]:
    """Order runner artifact dirs so lower minimum OS/glibc versions take priority on collisions."""
    name = artifact_dir_name.removeprefix("wheel-")
    low = name.lower()
    if low.startswith("ubuntu-"):
        family = 0
    elif low.startswith("macos-") or low.startswith("xcode-"):
        family = 1
    elif low.startswith("windows-"):
        family = 2
    else:
        family = 3

    nums = tuple(int(n) for n in re.findall(r"\d+", low))
    return (family, nums, low)


def prepare_release_assets(
    input_dir: Path,
    output_dir: Path,
    release_tag: str,
    cryptography_version: str,
    python_boringssl_tag: str,
    python_version: str,
    boringssl_version: str,
) -> dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)

    artifact_dirs = sorted(
        [p for p in input_dir.iterdir() if p.is_dir()],
        key=lambda p: runner_sort_key(p.name),
    )

    wheels_meta: dict[str, dict[str, str]] = {}
    checksum_lines: list[str] = []

    for adir in artifact_dirs:
        runner_name = adir.name.removeprefix("wheel-")
        for whl in sorted(adir.glob("*.whl")):
            dest = output_dir / whl.name
            if whl.name in wheels_meta:
                kept_by = wheels_meta[whl.name]["runner"]
                print(
                    f"Skipping duplicate wheel {whl.name} from {runner_name} (already keeping {kept_by})"
                )
                continue

            shutil.copy2(whl, dest)
            digest = hashlib.sha256(dest.read_bytes()).hexdigest()
            wheels_meta[whl.name] = {
                "filename": whl.name,
                "sha256": digest,
                "runner": runner_name,
            }
            checksum_lines.append(f"{digest}  {whl.name}")

    if not wheels_meta:
        raise RuntimeError(f"No .whl files found under {input_dir}")

    (output_dir / "SHA256SUMS.txt").write_text("\n".join(checksum_lines) + "\n", encoding="utf-8")

    manifest = {
        "release_tag": release_tag,
        "cryptography_version": cryptography_version,
        "python_boringssl_tag": python_boringssl_tag,
        "python_version": python_version,
        "boringssl_version": boringssl_version,
        "wheels": wheels_meta,
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare release assets for cryptography-wheels")
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--release-tag", required=True)
    parser.add_argument("--cryptography-version", required=True)
    parser.add_argument("--python-boringssl-tag", required=True)
    parser.add_argument("--python-version", required=True)
    parser.add_argument("--boringssl-version", required=True)
    args = parser.parse_args()

    manifest = prepare_release_assets(
        input_dir=args.input_dir,
        output_dir=args.output_dir,
        release_tag=args.release_tag,
        cryptography_version=args.cryptography_version,
        python_boringssl_tag=args.python_boringssl_tag,
        python_version=args.python_version,
        boringssl_version=args.boringssl_version,
    )
    print(f"Prepared {len(manifest['wheels'])} unique wheels in {args.output_dir}:")
    for name, info in manifest["wheels"].items():
        print(f"  - {name} ({info['runner']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
