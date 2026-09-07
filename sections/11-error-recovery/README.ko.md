# 11 · Error recovery

[English](README.md) · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · **한국어**

> 실패를 분류한 다음, 재시도하거나 조정하거나 멈춥니다.

agent 실행 하나는 여러 번의 모델 호출에 걸칠 수 있습니다. 어떤 호출이든 네트워크 문제, 과부하, 레이트 제한, 출력 한도, context 초과로 실패할 수 있습니다.

모델 호출은 실패 원인의 하나일 뿐입니다. 프로덕션 코딩 agent를 다룬 한 연구는 실패를 네 계층으로 나눕니다.

- **API.** 타임아웃, 레이트 제한, 과부하.
- **Tool.** 0이 아닌 코드로 끝난 명령이나 예외를 던진 핸들러.
- **Context.** prompt 초과, 또는 API가 거부하는 메시지 기록.
- **제어 흐름.** 반복되기만 하고 진전이 없는 단계.

먼저 계층을 판단하고, 그다음에 시도 횟수를 셉니다. 세는 일을 먼저 하면 재시도로 고칠 수 없는 오류에 예산을 씁니다.

loop는 실패마다 다른 대응이 필요합니다.

1. 일시적인 오류는 재시도합니다.
2. prompt나 출력 한도가 문제면 조정한 뒤 재시도합니다.
3. 복구할 수 없는 오류면 멈춥니다.

복구가 없으면 일시적인 API 실패 하나가 긴 작업을 끝내 버릴 수 있습니다.

---

## 메커니즘

![Mechanism diagram](assets/11-error-recovery.png)

모델 호출을 재시도 도우미로 감쌉니다. 도우미는 실패를 분류한 다음, 한도가 정해진 조치를 취합니다.

- 일시적인 상태 코드는 백오프 후 재시도합니다.
- prompt 초과는 compaction 콜백을 한 번 실행한 뒤 재시도합니다.
- 과부하가 반복되면 fallback 모델을 쓸 수 있습니다.
- 알 수 없거나 재시도할 수 없는 오류는 그대로 던집니다.

### 신규: 분류, 백오프, 재시도 도우미

```python
RETRY_STATUS = {408, 409, 429}                         # src/recovery.py; these plus any 5xx

def should_retry(status) -> bool:
    return status in RETRY_STATUS or (status is not None and 500 <= status < 600)

def retry_delay(attempt, retry_after=None) -> float:   # exponential backoff + jitter
    if retry_after is not None:
        return float(retry_after)
    base = min(BASE_DELAY * 2 ** (attempt - 1), MAX_DELAY)
    return base + base * 0.25 * random()
```

초과는 일반 상태 처리보다 먼저 확인합니다. compaction이 prompt를 줄일 수 있다면 `prompt_too_long` 오류는 복구할 수 있습니다.

```python
def _status(e):
    return getattr(e, "status_code", None)

def _is_overflow(e) -> bool:
    return getattr(e, "overflow", False) or "prompt is too long" in str(e).lower()
```

`with_retry`는 시도마다의 상태를 들고 있습니다.

```python
def with_retry(call, on_overflow=None, fallback_model=None,
               max_retries=DEFAULT_MAX_RETRIES, sleep=time.sleep):
    consecutive_529 = 0
    overflowed = False
    for attempt in range(1, max_retries + 2):
        try:
            return call()
        except Exception as e:
            if _is_overflow(e):
                if on_overflow is None or overflowed:
                    raise
                overflowed = True
                on_overflow()
                continue
            status = _status(e)
            if status is None:
                raise
            if status == 529:
                consecutive_529 += 1
                if fallback_model and consecutive_529 >= MAX_529_RETRIES:
                    raise FallbackTriggered(fallback_model)
            if attempt > max_retries or not should_retry(status):
                raise
            sleep(retry_delay(attempt, getattr(e, "retry_after", None)))
```

### 통합 방식

loop는 모델 호출을 감쌉니다.

```python
response = recovery.with_retry(
    lambda: model(messages, registry, system),
    on_overflow=lambda: _reactive_trim(messages),
    fallback_model=fallback_model)
```

- 복구는 모델 호출만 감쌉니다.
- `_reactive_trim`은 초과 재시도 한 번을 위해 `messages[]`를 제자리에서 바꿉니다.
- 복구가 포기하면 오류를 감추지 않고 드러냅니다.

### 더 읽을거리

이 내용은 `src/`에 없습니다. ai-agent-book에서 온 것이고, 표에 있는 시스템에서 확인된 내용은 아닙니다.

**예외를 던지지 않는 loop 잡아내기.** agent가 테스트 파일을 실행하고, 같은 오류를 읽고, 같은 테스트 파일을 다시 실행한다고 해 봅시다.
아무것도 던지지 않으므로 재시도 경로가 발화하지 않고 어떤 한도에도 걸리지 않습니다. 이것은 제어 흐름 실패이고, 전용 탐지기가 필요합니다.

탐지기는 지문입니다. tool 이름에 인자를 더한 것입니다. 같은 지문이 반복되면 agent가 같은 호출을 되풀이하고 있다는 뜻입니다.
단계 상한도 실행을 끝내기는 하지만, 예산을 다 쓴 뒤에야 끝냅니다. 지문 카운터는 몇 단계 만에 끝내고, 어느 호출이 막혔는지도 짚어 줍니다.

복구 경로에도 자체 카운터가 필요합니다. 경로마다 실패를 세면, 계속 실패하는 경로가 전역 상한을 기다리지 않고 자기 차단기를 내립니다.

**조용해진 스트림 종료하기.** 스트림은 연결되어 token 몇 개를 보낸 뒤 멈출 수 있습니다.
그때는 연결 타임아웃이 이미 지난 뒤라서 아무것도 발화하지 않고 loop는 기다립니다.

해법은 두 번째 타이머입니다. 연결 타임아웃 옆에 유휴 감시기를 두고, 정해진 시간 안에 token이 오지 않으면 호출을 취소합니다.
그러면 재시도 도우미가 그 취소를 평범한 일시적 실패로 다룹니다.

**깨진 메시지 기록 복구하기.** turn 도중에 크래시가 나면 짝이 맞는 `tool_result` 없이 `tool_use` 블록만 남을 수 있습니다.
그러면 다음 요청은 작업이 아니라 메시지 모양 때문에 실패하고, 짝이 맞춰질 때까지 계속 실패합니다.

복구가 무엇을 뜻하는지는 transcript를 무엇에 쓰는지에 달렸습니다. 답은 두 가지입니다.

- **제품 harness는 복구합니다.** 호출이 중단되었다고 말하는 자리표시 결과를 넣고, 실행을 이어 갑니다.
- **학습 데이터용 harness는 거부합니다.** 지어낸 결과는 실행된 적 없는 단계를 가르치게 됩니다.

**호출자에게 실패를 얼마나 보여 줄 것인가.** 복구는 하나의 결정이 아닙니다. 실패가 얼마나 드러나야 하는지로 등급을 나눕니다.

1. **조용히 재시도.** 호출자는 최종 결과만 봅니다.
2. **낮춰서 계속.** 더 작은 결과를 돌려주고, 무엇이 빠졌는지 말합니다.
3. **실패를 드러냄.** 시도들을 나열해서 모델이 다른 경로를 시도할 수 있게 합니다.

앞의 두 등급에서 나온 오류는 격리가 필요합니다. 도우미 안에 붙잡아 두고, 복구가 포기할 때만 내보냅니다.
모델에 일찍 닿은 오류는 최종 결과처럼 보이고, 모델은 이미 성공한 작업을 다시 할 수 있습니다.

**복구가 스스로를 먹이지 않게 하기.** 오류 경로는 hook, 요약, 알림을 발화시킬 수 있습니다.
그 작업이 모델을 다시 호출하고, 다시 실패하고, 새 실패가 같은 경로를 또 발화시킵니다.

이 고리는 두 가지 규칙으로 끊습니다. 오류 경로에서는 부수 효과 로직을 끄고, 그래도 남는 것에는 재귀 깊이 카운터를 둡니다.
백그라운드 호출에는 재시도를 아예 주지 않습니다. 이들은 임계 경로 밖에 있으므로, 재시도해 봐야 메인 loop에 필요한 할당량만 씁니다.

**한도는 어디서 오는가.** 이 섹션의 모든 한도는 누군가 고른 숫자입니다. 재시도 횟수, 스트라이크 횟수, 유휴 시간 창의 길이가 그렇습니다.
직관이 아니라 측정된 실패에서 각각을 고릅니다. 책의 세 번 스트라이크 compaction 한도는 반복된 복구 실패에 대한 프로덕션 데이터에서 나왔습니다.

---

## 시스템별

복구는 모델 호출을 감쌉니다. loop 본체는 그대로입니다.

| | Claude Code | mini-swe-agent | deepseek-harness |
| --- | --- | --- | --- |
| **장점** | 구체적인 경로가 뭉뚱그린 재시도보다 더 많은 실행을 살림. | 한도가 정해진 경로 세 개. 크래시가 나도 전체 trajectory가 남음. | 재시도가 기록되므로 재개된 session이 그것을 앎. |
| **단점** | 유지할 분기와 한도가 늘어남. | 살리는 실행이 더 적음. 초과는 중단이고, 형식 오류 세 번도 중단임. | fallback 모델이 없음. always 모드는 무한히 재시도함. |
| **이유** | 일시적인 API 실패 하나가 긴 작업을 끝내면 안 됨. | 재시도하고, 형식 오류는 되돌려 주고, 종료 사유를 밝힘. | 로그가 진실이므로, 복구는 새 turn을 다시 재생함. |
| **방법: 재시도** | 429, 408, 409, 5xx에서 백오프. `retry-after`가 우선함. | tenacity 백오프, 4초에서 60초, 10회 시도. | 실패한 turn 뒤에 오류 이벤트를 넣고, 새 turn을 시작함. |
| **방법: token 처리** | 출력 상한을 올리거나, `max_tokens` 정지 뒤에 이어 가거나, compaction함. | 없음. 초과는 실행을 중단함. | 초과 코드 하나. 먼저 잘라내고, 그다음 요약함. |
| **방법: 모델 fallback** | 과부하(529)가 반복되면 fallback 모델. | 없음. | 없음. 재시도 turn이 같은 요청을 다시 만듦. |

---

## 실패 모드

- **재시도 폭주.** 여러 클라이언트가 과부하 상황에서 재시도하면 부하가 더 나빠짐. 재시도를 제한하고 `retry-after`를 존중합니다.
- **끝나지 않는 복구.** 승격, 이어 가기, compaction이 서로 돌 수 있음. 경로마다 한도를 둡니다.
- **초과를 줄일 수 없음.** 반응형 compaction이 한 번 실패하면 계속 compaction하지 말고 멈춥니다.
- **오류가 사라짐.** 삼켜진 오류는 transcript에 결과가 빠진 자리를 남김. 복구를 다 쓴 뒤에는 실패를 드러냅니다.
- **정지 hook이 API 오류를 되풀이함.** API 오류 메시지에는 정지 hook을 건너뜁니다.
- **오류 없이 막힘.** 같은 호출이 반복되면 아무것도 던지지 않으므로 재시도 경로가 발화하지 않음. tool과 인자를 합친 지문의 반복을 세고, 실행을 멈춥니다.
- **조용한 스트림 정체.** 스트림이 열린 뒤 조용해질 수 있음. 연결 타임아웃은 이미 지나서 아무것도 발화하지 않음. 유휴 감시기를 둡니다.
- **복구가 기록을 오염시킴.** 자리표시 `tool_result`는 제품 실행을 살려 두지만, 실행된 적 없는 단계도 기록함. 학습 데이터로 보관하는 transcript는 복구하지 않습니다.
- **중간 오류가 새어 나감.** 복구가 끝나기 전에 보여 준 오류는 최종처럼 보이고, 모델은 작업을 다시 함. 복구가 포기할 때까지 도우미 안에 붙잡아 둡니다.

---

## 실행 방법

[`src/`](src/)는 10을 이어받고 다음을 추가합니다.

- [`recovery.py`](src/recovery.py): 재시도 분류, 백오프, 초과 처리, fallback 발화.
- [`loop.py`](src/loop.py): 모델 호출을 `with_retry`로 감쌉니다.
- [`test.py`](src/test.py): 가짜로 불안정한 호출을 써서 각 경로를 실행합니다.
- [`demo.py`](src/demo.py): 실제 실행에 모의 과부하를 한 번 주입합니다.

```bash
python sections/11-error-recovery/src/test.py         # offline checks, no key
uv run python sections/11-error-recovery/src/demo.py  # live demo, needs a key
```

---

## 출처

- [Claude Code source](https://github.com/yasasbanukaofficial/claude-code):
  `services/api/withRetry.ts`, `query.ts`, `services/api/claude.ts`, `services/api/errors.ts`, `query/tokenBudget.ts`, `utils/context.ts`.
- [mini-swe-agent source](https://github.com/swe-agent/mini-swe-agent):
  `models/utils/retry.py`, `models/litellm_model.py`, `agents/default.py`의 `run()`과 `max_consecutive_format_errors`.
- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness), `dsh-v0.1.0-rc.7` 기준:
  `packages/llm/llm-retry/README.md`, `packages/llm/llm-retry/src/types.ts`, `packages/core/agent-loop/src/agent.ts`,
  `docs/subsystems/llm-streaming.md`, `docs/subsystems/core.md`, `docs/subsystems/persistence.md`.
- [ai-agent-book · chapter 5](https://github.com/bojieli/ai-agent-book/blob/main/book/chapter5.md) (《深入理解 AI Agent》, 李博杰. 중국어 원문이 정본):
  네 계층 실패 분류, tool과 인자를 합친 loop 지문, 유휴 감시기, `tool_result` 짝 복구와 제품 대 학습 데이터의 갈림,
  오류 격리를 포함한 등급별 복구, 죽음의 나선 방어책. 각주 ch5-3은 이 분류를 프로덕션 agent 연구에서 가져왔다고 밝히며,
  Claude Code도 그 대상에 들어 있고, 구현이 빠르게 바뀐다고 경고합니다. 세 번 스트라이크 compaction 한도도 측정된 프로덕션 실패에서 정합니다.
- [learn-claude-code · s11_error_recovery](https://github.com/shareAI-lab/learn-claude-code): 섹션 구성.
