#!/usr/bin/env python3
"""Extract local acoustic, rhythm, pause, pitch, and optional speech evidence."""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
from typing import Any

from kunpeng_common import (
    AUDIO_EXTENSIONS,
    atomic_write_json,
    atomic_write_text,
    bounded_error,
    command_path,
    configure_utf8,
    find_sources,
    prepare_output,
    quantile,
    relative_artifact,
    run_command,
    sampled_fingerprint,
    source_id,
    status_counts,
    utc_now,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze standalone audio locally.")
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--no-recursive", action="store_true")
    parser.add_argument("--max-audio", type=int, default=500)
    parser.add_argument("--content-type", choices=("auto", "speech", "music", "mixed"), default="auto")
    parser.add_argument("--transcribe", choices=("auto", "on", "off"), default="auto")
    parser.add_argument("--whisper-model", default="small")
    parser.add_argument("--language")
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    parser.add_argument("--compute-type", default="auto")
    parser.add_argument("--model-cache", type=Path)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--max-analysis-seconds", type=float, default=7200.0)
    return parser.parse_args()


def probe_audio(path: Path) -> dict[str, Any]:
    ffprobe = command_path("ffprobe")
    if not ffprobe:
        raise RuntimeError("ffprobe is not available")
    result = run_command(
        [
            ffprobe, "-v", "error", "-show_entries",
            "format=duration,format_name,bit_rate:stream=index,codec_name,channels,sample_rate,channel_layout",
            "-of", "json", str(path),
        ]
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr or "ffprobe failed")
    return json.loads(result.stdout)


def extract_wav(source: Path, target: Path) -> None:
    ffmpeg = command_path("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is not available")
    target.parent.mkdir(parents=True, exist_ok=True)
    result = run_command(
        [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source), "-vn", "-ac", "1", "-ar", "16000", str(target)]
    )
    if result.returncode != 0 or not target.exists():
        raise RuntimeError(result.stderr or "ffmpeg could not extract audio")


def acoustic_metrics(path: Path, maximum_seconds: float) -> dict[str, Any]:
    import librosa
    import numpy as np

    y, sample_rate = librosa.load(path, sr=16000, mono=True, duration=max(1.0, maximum_seconds))
    if not len(y):
        raise RuntimeError("audio track is empty")
    hop = 512
    rms = librosa.feature.rms(y=y, hop_length=hop)[0]
    rms_db = librosa.amplitude_to_db(rms, ref=np.max)
    onset = librosa.onset.onset_strength(y=y, sr=sample_rate, hop_length=hop)
    tempo, beat_frames = librosa.beat.beat_track(onset_envelope=onset, sr=sample_rate, hop_length=hop)
    tempo_value = float(np.asarray(tempo).reshape(-1)[0]) if np.asarray(tempo).size else 0.0
    beat_times = librosa.frames_to_time(beat_frames, sr=sample_rate, hop_length=hop)
    non_silent = librosa.effects.split(y, top_db=35)
    silence: list[dict[str, float]] = []
    cursor = 0
    for start, end in non_silent:
        if start > cursor:
            silence.append({"start": round(cursor / sample_rate, 3), "end": round(start / sample_rate, 3)})
        cursor = max(cursor, end)
    if cursor < len(y):
        silence.append({"start": round(cursor / sample_rate, 3), "end": round(len(y) / sample_rate, 3)})
    centroid = librosa.feature.spectral_centroid(y=y, sr=sample_rate, hop_length=hop)[0]
    zcr = librosa.feature.zero_crossing_rate(y, hop_length=hop)[0]
    pitch_values: list[float] = []
    try:
        pitch = librosa.yin(y, fmin=65, fmax=600, sr=sample_rate, hop_length=hop)
        pitch_values = [float(value) for value in pitch if math.isfinite(float(value)) and 65 <= value <= 600]
    except Exception:
        pitch_values = []
    duration = len(y) / sample_rate
    return {
        "analyzed_seconds": round(duration, 3),
        "truncated": False,
        "sample_rate": sample_rate,
        "tempo_bpm_estimate": round(tempo_value, 2),
        "beat_times": [round(float(value), 3) for value in beat_times[:2000]],
        "rms_db": {
            "mean": round(float(np.mean(rms_db)), 3),
            "std": round(float(np.std(rms_db)), 3),
            "p10": round(quantile(rms_db, 0.1), 3),
            "p90": round(quantile(rms_db, 0.9), 3),
        },
        "silence_intervals": silence[:2000],
        "silence_share": round(sum(item["end"] - item["start"] for item in silence) / max(duration, 0.001), 4),
        "spectral_centroid_mean": round(float(np.mean(centroid)), 3),
        "zero_crossing_rate_mean": round(float(np.mean(zcr)), 5),
        "pitch_hz": {
            "median": round(quantile(pitch_values, 0.5), 2) if pitch_values else None,
            "p10": round(quantile(pitch_values, 0.1), 2) if pitch_values else None,
            "p90": round(quantile(pitch_values, 0.9), 2) if pitch_values else None,
        },
    }


def choose_device(device: str, compute_type: str) -> tuple[str, str]:
    selected_device = device
    if selected_device == "auto":
        try:
            import ctranslate2

            selected_device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        except Exception:
            selected_device = "cpu"
    selected_compute = compute_type
    if selected_compute == "auto":
        selected_compute = "float16" if selected_device == "cuda" else "int8"
    return selected_device, selected_compute


def make_whisper_loader(args: argparse.Namespace) -> Any:
    cache: list[tuple[Any, str, str]] = []

    def load() -> tuple[Any, str, str]:
        if cache:
            return cache[0]
        from faster_whisper import WhisperModel

        device, compute_type = choose_device(args.device, args.compute_type)
        kwargs: dict[str, Any] = {"device": device, "compute_type": compute_type}
        if args.model_cache:
            kwargs["download_root"] = str(args.model_cache.resolve())
        if args.offline:
            os.environ.setdefault("HF_HUB_OFFLINE", "1")
            kwargs["local_files_only"] = True
        try:
            model = WhisperModel(args.whisper_model, **kwargs)
        except TypeError:
            kwargs.pop("local_files_only", None)
            model = WhisperModel(args.whisper_model, **kwargs)
        cache.append((model, device, compute_type))
        return cache[0]

    return load


def transcribe(path: Path, load_model: Any, language: str | None) -> dict[str, Any]:
    model, selected_device, compute_type = load_model()
    segments, info = model.transcribe(str(path), language=language, vad_filter=True)
    rendered = [
        {"start": round(segment.start, 3), "end": round(segment.end, 3), "text": segment.text.strip()}
        for segment in segments if segment.text.strip()
    ]
    return {
        "language": getattr(info, "language", language),
        "device": selected_device,
        "compute_type": compute_type,
        "segments": rendered,
    }


def transcript_text(payload: dict[str, Any]) -> str:
    return "\n".join(
        f"[{item['start']:.3f}-{item['end']:.3f}] {item['text']}" for item in payload.get("segments", [])
    ) + "\n"


def main() -> int:
    configure_utf8()
    args = parse_args()
    sources = find_sources(args.source, AUDIO_EXTENSIONS, not args.no_recursive)
    if not sources:
        raise SystemExit("No supported audio files found.")
    if len(sources) > max(1, args.max_audio):
        raise SystemExit(f"Found {len(sources)} files; raise --max-audio to process them all.")
    output = prepare_output(args.output, False)
    root = args.source.resolve()
    load_whisper = make_whisper_loader(args)
    items: list[dict[str, Any]] = []
    for source in sources:
        item_id = source_id(source)
        item_dir = output / "audio" / item_id
        label = source.relative_to(root).as_posix() if root.is_dir() else source.name
        stages: dict[str, dict[str, Any]] = {}
        try:
            probe = probe_audio(source)
            stages["probe"] = {"status": "complete"}
            duration = float((probe.get("format") or {}).get("duration") or 0)
            wav_path = item_dir / "audio-16k-mono.wav"
            extract_wav(source, wav_path)
            stages["audio_extraction"] = {"status": "complete"}
            metrics = None
            try:
                metrics = acoustic_metrics(wav_path, args.max_analysis_seconds)
                metrics["truncated"] = bool(duration and metrics["analyzed_seconds"] + 0.5 < duration)
                stages["acoustic_metrics"] = {
                    "status": "degraded" if metrics["truncated"] else "complete"
                }
                atomic_write_json(item_dir / "acoustic-analysis.json", metrics)
            except Exception as exc:
                stages["acoustic_metrics"] = {
                    "status": "partial",
                    "error": bounded_error(exc, source, output),
                    "fallback": "host_audio_review",
                    "host_review_required": True,
                }

            transcript = None
            should_transcribe = args.transcribe == "on" or (
                args.transcribe == "auto" and args.content_type in {"auto", "speech", "mixed"}
            )
            if should_transcribe:
                try:
                    transcript = transcribe(wav_path, load_whisper, args.language)
                    atomic_write_json(item_dir / "transcript.json", transcript)
                    atomic_write_text(item_dir / "transcript.txt", transcript_text(transcript))
                    stages["transcription"] = {"status": "complete", "segment_count": len(transcript["segments"])}
                except Exception as exc:
                    stages["transcription"] = {"status": "partial", "error": bounded_error(exc, source, output), "host_review_required": True}
            else:
                stages["transcription"] = {"status": "not_applicable", "reason": "disabled_or_music_mode"}
            extraction_status = "partial" if any(stage["status"] == "partial" for stage in stages.values()) else "degraded" if any(stage["status"] == "degraded" for stage in stages.values()) else "complete"
            semantic_review_required = [
                "listen to representative opening, middle, ending, transitions, and unusual segments",
                "identify content structure, speaker relation, emphasis, emotion, music, sound layers, and intent",
                "create an evidence-linked semantic card before declaring distillation complete",
            ]
            analysis = {
                "schema_version": 1,
                "id": item_id,
                "status": extraction_status,
                "status_scope": "deterministic_evidence_only",
                "extraction_status": extraction_status,
                "distillation_status": "evidence_ready",
                "source": {"name": label, "fingerprint": sampled_fingerprint(source)},
                "content_type": args.content_type,
                "probe": probe,
                "stages": stages,
                "artifacts": {
                    "audio": relative_artifact(wav_path, output),
                    "acoustic_analysis": (
                        relative_artifact(item_dir / "acoustic-analysis.json", output)
                        if metrics else None
                    ),
                    "transcript": relative_artifact(item_dir / "transcript.json", output) if transcript else None,
                    "transcript_text": relative_artifact(item_dir / "transcript.txt", output) if transcript else None,
                },
                "host_review_required": semantic_review_required,
                "semantic_review_required": semantic_review_required,
                "limitations": [
                    "Tempo, pitch, loudness, and silence are acoustic evidence, not semantic interpretation.",
                    "Voice identity or cloning is not inferred or generated.",
                ],
            }
            atomic_write_json(item_dir / "analysis.json", analysis)
            items.append({
                "id": item_id,
                "source": label,
                "status": extraction_status,
                "extraction_status": extraction_status,
                "distillation_status": "evidence_ready",
                "analysis": relative_artifact(item_dir / "analysis.json", output),
                "host_review_required": analysis["host_review_required"],
            })
        except Exception as exc:
            items.append({"id": item_id, "source": label, "status": "failed", "error": bounded_error(exc, source, output)})
    counts = status_counts(items)
    manifest = {
        "schema_version": 1,
        "kind": "audio-analysis",
        "generated_at": utc_now(),
        "local_only": True,
        "source_count": len(sources),
        **counts,
        "status_scope": "deterministic_evidence_only",
        "distillation_status": "evidence_ready",
        "items": items,
    }
    atomic_write_json(output / "manifest.json", manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 1 if counts["failed_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
