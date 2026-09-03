#!/usr/bin/env python3
"""Build a local, time-aligned evidence package for video distillation."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
from collections import Counter
from pathlib import Path
from typing import Any, Callable

from kunpeng_common import (
    VIDEO_EXTENSIONS,
    aggregate_status,
    atomic_write_json,
    atomic_write_text,
    bounded_error,
    command_path,
    configure_utf8,
    evenly_spaced,
    find_sources,
    prepare_output,
    relative_artifact,
    reused_analysis_status,
    run_command,
    sampled_fingerprint,
    source_id,
    status_counts,
    utc_now,
)


SUBTITLE_EXTENSIONS = {".ass", ".srt", ".ssa", ".vtt"}
TIMESTAMP_RE = re.compile(
    r"(?P<start>(?:\d+:)?\d{1,2}:\d{2}[,.]\d{3})\s*-->\s*"
    r"(?P<end>(?:\d+:)?\d{1,2}:\d{2}[,.]\d{3})"
)
TAG_RE = re.compile(r"<[^>]+>|\{\\[^}]+\}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Analyze video locally with FFmpeg, faster-whisper, PaddleOCR, PySceneDetect, OpenCV, and librosa."
    )
    parser.add_argument("source", type=Path, help="Video file or directory.")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--no-recursive", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--transcript-source",
        choices=("auto", "subtitles", "asr", "both"),
        default="auto",
    )
    parser.add_argument("--whisper-model", default="small")
    parser.add_argument("--language", default="auto")
    parser.add_argument(
        "--audio-stream",
        type=int,
        help="Absolute ffprobe stream index. Defaults to the first audio stream.",
    )
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    parser.add_argument("--compute-type", default="auto")
    parser.add_argument("--model-cache", type=Path)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--ocr", choices=("auto", "on", "off"), default="auto")
    parser.add_argument("--ocr-lang", default="ch")
    parser.add_argument("--ocr-device", default="auto")
    parser.add_argument("--scene-threshold", type=float, default=27.0)
    parser.add_argument("--fallback-scene-seconds", type=float, default=5.0)
    parser.add_argument("--max-keyframes", type=int, default=120)
    parser.add_argument("--frames-per-scene", type=int, default=5)
    parser.add_argument("--motion-sample-fps", type=float, default=4.0)
    parser.add_argument("--max-motion-pairs", type=int, default=800)
    parser.add_argument("--ocr-interval", type=float, default=2.0)
    parser.add_argument("--max-ocr-frames", type=int, default=1800)
    parser.add_argument("--audio-analysis", choices=("on", "off"), default="on")
    parser.add_argument("--audio-max-seconds", type=float, default=7200.0)
    parser.add_argument("--max-videos", type=int, default=100)
    return parser.parse_args()


def ratio(value: Any) -> float | None:
    if value in (None, "", "0/0"):
        return None
    try:
        if isinstance(value, str) and "/" in value:
            numerator, denominator = value.split("/", 1)
            return float(numerator) / float(denominator) if float(denominator) else None
        return float(value)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def media_probe_cv2(
    path: Path, error: BaseException | str
) -> tuple[dict[str, Any], dict[str, Any], str, str]:
    import cv2

    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise RuntimeError("Neither ffprobe nor OpenCV could inspect the video")
    try:
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
        frame_count = float(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0)
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    finally:
        capture.release()
    duration = frame_count / fps if fps > 0 else 0.0
    reason = bounded_error(error, path)
    raw = {
        "fallback": "opencv",
        "reason": reason,
        "video": {
            "width": width,
            "height": height,
            "fps": fps,
            "frame_count": frame_count,
            "duration": duration,
        },
    }
    summary = {
        "duration_seconds": round(duration, 4),
        "format": path.suffix.casefold().lstrip("."),
        "bit_rate": None,
        "video_streams": [
            {
                "index": 0,
                "codec": None,
                "language": None,
                "width": width,
                "height": height,
                "fps": fps,
                "pixel_format": None,
            }
        ],
        "audio_streams": [],
        "subtitle_streams": [],
        "stream_inventory_complete": False,
    }
    return raw, summary, "degraded", reason


def media_probe(path: Path) -> tuple[dict[str, Any], dict[str, Any], str, str | None]:
    ffprobe = command_path("ffprobe")
    if not ffprobe:
        return media_probe_cv2(path, "ffprobe is not available on PATH")
    result = run_command(
        [
            ffprobe,
            "-v", "error",
            "-show_format",
            "-show_streams",
            "-print_format", "json",
            str(path),
        ]
    )
    if result.returncode != 0:
        return media_probe_cv2(path, result.stderr or "ffprobe failed")
    raw = json.loads(result.stdout)
    streams = raw.get("streams", [])
    format_info = raw.get("format", {})
    duration = ratio(format_info.get("duration")) or max(
        (ratio(stream.get("duration")) or 0.0 for stream in streams), default=0.0
    )
    videos, audios, subtitles, safe_streams = [], [], [], []
    for stream in streams:
        tags = stream.get("tags") or {}
        common = {
            "index": stream.get("index"),
            "codec": stream.get("codec_name"),
            "language": tags.get("language"),
        }
        safe_streams.append(
            {
                "index": stream.get("index"),
                "type": stream.get("codec_type"),
                "codec": stream.get("codec_name"),
                "duration": stream.get("duration"),
                "width": stream.get("width"),
                "height": stream.get("height"),
                "frame_rate": stream.get("avg_frame_rate"),
                "sample_rate": stream.get("sample_rate"),
                "channels": stream.get("channels"),
                "language": tags.get("language"),
            }
        )
        if stream.get("codec_type") == "video":
            videos.append(
                {
                    **common,
                    "width": stream.get("width"),
                    "height": stream.get("height"),
                    "fps": ratio(stream.get("avg_frame_rate") or stream.get("r_frame_rate")),
                    "pixel_format": stream.get("pix_fmt"),
                }
            )
        elif stream.get("codec_type") == "audio":
            audios.append(
                {
                    **common,
                    "channels": stream.get("channels"),
                    "sample_rate": ratio(stream.get("sample_rate")),
                }
            )
        elif stream.get("codec_type") == "subtitle":
            subtitles.append(common)
    summary = {
        "duration_seconds": round(duration, 4),
        "format": format_info.get("format_name"),
        "bit_rate": ratio(format_info.get("bit_rate")),
        "video_streams": videos,
        "audio_streams": audios,
        "subtitle_streams": subtitles,
        "stream_inventory_complete": True,
    }
    sanitized = {
        "format": {
            "name": format_info.get("format_name"),
            "duration": format_info.get("duration"),
            "size": format_info.get("size"),
            "bit_rate": format_info.get("bit_rate"),
        },
        "streams": safe_streams,
    }
    return sanitized, summary, "complete", None


def timestamp_seconds(value: str) -> float:
    value = value.replace(",", ".")
    parts = [float(part) for part in value.split(":")]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    return parts[0] * 60 + parts[1]


def parse_subtitle(path: Path) -> list[dict[str, Any]]:
    try:
        import pysubs2

        subtitles = pysubs2.load(str(path), encoding="utf-8")
        return [
            {
                "start": round(event.start / 1000.0, 3),
                "end": round(event.end / 1000.0, 3),
                "text": " ".join(event.plaintext.split()),
            }
            for event in subtitles
            if event.plaintext.strip()
        ]
    except (ImportError, UnicodeError, OSError):
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        lines = text.replace("\r\n", "\n").splitlines()
        segments: list[dict[str, Any]] = []
        index = 0
        while index < len(lines):
            match = TIMESTAMP_RE.search(lines[index])
            if not match:
                index += 1
                continue
            body: list[str] = []
            index += 1
            while index < len(lines) and lines[index].strip():
                cleaned = TAG_RE.sub("", lines[index]).strip()
                if cleaned:
                    body.append(cleaned)
                index += 1
            if body:
                segments.append(
                    {
                        "start": round(timestamp_seconds(match.group("start")), 3),
                        "end": round(timestamp_seconds(match.group("end")), 3),
                        "text": " ".join(body),
                    }
                )
        return segments


def copy_sidecar_subtitles(video: Path, destination: Path) -> list[Path]:
    found: list[Path] = []
    for candidate in sorted(video.parent.glob(video.stem + ".*")):
        if candidate == video or candidate.suffix.casefold() not in SUBTITLE_EXTENSIONS:
            continue
        target = destination / f"sidecar-{len(found) + 1:02d}{candidate.suffix.casefold()}"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(candidate, target)
        found.append(target)
    return found


def extract_embedded_subtitles(
    video: Path, streams: list[dict[str, Any]], destination: Path
) -> tuple[list[Path], list[str]]:
    if not streams:
        return [], []
    ffmpeg = command_path("ffmpeg")
    if not ffmpeg:
        return [], ["ffmpeg is not available"]
    extracted: list[Path] = []
    errors: list[str] = []
    for position, stream in enumerate(streams, start=1):
        target = destination / f"embedded-{position:02d}.srt"
        target.parent.mkdir(parents=True, exist_ok=True)
        result = run_command(
            [
                ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                "-i", str(video), "-map", f"0:{stream['index']}", "-c:s", "srt", str(target),
            ]
        )
        if result.returncode == 0 and target.exists() and target.stat().st_size:
            extracted.append(target)
        else:
            errors.append(
                f"stream {stream.get('index')} ({stream.get('codec')}): "
                + bounded_error(result.stderr or "conversion failed", video, destination)
            )
    return extracted, errors


def extract_audio(video: Path, target: Path, stream_index: int | None = None) -> None:
    ffmpeg = command_path("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is not available on PATH")
    target.parent.mkdir(parents=True, exist_ok=True)
    command = [
        ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(video),
    ]
    if stream_index is not None:
        command.extend(["-map", f"0:{stream_index}"])
    command.extend(["-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(target)])
    result = run_command(command)
    if result.returncode != 0 or not target.exists():
        raise RuntimeError(result.stderr or "audio extraction failed")


def choose_device(device: str, compute_type: str) -> tuple[str, str]:
    resolved_device = device
    if resolved_device == "auto":
        try:
            import ctranslate2

            resolved_device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        except Exception:
            resolved_device = "cpu"
    resolved_compute = compute_type
    if resolved_compute == "auto":
        resolved_compute = "float16" if resolved_device == "cuda" else "int8"
    return resolved_device, resolved_compute


def make_whisper_loader(args: argparse.Namespace) -> Callable[[], tuple[Any, str, str]]:
    cache: list[tuple[Any, str, str]] = []

    def load() -> tuple[Any, str, str]:
        if cache:
            return cache[0]
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise RuntimeError("faster-whisper is not installed") from exc
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


def transcribe_audio(
    audio: Path, load_model: Callable[[], tuple[Any, str, str]], language: str
) -> dict[str, Any]:
    model, device, compute_type = load_model()
    segments_iterator, info = model.transcribe(
        str(audio),
        language=None if language == "auto" else language,
        beam_size=5,
        vad_filter=True,
        word_timestamps=True,
    )
    segments: list[dict[str, Any]] = []
    for segment in segments_iterator:
        words = [
            {
                "start": round(float(word.start), 3) if word.start is not None else None,
                "end": round(float(word.end), 3) if word.end is not None else None,
                "text": word.word,
                "probability": round(float(word.probability), 4),
            }
            for word in (segment.words or [])
        ]
        segments.append(
            {
                "start": round(float(segment.start), 3),
                "end": round(float(segment.end), 3),
                "text": segment.text.strip(),
                "no_speech_probability": round(float(segment.no_speech_prob), 4),
                "words": words,
            }
        )
    return {
        "kind": "asr",
        "language": getattr(info, "language", language),
        "language_probability": round(float(getattr(info, "language_probability", 0.0)), 4),
        "device": device,
        "compute_type": compute_type,
        "segments": segments,
    }


def detect_scenes(
    video_path: Path, duration: float, threshold: float, fallback_seconds: float
) -> tuple[list[dict[str, Any]], str, str | None]:
    try:
        from scenedetect import SceneManager, open_video
        from scenedetect.detectors import ContentDetector

        video = open_video(str(video_path))
        manager = SceneManager()
        manager.add_detector(ContentDetector(threshold=threshold))
        manager.detect_scenes(video=video, show_progress=False)
        try:
            scene_list = manager.get_scene_list(start_in_scene=True)
        except TypeError:
            scene_list = manager.get_scene_list()
        scenes = [
            {
                "index": index,
                "start": round(start.get_seconds(), 3),
                "end": round(end.get_seconds(), 3),
                "duration": round(end.get_seconds() - start.get_seconds(), 3),
            }
            for index, (start, end) in enumerate(scene_list, start=1)
        ]
        if scenes:
            return scenes, "complete", None
        raise RuntimeError("no scenes returned")
    except Exception as exc:
        interval = max(1.0, fallback_seconds)
        scenes = []
        cursor = 0.0
        while cursor < max(duration, interval):
            end = min(duration, cursor + interval) if duration else cursor + interval
            scenes.append(
                {
                    "index": len(scenes) + 1,
                    "start": round(cursor, 3),
                    "end": round(end, 3),
                    "duration": round(max(0.0, end - cursor), 3),
                    "synthetic": True,
                }
            )
            cursor += interval
            if duration <= 0:
                break
        return scenes, "degraded", bounded_error(exc, video_path)


def write_frame(path: Path, frame: Any) -> None:
    import cv2

    path.parent.mkdir(parents=True, exist_ok=True)
    success, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
    if not success:
        raise RuntimeError("OpenCV could not encode frame")
    encoded.tofile(str(path))


def extract_frames(
    video: Path, timestamps: list[float], destination: Path, prefix: str
) -> list[dict[str, Any]]:
    import cv2

    capture = cv2.VideoCapture(str(video))
    if not capture.isOpened():
        raise RuntimeError("OpenCV could not open video")
    frames: list[dict[str, Any]] = []
    try:
        for index, timestamp in enumerate(timestamps, start=1):
            capture.set(cv2.CAP_PROP_POS_MSEC, max(0.0, timestamp) * 1000.0)
            ok, frame = capture.read()
            if not ok or frame is None:
                continue
            target = destination / f"{prefix}-{index:04d}-{timestamp:010.3f}s.jpg"
            write_frame(target, frame)
            frames.append(
                {
                    "index": index,
                    "timestamp": round(timestamp, 3),
                    "file": target.name,
                    "width": int(frame.shape[1]),
                    "height": int(frame.shape[0]),
                }
            )
    finally:
        capture.release()
    return frames


def extract_frames_ffmpeg(
    video: Path, timestamps: list[float], destination: Path, prefix: str
) -> list[dict[str, Any]]:
    ffmpeg = command_path("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("FFmpeg is not available for frame extraction fallback")
    frames: list[dict[str, Any]] = []
    for index, timestamp in enumerate(timestamps, start=1):
        target = destination / f"{prefix}-{index:04d}-{timestamp:010.3f}s.jpg"
        target.parent.mkdir(parents=True, exist_ok=True)
        result = run_command(
            [
                ffmpeg,
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-ss",
                f"{max(0.0, timestamp):.3f}",
                "-i",
                str(video),
                "-frames:v",
                "1",
                "-q:v",
                "2",
                str(target),
            ]
        )
        if result.returncode != 0 or not target.exists() or not target.stat().st_size:
            continue
        width = height = None
        try:
            from PIL import Image

            with Image.open(target) as image:
                width, height = image.size
        except Exception:
            pass
        frames.append(
            {
                "index": index,
                "timestamp": round(timestamp, 3),
                "file": target.name,
                "width": width,
                "height": height,
            }
        )
    if not frames:
        raise RuntimeError("FFmpeg could not extract any requested frames")
    return frames


def extract_frames_with_fallback(
    video: Path, timestamps: list[float], destination: Path, prefix: str
) -> tuple[list[dict[str, Any]], str, str | None]:
    try:
        return extract_frames(video, timestamps, destination, prefix), "complete", None
    except Exception as exc:
        frames = extract_frames_ffmpeg(video, timestamps, destination, prefix)
        return frames, "degraded", bounded_error(exc, video, destination)


def scene_frame_samples(
    scenes: list[dict[str, Any]], limit: int, frames_per_scene: int
) -> list[dict[str, Any]]:
    limit = max(1, limit)
    frames_per_scene = max(1, min(7, frames_per_scene))
    selected = evenly_spaced(scenes, max(1, limit // frames_per_scene))
    phase_table = {
        1: [("middle", 0.5)],
        2: [("opening", 0.08), ("ending", 0.92)],
        3: [("opening", 0.08), ("middle", 0.5), ("ending", 0.92)],
        4: [("opening", 0.06), ("early", 0.33), ("late", 0.67), ("ending", 0.94)],
        5: [("opening", 0.05), ("quarter", 0.25), ("middle", 0.5), ("three_quarter", 0.75), ("ending", 0.95)],
        6: [("opening", 0.04), ("early", 0.2), ("before_middle", 0.4), ("after_middle", 0.6), ("late", 0.8), ("ending", 0.96)],
        7: [("opening", 0.03), ("early", 0.17), ("first_third", 0.33), ("middle", 0.5), ("second_third", 0.67), ("late", 0.83), ("ending", 0.97)],
    }
    samples: list[dict[str, Any]] = []
    for scene in selected:
        start, end = float(scene["start"]), float(scene["end"])
        duration = max(0.0, end - start)
        for phase, fraction in phase_table[frames_per_scene]:
            samples.append(
                {
                    "scene_index": scene["index"],
                    "phase": phase,
                    "timestamp": round(start + duration * fraction, 3),
                }
            )
            if len(samples) >= limit:
                return samples
    return samples


def annotate_frames(frames: list[dict[str, Any]], samples: list[dict[str, Any]]) -> None:
    by_timestamp = {round(float(item["timestamp"]), 3): item for item in samples}
    for frame in frames:
        sample = by_timestamp.get(round(float(frame["timestamp"]), 3))
        if sample:
            frame["scene_index"] = sample["scene_index"]
            frame["phase"] = sample["phase"]


def read_gray_frame(capture: Any, timestamp: float, width: int = 480) -> Any | None:
    import cv2

    capture.set(cv2.CAP_PROP_POS_MSEC, max(0.0, timestamp) * 1000.0)
    ok, frame = capture.read()
    if not ok or frame is None:
        return None
    height, original_width = frame.shape[:2]
    if original_width > width:
        frame = cv2.resize(frame, (width, max(1, round(height * width / original_width))), interpolation=cv2.INTER_AREA)
    return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)


def estimate_global_motion(previous: Any, current: Any, delta_seconds: float) -> dict[str, Any]:
    import cv2
    import numpy as np

    points = cv2.goodFeaturesToTrack(previous, maxCorners=500, qualityLevel=0.01, minDistance=7, blockSize=7)
    if points is None or len(points) < 10:
        return {"status": "insufficient_features"}
    tracked, status, _ = cv2.calcOpticalFlowPyrLK(previous, current, points, None)
    if tracked is None or status is None:
        return {"status": "tracking_failed"}
    mask = status.reshape(-1).astype(bool)
    source_points = points.reshape(-1, 2)[mask]
    target_points = tracked.reshape(-1, 2)[mask]
    if len(source_points) < 8:
        return {"status": "insufficient_tracks", "track_count": int(len(source_points))}
    matrix, inliers = cv2.estimateAffinePartial2D(
        source_points,
        target_points,
        method=cv2.RANSAC,
        ransacReprojThreshold=3.0,
        maxIters=2000,
        confidence=0.99,
    )
    if matrix is None or inliers is None:
        return {"status": "transform_failed", "track_count": int(len(source_points))}
    inlier_mask = inliers.reshape(-1).astype(bool)
    inlier_count = int(np.count_nonzero(inlier_mask))
    if inlier_count < 6:
        return {"status": "insufficient_inliers", "track_count": int(len(source_points)), "inlier_count": inlier_count}
    a, b, shift_x = (float(value) for value in matrix[0])
    c, d, shift_y = (float(value) for value in matrix[1])
    scale = math.sqrt(max(0.0, a * a + b * b))
    rotation = math.degrees(math.atan2(c, a))
    height, width = previous.shape[:2]
    normalized_x = shift_x / max(1, width)
    normalized_y = shift_y / max(1, height)
    predicted = cv2.transform(source_points.reshape(-1, 1, 2), matrix).reshape(-1, 2)
    residual = np.linalg.norm(predicted - target_points, axis=1)
    residual_normalized = float(np.median(residual)) / max(1, math.hypot(width, height))
    translation = math.hypot(normalized_x, normalized_y)
    scale_change = math.log(max(scale, 1e-9))
    speed = translation / max(delta_seconds, 1e-3)
    if abs(scale_change) >= 0.012:
        movement = "push_or_zoom_in" if scale_change > 0 else "pull_or_zoom_out"
    elif abs(rotation) >= 0.8:
        movement = "roll_clockwise" if rotation > 0 else "roll_counterclockwise"
    elif translation < 0.0025 and abs(scale_change) < 0.004 and abs(rotation) < 0.25:
        movement = "static_or_stabilized"
    elif abs(normalized_x) >= abs(normalized_y):
        movement = "pan_left_likely" if normalized_x > 0 else "pan_right_likely"
    else:
        movement = "tilt_up_likely" if normalized_y > 0 else "tilt_down_likely"
    confidence = min(1.0, inlier_count / max(1, len(source_points)))
    if residual_normalized > 0.01:
        confidence *= 0.7
    return {
        "status": "complete",
        "movement": movement,
        "delta_seconds": round(delta_seconds, 4),
        "track_count": int(len(source_points)),
        "inlier_count": inlier_count,
        "inlier_share": round(inlier_count / max(1, len(source_points)), 4),
        "shift_x_normalized": round(normalized_x, 6),
        "shift_y_normalized": round(normalized_y, 6),
        "translation_speed_per_second": round(speed, 6),
        "scale": round(scale, 6),
        "rotation_degrees": round(rotation, 4),
        "residual_motion": round(residual_normalized, 6),
        "confidence": round(confidence, 4),
        "interpretation_note": "Global image transform is evidence; subject tracking and host review are required before asserting camera intent.",
    }


def analyze_camera_motion(
    video: Path,
    scenes: list[dict[str, Any]],
    sample_fps: float,
    max_pairs: int,
) -> dict[str, Any]:
    import cv2

    capture = cv2.VideoCapture(str(video))
    if not capture.isOpened():
        raise RuntimeError("OpenCV could not open video for motion analysis")
    selected_scenes = evenly_spaced(scenes, min(len(scenes), max(1, max_pairs // 2)))
    pair_budget = max(2, max_pairs // max(1, len(selected_scenes)))
    results: list[dict[str, Any]] = []
    completed_pairs = 0
    try:
        for scene in selected_scenes:
            start, end = float(scene["start"]), float(scene["end"])
            duration = max(0.0, end - start)
            if duration <= 0.06:
                results.append({"scene_index": scene["index"], "status": "too_short", "duration": round(duration, 3), "pairs": []})
                continue
            sample_count = min(pair_budget + 1, max(2, math.ceil(duration * max(0.5, sample_fps)) + 1))
            margin = min(0.04, duration * 0.08)
            timestamps = [
                start + margin + index * max(0.0, duration - 2 * margin) / max(1, sample_count - 1)
                for index in range(sample_count)
            ]
            frames = [(timestamp, read_gray_frame(capture, timestamp)) for timestamp in timestamps]
            frames = [(timestamp, frame) for timestamp, frame in frames if frame is not None]
            pairs: list[dict[str, Any]] = []
            for (left_time, left), (right_time, right) in zip(frames, frames[1:]):
                if left.shape != right.shape:
                    continue
                result = estimate_global_motion(left, right, right_time - left_time)
                result.update({"start": round(left_time, 3), "end": round(right_time, 3)})
                pairs.append(result)
                if result.get("status") == "complete":
                    completed_pairs += 1
            movements = [item["movement"] for item in pairs if item.get("status") == "complete"]
            dominant = max(set(movements), key=movements.count) if movements else "uncertain"
            confidence_values = [float(item.get("confidence", 0.0)) for item in pairs if item.get("status") == "complete"]
            results.append(
                {
                    "scene_index": scene["index"],
                    "status": "complete" if movements else "partial",
                    "duration": round(duration, 3),
                    "dominant_motion_candidate": dominant,
                    "mean_confidence": round(sum(confidence_values) / max(1, len(confidence_values)), 4),
                    "pairs": pairs,
                }
            )
    finally:
        capture.release()
    return {
        "schema_version": 1,
        "method": "KLT optical flow plus RANSAC partial-affine global transform",
        "sample_fps_target": sample_fps,
        "scene_count": len(results),
        "completed_pair_count": completed_pairs,
        "status": "complete" if completed_pairs else "partial",
        "scenes": results,
        "limitations": [
            "Global frame motion cannot by itself distinguish dolly from optical zoom or camera motion from dominant planar subject motion.",
            "Fast cuts, low texture, motion blur, parallax, animation, and large foreground objects can reduce confidence.",
            "Host review of multi-phase frames or source clips remains mandatory for camera intent and subject motion.",
        ],
    }


def interval_timestamps(duration: float, interval: float, limit: int) -> list[float]:
    if duration <= 0 or limit <= 0:
        return []
    interval = max(0.25, interval)
    count = max(1, math.ceil(duration / interval))
    if count > limit:
        interval = duration / limit
    timestamps = [min(duration - 0.001, index * interval + interval / 2.0) for index in range(math.ceil(duration / interval))]
    return [round(max(0.0, value), 3) for value in timestamps[:limit]]


def make_contact_sheet(frames: list[dict[str, Any]], directory: Path, target: Path) -> None:
    from PIL import Image, ImageDraw, ImageOps

    selected = evenly_spaced(frames, 36)
    if not selected:
        return
    cell_width, cell_height, label_height, columns = 240, 135, 22, 4
    rows = math.ceil(len(selected) / columns)
    sheet = Image.new("RGB", (columns * cell_width, rows * (cell_height + label_height)), "white")
    draw = ImageDraw.Draw(sheet)
    for position, frame in enumerate(selected):
        row, column = divmod(position, columns)
        x, y = column * cell_width, row * (cell_height + label_height)
        with Image.open(directory / frame["file"]) as opened:
            image = ImageOps.fit(opened.convert("RGB"), (cell_width, cell_height))
        sheet.paste(image, (x, y))
        draw.text((x + 5, y + cell_height + 3), f"{frame['timestamp']:.2f}s", fill="black")
    target.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(target, quality=88, optimize=True)


def normalize_ocr_text(lines: list[dict[str, Any]]) -> str:
    return " ".join(line.get("text", "").strip() for line in lines if line.get("text", "").strip())


def ocr_frames(
    engine: Any, frames: list[dict[str, Any]], directory: Path
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    observations: list[dict[str, Any]] = []
    intervals: list[dict[str, Any]] = []
    for frame in frames:
        lines = engine.recognize(directory / frame["file"])
        text = normalize_ocr_text(lines)
        observation = {"timestamp": frame["timestamp"], "text": text, "lines": lines}
        observations.append(observation)
        normalized = re.sub(r"\s+", "", text).casefold()
        if not normalized:
            continue
        if intervals and intervals[-1]["normalized"] == normalized:
            intervals[-1]["end"] = frame["timestamp"]
        else:
            intervals.append(
                {
                    "start": frame["timestamp"],
                    "end": frame["timestamp"],
                    "text": text,
                    "normalized": normalized,
                }
            )
    for interval in intervals:
        interval.pop("normalized", None)
    return observations, intervals


def audio_metrics(audio_path: Path, max_seconds: float) -> dict[str, Any]:
    import librosa
    import numpy as np

    y, sample_rate = librosa.load(
        str(audio_path), sr=16000, mono=True, duration=max(1.0, max_seconds)
    )
    if not len(y):
        raise RuntimeError("audio track is empty")
    hop_length = 512
    rms = librosa.feature.rms(y=y, hop_length=hop_length)[0]
    rms_db = librosa.amplitude_to_db(rms, ref=np.max)
    onset = librosa.onset.onset_strength(y=y, sr=sample_rate, hop_length=hop_length)
    tempo, beat_frames = librosa.beat.beat_track(
        onset_envelope=onset, sr=sample_rate, hop_length=hop_length
    )
    tempo_value = float(np.asarray(tempo).reshape(-1)[0]) if np.asarray(tempo).size else 0.0
    beat_times = librosa.frames_to_time(beat_frames, sr=sample_rate, hop_length=hop_length)
    non_silent = librosa.effects.split(y, top_db=35)
    silence: list[dict[str, float]] = []
    cursor = 0
    for start, end in non_silent:
        if start > cursor:
            silence.append(
                {"start": round(cursor / sample_rate, 3), "end": round(start / sample_rate, 3)}
            )
        cursor = max(cursor, int(end))
    if cursor < len(y):
        silence.append(
            {"start": round(cursor / sample_rate, 3), "end": round(len(y) / sample_rate, 3)}
        )
    spectral = librosa.feature.spectral_centroid(y=y, sr=sample_rate, hop_length=hop_length)[0]
    frame_times = librosa.frames_to_time(range(len(rms_db)), sr=sample_rate, hop_length=hop_length)
    envelope_indexes = [item for item in evenly_spaced(list(range(len(rms_db))), 500)]
    analyzed_seconds = len(y) / sample_rate
    return {
        "sample_rate": sample_rate,
        "analyzed_seconds": round(analyzed_seconds, 3),
        "truncated": False,
        "tempo_bpm_estimate": round(tempo_value, 2),
        "beat_times": [
            round(float(value), 3) for value in evenly_spaced(list(beat_times), 2000)
        ],
        "silence_intervals": evenly_spaced(silence, 2000),
        "silence_share": round(sum(item["end"] - item["start"] for item in silence) / max(analyzed_seconds, 0.001), 4),
        "rms_db": {
            "mean": round(float(np.mean(rms_db)), 3),
            "std": round(float(np.std(rms_db)), 3),
            "p10": round(float(np.percentile(rms_db, 10)), 3),
            "p90": round(float(np.percentile(rms_db, 90)), 3),
        },
        "spectral_centroid_hz_mean": round(float(np.mean(spectral)), 2),
        "loudness_envelope": [
            {"time": round(float(frame_times[index]), 3), "db": round(float(rms_db[index]), 3)}
            for index in envelope_indexes
        ],
    }


def format_time(seconds: float) -> str:
    milliseconds = round(seconds * 1000)
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"


def transcript_text(track: dict[str, Any]) -> str:
    return "\n".join(
        f"[{format_time(segment['start'])} - {format_time(segment['end'])}] {segment['text']}"
        for segment in track.get("segments", [])
    ) + "\n"


def build_timeline(
    scenes: list[dict[str, Any]], primary_track: dict[str, Any] | None, ocr_intervals: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for scene in scenes:
        events.append(
            {"kind": "scene", "start": scene["start"], "end": scene["end"], "index": scene["index"]}
        )
    if primary_track:
        for segment in primary_track.get("segments", []):
            events.append(
                {
                    "kind": "speech",
                    "start": segment["start"],
                    "end": segment["end"],
                    "text": segment["text"],
                }
            )
    for interval in ocr_intervals:
        events.append({"kind": "screen_text", **interval})
    return sorted(events, key=lambda event: (event.get("start", 0.0), event["kind"]))


def label_for(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix() if root.is_dir() else path.name


def process_video(
    path: Path,
    label: str,
    output: Path,
    args: argparse.Namespace,
    load_whisper: Callable[[], tuple[Any, str, str]],
    ocr_loader: Callable[[], Any],
) -> dict[str, Any]:
    item_id = source_id(path)
    item_dir = output / "videos" / item_id
    analysis_path = item_dir / "analysis.json"
    if args.resume and analysis_path.exists():
        reused_status = reused_analysis_status(analysis_path)
        return {
            "id": item_id,
            "source": label,
            "status": reused_status,
            "extraction_status": reused_status,
            "distillation_status": "evidence_ready",
            "reused": True,
            "analysis": relative_artifact(analysis_path, output),
        }

    item_dir.mkdir(parents=True, exist_ok=True)
    stages: dict[str, dict[str, Any]] = {}
    raw_probe, media, probe_status, probe_error = media_probe(path)
    atomic_write_json(item_dir / "probe.json", raw_probe)
    stages["probe"] = {"status": probe_status, "error": probe_error}
    duration = float(media.get("duration_seconds") or 0.0)

    subtitle_dir = item_dir / "subtitles"
    subtitle_files = copy_sidecar_subtitles(path, subtitle_dir)
    embedded, subtitle_errors = extract_embedded_subtitles(
        path, media.get("subtitle_streams", []), subtitle_dir
    )
    subtitle_files.extend(embedded)
    subtitle_tracks: list[dict[str, Any]] = []
    for subtitle_file in subtitle_files:
        try:
            segments = parse_subtitle(subtitle_file)
            if segments:
                subtitle_tracks.append(
                    {
                        "kind": "subtitle",
                        "file": relative_artifact(subtitle_file, output),
                        "segments": segments,
                    }
                )
        except Exception as exc:
            subtitle_errors.append(bounded_error(exc, path, output))
    stages["subtitles"] = {
        "status": (
            "complete"
            if subtitle_tracks
            else "degraded"
            if subtitle_errors
            else "not_applicable"
        ),
        "tracks": len(subtitle_tracks),
        "errors": subtitle_errors,
    }

    stream_inventory_complete = bool(media.get("stream_inventory_complete", True))
    has_audio = bool(media.get("audio_streams"))
    audio_may_exist = has_audio or not stream_inventory_complete
    audio_stream_indexes = [stream.get("index") for stream in media.get("audio_streams", [])]
    selected_audio_stream = (
        args.audio_stream if args.audio_stream is not None
        else (audio_stream_indexes[0] if audio_stream_indexes else None)
    )
    if (
        args.audio_stream is not None
        and stream_inventory_complete
        and args.audio_stream not in audio_stream_indexes
    ):
        raise RuntimeError(
            f"Requested audio stream {args.audio_stream} is not present; available: {audio_stream_indexes}"
        )
    needs_asr = args.transcript_source != "subtitles" and audio_may_exist
    needs_audio_file = audio_may_exist and (needs_asr or args.audio_analysis == "on")
    audio_path = item_dir / "audio.wav"
    if needs_audio_file:
        try:
            extract_audio(path, audio_path, selected_audio_stream)
            has_audio = True
            stages["audio_extraction"] = {
                "status": "complete",
                "stream_index": selected_audio_stream,
            }
        except Exception as exc:
            stages["audio_extraction"] = {
                "status": "partial",
                "error": bounded_error(exc, path, output),
            }
    else:
        stages["audio_extraction"] = {
            "status": "not_applicable",
            "reason": "no_audio" if not audio_may_exist else "not_requested",
        }

    tracks = list(subtitle_tracks)
    asr_track: dict[str, Any] | None = None
    if needs_asr and audio_path.exists():
        try:
            asr_track = transcribe_audio(audio_path, load_whisper, args.language)
            tracks.append(asr_track)
            stages["asr"] = {
                "status": "complete",
                "model": args.whisper_model,
                "language": asr_track.get("language"),
            }
        except Exception as exc:
            stages["asr"] = {
                "status": "degraded" if subtitle_tracks else "partial",
                "error": bounded_error(exc, path, output),
                "fallback": "subtitles" if subtitle_tracks else None,
            }
    elif needs_asr:
        stages["asr"] = {
            "status": "degraded" if subtitle_tracks else "partial",
            "error": "audio extraction did not complete",
            "fallback": "subtitles" if subtitle_tracks else None,
        }
    else:
        stages["asr"] = {
            "status": "not_applicable",
            "reason": "no_audio" if not audio_may_exist else "subtitle_only_requested",
        }

    if args.transcript_source == "subtitles":
        primary = subtitle_tracks[0] if subtitle_tracks else None
    elif args.transcript_source == "asr":
        primary = asr_track or (subtitle_tracks[0] if subtitle_tracks else None)
    else:
        primary = subtitle_tracks[0] if subtitle_tracks else asr_track
    transcript_payload = {
        "primary_kind": primary.get("kind") if primary else None,
        "tracks": tracks,
    }
    atomic_write_json(item_dir / "transcript.json", transcript_payload)
    if primary:
        atomic_write_text(item_dir / "transcript.txt", transcript_text(primary))
        transcript_status = "complete"
        if args.transcript_source == "both" and (not subtitle_tracks or not asr_track):
            transcript_status = "degraded"
        elif needs_asr and stages["asr"]["status"] != "complete":
            transcript_status = "degraded"
        stages["transcript"] = {
            "status": transcript_status,
            "primary": primary["kind"],
        }
    else:
        stages["transcript"] = {
            "status": "not_applicable" if not audio_may_exist and not subtitle_files else "partial"
        }

    scenes, scene_status, scene_error = detect_scenes(
        path, duration, args.scene_threshold, args.fallback_scene_seconds
    )
    atomic_write_json(item_dir / "scenes.json", scenes)
    stages["scenes"] = {"status": scene_status, "count": len(scenes), "error": scene_error}

    keyframe_dir = item_dir / "keyframes"
    contact_path = keyframe_dir / "contact-sheet.jpg"
    try:
        keyframe_samples = scene_frame_samples(
            scenes,
            max(1, args.max_keyframes),
            max(1, args.frames_per_scene),
        )
        keyframes, frame_status, frame_error = extract_frames_with_fallback(
            path,
            [sample["timestamp"] for sample in keyframe_samples],
            keyframe_dir,
            "keyframe",
        )
        annotate_frames(keyframes, keyframe_samples)
        atomic_write_json(keyframe_dir / "index.json", keyframes)
        contact_error = None
        try:
            make_contact_sheet(keyframes, keyframe_dir, contact_path)
        except Exception as exc:
            contact_error = bounded_error(exc, path, output)
        stages["keyframes"] = {
            "status": "degraded" if frame_status == "degraded" or contact_error else "complete",
            "count": len(keyframes),
            "frame_fallback_error": frame_error,
            "contact_sheet_error": contact_error,
        }
    except Exception as exc:
        keyframes = []
        stages["keyframes"] = {"status": "partial", "error": bounded_error(exc, path, output)}

    motion_analysis: dict[str, Any] | None = None
    try:
        motion_analysis = analyze_camera_motion(
            path,
            scenes,
            max(0.5, args.motion_sample_fps),
            max(1, args.max_motion_pairs),
        )
        atomic_write_json(item_dir / "motion-analysis.json", motion_analysis)
        stages["motion_analysis"] = {
            "status": motion_analysis["status"],
            "scene_count": motion_analysis["scene_count"],
            "completed_pair_count": motion_analysis["completed_pair_count"],
            "host_review_required": True,
        }
    except Exception as exc:
        stages["motion_analysis"] = {
            "status": "partial",
            "error": bounded_error(exc, path, output),
            "host_review_required": True,
        }

    ocr_observations: list[dict[str, Any]] = []
    ocr_intervals: list[dict[str, Any]] = []
    if args.ocr != "off" and duration > 0:
        try:
            engine = ocr_loader()
            ocr_dir = item_dir / "ocr-frames"
            ocr_samples, ocr_frame_status, ocr_frame_error = extract_frames_with_fallback(
                path,
                interval_timestamps(duration, args.ocr_interval, max(1, args.max_ocr_frames)),
                ocr_dir,
                "ocr",
            )
            atomic_write_json(ocr_dir / "index.json", ocr_samples)
            ocr_observations, ocr_intervals = ocr_frames(engine, ocr_samples, ocr_dir)
            atomic_write_json(
                item_dir / "ocr.json",
                {"observations": ocr_observations, "deduplicated_intervals": ocr_intervals},
            )
            stages["ocr"] = {
                "status": ocr_frame_status,
                "sample_count": len(ocr_samples),
                "text_interval_count": len(ocr_intervals),
                "frame_fallback_error": ocr_frame_error,
            }
        except Exception as exc:
            stages["ocr"] = {
                "status": "partial",
                "error": bounded_error(exc, path, output),
                "fallback": "host_visual_review" if keyframes else None,
                "fallback_ready": bool(keyframes),
                "host_review_required": bool(keyframes),
            }
    else:
        stages["ocr"] = {
            "status": "not_applicable" if args.ocr == "off" else "partial",
            "reason": "disabled_by_user" if args.ocr == "off" else "duration_unknown",
            "fallback": "host_visual_review" if args.ocr != "off" and keyframes else None,
            "fallback_ready": bool(args.ocr != "off" and keyframes),
            "host_review_required": bool(args.ocr != "off" and keyframes),
        }

    audio_analysis: dict[str, Any] | None = None
    if args.audio_analysis == "on" and audio_path.exists():
        try:
            audio_analysis = audio_metrics(audio_path, args.audio_max_seconds)
            media_duration = duration or audio_analysis["analyzed_seconds"]
            audio_analysis["truncated"] = audio_analysis["analyzed_seconds"] + 0.5 < media_duration
            atomic_write_json(item_dir / "audio-analysis.json", audio_analysis)
            stages["audio_analysis"] = {
                "status": "degraded" if audio_analysis["truncated"] else "complete",
                "truncated": audio_analysis["truncated"],
            }
        except Exception as exc:
            stages["audio_analysis"] = {
                "status": "partial",
                "error": bounded_error(exc, path, output),
                "fallback": "host_audio_review",
                "fallback_ready": True,
                "host_review_required": True,
            }
    else:
        stages["audio_analysis"] = {
            "status": (
                "not_applicable"
                if args.audio_analysis == "off" or not audio_may_exist
                else "partial"
            ),
            "reason": "disabled_by_user" if args.audio_analysis == "off" else "no_audio" if not audio_may_exist else "audio_unavailable",
        }

    timeline = build_timeline(scenes, primary, ocr_intervals)
    atomic_write_json(item_dir / "timeline.json", timeline)
    required_stage_names = ["probe", "scenes", "keyframes", "motion_analysis"]
    if audio_may_exist or subtitle_files:
        required_stage_names.append("transcript")
    if args.ocr != "off":
        required_stage_names.append("ocr")
    if args.audio_analysis == "on" and audio_may_exist:
        required_stage_names.append("audio_analysis")
    incomplete = [
        name for name in required_stage_names
        if stages.get(name, {}).get("status") in {"partial", "failed"}
    ]
    degraded = [
        name for name in required_stage_names
        if stages.get(name, {}).get("status") == "degraded"
    ]
    status = aggregate_status(stages[name]["status"] for name in required_stage_names)
    host_review_required = [
        name
        for name, stage in stages.items()
        if stage.get("host_review_required")
    ]
    analysis = {
        "schema_version": 2,
        "id": item_id,
        "status": status,
        "status_scope": "deterministic_video_evidence_only",
        "extraction_status": status,
        "distillation_status": "evidence_ready",
        "source": {"name": label, "fingerprint": sampled_fingerprint(path)},
        "media": media,
        "summary": {
            "scene_count": len(scenes),
            "keyframe_count": len(keyframes),
            "motion_scene_count": motion_analysis.get("scene_count", 0) if motion_analysis else 0,
            "transcript_segments": len(primary.get("segments", [])) if primary else 0,
            "ocr_text_intervals": len(ocr_intervals),
            "timeline_events": len(timeline),
        },
        "stages": stages,
        "host_review_required": host_review_required,
        "artifacts": {
            "probe": relative_artifact(item_dir / "probe.json", output),
            "transcript": relative_artifact(item_dir / "transcript.json", output),
            "transcript_text": relative_artifact(item_dir / "transcript.txt", output) if primary else None,
            "scenes": relative_artifact(item_dir / "scenes.json", output),
            "keyframes": relative_artifact(keyframe_dir / "index.json", output) if keyframes else None,
            "contact_sheet": relative_artifact(contact_path, output) if contact_path.exists() else None,
            "motion_analysis": relative_artifact(item_dir / "motion-analysis.json", output) if motion_analysis else None,
            "ocr": relative_artifact(item_dir / "ocr.json", output) if ocr_observations else None,
            "audio": relative_artifact(audio_path, output) if audio_path.exists() else None,
            "audio_analysis": relative_artifact(item_dir / "audio-analysis.json", output) if audio_analysis else None,
            "timeline": relative_artifact(item_dir / "timeline.json", output),
        },
        "incomplete_required_stages": incomplete,
        "degraded_stages": degraded,
        "limitations": [
            "OCR is sampled, not performed on every frame.",
            "Shot boundaries, global motion, and audio metrics require agent review to infer meaning, camera intent, subject motion, and music structure.",
        ],
        "semantic_review_required": [
            "view each important scene across opening, middle, and ending phases or inspect the source clip",
            "separate camera motion, subject motion, edit transitions, and visual effects",
            "align narrative, speech, on-screen text, music, pauses, beats, and cuts",
            "record evidence-linked patterns, content variables, exceptions, and uncertainty in a semantic card",
        ],
    }
    atomic_write_json(analysis_path, analysis)
    return {
        "id": item_id,
        "source": label,
        "status": status,
        "extraction_status": status,
        "distillation_status": "evidence_ready",
        "analysis": relative_artifact(analysis_path, output),
        "incomplete_required_stages": incomplete,
        "degraded_stages": degraded,
        "host_review_required": host_review_required,
    }


def main() -> int:
    configure_utf8()
    args = parse_args()
    sources = find_sources(args.source, VIDEO_EXTENSIONS, not args.no_recursive)
    if not sources:
        raise SystemExit("No supported videos found.")
    if len(sources) > max(1, args.max_videos):
        raise SystemExit(f"Found {len(sources)} videos; raise --max-videos to process them all.")
    output = prepare_output(args.output, args.resume)
    root = args.source.resolve()
    load_whisper = make_whisper_loader(args)
    ocr_cache: list[Any] = []

    def load_ocr() -> Any:
        if ocr_cache:
            return ocr_cache[0]
        from local_ocr import LocalOCR

        ocr_cache.append(LocalOCR(args.ocr_lang, args.ocr_device))
        return ocr_cache[0]

    items: list[dict[str, Any]] = []
    for path in sources:
        label = label_for(path, root)
        try:
            items.append(
                process_video(path, label, output, args, load_whisper, load_ocr)
            )
        except Exception as exc:
            items.append(
                {
                    "id": source_id(path),
                    "source": label,
                    "status": "failed",
                    "error": bounded_error(exc, path, output),
                }
            )

    counts = status_counts(items)
    collection_records: list[dict[str, Any]] = []
    motion_counts: Counter[str] = Counter()
    scene_durations: list[float] = []
    for item in items:
        if item.get("status") == "failed" or not item.get("analysis"):
            continue
        try:
            analysis_payload = json.loads((output / item["analysis"]).read_text(encoding="utf-8"))
            scenes_path = output / analysis_payload["artifacts"]["scenes"]
            scene_payload = json.loads(scenes_path.read_text(encoding="utf-8"))
            scene_durations.extend(float(scene.get("duration", 0.0)) for scene in scene_payload)
            motion_ref = analysis_payload.get("artifacts", {}).get("motion_analysis")
            if motion_ref:
                motion_payload = json.loads((output / motion_ref).read_text(encoding="utf-8"))
                motion_counts.update(
                    scene.get("dominant_motion_candidate", "uncertain")
                    for scene in motion_payload.get("scenes", [])
                )
            collection_records.append(
                {
                    "source_id": item["id"],
                    "duration_seconds": analysis_payload.get("media", {}).get("duration_seconds"),
                    "scene_count": analysis_payload.get("summary", {}).get("scene_count"),
                    "transcript_segments": analysis_payload.get("summary", {}).get("transcript_segments"),
                }
            )
        except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            continue
    collection_analysis = {
        "schema_version": 1,
        "source_count": len(collection_records),
        "items": collection_records,
        "scene_duration_seconds": {
            "mean": round(sum(scene_durations) / max(1, len(scene_durations)), 4),
            "median": round(sorted(scene_durations)[len(scene_durations) // 2], 4) if scene_durations else 0.0,
            "minimum": round(min(scene_durations), 4) if scene_durations else 0.0,
            "maximum": round(max(scene_durations), 4) if scene_durations else 0.0,
        },
        "motion_candidates": dict(motion_counts.most_common()),
        "distillation_status": "evidence_ready",
        "semantic_review_required": [
            "distinguish creator-level patterns from format, topic, location, subject, and single-video choices",
            "cluster narrative and shot patterns before building a cross-video profile",
            "treat a single video as a work recipe, not a creator style profile",
        ],
    }
    atomic_write_json(output / "collection-analysis.json", collection_analysis)
    manifest = {
        "schema_version": 2,
        "kind": "video-analysis",
        "generated_at": utc_now(),
        "local_only": True,
        "hosted_inference": False,
        "status_scope": "deterministic_video_evidence_only",
        "distillation_status": "evidence_ready",
        "source_count": len(sources),
        **counts,
        "settings": {
            "transcript_source": args.transcript_source,
            "whisper_model": args.whisper_model,
            "audio_stream": args.audio_stream,
            "offline": args.offline,
            "ocr": args.ocr,
            "ocr_interval": args.ocr_interval,
            "max_ocr_frames": args.max_ocr_frames,
            "frames_per_scene": args.frames_per_scene,
            "motion_sample_fps": args.motion_sample_fps,
            "max_motion_pairs": args.max_motion_pairs,
        },
        "collection_analysis": "collection-analysis.json",
        "items": items,
    }
    atomic_write_json(output / "manifest.json", manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 1 if counts["failed_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
