# 12 · Task system

[English](README.md) · **繁體中文** · [简体中文](README.zh-CN.md)

> 把工作存成可持久化的 task，連同相依關係一起管理。

第 5 章的 todo list 只存在記憶體中，process 一結束就會消失，也無法表達哪些工作必須先完成。

task system 會把每個工作單元存成硬碟上的記錄，並保留它和其他 task 的相依關係。只有前置條件都完成後，worker 才能認領該 task。

task system 必須：

1. 把每個工作單元保存成持久化物件。
2. 用資料明確表示執行順序與相依關係。
3. 即使跨 turn、跨 session 或遇到當機，task 仍然存在。
4. 確保同一個 task 不會被多個 worker 重複認領。

少了這一層，計畫只能活在目前的 context window 中，無法可靠地延續。

---

## 核心機制

![機制圖](assets/12-task-system.png)

每個 task 都是硬碟上的一筆 JSON 記錄。`blockedBy` 和 `blocks` 用來描述 task 之間的先後關係。worker 認領 task 前必須先取得 file lock，確保同一時間只有一個 worker 能完成認領。

- ID 是連續的，而且永不重複使用。
- create、get、update、list 都是單純的 CRUD。
- `claim` 是那道關卡。它在指派 owner 之前，會先檢查 ownership 和阻擋條件。
- 硬碟上的圖儲存整個計畫。另一個 runtime 可以追蹤正在進行的背景執行工作。

### 本章新增：task store 與 claim 關卡

`create` 會配置一個 id 並寫入一筆 task：

```python
def create(self, subject, blocked_by=()):              # src/tasks.py
    tid = self._next_id()
    task = {"id": tid, "subject": subject, "status": "pending",
            "owner": None, "blockedBy": list(blocked_by), "blocks": []}
    self._write(task)
    ...                                                # keep the reverse `blocks` edge in sync
    return task
```

`claim` 有加鎖。這讓「先檢查再設定」在多個 worker 之間也安全：

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

### 如何接進現有架構

task 工具只是 store 之上的一層薄包裝：

```python
for t in task_tools(TaskStore(dir)):                   # src/demo.py
    reg.register(t)                                    # TaskCreate / TaskUpdate / TaskGet / TaskList
```

loop 沒有改變。model 就像呼叫其他任何工具一樣，呼叫 `TaskCreate`、`TaskUpdate`、`TaskGet` 和 `TaskList`。

---

## 不同系統怎麼做

持久的 task 圖如何塑形，又如何推進。

| | Claude Code | deepseek-harness |
| --- | --- | --- |
| **優點** | 以檔案為後盾的 task 能在當機後存活，也支援多個 worker。 | task 狀態就寫在 session log 裡，重放、fork、續跑全都免費附送。 |
| **限制** | 代價是檔案系統的讀、寫和鎖，記錄還要驗證。 | 沒有相依關係的邊，也沒有認領機制。一個 session 只有一個 goal。 |
| **設計原因** | 放在記憶體裡的清單會跟著 process 消失，計畫得活得比它久。 | session log 是唯一的事實來源，所以 task 狀態就是一連串事件。 |
| **做法：task record** | 每個 task 一個 JSON 檔：id、subject、status、owner 和邊。 | 一份整份清單的快照，加上一個 goal，帶 phase 和輪數上限。 |
| **做法：dependencies** | `blockedBy` 和 `blocks` 兩種邊。阻擋條件沒清完，認領會被拒絕。 | 沒有。順序就只看清單本身的排列。 |
| **做法：persistence** | 每個 task 一個檔，外加已發出的最大 id。一個開關決定要不要取代 todo list。 | session 事件，載入時重放。自動續跑的開關從不寫進硬碟。 |
| **做法：lifecycle** | `pending -> in_progress -> completed`，認領動作靠一把鎖序列化。 | goal 的 phase：active、paused、blocked、complete。要改得由人出手。 |

---

## 常見問題

- **相依循環（Dependency cycle）：**兩個 task 可能互相阻擋。讓圖保持無環，或加上循環檢查。
- **認領競態（Claim race）：**兩個 agent 可能搶同一個 task。把認領路徑加鎖。
- **卡在 in_progress 的孤兒 task：**worker 可能在認領後死掉。在 worker 離開時清掉 ownership。
- **無效記錄（Invalid record）：**手動編輯或舊版的檔案可能不符合 schema。安全地解析，並跳過壞掉的記錄。
- **持久系統被關閉：**in-memory todo 仍可能遺失。對必須存活的工作，改用以硬碟為後盾的 task。

---

## 動手跑跑看

[`src/`](src/) 把 11 帶了過來，並加上：

- [`tasks.py`](src/tasks.py)：一個以硬碟為後盾的 `TaskStore`、claim 關卡，以及 `Task*` 工具。
- [`test.py`](src/test.py)：檢查相依關係、認領關卡，以及一場 10-agent 的認領競態。
- [`demo.py`](src/demo.py)：把一個三個 task 的計畫持久化成 JSON 檔。

```bash
python sections/12-task-system/src/test.py         # offline checks, no key
uv run python sections/12-task-system/src/demo.py  # live demo, needs a key
```

---

## 參考資料

- [Claude Code source](https://github.com/yasasbanukaofficial/claude-code)：`utils/tasks.ts`、`Task.ts`，以及 `Task*Tool/` 目錄。
- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness)（`dsh-v0.1.0-rc.7`）：
  `packages/goal/goal/src/index.ts`、`packages/goal/goal-round-driver/README.md`、`packages/todo/tool-todo/README.md`、
  `docs/subsystems/goal.md`、`docs/persistence-catalog.md`。
- [learn-claude-code · s12_task_system](https://github.com/shareAI-lab/learn-claude-code)：章節框架。
