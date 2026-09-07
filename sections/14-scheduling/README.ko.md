# 14 · Scheduling

[English](README.md) · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · **한국어**

> 에이전트의 턴을 사용자 입력뿐만 아니라 시계(clock)로부터 시작합니다.

백그라운드 작업은 여전히 누군가 또는 무언가가 시작해야 합니다. 많은 작업은 나중에 실행되거나 반복되어야 합니다: 보고서, 알림, 또는 폴링 작업.

스케줄링은 미래의 트리거를 저장합니다. 트리거가 발동하면 프롬프트를 큐에 넣습니다. 일반 루프는 그 프롬프트를 새로운 턴으로 처리합니다.

스케줄링은 다음을 수행해야 합니다:

1. 한 턴 외부에서 스케줄을 저장합니다.
2. 루프와 독립적으로 시간을 감시합니다.
3. 스케줄이 발동하면 프롬프트를 큐에 넣습니다.
4. 선택적으로 재시작 시에도 스케줄을 유지합니다.

이 계층이 없으면 에이전트는 사용자 입력에만 반응할 수 있습니다.

---

## 메커니즘

![메커니즘 다이어그램](assets/14-scheduling.png)

루프와 시계를 분리하세요. 스케줄러는 시간을 감시합니다. 모델을 직접 호출하지 않습니다.

실행 시간에는 스케줄러가 프롬프트만 큐에 넣습니다. 드라이버는 턴이 진행 중이 아닐 때 턴 사이에 큐를 비웁니다.
그리고 각 프롬프트를 사용자 입력을 처리하는 동일한 agent loop를 통해 실행합니다.

- 일정은 데이터입니다: 실행할 프롬프트, 실행 시간, 그리고 선택적인 반복 간격입니다. 스케줄러는 각 일정을 작업으로 저장합니다.
- 원샷은 한 번 발사되면 스스로 삭제됩니다.
- 반복 일정은 다음 간격으로 다시 설정됩니다.
- 내구성 있는 스케줄은 재시작 후에도 유지되지만, 호스트가 꺼져 있는 동안에는 실행되지 않습니다.
- 심장 박동은 질문을 던지는 반복적인 일정입니다. 그것은 깨어나고, 출처를 확인하며, 보통 할 말이 없다고 결정합니다.

### 새로움: 스케줄러와 화재 대기열

`tick`는 마감된 작업을 확인합니다. 실행(Firing)은 프롬프트를 대기열에 넣는 것을 의미합니다:

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

- 시계(clock)는 주입 가능하므로 테스트에서는 가짜 시계를 사용합니다.
- `run()`는 `tick`를 데몬 스레드에서 호출합니다.
- `_save`는 지속 가능한 작업을 JSON에 저장합니다.
- 동일한 경로의 새로운 `Scheduler`는 지속 가능한 작업을 다시 로드하고 ID를 재개합니다.

### 새로움: 답변 전달

실행된(run) 작업에는 기다리는 사람이 없으므로, 답변에는 경로(route)가 필요합니다. 각 작업은 채널을 지정할 수 있습니다.
채널은 작업의 필드입니다: `create(..., channel="console")`가 이를 저장하고, `tick`가 프롬프트와 함께 대기열에 넣습니다.
각 소진된(drained) 항목은 이미 `{"prompt": ..., "channel": ...}`이므로, 드라이버는 답변이 어디로 가는지 조회하지 않습니다.

`deliver`는 회차의 답변을 라우팅합니다(Hermes가 cron 출력을 작업의 채팅 플랫폼으로 전달합니다):

```python
SILENT = "[SILENT]"                              # a fired run may decide nothing is worth sending

def deliver(channels, fired, text) -> bool:      # src/scheduler.py
    if not fired.get("channel") or text.lstrip().startswith(SILENT):
        return False
    channels[fired["channel"]](text)
    return True
```

- `channels`는 채널 이름을 송신 호출 가능 객체에 매핑합니다(여기서 출력; 실제 어댑터는 19장의 작업입니다).
  작업이 채널의 이름을 지정하고, 드라이버가 맵을 소유합니다. 서로의 세부 사항은 알지 못합니다.
- 답변이 `[SILENT]`로 시작할 때, `deliver`는 채널 전송을 건너뜁니다. 이는 사용자에게 알릴 가치가 없는 예약 확인을 위한 관례입니다
  (변화를 발견하지 못한 폴링). 드라이버는 여전히 전체 텍스트를 보유하며 로그할 수 있습니다.
- 채널이 없으면 답변은 로컬에 그대로 유지되며, 전달 전 행동입니다.
- `bool` 반환은 운전자가 답변을 조용히 잃는 대신(데모에서는 전달되지 않은 답변을 출력함) 되돌아가도록 합니다.

### 하트비트

일부 소스는 절대 푸시하지 않습니다. 웹훅이 없는 메일함, 피드가 없는 페이지, 요청할 때만 응답하는 서비스가 있습니다.
그런 경우 남은 유일한 트리거는 시계입니다. 패턴은 하트비트: 에이전트에게 행동하지 말고 확인하라고 지시하는 반복 일정입니다.
소스를 확인하고 메시지를 보낼 가치가 있을 정도로 변경된 것이 있는지 판단하고, 그렇지 않으면 아무 말도 하지 마세요.

보고할 가치가 있는 것을 찾지 못한 하트비트 실행은 `[SILENT]`로 답변합니다. 위 규칙에 따라 `deliver`는 아무것도 보내지 않습니다.
틱은 모델 호출 한 번과 메시지 없음 비용이 들기 때문에, 채널을 넘치게 하지 않고 자주 실행할 수 있습니다.

하나의 심장박동과 cron 항목은 여기서 동일한 부품을 사용합니다: 프롬프트, 반복 간격, 그리고 채널. 프롬프트만 다릅니다.
cron 프롬프트는 명령을 내립니다. 심장박동 프롬프트는 질문을 합니다.

### 통합 방식

스케줄링은 두 부분으로 나뉩니다. `tick`는 자체 데몬 스레드(섹션 13의 background execution)에서 실행되며; 모델에 절대 접근하지 않고 fire에만 큐를 추가합니다:

```python
def run(self):                                        # src/scheduler.py; started by sched.run()
    def loop():
        while not self._stop.wait(self.CHECK_INTERVAL):   # wakes once per second
            self.tick()
    threading.Thread(target=loop, daemon=True).start()    # daemon: never keeps the process alive
```

턴 자체는 포그라운드에서 실행됩니다: 드라이버는 턴 사이에 큐를 비우고, fire된 작업마다 `run_turn`를 호출합니다:

```python
for task in sched.drain():                            # src/demo.py · between turns
    messages = [{"role": "user", "content": task["prompt"]}]
    deliver(channels, task, run_turn(messages, model, reg, session))
```

fire된 프롬프트는 새로운 사용자 스타일 턴이 됩니다. 동일한 루프, 권한, hooks, 메모리, context management, 및 복구 경로를 사용합니다. 그 답변은 작업의 채널로 전달됩니다.

### 추가 읽기

이 내용은 `src/`에 없습니다. ai-agent-book에서 나온 것이며, 표에 있는 시스템에 대해서는 확인되지 않았습니다.

**시계의 한계.** 심장 박동에는 중요한 설정 하나뿐입니다: 간격(interval).
이 간격은 청구와 최악의 지연 시간을 동시에 설정하며, 이 두 가지는 서로 상충합니다.
짧은 간격은 모델을 자주 깨우지만 대부분 아무것도 찾지 못합니다. 긴 간격은 비용이 적고 늦습니다.
어떤 간격도 이 문제를 해결하지 못합니다. 시계는 이벤트를 관찰하는 대신 상태를 샘플링하기 때문에 마지막으로 확인한 시점은 알지만, 사건이 실제로 발생한 시점은 알지 못합니다.

**가능한 곳에서는 푸시 방식을 선호하세요.** 소스가 에이전트를 호출할 수 있을 때, 이벤트가 발생하는 즉시 트리거가 작동하며 폴링 비용은 0이 됩니다.
따라서 순서는 소스가 지원하는 경우에는 푸시, 지원하지 않는 경우에는 하트비트, 그리고 월요일 보고서와 같이 실제로 시간 기반인 작업에는 cron입니다.
섹션 19는 인바운드 푸시 측을 다룹니다.

---

## 시스템별

각 에이전트가 예약된 작업을 실행할 시점을 결정하는 방법.

| | Claude Code | Hermes Agent | deepseek-harness |
| --- | --- | --- | --- |
| **장점** | 간단하고 비공개. 내구성 있는 일정은 재시작 후에도 유지됨. | 사람이 없이 실행됨, 호스팅 서비스 필요 없음. | 세션과 함께 알림이 다시 실행됨. 놓친 알림은 하나의 턴으로 압축됨. |
| **단점** | 세션이 실행되는 동안 틱이 발생함; 원격 트리거에는 서비스 필요. | 게이트웨이가 필요하며 중복 실행 방지를 위한 잠금 필요. | 고정 속도만 가능. 냉각 상태에서는 아무 것도 실행되지 않음. |
| **이유** | 로컬 세션이 실행 중이라고 가정함. | 게이트웨이는 서버이므로 일정이 사람이 없이 실행됨. | 알림은 대화 상태이므로 세션 로그가 소유함. |
| **방법: 트리거** | Cron, 슬립, 그리고 티커 상의 원격 트리거. | 사용자의 시간대에서 게이트웨이 틱에 Cron. | 지연 후, 특정 시간에, 또는 최대 5분마다. |
| **방법: 내구성** | 세션 상태, 또는 잠금이 있는 JSON 파일. | 원자적 클레임이 있는 공유 JSON 작업 스토어. | 세션 로그 이벤트. 포크는 기록을 유지하고, 알림은 삭제. |
| **방법: 웨이크업** | 실행 대기 중인 프롬프트 큐와 턴 사이에 실행. | 예정된 작업들은 병렬로 실행되어 채팅에 전달. | 예정된 작업은 유휴 상태를 기다린 후 한 번의 턴으로 큐에 추가. 최소 한 번 실행. |

---

## 실패 모드

- **이중 실행.** 빠른 틱은 같은 cron 분을 여러 번 일치시킬 수 있음. 마지막으로 실행된 분을 추적.
- **많은 일정이 동시에 실행됨.** 반복 작업에 결정론적 지터 추가.
- **내구형은 항상 켜져 있음을 의미합니다.** 로컬 내구형 스케줄은 재시작 후에는 유지되지 않습니다. 오프라인에서 실행하려면 원격 트리거나 OS 타이머를 사용하세요.
- **잘못된 cron 표현입니다.** 생성 시 유효성을 검사하고, 로드된 잘못된 항목은 건너뜁니다.
- **루프가 바쁩니다.** 프롬프트를 큐에 넣고 턴 사이에 비웁니다.
- **알림 피로.** 모든 틱에서 보고하는 하트비트는 사용자가 무시하도록 가르칩니다. 어떤 내용을 보낼 가치가 있는지 프롬프트가 결정하도록 하고 그 외에는 침묵하세요.
- **틱 사이의 이벤트.** 시계는 상태를 샘플링합니다. 두 틱 사이에 나타났다가 사라지는 변경은 보이지 않습니다. 로그나 커서를 읽거나, 소스를 이동시켜 푸시하세요.

---

## 실행 가능

[`src/`](src/)은 13을 앞으로 전달하고 다음을 추가합니다:

- [`scheduler.py`](src/scheduler.py): 스케줄러, 실행 대기열, 반복 재설정, 일회성 삭제, 지속 가능한 JSON 저장소, 채널 전달(`deliver`, `SILENT`).
- [`test.py`](src/test.py): 가짜 시계를 사용하여 일회성, 반복, 재로드 및 전달 동작을 테스트합니다.
- [`demo.py`](src/demo.py): 1초 후 즉시 실행을 예약하고, 새 턴에서 실행하며, 콘솔 채널로 답변을 전달합니다.

루프는 변경되지 않았습니다. 예약은 외부에서 턴을 시작합니다.

```bash
python sections/14-scheduling/src/test.py         # offline checks, no key
uv run python sections/14-scheduling/src/demo.py  # live demo, needs a key
```

---

## 출처

- [Claude Code 소스](https://github.com/yasasbanukaofficial/claude-code):
  `tools/ScheduleCronTool/`, `tools/RemoteTriggerTool/`, `tools/SleepTool/`, `utils/cronScheduler.ts`, `hooks/useScheduledTasks.ts`, `utils/queueProcessor.ts`.
- [Hermes Agent 소스](https://github.com/NousResearch/hermes-agent):
  `cron/scheduler.py` (`tick`, `_resolve_cron_disabled_toolsets`), `cron/jobs.py` (`_jobs_lock`, `claim_dispatch`), `hermes_time.py`.
- [deepseek-harness 소스](https://github.com/deepseek-ai/deepseek-harness) at `dsh-v0.1.0-rc.7`:
  `packages/schedule/schedule/src/runtime.ts`, `packages/schedule/schedule/src/persistence.ts`, `packages/schedule/schedule/src/tools.ts`,
  `docs/subsystems/schedule.md`, `docs/tool-catalog.md`.
- [learn-claude-code · s14_cron_scheduler](https://github.com/shareAI-lab/learn-claude-code): 섹션 프레이밍.
- [ai-agent-book](https://github.com/bojieli/ai-agent-book): `book/chapter4.md`, 중국어 원본 표준.
  판단을 통한 하트비트 웨이크업, 경고 피로, 시간 기반 트리거의 한계.
