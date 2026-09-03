#!/usr/bin/env python3
"""Build an incremental, local index for Kunpeng collection documents."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 2
DEFAULT_PATTERNS = [
    "*-收录.md",
    "*-蒸馏.md",
    "品牌视觉.md",
    "视觉蒸馏.md",
    "UI交互.md",
    "方法蒸馏.md",
    "技术方案.md",
]
H1_RE = re.compile(r"^#\s+(.+?)\s*$")
H2_RE = re.compile(r"^##\s+(.+?)\s*$")
NUMBER_PREFIX_RE = re.compile(r"^\d+[.、]\s*")
HIGH_CONFIDENCE_SECRETS = [
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[^\s`]+"),
]
ASSIGNMENT_SECRET_RE = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret)"
    r"(\s*[:=]\s*)[\"']?([^\s,;\"']{12,})[\"']?"
)
PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
    re.DOTALL,
)
SUBJECT_RE = re.compile(
    r"^(.{2,64}?)\s*(?:是一个|是一款|是面向|是围绕|是用于|是由|是个)"
)
GENERIC_SUBJECTS = {"这是", "该项目", "这个项目", "本项目", "网站", "应用", "仓库"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Index standardized Markdown collection files without external dependencies."
    )
    parser.add_argument("--library", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--pattern",
        action="append",
        help="Recursive glob pattern. Repeat to replace the default collection/distillation patterns.",
    )
    parser.add_argument("--force", action="store_true", help="Reparse every source file.")
    return parser.parse_args()


def redact_sensitive(text: str) -> str:
    redacted = PRIVATE_KEY_RE.sub("<redacted-private-key>", text)
    for pattern in HIGH_CONFIDENCE_SECRETS:
        if pattern.groups:
            redacted = pattern.sub(r"\1<redacted>", redacted)
        else:
            redacted = pattern.sub("<redacted>", redacted)
    return ASSIGNMENT_SECRET_RE.sub(
        lambda match: f"{match.group(1)}{match.group(2)}<redacted>", redacted
    )


def normalize_heading(value: str) -> str:
    return NUMBER_PREFIX_RE.sub("", value.strip()).strip()


def extract_subject_identity(introduction: str) -> str:
    for paragraph in re.split(r"\n\s*\n", introduction):
        line = " ".join(part.strip() for part in paragraph.splitlines() if part.strip())
        if not line or line.startswith(("项目地址", "GitHub 地址", "体验地址")):
            continue
        match = SUBJECT_RE.match(line)
        if not match:
            continue
        subject = re.sub(r"[`*_\[\]()]", "", match.group(1)).strip(" ：:，,。.")
        normalized = " ".join(subject.casefold().split())
        if normalized and normalized not in GENERIC_SUBJECTS:
            return normalized
    return ""


def parse_markdown(text: str, path: Path) -> dict[str, Any]:
    text = redact_sensitive(text.replace("\r\n", "\n"))
    h1 = ""
    current = "概览"
    sections: dict[str, list[str]] = {current: []}

    for line in text.splitlines():
        h1_match = H1_RE.match(line)
        if h1_match and not h1:
            h1 = h1_match.group(1).strip()
            continue

        h2_match = H2_RE.match(line)
        if h2_match:
            current = normalize_heading(h2_match.group(1))
            sections.setdefault(current, [])
            continue

        sections[current].append(line)

    compact_sections = {
        heading: "\n".join(lines).strip()
        for heading, lines in sections.items()
        if "\n".join(lines).strip()
    }
    display_name = path.stem
    if display_name.endswith("-收录"):
        display_name = display_name[: -len("-收录")]
    if h1 and h1 != "项目收录":
        display_name = h1

    return {
        "display_name": display_name,
        "subject_identity": extract_subject_identity(compact_sections.get("项目介绍", "")),
        "line_count": len(text.splitlines()),
        "sections": compact_sections,
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }


def load_existing(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    if data.get("schema_version") != SCHEMA_VERSION:
        return {}
    return data


def find_sources(library: Path, patterns: list[str]) -> list[Path]:
    found: set[Path] = set()
    for pattern in patterns:
        for path in library.rglob(pattern):
            if not path.is_file():
                continue
            relative_parts = path.relative_to(library).parts
            if ".kunpeng-cache" in relative_parts:
                continue
            found.add(path)
    return sorted(found, key=lambda item: item.relative_to(library).as_posix().casefold())


def build_index(
    library: Path, output: Path, patterns: list[str], force: bool
) -> dict[str, int]:
    library = library.resolve()
    if not library.is_dir():
        raise SystemExit(f"Library directory does not exist: {library}")

    existing = load_existing(output)
    previous = {
        item.get("relative_path"): item
        for item in existing.get("documents", [])
        if item.get("relative_path")
    }
    documents: list[dict[str, Any]] = []
    updated = 0
    reused = 0
    sources = find_sources(library, patterns)
    current_paths = {source.relative_to(library).as_posix() for source in sources}

    for source in sources:
        stat = source.stat()
        relative = source.relative_to(library).as_posix()
        cached = previous.get(relative)
        if (
            not force
            and cached
            and cached.get("size") == stat.st_size
            and cached.get("mtime_ns") == stat.st_mtime_ns
        ):
            documents.append(cached)
            reused += 1
            continue

        raw = source.read_text(encoding="utf-8-sig", errors="replace")
        parsed = parse_markdown(raw, source)
        documents.append(
            {
                "relative_path": relative,
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
                **parsed,
            }
        )
        updated += 1

    payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "patterns": patterns,
        "documents": documents,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporary, output)

    return {
        "documents": len(documents),
        "updated": updated,
        "reused": reused,
        "removed": len(set(previous) - current_paths),
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parse_args()
    library = args.library.resolve()
    output = (
        args.output.resolve()
        if args.output
        else library / ".kunpeng-cache" / "library-index.json"
    )
    patterns = args.pattern or DEFAULT_PATTERNS
    summary = build_index(library, output, patterns, args.force)
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
