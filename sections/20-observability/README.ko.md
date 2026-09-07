# 20 · Observability & evaluation

[English](README.md) · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · **한국어**

> 볼 수 없는 것은 고칠 수 없고, 아무도 기록하지 않은 실행을 평가할 수도 없습니다.

에이전트는 감독 없이 실행되며, 부작용을 일으키고 돈을 사용합니다. 모델 호출은 블랙박스입니다: 토큰을 소모하고 실제 작업을 유발합니다.

계측 없이 기본적인 질문에 답할 수 없습니다. 무엇을 했는가, 도구가 얼마나 자주 실패했는가, 이 세션에 비용이 얼마나 들었는가.

이 섹션은 기록을 담당합니다. 각 단계가 무엇을 했고 비용이 얼마였는지 기록하며, 그 기록을 충분히 깔끔하게 보관합니다.

변경이 품질을 더 좋게 만들었는지 나쁘게 만들었는지는 다른 작업입니다. 섹션 23이 그 작업을 수행하며, 이 섹션의 기록을 기반으로 작동합니다.

기록을 남기지 않으면 모든 비용 급증이 놀라움으로 다가옵니다. 모든 버그 보고서는 재현할 수 없습니다. eval 세트에는 실제로 참고할 것이 없습니다.

---

## 메커니즘

![메커니즘 다이어그램](assets/20-observability.png)

결코 루프의 제어 흐름과 닿지 않는 두 개의 분리 가능한 파이프라인.

원격 측정은 인라인으로 실행됩니다: 각 단계는 불러놓고 잊는(logger) 로거를 호출합니다.

이벤트는 싱크로 전송됩니다, 예를 들어 터미널, 파일, 또는 Datadog 같은 백엔드가 그 위치입니다.
로거는 싱크가 연결될 때까지 이벤트를 대기열에 넣고, 샘플링하며, 민감한 필드를 삭제하고, 확산합니다.

Evaluation은 자체 작업 세트(섹션 23)를 대상으로 오프라인으로 실행됩니다. 이 섹션에서 기록하는 것이 해당 작업 세트를 구성하는 것입니다.

- `emit`는 결코 블록되지 않으며, 결코 예외를 발생시키지 않으므로, 로깅 오류로 인해 루프가 정지하거나 충돌할 수 없습니다(섹션 1).
- 이벤트는 싱크가 연결될 때까지 큐에 버퍼링되며, 연결되면 배출되어 루프가 원격 측정 준비 전에 로그를 남길 수 있습니다.
- 샘플링은 비율에 따라 이벤트를 드롭하고; 스크러빙은 허용 목록에 있는 필드만 유지하므로 코드와 경로가 절대 노출되지 않습니다.
- 비용은 모델별로 USD 총액에 누적되며, 실시간 및 종료 시 표시됩니다.

### 새로운 기능: 날림(fire-and-forget) 이벤트 로깅

`telemetry.py`는 싱크가 연결될 때까지 이벤트를 큐에 저장하고, 연결되면 샘플링, 스크러빙, 그리고 분산(fan out)합니다. `emit`는 절대 발생하지 않습니다:

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

- 싱크가 연결되기 전, 이벤트는 `_queue`에 버퍼링되며; `attach`는 동일한 `_deliver` 경로를 통해 이를 배출하므로, 큐에 저장된 이벤트도 샘플링되고 스크러빙됩니다.
- `scrub`는 `SAFE_FIELDS`만 보유하므로, 안전하지 않은 값(코드, 파일 경로, 프롬프트 등)은 절대 백엔드에 도달하지 않습니다.
- 예외를 발생시키는 싱크는 무시되므로, 하나의 손상된 백엔드가 루프를 멈추거나 충돌시키지 않습니다.

### 새 기능: 모델별 비용 및 오프라인 eval

비용은 모델별로 누적되어 하나의 실행 중인 USD 총액으로 합산됩니다:

```python
def add(self, model, input_tokens, output_tokens):    # src/telemetry.py
    i, o = self.by_model.get(model, (0, 0))
    self.by_model[model] = (i + input_tokens, o + output_tokens)
    pi, po = PRICES.get(model, (0.0, 0.0))             # modelCost.ts pricing tiers
    self.cost_usd += input_tokens * pi + output_tokens * po
    return self.cost_usd
```

- `add`는 토큰별 가격을 조회하고 지출을 `cost_usd`에 합산하며, 이 금액은 실시간과 종료 시 표시됩니다.
- 해당 총액은 세션을 커버하며, 어떤 작업이 돈을 사용했는지는 표시하지 않습니다.

`run_eval`는 여기에서 가장 작은 eval입니다. 이는 후보 빌드에 대해 고정된 작업 세트를 재실행하고 통과 횟수를 세어 비율을 반환합니다.
섹션 23은 동일한 진입점에서 환경, 시뮬레이션된 사용자, 반복 실행을 설정합니다. 또한 해당 비율의 작은 감소가 일반적으로 잡음인 이유를 설명합니다.

### 통합 방식

데모는 모델 래퍼에서 원격 측정을 사용합니다. 루프는 변경되지 않습니다:

```python
def model(messages, registry, system):
    r = client.messages.create(...)
    cost.add(MODEL, r.usage.input_tokens, r.usage.output_tokens)   # cost rollup
    tel.emit("model_call", model=MODEL, tokens=..., cost_usd=...)  # scrubbed event
    return r
run_turn([...goal...], lambda m, r, s: model(m, r, SYSTEM), reg, Session(mode=DEFAULT))   # the one agent call
```

- 원격 측정은 외부에서 관찰합니다: 래퍼는 이벤트를 발생시키고 비용을 추적하므로 `run_turn` 및 디스패치는 섹션 13과 바이트 단위로 동일하게 유지됩니다.
- 싱크는 각 이벤트를 출력합니다; 세션 비용은 마지막에 출력됩니다; 그런 다음 오프라인 `run_eval`이 고정된 작업 세트를 평가합니다.
- 상류의 모든 것은 변경되지 않습니다. Observability는 부가 관찰자이며 루프의 새로운 단계가 아닙니다.

### 추가 읽기

이 내용은 `src/`에 포함되지 않습니다. 이는 ai-agent-book과 두 가지 추적 표준에서 가져온 것이며 표의 시스템에서 확인된 것은 아닙니다.

**스팬, 평면 이벤트가 아님.** 스팬은 런(run) 안의 하나의 작업 조각입니다: 모델 호출, 도구 호출, 검색 등입니다. 트레이스(trace)는 전체 런을 의미합니다.
모든 스팬은 다음을 기록합니다:

- 시작 시간과 소요 시간,
- 성공 여부,
- 어떤 스팬이 부모 스팬인지,
- 작업을 설명하는 자유 형식 속성들.

부모 링크가 중요합니다. 이것은 런의 스팬들을 트리 구조로 변환하여, 트리를 위에서 읽으면 어떤 단계에서 실패했는지,
어떤 단계가 느렸는지, 각 가지(branch)의 비용이 얼마인지 알 수 있습니다.

평면 이벤트로는 이를 할 수 없습니다. 이벤트는 호출이 발생했다고만 말할 뿐, 그 호출이 어떤 단계에 속했는지는 알 수 없습니다.
한 명의 사용자 요청이 여러 모델 호출, 도구 호출, 검색으로 바뀔 수 있으며, 일부는 다른 것 안에 중첩되고 일부는 동시에 실행됩니다.
이를 타임스탬프별로 정리하는 것은 추측에 불과합니다.

두 가지 표준이 스팬의 형식을 고정하므로, 백엔드를 추측할 필요가 없습니다:

- OpenTelemetry는 스팬 자체를 정의합니다: 트레이스 ID, 부모 ID, 타이밍, 상태, 속성.
- OpenInference는 그 위에서 LLM 작업을 명명합니다: 프롬프트, 완료, 모델, 토큰 수, 도구 호출.

한 번만 이름에 맞춰 계측을 작성하면, 백엔드를 전환하는 것은 재작성(rewrite)이 아니라 설정(config) 변경이 됩니다.

내보내기는 `emit`과 같은 규칙을 따릅니다: 주요 경로(hot path)에는 영향을 주지 않습니다. 스팬은 큐에 들어가고 백그라운드 작업자가 이를 배치(batch)로 전송합니다.
그래서 느린 수집기는 실행에 비용이 들지 않습니다. 이 섹션의 `emit`는 이 모든 것의 평면 버전입니다.
같은 이벤트에 추적 ID와 부모 ID를 추가하면 트리가 존재합니다.

**비선형 비용 및 태스크별 상한.** 비용은 모델이 읽는 토큰 수를 추적하며, 매 턴 전체 대화를 다시 보냅니다.
따라서 두 번째 턴에 돌아온 tool result는 세 번째, 네 번째, 다섯 번째 턴에도 다시 비용이 청구됩니다.
맥락에 추가된 모든 것은 그 이후의 모든 턴에서 비용이 청구되며, 총 비용은 턴 수보다 더 빨리 증가합니다.
단순한 단계 수만으로는 이를 예측할 수 없습니다.

두 가지 harness 기능이 비용의 일부를 절약하며, 그 절약액은 합산되지 않습니다:

- Prompt caching(섹션 10)는 동일하게 유지된 접두사를 할인합니다.
- 압축(섹션 8)은 컨텍스트에서 오래된 턴을 제거합니다.

이들은 겹칩니다: 압축은 캐싱이 무시했을 같은 토큰을 제거합니다.

한 세션 전체가 이 모든 것을 숨깁니다, 왜냐하면 어떤 작업이 돈을 썼는지 절대 말하지 않기 때문입니다.
그래서 작업별 비용을 추적하고, 각 작업에 한도를 줍니다. 한도는 완료되지 않을 루프를 멈추는 단계 제한처럼 실행을 멈춥니다(섹션 1).
책이 여기 유일한 출처이며 외부 인용은 없으므로, 이 비용 모델을 한 저자의 현장 보고로 읽으세요.

**트레이스는 eval 세트를 제공합니다.** 두 파이프라인은 한 방향으로 만납니다: 생산 트레이스가 eval 작업이 됩니다. 세 단계가 하나를 다른 것으로 바꿉니다.

- **선택.** 배울 가치가 있는 실행만 유지하세요: 오류가 발생한 실행, 사용자가 다시 시도하거나 수정한 실행, 나머지보다 훨씬 비용이 많이 든 실행.
  정상적으로 진행된 실행은 아무 것도 추가하지 않습니다.
- **정리.** 코드와 경로를 백엔드에서 제외시키는 허용 목록은 작업 파일에서도 이를 제외합니다.
- **재구성.** 추적에는 시작 상태와 모든 도구 호출이 포함되어 있어, 작업의 설정과 실행이 달성해야 할 결과를 제공합니다.

이 과정을 지속적으로 수행하면 eval 세트는 사용자가 실제로 수행하는 것을 따릅니다. 섹션 23은 그곳에 들어온 모든 것을 평가합니다.

---

## 시스템별

각 에이전트가 텔레메트리를 방출하고 비용을 추적하며 eval 세트에 공급하는 방법.

| | Claude Code | mini-swe-agent | deepseek-harness |
| --- | --- | --- | --- |
| **장점** | 풍부한 생산 가시성, 저렴하고 안전함. | 실행이 중단되더라도 파일이 남음. | 추가 계측 필요 없음: 모델이 보는 것이 기록됨. |
| **단점** | 무슨 일이 일어났는지 말해주지만, 답이 좋은지는 알 수 없음. | 생산 관련 원격 측정 거의 없음. | 검열 규칙 제공 안 함. 전달 중 손실되거나 반복될 수 있음. |
| **이유** | 루프를 건드리지 않고 프로덕션을 감시해야 함. | 벤치마크는 오프라인 채점, 따라서 전체 기록이 중요함. | 세션 로그가 이미 기록이므로 내보냄. |
| **방법: 원격 측정** | 이벤트가 싱크로 큐잉되고, 샘플링과 정리가 이루어짐. | 실행당 하나의 궤적 파일, 각 단계마다 저장됨. | 세션 이벤트는 검열 과정을 통해 외부로 복사됨. |
| **방법: 비용 추적** | 모델별 토큰이 세션 총액에 가격 책정됩니다. | 호출당 가격은 실행 및 전역 총액에 포함됩니다. | 재생 패스는 로그를 달러가 아닌 토큰으로 가격 책정합니다. |
| **방법: eval 피드** | 소스에 없음; 스크럽된 트레이스는 회귀 케이스가 됩니다. | 저장된 궤적은 벤치마크 실행기에 공급됩니다. | 기록된 실행은 키 없이 고정 자료로 재생됩니다. |

---

## 실패 모드

- **핫 경로에서의 텔레메트리.** 방해하거나 예외를 발생시키는 로깅 호출은 루프를 정지시킵니다(섹션 1). 네트워크를 기다리는 스팬 익스포터도 마찬가지입니다.
  완화: 사전 싱크 큐, 싱크별 킬스위치, 백그라운드 작업자의 배치 익스포트를 활용한 화재-망각 방식.
- **민감한 데이터가 로그로 유출됨.** 코드, 파일 경로, 또는 프롬프트가 일반 접근 백엔드에 도달하거나, 트레이스에서 생성된 작업 파일에 포함됩니다.
  완화: 허용 목록에 있는 기록 가능한 필드만 허용하고, 나머지는 팬아웃이나 저장 전에 정리합니다.
- **부모 링크가 없는 평면 스트림입니다.** 어떤 모델 호출이 어떤 단계에 속했는지 나타내지 않으므로, 실패한 실행은 타임스탬프를 기준으로 재구성해야 합니다.
  완화: 모든 이벤트에 추적 ID와 부모 스팬 ID를 추가하고, 백엔드가 이미 이해하는 명명 규칙을 따릅니다.
- **비용 편차가 눈에 띄지 않습니다.** 모델 교체나 무한 루프가 지출을 크게 증가시키고, 하나의 세션 총액은 단일 작업이 사용한 비용을 숨깁니다.
  완화: 모델별 및 작업별 합계를 실시간과 종료 시에 표시하고, 작업별 한도와 루프 단계 상한을 설정합니다(섹션 1).
- **eval 세트가 운영 환경과 달라집니다.** 오프라인 작업은 실제 사용을 놓치므로, 테스트 스위트는 통과하지만 사용자는 실패합니다(섹션 23).
  완화: 실패하고 비용이 많이 드는 실행의 흔적을 태스크 세트로 스크러빙하여 필터링 상태로 유지합니다.

---

## 실행 가능

[`src/`](src/)는 19개를 전달하고 다음을 추가합니다:

- [`telemetry.py`](src/telemetry.py): 이벤트 로거(`Telemetry.emit`, 큐 및 드레인, `sample`, `scrub`), 모델별 `CostTracker`, 그리고 오프라인 `run_eval`.
- [`test.py`](src/test.py): 큐-그다음-드레인, 샘플링, 스크럽 및 싱크 격리 실 도구 디스패치, 모델별 비용, 그리고 회귀된 빌드를 잡는 eval.
- [`demo.py`](src/demo.py): 모델 래퍼에서 텔레메트리로 관찰된 한 에이전트 턴, 라이브 세션 비용, 그리고 오프라인 eval.

루프와 디스패치는 변경되지 않습니다. 원격 관측(Telemetry)은 외부에서 관찰하며, 그것이 공급하는 eval은 핫 경로(섹션 23)에서 작동합니다.

```bash
python sections/20-observability/src/test.py         # offline checks, no key
uv run python sections/20-observability/src/demo.py  # live demo, needs a key
```

---

## 출처

- [Claude Code 분석](https://github.com/yasasbanukaofficial/claude-code):
  `services/analytics/index.ts` (큐 + `logEvent`), `sink.ts`, `datadog.ts`, `firstPartyEventLogger.ts`, `sinkKillswitch.ts`, `shouldSampleEvent`.
- [Claude Code 비용 및 진단](https://github.com/yasasbanukaofficial/claude-code):
  `cost-tracker.ts`, `utils/modelCost.ts`, `costHook.ts` (`formatTotalCost`), `diagnosticTracking.ts`, `upstreamproxy/relay.ts`.
- [deepseek-harness 소스](https://github.com/deepseek-ai/deepseek-harness) 위치 `dsh-v0.1.0-rc.7`:
  `docs/subsystems/telemetry.md`, `docs/subsystems/token-meter.md`, `packages/llm/llm-replay/README.md`, `docs/subsystems/invariants.md`.
- [ai-agent-book](https://github.com/bojieli/ai-agent-book): `book/chapter6.md`, 중국어 원본 정본.
  스팬 트리, 작업별 한도가 있는 비선형 에이전트 비용, 그리고 생산 추적이 eval 세트로 재활용되었습니다.
  비용 분석에는 외부 인용이 없으므로 단일 출처입니다.
- [OpenTelemetry 추적](https://opentelemetry.io/docs/specs/otel/trace/api/): 스팬 자체, 부모 링크, 시간, 상태 및 속성.
- [OpenInference](https://github.com/Arize-ai/openinference): LLM을 명명하는 의미 체계 규칙 및 스팬의 도구 속성.
- Evaluation은 Claude Code 소스에 존재하지 않으며, 섹션 23에서 여기서 관리합니다. 보류된 작업 세트와 LLM-as-judge는 재구성과 일반 관행으로 남아 있습니다.
- [mini-swe-agent 소스](https://github.com/swe-agent/mini-swe-agent):
  `serialize` 및 `save`은 `agents/default.py`에 있으며, `GLOBAL_MODEL_STATS`은 `models/__init__.py`에 있고, `run/benchmarks/swebench.py`, `run/utilities/inspector.py`.
- 프레이밍: [learn-claude-code · s20_comprehensive](https://github.com/shareAI-lab/learn-claude-code).
