#!/usr/bin/env python3
"""Build a safe, bounded evidence inventory for a local code repository."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any

from kunpeng_common import atomic_write_json, configure_utf8, prepare_output, sampled_fingerprint, utc_now


EXCLUDED_DIRECTORIES = {
    ".git", ".hg", ".svn", ".idea", ".vscode", ".venv", "venv", "env",
    "node_modules", "vendor", "dist", "build", "target", "coverage", "__pycache__",
    ".next", ".nuxt", ".cache", ".pytest_cache", ".mypy_cache", ".aws", ".ssh",
}
SENSITIVE_NAMES = {
    ".env", ".env.local", ".env.production", "credentials.json", "secrets.json",
    "id_rsa", "id_ed25519", ".npmrc", ".pypirc", "service-account.json",
}
SENSITIVE_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".jks", ".keystore"}
LANGUAGES = {
    ".py": "Python", ".js": "JavaScript", ".jsx": "JavaScript", ".mjs": "JavaScript",
    ".ts": "TypeScript", ".tsx": "TypeScript", ".java": "Java", ".kt": "Kotlin",
    ".go": "Go", ".rs": "Rust", ".c": "C", ".h": "C/C++", ".cpp": "C++",
    ".cs": "C#", ".php": "PHP", ".rb": "Ruby", ".swift": "Swift",
    ".vue": "Vue", ".svelte": "Svelte", ".html": "HTML", ".css": "CSS",
    ".scss": "SCSS", ".sql": "SQL", ".sh": "Shell", ".ps1": "PowerShell",
}
MANIFEST_NAMES = {
    "package.json", "pyproject.toml", "requirements.txt", "poetry.lock", "uv.lock",
    "cargo.toml", "go.mod", "pom.xml", "build.gradle", "build.gradle.kts",
    "composer.json", "gemfile", "dockerfile", "docker-compose.yml", "compose.yml",
}
ENTRYPOINT_NAMES = {
    "main.py", "app.py", "server.py", "manage.py", "index.js", "index.ts",
    "main.ts", "main.js", "main.go", "main.rs", "program.cs", "app.tsx", "app.jsx",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze a local repository without executing its code.")
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-files", type=int, default=20000)
    parser.add_argument("--max-tree-entries", type=int, default=400)
    return parser.parse_args()


def should_skip(path: Path, root: Path) -> bool:
    relative = path.relative_to(root)
    if any(part.casefold() in EXCLUDED_DIRECTORIES for part in relative.parts[:-1]):
        return True
    name = path.name.casefold()
    return (
        name in SENSITIVE_NAMES
        or name.startswith(".env.")
        or path.suffix.casefold() in SENSITIVE_SUFFIXES
        or ("credential" in name and name.endswith(".json"))
        or ("secret" in name and name.endswith(".json"))
    )


def language(path: Path) -> str:
    return LANGUAGES.get(path.suffix.casefold(), "Other")


def safe_text(path: Path, limit: int = 200_000) -> str:
    if path.stat().st_size > limit:
        return ""
    data = path.read_bytes()
    if b"\x00" in data[:4096]:
        return ""
    return data.decode("utf-8-sig", errors="replace")


def repository_id(root: Path, files: list[Path]) -> str:
    digest = hashlib.sha256(root.name.encode("utf-8", errors="replace"))
    for path in files:
        digest.update(path.relative_to(root).as_posix().encode("utf-8", errors="replace"))
        digest.update(str(path.stat().st_size).encode("ascii"))
    for index in sorted({0, len(files) // 2, len(files) - 1}):
        if files:
            digest.update(sampled_fingerprint(files[index]).encode("ascii"))
    return f"repository-{digest.hexdigest()[:12]}"


def parse_manifest(path: Path) -> dict[str, Any]:
    name = path.name.casefold()
    text = safe_text(path)
    result: dict[str, Any] = {"kind": name}
    if not text:
        result["status"] = "not_parsed"
        return result
    try:
        if name == "package.json":
            payload = json.loads(text)
            result.update(
                {
                    "name": payload.get("name"),
                    "scripts": sorted((payload.get("scripts") or {}).keys()),
                    "dependencies": sorted(
                        set((payload.get("dependencies") or {}).keys())
                        | set((payload.get("devDependencies") or {}).keys())
                    )[:300],
                }
            )
        elif name == "pyproject.toml":
            import tomllib

            payload = tomllib.loads(text)
            project = payload.get("project", {})
            poetry = payload.get("tool", {}).get("poetry", {})
            dependencies = project.get("dependencies", [])
            if isinstance(dependencies, list):
                dependencies = [re.split(r"[<>=!~\s]", str(item), 1)[0] for item in dependencies]
            else:
                dependencies = list(dependencies)
            result.update(
                {
                    "name": project.get("name") or poetry.get("name"),
                    "dependencies": sorted(set(dependencies) | set((poetry.get("dependencies") or {}).keys()))[:300],
                }
            )
        elif name == "requirements.txt":
            dependencies = []
            for line in text.splitlines():
                value = line.strip()
                if value and not value.startswith(("#", "-")):
                    dependencies.append(re.split(r"[<>=!~\[\s]", value, 1)[0])
            result["dependencies"] = sorted(set(dependencies))[:300]
        elif name == "go.mod":
            module = next((line.split(maxsplit=1)[1] for line in text.splitlines() if line.startswith("module ")), None)
            result["name"] = module
            result["dependencies"] = sorted(
                set(match.group(1) for match in re.finditer(r"(?m)^\s*([\w./-]+)\s+v\d", text))
            )[:300]
        elif name == "cargo.toml":
            import tomllib

            payload = tomllib.loads(text)
            result["name"] = payload.get("package", {}).get("name")
            result["dependencies"] = sorted((payload.get("dependencies") or {}).keys())[:300]
        else:
            result["status"] = "indexed_only"
    except Exception as exc:
        result["status"] = "parse_failed"
        result["error"] = " ".join(str(exc).splitlines())[:300]
    return result


def main() -> int:
    configure_utf8()
    args = parse_args()
    root = args.source.resolve()
    if not root.is_dir():
        raise SystemExit(f"Repository directory does not exist: {root}")
    output_target = args.output.resolve()
    if output_target == root or root in output_target.parents:
        raise SystemExit("Repository evidence output must be outside the analyzed repository.")
    output = prepare_output(args.output, False)
    files: list[Path] = []
    skipped_sensitive: list[str] = []
    skipped_symlinks: list[str] = []
    walk_errors: list[str] = []

    def record_walk_error(error: OSError) -> None:
        walk_errors.append(" ".join(str(error).splitlines())[:300])

    for directory, directory_names, file_names in os.walk(root, followlinks=False, onerror=record_walk_error):
        directory_names[:] = [
            name for name in directory_names
            if name.casefold() not in EXCLUDED_DIRECTORIES
            and not (Path(directory) / name).is_symlink()
        ]
        for file_name in file_names:
            path = Path(directory) / file_name
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                skipped_symlinks.append(relative)
                continue
            if should_skip(path, root):
                skipped_sensitive.append(relative)
                continue
            if not path.is_file():
                continue
            files.append(path)
            if len(files) > max(1, args.max_files):
                raise SystemExit(f"Repository exceeds --max-files={args.max_files}; narrow the source or raise the limit.")
    files.sort(key=lambda item: item.relative_to(root).as_posix().casefold())

    language_counts = Counter(language(path) for path in files)
    extension_counts = Counter(path.suffix.casefold() or "<none>" for path in files)
    top_level = Counter(path.relative_to(root).parts[0] for path in files)
    manifests = [path for path in files if path.name.casefold() in MANIFEST_NAMES]
    entrypoints = [path.relative_to(root).as_posix() for path in files if path.name.casefold() in ENTRYPOINT_NAMES]
    tests = [
        path.relative_to(root).as_posix()
        for path in files
        if any(part.casefold() in {"test", "tests", "spec", "specs"} for part in path.relative_to(root).parts)
        or path.name.casefold().startswith(("test_", "spec_"))
    ]
    tree = [
        {
            "path": path.relative_to(root).as_posix(),
            "bytes": path.stat().st_size,
            "language": language(path),
        }
        for path in files[: max(1, args.max_tree_entries)]
    ]
    extraction_status = "partial" if walk_errors else "complete"
    semantic_review_required = [
        "read project rules and primary documentation",
        "trace real entrypoints, data flow, state changes, and external boundaries",
        "inspect tests and failure handling",
        "separate implemented behavior from documentation claims and roadmap",
        "create evidence-linked semantic cards before declaring distillation complete",
    ]
    analysis = {
        "schema_version": 1,
        "id": repository_id(root, files),
        "status": extraction_status,
        "status_scope": "deterministic_inventory_only",
        "extraction_status": extraction_status,
        "distillation_status": "evidence_ready",
        "source": {"name": root.name, "type": "repository"},
        "summary": {
            "file_count": len(files),
            "total_bytes": sum(path.stat().st_size for path in files),
            "language_counts": dict(language_counts.most_common()),
            "extension_counts": dict(extension_counts.most_common(40)),
            "top_level_entries": dict(top_level.most_common()),
            "manifest_count": len(manifests),
            "entrypoint_count": len(entrypoints),
            "test_file_count": len(tests),
        },
        "manifests": [
            {"path": path.relative_to(root).as_posix(), **parse_manifest(path)} for path in manifests
        ],
        "entrypoints": entrypoints[:100],
        "test_files": tests[:200],
        "tree_sample": tree,
        "tree_truncated": len(files) > len(tree),
        "sensitive_files_skipped": skipped_sensitive,
        "symlinks_skipped": skipped_symlinks,
        "walk_errors": walk_errors,
        "host_review_required": semantic_review_required,
        "semantic_review_required": semantic_review_required,
        "limitations": [
            "Inventory does not execute untrusted project code.",
            "Architecture, product behavior, and tradeoffs require source-level agent review.",
            "Generated files and dependency directories are excluded by default.",
            "Symbolic links are not followed, so linked content outside the repository is not inspected.",
        ],
    }
    analysis_path = output / "analysis.json"
    atomic_write_json(analysis_path, analysis)
    manifest = {
        "schema_version": 1,
        "kind": "repository-analysis",
        "generated_at": utc_now(),
        "local_only": True,
        "source_count": 1,
        "complete_count": 1 if extraction_status == "complete" else 0,
        "degraded_count": 0,
        "partial_count": 1 if extraction_status == "partial" else 0,
        "failed_count": 0,
        "not_applicable_count": 0,
        "status_scope": "deterministic_inventory_only",
        "distillation_status": "evidence_ready",
        "items": [
            {
                "id": analysis["id"],
                "source": root.name,
                "status": extraction_status,
                "extraction_status": extraction_status,
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
