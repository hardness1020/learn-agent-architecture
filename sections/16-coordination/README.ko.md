# 16 · Coordination

[English](README.md) · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · **한국어**

> lead가 task 규모에 맞춰 팀을 짜고, 팀원을 각자의 thread 위에 띄우며, 이들은 공유 inbox로 대화합니다.

agent 하나에는 context window 하나와 진행 중인 작업 흐름 하나만 있습니다. 큰 일에는 여러 agent가 동시에 붙어야 할 때가 많습니다.

subagent는 초점이 좁은 task를 맡을 수 있지만, 한 번에 끝나는 subagent는 시작한 뒤에 방향을 바꾸기 어렵습니다.

agent를 하나 더 두면 그만큼 token을 쓰고, agent 둘이 같은 파일을 서로 다른 방향으로 고칠 수도 있습니다.
그래서 먼저 정해야 하는 것은 팀의 모양입니다. agent를 몇 개 둘지, 하나의 context를 공유할지, 누가 누구에게 지시할지입니다.

협력하는 agent에는 서로를 띄우는 방법, 고정된 이름, 대화할 inbox, 그리고 permission 요청을 사람에게 되돌려 보내는 경로가 필요합니다.

coordination이 해야 하는 일은 다음과 같습니다.

1. agent에게 고정된 주소를 줍니다.
2. lead가 task에 맞춰 팀 규모를 정하고 팀을 구성하게 합니다.
3. lead가 팀원을 각자의 thread 위에 띄우게 합니다.
4. 팀원이 스스로 자기 inbox를 가져와 스크립트의 구동 없이 행동하게 합니다.
5. 게이트에 걸린 동작을 사람 승인자에게 올려 보냅니다.

이 계층이 없으면 큰 작업은 직렬로 남거나, 서로 협력하지 못하는 worker들로 쪼개집니다.

---

## 메커니즘

![Mechanism diagram](assets/16-coordination.png)

agent마다 inbox를 하나씩 가집니다. 메시지를 보낸다는 것은 수신자의 inbox에 쓴다는 뜻입니다. 전달은 수신자가 자기 inbox를 비울 때 일어납니다.

팀의 규모와 이름은 스크립트에 박아 두지 않고 실행 시점에 lead의 모델이 정합니다.
lead는 `TeamCreate`를 호출해 task에 맞는 팀을 구성한 다음, 구성원을 하나씩 띄웁니다.

lead가 팀원을 직접 손으로 시작하지는 않습니다. lead는 `SpawnTeammate`를 호출하고, harness가 팀원의 loop을 백그라운드 thread에서 돌립니다(섹션 13).
그다음부터는 팀원이 스스로 자기 inbox를 가져와 행동하므로, 스크립트가 누구도 구동하지 않습니다.

데모에는 중앙 브로커가 없습니다. 이름, inbox 경로, 메시지 형태에 대한 공유 규약만 있습니다.

- agent마다 inbox 하나를 가집니다.
- 메시지에는 발신자, 수신자, 내용이 있습니다.
- lead는 `TeamCreate`로 명단의 규모를 정하고 구성합니다. 그다음 `SpawnTeammate`가 구성원을 하나씩 시작합니다.
- lead는 `SpawnTeammate`로 팀원을 띄웁니다. 그 팀원은 자기 thread 위에서 돕니다.
- `to="*"`는 발신자를 뺀 모든 팀원에게 브로드캐스트합니다.
- 발신자는 쓰고 바로 돌아옵니다. 답을 기다리며 막히지 않습니다.
- 팀원은 폴링할 때마다 자기 inbox를 가져와, 새 메시지를 다음 turn에 접어 넣습니다.
- permission 요청도 같은 경로를 씁니다.

### 이번에 추가되는 것: 팀 구성

`TeamCreate`는 lead가 호출해 명단의 규모를 정하고 구성하는 tool입니다. 슬롯이 하나인 홀더를 채워 두면, harness가 구성원을 띄울 때 그 값을 다시 읽습니다.

```python
def team_tools(root, me, formed):                      # src/mailbox.py
    def create(a):
        members = list(dict.fromkeys([me, *a["members"]]))   # the lead joins its own team
        formed["team"] = Team(root, members)                 # the tool call sizes and forms the team
        return f"team created: {', '.join(members)}"
    ...                                                # SendMessage stays inert until the team exists
```

- 스크립트는 규모도 이름도 정하지 않습니다. 둘 다 lead가 task를 보고 고릅니다.
- `SendMessage`는 `TeamCreate`가 실행되기 전까지 아무 동작도 하지 않습니다. 그래서 lead는 대화하기 전에 팀부터 구성합니다.
- `formed`는 슬롯이 하나인 홀더입니다(ponytail: team registry를 대신하는 프로세스 내 임시 구현. 다른 프로세스의 팀원도 합류시키려면 명단 파일로 뒷받침하면 됩니다).

### 이번에 추가되는 것: 팀원 띄우기

`SpawnTeammate`는 lead의 모델이 호출하는 tool입니다. harness는 섹션 13의 런타임 위에서, 자기 thread로 팀원의 loop을 시작합니다.

```python
def teammate_tools(runtime, spawn_worker):             # src/mailbox.py
    def spawn(a):
        runtime.start(lambda: spawn_worker(a["name"]))  # section-13 thread runs the teammate's loop
        return f"spawned teammate {a['name']}; it runs on its own thread and pulls its own work"
    return [Tool("SpawnTeammate", spawn, is_read_only=True, ...)]
```

팀원의 loop은 `serve_mailbox`입니다. inbox를 가져오고, 행동하고, 반복합니다. 띄워진 thread 위에서 돌기 때문에, 팀원은 스크립트가 아니라 스스로 반응합니다.

```python
def serve_mailbox(team, me, work, *, poll=0.05, max_idle_polls=None):   # src/mailbox.py
    while True:
        chat = [m for m in team.drain(me) if isinstance(m["content"], str)]
        if chat:                                        # a message to act on
            folded = "\n".join(f"<message from={m['from']!r}>{m['content']}</message>" for m in chat)
            work(folded)                                # one inner loop (section 1) on the message
            continue
        time.sleep(poll)                                # empty: poll again
```

- `spawn_worker(name)`은 애플리케이션이 넘기는 thunk입니다. 해당 팀원을 위해 `serve_mailbox` loop을 하나 돌립니다.
- 팀원은 inbox를 비우면서 메시지를 소비합니다. 그래서 메시지는 정확히 한 번만 전달됩니다.
- 아직 정상 정지 절차는 없습니다. 이 thread는 프로세스와 함께 죽는 데몬입니다. 섹션 17에서 shutdown 핸드셰이크를 더합니다.
- `max_idle_polls`는 유휴 대기에 상한을 걸어 데모나 테스트가 끝나게 합니다. 실제 팀원은 프로세스가 멈출 때까지 폴링합니다.

### inbox와 permission 경로

context가 서로 다른 agent가 대화하는 방법은 두 가지이고, 프로세스가 쓰는 두 가지와 같습니다.
공유 메모리에서는 모두가 한곳을 읽고 쓰며 같은 상태를 봅니다.
메시지 전달에서는 발신자가 사본을 수신자 한 명에게 보내고, 둘은 아무것도 공유하지 않습니다.
이 둘을 실어 나르는 경로는 셋입니다. tool call 인자는 한 방향으로만 가고 답신 경로가 없습니다. 파일은 재시작을 견디지만 잠금이 필요합니다.
메시지 버스는 주소와 순서를 더하고, 디스크에 쓸 때만 재시작을 견딥니다.
여기서 inbox는 잠금이 걸린 파일이므로, 공유 파일시스템 위의 메시지 전달입니다.
팀 memory(섹션 9)와 task 보드(섹션 18)가 공유 메모리 쪽입니다.
대부분의 팀은 둘 다 원합니다. 일을 나눠 줄 때는 메시지를, 메시지보다 오래 남아야 할 사실에는 공유 메모리를 씁니다.

`mailbox.py`는 이름 붙은 inbox들로 이루어진 `Team`을 구현합니다.

```python
def send(self, frm, to, content):                      # src/mailbox.py
    targets = [m for m in self.members if m != frm] if to == "*" else [self._check(to)]
    with self._lock():                                 # serialize concurrent senders
        for t in targets:
            inbox = self._read(t)
            inbox.append({"from": frm, "to": t, "content": content})
            self._path(t).write_text(json.dumps(inbox))
```

- `_check`는 모르는 이름이 경로가 되기 전에 거부합니다.
- 잠금이 읽기, 수정, 쓰기를 직렬화하므로 동시에 보내는 발신자들이 메시지를 잃지 않습니다.
- `drain`은 inbox 하나를 읽고 비웁니다.

permission 올려 보내기는 approver의 한 구현입니다. 게이트에 걸린 호출을 같은 경로로 사람에게 옮깁니다.

```python
def bubbling_approver(team, me, lead, human=None, timeout=0.0, poll=0.05):
    def approve(name, args):                            # approver for an agent with no human UI
        team.send(me, lead, {"kind": "permission_request", "tool": name, "args": args})
        if human is not None:                           # the lead routes it to its approval UI
            team.send(lead, me, {"kind": "permission_response", "tool": name, "ok": human(name, args)})
        deadline = time.time() + timeout
        while True:
            resp = [m["content"] for m in team.drain(me)
                    if isinstance(m["content"], dict) and m["content"].get("kind") == "permission_response"]
            if resp:
                return bool(resp[-1]["ok"])
            if time.time() >= deadline:
                return False                            # nobody answered in time: default deny
            time.sleep(poll)
    return approve
```

1. 팀원이 게이트에 걸린 tool call에 닿지만, 자기 loop에는 키보드 앞에 앉은 사람이 없습니다.
2. approver가 `permission_request`를 lead의 inbox로 보냅니다.
3. lead는 그것을 자기 승인 UI로 넘깁니다(여기서는 `human` 콜백).
4. 판정은 `permission_response`로 팀원의 inbox에 돌아옵니다.
5. 팀원은 그 응답을 읽고 게이트에 허용 또는 거부를 돌려줍니다.

게이트는 여전히 `approver(name, args)`를 호출하며 바뀌지 않습니다. 답이 직접 호출이 아니라 inbox 메시지로 오기 때문에, 에스컬레이션이 같은 경로를 재사용합니다.

`human`이 없으면 답은 다른 곳에서 와야 합니다(다른 thread의 lead, 채팅 플랫폼의 사람).
approver는 `timeout`까지 자기 inbox를 폴링하다가 거부합니다. 답이 오지 않은 permission은 거부이지, 멈춤도 허용도 아닙니다.
이는 hermes-agent의 clarify gateway를 닮았습니다. 거기서는 `wait_for_response`가 채팅 어댑터가 답하거나 timeout이 발화할 때까지 agent thread를 막습니다.

### 기존 구조에 붙이는 방법

데모는 메인 agent 하나를 돌립니다. lead가 한 걸음을 내딛고, 팀원은 스스로 돕니다.

```python
def spawn_worker(name, formed, model):                 # src/demo.py, module level
    team = formed["team"]                              # whatever the lead formed with TeamCreate
    ...                                                 # build the teammate's tools
    return mailbox.serve_mailbox(team, name, work)      # the teammate pulls its own inbox

run_turn([...goal...], model, lead_reg, session)        # the one agent call in demo(): the lead
```

- 스크립트가 넣어 주는 입력은 lead의 목표 하나뿐입니다. lead는 `TeamCreate`로 팀 규모를 정하고, `SpawnTeammate`로 각각을 띄우고, `SendMessage`로 일을 나눠 줍니다.
- `demo()`는 `run_turn`을 한 번, lead의 것으로 돌립니다. 팀원 자신의 `run_turn`은 `spawn_worker` 안에 있고, spawn tool을 통해서만 닿습니다.
- 팀원은 각자 섹션 13의 thread 위에서 `serve_mailbox`를 돌립니다. 자기 inbox를 가져오고, 일하고, 답합니다. 답이 몇 번 오갈지는 lead가 정하고, 메인 프로세스는 기다리기만 합니다.
- `loop.py`는 범용으로 남습니다. 접어 넣기와 pull loop은 coordination이며, `run_turn` 안이 아니라 이 래퍼에서 처리합니다.
- permission 게이트는 바뀌지 않습니다. 게이트에 걸린 호출은 여전히 lead에게 올라갑니다.

### 더 읽을거리

여기 나오는 내용은 `src/`에 없습니다. ai-agent-book과 공개된 multi-agent 연구에서 온 것이고, 표에 있는 시스템들에서 확인된 내용은 아닙니다.

**팀이 agent 하나를 이기는 때.** 두 번째 agent는 첫 번째가 볼 수 없던 것을 가져올 때만 추가합니다.
테스트 결과, 스크린샷, 직접 받아 온 페이지, 돌고 있는 시스템에서 얻은 답 같은 것입니다. 그것이 새 정보입니다.
같은 글을 다시 읽고 투표하는 agent는 새 정보를 가져오지 않습니다. token만 씁니다.

이걸 잘못했을 때의 값을 공개된 결과 두 개가 보여 줍니다. Tran과 Kiela는 agent 하나와 팀에 같은 thinking token 예산을 줬습니다.
그들이 측정한 task에서는 agent 하나가 뒤지지 않았습니다. Anthropic은 자사 리서치 팀이 채팅 turn 하나의 약 열다섯 배 token을 쓴다고 보고합니다.
그만큼 비싼 팀이라면 뭔가를 가져와야 합니다.

**공유 context와 격리 context.** agent 둘은 하나의 히스토리를 공유하거나, 각자 따로 가집니다.

- **공유.** 다음 agent가 전부를 물려받으므로, 따로 싸서 넘길 것도 없고 빠뜨릴 사실도 없습니다.
  대가는 한 번에 agent 하나만 돈다는 것과, window 하나가 팀 전체의 히스토리를 담는다는 것입니다.
- **격리.** agent마다 자기 window를 받고, 필요한 것을 말해야 합니다. agent들이 동시에 돌고, 한 agent의 혼란은 자기 window에서 멈춥니다.
  대가는 인계할 때마다 내용을 적어야 한다는 것입니다.

하위 task가 적고, 히스토리가 window 하나에 들어가고, 단계가 어차피 순서대로 돈다면 공유를 고릅니다. 그 밖에는 격리합니다.
이 저장소는 격리 쪽입니다. subagent는 빈 상태로 시작하고(섹션 6), 팀원은 자기 inbox만 읽습니다.

**세 가지 토폴로지.** 격리된 agent도 누가 누구와 말하는지는 알아야 합니다. 모양은 셋입니다.

- **동등.** 지위가 같은 agent들이 서로에게 메시지를 보냅니다. 리뷰와 교차 확인이 여기 맞습니다.
- **관리자.** lead 하나가 일을 나누고, 배분하고, 돌아온 것을 합칩니다. child agent는 자기 히스토리가 아니라 요약을 돌려줍니다.
- **분산.** lead가 없습니다. agent마다 다음에 누가 일을 받을지 고릅니다.

이 섹션은 관리자 형태를 만듭니다. lead가 모두를 대신해 계획하므로, 나쁜 분할은 나쁜 채로 남고 worker는 그것을 고칠 수 없습니다.
그래서 lead에게 가장 강한 모델을, worker에게 더 싼 모델을 주자는 논거가 나옵니다.

**분산 팀이 일을 배분하는 방법.** lead가 없어도 일은 다음 agent를 찾아가야 합니다. 공개된 설계 셋, 경로 셋입니다.

- **MetaGPT**는 모든 메시지를 풀에 올립니다. 역할마다 자기가 처리하는 메시지 유형을 구독하므로, 발신자가 수신자를 지목하지 않습니다.
- **AutoGen**의 group chat은 transcript 하나를 유지하고, 중앙 선택기가 다음 발언자를 고릅니다. 선택기가 같은 두 agent만 계속 고르면 채팅이 라이브락에 빠집니다.
- **OpenAI Swarm**은 인계를 tool call로 만들고, 일이 손을 바꿀 수 있는 횟수에 상한을 둡니다. 그래서 인계의 사슬이 끝납니다.

**파일 트리의 네 영역.** agent는 이름으로 서로를 찾고, 경로로 상태를 찾습니다. 책은 트리를 네 영역으로 나눕니다.

- **전용 스크래치패드.** agent 하나의 초안입니다. 아무도 읽지 않으므로 조율할 것이 없습니다.
- **공유 작업 공간.** 저장소, task 보드, 팀 memory입니다. 팀원 모두가 여기에 쓰므로, 충돌은 여기서 납니다.
  잠금이 필요하거나, agent마다 worktree가 필요합니다(섹션 15).
- **외부 마운트.** checkout이나 데이터셋처럼 팀이 만들지 않은 데이터입니다. 여기에 쓰면 팀 바깥의 무언가가 바뀝니다.
- **읽기 전용 기본 제공물.** skill, prompt, tool 정의입니다(섹션 7과 2). 실행 중에 바뀌지 않으므로 모든 agent가 같은 사본을 봅니다.

상태를 잘못된 영역에 두면 조율 버그로 돌아옵니다. agent 둘이 한 파일을 고쳤다면 그 파일이 공유 작업 공간에 있었다는 뜻입니다.
같은 사실을 세 번 보냈다면 그것은 팀 memory로 갔어야 합니다.

**인계가 담는 것.** 팀원은 lead의 채팅을 볼 수 없으므로, "실패하는 테스트를 고쳐라"만으로는 할 수 있는 게 없습니다. 인계 패킷은 세 가지를 담습니다.

1. task와, 수신자가 스스로 확인할 수 있는 완료 기준.
2. 이미 확인된 사실과 유효한 제약. 수신자가 다시 찾아보거나 깨뜨리지 않게 합니다.
3. 파일, 로그, 브랜치로 가는 경로.

발신자의 원본 히스토리는 뺍니다. 길고, 막다른 길로 가득하고, 수신자에게 발신자의 실수를 읽히게 만듭니다.

공유 context 인계는 다른 선택지이며 패킷을 건너뜁니다. agent 하나가 제어를 다른 하나에게 넘기고 히스토리 전체가 따라가므로, 남겨지는 것이 없습니다.
책은 역할 사이로 제어를 옮기는 tool로 이것을 보여 줍니다. 저자 자신의 실험이므로 하나의 소스로만 다루면 됩니다.
대가는 제어를 가진 agent가 하나뿐이라 아무것도 동시에 돌지 않는다는 것입니다. 패킷은 쓰는 수고가 들지만 병렬로 도는 작업을 사 옵니다.

> **다음:** 여기의 팀원은 정상 정지 절차가 없는 데몬이고, 메시지에만 반응합니다.
> 섹션 17은 shutdown 핸드셰이크를 더해 lead가 팀원을 깔끔하게 끝낼 수 있게 합니다.
> 섹션 18은 공유 task 보드를 더해, 유휴 팀원이 메시지를 기다리는 대신 스스로 일을 선점하게 합니다.

---

## 시스템별

각 설계가 협력하는 agent를 어떻게 띄우고 일을 어떻게 퍼뜨리는지 비교합니다.

| | Claude Code | Hermes Agent | deepseek-harness |
| --- | --- | --- | --- |
| **장점** | 동등한 agent끼리 직접 대화함. 파일 inbox는 프로세스를 넘나듦. | child agent를 어느 인터페이스에서든 일시 정지하고 끼어들 수 있음. | 스크립트가 강한 상한 아래에서 많은 child agent로 fan out 함. |
| **단점** | 폴링과 잠금 비용. 메모리 inbox는 프로세스와 함께 사라짐. | 동등한 agent용 inbox가 없음. clarify가 자기 thread를 막음. | child agent끼리 대화할 수 없음. 보내도 답이 없음. |
| **이유** | 동등한 agent에는 inbox와, 사람 승인자로 가는 경로가 필요함. | 조율은 부모에서 자식으로만 감. | 조율이 곧 소유권. child agent마다 부모가 하나임. |
| **방법: 팀원** | 프로세스 내 또는 원격이며, 각자 자기 loop을 돌림. | thread 위의 위임된 child agent, 일시 정지 플래그 포함. | 모델이 쓴 스크립트가 띄우고, 일부는 상주함. |
| **방법: 통신 경로** | SendMessage가 inbox에 쓰고 브로드캐스트도 가능함. | 완료 큐와 gateway 호출. | 부모에서 자식으로만. 자식은 보고 tool로 답함. |
| **방법: 공유 메모리** | 팀 task 목록과 팀 memory 디렉터리. | 공유 session DB와 계보 표시. | 부모의 디렉터리. fork는 끝난 turn도 함께 복사함. |
| **방법: permission 올려 보내기** | 원격 요청이 로컬 승인 프롬프트가 됨. | clarify는 채팅으로 감. child agent는 자동 거부 또는 자동 승인함. | 요청이 부모 사슬을 타고 올라감. |

---

## 실패 모드

- **메시지 유실 경쟁.** 발신자 둘이 한 inbox에 동시에 씁니다. 읽기, 수정, 쓰기를 잠급니다.
- **동등 agent 교착.** agent들이 서로를 기다립니다. 보내기에서 막지 말고, 메시지를 큐에 넣고 turn 사이에 비웁니다.
- **permission 멈춤.** 팀원에게는 사람이 쓰는 UI가 없습니다. 요청을 lead에게 올려 보냅니다.
- **생성 전 spawn.** lead가 `TeamCreate` 전에 spawn이나 메시지를 시도해 명단이 없습니다. 팀이 생기기 전까지 둘 다 아무 동작도 하지 않게 둡니다.
- **버려진 팀원.** 띄워진 팀원이 일이 끝난 뒤에도 계속 폴링합니다. 유휴 대기에 상한을 두거나, 섹션 17의 핸드셰이크로 멈춥니다.
- **모호한 agent 간 메시지.** 팀원은 lead의 채팅을 볼 수 없습니다. 패킷을 보냅니다. task, 완료 기준, 확인된 사실, 산출물 경로입니다.
- **채팅을 memory처럼 씀.** 오래 남아야 하는 공유 사실은 팀 memory에 둡니다.
- **비잔틴 팀원.** 실패한 agent는 죽지 않습니다. 틀린 답을 돌려주면서 확신에 찬 말투를 씁니다.
  같은 근거로 재시도하거나 투표해도 같은 답이 돌아옵니다. 모델 바깥의 무언가와 대조해야만 잡힙니다.
- **공유 파일의 갱신 유실.** agent 둘이 한 파일을 읽고 둘 다 다시 씁니다. 먼저 쓴 내용이 사라집니다.
  쓰기를 잠그거나, 버전 번호를 저장해 두고 맞지 않으면 재시도합니다.
- **의미 충돌.** 두 쓰기가 모두 깔끔하게 적용되는데 결과는 여전히 깨져 있습니다. 한 agent가 함수 이름을 바꾸는 동안 다른 agent가 옛 이름을 호출하는 코드를 더한 경우입니다.
  같은 대상을 두 agent가 맡지 않도록 일을 나누거나, 한 지점에서 병합합니다.
- **오류 연쇄.** agent 하나가 사실을 틀립니다. 다음 agent가 그것을 반복하고, 그다음도 다시 반복하면, 그쯤에는 확인된 사실처럼 읽힙니다.
  결론만 보는 리뷰어는 그것들이 서로 일관적이라고 판단합니다. 원본 근거를 확인할 사람을 두되, 그것을 만든 agent에게 맡기지 않습니다.

---

## 실행 방법

[`src/`](src/)는 15의 코드를 이어받아 다음을 더합니다.

- [`mailbox.py`](src/mailbox.py): 잠금이 있는 이름 붙은 inbox, 접어 넣기, `serve_mailbox` loop, timeout과 기본 거부가 있는 올려 보내기, 그리고 팀 tool들.
- [`test.py`](src/test.py): 주소 지정, 브로드캐스트, 동시 전송, 접어 넣기, 올려 보내기(인라인, 비동기, timeout 거부), mailbox loop, 팀 tool을 확인합니다.
- [`demo.py`](src/demo.py): lead가 한 걸음을 내딛고(`TeamCreate`, `SpawnTeammate`, `SendMessage`), 팀원은 각자 자기 inbox를 가져와 게이트가 걸린 shell task를 돌리고 결과를 보고합니다.

loop과 subagent 경로는 그대로입니다. coordination은 팀원을 띄우고, inbox를 비우고, approver를 넘기는 방식으로 turn을 감쌉니다.

```bash
python sections/16-coordination/src/test.py         # offline checks, no key
uv run python sections/16-coordination/src/demo.py  # live demo, needs a key
```

---

## 출처

- [Claude Code tools and inboxes](https://github.com/yasasbanukaofficial/claude-code):
  `tools/SendMessageTool/`, `tools/TeamCreateTool/`, `utils/mailbox.ts`, `utils/teammateMailbox.ts`.
- [Claude Code teammates](https://github.com/yasasbanukaofficial/claude-code):
  `tasks/InProcessTeammateTask/`, `tasks/RemoteAgentTask/`, `remote/remotePermissionBridge.ts`, `memdir/teamMemPaths.ts`.
- [Hermes Agent source](https://github.com/NousResearch/hermes-agent): `tools/delegate_tool.py`, `tools/async_delegation.py`, `tools/clarify_gateway.py`, `tools/interrupt.py`.
- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness) at `dsh-v0.1.0-rc.7`:
  `docs/subsystems/workflow.md`, `docs/subsystems/subagent.md`, `docs/subsystems/core.md`,
  `packages/workflow/workflow-worker-thread/README.md`, `packages/subagent/tool-subagent-report/README.md`.
- [learn-claude-code · s15_agent_teams](https://github.com/shareAI-lab/learn-claude-code): 섹션 구성 참고.
- [ai-agent-book](https://github.com/bojieli/ai-agent-book): `book/chapter10.md` (多 Agent 协作), 중국어 원문이 정본입니다.
  context 공유, 토폴로지 분류, 파일시스템 영역, 인계 패킷. 역할 이양 데모는 저자 자신의 실험입니다.
- Cemri et al., *Why Do Multi-Agent LLM Systems Fail?* ([arXiv:2503.13657](https://arxiv.org/abs/2503.13657)): MAST 분류와 비잔틴 관점.
- Tran, Kiela, *Single-Agent LLMs Outperform Multi-Agent Systems Under Equal Thinking Token Budgets* ([arXiv:2604.02460](https://arxiv.org/abs/2604.02460)).
- Erdogan et al., *Plan-and-Act* ([arXiv:2503.09572](https://arxiv.org/abs/2503.09572)): 계획 품질이 실행의 상한을 정합니다.
- Anthropic, [*How we built our multi-agent research system*](https://www.anthropic.com/engineering/multi-agent-research-system): 리서치 팀의 token 비용.
- [MetaGPT](https://arxiv.org/abs/2308.00352), [AutoGen](https://arxiv.org/abs/2308.08155), [OpenAI Swarm](https://github.com/openai/swarm): 분산 배분과 인계 상한.
