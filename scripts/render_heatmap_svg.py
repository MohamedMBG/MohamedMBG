#!/usr/bin/env python3
"""Render ``data/contributions.json`` into a self-contained animated heatmap SVG.

Produces a classic GitHub-style contribution calendar (up to 53 weeks x 7 days)
with month/weekday labels, a Less->More legend, a total summary and a compact
stats footer. Cells reveal diagonally from the top-left using CSS keyframes
defined inside the SVG; the animation plays once and freezes.

Set ``STATIC=1`` to emit all cells immediately (no animation).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent.parent
PROFILE_PATH = ROOT / "data" / "profile.json"
CONTRIB_PATH = ROOT / "data" / "contributions.json"

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
WEEKDAY_LABELS = {1: "Mon", 3: "Wed", 5: "Fri"}


def load_json(path: Path) -> dict:
    """Load a JSON file, raising a clear error on failure."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"File not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} is not valid JSON: {exc}") from exc


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="Render the contribution heatmap SVG.")
    parser.add_argument("--input", type=Path, default=CONTRIB_PATH)
    parser.add_argument("--output", type=Path, default=Path("contrib-heatmap.svg"))
    return parser.parse_args(argv)


def plural(count: int, noun: str) -> str:
    """Return '<count> noun(s)' with correct singular/plural wording."""
    return f"{count:,} {noun}" if count == 1 else f"{count:,} {noun}s"


def ordinal(date: dt.date) -> str:
    """Human-friendly 'Month Dayth, Year' for accessible cell titles."""
    day = date.day
    suffix = "th" if 11 <= day % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return f"{MONTHS[date.month - 1]} {day}{suffix}, {date.year}"


def build_svg(contrib: dict, profile: dict, static: bool) -> str:
    """Assemble the complete heatmap SVG document."""
    theme = profile.get("theme", {})
    h = profile.get("svg", {}).get("heatmap", {})
    palette = profile.get("contribution_palette",
                          ["#161b22", "#0e4429", "#006d32", "#26a641", "#39d353", "#69f0a0"])

    width = float(h.get("width", 860))
    size = float(h.get("cell_size", 12))
    gap = float(h.get("cell_gap", 3))
    radius = float(h.get("corner_radius", 2.5))
    pad = float(h.get("padding", 20))
    top_offset = float(h.get("top_offset", 40))
    left_offset = float(h.get("left_offset", 30))
    step = size + gap

    bg = theme.get("background", "#0d1117")
    fg = theme.get("foreground", "#c9d1d9")
    muted = theme.get("muted", "#8b949e")
    border = theme.get("border", "#30363d")
    accent = theme.get("accent", "#39d353")
    font_stack = theme.get("font_stack", "'Consolas', monospace")
    corner = float(theme.get("corner_radius", 8))
    stagger = float(profile.get("animation", {}).get("cell_stagger_seconds", 0.012))

    days = contrib.get("days", [])
    summary = contrib.get("summary", {})
    by_cell = {(d["week"], d["weekday"]): d for d in days}
    weeks = max((d["week"] for d in days), default=52) + 1
    weeks = min(weeks, 53)

    grid_x = left_offset + pad
    grid_top = top_offset
    footer_top = grid_top + 7 * step + 16
    height = footer_top + 54

    parts: list[str] = []
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {width:.0f} {height:.0f}" width="{width:.0f}" '
        f'role="img" aria-labelledby="title desc" '
        f'font-family="{escape(font_stack)}">'
    )
    total = summary.get("total_contributions", 0)
    parts.append(f'<title id="title">GitHub contribution heatmap for {escape(contrib.get("username", ""))}</title>')
    parts.append(f'<desc id="desc">{escape(plural(total, "contribution"))} in the last year, '
                 f'shown as a calendar of daily activity.</desc>')

    if static:
        parts.append('<style>.cell{opacity:1}</style>')
    else:
        parts.append(
            '<style>'
            '@keyframes pop{from{opacity:0;transform:translateY(-4px)}'
            'to{opacity:1;transform:translateY(0)}}'
            '.cell{opacity:0;animation:pop .45s ease-out forwards}'
            '@media (prefers-reduced-motion: reduce){.cell{opacity:1;animation:none}}'
            '</style>'
        )

    # Frame.
    parts.append(f'<rect x="0.5" y="0.5" width="{width - 1:.1f}" height="{height - 1:.1f}" '
                 f'rx="{corner}" ry="{corner}" fill="{bg}" stroke="{border}"/>')

    # Summary heading.
    parts.append(f'<text x="{grid_x:.1f}" y="24" fill="{fg}" font-size="14">'
                 f'{escape(plural(total, "contribution"))} in the last year</text>')

    # Month labels (non-overlapping).
    last_month = -1
    last_label_x = -999.0
    for week in range(weeks):
        first = min((by_cell[(week, wd)] for wd in range(7) if (week, wd) in by_cell),
                    key=lambda d: d["date"], default=None)
        if not first:
            continue
        month = dt.date.fromisoformat(first["date"]).month
        x = grid_x + week * step
        if month != last_month and (x - last_label_x) > step * 2.5:
            parts.append(f'<text x="{x:.1f}" y="{grid_top - 6:.1f}" fill="{muted}" '
                         f'font-size="10">{MONTHS[month - 1]}</text>')
            last_month = month
            last_label_x = x

    # Weekday labels.
    for wd, label in WEEKDAY_LABELS.items():
        y = grid_top + wd * step + size - 2
        parts.append(f'<text x="{grid_x - 8:.1f}" y="{y:.1f}" fill="{muted}" '
                     f'font-size="9" text-anchor="end">{label}</text>')

    # Cells.
    for week in range(weeks):
        for wd in range(7):
            day = by_cell.get((week, wd))
            x = grid_x + week * step
            y = grid_top + wd * step
            level = day["level"] if day else 0
            level = level if 0 <= level < len(palette) else min(level, len(palette) - 1)
            color = palette[level]
            style = "" if static else f' style="animation-delay:{(week + wd) * stagger:.3f}s"'
            if day:
                date = dt.date.fromisoformat(day["date"])
                label = f'{plural(day["count"], "contribution")} on {ordinal(date)}'
                parts.append(
                    f'<rect class="cell" x="{x:.1f}" y="{y:.1f}" width="{size:.1f}" '
                    f'height="{size:.1f}" rx="{radius}" ry="{radius}" fill="{color}"'
                    f' data-date="{day["date"]}" data-count="{day["count"]}"{style}>'
                    f'<title>{escape(label)}</title></rect>'
                )
            else:
                parts.append(
                    f'<rect class="cell" x="{x:.1f}" y="{y:.1f}" width="{size:.1f}" '
                    f'height="{size:.1f}" rx="{radius}" ry="{radius}" fill="{color}"{style}/>'
                )

    # Legend (Less -> More).
    legend_y = footer_top
    legend_x = grid_x
    parts.append(f'<text x="{legend_x:.1f}" y="{legend_y + size - 2:.1f}" fill="{muted}" '
                 f'font-size="10">Less</text>')
    lx = legend_x + 30
    for level, color in enumerate(palette):
        parts.append(f'<rect x="{lx + level * (size + 3):.1f}" y="{legend_y:.1f}" '
                     f'width="{size:.1f}" height="{size:.1f}" rx="{radius}" fill="{color}"/>')
    lx_end = lx + len(palette) * (size + 3) + 4
    parts.append(f'<text x="{lx_end:.1f}" y="{legend_y + size - 2:.1f}" fill="{muted}" '
                 f'font-size="10">More</text>')

    # Stats footer.
    best = summary.get("best_day", {}) or {}
    best_count = best.get("count", 0)
    best_txt = plural(best_count, "contribution") if best.get("date") else "—"
    stats = (
        f'Current streak: {plural(summary.get("current_streak", 0), "day")}   '
        f'Longest streak: {plural(summary.get("longest_streak", 0), "day")}   '
        f'Best day: {best_txt}   '
        f'Active days: {summary.get("active_days", 0):,}'
    )
    parts.append(f'<text x="{grid_x:.1f}" y="{legend_y + 30:.1f}" fill="{fg}" '
                 f'font-size="11">{escape(stats)}</text>')
    parts.append(f'<line x1="{grid_x:.1f}" y1="{legend_y - 8:.1f}" '
                 f'x2="{width - pad:.1f}" y2="{legend_y - 8:.1f}" stroke="{border}"/>')

    parts.append('</svg>')
    return "".join(parts)


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns a process exit code."""
    args = parse_args(argv)
    static = os.environ.get("STATIC", "").strip() not in ("", "0", "false", "False")
    try:
        profile = load_json(PROFILE_PATH)
        try:
            contrib = load_json(args.input)
        except FileNotFoundError:
            # Render gracefully even when contributions.json doesn't exist yet.
            print("[heatmap][warn] contributions.json missing; rendering empty calendar.")
            contrib = {"username": profile.get("github_username", ""), "summary": {}, "days": []}
        svg = build_svg(contrib, profile, static=static)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(svg, encoding="utf-8")
    except (FileNotFoundError, ValueError) as exc:
        print(f"[heatmap][error] {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"[heatmap][error] Unexpected failure: {exc}", file=sys.stderr)
        return 2
    mode = "static" if static else "animated"
    print(f"[heatmap] Wrote {mode} heatmap -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
