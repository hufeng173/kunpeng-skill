from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[1]


def run_script(name: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT_DIR / name), *arguments],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


class CompareAndValidateTests(unittest.TestCase):
    def test_empty_distillation_report_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            report = Path(temporary) / "empty.md"
            report.write_text(
                "目标模式：同风格\n可执行规则：无\n不可变项：无\n内容变量：无\n负向约束：无\n适用边界：无\n验收表：无\n",
                encoding="utf-8",
            )
            result = run_script("validate_output.py", str(report), "--profile", "distillation")
            self.assertEqual(result.returncode, 1)
            self.assertIn("placeholder", result.stdout)

    def test_text_compare_never_returns_automatic_pass(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reference = root / "reference.txt"
            candidate = root / "candidate.txt"
            reference.write_text("但是，决策不能靠感觉。因为资源有限，所以先比较代价。最后，选择可逆方案。", encoding="utf-8")
            candidate.write_text("微风穿过旧城的巷口。月光落在石阶上，旅人安静地走向远方。夜色缓慢合拢。", encoding="utf-8")
            result = run_script("compare_reproduction.py", str(reference), str(candidate), "--mode", "style")
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertNotIn("diagnostic_score", payload)
            self.assertIn("form_proxy_score", payload)
            self.assertFalse(payload["automatic_pass_allowed"])
            self.assertEqual(payload["automatic_verdict"], "not_evaluated")
            self.assertIn("rhetorical_marker_profile", payload["metrics"])


if __name__ == "__main__":
    unittest.main()
