# 5 · Planning & todos

[English](README.md) · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · **한국어**

> 다단계 작업을 하기 전에 계획을 저장하세요.

큰 작업은 눈에 보이는 계획이 필요합니다. 모델이 계획을 프롬프트에만 저장하면 여러 도구 결과 후에 추적을 놓칠 수 있습니다.

계획은 두 가지 별개의 문제를 해결합니다:

1. 에이전트가 작업하는 동안 현재 체크리스트가 필요합니다.
2. 에이전트는 작업을 이해하기 전에 파일을 수정해서는 안 됩니다.

이 섹션에서는 두 가지를 추가합니다: 할 일 도구와 계획 모드. 할 일 도구는 체크리스트를 저장합니다. 계획 모드는 작성된 계획이 승인될 때까지 읽기 전용 탐색을 허용합니다.

이 계층이 없으면 짧은 작업은 여전히 수행할 수 있습니다. 긴 작업은 단계를 건너뛰거나 너무 일찍 행동할 수 있습니다.

---

## 메커니즘

![메커니즘 다이어그램](assets/05-planning-and-todos.png)

두 가지 도구가 있습니다. 둘 다 일반 모델 호출 도구입니다. 어느 것도 핵심 루프를 바꾸지 않습니다.

**할 일 목록.** 모델은 구조화된 체크리스트를 덮어씁니다. 도구는 파일이나 셸 작업을 하지 않습니다. 세션의 계획 상태만 저장합니다.

**계획 모드.** 세션이 읽기 전용 모드로 들어갑니다. 모델은 탐색하고 계획을 작성하며 `ExitPlanMode`를 호출합니다. 그 종료는 권한 레이어에 의해 제한됩니다.

### 새로 추가: 할 일 및 계획 모드 도구

```python
@dataclass
class Session:                                   # src/loop.py: mutable, outlives a turn
    mode: str = DEFAULT
    todos: list = field(default_factory=list)

def todo_tool(session):                          # src/planning.py
    def write(a): session.todos = list(a["todos"])    # model overwrites its checklist
    return Tool("TodoWrite", write, is_read_only=True)    # no side effect, never gated

def exit_plan_mode_tool(session):                # src/planning.py
    def exit_plan(_): session.mode = ACCEPT_EDITS     # approval flips the live mode
    return Tool("ExitPlanMode", exit_plan)
```

- `Session`는 이제 `mode`와 `todos`를 저장합니다.
- `TodoWrite`는 `session.todos`만 변경하므로 외부에서는 읽기 전용입니다.
- `ExitPlanMode`는 승인 후 `session.mode`를 변경합니다.
- 다음 도구 호출은 동일한 권한 게이트를 통해 새로운 모드를 읽습니다.

### 통합 방식

섹션 3의 권한 로직은 이미 `PLAN`을 알고 있습니다:

```python
if mode == PLAN:                              # exploring, not acting yet
    if tool.is_read_only:           return "allow"
    if tool.name == "ExitPlanMode": return "ask"     # the approval handshake
    return "deny"                             # no edits until the plan is approved
```

섹션 5는 도구와 세션 상태를 추가합니다. 새로운 루프나 새로운 권한 경로는 추가하지 않습니다.

할 일 항목은 `{ content, status, activeForm }`입니다.

상태는 `pending`, `in_progress`, 또는 `completed`입니다. 모델은 매번 전체 리스트를 작성하며, harness는 현재 상태를 렌더링합니다.

---

## 시스템별

각 에이전트가 계획을 추적하고 실행을 조정하는 방법.

| | Claude Code | deepseek-harness |
| --- | --- | --- |
| **장점** | 단순하고 저렴합니다. 메모리 내 할 일 리스트는 종속성이나 잠금이 필요 없습니다. | 계획과 할 일 상태는 재시작, 포크, 압축 후에도 유지됩니다. |
| **단점** | 세션 상태만 존재합니다. 한 턴 이상 지속되는 작업은 작업 그래프가 필요합니다(섹션 12). | 계획 모드는 아무 것도 막지 않습니다. 편집을 막는 것은 sandbox 또는 승인 정책뿐입니다. |
| **이유** | 프롬프트에만 존재하는 계획은 사라집니다. 계획이 승인되기 전에는 수정할 수 없습니다. | 세션 로그가 진실이므로, 계획 상태는 하나의 추가 이벤트일 뿐입니다. |
| **방법: 계획 산출물** | 할 일 목록과 계획 파일. `TodoWrite`는 목록을 덮어쓰며, 방해받지 않습니다. | `todo_write`는 전체 목록을 이벤트로 추가합니다. 재실행 시 다시 생성됩니다. |
| **방법: 계획 모드** | 예. 진입 시 권한 모드를 계획으로 전환합니다. 세션은 읽기 전용 상태로 유지됩니다. | 로그된 플래그와 프롬프트 내 안내 텍스트. 권한 변경은 없습니다. |
| **방법: 실행 게이트** | `ExitPlanMode`가 승인을 요청합니다. 호출은 계획 모드 외부에서 거부됩니다. | 계획 중에는 없음. 거부된 계획은 도구 피드백으로 돌아옵니다. |

---

## 실패 모드

- **오래된 목록.** 모델이 할 일을 업데이트하는 것을 중지합니다. `in_progress` 항목 하나를 유지하고 작업이 완료되면 항목을 닫도록 상기시킵니다.
- **작은 작업 과다 계획.** 단일 단계 작업의 할 일 목록은 잡음을 추가합니다. 사소한 작업의 경우 건너뜁니다.
- **계획 모드에서 나올 수 없음.** 일부 화면은 승인 대화상자를 표시할 수 없습니다. 해당 화면에서는 진입과 종료를 함께 비활성화합니다.
- **진입 없이 종료.** 모델이 문맥과 상관없이 `ExitPlanMode`를 호출할 수 있습니다. 현재 모드가 `plan`인지 확인합니다.
- **계획은 맥락과 함께 사라진다.** 평면적인 할 일 목록은 세션 상태이다. 작업이 한 번의 턴이나 과정을 살아남아야 할 때 작업 시스템을 사용하라.

---

## 실행 가능

[`src/`](src/)는 04를 앞으로 가져가고 다음을 추가한다:

- [`planning.py`](src/planning.py): `TodoWrite`와 `ExitPlanMode`.
- [`loop.py`](src/loop.py): `Session`를 보유하여 모드가 실행 중에 바뀔 수 있음.
- [`test.py`](src/test.py): 할 일 기록, 계획 모드 거부, 승인 및 편집 실행을 점검.

```bash
python sections/05-planning-todos/src/test.py         # offline checks, no key
uv run python sections/05-planning-todos/src/demo.py  # live demo, needs a key
```

---

## 출처

- [Claude Code 출처](https://github.com/yasasbanukaofficial/claude-code):
  `tools/TodoWriteTool/TodoWriteTool.ts`, `tools/EnterPlanModeTool/EnterPlanModeTool.ts`, `tools/ExitPlanModeTool/ExitPlanModeV2Tool.ts`.
- [Claude Code 계획 도우미](https://github.com/yasasbanukaofficial/claude-code): `utils/plans.ts`, `utils/todo/types.ts`, `types/permissions.ts`.
- [deepseek-harness 소스](https://github.com/deepseek-ai/deepseek-harness) at `dsh-v0.1.0-rc.7`:
  `packages/todo/tool-todo/src/index.ts`, `packages/plan/plan-mode/src/index.ts`, `docs/subsystems/plan.md`, `docs/tool-catalog.md`.
- [learn-claude-code · s05_todo_write](https://github.com/shareAI-lab/learn-claude-code): 섹션 프레이밍.
