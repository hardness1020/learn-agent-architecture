# 0 · Harness 주제

[English](README.md) · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · **한국어**

> 모델은 무엇을 할지 결정합니다. harness는 모델에 tool, 상태, 제한을 줍니다.

모델은 추론, tool 선택, 언제 멈출지를 담당합니다. harness는 모델을 둘러싼 코드, 즉 loop, tool, memory, permission, 인터페이스입니다.

모델 호출 하나는 입력 하나에 대한 응답 하나입니다. 행동하겠다고 결정할 수는 있지만 스스로 행동하지는 못합니다. 지속되는 상태도, tool 실행기도, 파일 접근도, permission gate도 없습니다.

harness는 다음을 해야 합니다.

1. 행동이 실행될 자리를 제공합니다.
2. 모델에 쓸모 있는 관측 결과를 제공합니다.
3. 부수 효과가 바깥 세계에 닿기 전에 통제합니다.
4. 나중 호출이 앞선 호출 위에 쌓이도록 상태를 저장합니다.

harness가 없으면 모델은 답만 할 수 있습니다. tool을 실행하지도, 결과를 관측하지도, 호출 사이에 작업을 기억하지도 못합니다.

---

## 메커니즘

![Mechanism diagram](assets/00-harness-thesis.png)

이 섹션은 분해에 관한 이야기입니다. 가운데에는 작은 모델 호출 하나가 있습니다. harness는 그 입력을 공급하고 출력을 처리합니다.

모델은 판단을 담당합니다. harness는 환경을 담당합니다.

섹션 1의 loop가 핵심 제어 흐름입니다. 다른 섹션들은 그 주위에 입력, 검사, 상태를 덧붙입니다.

- 섹션 2는 tool runtime과 dispatch를 더합니다.
- 섹션 3은 permission과 sandbox를 더합니다.
- 섹션 4는 수명 주기 이벤트를 가로채는 hook을 더합니다.
- 섹션 8과 9는 context 관리와 memory를 더합니다.
- 섹션 10은 매 turn마다 system prompt를 조립합니다.
- 뒤쪽 섹션들은 task, 백그라운드 작업, scheduling, 격리를 더합니다.

이 부분들은 loop를 대체하지 않습니다. loop에 입력을 넣거나, loop를 통제하거나, loop를 위해 상태를 저장합니다.

### harness는 많을수록 좋은 것이 아닙니다

각 계층은 지금 모델이 혼자 못 하는 일을 메웁니다. 그래서 모든 계층에는 비용이 둘 붙습니다.

1. 유지할 코드가 늘고, 버그가 생길 자리도 늘어납니다.
2. 설계가 특정 모델 세대에 묶입니다. 더 새로운 모델은 스스로 계획하고, 복구하고, 검증할 수 있습니다. 이때 옛 우회책을 강요하면 성능은 올라가지 않고 오히려 떨어집니다.

그래서 harness 엔지니어링은 더하기만 하는 일이 아닙니다. 모델이 바뀌면 각 계층을 다시 평가해서, 여전히 도움이 되는 것은 남기고 새 모델이 대신하는 것은 지웁니다.
섹션 20과 21이 이를 위한 측정을 만듭니다. mini-swe-agent는 극단적인 사례로, harness가 거의 없으니 다시 평가할 것도 거의 없습니다.

deepseek-harness는 같은 문제를 반대쪽 끝에서 답합니다. loop를 포함해 모든 부분이 plugin이고, 나머지보다 특별한 코어는 없습니다.
각 기능은 이름이 붙은 seam에 공개되고 plugin 하나가 그 자리를 선점하는데, tool은 한 seam, permission은 다른 seam, context 처리는 또 다른 seam입니다.
그래서 계층을 다시 평가하는 일은 fork가 아니라 설정 변경입니다. 계층을 지우는 일은 그 plugin을 로드하지 않는 것입니다.

### 더 읽을거리

ai-agent-book이 내놓은 두 가지 관점입니다. 이 저장소가 직접 확인한 결과가 아니라 검증해 볼 주장입니다.

**경계를 검증하기.** 모델과 harness 사이의 선이 어디에 놓이는지는 모델이 좋아질수록 달라집니다. 책은 확인해 볼 만한 주장을 둘 내놓습니다.

- **scaffolding은 모델의 성능을 따라갑니다.** 약한 모델은 강제된 계획, 단계별 재시도, 스크립트로 짠 검사가 필요합니다. 강한 모델은 그 일을 스스로 합니다.
- **읽기 임계점은 모델의 정책입니다.** 코드 읽기를 멈추고 편집을 시작할 시점은 학습 과정에서 익힙니다. prompt 한 줄이나 단계 제한은 그것을 살짝 밀 뿐입니다. 어느 쪽도 그 시점을 정하지는 못합니다.

**두 주장은 하나의 실험으로 모입니다.** 같은 scaffold를 두 모델 세대에 돌리면 점수가 서로 반대 방향으로 움직일 수 있습니다.
책의 실험 하나가 이를 보고하므로, 수치의 크기를 믿기보다 방향을 검증해야 합니다.
그다음 각 계층이 그 선의 어느 쪽에 있는지 따져 봅니다. 모델이 이미 그 판단을 내린다면 그 계층은 중복이고 token만 태웁니다.

**agent가 먼저 통하는 곳.** 어떤 task가 지금 agent에 맞는지는 두 가지가 결정하는데, 목표를 얼마나 정확히 기술할 수 있는지, 그리고 결과를 기계가 검사할 수 있는지입니다.
코딩은 둘 다 점수가 높습니다. 티켓이나 실패하는 테스트가 목표를 기술합니다. 테스트, 타입, linter, git이 작업이 끝났는지 알려 줍니다.
사람들이 자기들 쓰려고 그 인프라를 만들어 두었고, agent는 그것을 이미 완성된 검증 harness로 재사용합니다. coding agent가 먼저 성숙한 이유가 이것입니다.

**한쪽 성질이 빠졌을 때.** task가 막연히 어려워지는 것이 아닙니다. 특정한 방식으로 실패하며, 경우는 둘입니다.

- **목표는 분명한데 자동 검사가 없는 경우.** 페이지를 더 잘 읽히게 다시 씁니다. loop에 정지 조건이 없으니, 아니라고 말해 주는 것이 없어서 작업을 끝났다고 선언합니다.
- **자동 검사는 있는데 목표가 분명하지 않은 경우.** 모듈을 정리합니다. loop는 목표 대신 검사를 겨냥합니다. 아무것도 깨지지 않았음을 증명하는데, 그것은 요청한 내용이 아닙니다.

**두 경우에 필요한 처방은 다릅니다.** 도메인에 검사가 없으면 섹션 21이 그 검사를 만듭니다. 기술할 수 없는 목표는 어떤 검사로도 고치지 못합니다.

---

## 시스템별

모델이 결정하는 것과 주변 코드가 만드는 것의 대비입니다.

| | Claude Code | mini-swe-agent |
| --- | --- | --- |
| **장점** | harness가 안전성, 지속성, subagent, 필요할 때 불러오는 지식을 더함. | harness 코드가 거의 없어서 유지할 것도 거의 없음. |
| **단점** | harness가 코드의 주된 영역이 됨. 동작 대부분과 버그 대부분이 거기 있음. | bash 실행을 넘는 모든 기능은 모델에서 나와야 함. |
| **이유** | 모델 호출은 스스로 행동하지 못하므로 harness가 환경을 담당함. | bash tool 하나면 충분하다는 가정. hook, skill, memory, task는 의도적으로 없음. |
| **방법: 모델의 몫** | 판단, tool 선택, 정지 결정. tool 이름, schema, 결과를 봄. | 판단, 편집 전술, 제출 시점. |
| **방법: harness의 몫** | loop, tool, permission, hook, 지식, task, 조율. | loop 하나, bash tool 하나, 확인 gate, 그리고 단계와 비용 예산. |
| **방법: 규모 신호** | 코드 대부분이 모델 호출 바깥에 있음. | agent 클래스 전체가 약 150줄. |

---

## 실패 모드

- **harness의 동작을 모델의 공으로 돌림.** permission 검사와 오류 복구는 harness의 동작입니다. 그것이 실패하면 harness를 고칩니다.
- **모델이 내려야 할 결정의 하드코딩.** 경직된 tool 순서와 스크립트로 짠 계획은 모델과 부딪칠 수 있습니다. 판단이 필요한 자리는 모델이 정하게 둡니다.
- **harness 부족.** tool도 permission도 context 관리도 없는 loop는 모델을 챗봇 수준에 묶어 둡니다. 빠진 계층을 더합니다.
- **harness 과잉.** 계층마다 유지 비용이 늘고, 옛 모델에 맞춰 만든 계층이 새 모델의 발목을 잡을 수 있습니다. 모델이 바뀌면 다시 평가하고, 더는 도움이 안 되는 것은 지웁니다.
- **모델의 정책을 harness 설정으로 착각.** 정보 수집을 언제 멈출지는 모델이 학습한 것입니다. prompt 규칙은 그것을 살짝 밀 뿐입니다. 계층을 남기기 전에 측정합니다.
- **기계가 결과를 검사할 수 없는 곳에서 agent를 실행.** loop는 끝난 task와 잘못된 task를 구분하지 못합니다. 검사기를 추가하거나 사람을 경로에 남겨 둡니다.
- **책임의 뒤섞임.** tool 실행 안에 permission 로직이 들어가면 테스트하기도 교체하기도 어려워집니다. `Tool.ts`와 `PreToolUse` 같은 명확한 계약을 지킵니다.

---

## 출처

- [Claude Code source (`cc-src/src`)](https://github.com/yasasbanukaofficial/claude-code): `QueryEngine.ts`, `query/`, `Tool.ts`, `tools/`, `hooks/`, `types/permissions.ts`.
- [mini-swe-agent source](https://github.com/swe-agent/mini-swe-agent): `agents/default.py`, `environments/local.py`, `__init__.py`의 protocol.
- [mini-swe-agent README](https://github.com/swe-agent/mini-swe-agent): 모델이 좋아질수록 최소 harness가 낫다는 논거.
- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness) `dsh-v0.1.0-rc.7`:
  `docs/architecture.md`, `docs/capability-seams.md`, `docs/cordis-primer.md`, `docs/subsystems/core.md`.
- [learn-claude-code · s20_comprehensive](https://github.com/shareAI-lab/learn-claude-code): 섹션 구성.
- [ai-agent-book](https://github.com/bojieli/ai-agent-book): `book/chapter5.md`, 중국어 원문이 기준. 경계에 대한 관점과 task 사분면은 둘 다 단일 출처.
