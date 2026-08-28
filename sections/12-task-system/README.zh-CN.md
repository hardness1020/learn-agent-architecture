# 12 · Task system

[English](README.md) · [繁体中文](README.zh-TW.md) · **简体中文**

> 把工作存成可持久化的 task，连同依赖关系一起管理。

第 5 章的 todo list 只存在内存中，process 一结束就会消失，也无法表达哪些工作必须先完成。

task system 会把每个工作单元存成磁盘上的记录，并保留它和其他 task 的依赖关系。只有前置条件都完成后，worker 才能认领该 task。

task system 必须：

1. 把每个工作单元保存成持久化对象。
2. 用数据明确表示执行顺序与依赖关系。
3. 即使跨 turn、跨 session 或遇到崩溃，task 仍然存在。
4. 确保同一个 task 不会被多个 worker 重复认领。

少了这一层，计划只能活在目前的 context window 中，无法可靠地延续。

---

## 核心机制

![机制图](assets/12-task-system.png)

每个 task 都是磁盘上的一笔 JSON 记录。`blockedBy` 和 `blocks` 用来描述 task 之间的先后关系。worker 认领 task 前必须先取得 file lock，确保同一时间只有一个 worker 能完成认领。

- ID 是连续的，而且永不重复使用。
- create、get、update、list 都是单纯的 CRUD。
- `claim` 是那道关卡。它在指派 owner 之前，会先检查 ownership 和阻挡条件。
- 磁盘上的图存储整个计划。另一个 runtime 可以追踪正在进行的后台执行工作。

### 本章添加：task store 与 claim 关卡

`create` 会配置一个 id 并写入一笔 task：

```python
def create(self, subject, blocked_by=()):              # src/tasks.py
    tid = self._next_id()
    task = {"id": tid, "subject": subject, "status": "pending",
            "owner": None, "blockedBy": list(blocked_by), "blocks": []}
    self._write(task)
    ...                                                # keep the reverse `blocks` edge in sync
    return task
```

`claim` 有加锁。这让「先检查再配置」在多个 worker 之间也安全：

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

### 如何集成到现有架构

task 工具只是 store 之上的一层薄包装：

```python
for t in task_tools(TaskStore(dir)):                   # src/demo.py
    reg.register(t)                                    # TaskCreate / TaskUpdate / TaskGet / TaskList
```

loop 没有改变。model 就像调用其他任何工具一样，调用 `TaskCreate`、`TaskUpdate`、`TaskGet` 和 `TaskList`。

---

## 不同系统怎么做

持久的 task 图如何塑形，又如何推进。

| | Claude Code | deepseek-harness |
| --- | --- | --- |
| **优点** | 以文件为后盾的 task 能在崩溃后存活，也支持多个 worker。 | task 状态就写在 session log 里，重放、fork、续跑全都免费附送。 |
| **限制** | 代价是文件系统的读、写和锁，记录还要验证。 | 没有依赖关系的边，也没有认领机制。一个 session 只有一个 goal。 |
| **设计原因** | 放在内存里的列表会跟着 process 消失，计划得活得比它久。 | session log 是唯一的事实来源，所以 task 状态就是一连串事件。 |
| **做法：task record** | 每个 task 一个 JSON 文件：id、subject、status、owner 和边。 | 一份整份列表的快照，加上一个 goal，带 phase 和轮数上限。 |
| **做法：dependencies** | `blockedBy` 和 `blocks` 两种边。阻挡条件没清完，认领会被拒绝。 | 没有。顺序就只看列表本身的排列。 |
| **做法：persistence** | 每个 task 一个档，外加已发出的最大 id。一个开关决定要不要替换 todo list。 | session 事件，加载时重放。自动续跑的开关从不写进磁盘。 |
| **做法：lifecycle** | `pending -> in_progress -> completed`，认领动作靠一把锁序列化。 | goal 的 phase：active、paused、blocked、complete。要改得由人出手。 |

---

## 常见问题

- **依赖循环（Dependency cycle）：**两个 task 可能互相阻挡。让图保持无环，或加上循环检查。
- **认领竞态（Claim race）：**两个 agent 可能抢同一个 task。把认领路径加锁。
- **卡在 in_progress 的孤儿 task：**worker 可能在认领后死掉。在 worker 离开时清掉 ownership。
- **无效记录（Invalid record）：**手动编辑或旧版的文件可能不符合 schema。安全地解析，并跳过坏掉的记录。
- **持久系统被关闭：**in-memory todo 仍可能遗失。对必须存活的工作，改用以磁盘为后盾的 task。

---

## 动手跑跑看

[`src/`](src/) 把 11 带了过来，并加上：

- [`tasks.py`](src/tasks.py)：一个以磁盘为后盾的 `TaskStore`、claim 关卡，以及 `Task*` 工具。
- [`test.py`](src/test.py)：检查依赖关系、认领关卡，以及一场 10-agent 的认领竞态。
- [`demo.py`](src/demo.py)：把一个三个 task 的计划持久化成 JSON 文件。

```bash
python sections/12-task-system/src/test.py         # offline checks, no key
uv run python sections/12-task-system/src/demo.py  # live demo, needs a key
```

---

## 参考资料

- [Claude Code source](https://github.com/yasasbanukaofficial/claude-code)：`utils/tasks.ts`、`Task.ts`，以及 `Task*Tool/` 目录。
- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness)（`dsh-v0.1.0-rc.7`）：
  `packages/goal/goal/src/index.ts`、`packages/goal/goal-round-driver/README.md`、`packages/todo/tool-todo/README.md`、
  `docs/subsystems/goal.md`、`docs/persistence-catalog.md`。
- [learn-claude-code · s12_task_system](https://github.com/shareAI-lab/learn-claude-code)：章节框架。
