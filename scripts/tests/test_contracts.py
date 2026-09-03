from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[1]
TEST_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(TEST_DIR))

from profile_contract import validate_card, validate_evaluation, validate_profile  # noqa: E402
from support import valid_card, write_json  # noqa: E402


class ContractTests(unittest.TestCase):
    def test_empty_card_is_rejected(self) -> None:
        card = {
            "schema_version": 1,
            "source_id": "source-a",
            "source_type": "document",
            "source_label": "空报告",
            "analysis_artifact": "analysis.json",
            "review_status": "complete",
            "summary": "无",
            "patterns": [],
            "variables": [],
            "exceptions": [],
            "limitations": [],
        }
        errors = validate_card(card)
        self.assertTrue(any("summary" in error for error in errors))
        self.assertTrue(any("patterns" in error for error in errors))

    def test_evidenced_card_passes(self) -> None:
        self.assertEqual(validate_card(valid_card("source-a")), [])

    def test_excluded_card_requires_reason_but_no_pattern(self) -> None:
        card = valid_card("source-duplicate")
        card["review_status"] = "excluded"
        card["exclusion_reason"] = "这是 source-a 的同稿转载，不能作为独立支持来源。"
        card["patterns"] = []
        self.assertEqual(validate_card(card), [])

    def test_profile_builder_keeps_singletons_out_of_stable_patterns(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cards = root / "cards"
            write_json(cards / "a.json", valid_card("source-a"))
            write_json(cards / "b.json", valid_card("source-b"))
            singleton = valid_card("source-c")
            singleton["patterns"][0]["key"] = "writing.ending.reader-action"
            singleton["patterns"][0]["statement"] = "结尾把抽象判断转换成读者下一步可以执行的动作。"
            write_json(cards / "c.json", singleton)
            excluded = valid_card("source-duplicate")
            excluded["review_status"] = "excluded"
            excluded["exclusion_reason"] = "这是 source-a 的同稿转载，不能作为独立支持来源。"
            excluded["patterns"] = []
            write_json(cards / "duplicate.json", excluded)
            output = root / "profile.draft.json"
            result = subprocess.run(
                [sys.executable, str(SCRIPT_DIR / "build_profile.py"), str(cards), "--output", str(output)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            profile = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(profile["review_status"], "draft")
            self.assertEqual(profile["generation_contract"]["review_status"], "draft")
            self.assertEqual(profile["stable_patterns"][0]["support_count"], 2)
            self.assertEqual(profile["observations"][0]["support_count"], 1)
            self.assertEqual(profile["excluded_sources"][0]["source_id"], "source-duplicate")
            self.assertEqual(validate_profile(profile, require_reviewed=False), [])
            self.assertTrue(validate_profile(profile, require_reviewed=True))

    def test_evaluation_requires_reanalysis_evidence(self) -> None:
        evaluation = {
            "schema_version": 1,
            "review_status": "complete",
            "profile_id": "writing-profile",
            "objective": "围绕新主题生成并验证一篇文章",
            "candidate": "candidate.md",
            "evidence_artifacts": [],
            "dimensions": [
                {
                    "name": f"dimension-{index}",
                    "hard_constraint": False,
                    "verdict": "pass",
                    "evidence": [
                        {
                            "artifact": "candidate-analysis.json",
                            "locator": f"/checks/{index}",
                            "observation": "候选复测结果给出了可定位的具体观察。",
                        }
                    ],
                    "notes": "符合画像要求",
                }
                for index in range(3)
            ],
            "overall_verdict": "pass",
            "required_revisions": [],
        }
        self.assertTrue(any("evidence_artifacts" in error for error in validate_evaluation(evaluation)))

    def test_evaluation_rejects_free_text_evidence_and_unresolved_hard_gate(self) -> None:
        evaluation = {
            "schema_version": 1,
            "review_status": "complete",
            "profile_id": "writing-profile",
            "objective": "围绕新主题生成并验证一篇文章",
            "candidate": "candidate.md",
            "evidence_artifacts": ["candidate-analysis.json"],
            "dimensions": [
                {
                    "name": f"dimension-{index}",
                    "hard_constraint": index == 0,
                    "verdict": "not_applicable" if index == 0 else "pass",
                    "evidence": "只有一句无法定位的自我评价",
                    "notes": "需要按实际证据进一步核对",
                }
                for index in range(3)
            ],
            "overall_verdict": "pass",
            "required_revisions": [],
        }
        errors = validate_evaluation(evaluation)
        self.assertTrue(any("located observation" in error for error in errors))
        self.assertTrue(any("hard constraint is unresolved" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
