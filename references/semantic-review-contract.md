# 语义事实卡与画像契约

## 目的

确定性指标只能证明文件、像素、帧、声音或代码结构发生了什么。宿主必须把需要理解的判断写成结构化事实卡，并把每条规律绑定到真实证据。没有完成事实卡，不得进入画像阶段。

## 准备事实卡

```bash
python scripts/kunpeng.py prepare-review <证据目录/manifest.json> --output <复核目录>
```

读取 `review-tasks.json`，逐项打开任务要求的原始证据，再填写 `cards/*.json`。不要只根据分析摘要补卡。

填写后可直接检查：

```bash
python scripts/kunpeng.py contract card <复核目录/cards>
```

重复、转载、模板副本、错误作者或与目标无关的来源也必须有卡，但使用 `review_status: "excluded"`、实质性的 `summary` 和 `exclusion_reason`，并保持 `patterns: []`。这样质量门能证明每个来源都被处理，同时不让重复项抬高支持度。

## 事实卡必填结构

```json
{
  "schema_version": 1,
  "source_id": "稳定来源编号",
  "source_type": "repository|website|app|ui|image|brand|video|audio|article|document|book|course|mixed|other",
  "source_label": "来源标签",
  "analysis_artifact": "对应分析文件",
  "review_status": "complete",
  "summary": "来源的目标、结构、主线和关键限制",
  "patterns": [
    {
      "key": "writing.hook.counterintuitive-claim",
      "dimension": "开头机制",
      "statement": "可观察、可迁移的规律",
      "mechanism": "规律为什么有效以及改变了什么",
      "trigger": "何时触发",
      "boundary": "何时不用",
      "scope": "global|stage|local|conditional|exception",
      "parameters": {},
      "confidence": 0.85,
      "transferable": true,
      "evidence": [
        {
          "artifact": "过程文件",
          "locator": "页码、段落、时间、镜头、区域、文件与行等",
          "observation": "不复制长原文的证据说明"
        }
      ]
    }
  ],
  "variables": ["由主题、主体、平台或任务改变的内容"],
  "exceptions": ["反例或不符合主要规律的情况"],
  "limitations": ["未覆盖或不确定内容"]
}
```

## 写卡规则

- `key` 表示同一机制的稳定标识。不同来源中的相同机制使用相同 key，便于自动聚合。
- `source_label` 和 `analysis_artifact` 必须回到当前 manifest 中的真实来源与分析文件。
- `statement` 写观察到的关系，不写“高级、专业、有氛围”等形容词。
- `mechanism` 说明输入、动作和效果之间的关系。
- `trigger`、`boundary` 和 `transferable` 必填；不确定时降低置信度并解释，不能填占位值。
- `parameters` 优先保存数量、比例、范围、顺序、方向、速度和触发条件。
- `evidence` 至少一项；稳定规律应尽量有多个来源证据。
- 不确定判断降低 `confidence` 并写入 `limitations`，不要补全猜测。
- 来源专属事实、Logo、人物、长句和故事标为变量或不可迁移内容。

## 构建画像

```bash
python scripts/kunpeng.py build-profile <复核目录/cards> --output <profile.draft.json>
```

脚本根据支持来源数量和占比，把模式分为：

- `stable_patterns`：多来源、高支持度候选规律。
- `conditional_patterns`：只在部分条件下出现的规律。
- `observations`：单来源或低支持度观察。

自动聚合结果始终是 `draft`。宿主必须：

1. 检查证据定位是否真实支持结论。
2. 合并同义模式并拆开含义不同的同名模式。
3. 把题材、平台和阶段造成的特征移出稳定规律。
4. 保留反例、冲突、条件和成本。
5. 完成媒介专属的 `generation_contract`，指定 `mode` 和 `medium`。
6. 清空未解决的 `statement_variants`，填写实质性的 `review_summary`。
7. 将画像和 generation contract 的 `review_status` 都改为 `reviewed`。

```bash
python scripts/kunpeng.py contract profile <profile.json>
```

已复核画像的关键字段：

```json
{
  "schema_version": 1,
  "profile_id": "author-x-writing-profile",
  "domain": "writing",
  "source_count": 2,
  "source_ids": ["source-a", "source-b"],
  "review_status": "reviewed",
  "review_summary": "已核对主题干扰、栏目分支、反例和全部证据定位。",
  "stable_patterns": [
    {
      "key": "writing.hook.evidence-turn",
      "dimension": "开头与论证机制",
      "statement": "先提出反常识判断，再用多类证据完成转折",
      "statement_variants": [],
      "mechanisms": ["认知落差促使读者继续，并由证据修正判断"],
      "triggers": ["存在常见误解且证据充足"],
      "boundaries": ["证据不足时不用"],
      "parameters": [{"opening_order": ["counter_claim", "evidence"]}],
      "support_count": 2,
      "support_share": 1.0,
      "mean_confidence": 0.86,
      "source_ids": ["source-a", "source-b"],
      "evidence": [
        {"source_id": "source-a", "artifact": "a/content.txt", "locator": "paragraphs 1-4", "observation": "判断之后连续出现两类证据"},
        {"source_id": "source-b", "artifact": "b/content.txt", "locator": "paragraphs 2-5", "observation": "另一主题也采用同一证据转折"}
      ]
    }
  ],
  "conditional_patterns": [],
  "observations": [],
  "excluded_sources": [],
  "generation_contract": {
    "review_status": "reviewed",
    "mode": "transfer",
    "medium": "writing",
    "target_effect": "用相同论证机制完成一个事实独立的新主题文章",
    "invariants": ["保留开头认知落差和证据转折关系"],
    "variables": ["替换主题、事实、人物和案例"],
    "sequence": ["先建立事实包，再按篇章职责写作并复测"],
    "negative_constraints": ["不复制来源长句、故事和专属事实"],
    "acceptance": ["每个阶段都能在候选中定位到对应机制"]
  }
}
```

## 候选验收

应用画像生成候选后，使用相同分析路线重新采集候选证据，再创建待填写模板：

```bash
python scripts/kunpeng.py prepare-evaluation <profile.json> <候选> --objective "与run.json完全一致的目标" --evidence <候选复测产物> --output <evaluation.json>
```

填写后的结构为：

```json
{
  "schema_version": 1,
  "review_status": "complete",
  "profile_id": "采用的画像编号",
  "objective": "必须与 run.json 中的目标一致",
  "candidate": "相对 evaluation.json 的候选路径",
  "evidence_artifacts": ["相对 evaluation.json 的候选复测产物"],
  "dimensions": [
    {
      "name": "结构机制",
      "hard_constraint": true,
      "verdict": "pass",
      "evidence": [
        {
          "artifact": "candidate-analysis.json",
          "locator": "/structure/paragraph_roles",
          "observation": "候选分析中的段落职责与画像逐项对应"
        }
      ],
      "notes": "开头、证据转折和收束均符合"
    },
    {
      "name": "事实正确",
      "hard_constraint": true,
      "verdict": "pass",
      "evidence": [
        {
          "artifact": "fact-check.json",
          "locator": "/claims",
          "observation": "事实核验记录覆盖所有人物、数据和引用"
        }
      ],
      "notes": "没有继承来源主题事实"
    },
    {
      "name": "节奏与语气",
      "hard_constraint": false,
      "verdict": "pass",
      "evidence": [
        {
          "artifact": "candidate-analysis.json",
          "locator": "/style/rhythm",
          "observation": "候选统计和逐段复核记录一致"
        }
      ],
      "notes": "句段节奏符合条件化规则"
    }
  ],
  "overall_verdict": "pass|fail",
  "required_revisions": []
}
```

至少检查三个互相独立的维度。每项 `evidence` 都是结构化数组，必须给出已声明复测产物内的真实 `artifact`、可定位 `locator` 和短 `observation`；自由文本自评不能通过。路径必须指向实际存在并已登记的候选和复测证据，复测包中至少有一份 JSON 标记 `distillation_status=evidence_ready`。任一硬约束未通过时总体不得通过。失败结果必须给出可执行的定向修改，不进行无差别重写。

```bash
python scripts/kunpeng.py contract evaluation <evaluation.json>
```

## 禁止的通过方式

- 只出现章节名称或关键词。
- 使用“无、未知、待补充”填满字段。
- 只有一个总相似度分数。
- 只看提示词是否完整，不分析生成结果。
- 让同一个判断在没有重新查看证据的情况下同时充当提取和质检。
