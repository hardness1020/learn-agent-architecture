# 15 · Worktree isolation

[English](README.md) · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · **한국어**

> 병렬로 도는 agent에게 각각 별도의 작업 디렉터리를 줍니다.

작업 디렉터리 하나를 같이 쓰면 그 디렉터리가 공유 가변 상태가 됩니다. agent 둘이 같은 파일을 동시에 쓰면 한쪽이 다른 쪽 작업을 덮어쓸 수 있습니다.

task 시스템은 어떤 일이 있는지를 정합니다. subagent는 그 일을 어떻게 나눌지를 정합니다.
worktree isolation은 쓰기를 서로 떼어 놓습니다. agent마다 자기 디렉터리에만 쓰므로 서로 간섭하지 않습니다.

작업 단위마다 자기 checkout과 브랜치를 하나씩 받습니다. agent의 파일 tool과 shell tool은 그 checkout 안에서 경로를 해석합니다.

isolation 계층이 해야 하는 일은 다음과 같습니다.

1. 작업 단위마다 전용 checkout을 만듭니다.
2. tool을 그 checkout에 묶습니다.
3. worktree 루트를 벗어나는 이름은 거부합니다.
4. 깨끗한 worktree는 지우고, 변경이 남은 worktree는 검토용으로 남깁니다.

이 계층이 없으면 같은 디렉터리를 동시에 편집하는 agent들이 서로의 파일을 망가뜨릴 수 있습니다.

---

## 메커니즘

![Mechanism diagram](assets/15-worktree-isolation.png)

구성 요소는 둘입니다.

1. 작업 단위마다 하나씩 두는 전용 git worktree.
2. context 단위의 작업 디렉터리 바인딩.

이 바인딩은 agent context 범위로 한정해야 합니다. 전역 `chdir`을 쓰면 같은 프로세스 내의 다른 agent까지 영향을 받습니다.

- worktree는 모두 같은 저장소를 각자의 브랜치로 checkout한 것입니다.
- slug는 경로가 되므로, 경로를 이어 붙이기 전에 먼저 검증합니다.
- tool은 전역 프로세스 cwd가 아니라 context에서 `get_cwd()`를 읽습니다.
- 정리 단계는 깨끗한 worktree만 지웁니다. 변경이 남은 worktree는 검토용으로 남습니다.

### 이번에 추가되는 것: worktree와 cwd 바인딩

`worktree.py`는 slug를 검증하고, worktree를 만들고, context 변수로 cwd를 묶습니다.

```python
_cwd = contextvars.ContextVar("cwd", default=None)   # per-context cwd

@contextlib.contextmanager
def cwd_override(path):
    token = _cwd.set(str(path))                       # bind, never os.chdir
    try:
        yield
    finally:
        _cwd.reset(token)

def remove(repo_root, slug, force=False):
    path = _path(repo_root, slug)                     # _path validates the slug first
    if not force and changes(path):
        return False                                  # keep for review
    _git(repo_root, "worktree", "remove", "--force", str(path))
    _git(repo_root, "branch", "-D", f"worktree-{slug}")
    return True
```

- `cwd_override`는 현재 context에만 영향을 줍니다.
- tool은 `get_cwd()`를 서브프로세스와 파일 연산에 넘깁니다.
- `create`는 `git worktree add -B worktree-<slug>`를 실행합니다.
- `validate_slug`는 상위 경로 탈출과 허용되지 않는 문자를 거부합니다.
- `remove`는 강제 옵션이 없으면 변경이 남은 worktree를 지우지 않습니다.

### 기존 구조에 붙이는 방법

isolation은 loop 바깥에서 turn 하나를 감쌉니다.

```python
wt = worktree.create(repo, "agent-1")                 # src/demo.py
with worktree.cwd_override(wt):
    run_turn([{"role": "user", "content": prompt}], model, reg, session)
worktree.remove(repo, "agent-1")                       # clean -> remove, dirty -> keep
```

loop과 subagent 경로에는 따로 처리할 로직이 필요 없습니다. tool이 보는 작업 디렉터리만 바뀝니다.

모델이 직접 고르게 하려면 `Agent` tool 스키마에 `isolation` 옵션을 넣고 `spawn`에서 분기하면 됩니다.

---

## 시스템별

각 시스템이 병렬 작업을 어떻게 격리하고 어떻게 정리하는지 비교합니다.

| | Claude Code |
| --- | --- |
| **장점** | 파일시스템 수준의 실제 격리와 깔끔한 diff. 변경이 남은 worktree는 검토용으로 남으므로 작업이 조용히 사라지지 않음. |
| **단점** | worktree는 디스크, 준비 시간, 나중의 병합 단계를 요구함. |
| **이유** | 여러 agent가 공유 디렉터리 하나에 안전하게 쓸 수 없으므로, 작업 단위마다 자기 checkout에 씀. |
| **방법: 격리 단위** | task 또는 session 단위의 git worktree, 각각 자기 브랜치 위에 둠. 모델이 subagent를 띄울 때 요청할 수 있음. |
| **방법: 바인딩** | subagent에는 범위가 한정된 cwd를 씀. 동시에 도는 agent끼리 영향을 주지 않음. session 모드는 프로세스 cwd를 바꿈. task 기록에는 바인딩을 저장하지 않음. |
| **방법: 정리** | 깨끗한 worktree는 제거. 사용자가 변경을 명시적으로 버리지 않는 한 변경이 남은 것은 유지. 주기적 청소가 오래된 임시 worktree를 제거. |

---

## 실패 모드

- **slug를 통한 경로 탈출.** 경로를 잇거나 git 명령을 실행하기 전에 검증합니다.
- **제거 시 조용한 유실.** 사용자가 변경을 명시적으로 버리지 않는 한 변경이 남은 worktree는 남깁니다.
- **agent 사이의 cwd 누수.** 동시에 도는 subagent에는 context 지역 cwd를 씁니다.
- **낡은 worktree 누적.** 임시 worktree로 알려진 것만 청소합니다.
- **fork 이후의 오래된 읽기.** fork된 child agent에게 worktree 안의 파일을 다시 읽으라고 알려 줍니다.

---

## 실행 방법

[`src/`](src/)는 14의 코드를 이어받아 다음을 더합니다.

- [`worktree.py`](src/worktree.py): slug 검증, worktree 생성, context 지역 cwd, 안전한 제거.
- [`test.py`](src/test.py): agent 둘이 서로 다른 worktree에 쓰는 경우와 깨끗함/변경 있음 제거 게이트를 확인합니다.
- [`demo.py`](src/demo.py): worktree 안에서 실제 turn 하나를 돌립니다.

loop과 subagent 경로는 그대로입니다. isolation은 cwd를 묶어서 turn을 감쌉니다.

```bash
python sections/15-worktree-isolation/src/test.py         # offline checks, real git, no key
uv run python sections/15-worktree-isolation/src/demo.py  # live demo, needs a key
```

---

## 출처

- [Claude Code source](https://github.com/yasasbanukaofficial/claude-code):
  `tools/EnterWorktreeTool/`, `tools/ExitWorktreeTool/`, `utils/worktree.ts`, `utils/cwd.ts`, `tools/AgentTool/AgentTool.tsx`.
- [learn-claude-code · s18_worktree_isolation](https://github.com/shareAI-lab/learn-claude-code): 섹션 구성 참고.
