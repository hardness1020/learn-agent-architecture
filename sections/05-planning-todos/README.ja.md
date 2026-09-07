# 5 · Planning & todos

[English](README.md) · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · **日本語** · [한국어](README.ko.md)

> 複数ステップの作業に入る前に、plan を保存します。

大きなタスクには目に見える plan が必要です。plan を prompt の中だけに置いておくと、tool result が積み重なるうちにモデルが見失うことがあります。

planning は、別々の 2 つの問題を解きます。

1. agent は作業中、最新のチェックリストを必要とします。
2. agent は、タスクを理解する前にファイルを編集すべきではありません。

このセクションではその両方を追加します。todo tool と plan mode です。todo tool はチェックリストを保存します。plan mode は、書かれた plan が承認されるまで読み取り専用の調査だけを許可します。

このレイヤがなくても、短いタスクは動きます。長いタスクでは、手順が飛んだり、早すぎる行動が起きたりします。

---

## 仕組み

![Mechanism diagram](assets/05-planning-and-todos.png)

tool は 2 つあります。どちらもモデルが呼び出す通常の tool です。どちらも中心の loop を変えません。

**todo リスト。** モデルは構造化されたチェックリストを上書きします。この tool はファイル操作も shell 操作も行いません。session の plan の状態を保存するだけです。

**plan mode。** session が読み取り専用のモードに入ります。モデルは調査を行い、plan を書き、`ExitPlanMode` を呼び出します。この退出は permission のレイヤがゲートします。

### 本節の追加: todo と plan mode の tool

```python
@dataclass
class Session:                                   # src/loop.py: mutable, outlives a turn
    mode: str = DEFAULT
    todos: list = field(default_factory=list)

def todo_tool(session):                          # src/planning.py
    def write(a): session.todos = list(a["todos"])    # model overwrites its checklist
    return Tool("TodoWrite", write, is_read_only=True)    # no side effect, never gated

def exit_plan_mode_tool(session):                # src/planning.py
    def exit_plan(_): session.mode = ACCEPT_EDITS     # approval flips the live mode
    return Tool("ExitPlanMode", exit_plan)
```

- `Session` は `mode` と `todos` を保存するようになりました。
- `TodoWrite` は `session.todos` だけを書き換えるので、外から見れば読み取り専用です。
- `ExitPlanMode` は承認の後に `session.mode` を変更します。
- 次の tool call は、同じ permission のゲートを通して新しい mode を読みます。

### 既存の構成への組み込み

セクション 3 の permission のロジックは、すでに `PLAN` を知っています。

```python
if mode == PLAN:                              # exploring, not acting yet
    if tool.is_read_only:           return "allow"
    if tool.name == "ExitPlanMode": return "ask"     # the approval handshake
    return "deny"                             # no edits until the plan is approved
```

セクション 5 が追加するのは tool と session の状態です。新しい loop も新しい permission の経路も追加しません。

todo の項目は `{ content, status, activeForm }` です。

status は `pending`、`in_progress`、`completed` のいずれかです。モデルは毎回リスト全体を書き、harness が現在の状態を表示します。

---

## システム別

各 agent が plan をどう追跡し、実行をどうゲートするか。

| | Claude Code | deepseek-harness |
| --- | --- | --- |
| **利点** | 単純で安価。メモリ上の todo リストは依存もロックも不要。 | plan と todo の状態が、再起動・fork・compaction をまたいで残る。 |
| **欠点** | session の状態のみ。turn を越えて続く作業には task グラフが必要 (セクション 12)。 | plan mode は何も止めない。編集を止めるのは sandbox か承認ポリシーだけ。 |
| **理由** | prompt の中だけに置いた plan は失われる。plan の承認前に編集はさせない。 | session ログが真実なので、plan の状態もイベントの 1 つ。 |
| **方法: plan artifact** | todo リストと plan ファイル。`TodoWrite` がリストを上書きし、ゲートは通らない。 | `todo_write` がリスト全体をイベントとして追記。再生で復元。 |
| **方法: plan mode** | あり。入ると permission の mode が plan に切り替わる。session は読み取り専用のまま。 | ログに記録されるフラグと prompt 中の案内文のみ。permission は変わらない。 |
| **方法: 実行ゲート** | `ExitPlanMode` が承認を求める。plan mode の外での呼び出しは拒否される。 | planning 中は何もなし。却下された plan は tool のフィードバックとして返る。 |

---

## 失敗モード

- **古いリスト。** モデルが todos の更新をやめる。1 項目を `in_progress` に保ち、作業が終わった項目は閉じるよう促す。
- **小さな作業への過剰な planning。** 1 手順のタスクに todo リストを作ると雑音になる。ささいなタスクでは省く。
- **plan mode から出られない。** 承認ダイアログを出せないインターフェースもある。そこでは入口と出口をまとめて無効にする。
- **入っていないのに出る。** モデルが文脈外で `ExitPlanMode` を呼ぶことがある。現在の mode が `plan` かどうかを検証する。
- **context とともに plan が消える。** フラットな todo リストは session の状態。turn やプロセスを越えて残す必要がある作業には task システムを使う。

---

## 実行

[`src/`](src/) は 04 を引き継ぎ、次を追加します。

- [`planning.py`](src/planning.py): `TodoWrite` と `ExitPlanMode`。
- [`loop.py`](src/loop.py): 実行の途中で mode を変えられるよう `Session` を保持。
- [`test.py`](src/test.py): todo の書き込み、plan mode での拒否、承認、編集の実行を検査。

```bash
python sections/05-planning-todos/src/test.py         # offline checks, no key
uv run python sections/05-planning-todos/src/demo.py  # live demo, needs a key
```

---

## 出典

- [Claude Code source](https://github.com/yasasbanukaofficial/claude-code):
  `tools/TodoWriteTool/TodoWriteTool.ts`, `tools/EnterPlanModeTool/EnterPlanModeTool.ts`, `tools/ExitPlanModeTool/ExitPlanModeV2Tool.ts`.
- [Claude Code planning helpers](https://github.com/yasasbanukaofficial/claude-code): `utils/plans.ts`, `utils/todo/types.ts`, `types/permissions.ts`.
- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness) at `dsh-v0.1.0-rc.7`:
  `packages/todo/tool-todo/src/index.ts`, `packages/plan/plan-mode/src/index.ts`, `docs/subsystems/plan.md`, `docs/tool-catalog.md`.
- [learn-claude-code · s05_todo_write](https://github.com/shareAI-lab/learn-claude-code): section framing.
