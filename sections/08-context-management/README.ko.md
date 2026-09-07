# 8 · Context management

[English](README.md) · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · **한국어**

> 긴 세션은 컨텍스트 한도 내에서 유지하세요.

`messages[]`는 실행 중에 증가합니다. 각 tool result, 어시스턴트 응답, 사용자 차례는 더 많은 텍스트를 추가합니다. 긴 세션은 결국 모델의 컨텍스트 한도에 도달할 것입니다.

Context management는 세션을 사용 가능하게 유지합니다. 다음 모델 호출 전에 이전 내용을 제거, 스텁 처리, 보존 또는 요약합니다.

컨텍스트가 가득 차면:

1. API는 요청을 거부할 수 있습니다.
2. 호출이 느려지고 비용이 증가합니다.
3. 오래되고 덜 유용한 내용이 현재 작업 정보와 경쟁합니다.

세 번째 항목에는 이름이 있습니다: 컨텍스트 부패(context rot). 관련 없는 텍스트가 쌓이면서 모델이 정확한 사실을 찾아낼 가능성이 줄어듭니다.
이것은 윈도우가 가득 차기 훨씬 전에 시작됩니다. 에이전트는 계속 실행되지만 단지 결정이 더 나빠집니다.

그래서 압축은 단순히 적합성과 비용만의 문제가 아닙니다. 문맥 내 학습은 추론보다는 검색처럼 작동합니다.
모델은 기록된 사실을 찾을 수 있습니다. 하지만 수십 번에 걸쳐 분산된 사실을 결합하는 데는 더 서툽니다.
결론을 한 번 기록하는 것이 매번 호출할 때 모델이 다시 도출하게 하는 것보다 저렴합니다.
그래서 좋은 요약은 윈도우에 여유가 있어도 답변을 개선합니다.

이 계층이 없으면 프롬프트가 더 이상 맞지 않을 때 긴 작업은 실패합니다.

---

## 메커니즘

![메커니즘 다이어그램](assets/08-context-management.png)

요약 전에 저렴한 축소기를 사용하세요. 저렴한 축소기는 지역적이며 대부분 손실이 없습니다. 요약은 모델 호출 비용이 들고 세부 사항을 잃을 수 있습니다.

Claude Code는 계층화된 순서를 사용합니다:

```text
budget   -> persist huge tool results to disk, leave a preview
snip     -> drop stale middle turns, keep head + recent tail
micro    -> replace old tool-result bodies with a stub
collapse -> optional independent context system
auto     -> LLM summarizes the whole history into one message
--- on prompt_too_long despite the above ---
reactive -> truncate the head and re-summarize, with a retry cap
```

순서가 중요합니다. 예를 들어, 큰 tool result는 어떤 패스가 본문을 스텁으로 바꾸기 전에 먼저 저장되어야 합니다.

### 새로운 항목: 축소 패스

```python
def manage(messages, summarizer=None):                 # src/context.py, run every turn
    _budget(messages)                                  # persist huge results   (lossless)
    _micro(messages, KEEP_RECENT)                      # stub old result bodies (cheap)
    if summarizer and estimate_tokens(messages) > TOKEN_LIMIT:
        return _auto(messages, KEEP_RECENT, summarizer)  # summarize history (lossy, last resort)
    return messages
```

- `manage`는 각 턴마다 저렴한 패스를 실행합니다.
- `_budget`는 과도하게 큰 도구 결과를 디스크에 쓰고 짧은 미리보기를 남깁니다.
- `_micro`는 오래된 도구 결과 본문을 스텁으로 만듭니다.
- `_auto`는 첫 번째 턴과 최근 꼬리를 유지한 후 가운데를 요약합니다.
- `summarizer=None`는 데모에서 정보 손실 요약을 비활성화합니다.

### 통합 방식

Context management는 각 모델 호출 전에 실행됩니다:

```python
for _ in range(max_steps):                             # src/loop.py
    messages = context.manage(messages, summarizer=summarizer)   # 8 · keep context under the window
    response = model(messages, registry)
    ...
```

이 섹션은 루프 본문 자체를 변경합니다. 이전 섹션들은 도구나 디스패치 동작을 추가하고 루프는 그대로 두었습니다.
컨텍스트 축소는 매 모델 호출 전에 실행되어야 하므로 루프 안에 있어야 합니다.

루프는 여전히 동일한 불변성을 유지합니다: 유효한 `messages[]`로 모델을 호출한 다음, 응답과 도구 결과를 추가합니다.

### 대비: 흘러나온 도구 출력

Claude Code와 이 섹션의 `_budget` 모두 큰 tool result를 제자리에서 축소합니다. 잘려 나간 텍스트는 사라집니다.

deepseek-harness는 이미 발생한 일을 절대 편집하지 않습니다. 세션 로그는 단지 추가되기만 하며, 모델이 보는 메시지는 그 로그의 투영입니다.
축소는 어느 구간을 교체할지 말해주는 또 하나의 기록된 이벤트이므로, 다시 시작된 세션이나 분기된 세션은 동일한 뷰를 재생합니다.

큰 도구 출력은 그 어떤 것보다도 먼저 별도의 경로를 가집니다. 인라인 바이트 제한을 초과하는 결과는 도구가 반환되는 즉시 스필 저장소로 갑니다.
저장은 전체 텍스트를 저장하고 로케이터를 반환합니다. 컨텍스트에 남는 것은 머리와 꼬리 미리보기, 그 로케이터, 그리고 읽거나 grep 하라는 힌트입니다.
그래서 출력은 여전히 접근 가능합니다: 모델은 나머지가 필요할 때 파일을 요청합니다.

[`src/spill.py`](src/spill.py)은 이것의 축소 버전입니다. 이것은 대비 데모이며 `manage()`에 연결되어 있지 않아서, 이후 섹션들은 동일한 경로를 이어갑니다.

### 추가 읽기

이것들 중 어느 것도 `src/`에는 없습니다. 이것은 ai-agent-book에서 나온 것이며 테이블에 있는 시스템과 확인된 것은 아닙니다.

**스텁은 매번 같은 문자열이어야 합니다.** tool result를 대체하는 텍스트는 접두사의 일부이므로 바이트 단위로 동일해야 합니다.
첫 번째 대체 시 선택하고 디스크에서 세션이 복원된 이후에도 재사용하십시오.
새로운 타임스탬프나 새로운 경로로 다시 렌더링되는 스텁은 접두사를 변경하며, 그 후 캐시는 유효하지 않게 됩니다.

**압축과 캐시는 서로 반대되는 요구를 합니다.** 압축은 히스토리를 다시 씁니다. 캐시는 히스토리를 그대로 둘 때만 효과가 있습니다.
모든 편집은 편집 시점 이후의 캐시를 무효화하므로, 다음 호출 시 전체 접두사를 다시 읽어야 합니다.
매번 조금씩 다듬으면 그 재구성이 매번 발생합니다. 토큰 임계값에서 한 번 더 큰 축소를 수행하면 한 번만 발생합니다.
어쨌든, 압축(compaction)은 API 호출 사이에서 실행되며, 호출 내부에서는 절대 실행되지 않습니다.

**API는 이 패스를 서버에서 실행할 수 있습니다.** Claude API에서의 컨텍스트 편집은 접두사에서 이전 도구 결과를 제거하므로, harness는 이에 대한 코드를 포함하지 않습니다.
캐시는 여전히 한 번 재구성됩니다. 이는 매번 수행되는 대신 순서의 오버플로우 끝 근처에 위치하게 됩니다.

**현재 작업에 대한 요약을 작성하세요, 세션 전체에 대한 것이 아닙니다.** 모든 발생한 일을 복습하는 것은 다음 호출에 필요하지 않습니다.
대신 한 가지 질문을 하세요: 다음 호출에 아직 필요한 것은 무엇입니까? 다음 항목들을 우선 순위가 높은 순서대로 유지하세요:

- 이미 내려진 아키텍처와 설계 결정.
- 생성되거나 변경된 파일 및 그 변경 내용.
- 마지막 검사 또는 테스트의 합격 및 실패 상태.
- 열린 TODO 및 현재 단계.

무언가가 이동해야 할 때 원시 도구 출력이 먼저 나옵니다. 예산 패스는 이미 큰 결과를 디스크에 작성했으므로, 에이전트는 필요할 때 이를 읽을 수 있습니다.

---

## 시스템별

각 에이전트가 공간을 확보하는 방법과 제거하는 항목.

| | Claude Code | mini-swe-agent | deepseek-harness |
| --- | --- | --- | --- |
| **장점** | 긴 세션도 유지됩니다. 축소 비용이 저렴하고 출력물을 다시 읽을 수 있습니다. | 스케줄링이나 조정이 필요 없습니다. 감사가 쉽습니다. | 기록이 절대 삭제되지 않습니다. |
| **단점** | 패스는 순서 규칙이 필요합니다. 요약은 세부 정보를 놓칠 수 있습니다. | 기록만 증가합니다. 긴 실행은 오버플로우 시 종료됩니다. | 로그가 디스크에 증가하며, 잠금과 폴드가 필요합니다. |
| **왜** | 인터랙티브 세션은 열린 형태이므로, 창이 채워집니다. | 예산이 먼저 실행을 종료한다고 가정합니다(섹션 21). | 로그가 진실이므로, 보기만 줄어듭니다. |
| **방법: 트리거** | 토큰 임계값, plus `prompt_too_long`에 대한 예비 대책. | 매 관찰, 렌더링 시점에서. | 각 단계의 측정된 압력, plus 확인된 오버플로. |
| **방법: 전략** | 저렴한 리듀서 먼저(지속, 스텁), 요약은 마지막. | 긴 출력은 앞부분과 뒷부분으로 잘라냄. 압축 없음. | 유출, 가지치기, 그런 다음 요약 이벤트. |
| **방법: 예산** | 출력과 안전 버퍼 예약. | 관찰당 10k 문자. | 라우팅된 모델별 비율: 0.8에서 압축, 0.16 유지. |

---

## 실패 모드

- **요약은 필요한 세부 정보를 잃습니다.** 전체 출력을 유지하고 필요 시 파일을 다시 읽으세요.
- **압축이 반복적으로 실패함.** 재시도 한도나 서킷 브레이커를 사용하세요.
- **하나의 큰 회전이 어쨌든 오버플로됨.** `prompt_too_long`에 반응하여 제한된 최후 수단 트림을 수행하세요.
- **잘못된 패스 순서로 데이터가 손실됨.** 기존 결과를 스터빙하기 전에 큰 결과를 저장하세요.
- **깨진 도구 쌍.** `tool_use`을 해당하는 `tool_result`에서 분리하지 마세요.
- **스터브 텍스트가 이동함.** 새 타임스탬프나 경로로 다시 렌더링되는 미리보기는 접두사를 변경하고 캐시를 삭제합니다. 문자열을 처음 사용할 때 고정하세요.
- **모든 회전에서 트리밍.** 각 편집은 편집 지점 이후의 캐시를 무효화하므로, 여러 작은 축소가 한 번의 일괄 패스보다 비용이 더 많이 듭니다. 임계값에서 트리거하세요.
- **모델은 요약을 신뢰합니다.** 주입된 상태는 사실로 읽히며 거의 다시 확인되지 않습니다. 잘못된 요약이 잡힐 수 있도록 영구적으로 저장된 원본의 포인터를 남겨두세요.

---

## 실행 가능

[`src/`](src/)는 07을 이어가며 다음을 추가합니다:

- [`context.py`](src/context.py): `budget`, `micro`, `auto`가 `manage`를 통해 실행됩니다.
- [`loop.py`](src/loop.py): 매 턴의 시작에서 `context.manage()`을 호출합니다.
- [`spill.py`](src/spill.py): deepseek-harness 대비: 큰 결과는 전체가 저장되며, 컨텍스트는 미리보기와 경로를 유지합니다.
- [`test.py`](src/test.py): 각 실행을 독립적으로 확인하며, 전체 텍스트를 읽을 수 있는 스필도 유지합니다.
- [`demo.py`](src/demo.py): context management가 연결된 상태로 루프를 구동합니다.

```bash
python sections/08-context-management/src/test.py         # offline checks, no key
uv run python sections/08-context-management/src/demo.py  # live demo, needs a key
```

---

## 출처

- [Claude Code 소스](https://github.com/yasasbanukaofficial/claude-code):
  `services/compact/autoCompact.ts`, `microCompact.ts`, `timeBasedMCConfig.ts`, `compact.ts`, `utils/toolResultStorage.ts`, `query.ts`, `query/tokenBudget.ts`.
- [mini-swe-agent 소스](https://github.com/swe-agent/mini-swe-agent): `models/litellm_model.py`의 `config/mini.yaml`, `abort_exceptions`에서의 관찰 템플릿.
- [deepseek-harness 소스](https://github.com/deepseek-ai/deepseek-harness) `dsh-v0.1.0-rc.7`에서:
  `packages/compaction/compaction/src/index.ts`, `packages/compaction/compaction-basic/README.md`, `packages/llm/token-meter/src/index.ts`,
  `packages/spill/spill/src/index.ts`, `packages/spill/spill-policy/README.md`, `docs/subsystems/compaction.md`, `docs/subsystems/session.md`.
- [learn-claude-code · s08_context_compact](https://github.com/shareAI-lab/learn-claude-code): 섹션 프레이밍.
- [ai-agent-book · 2장](https://github.com/bojieli/ai-agent-book/blob/main/book/chapter2.md) (《深入理解 AI Agent》, 李博杰; 중국어 원본이 기준임):
  컨텍스트 붕괴, 검색으로서의 인-컨텍스트 학습, 압축과 캐시의 상호작용, 유지 우선순위를 고려한 작업 인지 압축,
  API 수준의 컨텍스트 편집, 고정된 도구-결과 스텁, 모델이 주입된 요약을 사실로 읽는다는 발견.
- [Lost in the Middle](https://arxiv.org/abs/2307.03172) (Liu et al., TACL 2024): 긴 컨텍스트 중간에 위치한 사실에 대한 검색 정확도가 떨어진다. 컨텍스트 붕괴를 정립함.

추론됨; 위 Claude Code 소스 저장소에 완전히 존재하지 않음:

- `snipCompact.ts`: `snipCompactIfNeeded(messages)` 호출 지점만 보임.
- `reactiveCompact.ts`: 반응 경로가 `compact.ts`에 존재하는 것으로 보임.
