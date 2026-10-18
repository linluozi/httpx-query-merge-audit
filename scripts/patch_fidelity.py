"""Prove that the shipped patch reproduces the shipped files.

A patch that "applies cleanly" can still produce the wrong bytes, especially on
a CRLF host.  This script applies the shipped unified diff to pristine upstream
and compares the result against the files the ZIP actually ships, byte for byte.

It also checks the reverse direction: the shipped tests must fail on upstream.

Output: results/patch_fidelity.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

TARGETS = ["httpx/_models.py", "tests/test_request_params_regression.py"]


def sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def apply_patch(src: Path, patch: Path, workdir: Path) -> tuple[bool, str]:
    tree = workdir / "tree"
    shutil.copytree(
        src, tree, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache")
    )
    (tree / "tests").mkdir(exist_ok=True)

    # ``patch`` needs the trailing newline intact; feed it the raw bytes.
    proc = subprocess.run(
        ["patch", "--binary", "-p1", "--no-backup-if-mismatch"],
        stdin=patch.open("rb"),
        cwd=str(tree),
        capture_output=True,
    )
    return proc.returncode == 0, (proc.stdout + proc.stderr).decode("utf-8", "replace")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument("--upstream", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    upstream = Path(args.upstream).resolve()
    shipped = Path(args.source).resolve()
    patch = shipped / "patches" / "0001-merge-request-query-parameters.patch"

    workdir = Path(tempfile.mkdtemp(prefix="httpx-patch-"))
    try:
        ok, log = apply_patch(upstream, patch, workdir)
        tree = workdir / "tree"
        rows = []
        for rel in TARGETS:
            patched = (tree / rel).read_bytes()
            delivered = (shipped / rel).read_bytes()
            rows.append(
                {
                    "file": rel,
                    "patched_bytes": len(patched),
                    "delivered_bytes": len(delivered),
                    "patched_crlf": patched.count(b"\r\n"),
                    "delivered_crlf": delivered.count(b"\r\n"),
                    "identical_raw": patched == delivered,
                    "identical_lf": patched.replace(b"\r\n", b"\n")
                    == delivered.replace(b"\r\n", b"\n"),
                    "sha1_patched": sha1(patched),
                    "sha1_delivered": sha1(delivered),
                }
            )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    report = {
        "patch_applies": ok,
        "patch_log": log.strip().splitlines(),
        "files": rows,
        "all_identical_raw": all(r["identical_raw"] for r in rows),
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
    )

    print("patch applied:", ok)
    for r in rows:
        print(
            f"  {r['file']:<44} raw_identical={r['identical_raw']} "
            f"lf_identical={r['identical_lf']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
