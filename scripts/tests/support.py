from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def valid_card(source_id: str, statement: str = "先提出反常识判断，再用可验证证据拆解原因。") -> dict[str, Any]:
    return {
        "schema_version": 1,
        "source_id": source_id,
        "source_type": "document",
        "source_label": f"source-{source_id}",
        "analysis_artifact": f"../../evidence/{source_id}.json",
        "review_status": "complete",
        "summary": "这份来源围绕一个明确问题展开，并通过判断、证据和边界完成论证。",
        "patterns": [
            {
                "key": "writing.hook.evidence-turn",
                "dimension": "开头与论证机制",
                "statement": statement,
                "mechanism": "先制造认知落差，再提供证据，使读者继续阅读并修正原判断。",
                "trigger": "主题存在常见误解且后文有足够证据时",
                "boundary": "事实证据不足或结论本身没有反差时不使用",
                "scope": "stage",
                "parameters": {"stage": "opening", "evidence_blocks_min": 2},
                "confidence": 0.86,
                "transferable": True,
                "evidence": [
                    {
                        "artifact": f"../../evidence/{source_id}/content.txt",
                        "locator": "paragraphs 1-4",
                        "observation": "开头先给出与常见看法相反的判断，随后连续给出两类证据。",
                    }
                ],
            }
        ],
        "variables": ["主题事实、人物和案例必须替换"],
        "exceptions": ["纯通知文本不采用该开头机制"],
        "limitations": ["当前证据未覆盖该作者的短视频口播"],
    }


def evidence_analysis(source_id: str) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "id": source_id,
        "status": "complete",
        "status_scope": "text_extraction_only",
        "extraction_status": "complete",
        "distillation_status": "evidence_ready",
        "semantic_review_required": ["read the source and create an evidence-linked card"],
    }
