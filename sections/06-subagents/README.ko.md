# 6 · Subagents

[English](README.md) · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · **한국어**

> 집중된 자식 루프를 실행하고 그 결과만 반환합니다.

주요 에이전트는 subagent에게 작업을 전달할 수 있습니다: 위임하는 쪽이 부모이고, 보내지는 쪽이 자식입니다.

부모에게 이것은 단지 하나의 도구 호출일 뿐입니다. 하지만 그 호출 내부에서는 완전한 agent loop가 실행됩니다.
부모는 자식에게 프롬프트를 제공합니다. 자식은 새 `messages[]`를 받아 완료까지 실행하고 최종 답변을 반환합니다.

이것은 부수 조사들을 부모 컨텍스트에서 제외합니다. 부모는 자식의 모든 파일 읽기나 명령 결과를 필요로 하지 않습니다. 보통 필요한 것은 결론뿐입니다.

subagents 없이는 모든 조사가 메인 기록에 남아 있습니다. 긴 실행은 시끄럽고, 비용이 많이 들며, 모델이 따라가기 어려워집니다.

---

## 메커니즘

![메커니즘 다이어그램](assets/06-subagents.png)

`Agent` 도구는 하위 에이전트를 시작합니다. 하위 에이전트는 자체 세션과 메시지 목록을 가지고 있습니다. 부모와 동일한 루프를 실행합니다.

하위 에이전트의 최종 텍스트만 반환됩니다. 그 전사 기록은 폐기됩니다. 파일 쓰기 및 셸 부작용은 여전히 작업 디렉터리에서 발생합니다.

### 새로움: 에이전트 도구

```python
def agent_tool(model, child_registry, parent_session):     # src/subagents.py
    def spawn(a):
        child = Session(mode=parent_session.mode,          # fresh context, inherited authority
                        allow_rules=set(parent_session.allow_rules))
        messages = [{"role": "user", "content": a["description"]}]   # the child's own conversation
        return run_turn(messages, model, child_registry, child)      # the loop, run again
    return Tool("Agent", spawn, is_read_only=True)
```

- `agent_tool` 는 일반 도구를 반환합니다.
- 그 핸들러는 새로운 `Session` 와 함께 `run_turn()` 를 호출합니다.
- 하위 에이전트의 `messages[]` 는 하위 프롬프트만 가지고 시작합니다.
- 하위 에이전트는 `run_turn()` 가 반환하는 텍스트를 반환합니다.

### 통합 방식

루프는 변하지 않습니다. subagent 는 루프를 호출하는 또 다른 도구 핸들러일 뿐입니다.

세 가지 속성이 중요합니다:

- **신선한 컨텍스트.** 자식은 부모의 기록을 상속하지 않습니다. 부모도 자식의 흔적을 상속하지 않습니다.
- **상속된 권한.** 자식은 부모의 허가 모드와 허용 규칙을 복사합니다. 컨텍스트 분리는 권한 분리가 아닙니다.
- **재귀 제한.** 데모에서는 자식 레지스트리에서 `Agent`를 생략하므로, 자식이 또 다른 자식을 생성할 수 없습니다.

---

## 시스템별

각 에이전트가 하위 문제를 격리하고 결과를 반환하는 방법.

| | Claude Code | deepseek-harness |
| --- | --- | --- |
| **장점** | 자식 컨텍스트는 부모가 집중할 수 있도록 하고, 메인 기록을 깔끔하게 유지합니다. | 하나의 경계가 프로세스 내 자식, 외부 런타임, 제품 CLI에 걸쳐 확장됩니다. |
| **단점** | 부모는 자식이 어떻게 거기에 도달했는지 알 수 없다. 간단한 요약은 다시 묻는 것을 의미한다. | 여섯 개의 백엔드와 하나의 이력서 관리기가 있는데, 하나의 도구면 충분하다. |
| **이유** | 부모는 결론이 필요하지, 자식이 읽은 모든 파일이 필요한 것은 아니다. | 위임은 전달 수단의 선택이므로 각 백엔드는 이름 아래 등록된다. |
| **방법: 스폰 원시** | `Agent` 도구. subagent 타입은 내장 페르소나를 선택한다. | 등록된 백엔드당 하나의 도구: 새로운 자식, 포크, 외부 runtime, 또는 CLI. |
| **방법: 컨텍스트 격리** | 새로운 자식 메시지. 포크된 자식은 다시 포크할 수 없다. | 새로운 자식은 빈 상태로 시작한다. 포크는 부모의 완료된 턴만 복사한다. |
| **방법: 결과 반환** | 자식의 마지막 메시지 텍스트가 반환됩니다. 전사본은 삭제됩니다. | 마지막 보조 메시지, 선택적 출력이 스키마와 비교됨. |
| **방법: 재개** | 대부분의 에이전트는 재개합니다. 부모가 후속 메시지를 보냅니다. | 내구성 있는 자식은 후속 메시지를 큐에 저장하며 재시작 후 로그에서 다시 불러옵니다. |

---

## 실패 모드

- **손실 요약.** 자식이 너무 많이 압축할 수 있습니다. 중요한 내용을 디스크에 기록하도록 요청하세요.
- **무한 재귀.** 자식이 자식을 생성하면 무한히 증가할 수 있습니다. 자식 레지스트리에서 `Agent` 도구를 생략하거나 깊이 제한을 적용하세요.
- **자식 정지 없음.** 자식은 부모와 동일한 중단 위험이 있습니다. 각 자식에게 별도의 순서 또는 토큰 제한을 주세요.
- **가정된 권한 격리.** 자식은 여전히 일반 권한 게이트가 필요합니다. 컨텍스트가 분리되어 있다고 해서 건너뛰지 마십시오.
- **고아 비동기 자식.** 백그라운드 자식은 부모가 진행한 후에 완료될 수 있습니다. 작업 기록으로 추적하십시오.

---

## 실행 가능

[`src/`](src/)는 05를 이어가고 추가합니다:

- [`subagents.py`](src/subagents.py): `Agent` 도구.
- [`loop.py`](src/loop.py): 섹션 5에서 변경 없음.
- [`demo.py`](src/demo.py): 부모가 자식에게 카운트를 위임함.
- [`test.py`](src/test.py): 새로운 컨텍스트, 상속된 권한 및 재귀 방어 체크.

```bash
python sections/06-subagents/src/test.py         # offline checks, no key
uv run python sections/06-subagents/src/demo.py  # live demo, needs a key
```

---

## 출처

- [Claude Code 출처](https://github.com/yasasbanukaofficial/claude-code):
  `tools/AgentTool/AgentTool.tsx`, `runAgent.ts`, `resumeAgent.ts`, `forkSubagent.ts`, `builtInAgents.ts`, `tasks/LocalAgentTask/`.
- [deepseek-harness 소스](https://github.com/deepseek-ai/deepseek-harness) at `dsh-v0.1.0-rc.7`:
  `packages/subagent/subagent/src/index.ts`, `src/continuation.ts`, `packages/subagent/subagent-fork-in-process/README.md`,
  `packages/subagent/subagent-acp/README.md`, `docs/subsystems/subagent.md`, `docs/tool-catalog.md`.
- [learn-claude-code · s06_subagent](https://github.com/shareAI-lab/learn-claude-code): 섹션 프레이밍.
