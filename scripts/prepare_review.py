#!/usr/bin/env python3
"""Create evidence-bound semantic review tasks and card templates from a manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

from kunpeng_common import atomic_write_json, configure_utf8, prepare_output, slugify, utc_now
from profile_contract import SOURCE_TYPES


DIMENSIONS = {
    "document-analysis": [
        "structure and information order", "argument or teaching moves", "voice and audience distance",
        "sentence and paragraph rhythm", "rhetorical actions", "topic-dependent versus stable traits",
    ],
    "image-analysis": [
        "subject and hierarchy", "composition", "lighting", "color roles", "typography and graphics",
        "material and finish", "stable visual rules versus content variables",
    ],
    "video-analysis": [
        "narrative beats", "shot grammar", "camera and subject motion", "composition and continuity",
        "editing and transitions", "speech music text synchronization", "stable rules versus one-off choices",
    ],
    "audio-analysis": [
        "content structure", "speaker and audience relation", "pace pauses and emphasis", "prosody and emotion",
        "music and sound layers", "stable audio rules versus content variables",
    ],
    "repository-analysis": [
        "user-visible purpose", "architecture and module boundaries", "data and control flow",
        "technical decisions and tradeoffs", "failure handling", "transferable methods versus project-specific facts",
    ],
    "website-analysis": [
        "information architecture", "primary user path", "interaction states and feedback", "visual system",
        "data and service behavior", "transferable product rules versus brand-specific assets",
    ],
    "app-analysis": [
        "onboarding permissions and navigation", "primary task flows", "interaction states",
        "failure and recovery", "platform conventions", "transferable product rules",
    ],
    "ui-analysis": [
        "layout and information hierarchy", "component states", "interaction and motion",
        "responsive and accessible behavior", "content relationships", "transferable UI rules",
    ],
    "brand-analysis": [
        "identity logic", "color typography and imagery roles", "cross-format consistency",
        "content variables", "protected assets", "transferable visual rules",
    ],
    "course-analysis": [
        "learning objectives", "concept dependencies", "teaching sequence", "examples and exercises",
        "feedback and assessment", "transfer boundaries",
    ],
    "mixed-analysis": [
        "cross-source relationships", "shared mechanisms", "contradictions", "sequence and dependencies",
        "content variables", "transferable system-level rules",
    ],
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare semantic review cards from an analysis manifest.")
    parser.add_argument("manifest", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--source-type", choices=tuple(sorted(SOURCE_TYPES)),
        help="Override inferred source type for every card.",
    )
    return parser.parse_args()


def infer_source_type(kind: str) -> str:
    return {
        "document-analysis": "document",
        "image-analysis": "image",
        "video-analysis": "video",
        "audio-analysis": "audio",
        "repository-analysis": "repository",
        "website-analysis": "website",
        "app-analysis": "app",
        "ui-analysis": "ui",
        "brand-analysis": "brand",
        "course-analysis": "course",
        "mixed-analysis": "mixed",
    }.get(kind, "other")


def card_filename(source_id: str) -> str:
    if source_id not in {".", ".."} and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,119}", source_id):
        return f"{source_id}.json"
    digest = hashlib.sha256(source_id.encode("utf-8")).hexdigest()[:10]
    return f"{slugify(source_id)[:80] or 'source'}-{digest}.json"


def main() -> int:
    configure_utf8()
    args = parse_args()
    manifest_paths: list[Path] = []
    entries: list[tuple[Path, str, dict[str, Any], Path]] = []
    seen_ids: set[str] = set()
    for raw_path in args.manifest:
        manifest_path = raw_path.resolve()
        if not manifest_path.is_file():
            raise SystemExit(f"Manifest does not exist: {manifest_path}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        if not isinstance(manifest, dict):
            raise SystemExit(f"Manifest is not an object: {manifest_path}")
        kind = str(manifest.get("kind", "unknown-analysis"))
        if manifest.get("distillation_status") != "evidence_ready":
            raise SystemExit(f"Manifest is not evidence_ready: {manifest_path}")
        items = manifest.get("items")
        if not isinstance(items, list):
            raise SystemExit(f"Manifest items is not a list: {manifest_path}")
        manifest_paths.append(manifest_path)
        for item in items:
            if not isinstance(item, dict):
                raise SystemExit(f"Manifest contains a non-object item: {manifest_path}")
            item_id = str(item.get("id") or "source")
            if item.get("status") == "failed":
                raise SystemExit(f"Manifest contains failed evidence item {item_id}: {manifest_path}")
            if item.get("distillation_status") != "evidence_ready":
                raise SystemExit(f"Manifest item is not evidence_ready ({item_id}): {manifest_path}")
            if item_id in seen_ids:
                raise SystemExit(f"Duplicate source id across manifests: {item_id}")
            analysis_value = item.get("analysis")
            if not analysis_value:
                raise SystemExit(f"Manifest item has no analysis artifact ({item_id}): {manifest_path}")
            analysis_path = Path(str(analysis_value))
            if not analysis_path.is_absolute():
                analysis_path = (manifest_path.parent / analysis_path).resolve()
            if not analysis_path.is_file():
                raise SystemExit(f"Analysis artifact is missing ({item_id}): {analysis_path}")
            seen_ids.add(item_id)
            entries.append((manifest_path, kind, item, analysis_path))
    if not entries:
        raise SystemExit("Manifest contains no reviewable items.")

    output = prepare_output(args.output, args.resume)
    cards_dir = output / "cards"
    cards_dir.mkdir(parents=True, exist_ok=True)

    tasks: list[dict[str, Any]] = []
    for manifest_path, manifest_kind, item, analysis_path in entries:
        item_id = str(item.get("id") or "source")
        card_path = cards_dir / card_filename(item_id)
        item_kind = str(item.get("origin_kind") or manifest_kind)
        dimensions = DIMENSIONS.get(item_kind, DIMENSIONS["mixed-analysis"])
        source_type = args.source_type or item.get("source_type") or infer_source_type(item_kind)
        if source_type not in SOURCE_TYPES:
            source_type = infer_source_type(item_kind)
        analysis_reference = Path(os.path.relpath(analysis_path, card_path.parent)).as_posix()
        task_analysis_reference = Path(os.path.relpath(analysis_path, output)).as_posix()
        if not (args.resume and card_path.exists()):
            card = {
                "schema_version": 1,
                "source_id": item_id,
                "source_type": source_type,
                "source_label": item.get("source", item_id),
                "analysis_artifact": analysis_reference,
                "review_status": "pending",
                "exclusion_reason": "",
                "summary": "",
                "patterns": [],
                "variables": [],
                "exceptions": [],
                "limitations": [],
            }
            atomic_write_json(card_path, card)
        tasks.append(
            {
                "source_id": item_id,
                "source": item.get("source", item_id),
                "analysis": task_analysis_reference,
                "card": card_path.relative_to(output).as_posix(),
                "required_dimensions": dimensions,
                "instructions": [
                    "Open the real source artifacts required for each judgment.",
                    "Record mechanisms and conditions, not adjectives or generic advice.",
                    "Give every pattern a stable key, confidence, and at least one evidence locator.",
                    "Separate transferable patterns, content variables, exceptions, and limitations.",
                    "Set review_status to complete only after the card passes contract validation.",
                ],
            }
        )
    payload = {
        "schema_version": 1,
        "generated_at": utc_now(),
        "manifests": [Path(os.path.relpath(path, output)).as_posix() for path in manifest_paths],
        "manifest_kinds": sorted(
            {str(item.get("origin_kind") or kind) for _, kind, item, _ in entries}
        ),
        "source_count": len(tasks),
        "cards_directory": "cards",
        "tasks": tasks,
    }
    atomic_write_json(output / "review-tasks.json", payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
