#!/usr/bin/env python3
"""Validate Kunpeng skill files and user-facing Markdown outputs."""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path
from typing import Optional


FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
MARKDOWN_LINK_RE = re.compile(r"\[[^\]]+\]\((references/[^)#]+)")
WINDOWS_PATH_RE = re.compile(r"(?i)(?<![A-Za-z0-9])[A-Z]:\\[^\s`]+")
UNIX_HOME_RE = re.compile(r"(?<![A-Za-z0-9])/(?:Users|home)/[^/\s`]+")
SECRET_PATTERNS = {
    "OpenAI-style key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    "GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    "Bearer token": re.compile(r"(?i)authorization\s*:\s*bearer\s+[^\s`]+"),
    "private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "webhook URL": re.compile(r"https://(?:hooks\.slack\.com/services|discord(?:app)?\.com/api/webhooks)/[^\s`]+", re.IGNORECASE),
    "credential assignment": re.compile(
        r"(?i)\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret)"
        r"\s*[:=]\s*[\"']?[^\s,;\"']{12,}"
    ),
}
COLLECTION_HEADINGS = [
    "项目介绍",
    "适合参考的方向",
    "核心功能",
    "技术栈",
    "项目结构",
    "核心流程",
    "数据流",
    "外部依赖",
    "UI/交互亮点",
    "值得借鉴",
    "整理重点",
    "缺点",
    "运行方式",
]
PLAN_CONCEPTS = {
    "目标用户": ("目标用户", "用户与问题", "用户、问题", "用户场景"),
    "产品形态": ("产品形态", "产品载体", "推荐形态"),
    "核心路径": ("核心用户路径", "核心流程", "核心循环"),
    "MVP": ("MVP", "首版范围", "首版功能"),
    "视觉交互": ("视觉", "UI", "交互"),
    "技术方案": ("技术方案", "技术栈"),
    "数据": ("数据流", "数据模型", "数据与"),
    "外部服务": ("外部服务", "第三方服务", "外部依赖"),
    "实施": ("实施顺序", "开发阶段", "里程碑", "实施"),
    "验收": ("验收标准", "验收"),
    "风险": ("风险与取舍", "风险"),
}
DISTILLATION_CONCEPTS = {
    "目标模式": ("忠实重建", "同风格", "目标模式", "再生成目标"),
    "可执行规律": ("可执行规则", "生成规则", "阶段规则", "视觉规则", "表达规则", "镜头规则", "方法卡"),
    "不可变项": ("不可变项", "固定项", "必须保留"),
    "内容变量": ("内容变量", "可变项", "允许替换"),
    "负向约束": ("负向约束", "禁止项", "不要出现", "Don't"),
    "适用边界": ("适用边界", "不适用", "限制", "失败模式", "边界与代价"),
    "验收": ("验收表", "验收标准", "复核", "通过条件"),
}
EMPTY_VALUE_RE = re.compile(
    r"(?im)^\s*(?:[-*+]\s*)?(?:#{1,6}\s*)?[^：:\n]{1,30}[：:]\s*"
    r"(?:无|未知|待补充|待确认|暂无|不详|未填写|none|null|n/?a|todo|tbd)\s*[。.]?\s*$"
)
PLACEHOLDER_VALUES = {
    "", "-", "无", "未知", "待补充", "待确认", "暂无", "不详", "未填写",
    "none", "null", "n/a", "na", "todo", "tbd",
}
SOURCE_DISCLOSURE_PATTERNS = {
    "collection filename": re.compile(r"[\w\u3400-\u4dbf\u4e00-\u9fff-]+-收录\.md", re.IGNORECASE),
    "source section": re.compile(r"(?m)^#{1,6}\s*(?:参考来源|资料来源|引用依据|证据索引)\s*$"),
    "attribution wording": re.compile(r"(?:借鉴自|参考了.{0,20}(?:项目|资料)|来源映射|检索分数)"),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate Markdown output contracts.")
    parser.add_argument("path", type=Path)
    parser.add_argument(
        "--profile",
        choices=("general", "collection", "distillation", "product-plan", "skill"),
        default="general",
    )
    parser.add_argument("--max-lines", type=int, default=250)
    parser.add_argument("--index", type=Path, help="Optional library index for attribution checks.")
    return parser.parse_args()


def markdown_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path] if path.suffix.casefold() == ".md" else []
    return sorted(
        file
        for file in path.rglob("*.md")
        if ".kunpeng-cache" not in file.parts and file.is_file()
    )


def read_text(path: Path, errors: list[str]) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        errors.append(f"{path}: cannot read as UTF-8 ({exc})")
        return ""


def validate_common(
    path: Path, text: str, max_lines: Optional[int], errors: list[str]
) -> None:
    line_count = len(text.splitlines())
    if max_lines is not None and line_count > max_lines:
        errors.append(f"{path}: {line_count} lines exceeds {max_lines}")

    if WINDOWS_PATH_RE.search(text) or UNIX_HOME_RE.search(text):
        errors.append(f"{path}: contains a personal absolute path")

    for label, pattern in SECRET_PATTERNS.items():
        if pattern.search(text):
            errors.append(f"{path}: contains possible {label}")


def validate_collection(path: Path, text: str, errors: list[str]) -> None:
    headings = {
        re.sub(r"^\d+[.、]\s*", "", match.group(1).strip())
        for match in re.finditer(r"(?m)^##\s+(.+?)\s*$", text)
    }
    missing = [heading for heading in COLLECTION_HEADINGS if heading not in headings]
    if missing:
        errors.append(f"{path}: missing collection headings: {', '.join(missing)}")


def library_source_names(index_path: Optional[Path], errors: list[str]) -> list[str]:
    if index_path is None:
        return []
    try:
        data = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        errors.append(f"cannot read library index for attribution checks: {exc}")
        return []

    names: set[str] = set()
    for document in data.get("documents", []):
        for key in ("display_name", "subject_identity"):
            value = str(document.get(key, "")).strip()
            if len(value) >= 3:
                names.add(value)
    return sorted(names, key=len, reverse=True)


def validate_product_plan(
    text: str, errors: list[str], index_path: Optional[Path] = None
) -> None:
    for label, pattern in SOURCE_DISCLOSURE_PATTERNS.items():
        if pattern.search(text):
            errors.append(f"product plan exposes internal source information: {label}")

    for label, alternatives in PLAN_CONCEPTS.items():
        if not any(term in text for term in alternatives):
            errors.append(f"product plan is missing: {label}")

    attribution_words = r"(?:参考|借鉴|类似|像|来自|按照|仿照|案例)"
    for name in library_source_names(index_path, errors):
        escaped = re.escape(name)
        if re.search(
            rf"(?:{attribution_words}.{{0,24}}{escaped}|{escaped}.{{0,24}}{attribution_words})",
            text,
            re.IGNORECASE,
        ):
            errors.append("product plan attributes a recommendation to a library source")
            break


def validate_distillation(text: str, errors: list[str]) -> None:
    compact = re.sub(r"\s+", "", text)
    if len(compact) < 240:
        errors.append("distillation output is too short to contain an executable specification")
    if EMPTY_VALUE_RE.search(text):
        errors.append("distillation output contains an empty or placeholder field")

    lines = text.splitlines()
    substantive_lines = [
        line for line in lines
        if len(re.sub(r"^[\s#>*+\-\d.、)]+", "", line).strip()) >= 8
    ]
    if len(substantive_lines) < 8:
        errors.append("distillation output lacks substantive rules and checks")

    for label, alternatives in DISTILLATION_CONCEPTS.items():
        if not any(term.casefold() in text.casefold() for term in alternatives):
            errors.append(f"distillation output is missing: {label}")
            continue
        if not concept_has_substance(lines, alternatives, minimum=2 if label == "目标模式" else 8):
            errors.append(f"distillation output has no substantive content for: {label}")


def concept_has_substance(
    lines: list[str], alternatives: tuple[str, ...], minimum: int
) -> bool:
    for index, raw_line in enumerate(lines):
        stripped = raw_line.strip()
        for term in alternatives:
            match = re.match(
                rf"^(?:[-*+]\s*)?(?:#{{1,6}}\s*)?(?:\d+[.、)]\s*)?{re.escape(term)}(?:\s*[：:]\s*)?(?P<content>.*)$",
                stripped,
                re.IGNORECASE,
            )
            if not match:
                continue
            is_heading = stripped.startswith("#")
            candidate = match.group("content").strip(" ：:")
            if is_heading:
                block: list[str] = []
                for following in lines[index + 1:]:
                    if following.lstrip().startswith("#"):
                        break
                    block.append(following)
                candidate = " ".join(block)
            normalized = re.sub(r"[`*_>#\-]+", " ", candidate)
            normalized = " ".join(normalized.split()).strip(" ：:。.")
            if len(normalized) >= minimum and normalized.casefold() not in PLACEHOLDER_VALUES:
                return True
            if minimum <= 2 and term.casefold() in {"忠实重建", "同风格"}:
                return True
    return False


def validate_skill(root: Path, texts: dict[Path, str], errors: list[str]) -> None:
    skill_file = root / "SKILL.md"
    text = texts.get(skill_file)
    if text is None:
        errors.append(f"{root}: missing SKILL.md")
        return

    frontmatter = FRONTMATTER_RE.match(text)
    if not frontmatter:
        errors.append(f"{skill_file}: invalid YAML frontmatter block")
    else:
        keys = [
            match.group(1)
            for match in re.finditer(r"(?m)^([A-Za-z0-9_-]+)\s*:", frontmatter.group(1))
        ]
        if set(keys) != {"name", "description"} or len(keys) != 2:
            errors.append(f"{skill_file}: frontmatter must contain only name and description")
        if not re.search(r"(?m)^name:\s*kunpeng-skill\s*$", frontmatter.group(1)):
            errors.append(f"{skill_file}: name must be kunpeng-skill")

    if "TODO" in text:
        errors.append(f"{skill_file}: contains TODO placeholder")

    required_markers = (
        "代码仓库、网站、App、UI/交互、图片与品牌、视频、音频、文章、文档、书籍、课程及混合素材",
        "这不是模型训练、微调或权重更新",
        "只有用户要求规划或开发新产品且关键条件缺失时才询问 1–5 个问题",
        "evidence_ready",
        "语义复核",
        "build-profile",
        "gate check",
        "complete",
        "Codex",
        "Claude Code",
        "WorkBuddy",
        "OpenCode",
        "Hermes",
    )
    for marker in required_markers:
        if marker not in text:
            errors.append(f"{skill_file}: missing required behavior marker: {marker}")

    for relative in MARKDOWN_LINK_RE.findall(text):
        if not (root / relative).is_file():
            errors.append(f"{skill_file}: missing linked reference {relative}")

    for script in sorted((root / "scripts").glob("*.py")):
        try:
            ast.parse(script.read_text(encoding="utf-8-sig"), filename=str(script))
        except (OSError, UnicodeError, SyntaxError) as exc:
            errors.append(f"{script}: invalid Python ({exc})")

    required_files = [
        root / "requirements-standard.txt",
        root / "scripts" / "kunpeng.py",
        root / "scripts" / "capability_probe.py",
        root / "scripts" / "analyze_videos.py",
        root / "scripts" / "analyze_images.py",
        root / "scripts" / "analyze_documents.py",
        root / "scripts" / "analyze_repository.py",
        root / "scripts" / "register_host_evidence.py",
        root / "scripts" / "analyze_audio.py",
        root / "scripts" / "prepare_review.py",
        root / "scripts" / "build_profile.py",
        root / "scripts" / "prepare_evaluation.py",
        root / "scripts" / "validate_contract.py",
        root / "scripts" / "profile_contract.py",
        root / "scripts" / "workflow_gate.py",
        root / "scripts" / "merge_manifests.py",
        root / "scripts" / "compare_reproduction.py",
    ]
    for required in required_files:
        if not required.is_file():
            errors.append(f"{root}: missing required standard runtime file {required.name}")

    metadata = root / "agents" / "openai.yaml"
    if metadata.exists():
        metadata_text = read_text(metadata, errors)
        if "$kunpeng-skill" not in metadata_text:
            errors.append(f"{metadata}: default_prompt must mention $kunpeng-skill")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parse_args()
    target = args.path.resolve()
    if not target.exists():
        print(f"ERROR: path does not exist: {target}")
        return 2

    files = markdown_files(target)
    if args.profile == "collection" and target.is_dir():
        files = [file for file in files if file.name.endswith("-收录.md")]
    if not files:
        print(f"ERROR: no Markdown files found: {target}")
        return 2

    errors: list[str] = []
    texts: dict[Path, str] = {}
    for file in files:
        text = read_text(file, errors)
        texts[file] = text
        line_limit = None if args.profile == "skill" and file.name == "README.md" else args.max_lines
        validate_common(file, text, line_limit, errors)
        if args.profile == "collection":
            validate_collection(file, text, errors)

    aggregate = "\n".join(texts.values())
    if args.profile == "product-plan":
        validate_product_plan(aggregate, errors, args.index)
    elif args.profile == "distillation":
        validate_distillation(aggregate, errors)
    elif args.profile == "skill":
        root = target if target.is_dir() else target.parent
        validate_skill(root, texts, errors)

    if errors:
        print(f"FAIL: {len(errors)} issue(s)")
        for error in errors:
            print(f"- {error}")
        return 1

    print(f"PASS: {len(files)} Markdown file(s), profile={args.profile}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
