from __future__ import annotations

import importlib.util
import json
import math
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))


def run_script(name: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT_DIR / name), *arguments],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


class AnalyzerTests(unittest.TestCase):
    def test_image_analyzer_keeps_basic_evidence_without_opencv(self) -> None:
        from PIL import Image, ImageDraw

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "sample.png"
            image = Image.new("RGB", (320, 180), "#18243A")
            draw = ImageDraw.Draw(image)
            draw.rectangle((40, 30, 200, 150), fill="#F2A65A")
            image.save(source)
            output = root / "output"
            result = run_script("analyze_images.py", str(source), "--output", str(output), "--ocr", "off")
            self.assertEqual(result.returncode, 0, result.stderr)
            manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["items"][0]["distillation_status"], "evidence_ready")
            analysis_path = output / manifest["items"][0]["analysis"]
            analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
            self.assertIn(analysis["visual_metrics"]["method"], {"opencv", "numpy_gradient_fallback"})
            self.assertTrue(analysis["semantic_review_required"])

    def test_document_corpus_detects_exact_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            repeated = "共同页眉文本需要识别。\n\n这是第一段完整内容，用来测试批量文章的重复检测和结构统计。\n"
            (source / "a.txt").write_text(repeated, encoding="utf-8")
            (source / "b.txt").write_text(repeated, encoding="utf-8")
            (source / "c.txt").write_text("共同页眉文本需要识别。\n\n另一篇文章讨论新的主题和不同的论证步骤。\n", encoding="utf-8")
            output = root / "output"
            result = run_script("analyze_documents.py", str(source), "--output", str(output), "--ocr-scanned", "off")
            self.assertEqual(result.returncode, 0, result.stderr)
            corpus = json.loads((output / "corpus-analysis.json").read_text(encoding="utf-8"))
            self.assertEqual(corpus["distillation_status"], "evidence_ready")
            self.assertEqual(len(corpus["exact_duplicate_groups"]), 1)
            manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            self.assertTrue(all(item.get("distillation_status") == "evidence_ready" for item in manifest["items"]))
            first_analysis = json.loads((output / manifest["items"][0]["analysis"]).read_text(encoding="utf-8"))
            self.assertTrue(first_analysis["semantic_review_required"])

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg is unavailable")
    def test_audio_analyzer_degrades_without_optional_models(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "tone.wav"
            sample_rate = 16000
            with wave.open(str(source), "wb") as output_wave:
                output_wave.setnchannels(1)
                output_wave.setsampwidth(2)
                output_wave.setframerate(sample_rate)
                frames = b"".join(
                    struct.pack("<h", int(8000 * math.sin(2 * math.pi * 220 * index / sample_rate)))
                    for index in range(sample_rate)
                )
                output_wave.writeframes(frames)
            output = root / "output"
            result = run_script("analyze_audio.py", str(source), "--output", str(output), "--transcribe", "off")
            self.assertEqual(result.returncode, 0, result.stderr)
            manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["distillation_status"], "evidence_ready")
            self.assertIn(manifest["items"][0]["status"], {"complete", "degraded", "partial"})
            analysis = json.loads((output / manifest["items"][0]["analysis"]).read_text(encoding="utf-8"))
            self.assertTrue(analysis["semantic_review_required"])

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg is unavailable")
    def test_video_analyzer_preserves_multiphase_frames_on_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "sample.mp4"
            generated = subprocess.run(
                [
                    shutil.which("ffmpeg") or "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", "testsrc=size=160x90:rate=10:duration=2",
                    "-c:v", "mpeg4", "-y", str(source),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(generated.returncode, 0, generated.stderr)
            output = root / "output"
            result = run_script(
                "analyze_videos.py", str(source), "--output", str(output),
                "--transcript-source", "subtitles", "--ocr", "off", "--audio-analysis", "off",
                "--frames-per-scene", "5", "--max-keyframes", "10",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            analysis = json.loads((output / manifest["items"][0]["analysis"]).read_text(encoding="utf-8"))
            frames = json.loads((output / analysis["artifacts"]["keyframes"]).read_text(encoding="utf-8"))
            self.assertGreaterEqual(len(frames), 3)
            self.assertTrue(all("phase" in frame and "scene_index" in frame for frame in frames))
            self.assertEqual(analysis["distillation_status"], "evidence_ready")
            self.assertTrue(analysis["semantic_review_required"])

    def test_repository_inventory_does_not_include_sensitive_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository = root / "repository"
            repository.mkdir()
            (repository / "main.py").write_text("print('safe inventory')\n", encoding="utf-8")
            (repository / ".env").write_text("SECRET=do-not-read\n", encoding="utf-8")
            (repository / "client-secret.key").write_text("do-not-read\n", encoding="utf-8")
            output = root / "output"
            result = run_script("analyze_repository.py", str(repository), "--output", str(output))
            self.assertEqual(result.returncode, 0, result.stderr)
            analysis = json.loads((output / "analysis.json").read_text(encoding="utf-8"))
            self.assertIn(".env", analysis["sensitive_files_skipped"])
            self.assertIn("client-secret.key", analysis["sensitive_files_skipped"])
            self.assertNotIn(".env", [item["path"] for item in analysis["tree_sample"]])
            self.assertTrue(analysis["semantic_review_required"])

    def test_host_evidence_sanitizes_url_and_prepares_typed_review(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            capture = root / "capture"
            capture.mkdir()
            (capture / "home.png").write_bytes(b"captured-image-bytes")
            (root / "outside.png").write_bytes(b"must-not-be-accepted-by-capture-log")
            (capture / "capture-log.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "environment": "desktop 1440x900",
                        "observations": [
                            {"action": "open menu", "observation": "navigation drawer becomes visible", "artifact": "home.png"},
                            {"action": "read outside file", "observation": "must not be accepted", "artifact": "../outside.png"},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            evidence = root / "evidence"
            result = run_script(
                "register_host_evidence.py", str(capture), "--source-type", "website",
                "--source-label", "Example Site", "--source-url", "https://example.com/path?token=secret#section",
                "--output", str(evidence),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            analysis = json.loads((evidence / "analysis.json").read_text(encoding="utf-8"))
            self.assertEqual(analysis["source"]["public_url"], "https://example.com/path")
            self.assertEqual(analysis["capture_log"]["status"], "partial")
            self.assertIn("../outside.png", analysis["capture_log"]["missing_artifacts"])
            self.assertTrue(analysis["semantic_review_required"])
            review = root / "review"
            prepared = run_script("prepare_review.py", str(evidence / "manifest.json"), "--output", str(review))
            self.assertEqual(prepared.returncode, 0, prepared.stderr)
            task = json.loads((review / "review-tasks.json").read_text(encoding="utf-8"))["tasks"][0]
            self.assertIn("interaction states and feedback", task["required_dimensions"])

    def test_merge_preserves_analysis_identity_when_source_ids_collide(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifests: list[Path] = []
            for index, kind in enumerate(("document-analysis", "image-analysis"), start=1):
                evidence = root / f"evidence-{index}"
                evidence.mkdir()
                (evidence / "analysis.json").write_text(
                    json.dumps(
                        {
                            "schema_version": 1,
                            "id": "same-source-id",
                            "status": "complete",
                            "status_scope": "test_evidence_only",
                            "distillation_status": "evidence_ready",
                            "semantic_review_required": ["review the actual artifact"],
                        }
                    ),
                    encoding="utf-8",
                )
                manifest = evidence / "manifest.json"
                manifest.write_text(
                    json.dumps(
                        {
                            "schema_version": 1,
                            "kind": kind,
                            "status_scope": "test_evidence_only",
                            "distillation_status": "evidence_ready",
                            "source_count": 1,
                            "items": [
                                {
                                    "id": "same-source-id",
                                    "source": f"source-{index}",
                                    "status": "complete",
                                    "distillation_status": "evidence_ready",
                                    "analysis": "analysis.json",
                                }
                            ],
                        }
                    ),
                    encoding="utf-8",
                )
                manifests.append(manifest)
            output = root / "mixed"
            result = run_script(
                "merge_manifests.py", str(manifests[0]), str(manifests[1]), "--output", str(output)
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            merged = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(len({item["id"] for item in merged["items"]}), 2)
            self.assertEqual(merged["items"][1]["analysis_id"], "same-source-id")

    @unittest.skipUnless(importlib.util.find_spec("cv2") and importlib.util.find_spec("numpy"), "OpenCV is unavailable")
    def test_global_motion_reports_candidate_not_camera_fact(self) -> None:
        import cv2
        import numpy as np
        from analyze_videos import estimate_global_motion

        random = np.random.default_rng(7)
        previous = (random.random((240, 320)) * 255).astype("uint8")
        matrix = np.float32([[1, 0, 8], [0, 1, 0]])
        current = cv2.warpAffine(previous, matrix, (320, 240))
        result = estimate_global_motion(previous, current, 0.25)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["movement"], "pan_left_likely")
        self.assertIn("host review", result["interpretation_note"].lower())


if __name__ == "__main__":
    unittest.main()
