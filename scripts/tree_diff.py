"""Compare the shipped source tree against upstream and report every difference.

Purpose: back the claim "only ``httpx/_models.py`` was modified".  Anything else
that differs is either a packaging artefact (pycache, pytest cache) or an
unannounced change, and that distinction matters for trust.

Output: results/tree_diff.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

NOISE_DIRS = {"__pycache__", ".pytest_cache"}
NOISE_SUFFIXES = {".pyc"}
# Files the ZIP adds on purpose; everything else being equal is the point.
ADDED_ALLOWED = {
    "tests/test_request_params_regression.py",
    "patches/0001-merge-request-query-parameters.patch",
    "AUDIT_REPORT.md",
    "BASELINE_TESTS.txt",
    "MODIFICATION_LOG.md",
    "ORIGINAL_BASELINE_REPRODUCTION.txt",
    "README.md",
    "UPSTREAM_PROVENANCE.md",
    "VERIFICATION_RESULTS.md",
    "pyproject.toml",
}


def walk(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in NOISE_DIRS for part in path.parts):
            continue
        if path.suffix in NOISE_SUFFIXES:
            continue
        rel = path.relative_to(root).as_posix()
        out[rel] = hashlib.sha1(path.read_bytes()).hexdigest()
    return out


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument("--upstream", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    upstream = walk(Path(args.upstream).resolve())
    shipped = walk(Path(args.source).resolve())

    only_upstream = sorted(set(upstream) - set(shipped))
    only_shipped = sorted(set(shipped) - set(upstream))
    common = sorted(set(upstream) & set(shipped))
    changed = [f for f in common if upstream[f] != shipped[f]]

    unexpected = [f for f in only_shipped if f not in ADDED_ALLOWED]
    report = {
        "upstream_file_count": len(upstream),
        "shipped_file_count": len(shipped),
        "common_file_count": len(common),
        "changed": changed,
        "only_in_upstream": only_upstream,
        "only_in_shipped": only_shipped,
        "only_in_shipped_unexpected": unexpected,
        "verdict": "only _models.py changed"
        if changed == ["httpx/_models.py"] and not unexpected
        else "unexpected differences present",
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
    )

    print(f"common files   : {len(common)}")
    print(f"changed        : {changed or 'none'}")
    print(f"only upstream  : {len(only_upstream)} {only_upstream[:6]}")
    print(f"only shipped   : {only_shipped}")
    print(f"verdict        : {report['verdict']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
