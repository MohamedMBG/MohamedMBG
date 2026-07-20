#!/usr/bin/env python3
"""Prepare a source photo for monochrome ASCII conversion.

Pipeline:
    remove background -> composite over white -> grayscale -> CLAHE ->
    light denoise -> subtle sharpen -> crop empty margins -> save PNG.

This script is only needed locally when regenerating the portrait. It depends
on the heavier ``requirements-portrait.txt`` packages (pillow, numpy,
opencv-python, rembg) which are intentionally never installed in CI.

Usage:
    python scripts/prep_photo.py source-photo.jpg
    python scripts/prep_photo.py source-photo.jpg --output source-prepped.png
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, UnidentifiedImageError

# rembg is imported lazily inside remove_background() so that -h / argument
# errors do not pay the (slow) model-loading cost.

SUPPORTED_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff"}
MAX_DIMENSION = 1400  # resize only when larger than this on the long edge.


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Prepare a photo for monochrome ASCII conversion."
    )
    parser.add_argument("input", type=Path, help="Path to the source image.")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("source-prepped.png"),
        help="Output PNG path (default: source-prepped.png).",
    )
    return parser.parse_args(argv)


def validate_input(path: Path) -> None:
    """Ensure the input exists and is a supported image type."""
    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {path}")
    if not path.is_file():
        raise ValueError(f"Input path is not a file: {path}")
    if path.suffix.lower() not in SUPPORTED_SUFFIXES:
        raise ValueError(
            f"Unsupported image type '{path.suffix}'. "
            f"Supported: {', '.join(sorted(SUPPORTED_SUFFIXES))}"
        )
    try:
        with Image.open(path) as img:
            img.verify()
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError(f"File is not a readable image: {path} ({exc})") from exc


def remove_background(image: Image.Image) -> Image.Image:
    """Remove the background, returning an RGBA image with transparency."""
    from rembg import remove  # local import: heavy optional dependency.

    rgba = remove(image.convert("RGBA"))
    if not isinstance(rgba, Image.Image):
        rgba = Image.open(rgba) if hasattr(rgba, "read") else Image.fromarray(rgba)
    return rgba.convert("RGBA")


def crop_to_subject(rgba: Image.Image, margin: int = 12) -> Image.Image:
    """Crop away empty margins using the alpha channel bounding box."""
    alpha = rgba.getchannel("A")
    bbox = alpha.getbbox()
    if bbox is None:
        return rgba
    left, top, right, bottom = bbox
    left = max(0, left - margin)
    top = max(0, top - margin)
    right = min(rgba.width, right + margin)
    bottom = min(rgba.height, bottom + margin)
    return rgba.crop((left, top, right, bottom))


def composite_on_white(rgba: Image.Image) -> Image.Image:
    """Flatten an RGBA image over a pure white background (RGB)."""
    white = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
    return Image.alpha_composite(white, rgba).convert("RGB")


def enhance_grayscale(rgb: Image.Image) -> np.ndarray:
    """Grayscale + CLAHE + light denoise + subtle unsharp.

    Keeps highlights and shadows so the subject never collapses into a
    silhouette; the white background stays near-white so it maps to spaces.
    """
    gray = cv2.cvtColor(np.asarray(rgb), cv2.COLOR_RGB2GRAY)

    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    contrasted = clahe.apply(gray)

    denoised = cv2.fastNlMeansDenoising(contrasted, None, h=6,
                                        templateWindowSize=7, searchWindowSize=21)

    blur = cv2.GaussianBlur(denoised, (0, 0), sigmaX=1.2)
    sharp = cv2.addWeighted(denoised, 1.4, blur, -0.4, 0)

    # Protect the white background from being greyed by the sharpen pass.
    sharp = np.where(denoised >= 245, denoised, sharp)
    return np.clip(sharp, 0, 255).astype(np.uint8)


def resize_if_needed(array: np.ndarray, max_dim: int = MAX_DIMENSION) -> np.ndarray:
    """Downscale (preserving aspect ratio) only if the long edge exceeds max_dim."""
    height, width = array.shape[:2]
    long_edge = max(height, width)
    if long_edge <= max_dim:
        return array
    scale = max_dim / long_edge
    new_size = (max(1, int(width * scale)), max(1, int(height * scale)))
    return cv2.resize(array, new_size, interpolation=cv2.INTER_AREA)


def prepare(input_path: Path, output_path: Path) -> Path:
    """Run the full preparation pipeline and write the output PNG."""
    validate_input(input_path)
    print(f"[prep] Loading {input_path} ...")

    with Image.open(input_path) as opened:
        source = opened.convert("RGBA")

    print("[prep] Removing background ...")
    cutout = remove_background(source)

    print("[prep] Cropping to subject ...")
    cropped = crop_to_subject(cutout)

    print("[prep] Compositing over white ...")
    flattened = composite_on_white(cropped)

    print("[prep] Grayscale + contrast + denoise + sharpen ...")
    processed = enhance_grayscale(flattened)
    processed = resize_if_needed(processed)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(processed, mode="L").save(output_path, format="PNG")
    print(f"[prep] Saved -> {output_path} ({processed.shape[1]}x{processed.shape[0]})")
    return output_path


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns a process exit code."""
    args = parse_args(argv)
    try:
        prepare(args.input, args.output)
    except (FileNotFoundError, ValueError) as exc:
        print(f"[prep][error] {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - report anything unexpected, don't hide it.
        print(f"[prep][error] Unexpected failure: {exc}", file=sys.stderr)
        return 2
    print("[prep] Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
