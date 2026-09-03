#!/usr/bin/env python3
"""Register host-captured evidence for sources without a portable local extractor."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from kunpeng_common import (
    atomic_write_json,
    configure_utf8,
    prepare_output,
    sampled_fingerprint,
    slugify,
    utc_now,
)


SOURCE_TYPES = (
    "website", "app", "ui", "brand", "repository", "course", "mixed", "other"
)
SENSITIVE_NAMES = {
    ".env", ".env.local", ".env.production", "credentials.json", "secrets.json",
    "id_rsa", "id_ed25519", ".npmrc", ".pypirc", "cookies.json",
}
SENSITIVE_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".jks", ".keystore"}
REVIEW_DIMENSIONS = {
    "website": [
        "page hierarchy and primary user paths", "interaction states and feedback",
        "responsive behavior", "visual system", "observable service boundaries",
    ],
    "app": [
        "onboarding permissions and navigation", "primary task flows", "state recovery",
        "platform conventions", "offline or failure behavior",
    ],
    "ui": [
        "layout and hierarchy", "component states", "interaction and motion",
        "responsive and accessibility behavior", "content and data relationships",
    ],
    "brand": [
        "identity system", "color typography and imagery roles", "cross-format consistency",
        "content variables", "protected or source-specific assets",
    ],
    "repository": [
        "implemented behavior", "architecture and data flow", "tests and failure handling",
        "external boundaries", "tradeoffs and transferable methods",
    ],
    "course": [
        "learning objectives", "concept dependencies", "teaching sequence", "exercises",
        "feedback and assessment", "transfer limits",
    ],
    "mixed": [
        "cross-source relationships", "shared mechanisms", "contradictions",
        "dependencies", "source-specific variables",
    ],
    "other": [
        "purpose and structure", "observable mechanisms", "conditions and parameters",
        "exceptions", "transfer limits",
    ],
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inventory screenshots, recordings, exports, or notes captured by the host agent."
    )
    parser.add_argument("source", type=Path, help="A captured artifact file or directory.")
    parser.add_argument("--source-type", choices=SOURCE_TYPES, required=True)
    parser.add_argument("--source-label", required=True)
    parser.add_argument("--source-url", help="Optional public URL; query and fragment are removed.")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-files", type=int, default=2000)
    return parser.parse_args()


def is_sensitive(path: Path, root: Path) -> bool:
    relative = path.relative_to(root) if root.is_dir() else Path(path.name)
    names = [part.casefold() for part in relative.parts]
    return (
        any(name in SENSITIVE_NAMES or name.startswith(".env.") for name in names)
        or path.suffix.casefold() in SENSITIVE_SUFFIXES
    )


def artifacts(source: Path, maximum: int) -> tuple[list[Path], list[str]]:
    if source.is_file():
        root = source.parent
        candidates = [source]
    elif source.is_dir():
        root = source
        candidates = sorted(
            (path for path in source.rglob("*") if path.is_file() and not path.is_symlink()),
            key=lambda path: path.as_posix().casefold(),
        )
    else:
        raise SystemExit(f"Captured evidence does not exist: {source}")
    kept: list[Path] = []
    skipped: list[str] = []
    for path in candidates:
        relative = path.relative_to(root).as_posix() if root.is_dir() else path.name
        if is_sensitive(path, root):
            skipped.append(relative)
            continue
        if any(part.startswith(".") for part in Path(relative).parts):
            continue
        kept.append(path)
        if len(kept) > max(1, maximum):
            raise SystemExit(
                f"Captured evidence exceeds --max-files={maximum}; narrow it or raise the limit."
            )
    if not kept:
        raise SystemExit("No non-sensitive captured artifacts were found.")
    return kept, skipped


def safe_url(value: str | None) -> str | None:
    if not value:
        return None
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise SystemExit("--source-url must be a public http(s) URL.")
    if parts.username or parts.password:
        raise SystemExit("--source-url must not contain embedded credentials.")
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def evidence_id(label: str, paths: list[Path]) -> str:
    digest = hashlib.sha256(label.encode("utf-8"))
    for path in paths:
        digest.update(path.name.encode("utf-8", errors="replace"))
        digest.update(sampled_fingerprint(path).encode("ascii"))
    return f"{slugify(label)}-{digest.hexdigest()[:10]}"


def capture_log_summary(paths: list[Path]) -> dict[str, Any]:
    logs = [path for path in paths if path.name.casefold() == "capture-log.json"]
    if not logs:
        return {
            "status": "missing",
            "note": "No capture-log.json was supplied; interaction coverage must be reconstructed during review.",
        }
    try:
        payload = json.loads(logs[0].read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return {"status": "invalid", "error": " ".join(str(exc).splitlines())[:300]}
    observations = payload.get("observations", []) if isinstance(payload, dict) else []
    valid: list[dict[str, Any]] = []
    missing_artifacts: list[str] = []
    allowed_artifacts = {path.resolve() for path in paths}
    for item in observations:
        if not isinstance(item, dict):
            continue
        action = str(item.get("action", "")).strip()
        observation = str(item.get("observation", "")).strip()
        artifact = str(item.get("artifact", "")).strip()
        artifact_path = (logs[0].parent / artifact).resolve() if artifact else None
        artifact_allowed = bool(artifact_path and artifact_path in allowed_artifacts)
        if action and observation and artifact and artifact_allowed and artifact_path.exists():
            valid.append(item)
        elif artifact:
            missing_artifacts.append(artifact)
    return {
        "status": "available" if valid and len(valid) == len(observations) else "partial" if valid else "invalid",
        "observation_count": len(valid),
        "declared_observation_count": len(observations),
        "missing_artifacts": missing_artifacts,
        "declared_environment": payload.get("environment") if isinstance(payload, dict) else None,
    }


def main() -> int:
    configure_utf8()
    args = parse_args()
    label = " ".join(args.source_label.split())
    if len(label) < 2:
        raise SystemExit("--source-label must contain a meaningful label.")
    source = args.source.resolve()
    files, skipped = artifacts(source, args.max_files)
    root = source if source.is_dir() else source.parent
    public_url = safe_url(args.source_url)
    output_target = args.output.resolve()
    if source.is_dir() and (output_target == source or source in output_target.parents):
        raise SystemExit("Evidence output must be outside the captured artifact directory.")
    output = prepare_output(args.output, False)
    source_id = evidence_id(label, files)
    records = []
    for path in files:
        records.append(
            {
                "path": Path(os.path.relpath(path, output)).as_posix(),
                "source_relative_path": path.relative_to(root).as_posix(),
                "bytes": path.stat().st_size,
                "extension": path.suffix.casefold() or "<none>",
                "fingerprint": sampled_fingerprint(path),
            }
        )
    semantic_review_required = REVIEW_DIMENSIONS[args.source_type]
    analysis = {
        "schema_version": 1,
        "id": source_id,
        "status": "complete",
        "status_scope": "host_capture_inventory_only",
        "extraction_status": "complete",
        "distillation_status": "evidence_ready",
        "source": {
            "label": label,
            "type": args.source_type,
            "public_url": public_url,
        },
        "artifact_count": len(records),
        "artifacts": records,
        "capture_log": capture_log_summary(files),
        "sensitive_files_skipped": skipped,
        "host_review_required": semantic_review_required,
        "semantic_review_required": semantic_review_required,
        "limitations": [
            "This command inventories evidence already captured by the host; it does not browse or operate the source.",
            "A file fingerprint proves artifact identity, not the truth of a semantic observation.",
            "Missing states, pages, permissions, or interactions must remain explicit coverage gaps.",
        ],
    }
    atomic_write_json(output / "analysis.json", analysis)
    manifest = {
        "schema_version": 1,
        "kind": f"{args.source_type}-analysis",
        "generated_at": utc_now(),
        "local_only": True,
        "source_count": 1,
        "complete_count": 1,
        "degraded_count": 0,
        "partial_count": 0,
        "failed_count": 0,
        "not_applicable_count": 0,
        "status_scope": "host_capture_inventory_only",
        "distillation_status": "evidence_ready",
        "items": [
            {
                "id": source_id,
                "source": analysis["source"]["label"],
                "source_type": args.source_type,
                "status": "complete",
                "extraction_status": "complete",
                "distillation_status": "evidence_ready",
                "analysis": "analysis.json",
                "host_review_required": analysis["host_review_required"],
            }
        ],
    }
    atomic_write_json(output / "manifest.json", manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
