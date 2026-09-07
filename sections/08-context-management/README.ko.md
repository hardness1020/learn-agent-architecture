# 8 · Context 관리

[English](README.md) · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · **한국어**

> 긴 session을 context 한도 아래로 유지합니다.

`messages[]`는 실행 중에 계속 자랍니다. tool result, assistant 응답, 사용자 turn마다 텍스트가 늘어납니다. 긴 session은 결국 모델의 context 한도에 닿습니다.

context 관리는 session을 쓸 수 있는 상태로 유지합니다. 다음 모델 호출 전에 오래된 내용을 지우거나, 스텁으로 바꾸거나, 디스크에 남기거나, 요약합니다.

context가 차면 이런 일이 벌어집니다.

1. API가 요청을 거부할 수 있습니다.
2. 호출이 느려지고 비싸집니다.
3. 오래되고 덜 쓸모 있는 내용이 지금 작업에 필요한 정보와 경쟁합니다.

세 번째에는 이름이 있습니다. context rot입니다. 관계없는 텍스트가 쌓일수록 모델이 맞는 사실을 찾아내는 비율이 떨어집니다.
이 현상은 window가 다 차기 훨씬 전부터 시작됩니다. agent는 계속 돕니다. 다만 판단이 나빠집니다.

그래서 compaction은 크기와 비용만의 문제가 아닙니다. in-context learning은 추론보다 검색에 가깝게 동작합니다.
모델은 적혀 있는 사실은 찾아냅니다. 수십 turn에 흩어진 사실들을 엮는 일은 더 못합니다.
결론을 한 번 적어 두는 편이, 매 호출마다 모델이 그것을 다시 유도하게 하는 것보다 쌉니다.
그래서 좋은 요약은 window에 자리가 남아 있을 때에도 답을 개선합니다.

이 계층이 없으면 prompt가 더 이상 들어가지 않는 순간 긴 작업이 실패합니다.

---

## 메커니즘

![Mechanism diagram](assets/08-context-management.png)

요약보다 먼저 값싼 축약기를 씁니다. 값싼 축약기는 국소적이고 거의 손실이 없습니다. 요약은 모델 호출 비용이 들고 세부를 잃을 수 있습니다.

Claude Code는 계층화된 순서를 씁니다.

```text
budget   -> persist huge tool results to disk, leave a preview
snip     -> drop stale middle turns, keep head + recent tail
micro    -> replace old tool-result bodies with a stub
collapse -> optional independent context system
auto     -> LLM summarizes the whole history into one message
--- on prompt_too_long despite the above ---
reactive -> truncate the head and re-summarize, with a retry cap
```

순서가 중요합니다. 예를 들어 큰 tool result는 어떤 과정이 그 본문을 스텁으로 바꾸기 전에 먼저 디스크에 남겨야 합니다.

### 새로 추가: 축약 과정

```python
def manage(messages, summarizer=None):                 # src/context.py, run every turn
    _budget(messages)                                  # persist huge results   (lossless)
    _micro(messages, KEEP_RECENT)                      # stub old result bodies (cheap)
    if summarizer and estimate_tokens(messages) > TOKEN_LIMIT:
        return _auto(messages, KEEP_RECENT, summarizer)  # summarize history (lossy, last resort)
    return messages
```

- `manage`는 매 turn마다 값싼 과정들을 돌립니다.
- `_budget`은 지나치게 큰 tool result를 디스크에 쓰고 짧은 미리보기만 남깁니다.
- `_micro`는 오래된 tool result 본문을 스텁으로 바꿉니다.
- `_auto`는 첫 turn과 최근 꼬리를 남기고, 가운데를 요약합니다.
- `summarizer=None`이면 데모에서 손실이 있는 요약이 꺼집니다.

### 통합 방식

context 관리는 모델 호출마다 그 앞에서 돕니다.

```python
for _ in range(max_steps):                             # src/loop.py
    messages = context.manage(messages, summarizer=summarizer)   # 8 · keep context under the window
    response = model(messages, registry)
    ...
```

이 섹션은 loop 본문 자체를 바꿉니다. 앞선 섹션들은 tool이나 dispatch 동작을 추가했을 뿐 loop는 건드리지 않았습니다.
context 축약은 모든 모델 호출 앞에서 돌아야 하므로, loop 안에 있어야 합니다.

loop는 여전히 같은 불변식을 지킵니다. 유효한 `messages[]`로 모델을 호출하고, 그 응답과 tool result를 뒤에 덧붙입니다.

### 대비: 흘려 보낸 tool 출력

Claude Code와 이 섹션의 `_budget`은 둘 다 큰 tool result를 제자리에서 줄입니다. 잘려 나간 텍스트는 사라집니다.

deepseek-harness는 일어난 일을 절대 고치지 않습니다. session 로그에는 덧붙이기만 하고, 모델이 보는 메시지는 그 로그의 투영입니다.
축약도 어느 구간을 무엇으로 바꿀지 적어 둔 이벤트 하나이므로, 재개하거나 fork한 session도 같은 화면을 재생합니다.

큰 tool 출력은 그 모든 것에 앞서 별도 경로를 탑니다. 인라인 바이트 상한을 넘는 결과는 tool이 반환하는 즉시 spill 저장소로 갑니다.
저장소는 전체 텍스트를 저장하고 위치 식별자를 돌려줍니다. context에 남는 것은 머리와 꼬리 미리보기, 그 식별자, 그리고 읽거나 grep하라는 힌트입니다.
그래서 출력은 여전히 닿을 수 있습니다. 모델은 나머지가 필요할 때 그 파일을 요청합니다.

[`src/spill.py`](src/spill.py)는 이것의 축소판입니다. 대비용 데모이고 `manage()`에 연결되어 있지 않으므로, 이후 섹션들은 같은 과정들을 그대로 이어받습니다.

### 더 읽을거리

여기부터는 `src/`에 없습니다. ai-agent-book에서 온 내용이고, 표에 있는 시스템들에서 확인된 것은 아닙니다.

**스텁은 매번 같은 문자열이어야 합니다.** tool result를 대신하는 텍스트는 prefix의 일부이므로, 바이트 단위로 같아야 합니다.
처음 교체할 때 정하고 계속 재사용합니다. 디스크에서 session을 복원한 뒤에도 마찬가지입니다.
새 타임스탬프나 새 경로로 다시 그려지는 스텁은 prefix를 바꾸고, 그 뒤의 캐시는 사라집니다.

**압축과 캐시는 서로 반대를 원합니다.** compaction은 히스토리를 다시 씁니다. 캐시는 히스토리를 건드리지 않을 때만 이득입니다.
편집할 때마다 그 지점부터의 캐시가 무효가 되므로, 다음 호출은 prefix 전체를 다시 읽습니다.
매 turn마다 조금씩 다듬으면 그 재구축이 매 turn마다 일어납니다. token 임계값에서 한 번 크게 줄이면 한 번만 일어납니다.
어느 쪽이든 compaction은 API 호출 사이에서 돌고, 호출 안에서는 절대 돌지 않습니다.

**API가 이 과정을 서버에서 돌릴 수도 있습니다.** Claude API의 context editing은 오래된 tool result를 prefix에서 떨어뜨리므로, harness는 그에 해당하는 코드를 넣지 않습니다.
그래도 캐시는 한 번 다시 만들어집니다. 그래서 이것은 매 turn 쪽이 아니라 순서상 오버플로 쪽에 가깝습니다.

**요약은 session 전체가 아니라 지금 작업을 위해 씁니다.** 일어난 일 전부를 되짚는 요약은 다음 호출에 필요한 것이 아닙니다.
대신 질문 하나를 던집니다. 다음 호출에 여전히 필요한 것은 무엇인가. 다음을 우선순위가 높은 것부터 남깁니다.

- 이미 내린 아키텍처와 설계 결정.
- 만들거나 고친 파일, 그리고 무엇이 바뀌었는지.
- 마지막 점검이나 테스트의 통과와 실패 상태.
- 열린 TODO와 현재 단계.

무언가를 덜어내야 한다면 원시 tool 출력이 가장 먼저 나갑니다. budget 과정이 큰 결과를 이미 디스크에 써 두었으므로, 필요할 때 agent가 그것을 다시 읽을 수 있습니다.

---

## 시스템별

각 agent가 자리를 비우기로 결정하는 방식과 무엇을 덜어내는지입니다.

| | Claude Code | mini-swe-agent | deepseek-harness |
| --- | --- | --- | --- |
| **장점** | 긴 session이 살아남음. 축약이 싸고 출력을 다시 읽을 수 있음. | 예약하거나 조율할 것이 없음. 감사하기 쉬움. | 히스토리가 절대 파괴되지 않음. |
| **단점** | 과정마다 순서 규칙이 필요함. 요약이 세부를 잃을 수 있음. | 히스토리가 자라기만 함. 긴 실행은 오버플로로 죽음. | 로그가 디스크에서 자라고, 락과 접기가 필요함. |
| **이유** | 대화형 session은 끝이 열려 있어서 window가 참. | 예산이 실행을 먼저 끝낸다고 가정함 (섹션 21). | 로그가 진실이므로 줄어드는 것은 화면뿐. |
| **방법: trigger** | token 임계값, 그리고 `prompt_too_long`에 대한 대비책. | 관측마다, 렌더링 시점에. | 매 단계 측정한 압력, 그리고 확인된 오버플로. |
| **방법: strategy** | 값싼 축약기 먼저(디스크 저장, 스텁), 요약은 마지막. | 긴 출력을 머리와 꼬리로 잘라냄. compaction은 없음. | spill, 정리, 그다음 요약 이벤트. |
| **방법: budget** | 출력용 여유분과 안전 여유분을 확보함. | 관측 하나당 문자 1만 개. | 라우팅된 모델별 비율. 0.8에서 compaction, 0.16을 유지. |

---

## 실패 모드

- **요약이 필요한 세부를 잃음.** 전체 출력을 디스크에 남기고 필요할 때 파일을 다시 읽습니다.
- **compaction이 반복해서 실패함.** 재시도 상한이나 서킷 브레이커를 씁니다.
- **거대한 turn 하나가 그래도 넘침.** `prompt_too_long`에 대해 한도가 정해진 최후의 잘라내기로 대응합니다.
- **과정 순서가 틀려 데이터를 잃음.** 오래된 결과를 스텁으로 바꾸기 전에 큰 결과를 먼저 디스크에 남깁니다.
- **짝이 깨진 tool 쌍.** `tool_use`와 그에 대응하는 `tool_result`를 갈라놓지 않습니다.
- **스텁 텍스트가 흔들림.** 새 타임스탬프나 경로로 다시 그려지는 미리보기는 prefix를 바꾸고 캐시를 날립니다. 처음 쓸 때 문자열을 고정합니다.
- **매 turn마다 다듬기.** 편집할 때마다 그 뒤의 캐시가 무효가 되므로, 작은 축약을 여러 번 하는 것이 한 번에 모아서 하는 것보다 비쌉니다. 임계값에서 발화시킵니다.
- **모델이 요약을 믿음.** 주입된 상태는 사실로 읽히고 거의 다시 확인되지 않습니다. 잘못된 요약을 잡을 수 있도록 저장해 둔 원본으로 가는 포인터를 남깁니다.

---

## 실행 방법

[`src/`](src/)는 07을 이어받아 다음을 추가합니다.

- [`context.py`](src/context.py): `budget`, `micro`, `auto` 과정이 `manage`를 통해 돕니다.
- [`loop.py`](src/loop.py): 매 turn 맨 앞에서 `context.manage()`를 호출합니다.
- [`spill.py`](src/spill.py): deepseek-harness와의 대비입니다. 지나치게 큰 결과를 통째로 저장하고, context에는 미리보기와 경로를 남깁니다.
- [`test.py`](src/test.py): 각 과정을 따로 확인하고, spill 후에도 전체 텍스트를 읽을 수 있는지 확인합니다.
- [`demo.py`](src/demo.py): context 관리를 연결한 채로 loop를 돌립니다.

```bash
python sections/08-context-management/src/test.py         # offline checks, no key
uv run python sections/08-context-management/src/demo.py  # live demo, needs a key
```

---

## 출처

- [Claude Code 소스](https://github.com/yasasbanukaofficial/claude-code):
  `services/compact/autoCompact.ts`, `microCompact.ts`, `timeBasedMCConfig.ts`, `compact.ts`, `utils/toolResultStorage.ts`, `query.ts`, `query/tokenBudget.ts`.
- [mini-swe-agent 소스](https://github.com/swe-agent/mini-swe-agent): `config/mini.yaml`의 관측 템플릿, `models/litellm_model.py`의 `abort_exceptions`.
- [deepseek-harness 소스](https://github.com/deepseek-ai/deepseek-harness), `dsh-v0.1.0-rc.7` 기준:
  `packages/compaction/compaction/src/index.ts`, `packages/compaction/compaction-basic/README.md`, `packages/llm/token-meter/src/index.ts`,
  `packages/spill/spill/src/index.ts`, `packages/spill/spill-policy/README.md`, `docs/subsystems/compaction.md`, `docs/subsystems/session.md`.
- [learn-claude-code · s08_context_compact](https://github.com/shareAI-lab/learn-claude-code): 섹션 구성 참고.
- [ai-agent-book · chapter 2](https://github.com/bojieli/ai-agent-book/blob/main/book/chapter2.md) (《深入理解 AI Agent》, 李博杰. 중국어 원문이 기준):
  context rot, 검색으로서의 in-context learning, 압축과 캐시의 상호작용, 보존 우선순위를 둔 작업 인지 압축,
  API 수준의 context editing, 고정된 tool result 스텁, 그리고 모델이 주입된 요약을 사실로 읽는다는 발견.
- [Lost in the Middle](https://arxiv.org/abs/2307.03172) (Liu et al., TACL 2024): 긴 context 가운데에 놓인 사실은 검색 정확도가 떨어집니다. context rot의 근거입니다.

추정한 내용이며, 위 Claude Code 소스 리포지터리에 온전히 들어 있지는 않습니다.

- `snipCompact.ts`: `snipCompactIfNeeded(messages)` 호출 지점만 보입니다.
- `reactiveCompact.ts`: reactive 경로는 `compact.ts` 안에 있는 것으로 보입니다.
