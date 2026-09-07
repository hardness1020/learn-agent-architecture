# 4 · Hooks

[English](README.md) · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · **日本語** · [한국어](README.ko.md)

> Hooks ループの周りの固定点に動作を追加します。

Hooks はユーザー構成のコールバックです。これらは、ツール呼び出しの前、ツール呼び出しの後、プロンプトの送信時、またはセッションの開始時または停止時に実行できます。

hooks は、ロギング、検証、通知、および小規模なポリシー チェックに使用します。 hooks を使用しない場合、新しい動作ごとにループの編集またはフォークが必要になります。

Hooks ループを小さく保ちます。ループは固定イベントを公開します。拡張機能はそれらのイベントに付加されます。

---

## メカニズム

![Mechanism diagram](assets/04-hooks.png)

`Hooks` オブジェクトは、イベント名をコールバック リストにマップします。ループはカスタム チェックを直接呼び出しません。代わりに、`_dispatch` は名前付きイベントを発生させます。

ツールの実行には、次の 2 つの重要なポイントがあります。

- `PreToolUse` は許可ゲートの前で実行されます。呼び出しをブロックしたり、入力を書き換えたりする可能性があります。
- `PostToolUse` は、ツール呼び出しが成功した後に実行されます。結果を観察することができます。

### 新しい: hooks

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

- `on(event, fn)` はコールバックを登録します。
- `fire_pre` は、`PreToolUse` コールバックを実行します。
- プリフックは `{"deny": True}` をブロックに返すことができます。
- プリフックは `{"updated_args": ...}` を返して入力を書き換えることができます。
- `fire_post` は実行後にオブザーバーを実行します。

### 統合方法

2 つの呼び出しが `_dispatch` に追加されます。

```python
# src/loop.py _dispatch
blocked, args, msg = hooks.fire_pre(name, args)          # 4 · PreToolUse
if blocked: return res(msg)
decision = permissions.decide(tool, mode, allow_rules)   # 3 · gate (section 3)
...                                                      # deny / ask short-circuit
out = res(run_tool(tool, args))                          # 2 · execute -> tool_result
hooks.fire_post(name, args, out)                         # 4 · PostToolUse
```

- ブロックまたは拒否された通話は、`run_tool` に到達しません。
- `PostToolUse` は、実行が成功した後にのみ実行されます。
- Hooks は許可結果を厳しくすることはできますが、緩めるべきではありません。
- Claude Code では、`resolveHookPermissionDecision` は、ルールベースの権限を使用して hook 出力を調整します。

デモでは、`PreToolUse` hook を使用して、`bypassPermissions` の下でも `rm -rf` をブロックします。

このセクションでは、ライフサイクル hooks について説明します。 `hooks/` フォルダー内の React render hooks は、同じ単語を共有する無関係な UI コードです。

### コントラスト: 滝 hooks

Claude Code では、hook は外部コマンドです。 harness はサブプロセスとして実行し、その終了コードと出力を読み取ります。
deepseek-harness では、hook は、harness プロセス内で実行されるプレーン関数です。
ツール呼び出しの前に起動されるイベントなど、名前付きイベントに登録されます。
そして、終了コードの代わりに、型指定された決定、つまり拒否、質問、許可などの単純な値を返します。

1 つのイベントに複数の hooks を登録できます。これらはチェーンを形成し、イベントは最初のイベントのみを起動します。
各 hook は、イベント データと `next()` コールバックを受信し、次の 2 つの動きのいずれかを選択します。

- `next()` を呼び出さずに決定を返します。ここでチェーンが止まります。 Hooks さらに下は決して実行されません。
- `next()` に電話します。チェーンの残りの部分が決定し、この hook はその結果をそのまま、または調整されて返します。

dsh では、このディスパッチ スタイルをウォーターフォールと呼びます。既存の Claude Code シェル hooks は引き続き機能します。ブリッジによって実行されます。
そして、その出力を同じ型の決定に変換します。複数のシェル hooks が同時に応答すると、
ブリッジは最も厳密な答えを保持します: ビートを拒否する、尋ねる、ビートを許可するを尋ねる。

[`src/waterfall.py`](src/waterfall.py) はこれを取り除いたものです。これはコントラスト デモであり、`_dispatch` に接続されていないため、後のセクションで同じループを進めます。

### さらに読む

これは `src/` にはありません。これは ai-agent-book からのものであり、表内のシステムについては確認されていません。

例は書き込み時のリントです。書き込みまたは編集ツールが戻ります。次に、hook は、変更されたファイルに対してリンターを実行します。
診断を tool result に追加します。モデルは次のターンで、書き込み確認の次にエラーを読み取ります。
hook がない場合、そのエラーは次のビルドまたはテストの実行を待ちます。

このパターンが安価なままである理由は 2 つあります。

- 診断は tool result 内に戻るため、追加の操作は必要ありません。
- チェックはプロジェクト全体ではなく 1 つのファイルを対象とするため、書き込みと同じくらい時間がかかります。

パターンには 1 つの制限があります。ブロックされた書き込みは決して実行されないため、hook は診断を生成しません。

---

## システムごと

各エージェントがループの周囲でインターセプト ポイントを公開する方法。

| | Claude Code | deepseek-harness |
| --- | --- | --- |
| **長所** |ユーザーはループを編集せずに、ログ記録、検証、通知、ポリシー チェックなどの動作を拡張します。 | Hooks は処理中です plugins。既存のシェル hooks は引き続き実行されます。 |
| **短所** |固定イベントリストが限界です。 hook は、イベントが存在する場所のみをインターセプトします。 |学習すべき 2 つの hook スタイル。ブリッジはサブセットをカバーしているため、入力を書き換えることはできません。 |
| **理由** |ループを小さく保ちます。新しい動作は、フォークではなく固定イベントに付加されます。 |拡張サーフェスは、harness 自体が実行されるイベント システムです。 |
| **方法: hook イベント** |ツール、プロンプト、セッション、停止、subagent、コンパクト、セットアップにわたる 27 のライフサイクル イベント。 |フェーズごとのウォーターフォールおよびシリアル イベント。ブリッジはシェル hooks を接続します。 |
| **方法: 点火** |設定からロードされ、起動時にフリーズします。 `PreToolUse` は許可ゲートの前で発火します。 |実行前ウォーターフォール内、拒否専用ガードの前。 |
| **方法: ブロックまたは変更できますか?** |はい。拒否、質問、入力の更新、コンテキストの追加、または停止。ルールと調和した。 |はい、入力された決定を介して可能です。シェル hooks 拒否 > 要求 > 許可を折ります。 |

---

## 障害モード

- **Hook は権限をバイパスします。** hook は、拒否されたアクションを許可しようとする可能性があります。ルールベースのアクセス許可に対して hook 出力を解決します。
- **hook ループを永久に停止します。** `Stop` hook はブロックし、自己修正をトリガーし、再度起動できます。ストップ hook がすでにアクティブであることを追跡します。
- **Hook 構成はセッション中に変更されます。** プロセスは起動後に設定を編集する場合があります。 hook 構成のスナップショットを 1 回作成します。
- **遅い hook はループを停止させます。** hook は、動作を遅くする可能性があります。タイムアウトを追加します。
- **PostToolUse が予期せず停止します。** ポストフックが `preventContinuation` を返した場合、クラッシュではなく正常な停止として表示されます。
- **診断により結果が大量に表示されます。** プロジェクト全体に対して lint を実行すると、書き込み自体よりも多くのテキストが返される可能性があります。変更されたファイルのみをチェックし、追加されるファイルに上限を設けます。

---

## 実行可能

[`src/`](src/) 03 を前方に繰り上げて次を追加します。

- [`hooks.py`](src/hooks.py): `fire_pre` および `fire_post` を含む `Hooks` オブジェクト。
- [`loop.py`](src/loop.py): `_dispatch` はゲート前に `PreToolUse` を発射し、実行後に `PostToolUse` を発射します。
- [`waterfall.py`](src/waterfall.py): deepseek-harness のコントラスト: `next()` 委任を含む hook チェーンに、最も厳密な優先マージ (拒否 > 要求 > 許可) を加えたもの。
- [`test.py`](src/test.py): 事前フックは、`bypassPermissions` の下でも `rm -rf` をブロックします。ウォーターフォール チェックでは、決定、委任、マージがカバーされます。

```bash
python sections/04-hooks/src/test.py         # offline checks, no key
uv run python sections/04-hooks/src/demo.py  # live demo, needs a key
```

---

## ソース

- [Claude Code ソース](https://github.com/yasasbanukaofficial/claude-code):
  `types/hooks.ts`、`entrypoints/sdk/coreTypes.ts`、`services/tools/toolHooks.ts`、`query/stopHooks.ts`、`services/tools/toolExecution.ts`、`setup.ts`。
- [deepseek-harness ソース](https://github.com/deepseek-ai/deepseek-harness) `dsh-v0.1.0-rc.7`:
  `packages/hooks/README.md`、`packages/hooks/hooks-claude-code/README.md`、`packages/hooks/hook-protocol/README.md`、
  `docs/cordis-primer.md`、`docs/subsystems/core.md`。
- [learn-claude-code · s04_hooks](https://github.com/shareAI-lab/learn-claude-code): セクション フレーム。
- [ai-agent-book · Chapter 5](https://github.com/bojieli/ai-agent-book/blob/main/book/chapter5.md) (《深入理解 AI Agent》、李博杰、中国語の原文は正規版です):
  lint on write: ツール層は書き込み後にリンターを実行し、診断を tool result に追加します。
