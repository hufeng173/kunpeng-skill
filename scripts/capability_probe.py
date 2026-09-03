#!/usr/bin/env python3
"""Probe Kunpeng's local toolchain without importing heavy models."""

from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import json
import platform
from pathlib import Path
from typing import Any

from kunpeng_common import command_version, configure_utf8, utc_now


PROFILES = ("all", "repository", "web", "video", "audio", "image", "document")

COMMANDS = {
    "ffmpeg": {
        "purpose": "media, audio, and subtitle extraction",
        "required_for": ("video", "audio"),
    },
    "ffprobe": {
        "purpose": "media stream and duration inspection",
        "required_for": ("video", "audio"),
    },
}

MODULES = {
    "faster_whisper": ("faster-whisper", "audio transcription", ("video", "audio"), ("video", "audio")),
    "paddle": ("paddlepaddle", "PaddleOCR runtime", ("video", "image", "document"), ("video", "image", "document")),
    "paddleocr": ("paddleocr", "image and burned-subtitle OCR", ("video", "image", "document"), ("video", "image", "document")),
    "scenedetect": ("scenedetect", "shot boundary detection", ("video",), ("video",)),
    "cv2": ("opencv-python-headless", "frames and image metrics", ("video", "image"), ("video", "image")),
    "librosa": ("librosa", "audio rhythm, pitch, and silence metrics", ("video", "audio"), ("video", "audio")),
    "numpy": ("numpy", "numeric processing", ("video", "audio", "image"), ("video", "audio", "image")),
    "PIL": ("Pillow", "image decoding and contact sheets", ("video", "image"), ("video", "image")),
    "pypdf": ("pypdf", "PDF text extraction", ("document",), ("document",)),
    "docx": ("python-docx", "DOCX extraction", ("document",), ("document",)),
    "pptx": ("python-pptx", "PPTX extraction", ("document",), ("document",)),
    "bs4": ("beautifulsoup4", "HTML extraction", ("document",), ("document",)),
    "pypdfium2": ("pypdfium2", "scanned-PDF page rendering", ("document",), ("document",)),
    "trafilatura": ("trafilatura", "article-focused HTML extraction", (), ("document",)),
    "pysubs2": ("pysubs2", "subtitle parsing", (), ("video", "document")),
    "jieba": ("jieba", "Chinese writing statistics", (), ("document",)),
}

ROUTES = {
    "repository": (
        {
            "capability": "repository_inventory",
            "components": (),
            "fallback": "Use the host file tools to inventory the repository without executing source code.",
            "host_capability": "file_and_code_reading",
        },
    ),
    "web": (
        {
            "capability": "interactive_source_capture",
            "components": (),
            "fallback": "Analyze only user-supplied exports, screenshots, recordings, and notes; declare uncaptured states.",
            "host_capability": "browser_control",
            "host_required": True,
        },
    ),
    "video": (
        {
            "capability": "media_probe",
            "components": ("command:ffprobe",),
            "fallback": "OpenCV reads basic visual metadata; stream inventory remains incomplete.",
            "host_capability": "media_inspection",
        },
        {
            "capability": "transcription",
            "components": ("command:ffmpeg", "python:faster_whisper"),
            "fallback": "Use embedded or sidecar subtitles when present; otherwise leave speech uncovered.",
            "host_capability": "audio_understanding",
        },
        {
            "capability": "scene_detection",
            "components": ("python:scenedetect", "python:cv2"),
            "fallback": "Use fixed-interval sampling and label it as synthetic, not real shot boundaries.",
            "host_capability": "vision",
        },
        {
            "capability": "screen_text",
            "components": ("python:paddle", "python:paddleocr"),
            "fallback": "Keep keyframes and contact sheets for explicit host visual review.",
            "host_capability": "vision",
        },
        {
            "capability": "audio_metrics",
            "components": ("command:ffmpeg", "python:librosa"),
            "fallback": "Keep the local audio track for host review; do not invent rhythm metrics.",
            "host_capability": "audio_playback",
        },
    ),
    "audio": (
        {
            "capability": "audio_probe_and_extraction",
            "components": ("command:ffprobe", "command:ffmpeg"),
            "fallback": "Use host audio inspection and mark stream metadata as uncovered.",
            "host_capability": "audio_playback",
        },
        {
            "capability": "audio_metrics",
            "components": ("python:librosa", "python:numpy"),
            "fallback": "Listen with the host but do not fabricate tempo, pitch, loudness, or pause measurements.",
            "host_capability": "audio_playback",
        },
        {
            "capability": "audio_transcription",
            "components": ("command:ffmpeg", "python:faster_whisper"),
            "fallback": "Use supplied transcripts or verified host speech understanding.",
            "host_capability": "audio_understanding",
        },
    ),
    "image": (
        {
            "capability": "visual_metrics",
            "components": ("python:cv2", "python:numpy", "python:PIL"),
            "fallback": "Use Pillow/NumPy fallback metrics plus explicit host vision; mark OpenCV-specific evidence as degraded.",
            "host_capability": "vision",
        },
        {
            "capability": "image_text",
            "components": ("python:paddle", "python:paddleocr"),
            "fallback": "Use host vision on the original image; do not claim OCR coordinates or confidence.",
            "host_capability": "vision",
        },
    ),
    "document": (
        {
            "capability": "document_text",
            "components": ("python:pypdf", "python:docx", "python:pptx", "python:bs4"),
            "fallback": "Use the host's native file reader for the affected format only.",
            "host_capability": "document_reading",
        },
        {
            "capability": "scanned_pdf_text",
            "components": ("python:pypdfium2", "python:paddle", "python:paddleocr"),
            "fallback": "Render pages locally when possible, then use explicit host visual review.",
            "host_capability": "vision",
        },
    ),
}

HOST_CAPABILITIES = {
    "file_and_code_reading": "Read repository rules, source, manifests, tests, and configuration without executing untrusted code.",
    "vision": "View original images, video keyframes, contact sheets, and rendered PDF pages.",
    "browser_control": "Click, scroll, hover, and inspect key secondary pages for website collection.",
    "document_reading": "Read a local document format when its Python parser is missing.",
    "media_inspection": "Inspect playable local media and verify visual or stream-level observations.",
    "audio_understanding": "Understand speech only when the host explicitly provides that capability.",
    "audio_playback": "Listen to local audio for semantic review without fabricating numeric metrics.",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Probe the local standard toolchain.")
    parser.add_argument("--profile", choices=PROFILES, default="all")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Deployment acceptance: fail if a standard component for the profile is missing.",
    )
    parser.add_argument("--output", type=Path, help="Optional JSON output path.")
    return parser.parse_args()


def selected_profiles(profile: str) -> tuple[str, ...]:
    return ("repository", "web", "video", "audio", "image", "document") if profile == "all" else (profile,)


def module_status(
    import_name: str,
    distribution: str,
    purpose: str,
    required_for: tuple[str, ...],
    selected: tuple[str, ...],
) -> dict[str, Any]:
    available = importlib.util.find_spec(import_name) is not None
    version = None
    if available:
        try:
            version = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            version = "installed"
    return {
        "available": available,
        "version": version,
        "required": bool(set(required_for) & set(selected)),
        "required_for": list(required_for),
        "purpose": purpose,
    }


def build_report(profile: str = "all") -> dict[str, Any]:
    selected = selected_profiles(profile)
    commands: dict[str, dict[str, Any]] = {}
    for name, definition in COMMANDS.items():
        if not set(definition["required_for"]) & set(selected):
            continue
        detected = command_version(name)
        required_for = definition["required_for"]
        commands[name] = {
            "available": detected["available"],
            "version": detected["version"],
            "required": bool(set(required_for) & set(selected)),
            "required_for": list(required_for),
            "purpose": definition["purpose"],
        }
    modules = {
        name: module_status(name, distribution, purpose, required_for, selected)
        for name, (distribution, purpose, required_for, visible_for) in MODULES.items()
        if set(visible_for) & set(selected)
    }
    missing_required = [
        f"command:{name}" for name, item in commands.items()
        if item["required"] and not item["available"]
    ] + [
        f"python:{name}" for name, item in modules.items()
        if item["required"] and not item["available"]
    ]
    def component_available(component: str) -> bool:
        kind, name = component.split(":", 1)
        collection = commands if kind == "command" else modules
        return bool(collection.get(name, {}).get("available"))

    routes: list[dict[str, Any]] = []
    for selected_profile in selected:
        for definition in ROUTES[selected_profile]:
            missing = [
                component
                for component in definition["components"]
                if not component_available(component)
            ]
            host_required = bool(definition.get("host_required"))
            routes.append(
                {
                    "profile": selected_profile,
                    "capability": definition["capability"],
                    "status": "host_check_required" if host_required else ("complete" if not missing else "partial"),
                    "route": "agent_check_required" if host_required or missing else "standard",
                    "standard_components": list(definition["components"]),
                    "missing_components": missing,
                    "fallback": definition["fallback"] if host_required or missing else None,
                    "host_capability": definition["host_capability"] if host_required or missing else None,
                }
            )

    def available(name: str) -> bool:
        return bool(modules.get(name, {}).get("available"))

    def command_available(name: str) -> bool:
        return bool(commands.get(name, {}).get("available"))

    all_capabilities = {
        "repository_inventory": True,
        "host_capture_inventory": True,
        "video_transcription": command_available("ffmpeg") and available("faster_whisper"),
        "video_scene_analysis": command_available("ffprobe") and available("scenedetect") and available("cv2"),
        "video_audio_analysis": command_available("ffmpeg") and available("librosa"),
        "audio_transcription": command_available("ffmpeg") and available("faster_whisper"),
        "audio_analysis": command_available("ffmpeg") and command_available("ffprobe") and available("librosa"),
        "image_ocr": available("paddle") and available("paddleocr"),
        "image_metrics": available("cv2") and available("numpy") and available("PIL"),
        "document_text": all(available(name) for name in ("pypdf", "docx", "pptx", "bs4")),
        "scanned_pdf_ocr": available("pypdfium2") and available("paddleocr") and available("paddle"),
    }
    capability_profiles = {
        "repository_inventory": "repository",
        "host_capture_inventory": "web",
        "video_transcription": "video",
        "video_scene_analysis": "video",
        "video_audio_analysis": "video",
        "audio_transcription": "audio",
        "audio_analysis": "audio",
        "image_ocr": "image",
        "image_metrics": "image",
        "document_text": "document",
        "scanned_pdf_ocr": "document",
    }
    capabilities = {
        name: value
        for name, value in all_capabilities.items()
        if capability_profiles[name] in selected
    }
    needed_host_capabilities = {
        route["host_capability"]
        for route in routes
        if route["host_capability"]
    }
    return {
        "schema_version": 2,
        "generated_at": utc_now(),
        "profile": profile,
        "selected_profiles": list(selected),
        "runtime": {
            "python": platform.python_version(),
            "platform": platform.platform(),
        },
        "commands": commands,
        "python_modules": modules,
        "capabilities": capabilities,
        "ready": not missing_required,
        "missing_required": missing_required,
        "routes": routes,
        "host_capability_checks": [
            {
                "capability": name,
                "detectable_by_script": False,
                "agent_must_verify": True,
                "purpose": purpose,
            }
            for name, purpose in HOST_CAPABILITIES.items()
            if name in needed_host_capabilities
        ],
        "priority": [
            "deployed_standard_local_tool",
            "verified_host_capability",
            "deterministic_local_fallback",
            "mark_uncovered",
        ],
        "network_inference": False,
        "paid_api_required": False,
    }


def main() -> int:
    configure_utf8()
    args = parse_args()
    report = build_report(args.profile)
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 1 if args.strict and not report["ready"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
