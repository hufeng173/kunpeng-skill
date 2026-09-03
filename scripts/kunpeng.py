#!/usr/bin/env python3
"""Portable entry point that prefers the virtual environment beside this skill."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


COMMANDS = {
    "probe": "capability_probe.py",
    "repository": "analyze_repository.py",
    "host-evidence": "register_host_evidence.py",
    "video": "analyze_videos.py",
    "audio": "analyze_audio.py",
    "images": "analyze_images.py",
    "documents": "analyze_documents.py",
    "prepare-review": "prepare_review.py",
    "build-profile": "build_profile.py",
    "prepare-evaluation": "prepare_evaluation.py",
    "contract": "validate_contract.py",
    "merge": "merge_manifests.py",
    "gate": "workflow_gate.py",
    "compare": "compare_reproduction.py",
    "index": "build_library_index.py",
    "search": "search_library.py",
    "validate": "validate_output.py",
}


def skill_python(skill_root: Path) -> Path | None:
    candidates = [
        skill_root / ".venv" / "Scripts" / "python.exe",
        skill_root / ".venv" / "bin" / "python",
    ]
    return next((candidate for candidate in candidates if candidate.is_file()), None)


def usage() -> str:
    commands = " | ".join(COMMANDS)
    return (
        "Usage: python scripts/kunpeng.py <command> [arguments...]\n"
        f"Commands: {commands}\n"
        "Use '<command> --help' for command-specific options."
    )


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) < 2 or sys.argv[1] in {"-h", "--help"}:
        print(usage())
        return 0

    command = sys.argv[1]
    if command == "--show-python":
        print(sys.executable)
        return 0
    if command not in COMMANDS:
        print(f"Unknown command: {command}\n{usage()}", file=sys.stderr)
        return 2

    script_dir = Path(__file__).resolve().parent
    skill_root = script_dir.parent
    preferred = skill_python(skill_root)
    current = Path(sys.executable).resolve()
    if preferred and current != preferred.resolve():
        environment = os.environ.copy()
        environment.setdefault("PYTHONUTF8", "1")
        return subprocess.call(
            [str(preferred), str(Path(__file__).resolve()), *sys.argv[1:]],
            env=environment,
        )

    target = script_dir / COMMANDS[command]
    return subprocess.call([sys.executable, str(target), *sys.argv[2:]])


if __name__ == "__main__":
    raise SystemExit(main())
