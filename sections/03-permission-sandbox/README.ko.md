# 3 · Permission & sandbox

[English](README.md) · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · **한국어**

> 각 행동이 시스템에 닿기 전에 검사합니다.

모델은 켜져 있는 tool이라면 무엇이든 실행해 달라고 요청할 수 있습니다. permission 계층은 그 호출을 실행해도 되는지 결정합니다.

permission이 없는 tool runtime은 지켜보는 사람 없는 원격 shell에 가깝습니다.

잘못된 tool call 하나가 파일을 지우고, 비밀을 흘리고, 엉뚱한 코드를 push할 수 있습니다. 모델을 믿는 것은 안전 경계가 아닙니다. 실행 전에 코드가 요청을 검사해야 합니다.

이유는 단순합니다. 모델은 다른 사람이 쓴 텍스트를 읽습니다. 웹 페이지, 이슈 댓글, 저장소 안의 파일이 agent를 겨냥한
지시를 담고 있을 수 있습니다. 그 지시가 무엇을 할 수 있는지는 능력 셋이 결정합니다. agent는 사적인 데이터를 읽을 수 있습니다.
agent는 신뢰할 수 없는 내용을 받아들입니다. agent는 데이터를 밖으로 보낼 수 있습니다. 셋 중 둘까지는 버틸 수 있습니다. 셋이 한꺼번에 모이면
주입된 텍스트가 agent에게 비밀을 열어 어딘가에 올리라고 시킬 수 있습니다. 이 조합을 lethal trifecta라고 부릅니다.

지속되는 memory는 상황을 더 나쁘게 만듭니다. 주입된 지시가 memory 파일에 들어가면(섹션 9) 다음 session이 그것을 다시 읽습니다.
그러면 주입 한 번이 그것을 실어 나른 session이 끝난 뒤로도 오래 작동합니다.

gate는 그 세 능력을 빼앗지는 못합니다. 아무것도 읽지 않고 아무 데도 닿지 못하는 agent는 일을 할 수 없습니다. 그래서 gate는
다른 두 가지를 합니다. 세 능력을 완성시킬 호출 앞에 결정을 놓습니다. 허용한 호출 뒤에는 sandbox를 놓습니다.

permission 계층은 다음을 해야 합니다.

1. 실행 전에 각 tool call을 살펴봅니다.
2. `allow`, `ask`, `deny` 중 하나를 결정합니다.
3. 위험한 호출이 미리 승인되어 있지 않으면 사람에게 묻습니다.
4. 호출이 실제로 실행될 때 피해를 제한합니다.

이 계층이 없으면 잘못된 tool call 하나가 되돌릴 수 없는 부수 효과를 낳을 수 있습니다.

---

## 메커니즘

![Mechanism diagram](assets/03-permission-and-sandbox.png)

permission 결정은 순수 함수가 내립니다. 이 함수는 tool, 현재 mode, 그리고 허용 규칙을 읽습니다. 그리고 세 값 중 하나를 반환합니다.

- `allow`: tool을 실행합니다.
- `ask`: 멈추고 사람에게 묻습니다.
- `deny`: tool을 실행하지 않습니다.

mode는 기본 동작을 바꿉니다. 예를 들어 plan mode는 읽기 전용 tool은 허용하지만, 계획이 승인될 때까지 편집은 거부합니다.

### 새로 더하는 것: gate

`decide()`가 permission 결정의 전부입니다.

```python
def decide(tool, mode, allow_rules) -> str:      # src/permissions.py (new)
    if mode == BYPASS:                            # operator opted out
        return "allow"
    if mode == PLAN:                              # exploring, not acting yet
        if tool.is_read_only:           return "allow"
        if tool.name == "ExitPlanMode": return "ask"     # approval handshake (section 5)
        return "deny"                             # no side effects until approved
    if tool.is_read_only or tool.name in allow_rules:
        return "allow"
    if mode == ACCEPT_EDITS and tool.is_edit:
        return "allow"                            # a class of work pre-approved
    return "ask"                                  # default: when unsure, ask
```

이 함수에는 I/O가 없습니다. 그래서 mode별로 테스트하기 쉽습니다.

### 통합 방식

gate는 `_dispatch` 안, `run_tool` 바로 앞에서 실행됩니다.

```python
def _dispatch(block, registry, mode, allow_rules, approver):   # src/loop.py
    ...                                                  # resolve tool (section 2)
    decision = decide(tool, mode, allow_rules)           # 3 · the gate, the new line
    if decision == "deny":
        return res(f"{name} not allowed in {mode} mode")
    if decision == "ask" and not approver(name, block.input):
        return res(f"{name} denied by user")
    return res(run_tool(tool, block.input))              # only now does it run
```

- loop 본문은 섹션 1, 2에서 그대로입니다.
- `_dispatch`만 gate를 얻습니다.
- `deny`와 승인되지 않은 `ask`는 절대 `run_tool`까지 가지 않습니다.
- 거부도 `tool_result`로 돌아가므로, 모델은 무슨 일이 있었는지 보고 대응할 수 있습니다.
- `approver`의 기본값은 `False`이므로, `ask`는 사람이 승인하지 않는 한 거부를 뜻합니다.

핵심 불변식은 그대로입니다: 모든 tool call은 결과 메시지를 만들며, 실제 행동이 실행되지 않았을 때도 그렇습니다.

실제 시스템은 규칙 우선순위, 기억되는 승인, sandbox 안에서의 실행을 더합니다. 모두 같은 gate의 확장입니다.

### 더 읽을거리

이 내용은 `src/`에 없습니다. ai-agent-book에서 온 것이며, 표에 있는 시스템들에서 확인된 내용은 아닙니다.

**명령의 철자가 아니라 하는 일을 검사하기.** agent가 shell 명령을 실행해 달라고 요청합니다. `decide()`는 tool 이름 하나만 보므로,
실제 결정은 명령 문자열에 대한 것입니다. 흔한 답은 문자열 거부 목록이고, 그것은 실패합니다. `rm -rf /`는 잡기 쉽습니다.
다음은 통과합니다.

- `find . -exec rm {} \;` 형태는 삭제를 플래그 안에 넣습니다.
- `$(echo rm) -rf /` 형태는 shell이 도는 동안 `rm`이라는 단어를 만들어 냅니다.
- `curl -o /etc/crontab` 형태는 쓰기 명령을 이름으로 대지 않은 채 파일을 씁니다.

해법은 텍스트가 아니라 구조를 읽는 파서입니다. 파서는 명령을 프로그램과 인자로 나눕니다. 어떤 플래그가 값을 받는지 알기 때문에
인자와 플래그를 구분할 수 있습니다. 그런 다음 각 프로그램이 무엇을 할지 따집니다. `-exec`는 자기 명령을 품고 있으므로 그 명령도
함께 검사합니다. `-o`는 쓸 파일을 지정하므로 그 경로를 쓰기로 보고 검사합니다.

비용은 파서가 프로그램마다 규칙을 필요로 한다는 점입니다. 파서가 모르는 프로그램은 읽지 못하는 프로그램이므로, sandbox는 여전히 그 뒤에 있어야 합니다.

**검사를 통과해 버리는 파괴적 지름길 막기.** 망가진 테이블을 고치는 길은 둘이고, 둘 다 정상 테이블로 끝납니다.
하나는 테이블을 마이그레이션합니다. 다른 하나는 테이블을 지우고 처음부터 다시 만듭니다. 결과 검사(섹션 21)는 최종 상태만 보므로 둘 다 통과시킵니다.

해법은 목적지만이 아니라 경로를 통제하는 것입니다. 다시 만든 테이블이 옳더라도 지우고 다시 만들기는 계속 막힙니다.
비용은 정말로 다시 만드는 것이 옳은 처방일 때 이제 사람의 승인이 필요해진다는 점입니다.

**sandbox가 제한하는 것.** gate는 틀릴 수 있습니다. 잘못된 `allow`의 대가가 커지지 않게 막아 주는 것이 sandbox입니다. 제한 셋이 일의 대부분을 합니다.

- **바깥으로 나가는 통신.** 네트워크는 기본으로 막습니다. 허용된 트래픽은 허용 호스트 목록을 가진 프록시로 보냅니다.
  trifecta에서 잘라 내기 가장 싼 갈래가 이것입니다. agent는 여전히 코드를 읽고 파일도 씁니다. 다만 그것을 어디로도 보내지 못합니다.
- **마운트.** 소스는 읽기 전용으로 마운트합니다. 자격 증명 파일은 아예 마운트하지 않습니다. 쓸 수 있는 작업 디렉터리 하나만 주고 그 밖에는 주지 않습니다.
  agent는 열 수 없는 파일을 흘릴 수 없습니다.
- **할당량.** CPU, 메모리, 디스크, 실제 경과 시간에 한도를 겁니다. 한도에 걸리면 tool 결과로 오류를 반환합니다.
  프로세스를 조용히 종료하지는 않습니다. 모델은 timeout을 읽고 더 짧은 명령을 시도할 수 있습니다. 조용한 종료는 모델에 읽을 것을 아무것도 주지 않습니다.

**사용자를 두 번 기다리게 하지 않고 묻기.** gate가 `ask`를 반환하면 사용자는 이제 기다립니다. 검사 자체가 느렸다면,
사용자는 확인 창이 뜨기도 전에 이미 한 번 기다린 것입니다. 예측 검사는 그 첫 번째 기다림을 없앱니다. 순서는 이렇습니다.

- harness가 permission 검사를 백그라운드에서 시작합니다.
- 화면에는 진행 표시 줄이 곧바로 나옵니다. 그 줄은 시스템의 아무것도 바꾸지 않습니다.
- 그 줄이 떠 있는 동안 검사가 `allow`를 반환하면, tool이 실행되고 확인 창은 나오지 않습니다.
- 검사가 아직 결론이 나지 않았으면, 그 줄이 확인 창으로 바뀝니다.

그래도 안전한 이유는 이렇습니다: 미리 실행되는 것은 검사뿐입니다. tool 자체는 여전히 답을 기다립니다.

---

## 시스템별

각 agent가 부수 효과를 어떻게 통제하고, mode를 어떻게 바꾸고, 결정을 어떻게 기억하는지 봅니다.

| | Claude Code | mini-swe-agent | deepseek-harness |
| --- | --- | --- | --- |
| **장점** | mode, 순서가 있는 규칙, sandbox로 세밀하게 제어함. | 몇 분이면 감사 가능. 거부는 모델에 되먹여짐. | 거부는 절대 느슨해지지 않고, sandbox는 실패하면 닫힘. |
| **단점** | 상태가 많음. bypass와 사전 승인 경로를 좁게 유지해야 함. | 모든 명령을 똑같이 다루고, 아무것도 기억하지 않음. | 정책이 guard, 승인, sandbox, preset에 걸쳐 있음. |
| **이유** | 매번 물으면 피로해지므로 승인을 지속시킴. | 확인 창 하나와 정규식 몇 개. 피해는 환경이 제한함. | 관심사마다 실패하면 닫히는 별도 서비스. |
| **방법: gate 지점** | tool마다 그 앞에서. web, MCP, 원격은 따로 통제. | 단계가 실행되기 전. Enter는 승인, 댓글은 거부. | 실행 직전 이벤트, 그다음 거부만 하는 guard. |
| **방법: permission mode** | 기본, 편집 승인, plan, 거부, bypass. | `human`, `confirm`, `yolo`, 실행 중 전환. | sandbox mode에 ask 또는 never를 더해 preset으로 묶음. |
| **방법: sandbox** | bash를 sandbox 안에서 실행할 수 있음. | 환경 클래스 자체가 sandbox. 호스트, 컨테이너, wrapper. | 공급자가 각 argv를 감싸고, 거부는 분류되어 돌아옴. |
| **방법: 규칙 지속** | 규칙이 우선순위에 따라 session이나 설정으로 합쳐짐. | 설정의 정규식. 일치하면 확인 창을 건너뜀. | 설정 변경은 log 이벤트. 재생하면 정책이 접힘. |

---

## 실패 모드

- **패턴 일치 우회.** 문자열 거부 목록은 shell의 변형을 놓칩니다. 명령을 파싱해서 실제로 무엇을 할지 검사합니다. 파서 뒤에는 sandbox를 둡니다.
- **너무 열어 둔 mode.** 넓은 허용 규칙이나 bypass mode는 이후의 위험한 호출을 조용히 실행시킬 수 있습니다. bypass의 범위를 좁히고 지금 mode를 드러냅니다.
- **승인 피로.** 호출마다 물으면 사용자는 읽지 않고 승인하는 데 익숙해집니다. 위험이 낮은 부류는 미리 승인하되, 파괴적인 행동은 명시적으로 남깁니다.
- **subagent에서의 조용한 거부.** child agent에는 물어볼 터미널이 없을 수 있습니다. 조용히 실패하는 대신 확인 요청을 부모로 올립니다.
- **sandbox 꺼짐.** 허용된 명령이 sandbox 밖에서 실행되면 permission 확인 창이 마지막 검사입니다. sandbox를 쓰지 않는 경로는 정책으로 통제합니다.
- **승인된 호출을 통한 유출.** 호출은 하나하나 gate를 통과할 수 있고, 그래도 session 전체로 보면 비밀을 읽어 밖으로 보냅니다.
  네트워크를 기본으로 막아서 세 번째 능력이 애초에 쓸 수 없게 만듭니다.
- **검증은 통과하지만 파괴적임.** 지우고 다시 만들기는 최종 상태가 맞으므로 결과 검사를 통과합니다. 최종 상태만이 아니라 행동을 검사합니다.
- **오염된 memory.** memory 파일에 주입된 지시는 이후 모든 session에서 다시 읽힙니다. 저장된 memory는 운영자 규칙이 아니라 신뢰할 수 없는 내용으로 다룹니다.

---

## 실행 방법

[`src/`](src/)는 02를 이어받아 다음을 더합니다.

- [`permissions.py`](src/permissions.py): 네 가지 mode에 걸친 `decide`.
- [`loop.py`](src/loop.py): `_dispatch`에서 각 호출을 실행하기 전에 통제합니다.

```bash
python sections/03-permission-sandbox/src/test.py         # offline checks, no key
uv run python sections/03-permission-sandbox/src/demo.py  # live demo, needs a key
```

---

## 출처

- [Claude Code source](https://github.com/yasasbanukaofficial/claude-code):
  `QueryEngine.ts`, `hooks/useCanUseTool.tsx`, `types/permissions.ts`, `utils/permissions/PermissionUpdate.ts`.
- [Claude Code sandbox and web gates](https://github.com/yasasbanukaofficial/claude-code): `tools/BashTool/shouldUseSandbox.ts`, `tools/WebFetchTool/preapproved.ts`.
- [mini-swe-agent source](https://github.com/swe-agent/mini-swe-agent): `agents/interactive.py`, `environments/docker.py`, `environments/extra/bubblewrap.py`.
- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness) `dsh-v0.1.0-rc.7`:
  `docs/subsystems/tools.md`, `docs/subsystems/approval.md`, `docs/subsystems/sandbox.md`, `docs/subsystems/permission-presets.md`,
  `packages/sandbox/sandbox-local/README.md`, `packages/shell/bash-sandbox/README.md`.
- [ai-agent-book · chapter 5](https://github.com/bojieli/ai-agent-book/blob/main/book/chapter5.md) (《深入理解 AI Agent》, 李博杰. 중국어 원문이 기준):
  memory가 피해를 키우는 축, sandbox의 외부 통신, 마운트와 할당량 정책, 의미 기반 명령 파싱, 예측 permission 검사,
  그리고 결과만이 아니라 경로를 제약하기. 이 설계들의 단일 출처입니다.
- [The lethal trifecta for AI agents](https://simonwillison.net/2025/Jun/16/the-lethal-trifecta/) (Simon Willison):
  사적인 데이터 접근, 신뢰할 수 없는 내용, 외부 통신. 이 셋은 결합되어서는 안 되는 능력입니다.
- [learn-claude-code · s03_permission](https://github.com/shareAI-lab/learn-claude-code): 섹션 구성.
