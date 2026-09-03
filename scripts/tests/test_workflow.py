from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[1]
TEST_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TEST_DIR))

from support import evidence_analysis, valid_card, write_json  # noqa: E402


def run_script(name: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT_DIR / name), *arguments],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


class WorkflowTests(unittest.TestCase):
    def make_evidence(self, root: Path) -> tuple[Path, list[str]]:
        evidence = root / "evidence"
        source_ids = ["source-a", "source-b"]
        items = []
        for source_id in source_ids:
            analysis = evidence / f"{source_id}.json"
            write_json(analysis, evidence_analysis(source_id))
            source_content = evidence / source_id / "content.txt"
            source_content.parent.mkdir(parents=True, exist_ok=True)
            source_content.write_text(
                "开头先给出反常识判断，随后使用两类可核验证据展开。\n",
                encoding="utf-8",
            )
            items.append(
                {
                    "id": source_id,
                    "source": f"{source_id}.txt",
                    "status": "complete",
                    "extraction_status": "complete",
                    "distillation_status": "evidence_ready",
                    "analysis": analysis.name,
                }
            )
        manifest = evidence / "manifest.json"
        write_json(
            manifest,
            {
                "schema_version": 2,
                "kind": "document-analysis",
                "status_scope": "text_extraction_only",
                "distillation_status": "evidence_ready",
                "source_count": len(items),
                "items": items,
            },
        )
        return manifest, source_ids

    def reviewed_profile(self, root: Path, source_ids: list[str]) -> Path:
        cards = root / "review" / "cards"
        for source_id in source_ids:
            write_json(cards / f"{source_id}.json", valid_card(source_id))
        draft = root / "profile.json"
        result = run_script("build_profile.py", str(cards), "--output", str(draft), "--domain", "writing")
        self.assertEqual(result.returncode, 0, result.stderr)
        profile = json.loads(draft.read_text(encoding="utf-8"))
        profile["review_status"] = "reviewed"
        profile["review_summary"] = "已核对独立来源、主题干扰、适用边界和证据定位，并完成写作契约。"
        profile["generation_contract"]["review_status"] = "reviewed"
        for group in ("stable_patterns", "conditional_patterns", "observations"):
            for pattern in profile[group]:
                pattern["statement_variants"] = []
        write_json(draft, profile)
        return draft

    def test_distillation_cannot_complete_at_extraction(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest, _ = self.make_evidence(root)
            run = root / "run"
            objective = "提取两篇来源中的稳定表达机制并形成可迁移画像"
            self.assertEqual(run_script("workflow_gate.py", "init", "--output", str(run), "--objective", objective, "--mode", "distillation", "--domains", "document").returncode, 0)
            self.assertEqual(run_script("workflow_gate.py", "register", "--run", str(run), "--type", "manifest", "--path", str(manifest)).returncode, 0)
            checked = run_script("workflow_gate.py", "check", "--run", str(run))
            self.assertEqual(checked.returncode, 1)
            payload = json.loads((run / "run.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["stages"]["evidence_ready"]["status"], "complete")
            self.assertEqual(payload["overall_status"], "in_progress")

    def test_application_requires_matching_profile_candidate_and_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest, source_ids = self.make_evidence(root)
            profile = self.reviewed_profile(root, source_ids)
            cards = root / "review" / "cards"
            candidate = root / "candidate" / "article.md"
            candidate.parent.mkdir(parents=True)
            candidate.write_text("这是一篇经过生成与复核的新主题候选文章。\n", encoding="utf-8")
            evaluation_dir = root / "evaluation"
            candidate_evidence = evaluation_dir / "candidate-analysis.json"
            write_json(
                candidate_evidence,
                {
                    "schema_version": 2,
                    "id": "candidate-article",
                    "status_scope": "candidate_text_reanalysis",
                    "distillation_status": "evidence_ready",
                    "semantic_review_required": ["compare candidate mechanisms with the reviewed profile"],
                    "observations": ["结构与节奏已复测"],
                },
            )
            objective = "围绕新主题生成一篇文章并验证画像中的表达机制"
            evaluation = evaluation_dir / "evaluation.json"
            profile_payload = json.loads(profile.read_text(encoding="utf-8"))
            prepared = run_script(
                "prepare_evaluation.py", str(profile), str(candidate),
                "--objective", objective, "--evidence", str(candidate_evidence),
                "--output", str(evaluation),
            )
            self.assertEqual(prepared.returncode, 0, prepared.stderr)
            evaluation_payload = json.loads(evaluation.read_text(encoding="utf-8"))
            self.assertEqual(evaluation_payload["profile_id"], profile_payload["profile_id"])
            for dimension in evaluation_payload["dimensions"]:
                dimension["verdict"] = "pass"
                dimension["evidence"] = [
                    {
                        "artifact": evaluation_payload["evidence_artifacts"][0],
                        "locator": "/observations/0",
                        "observation": f"候选复测记录覆盖了{dimension['name']}的可定位结果。",
                    }
                ]
                dimension["notes"] = "候选满足画像中的具体要求"
            evaluation_payload["review_status"] = "complete"
            evaluation_payload["overall_verdict"] = "pass"
            write_json(evaluation, evaluation_payload)
            contract = run_script("validate_contract.py", "evaluation", str(evaluation))
            self.assertEqual(contract.returncode, 0, contract.stdout + contract.stderr)
            run = root / "run"
            self.assertEqual(run_script("workflow_gate.py", "init", "--output", str(run), "--objective", objective, "--mode", "application", "--domains", "document").returncode, 0)
            for artifact_type, path in (
                ("manifest", manifest), ("cards", cards), ("profile", profile),
                ("candidate", candidate), ("evaluation", evaluation),
            ):
                registered = run_script("workflow_gate.py", "register", "--run", str(run), "--type", artifact_type, "--path", str(path))
                self.assertEqual(registered.returncode, 0, registered.stderr)
            checked = run_script("workflow_gate.py", "check", "--run", str(run))
            self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
            payload = json.loads((run / "run.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["overall_status"], "complete")

    def test_gate_rejects_profile_support_not_present_in_cards(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest, source_ids = self.make_evidence(root)
            profile = self.reviewed_profile(root, source_ids)
            profile_payload = json.loads(profile.read_text(encoding="utf-8"))
            profile_payload["stable_patterns"][0]["key"] = "writing.fabricated.pattern"
            write_json(profile, profile_payload)
            run = root / "run"
            objective = "提取两篇来源中的稳定表达机制并形成可迁移画像"
            self.assertEqual(
                run_script(
                    "workflow_gate.py", "init", "--output", str(run),
                    "--objective", objective, "--mode", "distillation", "--domains", "document",
                ).returncode,
                0,
            )
            for artifact_type, artifact_path in (
                ("manifest", manifest),
                ("cards", root / "review" / "cards"),
                ("profile", profile),
            ):
                self.assertEqual(
                    run_script(
                        "workflow_gate.py", "register", "--run", str(run),
                        "--type", artifact_type, "--path", str(artifact_path),
                    ).returncode,
                    0,
                )
            checked = run_script("workflow_gate.py", "check", "--run", str(run))
            self.assertEqual(checked.returncode, 1)
            payload = json.loads((run / "run.json").read_text(encoding="utf-8"))
            self.assertTrue(
                any("completed card does not" in error for error in payload["stages"]["profile_ready"]["errors"])
            )


if __name__ == "__main__":
    unittest.main()
