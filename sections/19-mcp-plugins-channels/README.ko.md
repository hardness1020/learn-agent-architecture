# 19 · MCP / plugins / channels

[English](README.md) · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · **한국어**

> 능력이 부족한가요? 더 연결하세요. harness는 하나의 표준 프로토콜을 통해 세상과 연결됩니다.

harness는 도구가 허락하는 것만 할 수 있으며, 모든 내장 도구는 사전에 정의되어 있습니다: 입력 스키마, 실행, 오류 처리, 전부 다.

이는 사용자가 원하는 서비스에 확장되지 않습니다: 이슈 추적기, 배포 시스템, 지식 기반. 각각의 도구를 사용하는 언어별로 손으로 작성할 수는 없습니다.

MCP(모델 컨텍스트 프로토콜)는 그 격차를 메우는 공개 계약입니다. 외부 서비스는 자신의 도구를 선언하고, 에이전트는 누가 작성했는지 또는 어떻게 만들었는지 알지 못한 채 도구를 호출합니다.
MCP 관점에서 서비스는 서버이고, 연결하고 호출하는 harness는 클라이언트입니다.

그래서 에이전트는 아무도 harness를 편집하지 않아도 Jira 도구나 배포 도구를 획득합니다. MCP는 제외하고, 기능은 바이너리에 포함된 상태에서 고정됩니다.

두 가지 요소가 MCP를 기반으로 구축됩니다. plugin은 hooks와 skills와 함께 서버를 묶어 하나의 단위로 설치되도록 합니다.
채널은 서버가 메시지를 다시 푸시할 수 있게 합니다. 둘 다 동일한 프로토콜을 사용합니다.

---

## 메커니즘

![메커니즘 다이어그램](assets/19-mcp-plugins-channels.png)

각 서버에 연결하고, 그 도구(`tools/list`)를 발견하며, 각각을 runtime `Tool`(섹션 2)로 래핑하고, 그런 다음 루프가 디스패치하는 동일한 풀로 합칩니다.

이름은 네임스페이스화된 `mcp__<server>__<tool>`이므로 두 서버가 충돌하지 않습니다. 루프와 게이트는 변하지 않습니다: MCP 도구는 `Tool`이며, 그 `run()`는 전송을 통해 호출합니다.

- Discovery는 서버당 하나의 `tools/list` 호출입니다; 반환된 각 사양은 하나의 래핑된 `Tool`가 됩니다.
- 이름은 네임스페이스화되고 정규화되어 고유하며 API의 이름 패턴과 일치합니다.
- 각 도구의 MCP 주석(`readOnlyHint`, `destructiveHint`)은 게이트가 읽는 권한 힌트가 됩니다(섹션 3).
- 하나의 `Registry`로 병합되면, 모델은 MCP 도구와 내장 요소를 동일한 목록에서 확인합니다.

### 그 아래의 와이어 프로토콜

2026-07-28 사양 개정으로 인해 와이어가 상태를 가지지 않게 되었습니다. 이제 모든 요청은 독립적으로 처리되므로, 어떤 서버 복제본이라도 요청에 답변할 수 있습니다.
위의 harness 측(발견, 래핑, 병합)은 변경되지 않았습니다. 와이어에서 변경된 내용은 다음과 같습니다:

- **더 이상 핸드셰이크 없음.** 이전에는 클라이언트가 `initialize`를 호출하고 다른 작업을 하기 전에 기다렸습니다.
  이제는 어떤 요청이든 먼저 갈 수 있으며; 각 요청은 자체 프로토콜 버전과 기능을 `_meta`에 담습니다.
  버전을 미리 확인하고 싶은 클라이언트는 `server/discover`를 호출합니다.
- **더 이상 세션 없음.** 이전에는 서버가 연결별 상태를 세션 헤더 뒤에 유지했습니다.
  이제 호출 간 상태가 필요한 서버는 핸들을 반환하며, 클라이언트는 이를 일반 툴 인수로 다시 전달합니다.
- **하나의 알림 스트림.** 이전에는 클라이언트가 변경 사항을 듣기 위해 오래된 GET 연결을 유지했습니다.
  이제 하나의 `subscriptions/listen` 스트림을 열고 원하는 이벤트를 이름 붙입니다(변경된 도구 목록, 변경된 자원).
  목록 결과에는 클라이언트가 이를 얼마나 오래 캐시할 수 있는지를 나타내는 `ttlMs` 필드도 포함되어 있습니다.
- **서버는 호출하는 것이 아니라 응답함으로써 요청합니다.** 이전에는 서버가 클라이언트에게 자체 요청을 보낼 수 있었습니다.
  중간 도구 호출(사용자에게 질문하고, 모델에게 샘플을 요청). 이제 그것은 표시된 중간 결과를 반환합니다
  `input_required`, 그리고 클라이언트는 답변을 첨부하여 동일한 요청을 다시 시도합니다.
- **기능 감소.** Roots, Sampling, Logging, 그리고 이전 HTTP+SSE 전송 방식은 더 이상 사용되지 않습니다.
  공식적으로 남아 있는 전송 방식은 두 가지입니다: 로컬 서버용 stdio, 원격 서버용 Streamable HTTP.

에이전트를 사용하는 경우 화면상 아무것도 변하지 않습니다: 이전 서버는 계속 작동하며, v1 SDK는 유지 관리됩니다.
이점들은 아래에 있습니다: 원격 서버는 로드 밸런서 뒤에서 확장되며, 첫 번째 호출은 왕복을 건너뛰고, 캐시된 도구 목록은 토큰을 절약합니다.
사용 중단 기능 서버는 마이그레이션을 위해 12개월의 기간을 가집니다. 이 작업은 사용자가 아닌 서버 작성자가 수행합니다.

### 새 기능: 발견된 도구 래핑

`mcp.py`는 각 발견된 스펙을 `Tool`로 변환합니다. 이름은 네임스페이스 처리되어 서버 간 충돌이 없으며, API의 문자 세트로 정규화됩니다:

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

- `tool_name`는 모든 도구에 네임스페이스를 지정합니다; `normalize`는 `[a-zA-Z0-9_-]` 외부의 모든 문자를 `_`로 교체하여 API 이름 패턴을 만족시킵니다.
- `run`는 도구 이름 자체와 서버의 `call`를 포함하므로, 래핑된 `Tool`를 디스패치하면 전송을 통해 다시 접근할 수 있습니다.
- `readOnlyHint` 주석은 `is_read_only`가 되며, 이는 권한 게이트(3절)가 허용할지 묻기를 결정하기 위해 읽는 것입니다.

### 새로움: 검색 및 병합

`connect`는 한 번만 검색을 실행하고 래핑된 도구를 반환하며; 호출자는 이를 루프의 `Registry`에 병합합니다:

```python
def connect(server, conn):                             # src/mcp.py
    return [wrap(server, spec, conn.call) for spec in conn.list_tools()]
```

- `conn`는 실시간 전송입니다: `stdio` 또는 `http`는 프로덕션에서, 데모에서는 처리 중입니다. Discovery는 어느 쪽이든 상관하지 않습니다.
- 반환된 `Tool`는 내장 목록과 동일한 풀에 등록되므로, `registry.schemas()`는 함께 광고되고 루프는 동일한 방식으로 디스패치합니다.

### 새로 추가: 채널 및 plugin 설정

두 개의 작은 부분이 섹션을 완성합니다.

첫 번째는 메시지 흐름을 반대로 합니다. 일반적으로 에이전트가 서버를 호출하지만, 서버가 직접 메시지를 푸시할 수도 있습니다(예: Slack 메시지가 도착할 때).
harness는 해당 텍스트를 `<channel>` 태그로 감싸 에이전트의 다음 차례 앞에 배치하여 모델이 이를 읽도록 합니다:

```python
def wrap_channel(source, payload):                     # src/mcp.py
    return f'<{CHANNEL_TAG} source="{source}">{payload}</{CHANNEL_TAG}>'
```

두 번째는 설정 계층화입니다. 같은 서버는 plugin, 사용자, 프로젝트 설정에서 동시에 정의될 수 있으며; `merge_servers`가 우선순위에 따라 승자를 선택합니다:

```python
def merge_servers(*layers):                            # src/mcp.py
    merged = {}
    for scope in PRECEDENCE:                            # plugin < user < project < local
        for layer in layers:
            merged.update(layer.get(scope, {}))
    return merged
```

- `wrap_channel`는 Slack, Discord, 또는 SMS를 동일한 프로토콜 위에서 양방향 인터페이스로 변환합니다; 태그된 블록은 백그라운드 메모처럼 큐에 들어갑니다(섹션 13).
- `merge_servers`는 여러 범위에서 정의된 서버를 해결합니다: `local`가 `project`를 덮어쓰고, `user`를 덮어쓰고, `plugin`를 덮어씁니다.

누구든 채널로 메시지를 보낼 수 있습니다. 들어오는 Slack 또는 SMS 메시지가 반드시 사용자로부터 온 것은 아니며: 스팸이거나 에이전트를 조종하기 위한 지시일 수 있습니다.
따라서 메시지는 한 턴이 되기 전에 게이트를 통과해야 합니다(헤르메스는 인증 전에 모든 들어오는 메시지에 대해 `pre_gateway_dispatch`를 실행합니다):

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

- 게이트는 루프가 텍스트를 보기 전에 (스팸, 알 수 없는 발신자) 제거하거나 (편집) 재작성할 수 있습니다.
- `None`를 반환하면 아예 턴이 발생하지 않으며, 쓰레기 입력에 대한 가장 저렴한 결과입니다.

### 통합 방식

데모는 서버를 발견하고 한 번의 에이전트 턴을 실행합니다. 모델은 MCP 도구를 블라인드로 호출합니다.

```python
reg = Registry()
for t in mcp.connect("kb", KBServer()):                # discover, wrap, merge
    reg.register(t)
run_turn([...goal...], model, reg, Session(mode=DEFAULT))   # the one agent call
```

- 모델은 내장 도구 옆에 도구 목록에서 `mcp__kb__search`를 보고 호출하며, 도구를 누가 만들었는지 절대 알지 못합니다.
- 도구는 읽기 전용이므로 게이트는 프롬프트 없이 허용합니다. 파괴적인 도구는 물어보거나, 정식 이름으로 키가 지정된 규칙에 의해 사전에 승인됩니다.
- 루프는 변경되지 않습니다. MCP는 도구를 풀에 추가하며, 그 이후 모든 것은 섹션 2의 디스패치와 섹션 3의 게이팅입니다.

### 추가 읽기

이 내용은 `src/`에는 없습니다. 이 내용은 ai-agent-book과 MCP 사양에서 나온 것이며, 표에 있는 시스템을 확인한 것은 아닙니다.

**세 가지 원시 요소, 한 개의 풀.** 서버는 세 가지 종류의 항목을 제공할 수 있습니다. 오직 도구만 위의 풀에 도달합니다.

- **도구(툴)** 은 작업입니다. 모델이 하나를 선택하고 호출합니다. `tools/list`가 이를 반환하며, 위의 코드는 이를 감쌉니다.
- **자원(리소스)** 은 클라이언트가 읽을 수 있는 데이터로, 각각 URI를 가집니다: 파일, 테이블, 위키 페이지. 클라이언트가 하나를 가져와 본문에 텍스트를 넣습니다. 모델은 절대 이를 호출하지 않습니다.
- **프롬프트** 는 서버가 제공하는 템플릿입니다. 보통 사용자 실행 명령으로 나타나며, 모델이 선택하는 것은 아닙니다.

**리소스는 도구 목록에 포함되지 않습니다.** Claude Code는 개별적으로 리소스를 광고하지 않습니다. 두 가지 도구를 제공합니다. 하나는 리소스를 나열하고, 다른 하나는 이를 읽는 도구입니다.
그러므로 천 개의 문서를 보관하는 서버라도 도구 목록에는 두 항목만 필요합니다.

**연결과 광고는 두 가지 다른 결정입니다.** 서버에 연결하면 상호 운용성을 얻습니다. 도구를 광고하면 문맥을 소비합니다.
첫 번째를 수행하면서 두 번째를 모두 수행하지 않아도 됩니다.

**광고의 비용.** 광고된 각 도구는 모든 요청에서 토큰을 소모합니다. 이름, 설명, 전체 입력 스키마가 모두 작업 앞에 위치합니다.
다섯 개의 서버는 작업 자체보다 더 많은 텍스트를 추가할 수 있습니다. 긴 목록은 모델이 잘못된 도구를 선택할 가능성도 높입니다 (섹션 2).

**선택할 수 있는 세 가지 수준.** 서버별로 광고할 정도를 결정하세요. 모든 서버에 대해 한 번에 결정하지 마세요:

- **모두.** 가장 간단합니다. 세션이 거의 모든 턴에서 사용하는 서버에 적합합니다.
- **색인.** 이름과 한 줄 요약을 광고합니다. 모델이 해당 도구를 요청할 때 전체 스키마를 불러옵니다(섹션 2에서 검색 측면을 다룹니다).
- **하나의 문.** 서버 이름과 도구 이름을 받는 한 가지 도구만 광고합니다. 나머지는 그 뒤에 남아 있습니다. 에이전트는 50개가 아닌 하나의 스키마만 비용을 지불합니다.

**이 모든 것은 프로토콜에 포함되지 않습니다.** 명세서는 도구를 나열하고 호출하는 방법을 설명합니다. 프롬프트에 몇 개가 도달할지는 클라이언트가 결정합니다.
따라서 지연 로딩은 자신의 harness에서 확인해야 하는 설정입니다. 서버가 자동으로 켜져 있다고 가정할 수 없습니다.

---

## 시스템별

harness가 자신을 벗어나 어떻게 도달하는가.

| | Claude Code | Hermes Agent | deepseek-harness |
| --- | --- | --- | --- |
| **장점** | 모든 서비스, 모든 언어, harness 수정 없음. | 다른 클라이언트가 MCP 서버로 구동 가능. | 서버는 설정이 가능하므로 재시작 없이 교체 가능. |
| **단점** | 새로운 공격 표면, 자체 보고된 주석 기반. | 누구나 채널에 스팸 또는 텍스트 조종 전송 가능. | 도구 전용이며, 메시지를 전달할 채팅 채널 없음. |
| **이유** | MCP 없이는 기능이 출시된 상태에서 고정됨. | 에이전트는 한 번에 MCP 클라이언트와 서버. | 모든 것이 plugin이므로 서버는 또 하나의 plugin. |
| **방법: 전송** | 여섯 개, stdio에서 원격 http까지, 별도의 풀에서. | MCP 양방향, 채팅 어댑터 포함. | 로컬 stdio와 스트리밍 http, 서버당 하나의 plugin. |
| **방법: plugin 형식** | plugin이 서버, hooks, skills를 묶고, 우선순위에 따라 병합. | 매니페스트와 레지스터 엔트리. | 설정 행. 패치는 ID로 행 하나를 교체. |
| **방법: 도구 풀 조립** | 복제 및 네임스페이스 적용; 주석이 게이트 제공. | Plugin과 MCP 도구가 하나의 레지스트리에 합류. | 서버의 도구가 한 세트로 교체되거나 롤백 가능. |

---

## 실패 모드

- **이름 충돌.** 두 서버가 모두 `search`를 노출합니다. `mcp__server__tool` 네임스페이스는 충돌을 방지합니다; `__`가 있는 서버 이름은 여전히 잘못 해석되므로 이름을 단순하게 유지하세요.
- **도구 목록 증가.** 많은 서버가 큰 도구 목록을 만들어 토큰을 소모하고 선택을 혼란스럽게 합니다(섹션 2).
  완화 방법: 설명을 축약하고 모든 요청마다 모든 스키마를 보내는 대신 서버마다 광고할 범위를 결정합니다.
- **연결 후 오래된 풀.** 세션 중간에 추가된 서버는 캐시된 도구 목록에 없으므로 모델이 절대 보지 못합니다.
  완화 방법: 변경 시 풀을 재구축하고 프롬프트를 표시합니다(섹션 8); 2026-07-28 사양은 `toolsListChanged`를 `subscriptions/listen` 및 `ttlMs` 힌트 위에 추가합니다.
- **연결 끊김(Churn).** 불안정한 서버가 시간 초과되거나, 재설정되거나, 토큰이 만료될 수 있습니다. 완화 방법: 반복 실패 후 재연결, `401`에서 재인증, 각 호출에 시간 초과 설정(섹션 11).
  상태 없는(statelss) 리비전은 스트림 이어서(resumability)를 제거하므로, 진행 중인 요청이 끊기면 재개되지 않고 새 요청으로 다시 전송됩니다.
- **과도하게 신뢰된 부작용.** 서버가 파괴적 도구 `readOnlyHint: true`를 프롬프트 생략 대상으로 표시합니다. 완화 방법: 자격 이름 규칙이 어쨌든 이를 제한합니다(섹션 3).
- **설명(Description) 변조(poisoning).** 도구 설명은 서버가 작성한 텍스트이며, 모델은 이를 명령으로 읽습니다.
  서버는 사용자 키 파일을 먼저 읽어 전송하라는 등의 명령을 숨길 수 있습니다. 모델이 이를 수행할 수도 있습니다.
  완화 방법: 서버를 설치하기 전에 설명서를 읽으세요. 설명서가 변경되면, 변경된 코드처럼 검토하세요.
- **도구 그림자 문제.** 모든 서버가 하나의 프롬프트를 공유합니다. 따라서 한 서버의 설명서가 다른 서버의 도구에 대해 언급할 수 있으며,
  결제 도구가 고장났다고 주장하고 호출을 자기 자신으로 돌릴 수도 있습니다.
  완화 방법: 네임스페이싱은 이름 충돌을 막습니다. 하지만 이것은 막지 못합니다. 검토되지 않은 서버는 실제 자격 증명을 가진 세션에 포함하지 마세요.
- **탈취된 업데이트.** 서버가 검토를 통과한 후, 다음 시작 시 새로운 코드와 새로운 설명서를 배포할 수 있습니다. 프로토콜은 사용자에게 다시 묻지 않습니다.
  완화 방법: 버전을 고정하세요. 업그레이드 후 설명서를 다시 읽으세요. 각 서버에 최소 권한 자격 증명을 부여하여 한 악성 서버가 다른 서버의 범위에 접근하지 못하도록 하세요.

---

## 실행 가능

[`src/`](src/)은 18개를 앞으로 전달하고 다음을 추가합니다:

- [`mcp.py`](src/mcp.py): 발견 및 래핑, plugin 구성 병합, 채널 래핑, 그리고 인바운드 게이트(`gate_inbound`).
- [`test.py`](src/test.py): 발견 및 네임스페이싱, 힌트 매핑, 게이트와 함께 풀 병합, 구성 우선순위, 채널 태그, 그리고 인바운드 드롭 및 재작성.
- [`demo.py`](src/demo.py): 하나의 에이전트 턴이 발견된 `mcp__kb__search`을 통해 인프로세스 MCP 도구를 블라인드 호출합니다.

루프와 디스패치는 변경되지 않습니다. MCP는 도구를 섹션-2 풀에 추가합니다; 섹션-3 게이트는 그들의 자기 선언 주석을 읽습니다.

```bash
python sections/19-mcp-plugins-channels/src/test.py         # offline checks, no key
uv run python sections/19-mcp-plugins-channels/src/demo.py  # live demo, needs a key
```

---

## 출처

- [Claude Code MCP 전송](https://github.com/yasasbanukaofficial/claude-code):
  `services/mcp/types.ts` (`TransportSchema`), `client.ts` (`MCPTool` 클로닝, `buildMcpToolName`), `normalization.ts` (`normalizeNameForMCP`).
- [Claude Code MCP 구성 및 채널](https://github.com/yasasbanukaofficial/claude-code):
  `config.ts` (우선순위), `channelNotification.ts` (`CHANNEL_TAG`), 추가로 `McpAuthTool`, `ListMcpResourcesTool`, `ReadMcpResourceTool`.
- [deepseek-harness 소스](https://github.com/deepseek-ai/deepseek-harness) 위치 `dsh-v0.1.0-rc.7`:
  `docs/architecture.md`, `docs/cordis-primer.md`, `packages/acp/acp/README.md`, `packages/extensions/tool-cordis/README.md`.
- [Claude Code plugins](https://github.com/yasasbanukaofficial/claude-code): `plugins/builtinPlugins.ts`, `plugins/bundled/`, `types/plugin.ts`, 그리고 `remote/`와 `bridge/`.
- [Hermes Agent 소스](https://github.com/NousResearch/hermes-agent):
  `mcp_serve.py`, `hermes_cli/plugins.py` (`PluginManager`, `VALID_HOOKS`), `gateway/platforms/`, `gateway/platform_registry.py`, `plugins/platforms/`.
- [MCP 사양 2026-07-28](https://modelcontextprotocol.io/specification/2026-07-28) 및 그
  [변경로그](https://modelcontextprotocol.io/specification/2026-07-28/changelog): 상태 비저장 프로토콜, 세 가지 프리미티브(도구, 자원, 프롬프트),
  `server/discover`, `subscriptions/listen`, MRTR, 사용 중단.
- MCP 블로그: [전송의 미래](https://blog.modelcontextprotocol.io/posts/2025-12-19-mcp-transport-future/) (왜 프로토콜이 상태 비저장이 되었는지)
  [2026-07-28용 SDK 베타](https://blog.modelcontextprotocol.io/posts/sdk-betas-2026-07-28/) (v2 SDK, 하위 호환성).
- [ai-agent-book](https://github.com/bojieli/ai-agent-book): `book/chapter4.md`, 중국어 원본 표준. 도구 생태계 섹션:
  MCP 프리미티브, 광고된 스키마의 컨텍스트 오버헤드, 그리고 신뢰 모델(설명 오염, 도구 그림자, 탈취된 업데이트, 자격 증명 범위).
- 프레이밍: [learn-claude-code · s19_mcp_plugin](https://github.com/shareAI-lab/learn-claude-code).
