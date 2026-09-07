# 6 · Subagents

[English](README.md) · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · **日本語** · [한국어](README.ko.md)

> フォーカスされた子ループを実行し、その結果のみを返します。

メインエージェントは subagent に作業を任せることができます。委任する側が親であり、送り出される側が子です。

親にとって、これは単なる 1 つのツール呼び出しです。ただし、その呼び出し内では完全な agent loop が実行されます。
親は子供に指示を与えます。子は新しい `messages[]` を取得し、最後まで実行して、最終的な応答を返します。

これにより、サイド調査が親コンテキストから外されます。親は、子からのすべてのファイル読み取りまたはコマンド結果を必要としません。通常は結論が必要です。

subagents がない場合、すべての調査は主な記録に残ります。長時間実行するとノイズが多くなり、コストが高くつき、モデルの追跡が困難になります。

---

## メカニズム

![機構図](assets/06-subagents.png)

`Agent` ツールは子エージェントを開始します。子には独自のセッションとメッセージ リストがあります。親と同じループを実行します。

子供の最後のテキストだけが返されます。そのトランスクリプトは破棄されます。ファイルの書き込みとシェルの副作用は作業ディレクトリでも引き続き発生します。

### 新機能: エージェント ツール

```python
def agent_tool(model, child_registry, parent_session):     # src/subagents.py
    def spawn(a):
        child = Session(mode=parent_session.mode,          # fresh context, inherited authority
                        allow_rules=set(parent_session.allow_rules))
        messages = [{"role": "user", "content": a["description"]}]   # the child's own conversation
        return run_turn(messages, model, child_registry, child)      # the loop, run again
    return Tool("Agent", spawn, is_read_only=True)
```

- `agent_tool` は通常のツールを返します。
- そのハンドラーは、新しい `Session` を使用して `run_turn()` を呼び出します。
- 子の `messages[]` は、子のプロンプトのみで開始されます。
- 子は、`run_turn()` が返すテキストを返します。

### 統合方法

ループは変わりません。 subagent は、ループを呼び出す別のツール ハンドラーです。

次の 3 つのプロパティが重要です。

- **最新のコンテキスト。** 子は親のトランスクリプトを継承しません。親は子のトレースを継承しません。
- **継承された権限。** 子は親のアクセス許可モードと許可ルールをコピーします。コンテキストの分離は権限の分離ではありません。
- **再帰制限。** デモでは子レジストリから `Agent` が省略されているため、子は別の子を生成できません。

---

## システムごと

各エージェントが部分問題を切り分けて結果を返す方法。

| | Claude Code | deepseek-harness |
| --- | --- | --- |
| **長所** |子コンテキストにより、親に焦点が当てられ、メインのトランスクリプトがクリーンな状態に保たれます。 | 1 つのシームは、プロセス内の子、外部ランタイム、および製品 CLI にまたがります。 |
| **短所** |親は子供がどうやってそこにたどり着いたのかを失います。薄い要約は、もう一度尋ねることを意味します。 | 6 つのバックエンドと履歴書マネージャー。1 つのツールで十分です。 |
| **理由** |親は、子供が読んだすべてのファイルではなく、結論を必要とします。 |委任はトランスポートの選択であるため、各バックエンドは名前で登録されます。 |
| **方法: プリミティブをスポーン** | `Agent` ツール。 subagent タイプは、組み込みのペルソナを選択します。 |登録されたバックエンドごとに 1 つのツール: 新しい子、フォーク、runtime または CLI。 |
| **方法: コンテキストの分離** |新鮮な子供たちへのメッセージ。フォークした子は再びフォークすることはできません。 |生まれたばかりの子供は空から始まります。フォークは親の終了したターンのみをコピーします。 |
| **方法: 結果を返す** |子供の最後のメッセージのテキストが戻ります。トランスクリプトは削除されます。 |最後のアシスタント メッセージと、スキーマに対してチェックされたオプションの出力。 |
| **方法: 再開** |ほとんどのエージェントが再開します。親はフォローアップ メッセージを送信します。 |永続的な子はフォローアップをキューに入れ、再起動後にログからリロードします。 |

---

## 障害モード

- **損失の多い概要** 子は圧縮しすぎる可能性があります。重要な結果をディスクに書き込むように依頼します。
- **暴走再帰。** 子を生成する子は際限なく成長することができます。 `Agent` ツールを子レジストリから除外するか、深さ制限を適用します。
- **子は停止しません。** 子には親と同じ停止リスクがあります。各子供に独自のターンまたはトークン制限を与えます。
- **想定される許可の隔離。** 子供には依然として通常の許可ゲートが必要です。コンテキストは別なので飛ばさないでください。
- **孤立した非同期の子。** バックグラウンドの子は、親が先に進んだ後に終了できます。タスク記録で追跡します。

---

## 実行可能

[`src/`](src/) 05 を前方に繰り上げて次を追加します。

- [`subagents.py`](src/subagents.py): `Agent` ツール。
- [`loop.py`](src/loop.py): セクション 5 から変更なし。
- [`demo.py`](src/demo.py): 親は子にカウントを委任します。
- [`test.py`](src/test.py): 新しいコンテキスト、継承された権限、および再帰フェンシングをチェックします。

```bash
python sections/06-subagents/src/test.py         # offline checks, no key
uv run python sections/06-subagents/src/demo.py  # live demo, needs a key
```

---

## ソース

- [Claude Code ソース](https://github.com/yasasbanukaofficial/claude-code):
  `tools/AgentTool/AgentTool.tsx`、`runAgent.ts`、`resumeAgent.ts`、`forkSubagent.ts`、`builtInAgents.ts`、`tasks/LocalAgentTask/`。
- [deepseek-harness ソース](https://github.com/deepseek-ai/deepseek-harness) `dsh-v0.1.0-rc.7`:
  `packages/subagent/subagent/src/index.ts`、`src/continuation.ts`、`packages/subagent/subagent-fork-in-process/README.md`、
  `packages/subagent/subagent-acp/README.md`、`docs/subsystems/subagent.md`、`docs/tool-catalog.md`。
- [learn-claude-code · s06_subagent](https://github.com/shareAI-lab/learn-claude-code): セクションのフレーム化。
