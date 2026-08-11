#!/usr/bin/env python3
"""Generate a growth-first Avernet daily star PNG from the CSV report."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import os
from pathlib import Path
from typing import Any

os.environ.setdefault("MPLCONFIGDIR", "/private/tmp/avernet-matplotlib")

try:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    from matplotlib.patches import Circle
except ImportError as exc:  # pragma: no cover - environment dependency.
    raise SystemExit("matplotlib is required to generate the Avernet image") from exc


BG = "#F7F5EE"
TEXT = "#17222C"
MUTED = "#6A7885"
GRID = "#D5DCDB"
BLUE = "#275BD8"
GREEN = "#1E927A"
ORANGE = "#F06F2D"

CSV_FIELDS = {
    "date",
    "observed_at",
    "repo",
    "star_total",
    "rd_star_count",
    "non_rd_star_count",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--csv", type=Path, default=Path("reports/avernet_star_daily.csv"))
    parser.add_argument("--output", type=Path, default=Path("reports/avernet_star_growth.png"))
    return parser.parse_args()


def under_root(root: Path, path: Path) -> Path:
    return path if path.is_absolute() else root / path


def load_rows(csv_path: Path) -> list[dict[str, Any]]:
    with csv_path.open(newline="", encoding="utf-8") as source:
        reader = csv.DictReader(source)
        if reader.fieldnames is None or not CSV_FIELDS.issubset(reader.fieldnames):
            raise RuntimeError(f"CSV schema is invalid: {csv_path}")
        rows = []
        for raw in reader:
            total = int(raw["star_total"])
            internal = int(raw["rd_star_count"])
            external = int(raw["non_rd_star_count"])
            if total != internal + external:
                raise RuntimeError(f"CSV row does not balance for {raw['date']}")
            rows.append(
                {
                    "date": raw["date"],
                    "observed_at": dt.datetime.fromisoformat(raw["observed_at"]),
                    "repo": raw["repo"],
                    "star_total": total,
                    "internal": internal,
                    "external": external,
                }
            )
    if not rows:
        raise RuntimeError(f"CSV has no data rows: {csv_path}")
    rows.sort(key=lambda row: row["observed_at"])
    return rows


def configure_matplotlib() -> None:
    plt.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": ["Avenir Next", "Avenir", "DejaVu Sans"],
            "text.color": TEXT,
            "axes.labelcolor": MUTED,
            "xtick.color": MUTED,
            "ytick.color": MUTED,
            "axes.edgecolor": GRID,
        }
    )


def render_dashboard(rows: list[dict[str, Any]], output_path: Path) -> None:
    configure_matplotlib()
    first = rows[0]
    latest = rows[-1]
    total = latest["star_total"]
    internal = latest["internal"]
    external = latest["external"]
    growth = total - first["star_total"]
    internal_share = internal / total if total else 0
    external_share = external / total if total else 0

    figure = plt.figure(figsize=(20.48, 10.92), dpi=100, facecolor=BG)
    figure.text(0.052, 0.935, "Avernet Star Growth", fontsize=36, fontweight=700, color=TEXT)
    figure.text(
        0.052,
        0.886,
        f"{first['star_total']} to {total} stars  |  {first['observed_at']:%b %d} - {latest['observed_at']:%b %d}",
        fontsize=16,
        fontweight=600,
        color=MUTED,
    )
    figure.text(0.052, 0.81, "Total stars", fontsize=18, fontweight=700, color=TEXT)

    trend = figure.add_axes([0.052, 0.13, 0.65, 0.62], facecolor=BG)
    x_values = list(range(len(rows)))
    totals = [row["star_total"] for row in rows]
    labels = [row["observed_at"].strftime("%b %d") for row in rows]

    y_max = max(100, int(math.ceil(max(totals) * 1.12 / 10) * 10))
    trend.set_ylim(0, y_max)
    trend.set_xlim(-0.18, max(len(rows) - 1 + 0.18, 1))
    trend.set_yticks(list(range(0, y_max + 1, 20)))
    trend.set_xticks(x_values, labels)
    trend.grid(axis="y", color=GRID, linewidth=1)
    trend.fill_between(x_values, totals, 0, color="#DDE4F1", alpha=0.72, zorder=1)
    trend.plot(
        x_values,
        totals,
        color=BLUE,
        linewidth=5.5,
        marker="o",
        markersize=11,
        markerfacecolor=BG,
        markeredgewidth=3,
        zorder=4,
    )
    trend.scatter(
        [x_values[-1]],
        [totals[-1]],
        s=210,
        color=BLUE,
        edgecolor=BG,
        linewidth=3,
        zorder=5,
    )
    trend.set_ylabel("Stars", fontsize=14, fontweight=600, labelpad=18)
    trend.tick_params(axis="both", labelsize=13, length=0, pad=10)
    for spine in trend.spines.values():
        spine.set_visible(False)

    for index, value in enumerate(totals):
        is_latest = index == len(totals) - 1
        trend.annotate(
            str(value),
            (index, value),
            xytext=(0, 18 if is_latest else 14),
            textcoords="offset points",
            ha="center",
            color=BLUE,
            fontsize=17 if is_latest else 14,
            fontweight=700,
        )

    figure.add_artist(
        Line2D(
            [0.735, 0.735],
            [0.115, 0.82],
            transform=figure.transFigure,
            color=GRID,
            linewidth=1.2,
        )
    )

    figure.text(0.77, 0.805, "NET GROWTH", fontsize=12, fontweight=700, color=MUTED)
    growth_prefix = "+" if growth >= 0 else ""
    figure.text(0.77, 0.735, f"{growth_prefix}{growth}", fontsize=52, fontweight=700, color=BLUE)
    figure.text(
        0.77,
        0.695,
        f"stars since {first['observed_at']:%b %d}",
        fontsize=15,
        color=MUTED,
    )

    figure.text(0.77, 0.605, "Star source", fontsize=20, fontweight=700, color=TEXT)
    figure.text(0.77, 0.568, "Internal vs External", fontsize=13, color=MUTED)
    source = figure.add_axes([0.77, 0.31, 0.15, 0.25], facecolor=BG)
    source.pie(
        [internal, external],
        colors=[GREEN, ORANGE],
        startangle=90,
        counterclock=False,
        wedgeprops={"width": 0.34, "edgecolor": BG, "linewidth": 2},
    )
    source.text(
        0,
        0.08,
        f"{external_share:.0%}",
        ha="center",
        va="center",
        fontsize=28,
        fontweight=700,
        color=TEXT,
    )
    source.text(0, -0.2, "External", ha="center", va="center", fontsize=12, color=MUTED)
    source.set_axis_off()

    figure.add_artist(
        Circle((0.782, 0.265), 0.005, transform=figure.transFigure, facecolor=GREEN, edgecolor="none")
    )
    figure.text(
        0.797,
        0.257,
        f"Internal   {internal}  |  {internal_share:.1%}",
        color=TEXT,
        fontsize=13,
        fontweight=600,
    )
    figure.add_artist(
        Circle((0.782, 0.22), 0.005, transform=figure.transFigure, facecolor=ORANGE, edgecolor="none")
    )
    figure.text(
        0.797,
        0.212,
        f"External  {external}  |  {external_share:.1%}",
        color=TEXT,
        fontsize=13,
        fontweight=600,
    )

    figure.text(
        0.77,
        0.135,
        "Internal is matched against the team roster.",
        fontsize=11,
        color=MUTED,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output_path, dpi=100, facecolor=BG, edgecolor=BG)
    plt.close(figure)


def main() -> int:
    args = parse_args()
    repo_root = args.repo_root.resolve()
    csv_path = under_root(repo_root, args.csv)
    output_path = under_root(repo_root, args.output)

    rows = load_rows(csv_path)
    render_dashboard(rows, output_path)
    result = {
        "output": str(output_path),
        "width": 2048,
        "height": 1092,
        "rows": len(rows),
        "latest_total": rows[-1]["star_total"],
        "net_growth": rows[-1]["star_total"] - rows[0]["star_total"],
        "internal": rows[-1]["internal"],
        "external": rows[-1]["external"],
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
