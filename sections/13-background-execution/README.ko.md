# 13 · Background execution

[English](README.md) · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · **한국어**

> 메인 루프에서 느린 작업을 시작하고 나중에 보고합니다.

일부 작업은 시간이 오래 걸립니다: 설치, 빌드, 테스트 스위트, 메모리 정리, 또는 subagent 자체 루프를 실행하는 경우.

기본 agent loop는 도구 호출이 완료될 때까지 기다렸다가 모델을 다시 호출합니다.

빠른 읽기에는 괜찮지만, 다른 작업을 수행하는 동안 실행할 수 있는 느린 작업에는 비효율적입니다.

Background execution는 다음을 수행해야 합니다:

1. 차단 없이 실행할 수 있는 작업을 결정합니다.
2. 그것들을 시작하고 즉시 핸들을 반환합니다.
3. 실행 중, 완료, 실패, 종료 상태를 추적합니다.
4. 나중에 루프 안으로 완료 메시지를 보냅니다.

이 계층이 없으면, 한 가지 느린 명령으로 전체 에이전트가 멈출 수 있습니다.

---

## 메커니즘

![메커니즘 다이어그램](assets/13-background-execution.png)

세 가지 요소가 있습니다:

1. 핸들을 반환하는 오프-루프 스타터.
2. 작업 상태를 추적하는 runtime.
3. 나중에 완료 알림을 주입하는 큐.

루프는 느린 작업을 기다리지 않습니다.

- 백그라운드 실행은 실행 옵션일 뿐, 특별한 도구 유형이 아닙니다.
- 백그라운드 호출은 정상적인 `tool_result`를 바로 반환합니다.
- 실제 완료는 나중에 별도의 알림으로 도착합니다.
- 전체 subagent를 백그라운드에서 실행할 수 있습니다.

### 새 기능: 오프-루프 시작 및 알림 소진

`start`는 워커 스레드에서 작업을 실행하고 작업 ID를 반환합니다:

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

`drain_into` 완료된 알림을 다음 사용자 차례로 접어 넣습니다:

```python
def drain_into(messages, runtime):                     # src/background.py
    notes = runtime.drain() if runtime else []
    if notes and messages and isinstance(messages[-1].get("content"), str):
        messages[-1]["content"] = "\n".join(notes) + "\n\n" + messages[-1]["content"]
```

`backgroundable`는 모든 도구를 감싸고 `run_in_background`를 해당 스키마에 추가합니다:

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

이 래퍼는 또한 모델이 다시 받는 내용을 설정합니다. 백그라운드로 호출하면 작업만 시작됩니다. 작업 ID를 반환하며, 결과는 나중에 독립된 이벤트로 도착합니다.
느린 도구는 그렇게 이름을 지정하고 설명합니다 (`initiate_export`, `export`가 아님). 그런 다음 모델은 즉시 `tool_result`를 답이 아닌 영수증으로 읽습니다.

### 통합 방식

루프는 턴 시작 시 보류 중인 완료를 비웁니다:

```python
background.drain_into(messages, runtime)               # src/loop.py
```

하나의 도구 호출에서 하나의 도구 결과 규칙은 여전히 유효합니다. 늦은 완료는 이전 `tool_use_id`에 대한 지연된 `tool_result`가 아닙니다. 이는 새로운 알림 메시지입니다.

### 추가 읽기

이 내용은 `src/`에 포함되어 있지 않습니다. 이것은 ai-agent-book에서 나온 것이며, 표에 있는 시스템들과 확인된 것은 아닙니다.

**인터럽트와 안전 지점.** 일부 입력은 실행 중인 도구 호출이 끝날 때까지 기다릴 수 없습니다.
사용자 수정, 취소 또는 경고가 호출 중간에 발생할 수 있습니다. 한 가지 방법은 들어오는 모든 입력을 하나의 스트림의 이벤트로 만드는 것입니다.
루프는 안전 지점에서만 해당 스트림을 읽는데, 안전 지점은 완료된 tool result와 다음 모델 호출 사이의 간격입니다.
호출 중간에 쓰기를 하면 기록이 깨지므로, 이벤트는 간격이 올 때까지 기다립니다.

이벤트가 얼마나 긴급한지에 따라 어느 간격에서 대기할지가 결정됩니다:

- **큐.** 다음 간격까지 대기합니다. 이는 완료와 낮은 우선순위 알림의 기본 설정입니다.
- **취소.** 실행 중인 호출을 중단하여 지금 틈을 엽니다. 실행이 의미 없게 되는 수정이 있을 때 사용하세요.
- **병렬.** 이벤트를 부 루프에서 실행하고 메인 루프는 그대로 두세요.

이벤트를 정렬하는 것 자체는 비용이 적습니다. 작은 모델로도 가능하며, 트리아지는 이벤트당 한 번의 호출만 필요합니다.

**인터럽트 자리 표시자.** 취소 후에는 전사가 다시 합법적이 되려면 한 단계가 더 필요합니다.
중단된 호출은 `tool_use` 블록을 `tool_result` 없이 남겼고, 다음 모델 호출에는 그 쌍이 닫혀 있어야 합니다.
ai-agent-book은 이를 바로 닫습니다. 호출이 중단되었다고 표시하는 자리 표시자 `tool_result`를 동일한 ID에 씁니다.
위 규칙의 재사용 금지 규칙을 위반하지 않습니다. 이제 플레이스홀더가 쌍을 닫습니다. 실제 결과는 여전히 이후에 독립적인 알림으로 도착합니다.
플레이스홀더는 책 저자 자체의 설계입니다. 다른 출처에서는 설명하지 않습니다.

---

## 시스템별

각 에이전트가 루프에서 어떻게 움직이는지 작업하고 완료를 보고합니다.

| | Claude Code | deepseek-harness |
| --- | --- | --- |
| **장점** | 처리량이 개선되고 유휴 대기 시간이 사라집니다. 단순 대기는 아무 것도 막지 않습니다. | 하나의 레지스트리가 셸, 터미널, 자식 에이전트 모두를 제공합니다. |
| **단점** | 결과가 늦게 도착하거나 순서가 뒤바뀔 수 있습니다. runtime은 상태와 정리를 추적합니다. | 유휴 에이전트를 깨우는 데는 차례가 소모되므로 예산이 필요합니다. |
| **왜** | 하나의 느린 명령이 전체 에이전트를 멈추게 해서는 안 됩니다. | 완료된 작업은 모델이 폴링하지 않고도 모델에 도달해야 합니다. |
| **방법: 오프-루프 원시 방식** | 백그라운드 셸 및 에이전트 작업. 하위 프로세스가 실행되며 출력이 리디렉션됩니다. | 모든 도구는 백그라운드 실행 플래그를 받아 작업 ID를 반환합니다. |
| **방법: 알림** | 하나의 공유 큐를 통해 `<task_notification>` 메시지. | 작업당 하나의 알림. 먼저 완료된 것이 우선하며, 중복은 억제됩니다. |
| **방법: 재진입** | 회전 사이에 큐가 `now`, `next` 및 `later` 우선 순위로 비워집니다. | 바쁜 에이전트는 다음 단계를 받습니다. 유휴 상태인 것은 한도 내에서 깨워집니다. |

---

## 실패 모드

- **대화형 프롬프트가 멈춤.** 백그라운드 명령이 입력을 기다립니다. 프롬프트와 유사한 출력을 감지하고 모델에 비대화형으로 종료하거나 재실행하도록 알립니다.
- **완료 손실.** 완료된 작업이 루프에 도달하지 않습니다. 완료를 하나의 공유 큐를 통해 보내고 작업을 알림 처리합니다.
- **잘못된 알림 페어링.** 기존 `tool_use_id`를 재사용하면 기록이 깨집니다. 독립적인 알림 텍스트를 사용하십시오.
- **종료 후 부작용.** 타임아웃이나 취소는 호출이 도착했는지 알려주지 않습니다. 맹목적인 재시도는 두 번 요금이 부과될 수 있습니다. 먼저 상태를 조회하거나 멱등 키를 전송하십시오.
- **묶음 이벤트가 주의를 분산.** 한 번의 드레인이 여러 알림을 한 번에 합칠 수 있습니다. 그러면 모델은 마지막 것만 답변합니다. 이벤트에 번호를 매기고 요약 라인을 추가하십시오.
- **너무 많은 동시 실행.** 많은 백그라운드 작업이 자원을 고갈시킬 수 있습니다. 종료 경로와 한계를 추가하세요.
- **종료 시 프로세스 누수.** 백그라운드 작업이 세션보다 오래 지속될 수 있습니다. 정리(cleanup)를 등록하세요.

---

## 실행 가능

[`src/`](src/)은 12개를 전진시키고 다음을 추가합니다:

- [`background.py`](src/background.py): runtime, 알림 큐, `drain_into`, 및 `backgroundable`.
- [`loop.py`](src/loop.py): 모델 호출 전에 대기 중인 알림을 처리합니다.
- [`test.py`](src/test.py): 시작, 실패, 소모(drain), 및 백그라운드 subagents를 확인합니다.
- [`demo.py`](src/demo.py): 백그라운드에서 subagent를 실행하고 나중에 결과를 읽습니다.

```bash
python sections/13-background-execution/src/test.py         # offline checks, no key
uv run python sections/13-background-execution/src/demo.py  # live demo, needs a key
```

---

## 출처

- [Claude Code 작업 소스](https://github.com/yasasbanukaofficial/claude-code): `tasks/LocalShellTask/`, `tasks/DreamTask/`.
- [Claude Code 도구 및 큐 소스](https://github.com/yasasbanukaofficial/claude-code):
  `tools/BashTool/BashTool.tsx`, `tools/SleepTool/prompt.ts`, `utils/task/framework.ts`, `utils/messageQueueManager.ts`.
- [deepseek-harness 소스](https://github.com/deepseek-ai/deepseek-harness) at `dsh-v0.1.0-rc.7`:
  `packages/jobs/jobs/src/index.ts`, `packages/jobs/jobs-local/src/index.ts`, `packages/jobs/tool-jobs/README.md`,
  `docs/subsystems/jobs.md`, `docs/tool-catalog.md`.
- [learn-claude-code · s13_background_tasks](https://github.com/shareAI-lab/learn-claude-code): 섹션 프레이밍.
- [ai-agent-book](https://github.com/bojieli/ai-agent-book): `book/chapter4.md`, 중국어 원본 공식판.
  멱등성과 취소 의미론, 시작과 완료 명명, 안전 지점에서의 이벤트 분류, 인터럽트 자리표시자, 배치된 이벤트 주의.
