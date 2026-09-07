# 20 · Observability & evaluation

[English](README.md) · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · **한국어**

> 볼 수 없는 것은 고칠 수 없고, 아무도 기록하지 않은 실행은 채점할 수 없습니다.

agent는 사람이 지켜보지 않는 채로 실행되고, 부수 효과를 남기고, 돈을 씁니다. 모델 호출은 token을 소모하고 실제 동작을 일으키는 블랙박스입니다.

계측이 없으면 기본적인 질문에 답할 수 없습니다. 무엇을 했는지. tool이 얼마나 자주 실패했는지. 이 session에 비용이 얼마나 들었는지.

이 섹션은 기록을 담당합니다. 각 단계가 무엇을 했고 비용이 얼마였는지 적어 두고, 그 기록을 저장해도 될 만큼 깨끗하게 유지합니다.

어떤 변경이 품질을 좋게 했는지 나쁘게 했는지는 다른 일입니다. 섹션 23이 그 일을 맡고, 이 섹션이 기록한 것을 재료로 삼아 돌아갑니다.

기록을 빼면 비용이 튈 때마다 영문을 모르게 됩니다. 버그 리포트는 하나도 재현할 수 없습니다. eval 세트는 가져다 쓸 실제 재료가 없습니다.

---

## 메커니즘

![Mechanism diagram](assets/20-observability.png)

loop의 제어 흐름을 절대 건드리지 않는, 서로 분리할 수 있는 두 개의 파이프라인입니다.

telemetry는 인라인으로 실행됩니다: 각 단계가 발사 후 망각 방식의 logger를 호출합니다.

event는 터미널, 파일, Datadog 같은 backend 등 도착지에 해당하는 sink로 갑니다.
logger는 sink가 붙을 때까지 event를 큐에 쌓아 두었다가, 샘플링하고, 민감한 필드를 지우고, 여러 sink로 fan-out 합니다.

평가는 자체 task 세트를 대상으로 오프라인에서 실행됩니다(섹션 23). 그 task 세트는 이 섹션이 기록한 것으로 만듭니다.

- `emit`은 절대 블로킹하지 않고 예외도 던지지 않으므로, 로깅 결함이 loop를 멈추거나 죽일 수 없습니다(섹션 1).
- event는 sink가 붙을 때까지 큐에 쌓였다가 한꺼번에 빠져나가므로, telemetry가 준비되기 전에도 loop가 로그를 남길 수 있습니다.
- 샘플링은 레이트에 따라 event를 버리고, 스크러빙은 허용 목록에 있는 필드만 남기므로, 코드와 경로는 절대 새어 나가지 않습니다.
- 비용은 모델별로 쌓여 하나의 USD 총액이 되고, 실행 중과 종료 시에 표시됩니다.

### 새로 추가: 발사 후 망각 event 로깅

`telemetry.py`는 sink가 붙을 때까지 큐에 쌓였다가 샘플링, 스크러빙, fan-out을 거치는 event를 내보냅니다. `emit`은 절대 예외를 던지지 않습니다:

```python
def emit(self, name, **meta):                          # src/telemetry.py
    if not self.sinks:
        self._queue.append((name, meta))               # buffer until a sink is ready
        return
    self._deliver(name, meta)

def _deliver(self, name, meta):
    if not self.sample(name):                          # dropped by sampling rate
        return
    clean = scrub(meta)                                # allowlist before any backend sees it
    for sink in self.sinks:
        try:
            sink(name, clean)
        except Exception:                              # one bad sink never breaks the loop
            pass
```

- sink가 하나도 붙기 전에는 event가 `_queue`에 쌓이고, `attach`가 같은 `_deliver` 경로로 그것들을 흘려보내므로, 큐에 있던 event도 똑같이 샘플링과 스크러빙을 거칩니다.
- `scrub`은 `SAFE_FIELDS`에 있는 것만 남기므로, 안전하다고 확인되지 않은 값(코드, 파일 경로, prompt)은 backend에 절대 도달하지 않습니다.
- 예외를 던지는 sink는 그대로 삼켜지므로, 고장 난 backend 하나가 loop를 멈추거나 죽일 수 없습니다.

### 새로 추가: 모델별 비용과 오프라인 eval

비용은 모델별로 쌓여 하나의 누적 USD 총액이 됩니다:

```python
def add(self, model, input_tokens, output_tokens):    # src/telemetry.py
    i, o = self.by_model.get(model, (0, 0))
    self.by_model[model] = (i + input_tokens, o + output_tokens)
    pi, po = PRICES.get(model, (0.0, 0.0))             # modelCost.ts pricing tiers
    self.cost_usd += input_tokens * pi + output_tokens * po
    return self.cost_usd
```

- `add`는 token당 단가를 찾아, 실행 중과 종료 시에 표시되는 숫자인 `cost_usd`에 지출을 더합니다.
- 그 총액은 session 전체를 덮습니다. 어떤 task가 그 돈을 썼는지는 알려 주지 않습니다.

여기의 `run_eval`은 있을 수 있는 가장 작은 eval입니다. 고정된 task 세트를 후보 빌드에 다시 돌려서 통과 수를 세고 비율을 돌려줍니다.
섹션 23은 같은 진입점 아래에 환경, 모의 사용자, 반복 실행을 넣습니다. 그리고 그 비율이 조금 떨어진 것이 대개 왜 잡음인지도 설명합니다.

### 통합 방식

demo는 모델 wrapper 위에 telemetry를 얹습니다. loop는 바뀌지 않습니다:

```python
def model(messages, registry, system):
    r = client.messages.create(...)
    cost.add(MODEL, r.usage.input_tokens, r.usage.output_tokens)   # cost rollup
    tel.emit("model_call", model=MODEL, tokens=..., cost_usd=...)  # scrubbed event
    return r
run_turn([...goal...], lambda m, r, s: model(m, r, SYSTEM), reg, Session(mode=DEFAULT))   # the one agent call
```

- telemetry는 바깥에서 관찰합니다: wrapper가 event를 내보내고 비용을 추적하므로, `run_turn`과 dispatch는 섹션 13과 바이트 단위로 동일하게 남습니다.
- sink는 event를 하나씩 출력하고, session 비용은 마지막에 출력되며, 그다음 오프라인 `run_eval`이 고정된 task 세트를 채점합니다.
- 그 위쪽은 전부 그대로입니다. 관측 가능성은 loop에 새로 생긴 단계가 아니라 곁에서 지켜보는 관찰자입니다.

### 더 읽을거리

이 내용은 `src/`에 없습니다. ai-agent-book과 두 개의 tracing 표준에서 온 것이며, 표에 있는 시스템들에서 확인된 내용은 아닙니다.

**평평한 event가 아니라 span.** span은 한 실행 안의 작업 한 조각입니다: 모델 호출, tool 호출, 검색. trace는 실행 전체입니다.
모든 span은 다음을 기록합니다:

- 언제 시작했고 얼마나 걸렸는지,
- 성공했는지,
- 어느 span이 그 부모인지,
- 그 작업을 설명하는 자유 형식 속성.

중요한 것은 부모 링크입니다. 부모 링크는 한 실행의 span들을 트리로 만들어 주고, 그래서 트리를 위에서부터 읽으면 어느 단계가 실패했는지,
어느 단계가 느렸는지, 각 갈래가 얼마를 썼는지 드러납니다.

평평한 event로는 그렇게 할 수 없습니다. event는 호출이 일어났다고만 말할 뿐, 그 호출이 어느 단계에 속했는지는 말하지 않습니다.
사용자 요청 하나가 여러 번의 모델 호출, tool 호출, 검색으로 갈라질 수 있고, 그중 일부는 다른 것 안에 중첩되고 일부는 동시에 실행됩니다.
그것을 타임스탬프로 정리하는 일은 추측입니다.

두 표준이 span의 모양을 고정해 주므로, backend를 추측할 필요가 없습니다:

- OpenTelemetry는 span 자체를 정의합니다: trace id, 부모 id, 시간 측정값, 상태, 속성.
- OpenInference는 그 위에서 LLM 작업에 이름을 붙입니다: prompt, completion, 모델, token 수, tool 호출.

그 이름들에 맞춰 계측을 한 번만 작성해 두면, backend 교체는 다시 짜는 일이 아니라 설정 변경이 됩니다.

내보내기도 `emit`과 같은 규칙을 따릅니다: hot path 밖에 머무릅니다. span은 큐에 들어가고 백그라운드 워커가 묶음으로 보내므로,
수집기가 느려도 실행은 아무 대가를 치르지 않습니다. 이 섹션의 `emit`은 이 모든 것의 평평한 버전입니다.
같은 event에 trace id와 부모 id를 붙이면 트리가 생깁니다.

**비선형 비용과 task별 상한.** 비용은 모델이 읽는 token 수를 따라가고, 매 turn마다 대화 전체를 다시 보냅니다.
그래서 두 번째 turn에 돌아온 tool 결과는 세 번째, 네 번째, 다섯 번째 turn에서 또 값을 치릅니다.
context에 넣은 것은 그 뒤의 모든 turn이 값을 치르고, 총액은 turn 수보다 빠르게 올라갑니다.
단계 수만으로는 그것을 예측할 수 없습니다.

harness의 두 기능이 청구액 일부를 깎아 주지만, 그 절감분은 서로 더해지지 않습니다:

- prompt caching(섹션 10)은 그대로였던 앞부분을 할인해 줍니다.
- compaction(섹션 8)은 오래된 turn을 context에서 덜어 냅니다.

둘은 겹칩니다: compaction이 걷어 내는 token은 caching이 할인해 주었을 바로 그 token입니다.

session 총액 하나는 어떤 task가 그 돈을 썼는지 전혀 말해 주지 않기 때문에, 이 모든 것을 가립니다.
그래서 비용은 task별로 추적하고, task마다 상한을 둡니다. 그 상한은 끝나지 않는 loop를 단계 제한이 멈추는 것과 같은 방식으로 실행을 멈춥니다(섹션 1).
여기서는 그 책이 유일한 출처이고 외부 인용도 달려 있지 않으므로, 이 비용 모델은 저자 한 사람의 현장 기록으로 읽습니다.

**trace가 eval 세트를 채웁니다.** 두 파이프라인은 한 방향으로 만납니다: 프로덕션 trace가 eval task가 됩니다. 세 단계면 하나가 다른 하나로 바뀝니다.

- **고르기.** 배울 만한 실행을 남깁니다: 오류가 난 실행, 사용자가 다시 시도하거나 고친 실행, 나머지보다 비용이 훨씬 많이 든 실행.
  잘 끝난 실행은 보탤 것이 없습니다.
- **지우기.** 코드와 경로가 backend에 들어가지 않게 막는 그 허용 목록이 task 파일에도 똑같이 들어가지 않게 막습니다.
- **다시 만들기.** trace에는 시작 상태와 모든 tool 호출이 들어 있으므로, task의 초기 설정과 그 실행이 도달했어야 할 결과를 둘 다 제공합니다.

이 일을 계속하면 eval 세트가 사용자의 실제 행동을 따라갑니다. 거기 들어온 것은 섹션 23이 채점합니다.

---

## 시스템별

각 agent가 telemetry를 내보내고, 지출을 추적하고, eval 세트를 채우는 방식입니다.

| | Claude Code | mini-swe-agent | deepseek-harness |
| --- | --- | --- | --- |
| **장점** | 풍부한 프로덕션 가시성을 싸고 안전하게 얻음. | 실행이 죽어도 파일은 남음. | 따로 계측할 것이 없음. 모델이 본 것이 그대로 로그로 남음. |
| **단점** | 무슨 일이 있었는지만 말하고, 답이 좋았는지는 말하지 않음. | 프로덕션 telemetry가 거의 없음. | 마스킹 규칙을 제공하지 않음. 전달이 유실되거나 중복될 수 있음. |
| **이유** | loop를 건드리지 않고 프로덕션을 지켜봐야 함. | benchmark가 오프라인에서 채점하므로 전체 기록이 중요함. | session 로그가 이미 기록이므로 그대로 내보냄. |
| **방법: telemetry** | event가 sink를 기다리며 큐에 쌓였다가 샘플링과 마스킹을 거침. | 실행마다 trajectory 파일 하나, 단계마다 저장. | session event를 마스킹 단계를 거쳐 밖으로 복제함. |
| **방법: 비용 추적** | 모델별 token에 단가를 매겨 session 총액으로 합산. | 호출별 단가가 실행 총액과 전역 총액에 쌓임. | 재생 단계가 로그를 달러가 아니라 token으로 계산함. |
| **방법: eval 공급** | 소스에 없음. 마스킹된 trace가 회귀 사례가 됨. | 저장된 trajectory가 benchmark 실행기로 들어감. | 기록된 실행을 키 없이 재생해 fixture로 씀. |

---

## 실패 모드

- **hot path 위의 telemetry.** 블로킹하거나 예외를 던지는 로깅 호출은 loop를 멈춥니다(섹션 1). 네트워크를 기다리는 span 내보내기도 마찬가지입니다.
  완화: sink 앞에 큐를 둔 발사 후 망각, sink별 킬스위치, 백그라운드 워커의 묶음 내보내기.
- **로그로 새는 민감한 데이터.** 코드, 파일 경로, prompt가 누구나 접근하는 backend에, 또는 trace로 만든 task 파일에 도달합니다.
  완화: 로그로 남길 필드를 허용 목록으로 정하고, fan-out이나 저장 전에 나머지를 지웁니다.
- **부모 링크가 없는 평평한 스트림.** 어느 모델 호출이 어느 단계에 속했는지 아무것도 말해 주지 않아서, 실패한 실행을 타임스탬프로 짜 맞춰야 합니다.
  완화: 모든 event에 trace id와 부모 span id를 붙이고, backend가 이미 이해하는 이름 규칙을 따릅니다.
- **눈치채지 못한 비용 증가.** 모델 교체나 폭주한 loop가 지출을 몇 배로 불리는데, session 총액 하나는 그 돈을 태운 task 하나를 가립니다.
  완화: 모델별과 task별 총액을 실행 중과 종료 시에 보여 주고, task별 상한과 loop의 단계 상한을 둡니다(섹션 1).
- **프로덕션에서 멀어지는 eval 세트.** 오프라인 task가 실제 사용을 놓치므로, 스위트는 통과하는데 사용자는 실패합니다(섹션 23).
  완화: 실패했거나 비용이 컸던 실행의 마스킹된 trace를 계속 걸러서 task 세트에 넣습니다.

---

## 실행 방법

[`src/`](src/)는 19를 이어받고 다음을 추가합니다:

- [`telemetry.py`](src/telemetry.py): event logger(`Telemetry.emit`, 큐와 배출, `sample`, `scrub`), 모델별 `CostTracker`, 그리고 오프라인 `run_eval`.
- [`test.py`](src/test.py): 큐에 쌓았다가 배출하기, 샘플링, 실제 tool dispatch 위에서의 스크러빙과 sink 격리, 모델별 비용, 그리고 회귀한 빌드를 잡아내는 eval.
- [`demo.py`](src/demo.py): 모델 wrapper의 telemetry가 관찰하는 agent turn 하나, 실행 중 session 비용, 그다음 오프라인 eval.

loop와 dispatch는 바뀌지 않습니다. telemetry는 바깥에서 관찰하고, telemetry가 공급하는 eval은 hot path 밖에서 실행됩니다(섹션 23).

```bash
python sections/20-observability/src/test.py         # offline checks, no key
uv run python sections/20-observability/src/demo.py  # live demo, needs a key
```

---

## 출처

- [Claude Code analytics](https://github.com/yasasbanukaofficial/claude-code):
  `services/analytics/index.ts`(큐 + `logEvent`), `sink.ts`, `datadog.ts`, `firstPartyEventLogger.ts`, `sinkKillswitch.ts`, `shouldSampleEvent`.
- [Claude Code cost and diagnostics](https://github.com/yasasbanukaofficial/claude-code):
  `cost-tracker.ts`, `utils/modelCost.ts`, `costHook.ts`(`formatTotalCost`), `diagnosticTracking.ts`, `upstreamproxy/relay.ts`.
- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness), `dsh-v0.1.0-rc.7` 기준:
  `docs/subsystems/telemetry.md`, `docs/subsystems/token-meter.md`, `packages/llm/llm-replay/README.md`, `docs/subsystems/invariants.md`.
- [ai-agent-book](https://github.com/bojieli/ai-agent-book): `book/chapter6.md`, 중국어 원문이 기준입니다.
  span 트리, task별 상한을 둔 비선형 agent 비용, 그리고 eval 세트로 재활용되는 프로덕션 trace.
  거기서 비용 분석에는 외부 인용이 달려 있지 않으므로 단일 출처입니다.
- [OpenTelemetry tracing](https://opentelemetry.io/docs/specs/otel/trace/api/): span 자체, 그 부모 링크, 시간 측정값, 상태, 속성.
- [OpenInference](https://github.com/Arize-ai/openinference): span 위의 LLM 속성과 tool 속성에 이름을 붙이는 시맨틱 규약.
- 평가는 Claude Code 소스에 없고, 여기서는 섹션 23이 담당합니다. 홀드아웃 task 세트와 LLM-as-judge는 재구성이자 일반적인 관행으로 남습니다.
- [mini-swe-agent source](https://github.com/swe-agent/mini-swe-agent):
  `agents/default.py`의 `serialize`와 `save`, `models/__init__.py`의 `GLOBAL_MODEL_STATS`, `run/benchmarks/swebench.py`, `run/utilities/inspector.py`.
- 구성: [learn-claude-code · s20_comprehensive](https://github.com/shareAI-lab/learn-claude-code).
