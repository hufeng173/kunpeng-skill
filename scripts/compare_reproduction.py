#!/usr/bin/env python3
"""Compare local candidate output with a reference using deterministic diagnostics."""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

from kunpeng_common import (
    IMAGE_EXTENSIONS,
    VIDEO_EXTENSIONS,
    atomic_write_json,
    command_path,
    configure_utf8,
    run_command,
    utc_now,
)


TEXT_EXTENSIONS = {".md", ".rst", ".text", ".txt"}
RHETORICAL_GROUPS = {
    "contrast": ("但是", "但", "然而", "不过", "反而", "yet", "however", "but"),
    "cause": ("因为", "因此", "所以", "于是", "由于", "because", "therefore", "thus"),
    "example": ("例如", "比如", "举个例子", "for example", "for instance"),
    "definition": ("所谓", "意味着", "也就是说", "指的是", "means", "defined as"),
    "conclusion": ("总之", "归根结底", "最后", "可见", "in conclusion", "ultimately"),
    "reader_address": ("你", "你们", "读者", "you", "your"),
    "self_reference": ("我", "我们", "作者", "i ", "we ", "my ", "our "),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare a reference and candidate locally; no hosted model is used."
    )
    parser.add_argument("reference", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--kind", choices=("auto", "image", "video", "text"), default="auto")
    parser.add_argument("--mode", choices=("faithful", "style"), default="style")
    parser.add_argument("--samples", type=int, default=16)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def closeness(left: float, right: float, floor: float = 1e-6) -> float:
    return math.exp(-abs(math.log((abs(left) + floor) / (abs(right) + floor))))


def bounded_similarity(left: float, right: float, scale: float = 1.0) -> float:
    return max(0.0, min(1.0, 1.0 - abs(left - right) / max(scale, 1e-9)))


def weighted_score(metrics: dict[str, float], weights: dict[str, float]) -> float:
    total = sum(weights.get(name, 0.0) for name in metrics)
    if not total:
        return 0.0
    return round(100.0 * sum(metrics[name] * weights.get(name, 0.0) for name in metrics) / total, 2)


def image_signature(path: Path) -> dict[str, Any]:
    import cv2
    import numpy as np
    from PIL import Image, ImageOps

    with Image.open(path) as opened:
        image = ImageOps.exif_transpose(opened.copy()).convert("RGB")
    rgb = np.asarray(image)
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    reduced = cv2.resize(bgr, (256, 256), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(reduced, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(reduced, cv2.COLOR_BGR2HSV)
    histogram = cv2.calcHist([hsv], [0, 1], None, [24, 16], [0, 180, 0, 256])
    histogram = cv2.normalize(histogram, histogram).flatten()
    median = float(np.median(gray))
    edges = cv2.Canny(gray, int(max(0, median * 0.66)), int(min(255, max(1, median * 1.33))))
    layout = cv2.resize(gray, (32, 32), interpolation=cv2.INTER_AREA).astype(float)
    layout = (layout - layout.mean()) / max(layout.std(), 1e-6)
    dhash_bits = cv2.resize(gray, (9, 8), interpolation=cv2.INTER_AREA)
    dhash = (dhash_bits[:, 1:] > dhash_bits[:, :-1]).flatten()
    return {
        "width": image.width,
        "height": image.height,
        "aspect": image.width / max(1, image.height),
        "brightness": float(np.mean(gray)) / 255.0,
        "contrast": float(np.std(gray)) / 255.0,
        "saturation": float(np.mean(hsv[:, :, 1])) / 255.0,
        "edge_density": float(np.count_nonzero(edges)) / edges.size,
        "histogram": histogram,
        "layout": layout,
        "dhash": dhash,
    }


def compare_images(reference: Path, candidate: Path, mode: str) -> dict[str, Any]:
    import cv2
    import numpy as np

    left, right = image_signature(reference), image_signature(candidate)
    hist = (float(cv2.compareHist(left["histogram"], right["histogram"], cv2.HISTCMP_CORREL)) + 1.0) / 2.0
    layout = (float(np.corrcoef(left["layout"].flatten(), right["layout"].flatten())[0, 1]) + 1.0) / 2.0
    if math.isnan(layout):
        layout = 0.0
    dhash = 1.0 - float(np.count_nonzero(left["dhash"] != right["dhash"])) / len(left["dhash"])
    metrics = {
        "aspect_ratio": closeness(left["aspect"], right["aspect"]),
        "color_distribution": max(0.0, min(1.0, hist)),
        "brightness": bounded_similarity(left["brightness"], right["brightness"]),
        "contrast": bounded_similarity(left["contrast"], right["contrast"]),
        "saturation": bounded_similarity(left["saturation"], right["saturation"]),
        "edge_density": closeness(left["edge_density"], right["edge_density"]),
        "spatial_layout": max(0.0, min(1.0, layout)),
        "perceptual_hash": dhash,
    }
    weights = (
        {
            "aspect_ratio": 1.0, "color_distribution": 1.5, "brightness": 1.0,
            "contrast": 1.0, "saturation": 1.0, "edge_density": 1.0,
            "spatial_layout": 2.0, "perceptual_hash": 2.0,
        }
        if mode == "faithful"
        else {
            "aspect_ratio": 0.8, "color_distribution": 2.0, "brightness": 1.2,
            "contrast": 1.2, "saturation": 1.2, "edge_density": 1.2,
            "spatial_layout": 0.7, "perceptual_hash": 0.0,
        }
    )
    return {
        "form_proxy_score": weighted_score(metrics, weights),
        "dimensions": {
            "reference": [left["width"], left["height"]],
            "candidate": [right["width"], right["height"]],
        },
        "metrics": {name: round(value * 100.0, 2) for name, value in metrics.items()},
        "agent_review_required": [
            "subject and semantic correctness",
            "typography, readable text, and logo handling",
            "lighting intent, material character, and visual artifacts",
        ],
    }


def probe_video(path: Path) -> dict[str, float]:
    ffprobe = command_path("ffprobe")
    if not ffprobe:
        raise RuntimeError("ffprobe is not available")
    result = run_command(
        [
            ffprobe, "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height,avg_frame_rate:format=duration",
            "-of", "json", str(path),
        ]
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr or "ffprobe failed")
    data = json.loads(result.stdout)
    stream = (data.get("streams") or [{}])[0]
    fps_value = stream.get("avg_frame_rate", "0/1")
    numerator, denominator = (fps_value.split("/", 1) + ["1"])[:2]
    fps = float(numerator) / max(float(denominator), 1e-9)
    return {
        "width": float(stream.get("width") or 0),
        "height": float(stream.get("height") or 0),
        "duration": float((data.get("format") or {}).get("duration") or 0),
        "fps": fps,
    }


def video_signature(path: Path, samples: int) -> dict[str, Any]:
    import cv2
    import numpy as np

    probe = probe_video(path)
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise RuntimeError("OpenCV could not open video")
    duration = probe["duration"]
    timestamps = np.linspace(0.02 * duration, 0.98 * duration, max(2, samples)) if duration else np.arange(max(2, samples))
    histograms, brightness, contrast, edges, layouts = [], [], [], [], []
    previous = None
    motion = []
    try:
        for timestamp in timestamps:
            capture.set(cv2.CAP_PROP_POS_MSEC, float(timestamp) * 1000.0)
            ok, frame = capture.read()
            if not ok or frame is None:
                continue
            reduced = cv2.resize(frame, (192, 108), interpolation=cv2.INTER_AREA)
            gray = cv2.cvtColor(reduced, cv2.COLOR_BGR2GRAY)
            hsv = cv2.cvtColor(reduced, cv2.COLOR_BGR2HSV)
            histogram = cv2.calcHist([hsv], [0, 1], None, [18, 12], [0, 180, 0, 256])
            histograms.append(cv2.normalize(histogram, histogram).flatten())
            brightness.append(float(np.mean(gray)) / 255.0)
            contrast.append(float(np.std(gray)) / 255.0)
            edges.append(float(np.count_nonzero(cv2.Canny(gray, 60, 140))) / gray.size)
            layout = cv2.resize(gray, (16, 9), interpolation=cv2.INTER_AREA).astype(float)
            layout = (layout - layout.mean()) / max(layout.std(), 1e-6)
            layouts.append(layout)
            if previous is not None:
                motion.append(float(np.mean(cv2.absdiff(gray, previous))) / 255.0)
            previous = gray
    finally:
        capture.release()
    if not histograms:
        raise RuntimeError("No frames could be sampled")
    return {
        **probe,
        "histogram": np.mean(histograms, axis=0),
        "brightness": float(np.mean(brightness)),
        "contrast": float(np.mean(contrast)),
        "edge_density": float(np.mean(edges)),
        "motion": motion,
        "layouts": layouts,
    }


def sequence_similarity(left: list[float], right: list[float]) -> float:
    import numpy as np

    size = min(len(left), len(right))
    if size < 2:
        return 0.0
    left_values = np.asarray(left[:size], dtype=float)
    right_values = np.asarray(right[:size], dtype=float)
    correlation = float(np.corrcoef(left_values, right_values)[0, 1])
    return 0.0 if math.isnan(correlation) else max(0.0, min(1.0, (correlation + 1.0) / 2.0))


def compare_videos(reference: Path, candidate: Path, mode: str, samples: int) -> dict[str, Any]:
    import cv2
    import numpy as np

    left, right = video_signature(reference, samples), video_signature(candidate, samples)
    histogram = (float(cv2.compareHist(left["histogram"], right["histogram"], cv2.HISTCMP_CORREL)) + 1.0) / 2.0
    layouts = [
        (float(np.corrcoef(a.flatten(), b.flatten())[0, 1]) + 1.0) / 2.0
        for a, b in zip(left["layouts"], right["layouts"])
    ]
    layout = float(np.nanmean(layouts)) if layouts else 0.0
    metrics = {
        "aspect_ratio": closeness(left["width"] / max(1, left["height"]), right["width"] / max(1, right["height"])),
        "duration": closeness(left["duration"], right["duration"]),
        "frame_rate": closeness(left["fps"], right["fps"]),
        "color_distribution": max(0.0, min(1.0, histogram)),
        "brightness": bounded_similarity(left["brightness"], right["brightness"]),
        "contrast": bounded_similarity(left["contrast"], right["contrast"]),
        "edge_density": closeness(left["edge_density"], right["edge_density"]),
        "sampled_layout": max(0.0, min(1.0, layout)),
        "sampled_pixel_change_rhythm": sequence_similarity(left["motion"], right["motion"]),
    }
    weights = (
        {
            "aspect_ratio": 1.0, "duration": 1.5, "frame_rate": 0.5,
            "color_distribution": 1.0, "brightness": 0.7, "contrast": 0.7,
            "edge_density": 0.7, "sampled_layout": 2.0, "sampled_pixel_change_rhythm": 1.5,
        }
        if mode == "faithful"
        else {
            "aspect_ratio": 0.7, "duration": 0.0, "frame_rate": 0.3,
            "color_distribution": 1.8, "brightness": 1.0, "contrast": 1.0,
            "edge_density": 1.0, "sampled_layout": 0.5, "sampled_pixel_change_rhythm": 1.7,
        }
    )
    return {
        "form_proxy_score": weighted_score(metrics, weights),
        "metrics": {name: round(value * 100.0, 2) for name, value in metrics.items()},
        "agent_review_required": [
            "shot boundaries, camera movement, transitions, and continuity",
            "subject action and semantic sequence",
            "speech, music, sound effects, synchronization, and on-screen text",
        ],
    }


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig", errors="replace")


def trigrams(text: str) -> set[str]:
    compact = "".join(text.casefold().split())
    return {compact[index : index + 3] for index in range(max(0, len(compact) - 2))}


def cosine_counter(left: dict[str, int], right: dict[str, int]) -> float:
    keys = set(left) | set(right)
    dot = sum(left.get(key, 0) * right.get(key, 0) for key in keys)
    left_norm = math.sqrt(sum(value * value for value in left.values()))
    right_norm = math.sqrt(sum(value * value for value in right.values()))
    return dot / (left_norm * right_norm) if left_norm and right_norm else 0.0


def rhetorical_profile(text: str) -> dict[str, int]:
    folded = text.casefold()
    return {
        group: sum(folded.count(marker.casefold()) for marker in markers)
        for group, markers in RHETORICAL_GROUPS.items()
    }


def distribution_similarity(
    left: dict[str, float], right: dict[str, float], keys: tuple[str, str, str]
) -> float:
    values = [closeness(float(left.get(key, 0.0)), float(right.get(key, 0.0))) for key in keys]
    return sum(values) / len(values)


def compare_texts(reference: Path, candidate: Path, mode: str) -> dict[str, Any]:
    from analyze_documents import style_metrics

    left_text, right_text = read_text(reference), read_text(candidate)
    left, right = style_metrics(left_text), style_metrics(right_text)
    metrics = {
        "sentence_length": closeness(left["sentence_length"]["mean"], right["sentence_length"]["mean"]),
        "paragraph_length": closeness(left["paragraph_length"]["mean_chars"], right["paragraph_length"]["mean_chars"]),
        "sentence_length_distribution": distribution_similarity(
            left["sentence_length"], right["sentence_length"], ("p25", "median", "p75")
        ),
        "paragraph_length_distribution": distribution_similarity(
            left["paragraph_length"], right["paragraph_length"],
            ("p25_chars", "median_chars", "p75_chars"),
        ),
        "lexical_diversity": closeness(left["lexical_diversity"], right["lexical_diversity"]),
        "question_share": bounded_similarity(left["question_sentence_share"], right["question_sentence_share"], 0.25),
        "exclamation_share": bounded_similarity(left["exclamation_sentence_share"], right["exclamation_sentence_share"], 0.25),
        "heading_share": bounded_similarity(left["heading_line_share"], right["heading_line_share"], 0.25),
        "list_share": bounded_similarity(left["list_line_share"], right["list_line_share"], 0.25),
        "punctuation_pattern": cosine_counter(left["punctuation"], right["punctuation"]),
        "rhetorical_marker_profile": cosine_counter(
            rhetorical_profile(left_text), rhetorical_profile(right_text)
        ),
        "length": closeness(len(left_text), len(right_text)),
    }
    left_grams, right_grams = trigrams(left_text), trigrams(right_text)
    metrics["surface_content_overlap"] = (
        len(left_grams & right_grams) / max(1, len(left_grams | right_grams))
    )
    weights = (
        {
            "sentence_length": 1.0, "paragraph_length": 1.0,
            "sentence_length_distribution": 0.8, "paragraph_length_distribution": 0.8,
            "lexical_diversity": 0.8,
            "question_share": 0.5, "exclamation_share": 0.5, "heading_share": 0.6,
            "list_share": 0.6, "punctuation_pattern": 1.0,
            "rhetorical_marker_profile": 1.2, "length": 0.7,
            "surface_content_overlap": 2.0,
        }
        if mode == "faithful"
        else {
            "sentence_length": 1.2, "paragraph_length": 1.0,
            "sentence_length_distribution": 1.2, "paragraph_length_distribution": 1.2,
            "lexical_diversity": 0.8,
            "question_share": 0.8, "exclamation_share": 0.8, "heading_share": 1.0,
            "list_share": 1.0, "punctuation_pattern": 1.0,
            "rhetorical_marker_profile": 2.0, "length": 0.2,
            "surface_content_overlap": 0.0,
        }
    )
    return {
        "form_proxy_score": weighted_score(metrics, weights),
        "metrics": {name: round(value * 100.0, 2) for name, value in metrics.items()},
        "agent_review_required": [
            "argument structure, narrative voice, and rhetorical intent",
            "factual correctness and preservation of requested meaning",
            "originality, naturalness, and prohibited phrase reuse",
        ],
    }


def infer_kind(reference: Path, candidate: Path) -> str:
    extensions = {reference.suffix.casefold(), candidate.suffix.casefold()}
    for kind, supported in (("image", IMAGE_EXTENSIONS), ("video", VIDEO_EXTENSIONS), ("text", TEXT_EXTENSIONS)):
        if extensions <= supported:
            return kind
    raise SystemExit("Reference and candidate must be the same supported kind, or pass --kind.")


def main() -> int:
    configure_utf8()
    args = parse_args()
    reference, candidate = args.reference.resolve(), args.candidate.resolve()
    if not reference.is_file() or not candidate.is_file():
        raise SystemExit("Reference and candidate must both be files.")
    kind = infer_kind(reference, candidate) if args.kind == "auto" else args.kind
    if kind == "image":
        result = compare_images(reference, candidate, args.mode)
    elif kind == "video":
        result = compare_videos(reference, candidate, args.mode, max(4, args.samples))
    else:
        result = compare_texts(reference, candidate, args.mode)
    report = {
        "schema_version": 2,
        "generated_at": utc_now(),
        "kind": kind,
        "mode": args.mode,
        "local_only": True,
        "automatic_verdict": "not_evaluated",
        "automatic_pass_allowed": False,
        "score_meaning": "The form_proxy_score covers only listed deterministic proxies and can never pass a reproduction by itself.",
        **result,
    }
    if args.output:
        atomic_write_json(args.output.resolve(), report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
