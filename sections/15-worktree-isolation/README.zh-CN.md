# 15 · Worktree isolation

[English](README.md) · [繁體中文](README.zh-TW.md) · **简体中文** · [日本語](README.ja.md) · [한국어](README.ko.md)

> 为并行工作的 agent 准备彼此隔离的工作目录。

单一工作目录是一份共享的可变状态。两个 agent 同时修改同一个文件时，其中一方可能覆盖另一方的成果。

task system 负责记录有哪些工作，subagent 负责决定工作怎么拆，而 worktree isolation 则把实际写入隔开。每个 agent 都在自己的目录工作，避免互相干扰。

每个工作单元都有独立的 checkout 和 branch，agent 的文件工具与 shell 工具也只会在该 checkout 中解析路径。

隔离层必须：

1. 为每个工作单元创建独立 checkout。
2. 把所有工具绑定到对应的 checkout。
3. 拒绝任何可能逃出 worktree 根目录的路径。
4. 移除没有变更的 worktree，保留有改动的供后续审查。

没有这一层，多个 agent 同时修改同一个目录时，可能损坏彼此的文件。

---

## 核心机制

![机制图](assets/15-worktree-isolation.png)

有两个部分：

1. 每个工作单元有自己私有的 git worktree。
2. 每个 context 有自己绑定的工作目录。

这个绑定必须限定在 agent context 的范围内。全局的 `chdir` 会影响同一个 process 里的其他 agent。

- 每个 worktree 都是同一个 repo 在自己 branch 上的 checkout。
- slug 会变成路径，所以在任何路径组合之前先验证它。
- 工具从 context 读取 `get_cwd()`，而不是从全局 process cwd 读取。
- 收尾清理时，只移除干净的 worktree。有变更的会保留下来供审查。

### 本章添加：worktree 与 cwd 绑定

`worktree.py` 验证一个 slug、创建一个 worktree，并通过 context variable 绑定 cwd：

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

- `cwd_override` 只影响当前的 context。
- 工具把 `get_cwd()` 传给子进程与文件操作。
- `create` 执行 `git worktree add -B worktree-<slug>`。
- `validate_slug` 拒绝路径穿越与不允许的字符。
- `remove` 除非强制，否则拒绝移除有变更的 worktree。

### 如何集成到现有架构

隔离从 loop 外面包住一个 turn：

```python
wt = worktree.create(repo, "agent-1")                 # src/demo.py
with worktree.cwd_override(wt):
    run_turn([{"role": "user", "content": prompt}], model, reg, session)
worktree.remove(repo, "agent-1")                       # clean -> remove, dirty -> keep
```

loop 与 subagent 路径不需要特殊逻辑。只有工具看到的工作目录改变了。

若要让模型能自行选择这个模式，在 `Agent` 工具的 schema 加上 `isolation` 选项，并在 `spawn` 里分支处理。

---

## 不同系统怎么做

各系统如何隔离并行工作并在事后清理。

| | Claude Code |
| --- | --- |
| **优点** | 真正的文件系统隔离，diff 也干净。有变更的 worktree 会留下来供审查，成果不会默默遗失。 |
| **限制** | 要付出磁盘空间、构建时间，以及之后的 merge 步骤。 |
| **设计原因** | 好几个 agent 同时写同一个目录不安全，所以每个工作单元都在自己的 checkout 里写。 |
| **做法：isolation unit** | 每个 task 或 session 一个 git worktree，各自有自己的 branch。模型开 subagent 时可以自己要求一个。 |
| **做法：binding** | subagent 用限定范围的 cwd，并行的 agent 互不影响。session 模式改 process cwd。task 记录从不保存这个绑定。 |
| **做法：cleanup** | 移除干净的 worktree。有变更的会保留，除非用户明确舍弃变更。周期性的清扫会移除旧的临时 worktree。 |

---

## 常见问题

- **slug 里的路径穿越：**在路径组合或 git 命令之前先验证。
- **移除时默默遗失：**除非用户明确舍弃变更，否则保留有变更的 worktree。
- **cwd 在 agent 之间外泄：**对并行的 subagent 使用 context-local 的 cwd。
- **陈旧 worktree 堆积：**只清扫已知的临时 worktree。
- **fork 后读到陈旧内容：**告诉 fork 出来的子进程重新读取 worktree 里的文件。

---

## 动手跑跑看

[`src/`](src/) 承接第 14 章并加上：

- [`worktree.py`](src/worktree.py)：slug 验证、worktree 创建、context-local 的 cwd，以及安全移除。
- [`test.py`](src/test.py)：检查两个 agent 在各自的 worktree 里写入，以及干净/有变更的移除闸门。
- [`demo.py`](src/demo.py)：在 worktree 里跑一个 live turn。

loop 与 subagent 路径不变。隔离通过绑定 cwd 来包住 turn。

```bash
python sections/15-worktree-isolation/src/test.py         # offline checks, real git, no key
uv run python sections/15-worktree-isolation/src/demo.py  # live demo, needs a key
```

---

## 参考资料

- [Claude Code 源代码](https://github.com/yasasbanukaofficial/claude-code)：
  `tools/EnterWorktreeTool/`、`tools/ExitWorktreeTool/`、`utils/worktree.ts`、`utils/cwd.ts`、`tools/AgentTool/AgentTool.tsx`。
- [learn-claude-code · s18_worktree_isolation](https://github.com/shareAI-lab/learn-claude-code)：章节框架。
