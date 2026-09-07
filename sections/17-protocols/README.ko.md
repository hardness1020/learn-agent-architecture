# 17 · Protocols

[English](README.md) · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · **한국어**

> 메시지에 계약을 부여하라: 행동하기 전에 승인하고, 중지하기 전에 확인하라.

조정(섹션 16)은 에이전트에게 채널을 제공하지만, 채널은 단순히 텍스트를 전달할 뿐이다.
텍스트만으로는 규칙이 없다: 요청과 응답을 구분하는 방법도 없고, 한쪽이 행동하기 전에 답변을 기다리게 하는 것도 없다.

프로토콜은 채널 위에 합의된 규칙이다: 요청과 그에 대한 응답이 어떻게 형성되는지, 그리고 응답이 어떤 요청에 대한 것인지 어떻게 일치시키는지.

이것이 가장 필요한 두 가지 상황이 있다. 편집 중 동료를 중간에 제거하는 주도자는 절반만 작성된 파일과 열린 작업 기록을 남긴다.

동의 없이 위험한 리팩터링을 실행하는 동료는 먼저 행동하고 나중에 보고한다.

두 경우 모두 같은 것이 필요하다: 한쪽은 요청하고, 다른 쪽은 응답하며, ID가 그들을 연결한다.

프로토콜은 다음과 같아야 합니다:

1. 요청과 그 응답에 유형화된 형태를 부여합니다.
2. 각 응답을 그것이 답하는 요청과 연관시킵니다.
3. 작업이 시작되기 전에 위험한 계획을 게이트합니다.
4. 진행 중인 작업을 잃지 않고 에이전트를 중지합니다.
5. 한 작업자가 승리하면 전체 팬아웃을 중지하고, 그 경주를 정확히 한 번만 완료합니다.
6. 신뢰 경계를 넘어 팀 외부의 에이전트에 도달합니다.

이 계층 없이는 조정이 구조화되지 않은 채팅에 불과합니다. 아무 것도 게이트되지 않으며, 아무 것도 깔끔하게 중단되지 않으며, 응답을 무엇에 대한 답인지 연결할 수 없습니다.

---

## 메커니즘

![메커니즘 다이어그램](assets/17-protocols.png)

모든 교환은 하나의 `requestId`를 공유하는 유형화된 요청과 유형화된 응답입니다.

발신자는 요청을 대기 중으로 기록하고, 응답을 그 유형에 따라 라우팅하며, 일치하는 요청을 해결합니다.

세 가지 규칙이 있기 때문에 두 개의 메시지만 있는 것이 아니라 프로토콜이 됩니다:

- **타입화된 변형.** 각 메시지는 `type` 필드의 한 변형입니다. 핸들러가 타입에 따라 분기하므로, 답장은 관련 없는 요청으로 오해되지 않습니다.
- **상관 ID.** 요청이 나갈 때 `requestId`가 설정되고, 답장에 그대로 반영됩니다. 발신자는 어떤 대기 중인 요청이 답변으로 해결되는지 알 수 있습니다.
- **작은 상태 머신.** 요청은 `pending`를 거친 다음 `approved` 또는 `rejected`로 진행됩니다. 이미 해결된 ID에 대한 답장은 무시되므로 중복은 무해합니다.

셔utdown과 계획 흐름은 방향만 반대인 동일한 교환입니다.
Shut down에서는 리드가 요청하고 팀원이 확인합니다. 계획 승인에서는 팀원이 요청하고 리드가 확인합니다.

승인에는 작업이 실행되는 권한 모드도 포함될 수 있으므로 판결과 모드가 함께 이동합니다(섹션 3).

### 새로운: 프로토콜 추적기

`protocols.py`는 section-16 채널을 통해 에이전트당 하나의 `Protocol`입니다. 요청은 상관관계 ID를 생성하고 자신을 대기 중으로 기록합니다. 응답은 그 ID를 다시 반향합니다:

```python
def request(self, to, kind, **fields):                 # src/protocols.py
    self._n += 1
    rid = f"{self.me}-{self._n}"                        # per-sender id: unique, deterministic
    self.pending[rid] = {"kind": kind, "state": PENDING}
    self.team.send(self.me, to, {"type": kind, "request_id": rid, **fields})
    return rid

def reply(self, msg, kind, **fields):                  # echo the id back, do not mint a new one
    req = msg["content"]
    self.team.send(self.me, msg["from"], {"type": kind, "request_id": req["request_id"], **fields})
```

- `request`는 각 ID `me-N`에 번호를 매기므로, ID는 발신자별로 고유하며 에이전트 간에 절대 충돌하지 않습니다.
- `reply`는 요청의 `request_id`를 재사용합니다. 그 에코(echo)가 전체 트릭입니다: 그것이 발신자가 나중에 어떤 응답이 무엇에 대한 것인지 맞추는 방법입니다.

각 요청에 어떤 종류의 답변이 응답할 수 있는지와 각 답변이 의미하는 판결을 나타내는 작은 표:

```python
_REPLIES = {                                           # src/protocols.py
    "shutdown_request": {"shutdown_approved": APPROVED, "shutdown_rejected": REJECTED},
    "plan_approval_request": {"plan_approval_response": None},   # None: the verdict rides an `approved` field
}
```

`resolve`는 정확히 한 번만 테이블을 읽어 일치하지 않는 응답을 거부하고 판정을 기록합니다:

```python
def resolve(self, msg):                                # src/protocols.py
    reply = msg["content"]
    req = self.pending.get(reply.get("request_id"))
    if not req or req["state"] != PENDING:             # unknown id or already resolved
        return None
    verdicts = _REPLIES[req["kind"]]
    if reply.get("type") not in verdicts:              # type-confusion guard
        return None
    state = verdicts[reply["type"]]
    if state is None:                                  # single-response flow carries the bool
        state = APPROVED if reply.get("approved") else REJECTED
    req["state"] = state
    return state
```

- `resolve`는 멱등성입니다: 중복되거나 잘못된 응답은 `state != PENDING` 또는 알 수 없는 ID 방어를 통과하고 `None`를 반환합니다.
- `verdicts` 조회는 타입 혼동 방어입니다: `plan_approval_response`는 `shutdown_request`를 해결할 수 없습니다, 해당 타입이 종료 행에 없기 때문입니다.
- 종료는 판정을 두 가지 응답 종류로 나눕니다; 계획 승인에서는 불리언을 포함한 한 종류를 사용합니다. 둘 다 같은 `pending`에서 `approved` 또는 `rejected` 상태에 도달합니다.
- `protocol_tools`는 핸드셰이크 시작을 도구(`ExitPlanMode`, `ApprovePlan`, `StopTeammate`)로 노출합니다.
- 종료 확인은 도구가 아닙니다; 팀원의 `run_teammate` 루프는 자동으로 응답합니다(하니스 구동 수신).

### 새로움: 팀원 루프

`run_teammate`는 종료 핸드셰이크가 포함된 섹션 16의 `serve_mailbox`입니다. 이제 생성된 팀원은 데몬 스레드와 함께 사라지는 대신 요청에서 멈춥니다:

```python
def run_teammate(team, me, lead, work, *, poll=0.05, max_idle_polls=None):   # src/protocols.py
    proto = Protocol(team, me)
    while True:
        inbox = team.drain(me)
        shutdown = next((m for m in inbox if _is_shutdown(m)), None)
        if shutdown is not None:
            proto.reply(shutdown, "shutdown_approved")     # confirm, then stop
            return "shutdown"
        chat = [m for m in inbox if isinstance(m["content"], str)]
        if chat:
            work(_fold(chat)); continue                    # section 16: fold and run
        time.sleep(poll)                                   # empty: poll again
```

- 종료는 채팅 전에 확인되므로, 피어 트래픽이 정지를 막을 수 없습니다.
- 시작은 모델 기반(리더의 `StopTeammate`)이고; 수신은 하니스 기반(루프가 확인), 참조의 분할과 일치합니다.
- 루프는 `"shutdown"`를 반환하므로, 생성 runtime(섹션 13)가 정상 정지를 보고합니다.
- 섹션 18은 하나의 분기 추가: 받은 편지함이 비어 있을 때 공유 보드에서 작업을 주장합니다.

### 통합 방식

데모는 하나의 메인 에이전트를 실행합니다. 리드는 팀원을 생성하고, 위임하며, 한 턴 안에 이를 중지합니다; 팀원은 자신의 스레드에서 확인합니다:

```python
def spawn_worker(name, team, model):                   # src/demo.py, module level
    ...                                                 # build the teammate's tools
    return run_teammate(team, name, "lead", work)       # serve_mailbox plus the shutdown handshake

run_turn([...goal...], model, lead_reg, session)        # the one agent call in demo(): the lead
state = next(filter(None, (lead_proto.resolve(m) for m in team.drain("lead")   # -> approved
                           if isinstance(m["content"], dict))), None)
```

- `demo()`는 하나의 `run_turn`, 리드의 것을 실행합니다. 그것은 `SpawnTeammate`, `SendMessage`을 호출한 후 `StopTeammate`를 호출합니다.
- `StopTeammate`는 `shutdown_request`를 보냅니다; 팀원의 `run_teammate`가 이를 확인하고 반환합니다. 중지는 종료가 아니라 핸드셰이크입니다.
- 리드는 에코된 `shutdown_approved`를 `approved`로 해결합니다. 메인 프로세스는 단지 기다리기만 합니다.
- 계획 승인 흐름은 대칭적인 역순(`ExitPlanMode` 다음 `ApprovePlan`)이며, 동일한 도구로 구동되며 test.py에서 입증됩니다.
- 루프는 변하지 않습니다. 프로토콜은 요청을 구성하고 채널에서 응답을 해결함으로써 한 턴을 감쌉니다.

### 추가 읽기

이 모든 내용은 `src/`에는 없습니다. 이는 ai-agent-book과 A2A 사양에서 나온 것이며, 표에 있는 시스템의 확인된 내용이 아닙니다.

**전체 팬 아웃 중지.** 팬 아웃은 여러 작업자가 한 문제를 동시에 처리하며, 단 하나의 답만 필요합니다.
성공한 첫 번째 작업자가 보고하고, 리더는 다른 모든 작업자에게 중지를 보냅니다.
데모에서는 한 팀원만 중지시키지만, 여기서는 새로운 내용이 전송되지 않습니다. 각 중지는 동일한 요청과 확인입니다.
그래서 패배한 작업자도 파일을 완료하고 작업 기록을 닫습니다. 이것이 많은 대상에게 전송되는 종료 흐름입니다.

**동시에 두 명의 승자.** 두 작업자가 동시에 성공할 수 있습니다. 그러면 두 명 모두 첫 번째로 간주되며, 리더는 두 번의 중지 요청을 보내고 두 개의 결과가 기록됩니다.
잠금 장치가 그것을 해결합니다. 먼저 도착한 작업자가 잠금을 가져가서 누가 승리했는지 기록하고 잠금을 해제합니다.
다음으로 두 번째 작업자가 잠금을 획득하면 이미 기록된 승자를 확인하고 아무도 막지 않고 돌아갑니다. 누가 먼저 도착하든, 경주는 한 번만 결정됩니다.

**확인이 오지 않을 때.** 확인을 기다리는 중지는 응답이 없을 수 있습니다. 긴 도구 호출 중인 작업자는 자신의 받은 편지함을 보고 있지 않습니다.
그래서 중지는 두 단계로 나뉩니다. 리더가 먼저 요청하고, 데드라인까지 확인을 기다린 후, 아직 실행 중인 모든 것을 종료합니다.
종료는 첫 번째 수단이 아니라 대체 수단입니다. 리더가 먼저 요청하므로, 시간이 있을 때마다 정리 작업이 실행됩니다.

**한 가지 출처, 설문조사가 아님.** 두 단계와 잠금은 모두 책의 저자가 직접 수행한 실험에서 나온 것이며, 여러 시스템을 비교한 것이 아닙니다.

**자신이 소유하지 않은 에이전트와 대화하기.** 위의 모든 내용은 하나의 팀, 하나의 프로세스, 하나의 소유자를 전제로 합니다.
채널은 공유되며, 로스터는 생성 시점에 알려져 있고, 모든 에이전트는 전송되는 ID를 신뢰합니다.
하지만 이러한 모든 것은 조직 경계를 넘어서면 유지되지 않습니다. `request_id`를 찍을 공유 인박스가 없고, 상대편의 로스터는 보이지 않으며, 그들의 도구 목록도 신뢰할 수 없습니다.
이 경우에는 A2A가 프로토콜입니다. 요청과 응답의 핵심을 유지하면서 세 가지 부분을 추가합니다.

- **에이전트 카드 발견.** 각 에이전트는 알려진 URL에 문서를 게시합니다: 이름, skills, 엔드포인트, 그리고 인증 방법.
  호출자는 먼저 카드를 읽고, 그 후 전송할 것을 결정합니다. 팀 내에서는 로스터가 생성 시점에 전달됩니다. 경계를 넘을 경우 호출자는 로스터를 가져와야 합니다.
- **작업 수명 주기.** 원격 호출은 ID와 상태를 가진 작업입니다: `submitted`, `working`, `input-required`, `completed`, `failed`. 호출자는 해당 ID를 폴링하거나 구독합니다.
  `input-required`는 이 섹션에서 이름이 없는 상태입니다. 원격 에이전트는 일시 중지하고 추가 정보를 요청하며, 작업은 기다리는 동안 계속 살아 있습니다.
- **불투명한 산출물.** 결과는 산출물로 반환됩니다: 파일, 텍스트, 구조화된 부분. 원격 에이전트의 경로는 돌아오지 않습니다.
  호출자는 작업이 어떻게 수행되었는지 볼 수 없습니다. 오직 결과만 전달됩니다.

**요청 상태와 작업 상태.** 두 가지 설계는 서로 다른 것을 추적합니다. 이 섹션은 하나의 요청을 추적합니다: `pending`로 간 후, `approved` 또는 `rejected`로 이동합니다.
A2A는 하나의 작업을 추적합니다: `submitted`, `working`, `input-required`, `completed`, `failed`.
차이점은 기록이 유지되는 시간입니다. 요청 기록은 그것을 생성한 교환과 함께 끝납니다.
작업 ID는 나중에 여전히 확인됩니다: 응답이 도착한 후, 추가 정보가 위해 잠시 멈춘 후, 연결이 끊어졌다가 다시 연결된 후에도.
경계를 넘는 호출자는 둘 다 유지합니다. 요청 상태는 이 한 메시지가 수락되었는지 여부를 나타내고, 작업 상태는 전체 작업의 진행 상태를 나타냅니다.

---

## 시스템별

한 설계가 요청을 어떻게 형성하고, 계획을 통제하며, 에이전트를 깔끔하게 중지시키는지에 대한 내용입니다.

| | Claude Code | deepseek-harness |
| --- | --- | --- |
| **장점** | 모든 중지는 확인되고 모든 위험한 계획은 통제됩니다. | 공개 프로토콜을 사용하는 모든 클라이언트나 서버가 상호 운용됩니다. |
| **단점** | 각 핸드셰이크는 왕복과 프로토콜 상태를 소모합니다. | 출력은 커밋될 때만 반영되므로 실시간 진행 상황은 숨겨집니다. |
| **이유** | 편집 중 강제 종료하면 절반만 작성된 파일이 남습니다. 위험한 계획은 먼저 승인을 받아야 합니다. | 상대방은 당신이 소유하지 않은 프로세스이므로 공개 계약을 사용하세요. |
| **방법: 메시지 형식** | `type` 필드에 단일 타입 유니온, 각 답장은 `request_id` 포함. | 세션 ID로 키가 지정된 JSON-RPC 메서드. 세션당 한 번의 프롬프트 진행. |
| **방법: 계획 승인** | 팀원은 기다립니다. 팀장의 답변에는 판결, 피드백, 모드가 포함됩니다. | 계획은 인간에게 전달됩니다. 거부 시 피드백과 함께 실패한 호출로 반환됩니다. |
| **방법: 종료(shutdown)** | 리드가 요청하고, 팀원이 확인하면, 그때 종료가 실행됩니다. | 취소, 입력 종료, 신호, 그런 다음 종료. 모든 단계는 시간 제한이 있습니다. |

---

## 실패 모드

- **핸드셰이크 대신 강제 종료.** 팀원의 스레드를 종료하면 진행 중인 작업이 손실되거나 그 작업 기록이 고아 상태가 됩니다. 요청 후 확인 흐름을 사용하여 작업을 `notified`으로 표시하세요.
- **고아 요청.** 도착하지 않는 응답은 요청 `pending`을 영구적으로 남기므로 발신자가 차단됩니다. 지연된 요청을 표시하는 타임아웃 또는 유휴 체크를 추가하세요.
- **타입 혼동.** id만으로 응답을 매칭하면 종료 응답이 계획 요청을 해결할 수 있습니다. 응답 변형이 기록된 요청 타입과 일치하는지 확인하세요.
- **집행 없는 승인.** 승인된 계획이라도 실행을 허용하는 권한 계층이 필요합니다(섹션 3). 응답에 `permissionMode`를 포함하세요.
- **중복 응답.** 재시도된 응답은 이미 해결된 상태를 바꿀 수 있습니다. 대기 중이 아닌 id에 대한 모든 응답은 무시 처리하세요.
- **락 없이 팬 아웃 중지.** 두 작업자가 동시에 완료되면 둘 다 첫 번째로 간주됩니다. 그런 다음 리더는 두 번의 중지 신호를 보내고 두 명의 승자를 기록합니다.
  승자를 기록하기 전에 락을 가져가세요. 나중에 오는 승자는 이미 이름이 기록되어 있는 것을 보고 아무도 중지시키지 않습니다.
- **마감 없는 확인 대기.** 길게 걸리는 툴 호출로 바쁜 작업자는 요청을 읽지 못합니다. 확인이 도착하지 않고 리더는 영원히 기다립니다.
  기다림에 마감일을 설정하고 그 이후에는 종료합니다. 요청은 첫 번째 움직임으로 남습니다. 더 이상 유일한 것은 아닙니다.
- **하나의 응답으로 처리되는 원격 작업.** 당신이 소유하지 않은 에이전트는 일시 중지하고 추가 정보를 요청할 수 있습니다. 그것은 응답이 아닌 작업 상태입니다.
  작업 ID와 상태를 추적합니다. 일시 중지된 작업은 그것을 시작한 교환이 종료된 후에도 여전히 접근 가능합니다.

---

## 실행 가능

[`src/`](src/)에는 16이 앞으로 전달되며 다음이 추가됩니다:

- [`protocols.py`](src/protocols.py): 요청 추적기(타입 변형, 상관 ID, 상태 기계), 핸드셰이크 도구, 그리고 `run_teammate` 루프.
- [`test.py`](src/test.py): 종료 및 계획 흐름 검사, 가드, 도구 기반 핸드셰이크, 핸드셰이크로 중지된 자체 실행 팀원.
- [`demo.py`](src/demo.py): 한 리드 턴이 팀원을 생성하고, 위임하며, StopTeammate로 중지함; 팀원은 자체 스레드에서 확인함.

루프와 subagent 경로는 변경되지 않음. 프로토콜은 요청을 구성하고 채널에서 응답을 해결하여 턴을 감쌈.

```bash
python sections/17-protocols/src/test.py         # offline checks, no key
uv run python sections/17-protocols/src/demo.py  # live demo, needs a key
```

---

## 출처

- [Claude Code 프로토콜 구성](https://github.com/yasasbanukaofficial/claude-code): `tools/SendMessageTool/SendMessageTool.ts`, `utils/teammateMailbox.ts`.
- [Claude Code 계획 및 중지](https://github.com/yasasbanukaofficial/claude-code):
  `tools/ExitPlanModeTool/ExitPlanModeV2Tool.ts`, `tasks/stopTask.ts`, `coordinator/coordinatorMode.ts`.
- [deepseek-harness 소스](https://github.com/deepseek-ai/deepseek-harness) at `dsh-v0.1.0-rc.7`:
  `packages/acp/acp/README.md`, `packages/subagent/subagent-acp/README.md`, `docs/subsystems/session.md`,
  `docs/subsystems/plan.md`, `docs/subsystems/approval.md`.
- [learn-claude-code · s16_team_protocols](https://github.com/shareAI-lab/learn-claude-code): 섹션 프레이밍.
- [ai-agent-book](https://github.com/bojieli/ai-agent-book): `book/chapter10.md` (다중 에이전트 협업), 중국어 원본 정경.
  정리 및 확인용 중지, 백업 계층으로서의 종료, 첫 성공 시 전체 팬아웃을 잠금과 함께 중지하여 경쟁 상황을 한 번에 해결.
  둘 다 책 저자의 실험에 기반하며 단일 출처임.
- [A2A 프로토콜](https://github.com/a2aproject/A2A) (리눅스 재단): 에이전트 카드 탐지, 작업 라이프사이클 상태
  (`submitted`, `working`, `input-required`, `completed`, `failed`), 그리고 신뢰 경계를 넘어 불투명한 아티팩트 교환.
