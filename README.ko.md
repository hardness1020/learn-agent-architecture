<h1 align="center" style="margin-top: 0;">Awesome Agent Architecture</h1>

<p align="center">
  <strong>최신 AI Agent가 LLM을 중심으로 어떻게 구축되는지 알아봅니다.</strong><br>
</p>

<p align="center">
  <a href="#섹션"><img src="https://img.shields.io/badge/Focus-Harness_Engineering-8250df" alt="Focus: Harness Engineering"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-d29922" alt="License"></a>
  <br>
  <a href="https://github.com/anthropics/claude-code"><img src="https://img.shields.io/badge/Claude_Code-D97757" alt="Claude Code"></a>
  <a href="https://github.com/NousResearch/hermes-agent"><img src="https://img.shields.io/badge/Hermes_Agent-1A1A1A" alt="Hermes Agent"></a>
  <a href="https://github.com/swe-agent/mini-swe-agent"><img src="https://img.shields.io/badge/mini--swe--agent-7E56D8" alt="mini-swe-agent"></a>
  <a href="https://github.com/deepseek-ai/deepseek-harness"><img src="https://img.shields.io/badge/deepseek--harness-4D6BFE" alt="deepseek-harness"></a>
</p>

<p align="center">
  <img src="https://github.com/user-attachments/assets/472d8152-5e46-4e39-9f09-e77dcd07936a" alt="Awesome Agent Architecture">
</p>

<p align="center">
  <a href="README.md">English</a> · <a href="README.zh-TW.md">繁體中文</a> · <a href="README.zh-CN.md">简体中文</a> · <a href="README.ja.md">日本語</a> · <strong>한국어</strong>
</p>

모델은 추론합니다. harness는 그 추론을 제어된 행동으로 바꿉니다. 도구를 실행하고, 호출 사이의 상태를 유지하며, 부작용을 통제하고, 여러 loop를 조정합니다.
모델 호출 하나만으로는 이런 일을 수행할 수 없습니다.

이 저장소는 harness를 loop, tool, memory, permission, context, task, interface로 나누어 설명합니다.
한 번 익혀두면 다양한 Agent를 이해할 수 있습니다. coding tool, chat assistant, autonomous runner의 차이는 대부분 harness 설계에서 나오기 때문입니다.

한 섹션에서 모두 다루기 어려운 내용은 다음 두 저장소에서 더 깊이 설명합니다:

- [learn-agent-memory](https://github.com/hardness1020/learn-agent-memory): memory loop를 production memory subsystem으로 확장합니다.
- [learn-deepseek-harness](https://github.com/hardness1020/learn-deepseek-harness): deepseek-harness를 처음부터 배우며 plugin seam을 하나씩 살펴봅니다.

**목차:** [Loop](#agent-loop) · [학습 방법](#학습-방법) · [대상 시스템](#연구-대상-시스템) ·
[섹션](#섹션) · [구조](#리포지토리-구조) · [데모 실행](#데모-실행)

---

## Agent Loop

![agent loop](assets/the-agent-loop.png)

대부분의 에이전트는 동일한 제어 흐름을 공유합니다: 모델을 호출하고, 요청된 도구를 실행하며, 결과를 첨부하고, 다시 모델을 호출합니다.

루프는 작습니다. 대부분의 엔지니어링은 그 주변에서 이루어집니다: 디스패치 도구, 게이트 부작용, 컨텍스트 관리, 상태 유지, 그리고 다른 루프 조정.

---

## 학습 방법

각 섹션은 독립적이며 동일한 네 부분의 관점을 사용합니다:

1. **개요.** 이 계층이 해결하는 문제.
2. **메커니즘.** 일반 설계 및 제어 흐름.
3. **시스템별.** 실제 시스템에서의 구현 방법.
4. **실패 모드.** 무엇이 깨지는지와 이를 완화하는 방법.

이 저장소에서 학습하려면:

- **섹션을 순서대로 읽으세요. 각 섹션은 이전 계층 위에 구축됩니다.**
- 실행 가능한 섹션의 경우, `src/loop.py`를 읽고 그 후 `demo.py`를 실행하세요.
- 섹션 `src/`를 이전 섹션과 비교(diff)합니다. diff는 그 섹션이 추가하는 유일한 메커니즘입니다.

---

## 연구 대상 시스템

각 시스템은 아래 섹션의 실습 예제입니다.

| 시스템 | 사람들이 사용하는 이유 | 읽는 목적 | 섹션 | 연구된 버전 |
| --- | --- | --- | --- | --- |
| **Claude Code**  | 최첨단 coding agent. 실제 저장소에서 파일을 편집하고 명령을 실행해 변경 사항을 반영합니다. | 전체 harness. 여기서 시작     | 0~23 (전체)       | v2.1.88         |
| **Hermes Agent** | 장기 사용을 위한 assistant. 사용자를 기억하고 workflow를 학습하며 다양한 환경에서 실행됩니다. | Memory, skills, always-on channel | 7, 9, 14, 16, 19, 21, 22 | v2026.7.1 |
| **mini-swe-agent** | 연구용 baseline. bash tool 하나, 약 150줄. | 가장 작은 완전한 loop, budget, eval harness | 0~3, 8, 10, 11, 20~23 | v2.4.5 |
| **deepseek-harness** | Plugin-first harness. loop 자체도 교체 가능한 plugin입니다. | Plugin seam, durable session log, ACP | 1~8, 10~14, 16~21 | dsh-v0.1.0-rc.7 |
| *(추가 예정)* | | | | |

> OpenClaw와 aider를 포함한 더 많은 시스템을 나중에 추가할 수 있습니다.
> 두 개의 동반 저장소가 더 깊이 다룹니다: [learn-agent-memory](https://github.com/hardness1020/learn-agent-memory) 메모리 계층용,
> 그리고 [learn-deepseek-harness](https://github.com/hardness1020/learn-deepseek-harness) 처음부터 deepseek-harness 학습용.

---

## 섹션

여덟 층, 기본 루프부터 스스로 실행되는 harness까지. 각 행은 독립적으로 작성된 글과 연결됩니다.

> 섹션 9는 [learn-agent-memory](https://github.com/hardness1020/learn-agent-memory)에서 계속됩니다: 메모리 루프를 생산 환경으로 확장하는 10단계 추가.

![학습 경로](assets/learning-path.png)

| #  | 섹션                                                       | 질문                                             | 주요 메커니즘                                         |
| -- | ------------------------------------------------------------ | -------------------------------------------------- | ----------------------------------------------------- |
|    | **계층 0 · 기초**                             |                                                    |                                                       |
| 0  | [Harness thesis](sections/00-harness-thesis/)                 | Agent의 자율적 행동은 어디에서 생기는가?                       | Model vs harness, action, observation, permission  |
|    | **계층 1 · 핵심 루프**                               |                                                    |                                                       |
| 1  | [Agent Loop](sections/01-agent-loop/)                         | Agent는 어떻게 작업을 계속하는가?                      | `messages[]`, loop, `stop_reason`                 |
| 2  | [Tool Runtime](sections/02-tool-runtime/)                     | Tool은 어떻게 호출되고 라우팅되는가?                   | Registry, schema, dispatch, deferred search          |
| 3  | [권한 & sandbox](sections/03-permission-sandbox/)   | 부작용은 어떻게 제한되나요?                        | 권한 모드, 승인, 샌드박싱               |
| 4  | [Hooks](sections/04-hooks/)                                   | 확장은 루프에 어떻게 연결되나요?              | `PreToolUse`, `PostToolUse`, 라이프사이클 이벤트     |
|    | **레이어 2 · 복잡한 작업**                            |                                                    |                                                       |
| 5  | [Planning & todos](sections/05-planning-todos/)           | 큰 작업은 어떻게 분해되는가?                        | Plan mode, todo list, 편집 전 승인           |
| 6  | [Subagents](sections/06-subagents/)                           | 하위 문제는 어떻게 격리되는가?                      | 새로운 `messages[]`, 위임, 자식 루프          |
| 7  | [Skills](sections/07-skills/)                                 | 기능은 어떻게 필요 시 로드되는가?             | `SKILL.md`, 카탈로그, progressive disclosure         |
| 8  | [Context management](sections/08-context-management/)         | 긴 세션은 어떻게 창에 맞춰지는가?               | 예산 설정, 스텁, 압축, 요약               |
|    | **레이어 3 · 지식 및 회복력**                  |                                                    |                                                       |
| 9  | [Memory](sections/09-memory/)                                 | 실행 사이에 기억을 어떻게 유지하는가?                  | Selection, recall, extraction, consolidation          |
| 10 | [System prompt assembly](sections/10-system-prompt/)          | 매 turn마다 prompt는 어떻게 구성되는가?                 | Prompt section, live state, cache boundary         |
| 11 | [Error recovery](sections/11-error-recovery/)                 | 장시간 task는 장애에서 어떻게 복구하는가?              | Retry, overflow recovery, fallback model            |
|    | **레이어 4 · 장기 실행 및 비동기**                   |                                                    |                                                       |
| 12 | [작업 시스템](sections/12-task-system/)                       | 작업은 어떻게 한 턴을 넘어 지속되는가?               | 작업 기록, 의존성, 잠금                                |
| 13 | [Background execution](sections/13-background-execution/)     | 작업은 어떻게 메인 루프 밖에서 실행되는가?           | 핸들, 작업 상태, 알림 큐                               |
| 14 | [스케줄링](sections/14-scheduling/)                         | 에이전트는 어떻게 나중에 실행되는가?                | Cron, 대기, 원격 트리거, 큐                |
| 15 | [Worktree isolation](sections/15-worktree-isolation/)         | 병렬 작업은 어떻게 충돌을 피합니까?           | Git worktrees, cwd 바인딩, 안전한 정리              |
|    | **계층 5 · 다중 에이전트**                             |                                                    |                                                       |
| 16 | [조정](sections/16-coordination/)                     | 많은 에이전트가 어떻게 통신합니까?                           | 받은 편지함, 방송, 권한 전파              |
| 17 | [프로토콜](sections/17-protocols/)                           | 에이전트가 어떻게 합의하고 정상 종료합니까?              | 계획 승인, 종료 핸드셰이크                    |
| 18 | [자율성](sections/18-autonomy/)                             | 에이전트들은 어떻게 스스로 조직하는가?                 | 유휴 사이클, 작업 주장, 자기 조직화          |
|    | **레이어 6 · 확장 및 통합**                 |                                                    |                                                       |
| 19 | [MCP / plugins / 채널](sections/19-mcp-plugins-channels/) | harness는 세계에 어떻게 도달하는가?              | 운송, 채널, 도구 풀 구성              |
| 20 | [Observability & evaluation](sections/20-observability/)  | 우리는 그것이 작동하는 것을 어떻게 알 수 있는가?                           | 추적, 지표, evals, 실패 분석             |
| 23 | [Evaluation](sections/23-evaluation/)                         | 변경이 실제로 더 나아졌다는 것을 어떻게 알 수 있나요?            | Eval 환경, 리셋, 평가자, Pass^k             |
|    | **레이어 7 · 구성**                             |                                                    |                                                       |
| 21 | [Loop engineering](sections/21-loop-engineering/)             | 반복문이 어떻게 스스로 실행되는 시스템으로 쌓이나요? | 검증 루프, 트리거, 예산, 성숙도 수준 |
| 22 | [Graph engineering](sections/22-graph-engineering/)           | 제어 흐름이 모델에서 코드로 언제 이동하나요? | 노드, 코드화된 엣지, 주기, 노드로서의 에이전트           |

---

## 리포지토리 구조

모든 24섹션 작성 내용이 `00-harness-thesis/`부터 `23-evaluation/`까지 존재합니다.

```text
awesome-agent-architecture/
├── README.md                  # top-level map
├── sections/                  # one folder per section
│   ├── 00-harness-thesis/     # README.md per section
│   ├── 01-agent-loop/src/     # runnable chain starts here
│   ├── ...
│   └── 23-evaluation/
└── references/                # primary sources and prior art
```

각 섹션 폴더는 `NN-name/`이고, `README.md`를 포함하고 있습니다.

섹션 1부터 23까지에도 실행 가능한 `src/`가 포함되어 있습니다. 코드는 섹션별로 누적됩니다.
각 섹션은 하나의 메커니즘을 추가하고 `loop.py`를 진화시키므로, 인접 섹션 간의 차이는 변경된 내용을 보여줍니다.

하나의 섹션보다 더 깊은 탐구는 자체 리포지토리에 존재합니다.
[learn-agent-memory](https://github.com/hardness1020/learn-agent-memory)는 섹션 9 루프를 전체 메모리 서브시스템으로 확장합니다.
[learn-deepseek-harness](https://github.com/hardness1020/learn-deepseek-harness)는 deepseek-harness를 처음부터 하나의 plugin 접합부씩 학습합니다.

---

## 데모 실행

섹션 1부터 23까지는 실행 가능한 데모를 제공합니다. 저장소 루트에서 한 번 설정하세요:

```bash
uv venv
uv pip install -r requirements.txt
cp .env.example .env        # then add your ANTHROPIC_API_KEY
```

고정된 종속성은 [`requirements.txt`](requirements.txt)에 있습니다. `.env`는 gitignore 처리되어 있으며 다음을 포함합니다:

- `ANTHROPIC_API_KEY`
- 선택 사항 `ANTHROPIC_MODEL`
- 선택 사항 `ANTHROPIC_BASE_URL`

각 실행 가능한 섹션에는 다음이 포함됩니다:

- `test.py`: 오프라인 검사, 키 필요 없음.
- `demo.py`: API에 대한 실시간 데모.

```bash
python sections/01-agent-loop/src/test.py         # offline
uv run python sections/01-agent-loop/src/demo.py  # live
```

---

## 기여

- **시스템 추가.** 동일한 섹션 구조에 새로운 에이전트를 배치합니다.
- **섹션 심화.** 메커니즘, 더 명확한 다이어그램, 또는 더 선명한 실패 모드를 추가합니다.
- **기록을 수정하십시오.** 이것들은 소스, 문서 및 동작에서 재구성한 것입니다. 출처가 있는 수정은 환영합니다.

추측보다는 명명된 검증 가능한 메커니즘을 선호하십시오. 출처를 인용하십시오.
전체 PR 체크리스트는 [CONTRIBUTING.md](CONTRIBUTING.md)를 참조하십시오.

---

## 참고 문헌

- [claude-code](https://github.com/yasasbanukaofficial/claude-code): 메커니즘 이름 및 구현 경로에 사용된 Claude Code 소스 백업.
- [hermes-agent](https://github.com/NousResearch/hermes-agent): 연구 대상 두 번째 시스템으로 사용된 오픈 소스 agent harness (MIT).
- [mini-swe-agent](https://github.com/swe-agent/mini-swe-agent): 연구 대상 세 번째 시스템으로 사용된 최소 SWE 에이전트 (MIT).
- [deepseek-harness](https://github.com/deepseek-ai/deepseek-harness): 연구 대상 네 번째 시스템으로 사용된 플러그인 기반 agent harness (MIT).
- [learn-claude-code](https://github.com/shareAI-lab/learn-claude-code): 코드 우선 harness 재구성 및 섹션 프레이밍.
- [Anthropic Agent Skills 모범 사례](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices): Progressive disclosure 수준 for skills.
- [Anthropic prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching): 캐시 중단점, TTL, 가격, 및 토큰 최소값.
- [cobusgreyling/loop-engineering](https://github.com/cobusgreyling/loop-engineering): 루프 빌딩 블록 및 준비 수준.
- [LangChain · loop engineering의 예술](https://www.langchain.com/blog/the-art-of-loop-engineering): 네 개로 쌓인 루프.
- [Addy Osmani · Loop engineering](https://addyosmani.com/blog/loop-engineering/): 에이전트 루프용 구성된 빌딩 블록.
- [MindStudio · loop engineering란 무엇인가](https://www.mindstudio.ai/blog/what-is-loop-engineering-autonomous-ai-agent-workflows): 자율 워크플로우를 위한 목표 조건.
- [Lilian Weng · 자기 계발을 위한 Harness engineering](https://lilianweng.github.io/posts/2026-07-04-harness/): 루프 외부의 게이트가 있는 개선 루프.
- [LangChain · graph engineering의 3년](https://www.langchain.com/blog/3-years-of-graph-engineering-with-langgraph): 노드, 엣지, 사이클, 그리고 노드로서의 에이전트.
- [Anthropic · 효과적인 에이전트 구축](https://www.anthropic.com/engineering/building-effective-agents): 워크플로우 vs 에이전트와 다섯 가지 워크플로우 형태.
- [Google · 우리가 ADK 2.0을 만든 이유](https://developers.googleblog.com/en/why-we-built-adk-20/): 코드에서의 라우팅과 노드 간 컨텍스트 격리.
- [ai-agent-book](https://github.com/bojieli/ai-agent-book): 《AI Agent 심층 이해》, 저자 李博杰 (Apache-2.0). 6장에서 evaluation 섹션을 다룸.
