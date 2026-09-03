#!/usr/bin/env python3
"""Shared semantic-card, profile, and evaluation contracts for Kunpeng."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


PLACEHOLDERS = {
    "", "-", "n/a", "na", "none", "null", "todo", "tbd", "unknown",
    "无", "未知", "待补充", "待确认", "暂无", "不详", "未填写",
}
PATTERN_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,127}$")
SOURCE_TYPES = {
    "repository", "website", "app", "ui", "image", "brand", "video",
    "audio", "article", "document", "book", "course", "mixed", "other",
}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def is_substantive(value: Any, minimum: int = 2) -> bool:
    if not isinstance(value, str):
        return False
    compact = " ".join(value.split()).strip()
    return len(compact) >= minimum and compact.casefold() not in PLACEHOLDERS


def nonempty_list(value: Any) -> bool:
    return isinstance(value, list) and bool(value)


def validate_card(card: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(card, dict):
        return ["card must be a JSON object"]
    if card.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    if not is_substantive(card.get("source_id"), 3):
        errors.append("source_id is missing")
    if card.get("source_type") not in SOURCE_TYPES:
        errors.append("source_type is unsupported")
    if not is_substantive(card.get("source_label"), 1):
        errors.append("source_label is missing")
    if not is_substantive(card.get("analysis_artifact"), 1):
        errors.append("analysis_artifact is missing")
    review_status = card.get("review_status")
    if review_status not in {"complete", "excluded"}:
        errors.append("review_status must be complete or excluded")
    if not is_substantive(card.get("summary"), 12):
        errors.append("summary must contain a substantive semantic summary")

    for field in ("variables", "exceptions", "limitations"):
        value = card.get(field, [])
        if not isinstance(value, list):
            errors.append(f"{field} must be a list")
        elif any(not is_substantive(item, 2) for item in value):
            errors.append(f"{field} contains an empty or placeholder item")

    if review_status == "excluded":
        if not is_substantive(card.get("exclusion_reason"), 8):
            errors.append("excluded card requires a substantive exclusion_reason")
        if card.get("patterns") not in ([], None):
            errors.append("excluded card must not contain patterns")
        return errors

    patterns = card.get("patterns")
    if not nonempty_list(patterns):
        errors.append("patterns must contain at least one evidenced pattern")
        patterns = []
    pattern_keys: set[str] = set()
    for index, pattern in enumerate(patterns):
        prefix = f"patterns[{index}]"
        if not isinstance(pattern, dict):
            errors.append(f"{prefix} must be an object")
            continue
        key = pattern.get("key")
        if not isinstance(key, str) or not PATTERN_KEY_RE.fullmatch(key):
            errors.append(f"{prefix}.key must be a stable lowercase identifier")
        elif key in pattern_keys:
            errors.append(f"{prefix}.key duplicates another pattern in this card")
        else:
            pattern_keys.add(key)
        if not is_substantive(pattern.get("dimension"), 2):
            errors.append(f"{prefix}.dimension is missing")
        if not is_substantive(pattern.get("statement"), 8):
            errors.append(f"{prefix}.statement is not substantive")
        if not is_substantive(pattern.get("mechanism"), 8):
            errors.append(f"{prefix}.mechanism is not substantive")
        if not is_substantive(pattern.get("trigger"), 3):
            errors.append(f"{prefix}.trigger is missing")
        if not is_substantive(pattern.get("boundary"), 3):
            errors.append(f"{prefix}.boundary is missing")
        if not isinstance(pattern.get("parameters"), dict):
            errors.append(f"{prefix}.parameters must be an object")
        confidence = pattern.get("confidence")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
            errors.append(f"{prefix}.confidence must be between 0 and 1")
        scope = pattern.get("scope", "global")
        if scope not in {"global", "stage", "local", "conditional", "exception"}:
            errors.append(f"{prefix}.scope is invalid")
        if not isinstance(pattern.get("transferable"), bool):
            errors.append(f"{prefix}.transferable must be true or false")
        evidence = pattern.get("evidence")
        if not nonempty_list(evidence):
            errors.append(f"{prefix}.evidence must contain at least one observation")
            evidence = []
        for evidence_index, item in enumerate(evidence):
            evidence_prefix = f"{prefix}.evidence[{evidence_index}]"
            if not isinstance(item, dict):
                errors.append(f"{evidence_prefix} must be an object")
                continue
            if not is_substantive(item.get("artifact"), 1):
                errors.append(f"{evidence_prefix}.artifact is missing")
            if not is_substantive(item.get("locator"), 1):
                errors.append(f"{evidence_prefix}.locator is missing")
            if not is_substantive(item.get("observation"), 6):
                errors.append(f"{evidence_prefix}.observation is not substantive")

    return errors


def _validate_string_list(
    owner: dict[str, Any], field: str, errors: list[str], minimum_items: int = 1
) -> None:
    value = owner.get(field)
    if not isinstance(value, list) or len(value) < minimum_items:
        errors.append(f"{field} must contain at least {minimum_items} item(s)")
        return
    if any(not is_substantive(item, 4) for item in value):
        errors.append(f"{field} contains an empty or placeholder item")


def validate_profile(profile: Any, require_reviewed: bool = True) -> list[str]:
    errors: list[str] = []
    if not isinstance(profile, dict):
        return ["profile must be a JSON object"]
    if profile.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    if not is_substantive(profile.get("profile_id"), 3):
        errors.append("profile_id is missing")
    if not is_substantive(profile.get("domain"), 2):
        errors.append("domain is missing")
    if (
        isinstance(profile.get("source_count"), bool)
        or not isinstance(profile.get("source_count"), int)
        or profile.get("source_count", 0) < 1
    ):
        errors.append("source_count must be at least 1")
    source_ids = profile.get("source_ids")
    if not nonempty_list(source_ids) or any(not is_substantive(item, 3) for item in source_ids):
        errors.append("source_ids must contain substantive identifiers")
        source_ids = []
    elif len(source_ids) != len(set(source_ids)):
        errors.append("source_ids contains duplicates")
    if isinstance(profile.get("source_count"), int) and len(source_ids) != profile.get("source_count"):
        errors.append("source_count must equal the number of source_ids")
    excluded_sources = profile.get("excluded_sources", [])
    if not isinstance(excluded_sources, list):
        errors.append("excluded_sources must be a list")
    else:
        excluded_ids: set[str] = set()
        for index, item in enumerate(excluded_sources):
            prefix = f"excluded_sources[{index}]"
            if not isinstance(item, dict):
                errors.append(f"{prefix} must be an object")
                continue
            source_id = item.get("source_id")
            if not is_substantive(source_id, 3):
                errors.append(f"{prefix}.source_id is missing")
            elif source_id in excluded_ids or source_id in set(source_ids):
                errors.append(f"{prefix}.source_id is duplicated or retained")
            else:
                excluded_ids.add(str(source_id))
            if not is_substantive(item.get("reason"), 8):
                errors.append(f"{prefix}.reason is not substantive")
    if require_reviewed and profile.get("review_status") != "reviewed":
        errors.append("review_status must be reviewed")
    elif profile.get("review_status") not in {"draft", "reviewed"}:
        errors.append("review_status must be draft or reviewed")
    if require_reviewed and not is_substantive(profile.get("review_summary"), 12):
        errors.append("review_summary must explain the semantic review decisions")

    pattern_fields = ("stable_patterns", "conditional_patterns", "observations")
    total_patterns = 0
    seen_keys: set[str] = set()
    known_source_ids = set(str(item) for item in source_ids)
    for field in pattern_fields:
        value = profile.get(field, [])
        if not isinstance(value, list):
            errors.append(f"{field} must be a list")
            continue
        total_patterns += len(value)
        for index, pattern in enumerate(value):
            prefix = f"{field}[{index}]"
            if not isinstance(pattern, dict):
                errors.append(f"{prefix} must be an object")
                continue
            key = pattern.get("key")
            if not isinstance(key, str) or not PATTERN_KEY_RE.fullmatch(key):
                errors.append(f"{prefix}.key must be a stable lowercase identifier")
            elif key in seen_keys:
                errors.append(f"{prefix}.key duplicates another profile pattern")
            else:
                seen_keys.add(str(key))
            if not is_substantive(pattern.get("statement"), 8):
                errors.append(f"{prefix}.statement is not substantive")
            if not is_substantive(pattern.get("dimension"), 2):
                errors.append(f"{prefix}.dimension is missing")
            if require_reviewed and pattern.get("statement_variants"):
                errors.append(f"{prefix}.statement_variants must be resolved before review")
            support_count = pattern.get("support_count")
            if isinstance(support_count, bool) or not isinstance(support_count, int) or support_count < 1:
                errors.append(f"{prefix}.support_count must be at least 1")
            pattern_source_ids = pattern.get("source_ids")
            if not nonempty_list(pattern_source_ids) or any(
                not is_substantive(item, 3) for item in pattern_source_ids
            ):
                errors.append(f"{prefix}.source_ids must contain substantive identifiers")
                pattern_source_ids = []
            elif len(set(pattern_source_ids)) != len(pattern_source_ids):
                errors.append(f"{prefix}.source_ids contains duplicates")
            elif not set(str(item) for item in pattern_source_ids).issubset(known_source_ids):
                errors.append(f"{prefix}.source_ids contains an unknown source")
            if isinstance(support_count, int) and not isinstance(support_count, bool) and support_count != len(set(pattern_source_ids)):
                errors.append(f"{prefix}.support_count must equal unique source_ids")
            if field == "stable_patterns" and isinstance(support_count, int) and support_count < 2:
                errors.append(f"{prefix} needs support from at least two sources")
            support_share = pattern.get("support_share")
            expected_share = len(set(pattern_source_ids)) / max(1, len(known_source_ids))
            if (
                isinstance(support_share, bool)
                or not isinstance(support_share, (int, float))
                or not 0 < support_share <= 1
            ):
                errors.append(f"{prefix}.support_share must be between 0 and 1")
            elif abs(float(support_share) - expected_share) > 0.001:
                errors.append(f"{prefix}.support_share does not match profile sources")
            mean_confidence = pattern.get("mean_confidence")
            if (
                isinstance(mean_confidence, bool)
                or not isinstance(mean_confidence, (int, float))
                or not 0 <= mean_confidence <= 1
            ):
                errors.append(f"{prefix}.mean_confidence must be between 0 and 1")
            for list_field in ("mechanisms", "triggers", "boundaries"):
                field_value = pattern.get(list_field)
                if not nonempty_list(field_value) or any(
                    not is_substantive(item, 3) for item in field_value
                ):
                    errors.append(f"{prefix}.{list_field} must contain substantive values")
            parameters = pattern.get("parameters")
            if not isinstance(parameters, list) or any(not isinstance(item, dict) for item in parameters):
                errors.append(f"{prefix}.parameters must be a list of objects")
            evidence = pattern.get("evidence")
            if not nonempty_list(evidence):
                errors.append(f"{prefix}.evidence is empty")
                evidence = []
            evidence_source_ids: set[str] = set()
            for evidence_index, item in enumerate(evidence):
                evidence_prefix = f"{prefix}.evidence[{evidence_index}]"
                if not isinstance(item, dict):
                    errors.append(f"{evidence_prefix} must be an object")
                    continue
                if str(item.get("source_id")) not in known_source_ids:
                    errors.append(f"{evidence_prefix}.source_id is unknown")
                else:
                    evidence_source_ids.add(str(item.get("source_id")))
                for evidence_field, minimum in (("artifact", 1), ("locator", 1), ("observation", 6)):
                    if not is_substantive(item.get(evidence_field), minimum):
                        errors.append(f"{evidence_prefix}.{evidence_field} is not substantive")
            missing_evidence_ids = set(str(item) for item in pattern_source_ids) - evidence_source_ids
            if missing_evidence_ids:
                errors.append(
                    f"{prefix}.evidence is missing source(s): "
                    + ", ".join(sorted(missing_evidence_ids))
                )
    if total_patterns < 1:
        errors.append("profile contains no patterns")

    contract = profile.get("generation_contract")
    if not isinstance(contract, dict):
        errors.append("generation_contract is missing")
    else:
        if require_reviewed and contract.get("review_status") != "reviewed":
            errors.append("generation_contract.review_status must be reviewed")
        elif contract.get("review_status") not in {"draft", "reviewed"}:
            errors.append("generation_contract.review_status must be draft or reviewed")
        if contract.get("mode") not in {"faithful", "transfer"}:
            errors.append("generation_contract.mode must be faithful or transfer")
        if not is_substantive(contract.get("medium"), 2):
            errors.append("generation_contract.medium is missing")
        if not is_substantive(contract.get("target_effect"), 8):
            errors.append("generation_contract.target_effect is not substantive")
        for field in ("invariants", "variables", "sequence", "negative_constraints", "acceptance"):
            _validate_string_list(contract, field, errors)
    return errors


def validate_evaluation(evaluation: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(evaluation, dict):
        return ["evaluation must be a JSON object"]
    if evaluation.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    if evaluation.get("review_status") != "complete":
        errors.append("review_status must be complete")
    if not is_substantive(evaluation.get("candidate"), 1):
        errors.append("candidate is missing")
    if not is_substantive(evaluation.get("profile_id"), 3):
        errors.append("profile_id is missing")
    if not is_substantive(evaluation.get("objective"), 8):
        errors.append("objective is missing")
    evidence_artifacts = evaluation.get("evidence_artifacts")
    if not nonempty_list(evidence_artifacts):
        errors.append("evidence_artifacts must contain candidate re-analysis artifacts")
        evidence_artifacts = []
    elif any(not is_substantive(item, 1) for item in evidence_artifacts):
        errors.append("evidence_artifacts contains an empty or placeholder item")
    elif len({str(item) for item in evidence_artifacts}) != len(evidence_artifacts):
        errors.append("evidence_artifacts contains duplicates")
    dimensions = evaluation.get("dimensions")
    if not isinstance(dimensions, list) or len(dimensions) < 3:
        errors.append("dimensions must contain at least three independent checks")
        dimensions = []
    failed: list[str] = []
    unresolved_hard: list[str] = []
    passed = 0
    names: set[str] = set()
    for index, dimension in enumerate(dimensions):
        prefix = f"dimensions[{index}]"
        if not isinstance(dimension, dict):
            errors.append(f"{prefix} must be an object")
            continue
        name = dimension.get("name")
        if not is_substantive(name, 2):
            errors.append(f"{prefix}.name is missing")
        elif str(name).casefold() in names:
            errors.append(f"{prefix}.name duplicates another dimension")
        else:
            names.add(str(name).casefold())
        verdict_value = dimension.get("verdict")
        if verdict_value not in {"pass", "fail", "not_applicable"}:
            errors.append(f"{prefix}.verdict is invalid")
        evidence = dimension.get("evidence")
        if not nonempty_list(evidence):
            errors.append(f"{prefix}.evidence must contain at least one located observation")
            evidence = []
        for evidence_index, item in enumerate(evidence):
            evidence_prefix = f"{prefix}.evidence[{evidence_index}]"
            if not isinstance(item, dict):
                errors.append(f"{evidence_prefix} must be an object")
                continue
            if not is_substantive(item.get("artifact"), 1):
                errors.append(f"{evidence_prefix}.artifact is missing")
            if not is_substantive(item.get("locator"), 1):
                errors.append(f"{evidence_prefix}.locator is missing")
            if not is_substantive(item.get("observation"), 6):
                errors.append(f"{evidence_prefix}.observation is not substantive")
        if not is_substantive(dimension.get("notes"), 4):
            errors.append(f"{prefix}.notes is not substantive")
        if not isinstance(dimension.get("hard_constraint"), bool):
            errors.append(f"{prefix}.hard_constraint must be true or false")
        elif dimension.get("hard_constraint") and verdict_value != "pass":
            unresolved_hard.append(str(name or index))
        if verdict_value == "fail":
            failed.append(str(dimension.get("name", index)))
        elif verdict_value == "pass":
            passed += 1
    verdict = evaluation.get("overall_verdict")
    if verdict not in {"pass", "fail"}:
        errors.append("overall_verdict must be pass or fail")
    if failed and verdict == "pass":
        errors.append("overall_verdict cannot pass while a dimension fails")
    if unresolved_hard and verdict == "pass":
        errors.append(
            "overall_verdict cannot pass while a hard constraint is unresolved: "
            + ", ".join(unresolved_hard)
        )
    if verdict == "pass" and passed < 3:
        errors.append("passing evaluation requires at least three passed dimensions")
    revisions = evaluation.get("required_revisions")
    if not isinstance(revisions, list):
        errors.append("required_revisions must be a list")
    elif any(not is_substantive(item, 4) for item in revisions):
        errors.append("required_revisions contains an empty or placeholder item")
    if verdict == "fail" and not nonempty_list(revisions):
        errors.append("failed evaluation must contain required_revisions")
    if verdict == "pass" and nonempty_list(revisions):
        errors.append("passing evaluation cannot contain required_revisions")
    return errors
