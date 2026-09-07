# 14 · Scheduling

[English](README.md) · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · **한국어**

> 사용자 입력만이 아니라 시계로도 agent turn을 시작합니다.

백그라운드 작업도 누군가 또는 무언가가 시작해 주어야 합니다. 많은 작업은 나중에 돌거나 반복되어야 합니다. 보고서, 알림, 폴링 작업 같은 것들입니다.

scheduling은 미래의 트리거를 저장합니다. 트리거가 발화하면 prompt를 큐에 넣습니다. 평범한 loop가 그 prompt를 새 turn으로 처리합니다.

scheduling이 해야 할 일은 이렇습니다.

1. schedule을 turn 하나의 바깥에 저장합니다.
2. loop와 무관하게 시간을 지켜봅니다.
3. schedule이 발화하면 prompt를 큐에 넣습니다.
4. 필요하면 schedule을 재시작 너머로 보존합니다.

이 계층이 없으면 agent는 사용자 입력에만 반응할 수 있습니다.

---

## 메커니즘

![Mechanism diagram](assets/14-scheduling.png)

시계를 loop에서 떼어 냅니다. scheduler는 시간을 지켜봅니다. 모델을 직접 호출하지는 않습니다.

발화 시점에 scheduler는 prompt를 큐에 넣기만 합니다. 구동부는 turn 사이, 진행 중인 turn이 없을 때 큐를 비우고,
사용자 입력을 처리하는 것과 같은 agent loop로 각 prompt를 돌립니다.

- schedule은 데이터입니다. 실행할 prompt, 발화 시각, 그리고 선택적인 반복 간격입니다. scheduler는 각각을 task로 저장합니다.
- 한 번짜리는 한 번 발화한 뒤 스스로를 지웁니다.
- 반복 schedule은 다음 간격으로 다시 장전합니다.
- 영속 schedule은 재시작을 견디지만, 호스트가 꺼져 있는 동안에는 발화하지 않습니다.
- heartbeat는 질문을 던지는 반복 schedule입니다. 깨어나서 소스를 확인하고, 대개 말할 것이 없다고 판단합니다.

### 신규: scheduler와 발화 큐

`tick`은 기한이 된 task를 확인합니다. 발화란 prompt를 큐에 넣는 일입니다.

```python
def tick(self):                                       # src/scheduler.py; called by a daemon thread
    now = self._clock()
    for tid, t in list(self._tasks.items()):
        if now >= t["due"]:
            self._pending.put({"prompt": t["prompt"], "channel": t.get("channel")})
            if t["every"]:                            # enqueue, do not run the model here
                t["due"] = now + t["every"]
            else:
                self._tasks.pop(tid, None)
    self._save()                                      # durable tasks only
```

- 시계는 주입할 수 있어서 테스트는 가짜 시계를 씁니다.
- `run()`은 데몬 스레드에서 `tick`을 호출합니다.
- `_save`는 영속 task를 JSON으로 저장합니다.
- 같은 경로로 새 `Scheduler`를 만들면 영속 task를 다시 불러오고 id를 이어 갑니다.

### 신규: 답변 전달

발화로 시작된 실행에는 기다리는 사람이 없으므로, 답변이 나갈 경로가 필요합니다. task마다 channel을 지정할 수 있습니다.
channel은 task의 필드입니다. `create(..., channel="console")`이 그것을 저장하고, `tick`이 prompt와 함께 큐에 넣습니다.
큐에서 꺼낸 항목은 이미 `{"prompt": ..., "channel": ...}` 형태이므로, 구동부는 답변이 어디로 가는지 따로 찾아보지 않습니다.

`deliver`는 그 turn의 답변을 라우팅합니다(Hermes는 cron 출력을 그 job의 채팅 플랫폼으로 전달합니다).

```python
SILENT = "[SILENT]"                              # a fired run may decide nothing is worth sending

def deliver(channels, fired, text) -> bool:      # src/scheduler.py
    if not fired.get("channel") or text.lstrip().startswith(SILENT):
        return False
    channels[fired["channel"]](text)
    return True
```

- `channels`는 channel 이름을 보내기 호출 대상에 대응시킵니다(여기서는 print이고, 실제 어댑터는 section 19가 맡습니다).
  channel은 task가 지정하고, 대응 표는 구동부가 소유합니다. 둘은 서로의 세부를 모릅니다.
- 답변이 `[SILENT]`로 시작하면 `deliver`는 channel 전송을 건너뜁니다. 예약된 확인이 사용자에게 알릴 만한 것을 찾지 못했을 때의 관례입니다
  (변화가 없던 폴링 같은 경우). 구동부는 여전히 전체 텍스트를 들고 있으므로 기록할 수 있습니다.
- channel이 없으면 답변은 로컬에 남습니다. 전달 기능이 생기기 전의 동작입니다.
- `bool` 반환값 덕분에 구동부는 답변을 조용히 잃는 대신 대체 경로를 쓸 수 있습니다(데모는 전달되지 않은 답변을 출력합니다).

### heartbeat

어떤 소스는 절대 밀어 주지 않습니다. webhook이 없는 메일함, 피드가 없는 페이지, 물어봐야만 답하는 서비스가 그렇습니다.
그런 소스에 남은 트리거는 시계뿐입니다. 그 패턴이 heartbeat입니다. 행동하라가 아니라 살펴보라고 지시하는 prompt를 가진 반복 schedule입니다.
소스를 확인하고, 메시지를 보낼 만큼 달라진 것이 있는지 판단하고, 아니면 아무 말도 하지 않습니다.

보고할 만한 것을 찾지 못한 heartbeat 실행은 `[SILENT]`로 답합니다. 위의 규칙에 따라 `deliver`는 아무것도 보내지 않습니다.
그 tick의 비용은 모델 호출 한 번이고 메시지는 없으므로, channel을 넘치게 하지 않으면서 schedule을 자주 돌릴 수 있습니다.

heartbeat와 cron 항목은 여기서 같은 부품을 씁니다. prompt, 반복 간격, channel입니다. 다른 것은 prompt뿐입니다.
cron prompt는 명령을 내립니다. heartbeat prompt는 질문을 던집니다.

### 통합 방식

scheduling은 두 부분입니다. `tick`은 자기 데몬 스레드에서 돕니다(section 13의 백그라운드 실행). 모델은 전혀 건드리지 않고, 발화 시 큐에 넣기만 합니다.

```python
def run(self):                                        # src/scheduler.py; started by sched.run()
    def loop():
        while not self._stop.wait(self.CHECK_INTERVAL):   # wakes once per second
            self.tick()
    threading.Thread(target=loop, daemon=True).start()    # daemon: never keeps the process alive
```

turn 자체는 전면에서 돕니다. 구동부는 turn 사이에 큐를 비우고, 발화한 task마다 `run_turn`을 한 번씩 호출합니다.

```python
for task in sched.drain():                            # src/demo.py · between turns
    messages = [{"role": "user", "content": task["prompt"]}]
    deliver(channels, task, run_turn(messages, model, reg, session))
```

발화한 prompt는 사용자 입력과 같은 형태의 새 turn이 됩니다. 같은 loop, permission, hook, memory, context 관리, 복구 경로를 씁니다. 답변은 그 task의 channel로 나갑니다.

### 더 읽을거리

이 내용은 `src/`에 없습니다. ai-agent-book에서 온 것이고, 표에 있는 시스템에서 확인된 내용은 아닙니다.

**시계의 한계.** heartbeat에서 중요한 설정은 하나, 간격입니다.
간격이 청구액과 최악의 지연을 한꺼번에 정하는데, 이 둘은 서로 반대로 당깁니다.
짧은 간격은 모델을 자주 깨우고 대개는 아무것도 찾지 못합니다. 긴 간격은 싸지만 늦습니다.
어떤 간격도 이를 해결하지 못합니다. 시계는 이벤트를 지켜보는 대신 상태를 표본으로 뜨므로, 마지막으로 본 시점은 알아도 일이 일어난 시점은 모릅니다.

**가능하면 push를 씁니다.** 소스가 agent를 호출할 수 있으면 트리거가 이벤트가 일어나는 순간 발화하고 폴링 비용은 0이 됩니다.
그래서 순서는 이렇습니다. 소스가 지원하면 push, 지원하지 않으면 heartbeat, 월요일 보고서처럼 정말로 시간 기반인 작업에는 cron.
들어오는 push 쪽은 section 19가 다룹니다.

---

## 시스템별

각 agent가 예약된 작업을 언제 돌릴지 어떻게 정하는지.

| | Claude Code | Hermes Agent | deepseek-harness |
| --- | --- | --- | --- |
| **장점** | 단순하고 로컬에 머묾. 영속 schedule이 재시작을 견딤. | 사람 없이 발화하고, 호스팅 서비스가 필요 없음. | 알림이 session과 함께 재생됨. 놓친 발화는 turn 하나로 합쳐짐. |
| **단점** | session이 도는 동안에만 tick함. 원격 트리거에는 서비스가 필요함. | 게이트웨이가 필요하고 이중 발화를 막을 락이 필요함. | 고정된 레이트만 지원함. 차가운 상태에서는 아무것도 발화하지 않음. |
| **이유** | 로컬 session이 돌고 있다고 가정함. | 게이트웨이가 서버이므로 schedule이 사람 없이 발화함. | 알림은 대화 상태이므로 session 로그가 소유함. |
| **방법: 트리거** | 티커 위의 cron, sleep, 원격 트리거. | 게이트웨이 tick 위의 cron, 사용자 시간대 기준. | 일정 시간 뒤, 지정 시각, 또는 최소 5분 간격. |
| **방법: 영속성** | session 상태, 또는 락이 걸린 JSON 파일. | 원자적 선점을 갖춘 공유 JSON job 저장소. | session 로그 이벤트. fork는 기록은 유지하고 알림은 버림. |
| **방법: 깨우기** | 발화한 prompt가 큐에 쌓여 turn 사이에 실행됨. | 기한이 된 job이 병렬로 돌고 채팅으로 전달됨. | 기한이 된 작업은 유휴 상태를 기다렸다가 turn 하나를 큐에 넣음. 최소 한 번. |

---

## 실패 모드

- **이중 발화.** 빠른 tick이 같은 cron 분에 두 번 이상 걸릴 수 있음. 마지막으로 발화한 분을 기록합니다.
- **여러 schedule이 함께 발화함.** 반복 task에 결정적인 지터를 넣습니다.
- **영속이 곧 상시 가동은 아님.** 로컬 영속 schedule은 재시작을 견딜 뿐입니다. 오프라인 발화에는 원격 트리거나 OS 타이머를 씁니다.
- **잘못된 cron 식.** 생성할 때 검증하고, 불러온 항목 중 유효하지 않은 것은 건너뜁니다.
- **loop가 바쁨.** prompt를 큐에 넣고 turn 사이에 꺼냅니다.
- **알림 피로.** tick마다 보고하는 heartbeat는 사용자에게 무시하는 법을 가르침. 보낼 가치가 있는지 prompt가 정하게 하고, 아니면 침묵합니다.
- **tick 사이의 이벤트.** 시계는 상태를 표본으로 뜸. 두 tick 사이에 나타났다 되돌아간 변화는 보이지 않음. 로그나 커서를 읽거나, 소스를 push로 옮깁니다.

---

## 실행 방법

[`src/`](src/)는 13을 이어받고 다음을 추가합니다.

- [`scheduler.py`](src/scheduler.py): scheduler, 발화 큐, 반복 재장전, 한 번짜리 삭제, 영속 JSON 저장소, channel 전달(`deliver`, `SILENT`).
- [`test.py`](src/test.py): 가짜 시계로 한 번짜리, 반복, 다시 불러오기, 전달 동작을 시험합니다.
- [`demo.py`](src/demo.py): prompt를 1초 뒤로 예약하고, 새 turn으로 실행하고, 답변을 콘솔 channel로 전달합니다.

loop는 그대로입니다. scheduling은 loop 바깥에서 turn을 시작합니다.

```bash
python sections/14-scheduling/src/test.py         # offline checks, no key
uv run python sections/14-scheduling/src/demo.py  # live demo, needs a key
```

---

## 출처

- [Claude Code source](https://github.com/yasasbanukaofficial/claude-code):
  `tools/ScheduleCronTool/`, `tools/RemoteTriggerTool/`, `tools/SleepTool/`, `utils/cronScheduler.ts`, `hooks/useScheduledTasks.ts`, `utils/queueProcessor.ts`.
- [Hermes Agent source](https://github.com/NousResearch/hermes-agent):
  `cron/scheduler.py`(`tick`, `_resolve_cron_disabled_toolsets`), `cron/jobs.py`(`_jobs_lock`, `claim_dispatch`), `hermes_time.py`.
- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness), `dsh-v0.1.0-rc.7` 기준:
  `packages/schedule/schedule/src/runtime.ts`, `packages/schedule/schedule/src/persistence.ts`, `packages/schedule/schedule/src/tools.ts`,
  `docs/subsystems/schedule.md`, `docs/tool-catalog.md`.
- [learn-claude-code · s14_cron_scheduler](https://github.com/shareAI-lab/learn-claude-code): 섹션 구성.
- [ai-agent-book](https://github.com/bojieli/ai-agent-book): `book/chapter4.md`, 중국어 원문이 정본.
  판단을 동반한 heartbeat 깨우기, 알림 피로, 시간 기반 트리거의 한계.
