# 17 · Protocols

[English](README.md) · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · **한국어**

> 메시지에 계약을 부여합니다. 행동하기 전에 승인받고, 멈추기 전에 확인합니다.

coordination(섹션 16)은 agent에게 통신 경로를 주지만, 경로는 텍스트만 옮깁니다.
텍스트만으로는 규칙이 없습니다. 요청과 응답을 구분할 것이 없고, 한쪽이 행동하기 전에 답을 기다리게 만들 것도 없습니다.

protocol은 그 경로 위에 얹는 합의된 규칙입니다. 요청과 그 응답의 모양을 정하고, 응답을 그것이 답하는 요청에 맞추는 방법을 정합니다.

이것이 특히 필요한 상황이 둘 있습니다. lead가 편집 중인 팀원을 종료시키면 반쯤 쓰다 만 파일과 열린 채 남은 task 기록이 생깁니다.

위험한 리팩터링을 묻지 않고 실행하는 팀원은 먼저 저지르고 나중에 보고합니다.

둘 다 같은 것이 필요합니다. 한쪽이 요청하고, 다른 쪽이 응답하고, id가 둘을 묶어 줍니다.

protocol이 해야 하는 일은 다음과 같습니다.

1. 요청과 그 응답에 타입이 있는 모양을 줍니다.
2. 각 응답을 그것이 답하는 요청과 짝지웁니다.
3. 위험한 계획은 일이 시작되기 전에 게이트를 겁니다.
4. 진행 중인 작업을 잃지 않고 agent를 멈춥니다.
5. worker 하나가 이기면 fan out 전체를 멈추고, 그 경쟁을 정확히 한 번만 정리합니다.
6. 신뢰 경계를 넘어, 팀 밖의 agent에 닿습니다.

이 계층이 없으면 coordination은 구조 없는 잡담입니다. 게이트가 걸리는 것도, 깔끔하게 멈추는 것도 없고, 응답을 그것이 답하는 대상에 맞출 수도 없습니다.

---

## 메커니즘

![Mechanism diagram](assets/17-protocols.png)

모든 주고받기는 하나의 `requestId`를 공유하는, 타입이 있는 요청과 타입이 있는 응답입니다.

발신자는 요청을 대기 중으로 기록하고, 응답을 타입에 따라 배분하고, 짝이 맞는 요청을 해소합니다.

이것을 단순한 메시지 둘이 아니라 protocol로 만들어 주는 규칙은 셋입니다.

- **타입이 있는 변종.** 각 메시지는 `type` 필드로 구분되는 변종 하나입니다. 핸들러가 타입으로 배분하므로, 응답이 무관한 요청으로 오인되지 않습니다.
- **상관 id.** `requestId`는 요청을 보낼 때 정해지고 응답에 그대로 되돌아옵니다. 발신자는 어떤 대기 중 요청이 해소되는지 알 수 있습니다.
- **작은 상태 기계.** 요청은 `pending`을 거쳐 `approved` 또는 `rejected`가 됩니다. 이미 해소된 id에 대한 응답은 무시되므로 중복은 해롭지 않습니다.

shutdown 흐름과 계획 흐름은 방향만 반대인 같은 주고받기입니다.
shutdown에서는 lead가 요청하고 팀원이 확인합니다. 계획 승인에서는 팀원이 요청하고 lead가 확인합니다.

승인은 그 작업이 돌아갈 permission 모드를 함께 실을 수도 있습니다. 그러면 판정과 모드가 같이 이동합니다(섹션 3).

### 이번에 추가되는 것: protocol 추적기

`protocols.py`는 섹션 16의 경로 위에 agent마다 하나씩 두는 `Protocol`입니다. 요청은 상관 id를 만들어 자기를 대기 중으로 기록하고, 응답은 그 id를 되돌려 줍니다.

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

- `request`는 id마다 `me-N`으로 번호를 매깁니다. 그래서 id는 발신자별로 고유하고 agent 사이에서 절대 겹치지 않습니다.
- `reply`는 요청의 `request_id`를 그대로 씁니다. 이 되돌려 주기가 핵심입니다. 발신자는 나중에 이것으로 응답과 그것이 답하는 대상을 맞춥니다.

작은 표 하나가 각 요청에 어떤 응답 종류가 답할 수 있는지, 그리고 각각이 뜻하는 판정이 무엇인지 정합니다.

```python
_REPLIES = {                                           # src/protocols.py
    "shutdown_request": {"shutdown_approved": APPROVED, "shutdown_rejected": REJECTED},
    "plan_approval_request": {"plan_approval_response": None},   # None: the verdict rides an `approved` field
}
```

`resolve`는 그 표를 읽어, 짝이 맞지 않는 응답을 거부하고 판정을 정확히 한 번만 기록합니다.

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

- `resolve`는 멱등합니다. 중복이거나 엉뚱한 응답은 `state != PENDING` 검사나 모르는 id 검사에 걸려 `None`을 돌려줍니다.
- `verdicts` 조회가 타입 혼동 방지 장치입니다. `plan_approval_response`는 `shutdown_request`를 해소할 수 없습니다. 그 타입이 shutdown 행에 없기 때문입니다.
- shutdown은 판정을 두 가지 응답 종류로 나눠 담고, 계획 승인은 bool을 실은 한 종류를 씁니다. 둘 다 같은 `pending`에서 `approved` 또는 `rejected` 상태로 갑니다.
- `protocol_tools`는 핸드셰이크를 시작하는 쪽을 tool로 노출합니다(`ExitPlanMode`, `ApprovePlan`, `StopTeammate`).
- shutdown을 확인하는 것은 tool이 아닙니다. 팀원의 `run_teammate` loop이 자동으로 응답합니다(harness가 구동하는 수신).

### 이번에 추가되는 것: 팀원 loop

`run_teammate`는 섹션 16의 `serve_mailbox`에 shutdown 핸드셰이크를 접어 넣은 것입니다. 이제 띄워진 팀원은 데몬 thread와 함께 죽는 대신 요청을 받고 멈춥니다.

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

- shutdown을 잡담보다 먼저 확인하므로, 동료들이 보내는 메시지가 정지를 리소스 부족에 빠뜨릴 수 없습니다.
- 시작은 모델이 구동하고(lead의 `StopTeammate`), 수신은 harness가 구동합니다(loop이 확인). 참조 구현의 구분과 같습니다.
- loop은 `"shutdown"`을 돌려주므로, 띄운 런타임(섹션 13)이 깔끔한 정지를 보고합니다.
- 섹션 18은 분기를 하나 더 더합니다. inbox가 비어 있으면 공유 보드에서 task를 선점합니다.

### 기존 구조에 붙이는 방법

데모는 메인 agent 하나를 돌립니다. lead는 한 turn 안에서 팀원을 띄우고, 일을 맡기고, 멈춥니다. 팀원은 자기 thread 위에서 확인 응답을 보냅니다.

```python
def spawn_worker(name, team, model):                   # src/demo.py, module level
    ...                                                 # build the teammate's tools
    return run_teammate(team, name, "lead", work)       # serve_mailbox plus the shutdown handshake

run_turn([...goal...], model, lead_reg, session)        # the one agent call in demo(): the lead
state = next(filter(None, (lead_proto.resolve(m) for m in team.drain("lead")   # -> approved
                           if isinstance(m["content"], dict))), None)
```

- `demo()`는 `run_turn`을 한 번, lead의 것으로 돌립니다. `SpawnTeammate`, `SendMessage`, 그다음 `StopTeammate`를 호출합니다.
- `StopTeammate`는 `shutdown_request`를 보냅니다. 팀원의 `run_teammate`가 그것을 확인하고 반환합니다. 정지는 강제 종료가 아니라 핸드셰이크입니다.
- lead는 되돌아온 `shutdown_approved`를 `approved`로 해소합니다. 메인 프로세스는 기다리기만 합니다.
- 계획 승인 흐름은 방향이 대칭인 반대 흐름입니다(`ExitPlanMode` 다음 `ApprovePlan`). 같은 tool들이 구동하며 test.py에서 확인합니다.
- loop은 바뀌지 않습니다. protocol은 경로 위에서 요청의 모양을 잡고 응답을 해소하는 방식으로 turn을 감쌉니다.

### 더 읽을거리

여기 나오는 내용은 `src/`에 없습니다. ai-agent-book과 A2A 명세에서 온 것이고, 표에 있는 시스템들에서 확인된 내용은 아닙니다.

**fan out 전체 멈추기.** fan out은 worker 여럿을 한 문제에 보내고 답은 하나만 필요합니다.
가장 먼저 성공한 worker가 보고하고, 그러면 lead가 나머지 모든 worker에게 정지를 보냅니다.
데모는 팀원 하나만 멈추지만, 전송 경로상에 새로 오가는 것은 없습니다. 정지는 하나하나가 똑같은 요청과 확인이며,
그래서 진 worker도 자기 파일을 마저 쓰고 자기 task 기록을 닫습니다. 여러 대상에게 보내는 shutdown 흐름 그대로입니다.

**동시에 두 명이 이기는 경우.** worker 둘이 같은 순간에 성공할 수 있습니다. 그러면 둘 다 1등이 되고, lead는 정지를 두 차례 보내고, 결과도 두 개가 기록됩니다.
잠금이 이것을 고칩니다. 먼저 도착한 worker가 잠금을 잡고, 누가 이겼는지 적고, 놓습니다.
두 번째가 다음으로 잠금을 잡고, 이미 적힌 승자를 보고, 아무도 멈추지 않은 채 돌아갑니다. 누가 먼저 도착하든 경쟁은 한 번만 정리됩니다.

**확인이 끝내 오지 않을 때.** 확인을 기다리는 정지는 답을 못 받을 수 있습니다. 긴 tool call 안에 있는 worker는 자기 inbox를 읽지 않습니다.
그래서 정지는 두 단계입니다. lead가 요청하고, 마감 시각까지 확인을 기다리고, 그때까지 돌고 있는 것은 종료시킵니다.
강제 종료는 첫 수가 아니라 대비책입니다. lead가 먼저 묻기 때문에, 시간이 있는 한 정리 작업이 실행됩니다.

**소스 하나이며 비교 조사가 아님.** 두 단계 정지와 잠금은 책 저자 자신의 실험 하나에서 온 것이며, 여러 시스템을 비교한 결과가 아닙니다.

**내가 소유하지 않은 agent와 대화하기.** 위의 모든 이야기는 팀 하나, 프로세스 하나, 소유자 하나를 전제합니다.
경로는 공유되고, 명단은 띄우는 시점에 알려져 있고, 모든 agent가 전송 경로상의 id를 신뢰합니다.
조직 경계를 넘으면 그중 무엇도 남지 않습니다. `request_id`를 찍을 공유 inbox가 없습니다. 상대편의 명단은 보이지 않습니다. 상대의 tool 목록도 신뢰할 수 없습니다.
A2A가 그 경우를 위한 protocol입니다. 요청과 응답이라는 핵심은 유지하고 세 부분을 더합니다.

- **Agent Card 탐색.** agent마다 알려진 URL에 문서를 공개합니다. 이름, skill, 엔드포인트, 인증 방법이 담깁니다.
  호출하는 쪽은 카드를 먼저 읽고 무엇을 보낼지 정합니다. 팀 안에서는 명단이 띄우는 시점에 옵니다. 경계를 넘으면 호출하는 쪽이 직접 가져와야 합니다.
- **task 수명 주기.** 원격 호출은 id와 상태를 가진 task입니다. `submitted`, `working`, `input-required`, `completed`, `failed`가 있습니다. 호출하는 쪽은 그 id로 폴링하거나 구독합니다.
  `input-required`는 이 섹션에 이름이 없는 상태입니다. 원격 agent가 멈추고 정보를 더 요구하며, 기다리는 동안에도 task는 살아 있습니다.
- **불투명한 산출물.** 결과는 산출물로 돌아옵니다. 파일, 텍스트, 구조화된 조각입니다. 원격 agent의 trajectory는 돌아오지 않습니다.
  호출하는 쪽은 그 일이 어떻게 됐는지 볼 수 없습니다. 결과만 경계를 넘습니다.

**요청 상태와 task 상태.** 두 설계가 추적하는 대상이 다릅니다. 이 섹션은 요청 하나를 추적합니다. `pending`을 거쳐 `approved` 또는 `rejected`가 됩니다.
A2A는 task 하나를 추적합니다. `submitted`, `working`, `input-required`, `completed`, `failed`입니다.
차이는 기록이 얼마나 오래 사는지입니다. 요청 기록은 그것을 만든 주고받기와 함께 끝납니다.
task id는 나중에도 여전히 해소됩니다. 응답이 도착한 뒤에도, 정보를 더 받으려고 멈춘 뒤에도, 연결이 끊겼다 돌아온 뒤에도 그렇습니다.
경계 너머를 호출하는 쪽은 둘 다 가집니다. 요청 상태는 이 메시지 하나가 받아들여졌는지를 말합니다. task 상태는 일 전체가 어디까지 왔는지를 말합니다.

---

## 시스템별

각 설계가 요청의 모양을 어떻게 잡고, 계획에 어떻게 게이트를 걸고, agent를 어떻게 깔끔하게 멈추는지 비교합니다.

| | Claude Code | deepseek-harness |
| --- | --- | --- |
| **장점** | 모든 정지가 확인을 거치고 위험한 계획은 모두 게이트를 지남. | 공개 protocol을 말하는 클라이언트나 서버라면 무엇이든 상호 연동됨. |
| **단점** | 핸드셰이크마다 왕복과 protocol 상태 비용이 듦. | 출력이 커밋될 때만 도착하므로 진행 상황이 실시간으로 보이지 않음. |
| **이유** | 편집 도중의 강제 종료는 반쯤 쓰다 만 파일을 남김. 위험한 계획은 먼저 승인이 필요함. | 상대편은 내가 소유하지 않을 수도 있는 프로세스이므로 공개 계약을 씀. |
| **방법: 메시지 모양** | `type` 필드로 구분되는 타입 유니언 하나, 응답마다 `request_id`를 둠. | session id로 구분되는 JSON-RPC 메서드. session마다 진행 중인 prompt는 하나. |
| **방법: 계획 승인** | 팀원이 기다림. lead의 응답이 판정, 피드백, 모드를 실어 옴. | 계획이 사람에게 감. 거부는 피드백이 붙은 실패한 호출로 돌아옴. |
| **방법: shutdown** | lead가 요청하고, 팀원이 확인하고, 그다음 강제 종료가 실행됨. | 취소, 입력 종료, 시그널, 그다음 강제 종료. 모든 단계에 시간 제한이 있음. |

---

## 실패 모드

- **핸드셰이크 대신 강제 종료.** 팀원의 thread를 죽이면 진행 중인 작업이 사라지고 task 기록이 주인을 잃습니다. task를 `notified`로 표시하는 요청 후 확인 흐름을 씁니다.
- **주인 없는 요청.** 끝내 오지 않는 응답은 요청을 영원히 `pending`으로 남기고, 발신자는 막힙니다. 막힌 요청을 드러내는 timeout이나 유휴 검사를 넣습니다.
- **타입 혼동.** id만으로 응답을 맞추면 shutdown 응답이 계획 요청을 해소할 수 있습니다. 응답 변종이 기록된 요청 타입과 맞는지 확인합니다.
- **집행 없는 승인.** 승인된 계획도 실행을 막으려면 permission 계층이 필요합니다(섹션 3). 응답에 `permissionMode`를 실어 보냅니다.
- **중복 응답.** 재시도된 응답이 이미 해소된 상태를 뒤집을 수 있습니다. 대기 중이 아닌 id에 대한 응답은 무시합니다.
- **잠금 없이 fan out 멈추기.** worker 둘이 같은 순간에 끝나 둘 다 1등이 됩니다. 그러면 lead가 정지를 두 차례 보내고 승자를 둘 기록합니다.
  누가 이겼는지 적기 전에 잠금을 잡습니다. 나중에 이긴 쪽은 이미 적힌 이름을 보고 아무도 멈추지 않습니다.
- **마감 시각 없이 확인 기다리기.** 긴 tool call로 바쁜 worker는 요청을 아예 읽지 않습니다. 확인은 오지 않고 lead는 영원히 기다립니다.
  기다림에 마감 시각을 두고 그 뒤에는 종료시킵니다. 묻는 것은 여전히 첫 수입니다. 다만 유일한 수는 아닙니다.
- **원격 task를 응답 하나로 취급.** 내가 소유하지 않은 agent는 멈추고 정보를 더 요구할 수 있습니다. 그것은 응답이 아니라 task 상태입니다.
  task id와 그 상태를 추적합니다. 그러면 멈춘 작업도 그것을 시작한 주고받기가 끝난 뒤에 여전히 주소로 가리킬 수 있습니다.

---

## 실행 방법

[`src/`](src/)는 16의 코드를 이어받아 다음을 더합니다.

- [`protocols.py`](src/protocols.py): 요청 추적기(타입이 있는 변종, 상관 id, 상태 기계), 핸드셰이크 tool들, 그리고 `run_teammate` loop.
- [`test.py`](src/test.py): shutdown 흐름과 계획 흐름, 방지 장치들, tool이 구동하는 핸드셰이크, 그리고 핸드셰이크로 멈춘 자율 실행 팀원을 확인합니다.
- [`demo.py`](src/demo.py): lead의 turn 하나가 팀원을 띄우고, 일을 맡기고, StopTeammate로 멈춥니다. 팀원은 자기 thread 위에서 확인 응답을 보냅니다.

loop과 subagent 경로는 그대로입니다. protocol은 경로 위에서 요청의 모양을 잡고 응답을 해소하는 방식으로 turn을 감쌉니다.

```bash
python sections/17-protocols/src/test.py         # offline checks, no key
uv run python sections/17-protocols/src/demo.py  # live demo, needs a key
```

---

## 출처

- [Claude Code protocol shape](https://github.com/yasasbanukaofficial/claude-code): `tools/SendMessageTool/SendMessageTool.ts`, `utils/teammateMailbox.ts`.
- [Claude Code plan and stop](https://github.com/yasasbanukaofficial/claude-code):
  `tools/ExitPlanModeTool/ExitPlanModeV2Tool.ts`, `tasks/stopTask.ts`, `coordinator/coordinatorMode.ts`.
- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness) at `dsh-v0.1.0-rc.7`:
  `packages/acp/acp/README.md`, `packages/subagent/subagent-acp/README.md`, `docs/subsystems/session.md`,
  `docs/subsystems/plan.md`, `docs/subsystems/approval.md`.
- [learn-claude-code · s16_team_protocols](https://github.com/shareAI-lab/learn-claude-code): 섹션 구성 참고.
- [ai-agent-book](https://github.com/bojieli/ai-agent-book): `book/chapter10.md` (多 Agent 协作), 중국어 원문이 정본입니다.
  정리하고 확인 응답을 보내는 정지, 대비책 단계로서의 강제 종료, 그리고 처음 성공한 시점에 fan out 전체를 멈추면서 잠금으로 경쟁을 한 번만 정리하는 방식.
  둘 다 책 저자 자신의 실험에 기대며, 소스는 하나입니다.
- [A2A protocol](https://github.com/a2aproject/A2A) (Linux Foundation): Agent Card 탐색, task 수명 주기 상태
  (`submitted`, `working`, `input-required`, `completed`, `failed`), 그리고 신뢰 경계를 넘는 불투명한 산출물 교환.
