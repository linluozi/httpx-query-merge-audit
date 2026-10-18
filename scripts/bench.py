"""Measure how much the merge costs, with the noise floor measured first.

Rules this script follows, learned the hard way:

* Every (arm, case) pair runs in its own subprocess.  Running several configs
  in one process lets V8/CPython warm-up leak between them.
* Arms are interleaved and alternated in order, so a slow machine phase cannot
  land entirely on one arm.
* The noise floor is measured first with two copies of *byte-identical* code.
  If the noise floor is comparable to the effect, no ratio is reported.
* Ratios are reported per round, and the median of the ratios is quoted; the
  absolute microseconds are only used to say "same order of magnitude".

Output: results/bench.json
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path

CODE = r'''
import statistics, sys, time
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import httpx

URL_WITH_QUERY = "https://example.com/search?page=1&s=list&q=hello+world"
URL_PLAIN = "https://example.com/search"
PARAMS = {"pid": 0, "tags": "k-on!"}

def bench(fn, n):
    fn()
    samples = []
    for _ in range(7):
        t0 = time.perf_counter()
        for _ in range(n):
            fn()
        samples.append((time.perf_counter() - t0) / n * 1e6)
    return statistics.median(samples)

N = 8000
cases = {
    "request_with_query": lambda: httpx.Request("GET", URL_WITH_QUERY, params=PARAMS),
    "request_plain": lambda: httpx.Request("GET", URL_PLAIN, params=PARAMS),
    "request_no_params": lambda: httpx.Request("GET", URL_WITH_QUERY),
}
print(__import__("json").dumps({k: bench(v, N) for k, v in cases.items()}))
'''

CASES = ("request_with_query", "request_plain", "request_no_params")


def run_arm(root: Path) -> dict[str, float]:
    env = dict(os.environ, PYTHONPATH=str(root))
    proc = subprocess.run(
        [sys.executable, "-c", CODE],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        cwd=str(root),
    )
    if proc.returncode != 0:
        raise SystemExit(proc.stderr[-2000:])
    import json as _json

    return _json.loads(proc.stdout.strip().splitlines()[-1])


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument("--arms", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--rounds", type=int, default=5)
    args = parser.parse_args()

    arms_root = Path(args.arms).resolve()
    arms = sorted(p.name for p in arms_root.iterdir() if (p / "httpx" / "__init__.py").is_file())

    # Noise floor: two byte-identical copies of the pristine upstream tree.
    workspace = Path(tempfile.mkdtemp(prefix="httpx-bench-"))
    a, b = workspace / "aa", workspace / "bb"
    src = arms_root / "upstream"
    for dest in (a, b):
        shutil.copytree(src, dest, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))

    noise: dict[str, list[float]] = {c: [] for c in CASES}
    for rnd in range(args.rounds):
        order = [a, b] if rnd % 2 == 0 else [b, a]
        vals = [run_arm(p) for p in order]
        for c in CASES:
            noise[c].append(vals[0][c] / vals[1][c])
    shutil.rmtree(workspace, ignore_errors=True)

    noise_report = {}
    for c, v in noise.items():
        ordered = sorted(v)
        noise_report[c] = {
            "ratio_median": round(statistics.median(v), 4),
            "ratio_p25": round(ordered[max(0, int(0.25 * (len(ordered) - 1)))], 4),
            "ratio_p75": round(ordered[int(0.75 * (len(ordered) - 1))], 4),
            "ratio_min": round(min(v), 4),
            "ratio_max": round(max(v), 4),
            "ratio_samples": [round(x, 4) for x in v],
        }

    samples: dict[str, dict[str, list[float]]] = {c: {a_: [] for a_ in arms} for c in CASES}
    for rnd in range(args.rounds):
        order = arms if rnd % 2 == 0 else list(reversed(arms))
        for arm in order:
            vals = run_arm(arms_root / arm)
            for c in CASES:
                samples[c][arm].append(vals[c])

    report: dict[str, object] = {"noise_floor": noise_report, "cases": {}}
    for c in CASES:
        base = samples[c]["upstream"]
        entry: dict[str, object] = {
            "upstream_median_us": round(statistics.median(base), 3),
            "arms": {},
        }
        for arm in arms:
            if arm == "upstream":
                continue
            ratios = [samples[c][arm][i] / base[i] for i in range(len(base))]
            ordered = sorted(ratios)
            # Quartiles survive a single stalled round; min/max do not.  Keep both
            # so a reader can see the tail without having the chart dominated by it.
            entry["arms"][arm] = {
                "ratio_median": round(statistics.median(ratios), 3),
                "ratio_p25": round(ordered[max(0, int(0.25 * (len(ordered) - 1)))], 3),
                "ratio_p75": round(ordered[int(0.75 * (len(ordered) - 1))], 3),
                "ratio_min": round(min(ratios), 3),
                "ratio_max": round(max(ratios), 3),
                "ratio_samples": [round(r, 3) for r in ratios],
                "median_us": round(statistics.median(samples[c][arm]), 3),
            }
        report["cases"][c] = entry

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
    )

    print("noise floor (identical code, ratio A/B):")
    for c, v in noise_report.items():
        print(
            f"  {c:<22} median={v['ratio_median']:.3f} "
            f"iqr=[{v['ratio_p25']:.3f},{v['ratio_p75']:.3f}] "
            f"min/max=[{v['ratio_min']:.3f},{v['ratio_max']:.3f}]"
        )
    print("\nvs upstream:")
    for c, v in report["cases"].items():
        print(f"  {c:<22} upstream={v['upstream_median_us']:.2f}us")
        for arm, s in v["arms"].items():
            print(
                f"    {arm:<10} {s['ratio_median']:.2f}x "
                f"iqr=[{s['ratio_p25']:.2f},{s['ratio_p75']:.2f}] "
                f"min/max=[{s['ratio_min']:.2f},{s['ratio_max']:.2f}]"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
