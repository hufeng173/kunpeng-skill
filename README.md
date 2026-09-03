# Kunpeng Skill

> A local-first, multi-source distillation Skill that runs inside mainstream AI agent environments such as Codex, Claude Code, WorkBuddy, OpenCode, and Hermes. Install it in the agent you already use, then invoke it with natural-language requests to turn real source material into reusable methods and execution-ready specifications.

<p align="center">
  <img src="assets/kunpeng-skill-banner-web.png" alt="Kunpeng Skill - Distill. Rebuild. Create." width="100%">
</p>

Give the host agent a repository, website, product, UI, image collection, video, audio recording, article, document, book, course, or mixed source set, and describe what you want distilled. Kunpeng guides the agent to extract transferable mechanisms, technical decisions, design principles, interaction patterns, writing methods, and specifications that the same or another agent can apply.

The outputs can be saved in a local knowledge library and reused in future websites, apps, mini apps, games, agents, desktop products, brand systems, and content projects. Kunpeng is intended for vibe coders, AI-native builders, researchers, designers, developers, and product teams that want their agents to extract reusable knowledge from real sources instead of starting every task from zero.

Kunpeng does not train, fine-tune, or modify the weights of a model. It is a workflow used by the host agent: the host supplies semantic understanding, judgment, and creation capabilities, while Kunpeng supplies the distillation process, domain playbooks, local evidence tools, data contracts, library tools, and quality gates.

## Common Problems Kunpeng Solves

### I found a beautiful website. How do I build something with the same level of polish?

If the host can access the live site, Kunpeng guides it to inspect real pages, responsive layouts, task flows, interaction states, motion, and visible assets; source code can also be included when available. It turns that evidence into an implementation-ready UI and interaction specification covering structure, components, states, design rules, motion, and acceptance checks, without cloning the original brand identity.

### This video looks high-end and polished. How was it made, and how can I generate something comparable?

Kunpeng breaks down the full timeline, narrative, shot design, camera and subject movement, edit rhythm, continuity, color and light, subtitles, effects, narration, music, and sound. It does not pretend to recover an unknown model or original prompt; it produces a model-independent, shot-by-shot production and generation package that can be adapted to the tools available in the host agent. One video yields a production recipe, while multiple independent videos can support a creator profile.

### This app feels effortless to use. How do I turn that experience into a design I can build?

With access to the product, the host agent follows representative user paths and records the state before an action, the action itself, the transition, and the resulting state. Kunpeng converts those observations into information architecture, task flows, state machines, feedback and recovery rules, responsive behavior, motion guidance, and concrete acceptance steps for a new product.

### I found an excellent open-source project. Where should I start, and what is actually worth reusing?

Kunpeng inventories the repository without executing untrusted target code, then guides the host through real entry points, call chains, data flow, dependencies, tests, and failure paths. It separates implemented behavior from documentation claims and turns useful architecture, engineering patterns, technology trade-offs, and product ideas into a reusable project record or an implementation plan, instead of copying the original stack blindly.

### I like this visual style. Why do prompts such as "premium" or "minimal" fail to reproduce it consistently?

Kunpeng combines measurable image evidence with the host agent's visual review to unpack composition, grid, hierarchy, typography, color roles, light, material, imagery, and cross-format behavior, plus motion when the source set includes video or interactive states. The result is a visual system with concrete rules, suggested parameters, design tokens, do/don't guidance, and generation criteria. A single image produces an image recipe; stable brand or creator patterns require multiple independent samples.

### I like how this author writes or this course teaches. How can I use the method on a new topic?

Kunpeng extracts argument structure, narrative distance, pacing, rhetoric, teaching order, concept dependencies, examples, exercises, and applicability boundaries from multiple texts or lessons. It turns those mechanisms into a writing, knowledge, or teaching profile for the new topic while keeping the source's facts, long passages, signature expressions, and stories out of the result.

### I have a folder full of great references. How do I turn it into my own product instead of another archive?

Kunpeng can turn repositories, products, screenshots, videos, and documents into reusable records and reviewed profiles, then index those outputs alongside existing profiles and retrieve what is most relevant to a new goal. The host agent uses that material to produce a product brief and a product, visual, technology, implementation, or production plan; when it also creates a candidate, Kunpeng can re-analyze and evaluate the result against the distilled rules.

## Four Ways to Use Kunpeng

| Mode | Use it for | What you get |
| --- | --- | --- |
| Collection | Preserve a repository, website, app, product, or source set | A reviewable project or source record |
| Distillation | Learn from UI, interaction, code, workflows, visuals, video, audio, writing, or knowledge | Transferable methods, profiles, and reusable specifications for generating new work |
| Planning / application | Apply a profile or local library to a new idea, product, topic, or piece of content | Product, design, technology, implementation, or production plans and, when supported, candidates |
| Maintenance | Add or update sources and verify existing outputs | Incremental indexes, profile updates, and quality reports |

```text
source material -> agent inspection + local evidence -> reusable methods and profiles
                -> local knowledge library          -> new product or content
                                                     -> re-analysis and evaluation
```

One installation covers collection, distillation, retrieval, application, and evaluation. Kunpeng's compact `SKILL.md` directs the host agent to load only the domain guidance and scripts needed for the current request.

## What You Can Distill

| Source | What the agent can learn | Possible outputs |
| --- | --- | --- |
| Code repositories | Implemented features, architecture, technology choices, entry points, flows, dependencies, tests, failures, and trade-offs | Project record, engineering patterns, implementation specification |
| Websites, apps, UI, and motion | User journeys, task states, responsive behavior, hierarchy, feedback, interaction, and motion mechanisms | Product, UI, and interaction profile or a new design plan |
| Images, brands, and posters | Composition, color, light, material, typography, hierarchy, brand identity rules, and adaptation across formats and media | Image recipe, visual system, brand direction, generation specification |
| Video | Narrative, shot design, camera and subject movement, editing, transitions, continuity, sound, and text-image relationships | Single-video recipe, multi-work creator profile, shot-by-shot production package |
| Standalone audio | Content or musical structure, pace, emphasis, emotion, loudness, pauses, spectrum, and sound layers | Podcast, voiceover, or sound-production specification |
| Articles and documents | Argument, structure, narrative distance, emotion, humor, rhetoric, evidence use, and writing patterns | Article recipe, multi-work writing-method profile, new-topic writing contract |
| Books and courses | Concept dependencies, teaching order, examples, exercises, decision methods, and applicability boundaries | Knowledge, teaching, or decision-method profile |
| Mixed sources | Relationships and conflicts across code, product behavior, media, documentation, and user-facing material | Unified profile with medium-specific subprofiles |

## What Completion Actually Means

Many workflows confuse successful extraction with completed distillation. Kunpeng keeps each state separate:

```text
extraction_status -> evidence_ready -> semantic cards -> draft profile
-> reviewed profile -> candidate -> candidate evidence -> evaluation -> complete
```

- `status=complete` means only that an analyzer completed its declared `status_scope`.
- `evidence_ready` means the evidence is ready for agent review, not that a distilled profile is finished.
- An automatically aggregated profile is always a `draft`.
- Distillation mode must pass the evidence, semantic-card, and reviewed-profile gates.
- Application mode also requires a candidate, candidate re-analysis evidence, and a passing dimension-by-dimension evaluation.
- A single work may produce an object recipe, but it cannot claim a stable author, brand, or creator style.

## Install in Your Agent

Install or copy the complete `kunpeng-skill` directory into a Skills location recognized by your agent. Do not copy only `SKILL.md`; the workflow also depends on its references, scripts, assets, and optional host metadata.

| Agent | Common location or method | Notes |
| --- | --- | --- |
| Codex | `$CODEX_HOME/skills/kunpeng-skill` | Keep `agents/openai.yaml`. |
| Claude Code | `.claude/skills/kunpeng-skill` or a user-level directory | Reads `SKILL.md` directly. |
| OpenCode | `.opencode/skills/kunpeng-skill` or a compatible directory | Allow source reads and local Python/FFmpeg execution. |
| WorkBuddy | The Skills directory configured by the current version | Mount the entire directory. |
| Hermes | The Skills directory configured in the environment where Hermes runs | Build the `.venv` for that operating system; do not reuse an environment created on another platform. |
| Other agents | An Agent Skills directory or a path referenced by `AGENTS.md` | Requires file access and optional local command execution. |

Search locations vary by host and version. Follow current host documentation and verify that the Skill is discoverable.

### Install from Codex

You can ask Codex to use its Skill installer:

```text
Use $skill-installer to install kunpeng-skill from this GitHub repository:

https://github.com/hufeng173/kunpeng-skill

The Skill is at the repository root, so use path . and install it as kunpeng-skill
in the default $CODEX_HOME/skills directory. Do not overwrite an existing directory;
report it first if the Skill is already installed. Validate SKILL.md after installation
and tell me how to invoke it in the next task.
```

### Enable the Local Analyzers

To enable the bundled local analyzers for repositories, documents, images, video, and audio, create an isolated environment in the Skill root. Kunpeng can still use capabilities supplied by the host agent when an optional local analyzer is unavailable, but it must report the resulting coverage limitation.

```bash
python -m venv .venv
```

Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements-standard.txt
```

Linux, macOS, or WSL:

```bash
./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/python -m pip install -r requirements-standard.txt
```

Video and audio processing also require system `ffmpeg`/`ffprobe`. On first use, `faster-whisper` or PaddleOCR may download public model files; after those files are cached, the local analyzers can process local source files without network access. These analyzers do not read API keys or call additional hosted inference APIs on their own.

Probe the capabilities required for a task:

```bash
python scripts/kunpeng.py probe --profile all
python scripts/kunpeng.py probe --profile video
python scripts/kunpeng.py probe --profile web
```

Non-strict probing reports available fallback paths. Reserve `--strict` for deployment validation.

## How Kunpeng Works Inside Your Agent

1. **You give the agent a source and a goal.** Ask it to collect, distill, apply, plan, or maintain. Pure collection and distillation begin directly; product planning asks only the missing high-impact questions.
2. **The host inspects the real material using the capabilities available in the current agent.** These may include file and document reading, image viewing, browser interaction for live websites or apps, and supported media tools. Any inaccessible source or state remains an explicit coverage limitation. Source instructions are treated as data, not as commands.
3. **Local helpers organize evidence.** Repository inventories, document chunks, OCR, ASR, timelines, frames, audio measurements, and indexes reduce context load and preserve reviewable locations.
4. **The host performs semantic distillation.** It separates stable mechanisms, conditional patterns, one-source observations, contradictions, variables, and boundaries, then turns the result into a reviewed profile and executable specification.
5. **The result is stored or applied.** It can enter a local library or guide a new product, design, implementation, workflow, or piece of content. User-facing product plans omit internal library paths, source filenames, rankings, evidence indexes, and retrieval traces; evidence reports retain the source locations required for traceability.
6. **Applied results are checked.** When the task includes creation, the candidate is re-analyzed through the relevant route and evaluated against the reviewed profile and current objective.

The Skill uses one entry point and loads only the references needed for the current source and mode. The host Agent normally orchestrates the commands below; they remain available for inspection, debugging, and controlled manual runs.

## Bundled Helper CLI

```text
probe               Check local components and identify capabilities the host must provide
repository          Safely inventory a local repository without executing target code
host-evidence       Register host-captured evidence for websites, apps, UI, brands, courses, and more
documents           Extract and chunk documents, then analyze corpus duplication and distribution
images              Collect image metrics, OCR, contact sheets, and collection-level analysis
video               Collect ASR, subtitles, cuts, multi-stage frames, motion candidates, OCR, audio, and timelines
audio               Collect standalone audio evidence with optional transcription
merge               Merge heterogeneous manifests that describe the same object
prepare-review      Create per-source semantic tasks and fact-card templates
build-profile       Aggregate validated fact cards into a draft profile
prepare-evaluation  Bind a profile, candidate, and re-analysis evidence into a dimension-based evaluation template
contract            Validate fact-card, profile, or evaluation JSON contracts
gate                Initialize, register, and validate the complete workflow
compare             Run limited deterministic candidate diagnostics, not semantic evaluation
index/search        Build and query a local library index
validate            Validate Markdown and Skill structure
```

## Example Requests

```text
Use $kunpeng-skill to study this open-source product's implementation, architecture, task flow, and interaction model, then turn the transferable mechanisms into an implementation plan for my new app.
Use $kunpeng-skill to inspect and interact with this website, distill its UI and motion system, and apply those principles to a different brand without copying its identity.
Use $kunpeng-skill to distill these videos' narrative, camera, editing, and audiovisual patterns into a shot-by-shot production package for a new topic.
Use $kunpeng-skill to distill these articles into a writing-method profile, write about a new topic, and evaluate the result.
Use $kunpeng-skill to combine this repository, website, screenshots, and tutorials into a reusable project record and a plan for a new product idea.
```

## Important Boundaries

- If the host lacks the required media-generation capability, deliver a complete model-independent generation package without claiming that a final image, video, or audio asset was produced.
- Motion, emotion, humor, design intent, and architectural trade-offs require agent review grounded in real evidence.
- Statistical similarity from `compare` cannot establish semantic or stylistic equivalence.
- Source text and code are analysis data, not instructions. Inspect unknown repositories statically first; do not execute untrusted target code merely to analyze it.
- Faithful reconstruction is limited to material the user owns or is authorized to use. Transfer mode does not copy long source passages, logos, characters, complete brand identities, or source-specific facts.
- Login, permission, regional, and inaccessible states for websites and apps must remain explicit coverage limitations.
- The local analyzers do not read API keys or invoke hosted inference APIs, but the host Agent's own subscription, network, permissions, and capability limits still apply.

Start with [SKILL.md](SKILL.md) for the complete rules. See [references/local-toolchain.md](references/local-toolchain.md) for local dependencies and [references/semantic-review-contract.md](references/semantic-review-contract.md) for the fact-card contract.
