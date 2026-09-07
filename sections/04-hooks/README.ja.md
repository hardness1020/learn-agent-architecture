# 4 · Hooks

[English](README.md) · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · **日本語** · [한국어](README.ko.md)

> hook は loop の周りの決まった地点に挙動を足します。

hook はユーザーが設定するコールバックです。tool 呼び出しの前、tool 呼び出しの後、prompt が送信されたとき、session が始まるか終わるときに走れます。

hook はログ記録、検証、通知、小さな方針の検査に使ってください。hook がなければ、新しい挙動のたびに loop を編集するか fork する必要が出ます。

hook は loop を小さく保ちます。loop は決まったイベントを公開します。拡張はそのイベントに取り付きます。

---

## 仕組み

![Mechanism diagram](assets/04-hooks.png)

`Hooks` オブジェクトは、イベント名をコールバックのリストに対応づけます。loop はカスタムの検査を直接呼びません。代わりに `_dispatch` が名前付きのイベントを発火します。

tool の実行については、重要な地点が 2 つあります。

- `PreToolUse` は permission のゲートより前に走ります。呼び出しを止めることも、入力を書き換えることもできます。
- `PostToolUse` は tool 呼び出しが成功したあとに走ります。結果を観測できます。

### 新規: hook

```python
class Hooks:                                     # src/hooks.py
    def fire_pre(self, name, args):               # PreToolUse: block or rewrite
        for fn in self._hooks["PreToolUse"]:
            out = fn(name, args) or {}
            if out.get("updated_args"): args = out["updated_args"]
            if out.get("deny"):         return True, args, out.get("message", "")
        return False, args, ""
    def fire_post(self, name, args, result):      # PostToolUse: observe
        for fn in self._hooks["PostToolUse"]: fn(name, args, result)
```

- `on(event, fn)` がコールバックを登録します。
- `fire_pre` が `PreToolUse` のコールバックを走らせます。
- pre-hook は `{"deny": True}` を返して呼び出しを止められます。
- pre-hook は `{"updated_args": ...}` を返して入力を書き換えられます。
- `fire_post` は、実行後に観測側を走らせます。

### 組み込み方

`_dispatch` に呼び出しが 2 つ足されます。

```python
# src/loop.py _dispatch
blocked, args, msg = hooks.fire_pre(name, args)          # 4 · PreToolUse
if blocked: return res(msg)
decision = permissions.decide(tool, mode, allow_rules)   # 3 · gate (section 3)
...                                                      # deny / ask short-circuit
out = res(run_tool(tool, args))                          # 2 · execute -> tool_result
hooks.fire_post(name, args, out)                         # 4 · PostToolUse
```

- 止められた呼び出しも拒否された呼び出しも、`run_tool` に決して届きません。
- `PostToolUse` は実行が成功したあとにだけ走ります。
- hook は permission の結果を厳しくできますが、緩めるべきではありません。
- Claude Code では、`resolveHookPermissionDecision` が hook の出力と規則に基づく permission を突き合わせます。

デモでは `PreToolUse` hook を使い、`bypassPermissions` の下でも `rm -rf` を止めます。

このセクションが扱うのはライフサイクルの hook です。`hooks/` フォルダにある React のレンダー hook は、同じ語を共有しているだけの無関係な UI コードです。

### 対比: waterfall 型の hook

Claude Code では、hook は外部コマンドです。harness がそれをサブプロセスとして実行し、終了コードと出力を読みます。
deepseek-harness では、hook は harness のプロセス内で走る普通の関数です。
名前付きのイベント、たとえば tool 呼び出しの前に発火するイベントに登録します。
そして終了コードの代わりに、型付きの判断を返します。deny、ask、allow といった素の値です。

1 つのイベントに複数の hook を登録できます。それらは連鎖を作り、イベントが発火するのは先頭の 1 つだけです。
各 hook はイベントのデータと `next()` コールバックを受け取り、2 つの動きのどちらかを選びます。

- `next()` を呼ばずに判断を返します。連鎖はそこで止まります。後ろの hook は走りません。
- `next()` を呼びます。連鎖の残りが判断し、この hook はその結果を、そのままか調整して返します。

dsh はこの dispatch のやり方を waterfall と呼びます。既存の Claude Code の shell hook もそのまま動きます。ブリッジがそれらを実行し、
出力を同じ型付きの判断に変換します。複数の shell hook が同時に答えた場合、
ブリッジは最も厳しい答えを残します。deny が ask に勝ち、ask が allow に勝ちます。

[`src/waterfall.py`](src/waterfall.py) はこれを削ぎ落としたものです。対比のためのデモであって `_dispatch` には組み込まれていないので、後続のセクションは同じ loop をそのまま引き継ぎます。

### さらに読む

ここから先は `src/` にはありません。ai-agent-book から来ていて、表に挙げたシステムで確認が取れているわけではありません。

例は書き込み時の lint です。書き込みか編集の tool が返ります。すると hook が、変更されたファイルに linter を走らせます。
そして診断結果を tool の結果に足します。モデルは次の turn で、書き込みの確認と並べてそのエラーを読みます。
hook がなければ、そのエラーは次のビルドかテスト実行まで待たされます。

このやり方が安上がりなのには 2 つ理由があります。

- 診断結果は tool の結果の中に戻るので、余分な turn が要りません。
- 検査はプロジェクト全体ではなく 1 ファイルだけなので、書き込みと同じくらいの時間で済みます。

このやり方には限界が 1 つあります。止められた書き込みは走らないので、hook は診断結果を出しません。

---

## システム別

各 agent が loop の周りの横取り地点をどう公開するかです。

| | Claude Code | deepseek-harness |
| --- | --- | --- |
| **利点** | loop を編集せずにユーザーが挙動を拡張可能。ログ記録、検証、通知、方針の検査。 | hook はプロセス内の plugin。既存の shell hook もそのまま動作。 |
| **欠点** | 決まったイベント一覧が限界。hook が横取りできるのはイベントがある場所だけ。 | hook の型が 2 つあり学習が必要。ブリッジが賄うのは一部のみで、入力の書き換えは不可。 |
| **理由** | loop を小さく保つため。新しい挙動は fork ではなく、決まったイベントに接続。 | 拡張の面が、harness 自身が動いているイベントシステムそのもの。 |
| **方法: hook events** | tool、prompt、session、stop、subagent、compact、setup にまたがる 27 のライフサイクルイベント。 | 段階ごとの waterfall イベントと直列イベント。ブリッジが shell hook を接続。 |
| **方法: fire point** | 設定から読み込み、起動時に固定。`PreToolUse` は permission のゲートより前に発火。 | 実行前の waterfall の中、拒否だけを出すガードより前。 |
| **方法: can block or modify?** | 可能。拒否、確認、入力の更新、context の追加、停止。規則との突き合わせあり。 | 可能。型付きの判断による。shell hook は deny > ask > allow で畳み込み。 |

---

## 失敗モード

- **hook が permission を迂回する。** hook が拒否済みの行動を許可しようとしえます。hook の出力は、規則に基づく permission と突き合わせて解決してください。
- **Stop hook が無限に回る。** `Stop` hook は停止を止め、自己修正を起こし、また発火しえます。stop hook がすでに動いていることを記録しておいてください。
- **session の途中で hook の設定が変わる。** 起動後に設定を編集するプロセスがありえます。hook の設定は一度スナップショットを取ってください。
- **遅い hook が loop を止める。** hook は遅い処理を外部プロセスに投げえます。タイムアウトを足してください。
- **PostToolUse が予期せず止める。** post-hook が `preventContinuation` を返したら、クラッシュではなく穏やかな停止として見せてください。
- **診断結果が結果を埋める。** プロジェクト全体の lint 実行は、書き込み本体より多くのテキストを返しえます。変更されたファイルだけを検査し、追記する量に上限を設けてください。

---

## 実行

[`src/`](src/) は 03 を引き継ぎ、次を足します。

- [`hooks.py`](src/hooks.py): `fire_pre` と `fire_post` を持つ `Hooks` オブジェクト。
- [`loop.py`](src/loop.py): `_dispatch` がゲートの前に `PreToolUse` を、実行後に `PostToolUse` を発火します。
- [`waterfall.py`](src/waterfall.py): deepseek-harness との対比。`next()` による委譲を伴う hook の連鎖と、最も厳しいものが勝つ統合 (deny > ask > allow)。
- [`test.py`](src/test.py): pre-hook が `bypassPermissions` の下でも `rm -rf` を止めます。waterfall の検査は、判断、委譲、統合を押さえます。

```bash
python sections/04-hooks/src/test.py         # offline checks, no key
uv run python sections/04-hooks/src/demo.py  # live demo, needs a key
```

---

## 出典

- [Claude Code source](https://github.com/yasasbanukaofficial/claude-code):
  `types/hooks.ts`, `entrypoints/sdk/coreTypes.ts`, `services/tools/toolHooks.ts`, `query/stopHooks.ts`, `services/tools/toolExecution.ts`, `setup.ts`。
- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness) の `dsh-v0.1.0-rc.7`:
  `packages/hooks/README.md`, `packages/hooks/hooks-claude-code/README.md`, `packages/hooks/hook-protocol/README.md`,
  `docs/cordis-primer.md`, `docs/subsystems/core.md`。
- [learn-claude-code · s04_hooks](https://github.com/shareAI-lab/learn-claude-code): セクションの組み立て方。
- [ai-agent-book · chapter 5](https://github.com/bojieli/ai-agent-book/blob/main/book/chapter5.md) (『深入理解 AI Agent』, 李博杰。中国語の原文が正典):
  書き込み時の lint。tool の層が書き込みのあとに linter を走らせ、診断結果を tool の結果に足します。
