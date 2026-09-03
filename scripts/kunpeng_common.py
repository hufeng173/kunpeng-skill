#!/usr/bin/env python3
"""Shared helpers for Kunpeng's local-only processing scripts."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence


VIDEO_EXTENSIONS = {
    ".3gp", ".avi", ".flv", ".m2ts", ".m4v", ".mkv", ".mov", ".mp4",
    ".mpeg", ".mpg", ".mts", ".webm", ".wmv",
}
AUDIO_EXTENSIONS = {
    ".aac", ".aiff", ".alac", ".amr", ".ape", ".flac", ".m4a", ".mp3",
    ".ogg", ".opus", ".wav", ".wma",
}
IMAGE_EXTENSIONS = {
    ".avif", ".bmp", ".gif", ".heic", ".heif", ".jpeg", ".jpg", ".png",
    ".tif", ".tiff", ".webp",
}
DOCUMENT_EXTENSIONS = {
    ".ass", ".csv", ".docx", ".htm", ".html", ".json", ".md", ".pdf",
    ".pptx", ".rst", ".srt", ".text", ".txt", ".vtt", ".yaml", ".yml",
}

ANALYSIS_STATUSES = (
    "complete",
    "degraded",
    "partial",
    "failed",
    "not_applicable",
)
STATUS_PRIORITY = {
    "not_applicable": 0,
    "complete": 1,
    "degraded": 2,
    "partial": 3,
    "failed": 4,
}


def configure_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def atomic_write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(value, encoding="utf-8")
    os.replace(temporary, path)


def prepare_output(path: Path, resume: bool) -> Path:
    resolved = path.resolve()
    if resolved.exists() and not resume:
        raise SystemExit(
            f"Output already exists: {resolved}. Choose a new directory or pass --resume."
        )
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def slugify(value: str, fallback: str = "source") -> str:
    value = re.sub(r"[^\w.-]+", "-", value, flags=re.UNICODE).strip("-._")
    return value[:80] or fallback


def sampled_fingerprint(path: Path, block_size: int = 1024 * 1024) -> str:
    """Hash size plus bounded file samples so large media is not read twice."""
    size = path.stat().st_size
    digest = hashlib.sha256(str(size).encode("ascii"))
    with path.open("rb") as handle:
        digest.update(handle.read(block_size))
        if size > block_size * 2:
            handle.seek(max(0, size // 2 - block_size // 2))
            digest.update(handle.read(block_size))
            handle.seek(max(0, size - block_size))
            digest.update(handle.read(block_size))
    return digest.hexdigest()


def source_id(path: Path) -> str:
    return f"{slugify(path.stem)}-{sampled_fingerprint(path)[:10]}"


def find_sources(
    source: Path, extensions: set[str], recursive: bool = True
) -> list[Path]:
    resolved = source.resolve()
    if resolved.is_file():
        if resolved.suffix.casefold() not in extensions:
            raise SystemExit(f"Unsupported source type: {resolved.suffix or '<none>'}")
        return [resolved]
    if not resolved.is_dir():
        raise SystemExit(f"Source does not exist: {resolved}")

    iterator = resolved.rglob("*") if recursive else resolved.glob("*")
    return sorted(
        (
            item
            for item in iterator
            if not item.is_symlink()
            and item.is_file()
            and item.suffix.casefold() in extensions
            and not any(part.startswith(".") for part in item.relative_to(resolved).parts)
        ),
        key=lambda item: item.as_posix().casefold(),
    )


def command_path(name: str) -> str | None:
    return shutil.which(name)


def run_command(
    args: Sequence[str], timeout: float | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args),
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def command_version(name: str, arguments: Sequence[str] = ("-version",)) -> dict[str, Any]:
    executable = command_path(name)
    if not executable:
        return {"available": False, "path": None, "version": None}
    try:
        result = run_command([executable, *arguments], timeout=15)
        combined = (result.stdout or result.stderr).strip().splitlines()
        version = combined[0][:300] if combined else None
        return {
            "available": result.returncode == 0,
            "path": executable,
            "version": version,
        }
    except (OSError, subprocess.SubprocessError) as exc:
        return {"available": False, "path": executable, "version": str(exc)}


def bounded_error(
    error: BaseException | str, source: Path | None = None, output: Path | None = None
) -> str:
    message = " ".join(str(error).replace("\r", "\n").splitlines()).strip()
    if source:
        message = message.replace(str(source), "<source>")
    if output:
        message = message.replace(str(output), "<output>")
    return message[:1000]


def relative_artifact(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def evenly_spaced(items: Sequence[Any], limit: int) -> list[Any]:
    if limit <= 0 or len(items) <= limit:
        return list(items)
    if limit == 1:
        return [items[0]]
    indexes = {
        round(index * (len(items) - 1) / (limit - 1)) for index in range(limit)
    }
    return [items[index] for index in sorted(indexes)]


def quantile(values: Iterable[float], fraction: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return 0.0
    position = max(0.0, min(1.0, fraction)) * (len(ordered) - 1)
    lower = int(position)
    upper = min(len(ordered) - 1, lower + 1)
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def aggregate_status(statuses: Iterable[str]) -> str:
    """Return the most severe truthful status for a set of applicable stages."""
    normalized = [status for status in statuses if status in STATUS_PRIORITY]
    if not normalized:
        return "not_applicable"
    return max(normalized, key=STATUS_PRIORITY.__getitem__)


def status_counts(items: Iterable[dict[str, Any]]) -> dict[str, int]:
    counts = {f"{status}_count": 0 for status in ANALYSIS_STATUSES}
    for item in items:
        status = item.get("status", "failed")
        if status not in ANALYSIS_STATUSES:
            status = "failed"
        counts[f"{status}_count"] += 1
    return counts


def reused_analysis_status(path: Path) -> str:
    """Read a prior analysis status while remaining compatible with schema v1."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        status = payload.get("status", "complete")
        return status if status in ANALYSIS_STATUSES else "failed"
    except (OSError, UnicodeError, json.JSONDecodeError):
        return "failed"
