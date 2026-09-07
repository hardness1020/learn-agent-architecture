# 12 · Task system

[English](README.md) · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · **한국어**

> 의존 관계가 있는 내구성 있는 작업으로 업무를 저장합니다.

섹션 5의 할 일 목록은 메모리에서만 존재하며 프로세스가 종료되면 사라집니다. 또한 어떤 작업이 다른 작업을 기다려야 하는지도 알 수 없습니다.

작업 시스템은 업무를 디스크에 기록으로 저장합니다. 각 기록은 의존성을 가질 수 있습니다. 작업자는 차단 작업이 완료되면 업무를 가져갑니다.

작업 시스템은 다음을 수행해야 합니다:

1. 각 업무 단위를 내구성 있는 객체로 저장합니다.
2. 순서를 데이터로 나타냅니다.
3. 턴, 세션 및 충돌을 견딥니다.
4. 단일 작업자만 업무를 가져갈 수 있게 합니다.

이 계층이 없으면 계획은 현재 context window에만 존재합니다.

---

## 메커니즘

![메커니즘 다이어그램](assets/12-task-system.png)

작업은 디스크에 있는 JSON 레코드입니다. `blockedBy`와 `blocks`는 종속성 엣지입니다. 파일 잠금은 요청을 직렬화합니다.

- ID는 순차적이며 재사용되지 않습니다.
- 생성, 가져오기, 업데이트, 목록은 일반적인 CRUD입니다.
- `claim`는 게이트입니다. 소유권과 방해 요소를 확인한 후 소유자를 할당합니다.
- 디스크 그래프는 계획을 저장합니다. 별도의 runtime은 활성 백그라운드 작업을 추적할 수 있습니다.

### 새로움: 작업 저장소와 청구 게이트

`create`는 ID를 할당하고 작업을 기록합니다:

```python
def create(self, subject, blocked_by=()):              # src/tasks.py
    tid = self._next_id()
    task = {"id": tid, "subject": subject, "status": "pending",
            "owner": None, "blockedBy": list(blocked_by), "blocks": []}
    self._write(task)
    ...                                                # keep the reverse `blocks` edge in sync
    return task
```

`claim`는 잠겨 있습니다. 이는 워커 간 체크-후-설정이 안전하게 이루어지게 합니다:

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

작업 도구는 저장소 위에 얇은 래퍼입니다:

```python
for t in task_tools(TaskStore(dir)):                   # src/demo.py
    reg.register(t)                                    # TaskCreate / TaskUpdate / TaskGet / TaskList
```

루프는 변하지 않습니다. 모델은 다른 도구들처럼 `TaskCreate`, `TaskUpdate`, `TaskGet`, `TaskList`를 호출합니다.

---

## 시스템별

내구성 작업 그래프가 어떻게 형성되고 진행되는지.

| | Claude Code | deepseek-harness |
| --- | --- | --- |
| **장점** | 파일 기반 작업은 충돌 시에도 유지되며 여러 작업자를 지원합니다. | 작업 상태가 세션 로그를 따라가므로 재생, 분기, 재개가 무료로 가능합니다. |
| **단점** | 읽기, 쓰기 및 잠금 비용이 듭니다. 기록은 검증이 필요합니다. | 의존성 간선과 청구 게이트가 없습니다. 세션당 하나의 목표만 있습니다. |
| **이유** | 메모리 내 목록은 프로세스와 함께 사라지므로 계획이 이를 초과해야 합니다. | 세션 로그가 유일한 진실의 출처이므로 작업 상태는 이벤트입니다. |
| **방법: 작업 기록** | 작업당 JSON 파일: ID, 주제, 상태, 소유자, 연결선. | 전체 목록 스냅샷, 하나의 목표와 한 단계 및 라운드 제한. |
| **방법: 의존성** | `blockedBy` 및 `blocks` 연결선. 블로커가 완료될 때까지 청구는 거부됨. | 없음. 목록 순서만 유일한 순서 지정 방식. |
| **방법: 지속성** | 작업당 하나의 파일, 가장 큰 발급 ID 포함. 스위치가 할 일 목록을 대체 가능. | 세션 이벤트, 로드 시 재생됨. 자기 계속은 절대 저장되지 않음. |
| **방법: 라이프사이클** | `pending -> in_progress -> completed`, 청구에 잠금 적용. | 목표 단계: 활성, 일시 정지, 차단됨, 완료. 편집은 사람이 필요. |

---

## 실패 모드

- **의존성 순환.** 두 작업이 서로를 차단할 수 있음. 그래프를 비순환으로 유지하거나 순환 확인 추가.
- **클레임 경쟁.** 두 에이전트가 같은 작업을 시도할 수 있습니다. 클레임 경로를 잠그세요.
- **작업 중 고아 상태.** 작업자가 클레임 후 죽을 수 있습니다. 작업자 종료 시 소유권을 제거하세요.
- **잘못된 레코드.** 수동 편집되었거나 오래된 파일은 스키마와 일치하지 않을 수 있습니다. 안전하게 파싱하고 잘못된 레코드를 건너뛰세요.
- **내구성 시스템 비활성화.** 메모리 기반 할 일은 여전히 손실될 수 있습니다. 반드시 유지되어야 하는 작업에는 디스크 기반 작업을 사용하세요.

---

## 실행 가능

[`src/`](src/)은 11을 전달하고 다음을 추가합니다:

- [`tasks.py`](src/tasks.py): 디스크 기반 `TaskStore`, 클레임 게이트, 그리고 `Task*` 도구.
- [`test.py`](src/test.py): 종속성 확인, 클레임 게이팅, 그리고 10-에이전트 클레임 경쟁.
- [`demo.py`](src/demo.py): JSON 파일로 세 가지 작업 계획을 지속합니다.

```bash
python sections/12-task-system/src/test.py         # offline checks, no key
uv run python sections/12-task-system/src/demo.py  # live demo, needs a key
```

---

## 출처

- [Claude Code 소스](https://github.com/yasasbanukaofficial/claude-code): `utils/tasks.ts`, `Task.ts` 및 `Task*Tool/` 디렉토리.
- [deepseek-harness 소스](https://github.com/deepseek-ai/deepseek-harness) `dsh-v0.1.0-rc.7`에서:
  `packages/goal/goal/src/index.ts`, `packages/goal/goal-round-driver/README.md`, `packages/todo/tool-todo/README.md`,
  `docs/subsystems/goal.md`, `docs/persistence-catalog.md`.
- [learn-claude-code · s12_task_system](https://github.com/shareAI-lab/learn-claude-code): 섹션 프레이밍.
