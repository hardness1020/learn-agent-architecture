# 6 · Subagent

[English](README.md) · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · **한국어**

> 좁게 집중한 child loop를 돌리고 그 결과만 돌려받습니다.

메인 agent는 작업을 subagent에게 넘길 수 있습니다. 넘기는 쪽이 parent이고, 내보내진 쪽이 child입니다.

parent 입장에서 이것은 tool call 하나일 뿐입니다. 그런데 그 호출 안에서는 완전한 agent loop가 돕니다.
parent가 child에게 prompt를 줍니다. child는 새 `messages[]`를 받아 끝까지 실행하고, 최종 답변을 돌려줍니다.

이렇게 하면 곁가지 조사가 parent context 밖에 머뭅니다. parent는 child가 읽은 파일이나 실행한 명령의 결과를 전부 알 필요가 없습니다. 대개 필요한 것은 결론입니다.

subagent가 없으면 모든 조사가 메인 transcript에 남습니다. 긴 실행은 시끄럽고 비싸지며, 모델이 따라가기도 어려워집니다.

---

## 메커니즘

![Mechanism diagram](assets/06-subagents.png)

`Agent` tool이 child agent를 시작합니다. child는 자기 session과 메시지 목록을 가집니다. parent와 같은 loop를 돕니다.

돌아오는 것은 child의 최종 텍스트뿐입니다. child의 transcript는 버려집니다. 파일 쓰기와 shell 부수 효과는 작업 디렉터리에 그대로 남습니다.

### 새로 추가: Agent tool

```python
def agent_tool(model, child_registry, parent_session):     # src/subagents.py
    def spawn(a):
        child = Session(mode=parent_session.mode,          # fresh context, inherited authority
                        allow_rules=set(parent_session.allow_rules))
        messages = [{"role": "user", "content": a["description"]}]   # the child's own conversation
        return run_turn(messages, model, child_registry, child)      # the loop, run again
    return Tool("Agent", spawn, is_read_only=True)
```

- `agent_tool`은 평범한 tool을 돌려줍니다.
- 그 핸들러는 새 `Session`을 만들어 `run_turn()`을 호출합니다.
- child의 `messages[]`는 child prompt 하나로만 시작합니다.
- child는 `run_turn()`이 돌려주는 텍스트를 반환합니다.

### 통합 방식

loop는 바뀌지 않습니다. subagent는 loop를 호출하는 또 하나의 tool 핸들러일 뿐입니다.

중요한 성질이 세 가지 있습니다.

- **새 context.** child는 parent의 transcript를 물려받지 않습니다. parent도 child의 실행 기록을 물려받지 않습니다.
- **권한 상속.** child는 parent의 permission mode와 허용 규칙을 복사합니다. context를 분리한다고 permission이 분리되지는 않습니다.
- **재귀 제한.** 데모는 child registry에서 `Agent`를 빼 두었으므로, child는 또 다른 child를 띄울 수 없습니다.

---

## 시스템별

각 agent가 하위 문제를 분리하고 결과를 돌려주는 방식입니다.

| | Claude Code | deepseek-harness |
| --- | --- | --- |
| **장점** | child context 덕분에 parent는 집중을 유지하고 메인 transcript는 깨끗함. | 하나의 이음매가 프로세스 내 child, 외부 런타임, 제품 CLI를 모두 아우름. |
| **단점** | parent는 child가 거쳐 온 과정을 잃음. 요약이 얇으면 다시 물어봐야 함. | tool 하나면 될 일에 백엔드 여섯 개와 재개 관리자가 붙음. |
| **이유** | parent에게 필요한 것은 결론이지, child가 읽은 파일 전부가 아님. | 위임은 전송 방식의 선택이므로, 백엔드마다 이름을 걸고 등록함. |
| **방법: spawn primitive** | `Agent` tool. subagent 종류가 내장 페르소나를 고름. | 등록된 백엔드마다 tool 하나. 새 child, fork, 외부 런타임, CLI. |
| **방법: context isolation** | child 메시지를 새로 시작함. fork로 만든 child는 다시 fork할 수 없음. | 새 child는 빈 상태로 시작함. fork는 parent의 끝난 turn만 복사함. |
| **방법: result return** | child의 마지막 메시지 텍스트가 돌아감. transcript는 버려짐. | 마지막 assistant 메시지, 그리고 스키마로 검사한 선택적 출력. |
| **방법: resume** | 대부분의 agent가 재개 가능. parent가 후속 메시지를 보냄. | 지속형 child는 후속 요청을 큐에 쌓고, 재시작 후 로그에서 다시 읽어 들임. |

---

## 실패 모드

- **손실이 큰 요약.** child가 지나치게 압축할 수 있습니다. 중요한 발견은 디스크에 쓰라고 지시합니다.
- **폭주하는 재귀.** child가 child를 띄우면 끝없이 불어날 수 있습니다. child registry에서 `Agent` tool을 빼거나 깊이 제한을 겁니다.
- **child에 정지 조건이 없음.** child도 parent와 같은 정지 실패 위험을 가집니다. child마다 자체 turn 한도나 token 한도를 줍니다.
- **permission이 분리되었다는 착각.** child에도 평범한 permission gate가 필요합니다. context가 분리되었다고 건너뛰지 않습니다.
- **버려진 비동기 child.** 백그라운드 child는 parent가 다음 일로 넘어간 뒤에 끝날 수 있습니다. task 레코드로 추적합니다.

---

## 실행 방법

[`src/`](src/)는 05를 이어받아 다음을 추가합니다.

- [`subagents.py`](src/subagents.py): `Agent` tool.
- [`loop.py`](src/loop.py): 섹션 5에서 바뀌지 않았습니다.
- [`demo.py`](src/demo.py): parent가 세는 작업을 child에게 위임합니다.
- [`test.py`](src/test.py): 새 context, 권한 상속, 재귀 차단을 확인합니다.

```bash
python sections/06-subagents/src/test.py         # offline checks, no key
uv run python sections/06-subagents/src/demo.py  # live demo, needs a key
```

---

## 출처

- [Claude Code 소스](https://github.com/yasasbanukaofficial/claude-code):
  `tools/AgentTool/AgentTool.tsx`, `runAgent.ts`, `resumeAgent.ts`, `forkSubagent.ts`, `builtInAgents.ts`, `tasks/LocalAgentTask/`.
- [deepseek-harness 소스](https://github.com/deepseek-ai/deepseek-harness), `dsh-v0.1.0-rc.7` 기준:
  `packages/subagent/subagent/src/index.ts`, `src/continuation.ts`, `packages/subagent/subagent-fork-in-process/README.md`,
  `packages/subagent/subagent-acp/README.md`, `docs/subsystems/subagent.md`, `docs/tool-catalog.md`.
- [learn-claude-code · s06_subagent](https://github.com/shareAI-lab/learn-claude-code): 섹션 구성 참고.
