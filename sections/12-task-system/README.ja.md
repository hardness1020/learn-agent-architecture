# 12 · Task system

[English](README.md) · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · **日本語** · [한국어](README.ko.md)

> 作業を、依存関係を持つ永続的な task として保存します。

section 5 の todo リストは memory 上にしかなく、プロセスが終われば消えます。どの task が別の task を待つべきかも表せません。

task system は作業をディスク上のレコードとして保存します。各レコードは依存関係を持てます。worker は自分をふさぐものが完了したときに task を確保します。

task system は次を満たす必要があります。

1. 作業の単位を、それぞれ永続的なオブジェクトとして保存すること。
2. 順序をデータとして表すこと。
3. turn、session、クラッシュをまたいで残ること。
4. 1 つの task を確保できる worker を 1 つだけにすること。

この層がないと、計画は今の context ウィンドウの中にしか存在しません。

---

## 仕組み

![Mechanism diagram](assets/12-task-system.png)

task はディスク上の JSON のレコードです。`blockedBy` と `blocks` が依存関係の辺です。ファイルの lock が確保を直列化します。

- ID は連番で、再利用しません。
- 作成、取得、更新、一覧は、ふつうの CRUD です。
- `claim` がゲートです。所有者を割り当てる前に、所有状況とふさいでいるものを確認します。
- ディスク上のグラフが計画を保存します。実行中のバックグラウンドの作業は、別の runtime が追えます。

### 新規: task の保存先と claim のゲート

`create` は id を割り当てて task を書き込みます。

```python
def create(self, subject, blocked_by=()):              # src/tasks.py
    tid = self._next_id()
    task = {"id": tid, "subject": subject, "status": "pending",
            "owner": None, "blockedBy": list(blocked_by), "blocks": []}
    self._write(task)
    ...                                                # keep the reverse `blocks` edge in sync
    return task
```

`claim` は lock されています。これで、確認してから書き込む処理が worker をまたいでも安全になります。

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

### 統合のしかた

task の tool は、保存先を薄く包んだものです。

```python
for t in task_tools(TaskStore(dir)):                   # src/demo.py
    reg.register(t)                                    # TaskCreate / TaskUpdate / TaskGet / TaskList
```

loop は変わりません。モデルは `TaskCreate`、`TaskUpdate`、`TaskGet`、`TaskList` を、他の tool と同じように呼び出します。

---

## システム別

永続的な task のグラフをどう形作り、どう進めるか。

| | Claude Code | deepseek-harness |
| --- | --- | --- |
| **利点** | ファイルに支えられた task はクラッシュを生き延び、多数の worker に対応する。 | task の状態が session のログに載るので、再生、fork、再開がただで付いてくる。 |
| **欠点** | 読み込み、書き込み、lock のコストがかかる。レコードには検証が要る。 | 依存関係の辺も claim のゲートもない。session ごとにゴールは 1 つ。 |
| **理由** | memory 上のリストはプロセスと共に死ぬので、計画はそれより長生きしなければならない。 | session のログが唯一の真実の源なので、task の状態はイベントである。 |
| **方法: task record** | task ごとに JSON ファイルが 1 つ。id、件名、状態、所有者、辺。 | リスト全体のスナップショットと、フェーズとラウンド上限を持つゴール 1 つ。 |
| **方法: dependencies** | `blockedBy` と `blocks` の辺。ふさいでいるものが終わるまで確保は拒否される。 | なし。リストの並び順だけが順序である。 |
| **方法: persistence** | task ごとに 1 ファイル、それと発行済みの最大 id。切り替えで todo を置き換えられる。 | session のイベントを、読み込み時に再生する。自己継続は保存しない。 |
| **方法: lifecycle** | `pending -> in_progress -> completed`。確保には lock が付く。 | ゴールのフェーズは active、paused、blocked、complete。編集には人間が要る。 |

---

## 失敗モード

- **依存関係の循環。** 2 つの task が互いをふさぎうる。グラフを非循環に保つか、循環のチェックを足す。
- **確保の競合。** 2 つの agent が同じ task を狙いうる。確保の経路を lock する。
- **孤立した in_progress の task。** worker は確保した後に死にうる。worker の終了時に所有者を消す。
- **不正なレコード。** 手で編集されたファイルや古いファイルは、スキーマに合わないことがある。安全にパースし、壊れたレコードは飛ばす。
- **永続的な仕組みを無効にしている。** memory 上の todo は依然として失われうる。残らなければならない作業には、ディスクに支えられた task を使う。

---

## 実行

[`src/`](src/) は 11 を引き継ぎ、次を追加します。

- [`tasks.py`](src/tasks.py): ディスクに支えられた `TaskStore`、claim のゲート、`Task*` の tool。
- [`test.py`](src/test.py): 依存関係、claim のゲート、10 個の agent による確保の競合を確認します。
- [`demo.py`](src/demo.py): 3 つの task からなる計画を JSON ファイルとして永続化します。

```bash
python sections/12-task-system/src/test.py         # offline checks, no key
uv run python sections/12-task-system/src/demo.py  # live demo, needs a key
```

---

## 出典

- [Claude Code source](https://github.com/yasasbanukaofficial/claude-code): `utils/tasks.ts`, `Task.ts`, and the `Task*Tool/` directories.
- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness) at `dsh-v0.1.0-rc.7`:
  `packages/goal/goal/src/index.ts`, `packages/goal/goal-round-driver/README.md`, `packages/todo/tool-todo/README.md`,
  `docs/subsystems/goal.md`, `docs/persistence-catalog.md`.
- [learn-claude-code · s12_task_system](https://github.com/shareAI-lab/learn-claude-code): section の枠組み。
