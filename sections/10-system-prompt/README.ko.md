# 10 · System prompt assembly

[English](README.md) · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · **한국어**

> 매 turn마다 실시간 상태로 prompt를 만듭니다.

system prompt는 agent의 상시 지시 모음입니다. 정체성, 규칙, tool, 프로젝트 context, 활성 기능을 기술합니다.

실제 agent에서는 이것을 하드코딩된 문자열 하나로 둘 수 없습니다.

tool, memory, 출력 스타일, MCP 서버, 모드는 session마다 달라질 수 있습니다. prompt는 실제로 활성화된 것을 기술해야 합니다.

prompt 조립기는 세 가지 문제를 해결합니다.

1. 새 기능의 텍스트가 들어갈 자리가 분명합니다.
2. 비활성 기능의 텍스트는 건너뛸 수 있습니다.
3. 안정적인 섹션은 prompt caching을 쓸 수 있습니다.

조립이 없으면 prompt는 낡거나, 비대해지거나, 안전하게 바꾸기 어려워집니다.

---

## 메커니즘

![Mechanism diagram](assets/10-system-prompt-assembly.png)

prompt를 이름이 붙은 섹션들로 정의합니다. 어떤 섹션은 정적입니다. 다른 섹션은 실시간 상태에서 텍스트를 계산하고, 해당하지 않으면 `None`을 반환합니다.

조립은 단순합니다. 모든 섹션을 계산하고, `None`을 버리고, 나머지를 이어 붙입니다.

```python
sections = [
    intro, system_rules, doing_tasks, tools_section,
    session_guidance(), memory(), env_info(),
    output_style(), mcp_instructions(),
]
prompt = [s for s in resolve(sections) if s is not None]
```

두 가지 규칙이 이 구조를 다룰 만하게 유지합니다.

1. 키워드 추측이 아니라 상태를 보고 섹션을 포함합니다.
2. 자주 바뀌는 내용은 안정적인 prompt 앞부분에서 떼어 놓습니다.

### 신규: 섹션과 assemble

```python
@dataclass
class Section:                                          # src/prompt.py
    name: str
    compute: Callable    # (state) -> str | None ; static sections ignore state

def static(name, text) -> Section:
    return Section(name, lambda _state: text)

def assemble(sections, state) -> str:                  # the prompt for this turn
    parts = (s.compute(state) for s in sections)
    return "\n\n".join(p for p in parts if p is not None)
```

섹션 목록이 상태 기반 포함 여부를 담당합니다.

```python
DEMO_SECTIONS = [
    static("intro", "You are a tiny agent. ..."),
    Section("tools", lambda s: "Tools: " + ", ".join(s["tools"]) if s.get("tools") else None),
    Section("env", lambda s: f"cwd: {s['cwd']}" if s.get("cwd") else None),
    Section("mcp", lambda s: "MCP servers connected; ..." if s.get("mcp") else None),
]
```

recall한 memory는 이 prompt의 일부가 아닙니다. section 9가 `<system-reminder>` 메시지로 주입합니다. 그래서 prompt 앞부분이 더 안정적으로 유지됩니다.

### Prompt caching

대부분의 system prompt 섹션은 session 동안 안정적입니다. 데모는 최상위에 cache breakpoint를 하나 설정합니다.

```python
client.messages.create(model=MODEL, system=assemble(DEMO_SECTIONS, state),
                       messages=messages, cache_control={"type": "ephemeral"})
```

안정적인 내용이 자주 바뀌는 내용보다 앞에 와야 합니다. 바뀌는 값이 앞쪽에 나오면 cache가 더 많이 무효화될 수 있습니다.

이 규칙을 엄격하게 만드는 것은 가격표입니다. cache는 정확한 token 앞부분을 키로 씁니다.
token 하나만 바꿔도 그 뒤에 cache된 token은 전부 사라집니다. cache 읽기는 새 입력 token의 약 10분의 1 비용이고, cache 쓰기는 새 token보다 비쌉니다.
그래서 단어 하나가 자리를 옮기면 cache된 호출이 정가 호출로 바뀔 수 있습니다.
이 일을 반복해서 일으키는 원인은 두 가지입니다. prompt 위쪽에 찍히는 타임스탬프나 token 수, 그리고 실행마다 순서가 바뀌는 tool 목록입니다.

Claude Code는 명시적인 동적 경계도 씁니다. 이 경계는 뒤쪽의 작은 동적 부분이 바뀔 때 앞쪽의 큰 정적 부분을 보호합니다.

### 통합 방식

loop는 모델을 호출하기 전에 매번 prompt를 조립합니다.

```python
for _ in range(max_steps):                             # src/loop.py
    messages = context.manage(messages, summarizer=summarizer)
    system = prompt(registry, session) if prompt else None   # 10 · assemble from live state
    response = model(messages, registry, system)
    ...
```

- `prompt`는 섹션 목록을 클로저로 담은 호출 가능 객체입니다.
- 활성화된 tool이나 session 모드 같은 실시간 상태를 읽습니다.
- `prompt=None`을 넘기면 section 9의 동작이 그대로 유지됩니다.

### 대비: 섹션 registry

위의 목록은 파일 하나에 고정되어 있습니다. 섹션을 추가하려면 그 파일을 고쳐야 하고, 파일 순서가 곧 prompt 순서입니다.

deepseek-harness는 대신 등록으로 조립합니다. 각 plugin이 이름이 붙은 섹션과 위치를 나타내는 숫자를 등록합니다.
숫자는 관례에 따라 대역으로 나뉩니다. harness 정체성이 먼저, 배포 페르소나가 그다음, tool 안내가 그 뒤입니다.
조립은 숫자로 정렬하므로, plugin은 다른 무엇이 등록되어 있는지 몰라도 자기 자리를 잡습니다.

registry에는 규칙이 두 가지 더 따라옵니다.

- 한 agent는 이미 있는 이름으로 자기 섹션을 등록할 수 있습니다. 그 agent는 자기 버전을 보고, 나머지는 공용 버전을 그대로 씁니다.
- 섹션 텍스트는 `{{variables}}`를 담을 수 있고, 렌더링은 엄격합니다. 이름을 모르면 출시된 prompt에 구멍을 렌더링하는 대신 예외를 냅니다.

동적인 사실은 이 prompt 밖에 둡니다. 스냅숏으로 대화에 덧붙이되 렌더링된 텍스트가 실제로 바뀐 경우에만 붙이므로, 앞부분은 cache가 안정적으로 유지됩니다.

[`src/registry.py`](src/registry.py)가 이를 축약한 것입니다. 대비용 데모이고 `assemble()`에 연결되어 있지 않으므로, 이후 섹션은 같은 prompt 코드를 그대로 이어 갑니다.

### 더 읽을거리

이 내용은 `src/`에 없습니다. ai-agent-book에서 온 것이고, 표에 있는 시스템에서 확인된 내용은 아닙니다.

**경계 앞의 조건은 앞부분을 배로 늘립니다.** 런타임 조건 하나를 경계 앞에 두면 cache는 결과마다 하나씩, 앞부분을 두 벌 들고 있어야 합니다.
조건이 셋이면 여덟 벌입니다. 열이면 천 벌이 넘고, 각각 따로 데워지므로 거의 모든 session이 차가운 상태로 시작합니다.
조건부 섹션을 경계 뒤에 두면 앞부분은 다시 한 벌입니다.

**작업 유형마다 예제 집합을 하나 골라 그대로 둡니다.** few-shot 예제는 앞부분에 놓이므로 위의 규칙이 그대로 적용됩니다.
요청마다 가장 좋은 예제를 검색해 오면 호출할 때마다 앞부분이 다시 쓰이고 cache를 포기하게 됩니다.
고정된 집합은 요청에 조금 덜 들어맞지만, session 내내 앞부분을 따뜻하게 유지합니다.

**상태 표시줄은 실행이 지금 어디쯤인지 모델에게 알려 줍니다.** 모델은 harness를 볼 수 없으므로, 일부 harness는 실시간 상태를 context 끝에 몇 줄로 씁니다.

- tool 호출이 몇 번 실행되었는지
- 현재 TODO
- 경과 시간
- 작업 디렉터리

이 줄들은 항상 최신이어야 하고, 방법은 두 가지입니다. 어느 쪽도 거저 되지 않습니다.
매 turn마다 블록을 교체하면 상태의 참값 사본이 하나뿐이지만, 뒷부분이 다시 쓰이고 그 뒤의 cache는 사라집니다.
매 turn마다 새 블록을 덧붙이면 cache는 유지되지만, 옛 블록이 기록에 남아 모델이 이미 바뀐 상태를 보고 행동할 수 있습니다.
Claude Code는 section 9의 `<system-reminder>` 메시지를 써서 덧붙이는 쪽을 택합니다.
어느 쪽이든 블록은 실제 상태를 읽는 코드로 써야 합니다. LLM 요약기를 쓰면 호출이 하나 늘고, 지연이 늘고, 틀릴 수도 있습니다.

**외부 텍스트는 데이터이지 명령이 아닙니다.** 가져온 웹 페이지, 파일, 이슈 댓글, MCP 서버 응답은 모두 데이터입니다. 어느 것도 사용자가 말한 것이 아닙니다.
그 텍스트를 아무 표시 없이 넣으면, 그 안의 지시처럼 보이는 문장이 system prompt와 대등한 자격으로 경쟁합니다. 그것이 prompt injection입니다.
위협 모델과 실행 계층의 답은 section 3이 담당합니다. permission과 sandbox가 탈취당한 agent에게 무엇을 허용할지 정합니다.
prompt 계층은 지시와 데이터를 갈라 놓는 방식으로 그보다 먼저 손을 쓸 수 있습니다.

- 외부 내용은 출처를 밝히는 태그 블록으로 감쌉니다. 태그가 붙은 내용은 읽을 데이터일 뿐 따를 지시가 아니라고 prompt에 적습니다.
- 역할을 엄격히 지킵니다. 지시는 system prompt에, 결과는 `tool_result` 블록에, 사람의 말은 user turn에 들어갑니다.
- 충성 규칙을 한 번 명시합니다. agent는 사용자와 운영자를 위해 일하고, tool을 통해 들어온 어떤 텍스트도 그것을 바꿀 수 없습니다. 책은 이를 principal loyalty라고 부릅니다.

**prompt 계층은 경계가 아닙니다.** 모델은 여전히 설득당해 규칙에서 벗어날 수 있고, 그래서 section 3의 검사가 어차피 실행됩니다.
prompt 계층은 확률을 낮춥니다. 실행 계층은 피해를 한정합니다.

---

## 시스템별

매 turn마다 prompt를 어떻게 구성하는지.

| | Claude Code | mini-swe-agent | deepseek-harness |
| --- | --- | --- | --- |
| **장점** | 낡은 지시가 없음. 안내가 실제 활성 tool과 일치함. | 설정에서 한 번 렌더링. 무효화할 것이 없음. | prompt의 모든 사실에 소유자가 하나씩 있음. 잘못된 참조는 요란하게 실패함. |
| **단점** | 섹션 registry, cache 규칙, 순서 규율이 필요함. | 실행 도중에는 prompt를 바꿀 수 없음. | registry, 범위, 순서 대역까지 장치가 많음. |
| **이유** | tool, memory, 모드가 session마다 달라짐. | 실행 도중 tool 집합이 바뀌지 않는다고 가정함. | plugin이 자기 사실을 소유하므로, prompt는 조립될 뿐 편집되지 않음. |
| **방법: 조립 지점** | prompt 빌더, 섹션마다 문자열 하나. | Jinja2 템플릿. 빠진 변수는 요란하게 실패함. | registry, 그리고 각 범위가 조정할 수 있는 이벤트. |
| **방법: 섹션** | 정적 섹션과 동적 섹션. 프로젝트 context는 메시지에 실려 감. | 템플릿 두 개, system과 instance. | 숫자 대역에 놓인 이름 붙은 섹션. 범위가 가림. |
| **방법: 조립 시점** | 실시간 상태로 turn마다. 동적 부분은 메모이제이션함. | 실행 시작 때 한 번. | 단계마다 한 번. 바뀌는 사실은 대신 스냅숏으로 덧붙음. |

---

## 실패 모드

- **자주 바뀌는 텍스트가 cache를 깨뜨림.** 바뀌는 내용은 뒤쪽에 두거나 prompt 앞부분 밖으로 뺍니다.
- **낡은 섹션 cache.** session 상태가 바뀌면 메모이제이션된 섹션을 비웁니다.
- **prompt가 없는 tool을 언급함.** tool 텍스트는 실제 활성 tool 집합에서 생성합니다.
- **context가 prompt에 섞임.** 프로젝트 파일, 날짜, git 상태는 자주 바뀌므로 context 메시지에 둡니다.
- **prompt 재정의 충돌.** 우선순위를 정하는 해석기를 하나만 씁니다.
- **cache 키가 너무 많음.** 경계 앞의 런타임 조건 하나마다 따로 데워야 할 앞부분이 두 배가 됩니다. 조건부 섹션은 경계 뒤에 둡니다.
- **낡은 상태 블록.** 덧붙인 상태가 쌓이면 모델이 옛 사본을 보고 행동할 수 있습니다. 최신 블록을 표시하거나, 교체하고 cache 재구축을 감수합니다.
- **외부 내용을 지시로 읽음.** tool 결과에 출처 태그를 붙이고 태그가 붙은 내용은 데이터라고 명시합니다. 진짜 경계는 여전히 section 3의 permission 검사입니다.

---

## 실행 방법

[`src/`](src/)는 09를 이어받고 다음을 추가합니다.

- [`prompt.py`](src/prompt.py): `Section`, `static`, `assemble`.
- [`registry.py`](src/registry.py): deepseek-harness와의 대비. 순서 번호로 등록되는 섹션, 범위 가리기, 엄격한 `{{variable}}` 렌더링.
- [`loop.py`](src/loop.py): 매 turn마다 prompt를 다시 조립합니다.
- [`demo.py`](src/demo.py): 최상위 `cache_control`을 추가합니다.
- [`test.py`](src/test.py): 상태 기반 포함 여부를 확인합니다. registry 검사는 순서, 가리기, 요란하게 실패하는 변수를 다룹니다.

```bash
python sections/10-system-prompt/src/test.py         # offline checks, no key
uv run python sections/10-system-prompt/src/demo.py  # live demo, needs a key
```

---

## 출처

- [Claude Code source](https://github.com/yasasbanukaofficial/claude-code): `constants/prompts.ts`, `constants/systemPromptSections.ts`, `utils/api.ts`, `QueryEngine.ts`.
- [mini-swe-agent source](https://github.com/swe-agent/mini-swe-agent):
  `config/mini.yaml`, `agents/default.py`의 `_render_template`과 `get_template_vars`, `models/utils/cache_control.py`.
- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness), `dsh-v0.1.0-rc.7` 기준:
  `packages/core/system-prompt/README.md`, `packages/core/system-prompt/src/index.ts`, `packages/core/agent-loop/src/runtime-context.ts`,
  `docs/subsystems/system-prompt.md`, `docs/agent-lifecycle.md`.
- [Anthropic prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching): cache breakpoint, TTL, 가격, token 최소치.
- [Claude Code prompt caching docs](https://code.claude.com/docs/en/prompt-caching): 정적 앞부분과 동적 뒷부분 사이의 명시적 cache 경계.
- [ai-agent-book · chapter 2](https://github.com/bojieli/ai-agent-book/blob/main/book/chapter2.md) (《深入理解 AI Agent》, 李博杰. 중국어 원문이 정본):
  KV cache 경제학, 아키텍처 제약으로서의 cache(경계 앞의 조건이 cache 키를 배로 늘림), few-shot 앞부분 안정성,
  agent 상태 표시줄과 교체 대 덧붙이기의 절충, principal loyalty를 포함한 context 계층의 injection 방어.
  책의 상태 표시줄과 충성 관련 측정치는 저자 본인의 benchmark라서 출처가 하나뿐이므로 여기서는 옮기지 않습니다.
- [learn-claude-code · s10_system_prompt](https://github.com/shareAI-lab/learn-claude-code): 섹션 구성.
