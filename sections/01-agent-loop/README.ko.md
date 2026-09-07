# 1 · Agent Loop

[English](README.md) · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · **한국어**

> loop 하나가 모델이 답을 내놓거나 tool을 요청할 때까지 모델을 계속 호출합니다.

날것의 모델 호출은 한 번으로 끝납니다. 메시지를 보내고 응답 하나를 받습니다.

agent에는 단계가 하나 더 필요합니다. 모델이 요청한 tool을 실행하고, 결과를 덧붙이고, 모델을 다시 호출해야 합니다. 같은 `messages[]`가 turn 내내 계속 자라야 합니다.

loop는 다음을 해야 합니다.

1. 호출 사이에 대화 상태를 유지합니다.
2. tool 사용인지 최종 답변인지 구분합니다.
3. 요청된 tool을 실행하고 결과를 덧붙입니다.
4. 모델이 멈출 때까지 다시 호출합니다.

이 loop가 없으면 모델은 행동을 추론할 수는 있어도 행동하지는 못합니다. loop가 잘못되면 너무 일찍 멈추거나 끝없이 돕니다.

---

## 메커니즘

![Mechanism diagram](assets/01-agent-loop.png)

하나의 `messages[]` 위에 loop가 둘 있습니다.

채팅 창을 떠올려 봅니다. "타이베이 날씨는 어떤가요? 우산을 챙겨야 할까요?"라고 묻습니다.
모델은 먼저 날씨 tool을 호출하고, 그 결과를 본 뒤 강수 확률 tool을 호출하고, 그러고 나서야 답할 수 있습니다.
**그래서 한 turn 안에서 모델은 여러 번 호출되고, 그 사이사이에 tool call이 끼어듭니다.**
질문에서 최종 답변까지 이어지는 그 구간 전체가 inner loop, 곧 사용자 turn 하나입니다.
inner loop는 모델을 호출하고, `stop_reason`을 확인하고, 필요하면 tool을 실행하고, 결과를 덧붙이며, 모델이 이번 turn의 답을 내놓을 때까지 반복합니다.

그다음 같은 창에서 "내일은요?"라고 묻습니다. 그것이 새 turn입니다.
outer loop는 turn을 차례로 이어 하나의 대화로 엮어 주는 부분입니다.
새 turn은 같은 `messages[]`에 덧붙으므로, 모델이 "내일"에 답할 때도 타이베이를 물었다는 사실을 여전히 봅니다.

inner loop는 호출자가 소유한 `messages[]` 위의 turn 하나입니다.

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

- [`src/loop.py`](src/loop.py)의 `run_turn()`이 inner loop입니다.
- `messages`는 Anthropic Messages 형식으로 된 공유 상태입니다.
- `max_steps`는 loop가 폭주할 때를 대비한 안전 한도입니다.
- `run_tool(name, input)`은 tool을 찾아 실행하고 `tool_result`에 넣을 텍스트를 반환합니다.
- [`src/demo.py`](src/demo.py)의 `model()`은 `client.messages.create` 호출 하나입니다. loop는 특정 공급자에 묶이지 않습니다.

outer loop는 turn마다 사용자 메시지를 하나씩 덧붙이고 버퍼를 그대로 들고 갑니다.

```python
messages = []                                        # src/demo.py · the conversation, owned by the caller
for user_text in turns:                              # the outer loop: one iteration per user turn
    messages.append({"role": "user", "content": user_text})
    reply = run_turn(messages, model)                # appends in place; turn N sees turns 1..N-1
```

`stop_reason` 값 두 개가 loop를 움직입니다.

- `tool_use`: tool을 실행하고, 결과를 덧붙이고, 모델을 다시 호출합니다.
- `end_turn`: 최종 답변을 반환합니다. 데모는 `tool_use`가 아닌 값이면 모두 멈춥니다.

`messages[]`는 이 session의 대화 memory 전체입니다. tool 결과도 assistant 답변도 모두 여기 들어갑니다. 다음 모델 호출은 그 상태 전체를 놓고 추론합니다.

이 맨몸 loop에는 permission gate가 없습니다. 섹션 3이 tool 실행 앞에 그 gate를 더합니다.

---

## 시스템별

각 agent가 loop를 어떻게 소유하고 언제 멈출지를 어떻게 정하는지 봅니다.

| | Claude Code | mini-swe-agent | deepseek-harness |
| --- | --- | --- | --- |
| **장점** | 진행 상황을 streaming하고, 부수 효과를 통제하고, tool을 병렬로 실행함. | 아주 작은 loop, 읽기도 감사하기도 쉬움. | 교체 가능한 loop, 가로챌 수 있는 단계, 재생되는 log. |
| **단점** | loop가 더 큰 runtime 안에 들어 있음. | 부수 효과 gate도, streaming도, 병렬 tool도 없음. | 움직이는 부품이 가장 많음. turn, step, inbox 용어를 익혀야 함. |
| **이유** | 핵심 분기는 하나로 두고 기능은 그 주위에 붙임. | 최소한의 loop 자체가 목적. 완료를 감지하는 쪽은 모델이 아니라 환경. | loop도 동등한 plugin 가운데 하나. |
| **방법: loop 구동부** | 비동기 generator. tool은 계약 하나로 꽂힘. | while loop. 모델에 명령을 물어보고 실행함. | 지속되는 이벤트 log 위에서 도는 교체 가능한 plugin. |
| **방법: 정지 신호** | `stop_reason: end_turn`. | 환경이 제출 표시를 보고 `role: "exit"`을 덧붙임. | 남은 일 없음, checkpoint 블록 없음, 또는 turn을 끝내는 결과. |
| **방법: 병렬 tool** | 예. 한 모델 turn 안의 호출들이 병렬로 실행될 수 있음. | 아니요. 행동은 순서대로 실행됨. | 예. 배타적 호출은 barrier를 이루고, 안전한 호출은 크기가 정해진 풀을 공유함. |
| **방법: streaming** | 예. 모델 token, tool call, tool 결과를 나오는 대로 내보냄. | 아니요. | 예. stream 조각이 session log에 지속되는 이벤트로 쌓임. |

---

## 실패 모드

- **정지 조건 없음.** 버그나 tool loop가 끝없이 돌 수 있습니다. 최대 단계 수나 token 한도를 둡니다.
- **loop 도중의 context 넘침.** `messages[]`는 늘어나기만 합니다. 섹션 8이 context 관리를 더합니다.
- **일부 tool 실패.** 실패한 tool도 `tool_result`는 반드시 반환해야 모델이 복구할 수 있습니다.
- **결과 유실.** assistant의 tool call이나 tool 결과 중 하나라도 빠뜨리면 transcript가 깨집니다. 둘 다 덧붙입니다.

---

## 실행 방법

[`src/`](src/)가 사슬을 시작하며 다음을 담습니다.

- [`loop.py`](src/loop.py): inner loop와 공유 `messages[]`.
- [`demo.py`](src/demo.py): turn 두 개짜리 라이브 데모. turn 2는 turn 1이 버퍼에 남아 있어야 성립합니다.
- [`test.py`](src/test.py): tool dispatch, 최종 텍스트, 여러 turn에 걸친 상태를 확인하는 오프라인 검사.

섹션 2부터 11까지 이 `src/`를 이어받아 `loop.py`를 발전시키고 섹션마다 파일을 하나씩 더합니다.

```bash
python sections/01-agent-loop/src/test.py         # offline checks, no key
uv run python sections/01-agent-loop/src/demo.py  # live demo, needs a key
```

---

## 출처

- [Claude Code source](https://github.com/yasasbanukaofficial/claude-code): `QueryEngine.ts`, `query/`, `Tool.ts`.
- [mini-swe-agent source](https://github.com/swe-agent/mini-swe-agent): `agents/default.py`, `exceptions.py`, `environments/local.py`.
- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness) `dsh-v0.1.0-rc.7`:
  `docs/architecture.md`, `docs/agent-lifecycle.md`, `docs/subsystems/core.md`, `packages/core/agent-loop/src/agent.ts`, `packages/core/agent/src/types.ts`.
- [learn-claude-code · s01 Agent Loop](https://github.com/shareAI-lab/learn-claude-code): 섹션 구성.
