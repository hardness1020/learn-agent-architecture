# 19 · MCP / plugins / channels

[English](README.md) · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · **한국어**

> 할 수 있는 게 모자라면 더 꽂아 넣습니다. harness는 표준 protocol 하나로 바깥 세상에 닿습니다.

harness는 자기 tool이 허락하는 일만 할 수 있고, 기본 제공 tool은 전부 미리 정의돼 있습니다. 입력 스키마, 실행, 오류 처리까지 전부 그렇습니다.

그 방식으로는 사용자가 원하는 서비스를 감당할 수 없습니다. 이슈 트래커, 배포 시스템, 지식 베이스 같은 것들입니다. 각각을, 각 서비스가 쓰는 언어마다, 손으로 tool을 짜 줄 수는 없습니다.

MCP(Model Context Protocol)는 그 간극을 메우는 개방형 계약입니다. 외부 서비스가 자기 tool을 선언하면, agent는 누가 만들었는지도 어떻게 만들었는지도 모르는 채로 그것을 호출합니다.
MCP 용어로 그 서비스가 서버이고, 접속해서 호출하는 harness가 클라이언트입니다.

그래서 agent는 아무도 harness를 고치지 않아도 Jira tool이나 배포 tool을 얻습니다. MCP가 없으면 할 수 있는 일이 바이너리에 함께 출시된 범위에 묶입니다.

MCP 위에 얹히는 조각이 둘 더 있습니다. plugin은 서버를 hook, skill과 묶어서 한 덩어리로 설치되게 합니다.
channel은 서버가 메시지를 안으로 밀어 넣을 수 있게 합니다. 둘 다 같은 protocol을 탑니다.

---

## 메커니즘

![Mechanism diagram](assets/19-mcp-plugins-channels.png)

서버마다 접속하고, 그 tool을 찾아내고(`tools/list`), 각각을 런타임 `Tool`로 감싸고(섹션 2), 그것들을 loop이 배분하는 같은 풀에 합칩니다.

이름은 `mcp__<server>__<tool>`로 namespace를 붙여, 서버 둘이 절대 충돌하지 않게 합니다. loop과 게이트는 바뀌지 않습니다. MCP tool은 `run()`이 전송 계층 너머로 호출을 내보내는 `Tool`일 뿐입니다.

- 탐색은 서버당 `tools/list` 호출 한 번입니다. 돌아온 명세 하나하나가 감싸인 `Tool` 하나가 됩니다.
- 이름에는 namespace를 붙이고 정규화합니다. 그래서 고유하고 API의 이름 패턴에도 맞습니다.
- 각 tool의 MCP 주석(`readOnlyHint`, `destructiveHint`)은 게이트가 읽는 permission 힌트가 됩니다(섹션 3).
- 하나의 `Registry`에 합쳐지므로, 모델은 MCP tool과 기본 제공 tool을 같은 목록에서 봅니다.

### 그 아래의 전송 protocol

2026-07-28 명세 개정판은 전송 경로를 상태 없는 방식으로 바꿨습니다. 이제 모든 요청이 홀로 서므로, 서버 복제본 어느 것이든 답할 수 있습니다.
위에서 설명한 harness 쪽(탐색, 감싸기, 합치기)은 바뀌지 않습니다. 전송 경로에서 바뀐 것은 다음과 같습니다.

- **핸드셰이크가 없어졌습니다.** 전에는 클라이언트가 `initialize`를 호출하고 기다린 뒤에야 다른 일을 할 수 있었습니다.
  이제는 어떤 요청이든 먼저 갈 수 있고, 요청마다 자기 protocol 버전과 능력을 `_meta`에 실어 보냅니다.
  버전을 미리 확인하고 싶은 클라이언트는 `server/discover`를 호출합니다.
- **session이 없어졌습니다.** 전에는 서버가 session 헤더 뒤에 연결별 상태를 들고 있었습니다.
  이제 호출 사이에 상태가 필요한 서버는 핸들을 돌려주고, 클라이언트는 그것을 평범한 tool 인자로 되돌려 보냅니다.
- **알림 스트림이 하나입니다.** 전에는 클라이언트가 변경 소식을 들으려고 긴 GET 연결을 열어 두었습니다.
  이제는 `subscriptions/listen` 스트림 하나를 열고 원하는 이벤트를 지정합니다(tool 목록 변경, 리소스 변경).
  목록 결과에는 클라이언트가 얼마나 오래 캐시해도 되는지 알려 주는 `ttlMs` 필드도 함께 옵니다.
- **서버는 되부르지 않고 응답으로 묻습니다.** 전에는 서버가 tool call 도중에 자기 요청을 클라이언트에 보낼 수 있었습니다
  (사용자에게 질문하거나 모델에 샘플링을 요청). 이제는 `input_required`로 표시된 중간 결과를 돌려주고,
  클라이언트가 답을 붙여 같은 요청을 다시 보냅니다.
- **기능이 줄었습니다.** Roots, Sampling, Logging, 그리고 옛 HTTP+SSE 전송이 폐기 예정입니다.
  공식 전송은 둘만 남았습니다. 로컬 서버용 stdio와 원격 서버용 Streamable HTTP입니다.

agent를 쓰는 사람 눈에는 화면상 달라지는 것이 없습니다. 옛 서버는 계속 동작하고, v1 SDK도 계속 관리됩니다.
이득은 아래쪽에 떨어집니다. 원격 서버가 로드 밸런서 뒤에서 확장되고, 첫 호출이 왕복 한 번을 건너뛰고, 캐시된 tool 목록이 token을 아낍니다.
폐기 예정 기능을 쓰는 서버에는 이전할 시간이 12개월 주어집니다. 그 작업은 사용자가 아니라 서버 작성자의 몫입니다.

### 이번에 추가되는 것: 찾아낸 tool 감싸기

`mcp.py`는 찾아낸 명세 하나하나를 `Tool`로 바꿉니다. 이름에는 namespace를 붙여 서버끼리 절대 충돌하지 않게 하고, API의 문자 집합에 맞게 정규화합니다.

```python
def tool_name(server, tool):                           # src/mcp.py
    return f"mcp__{normalize(server)}__{normalize(tool)}"   # buildMcpToolName

def wrap(server, spec, call):
    ann = spec.get("annotations", {})
    read_only = bool(ann.get("readOnlyHint"))
    bare = spec["name"]
    return Tool(
        name=tool_name(server, bare),
        run=lambda args, _t=bare: call(_t, args),      # dispatch calls out over the transport
        input_schema=spec.get("inputSchema") or dict(NO_INPUT),
        is_read_only=read_only,
        is_concurrency_safe=read_only,                 # reads are safe to batch
    )
```

- `tool_name`은 모든 tool에 namespace를 붙입니다. `normalize`는 `[a-zA-Z0-9_-]` 밖의 문자를 `_`로 바꿔 API 이름 패턴을 만족시킵니다.
- `run`은 벌거벗은 tool 이름과 그 서버의 `call`을 클로저로 잡습니다. 그래서 감싸인 `Tool`을 배분하면 전송 계층 너머로 되돌아 닿습니다.
- `readOnlyHint` 주석은 `is_read_only`가 되고, permission 게이트(섹션 3)는 그것을 읽어 허용할지 물어볼지 정합니다.

### 이번에 추가되는 것: 탐색과 합치기

`connect`는 탐색을 한 번 돌리고 감싸인 tool들을 돌려줍니다. 호출하는 쪽이 그것을 loop의 `Registry`에 합칩니다.

```python
def connect(server, conn):                             # src/mcp.py
    return [wrap(server, spec, conn.call) for spec in conn.list_tools()]
```

- `conn`은 살아 있는 전송 계층입니다. 프로덕션에서는 `stdio`나 `http`, 데모에서는 프로세스 내 방식입니다. 탐색은 어느 쪽인지 신경 쓰지 않습니다.
- 돌아온 `Tool`들은 기본 제공 tool과 같은 풀에 등록됩니다. 그래서 `registry.schemas()`가 둘을 함께 알리고, loop도 똑같이 배분합니다.

### 이번에 추가되는 것: channel과 plugin 설정

작은 조각 둘이 이 섹션을 마무리합니다.

첫째는 메시지 흐름을 뒤집습니다. 보통은 agent가 서버를 호출하지만, 서버도 스스로 메시지를 밀어 넣을 수 있습니다(Slack 메시지가 도착하는 경우).
harness는 그 텍스트를 `<channel>` 태그로 감싸 agent의 다음 turn 앞에 놓습니다. 그러면 모델이 그것을 읽습니다.

```python
def wrap_channel(source, payload):                     # src/mcp.py
    return f'<{CHANNEL_TAG} source="{source}">{payload}</{CHANNEL_TAG}>'
```

둘째는 설정 계층입니다. 같은 서버가 plugin, user, project 설정에 동시에 정의될 수 있습니다. `merge_servers`가 우선순위로 승자를 고릅니다.

```python
def merge_servers(*layers):                            # src/mcp.py
    merged = {}
    for scope in PRECEDENCE:                            # plugin < user < project < local
        for layer in layers:
            merged.update(layer.get(scope, {}))
    return merged
```

- `wrap_channel`은 Slack, Discord, SMS를 같은 protocol 위의 양방향 인터페이스로 만듭니다. 태그가 붙은 블록은 백그라운드 알림처럼 큐에 들어갑니다(섹션 13).
- `merge_servers`는 둘 이상의 범위에 정의된 서버를 정리합니다. `local`이 `project`를 덮고, `project`가 `user`를 덮고, `user`가 `plugin`을 덮습니다.

channel에는 누구나 보낼 수 있습니다. 들어온 Slack이나 SMS 메시지가 반드시 사용자에게서 온 것은 아닙니다. 스팸일 수도 있고, agent를 조종하려는 지시일 수도 있습니다.
그래서 그것이 turn이 되기 전에 게이트를 지납니다(hermes-agent는 인증 전에, 들어오는 모든 메시지에 `pre_gateway_dispatch`를 발화시킵니다).

```python
def gate_inbound(source, payload, gates=()):           # src/mcp.py
    for gate in gates:
        out = gate(source, payload) or {}
        if out.get("drop"):
            return None                                # discarded: the model never reads it
        if out.get("rewrite") is not None:
            payload = out["rewrite"]                   # e.g. redact a secret
    return wrap_channel(source, payload)
```

- 게이트는 loop이 텍스트를 보기 전에 버리거나(스팸, 모르는 발신자) 고쳐 쓸 수 있습니다(마스킹).
- `None`을 돌려주면 turn이 아예 일어나지 않습니다. 쓸모없는 입력에 대해 가장 값싼 결말입니다.

### 기존 구조에 붙이는 방법

데모는 서버를 하나 찾아내고 agent turn을 하나 돌립니다. 모델은 그 MCP tool을 아무것도 모르는 채로 호출합니다.

```python
reg = Registry()
for t in mcp.connect("kb", KBServer()):                # discover, wrap, merge
    reg.register(t)
run_turn([...goal...], model, reg, Session(mode=DEFAULT))   # the one agent call
```

- 모델은 자기 tool 목록에서 기본 제공 tool 옆에 있는 `mcp__kb__search`를 보고 호출합니다. 그 tool을 누가 만들었는지는 끝내 모릅니다.
- 이 tool은 읽기 전용이라 게이트가 묻지 않고 허용합니다. 파괴적인 tool이라면 물어보거나, 정규화된 이름을 키로 하는 규칙으로 미리 승인해 둡니다.
- loop은 바뀌지 않습니다. MCP는 풀에 tool을 더할 뿐이고, 그 뒤는 전부 섹션 2의 배분과 섹션 3의 게이트입니다.

### 더 읽을거리

여기 나오는 내용은 `src/`에 없습니다. ai-agent-book과 MCP 명세에서 온 것이고, 표에 있는 시스템들에서 확인된 내용은 아닙니다.

**세 가지 기본 요소, 하나의 풀.** 서버는 세 종류를 내놓을 수 있습니다. 위의 풀에 닿는 것은 tool뿐입니다.

- **tool**은 동작입니다. 모델이 하나를 골라 호출합니다. `tools/list`가 이것들을 돌려주고, 위의 코드가 그것을 감쌉니다.
- **리소스**는 클라이언트가 읽을 수 있는 데이터이며 각각 URI가 있습니다. 파일, 테이블, 위키 문서 같은 것입니다. 클라이언트가 하나를 가져와 그 텍스트를 context에 넣습니다. 모델이 호출하지는 않습니다.
- **prompt**는 서버가 넘겨주는 템플릿입니다. 보통 모델이 고르는 것이 아니라 사용자가 실행하는 명령으로 나타납니다.

**리소스는 tool 목록에 올리지 않습니다.** Claude Code는 리소스를 하나씩 광고하지 않습니다. tool 둘을 제공합니다. 하나는 리소스를 나열하고, 하나는 읽습니다.
그래서 문서를 천 개 들고 있는 서버도 tool 목록에서는 항목 두 개만 차지합니다.

**접속하는 것과 광고하는 것은 별개의 결정입니다.** 서버에 접속하면 상호 연동을 얻습니다. 그 tool을 광고하면 context를 씁니다.
앞의 것만 하고 뒤의 것은 전부 하지 않을 수 있습니다.

**광고에 드는 비용.** 광고된 tool은 요청마다 token을 씁니다. 이름, 설명, 입력 스키마 전체가 task 앞에 놓입니다.
서버 다섯 개면 task 자체보다 긴 텍스트가 붙을 수 있습니다. 목록이 길면 모델이 엉뚱한 tool을 고르는 일도 잦아집니다(섹션 2).

**고를 수 있는 세 단계.** 전부에 대해 한 번에 정하지 말고, 서버마다 얼마나 광고할지 정합니다.

- **전부.** 가장 단순합니다. session이 거의 매 turn 쓰는 서버에 맞습니다.
- **색인.** 이름과 한 줄 요약만 광고합니다. 모델이 그 tool을 요청하면 그때 전체 스키마를 싣습니다(탐색 쪽은 섹션 2에서 다룹니다).
- **문 하나.** 서버 이름과 tool 이름을 받는 tool 하나만 광고합니다. 나머지는 그 뒤에 둡니다. agent는 스키마 쉰 개가 아니라 하나 값만 냅니다.

**이 중 어느 것도 protocol에 있지 않습니다.** 명세는 tool을 나열하는 방법과 호출하는 방법을 정합니다. 그중 몇 개가 prompt에 닿을지는 클라이언트가 정합니다.
그러므로 지연 로딩은 자기 harness에서 직접 확인해야 하는 설정입니다. 서버는 그것이 켜져 있다고 가정할 수 없습니다.

---

## 시스템별

harness가 자기 바깥에 어떻게 닿는지 비교합니다.

| | Claude Code | Hermes Agent | deepseek-harness |
| --- | --- | --- | --- |
| **장점** | 어떤 서비스든, 어떤 언어든, harness를 고치지 않아도 됨. | 다른 클라이언트가 이것을 MCP 서버로 삼아 구동할 수 있음. | 서버가 설정이므로 재시작 없이 바꿔 끼울 수 있음. |
| **단점** | 새로운 공격 표면이 생기며, 그것이 서버의 자기 신고 주석에 기댐. | channel에는 누구나 보낼 수 있음. 스팸이거나 조종하는 텍스트일 수 있음. | tool만 있고, 메시지를 밀어 넣을 채팅 channel이 없음. |
| **이유** | MCP가 없으면 할 수 있는 일이 함께 출시된 범위에 묶임. | agent가 MCP 클라이언트이면서 동시에 서버임. | 모든 것이 plugin이므로 서버도 plugin 하나일 뿐임. |
| **방법: 전송 계층** | stdio부터 원격 http까지 여섯 가지, 각각 별도 풀에 둠. | 양방향 MCP와 채팅 어댑터. | 로컬 stdio와 스트리밍 http, 서버당 plugin 하나. |
| **방법: plugin 형식** | plugin이 서버, hook, skill을 묶고 우선순위로 합쳐짐. | 매니페스트와 등록 항목 하나. | 설정 행. 패치가 id로 행 하나를 교체함. |
| **방법: tool 풀 구성** | 복제하고 namespace를 붙임. 주석이 게이트로 들어감. | plugin tool과 MCP tool이 하나의 registry에 합류함. | 한 서버의 tool이 한 묶음으로 교체되거나 되돌려짐. |

---

## 실패 모드

- **이름 충돌.** 서버 둘이 모두 `search`를 노출합니다. `mcp__server__tool` namespace가 충돌을 막습니다. 다만 이름에 `__`가 든 서버는 여전히 잘못 파싱되므로 이름을 단순하게 둡니다.
- **tool 목록 비대화.** 서버가 많으면 tool 목록이 커져 token을 쓰고 선택을 흐립니다(섹션 2).
  완화: 설명을 잘라 내고, 요청마다 모든 스키마를 보내는 대신 서버마다 얼마나 광고할지 정합니다.
- **접속 뒤 낡은 풀.** session 도중에 추가한 서버는 캐시된 tool 목록에 없어서 모델이 끝내 보지 못합니다.
  완화: 변경 시 풀과 prompt를 다시 만듭니다(섹션 8). 2026-07-28 명세는 이를 위해 `subscriptions/listen` 위의 `toolsListChanged`와 `ttlMs` 힌트를 더합니다.
- **연결 요동.** 불안정한 서버가 timeout을 내거나, 연결을 끊거나, token을 만료시킵니다. 완화: 반복 실패 뒤 재접속하고, `401`에는 재인증하고, 호출마다 timeout을 겁니다(섹션 11).
  상태 없는 개정판은 스트림 재개를 없앴으므로, 진행 중이던 요청이 깨지면 재개가 아니라 새 요청으로 다시 보냅니다.
- **과신된 부작용.** 서버가 파괴적인 tool에 `readOnlyHint: true`를 달아 확인 프롬프트를 건너뜁니다. 완화: 정규화된 이름에 건 규칙이 그래도 게이트를 겁니다(섹션 3).
- **설명 오염.** tool 설명은 서버가 쓴 텍스트이고, 모델은 그것을 지시로 읽습니다.
  서버는 거기에 명령을 숨길 수 있습니다. 사용자의 키 파일을 먼저 읽어서 같이 보내라는 식입니다. 모델은 그대로 할 수 있습니다.
  완화: 서버를 설치하기 전에 설명을 읽습니다. 설명이 바뀌면 바뀐 코드처럼 리뷰합니다.
- **tool 가리기.** 모든 서버가 하나의 prompt를 공유합니다. 그래서 한 서버의 설명이 다른 서버의 tool을 언급하며,
  결제 tool이 고장 났다고 주장하고 호출을 자기 쪽으로 끌어올 수 있습니다.
  완화: namespace는 이름 충돌을 막습니다. 이것은 막지 못합니다. 리뷰하지 않은 서버는 실제 자격 증명이 있는 session에서 빼 둡니다.
- **탈취된 업데이트.** 서버가 리뷰를 통과한 뒤, 다음 시작 때 새 코드와 새 설명을 내놓습니다. protocol은 사용자에게 다시 묻지 않습니다.
  완화: 버전을 고정합니다. 업그레이드 뒤에는 설명을 다시 읽습니다. 서버마다 최소 권한 자격 증명을 따로 줘서, 나쁜 서버 하나가 다른 서버의 범위에 닿지 못하게 합니다.

---

## 실행 방법

[`src/`](src/)는 18의 코드를 이어받아 다음을 더합니다.

- [`mcp.py`](src/mcp.py): 탐색과 감싸기, plugin 설정 병합, channel 감싸기, 그리고 인바운드 게이트(`gate_inbound`).
- [`test.py`](src/test.py): 탐색과 namespace 붙이기, 힌트 대응, 게이트를 포함한 풀 합치기, 설정 우선순위, channel 태그, 인바운드 버리기와 고쳐 쓰기를 확인합니다.
- [`demo.py`](src/demo.py): agent turn 하나가 찾아낸 `mcp__kb__search`를 통해 프로세스 내 MCP tool을 아무것도 모르는 채로 호출합니다.

loop과 배분은 바뀌지 않습니다. MCP는 섹션 2의 풀에 tool을 더하고, 섹션 3의 게이트가 그 tool이 스스로 선언한 주석을 읽습니다.

```bash
python sections/19-mcp-plugins-channels/src/test.py         # offline checks, no key
uv run python sections/19-mcp-plugins-channels/src/demo.py  # live demo, needs a key
```

---

## 출처

- [Claude Code MCP transport](https://github.com/yasasbanukaofficial/claude-code):
  `services/mcp/types.ts` (`TransportSchema`), `client.ts` (`MCPTool` cloning, `buildMcpToolName`), `normalization.ts` (`normalizeNameForMCP`).
- [Claude Code MCP config and channels](https://github.com/yasasbanukaofficial/claude-code):
  `config.ts` (precedence), `channelNotification.ts` (`CHANNEL_TAG`), plus `McpAuthTool`, `ListMcpResourcesTool`, `ReadMcpResourceTool`.
- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness) at `dsh-v0.1.0-rc.7`:
  `docs/architecture.md`, `docs/cordis-primer.md`, `packages/acp/acp/README.md`, `packages/extensions/tool-cordis/README.md`.
- [Claude Code plugins](https://github.com/yasasbanukaofficial/claude-code): `plugins/builtinPlugins.ts`, `plugins/bundled/`, `types/plugin.ts`, plus `remote/` and `bridge/`.
- [Hermes Agent source](https://github.com/NousResearch/hermes-agent):
  `mcp_serve.py`, `hermes_cli/plugins.py` (`PluginManager`, `VALID_HOOKS`), `gateway/platforms/`, `gateway/platform_registry.py`, `plugins/platforms/`.
- [MCP specification 2026-07-28](https://modelcontextprotocol.io/specification/2026-07-28)과 그
  [changelog](https://modelcontextprotocol.io/specification/2026-07-28/changelog): 상태 없는 protocol, 세 가지 기본 요소(tool, 리소스, prompt),
  `server/discover`, `subscriptions/listen`, MRTR, 폐기 예정 항목.
- MCP 블로그: [the future of transports](https://blog.modelcontextprotocol.io/posts/2025-12-19-mcp-transport-future/) (protocol이 상태 없는 방식으로 간 이유),
  [SDK betas for 2026-07-28](https://blog.modelcontextprotocol.io/posts/sdk-betas-2026-07-28/) (v2 SDK, 하위 호환성).
- [ai-agent-book](https://github.com/bojieli/ai-agent-book): `book/chapter4.md`, 중국어 원문이 정본입니다. tool 생태계 절에서 다루는 내용은
  MCP 기본 요소, 광고된 스키마의 context 부담, 그리고 신뢰 모델(설명 오염, tool 가리기, 탈취된 업데이트, 자격 증명 범위)입니다.
- 구성 참고: [learn-claude-code · s19_mcp_plugin](https://github.com/shareAI-lab/learn-claude-code).
