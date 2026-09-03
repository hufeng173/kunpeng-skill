#!/usr/bin/env python3
"""Aggregate validated semantic cards into an evidence-linked profile draft."""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

from kunpeng_common import atomic_write_json, configure_utf8, slugify, utc_now
from profile_contract import load_json, validate_card


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a cross-source distillation profile draft.")
    parser.add_argument("cards", type=Path, help="Directory containing completed semantic cards.")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--domain", help="Profile domain; inferred from cards when omitted.")
    parser.add_argument("--profile-id", help="Stable profile identifier.")
    parser.add_argument("--min-support-count", type=int, default=2)
    parser.add_argument("--min-support-share", type=float, default=0.5)
    return parser.parse_args()


def unique_strings(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = " ".join(str(value).split()).strip()
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            result.append(text)
    return result


def aggregate_pattern(key: str, occurrences: list[tuple[str, dict[str, Any]]], total: int) -> dict[str, Any]:
    source_ids = sorted({source_id for source_id, _ in occurrences})
    patterns = [pattern for _, pattern in occurrences]
    evidence: list[dict[str, Any]] = []
    for source_id, pattern in occurrences:
        for item in pattern.get("evidence", []):
            evidence.append({"source_id": source_id, **item})
    confidences = [float(pattern.get("confidence", 0.0)) for pattern in patterns]
    statements = unique_strings([pattern.get("statement", "") for pattern in patterns])
    mechanisms = unique_strings([pattern.get("mechanism", "") for pattern in patterns])
    triggers = unique_strings([pattern.get("trigger", "") for pattern in patterns if pattern.get("trigger")])
    boundaries = unique_strings([pattern.get("boundary", "") for pattern in patterns if pattern.get("boundary")])
    return {
        "key": key,
        "dimension": str(patterns[0].get("dimension", "other")),
        "statement": statements[0],
        "statement_variants": statements[1:],
        "mechanisms": mechanisms,
        "triggers": triggers,
        "boundaries": boundaries,
        "parameters": [pattern.get("parameters", {}) for pattern in patterns if pattern.get("parameters")],
        "support_count": len(source_ids),
        "support_share": round(len(source_ids) / max(1, total), 4),
        "mean_confidence": round(statistics.fmean(confidences), 4),
        "source_ids": source_ids,
        "evidence": evidence,
    }


def main() -> int:
    configure_utf8()
    args = parse_args()
    cards_root = args.cards.resolve()
    if not cards_root.is_dir():
        raise SystemExit(f"Semantic cards directory does not exist: {cards_root}")
    output = args.output.resolve()
    if output.exists():
        raise SystemExit(f"Output already exists: {output}")
    if output == cards_root or cards_root in output.parents:
        raise SystemExit("Profile output must be outside the semantic cards directory.")
    card_paths = sorted(path for path in cards_root.rglob("*.json") if path.is_file())
    if not card_paths:
        raise SystemExit("No semantic card JSON files found.")
    cards: list[dict[str, Any]] = []
    excluded_cards: list[dict[str, Any]] = []
    failures: list[str] = []
    seen_source_ids: set[str] = set()
    for path in card_paths:
        try:
            card = load_json(path)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            failures.append(f"{path.name}: cannot read JSON ({exc})")
            continue
        errors = validate_card(card)
        source_id = str(card.get("source_id", ""))
        if not errors and source_id in seen_source_ids:
            errors.append(f"duplicate source_id: {source_id}")
        if errors:
            failures.extend(f"{path.name}: {error}" for error in errors)
        else:
            seen_source_ids.add(source_id)
            if card.get("review_status") == "excluded":
                excluded_cards.append(card)
            else:
                cards.append(card)
    if failures:
        raise SystemExit("Semantic cards are not ready:\n- " + "\n- ".join(failures))
    if not cards:
        raise SystemExit("No retained completed cards remain after exclusions.")

    grouped: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    for card in cards:
        for pattern in card["patterns"]:
            if pattern.get("transferable", True):
                grouped[pattern["key"]].append((card["source_id"], pattern))
    if not grouped:
        raise SystemExit("Completed cards contain no transferable patterns.")
    aggregated = [aggregate_pattern(key, values, len(cards)) for key, values in sorted(grouped.items())]
    stable: list[dict[str, Any]] = []
    conditional: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    minimum_count = max(2, args.min_support_count)
    minimum_share = max(0.0, min(1.0, args.min_support_share))
    for pattern in aggregated:
        if pattern["support_count"] >= minimum_count and pattern["support_share"] >= minimum_share:
            stable.append(pattern)
        elif pattern["support_count"] >= 2:
            conditional.append(pattern)
        else:
            observations.append(pattern)

    domains = sorted({str(card.get("source_type", "other")) for card in cards})
    domain = args.domain or (domains[0] if len(domains) == 1 else "mixed")
    retained_ids = {str(card["source_id"]) for card in cards}
    identity = hashlib.sha256("\n".join(sorted(retained_ids)).encode("utf-8")).hexdigest()[:10]
    profile_id = args.profile_id or slugify(f"{domain}-{identity}-profile")
    variables = unique_strings([item for card in cards for item in card.get("variables", [])])
    exceptions = unique_strings([item for card in cards for item in card.get("exceptions", [])])
    limitations = unique_strings([item for card in cards for item in card.get("limitations", [])])
    preferred = stable or conditional or observations
    invariants = [pattern["statement"] for pattern in preferred[:12]]
    acceptance = [f"Verify pattern {pattern['key']}: {pattern['statement']}" for pattern in preferred[:12]]
    profile = {
        "schema_version": 1,
        "profile_id": profile_id,
        "generated_at": utc_now(),
        "review_status": "draft",
        "review_summary": "",
        "domain": domain,
        "source_count": len(cards),
        "source_ids": [card["source_id"] for card in cards],
        "excluded_sources": [
            {"source_id": card["source_id"], "reason": card["exclusion_reason"]}
            for card in excluded_cards
        ],
        "source_types": domains,
        "stable_patterns": stable,
        "conditional_patterns": conditional,
        "observations": observations,
        "content_variables": variables,
        "exceptions": exceptions,
        "limitations": limitations,
        "generation_contract": {
            "review_status": "draft",
            "mode": "transfer",
            "medium": domain,
            "target_effect": "Transfer the evidenced mechanisms to a new idea while replacing source-specific content and protected assets.",
            "invariants": invariants or ["Preserve the evidenced structural and behavioral relationships recorded in this profile."],
            "variables": variables or ["Replace topic, entities, facts, examples, scenes, copy, and other source-specific content."],
            "sequence": [
                "Define the new objective, audience, medium, and constraints.",
                "Map applicable profile patterns to the new content structure.",
                "Create the candidate from global structure to local details.",
                "Re-analyze the candidate with the same evidence pipeline.",
                "Revise failed dimensions without copying source-specific expressions or assets.",
            ],
            "negative_constraints": [
                "Do not copy distinctive passages, logos, identities, characters, or protected assets.",
                "Do not turn single-source observations into universal rules.",
                "Do not claim unsupported capabilities or fabricate missing evidence.",
            ],
            "acceptance": acceptance or ["Confirm that every retained rule is observable in the candidate and linked to evidence."],
        },
        "review_requirements": [
            "Resolve statement variants and contradictions.",
            "Move topic-dependent traits out of stable_patterns.",
            "Check evidence locators and remove unsupported rules.",
            "Adapt the generation contract to the actual medium and objective.",
            "Set review_status to reviewed only after these checks pass.",
        ],
    }
    atomic_write_json(output, profile)
    print(json.dumps(profile, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
