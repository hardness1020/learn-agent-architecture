# 1 · Agent Loop

[English](README.md) · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · **한국어**

> 한 루프가 모델이 대답하거나 도구를 요청할 때까지 모델을 계속 호출합니다.

원시 모델 호출은 한 번만 수행됩니다. 메시지를 보내고 하나의 응답을 받습니다.

에이전트는 추가 단계가 필요합니다. 모델이 요청한 도구를 실행하고 결과를 추가한 후 모델을 다시 호출해야 합니다. 동일한 `messages[]`는 턴이 진행되는 동안 계속 확장되어야 합니다.

루프는 다음을 수행해야 합니다:

1. 호출 간 대화 상태를 유지합니다.
2. tool use와 최종 답변을 구별합니다.
3. 요청된 도구를 실행하고 결과를 추가합니다.
4. 모델이 중단될 때까지 다시 호출합니다.

이 루프가 없으면 모델은 행동에 대해 추론할 수 있지만 실제로 행동할 수 없습니다. 루프가 잘못되면 너무 일찍 멈추거나 영원히 실행됩니다.

---

## 메커니즘

![메커니즘 다이어그램](assets/01-agent-loop.png)

하나의 `messages[]`에 대해 두 개의 루프가 있습니다.

채팅 창을 상상해 보세요. 당신이 "타이베이 날씨가 어때요? 우산을 가져가야 하나요?"라고 묻습니다.
모델은 먼저 날씨 도구를 호출하고, 결과를 확인한 후에 비올 확률 도구를 호출하고, 그 다음에야 답변을 줄 수 있습니다.
**따라서 한 턴 안에서 모델은 종종 여러 번 호출되며 중간에 도구 호출이 있습니다.**
질문부터 최종 답변까지 전체 구간이 내부 루프입니다: 하나의 사용자 턴.
모델을 호출하고, `stop_reason`를 확인하고, 필요하면 도구를 실행하고, 결과를 추가하며, 모델이 해당 턴에 대한 답변을 줄 때까지 반복합니다.

그런 다음 같은 창에서 "내일은 어떨까요?"라고 묻습니다. 이것이 새로운 턴입니다.
외부 루프는 문자열이 차례차례 하나의 대화로 이어지는 것입니다.
각 새로운 차례는 동일한 `messages[]`에 추가되므로, 모델이 "내일"이라고 대답할 때에도 당신이 타이페이에 대해 물었던 것을 여전히 볼 수 있습니다.

내부 루프는 호출자가 소유한 `messages[]`에서의 한 차례입니다:

```python
def run_turn(messages, model, max_steps=10):        # src/loop.py · one turn over the shared messages[]
    for _ in range(max_steps):                       # the inner loop, with a backstop
        response = model(messages)                   # one Anthropic Messages call
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason != "tool_use":       # model produced its answer for this turn
            return final_text(response)

        results = []                                 # tool_use: run each, feed back
        for block in response.content:
            if block.type == "tool_use":
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": run_tool(block.name, block.input)})
        messages.append({"role": "user", "content": results})

    raise RuntimeError("hit max_steps without end_turn")
```

- [`src/loop.py`](src/loop.py)의 `run_turn()`이 내부 루프입니다.
- `messages`은 Anthropic Messages 형식의 공유 상태입니다.
- `max_steps`은 무한 루프를 방지하기 위한 안전 한도입니다.
- `run_tool(name, input)`은 도구를 해결하고 실행하며 `tool_result`에 대한 텍스트를 반환합니다.
- [`src/demo.py`](src/demo.py)의 `model()`은 하나의 `client.messages.create` 호출입니다. 루프는 하나의 제공자에 의존하지 않습니다.

외부 루프는 한 번의 턴마다 사용자 메시지를 하나씩 추가하고 버퍼를 유지합니다:

```python
messages = []                                        # src/demo.py · the conversation, owned by the caller
for user_text in turns:                              # the outer loop: one iteration per user turn
    messages.append({"role": "user", "content": user_text})
    reply = run_turn(messages, model)                # appends in place; turn N sees turns 1..N-1
```

두 개의 `stop_reason` 값이 루프를 구동합니다:

- `tool_use`: 도구를 실행하고, 결과를 추가하고, 다시 모델을 호출합니다.
- `end_turn`: 최종 답변을 반환합니다. 데모는 `tool_use`가 아닌 값에서 중지됩니다.

`messages[]`는 이번 세션의 전체 대화 메모리입니다. 도구 결과와 어시스턴트 응답이 모두 여기에 포함됩니다. 다음 모델 호출은 해당 전체 상태를 기반으로 추론합니다.

이 단순 루프에는 권한 게이트가 없습니다. 섹션 3에서는 도구 실행 전에 이 게이트를 추가합니다.

---

## 시스템별

각 에이전트가 루프를 어떻게 소유하며 언제 중지할지 결정하는 방법입니다.

| | Claude Code | mini-swe-agent | deepseek-harness |
| --- | --- | --- | --- |
| **장점** | 스트림 진행, 부작용 제어, 도구 병렬 실행. | 작은 루프, 읽고 감사하기 쉬움. | 교체 가능한 루프, 가로채기 가능한 단계, 재생 가능한 로그. |
| **단점** | 루프가 더 큰 runtime 안에 있음. | 부작용 제어 없음, 스트리밍 없음, 병렬 도구 없음. | 가장 많은 이동 부품. 회전(turn), 단계(step), 인박스(inbox) 용어 필요. |
| **이유** | 하나의 핵심 분기를 유지하고 그 주위에 기능 추가. | 최소한의 루프가 핵심. 완료 여부는 모델이 아니라 환경이 감지. | 루프는 동료들 사이의 plugin 중 하나. |
| **방법: 루프 드라이버** | 비동기 제너레이터. 도구는 하나의 계약을 통해 연결. | while 루프. 모델에게 명령을 요청하고 실행. | 내구성 있는 이벤트 로그 위에서 교체 가능한 plugin. |
| **방법: 정지 신호** | `stop_reason: end_turn`. | 환경은 제출 마커를 보고 `role: "exit"`를 추가합니다. | 미지급, 체크포인트 블록 없음, 또는 턴 종료 결과. |
| **방법: 병렬 도구** | 예. 하나의 모델 턴에서 호출은 병렬로 실행될 수 있습니다. | 아니요. 작업은 순서대로 실행됩니다. | 예. 배타적 호출은 장벽을 형성하고; 안전 호출은 제한된 풀을 공유합니다. |
| **방법: 스트리밍** | 예. 발생하는 즉시 모델 토큰, 도구 호출 및 도구 결과를 제공합니다. | 아니요. | 예. 세션 로그에 스트림 청크가 지속 가능한 이벤트로 기록됩니다. |

---

## 실패 모드

- **정지 조건 없음.** 버그나 도구 루프가 무한히 실행될 수 있습니다. 최대 단계 수 또는 토큰 제한을 사용하세요.
- **루프 중간의 컨텍스트 오버플로우.** `messages[]`는 계속 증가합니다. 섹션 8은 context management를 추가합니다.
- **부분 도구 실패.** 실패한 도구도 여전히 `tool_result`를 반환해야 하며, 그래야 모델이 복구할 수 있습니다.
- **결과 손실.** 어시스턴트 도구 호출이나 tool result 중 하나라도 누락되면 기록이 깨집니다. 두 가지 모두 추가하세요.

---

## 실행 가능

[`src/`](src/)은 체인을 다음과 함께 시작합니다:

- [`loop.py`](src/loop.py): 내부 루프와 공유 `messages[]`.
- [`demo.py`](src/demo.py): 두 턴의 라이브 데모. 두 번째 턴은 첫 번째 턴이 버퍼에 남아 있어야 의존합니다.
- [`test.py`](src/test.py): 도구 배치, 최종 텍스트, 다중 턴 상태에 대한 오프라인 점검.

2~11절은 이 `src/`를 앞으로 진행시키며, `loop.py`를 발전시키고 각 섹션마다 하나의 파일을 추가합니다.

```bash
python sections/01-agent-loop/src/test.py         # offline checks, no key
uv run python sections/01-agent-loop/src/demo.py  # live demo, needs a key
```

---

## 출처

- [Claude Code 소스](https://github.com/yasasbanukaofficial/claude-code): `QueryEngine.ts`, `query/`, `Tool.ts`.
- [mini-swe-agent 소스](https://github.com/swe-agent/mini-swe-agent): `agents/default.py`, `exceptions.py`, `environments/local.py`.
- [deepseek-harness 소스](https://github.com/deepseek-ai/deepseek-harness) 위치 `dsh-v0.1.0-rc.7`:
  `docs/architecture.md`, `docs/agent-lifecycle.md`, `docs/subsystems/core.md`, `packages/core/agent-loop/src/agent.ts`, `packages/core/agent/src/types.ts`.
- [learn-claude-code · s01 Agent Loop](https://github.com/shareAI-lab/learn-claude-code): 섹션 프레이밍.
