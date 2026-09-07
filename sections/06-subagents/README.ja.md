# 6 · Subagents

[English](README.md) · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · **日本語** · [한국어](README.ko.md)

> 目的を絞った child loop を走らせ、その結果だけを返します。

メインの agent は subagent に作業を渡せます。渡す側が親、送り出される側が子です。

親から見れば、これは tool call が 1 回あるだけです。しかしその呼び出しの中では、完全な agent の loop が走っています。
親は子に prompt を渡します。子は新しい `messages[]` を受け取り、最後まで走り、最終的な答えを返します。

こうすると、脇道の調査が親の context に入りません。親は、子が読んだファイルや実行したコマンドの結果を全部知る必要はありません。ふつう必要なのは結論だけです。

subagent がないと、調査はすべてメインの transcript に残ります。長い実行は雑音が増え、費用がかさみ、モデルにとって追いにくくなります。

---

## 仕組み

![Mechanism diagram](assets/06-subagents.png)

`Agent` tool が child agent を起動します。子は自分の session と message のリストを持ちます。走らせる loop は親と同じです。

戻ってくるのは子の最終テキストだけです。子の transcript は捨てられます。ファイルの書き込みや shell の副作用は、作業ディレクトリにそのまま残ります。

### 本節の追加: Agent tool

```python
def agent_tool(model, child_registry, parent_session):     # src/subagents.py
    def spawn(a):
        child = Session(mode=parent_session.mode,          # fresh context, inherited authority
                        allow_rules=set(parent_session.allow_rules))
        messages = [{"role": "user", "content": a["description"]}]   # the child's own conversation
        return run_turn(messages, model, child_registry, child)      # the loop, run again
    return Tool("Agent", spawn, is_read_only=True)
```

- `agent_tool` は通常の tool を返します。
- そのハンドラは、新しい `Session` を渡して `run_turn()` を呼びます。
- 子の `messages[]` は、子の prompt だけから始まります。
- 子は `run_turn()` が返したテキストを返します。

### 既存の構成への組み込み

loop は変わりません。subagent は、loop を呼ぶだけのもう 1 つの tool ハンドラです。

重要な性質が 3 つあります。

- **新しい context。** 子は親の transcript を引き継ぎません。親は子の実行の記録を引き継ぎません。
- **引き継がれる権限。** 子は親の permission の mode と allow ルールをコピーします。context の分離は permission の分離ではありません。
- **再帰の制限。** デモでは child registry から `Agent` を外しているので、子はさらに子を生成できません。

---

## システム別

各 agent が部分問題をどう切り離し、結果をどう返すか。

| | Claude Code | deepseek-harness |
| --- | --- | --- |
| **利点** | 子の context が親の集中を保ち、メインの transcript をきれいに保つ。 | 1 つの接続点で、プロセス内の子・外部ランタイム・製品の CLI をまとめて扱える。 |
| **欠点** | 親は子がそこに至った経緯を失う。要約が薄いと聞き直しになる。 | 1 つの tool で足りるところに、6 つのバックエンドと再開の管理を抱えている。 |
| **理由** | 親に必要なのは結論であって、子が読んだファイルのすべてではない。 | 委任は転送方式の選択なので、各バックエンドを名前で登録する。 |
| **方法: 生成の基本操作** | `Agent` tool。subagent の型が組み込みのペルソナを選ぶ。 | 登録済みバックエンドごとに tool が 1 つ。新しい子、fork、外部ランタイム、CLI。 |
| **方法: context の分離** | 子の messages は新規。fork した子はさらに fork できない。 | 新しい子は空から始まる。fork は親の完了した turn だけをコピーする。 |
| **方法: 結果の返却** | 子の最後の message のテキストが返る。transcript は破棄される。 | 最後の assistant message と、スキーマ検査を通した任意の出力。 |
| **方法: 再開** | ほとんどの agent は再開できる。親が追加の message を送る。 | 永続化された子は追加依頼をキューに入れ、再起動後はログから読み直す。 |

---

## 失敗モード

- **要約による欠落。** 子が圧縮しすぎることがある。重要な発見はディスクに書くよう指示する。
- **暴走する再帰。** 子が子を生成し続けると際限なく増える。child registry から `Agent` tool を外すか、深さの上限を設ける。
- **子が止まらない。** 子は親と同じ停止のリスクを持つ。子ごとに turn か token の上限を与える。
- **permission が分離されている前提。** 子にも通常の permission のゲートが必要。context が別だからといって省いてはいけない。
- **放置される非同期の子。** バックグラウンドの子は、親が先へ進んだ後に終わることがある。task の記録で追跡する。

---

## 実行

[`src/`](src/) は 05 を引き継ぎ、次を追加します。

- [`subagents.py`](src/subagents.py): `Agent` tool。
- [`loop.py`](src/loop.py): セクション 5 から変更なし。
- [`demo.py`](src/demo.py): 親が数え上げを子に委任する。
- [`test.py`](src/test.py): 新しい context、権限の引き継ぎ、再帰の遮断を検査。

```bash
python sections/06-subagents/src/test.py         # offline checks, no key
uv run python sections/06-subagents/src/demo.py  # live demo, needs a key
```

---

## 出典

- [Claude Code source](https://github.com/yasasbanukaofficial/claude-code):
  `tools/AgentTool/AgentTool.tsx`, `runAgent.ts`, `resumeAgent.ts`, `forkSubagent.ts`, `builtInAgents.ts`, `tasks/LocalAgentTask/`.
- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness) at `dsh-v0.1.0-rc.7`:
  `packages/subagent/subagent/src/index.ts`, `src/continuation.ts`, `packages/subagent/subagent-fork-in-process/README.md`,
  `packages/subagent/subagent-acp/README.md`, `docs/subsystems/subagent.md`, `docs/tool-catalog.md`.
- [learn-claude-code · s06_subagent](https://github.com/shareAI-lab/learn-claude-code): section framing.
