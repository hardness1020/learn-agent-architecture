# 15 · Worktree isolation

[English](README.md) · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · **한국어**

> 병렬 에이전트에는 별도의 작업 디렉토리를 제공합니다.

하나의 작업 디렉토리는 공유 가능한 변경 상태입니다. 두 에이전트가 동시에 같은 파일을 쓰면, 하나가 다른 하나의 작업을 덮어쓸 수 있습니다.

작업 시스템은 어떤 작업이 존재하는지를 결정합니다. Subagents는 작업이 어떻게 나누어지는지를 결정합니다.
Worktree isolation는 쓰기를 분리하여 유지합니다: 각 에이전트는 자신의 디렉토리에 쓰므로 서로 방해하지 않습니다.

각 작업 단위는 고유한 체크아웃과 브랜치를 갖습니다. 에이전트의 파일 및 셸 도구는 그 체크아웃 내에서 경로를 해결합니다.

격리 계층은 다음을 수행해야 합니다:

1. 각 작업 단위에 대한 개인 체크아웃을 생성합니다.
2. 도구를 그 체크아웃에 바인딩합니다.
3. worktree 루트를 벗어날 수 있는 이름은 거부합니다.
4. 깨끗한 worktrees를 제거하고 더러운 것은 검토를 위해 보관하십시오.

이 레이어가 없으면 동일한 디렉터리를 동시에 편집하는 에이전트가 서로의 파일을 손상시킬 수 있습니다.

---

## 메커니즘

![메커니즘 다이어그램](assets/15-worktree-isolation.png)

두 가지 부분이 있습니다:

1. 작업 단위마다 개별적인 git worktree.
2. 컨텍스트별 작업 디렉터리 바인딩.

바인딩은 에이전트 컨텍스트 범위 내에서 이루어져야 합니다. 글로벌 `chdir`은 동일 프로세스 내의 다른 에이전트에 영향을 줄 수 있습니다.

- 각 worktree는 자체 브랜치에서 동일한 저장소를 체크아웃한 것입니다.
- 슬러그는 경로가 되므로, 경로 결합 전에 반드시 확인하십시오.
- 도구는 글로벌 프로세스의 cwd가 아니라 컨텍스트에서 `get_cwd()`를 읽습니다.
- Teardown는 깨끗한 worktrees만 제거합니다. 더러운 worktrees는 검토를 위해 남겨둡니다.

### 새로움: worktree 및 cwd 바인딩

`worktree.py`는 슬러그(slug)를 검증하고, worktree를 생성하며, 컨텍스트 변수를 통해 cwd를 바인딩합니다:

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

- `cwd_override`는 현재 컨텍스트에만 영향을 줍니다.
- 도구는 `get_cwd()`를 하위 프로세스 및 파일 작업에 전달합니다.
- `create`는 `git worktree add -B worktree-<slug>`를 실행합니다.
- `validate_slug`는 경로 탐색(traversal) 및 허용되지 않은 문자를 거부합니다.
- `remove`는 강제로 하지 않는 한 더러운 worktree를 제거하는 것을 거부합니다.

### 통합 방식

Isolation은 루프 외부에서의 턴(turn)을 감쌉니다:

```python
wt = worktree.create(repo, "agent-1")                 # src/demo.py
with worktree.cwd_override(wt):
    run_turn([{"role": "user", "content": prompt}], model, reg, session)
worktree.remove(repo, "agent-1")                       # clean -> remove, dirty -> keep
```

루프와 subagent 경로는 특별한 로직이 필요하지 않습니다. 도구에서 보는 작업 디렉토리만 변경됩니다.

이 모델을 선택 가능하게 만들려면 `Agent` 도구 스키마와 `spawn` 브랜치에 `isolation` 옵션을 추가하십시오.

---

## 시스템별

각 시스템이 병렬 작업을 어떻게 격리하고 정리하는지.

| | Claude Code |
| --- | --- |
| **장점** | 실제 파일 시스템 격리와 깨끗한 차이점. 검토를 위해 더러운 worktrees가 유지되므로 작업이 조용히 손실되지 않습니다. |
| **단점** | Worktrees는 디스크, 설정 시간 및 나중의 병합 단계가 필요합니다. |
| **이유** | 여러 에이전트가 하나의 공유 디렉토리에 안전하게 쓸 수 없으므로 각 작업 단위는 자체 체크아웃에 작성합니다. |
| **방법: 격리 유닛** | Git worktree 작업 또는 세션별, 각자 별도의 브랜치에서. 모델은 subagent를 생성할 때 하나를 요청할 수 있습니다. |
| **방법: 바인딩** | subagents용 스코프된 cwd, 따라서 동시에 실행되는 에이전트가 서로 영향을 미치지 않음. 세션 모드는 프로세스 cwd를 변경합니다. 작업 기록은 바인딩을 저장하지 않습니다. |
| **방법: 정리** | 깨끗한 worktrees 제거. 사용자가 명시적으로 변경 사항을 버리지 않는 한 더러운 것들은 유지. 주기적인 스윕으로 오래된 임시 worktrees 제거. |

---

## 실패 모드

- **슬러그에서 경로 탐색.** 경로 결합 또는 git 명령 전에 검증.
- **제거 시 무음 손실.** 사용자가 명시적으로 변경 사항을 버리지 않는 한 더러운 worktrees 유지.
- **에이전트 간 cwd 누수.** 동시 subagents에 대해 컨텍스트 로컬 cwd를 사용하세요.
- **오래된 worktree 누적.** 알려진 일시적 worktrees만 정리하세요.
- **포크 후 오래된 읽기.** 포크된 자식에게 worktree 안에서 파일을 다시 읽도록 하세요.

---

## 실행 가능

[`src/`](src/)는 14개를 전달하고 다음을 추가합니다:

- [`worktree.py`](src/worktree.py): 슬러그 검증, worktree 생성, 컨텍스트 로컬 cwd, 안전한 제거.
- [`test.py`](src/test.py): 서로 다른 worktrees에 쓰는 두 에이전트를 확인하고 깨끗/더러운 제거 게이트 확인.
- [`demo.py`](src/demo.py): worktree 안에서 라이브 턴 실행.

루프와 subagent 경로는 변경되지 않았습니다. 격리는 cwd를 바인딩하여 회전을 감쌉니다.

```bash
python sections/15-worktree-isolation/src/test.py         # offline checks, real git, no key
uv run python sections/15-worktree-isolation/src/demo.py  # live demo, needs a key
```

---

## 출처

- [Claude Code 소스](https://github.com/yasasbanukaofficial/claude-code):
  `tools/EnterWorktreeTool/`, `tools/ExitWorktreeTool/`, `utils/worktree.ts`, `utils/cwd.ts`, `tools/AgentTool/AgentTool.tsx`.
- [learn-claude-code · s18_worktree_isolation](https://github.com/shareAI-lab/learn-claude-code): 섹션 프레이밍.
