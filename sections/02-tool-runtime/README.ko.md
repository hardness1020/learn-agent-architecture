# 2 · Tool runtime

[English](README.md) · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · **한국어**

> 기능을 더하는 일은 tool을 등록하는 일입니다. loop는 그대로입니다.

agent loop는 tool을 통해서만 행동할 수 있습니다. 모델은 `name`과 `input`을 담은 구조화된 `tool_use` 블록을 내보냅니다.

harness는 그 이름을 코드에 연결합니다. 입력을 검증하고, handler를 실행하고, 결과를 반환합니다.

runtime은 다음을 해야 합니다.

1. 어떤 tool이 있는지 모델에 알립니다.
2. 각 tool의 입력 schema를 설명합니다.
3. 각 `tool_use`를 이름으로 라우팅합니다.
4. 가능한 경우 안전한 호출을 병렬로 실행합니다.
5. tool 목록이 커져도 찾을 수 있게 유지합니다.

이 계층이 없으면 모델은 행동하겠다고 요청할 수는 있어도 그 행동을 실행할 주체가 없습니다.

`bash` tool 하나만 있으면 모든 기능이 문자열 처리로 바뀝니다. tool별 검증도 permission 로직도 없습니다.

여기서 시작되지만 모델 탓으로 돌아가는 실패가 둘 있습니다. 설명 두 개가 겹쳐서 모델이 엉뚱한 tool을 고릅니다. harness가 입력을 고쳐 써서 편집이 실패합니다.

---

## 메커니즘

![Mechanism diagram](assets/02-tool-runtime.png)

tool은 이름, handler, schema, 술어 몇 개를 가진 작은 객체입니다. registry는 tool을 이름으로 저장합니다. dispatch는 조회입니다.

### 새로 더하는 것: tool runtime

```python
@dataclass
class Tool:                                  # src/tools.py
    name: str
    run: Callable[[dict], Any]
    description: str = ""                      # advertised to the model
    input_schema: dict = ...                   # the Anthropic schema it accepts
    is_read_only: bool = False
    is_concurrency_safe: bool = False         # may batch in parallel
    is_edit: bool = False                     # read by the gate (section 3)

class Registry:                              # src/tools.py
    def register(self, tool): self._tools[tool.name] = tool   # add a handler
    def get(self, name):      return self._tools.get(name)    # dispatch = lookup
    def schemas(self):        ...             # the tools list handed to the model
```

- tool은 dataclass입니다.
- registry는 `name -> tool` 대응입니다.
- 기능을 더하는 일은 handler 하나를 등록하는 일입니다.
- `schemas()`는 모델에 알릴 tool 목록을 반환합니다.
- `run_concurrently`는 `is_concurrency_safe`로 표시된 tool을 묶어 실행합니다.
- 안전하지 않은 호출은 순서를 지키므로 쓰기끼리 경합하지 않습니다.

### 통합 방식

섹션 1은 코드에 박아 넣은 `HANDLERS` dict를 썼습니다. 섹션 2는 loop에 `registry`를 넘기고 각 `tool_use`를 `_dispatch`로 라우팅합니다.

```python
def run_turn(messages, model, registry, max_steps=10): # src/loop.py (now takes a registry)
    ...
    results = [_dispatch(b, registry)                   # was: run_tool(call)
               for b in response.content if b.type == "tool_use"]
    messages.append({"role": "user", "content": results})

def _dispatch(block, registry):              # resolve, run, wrap as a tool_result
    tool = registry.get(block.name)           # name -> tool
    content = run_tool(tool, block.input)
    return {"type": "tool_result", "tool_use_id": block.id, "content": content}
```

그 밖에 loop 본문은 그대로입니다. dispatch 단계만 이제 registry를 씁니다.

`_dispatch`가 다음 확장 지점입니다. 섹션 3은 거기에 permission gate를 더합니다. 섹션 4는 거기에 hook을 더합니다.

데모는 이해를 돕기 위해 순차적으로 dispatch합니다. 실제 runtime은 안전한 호출을 묶어 실행하고 큰 tool schema는 필요할 때 로드합니다.

### 더 읽을거리

이 내용은 `src/`에 없습니다. ai-agent-book과 tool 사용에 관해 공개된 연구에서 온 것이며, 표에 있는 시스템들에서 확인된 내용은 아닙니다.
Claude Code가 이름으로 나오는 대목의 대비는 Claude Code 자체 소스에서 온 것입니다.

**분류.** tool은 호출이 어디로 가고 무엇을 건드리는지에 따라 다섯 갈래로 나뉩니다.

- **인지**는 바깥 세계를 읽습니다.
- **실행**은 바깥 세계를 바꿉니다.
- **협업**은 다른 agent에 닿습니다.
- **이벤트 트리거**는 바깥 세계가 agent를 깨울 수 있게 합니다.
- **사용자 소통**은 사람에게 닿습니다.

다섯 갈래 가운데 넷은 뒤에서 각자의 섹션을 갖습니다. 섹션 6, 12, 16이 협업을 만듭니다. 섹션 13과 14가 이벤트 트리거를 만듭니다.
섹션 19가 사용자 channel을 만듭니다. 이 섹션은 다섯 갈래가 모두 올라앉는 계층을 만듭니다.
이 분류가 값을 하는 이유는 계약이 서로 다르기 때문인데, 인지 호출은 반복해도 묶어 실행해도 안전하지만 실행 호출은 그렇지 않습니다.

**세분화 정도.** agent가 PDF, Word 파일, 스프레드시트를 읽어야 한다고 해 봅니다. tool 하나일까요, 셋일까요?

같은 종류의 입력에 같은 일을 한다면 합칩니다. 타입 파라미터를 받는 `read_document` 하나가 거의 똑같은 reader 셋보다 고르기 쉽습니다.
파라미터가 더는 겹치지 않으면 나눕니다. 서로 무관한 필드를 합집합으로 묶은 schema는 어떤 필드가 해당되는지 말해 주지 못하므로, 모델이 엉뚱한 필드를 채웁니다.

**description 작성법.** `description`은 모델이 tool을 고르기 전에 읽는 유일한 텍스트입니다. 사람을 위한 문서가 아닙니다.

쓸모 있는 description은 다섯 가지를 담습니다.

- 이 tool을 언제 쓰는지.
- 언제 쓰지 않는지.
- 각 파라미터의 실제 값.
- 돌아오는 결과의 형태.
- 호출 한 번의 비용.

풀어 쓴 예시 몇 개가 산문 한 문단을 더 붙이는 것보다 도움이 됩니다. 책은 예시를 넣어서 큰 개선을 얻었다고 보고합니다.
그 수치에는 출처가 없으므로, 크기가 아니라 방향만 받아들입니다.

**파라미터 원본 보존.** 모델이 검색 텍스트에 둥근 따옴표가 들어간 편집을 보냅니다. handler로 가는 길에 harness가 그것을 곧은 따옴표로 바꿉니다.

이제 편집은 빗나가고, 모델은 문자열이 일치하지 않았다는 사실만 봅니다. 모델은 옳은 입력을 보냈으니 transcript 어디에도 그 차이를 설명하는 것이 없습니다.
그래서 규칙이 따라 나옵니다: 입력은 손대지 않은 채 handler에 넘깁니다. 잘못된 입력은 거부하고 이유를 밝힙니다. 절대 고쳐 쓰지 않고, 모델이 쓰지 않은 인자를 넣지도 않습니다.

**체크리스트 파라미터.** 환불 tool이 handler는 쓰지도 않는 `expected_price`를 받습니다.

적어 내게 하는 것 자체가 목적입니다. 모델은 호출이 실행되기 전에 자신이 믿는 가격을 밝혀야 합니다.
handler는 저장된 가격을 읽어 그것으로 판단하고, 둘이 다르면 두 값을 log에 남깁니다.
그래서 마지막 검사는 모델이 위조할 수 없는 데이터 위에 섭니다. τ-bench도 같은 방식으로 실행을 채점하는데, agent가 했다고 말한 내용이 아니라 최종 데이터베이스 상태를 읽습니다.

**인지 인터페이스.** 큰 저장소를 검색해 4000줄이 걸리는데, context에는 앞의 50줄만 들어갑니다.

결과를 정직하게 지키는 규칙이 셋 있습니다.

- 검색은 후보 한 페이지와 cursor를 함께 반환합니다.
- 읽기는 offset과 limit을 받아서, 모델이 긴 파일을 걸어 다닐 수 있게 합니다.
- 잘라 냈다는 사실은 결과에 표시합니다.

조용히 자르는 편이 오류보다 나쁩니다. 모델은 일부만 있는 파일을 전체인 양 읽고, 이후의 모든 단계가 그 빈틈을 물려받습니다.

코드 검색이 그 선택을 잘 보여 줍니다. 접근 방식은 넷이고, 하나만 쓰는 시스템은 없습니다.

| 접근 방식 | 찾는 것 | 비용 |
| --- | --- | --- |
| **Glob** | 경로 패턴으로 파일. | 내용에 대해서는 아무것도 알려 주지 않음. |
| **Grep** | 정확한 문자열과 정규식, 줄 번호까지. | 질의를 좁히려면 여러 번 호출. 동의어는 놓침. |
| **임베딩 인덱스** | 의미로 코드를 찾아, 평범한 말로 쓴 질의도 걸림. | 만들고 계속 맞춰 두어야 하는 인덱스. 순위 근거가 불투명함. |
| **LSP 심볼** | 정의, 참조, 타입을 정확하게. | 언어마다 language server 하나씩. |

Claude Code와 Cursor는 그 표의 양 끝에 있습니다. Claude Code는 인덱스를 함께 내놓지 않고 단계별로 검색하는데, glob 다음 grep,
그다음 read로 가면서 호출 사이에 모델이 질의를 좁힙니다. 책은 Cursor가 그 대신 비용을 들여 인덱스를 만든다고 설명하며,
그래서 평범한 말로 쓴 질의로도 식별자를 하나도 대지 않은 코드를 찾을 수 있습니다.

편집도 똑같이 갈립니다. 무엇이 바뀌었는지 말하는 방법이 다섯입니다.

| 방식 | 모델이 내보내는 것 | 절충점 |
| --- | --- | --- |
| **diff와 적용 모델** | 거친 뼈대 diff, 두 번째 학습된 모델이 다시 씀. | 빠르고 너그러움. 그 두 번째 모델이 필요함. |
| **옛 문자열과 새 문자열** | 찾을 정확한 텍스트와 그 자리에 넣을 텍스트. | 모호함이 없고 실패하면 요란하게 알림. 먼저 새로 읽어야 함. |
| **줄 번호** | 범위와 그 대체 내용. | 간결함. 앞선 편집이 파일을 밀면 곧 낡음. |
| **에디터 명령** | vim 스타일의 작은 명령 언어. | 짧음. 틀리기 쉬운 문법이 하나 늘어남. |
| **앵커** | 시작 표시와 끝 표시. | 위치가 밀려도 견딤. 표시가 반복되면 모호함. |

같은 두 시스템이 편집에서도 다시 갈립니다. Claude Code는 정확한 옛 문자열을 치환하고 모델이 파일을 먼저 읽게 하므로,
낡은 문자열은 엉뚱한 줄을 고치는 대신 요란하게 실패합니다. 책은 Cursor가 그 대신 거친 뼈대를 보내고
두 번째 학습된 모델이 그것으로 파일을 다시 쓴다고 설명하며, 그 경로가 더 빠르다고 보고합니다.

**이른 시작과 연쇄 중단.** 호출 하나가 배치의 나머지를 기다릴 필요는 없습니다. 자기 인자의 파싱이 끝나는 순간 시작할 수 있습니다.

모델은 아직 뒤쪽 호출을 쓰고 있으므로, 먼저 시작한 호출의 지연은 생성 시간 안에 숨습니다. 그만큼 속도를 벌고, 실패에 대비한 규칙이 하나 필요합니다.
오류는 그 호출에 의존하던 호출들을 멈춥니다. 같은 배치 안의 독립적인 호출은 계속 실행되고, 부모 turn도 계속됩니다.

**shell 상태.** 어떤 호출이 `cd build`를 실행한 뒤 가상 환경을 활성화합니다. 다음 호출은 그 둘 중 하나라도 여전히 볼까요? 설계는 둘이고, 둘 다 근거가 있습니다.

- **호출마다 초기화.** Claude Code의 bash tool은 호출 사이에 살아 있는 shell을 두지 않습니다. 한 호출에서 설정한 변수와 shell 함수는 다음 호출에서 사라지고,
  tool description은 모델에 절대 경로를 쓰라고 알려 줍니다. 각 호출은 혼자서 재현되고, 병렬 호출끼리 서로 새어 들어갈 수 없습니다.
- **지속되는 session 하나.** 책은 공유 터미널을 기본으로 삼아서 `cd`, export한 변수, 활성화된 가상 환경이 모두 살아남습니다.
  병렬 작업을 위한 별도의 shell도 계속 쓸 수 있습니다. 모델은 준비 명령을 덜 반복하고, harness는 추적하고 초기화해야 할 session 상태를 떠안습니다.

**규모가 커졌을 때의 탐색.** 연결된 서버 스무 대가 tool 수백 개를 제공하는데, 그 전체 schema는 prompt에 들어가지 않습니다.

그래서 registry는 이름을 먼저 보내고, 누가 요청할 때만 전체 schema를 로드합니다. 그 요청은 모델이 평범한 말로 보낼 수도 있습니다.
MCP-Zero는 agent가 어떤 기능이 없는지 말하게 하고, 그것을 서버에 맞추고, 다시 그 서버의 tool에 맞춘 다음, 맞은 schema만 주입합니다.
모델은 그 tool이 있다는 사실을 알 필요조차 없었는데, 이것이 키워드 검색으로는 안 되는 일입니다.

**캐시를 깨지 않는 로딩.** 로드한 schema가 context의 어디에 놓이는지가 그 비용을 결정합니다.

끝에 한 번 덧붙이고 그대로 둡니다. prompt 앞쪽의 tool 블록을 고치면 캐시된 접두부와 그 뒤의 모든 token이 무효가 됩니다(섹션 10).
덧붙이기는 접두부를 건드리지 않고, 그 schema는 다음 turn에서 평범한 기록이 됩니다.

---

## 시스템별

각 agent가 tool을 어떻게 정의하고, 호출을 어떻게 라우팅하고, 병렬성을 어떻게 다루고, 큰 목록을 어떻게 드러내는지 봅니다.

| | Claude Code | mini-swe-agent | deepseek-harness |
| --- | --- | --- | --- |
| **장점** | tool별 검증, permission, 병렬성, 지연 탐색. | `bash` tool 하나, 작은 runtime, 목록 없음. | agent마다 다른 tool 집합. 호출마다 감사되는 파이프라인 하나. |
| **단점** | 모든 tool이 계약을 짊어져야 함. | tool별 검증도 permission도 없음. gate는 명령 문자열 하나만 봄. | 단순한 tool도 출력 계약을 선언해야 함. |
| **이유** | 기능마다 새 tool 하나. loop는 제자리에 둠. | 모든 행동이 shell 명령이니 tool 하나면 충분함. | 범위별 resolver 하나가 조회, dispatch, 표시를 함께 먹임. |
| **방법: tool 정의** | schema, handler, 술어. | `bash` schema 하나, 명령 필드 하나. 다른 이름은 오류. | schema, 타입이 붙은 출력 계약, 본문, 순수 presenter. |
| **방법: dispatch** | permission으로 걸러진 풀 위에서 별칭 조회, MCP 포함. | registry 없음. 모든 호출이 shell 명령. | 범위별 조회 뒤 다섯 단계 보호 파이프라인. |
| **방법: 병렬 호출** | 안전한 호출은 묶고, 안전하지 않은 호출은 단독 실행. 플래그는 기본 꺼짐. | 아니요. 텍스트 모드는 응답마다 행동 하나. | 호출마다 분류하고, 판단이 안 되면 배타 실행으로 닫음. |
| **방법: 탐색** | 이름 먼저. 전체 schema는 이름이나 키워드로 요청할 때 로드. | tool이 하나라 필요 없음. | 지연 로딩 없음. 제한과 preset이 각 범위를 정함. |

---

## 실패 모드

- **없는 tool 이름.** 모델이 없거나 꺼진 tool을 부릅니다. loop를 죽이는 대신 `tool_result` 오류를 반환합니다.
- **schema 어긋남.** schema는 이렇다고 하는데 handler는 저것을 기대합니다. dispatch 전에 검증합니다.
- **안전하지 않은 병렬 실행.** 쓰기 둘이 같은 파일을 망가뜨릴 수 있습니다. tool이 안전하다고 알려진 경우가 아니면 순차 실행을 기본으로 둡니다.
- **목록 넘침.** tool schema가 너무 많으면 prompt를 밀어냅니다. 전체 schema는 필요할 때까지 미루고, 로드한 schema는 끝에 덧붙여 캐시된 접두부가 살아남게 합니다.
- **결과가 너무 큼.** 큰 출력은 context window를 채울 수 있습니다. 결과에 상한을 두고, 전체 출력은 저장하고, 미리 보기와 경로를 반환합니다.
  잘라 낸 부분은 표시합니다. 조용히 자르면 모델은 일부만 있는 파일을 전체인 양 읽게 됩니다.
- **엉뚱한 tool 선택.** 설명 둘이 겹치거나 tool 하나가 두 가지 일을 합니다. 중복은 합치고, 과부하된 schema는 나누고, 각 tool이 무엇을 위한 것이 아닌지 밝힙니다.
- **입력이 조용히 바뀜.** harness가 handler로 가는 길에 인자를 정규화하거나 덧붙입니다. 호출은 실패하고 모델은 이유를 알 수 없습니다. 잘못된 입력은 이유와 함께 거부합니다.
- **배치 실패의 번짐.** 병렬 배치에서 호출 하나가 실패해 turn 전체가 죽습니다. 그 호출에 의존하던 호출만 중단합니다.

---

## 실행 방법

[`src/`](src/)는 01을 이어받아 다음을 더합니다.

- [`tools.py`](src/tools.py): `Tool`, `Registry`, `run_concurrently`.
- [`loop.py`](src/loop.py): 각 `tool_use`를 `Registry`를 통해 dispatch합니다.
- [`demo.py`](src/demo.py): `ReadFile` tool을 등록하고 API를 상대로 loop를 실행합니다.
- [`test.py`](src/test.py): dispatch, 없는 tool 오류, 병렬 묶음 실행을 확인합니다.

```bash
python sections/02-tool-runtime/src/test.py         # offline checks, no key
uv run python sections/02-tool-runtime/src/demo.py  # live demo, needs a key
```

---

## 출처

- [Claude Code source](https://github.com/yasasbanukaofficial/claude-code):
  `Tool.ts`, `tools.ts`, `services/tools/toolOrchestration.ts`, `services/tools/toolExecution.ts`, `tools/ToolSearchTool/ToolSearchTool.ts`.
- [mini-swe-agent source](https://github.com/swe-agent/mini-swe-agent): `models/utils/actions_toolcall.py`, `models/utils/actions_text.py`, `environments/__init__.py`.
- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness) `dsh-v0.1.0-rc.7`:
  `docs/subsystems/tools.md`, `docs/tool-execution-pipeline.md`, `packages/core/tools/src/index.ts`, `packages/core/tools/src/schema.ts`.
- [learn-claude-code · s02_tool_use](https://github.com/shareAI-lab/learn-claude-code): 섹션 구성.
- [ai-agent-book](https://github.com/bojieli/ai-agent-book): `book/chapter4.md`, `book/chapter5.md` (《深入理解 AI Agent》, 李博杰. 중국어 원문이 기준):
  다섯 갈래 tool 분류, 세분화 정도, description 작성법, 파라미터 원본 보존, 인지 인터페이스 규칙, 능동적 탐색, 캐시를 깨지 않는 로딩,
  연쇄 중단을 동반한 streaming tool 시작, 지속되는 shell 기본값, 검색과 편집 비교, 그리고 체크리스트 파라미터.
  Claude Code와 Cursor에 대한 서술은 저자가 빠르게 바뀌는 구현을 직접 조사한 결과이므로, 그 시점의 증거로 읽어야 합니다.
- [MCP-Zero](https://arxiv.org/abs/2506.01056) (Fei 외): agent가 부족한 기능을 선언하고, 매칭은 서버를 먼저, 그다음 tool을 봅니다.
- [τ-bench](https://arxiv.org/abs/2406.12045) (Sierra): 성공을 최종 데이터베이스 상태로 판정하며, 체크리스트 파라미터가 기대는 지점이 이것입니다.
