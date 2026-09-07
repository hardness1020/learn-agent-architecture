# 22 · Graph engineering

[English](README.md) · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · **한국어**

> 다음에 무엇을 실행할지 모델에게 묻는 일을 멈춥니다. 이미 알고 있는 경로를 코드로 적고, 판단이 필요한 곳에만 모델을 씁니다.

섹션 21은 agent 하나를 둘러싸고 loop를 쌓았습니다. 이 섹션은 모델 호출 사이의 작업에 모양을 부여합니다.

많은 task에는 모델을 호출하기 전에 이미 알고 있는 구조가 있습니다: 움직이기 전에 티켓을 분류하고, 커밋하기 전에 diff를 검토하고, 외부로 나가는 일 전에는 승인을 받습니다.
평범한 agent loop는 다음에 무엇을 할지 모델에게 물어보며 그 구조를 실행마다 다시 발견합니다. 모델이 주도하는 라우팅은 느리고, token을 쓰고, 실행마다 달라집니다.

graph engineering은 이미 아는 구조를 코드 안의 방향 그래프로 적습니다:

1. 노드가 일을 합니다. 노드는 평범한 코드일 수도, 모델 호출 하나일 수도, agent 실행 전체일 수도 있습니다.
2. 엣지가 다음 노드를 고릅니다. harness는 모델 호출이 아니라 코드로 엣지를 평가합니다.
3. 순환이 허용됩니다. 재시도, 검토 후 수정, 사람의 일시 정지에는 모두 뒤로 가는 경로가 필요합니다.
4. 상태는 그래프를 따라 움직이는 레코드 하나입니다. 각 노드가 그것을 읽고 자기 갱신분을 씁니다.

구조는 코드가 쥡니다. 판단은 모델이 댑니다. loop(섹션 21)는 그런 그래프 중 가장 작은 것입니다: 노드 둘과 뒤로 가는 엣지 하나. 이 섹션은 그것을 일반화합니다.

---

## 메커니즘

![Mechanism diagram](assets/22-graph-engineering.png)

단순한 버전은 세 조각으로 되어 있습니다: 각 노드 이름을 함수에 대응시키는 dict, 각 노드를 다음에 실행될 것에 대응시키는 dict, 그리고 모든 노드가 읽고 쓰는 상태 dict 하나.

```python
def run_graph(nodes, edges, state, start, budget=20):  # src/graph.py
    state = dict(state)
    trace = []
    node = start
    for _ in range(budget):                        # the ceiling: harness-enforced
        state.update(nodes[node](state) or {})     # a node returns only its updates
        trace.append(node)
        step = edges.get(node, END)
        node = step(state) if callable(step) else step   # a coded edge: no model call
        if node == END:
            return {"ok": True, "state": state, "trace": trace}
    return {"ok": False, "state": state, "trace": trace}   # budget spent: escalate
```

- `nodes`는 dispatch 맵입니다(섹션 2). 노드는 상태를 읽고 자기가 바꾼 키만 돌려줍니다.
- 엣지는 고정된 이름(결정적)이거나 상태를 받는 호출 가능 객체(조건부)입니다. 어느 쪽이든 harness가 코드로 평가합니다. 라우팅에는 token이 들지 않습니다.
- 엣지가 없는 노드는 그래프를 끝냅니다. 예산은 섹션 21의 상한입니다: 순환은 그 횟수에서 멈추고 `ok: False`로 에스컬레이션합니다.
- `trace`는 어떤 노드가 어떤 순서로 실행되었는지 기록합니다. 이것이 섹션 20을 위한 실행 기록입니다.

### 노드: 결정성에서 자율성까지의 척도

각 노드는 하나의 척도 위에서 한 점을 고릅니다:

- **코드 노드.** 파싱, 검증, 고정된 API 호출. 결정적이고 token이 들지 않습니다.
- **모델 노드.** 분류기 같은 LLM 호출 하나. 범위가 제한된 판단.
- **agent 노드.** tool을 갖춘 섹션 1의 loop 전체. 고정된 자리 안에서 이루어지는 열린 판단.

`agent_node`는 안쪽 loop를 노드로 장착합니다. 방문할 때마다 상태에서 만든 새 `messages[]` 위에서 `run_turn`을 실행하므로,
노드는 실행 전체가 아니라 자기 prompt 빌더가 넘겨준 것만 봅니다.

이 척도가 곧 예산 규율입니다: 분기를 미리 알 수 있는 곳은 코드로 라우팅하고, 모델 호출은 판단이 필요한 노드 안에서만 씁니다.

### 이름 붙은 모양들

출처들이 이름 붙인 workflow 패턴은 곧 그래프의 모양입니다:

- **Prompt chaining.** 노드가 한 줄로 이어진 경로이고, 사이사이에 코드 게이트가 있습니다.
- **Routing.** 조건부 엣지 하나가 전문 노드들로 갈라집니다.
- **Parallelization.** 형제 갈래가 동시에 실행되어 한 노드에서 합쳐지며, task를 나누거나(sectioning) 같은 task를 되풀이해 투표를 받습니다(voting).
- **Orchestrator-workers.** 실행 시점에 fan-out을 정하는 노드, 그리고 합류 노드. 엣지 집합은 동적이지만 모양은 여전히 그래프입니다.
- **Evaluator-optimizer.** 워커 노드와 검사 노드에 뒤로 가는 엣지 하나. 섹션 21의 검증 loop를 부분 그래프로 만든 것입니다.

이름은 출처마다 정해져 있지 않습니다. `ai-agent-book`은 같은 내용을 "collaboration topology"와 "orchestration"으로 다루고,
"graph engineering"은 용어를 설명하는 주석에서만 언급합니다. 이 섹션은 자기 이름을 유지합니다. 여기서 설명하는 것이 코드로 쓴 그래프이기 때문입니다.
두 번째 출처를 읽을 때는 단어가 아니라 메커니즘으로 맞춰 봅니다.

### 그래프로 만들지 말아야 할 때

열린 작업은 미리 정해진 경로를 거부합니다. 깊은 조사와 어려운 디버깅에는 실행 도중에 떠오르는 계획이 필요하고, 미리 그려 둔 그래프는 해답에 필요한 경로를 막습니다.
출처들의 규칙은 이렇습니다: 어차피 강제했을 구조만 코드로 적고(움직이기 전에 분류, 커밋 전에 검토, 보내기 전에 승인),
결과가 좋아진다는 것이 보일 때에만 구조를 더합니다. 나머지는 평범한 loop를 쓰고 모델이 계획하게 둡니다.

흔한 경우는 혼합형입니다: 고정된 그래프 안의 노드 하나가 agent입니다. 그래프는 검토가 반드시 일어나게 보장하고, agent는 자기 자리 안에서 어떻게 일할지 정합니다.

### 통합 방식

이 섹션은 작은 기본 요소 하나(엣지 맵)를 추가하고 나머지는 재사용합니다:

- 노드가 하는 일은 섹션 1의 loop이고, `agent_node`는 `run_turn`을 고치지 않고 감쌉니다.
- 코드로 된 엣지는 섹션 2의 dispatch 규율을 따릅니다. 모델 출력이 아니라 맵입니다.
- 워커와 검사기를 노드로 나누는 것은 섹션 6이고, 형제 갈래는 섹션 15의 worktree에서 격리됩니다.
- 단계 예산과 에스컬레이션 계약은 섹션 21입니다.
- trace는 섹션 20의 telemetry로 들어갑니다. 어떤 엣지가 발화했는지를 보면 어떤 갈래가 죽어 있는지 알 수 있습니다.

실행 가능한 코드는 위 그림의 demo 그래프를 그대로 연결합니다:

```python
nodes = {                                          # src/demo.py
    "classify": lambda s: {"route": "math" if any(c.isdigit() for c in s["task"]) else "prose"},
    "math": agent_node(prompt, model, math_reg),   # a full agent run as one node
    "prose": agent_node(prompt, model, Registry()),
    "check": check_node,                           # section 21's checker, now a node
}
edges = {
    "classify": lambda s: s["route"],              # a coded edge: routing costs no tokens
    "math": "check",
    "prose": "check",
    "check": lambda s: END if s["verdict"]["passed"] else s["route"],   # the cycle
}
```

### 더 읽을거리

이 내용은 `src/`에 없습니다. ai-agent-book에서 온 것이며, 표에 있는 시스템들에서 확인된 내용은 아닙니다.

**페이즈 노드.** 페이즈 노드는 하나의 작업을 여러 단계로 나누어 실행하고, 모든 단계가 하나의 `messages[]`를 공유합니다.
탐색, 구현, 검토는 세 개의 별개 작업이 아니라 한 덩어리 작업의 단계입니다.
trajectory가 한 단계에서 알아낸 것을 다음 단계로 나르므로, 어떤 단계도 task를 처음부터 다시 읽지 않습니다.

**페이즈별 tool.** 각 페이즈는 자기만의 system prompt와 자기만의 tool 집합을 갖고, 페이즈가 바뀌면 harness가 둘 다 교체합니다.
히스토리는 그 자리에 그대로 있으므로, 다음 페이즈를 위해 따로 챙길 것이 없습니다. 책의 기술에 나오는 세 페이즈는 이렇습니다:

- **탐색.** 읽기와 검색.
- **구현.** 편집과 실행.
- **검토.** 읽기, 그리고 판정을 돌려주는 tool.

**게이트 tool.** 모델은 `finish_exploring` 같은 게이트 tool을 호출해서 페이즈를 떠납니다.
harness는 그 호출을 엣지로 읽고 다음 페이즈를 시작합니다. 게이트가 유일한 출구이므로, 페이즈가 언제 끝나는지는 모델이 아니라 harness가 정합니다.

**경로.** 탐색이 먼저 실행되고, 그다음 구현, 그다음 검토입니다. 검토가 실패하면 실행은 구현으로 돌아가고,
구현은 검토의 메모가 이미 trajectory에 들어 있는 상태에서 이어 갑니다. 이 섹션의 용어로는 뒤로 가는 엣지가 하나 있는 경로이고,
위의 evaluator-optimizer와 같은 모양입니다.

**무엇을 장착할지.** 갈래끼리 관련이 없으면 새 `messages[]`를 씁니다. 노드들이 한 작업의 단계라면 하나의 trajectory를 씁니다.
새 `messages[]`는 각 노드의 창을 작게 유지하고 그 갈래를 독립적으로 만듭니다.
하나의 trajectory는 앞서 알아낸 것을 전부 시야에 남기고, 경로가 길어질수록 창을 더 많이 채웁니다. 이 선택은 context 문제입니다(섹션 8).

**무엇을 multi-agent로 볼 것인가.** 책은 이 설계를 multi-agent라고 부릅니다. 페이즈마다 prompt와 tool이 바뀌기 때문입니다.
이 저장소는 그것을 prompt와 tool이 바뀌는 agent 하나라고 부릅니다.
어느 이름으로 부르든 메커니즘은 같으므로, 그 결과를 인용할 때는 어느 정의를 말하는지 밝힙니다.

**단일 출처.** 이 기술은 책 자체의 실험에 기대고 있습니다. 이를 확인해 주는 독립적인 보고는 없습니다.

---

## 시스템별

각 agent가 다음에 무엇을 실행할지 정하는 방식입니다.

| | Claude Code | Hermes Agent | mini-swe-agent |
| --- | --- | --- | --- |
| **장점** | 코드로 하는 라우팅. token도 편차도 없음. 끝난 노드는 재개할 때 재생됨. | 작성할 그래프가 없음. 구조가 task에 맞춰짐. | 그래프 전체를 한눈에 감사할 수 있음. |
| **단점** | 그래프가 실행마다 쓰는 스크립트이고, 재사용할 수 있게 선언된 그래프가 아님. | 라우팅에 token이 들고 실행마다 달라질 수 있음. | 모든 task에 모양이 하나뿐임. 어떤 갈래도 특화될 수 없음. |
| **이유** | 오케스트레이션은 프로그램임. 한 번 써 두고 결정적으로 실행함. | 어시스턴트 작업은 미리 선언하기에는 너무 열려 있음. | 선택은 모델에 두고, harness는 순환 하나로 유지함. |
| **방법: 노드** | 노드마다 subagent 하나, 출력은 스키마로 검사한 구조화 출력. | 위임된 subagent이며, 깊이와 동시 실행 수에 상한이 있음. | 둘뿐임. 모델 단계와 환경 단계. |
| **방법: 라우팅** | 단계 사이는 평범한 코드. 조건문, 반복문, fan-out. | 모델이 tool 호출로 라우팅함. 코드로 된 엣지는 없음. | 제출하거나 예산이 멈출 때까지 고정된 순환 하나. |
| **방법: 상태** | 반환값이 앞으로 이어지고, 저널이 재개용으로 노드 출력을 기록함. | 결과가 완료 큐를 통해 돌아옴. | 메시지 목록이 상태 전부임. |

---

## 실패 모드

- **라우터 역할을 하는 모델.** 라우팅을 모델에 맡기면 token을 태우고, 지연이 늘고, 실행마다 달라집니다. 맨 앞에서 한 번 잘못 라우팅하면 그 뒤가 전부 어긋납니다.
  완화: 전이는 코드로 평가하고, 모델 호출은 판단이 필요한 노드에만 남깁니다.
- **과도한 그래프화.** 탐색이 필요했던 task에 고정된 그래프를 씌우면 해답에 필요한 경로가 막힙니다.
  완화: 어차피 강제했을 구조만 코드로 적고, 열린 작업은 평범한 loop에 맡깁니다.
- **실패 엣지 없음.** FAIL을 보낼 곳이 없는 검사 노드는 나쁜 출력을 그대로 아래로 흘려보냅니다.
  완화: 모든 검사 노드에 예산이 붙은, 뒤로 가는 엣지를 줍니다(섹션 21).
- **끝없는 순환.** 상한이 없는 재시도 엣지는 영원히 돕니다. 완화: harness가 강제하는 단계 예산. 예산을 다 쓰면 에스컬레이션합니다.
- **상태 비대.** 모든 노드가 자기 출력을 통째로 공유 상태에 쏟아부어서, 뒤쪽 노드가 그 안에 묻힙니다.
  완화: 엄격한 상태 경계. 노드는 필요한 부분만 읽고 자기 갱신분만 돌려줍니다(섹션 8).
- **실행 도중 사망.** 긴 그래프가 일곱 번째 노드에서 죽고 첫 노드부터 다시 시작합니다.
  완화: 각 노드의 출력을 기록하고, 재개할 때는 그 기록에서 끝난 노드를 재생합니다(섹션 11, 12).
- **끝나지 않는 페이즈.** 모델이 게이트 tool을 끝내 호출하지 않아서, 같은 prompt와 같은 tool로 페이즈가 계속 일합니다. 그것을 멈추는 것은 예산뿐입니다.
  완화: 게이트를 유일한 출구로 만들고, 페이즈마다 자기 단계 예산을 주고, 예산을 다 쓰면 다음 페이즈로 넘기거나 에스컬레이션합니다.
- **모든 페이즈를 안고 가는 trajectory.** 하나의 trajectory는 페이즈마다 커집니다. 거기에는 현재 페이즈가 장착하지 않은 tool의 호출이 여전히 남아 있고, 모델이 그것을 다시 시도할 수 있습니다.
  완화: 페이즈 prompt에 현재 페이즈와 그 tool을 밝히고, 장착되지 않은 tool 호출은 분명한 오류로 거절하고, 끝난 페이즈는 compaction 합니다(섹션 8).

---

## 실행 방법

[`src/`](src/)는 21을 이어받고 다음을 추가합니다:

- [`graph.py`](src/graph.py): `run_graph`(노드의 dispatch 맵, 고정 엣지와 조건 엣지, 이어지는 상태, 단계 예산)와 안쪽 loop를 노드로 장착한 `agent_node`.
- [`test.py`](src/test.py): 체인 순서와 상태 병합, 코드만으로 하는 라우팅, 예산에서 멈추는 순환, agent 노드를 방문할 때마다 새로 만드는 `messages[]`에 대한 오프라인 검사.
- [`demo.py`](src/demo.py): 라우팅되는 실행 하나. 코드 노드가 분류하고, 코드로 된 엣지가 라우팅하고, agent 노드가 답하고,
  섹션 21의 검사기가 채점하고, 실패한 판정은 피드백과 함께 되돌아갑니다.

loop는 바뀌지 않습니다. 그것이 언제 실행될지는 그래프가 정합니다.

```bash
python sections/22-graph-engineering/src/test.py         # offline checks, no key
uv run python sections/22-graph-engineering/src/demo.py  # live demo, needs a key
```

---

## 출처

- [LangChain · 3 years of graph engineering](https://www.langchain.com/blog/3-years-of-graph-engineering-with-langgraph): 노드, 엣지, 순환, 노드로 쓰는 agent, 그래프로 만들지 말아야 할 때.
- [Anthropic · Building effective agents](https://www.anthropic.com/engineering/building-effective-agents): workflow와 agent의 대비, 그리고 다섯 가지 workflow 모양.
- [Google · Why we built ADK 2.0](https://developers.googleblog.com/en/why-we-built-adk-20/): 코드로 하는 라우팅, 노드 사이의 context 격리, workflow 노드에 놓인 agent.
- [Claude Code](https://code.claude.com/docs): `Workflow` 스크립트 계약(파이프라인, 병렬 fan-out, 구조화 출력, 재개).
  소스 백업이 아니라 tool 스키마와 문서화된 동작에서 가져왔습니다.
- [Hermes Agent source](https://github.com/NousResearch/hermes-agent): `tools/delegate_tool.py`, `tools/async_delegation.py`, `batch_runner.py`.
- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness), `dsh-v0.1.0-rc.7` 기준:
  `docs/subsystems/workflow.md`, `packages/workflow/tool-workflow/README.md`: 실행마다 모델이 쓰는 스크립트이고, 지속되는 그래프는 없습니다.
- [mini-swe-agent source](https://github.com/swe-agent/mini-swe-agent): `agents/default.py`와 `run/benchmarks/swebench.py`의 실행 loop와 예산.
- [ai-agent-book · chapter 10](https://github.com/bojieli/ai-agent-book/blob/main/book/chapter10.md) (《深入理解 AI Agent》, 李博杰, 多 Agent 协作. 중국어 원문이 기준):
  하나의 trajectory 위에서 이루어지는 다단계 역할 전환. 페이즈마다 system prompt와 tool 집합, tool 호출로 만드는 페이즈 게이트, 그리고 구현으로 되돌아가는 검토 라우팅.
  근거는 책 자체의 실험뿐입니다. 같은 장은 "collaboration topology"와 "orchestration"을 주된 용어로 유지합니다.
