#!/usr/bin/env python3
"""Merge heterogeneous analysis manifests into one mixed-source review manifest."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from kunpeng_common import atomic_write_json, configure_utf8, prepare_output, status_counts, utc_now


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Merge two or more Kunpeng analysis manifests.")
    parser.add_argument("manifests", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def relative_to_output(path: Path, output: Path) -> str:
    return Path(os.path.relpath(path.resolve(), output.resolve())).as_posix()


def main() -> int:
    configure_utf8()
    args = parse_args()
    if len(args.manifests) < 2:
        raise SystemExit("Mixed-source analysis requires at least two manifests.")
    loaded: list[tuple[Path, dict[str, Any]]] = []
    seen_manifests: set[Path] = set()
    for raw_path in args.manifests:
        manifest_path = raw_path.resolve()
        if manifest_path in seen_manifests:
            raise SystemExit(f"Manifest was supplied more than once: {manifest_path}")
        seen_manifests.add(manifest_path)
        if not manifest_path.is_file():
            raise SystemExit(f"Manifest does not exist: {manifest_path}")
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise SystemExit(f"Cannot read manifest {manifest_path}: {exc}") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
            raise SystemExit(f"Manifest must be an object with an items list: {manifest_path}")
        if payload.get("distillation_status") != "evidence_ready":
            raise SystemExit(f"Manifest is not evidence_ready: {manifest_path}")
        if payload.get("source_count") != len(payload["items"]):
            raise SystemExit(f"Manifest source_count does not match items: {manifest_path}")
        if any(not isinstance(item, dict) for item in payload["items"]):
            raise SystemExit(f"Manifest contains a non-object item: {manifest_path}")
        loaded.append((manifest_path, payload))

    output = prepare_output(args.output, False)
    items: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    for index, (manifest_path, payload) in enumerate(loaded, start=1):
        kind = str(payload.get("kind", "unknown-analysis"))
        sources.append(
            {
                "kind": kind,
                "manifest": relative_to_output(manifest_path, output),
                "source_count": payload.get("source_count", len(payload.get("items", []))),
            }
        )
        for item in payload.get("items", []):
            original_id = str(item.get("id") or f"item-{len(items) + 1}")
            merged_id = original_id
            if merged_id in used_ids:
                base_id = f"source-{index}-{original_id}"
                merged_id = base_id
                suffix = 2
                while merged_id in used_ids:
                    merged_id = f"{base_id}-{suffix}"
                    suffix += 1
            used_ids.add(merged_id)
            merged = {**item, "id": merged_id, "origin_kind": kind}
            if merged_id != original_id:
                merged["analysis_id"] = original_id
            analysis_ref = item.get("analysis")
            if analysis_ref:
                analysis_path = Path(analysis_ref)
                if not analysis_path.is_absolute():
                    analysis_path = manifest_path.parent / analysis_path
                merged["analysis"] = relative_to_output(analysis_path, output)
            items.append(merged)
    counts = status_counts(items)
    manifest = {
        "schema_version": 1,
        "kind": "mixed-analysis",
        "generated_at": utc_now(),
        "local_only": True,
        "source_count": len(items),
        **counts,
        "status_scope": "combined_deterministic_evidence_only",
        "distillation_status": "evidence_ready",
        "source_manifests": sources,
        "items": items,
        "semantic_review_required": [
            "map relationships and dependencies across source types",
            "resolve contradictions before combining rules",
            "preserve modality-specific evidence locators",
            "build one mixed profile only when the sources describe the same system or objective",
        ],
    }
    atomic_write_json(output / "manifest.json", manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 1 if counts["failed_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
