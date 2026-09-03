#!/usr/bin/env python3
"""Compatibility wrapper for local PaddleOCR 2.x and 3.x runtimes."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable


class LocalOCR:
    def __init__(self, lang: str = "ch", device: str = "auto") -> None:
        try:
            from paddleocr import PaddleOCR
        except ImportError as exc:
            raise RuntimeError("PaddleOCR is not installed") from exc

        resolved_device = None if device == "auto" else device
        modern: dict[str, Any] = {
            "lang": lang,
            "use_doc_orientation_classify": False,
            "use_doc_unwarping": False,
            "use_textline_orientation": True,
        }
        if resolved_device:
            modern["device"] = resolved_device
        try:
            self.engine = PaddleOCR(**modern)
            self.api = "predict"
        except (TypeError, ValueError):
            legacy: dict[str, Any] = {
                "lang": lang,
                "use_angle_cls": True,
                "show_log": False,
            }
            if resolved_device:
                legacy["use_gpu"] = resolved_device.startswith("gpu")
            self.engine = PaddleOCR(**legacy)
            self.api = "ocr"

    def recognize(self, image: str | Path | Any) -> list[dict[str, Any]]:
        if self.api == "predict":
            raw = list(self.engine.predict(str(image)))
            return _normalize_modern(raw)
        raw = self.engine.ocr(str(image), cls=True)
        return _normalize_legacy(raw)


def _as_mapping(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    candidate = getattr(value, "json", None)
    if callable(candidate):
        candidate = candidate()
    if isinstance(candidate, dict):
        return candidate
    candidate = getattr(value, "res", None)
    return candidate if isinstance(candidate, dict) else None


def _normalize_box(box: Any) -> list[list[float]]:
    if box is None:
        return []
    if hasattr(box, "tolist"):
        box = box.tolist()
    try:
        return [[round(float(point[0]), 2), round(float(point[1]), 2)] for point in box]
    except (TypeError, ValueError, IndexError):
        return []


def _normalize_modern(results: Iterable[Any]) -> list[dict[str, Any]]:
    lines: list[dict[str, Any]] = []
    for result in results:
        mapping = _as_mapping(result) or {}
        payload = mapping.get("res", mapping)
        texts = payload.get("rec_texts") or payload.get("texts") or []
        scores = payload.get("rec_scores") or payload.get("scores") or []
        boxes = payload.get("dt_polys") or payload.get("rec_polys") or []
        for index, text in enumerate(texts):
            if not str(text).strip():
                continue
            score = scores[index] if index < len(scores) else 0.0
            box = boxes[index] if index < len(boxes) else []
            lines.append(
                {
                    "text": str(text).strip(),
                    "confidence": round(float(score), 4),
                    "box": _normalize_box(box),
                }
            )
    return lines


def _looks_like_line(value: Any) -> bool:
    return (
        isinstance(value, (list, tuple))
        and len(value) >= 2
        and isinstance(value[1], (list, tuple))
        and len(value[1]) >= 2
        and isinstance(value[1][0], str)
    )


def _walk_legacy(value: Any) -> Iterable[Any]:
    if _looks_like_line(value):
        yield value
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            yield from _walk_legacy(item)


def _normalize_legacy(result: Any) -> list[dict[str, Any]]:
    lines: list[dict[str, Any]] = []
    for line in _walk_legacy(result):
        text, score = line[1][0], line[1][1]
        if not text.strip():
            continue
        lines.append(
            {
                "text": text.strip(),
                "confidence": round(float(score), 4),
                "box": _normalize_box(line[0]),
            }
        )
    return lines

