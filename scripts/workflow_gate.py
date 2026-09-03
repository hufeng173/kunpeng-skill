#!/usr/bin/env python3
"""Persist and enforce Kunpeng's evidence-to-profile-to-evaluation workflow."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from kunpeng_common import ANALYSIS_STATUSES, atomic_write_json, configure_utf8, prepare_output, utc_now
from profile_contract import is_substantive, load_json, validate_card, validate_evaluation, validate_profile


ARTIFACT_KEYS = {"manifest", "cards", "profile", "candidate", "evaluation"}
EVIDENCE_STATUSES = {"complete", "degraded", "partial"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Initialize, register, or check a Kunpeng workflow.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    init = subparsers.add_parser("init")
    init.add_argument("--output", type=Path, required=True)
    init.add_argument("--objective", required=True)
    init.add_argument("--mode", choices=("distillation", "application"), default="distillation")
    init.add_argument("--domains", default="mixed", help="Comma-separated source domains.")

    register = subparsers.add_parser("register")
    register.add_argument("--run", type=Path, required=True)
    register.add_argument("--type", choices=tuple(sorted(ARTIFACT_KEYS)), required=True)
    register.add_argument("--path", type=Path, required=True)

    check = subparsers.add_parser("check")
    check.add_argument("--run", type=Path, required=True)
    return parser.parse_args()


def run_file(path: Path) -> Path:
    resolved = path.resolve()
    return resolved / "run.json" if resolved.is_dir() else resolved


def relative_reference(path: Path, root: Path) -> str:
    return Path(os.path.relpath(path.resolve(), root.resolve())).as_posix()


def resolve_reference(value: str | None, root: Path) -> Path | None:
    if not value:
        return None
    path = Path(value)
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def path_is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def nonempty_artifact(path: Path) -> bool:
    if path.is_file():
        return path.stat().st_size > 0
    if path.is_dir():
        return any(item.is_file() and item.stat().st_size > 0 for item in path.rglob("*"))
    return False


def contains_evidence_ready(path: Path) -> bool:
    if path.is_file():
        candidates = [path]
    else:
        preferred = [path / "manifest.json", path / "analysis.json"]
        remaining = [item for item in sorted(path.rglob("*.json")) if item not in preferred]
        candidates = [item for item in preferred if item.is_file()] + remaining[:200]
    for candidate in candidates:
        if candidate.suffix.casefold() != ".json" or not candidate.is_file():
            continue
        try:
            payload = load_json(candidate)
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict) or payload.get("distillation_status") != "evidence_ready":
            continue
        has_scope = is_substantive(payload.get("status_scope"), 3)
        is_manifest = (
            is_substantive(payload.get("kind"), 3)
            and isinstance(payload.get("items"), list)
            and bool(payload.get("items"))
        )
        is_analysis = (
            is_substantive(payload.get("id"), 3)
            and isinstance(payload.get("semantic_review_required"), list)
            and bool(payload.get("semantic_review_required"))
        )
        if has_scope and (is_manifest or is_analysis):
            return True
    return False


def validate_run_header(run: Any) -> list[str]:
    if not isinstance(run, dict):
        return ["run.json must contain an object"]
    errors: list[str] = []
    if run.get("schema_version") != 1:
        errors.append("run schema_version must be 1")
    if run.get("mode") not in {"distillation", "application"}:
        errors.append("run mode must be distillation or application")
    if not is_substantive(run.get("objective"), 8):
        errors.append("run objective is missing or too short")
    if (
        not isinstance(run.get("domains"), list)
        or not run.get("domains")
        or any(not is_substantive(item, 2) for item in run.get("domains", []))
    ):
        errors.append("run domains must contain at least one domain")
    artifacts = run.get("artifacts")
    if not isinstance(artifacts, dict):
        errors.append("run artifacts must be an object")
    elif not isinstance(artifacts.get("manifests"), list):
        errors.append("run artifacts.manifests must be a list")
    return errors


def initialize(args: argparse.Namespace) -> int:
    if len(" ".join(args.objective.split())) < 8:
        raise SystemExit("Objective must describe a concrete distillation or application goal.")
    domains = [item.strip() for item in args.domains.split(",") if item.strip()]
    if not domains:
        raise SystemExit("At least one source domain is required.")
    output = prepare_output(args.output, False)
    for directory in ("manifests", "cards", "profiles", "candidates", "evaluations"):
        (output / directory).mkdir(parents=True, exist_ok=True)
    run = {
        "schema_version": 1,
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "mode": args.mode,
        "objective": " ".join(args.objective.split()),
        "domains": domains,
        "artifacts": {"manifests": [], "cards": None, "profile": None, "candidate": None, "evaluation": None},
        "stages": {},
        "overall_status": "initialized",
    }
    atomic_write_json(output / "run.json", run)
    print(json.dumps(run, ensure_ascii=False, indent=2))
    return 0


def register(args: argparse.Namespace) -> int:
    path = run_file(args.run)
    if not path.is_file():
        raise SystemExit(f"Run file does not exist: {path}")
    payload = load_json(path)
    run_errors = validate_run_header(payload)
    if run_errors:
        raise SystemExit("Run file is invalid:\n- " + "\n- ".join(run_errors))
    root = path.parent
    artifact = args.path.resolve()
    if not artifact.exists():
        raise SystemExit(f"Artifact does not exist: {artifact}")
    reference = relative_reference(artifact, root)
    artifacts = payload.setdefault("artifacts", {})
    if args.type == "manifest":
        manifests = artifacts.setdefault("manifests", [])
        if reference not in manifests:
            manifests.append(reference)
    else:
        artifacts[args.type] = reference
    payload["updated_at"] = utc_now()
    atomic_write_json(path, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def manifest_gate(paths: list[Path]) -> tuple[bool, list[str], dict[str, Path]]:
    errors: list[str] = []
    source_artifacts: dict[str, Path] = {}
    if not paths:
        return False, ["no analysis manifests registered"], source_artifacts
    for path in paths:
        if not path.is_file():
            errors.append(f"manifest missing: {path}")
            continue
        try:
            payload = load_json(path)
        except Exception as exc:
            errors.append(f"cannot read manifest {path}: {exc}")
            continue
        if not isinstance(payload, dict):
            errors.append(f"manifest is not an object: {path}")
            continue
        schema_version = payload.get("schema_version")
        if isinstance(schema_version, bool) or not isinstance(schema_version, int) or schema_version < 1:
            errors.append(f"manifest schema_version is invalid: {path}")
        if not is_substantive(payload.get("kind"), 3):
            errors.append(f"manifest kind is missing: {path}")
        if not is_substantive(payload.get("status_scope"), 3):
            errors.append(f"manifest status_scope is missing: {path}")
        if payload.get("distillation_status") != "evidence_ready":
            errors.append(f"manifest is not marked evidence_ready: {path}")
        all_items = payload.get("items", [])
        if not isinstance(all_items, list):
            errors.append(f"manifest items is not a list: {path}")
            continue
        if any(not isinstance(item, dict) for item in all_items):
            errors.append(f"manifest contains a non-object item: {path}")
        if payload.get("source_count") != len(all_items):
            errors.append(f"manifest source_count does not match items: {path}")
        if any(item.get("status") == "failed" for item in all_items if isinstance(item, dict)):
            errors.append(f"manifest contains failed evidence items: {path}")
        items = [
            item for item in all_items
            if isinstance(item, dict) and item.get("status") != "failed"
        ]
        if not items:
            errors.append(f"manifest has no successful evidence items: {path}")
        for item in items:
            item_id = str(item.get("id", "")).strip()
            if not item_id:
                errors.append(f"manifest item has no id: {path}")
                continue
            if item_id in source_artifacts:
                errors.append(f"duplicate source id across manifests: {item_id}")
            if item.get("status") not in ANALYSIS_STATUSES:
                errors.append(f"manifest item {item_id} has invalid extraction status")
            elif item.get("status") not in EVIDENCE_STATUSES:
                errors.append(f"manifest item {item_id} has no usable evidence")
            if item.get("distillation_status") != "evidence_ready":
                errors.append(f"manifest item {item_id} is not marked evidence_ready")
            analysis_value = item.get("analysis")
            if not is_substantive(analysis_value, 1):
                errors.append(f"manifest item {item_id} has no analysis artifact")
                continue
            analysis_path = Path(str(analysis_value))
            if not analysis_path.is_absolute():
                analysis_path = path.parent / analysis_path
            analysis_path = analysis_path.resolve()
            source_artifacts[item_id] = analysis_path
            if not analysis_path.is_file():
                errors.append(f"analysis artifact is missing for {item_id}: {analysis_path}")
                continue
            try:
                analysis = load_json(analysis_path)
            except Exception as exc:
                errors.append(f"cannot read analysis artifact for {item_id}: {exc}")
                continue
            if not isinstance(analysis, dict):
                errors.append(f"analysis artifact is not an object: {item_id}")
                continue
            analysis_schema = analysis.get("schema_version")
            if (
                isinstance(analysis_schema, bool)
                or not isinstance(analysis_schema, int)
                or analysis_schema < 1
            ):
                errors.append(f"analysis schema_version is invalid: {item_id}")
            expected_analysis_id = str(item.get("analysis_id") or item_id)
            if str(analysis.get("id")) != expected_analysis_id:
                errors.append(f"analysis id does not match manifest item: {item_id}")
            if analysis.get("status") != item.get("status"):
                errors.append(f"analysis extraction status does not match manifest item: {item_id}")
            if not is_substantive(analysis.get("status_scope"), 3):
                errors.append(f"analysis status_scope is missing: {item_id}")
            if analysis.get("distillation_status") != "evidence_ready":
                errors.append(f"analysis artifact is not evidence_ready: {item_id}")
            review_tasks = analysis.get("semantic_review_required")
            if not isinstance(review_tasks, list) or not review_tasks:
                errors.append(f"analysis artifact has no semantic review requirements: {item_id}")
    return not errors, errors, source_artifacts


def cards_gate(
    path: Path | None, expected_artifacts: dict[str, Path]
) -> tuple[bool, list[str], set[str], dict[str, str], dict[str, dict[str, set[tuple[str, str]]]]]:
    if path is None or not path.is_dir():
        return False, ["semantic cards directory is not registered or missing"], set(), {}, {}
    errors: list[str] = []
    reviewed_ids: set[str] = set()
    retained_ids: set[str] = set()
    excluded: dict[str, str] = {}
    pattern_evidence: dict[str, dict[str, set[tuple[str, str]]]] = {}
    for card_path in sorted(path.rglob("*.json")):
        try:
            card = load_json(card_path)
        except Exception as exc:
            errors.append(f"{card_path.name}: cannot read JSON ({exc})")
            continue
        card_errors = validate_card(card)
        errors.extend(f"{card_path.name}: {error}" for error in card_errors)
        if not card_errors:
            source_id = str(card.get("source_id"))
            if source_id in reviewed_ids:
                errors.append(f"duplicate semantic card source_id: {source_id}")
                continue
            reviewed_ids.add(source_id)
            expected_analysis = expected_artifacts.get(source_id)
            declared_analysis = resolve_reference(str(card.get("analysis_artifact")), card_path.parent)
            if expected_analysis is None or declared_analysis != expected_analysis.resolve():
                errors.append(f"{card_path.name}: analysis_artifact does not match the registered manifest")
            if card.get("review_status") == "excluded":
                excluded[source_id] = str(card.get("exclusion_reason"))
            else:
                retained_ids.add(source_id)
                source_patterns: dict[str, set[tuple[str, str]]] = {}
                for pattern in card.get("patterns", []):
                    citations: set[tuple[str, str]] = set()
                    for evidence in pattern.get("evidence", []):
                        artifact_value = str(evidence.get("artifact", ""))
                        artifact_path = resolve_reference(artifact_value, card_path.parent)
                        if artifact_path is None or not nonempty_artifact(artifact_path):
                            errors.append(
                                f"{card_path.name}: evidence artifact is missing or empty: {artifact_value}"
                            )
                        elif artifact_path.resolve() == card_path.resolve():
                            errors.append(f"{card_path.name}: a card cannot cite itself as source evidence")
                        citations.add((artifact_value, str(evidence.get("locator", ""))))
                    source_patterns[str(pattern.get("key"))] = citations
                pattern_evidence[source_id] = source_patterns
    expected_ids = set(expected_artifacts)
    missing = sorted(expected_ids - reviewed_ids)
    if missing:
        errors.append("missing completed cards for: " + ", ".join(missing))
    if not retained_ids:
        errors.append("no retained completed semantic cards found")
    extras = sorted(reviewed_ids - expected_ids)
    if extras:
        errors.append("cards are not backed by registered manifests: " + ", ".join(extras))
    return not errors, errors, retained_ids, excluded, pattern_evidence


def profile_binding_errors(
    profile: dict[str, Any],
    pattern_evidence: dict[str, dict[str, set[tuple[str, str]]]],
) -> list[str]:
    """Ensure profile support and citations are inherited from completed source cards."""
    errors: list[str] = []
    for group in ("stable_patterns", "conditional_patterns", "observations"):
        for index, pattern in enumerate(profile.get(group, [])):
            if not isinstance(pattern, dict):
                continue
            prefix = f"{group}[{index}]"
            key = str(pattern.get("key", ""))
            for source_id in pattern.get("source_ids", []):
                source_key_evidence = pattern_evidence.get(str(source_id), {}).get(key)
                if source_key_evidence is None:
                    errors.append(
                        f"{prefix} claims {source_id} supports {key}, but its completed card does not"
                    )
            for citation_index, citation in enumerate(pattern.get("evidence", [])):
                if not isinstance(citation, dict):
                    continue
                source_id = str(citation.get("source_id", ""))
                pair = (str(citation.get("artifact", "")), str(citation.get("locator", "")))
                source_key_evidence = pattern_evidence.get(source_id, {}).get(key, set())
                if pair not in source_key_evidence:
                    errors.append(
                        f"{prefix}.evidence[{citation_index}] is not present in source card {source_id}"
                    )
    return errors


def candidate_gate(path: Path | None) -> tuple[bool, list[str]]:
    if path is None or not path.exists():
        return False, ["candidate is not registered or missing"]
    if path.is_file():
        return (True, []) if path.stat().st_size > 0 else (False, ["candidate file is empty"])
    if path.is_dir():
        populated = any(item.is_file() and item.stat().st_size > 0 for item in path.rglob("*"))
        return (True, []) if populated else (False, ["candidate directory contains no non-empty files"])
    return False, ["candidate path is neither a regular file nor a directory"]


def check(args: argparse.Namespace) -> int:
    path = run_file(args.run)
    if not path.is_file():
        raise SystemExit(f"Run file does not exist: {path}")
    try:
        run = load_json(path)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Cannot read run file: {exc}") from exc
    run_errors = validate_run_header(run)
    if run_errors:
        raise SystemExit("Run file is invalid:\n- " + "\n- ".join(run_errors))
    root = path.parent
    artifacts = run.get("artifacts", {})
    manifest_paths = [resolve_reference(value, root) for value in artifacts.get("manifests", [])]
    evidence_ok, evidence_errors, source_artifacts = manifest_gate([item for item in manifest_paths if item])
    cards_path = resolve_reference(artifacts.get("cards"), root)
    cards_ok, cards_errors, card_ids, excluded_cards, pattern_evidence = (
        cards_gate(cards_path, source_artifacts)
        if evidence_ok else (False, ["evidence gate failed"], set(), {}, {})
    )

    profile_errors: list[str] = []
    profile_path = resolve_reference(artifacts.get("profile"), root)
    if cards_ok and profile_path and profile_path.is_file():
        try:
            profile = load_json(profile_path)
            profile_errors = validate_profile(profile, require_reviewed=True)
            profile_ids = set(str(item) for item in profile.get("source_ids", []))
            missing_profile_ids = card_ids - profile_ids
            extra_profile_ids = profile_ids - card_ids
            if missing_profile_ids:
                profile_errors.append("profile omits reviewed sources: " + ", ".join(sorted(missing_profile_ids)))
            if extra_profile_ids:
                profile_errors.append("profile includes unreviewed sources: " + ", ".join(sorted(extra_profile_ids)))
            profile_excluded = {
                str(item.get("source_id")): str(item.get("reason"))
                for item in profile.get("excluded_sources", [])
                if isinstance(item, dict)
            }
            if profile_excluded != excluded_cards:
                profile_errors.append("profile excluded_sources does not match reviewed exclusions")
            profile_errors.extend(profile_binding_errors(profile, pattern_evidence))
        except Exception as exc:
            profile_errors = [f"cannot read profile: {exc}"]
    else:
        profile_errors = ["reviewed profile is not registered or prior gate failed"]
    profile_ok = not profile_errors

    candidate_path = resolve_reference(artifacts.get("candidate"), root)
    candidate_ok, candidate_errors = candidate_gate(candidate_path)

    evaluation_path = resolve_reference(artifacts.get("evaluation"), root)
    evaluation_errors: list[str] = []
    evaluation_pass = False
    if profile_ok and candidate_ok and evaluation_path and evaluation_path.is_file():
        try:
            evaluation = load_json(evaluation_path)
            evaluation_errors = validate_evaluation(evaluation)
            if evaluation.get("profile_id") != profile.get("profile_id"):
                evaluation_errors.append("evaluation profile_id does not match the registered profile")
            if " ".join(str(evaluation.get("objective", "")).split()) != run.get("objective"):
                evaluation_errors.append("evaluation objective does not match the workflow objective")
            declared_candidate = Path(str(evaluation.get("candidate", "")))
            if not declared_candidate.is_absolute():
                declared_candidate = evaluation_path.parent / declared_candidate
            if not candidate_path or declared_candidate.resolve() != candidate_path.resolve():
                evaluation_errors.append("evaluation candidate does not match the registered candidate")
            declared_evidence_paths: list[Path] = []
            for artifact in evaluation.get("evidence_artifacts", []):
                artifact_path = Path(str(artifact))
                if not artifact_path.is_absolute():
                    artifact_path = evaluation_path.parent / artifact_path
                artifact_path = artifact_path.resolve()
                if artifact_path == evaluation_path.resolve():
                    evaluation_errors.append("evaluation file cannot serve as its own candidate evidence")
                    continue
                if candidate_path and artifact_path == candidate_path.resolve():
                    evaluation_errors.append("candidate cannot serve as its own re-analysis evidence")
                    continue
                if not nonempty_artifact(artifact_path):
                    evaluation_errors.append(f"candidate evidence artifact is missing or empty: {artifact}")
                    continue
                declared_evidence_paths.append(artifact_path)
            if declared_evidence_paths and not any(
                contains_evidence_ready(artifact_path) for artifact_path in declared_evidence_paths
            ):
                evaluation_errors.append(
                    "candidate evidence contains no JSON artifact marked distillation_status=evidence_ready"
                )
            for dimension_index, dimension in enumerate(evaluation.get("dimensions", [])):
                if not isinstance(dimension, dict):
                    continue
                for citation_index, citation in enumerate(dimension.get("evidence", [])):
                    if not isinstance(citation, dict):
                        continue
                    artifact_value = str(citation.get("artifact", ""))
                    cited_path = resolve_reference(artifact_value, evaluation_path.parent)
                    prefix = f"dimensions[{dimension_index}].evidence[{citation_index}]"
                    if cited_path is None or not nonempty_artifact(cited_path):
                        evaluation_errors.append(f"{prefix} artifact is missing or empty: {artifact_value}")
                        continue
                    if not any(
                        cited_path.resolve() == declared.resolve()
                        or (declared.is_dir() and path_is_within(cited_path, declared))
                        for declared in declared_evidence_paths
                    ):
                        evaluation_errors.append(
                            f"{prefix} is not inside a declared candidate evidence artifact"
                        )
            evaluation_pass = not evaluation_errors and evaluation.get("overall_verdict") == "pass"
            if not evaluation_errors and not evaluation_pass:
                evaluation_errors.append("evaluation requires revision before completion")
        except Exception as exc:
            evaluation_errors = [f"cannot read evaluation: {exc}"]
    else:
        evaluation_errors = ["evaluation is not registered or a prerequisite gate failed"]

    application = run.get("mode") == "application"
    stages = {
        "evidence_ready": {"status": "complete" if evidence_ok else "blocked", "errors": evidence_errors},
        "semantic_cards_ready": {"status": "complete" if cards_ok else "blocked", "errors": cards_errors},
        "profile_ready": {"status": "complete" if profile_ok else "blocked", "errors": profile_errors},
        "candidate_created": {
            "status": ("complete" if candidate_ok else "blocked") if application else "not_required",
            "errors": candidate_errors if application else [],
        },
        "evaluated": {
            "status": ("complete" if evaluation_pass else "blocked") if application else "not_required",
            "errors": evaluation_errors if application else [],
        },
    }
    complete = profile_ok if not application else profile_ok and candidate_ok and evaluation_pass
    run["stages"] = stages
    run["overall_status"] = "complete" if complete else "in_progress"
    run["updated_at"] = utc_now()
    atomic_write_json(path, run)
    print(json.dumps(run, ensure_ascii=False, indent=2))
    return 0 if complete else 1


def main() -> int:
    configure_utf8()
    args = parse_args()
    if args.command == "init":
        return initialize(args)
    if args.command == "register":
        return register(args)
    return check(args)


if __name__ == "__main__":
    raise SystemExit(main())
