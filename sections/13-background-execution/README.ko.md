# 13 · Background execution

[English](README.md) · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · **한국어**

> 느린 작업은 메인 loop 밖에서 시작하고 나중에 보고합니다.

어떤 작업은 오래 걸립니다. 설치, 빌드, 테스트 모음, memory 통합, 자기 loop를 도는 subagent 같은 것들입니다.

기본 agent loop는 tool 호출이 끝난 뒤에야 모델을 다시 호출합니다.

빠른 읽기에는 그래도 괜찮습니다. agent가 다른 일을 하는 동안 돌 수 있는 느린 작업에는 낭비입니다.

백그라운드 실행이 해야 할 일은 이렇습니다.

1. 어떤 작업이 막지 않고 돌 수 있는지 정합니다.
2. 그 작업을 시작하고 핸들을 즉시 돌려줍니다.
3. 실행 중, 완료, 실패, 종료 상태를 추적합니다.
4. 나중에 완료 메시지를 loop로 돌려보냅니다.

이 계층이 없으면 느린 명령 하나가 agent 전체를 멈춰 세울 수 있습니다.

---

## 메커니즘

![Mechanism diagram](assets/13-background-execution.png)

구성 요소는 세 가지입니다.

1. 핸들을 돌려주는, loop 밖의 시작기.
2. task 상태를 추적하는 런타임.
3. 나중 turn에 완료 알림을 끼워 넣는 큐.

loop는 느린 작업을 기다리지 않습니다.

- 백그라운드로 돌리는 것은 실행 옵션이지 특별한 tool 종류가 아닙니다.
- 백그라운드로 돌린 호출은 곧바로 평범한 `tool_result`를 돌려줍니다.
- 진짜 완료는 나중에 별도 알림으로 도착합니다.
- subagent 하나를 통째로 백그라운드에서 돌릴 수 있습니다.

### 신규: loop 밖 시작과 알림 꺼내기

`start`는 워커 스레드에서 작업을 돌리고 task id를 돌려줍니다.

```python
def start(self, fn):                                   # src/background.py; returns immediately
    self._next += 1
    tid = self._next
    self._state[tid] = "running"
    def work():
        try:
            self._finish(tid, "completed", str(fn()))  # enqueues a <task_notification>
        except Exception as e:
            self._finish(tid, "failed", f"{type(e).__name__}: {e}")
    threading.Thread(target=work, daemon=True).start()
    return tid
```

`drain_into`는 완료 알림을 다음 user turn에 접어 넣습니다.

```python
def drain_into(messages, runtime):                     # src/background.py
    notes = runtime.drain() if runtime else []
    if notes and messages and isinstance(messages[-1].get("content"), str):
        messages[-1]["content"] = "\n".join(notes) + "\n\n" + messages[-1]["content"]
```

`backgroundable`은 아무 tool이나 감싸고 그 스키마에 `run_in_background`를 추가합니다.

```python
def backgroundable(tool, runtime):                     # src/background.py; wraps ANY tool
    def run(a):
        if a.get("run_in_background"):
            inner = {k: v for k, v in a.items() if k != "run_in_background"}
            tid = runtime.start(lambda: tool.run(inner))
            return f"started background task {tid} ({tool.name}); ..."
        return tool.run(a)
    ...
    return replace(tool, run=run, ...)
```

이 래퍼는 모델이 무엇을 돌려받는지도 정합니다. 백그라운드로 돌린 호출은 작업을 시작하기만 합니다. task id를 돌려주고, 결과는 나중에 자기 이벤트로 도착합니다.
느린 tool의 이름과 설명도 그렇게 씁니다(`export`가 아니라 `initiate_export`). 그러면 모델은 즉시 받은 `tool_result`를 답이 아니라 접수증으로 읽습니다.

### 통합 방식

loop는 turn을 시작할 때 밀린 완료 알림을 꺼냅니다.

```python
background.drain_into(messages, runtime)               # src/loop.py
```

tool 호출 하나에 tool 결과 하나라는 규칙은 그대로입니다. 늦게 온 완료는 옛 `tool_use_id`에 대한 지연된 `tool_result`가 아닙니다. 새 알림 메시지입니다.

### 더 읽을거리

이 내용은 `src/`에 없습니다. ai-agent-book에서 온 것이고, 표에 있는 시스템에서 확인된 내용은 아닙니다.

**인터럽트와 안전 지점.** 어떤 입력은 실행 중인 tool 호출이 끝나기를 기다릴 수 없습니다.
사용자의 정정, 취소, 경보는 호출 도중에 들어올 수 있습니다. 한 가지 답은 들어오는 모든 입력을 하나의 스트림 위 이벤트로 만드는 것입니다.
loop는 안전 지점에서만 그 스트림을 읽습니다. 안전 지점은 tool 결과가 끝난 시점과 다음 모델 호출 사이의 틈입니다.
호출 한가운데에 무언가를 쓰면 transcript가 깨지므로, 이벤트는 그 틈을 기다립니다.

이벤트가 얼마나 급한지가 어느 틈을 기다릴지 정합니다.

- **큐에 넣기.** 다음 틈을 기다립니다. 완료와 낮은 우선순위 통지의 기본값입니다.
- **취소.** 실행 중인 호출을 멈춰 지금 틈을 엽니다. 정정 때문에 실행 중인 작업이 무의미해질 때 씁니다.
- **병렬.** 이벤트를 곁가지 loop에서 돌리고 메인 loop는 건드리지 않습니다.

이벤트를 분류하는 일 자체는 쌉니다. 작은 모델로도 되므로 분류 비용은 이벤트당 호출 한 번입니다.

**인터럽트 자리표시.** 취소가 일어나면 transcript가 다시 유효해지기까지 한 단계가 더 필요합니다.
멈춘 호출은 `tool_result` 없는 `tool_use` 블록을 남겼고, 다음 모델 호출은 그 짝이 닫혀 있어야 합니다.
ai-agent-book은 그 자리에서 바로 닫습니다. 같은 id로 호출이 중단되었다고 말하는 자리표시 `tool_result`를 씁니다.
이것은 위의 재사용 금지 규칙을 어기지 않습니다. 자리표시는 지금 짝을 닫습니다. 진짜 결과는 여전히 나중에 자기 알림으로 도착합니다.
이 자리표시는 책 저자 본인의 설계입니다. 다른 어떤 출처도 이를 기술하지 않습니다.

---

## 시스템별

각 agent가 작업을 loop 밖으로 어떻게 옮기고 완료를 어떻게 보고하는지.

| | Claude Code | deepseek-harness |
| --- | --- | --- |
| **장점** | 처리량이 올라가고 유휴 대기가 사라짐. 단순 대기는 아무것도 막지 않음. | registry 하나가 shell, 터미널, child agent를 똑같이 다룸. |
| **단점** | 결과가 늦게, 순서가 뒤바뀌어 올 수 있음. 런타임이 상태와 정리를 추적함. | 유휴 agent를 깨우는 데 turn이 들므로 예산이 필요함. |
| **이유** | 느린 명령 하나가 agent 전체를 멈춰 세우면 안 됨. | 끝난 job은 모델이 폴링하지 않아도 모델에 닿아야 함. |
| **방법: loop 밖 기본 수단** | 백그라운드 shell과 agent task. 서브프로세스는 출력이 리다이렉트된 채 계속 돎. | 아무 tool이나 백그라운드 실행 플래그를 받고 job id를 돌려줌. |
| **방법: 알림** | 공유 큐 하나를 통해 오는 `<task_notification>` 메시지. | job마다 통지 하나. 먼저 끝난 쪽이 채택되고, 중복은 억제됨. |
| **방법: 재진입** | 큐를 turn 사이에 비우며, `now`, `next`, `later` 우선순위를 씀. | 바쁜 agent는 다음 단계에 받음. 유휴 agent는 상한 안에서 깨움. |

---

## 실패 모드

- **대화형 prompt에서 멈춤.** 백그라운드 명령이 입력을 기다림. prompt처럼 보이는 출력을 감지해 종료하거나 비대화형으로 다시 실행하라고 모델에 알립니다.
- **완료를 잃음.** 끝난 task가 loop에 닿지 못함. 완료는 공유 큐 하나로 보내고 task에 알림 완료 표시를 합니다.
- **짝이 어긋난 알림.** 옛 `tool_use_id`를 재사용하면 transcript가 깨짐. 독립된 알림 텍스트를 씁니다.
- **종료 후의 부수 효과.** 타임아웃이나 취소는 호출이 실제로 반영되었는지 알려 주지 않음. 무턱대고 재시도하면 두 번 청구될 수 있음. 상태를 먼저 조회하거나 멱등성 키를 보냅니다.
- **묶인 이벤트가 주의를 흩뜨림.** 한 번 꺼낼 때 알림 여러 개가 한 turn에 접혀 들어갈 수 있음. 그러면 모델은 마지막 것에만 답함. 이벤트에 번호를 붙이고 요약 줄을 넣습니다.
- **동시성 과다.** 백그라운드 task가 많으면 리소스가 고갈될 수 있음. 종료 경로와 한도를 둡니다.
- **종료 시 프로세스 누수.** 백그라운드 작업이 session보다 오래 살아남을 수 있음. 정리 절차를 등록합니다.

---

## 실행 방법

[`src/`](src/)는 12를 이어받고 다음을 추가합니다.

- [`background.py`](src/background.py): 런타임, 알림 큐, `drain_into`, `backgroundable`.
- [`loop.py`](src/loop.py): 모델 호출 전에 밀린 알림을 꺼냅니다.
- [`test.py`](src/test.py): 시작, 실패, 꺼내기, 백그라운드 subagent를 확인합니다.
- [`demo.py`](src/demo.py): subagent를 백그라운드로 띄우고 나중에 결과를 읽습니다.

```bash
python sections/13-background-execution/src/test.py         # offline checks, no key
uv run python sections/13-background-execution/src/demo.py  # live demo, needs a key
```

---

## 출처

- [Claude Code task sources](https://github.com/yasasbanukaofficial/claude-code): `tasks/LocalShellTask/`, `tasks/DreamTask/`.
- [Claude Code tool and queue sources](https://github.com/yasasbanukaofficial/claude-code):
  `tools/BashTool/BashTool.tsx`, `tools/SleepTool/prompt.ts`, `utils/task/framework.ts`, `utils/messageQueueManager.ts`.
- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness), `dsh-v0.1.0-rc.7` 기준:
  `packages/jobs/jobs/src/index.ts`, `packages/jobs/jobs-local/src/index.ts`, `packages/jobs/tool-jobs/README.md`,
  `docs/subsystems/jobs.md`, `docs/tool-catalog.md`.
- [learn-claude-code · s13_background_tasks](https://github.com/shareAI-lab/learn-claude-code): 섹션 구성.
- [ai-agent-book](https://github.com/bojieli/ai-agent-book): `book/chapter4.md`, 중국어 원문이 정본.
  멱등성과 취소 의미론, 시작과 완료를 나누는 이름 짓기, 안전 지점에서의 이벤트 분류, 인터럽트 자리표시, 묶인 이벤트와 주의.
