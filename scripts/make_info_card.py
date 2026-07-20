#!/usr/bin/env python3
"""Render a neofetch-style profile information card as a self-contained SVG.

Rows fade in and rise slightly, one after another, using CSS keyframes defined
inside the SVG. The animation plays once and freezes on the final frame. All
content comes from ``data/profile.json``.

Set ``STATIC=1`` to emit the completed, non-animated card.

Usage:
    python scripts/make_info_card.py
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import textwrap
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent.parent
PROFILE_PATH = ROOT / "data" / "profile.json"


def load_profile(path: Path = PROFILE_PATH) -> dict:
    """Load the shared profile configuration."""
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"Profile config not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Profile config is not valid JSON: {exc}") from exc


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="Generate a neofetch-style info card SVG.")
    parser.add_argument("--output", type=Path, default=Path("info-card.svg"))
    return parser.parse_args(argv)


def build_rows(profile: dict) -> list[tuple[str, str]]:
    """Build the ordered (key, value) rows from the profile."""
    stack = ", ".join(profile.get("tech_stack", []))
    highlights = ", ".join(profile.get("highlights", []))
    return [
        ("user", profile.get("display_name", "")),
        ("username", profile.get("terminal_username", "")),
        ("role", profile.get("role", "")),
        ("location", profile.get("location", "")),
        ("focus", profile.get("current_focus", "")),
        ("project", profile.get("project_name", "")),
        ("stack", stack),
        ("highlights", highlights),
    ]


def wrap_value(value: str, width: int) -> list[str]:
    """Wrap a value to at most ``width`` characters per line (never empty)."""
    lines = textwrap.wrap(value, width=width, break_long_words=False,
                          break_on_hyphens=False)
    return lines or [""]


def build_svg(profile: dict, static: bool) -> str:
    """Assemble the complete info-card SVG."""
    theme = profile.get("theme", {})
    c = profile.get("svg", {}).get("info_card", {})

    width = float(c.get("width", 490))
    pad = float(c.get("padding", 22))
    title_h = float(c.get("title_bar_height", 34))
    line_h = float(c.get("line_height", 22))
    key_w = float(c.get("key_width", 96))
    font_size = float(c.get("font_size", 13))
    wrap_chars = int(c.get("wrap_chars", 44))

    bg = theme.get("background", "#0d1117")
    fg = theme.get("foreground", "#c9d1d9")
    muted = theme.get("muted", "#8b949e")
    border = theme.get("border", "#30363d")
    accent = theme.get("accent", "#39d353")
    accent2 = theme.get("accent_secondary", "#58a6ff")
    controls = theme.get("window_controls", ["#ff5f56", "#ffbd2e", "#27c93f"])
    font_stack = theme.get("font_stack", "'Consolas', monospace")
    radius = float(theme.get("corner_radius", 8))
    stagger = float(profile.get("animation", {}).get("stagger_seconds", 0.05))

    rows = build_rows(profile)
    prompt = f'{profile.get("terminal_username", "user")}@github ~ $ neofetch'

    # Pre-compute wrapped lines to size the card vertically.
    wrapped = [(key, wrap_value(val, wrap_chars)) for key, val in rows]
    total_lines = sum(len(v) for _, v in wrapped)
    body_top = title_h + pad
    height = body_top + total_lines * line_h + pad

    parts: list[str] = []
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {width:.0f} {height:.1f}" width="{width:.0f}" '
        f'role="img" aria-labelledby="title desc" '
        f'font-family="{escape(font_stack)}" font-size="{font_size:.0f}">'
    )
    parts.append(f'<title id="title">Terminal profile information for {escape(profile.get("display_name", ""))}</title>')
    parts.append('<desc id="desc">A neofetch-style terminal card listing role, location, focus, project and tech stack.</desc>')

    keyframes = (
        '@keyframes fadeUp{from{opacity:0;transform:translateY(6px)}'
        'to{opacity:1;transform:translateY(0)}}'
    )
    if static:
        parts.append(f'<style>.row{{opacity:1}}</style>')
    else:
        parts.append(
            '<style>'
            f'{keyframes}'
            '.row{opacity:0;animation:fadeUp .5s ease-out forwards}'
            '@media (prefers-reduced-motion: reduce){.row{opacity:1;animation:none}}'
            '</style>'
        )

    # Frame + title bar.
    parts.append(f'<rect x="0.5" y="0.5" width="{width - 1:.1f}" height="{height - 1:.1f}" '
                 f'rx="{radius}" ry="{radius}" fill="{bg}" stroke="{border}"/>')
    parts.append(f'<line x1="0" y1="{title_h:.1f}" x2="{width:.1f}" y2="{title_h:.1f}" stroke="{border}"/>')
    for i, color in enumerate(controls[:3]):
        parts.append(f'<circle cx="{16 + i * 16}" cy="{title_h / 2:.1f}" r="5.5" fill="{color}"/>')
    parts.append(f'<text x="{width / 2:.1f}" y="{title_h / 2 + 4:.1f}" text-anchor="middle" '
                 f'fill="{muted}" font-size="11">profile.json</text>')

    # Prompt line (delay 0).
    delay0 = "0s"
    style0 = "" if static else f' style="animation-delay:{delay0}"'
    parts.append(
        f'<g class="row"{style0}>'
        f'<text x="{pad:.1f}" y="{body_top:.1f}" fill="{accent}">{escape(prompt)}</text>'
        f'</g>'
    )

    line_index = 1  # prompt occupies the first visual slot
    for row_i, (key, lines) in enumerate(wrapped):
        delay = (row_i + 1) * stagger
        style = "" if static else f' style="animation-delay:{delay:.3f}s"'
        parts.append(f'<g class="row"{style}>')
        for j, line in enumerate(lines):
            y = body_top + (line_index + 1) * line_h
            if j == 0:
                parts.append(f'<text x="{pad:.1f}" y="{y:.1f}" fill="{accent2}">{escape(key)}</text>')
            parts.append(f'<text x="{pad + key_w:.1f}" y="{y:.1f}" fill="{fg}">{escape(line)}</text>')
            line_index += 1
        parts.append('</g>')

    parts.append('</svg>')
    return "".join(parts)


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns a process exit code."""
    args = parse_args(argv)
    static = os.environ.get("STATIC", "").strip() not in ("", "0", "false", "False")
    try:
        profile = load_profile()
        svg = build_svg(profile, static=static)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(svg, encoding="utf-8")
    except (FileNotFoundError, ValueError) as exc:
        print(f"[card][error] {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"[card][error] Unexpected failure: {exc}", file=sys.stderr)
        return 2
    mode = "static" if static else "animated"
    print(f"[card] Wrote {mode} info card -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
