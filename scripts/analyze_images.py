#!/usr/bin/env python3
"""Extract deterministic visual evidence from one image or an image collection."""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

from kunpeng_common import (
    IMAGE_EXTENSIONS,
    aggregate_status,
    atomic_write_json,
    bounded_error,
    configure_utf8,
    find_sources,
    prepare_output,
    quantile,
    relative_artifact,
    reused_analysis_status,
    sampled_fingerprint,
    source_id,
    status_counts,
    utc_now,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Analyze images locally with OpenCV, Pillow, and optional PaddleOCR."
    )
    parser.add_argument("source", type=Path, help="Image file or directory.")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--no-recursive", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--ocr", choices=("auto", "on", "off"), default="auto")
    parser.add_argument("--ocr-lang", default="ch")
    parser.add_argument("--ocr-device", default="auto")
    parser.add_argument("--max-images", type=int, default=1000)
    parser.add_argument("--contact-sheet-limit", type=int, default=64)
    return parser.parse_args()


def load_image(path: Path) -> tuple[Any, dict[str, Any]]:
    import numpy as np
    from PIL import Image, ImageOps

    with Image.open(path) as opened:
        frame_count = int(getattr(opened, "n_frames", 1))
        image = ImageOps.exif_transpose(opened.copy())
        has_alpha = "A" in image.getbands()
        rgb = image.convert("RGB")
        array = np.asarray(rgb)
        metadata = {
            "width": rgb.width,
            "height": rgb.height,
            "mode": opened.mode,
            "format": opened.format or path.suffix.lstrip(".").upper(),
            "has_alpha": has_alpha,
            "frame_count": frame_count,
        }
    return array, metadata


def dominant_palette(rgb: Any, colors: int = 6) -> list[dict[str, Any]]:
    from PIL import Image

    height, width = rgb.shape[:2]
    scale = min(1.0, 320.0 / max(height, width))
    image = Image.fromarray(rgb)
    if scale < 1.0:
        image = image.resize(
            (max(1, round(width * scale)), max(1, round(height * scale))),
            Image.Resampling.LANCZOS,
        )
    cluster_count = max(1, min(colors, image.width * image.height))
    quantized = image.quantize(colors=cluster_count, method=Image.Quantize.MEDIANCUT)
    color_counts = sorted(quantized.getcolors(maxcolors=cluster_count) or [], reverse=True)
    palette_values = quantized.getpalette() or []
    total = max(1, sum(count for count, _ in color_counts))
    palette: list[dict[str, Any]] = []
    for count, index in color_counts:
        start = int(index) * 3
        red, green, blue = palette_values[start : start + 3]
        palette.append(
            {
                "hex": f"#{red:02X}{green:02X}{blue:02X}",
                "rgb": [red, green, blue],
                "share": round(float(count / total), 4),
            }
        )
    return palette


def composition_metrics(rgb: Any) -> dict[str, Any]:
    import numpy as np

    rgb_float = rgb.astype(np.float32)
    gray = (
        0.299 * rgb_float[:, :, 0]
        + 0.587 * rgb_float[:, :, 1]
        + 0.114 * rgb_float[:, :, 2]
    )
    maximum = np.max(rgb_float, axis=2)
    minimum = np.min(rgb_float, axis=2)
    saturation = np.divide(
        maximum - minimum,
        maximum,
        out=np.zeros_like(maximum),
        where=maximum > 0,
    )
    method = "opencv"
    try:
        import cv2

        gray_uint8 = np.clip(gray, 0, 255).astype(np.uint8)
        median = float(np.median(gray_uint8))
        lower = int(max(0, 0.66 * median))
        upper = int(min(255, max(lower + 1, 1.33 * median)))
        edges = cv2.Canny(gray_uint8, lower, upper).astype(np.float64)
        blurred = cv2.GaussianBlur(gray_uint8, (0, 0), 7)
        attention = cv2.absdiff(gray_uint8, blurred).astype(np.float64) + edges
        sharpness = float(cv2.Laplacian(gray_uint8, cv2.CV_64F).var())
    except ImportError:
        method = "numpy_gradient_fallback"
        gradient_x = np.zeros_like(gray)
        gradient_y = np.zeros_like(gray)
        gradient_x[:, 1:] = np.abs(np.diff(gray, axis=1))
        gradient_y[1:, :] = np.abs(np.diff(gray, axis=0))
        gradient = np.hypot(gradient_x, gradient_y)
        threshold = max(12.0, float(np.percentile(gradient, 80)))
        edges = (gradient >= threshold).astype(np.float64) * 255.0
        attention = gradient + edges
        laplacian = np.zeros_like(gray)
        if min(gray.shape) >= 3:
            laplacian[1:-1, 1:-1] = (
                -4 * gray[1:-1, 1:-1]
                + gray[:-2, 1:-1] + gray[2:, 1:-1]
                + gray[1:-1, :-2] + gray[1:-1, 2:]
            )
        sharpness = float(np.var(laplacian))
    total_attention = float(attention.sum())
    height, width = gray.shape
    if total_attention:
        y_grid, x_grid = np.indices(gray.shape)
        center_x = float((attention * x_grid).sum() / total_attention / max(1, width - 1))
        center_y = float((attention * y_grid).sum() / total_attention / max(1, height - 1))
    else:
        center_x = center_y = 0.5

    horizontal_similarity = 1.0 - float(
        np.mean(np.abs(gray.astype(np.float32) - np.fliplr(gray).astype(np.float32))) / 255.0
    )
    vertical_similarity = 1.0 - float(
        np.mean(np.abs(gray.astype(np.float32) - np.flipud(gray).astype(np.float32))) / 255.0
    )
    white_mask = (gray >= 242) & (saturation <= 0.10)
    warm_balance = float(
        np.mean(rgb[:, :, 0].astype(np.float32) - rgb[:, :, 2].astype(np.float32)) / 255.0
    )
    luminance_values = gray.reshape(-1).astype(float)
    return {
        "method": method,
        "brightness_mean": round(float(np.mean(gray)) / 255.0, 4),
        "luminance_p10": round(quantile(luminance_values, 0.10) / 255.0, 4),
        "luminance_p90": round(quantile(luminance_values, 0.90) / 255.0, 4),
        "contrast_std": round(float(np.std(gray)) / 255.0, 4),
        "saturation_mean": round(float(np.mean(saturation)), 4),
        "warm_cool_balance": round(warm_balance, 4),
        "edge_density": round(float(np.count_nonzero(edges)) / edges.size, 4),
        "sharpness_laplacian": round(sharpness, 2),
        "attention_centroid": {"x": round(center_x, 4), "y": round(center_y, 4)},
        "horizontal_symmetry": round(max(0.0, horizontal_similarity), 4),
        "vertical_symmetry": round(max(0.0, vertical_similarity), 4),
        "light_neutral_area": round(float(np.mean(white_mask)), 4),
    }


def polygon_area(points: list[list[float]]) -> float:
    if len(points) < 3:
        return 0.0
    return abs(
        sum(
            points[index][0] * points[(index + 1) % len(points)][1]
            - points[(index + 1) % len(points)][0] * points[index][1]
            for index in range(len(points))
        )
    ) / 2.0


def make_contact_sheet(entries: list[tuple[Path, str]], output: Path) -> None:
    from PIL import Image, ImageDraw, ImageOps

    if not entries:
        return
    cell_width, cell_height, label_height = 240, 180, 24
    columns = min(4, max(1, math.ceil(math.sqrt(len(entries)))))
    rows = math.ceil(len(entries) / columns)
    sheet = Image.new("RGB", (columns * cell_width, rows * (cell_height + label_height)), "white")
    draw = ImageDraw.Draw(sheet)
    for index, (path, label) in enumerate(entries):
        row, column = divmod(index, columns)
        x, y = column * cell_width, row * (cell_height + label_height)
        with Image.open(path) as opened:
            image = ImageOps.exif_transpose(opened.copy()).convert("RGB")
            fitted = ImageOps.contain(image, (cell_width, cell_height))
        offset = (x + (cell_width - fitted.width) // 2, y + (cell_height - fitted.height) // 2)
        sheet.paste(fitted, offset)
        draw.text((x + 6, y + cell_height + 4), label[:34], fill="black")
    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output, quality=88, optimize=True)


def source_label(path: Path, root: Path) -> str:
    if root.is_dir():
        return path.relative_to(root.resolve()).as_posix()
    return path.name


def main() -> int:
    configure_utf8()
    args = parse_args()
    sources = find_sources(args.source, IMAGE_EXTENSIONS, not args.no_recursive)
    if not sources:
        raise SystemExit("No supported images found.")
    if len(sources) > max(1, args.max_images):
        raise SystemExit(f"Found {len(sources)} images; raise --max-images to process them all.")
    output = prepare_output(args.output, args.resume)

    ocr_engine = None
    ocr_error = None
    if args.ocr != "off":
        try:
            from local_ocr import LocalOCR

            ocr_engine = LocalOCR(args.ocr_lang, args.ocr_device)
        except Exception as exc:  # optional dependency/model initialization
            ocr_error = bounded_error(exc)

    root = args.source.resolve()
    manifest_items: list[dict[str, Any]] = []
    contact_entries: list[tuple[Path, str]] = []
    for index, path in enumerate(sources, start=1):
        item_id = source_id(path)
        item_dir = output / "images" / item_id
        analysis_path = item_dir / "analysis.json"
        label = source_label(path, root)
        if args.resume and analysis_path.exists():
            reused_status = reused_analysis_status(analysis_path)
            manifest_items.append(
                {
                    "id": item_id,
                    "source": label,
                    "status": reused_status,
                    "extraction_status": reused_status,
                    "distillation_status": "evidence_ready",
                    "reused": True,
                    "analysis": relative_artifact(analysis_path, output),
                }
            )
            contact_entries.append((path, f"{index:03d} {path.name}"))
            continue

        try:
            rgb, metadata = load_image(path)
            height, width = rgb.shape[:2]
            palette = dominant_palette(rgb)
            visual_metrics = composition_metrics(rgb)
            ocr_lines: list[dict[str, Any]] = []
            stages: dict[str, dict[str, Any]] = {
                "metrics": {
                    "status": "complete" if visual_metrics["method"] == "opencv" else "degraded",
                    "method": visual_metrics["method"],
                    "fallback": "numpy_gradient_fallback" if visual_metrics["method"] != "opencv" else None,
                },
            }
            if ocr_engine:
                try:
                    ocr_lines = ocr_engine.recognize(path)
                    stages["ocr"] = {"status": "complete"}
                except Exception as exc:
                    item_ocr_error = bounded_error(exc, path, output)
                    ocr_error = ocr_error or item_ocr_error
                    stages["ocr"] = {
                        "status": "partial",
                        "error": item_ocr_error,
                        "fallback": "host_visual_review",
                        "fallback_ready": True,
                        "host_review_required": True,
                    }
            elif args.ocr != "off":
                stages["ocr"] = {
                    "status": "partial",
                    "error": ocr_error,
                    "fallback": "host_visual_review",
                    "fallback_ready": True,
                    "host_review_required": True,
                }
            else:
                stages["ocr"] = {
                    "status": "not_applicable",
                    "reason": "disabled_by_user",
                }

            status = aggregate_status(stage["status"] for stage in stages.values())
            host_review_required = [
                name
                for name, stage in stages.items()
                if stage.get("host_review_required")
            ]

            text_area = sum(polygon_area(line.get("box", [])) for line in ocr_lines)
            analysis = {
                "schema_version": 2,
                "id": item_id,
                "status": status,
                "status_scope": "deterministic_visual_evidence_only",
                "extraction_status": status,
                "distillation_status": "evidence_ready",
                "source": {"name": label, "fingerprint": sampled_fingerprint(path)},
                "image": {
                    **metadata,
                    "aspect_ratio": round(width / max(1, height), 5),
                    "orientation": "landscape" if width > height else "portrait" if height > width else "square",
                },
                "palette": palette,
                "visual_metrics": visual_metrics,
                "ocr": {
                    "line_count": len(ocr_lines),
                    "text_coverage_estimate": round(min(1.0, text_area / max(1, width * height)), 4),
                    "lines": ocr_lines,
                },
                "stages": stages,
                "host_review_required": host_review_required,
                "semantic_review_required": [
                    "open the original image rather than relying on palette and edge statistics",
                    "identify subject, hierarchy, composition, lighting, material, typography, intent, variables, and exceptions",
                    "attach image-region or OCR evidence to every proposed transferable rule",
                ],
                "limitations": [
                    "Deterministic metrics do not identify subjects, intent, or hidden interactions.",
                    *(
                        [f"OCR unavailable or failed: {stages['ocr'].get('error')}"]
                        if stages["ocr"]["status"] == "partial"
                        else []
                    ),
                ],
            }
            atomic_write_json(analysis_path, analysis)
            manifest_items.append(
                {
                    "id": item_id,
                    "source": label,
                    "status": status,
                    "extraction_status": status,
                    "distillation_status": "evidence_ready",
                    "analysis": relative_artifact(analysis_path, output),
                    "host_review_required": host_review_required,
                }
            )
            contact_entries.append((path, f"{index:03d} {path.name}"))
        except Exception as exc:
            manifest_items.append(
                {
                    "id": item_id,
                    "source": label,
                    "status": "failed",
                    "error": bounded_error(exc, path, output),
                }
            )

    contact_path = output / "contact-sheet.jpg"
    try:
        make_contact_sheet(contact_entries[: max(0, args.contact_sheet_limit)], contact_path)
        contact_artifact = relative_artifact(contact_path, output) if contact_path.exists() else None
    except Exception as exc:
        contact_artifact = None
        ocr_error = ocr_error or bounded_error(exc, output=output)

    counts = status_counts(manifest_items)
    analyses: list[dict[str, Any]] = []
    for item in manifest_items:
        if item.get("status") == "failed" or not item.get("analysis"):
            continue
        try:
            analyses.append(json.loads((output / item["analysis"]).read_text(encoding="utf-8")))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
    metric_names = (
        "brightness_mean", "contrast_std", "saturation_mean", "warm_cool_balance",
        "edge_density", "sharpness_laplacian", "horizontal_symmetry", "vertical_symmetry",
        "light_neutral_area",
    )
    metric_distributions: dict[str, dict[str, float]] = {}
    for name in metric_names:
        values = [float(item.get("visual_metrics", {}).get(name)) for item in analyses if isinstance(item.get("visual_metrics", {}).get(name), (int, float))]
        metric_distributions[name] = {
            "mean": round(sum(values) / max(1, len(values)), 4),
            "p25": round(quantile(values, 0.25), 4),
            "median": round(quantile(values, 0.5), 4),
            "p75": round(quantile(values, 0.75), 4),
        }
    collection_analysis = {
        "schema_version": 1,
        "source_count": len(analyses),
        "orientations": dict(
            Counter(item.get("image", {}).get("orientation", "unknown") for item in analyses)
        ),
        "metric_distributions": metric_distributions,
        "palette_observations": [
            {"source_id": item.get("id"), "colors": item.get("palette", [])[:6]} for item in analyses
        ],
        "distillation_status": "evidence_ready",
        "semantic_review_required": [
            "cluster by purpose and composition before declaring a stable visual system",
            "separate brand constants from subject, campaign, format, and one-off creative choices",
            "create evidence-linked semantic cards for every retained image",
        ],
    }
    atomic_write_json(output / "collection-analysis.json", collection_analysis)
    manifest = {
        "schema_version": 2,
        "kind": "image-analysis",
        "generated_at": utc_now(),
        "local_only": True,
        "status_scope": "deterministic_visual_evidence_only",
        "distillation_status": "evidence_ready",
        "source_count": len(sources),
        **counts,
        "contact_sheet": contact_artifact,
        "collection_analysis": "collection-analysis.json",
        "ocr": {"mode": args.ocr, "available": ocr_engine is not None, "error": ocr_error},
        "items": manifest_items,
    }
    atomic_write_json(output / "manifest.json", manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 1 if counts["failed_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
