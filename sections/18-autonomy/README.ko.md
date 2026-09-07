# 18 · Autonomy

[English](README.md) · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · **한국어**

> 사람의 prompt 없이 loop을 돌립니다. 놀고 있으면 보드를 훑고, 준비된 task를 선점하고, 처리합니다.

autonomy는 섹션 1(agent loop)을 turn마다 사람이 prompt를 넣어 주지 않은 채 돌리는 것입니다.

팀을 띄우면 lead가 worker마다 다음 task를 건네주는 설계가 먼저 떠오릅니다.

그 방식은 규모를 감당하지 못합니다. 선점되지 않은 task가 열 개면 수동 배정도 열 번이고, lead가 병목이 됩니다.

일을 끝내자마자 노는 worker는 방금 채워 놓은 context도 낭비합니다.

해법은 중앙 배정이 아니라 자기 조직화입니다.

중앙 배정도 여전히 실제로 쓰이는 설계이고, 공개된 multi-agent 연구는 대부분 그쪽을 설명합니다.
관리자 패턴에서는 child agent 하나하나가 tool로 등록되고, 관리자 하나가 모든 하위 task를 나눠 줍니다.
관리자가 계획 전체를 쥐고 있으므로 일의 순서를 정하고, 중복 task를 버리고, 실행을 일찍 끝낼 수 있습니다.
대신 모든 task를 두 번씩 거칩니다. 한 번은 나눠 주려고, 한 번은 결과를 읽으려고 거칩니다.
두 설계는 드는 비용이 다릅니다. 관리자는 전역 순서를 하나 주지만, 모든 task가 관리자의 차례를 기다립니다.
보드는 처리량을 주지만, 선점 정보가 금방 낡기 때문에 잠금이 필요합니다. 이 섹션은 보드 쪽을 만듭니다.

autonomy는 노는 agent가 다음을 할 수 있게 해야 합니다.

1. 할 일이 없다는 것을 알아챕니다(작업 단계가 `end_turn`에 도달).
2. 주인이 없고 막혀 있지도 않은 task를 공유 보드에서 찾습니다.
3. 노는 다른 agent와 경쟁하지 않고 하나를 선점합니다.
4. 선점한 task로 loop에 다시 들어가고, 보드가 빌 때까지 반복합니다.

이것이 없으면 모든 agent가 꼭두각시입니다. 사람이나 lead가 다음 prompt를 밀어 넣어 주기를 기다리므로, 처리량은 배분하는 쪽이 prompt를 얼마나 빨리 내주느냐로 묶입니다.

---

## 메커니즘

![Mechanism diagram](assets/18-autonomy.png)

바깥 loop이 agent loop을 감쌉니다.

안쪽 loop은 섹션 1의 평범한 `while`입니다. `end_turn`에 도달해도 agent는 반환하지 않습니다. 폴링으로 들어갑니다.

폴링은 두 경로를 비웁니다. 이 agent 앞으로 온 메시지를 담는 지정 inbox(섹션 16)와, 노는 agent라면 누구나 선점할 수 있는 task를 담는 비지정 보드(섹션 12)입니다.

확인 순서는 우선순위대로입니다. shutdown 요청이 먼저, 그다음 inbox 메시지, 그다음 보드의 task입니다.

찾은 것이 무엇이든 다음 prompt가 되고, 안쪽 loop이 다시 돕니다.

- 안쪽 loop은 모델의 `stop_reason`으로 끝납니다. 섹션 1과 같은 신호입니다.
- 폴링은 shutdown을 먼저 확인하므로, 정지가 동료들의 메시지에 묻히지 않습니다.
- 선점은 잠금 아래에서 읽고, 확인하고, 쓰는 것입니다. 주인이 없고 막히지 않은 task를 고른 다음, 다른 agent보다 먼저 소유권을 씁니다.
- task는 의존 대상이 모두 `completed`일 때만 선점할 수 있습니다. 그래서 막힌 일을 선점하는 agent가 없습니다.

shutdown 요청과 그 확인은 섹션 17의 protocol입니다. 그래서 정지는 강제 종료가 아니라 핸드셰이크입니다.

### 이번에 추가되는 것: 유휴 폴링

`autonomy.py`는 바깥 loop과 폴링 한 번을 더합니다. `next_action`은 inbox를 한 번 비운 다음, 우선순위대로 가장 먼저 찾은 것을 돌려줍니다.

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

- 셋 중 가장 먼저 찾은 것을 돌려줍니다. shutdown(확인하고 정지), 접어 넣은 잡담, 선점한 task 순입니다.
- shutdown을 잡담보다 먼저 확인하므로, 동료들이 보내는 메시지가 정지를 리소스 부족에 빠뜨릴 수 없습니다(섹션 16).
- `claim_next`는 대기 중이고 주인이 없는 첫 task를 선점합니다. `TaskStore.claim`은 막힌 일을 거부하고 선점을 직렬화합니다(섹션 12).
- `None`은 유휴 상태라는 뜻입니다. 바깥 loop이 잠시 자고 다시 폴링합니다.

### 잠금 아래의 선점

폴링은 task를 제안하고, 잠금이 누가 가져갈지 정합니다. `claim_next`는 보드를 오래된 것부터 훑어, 주인이 없고 대기 중인 첫 task를 제안합니다.

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

`claim_next`의 확인은 힌트일 뿐입니다. 노는 agent 둘이 같은 task를 동시에 주인 없음으로 읽을 수 있습니다. `TaskStore.claim`(섹션 12)이 잠금 아래에서 이것을 정리합니다.

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

- 잠금이 읽기, 확인, 쓰기를 하나의 원자적 단계로 만듭니다. 그래서 확인이 쓰기 전에 낡을 수 없습니다.
- 진 쪽은 잠금 안에서 다시 읽어 `owner`가 설정된 것을 보고 `already_claimed`를 받습니다. `claim_next`는 다음 task로 넘어갑니다.
- 막힌 task도 여기서 거부됩니다. 그래서 의존 대상이 `completed`가 아닌 일을 선점하는 agent가 없습니다.
- thread 둘이 공유 상태를 두고 다투는 곳은 여기뿐입니다. 폴링의 나머지는 전부 지역적입니다.

### 기존 구조에 붙이는 방법

바깥 loop이 `run_turn`을 바깥에서 감싸므로, loop과 subagent 경로는 바뀌지 않습니다.

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

- 이 `run_teammate`는 섹션 17의 것에 폴링 대상 하나, 즉 task 보드를 더한 것입니다. shutdown(섹션 17)과 잡담(섹션 16)은 그대로입니다.
- `work(prompt, claimed)`는 선점한 task에 대해 안쪽 loop을 `end_turn`까지 한 번 돌리고, 그다음 agent가 자기가 여유롭다고 알립니다.
- 선점한 task가 다음 prompt가 됩니다. 폴링에서 아무것도 못 찾으면 worker가 스스로 멈출 시점을 정합니다.
- 그 정지는 둘 중 하나입니다. shutdown 핸드셰이크가 올 때까지 노는 방식(섹션 17)이거나, 유한한 보드에서 빈 폴링이 정해진 횟수를 넘으면 정리하고 끝내는 방식입니다.
- 여기서는 worker 하나가 돌지만, loop은 agent마다 하나씩입니다. 실제 팀은 lead loop 하나와 worker loop 여럿을 하나의 공유 보드와 inbox 묶음 위에서 동시에 돌립니다.
- lead가 능동적으로 하는 일은 한 걸음입니다. tool을 호출해 팀과 일감을 만들고, 그것으로 끝입니다.
- `TeamCreate`와 `SpawnTeammate`는 섹션 16의 tool입니다. `TaskCreate`가 보드에 일감을 올립니다(섹션 12).
- `SpawnTeammate`는 `runtime.start(...)`입니다(섹션 13). lead의 tool call이 thread 위에서 worker의 autonomy loop을 시작합니다.
- 띄운 뒤로는 일을 가져오는 것도 언제 멈출지 정하는 것도 각 worker 자신의 몫이고, lead나 스크립트의 몫이 아닙니다. 메인 프로세스는 worker들이 정리하고 끝나기를 기다리기만 합니다.
- 팀 구성, 띄우기, 보드에 일감 올리기는 모델의 결정입니다(섹션 16과 12). 자율적인 선점이 섹션 18이 더하는 부분입니다.

### 더 읽을거리

여기 나오는 내용은 `src/`에 없습니다. ai-agent-book과 공개된 연구에서 온 것이고, 표에 있는 시스템들에서 확인된 내용은 아닙니다.

**바쁜 worker에게 물어보기.** 폴링은 worker에게 다음에 무엇을 할지 알려 줍니다. lead에게 돌고 있는 worker의 상태를 알려 주지는 않습니다.

**상태 조회 호출이 약한 이유.** tool call 도중인 worker는 메시지를 듣고 있지 않습니다. 그래서 호출은 멈춰 있거나 빈 채로 돌아옵니다.
정말로 막힌 worker가 바로 답하지 않는 worker입니다.

**실제로 통하는 세 가지 방법.** 첫 번째는 worker의 협조가 필요하고, 마지막은 아무것도 필요 없습니다.

1. 메시지로 묻습니다. inbox에 상태 요청을 넣어 둡니다(섹션 16). worker가 다음 폴링에서 답합니다. 정확하지만, worker가 아직 폴링하고 있을 때만 통합니다.
2. 약속된 진행 상황 파일을 읽습니다. worker가 양쪽이 아는 경로에 단계마다 한 줄씩 더합니다. 읽는 쪽은 작업을 방해하지 않습니다.
3. 저장된 trajectory를 따라 읽습니다. 런타임이 이미 모든 turn을 디스크에 씁니다(섹션 13). lead가 그 turn들을 읽고, worker는 아무것도 하지 않습니다.

**멈춤 알아채기.** 뒤의 두 방법에서는 이것이 거저 따라옵니다. 파일이 마지막으로 쓰인 시각을 봅니다. 새 쓰기가 없다는 것은 새 진전이 없다는 뜻이기 때문입니다.
임계값 하나가 그것을 판단으로 바꿔 줍니다. 느린 tool call은 도는 동안 아무것도 쓰지 않으므로, 예상되는 가장 느린 호출보다 임계값을 위로 잡습니다. 그것을 넘으면 worker가 막힌 것으로 봅니다.
그 임계값이 바로 바쁜 채로 막히는 실패 모드에 없던 방아쇠입니다. lead는 영원히 기다리는 대신 task를 회수하거나 shutdown 핸드셰이크를 시작할 수 있습니다(섹션 17).

**worker 풀의 예산.** 폴링에는 worker에게 선점을 그만하라고 말해 주는 것이 없습니다. 노는 worker는 매번 task를 하나 더 가져갑니다.
그래서 실행은 일이 끝났을 때가 아니라 예산이 떨어졌을 때 끝납니다.

**무엇을 배분할 것인가.** 공개된 어느 multi-agent 시스템에서는 token 사용량만으로 실행 성적 차이의 약 80%가 설명됐습니다.
그러니 배분할 단위는 turn이 아니라 token입니다. 보드와 worker 풀에 붙는 조절 손잡이는 넷입니다.

- task별 예산. task마다 자기 단계 상한과 token 상한을 가지며, 보드에 올릴 때 적어 둡니다. 그러면 폭주하는 task 하나가 실행 전체를 말려 버릴 수 없습니다.
- 동시 실행 상한. `in_progress`에 동시에 몇 개까지 둘지 제한합니다. 보드가 이미 그 수를 세고 있으므로, 상한을 넘는 선점은 그냥 실패합니다.
- 모델 배치. 가장 강한 모델을 사고가 가장 어려운 자리에 둡니다. 계획 품질이 결과를 정하므로 lead가 그 모델을 받고, 반복 작업을 하는 worker는 더 싼 모델로 돕니다.
- 선점 회수. 예산을 넘겼거나 임계값을 넘겨 멈춘 worker는 task를 보드로 되돌립니다. 다음에 선점하는 쪽은 깨끗한 상태에서 시작합니다.

**worker에게 자기 예산을 보여 주기.** 남은 양을 아는 agent는 그냥 더 큰 상한을 받은 agent와 다르게 씁니다.
이 결과는 책 저자 자신의 실험에서 나온 것이므로 소스가 하나입니다. 베껴 쓸 수치가 아니라 시험해 볼 것으로 다루면 됩니다.

---

## 시스템별

노는 agent가 자기 일을 어떻게 찾고 어떻게 선점하는지 비교합니다.

| | Claude Code | deepseek-harness |
| --- | --- | --- |
| **장점** | 배분하는 쪽이 병목이 되지 않음. 감시자가 다른 곳에서 만들어진 task까지 집어 감. | 사람 없이 도는 실행이 예측 가능하고, 이어서 도는 상태가 오래 남음. |
| **단점** | 노는 agent 둘이 한 task를 노릴 수 있어, 잠금이 경쟁을 정리해야 함. | agent 하나에 목표 하나. 목표 달성 여부를 모델 자신이 판단함. |
| **이유** | 모든 task를 나눠 주는 lead는 병목이 됨. | autonomy는 모드가 아니라 예산이 붙은 permission 수준임. |
| **방법: 유휴 시 동작** | 500ms 폴링. shutdown, 그다음 읽지 않은 메시지, 그다음 선점. | 완전히 놀고 있으면 다음 라운드를 예약하고 prompt 하나를 큐에 넣음. |
| **방법: 일 선점** | 막히지 않은 task의 소유권을 잠금 아래에서 씀. 그래서 선점자는 하나만 이김. | 목표의 정확한 리비전을 기준으로 다음 라운드를 예약하거나 실패함. |
| **방법: 자기 조직화** | worker가 보드에서 일을 가져감(섹션 12). lead는 종합만 하고 배분하지 않음. | 보드가 없음. agent가 자기 목표를 이어 가고 fan out에는 상한이 있음. |

---

## 실패 모드

- **선점 경쟁.** agent 둘이 같은 task를 주인 없음으로 읽고 둘 다 선점해, 한쪽의 작업이 사라집니다. 확인과 쓰기를 원자적으로 만드는 파일 잠금 안에서 선점합니다(섹션 12).
- **잡담에 의한 리소스 부족.** 동료들의 잡담이 shutdown 요청을 묻어 버려, 멈춰야 할 agent가 계속 폴링합니다. 일반 메시지보다 shutdown을 먼저 확인합니다(섹션 16).
- **막힌 일의 성급한 선점.** agent가 의존 대상이 끝나지 않은 task를 선점하고 그대로 멈춥니다. `blockedBy`에 해소되지 않은 id가 있는 task는 건너뜁니다(섹션 12).
- **compaction 이후의 정체성 상실.** 오래 도는 팀원이 실행 도중 자동 compaction을 겪고(섹션 8) 자기 역할을 잊습니다. system prompt를 보존해 역할이 살아남게 합니다.
- **바쁜 채로 막힘, 또는 논 채로 막힘.** `end_turn`에 끝내 닿지 않는 단계는 자원을 놓지 않고, 빠져나갈 길 없는 폴링은 헛돕니다. 정지 신호로 끝내고(섹션 1), 폴링마다 중단 여부를 확인합니다.
- **감지되지 않는 멈춤.** worker가 task를 쥔 채 바빠 보이지만 진전이 없어, 보드가 그것을 영영 놓지 못합니다. 진행 상황 파일이 마지막으로 쓰인 시각을 지켜보다가 task를 회수합니다.
- **worker 풀의 무제한 지출.** 노는 worker가 계속 선점하므로, 실행은 예산이 떨어져야만 끝납니다. task마다 단계 상한과 token 상한을 주고, 동시에 도는 개수에도 상한을 둡니다.
- **회수 시 이중 쓰기.** 놓아 준 task를 옛 worker가 아직 도는 동안 다시 선점해, agent 둘이 같은 파일에 씁니다. 정지 확인 응답을 받은 뒤에만 놓아 줍니다(섹션 17).

---

## 실행 방법

[`src/`](src/)는 17의 코드를 이어받아 다음을 더합니다.

- [`autonomy.py`](src/autonomy.py): 섹션 12의 보드 위에서 도는 바깥 loop과 유휴 폴링(섹션 16의 `SpawnTeammate`가 각 worker를 시작합니다).
- [`test.py`](src/test.py): worker 하나짜리 메커니즘, `TeamCreate` 확인, 강제로 만든 선점 경쟁(thread 16개, task 1개, 승자 1명), thread 파이프라인, spawn tool 확인.
- [`demo.py`](src/demo.py): lead가 한 걸음을 내딛고(`TeamCreate`, `TaskCreate`, `SpawnTeammate`), 그다음 worker들이 보드에서 task를 가져가다가 보드가 비면 스스로 멈춥니다.

메커니즘 절에 나온 worker 하나짜리 `run_teammate`는 설명용으로 줄인 것입니다.

실제 팀은 lead loop 하나와 worker loop 여럿을 하나의 공유 보드와 inbox 묶음 위에서 동시에 돌립니다.

섹션 13이 thread 위에서 일을 시작하고, 섹션 12와 섹션 16의 파일 잠금이 경쟁 상황에서도 공유 상태를 안전하게 지킵니다.

동시 실행 데모와 선점 경쟁 테스트가 그것을 엮어 놓았습니다.

```bash
python sections/18-autonomy/src/test.py         # offline checks, no key
uv run python sections/18-autonomy/src/demo.py  # live demo, needs a key
```

---

## 출처

- [Claude Code autonomy](https://github.com/yasasbanukaofficial/claude-code):
  `utils/swarm/inProcessRunner.ts` (`runInProcessTeammate`, `waitForNextPromptOrShutdown`, `findAvailableTask`, `tryClaimNextTask`, `sendIdleNotification`).
- [Claude Code claim and watch](https://github.com/yasasbanukaofficial/claude-code):
  `utils/tasks.ts` (`claimTask`, `claimTaskWithBusyCheck` under `proper-lockfile`), `hooks/useTaskListWatcher.ts`, `coordinator/coordinatorMode.ts`.
- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness) at `dsh-v0.1.0-rc.7`:
  `packages/bundle/headless/README.md`, `packages/goal/goal-round-driver/README.md`, `packages/workflow/tool-ralph/README.md`,
  `docs/subsystems/permission-presets.md`, `docs/subsystems/goal.md`.
- [learn-claude-code · s17 autonomous agents](https://github.com/shareAI-lab/learn-claude-code): 섹션 구성 참고.
- [ai-agent-book · chapter 10](https://github.com/bojieli/ai-agent-book/blob/main/book/chapter10.md) (《深入理解 AI Agent》, 李博杰; 중국어 원문이 정본입니다):
  끌어오는 방식의 상태 조회가 약한 이유, 진행 상황 파일과 trajectory 따라 읽기, mtime으로 멈춤 감지하기, 관리자 패턴의 중앙 배정,
  그리고 팀 자원 스케줄링(하위 task별 예산, 동시 실행 상한, 모델 배치, 선점 회수).
  예산 인지 결과는 책 저자 자신의 실험에서 나온 것이므로 소스가 하나입니다.
- [Plan-and-Act](https://arxiv.org/abs/2503.09572) (Erdogan et al., 2025): 계획자와 실행자를 나누는 방식. 결과를 좌우하는 것은 계획 품질입니다.
- [How we built our multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system) (Anthropic, 2025):
  token 사용량만으로 성능 분산의 약 80%가 설명되고, 그다음이 tool call 횟수와 모델 선택입니다.
