# 5 · Planning & todos

[English](README.md) · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · **日本語** · [한국어](README.ko.md)

> 複数のステップの作業を行う前に計画を保存します。

大規模なタスクには、目に見える計画が必要です。モデルがプロンプト内の計画のみを保持している場合、多くのツールの結果の後で追跡できなくなる可能性があります。

計画を立てることで、次の 2 つの別々の問題が解決されます。

1. エージェントが動作する間、最新のチェックリストが必要です。
2. エージェントはタスクを理解する前にファイルを編集してはなりません。

このセクションでは、ToDo ツールと計画モードの両方を追加します。 Todo ツールはチェックリストを保存します。計画モードでは、書面による計画が承認されるまで読み取り専用の探索が可能です。

このレイヤーがなくても、短いタスクは引き続き機能します。タスクが長い場合、手順がスキップされたり、アクションが早すぎたりする可能性があります。

---

## メカニズム

![機構図](assets/05-planning-and-todos.png)

ツールは 2 つあります。どちらも通常のモデルと呼ばれるツールです。どちらもコアループを変更しません。

**Todo リスト。** モデルは構造化されたチェックリストを上書きします。このツールはファイルやシェルの作業を行いません。セッションの計画状態のみが保存されます。

**計画モード。** セッションは読み取り専用モードに入ります。モデルは探索し、計画を作成し、`ExitPlanMode` を呼び出します。その出口は許可レイヤーによってゲートされます。

### 新機能: Todo およびプランモード ツール

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

- `Session` は、`mode` および `todos` を保存するようになりました。
- `TodoWrite` は `session.todos` のみを変更するため、外部からは読み取り専用になります。
- `ExitPlanMode` は、承認後に `session.mode` に変更されます。
- 次のツール呼び出しでは、同じ許可ゲートを通じて新しいモードが読み取られます。

### 統合方法

セクション 3 の許可ロジックは、すでに `PLAN` について認識しています。

```python
if mode == PLAN:                              # exploring, not acting yet
    if tool.is_read_only:           return "allow"
    if tool.name == "ExitPlanMode": return "ask"     # the approval handshake
    return "deny"                             # no edits until the plan is approved
```

セクション 5 では、ツールとセッション状態を追加します。新しいループや新しい権限パスは追加されません。

Todo アイテムは `{ content, status, activeForm }` です。

ステータスは、`pending`、`in_progress`、または `completed` です。モデルは毎回リスト全体を書き込み、harness が現在の状態をレンダリングします。

---

## システムごと

各エージェントが計画を追跡し、実行を制御する方法。

| | Claude Code | deepseek-harness |
| --- | --- | --- |
| **長所** |シンプルで安い。メモリ内の ToDo リストには依存関係やロックは必要ありません。 | Plan と todo の状態は、再起動、フォーク、および圧縮後に存続します。 |
| **短所** |セッション状態のみ。ターンを超えて存続する作業にはタスク グラフが必要です (セクション 12)。 |プラン モードでは何もブロックされません。編集を停止できるのは、sandbox または承認ポリシーのみです。 |
| **理由** |プロンプト内にのみ保持されている計画は失われます。計画が承認されるまでは編集できません。 |セッション ログは真実であるため、計画の状態はもう 1 つのイベントです。 |
| **方法: 成果物を計画する** | ToDo リストと計画ファイル。 `TodoWrite` はリストを上書きし、ゲートされることはありません。 | `todo_write` はリスト全体をイベントとして追加します。リプレイはそれを再構築します。 |
| **方法: 計画モード** |はい。を入力すると、権限モードが計画に切り替わります。セッションは読み取り専用のままです。 |ログに記録されたフラグとプロンプト内のガイダンス テキスト。権限の変更はありません。 |
| **方法: 処刑ゲート** | `ExitPlanMode` は承認を求めています。通話はプラン モード以外では拒否されます。 |計画中は何もありません。拒否された計画はツールのフィードバックとして返されます。 |

---

## 障害モード

- **古いリスト。** モデルは Todo の更新を停止します。 1 つの項目 `in_progress` を保持し、作業が完了したら項目を閉じるように通知します。
- **細かい作業を過剰に計画する。** ワンステップ タスクの ToDo リストはノイズを追加します。些細なタスクの場合はスキップしてください。
- **計画モードを終了できません。** 一部のサーフェスでは承認ダイアログを表示できません。これらのサーフェスで同時に出入りすることを無効にします。
- **入力せずに終了します。** モデルはコンテキスト外で `ExitPlanMode` を呼び出す可能性があります。現在のモードが `plan` であることを確認します。
- **計画はコンテキストとともに消えます。** フラットな Todo リストはセッション状態です。作業がターンまたはプロセスを終了しなければならない場合は、タスク システムを使用します。

---

## 実行可能

[`src/`](src/) 04 を前方に繰り上げて次を追加します。

- [`planning.py`](src/planning.py): `TodoWrite` および `ExitPlanMode`。
- [`loop.py`](src/loop.py): 実行中にモードを変更できるように、`Session` を保持します。
- [`test.py`](src/test.py): Todo の書き込み、プランモードの拒否、承認、および編集の実行をチェックします。

```bash
python sections/05-planning-todos/src/test.py         # offline checks, no key
uv run python sections/05-planning-todos/src/demo.py  # live demo, needs a key
```

---

## ソース

- [Claude Code ソース](https://github.com/yasasbanukaofficial/claude-code):
  `tools/TodoWriteTool/TodoWriteTool.ts`、`tools/EnterPlanModeTool/EnterPlanModeTool.ts`、`tools/ExitPlanModeTool/ExitPlanModeV2Tool.ts`。
- [Claude Code 計画ヘルパー](https://github.com/yasasbanukaofficial/claude-code): `utils/plans.ts`、`utils/todo/types.ts`、`types/permissions.ts`。
- [deepseek-harness ソース](https://github.com/deepseek-ai/deepseek-harness) `dsh-v0.1.0-rc.7`:
  `packages/todo/tool-todo/src/index.ts`、`packages/plan/plan-mode/src/index.ts`、`docs/subsystems/plan.md`、`docs/tool-catalog.md`。
- [learn-claude-code · s05_todo_write](https://github.com/shareAI-lab/learn-claude-code): セクションのフレーム化。
