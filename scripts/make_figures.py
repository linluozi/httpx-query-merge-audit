"""Render the three figures in this repo from results/*.json.

Warm editorial palette, English labels (CJK glyphs turn into boxes without a
font bundle, and these figures are published on GitHub).

    python scripts/make_figures.py

Figures
-------
fig1_matrix.png    behaviour matrix: 21 cases x 4 arms
fig2_mutation.png  mutation survival: which wrong fixes the suite misses
fig3_bench.png     cost of the merge, with the measured noise floor drawn in
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"
FIGURES = ROOT / "figures"

INK = "#2b2119"
PAPER = "#faf6f0"
AMBER = "#c8892b"
TERRACOTTA = "#b4552f"
OLIVE = "#6b6b3a"
MUTED = "#8a7a68"
OK_GREEN = "#5d7350"
BAD_RED = "#a63d2a"

ARMS = ["upstream", "pr3759", "pr3761", "shipped"]
ARM_LABEL = {
    "upstream": "0.28.1\n(baseline)",
    "pr3759": "PR #3759\n(closed)",
    "pr3761": "PR #3761\n(open)",
    "shipped": "shipped\nZIP",
}


def style() -> None:
    plt.rcParams.update(
        {
            "figure.facecolor": PAPER,
            "axes.facecolor": PAPER,
            "savefig.facecolor": PAPER,
            "font.family": "DejaVu Sans",
            "font.size": 9,
            "text.color": INK,
            "axes.labelcolor": INK,
            "xtick.color": INK,
            "ytick.color": INK,
            "axes.edgecolor": MUTED,
            "axes.linewidth": 0.8,
        }
    )


def fig1_matrix() -> None:
    data = json.loads((RESULTS / "behavior_matrix.json").read_text(encoding="utf-8"))
    cases = data["cases"]
    ids = [c["id"] for c in cases]

    height = 0.30 * len(ids) + 2.0
    fig, ax = plt.subplots(figsize=(9.2, height))

    for row, case in enumerate(cases):
        y = len(ids) - row - 1
        for col, arm in enumerate(ARMS):
            ok = case["results"][arm]["ok"]
            ax.add_patch(
                Rectangle(
                    (col, y - 0.42),
                    1,
                    0.84,
                    facecolor=OK_GREEN if ok else BAD_RED,
                    edgecolor=PAPER,
                    linewidth=2,
                    alpha=0.92 if ok else 0.88,
                )
            )
            ax.text(
                col + 0.5,
                y,
                "PASS" if ok else "fail",
                ha="center",
                va="center",
                color=PAPER,
                fontsize=7.6,
                fontweight="bold",
            )

    ax.set_xlim(0, len(ARMS))
    ax.set_ylim(-0.6, len(ids) - 0.1)
    ax.set_xticks([i + 0.5 for i in range(len(ARMS))])
    ax.set_xticklabels([ARM_LABEL[a] for a in ARMS], fontsize=8.2)
    ax.xaxis.set_ticks_position("top")
    ax.tick_params(axis="x", length=0, pad=6)
    ax.set_yticks([len(ids) - i - 1 for i in range(len(ids))])
    ax.set_yticklabels(ids, fontsize=7.8)
    ax.set_xticks([], minor=True)
    ax.tick_params(axis="y", length=0, pad=6)
    for spine in ax.spines.values():
        spine.set_visible(False)

    # Group the byte-fidelity cases visually; they are the whole punchline.
    first_bytes = next(i for i, c in enumerate(ids) if c.startswith("bytes_pct2F"))
    y_line = len(ids) - first_bytes - 0.5
    ax.axhline(y_line, color=AMBER, lw=1.2, ls=(0, (4, 2)))

    ax.set_title(
        "Behaviour matrix: 21 cases x 4 arms\n"
        "every merge-based fix repairs the dropped keys, none preserves query byte-for-byte\n"
        "--- dashed line: byte-fidelity cases start below it ---",
        fontsize=10.2,
        pad=30,
        loc="left",
        color=INK,
    )
    fig.text(
        0.012,
        0.012,
        "Source: results/behavior_matrix.json  |  each arm run in its own process",
        fontsize=6.8,
        color=MUTED,
    )
    fig.tight_layout(rect=(0.012, 0.02, 1, 1))
    fig.savefig(FIGURES / "fig1_matrix.png", dpi=170)
    plt.close(fig)


def fig2_mutation() -> None:
    mut = json.loads((RESULTS / "mutation.json").read_text(encoding="utf-8"))
    eq = json.loads((RESULTS / "mutation_equivalence.json").read_text(encoding="utf-8"))
    verdicts = {r["mutant"]: r for r in eq["rows"]}

    rows = mut["rows"]
    labels = [r["mutant"].split("_", 1)[1].replace("_", " ") for r in rows]

    fig, (ax1, ax2) = plt.subplots(
        1, 2, figsize=(11.2, 4.4), gridspec_kw={"width_ratios": [1.35, 1]}
    )

    y = list(range(len(rows)))[::-1]
    for yi, row in zip(y, rows):
        caught = row["detected"]
        color = AMBER if caught else TERRACOTTA
        ax1.barh(yi, row["failed"], color=color, height=0.62, edgecolor="none")
        ax1.text(
            row["failed"] + 0.12,
            yi,
            f"{row['failed']} failed / {row['passed']} passed",
            va="center",
            fontsize=7.6,
            color=INK,
        )
    ax1.set_yticks(y)
    ax1.set_yticklabels(labels, fontsize=8.0)
    ax1.set_xlabel("tests that turned red", fontsize=8.4)
    ax1.set_xlim(0, 11.4)
    ax1.tick_params(axis="y", length=0)
    for spine in ("top", "right"):
        ax1.spines[spine].set_visible(False)
    ax1.set_title(
        "Does the shipped 9-test suite notice a wrong fix?",
        fontsize=9.6,
        loc="left",
        color=INK,
        pad=22,
    )
    ax1.text(
        0.0,
        1.015,
        "amber = suite catches it    terracotta = suite stays green",
        transform=ax1.transAxes,
        fontsize=7.2,
        color=MUTED,
    )

    survivors = [r["mutant"] for r in rows if not r["detected"] and r["mutant"] != "M0_unchanged"]
    names = [s.split("_", 1)[1].replace("_", " ") for s in survivors]

    # Right panel: a bar chart of "how many cases differ" would draw three
    # identical bars.  What a reader needs is *which* case changes, so show the
    # concrete before/after instead.
    ax2.axis("off")
    ax2.set_title(
        "Are those survivors harmless?\nNo -- each one changes a real output",
        fontsize=9.6,
        loc="left",
        color=INK,
        pad=22,
    )

    y_cursor = 0.95
    for name, mutant in zip(names, survivors):
        info = verdicts[mutant]
        diff = info["diffs"][0]
        ax2.text(0.0, y_cursor, name, fontsize=8.4, color=INK, fontweight="bold",
                 va="top", transform=ax2.transAxes)
        ax2.text(0.0, y_cursor - 0.062, f"case: {diff['case']}", fontsize=7.2,
                 color=MUTED, va="top", transform=ax2.transAxes)
        ax2.text(0.02, y_cursor - 0.122, "shipped", fontsize=6.6, color=MUTED,
                 va="top", transform=ax2.transAxes)
        ax2.text(0.155, y_cursor - 0.122, diff["shipped"], fontsize=6.6, color=OK_GREEN,
                 va="top", transform=ax2.transAxes, family="DejaVu Sans Mono")
        ax2.text(0.02, y_cursor - 0.174, "mutant", fontsize=6.6, color=MUTED,
                 va="top", transform=ax2.transAxes)
        ax2.text(0.155, y_cursor - 0.174, diff["mutant"], fontsize=6.6, color=BAD_RED,
                 va="top", transform=ax2.transAxes, family="DejaVu Sans Mono")
        y_cursor -= 0.285

    ax2.text(
        0.0,
        -0.03,
        "The suite is not merely conservative:\nit misses changes a caller would notice.",
        fontsize=7.2,
        color=AMBER,
        style="italic",
        va="top",
        transform=ax2.transAxes,
    )

    fig.text(
        0.012,
        0.015,
        "Source: results/mutation.json, results/mutation_equivalence.json  |  "
        "control mutant M0 is excluded from the right panel",
        fontsize=6.8,
        color=MUTED,
    )
    fig.tight_layout(rect=(0.012, 0.04, 1, 0.97))
    fig.savefig(FIGURES / "fig2_mutation.png", dpi=170)
    plt.close(fig)


def fig3_bench() -> None:
    data = json.loads((RESULTS / "bench.json").read_text(encoding="utf-8"))
    cases = list(data["cases"].keys())
    arms = ["pr3759", "pr3761", "shipped"]
    colors = {"pr3759": AMBER, "pr3761": OLIVE, "shipped": TERRACOTTA}
    markers = {"pr3759": "o", "pr3761": "s", "shipped": "^"}
    short = {
        "request_with_query": "URL has a query\n+ params=",
        "request_plain": "no query\n+ params=",
        "request_no_params": "URL has a query\nno params=",
    }

    fig, ax = plt.subplots(figsize=(9.6, 5.0))
    xs = list(range(len(cases)))
    offsets = {"pr3759": -0.20, "pr3761": 0.0, "shipped": 0.20}

    # Noise floor drawn first, behind everything, as a narrow band per group.
    noise = data["noise_floor"]
    for x, c in zip(xs, cases):
        lo, hi = noise[c]["ratio_p25"], noise[c]["ratio_p75"]
        med = noise[c]["ratio_median"]
        ax.add_patch(
            Rectangle(
                (x - 0.5, lo),
                1.0,
                hi - lo,
                facecolor=MUTED,
                alpha=0.14,
                edgecolor="none",
                zorder=0,
            )
        )
        ax.plot([x - 0.5, x + 0.5], [med, med], color=MUTED, lw=1.0, ls=(0, (3, 3)), zorder=1)
        ax.text(
            x,
            lo - 0.16,
            f"noise IQR\n{lo:.2f}-{hi:.2f}x",
            ha="center",
            va="top",
            fontsize=6.4,
            color=MUTED,
            linespacing=1.15,
        )

    for arm in arms:
        block = [data["cases"][c]["arms"][arm] for c in cases]
        meds = [b["ratio_median"] for b in block]
        lows = [b["ratio_p25"] for b in block]
        highs = [b["ratio_p75"] for b in block]
        tails_lo = [b["ratio_min"] for b in block]
        tails_hi = [b["ratio_max"] for b in block]
        px = [x + offsets[arm] for x in xs]
        # Thin whisker for the full spread, thick bar for the IQR: a single stalled
        # round shows up as a long hairline instead of stretching the whole box.
        ax.vlines(px, tails_lo, tails_hi, color=colors[arm], lw=0.7, alpha=0.35, zorder=2)
        ax.vlines(px, lows, highs, color=colors[arm], lw=3.0, alpha=0.85, zorder=3)
        ax.plot(
            px,
            meds,
            linestyle="none",
            marker=markers[arm],
            markersize=7.5,
            color=PAPER,
            markeredgecolor=colors[arm],
            markeredgewidth=1.4,
            label=f"{ARM_LABEL[arm].replace(chr(10), ' ')}",
            zorder=4,
        )
        for x, m, h in zip(px, meds, highs):
            ax.text(x, h + 0.06, f"{m:.2f}x", ha="center", va="bottom", fontsize=7.0,
                    color=colors[arm], zorder=5)

    ax.plot([], [], color=MUTED, lw=1.0, ls=(0, (3, 3)), label="noise floor (identical code)")
    ax.axhline(1.0, color=INK, lw=0.9, alpha=0.55, zorder=1, label="no change (1.0x)")

    ax.set_xticks(xs)
    ax.set_xticklabels([short[c] for c in cases], fontsize=8.4)
    ax.set_ylabel("median time vs 0.28.1\n(x slower)", fontsize=8.8, labelpad=10)
    ax.set_xlim(-0.55, len(cases) - 0.45)
    ax.set_ylim(0.0, 3.15)
    ax.tick_params(axis="x", length=0, pad=8)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    ax.grid(axis="y", color=MUTED, alpha=0.15, lw=0.7)
    ax.set_axisbelow(True)
    ax.legend(
        loc="upper center",
        bbox_to_anchor=(0.5, -0.20),
        ncol=5,
        frameon=False,
        fontsize=7.8,
    )
    ax.set_title(
        "Cost of the merge: thick bar = interquartile range, hairline = full spread, "
        "ring = median\n"
        "only the first two groups clear the noise floor",
        fontsize=9.4,
        loc="left",
        color=INK,
        pad=12,
    )
    fig.text(
        0.012,
        0.015,
        "Source: results/bench.json  |  noise floor measured on two byte-identical copies of 0.28.1  |  "
        "Py3.14.5 / Windows",
        fontsize=6.6,
        color=MUTED,
    )
    fig.tight_layout(rect=(0.012, 0.04, 1, 0.97))
    fig.savefig(FIGURES / "fig3_bench.png", dpi=170)
    plt.close(fig)


def main() -> int:
    FIGURES.mkdir(parents=True, exist_ok=True)
    style()
    fig1_matrix()
    fig2_mutation()
    fig3_bench()
    for name in ("fig1_matrix.png", "fig2_mutation.png", "fig3_bench.png"):
        path = FIGURES / name
        print(f"{path.name:<22} {path.stat().st_size:>8} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
