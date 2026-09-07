# 12 · Task system

[English](README.md) · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · **日本語** · [한국어](README.ko.md)

> 作業を依存関係のある永続的なタスクとして保存します。

セクション 5 の todo リストはメモリ内にのみ存在し、プロセスが終了すると消えます。また、どのタスクが別のタスクを待たなければならないのかもわかりません。

タスク システムは、作業をレコードとしてディスクに保存します。各レコードには依存関係がある可能性があります。ワーカーは、ブロッカーが完了するとタスクを要求します。

タスク システムは次のことを行う必要があります。

1. 各作業単位を耐久性のあるオブジェクトとして保存します。
2. 順序をデータとして表現します。
3. ターン、セッション、クラッシュを生き延びます。
4. 1 人のワーカーのみがタスクを要求できるようにします。

このレイヤーがないと、プランは現在の context window にのみ存在します。

---

## メカニズム

![機構図](assets/12-task-system.png)

タスクは、ディスク上の JSON レコードです。 `blockedBy` および `blocks` は依存関係エッジです。ファイル ロックはクレームをシリアル化します。

- ID は連続しており、再利用されることはありません。
- 作成、取得、更新、およびリストはプレーンな CRUD です。
- `claim` はゲートです。所有者を割り当てる前に、所有権とブロッカーをチェックします。
- ディスクグラフには計画が保存されます。別の runtime は、アクティブなバックグラウンド作業を追跡できます。

### 新機能: タスク ストアとクレーム ゲート

`create` は ID を割り当て、タスクを書き込みます。

```python
def create(self, subject, blocked_by=()):              # src/tasks.py
    tid = self._next_id()
    task = {"id": tid, "subject": subject, "status": "pending",
            "owner": None, "blockedBy": list(blocked_by), "blocks": []}
    self._write(task)
    ...                                                # keep the reverse `blocks` edge in sync
    return task
```

`claim` はロックされています。これにより、ワーカー間での check-then set が安全になります。

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

### 統合方法

タスク ツールはストアの薄いラッパーです。

```python
for t in task_tools(TaskStore(dir)):                   # src/demo.py
    reg.register(t)                                    # TaskCreate / TaskUpdate / TaskGet / TaskList
```

ループは変わりません。モデルは、他のツールと同様に、`TaskCreate`、`TaskUpdate`、`TaskGet`、および `TaskList` を呼び出します。

---

## システムごと

永続的なタスク グラフがどのように形成され、進化するか。

| | Claude Code | deepseek-harness |
| --- | --- | --- |
| **長所** |ファイルベースのタスクはクラッシュしても生き残り、多くのワーカーをサポートします。 |タスクの状態はセッション ログに反映されるため、リプレイ、フォーク、再開が自由に行えます。 |
| **短所** |読み取り、書き込み、ロックにコストがかかります。記録には検証が必要です。 |依存関係エッジやクレーム ゲートはありません。セッションごとに 1 つの目標。 |
| **理由** |メモリ内のリストはプロセスとともに消滅するため、計画はそれより長く存続する必要があります。 |セッション ログが唯一の真実の情報源であるため、タスクの状態はイベントです。 |
| **方法: タスクの記録** |タスクごとの JSON ファイル: ID、件名、ステータス、所有者、エッジ。 |リスト全体のスナップショットと、フェーズとラウンドキャップを含む 1 つの目標。 |
| **方法: 依存関係** | `blockedBy` および `blocks` エッジ。ブロッカーが終了するまで、クレームは拒否されます。 |なし。順序はリスト順のみです。 |
| **方法: 永続性** |タスクごとに 1 つのファイルと、発行された最大の ID。スイッチは Todo を置き換えることができます。 |ロード時に再生されるセッション イベント。自己継続は決して保存されません。 |
| **方法: ライフサイクル** | `pending -> in_progress -> completed`、クレームにロックがかかります。 |目標フェーズ: アクティブ、一時停止、ブロック、完了。編集には人間が必要です。 |

---

## 障害モード

- **依存関係サイクル** 2 つのタスクが相互にブロックされる可能性があります。グラフを非周期的に保つか、サイクル チェックを追加します。
- **レースを主張します。** 2 人のエージェントが同じタスクに挑戦できます。クレームパスをロックします。
- **孤立した進行中のタスク。** ワーカーは要求後に死亡する可能性があります。ワーカー終了時に所有権をクリアします。
- **無効なレコードです。** 手動で編集されたファイルまたは古いファイルはスキーマと一致しない可能性があります。安全に解析し、悪いレコードをスキップします。
- **永続システムが無効になっています。** メモリ内の Todo は依然として失われる可能性があります。存続する必要がある作業には、ディスクバックアップのタスクを使用します。

---

## 実行可能

[`src/`](src/) は 11 を繰り上げて次のように追加します。

- [`tasks.py`](src/tasks.py): ディスクバックアップの `TaskStore`、クレーム ゲート、および `Task*` ツール。
- [`test.py`](src/test.py): 依存関係、クレーム ゲート、および 10 エージェントのクレーム レースをチェックします。
- [`demo.py`](src/demo.py): 3 つのタスク プランを JSON ファイルとして保持します。

```bash
python sections/12-task-system/src/test.py         # offline checks, no key
uv run python sections/12-task-system/src/demo.py  # live demo, needs a key
```

---

## ソース

- [Claude Code ソース](https://github.com/yasasbanukaofficial/claude-code): `utils/tasks.ts`、`Task.ts`、および `Task*Tool/` ディレクトリ。
- [deepseek-harness ソース](https://github.com/deepseek-ai/deepseek-harness) `dsh-v0.1.0-rc.7`:
  `packages/goal/goal/src/index.ts`、`packages/goal/goal-round-driver/README.md`、`packages/todo/tool-todo/README.md`、
  `docs/subsystems/goal.md`、`docs/persistence-catalog.md`。
- [learn-claude-code · s12_task_system](https://github.com/shareAI-lab/learn-claude-code): セクションのフレーム化。
