# 12 · Task system

[English](README.md) · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · **한국어**

> 작업을 의존성을 가진 영속 task로 저장합니다.

section 5의 todo 목록은 메모리에만 있어서 프로세스가 끝나면 사라집니다. 어떤 task가 다른 task를 기다려야 하는지도 표현하지 못합니다.

task 시스템은 작업을 디스크의 레코드로 저장합니다. 레코드마다 의존성을 가질 수 있습니다. worker는 자기를 막는 task가 모두 끝났을 때 task를 선점합니다.

task 시스템이 해야 할 일은 이렇습니다.

1. 작업의 각 단위를 영속 객체로 저장합니다.
2. 순서를 데이터로 표현합니다.
3. turn, session, 크래시를 견딥니다.
4. 한 task는 worker 하나만 선점하게 합니다.

이 계층이 없으면 계획은 현재 context 창 안에만 존재합니다.

---

## 메커니즘

![Mechanism diagram](assets/12-task-system.png)

task는 디스크에 있는 JSON 레코드입니다. `blockedBy`와 `blocks`가 의존성 간선입니다. 파일 락이 선점을 직렬화합니다.

- ID는 순차적으로 늘고 재사용되지 않습니다.
- 생성, 조회, 갱신, 목록은 평범한 CRUD입니다.
- `claim`이 게이트입니다. 소유자와 막는 task를 확인한 뒤에 소유자를 지정합니다.
- 디스크의 그래프가 계획을 담습니다. 진행 중인 백그라운드 작업은 별도의 런타임이 추적할 수 있습니다.

### 신규: task 저장소와 선점 게이트

`create`는 id를 할당하고 task를 씁니다.

```python
def create(self, subject, blocked_by=()):              # src/tasks.py
    tid = self._next_id()
    task = {"id": tid, "subject": subject, "status": "pending",
            "owner": None, "blockedBy": list(blocked_by), "blocks": []}
    self._write(task)
    ...                                                # keep the reverse `blocks` edge in sync
    return task
```

`claim`은 락을 겁니다. 그래서 확인 후 설정이 여러 worker 사이에서 안전합니다.

```python
def claim(self, tid, owner):                           # src/tasks.py
    with self._lock():                                 # fcntl.flock, exclusive
        task = self.get(tid)
        if task["owner"] is not None:
            return {"ok": False, "reason": "already_claimed"}
        unmet = [b for b in task["blockedBy"]
                 if (self.get(b) or {}).get("status") != "completed"]
        if unmet:
            return {"ok": False, "reason": "blocked"}
        task["owner"], task["status"] = owner, "in_progress"
        self._write(task)
        return {"ok": True, "task": task}
```

### 통합 방식

task tool은 저장소를 얇게 감싼 것입니다.

```python
for t in task_tools(TaskStore(dir)):                   # src/demo.py
    reg.register(t)                                    # TaskCreate / TaskUpdate / TaskGet / TaskList
```

loop는 바뀌지 않습니다. 모델은 `TaskCreate`, `TaskUpdate`, `TaskGet`, `TaskList`를 다른 tool과 똑같이 호출합니다.

---

## 시스템별

영속 task 그래프를 어떤 모양으로 만들고 어떻게 진행시키는지.

| | Claude Code | deepseek-harness |
| --- | --- | --- |
| **장점** | 파일에 저장된 task가 크래시를 견디고 여러 worker를 지원함. | task 상태가 session 로그에 실려 있어서 재생, fork, 재개가 거저 따라옴. |
| **단점** | 읽기, 쓰기, 락 비용이 듦. 레코드에 검증이 필요함. | 의존성 간선도 선점 게이트도 없음. session마다 목표 하나. |
| **이유** | 메모리 목록은 프로세스와 함께 죽으므로, 계획은 프로세스보다 오래 살아남아야 함. | session 로그가 유일한 진실 원천이므로, task 상태는 이벤트임. |
| **방법: task 레코드** | task마다 JSON 파일 하나. id, 제목, 상태, 소유자, 간선. | 목록 전체의 스냅숏, 그리고 단계와 라운드 상한을 가진 목표 하나. |
| **방법: 의존성** | `blockedBy`와 `blocks` 간선. 막는 task가 끝날 때까지 선점을 거부함. | 없음. 목록 순서가 유일한 순서임. |
| **방법: 영속성** | task마다 파일 하나, 그리고 마지막으로 발급한 id. 스위치를 켜면 todo를 대체할 수 있음. | session 이벤트를 불러올 때 재생함. 스스로 이어 가는 상태는 저장하지 않음. |
| **방법: 수명 주기** | `pending -> in_progress -> completed`, 선점에는 락. | 목표 단계는 active, paused, blocked, complete. 편집에는 사람이 필요함. |

---

## 실패 모드

- **의존성 순환.** 두 task가 서로를 막을 수 있음. 그래프를 비순환으로 유지하거나 순환 검사를 넣습니다.
- **선점 경합.** 두 agent가 같은 task를 시도할 수 있음. 선점 경로에 락을 겁니다.
- **주인 없는 진행 중 task.** worker가 선점한 뒤 죽을 수 있음. worker가 종료될 때 소유자를 비웁니다.
- **잘못된 레코드.** 손으로 고쳤거나 오래된 파일은 스키마와 다를 수 있음. 안전하게 파싱하고 잘못된 레코드는 건너뜁니다.
- **영속 시스템 비활성.** 메모리의 todo는 여전히 잃을 수 있음. 살아남아야 하는 작업에는 디스크 기반 task를 씁니다.

---

## 실행 방법

[`src/`](src/)는 11을 이어받고 다음을 추가합니다.

- [`tasks.py`](src/tasks.py): 디스크 기반 `TaskStore`, 선점 게이트, `Task*` tool.
- [`test.py`](src/test.py): 의존성, 선점 게이트, agent 10개의 선점 경합을 확인합니다.
- [`demo.py`](src/demo.py): task 세 개짜리 계획을 JSON 파일로 저장합니다.

```bash
python sections/12-task-system/src/test.py         # offline checks, no key
uv run python sections/12-task-system/src/demo.py  # live demo, needs a key
```

---

## 출처

- [Claude Code source](https://github.com/yasasbanukaofficial/claude-code): `utils/tasks.ts`, `Task.ts`, 그리고 `Task*Tool/` 디렉터리들.
- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness), `dsh-v0.1.0-rc.7` 기준:
  `packages/goal/goal/src/index.ts`, `packages/goal/goal-round-driver/README.md`, `packages/todo/tool-todo/README.md`,
  `docs/subsystems/goal.md`, `docs/persistence-catalog.md`.
- [learn-claude-code · s12_task_system](https://github.com/shareAI-lab/learn-claude-code): 섹션 구성.
