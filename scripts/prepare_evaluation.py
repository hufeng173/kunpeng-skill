#!/usr/bin/env python3
"""Create a medium-aware candidate evaluation template linked to real artifacts."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from kunpeng_common import atomic_write_json, configure_utf8
from profile_contract import load_json, validate_profile


DIMENSIONS = {
    "writing": [
        ("facts and new-topic coverage", True), ("article structure and reasoning moves", True),
        ("voice audience distance and emotional movement", False),
        ("sentence paragraph rhythm and rhetorical actions", False),
        ("originality and protected-expression boundary", True),
    ],
    "document": [
        ("factual and structural correctness", True), ("method or teaching sequence", True),
        ("voice and readability", False), ("boundaries examples and counterexamples", True),
    ],
    "video": [
        ("format duration and required assets", True), ("narrative and shot structure", True),
        ("camera subject motion and continuity", True), ("editing text and sound synchronization", False),
        ("technical artifacts and protected assets", True),
    ],
    "audio": [
        ("duration content and required assets", True), ("section and speech structure", True),
        ("pace pauses emphasis and prosody", False), ("sound layers transitions and dynamics", False),
    ],
    "image": [
        ("format content and required objects", True), ("composition and information hierarchy", True),
        ("color lighting material and finish", False), ("typography readability and asset boundary", True),
    ],
    "brand": [
        ("brand objective and required content", True), ("composition and identity system", True),
        ("color typography and imagery roles", False), ("protected asset and adaptation boundary", True),
    ],
    "website": [
        ("primary task completion", True), ("states feedback and recovery", True),
        ("layout interaction and motion", False), ("responsive accessibility and performance", True),
    ],
    "app": [
        ("primary task completion", True), ("navigation permissions and state recovery", True),
        ("platform interaction and visual system", False), ("offline accessibility and failure handling", True),
    ],
    "ui": [
        ("task path and state coverage", True), ("layout hierarchy and component relationships", True),
        ("interaction motion and feedback", False), ("responsive and accessible behavior", True),
    ],
    "repository": [
        ("requested behavior and outputs", True), ("architecture data and control flow", True),
        ("failure handling tests and operability", True), ("method transfer and project-specific boundary", False),
    ],
    "course": [
        ("learning objective and factual correctness", True), ("concept dependency and teaching sequence", True),
        ("examples exercises feedback and assessment", False), ("prerequisites and transfer boundary", True),
    ],
    "mixed": [
        ("new objective and required outputs", True), ("cross-source mechanism consistency", True),
        ("modality-specific quality", False), ("dependencies contradictions and boundaries", True),
    ],
}

MEDIUM_ALIASES = {
    "article": "writing",
    "book": "document",
    "visual": "image",
    "podcast": "audio",
    "web": "website",
    "interaction": "ui",
    "project": "repository",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare a candidate evaluation JSON template.")
    parser.add_argument("profile", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--objective", required=True)
    parser.add_argument("--evidence", action="append", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def nonempty(path: Path) -> bool:
    if path.is_file():
        return path.stat().st_size > 0
    return path.is_dir() and any(item.is_file() and item.stat().st_size > 0 for item in path.rglob("*"))


def relative(path: Path, root: Path) -> str:
    return Path(os.path.relpath(path.resolve(), root.resolve())).as_posix()


def main() -> int:
    configure_utf8()
    args = parse_args()
    objective = " ".join(args.objective.split())
    if len(objective) < 8:
        raise SystemExit("Objective must describe the concrete candidate goal.")
    profile_path = args.profile.resolve()
    if not profile_path.is_file():
        raise SystemExit(f"Profile does not exist: {profile_path}")
    profile = load_json(profile_path)
    profile_errors = validate_profile(profile, require_reviewed=True)
    if profile_errors:
        raise SystemExit("Profile is not reviewed:\n- " + "\n- ".join(profile_errors))
    candidate = args.candidate.resolve()
    if not candidate.exists() or not nonempty(candidate):
        raise SystemExit("Candidate is missing or empty.")
    evidence = [path.resolve() for path in args.evidence]
    missing = [str(path) for path in evidence if not path.exists() or not nonempty(path)]
    if missing:
        raise SystemExit("Candidate evidence is missing or empty:\n- " + "\n- ".join(missing))
    output = args.output.resolve()
    if output.exists():
        raise SystemExit(f"Output already exists: {output}")
    medium = str(profile.get("generation_contract", {}).get("medium") or profile.get("domain") or "mixed")
    normalized_medium = MEDIUM_ALIASES.get(medium.casefold(), medium.casefold())
    dimensions = DIMENSIONS.get(normalized_medium, DIMENSIONS["mixed"])
    evidence_references = [relative(path, output.parent) for path in evidence]
    payload = {
        "schema_version": 1,
        "review_status": "pending",
        "profile_id": profile["profile_id"],
        "objective": objective,
        "candidate": relative(candidate, output.parent),
        "evidence_artifacts": evidence_references,
        "dimensions": [
            {
                "name": name,
                "hard_constraint": hard_constraint,
                "verdict": "pending",
                "evidence": [
                    {
                        "artifact": evidence_references[0],
                        "locator": "",
                        "observation": "",
                    }
                ],
                "notes": "",
            }
            for name, hard_constraint in dimensions
        ],
        "overall_verdict": "pending",
        "required_revisions": [],
    }
    atomic_write_json(output, payload)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
