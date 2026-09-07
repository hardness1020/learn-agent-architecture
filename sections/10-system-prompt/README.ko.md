# 10 · System prompt assembly

[English](README.md) · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · **한국어**

> 매 턴마다 라이브 상태에서 프롬프트를 생성합니다.

system prompt는 에이전트의 기본 지침 세트입니다. 여기에는 정체성, 규칙, 도구, 프로젝트 맥락, 활성 기능이 설명되어 있습니다.

실제 에이전트에서는 이것이 하나의 하드코딩된 문자열로 유지될 수 없습니다.

도구, 메모리, 출력 스타일, MCP 서버 및 모드는 세션마다 달라질 수 있습니다. 프롬프트는 실제로 활성화된 것을 설명해야 합니다.

프롬프트 조립기는 세 가지 문제를 해결합니다:

1. 새로운 기능 텍스트는 명확한 위치를 갖습니다.
2. 비활성 기능 텍스트는 건너뛸 수 있습니다.
3. 안정적인 섹션은 prompt caching를 사용할 수 있습니다.

조립이 없으면 프롬프트는 오래되거나, 부풀려지거나, 안전하게 변경하기 어려워집니다.

---

## 메커니즘

![메커니즘 다이어그램](assets/10-system-prompt-assembly.png)

프롬프트를 명명된 섹션으로 정의하세요. 일부 섹션은 고정되어 있습니다. 다른 섹션은 실시간 상태에서 텍스트를 계산하고 적용되지 않으면 `None`를 반환합니다.

조립은 간단합니다: 모든 섹션을 해결하고, `None`를 제거한 후 나머지를 결합하세요.

```python
sections = [
    intro, system_rules, doing_tasks, tools_section,
    session_guidance(), memory(), env_info(),
    output_style(), mcp_instructions(),
]
prompt = [s for s in resolve(sections) if s is not None]
```

두 가지 규칙이 관리 가능하게 합니다:

1. 키워드 추측이 아니라 상태에 따라 섹션을 포함하세요.
2. 변동성이 있는 콘텐츠는 안정적인 프롬프트 접두사와 떨어뜨리세요.

### 새로움: 섹션과 조립

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

섹션 목록은 상태 기반 포함을 소유합니다:

```python
DEMO_SECTIONS = [
    static("intro", "You are a tiny agent. ..."),
    Section("tools", lambda s: "Tools: " + ", ".join(s["tools"]) if s.get("tools") else None),
    Section("env", lambda s: f"cwd: {s['cwd']}" if s.get("cwd") else None),
    Section("mcp", lambda s: "MCP servers connected; ..." if s.get("mcp") else None),
]
```

기억된 메모리는 이 프롬프트의 일부가 아닙니다. 섹션 9에서 `<system-reminder>` 메시지로 주입됩니다. 이는 프롬프트 접두사를 더 안정적으로 유지합니다.

### Prompt caching

대부분의 system prompt 섹션은 세션 동안 안정적입니다. 데모는 최상위 캐시 브레이크포인트를 설정합니다:

```python
client.messages.create(model=MODEL, system=assemble(DEMO_SECTIONS, state),
                       messages=messages, cache_control={"type": "ephemeral"})
```

안정적인 콘텐츠는 변동 가능한 콘텐츠보다 먼저 나와야 합니다. 값이 초기에 변하면 캐시의 더 많은 부분이 무효화될 수 있습니다.

가격 목록이 이 규칙을 엄격하게 만드는 이유입니다. 캐시는 정확한 토큰 접두사를 기준으로 설정됩니다.
한 개의 토큰을 바꾸면 그 뒤의 모든 캐시된 토큰이 사라집니다. 캐시 읽기는 새로운 입력 토큰의 약 1/10 정도의 비용이 들고, 캐시 쓰기는 새 토큰보다 더 많은 비용이 듭니다.
따라서 한 단어가 이동하면 캐시된 호출이 전체 가격 호출로 변할 수 있습니다.
이 현상을 반복적으로 유발하는 두 가지는 프롬프트 상단 근처에 출력되는 타임스탬프나 토큰 수, 그리고 실행마다 순서가 바뀌는 도구 목록입니다.

Claude Code 또한 명시적인 동적 경계를 사용합니다. 이는 작은 동적 꼬리가 변경될 때 큰 정적 접두사를 보호합니다.

### 통합 방식

루프는 각 모델 호출 전에 프롬프트를 조립합니다:

```python
for _ in range(max_steps):                             # src/loop.py
    messages = context.manage(messages, summarizer=summarizer)
    system = prompt(registry, session) if prompt else None   # 10 · assemble from live state
    response = model(messages, registry, system)
    ...
```

- `prompt`는 섹션 목록을 닫는 호출 가능한 함수입니다.
- 활성화된 도구와 세션 모드와 같은 실시간 상태를 읽습니다.
- `prompt=None`를 전달하면 섹션-9 동작이 유지됩니다.

### 대비: 섹션 등록

위의 목록은 한 파일에 고정되어 있습니다. 섹션을 추가하려면 해당 파일을 편집해야 하며, 파일 순서가 프롬프트 순서입니다.

deepseek-harness는 대신 등록에서 조립합니다. 각 plugin은 이름이 지정된 섹션과 삽입 위치를 나타내는 번호를 등록합니다.
숫자는 관례에 따라 대역별로 나뉩니다: harness 먼저 신원, 다음으로 배포 페르소나, 그 후 도구 안내.
조립은 숫자 순으로 정렬되므로 plugin은 다른 등록 내역을 알지 못한 채 자신의 위치를 선택합니다.

등록부에는 두 가지 규칙이 더 있습니다.

- 하나의 에이전트는 이미 존재하는 이름 아래 자신의 섹션을 등록할 수 있습니다. 해당 에이전트는 자신의 버전을 보고, 다른 모든 사람은 공유 버전을 유지합니다.
- 섹션 텍스트는 `{{variables}}`를 포함할 수 있으며, 렌더링은 엄격합니다. 알려지지 않은 이름은 생성된 프롬프트에 구멍을 렌더링하는 대신 오류를 발생시킵니다.

동적 사실은 이 프롬프트에 포함되지 않습니다. 그것들은 대화에 스냅샷으로 추가되며, 실제로 렌더링된 텍스트가 변경될 때만 추가되므로 접두사는 캐시 안정성을 유지합니다.

[`src/registry.py`](src/registry.py)는 이것의 축소판입니다. 이는 대비 시연이며 `assemble()`와 연결되어 있지 않으므로, 이후 섹션은 동일한 프롬프트 코드를 이어갑니다.

### 추가 읽기

이 모든 것은 `src/`에 없습니다. 이것은 ai-agent-book에서 나온 것이며, 표에 있는 시스템들에 대한 확인은 되지 않았습니다.

**경계 이전 조건은 접두사를 곱합니다.** 경계 전에 runtime 조건 하나를 두면 캐시는 결과별로 접두사 두 개를 보관해야 합니다.
세 가지 조건은 여덟 개를 만듭니다. 열 가지 조건은 천 개 이상을 만들며, 각각 따로 가열되므로 거의 모든 세션이 처음에는 차가운 상태로 시작합니다.
경계 이후 조건 섹션을 유지하면 다시 하나의 접두사가 있습니다.

**작업 유형별로 예제 세트 하나를 선택하고 그대로 두세요.** 몇 가지 예제는 접두사에 들어가므로 위 규칙이 이를 포함합니다.
각 요청에 대한 최고의 예제를 가져오는 것은 호출할 때마다 접두사를 다시 쓰고 캐시를 포기하게 합니다.
고정된 세트는 요청에 조금 덜 적합하지만, 세션 전체 동안 접두사를 따뜻하게 유지합니다.

**상태 표시줄은 모델에게 현재 실행 위치를 알려줍니다.** 모델은 harness를 볼 수 없으므로, 일부 하니스는 몇 줄 끝부분에 실시간 상태를 기록합니다:

- 실행된 도구 호출 수
- 현재 할 일(TODO)
- 경과 시간
- 작업 디렉토리

그 줄들은 현재 상태를 유지해야 하며, 이를 하는 두 가지 방법이 있습니다. 어느 것도 무료가 아닙니다.
각 턴마다 블록을 교체하면 상태의 유일한 사본이 존재하지만, 꼬리는 다시 쓰여지고 그 뒤의 캐시는 사라집니다.
각 턴마다 새 블록을 추가하고 캐시는 유지되지만, 이전 블록들은 히스토리에 남아 있으며 모델은 이미 변경된 상태를 기반으로 동작할 수 있습니다.
Claude Code는 9절의 `<system-reminder>` 메시지를 사용하여 추가됩니다.
어느 쪽이든, 실제 상태를 읽는 코드로 블록을 작성해야 합니다. LLM 요약기는 호출을 추가하고 지연을 증가시키며 잘못될 수도 있습니다.

**외부 텍스트는 데이터이며, 절대 명령이 아닙니다.** 가져온 웹 페이지, 파일, 이슈 댓글, MCP 서버 응답은 모두 데이터입니다. 이들 중 어느 것도 사용자가 말하는 것이 아닙니다.
마커 없이 해당 텍스트를 보내고 그 안에 지침처럼 보이는 문장이 있으면 system prompt와 동등한 조건에서 경쟁하게 됩니다. 그것이 바로 프롬프트 인젝션입니다.
3절은 위협 모델과 실행 레이어 답변을 담당합니다: 권한과 sandbox가 탈취된 에이전트가 무엇을 할 수 있는지를 결정합니다.
프롬프트 레이어는 지침과 데이터를 분리하여 더 일찍 행동할 수 있습니다:

- 외부 콘텐츠를 출처를 명시한 태그 블록으로 감쌉니다. 프롬프트에서 태그된 콘텐츠는 읽어야 할 데이터일 뿐, 따라야 할 지침이 아님을 명시합니다.
- 역할을 엄격히 유지합니다. 지침은 system prompt 블록에, 결과는 `tool_result` 블록에 넣고, 사람은 user 턴에서 발언합니다.
- 충성 규칙을 한 번 명시합니다: 에이전트는 사용자와 운영자를 위해 일하며, 도구를 통해 들어오는 어떤 텍스트도 이를 변경할 수 없습니다. 책에서는 이를 주체 충성(principal loyalty)이라고 부릅니다.

**프롬프트 레이어는 경계가 아니다.** 모델은 여전히 규칙에서 벗어나도록 설득될 수 있으므로, 섹션 3의 검사는 어쨌든 실행된다.
프롬프트 레이어는 확률을 낮춘다. 실행 레이어는 피해를 제한한다.

---

## 시스템별

각 턴마다 프롬프트가 구성되는 방식.

| | Claude Code | mini-swe-agent | deepseek-harness |
| --- | --- | --- | --- |
| **장점** | 오래된 지침 없음. 안내가 실제 도구와 일치함. | 구성에서 단 한번의 렌더. 무효화할 것이 없음. | 모든 프롬프트 사실에 하나의 소유자. 잘못된 참조는 명확하게 실패함. |
| **단점** | 섹션 레지스트리, 캐시 규칙, 순서 규율 필요. | 실행 중에는 프롬프트를 변경할 수 없음. | 레지스트리, 범위, 순서 밴드가 많은 장치를 요구함. |
| **이유** | 도구, 메모리, 모드는 세션마다 다릅니다. | 도구 세트가 실행 중간에 변경되지 않는다고 가정합니다. | Plugins 자체 사실을 소유하므로 프롬프트는 조립되며, 절대 편집되지 않습니다. |
| **방법: 조립 지점** | 프롬프트 빌더, 섹션별로 하나의 문자열. | Jinja2 템플릿; 누락된 변수가 있으면 오류 발생. | 레지스트리와 각 범위가 조정할 수 있는 이벤트. |
| **방법: 섹션** | 정적 및 동적 섹션; 프로젝트 컨텍스트는 메시지에 포함됩니다. | 두 개의 템플릿, 시스템과 인스턴스. | 숫자 범위로 명명된 섹션, 범위에 따라 가려짐. |
| **방법: 빌드 시점** | 라이브 상태에서 턴별로, 동적 부분은 메모이징됨. | 실행 시작 시 한 번. | 단계별로 한 번. 사실이 변경되면 대신 스냅샷으로 추가. |

---

## 실패 모드

- **변동 텍스트는 캐시를 깨뜨립니다.** 변경되는 내용은 프롬프트 접두사 뒤나 끝에 배치하세요.
- **오래된 섹션 캐시.** 세션 상태가 변경될 때 메모이제이션된 섹션을 지우세요.
- **도구가 없는 프롬프트 이름.** 활성화된 도구 세트에서 도구 텍스트를 생성하세요.
- **프롬프트에 혼합된 컨텍스트.** 프로젝트 파일, 날짜, git 상태를 자주 변경될 경우 컨텍스트 메시지에 포함하세요.
- **프롬프트 오버라이드 충돌.** 우선순위를 정의할 때 하나의 해결자를 사용하세요.
- **너무 많은 캐시 키.** 경계 전에 각 runtime 조건이 따로 워밍해야 하는 접두사를 두 배로 만듭니다. 조건부 섹션은 그 이후에 두세요.
- **오래된 상태 블록.** 추가된 상태가 누적되어 모델이 오래된 사본으로 작동할 수 있습니다. 최신 블록을 표시하거나 교체하고 캐시 재구성을 수락하세요.
- **외부 콘텐츠를 지침으로 읽음.** 도구 결과를 출처별로 태그하고 태그된 콘텐츠를 데이터라고 말합니다. 섹션 3의 권한 검사는 실제 경계를 유지합니다.

---

## 실행 가능

[`src/`](src/)는 09를 앞으로 가져가고 다음을 추가합니다:

- [`prompt.py`](src/prompt.py): `Section`, `static`, 그리고 `assemble`.
- [`registry.py`](src/registry.py): deepseek-harness 대비: 주문 번호로 등록된 섹션, 범위 섀도잉, 그리고 엄격한 `{{variable}}` 렌더링.
- [`loop.py`](src/loop.py): 매 턴마다 프롬프트를 다시 조립합니다.
- [`demo.py`](src/demo.py): 최상위 `cache_control`를 추가합니다.
- [`test.py`](src/test.py): 상태 기반 포함을 확인합니다; 레지스트리 확인은 순서 지정, 섀도잉 및 fail-loud 변수를 포함합니다.

```bash
python sections/10-system-prompt/src/test.py         # offline checks, no key
uv run python sections/10-system-prompt/src/demo.py  # live demo, needs a key
```

---

## 출처

- [Claude Code 소스](https://github.com/yasasbanukaofficial/claude-code): `constants/prompts.ts`, `constants/systemPromptSections.ts`, `utils/api.ts`, `QueryEngine.ts`.
- [mini-swe-agent 소스](https://github.com/swe-agent/mini-swe-agent):
  `config/mini.yaml`, `_render_template` 그리고 `get_template_vars` in `agents/default.py`, `models/utils/cache_control.py`.
- [deepseek-harness 소스](https://github.com/deepseek-ai/deepseek-harness) at `dsh-v0.1.0-rc.7`:
  `packages/core/system-prompt/README.md`, `packages/core/system-prompt/src/index.ts`, `packages/core/agent-loop/src/runtime-context.ts`,
  `docs/subsystems/system-prompt.md`, `docs/agent-lifecycle.md`.
- [Anthropic prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching): 캐시 중단점, TTL, 가격 책정 및 토큰 최소값.
- [Claude Code prompt caching 문서](https://code.claude.com/docs/en/prompt-caching): 정적 접두사와 동적 꼬리 사이의 명시적 캐시 경계.
- [ai-agent-book · 2장](https://github.com/bojieli/ai-agent-book/blob/main/book/chapter2.md) (《深入理解 AI Agent》, 이보걸; 중국어 원본이 정본입니다):
  KV 캐시 경제학, 아키텍처 제약으로서의 캐시(경계 이전 조건이 캐시 키를 곱함), 소수 샷 접두사 안정성,
  에이전트 상태 표시줄과 교체 대 추가 트레이드오프, 그리고 주체 충성도를 통한 컨텍스트 계층 주입 방어.
  책의 상태 표시줄과 충성도 측정치는 저자의 자체 벤치마크이므로 수치는 단일 출처이며 여기서 반복되지 않습니다.
- [learn-claude-code · s10_system_prompt](https://github.com/shareAI-lab/learn-claude-code): 섹션 프레이밍.
