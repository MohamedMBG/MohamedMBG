#!/usr/bin/env python3
"""Render a prepared photo into a self-contained animated ASCII-art SVG.

Each row of the portrait "types" itself from left to right using an SVG clip
rectangle whose width animates once and freezes. A small block cursor follows
the reveal edge and disappears when its row finishes. The animation never
loops; the final frame shows the complete portrait.

Set the environment variable ``STATIC=1`` to emit a fully-visible, non-animated
SVG (useful for local preview and debugging).

Usage:
    python scripts/make_ascii_svg.py
    python scripts/make_ascii_svg.py --input source-prepped.png \
        --output avi-ascii.svg --columns 100
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from xml.sax.saxutils import escape

import numpy as np
from PIL import Image, UnidentifiedImageError

ROOT = Path(__file__).resolve().parent.parent
PROFILE_PATH = ROOT / "data" / "profile.json"
DEFAULT_RAMP = " .`:-=+*cs#%@"


def load_profile(path: Path = PROFILE_PATH) -> dict:
    """Load the shared profile configuration (single source of truth)."""
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"Profile config not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Profile config is not valid JSON: {exc}") from exc


def cfg(profile: dict, *keys: str, default=None):
    """Safely walk nested config keys, returning ``default`` if missing."""
    node = profile
    for key in keys:
        if not isinstance(node, dict) or key not in node:
            return default
        node = node[key]
    return node


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="Generate an animated ASCII portrait SVG.")
    parser.add_argument("--input", type=Path, default=Path("source-prepped.png"))
    parser.add_argument("--output", type=Path, default=Path("avi-ascii.svg"))
    parser.add_argument("--columns", type=int, default=None,
                        help="Character columns (default: from profile.json).")
    return parser.parse_args(argv)


def image_to_ascii(image_path: Path, columns: int, char_aspect: float,
                   ramp: str, max_rows: int) -> list[str]:
    """Convert an image into ASCII rows. Bright pixels map to spaces."""
    if not image_path.exists():
        raise FileNotFoundError(f"Input image not found: {image_path}")
    try:
        with Image.open(image_path) as opened:
            gray = opened.convert("L")
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError(f"Cannot read image {image_path}: {exc}") from exc

    width, height = gray.size
    rows = max(1, int(round(columns * (height / width) * char_aspect)))
    rows = min(rows, max_rows)
    resized = gray.resize((columns, rows), Image.LANCZOS)
    pixels = np.asarray(resized, dtype=np.float32)

    ramp = ramp or DEFAULT_RAMP
    last = len(ramp) - 1
    # 255 (white) -> index 0 (space); 0 (black) -> index last (dense).
    indices = np.clip(((255.0 - pixels) / 255.0 * last).round().astype(int), 0, last)
    return ["".join(ramp[i] for i in row) for row in indices]


def build_svg(rows: list[str], profile: dict, static: bool) -> str:
    """Assemble the complete SVG document as a string."""
    theme = profile.get("theme", {})
    p = profile.get("svg", {}).get("portrait", {})

    width = float(p.get("width", 370))
    pad = float(p.get("padding", 18))
    title_h = float(p.get("title_bar_height", 26))
    line_ratio = float(p.get("line_height", 1.06))
    char_ratio = float(p.get("char_width_ratio", 0.6))

    columns = max((len(r) for r in rows), default=1)
    text_w = width - 2 * pad
    cell_w = text_w / columns
    font_size = cell_w / char_ratio
    cell_h = font_size * line_ratio
    top = title_h + pad
    height = top + len(rows) * cell_h + pad

    bg = theme.get("background", "#0d1117")
    fg = theme.get("foreground", "#c9d1d9")
    border = theme.get("border", "#30363d")
    cursor = theme.get("cursor", "#39d353")
    controls = theme.get("window_controls", ["#ff5f56", "#ffbd2e", "#27c93f"])
    font_stack = theme.get("font_stack", "'Consolas', monospace")
    radius = float(theme.get("corner_radius", 8))

    stagger = float(cfg(profile, "animation", "stagger_seconds", default=0.05))
    row_dur = 0.55

    parts: list[str] = []
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {width:.1f} {height:.1f}" width="{width:.0f}" '
        f'role="img" aria-labelledby="title desc" '
        f'font-family="{escape(font_stack)}">'
    )
    parts.append(f'<title id="title">Animated ASCII portrait of {escape(cfg(profile, "display_name", default="") or "")}</title>')
    parts.append('<desc id="desc">A monochrome terminal-style ASCII portrait that types itself row by row.</desc>')
    parts.append(
        '<style>'
        '@media (prefers-reduced-motion: reduce){.cursor{display:none}}'
        f'.ascii{{fill:{fg};font-size:{font_size:.2f}px;white-space:pre}}'
        '</style>'
    )

    # Terminal frame.
    parts.append(f'<rect x="0.5" y="0.5" width="{width - 1:.1f}" height="{height - 1:.1f}" '
                 f'rx="{radius}" ry="{radius}" fill="{bg}" stroke="{border}"/>')
    parts.append(f'<line x1="0" y1="{title_h:.1f}" x2="{width:.1f}" y2="{title_h:.1f}" stroke="{border}"/>')
    for i, color in enumerate(controls[:3]):
        parts.append(f'<circle cx="{16 + i * 16}" cy="{title_h / 2:.1f}" r="5" fill="{color}"/>')
    parts.append(f'<text x="{width / 2:.1f}" y="{title_h / 2 + 4:.1f}" text-anchor="middle" '
                 f'fill="{theme.get("muted", "#8b949e")}" font-size="11">portrait.txt</text>')

    for i, row in enumerate(rows):
        y = top + (i + 0.85) * cell_h
        safe = escape(row)
        clip_id = f"clip_r{i}"

        if static:
            parts.append(
                f'<text class="ascii" x="{pad:.1f}" y="{y:.2f}" xml:space="preserve" '
                f'textLength="{text_w:.2f}" lengthAdjust="spacing">{safe}</text>'
            )
            continue

        start = i * stagger
        # Per-row wipe: rect grows from width 0 to full, clipping the row text.
        parts.append(
            f'<clipPath id="{clip_id}">'
            f'<rect x="{pad:.1f}" y="{top + i * cell_h:.2f}" width="0" height="{cell_h + 1:.2f}">'
            f'<animate attributeName="width" from="0" to="{text_w:.2f}" '
            f'begin="{start:.3f}s" dur="{row_dur:.3f}s" fill="freeze"/>'
            f'</rect></clipPath>'
        )
        parts.append(
            f'<text class="ascii" x="{pad:.1f}" y="{y:.2f}" xml:space="preserve" '
            f'textLength="{text_w:.2f}" lengthAdjust="spacing" '
            f'clip-path="url(#{clip_id})">{safe}</text>'
        )
        # Cursor: hidden until the row starts, follows the edge, then hides.
        parts.append(
            f'<rect class="cursor" x="{pad:.1f}" y="{top + i * cell_h:.2f}" '
            f'width="{cell_w:.2f}" height="{cell_h:.2f}" fill="{cursor}" opacity="0">'
            f'<set attributeName="opacity" to="0.85" begin="{start:.3f}s"/>'
            f'<animate attributeName="x" from="{pad:.1f}" to="{pad + text_w - cell_w:.2f}" '
            f'begin="{start:.3f}s" dur="{row_dur:.3f}s" fill="freeze"/>'
            f'<set attributeName="opacity" to="0" begin="{start + row_dur:.3f}s"/>'
            f'</rect>'
        )

    parts.append('</svg>')
    return "".join(parts)


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns a process exit code."""
    args = parse_args(argv)
    static = os.environ.get("STATIC", "").strip() not in ("", "0", "false", "False")
    try:
        profile = load_profile()
        columns = args.columns or int(cfg(profile, "ascii", "columns", default=100))
        char_aspect = float(cfg(profile, "ascii", "char_aspect", default=0.5))
        ramp = str(cfg(profile, "ascii", "ramp", default=DEFAULT_RAMP))
        max_rows = int(cfg(profile, "ascii", "max_rows", default=120))

        rows = image_to_ascii(args.input, columns, char_aspect, ramp, max_rows)
        svg = build_svg(rows, profile, static=static)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(svg, encoding="utf-8")
    except (FileNotFoundError, ValueError) as exc:
        print(f"[ascii][error] {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"[ascii][error] Unexpected failure: {exc}", file=sys.stderr)
        return 2
    mode = "static" if static else "animated"
    print(f"[ascii] Wrote {mode} portrait -> {args.output} ({len(rows)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
