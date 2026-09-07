# 5 · 계획과 todo

[English](README.md) · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · **한국어**

> 여러 단계로 이어지는 작업을 시작하기 전에 계획을 저장합니다.

큰 작업에는 눈에 보이는 계획이 필요합니다. 모델이 계획을 prompt 안에만 담아 두면, tool result가 많이 쌓인 뒤에 흐름을 놓칠 수 있습니다.

계획 수립은 서로 다른 두 가지 문제를 풉니다.

1. agent는 작업하는 동안 최신 체크리스트가 필요합니다.
2. agent는 작업을 이해하기 전에 파일을 고쳐서는 안 됩니다.

이 섹션은 둘 다 추가합니다. todo tool과 plan mode입니다. todo tool은 체크리스트를 저장합니다. plan mode는 작성한 계획이 승인될 때까지 읽기 전용 탐색만 허용합니다.

이 계층이 없어도 짧은 작업은 그대로 됩니다. 긴 작업은 단계를 건너뛰거나 너무 일찍 손을 댈 수 있습니다.

---

## 메커니즘

![Mechanism diagram](assets/05-planning-and-todos.png)

tool은 두 개입니다. 둘 다 모델이 호출하는 평범한 tool입니다. 어느 쪽도 핵심 loop를 바꾸지 않습니다.

**Todo 목록.** 모델이 구조화된 체크리스트를 통째로 덮어씁니다. 이 tool은 파일 작업도 shell 작업도 하지 않습니다. session의 계획 상태만 저장합니다.

**Plan mode.** session이 읽기 전용 모드로 들어갑니다. 모델은 탐색하고, 계획을 쓰고, `ExitPlanMode`를 호출합니다. 이 종료 호출은 permission 계층이 통제합니다.

### 새로 추가: todo tool과 plan mode tool

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

- `Session`은 이제 `mode`와 `todos`를 저장합니다.
- `TodoWrite`는 `session.todos`만 바꾸므로, 바깥에서 보면 읽기 전용입니다.
- `ExitPlanMode`는 승인이 난 뒤에 `session.mode`를 바꿉니다.
- 다음 tool call은 같은 permission gate를 거쳐 새 mode를 읽습니다.

### 통합 방식

섹션 3의 permission 로직은 이미 `PLAN`을 알고 있습니다.

```python
if mode == PLAN:                              # exploring, not acting yet
    if tool.is_read_only:           return "allow"
    if tool.name == "ExitPlanMode": return "ask"     # the approval handshake
    return "deny"                             # no edits until the plan is approved
```

섹션 5는 tool과 session 상태를 추가합니다. 새 loop나 새 permission 경로를 추가하지는 않습니다.

todo 항목 하나는 `{ content, status, activeForm }`입니다.

status는 `pending`, `in_progress`, `completed` 중 하나입니다. 모델은 매번 목록 전체를 쓰고, harness는 현재 상태를 그려 줍니다.

---

## 시스템별

각 agent가 계획을 추적하고 실행을 통제하는 방식입니다.

| | Claude Code | deepseek-harness |
| --- | --- | --- |
| **장점** | 단순하고 저렴함. 메모리에만 두는 todo 목록은 의존성도 락도 필요 없음. | 계획과 todo 상태가 재시작, fork, compaction 이후에도 남음. |
| **단점** | session 상태로 한정됨. turn을 넘겨 살아남는 작업에는 task 그래프가 필요함 (섹션 12). | plan mode가 막는 것은 없음. 편집을 멈추는 것은 sandbox나 승인 정책뿐. |
| **이유** | prompt 안에만 둔 계획은 사라짐. 계획이 승인되기 전에는 편집 없음. | session 로그가 진실이므로, 계획 상태도 이벤트 하나일 뿐. |
| **방법: plan artifact** | todo 목록과 계획 파일. `TodoWrite`가 목록을 덮어쓰며, gate를 거치지 않음. | `todo_write`가 목록 전체를 이벤트로 덧붙임. 재생하면 다시 만들어짐. |
| **방법: plan mode** | 있음. 진입하면 permission mode가 plan으로 바뀜. session은 읽기 전용으로 유지됨. | 로그에 남는 플래그와 prompt 안의 안내 문구. permission 변경은 없음. |
| **방법: execution gate** | `ExitPlanMode`가 승인을 요청함. plan mode 밖에서 온 호출은 거부됨. | 계획 중에는 없음. 거부된 계획은 tool 피드백으로 돌아옴. |

---

## 실패 모드

- **낡은 목록.** 모델이 todo 갱신을 멈춥니다. 항상 한 항목을 `in_progress`로 두고 작업이 끝나면 항목을 닫으라고 상기시킵니다.
- **작은 일에 과한 계획.** 한 단계짜리 작업에 todo 목록을 만들면 잡음만 늘어납니다. 사소한 작업에서는 건너뜁니다.
- **plan mode를 빠져나오지 못함.** 승인 대화창을 띄울 수 없는 인터페이스도 있습니다. 그런 인터페이스에서는 진입과 종료를 함께 꺼 둡니다.
- **진입 없는 종료.** 모델이 맥락과 무관하게 `ExitPlanMode`를 호출할 수 있습니다. 현재 mode가 `plan`인지 검증합니다.
- **context와 함께 사라지는 계획.** 평평한 todo 목록은 session 상태입니다. 작업이 turn이나 프로세스를 넘겨 살아남아야 한다면 task 시스템을 씁니다.

---

## 실행 방법

[`src/`](src/)는 04를 이어받아 다음을 추가합니다.

- [`planning.py`](src/planning.py): `TodoWrite`와 `ExitPlanMode`.
- [`loop.py`](src/loop.py): 실행 도중 mode가 바뀔 수 있도록 `Session`을 들고 있습니다.
- [`test.py`](src/test.py): todo 쓰기, plan mode의 거부, 승인, 편집 실행을 확인합니다.

```bash
python sections/05-planning-todos/src/test.py         # offline checks, no key
uv run python sections/05-planning-todos/src/demo.py  # live demo, needs a key
```

---

## 출처

- [Claude Code 소스](https://github.com/yasasbanukaofficial/claude-code):
  `tools/TodoWriteTool/TodoWriteTool.ts`, `tools/EnterPlanModeTool/EnterPlanModeTool.ts`, `tools/ExitPlanModeTool/ExitPlanModeV2Tool.ts`.
- [Claude Code 계획 헬퍼](https://github.com/yasasbanukaofficial/claude-code): `utils/plans.ts`, `utils/todo/types.ts`, `types/permissions.ts`.
- [deepseek-harness 소스](https://github.com/deepseek-ai/deepseek-harness), `dsh-v0.1.0-rc.7` 기준:
  `packages/todo/tool-todo/src/index.ts`, `packages/plan/plan-mode/src/index.ts`, `docs/subsystems/plan.md`, `docs/tool-catalog.md`.
- [learn-claude-code · s05_todo_write](https://github.com/shareAI-lab/learn-claude-code): 섹션 구성 참고.
