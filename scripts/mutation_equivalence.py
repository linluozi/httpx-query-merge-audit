"""Decide whether a surviving mutant is a real blind spot or an equivalent mutant.

A mutant that survives is only interesting if it *changes observable behaviour*.
This script answers that by running the same case matrix against a single
mutant and diffing it against the shipped package.

Usage: mutation_equivalence.py --source <zip> --out results/mutation_equivalence.json
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mutation_probe import MUTANTS, ORIGINAL  # noqa: E402
from probe_matrix import CASES, RUNNER, U_AWS  # noqa: E402


def evaluate(root: Path) -> dict[str, str]:
    cases_payload = [[cid, body] for cid, _d, body, _e in CASES]
    script = RUNNER.format(u_aws=U_AWS, cases=cases_payload)
    env = dict(os.environ, PYTHONPATH=str(root))
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        cwd=str(root),
    )
    if proc.returncode != 0:
        raise SystemExit(proc.stderr[-2000:])
    raw = json.loads(proc.stdout.strip().splitlines()[-1])
    return {
        cid: (raw[cid]["value"] if raw[cid]["ok"] else "ERROR:" + raw[cid]["error"])
        for cid in raw
    }


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    src = Path(args.source).resolve()
    base = evaluate(src)

    rows = []
    workspace = Path(tempfile.mkdtemp(prefix="httpx-eq-"))
    try:
        for name, (body, description) in MUTANTS.items():
            if name == "M0_unchanged":
                continue
            arm = workspace / name
            shutil.copytree(
                src, arm, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache")
            )
            target = arm / "httpx" / "_models.py"
            text = target.read_text(encoding="utf-8", newline="")
            target.write_text(text.replace(ORIGINAL, body, 1), encoding="utf-8", newline="")

            got = evaluate(arm)
            diffs = [
                {"case": cid, "shipped": base[cid], "mutant": got[cid]}
                for cid in base
                if base[cid] != got[cid]
            ]
            verdict = "equivalent" if not diffs else "real blind spot"
            rows.append(
                {
                    "mutant": name,
                    "description": description,
                    "verdict": verdict,
                    "differing_cases": len(diffs),
                    "diffs": diffs,
                }
            )
            print(f"{name:<30} {verdict:<18} differs in {len(diffs)} case(s)")
            for d in diffs[:3]:
                print(f"    {d['case']}: {d['shipped']} -> {d['mutant']}")
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps({"rows": rows}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
