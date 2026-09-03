# 本地工具链与配置

## 边界

标准版只在本机执行，不调用 OpenAI、Claude、Gemini、Seedance 或其他托管推理 API，不读取 API Key，也不产生按次调用费用。宿主智能体负责理解和写最终蒸馏文档；脚本只做确定性提取与统计。

首次安装 Python 包以及首次加载 faster-whisper、PaddleOCR 模型可能从公开模型仓库下载文件，会消耗网络和磁盘，但不是付费推理。需要完全离线时先准备本地模型缓存，并在视频或音频命令中加 `--offline`。

## 运行环境

- 推荐 Python 3.10–3.12、64 位系统、8 GB 以上内存。
- FFmpeg 与 `ffprobe` 必须能从当前终端的 `PATH` 找到。
- Python 依赖必须装进 Skill 自己或工作区的虚拟环境，不安装为全局包。
- CPU 可以完成全部流程；NVIDIA GPU 可加速 ASR 和 OCR，但不是必需条件。
- 模型、过程文件和原素材都留在本地。不要把过程目录提交到公开仓库。

## 安装

在 `kunpeng-skill` 目录执行：

```bash
python -m venv .venv
```

Windows PowerShell：

```powershell
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements-standard.txt
.\.venv\Scripts\python.exe scripts\capability_probe.py --profile all --strict
```

Linux、macOS 或 WSL：

```bash
./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/python -m pip install -r requirements-standard.txt
./.venv/bin/python scripts/capability_probe.py --profile all --strict
```

安装后可统一使用 `python scripts/kunpeng.py ...`；入口会自动选择本 Skill 的 `.venv`。

FFmpeg 使用操作系统的软件源或官方构建安装。常见命令是 Windows `winget install Gyan.FFmpeg`、Ubuntu/WSL `sudo apt install ffmpeg`、macOS `brew install ffmpeg`。这些命令会修改系统，必须由使用者主动执行，智能体不得自行安装。

## 开源组件

| 组件 | 上游 | 作用与许可提醒 |
| --- | --- | --- |
| FFmpeg | [FFmpeg/FFmpeg](https://github.com/FFmpeg/FFmpeg) | 媒体探测、音轨和字幕提取；具体构建可能是 LGPL 或 GPL。 |
| faster-whisper | [SYSTRAN/faster-whisper](https://github.com/SYSTRAN/faster-whisper) | 本地 ASR，MIT；模型权重许可单独核对。 |
| PaddleOCR/PaddlePaddle | [PaddlePaddle/PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) | 本地 OCR，Apache-2.0；模型许可单独核对。 |
| PySceneDetect | [Breakthrough/PySceneDetect](https://github.com/Breakthrough/PySceneDetect) | 镜头边界，BSD-3-Clause。 |
| OpenCV | [opencv/opencv](https://github.com/opencv/opencv) | 抽帧、图像和复核指标，Apache-2.0。 |
| librosa | [librosa/librosa](https://github.com/librosa/librosa) | 节拍、响度和停顿统计，ISC。 |
| 文档解析器 | [pypdf](https://github.com/py-pdf/pypdf)、[python-docx](https://github.com/python-openxml/python-docx)、[python-pptx](https://github.com/scanny/python-pptx)、[trafilatura](https://github.com/adbar/trafilatura) | PDF、Office 和 HTML 本地提取；分发前分别核对许可证。 |

仓库盘点、宿主证据登记、事实卡、画像聚合和质量门只使用 Python 标准库，不增加模型或 API 依赖。网站/App/UI 的实际操作由当前宿主浏览器能力完成；脚本只登记已经保存的证据，不能代替浏览。

## PaddleOCR

`requirements-standard.txt` 默认安装 CPU 版 `paddlepaddle`。GPU 用户应按 PaddlePaddle 官方与本机 CUDA 对照表，在虚拟环境中替换为匹配的 `paddlepaddle-gpu`，然后再运行能力探测；不要同时保留 CPU 和 GPU 包。

首次 OCR 会准备模型。完全离线机器应在联网环境预热相同语言模型后迁移 Paddle 缓存，或按 PaddleOCR 当前版本指定本地模型目录。脚本兼容 PaddleOCR 2.x 与 3.x 常见接口，但具体模型文件仍须匹配已安装版本。

## faster-whisper

默认模型为 `small`，CPU 使用 `int8`，检测到 CUDA 时使用 `float16`。可按素材调整：

- `tiny`/`base`：快，适合粗筛。
- `small`：标准默认，速度与准确率平衡。
- `medium`/`large-v3`：更准但显存、内存和时间明显增加。
- 本地模型：把 `--whisper-model` 设为模型目录，并加 `--offline`。

用 `--model-cache <目录>` 固定下载缓存。模型权重许可证需单独核对。

## Agent 挂载

Skill 采用通用 `SKILL.md + references/ + scripts/` 结构。把完整目录复制或建立目录链接到宿主当前版本声明的 Skill 搜索目录，不要只复制 `SKILL.md`。

| 宿主 | 常见位置 | 配置要点 |
| --- | --- | --- |
| Codex | `$CODEX_HOME/skills/kunpeng-skill` | 保留 `agents/openai.yaml`；项目级目录以当前 Codex 文档为准。 |
| Claude Code | `.claude/skills/kunpeng-skill` 或用户级 Skills 目录 | 直接识别 `SKILL.md`，脚本仍使用本 Skill 的 `.venv`。 |
| OpenCode | `.opencode/skills/kunpeng-skill` 或其兼容 Skills 目录 | 在权限配置中允许读取素材和执行本地 Python/FFmpeg。 |
| WorkBuddy | 当前版本配置的 Skills 目录 | 指向整个目录；不要改写内部相对路径。 |
| Hermes | WSL 内 Hermes 配置的 Skills 目录 | Skill 和 `.venv` 放在 WSL 文件系统性能更好；Windows 素材可通过 `/mnt/<盘符>/...` 读取。 |

不同版本的搜索目录可能变化，以宿主官方文档和实际 `skill list`/诊断结果为准。不能自动发现 Skill 的宿主，可在其 `AGENTS.md` 中要求任务命中时读取本目录的 `SKILL.md`。

## 智能体执行约定

1. 从当前 `SKILL.md` 的真实位置解析 `scripts/`，不要假设当前工作目录就是 Skill 目录。
2. 优先使用 `.venv` 内 Python；不存在时使用当前 Python 做非严格探测，不擅自安装依赖。
3. 过程文件写到用户指定的工作目录，不写入 Skill 本体和资料库根目录。
4. 输出目录已存在时选择新目录；只有明确继续中断任务时才使用 `--resume`。
5. 普通任务使用对应 profile 的非严格探测；`--strict` 只用于安装后的完整部署验收。
6. 先运行所有已部署标准组件，再核实宿主能力；只处理缺失组件的替代路线，不中断其他阶段。
7. 宿主实际完成视觉、文件或音频复核后才能把未覆盖项记为已替代，不能因工具“可能存在”就声称覆盖。

普通任务：

```bash
python scripts/kunpeng.py probe --profile video
python scripts/kunpeng.py probe --profile audio
python scripts/kunpeng.py probe --profile image
python scripts/kunpeng.py probe --profile document
python scripts/kunpeng.py probe --profile repository
python scripts/kunpeng.py probe --profile web
```

部署验收：

```bash
python scripts/kunpeng.py probe --profile all --strict
```

非严格探测即使发现缺项也返回成功，以便 Agent 继续执行可用阶段；真实缺口写入 `routes`。严格探测缺少任一标准组件时返回失败，只用于检查部署是否完整。

## 能力优先级

1. **已部署标准本地工具**：能用就必须运行，保留可核验的转写、OCR、镜头、指标和文档过程文件。
2. **已核实宿主能力**：使用 Agent 自带视觉、浏览器、文件读取或音频能力补充缺失单项；必须真的打开和检查素材。
3. **确定性本地替代**：固定间隔取样、外挂字幕、基础解析等低配方法，只覆盖它实际能处理的部分。
4. **标记未覆盖**：没有任何办法处理的重要内容保持 `partial`，不猜测、不调用付费 API。

## 提取状态语义

| 状态 | 含义 |
| --- | --- |
| `complete` | 当前 `status_scope` 内适用的提取组件全部成功执行。 |
| `degraded` | 标准组件缺失或失败，但已由宿主能力或本地替代实际覆盖。 |
| `partial` | 仍有重要内容没有任何方法覆盖。 |
| `failed` | 素材无法读取或核心流程无法形成可用产物。 |
| `not_applicable` | 素材本身不需要该阶段，例如视频没有音轨。 |

脚本会保留 `host_review_required`。Agent 完成其中的实际复核后，最终报告可把对应缺口说明为已降级覆盖；未复核时必须保持 `partial` 或明确限制。无论提取状态如何，分析器只产出 `distillation_status=evidence_ready`；完整蒸馏必须继续事实卡、reviewed 画像和 workflow gate。

## 降级顺序

- 无 faster-whisper：优先使用原生或外挂字幕；都没有则只能做画面与音频统计，不能声称完成语义转写。其他视频阶段照常运行。
- 无 PaddleOCR：保留原图、关键帧、联系表或 PDF 渲染页供宿主实际查看；不能声称得到 OCR 坐标或置信度。
- 无 PySceneDetect：脚本按固定时间段取样并标记 `degraded`，不能称为真实镜头切分。
- 无 librosa：保留音轨和字幕，只跳过节拍、响度与停顿统计；宿主听取音频也不能伪造数值指标。
- 无某类文档解析器：先尝试宿主本地文件读取，再要求离线转换为 PDF、HTML、Markdown 或 TXT；不上传到在线转换服务。
