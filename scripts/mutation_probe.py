"""Fault-injection check on the shipped package's own regression suite.

The shipped ZIP claims 9 passing tests prove the fix.  Passing tests only
prove the tests pass; this script asks the harder question: *if the fix were
wrong in a specific way, would those 9 tests notice?*

Each mutant is a plausible-but-wrong implementation of the same intent.
A mutant that still passes the suite is a blind spot in the suite.

Output: results/mutation.json
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

ORIGINAL = """        if params is None:
            self.url = URL(url)
        else:
            # Merge request parameters with any query string already in the URL.
            # URL.copy_merge_params() preserves unrelated and repeated query keys,
            # while supplied keys replace prior values in accordance with
            # QueryParams.merge() semantics.
            self.url = URL(url).copy_merge_params(params)
"""

MUTANTS: dict[str, tuple[str, str]] = {
    "M0_unchanged": (ORIGINAL, "control: the code exactly as shipped"),
    "M1_revert_to_replace": (
        "        self.url = URL(url) if params is None else URL(url, params=params)\n",
        "revert the fix entirely",
    ),
    "M2_copy_with_instead_of_merge": (
        ORIGINAL.replace("copy_merge_params(params)", "copy_with(params=params)"),
        "replace the query instead of merging it",
    ),
    "M3_truthiness_guard": (
        ORIGINAL.replace("if params is None:", "if not params:"),
        "use truthiness instead of an identity check",
    ),
    "M4_drop_none_branch": (
        "        self.url = URL(url).copy_merge_params(params)\n",
        "always merge, never take the fast path",
    ),
    "M5_drop_fragment": (
        ORIGINAL.replace(
            "copy_merge_params(params)",
            "copy_merge_params(params).copy_with(fragment=None)",
        ),
        "merge but silently drop the URL fragment",
    ),
    "M6_merge_then_clear_old_keys": (
        ORIGINAL.replace(
            "self.url = URL(url).copy_merge_params(params)",
            "self.url = URL(url).copy_with(params=QueryParams(params))",
        ),
        "keep the new signature but still discard old keys",
    ),
}


def run_suite(root: Path, test_file: Path) -> tuple[int, int, str]:
    env = dict(os.environ, PYTHONPATH=str(root))
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(test_file), "-p", "no:cacheprovider"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        cwd=str(root),
    )
    summary = ""
    for line in reversed(proc.stdout.strip().splitlines()):
        if "passed" in line or "failed" in line or "error" in line:
            summary = line.strip()
            break
    passed = failed = 0
    if summary:
        for token in summary.replace("=", " ").split():
            pass
        import re

        m_pass = re.search(r"(\d+) passed", summary)
        m_fail = re.search(r"(\d+) (?:failed|error)", summary)
        passed = int(m_pass.group(1)) if m_pass else 0
        failed = int(m_fail.group(1)) if m_fail else 0
    return passed, failed, summary


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, help="extracted ZIP package root")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    src = Path(args.source).resolve()
    models = src / "httpx" / "_models.py"
    test_file_rel = "tests/test_request_params_regression.py"

    if ORIGINAL not in models.read_text(encoding="utf-8", newline=""):
        raise SystemExit("FATAL: the shipped _models.py does not match the expected shape")

    rows = []
    workspace = Path(tempfile.mkdtemp(prefix="httpx-mut-"))
    try:
        for name, (body, description) in MUTANTS.items():
            arm = workspace / name
            shutil.copytree(
                src, arm, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache")
            )
            target = arm / "httpx" / "_models.py"
            text = target.read_text(encoding="utf-8", newline="")
            target.write_text(text.replace(ORIGINAL, body, 1), encoding="utf-8", newline="")

            passed, failed, summary = run_suite(arm, arm / test_file_rel)
            caught = failed > 0
            rows.append(
                {
                    "mutant": name,
                    "description": description,
                    "passed": passed,
                    "failed": failed,
                    "summary": summary,
                    "detected": caught,
                }
            )
            flag = "caught" if caught else "SURVIVED (blind spot)"
            print(f"{name:<30} {passed}p/{failed}f  {flag}")
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    total = len(rows)
    detected = sum(1 for r in rows if r["detected"])
    survivors = [r["mutant"] for r in rows if not r["detected"]]
    payload = {
        "suite_size": "9 tests in tests/test_request_params_regression.py",
        "mutants_total": total,
        "mutants_detected": detected,
        "survivors": survivors,
        "rows": rows,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
    )
    print(f"\n{detected}/{total} mutants detected; survivors: {', '.join(survivors) or 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
