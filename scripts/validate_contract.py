#!/usr/bin/env python3
"""Validate semantic cards, profiles, or candidate evaluations."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Callable

from kunpeng_common import configure_utf8
from profile_contract import load_json, validate_card, validate_evaluation, validate_profile


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate Kunpeng JSON contracts.")
    parser.add_argument("kind", choices=("card", "profile", "evaluation"))
    parser.add_argument("path", type=Path)
    parser.add_argument(
        "--allow-draft", action="store_true",
        help="Allow a draft profile; ignored for cards and evaluations.",
    )
    return parser.parse_args()


def targets(kind: str, path: Path) -> list[Path]:
    resolved = path.resolve()
    if resolved.is_file():
        return [resolved]
    if kind == "card" and resolved.is_dir():
        return sorted(item for item in resolved.rglob("*.json") if item.is_file())
    raise SystemExit(f"Expected a JSON file{' or cards directory' if kind == 'card' else ''}: {resolved}")


def main() -> int:
    configure_utf8()
    args = parse_args()
    validators: dict[str, Callable[[Any], list[str]]] = {
        "card": validate_card,
        "profile": lambda value: validate_profile(value, require_reviewed=not args.allow_draft),
        "evaluation": validate_evaluation,
    }
    files = targets(args.kind, args.path)
    if not files:
        raise SystemExit("No JSON contract files found.")
    failures: list[dict[str, Any]] = []
    for path in files:
        try:
            payload = load_json(path)
            errors = validators[args.kind](payload)
        except Exception as exc:
            errors = [f"cannot read JSON: {exc}"]
        if errors:
            failures.append({"path": str(path), "errors": errors})
    report = {
        "kind": args.kind,
        "checked": len(files),
        "passed": len(files) - len(failures),
        "failed": len(failures),
        "failures": failures,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
