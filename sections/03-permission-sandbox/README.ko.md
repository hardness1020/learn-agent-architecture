# 3 · Permission & sandbox

[English](README.md) · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · **한국어**

> 각 작업이 시스템에 도달하기 전에 확인하세요.

모델은 활성화된 도구를 실행하도록 요청할 수 있습니다. 권한 레이어가 그 호출이 실행될 수 있는지 결정합니다.

권한 없는 tool runtime은 거의 무인 원격 셸과 같습니다.

잘못된 도구 호출은 파일을 삭제하거나, 비밀을 유출하거나, 잘못된 코드를 푸시할 수 있습니다. 모델을 신뢰하는 것은 안전 경계가 아닙니다. 코드는 실행 전에 요청을 확인해야 합니다.

이유는 간단합니다. 모델은 다른 사람이 작성한 텍스트를 읽습니다. 웹 페이지, 이슈 댓글 또는 저장소의 파일이 에이전트를 대상으로 한 지침을 포함할 수 있습니다.
세 가지 기능이 그 지침이 무엇을 할 수 있는지 결정합니다. 에이전트는 비공개 데이터를 읽을 수 있습니다.
에이전트는 신뢰할 수 없는 콘텐츠를 받아들입니다. 에이전트는 데이터를 외부로 보낼 수 있습니다. 세 가지 중 두 가지만 만족해도 견딜 수 있습니다. 세 가지 모두 동시에 발생하면
주입된 텍스트가 에이전트에게 비밀을 열고 어디론가 게시하도록 지시할 수 있습니다. 이 조합을 치명적 삼중주라고 부릅니다.

영속적인 메모리가 상황을 악화시킵니다. 주입된 명령이 메모리 파일(섹션 9)에 들어가면, 다음 세션에서 다시 읽게 됩니다.
한 번 주입되면, 해당 주입을 담았던 세션이 끝난 후에도 계속 작동합니다.

게이트는 이 세 가지 능력을 제거할 수 없습니다. 아무 것도 읽지 않고 아무 것도 도달하지 않는 에이전트는 작동할 수 없습니다. 그래서 게이트는
다른 두 가지를 수행합니다. 삼위를 완성할 수 있는 호출 앞에 결정을 놓습니다. 허용된 호출 뒤에 sandbox를 놓습니다.

권한 레이어는 다음을 수행해야 합니다:

1. 각 도구 호출이 실행되기 전에 점검합니다.
2. `allow`, `ask`, 또는 `deny`을 결정합니다.
3. 위험한 호출이 사전 승인되지 않은 경우 사람에게 문의합니다.
4. 호출이 실행될 때 피해를 제한합니다.

이 계층이 없으면 하나의 잘못된 도구 호출이 회복할 수 없는 부작용을 초래할 수 있습니다.

---

## 메커니즘

![메커니즘 다이어그램](assets/03-permission-and-sandbox.png)

순수 함수가 권한 결정을 내립니다. 이 함수는 도구, 현재 모드 및 허용 규칙을 읽습니다. 그리고 세 가지 값 중 하나를 반환합니다:

- `allow`: 도구를 실행합니다.
- `ask`: 일시 중지하고 사람에게 문의합니다.
- `deny`: 도구를 실행하지 않습니다.

모드는 기본 동작을 변경합니다. 예를 들어, 계획 모드는 읽기 전용 도구를 허용하지만 계획이 승인될 때까지 편집을 거부합니다.

### 새로운 기능: 게이트

`decide()`는 전체 권한 결정을 나타냅니다:

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

이 함수는 입출력이 없습니다. 그래서 모드별로 테스트하기 쉽습니다.

### 통합 방식

게이트는 `_dispatch` 내부에서 실행되며, `run_tool` 바로 전에 실행됩니다:

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

- 루프 본문은 1섹션과 2섹션에서 변경되지 않았습니다.
- `_dispatch`만 게이트를 얻습니다.
- `deny`와 승인되지 않은 `ask`는 `run_tool`에 도달하지 않습니다.
- 거부는 여전히 `tool_result`로 반환되므로, 모델은 발생한 일을 보고 적응할 수 있습니다.
- `approver`는 기본적으로 `False`로 설정되므로, `ask`는 인간이 승인하지 않으면 '아니오'를 의미합니다.

핵심 불변성은 그대로 유지됩니다: 실제 동작이 실행되지 않더라도 모든 도구 호출은 결과 메시지를 생성합니다.

실제 시스템은 규칙 우선순위, 기억된 승인, 샌드박스 실행을 추가합니다. 이는 동일한 게이트의 확장입니다.

### 추가 읽기

이 모든 것은 `src/`에 없습니다. 이것은 ai-agent-book에서 나온 것이며, 표에 있는 시스템들에 대한 확인은 되지 않았습니다.

**명령어가 어떻게 작성되었는지가 아니라 무엇을 하는지 확인하기.** 에이전트가 쉘 명령어 실행을 요청합니다. `decide()`에서는 하나의 도구 이름만 봅니다.
그래서 실제 결정은 명령어 문자열에 관한 것입니다. 문자열의 차단 목록이 일반적인 답이며, 이는 실패합니다. `rm -rf /`는 쉽게 포착됩니다.
다음과 같은 것들은 통과됩니다:

- `find . -exec rm {} \;`는 삭제를 플래그 안에 넣습니다.
- `$(echo rm) -rf /`는 셸이 실행되는 동안 `rm`라는 단어를 생성합니다.
- `curl -o /etc/crontab`는 쓰기 명령을 지정하지 않고 파일을 작성합니다.

수정 방법은 텍스트 대신 구조를 읽는 파서입니다. 이 파서는 명령을 프로그램과 인수로 나눕니다. 어떤 플래그가 필요한지 알고 있습니다.
값을 가지므로 플래그와 인수를 구분할 수 있습니다. 그런 다음 각 프로그램이 무엇을 할지 묻습니다. `-exec`는 자체 명령을 가지고 있으므로 그 명령
또한 확인됩니다. `-o`는 쓸 파일의 이름을 지정하므로, 그 경로도 쓰기로 확인됩니다.

비용은 파서가 프로그램마다 규칙이 필요하다는 것입니다. 알지 못하는 프로그램은 읽을 수 없는 프로그램이므로 sandbox는 여전히 그 뒤에 남아 있습니다.

**여전히 통과할 수 있는 파괴적인 지름길을 차단하기.** 손상된 테이블을 고치는 방법은 두 가지가 있으며, 둘 다 결국 작동하는 테이블로 끝납니다.
하나는 테이블을 마이그레이션하는 것이고, 다른 하나는 삭제 후 처음부터 다시 구축하는 것입니다. 결과 확인(섹션 21)은 둘 다 통과합니다. 왜냐하면 이 확인은 최종 상태만 보기 때문입니다.

수정 방법은 목적지만 게이트로 막는 것이 아니라 경로까지 막는 것입니다. 삭제 후 재구축은, 재구축된 테이블이 올바를지라도 여전히 차단됩니다.
비용은, 실제로 올바른 수정인 재구축이 이제 사람의 승인을 필요로 한다는 것입니다.

**sandbox가 제한하는 것.** 게이트는 틀릴 수 있습니다. sandbox가 틀린 `allow`가 큰 비용을 발생시키지 않도록 막습니다. 세 가지 제한이 대부분의 작업을 수행합니다.

- **이그레스(Egress).** 기본적으로 네트워크를 차단합니다. 허용된 트래픽만 허용된 호스트 목록을 가진 프록시를 통해 전송합니다.
  이것은 삼위일체를 줄이는 데 가장 저렴한 방법입니다. 에이전트는 여전히 코드를 읽고 파일을 작성할 수 있습니다. 단지 어디에도 보낼 수 없을 뿐입니다.
- **마운트(Mounts).** 소스를 읽기 전용으로 마운트합니다. 자격 증명 파일은 절대 마운트하지 마세요. 쓰기 가능한 작업 디렉터리 하나만 제공하고 그 외에는 아무것도 제공하지 않습니다.
  에이전트는 열 수 없는 파일을 유출할 수 없습니다.
- **쿼터(Quotas).** CPU, 메모리, 디스크, 실제 시간에 대한 제한을 설정합니다. 제한에 도달하면 tool result와 같은 오류를 반환합니다.
  프로세스를 조용히 종료하지 마세요. 모델은 타임아웃을 읽고 더 짧은 명령을 시도할 수 있습니다. 조용한 종료는 읽을 것을 제공하지 않습니다.

**사용자가 두 번 기다리지 않도록 묻기.** 게이트는 `ask`을 반환하고, 사용자는 현재 기다리고 있습니다. 만약 체크 자체가 느리다면,
프롬프트가 나타나기 전에 이미 한 번 기다린 것입니다. 추측 체크(speculative check)는 그 첫 번째 기다림을 제거합니다. 순서는 다음과 같습니다:

- harness가 백그라운드에서 권한 체크를 시작합니다.
- 화면은 즉시 진행 상태(progress line)를 표시합니다. 이 줄은 시스템에는 아무런 영향을 주지 않습니다.
- 체크가 진행 중일 때 `allow`을 반환하면, 도구는 실행되고 프롬프트는 나타나지 않습니다.
- 체크가 아직 결정되지 않은 경우, 줄은 확인(confirm) 프롬프트로 바뀝니다.

이 방식이 여전히 안전한 이유: 초기에서 실행되는 유일한 것은 체크뿐입니다. 도구 자체는 여전히 답변을 기다립니다.

---

## 시스템별

각 에이전트가 부작용을 어떻게 제어하고, 모드를 변경하며, 결정을 기억하는지.

| | Claude Code | mini-swe-agent | deepseek-harness |
| --- | --- | --- | --- |
| **장점** | 모드, 순서화된 규칙, 샌드박싱이 세밀한 제어를 제공함. | 몇 분 안에 감사 가능. 거부는 모델에 피드백됨. | 거부는 절대 완화되지 않음; 샌드박싱은 닫힌 상태에서 실패함. |
| **단점** | 상태가 많음. 우회 및 사전승인 경로는 좁게 유지해야 함. | 모든 명령을 동일하게 처리하며, 아무 것도 기억하지 않음. | 정책이 가드, 승인, sandbox, 프리셋에 걸쳐 있음. |
| **이유** | 매번 묻는 것은 피로를 유발하므로 승인이 지속됨. | 프롬프트와 정규식; 환경이 피해를 제한함. | 각 관심사는 자체적인 닫힌 실패 서비스임. |
| **방법: 게이트 포인트** | 각 도구 실행 전에; 웹, MCP, 원격 게이트를 별도로. | 단계가 실행되기 전에. 승인 입력, 댓글은 거부. | 사전 실행 이벤트, 그다음 거부 전용 가드. |
| **방법: 권한 모드** | 기본, 편집 승인, 계획, 거부, 우회. | `human`, `confirm`, `yolo`, runtime에서 전환됨. | Sandbox 모드에 묻기 또는 절대 없음, 프리셋으로 묶음. |
| **방법: sandbox** | Bash는 sandbox 내부에서 실행 가능. | 환경 클래스는 sandbox: 호스트, 컨테이너, 래퍼. | 제공자는 각 argv를 래핑; 거부는 분류되어 반환. |
| **방법: 규칙 지속** | 규칙은 우선순위에 따라 세션이나 설정으로 병합됩니다. | 정규표현식 구성; 일치하면 프롬프트를 건너뜁니다. | 컨트롤 변경은 로그 이벤트이며; 재생 시 정책이 적용됩니다. |

---

## 실패 모드

- **패턴 매치 우회.** 문자열 거부 목록은 쉘 변형을 놓칠 수 있습니다. 명령을 파싱하고 실제로 무엇을 수행할지 확인하세요. 파서 뒤에 sandbox를 유지하세요.
- **모드가 너무 열려 있음.** 광범위한 허용 규칙이나 우회 모드는 이후 위험한 호출이 조용히 실행되는 것을 허용할 수 있습니다. 우회를 범위화하고 활성 모드를 표시하세요.
- **승인 피로.** 모든 호출마다 요청하면 사용자가 읽지 않고 승인하도록 훈련됩니다. 저위험 클래스는 사전 승인하되, 파괴적 행동은 명시적으로 유지하세요.
- **subagent에서 조용한 거부.** 자식 에이전트는 요청할 터미널이 없을 수 있습니다. 조용히 실패하는 대신 프롬프트를 부모로 전달하세요.
- **Sandbox 비활성화됨.** 허용된 명령이 sandbox 외부에서 실행되면, 권한 프롬프트가 마지막 확인 단계입니다. 정책 뒤에 모든 비샌드박스 경로를 차단하세요.
- **승인된 호출을 통한 유출.** 각 호출은 자체적으로 게이트를 통과할 수 있으며, 세션 전체는 여전히 비밀을 읽고 전송합니다.
  기본적으로 네트워크를 차단하여 세 번째 기능이 사용되지 않도록 합니다.
- **검증되었지만 파괴적임.** 삭제하고 재구성하는 작업은 결과 검사를 통과합니다. 최종 상태가 올바르기 때문입니다. 동작을 확인하고, 단순히 최종 상태만 보지 마세요.
- **오염된 메모리.** 메모리 파일에 주입된 명령어는 모든 이후 세션에서 다시 읽힙니다. 저장된 메모리를 신뢰할 수 없는 콘텐츠로 취급하고, 운영자 규칙으로 간주하지 마세요.

---

## 실행 가능

[`src/`](src/)는 02를 앞으로 가져가고 다음을 추가합니다:

- [`permissions.py`](src/permissions.py): `decide`를 네 가지 모드에서 수행합니다.
- [`loop.py`](src/loop.py): 실행하기 전에 `_dispatch`의 각 호출을 게이트합니다.

```bash
python sections/03-permission-sandbox/src/test.py         # offline checks, no key
uv run python sections/03-permission-sandbox/src/demo.py  # live demo, needs a key
```

---

## 출처

- [Claude Code 소스](https://github.com/yasasbanukaofficial/claude-code):
  `QueryEngine.ts`, `hooks/useCanUseTool.tsx`, `types/permissions.ts`, `utils/permissions/PermissionUpdate.ts`.
- [Claude Code sandbox 및 웹 게이트](https://github.com/yasasbanukaofficial/claude-code): `tools/BashTool/shouldUseSandbox.ts`, `tools/WebFetchTool/preapproved.ts`.
- [mini-swe-agent 소스](https://github.com/swe-agent/mini-swe-agent): `agents/interactive.py`, `environments/docker.py`, `environments/extra/bubblewrap.py`.
- [deepseek-harness 소스](https://github.com/deepseek-ai/deepseek-harness)에서 `dsh-v0.1.0-rc.7`:
  `docs/subsystems/tools.md`, `docs/subsystems/approval.md`, `docs/subsystems/sandbox.md`, `docs/subsystems/permission-presets.md`,
  `packages/sandbox/sandbox-local/README.md`, `packages/shell/bash-sandbox/README.md`.
- [ai-agent-book · chapter 5](https://github.com/bojieli/ai-agent-book/blob/main/book/chapter5.md) (《深入理解 AI Agent》, 李博杰; 중국어 원본이 권위 있음):
  메모리 증폭 축, sandbox 출구, 마운트 및 할당 정책, 의미 명령 구문 분석, 추측적 권한 검사,
  그리고 결과만이 아니라 경로를 제한하는 것. 이러한 설계에 대한 단일 소스.
- [AI agents의 치명적 삼합](https://simonwillison.net/2025/Jun/16/the-lethal-trifecta/) (Simon Willison):
  개인 데이터 접근, 신뢰할 수 없는 콘텐츠, 외부 통신이라는 세 가지 기능이 결합되어서는 안 됨.
- [learn-claude-code · s03_permission](https://github.com/shareAI-lab/learn-claude-code): 섹션 프레이밍.
