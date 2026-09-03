#!/usr/bin/env python3
"""Search a Kunpeng library index and return context-bounded candidates."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 2
ASCII_WORD_RE = re.compile(r"[a-z0-9][a-z0-9+.#_-]*", re.IGNORECASE)
CJK_RUN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")
SPACE_RE = re.compile(r"\s+")
PHRASE_SPLIT_RE = re.compile(r"[\s,，;；|/]+")

FIELD_WEIGHTS = {
    "标题": 8.0,
    "项目介绍": 4.5,
    "适合参考的方向": 5.0,
    "核心功能": 5.0,
    "技术栈": 4.0,
    "项目结构": 1.5,
    "核心流程": 3.5,
    "数据流": 3.0,
    "外部依赖": 2.5,
    "UI/交互亮点": 5.5,
    "值得借鉴": 6.0,
    "整理重点": 2.0,
    "缺点": 3.5,
    "运行方式": 1.0,
    "概览": 2.0,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Search an index with lexical scoring and bounded excerpts."
    )
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--query", required=True)
    parser.add_argument("--limit", type=int, default=6)
    parser.add_argument("--max-chars", type=int, default=8000)
    return parser.parse_args()


def tokenize(text: str) -> list[str]:
    lowered = text.casefold()
    result = ASCII_WORD_RE.findall(lowered)
    for run in CJK_RUN_RE.findall(lowered):
        if 2 <= len(run) <= 10:
            result.append(run)
        for width in (2, 3):
            if len(run) >= width:
                result.extend(run[index : index + width] for index in range(len(run) - width + 1))
    return result


def query_phrases(query: str) -> list[str]:
    phrases = [item.casefold() for item in PHRASE_SPLIT_RE.split(query) if len(item) >= 2]
    return list(dict.fromkeys(phrases))


def load_index(path: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Cannot read index: {exc}") from exc
    if data.get("schema_version") != SCHEMA_VERSION:
        raise SystemExit("Unsupported index version; rebuild it with build_library_index.py")
    return data.get("documents", [])


def document_fields(document: dict[str, Any]) -> dict[str, str]:
    fields = {"标题": document.get("display_name", "")}
    fields.update(document.get("sections", {}))
    return {name: value for name, value in fields.items() if value}


def make_excerpt(text: str, needles: list[str], limit: int) -> str:
    compact = SPACE_RE.sub(" ", text).strip()
    if len(compact) <= limit:
        return compact

    lowered = compact.casefold()
    positions = [lowered.find(needle) for needle in needles if lowered.find(needle) >= 0]
    start = max(0, min(positions) - limit // 4) if positions else 0
    end = min(len(compact), start + limit)
    prefix = "..." if start else ""
    suffix = "..." if end < len(compact) else ""
    return f"{prefix}{compact[start:end].strip()}{suffix}"


def search(
    documents: list[dict[str, Any]], query: str, limit: int, max_chars: int
) -> list[dict[str, Any]]:
    query_tokens = list(dict.fromkeys(tokenize(query)))
    phrases = query_phrases(query)
    if not query_tokens and not phrases:
        return []

    prepared: list[tuple[dict[str, Any], dict[str, Counter[str]], set[str]]] = []
    document_frequency: Counter[str] = Counter()

    for document in documents:
        counters: dict[str, Counter[str]] = {}
        all_tokens: set[str] = set()
        for field, value in document_fields(document).items():
            counter = Counter(tokenize(value))
            counters[field] = counter
            all_tokens.update(counter)
        prepared.append((document, counters, all_tokens))
        for token in query_tokens:
            if token in all_tokens:
                document_frequency[token] += 1

    total_documents = max(1, len(documents))
    scored: list[tuple[float, dict[str, Any], list[tuple[float, str]]]] = []

    for document, counters, _ in prepared:
        total_score = 0.0
        section_scores: list[tuple[float, str]] = []
        fields = document_fields(document)
        for field, counter in counters.items():
            weight = FIELD_WEIGHTS.get(field, 2.0)
            field_score = 0.0
            for token in query_tokens:
                frequency = counter.get(token, 0)
                if not frequency:
                    continue
                df = document_frequency.get(token, 0)
                inverse_frequency = math.log(1.0 + (total_documents + 0.5) / (df + 0.5))
                field_score += weight * inverse_frequency * (1.0 + math.log(frequency))

            lowered = fields[field].casefold()
            for phrase in phrases:
                if phrase in lowered:
                    field_score += weight * 2.5

            if field_score > 0:
                section_scores.append((field_score, field))
                total_score += field_score

        if total_score > 0:
            scored.append((total_score, document, section_scores))

    scored.sort(key=lambda item: (-item[0], item[1].get("relative_path", "")))
    result_limit = max(1, min(limit, 20))
    excerpt_limit = max(160, min(700, max_chars // max(1, result_limit * 3)))
    results: list[dict[str, Any]] = []
    seen_identities: set[str] = set()

    for score, document, section_scores in scored:
        identity = document.get("subject_identity")
        if identity and identity in seen_identities:
            continue
        sections = document_fields(document)
        ranked_sections = sorted(section_scores, reverse=True)[:3]
        excerpts = [
            {
                "section": field,
                "text": make_excerpt(sections[field], phrases or query_tokens, excerpt_limit),
            }
            for _, field in ranked_sections
        ]
        results.append(
            {
                "relative_path": document.get("relative_path"),
                "display_name": document.get("display_name"),
                "score": round(score, 3),
                "excerpts": excerpts,
            }
        )
        if identity:
            seen_identities.add(identity)
        if len(results) >= result_limit:
            break

    while results and len(json.dumps(results, ensure_ascii=False)) > max_chars:
        results.pop()
    return results


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parse_args()
    documents = load_index(args.index)
    results = search(documents, args.query, args.limit, max(1000, args.max_chars))
    print(
        json.dumps(
            {"query": args.query, "candidate_count": len(results), "candidates": results},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
