# 18 · Autonomy

[English](README.md) · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · **한국어**

> 인간의 지시 없이 루프를 실행합니다: 대기 상태에서 보드를 스캔하고, 준비된 작업을 청구하고, 작업을 수행합니다.

자율성은 각 턴을 시작할 때 인간의 지시 없이 실행되는 섹션 1(agent loop)입니다.

팀을 구성하면 명확한 설계는 각 워커에게 다음 작업을 전달하는 리더가 있는 방식입니다.

하지만 이는 확장되지 않습니다. 10개의 청구되지 않은 작업은 10개의 수동 할당을 의미하며, 리더가 병목이 됩니다.

작업을 마치자마자 대기 상태로 전환되는 워커는 방금 불러온 컨텍스트를 낭비합니다.

해결책은 중앙 할당이 아니라 자기 조직화입니다.

중앙 할당은 여전히 실제 설계이며, 대부분의 공개된 다중 에이전트 연구는 그것을 설명합니다.
매니저 패턴에서는 각 하위 에이전트를 도구로 등록하고, 한 명의 매니저가 모든 하위 작업을 할당합니다.
관리자는 전체 계획을 보유하고 있어서 작업을 지시하고, 중복 작업을 제거하며, 실행을 조기에 중단할 수 있습니다.
또한 모든 작업을 두 번 다루는데, 한 번은 작업을 배정할 때, 한 번은 결과를 읽을 때입니다.
두 설계는 비용이 다릅니다. 관리자는 하나의 글로벌 순서를 제공하며, 모든 작업은 관리자의 차례를 기다립니다.
보드는 처리량을 제공하며, 클레임이 오래되기 때문에 잠금이 필요합니다. 이 섹션에서는 보드를 구축합니다.

자율성은 유휴 에이전트가 다음을 할 수 있어야 합니다:

1. 해야 할 일이 없음을 인식합니다 (작업 단계가 `end_turn`에 도달함).
2. 공유 보드를 확인하여 아무도 소유하지 않고 막히지 않은 작업을 찾습니다.
3. 다른 유휴 에이전트와 경쟁하지 않고 하나를 클레임합니다.
4. 클레임된 작업으로 루프에 다시 들어가고, 보드가 비어 있을 때까지 반복합니다.

이를 제외하면 모든 에이전트는 꼭두각시일 뿐입니다. 에이전트는 다음 프롬프트를 밀어줄 인간이나 리드를 기다리므로 처리량은 디스패처가 프롬프트를 배포하는 속도로 제한됩니다.

---

## 메커니즘

![메커니즘 다이어그램](assets/18-autonomy.png)

외부 루프는 agent loop를 감쌉니다.

내부 루프는 1절의 일반적인 `while`입니다. `end_turn`에 도달하면 에이전트는 반환하지 않고 폴에 들어갑니다.

폴은 두 개의 채널을 비웁니다: 이 에이전트에게 전달된 메시지를 위한 지시된 인박스(16절)와 유휴 상태인 어떤 에이전트든 가져갈 수 있는 작업들로 구성된 비지시 보드(12절)입니다.

우선 순위 순서대로 확인합니다: 먼저 종료 요청, 그 다음 인박스 메시지, 그 다음 보드의 작업 순입니다.

발견한 내용이 다음 프롬프트가 되며, 내부 루프가 다시 실행됩니다.

- 내부 루프는 모델 `stop_reason`에서 끝나며, 이는 섹션 1과 동일한 신호입니다.
- 폴링은 먼저 종료를 확인하므로, 중지 명령이 다른 에이전트 메시지 아래에 묻히지 않습니다.
- 클레임은 읽기, 확인, 잠금 하에 쓰기 순서입니다: 소유되지 않았고 차단되지 않은 작업을 선택한 다음, 다른 에이전트가 접근하기 전에 소유권을 기록합니다.
- 작업은 해당 의존성이 `completed`일 때만 클레임할 수 있어, 차단된 작업을 에이전트가 클레임하지 않습니다.

종료 요청과 그 확인은 섹션 17 프로토콜에 해당하므로, 중지는 종료가 아니라 핸드셰이크입니다.

### 새 기능: 유휴 폴링

`autonomy.py`는 외부 루프와 하나의 폴 패스를 추가합니다. `next_action`는 수신함을 한 번 비운 후, 우선 순위 순서대로 처음 찾은 항목을 반환합니다:

```python
def next_action(proto, team, store, me):               # src/autonomy.py
    inbox = team.drain(me)
    shutdown = next((m for m in inbox if _is(m, "shutdown_request")), None)
    if shutdown is not None:                            # checked first, so chat cannot starve a stop
        proto.reply(shutdown, "shutdown_approved")
        return ("shutdown", shutdown["content"].get("reason"))
    chat = [m for m in inbox if isinstance(m["content"], str)]
    if chat:
        chat.sort(key=lambda m: m["from"] != "lead")   # lead before peers; sort is stable
        return ("message", _fold(chat))                # section 16's shared fold helper
    task = claim_next(store, me)                        # else claim the next ready task
    return ("task", task) if task is not None else None
```

- 다음 중 첫 번째를 반환합니다: 종료(확인 및 중지), 접힌 채팅, 또는 클레임된 작업.
- 채팅보다 먼저 종료를 확인하므로, 피어 트래픽이 중지를 방해할 수 없습니다(섹션 16).
- `claim_next`는 첫 번째 대기 중인, 소유되지 않은 작업을 클레임합니다; `TaskStore.claim`는 차단된 작업을 거부하고 클레임을 직렬화합니다(섹션 12).
- `None`는 유휴 상태를 의미합니다: 외부 루프가 잠들었다가 다시 폴링합니다.

### 잠금 상태에서의 클레임

폴링은 작업을 제안합니다; 잠금은 누가 받는지를 결정합니다. `claim_next`는 보드를 오래된 순서대로 스캔하며 첫 번째 소유되지 않은, 대기 중인 작업을 제안합니다:

```python
def claim_next(store, me):                             # src/autonomy.py
    for t in store.list():                             # oldest first
        if t["status"] == "pending" and t["owner"] is None:
            got = store.claim(t["id"], me)             # read, check, write under a lock
            if got["ok"]:
                return got["task"]
            # not ok: another agent won it, or it just became blocked; try the next
    return None
```

`claim_next`의 확인은 단지 힌트일 뿐입니다: 두 명의 유휴 에이전트가 동시에 같은 작업을 소유되지 않은 것으로 읽을 수 있습니다. `TaskStore.claim`(섹션 12)는 잠금 상태에서 이를 확정합니다:

```python
def claim(self, tid, owner):                           # src/tasks.py, section 12
    with self._lock():                                 # fcntl.flock: one claimer at a time
        task = self.get(tid)
        if task["owner"] is not None:                  # someone already won: back off
            return {"ok": False, "reason": "already_claimed"}
        unmet = [b for b in task["blockedBy"]
                 if (self.get(b) or {}).get("status") != "completed"]
        if unmet:                                       # a dependency is not done yet
            return {"ok": False, "reason": "blocked"}
        task["owner"], task["status"] = owner, "in_progress"
        self._write(task)
        return {"ok": True, "task": task}
```

- 이 잠금은 읽기, 확인, 쓰기를 하나의 원자적 단계로 만들어, 확인이 쓰기 전에 오래되도록 만들 수 없습니다.
- 패자는 잠금 안에서 다시 읽고 `owner`가 설정된 것을 확인하며 `already_claimed`를 얻습니다; `claim_next`는 다음 작업으로 넘어갑니다.
- 차단된 작업도 여기서 거부되므로, 의존성이 `completed`가 아닌 작업을 어떤 에이전트도 가져가지 않습니다.
- 이것이 두 스레드가 공유 상태를 놓고 경쟁하는 유일한 장소입니다. 나머지 폴링은 로컬입니다.

### 통합 방식

외부 루프는 `run_turn`를 외부에서 감싸므로, 루프와 subagent 경로는 변경되지 않습니다:

```python
def run_teammate(team, store, me, lead, work):         # src/autonomy.py
    proto, prompt, claimed = Protocol(team, me), None, None
    while True:
        if prompt is not None:
            work(prompt, claimed)                      # inner loop (section 1) does the claimed task
            prompt, claimed = None, None
            team.send(me, lead, {"type": "idle", "reason": "available"})
        action = next_action(proto, team, store, me)   # poll: shutdown, message, or task
        if action is None:                             # idle: sleep, then poll again
            time.sleep(POLL_INTERVAL); continue
        kind, payload = action
        if kind == "shutdown":
            return "shutdown"
        if kind == "task":
            prompt, claimed = task_prompt(payload), payload
        else:
            prompt = payload
```

- 이 `run_teammate`는 섹션 17에 속하며, 하나 더 많은 폴링 소스가 있습니다: 작업 보드. 셧다운(섹션 17)과 채팅(섹션 16)은 변경되지 않습니다.
- `work(prompt, claimed)`는 주장된 작업에서 `end_turn`까지 하나의 내부 루프를 실행한 다음, 에이전트는 사용 가능하다고 알립니다.
- 주장된 작업이 다음 프롬프트가 됩니다. 폴링에서 아무것도 찾지 못하면, 작업자는 스스로 중지를 결정합니다.
- 그 중지는 두 가지 모드 중 하나입니다: 종료 핸드셰이크(section 17)까지 유휴 상태이거나, 유한한 보드에서 빈 폴링이 일정 횟수 발생한 후 종료됩니다.
- 여기서는 한 작업자가 실행되지만, 루프는 에이전트별입니다. 실제 팀은 하나의 공유 보드와 인박스 집합에서 리드 루프와 여러 작업자 루프를 동시에 실행합니다.
- 리드는 하나의 활성 단계를 수행합니다: 도구를 호출하여 팀과 작업을 구성한 후 완료됩니다.
- `TeamCreate`과 `SpawnTeammate`은 섹션 16의 도구이며; `TaskCreate`는 보드(section 12)를 게시합니다.
- `SpawnTeammate`는 `runtime.start(...)`(섹션 13)입니다: 리드의 도구 호출이 스레드에서 워커의 자율 루프를 시작합니다.
- 스폰 후, 작업을 가져오고 언제 멈출지 결정하는 것은 각 워커 자신의 일이지, 리드나 스크립트의 일이 아닙니다. 메인 프로세스는 단지 워커들이 마무리될 때까지 기다립니다.
- 팀 구성, 스폰, 게시판 게시 등은 모델의 결정입니다(섹션 16과 12); 자율적 클레임은 섹션 18에 추가된 것입니다.

### 추가 읽기

이 모든 것은 `src/`에는 없습니다. 이는 ai-agent-book과 출판된 연구에서 나온 것이며, 표에 있는 시스템에서는 확인된 것이 아닙니다.

**바쁜 워커에게 묻기.** 폴은 워커에게 다음에 무엇을 할지 알려줍니다. 그것은 실행 중인 워커가 어떻게 하고 있는지를 리드에게 절대 알려주지 않습니다.

**상태 호출이 약한 이유.** 도구 호출 중인 작업자는 메시지를 듣고 있지 않습니다. 따라서 호출은 중단되거나 빈 값으로 돌아옵니다.
진짜로 멈춰 있는 작업자는 절대 응답하지 않는 작업자입니다.

**작동하는 세 가지 방법.** 첫 번째 방법은 작업자의 도움이 필요하고, 마지막 방법은 전혀 필요하지 않습니다:

1. 메시지로 요청하기. 수신함에 상태 요청을 놓습니다(섹션 16). 작업자는 다음 폴링 시 응답합니다. 정확하며, 작업자가 아직 폴링 중일 때만 작동합니다.
2. 합의된 진행 파일을 읽기. 작업자는 각 단계마다 양측이 아는 경로에 한 줄씩 추가합니다. 읽는 사람은 작업을 방해하지 않습니다.
3. 저장된 경로 확인(tail)하기. runtime은 이미 모든 회전을 디스크에 기록합니다(섹션 13). 책임자가 그 회전을 읽고, 작업자는 아무것도 하지 않습니다.

**정체(stall) 감지하기.** 마지막 두 가지 방법은 그것을 공짜로 알려준다. 파일이 마지막으로 작성된 시점을 확인하라. 새로운 쓰기가 없다는 것은 새로운 진행이 없다는 뜻이다.
한 가지 임계값(threshold)이 그것을 결정으로 바꾼다. 느린 도구 호출은 실행 중에는 아무 것도 쓰지 않으므로, 예상되는 가장 느린 호출보다 임계값을 높게 설정하라. 임계값을 넘으면 워커(worker)가 멈췄다고 간주한다.
그 임계값이 이전엔 없었던 멈춤-바쁨(stuck-busy) 실패 모드의 트리거(trigger)다. 리드(lead)는 작업을 다시 가져오거나, 영원히 기다리는 대신 종료(handshake) 절차(section 17)를 시작할 수 있다.

**풀(pool)에 대한 예산(Budgets).** 풀(poll)에는 워커에게 더 이상 작업을 청구하지 말라고 알려주는 것이 없다. 모든 유휴 워커(idle worker)는 또 다른 작업을 가져간다.
그래서 실행(run)은 작업이 끝났을 때가 아니라 예산이 소진될 때 종료된다.

**무엇을 배분할 것인가.** 한 연구에서 발표된 다중 에이전트 시스템은 토큰 사용만으로도 실행 성과 차이의 약 80%를 설명할 수 있다는 결과를 발견했습니다.
따라서 배분 단위는 턴이 아니라 토큰입니다. 네 개의 조절 장치가 보드와 작업자 풀에 연결됩니다:

- 작업별 예산. 각 작업은 게시될 때 자체 단계 상한 및 토큰 상한을 가지고 있습니다. 하나의 통제 불능 작업이 전체 실행을 소모하지 않도록 합니다.
- 동시 실행 상한. 한 번에 `in_progress`에 앉아 있는 작업 수를 제한합니다. 보드는 이미 이를 계산하기 때문에, 상한을 초과한 요청은 단순히 실패합니다.
- 모델 배치. 사고가 가장 어려운 곳에 가장 강력한 모델을 배치합니다. 계획 품질이 결과를 결정하므로, 리더가 그 모델을 사용하고 일반 작업자는 더 저렴한 모델을 사용합니다.
- 선점(Preemption). 예산을 초과했거나 임계값을 넘어 지연된 작업자는 보드에서 자신의 작업을 잃게 됩니다. 다음에 누가 이를 가져가든 깨끗한 상태에서 시작합니다.

**작업자에게 예산을 보여주세요.** 남은 예산을 아는 에이전트는 단순히 더 큰 한도를 받는 에이전트와는 다르게 사용합니다.
그 결과는 책 자체 실험에서 나온 것이므로, 하나의 출처가 이를 뒷받침합니다. 복사할 숫자가 아니라 테스트해 볼 내용으로 다루십시오.

---

## 시스템별

유휴 에이전트가 자신의 작업을 찾고 가져가는 방법.

| | Claude Code | deepseek-harness |
| --- | --- | --- |
| **장점(Pros)** | 디스패처 병목 현상이 없음. 감시자가 다른 곳에서 생성된 작업도 챙깁니다. | 무인 실행이 예측 가능하고, 계속 상태가 지속됩니다. |
| **단점** | 두 명의 유휴 에이전트가 하나의 작업을 주시할 수 있으므로, 경합을 해결하기 위해 잠금이 필요합니다. | 한 명의 에이전트, 한 가지 목표. 목표 달성 여부는 모델 자체가 판단합니다. |
| **이유** | 모든 작업을 배분하는 리더는 병목이 됩니다. | 자율성은 모드가 아니라 예산으로 정해진 허용 수준입니다. |
| **방법: 유휴 행동** | 500ms 간격으로 폴링: 종료 -> 읽지 않은 메시지 -> 클레임. | 완전히 유휴 상태일 때는 다음 라운드를 예약하고 하나의 프롬프트를 큐에 넣습니다. |
| **방법: 작업 클레임** | 잠금 아래에서 차단되지 않은 작업의 소유권을 기록하여 하나의 클레임자가 승리합니다. | 목표의 정확한 수정본에 맞춰 다음 라운드를 예약하거나 실패합니다. |
| **방법: 자기 조직화** | 워커는 게시판에서 작업을 가져옵니다(12절). 리더는 합성만 하며, 라우팅은 하지 않습니다. | 게시판 없음. 에이전트는 자신의 목표를 계속 수행하며, 팬아웃은 제한됩니다. |

---

## 실패 모드

- **작업 경쟁(claim race).** 두 에이전트가 소유되지 않은 작업으로 읽고 둘 다 이를 주장하여 한 에이전트의 작업이 취소됩니다. 확인과 쓰기를 원자적으로 수행하는 파일 잠금 내에서 작업을 주장하세요(섹션 12).
- **잡담으로 인한 기아(starvation by chatter).** 동료의 잡담 때문에 종료 요청이 묻혀, 멈춰야 하는 에이전트가 계속 폴링합니다. 일반 메시지 처리 전에 종료 여부를 확인하세요(섹션 16).
- **차단된 작업의 조기 주장(premature claim of blocked work).** 종속성이 완료되지 않은 작업을 에이전트가 주장하면 작업이 지연됩니다. 해결되지 않은 ID를 가진 `blockedBy`가 있는 작업은 건너뛰세요(섹션 12).
- **압축 후 정체성 손실(identity loss after compaction).** 장기 실행 중인 팀원이 실행 중간에 자동 압축됩니다(섹션 8) 그리고 자신의 역할을 잊습니다. 역할이 유지되도록 system prompt를 보존하세요.
- **바쁘거나, 정지 상태에 갇힘.** `end_turn`에 도달하지 못하는 단계는 결코 해제되지 않으며; 종료 없는 폴링은 계속 회전한다. 중지 신호(섹션 1)에서 종료하고, 각 폴링을 중단할 때 확인한다.
- **감지되지 않은 정지.** 작업자가 태스크를 잡고 바쁘게 보이지만 아무것도 진행하지 않아, 보드는 이를 결코 해제하지 않는다. 마지막으로 진행 파일이 기록된 시간을 확인한 후 태스크를 되돌려 가져온다.
- **무제한 풀 사용.** 유휴 작업자가 계속 요청하므로, 예산이 소진될 때만 실행이 종료된다. 모든 태스크에 단계와 토큰 제한을 부여하고, 동시에 실행되는 수를 제한한다.
- **선점 이중 쓰기.** 해제된 태스크가 이전 작업자가 실행 중일 때 다시 가져와지므로 두 에이전트가 같은 파일을 쓴다. 중지가 확인될 때(섹션 17)만 해제한다.

---

## 실행 가능

[`src/`](src/)은 17을 이어서 다음을 추가한다:

- [`autonomy.py`](src/autonomy.py): 섹션-12 보드에서 외부 루프와 대기 폴링(섹션 16의 `SpawnTeammate`가 각 작업자를 시작함).
- [`test.py`](src/test.py): 단일 작업자 메커니즘, `TeamCreate` 검사, 강제 클레임 경쟁(16개 스레드, 한 작업, 한 승자), 스레드 파이프라인, 그리고 스폰 도구 검사.
- [`demo.py`](src/demo.py): 리드가 한 단계 수행(`TeamCreate`, `TaskCreate`, `SpawnTeammate`); 그런 다음 작업자들이 보드에서 작업을 끌어와 보드가 비면 스스로 중지함.

메커니즘에서 단일 작업자 `run_teammate`은 교육 감소(teaching reduction)임.

실제 팀은 하나의 공유 보드와 인박스 세트에서 리드 루프와 여러 작업자 루프를 동시에 실행함.

섹션 13은 스레드 작업을 시작합니다; 섹션 12와 섹션 16 파일 잠금은 경쟁 상황에서 공유 상태를 안전하게 유지합니다.

동시 실행 데모와 클레임-레이스 테스트가 그것을 연결합니다.

```bash
python sections/18-autonomy/src/test.py         # offline checks, no key
uv run python sections/18-autonomy/src/demo.py  # live demo, needs a key
```

---

## 출처

- [Claude Code 자율성](https://github.com/yasasbanukaofficial/claude-code):
  `utils/swarm/inProcessRunner.ts` (`runInProcessTeammate`, `waitForNextPromptOrShutdown`, `findAvailableTask`, `tryClaimNextTask`, `sendIdleNotification`).
- [Claude Code 클레임 및 감시](https://github.com/yasasbanukaofficial/claude-code):
  `utils/tasks.ts` (`claimTask`, `claimTaskWithBusyCheck` 하 `proper-lockfile`), `hooks/useTaskListWatcher.ts`, `coordinator/coordinatorMode.ts`.
- [deepseek-harness 소스](https://github.com/deepseek-ai/deepseek-harness) `dsh-v0.1.0-rc.7`에서:
  `packages/bundle/headless/README.md`, `packages/goal/goal-round-driver/README.md`, `packages/workflow/tool-ralph/README.md`,
  `docs/subsystems/permission-presets.md`, `docs/subsystems/goal.md`.
- [learn-claude-code · s17 자율 에이전트](https://github.com/shareAI-lab/learn-claude-code): 섹션 프레이밍.
- [ai-agent-book · 10장](https://github.com/bojieli/ai-agent-book/blob/main/book/chapter10.md) (《深入理解 AI Agent》, 李博杰; 중국 원본은 정본입니다):
  풀 방식 상태 조회가 왜 약한지, 진행 파일과 궤적 추적, mtime 정체 감지, 매니저 패턴의 중앙 할당,
  그리고 팀 자원 스케줄링(하위 작업별 예산, 동시성 제한, 모델 배치, 선점).
  예산 인식 결과는 책 자체 실험에서 나온 것이므로 단일 출처입니다.
- [Plan-and-Act](https://arxiv.org/abs/2503.09572) (Erdogan 외, 2025): 실행기에서 플래너를 분리하고, 플랜의 품질이 결과를 결정하는 방식.
- [우리가 멀티 에이전트 연구 시스템을 구축한 방법](https://www.anthropic.com/engineering/multi-agent-research-system) (Anthropic, 2025):
  토큰 사용만으로 성능의 약 80% 변동을 설명하며, 그 다음은 도구 호출 횟수와 모델 선택입니다.
