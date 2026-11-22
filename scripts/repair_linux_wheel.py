#!/usr/bin/env python3
"""
repair_linux_wheel.py - Repairs Linux wheels using auditwheel, with a clean PEP 600
fallback if the host glibc is newer than auditwheel's bundled policy JSON.
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


def detect_host_manylinux_tag(arch: str) -> str:
    """Return PEP 600 manylinux tag matching current host glibc (e.g. manylinux_2_43_x86_64)."""
    libc_ver = ""
    if hasattr(os, "confstr"):
        try:
            libc_ver = os.confstr("CS_GNU_LIBC_VERSION") or ""
        except (ValueError, OSError):
            libc_ver = ""
    match = re.search(r"(\d+)\.(\d+)", libc_ver)
    if not match:
        out = subprocess.check_output(["ldd", "--version"], text=True, errors="replace")
        match = re.search(r"(\d+)\.(\d+)", out.splitlines()[0])
    if not match:
        return f"linux_{arch}"
    return f"manylinux_{match.group(1)}_{match.group(2)}_{arch}"


def retag_wheel(src_whl: Path, dest_dir: Path, new_platform_tag: str) -> Path:
    """Rewrite a wheel's platform tag in filename, WHEEL metadata, and RECORD."""
    stem_parts = src_whl.stem.split("-")
    if len(stem_parts) < 5:
        raise ValueError(f"Unexpected wheel filename format: {src_whl.name}")
    # filename structure: {dist}-{ver}(-{build})?-{py}-{abi}-{plat}
    py_tag, abi_tag = stem_parts[-3], stem_parts[-2]
    stem_parts[-1] = new_platform_tag
    dest_whl = dest_dir / ("-".join(stem_parts) + ".whl")
    full_tag = f"{py_tag}-{abi_tag}-{new_platform_tag}"

    record_path: str | None = None
    updated_files: dict[str, bytes] = {}

    with zipfile.ZipFile(src_whl, "r") as zin:
        infos = zin.infolist()
        for info in infos:
            data = zin.read(info.filename)
            if info.filename.endswith(".dist-info/WHEEL"):
                lines = data.decode("utf-8").splitlines()
                new_lines = []
                replaced_tag = False
                for line in lines:
                    if line.startswith("Tag:"):
                        if not replaced_tag:
                            new_lines.append(f"Tag: {full_tag}")
                            replaced_tag = True
                    else:
                        new_lines.append(line)
                if not replaced_tag:
                    new_lines.append(f"Tag: {full_tag}")
                data = ("\n".join(new_lines) + "\n").encode("utf-8")
            elif info.filename.endswith(".dist-info/RECORD"):
                record_path = info.filename
            updated_files[info.filename] = data

        if record_path is None:
            raise ValueError(f"Wheel missing .dist-info/RECORD: {src_whl}")

        buf = io.StringIO()
        writer = csv.writer(buf, lineterminator="\n")
        for info in infos:
            fname = info.filename
            if fname == record_path:
                writer.writerow([fname, "", ""])
                continue
            payload = updated_files[fname]
            digest = base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).rstrip(b"=").decode("ascii")
            writer.writerow([fname, f"sha256={digest}", str(len(payload))])
        updated_files[record_path] = buf.getvalue().encode("utf-8")

        with zipfile.ZipFile(dest_whl, "w") as zout:
            for info in infos:
                new_info = zipfile.ZipInfo(info.filename, date_time=info.date_time)
                new_info.compress_type = zipfile.ZIP_DEFLATED
                new_info.external_attr = info.external_attr
                zout.writestr(new_info, updated_files[info.filename])

    return dest_whl


def repair_wheel(wheelhouse: Path) -> Path:
    wheels = sorted(wheelhouse.glob("*.whl"))
    if len(wheels) != 1:
        raise RuntimeError(f"Expected 1 wheel in {wheelhouse}, found {len(wheels)}: {wheels}")
    src_whl = wheels[0]

    subprocess.run([sys.executable, "-m", "auditwheel", "show", str(src_whl)], check=False)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_out = Path(tmpdir)
        proc = subprocess.run(
            [sys.executable, "-m", "auditwheel", "repair", str(src_whl), "-w", str(tmp_out)],
            check=False,
        )
        repaired = list(tmp_out.glob("*.whl"))
        if proc.returncode == 0 and repaired:
            src_whl.unlink()
            dest = wheelhouse / repaired[0].name
            shutil.move(str(repaired[0]), str(dest))
            print(f"auditwheel produced: {dest.name}")
            return dest

        print(
            "auditwheel repair did not succeed (likely glibc newer than auditwheel policy); applying host manylinux tag fallback.",
            file=sys.stderr,
        )
        arch = src_whl.stem.split("-")[-1].removeprefix("linux_")
        plat_tag = detect_host_manylinux_tag(arch)
        dest = retag_wheel(src_whl, wheelhouse, plat_tag)
        if dest != src_whl:
            src_whl.unlink()
        print(f"Retagged Linux wheel: {dest.name}")
        return dest


def main() -> int:
    parser = argparse.ArgumentParser(description="Repair or retag built Linux wheel")
    parser.add_argument("--wheelhouse", type=Path, required=True)
    args = parser.parse_args()
    repair_wheel(args.wheelhouse)
    return 0


if __name__ == "__main__":
    sys.exit(main())
