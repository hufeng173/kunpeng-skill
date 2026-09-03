# 质量门

## 原则

质量门验证“产物与证据是否真的存在、契约是否完整、结论是否经过复核”，而不是验证标题或关键词出现。脚本能检查结构一致性；语义正确、审美、因果和可迁移性仍由宿主实际查看证据后判断。

## 状态门

- 提取器成功只得到 `evidence_ready`。
- 事实卡模板、空字段、占位值或无证据定位不能进入聚合；排除项必须有 excluded 卡和具体理由。
- 自动画像始终是 `draft`；未解决冲突、未改媒介契约不能标 `reviewed`。
- 单样本画像只能叫对象配方，不能叫创作者/作者/品牌稳定风格。
- 应用任务没有候选复测证据和分维度验收不能标 `complete`。

使用运行目录持久化质量状态：

```bash
python scripts/kunpeng.py gate init --output <运行目录> --objective "具体目标" --mode <distillation|application> --domains <类型>
python scripts/kunpeng.py gate register --run <运行目录> --type manifest --path <证据目录/manifest.json>
python scripts/kunpeng.py gate register --run <运行目录> --type cards --path <复核目录/cards>
python scripts/kunpeng.py gate register --run <运行目录> --type profile --path <profile.json>
python scripts/kunpeng.py gate check --run <运行目录>
```

应用模式还要登记 `candidate` 和 `evaluation`。质量门会检查 manifest 与 analysis 对应、来源 ID 覆盖、事实卡是否引用注册分析和真实文件、画像支持来源是否继承自事实卡、候选非空、画像 ID/目标/候选路径一致，以及每项评价定位是否落在已声明的复测证据中。

## 证据门

- manifest 是否写明 `status_scope` 和 `distillation_status=evidence_ready`？
- 每项 analysis 是否存在、ID 一致并列出语义复核任务？
- 是否处理全部来源，失败项是否被修复而非静默忽略？
- 证据定位是否可回到页、段、时间、镜头、区域、状态或代码位置？
- 是否记录去重、排除、异常和覆盖范围？
- 需要宿主查看的原文、原图、连续帧、音频或交互是否真的查看？

## 规律门

每条规律必须回答：

1. 观察到了什么关系？
2. 它为何或如何产生效果？
3. 在什么条件触发？
4. 参数、顺序、比例、方向或阈值是什么？
5. 何时不适用，反例是什么？
6. 哪些独立来源支持，置信度为何？
7. 它是稳定规律、条件规律还是单项观察？

“简洁、专业、电影感、用户友好、保持一致”不能单独通过。频率高也不自动等于关键规律；可能只是平台模板或题材要求。

## 来源专属门

### 仓库与项目

检查真实入口、用户主线、数据/控制流、状态变化、外部边界、错误路径和测试。README、依赖名和未接入代码不能替代实现证据。

### 网站、App、UI 和动效

检查代表性任务和前后状态，而不只看首页或静态截图。记录视口、登录态和未覆盖权限；动效要有触发、前态、过渡、后态和降级。

### 图片与品牌

查看原图并区分内容、构图、成像、设计、品牌和媒介适配。重复裁切不算独立支持，专属资产不得写成可迁移规则。

### 视频与音频

覆盖完整时长和关键转折。视频用连续阶段区分相机/主体/转场；音频区分讲话、音乐、环境、拟音和静默。数值候选不能冒充语义或意图。

### 文章、书籍和课程

去重、分组并读取全局与关键分块。区分主题与稳定表达，区分事实、作者主张和推断；知识方法要包含前提、案例、反例、练习或使用边界。

## 画像门

- `source_count` 与唯一 `source_ids` 是否一致？
- stable 模式是否至少由两个独立来源支持？
- statement variants 和矛盾是否已解决或条件化？
- 题材、平台、时期和协作者因素是否移出稳定规律？
- `review_summary` 是否说明关键取舍？
- generation contract 是否针对实际媒介，并与画像一起标 `reviewed`？
- 不可变机制、内容变量、执行顺序、负向约束和验收是否均为实质内容？

## 应用门

- 新任务事实是否与来源画像分离？
- 是否先结构后局部，避免只套表面词汇、色调或镜头效果？
- 候选是否经过同路线重新分析？
- 验收是否至少三项独立维度，并用 artifact、locator、observation 引用候选复测证据？
- 硬约束、事实、连续性、原创性或安全边界任一失败时，总体是否失败？
- 修改是否针对失败维度，而非无差别重写？

## 其他产物校验

```bash
python scripts/kunpeng.py validate <产物> --profile general
python scripts/kunpeng.py validate <收录文件> --profile collection
python scripts/kunpeng.py validate <蒸馏报告> --profile distillation
python scripts/kunpeng.py validate <方案目录> --profile product-plan --index <索引文件>
python scripts/kunpeng.py validate <Skill目录> --profile skill
```

Markdown 校验是结构 linter，不能替代 workflow gate 和语义复核。

JSON 契约可单独检查：

```bash
python scripts/kunpeng.py contract card <cards目录>
python scripts/kunpeng.py contract profile <profile.json>
python scripts/kunpeng.py contract evaluation <evaluation.json>
```
