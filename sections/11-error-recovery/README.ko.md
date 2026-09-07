# 11 · Error recovery

[English](README.md) · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · **한국어**

> 실패를 분류한 다음 재시도, 조정 또는 중단합니다.

에이전트 실행은 여러 모델 호출에 걸쳐 진행될 수 있습니다. 네트워크 문제, 과부하, 속도 제한, 출력 제한 또는 컨텍스트 오버플로우 때문에 어떤 호출이든 실패할 수 있습니다.

모델 호출은 실패의 유일한 원인이 아닙니다. 한 연구에서는 실제 코딩 에이전트에서 실패를 네 가지 층으로 분류했습니다:

- **API.** 타임아웃, 속도 제한, 과부하.
- **도구.** 비제로로 종료되는 명령이나 예외를 발생시키는 핸들러.
- **컨텍스트.** 프롬프트 오버플로우, 또는 API가 거부하는 메시지 기록.
- **제어 흐름.** 반복하지만 성과 없는 단계.

먼저 층을 파악한 다음 시도 횟수를 세기 시작합니다. 먼저 세면 어떤 재시도로도 고칠 수 없는 오류에 예산을 쓰게 됩니다.

루프는 서로 다른 실패에 대해 서로 다른 대응이 필요합니다:

1. 일시적인 오류는 재시도합니다.
2. 프롬프트나 출력 제한 때문에 발생하는 문제는 조정 후 재시도합니다.
3. 오류가 복구 불가능하면 중지합니다.

복구가 없으면 단 한 번의 일시적인 API 실패가 긴 작업을 종료시킬 수 있습니다.

---

## 메커니즘

![메커니즘 다이어그램](assets/11-error-recovery.png)

모델 호출을 재시도 도우미로 감쌉니다. 도우미는 실패를 분류한 다음 제한된 조치를 취합니다.

- 일시적인 상태 코드는 백오프 후 재시도합니다.
- 프롬프트 오버플로우는 압축 콜백을 한 번 실행한 후 재시도합니다.
- 반복적인 오버로드는 대체 모델을 실행할 수 있습니다.
- 알 수 없거나 재시도 불가능한 오류는 발생시킵니다.

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

오버플로우는 일반 상태 처리를 하기 전에 확인됩니다. 컴팩션이 프롬프트를 축소할 수 있는 경우, `prompt_too_long` 오류는 복구 가능할 수 있습니다.

```python
def _status(e):
    return getattr(e, "status_code", None)

def _is_overflow(e) -> bool:
    return getattr(e, "overflow", False) or "prompt is too long" in str(e).lower()
```

`with_retry`는 시도별 상태를 유지합니다:

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

루프는 자신의 모델 호출을 감쌉니다:

```python
response = recovery.with_retry(
    lambda: model(messages, registry, system),
    on_overflow=lambda: _reactive_trim(messages),
    fallback_model=fallback_model)
```

- 복구는 오직 모델 호출만 감쌉니다.
- `_reactive_trim`는 하나의 오버플로우 재시도를 위해 `messages[]`를 제자리에서 변경합니다.
- 복구가 포기할 때, 오류는 숨김 없이 나타납니다.

### 추가 읽기

이 모든 것은 `src/`에는 포함되어 있지 않습니다. 이는 ai-agent-book에서 비롯된 것이며, 표에 있는 시스템에는 확인되지 않았습니다.

**절대 발생하지 않는 루프를 잡기.** 예를 들어 에이전트가 테스트 파일을 실행하고, 같은 오류를 읽고, 다시 같은 테스트 파일을 실행한다고 가정합니다.
아무것도 던지지 않으므로, 재시도 경로가 실행되지 않고 어떤 제한에도 도달하지 않습니다. 이는 제어 흐름 실패이며, 자체적으로 탐지기가 필요합니다.

탐지기는 지문입니다: 도구 이름과 그 인수입니다. 반복되는 지문은 에이전트가 동일한 호출을 다시 수행하고 있음을 나타냅니다.
단계 제한은 실행을 종료시키지만, 전체 예산이 소진된 후에만 종료됩니다. 지문 카운터는 몇 단계 후에 실행을 종료시키며, 중단된 호출을 식별할 수 있습니다.

복구 경로 또한 자체 카운터가 필요합니다. 경로별 실패를 계산하여, 계속 실패하는 경로는 전체 제한을 기다리지 않고 자체 차단기를 작동시킵니다.

**조용해진 스트림 종료** 스트림은 연결하고 몇 개의 토큰을 전송한 후 멈출 수 있습니다.
그 시점에는 연결 타임아웃이 이미 지나기 때문에, 아무것도 실행되지 않고 루프는 대기합니다.

수정은 두 번째 타이머입니다. 연결 타임아웃 옆에 유휴 감시자를 추가하고, 지정된 시간 내에 토큰이 도착하지 않으면 호출을 취소합니다.
재시도 도우미는 그런 다음 취소를 일반적인 일시적 실패로 처리합니다.

**손상된 메시지 기록 복구 중.** 턴 도중의 충돌은 `tool_use` 블록에 일치하는 `tool_result`가 남지 않은 상태로 남길 수 있습니다.
다음 요청은 작업이 아니라 메시지 형식에서 실패하며, 쌍이 수정될 때까지 계속 실패합니다.

복구가 의미하는 바는 대본이 무엇을 위한 것인지에 따라 다릅니다. 답은 두 가지입니다:

- **제품 harness가 복구하는 경우.** 호출이 중단되었다고 표시하는 자리 표시자 결과를 추가하고 실행을 계속합니다.
- **훈련 데이터 harness가 거부됩니다.** 만들어진 결과는 실행되지 않은 단계를 가르치게 됩니다.

**호출자가 얼마나 많은 실패를 보아야 하는지.** 복구는 한 번의 결정이 아닙니다. 실패가 얼마나 눈에 띄어야 하는지에 따라 등급을 매기세요:

1. **조용히 재시도합니다.** 호출자는 최종 결과만 봅니다.
2. **저하 후 계속합니다.** 더 작은 결과를 반환하고 누락된 부분을 알려줍니다.
3. **실패를 표면화합니다.** 시도한 내역을 나열하여 모델이 다른 경로를 시도할 수 있도록 합니다.

첫 두 등급에서의 오류는 격리해야 합니다. 헬퍼 내부에 보관하고 복구가 포기할 때만 공개하세요.
모델에 일찍 도달한 오류는 최종처럼 보이며, 모델이 이미 성공한 작업을 다시 수행할 수 있습니다.

**자체 피드백에서 회복을 멈추기.** 오류 경로는 hook, 요약 또는 알림을 트리거할 수 있습니다.
그 작업은 모델을 다시 호출하고, 다시 실패하며, 새로운 실패가 동일한 경로를 다시 트리거합니다.

두 가지 규칙이 체인을 끊습니다. 오류 경로에서 부작용 로직을 끄고, 살아남은 것에 대해 재귀 깊이 카운터를 유지합니다.
백그라운드 호출은 전혀 재시도하지 않습니다: 중요한 경로에 있지 않기 때문에 재시도는 메인 루프에서 필요한 쿼터만 소모합니다.

**한계가 어디서 오는가.** 이 섹션의 모든 한계는 누군가가 정한 숫자입니다: 재시도 횟수, 스트라이크 수, 유휴 창의 길이.
각각을 직관이 아닌 측정된 실패에서 선택하십시오. 책에서 언급한 3-스트라이크 압축 한계는 반복 회복 실패에 대한 생산 데이터에서 나왔습니다.

---

## 시스템별

복구는 모델 호출을 감쌉니다. 루프 본문은 동일하게 유지됩니다.

| | Claude Code | mini-swe-agent | deepseek-harness |
| --- | --- | --- | --- |
| **장점** | 특정 경로는 전체 재시도보다 많은 실행을 저장합니다. | 세 개의 제한된 경로. 충돌이 발생하면 전체 궤적이 남습니다. | 재시도가 기록되므로, 재개된 세션이 이를 알 수 있습니다. |
| **단점** | 유지해야 할 분기와 제한이 더 많습니다. | 저장되는 실행이 더 적습니다. 오버플로우가 발생하면 중단되고, 세 가지 형식 오류도 중단됩니다. | 대체 모델이 없습니다. 항상 모드에서 재시도를 무한히 수행합니다. |
| **이유** | 한 번의 임시 API 실패가 긴 작업을 끝내서는 안 됩니다. | 재시도하고, 형식 오류를 다시 전달하며, 종료를 명명합니다. | 로그가 진실이므로, 복구는 새 턴을 재생합니다. |
| **방법: 재시도** | 429, 408, 409, 5xx에서 백오프; `retry-after`가 승리합니다. | tenacity 백오프, 4~60초, 10회 시도. | 실패한 회차 이후 오류 이벤트, 그 다음 새로운 이벤트. |
| **방법: 토큰 처리** | 출력 한도 증가, `max_tokens` 정지 후 계속, 또는 압축. | 없음. 오버플로우는 실행 중단. | 하나의 오버플로우 코드: 가지치기 후 요약. |
| **방법: 모델 폴백** | 반복적인 과부하(529) 발생 시 폴백 모델. | 없음. | 없음. 재시도 회차는 동일한 요청 재구성. |

---

## 실패 모드

- **재시도 폭풍.** 과부하 시 많은 클라이언트가 재시도하면 부하가 악화될 수 있음. 재시도를 제한하고 `retry-after`를 준수.
- **무한 복구.** 상승, 계속, 압축이 반복될 수 있음. 각 경로를 제한.
- **오버플로우는 줄일 수 없습니다.** 하나의 반응식 압축이 실패하면, 계속 압축하지 말고 중지하십시오.
- **오류가 사라집니다.** 무시된 오류는 결과가 누락된 기록을 남깁니다. 복구가 완료된 후 실패를 표시합니다.
- **중지 hook은 API 오류를 반복합니다.** API 오류 메시지에 대해서는 중지 hooks를 건너뜁니다.
- **오류 없이 멈춤.** 반복되는 호출은 아무 것도 발생시키지 않으므로 재시도 경로가 작동하지 않습니다. 반복된 도구+인수 지문을 계산하고 실행을 중지하십시오.
- **조용한 스트림 정지.** 스트림이 열리고 조용해질 수 있습니다. 연결 시간 초과가 이미 지났기 때문에 아무 것도 발생하지 않습니다. 유휴 감시자를 추가하십시오.
- **수정은 기록을 오염시킨다.** 자리 표시자 `tool_result`는 제품 실행을 유지시킨다. 또한 실행되지 않은 단계를 기록한다. 훈련 데이터로 보관된 전사를 수정하지 마라.
- **중간 오류가 누출된다.** 복구가 완료되기 전에 표시된 오류는 최종 오류처럼 보이며, 모델은 작업을 다시 수행한다. 복구가 포기할 때까지 helper 안에 보관하라.

---

## 실행 가능

[`src/`](src/)는 10을 전달하고 다음을 추가한다:

- [`recovery.py`](src/recovery.py): 분류 재시도, 백오프, 오버플로 처리, 대체 트리거.
- [`loop.py`](src/loop.py): `with_retry` 안에서 모델 호출을 래핑한다.
- [`test.py`](src/test.py): 각 경로를 가짜 변덕스러운 호출로 구동한다.
- [`demo.py`](src/demo.py): 라이브 실행에서 하나의 시뮬레이션된 과부하를 주입합니다.

```bash
python sections/11-error-recovery/src/test.py         # offline checks, no key
uv run python sections/11-error-recovery/src/demo.py  # live demo, needs a key
```

---

## 출처

- [Claude Code 소스](https://github.com/yasasbanukaofficial/claude-code):
  `services/api/withRetry.ts`, `query.ts`, `services/api/claude.ts`, `services/api/errors.ts`, `query/tokenBudget.ts`, `utils/context.ts`.
- [mini-swe-agent 소스](https://github.com/swe-agent/mini-swe-agent):
  `models/utils/retry.py`, `models/litellm_model.py`, `run()` 및 `max_consecutive_format_errors`를 `agents/default.py`에서.
- [deepseek-harness 소스](https://github.com/deepseek-ai/deepseek-harness) at `dsh-v0.1.0-rc.7`:
  `packages/llm/llm-retry/README.md`, `packages/llm/llm-retry/src/types.ts`, `packages/core/agent-loop/src/agent.ts`,
  `docs/subsystems/llm-streaming.md`, `docs/subsystems/core.md`, `docs/subsystems/persistence.md`.
- [ai-agent-book · 5장](https://github.com/bojieli/ai-agent-book/blob/main/book/chapter5.md) (《深入理解 AI Agent》, 리보제; 중국어 원본이 표준임):
  4층 실패 분류법, 툴-플러스-인자 루프 지문, 유휴 와치독, `tool_result` 쌍 수리와 그 결과물 대 훈련 데이터 분할,
  오류 격리를 통한 단계적 복구, 및 죽음의 나선 방어. 각주 ch5-3은 생산 에이전트 연구에서 분류법을 인용하고,
  Claude Code 등을 포함하며, 구현이 빠르게 진행됨을 경고함. 또한 측정된 생산 오류에서 3회 규정 압축 한계를 설정함.
- [learn-claude-code · s11_error_recovery](https://github.com/shareAI-lab/learn-claude-code): 섹션 구성.
