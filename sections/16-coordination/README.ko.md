# 16 · Coordination

[English](README.md) · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · **한국어**

> 리드는 작업에 맞는 크기의 팀을 구성하고, 각자의 스레드에서 팀원을 생성하며, 공유된 인박스에서 대화합니다.

한 요원은 하나의 context window와 하나의 활성 작업 라인을 가집니다. 큰 작업은 종종 여러 요원이 동시에 작업해야 합니다.

subagent는 집중된 작업을 수행할 수 있지만, 한 번 사용하는 subagent는 시작 후에 조정하기 어렵습니다.

추가 요원마다 토큰 비용이 들며, 두 요원이 같은 파일을 다른 방향으로 편집할 수 있습니다.
따라서 첫 번째 결정은 팀의 형태입니다: 요원의 수, 하나의 컨텍스트를 공유할지 여부, 누가 누구에게 무엇을 지시할지.

조정된 요원은 서로를 생성할 방법, 안정적인 이름, 대화용 인박스, 인간에게 권한 요청을 보내는 방법이 필요합니다.

조정은 다음을 수행해야 합니다:

1. 에이전트에게 안정적인 주소를 제공합니다.
2. 리드가 크기를 정하고 작업을 위해 팀을 구성하게 합니다.
3. 리드가 각 팀원을 자체 스레드에 생성하게 합니다.
4. 각 팀원이 받은 편지함을 확인하고 스크립트 없이 행동하게 합니다.
5. 제한된 행동을 인간 승인자에게 전달합니다.

이 단계가 없으면, 큰 작업은 직렬로 유지되거나 협업할 수 없는 작업자들로 분할됩니다.

---

## 메커니즘

![메커니즘 다이어그램](assets/16-coordination.png)

각 에이전트는 받은 편지함을 소유합니다. 메시지를 보내는 것은 수신자의 받은 편지함에 쓰는 것을 의미합니다. 전달은 수신자가 받은 편지함을 비울 때 발생합니다.

팀의 크기와 이름은 스크립트에 하드코딩되지 않고 리드 모델에 의해 실행 시 결정됩니다.
리드는 `TeamCreate`를 호출하여 작업을 위해 팀을 구성한 후 각 멤버를 생성합니다.

리드는 팀원을 직접 시작하지 않습니다. 리드는 `SpawnTeammate`를 호출하고, harness는 백그라운드 스레드에서 팀원의 루프를 실행합니다(섹션 13).
그런 다음 팀원은 자신의 인박스를 확인하고 동작하므로 스크립트가 아무도 직접 조종하지 않습니다.

데모에는 중앙 브로커가 없습니다. 이름, 인박스 경로, 메시지 형식에 대한 공통 규약이 있습니다.

- 각 에이전트는 하나의 인박스를 소유합니다.
- 메시지는 발신자, 수신자, 내용이 있습니다.
- 리드는 `TeamCreate`를 호출하여 명단을 구성하고 크기를 정합니다; 이후 `SpawnTeammate`가 각 멤버를 시작합니다.
- 리드는 `SpawnTeammate`를 사용해 팀원을 생성합니다; 해당 팀원은 자체 스레드에서 실행됩니다.
- `to="*"`는 발신자를 제외한 모든 팀원에게 방송합니다.
- 보낸 사람은 작성하고 반환합니다. 그들은 답장을 기다리며 차단하지 않습니다.
- 팀원은 각 폴링마다 자신의 받은 편지함을 확인하고 새 메시지를 다음 턴으로 접어 넣습니다.
- 권한 요청은 동일한 채널을 사용합니다.

### 새로움: 팀 구성

`TeamCreate`는 리더가 호출하여 명단 크기를 정하고 팀을 구성하는 도구입니다. 각 멤버가 생성될 때 harness가 읽는 한 슬롯의 공간을 채웁니다:

```python
def team_tools(root, me, formed):                      # src/mailbox.py
    def create(a):
        members = list(dict.fromkeys([me, *a["members"]]))   # the lead joins its own team
        formed["team"] = Team(root, members)                 # the tool call sizes and forms the team
        return f"team created: {', '.join(members)}"
    ...                                                # SendMessage stays inert until the team exists
```

- 스크립트는 크기나 이름을 고정하지 않습니다; 리더가 작업에서 둘 다 선택합니다.
- `SendMessage`는 `TeamCreate`가 실행될 때까지 비활성 상태이므로, 리더가 팀과 대화할 수 있기 전에 팀을 구성합니다.
- `formed`는 한 슬롯 홀더입니다 (포니테일: 진행 중인 팀 등록의 임시 대리; 다른 프로세스의 팀원이 참여할 수 있도록 명단 파일로 지원하세요).

### 새로움: 팀원 생성

`SpawnTeammate`는 팀장의 모델이 호출하는 도구입니다. harness는 Section-13 runtime에서 팀원의 루프를 자체 스레드에서 시작합니다:

```python
def teammate_tools(runtime, spawn_worker):             # src/mailbox.py
    def spawn(a):
        runtime.start(lambda: spawn_worker(a["name"]))  # section-13 thread runs the teammate's loop
        return f"spawned teammate {a['name']}; it runs on its own thread and pulls its own work"
    return [Tool("SpawnTeammate", spawn, is_read_only=True, ...)]
```

팀원의 루프는 `serve_mailbox`입니다: 받은 편지함을 확인하고, 행동하며, 반복합니다. 이는 생성된 스레드에서 실행되므로, 팀원은 스크립트가 아닌 자체적으로 반응합니다:

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

- `spawn_worker(name)`는 앱의 thunk입니다; 해당 팀원을 위해 한 번의 `serve_mailbox` 루프를 실행합니다.
- 팀원은 메시지를 소모하면서 처리하므로, 메시지는 한 번만 전달됩니다.
- 아직 우아한 종료 방법은 없습니다. 스레드는 프로세스와 함께 종료되는 데몬입니다. 섹션 17에서는 종료 핸드셰이크를 추가합니다.
- `max_idle_polls`는 아이들 대기 시간을 제한하여 데모나 테스트가 종료되도록 합니다; 실제 팀원은 프로세스가 중지될 때까지 폴링합니다.

### 인박스와 권한 채널

별도의 컨텍스트를 가진 에이전트는 프로세스가 가진 것과 같은 두 가지 방법으로 통신할 수 있습니다.
공유 메모리를 사용하면, 모든 사람이 한 곳을 읽고 쓰며 동일한 상태를 봅니다.
메시지 전달을 사용하면, 발신자는 사본을 하나의 수신자에게 보내고 두 사람은 아무것도 공유하지 않습니다.
세 가지 채널이 이 두 가지를 운반합니다. 도구 호출 인수는 한 방향으로 전달되며 응답 경로가 없습니다. 파일은 재시작 후에도 유지되지만 잠금이 필요합니다.
메시지 버스는 주소와 순서를 추가하며, 디스크에 기록될 경우에만 재시작 후에도 살아남습니다.
여기서 인박스는 잠긴 파일이므로, 공유 파일 시스템을 통한 메시지 전달입니다.
팀 메모리(섹션 9)와 작업 보드(섹션 18)는 공유 메모리 측입니다.
대부분의 팀은 둘 다 원합니다: 작업을 분배하기 위한 메시지, 메시지보다 오래 살아남는 사실을 위한 공유 메모리.

`mailbox.py`는 명명된 인박스의 `Team`을 구현합니다:

```python
def send(self, frm, to, content):                      # src/mailbox.py
    targets = [m for m in self.members if m != frm] if to == "*" else [self._check(to)]
    with self._lock():                                 # serialize concurrent senders
        for t in targets:
            inbox = self._read(t)
            inbox.append({"from": frm, "to": t, "content": content})
            self._path(t).write_text(json.dumps(inbox))
```

- `_check`는 경로가 되기 전에 알 수 없는 이름을 거부합니다.
- 락은 읽기-수정-쓰기 동작을 직렬화하여, 동시에 전송하는 송신자가 메시지를 잃지 않도록 합니다.
- `drain`는 한 인박스를 읽고 지웁니다.

권한 버블링은 승인자 구현입니다. 이는 제한된 호출을 동일한 채널을 통해 사람에게 전달합니다:

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

1. 팀원이 제한된 도구 호출을 시도하지만, 자신의 루프에는 키보드 앞에 사람이 없습니다.
2. 승인자가 `permission_request`를 리더의 인박스로 보냅니다.
3. 리더는 이를 승인 UI로 전달합니다(여기서 `human` 콜백).
4. 판정 결과가 `permission_response`로 팀원의 인박스에 반환됩니다.
5. 팀원이 그 응답을 읽고 게이트에 허용 또는 거부를 반환합니다.

게이트는 여전히 `approver(name, args)`를 호출하며 변경되지 않습니다. 답변은 직접 호출이 아니라 인박스 메시지로 도착하므로, 에스컬레이션은 동일한 채널을 재사용합니다.

`human` 없이는 답이 다른 곳에서 나와야 합니다 (다른 스레드의 단서나 채팅 플랫폼의 사람).
승인자는 자신의 인박스를 `timeout`까지 폴링한 후 거부합니다: 응답하지 않은 권한은 아니오이며, 절대 보류나 예가 될 수 없습니다.
이는 Hermes의 clarify 게이트웨이를 반영하며, `wait_for_response`는 채팅 어댑터가 응답하거나 시간 초과가 발생할 때까지 에이전트 스레드를 차단합니다.

### 통합 방식

데모는 하나의 메인 에이전트를 실행합니다. 리드는 한 단계 진행하고, 팀원은 스스로 실행됩니다:

```python
def spawn_worker(name, formed, model):                 # src/demo.py, module level
    team = formed["team"]                              # whatever the lead formed with TeamCreate
    ...                                                 # build the teammate's tools
    return mailbox.serve_mailbox(team, name, work)      # the teammate pulls its own inbox

run_turn([...goal...], model, lead_reg, session)        # the one agent call in demo(): the lead
```

- 유일한 스크립트 입력은 리드의 목표입니다. 리드는 `TeamCreate`로 팀 규모를 결정하고, `SpawnTeammate`로 각각을 생성하며, `SendMessage`로 위임합니다.
- `demo()`는 `run_turn`, 즉 리드가 실행하는 것을 하나 실행합니다. 팀원의 자체 `run_turn`는 `spawn_worker`에 있으며, 스폰 도구를 통해서만 접근할 수 있습니다.
- 각 팀원은 섹션-13 스레드에서 `serve_mailbox`를 실행합니다: 이것은 자신의 인박스를 가져오고, 작업하며, 회신합니다. 회신 수는 리드가 결정하며, 메인 프로세스는 단지 대기만 합니다.
- `loop.py`는 일반적으로 유지됩니다. 폴딩과 풀 루프는 조정 작업이며, `run_turn` 내부가 아닌 이 래퍼에서 수행됩니다.
- 권한 게이트는 변경되지 않습니다; 게이트가 있는 호출도 여전히 리드에게 전달됩니다.

### 추가 읽기

이 모든 내용은 `src/` 안에 포함되어 있지 않습니다. 이는 ai-agent-book과 공개된 다중 에이전트 연구에서 나온 것이며, 표에 있는 시스템에서 확인된 것은 아닙니다.

**팀이 한 에이전트를 이겼을 때.** 첫 번째 에이전트가 볼 수 없었던 무언가를 가져올 때만 두 번째 에이전트를 추가하세요.
테스트 결과, 스크린샷, 가져온 페이지, 실행 중인 시스템에서 나온 답변. 그것이 새로운 정보입니다.
같은 텍스트를 다시 읽고 투표하는 에이전트는 새로운 정보를 가져오지 않습니다. 단지 토큰만 소모합니다.

두 개의 발표된 결과는 잘못될 경우 비용을 설정합니다. Tran과 Kiela는 단일 에이전트와 팀에게 동일한 사고 토큰 예산을 주었습니다.
그들이 측정한 작업에서 단일 에이전트는 따라잡았습니다. Anthropic은 연구팀이 한 채팅 턴의 약 15배 토큰을 사용한다고 보고합니다.
그렇게 비용이 많이 드는 팀은 반드시 무엇인가를 가져와야 합니다.

**공유 또는 분리된 컨텍스트.** 두 에이전트는 하나의 히스토리를 공유하거나 각각 별도로 유지합니다:

- **공유.** 다음 에이전트는 모든 것을 상속하므로, 아무것도 포장할 필요가 없고 어떤 정보도 누락되지 않습니다.
  비용은 한 번에 한 에이전트만 실행되며, 한 창에 팀 전체의 기록이 저장된다는 것입니다.
- **격리.** 각 에이전트는 자신만의 창을 가지며 필요한 것을 직접 명시해야 합니다. 에이전트는 동시에 실행되며, 한 에이전트의 혼란은 그 자신의 창에서만 멈춥니다.
  비용은 모든 인계 사항을 기록해야 한다는 것입니다.

작업이 적고, 기록이 한 창에 맞으며, 단계가 어쨌든 순서대로 실행될 때는 공유를 선택하세요. 그렇지 않으면 격리합니다.
이 저장소는 격리됩니다: subagent는 빈 상태로 시작하고(섹션 6), 팀원은 자신의 받은 편지만 읽습니다.

**세 가지 토폴로지.** 격리된 에이전트라도 누가 누구와 소통하는지 알아야 합니다. 세 가지 형태가 있습니다:

- **동료.** 동등한 지위의 에이전트가 서로 메시지를 주고받습니다. 검토와 교차 확인이 여기에 해당합니다.
- **관리자.** 한 명의 리더가 작업을 나누고, 할당하며, 돌아오는 결과를 통합합니다. 자식 에이전트는 자신의 이력 대신 요약만 반환합니다.
- **분산.** 리더 없음. 각 에이전트가 다음 작업을 받을 사람을 선택합니다.

이 섹션에서는 관리자를 구축합니다. 리더는 모두를 계획하므로, 잘못된 분배가 있으면 그대로 남고 어떤 작업자도 이를 고칠 수 없습니다.
이것이 리더에게 가장 강력한 모델을, 작업자에게는 더 저렴한 모델을 주어야 한다는 논거입니다.

**분산 팀이 작업을 배분하는 방법.** 리더가 없더라도 작업은 여전히 다음 에이전트를 찾아야 합니다. 발표된 세 가지 설계, 세 가지 경로:

- **MetaGPT**는 모든 메시지를 풀에 게시합니다. 각 역할은 자신이 처리하는 메시지 유형을 구독하므로, 발신자가 수신자를 지정하지 않습니다.
- **AutoGen** 그룹 채팅은 하나의 기록을 유지하고 중앙 선택기가 다음에 누가 말할지 선택하게 합니다. 선택기가 같은 두 에이전트만 계속 선택하면, 채팅이 교착 상태에 빠집니다.
- **OpenAI Swarm**은 각 전달을 도구 호출로 만들고, 작업이 전달될 수 있는 횟수에 제한을 두어, 전달 체인의 종료를 보장합니다.

**파일 트리의 네 영역.** 에이전트는 이름으로 서로를 찾고, 경로로 상태를 찾습니다. 이 책에서는 트리를 네 영역으로 나눕니다:

- **개인 스크래치패드.** 한 에이전트의 초안입니다. 다른 사람은 읽지 않으므로, 조정할 필요가 없습니다.
- **공유 작업 공간.** 저장소, 작업 보드, 그리고 팀 메모리. 모든 팀원이 여기에 작성하므로, 여기서 충돌이 발생합니다.
  잠금이 필요하며, 또는 에이전트별로 worktree가 필요합니다(섹션 15).
- **외부 마운트.** 체크아웃이나 데이터셋과 같이 팀이 만들지 않은 데이터. 여기에 쓰면 팀 외부의 무언가가 변경됩니다.
- **읽기 전용 내장 기능.** Skills, 프롬프트, 도구 정의(섹션 7과 2). 실행 중에는 변경되지 않으므로 모든 에이전트가 동일한 사본을 봅니다.

상태를 잘못된 영역에 두면 이것이 조정 버그로 돌아옵니다. 두 명의 에이전트가 하나의 파일을 편집한다는 것은 그 파일이 공유 작업 공간에 있었음을 의미합니다.
세 번 보내진 사실은 팀 메모리로 가야 했음을 의미합니다.

**핸드오프가 전달하는 것.** 팀원은 리더의 채팅을 볼 수 없기 때문에 '실패한 테스트를 수정하라'는 지시는 아무 행동도 취할 수 없습니다. 핸드오프 패킷은 세 가지를 전달합니다:

1. 수신자가 스스로 확인할 수 있는 수용 기준이 포함된 작업.
2. 이미 확인된 사실과 유효한 제약 조건, 따라서 수신자는 이를 다시 조회하거나 깨뜨리지 않습니다.
3. 파일, 로그 및 브랜치의 경로.

발신자의 원시 기록은 포함되지 않습니다. 기록은 길고, 막다른 길로 가득하며, 수신자가 발신자의 실수를 읽게 합니다.

공유 컨텍스트 핸드오프는 다른 옵션이며, 패킷을 생략합니다. 한 에이전트가 다른 에이전트에게 제어를 넘기고 전체 기록이 함께 전달되므로 아무것도 남지 않습니다.
이 책은 역할 간에 제어를 이동시키는 도구를 통해 이것을 보여준다. 그것은 저자의 자체 실험이므로 하나의 출처로 취급하라.
비용은 한 에이전트만 제어를 가지므로 동시에 아무 것도 실행되지 않는다는 점이다. 패킷은 작성하는 데 작업이 필요하며 병렬로 실행되는 작업을 구매한다.

> **다음:** 여기 팀원은 정상적인 종료가 없는 데몬이며, 메시지에만 반응한다.
> 17장은 리더가 팀원을 깔끔하게 종료할 수 있도록 종료 핸드셰이크를 추가한다.
> 18장은 공유 작업 보드를 추가하여, 한가한 팀원이 메시지를 기다리지 않고 자신의 작업을 스스로 가져간다.

---

## 시스템별

어떻게 한 설계가 협력하는 에이전트를 생성하고 작업을 그들에게 분산시키는지.

| | Claude Code | Hermes Agent | deepseek-harness |
| --- | --- | --- | --- |
| **장점** | 동료들은 직접 대화합니다. 파일 인박스는 프로세스를 넘나듭니다. | 아이들은 어떤 표면에서도 일시정지하고 중단할 수 있습니다. | 스크립트는 하드 캡 안에서 여러 아이들을 분산시킵니다. |
| **단점** | 폴링과 잠금 비용이 있습니다. 메모리 인박스는 프로세스와 함께 사라집니다. | 피어 인박스가 없습니다. 클라리파이는 스레드를 블록합니다. | 아이들은 대화를 할 수 없습니다. 보내도 답장이 없습니다. |
| **이유** | 동료들은 인박스가 필요하며, 인간 승인자에게 가는 경로도 필요합니다. | 조정은 부모에서 자식으로 유지됩니다. | 조정은 소유권입니다. 각 아이는 하나의 부모를 가집니다. |
| **방법: 팀원들** | 프로세스 내 또는 원격에서 각자 자신의 루프를 실행합니다. | 쓰레드 상의 위임된 아이들, 일시정지 플래그 포함. | 모델이 작성한 스크립트가 이들을 생성하며, 일부는 상주합니다. |
| **방법: 채널** | SendMessage는 받은 편지함에 메시지를 쓰고 브로드캐스트할 수 있습니다. | 완료 큐와 게이트웨이 호출. | 부모에서 자식으로만. 자식은 보고 도구로 응답합니다. |
| **방법: 공유 메모리** | 팀 작업 목록과 팀 메모리 디렉토리. | 공유 세션 DB와 계보 표시기. | 부모의 디렉토리. 포크는 완료된 턴도 복사합니다. |
| **방법: 권한 전달** | 원격 요청이 로컬 승인 요청으로 변환됩니다. | Clarify는 채팅으로 전달; 자식은 자동으로 거부하거나 승인합니다. | 요청이 부모 체인을 따라 올라갑니다. |

---

## 실패 모드

- **메시지 손실 경합.** 두 발신자가 동시에 하나의 받은편지함에 작성합니다. 읽기-수정-쓰기 잠금.
- **피어 교착 상태.** 에이전트들이 서로를 기다립니다. 전송을 차단하는 대신 메시지를 큐에 넣고 턴 사이에 처리합니다.
- **권한 지연.** 팀원에게 인간 UI가 없습니다. 요청을 팀장에게 전달하세요.
- **생성 전에 스폰.** 팀장이 `TeamCreate` 이전에 스폰되거나 메시지를 보냅니다. 따라서 명단이 없습니다. 팀이 존재할 때까지 둘 다 동작하지 않게 유지하세요.
- **고아 팀원.** 스폰된 팀원이 작업이 끝난 후에도 계속 폴링합니다. 유휴 대기를 제한하거나 섹션-17 핸드셰이크로 중지하세요.
- **모호한 크로스 에이전트 메시지.** 팀원이 팀장의 채팅을 볼 수 없습니다. 패킷을 보내세요: 작업, 수락 조건, 확인된 사실, 산출물 경로.
- **메모리로 사용된 채팅.** 지속적인 공유 사실은 팀 메모리에 저장해야 합니다.
- **비잔틴 팀원.** 실패한 에이전트는 크래시하지 않습니다. 잘못된 답변을 반환하며 자신 있는 듯 보입니다.
  다시 시도하거나 같은 증거에 대해 투표해도 같은 답을 받게 됩니다. 모델 외부의 무언가에 대한 검증만이 이를 잡아낼 수 있습니다.
- **공유 파일에서 업데이트 손실.** 두 에이전트가 같은 파일을 읽고 둘 다 다시 씁니다. 첫 번째 작성 내용은 사라집니다.
  쓰기를 잠그거나, 버전 번호를 저장하고 일치하지 않을 경우 다시 시도하세요.
- **의미 충돌.** 두 작성 모두 깔끔하게 적용되지만 결과는 여전히 깨진 상태입니다. 한 에이전트가 함수를 이름을 바꿨고 다른 에이전트는 이전 이름으로 호출을 추가했습니다.
  두 에이전트가 같은 것을 소유하지 않도록 작업을 나누거나 한 지점에서 병합하세요.
- **오류 연쇄.** 한 에이전트가 사실을 잘못 이해합니다. 다음 에이전트가 이를 반복하고, 그 다음 에이전트도 반복하며, 그때쯤이면 확정된 것으로 읽히게 됩니다.
  결론만 보는 검토자는 그것들을 일관성 있게 찾습니다. 데이터를 생성한 에이전트가 아니라 원시 증거를 확인하도록 하세요.

---

## 실행 가능

[`src/`](src/)은 15를 전달하고 추가합니다:

- [`mailbox.py`](src/mailbox.py): 잠금, 접기, `serve_mailbox` 루프, 시간 초과와 기본 거부와 함께 버블링, 팀 도구가 포함된 명명된 인박스.
- [`test.py`](src/test.py): 주소 지정, 방송, 동시 전송, 접기, 버블링(인라인, 비동기 및 시간 초과 거부), 메일박스 루프, 팀 도구를 확인합니다.
- [`demo.py`](src/demo.py): 리드는 한 단계를 진행합니다(`TeamCreate`, `SpawnTeammate`, `SendMessage`); 각 팀원은 자신의 인박스를 당겨서 게이트된 셸 작업을 실행하고 보고합니다.

루프와 subagent 경로는 변경되지 않았습니다. 조정은 팀원을 생성하고, 받은 편지함을 비우며, 승인자에게 전달함으로써 한 바퀴를 감쌉니다.

```bash
python sections/16-coordination/src/test.py         # offline checks, no key
uv run python sections/16-coordination/src/demo.py  # live demo, needs a key
```

---

## 출처

- [Claude Code 도구 및 받은 편지함](https://github.com/yasasbanukaofficial/claude-code):
  `tools/SendMessageTool/`, `tools/TeamCreateTool/`, `utils/mailbox.ts`, `utils/teammateMailbox.ts`.
- [Claude Code 팀원](https://github.com/yasasbanukaofficial/claude-code):
  `tasks/InProcessTeammateTask/`, `tasks/RemoteAgentTask/`, `remote/remotePermissionBridge.ts`, `memdir/teamMemPaths.ts`.
- [Hermes Agent 소스](https://github.com/NousResearch/hermes-agent): `tools/delegate_tool.py`, `tools/async_delegation.py`, `tools/clarify_gateway.py`, `tools/interrupt.py`.
- [deepseek-harness 소스](https://github.com/deepseek-ai/deepseek-harness) at `dsh-v0.1.0-rc.7`:
  `docs/subsystems/workflow.md`, `docs/subsystems/subagent.md`, `docs/subsystems/core.md`,
  `packages/workflow/workflow-worker-thread/README.md`, `packages/subagent/tool-subagent-report/README.md`.
- [learn-claude-code · s15_agent_teams](https://github.com/shareAI-lab/learn-claude-code): 섹션 구성.
- [ai-agent-book](https://github.com/bojieli/ai-agent-book): `book/chapter10.md` (다중 에이전트 협업), 중국어 원본 정식판.
  컨텍스트 공유, 토폴로지 분류, 파일 시스템 영역, 전달 패킷. 역할 전환 데모는 저자의 자체 실험임.
- Cemri 등, *왜 다중 에이전트 LLM 시스템은 실패하는가?* ([arXiv:2503.13657](https://arxiv.org/abs/2503.13657)): MAST 분류와 비잔틴 구성을 다룸.
- Tran, Kiela, *단일 에이전트 LLM이 동일한 사고 토큰 예산에서 다중 에이전트 시스템보다 우수함* ([arXiv:2604.02460](https://arxiv.org/abs/2604.02460)).
- Erdogan 등, *계획 및 실행* ([arXiv:2503.09572](https://arxiv.org/abs/2503.09572)): 계획자 품질이 실행을 제한함.
- Anthropic, [*우리가 다중 에이전트 연구 시스템을 구축한 방법*](https://www.anthropic.com/engineering/multi-agent-research-system): 연구팀의 토큰 비용.
- [MetaGPT](https://arxiv.org/abs/2308.00352), [AutoGen](https://arxiv.org/abs/2308.08155), [OpenAI Swarm](https://github.com/openai/swarm): 분산 라우팅 및 인계 한도.
