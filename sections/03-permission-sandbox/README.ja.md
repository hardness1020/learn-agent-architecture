# 3 · Permission と sandbox

[English](README.md) · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · **日本語** · [한국어](README.ko.md)

> どの行動も、システムに届く前に検査します。

モデルは有効になっているどの tool でも実行を要求できます。permission の層は、その呼び出しを走らせてよいかを決めます。

permission のない tool runtime は、無人のリモート shell とほとんど変わりません。

まずい tool 呼び出しは、ファイルを消し、秘密を漏らし、間違ったコードを push しかねません。モデルを信頼することは安全境界ではありません。実行の前に、コードが要求を検査しなければなりません。

理由は単純です。モデルは他人が書いたテキストを読みます。web ページ、issue のコメント、repo の中のファイルが、agent に向けた
指示を運びうるからです。その指示に何ができるかは、3 つの能力で決まります。agent は非公開のデータを読めます。
agent は信頼できないコンテンツを取り込みます。agent はデータを外に送れます。3 つのうち 2 つまでなら耐えられます。3 つが同時に揃うと、
注入されたテキストが agent に、秘密を開いてどこかへ投稿しろと命じられるようになります。この組み合わせを lethal trifecta と呼びます。

永続的な memory は事態を悪くします。注入された指示が memory ファイル (セクション 9) に入り込むと、次の session がそれを読み戻します。
1 回の注入が、それを運んだ session が終わったあともずっと効き続けます。

ゲートはこの 3 つの能力を取り上げることはできません。何も読まず何にも届かない agent は仕事になりません。そこでゲートは
別の 2 つをやります。3 つ揃ってしまう呼び出しの手前に判断を置きます。そして、通した呼び出しの後ろに sandbox を置きます。

permission の層は次のことをしなければなりません。

1. 各 tool 呼び出しを、実行前に調べます。
2. `allow`、`ask`、`deny` のいずれかを決めます。
3. 事前承認のない危険な呼び出しでは、人に尋ねます。
4. 呼び出しが実際に走ったときの被害を抑えます。

この層がなければ、1 回のまずい tool 呼び出しが取り返しのつかない副作用を起こしえます。

---

## 仕組み

![Mechanism diagram](assets/03-permission-and-sandbox.png)

permission の判断は純粋関数が行います。tool、現在のモード、そして allow 規則を読み、3 つの値のうち 1 つを返します。

- `allow`: tool を実行します。
- `ask`: いったん止めて、人に尋ねます。
- `deny`: tool を実行しません。

モードは既定の挙動を変えます。たとえば plan mode は読み取り専用の tool を通しますが、計画が承認されるまで編集を拒否します。

### 新規: ゲート

permission の判断は `decide()` がすべてです。

```python
def decide(tool, mode, allow_rules) -> str:      # src/permissions.py (new)
    if mode == BYPASS:                            # operator opted out
        return "allow"
    if mode == PLAN:                              # exploring, not acting yet
        if tool.is_read_only:           return "allow"
        if tool.name == "ExitPlanMode": return "ask"     # approval handshake (section 5)
        return "deny"                             # no side effects until approved
    if tool.is_read_only or tool.name in allow_rules:
        return "allow"
    if mode == ACCEPT_EDITS and tool.is_edit:
        return "allow"                            # a class of work pre-approved
    return "ask"                                  # default: when unsure, ask
```

この関数は I/O を持ちません。そのおかげで、モードごとにテストするのが簡単です。

### 組み込み方

ゲートは `_dispatch` の中、`run_tool` の直前で走ります。

```python
def _dispatch(block, registry, mode, allow_rules, approver):   # src/loop.py
    ...                                                  # resolve tool (section 2)
    decision = decide(tool, mode, allow_rules)           # 3 · the gate, the new line
    if decision == "deny":
        return res(f"{name} not allowed in {mode} mode")
    if decision == "ask" and not approver(name, block.input):
        return res(f"{name} denied by user")
    return res(run_tool(tool, block.input))              # only now does it run
```

- loop の本体はセクション 1 と 2 から変わっていません。
- ゲートが付くのは `_dispatch` だけです。
- `deny` と、承認されなかった `ask` は `run_tool` に決して届きません。
- 拒否も `tool_result` として返るので、モデルは何が起きたかを見て動きを変えられます。
- `approver` の既定は `False` です。つまり `ask` は、人が承認しない限り「いいえ」を意味します。

肝心の不変条件は保たれます。実際の行動が走らなかったときでも、どの tool 呼び出しも結果メッセージを 1 つ生みます。

実際のシステムは、規則の優先順位、記憶される承認、sandbox 内での実行を足します。どれも同じゲートの延長です。

### さらに読む

ここから先は `src/` にはありません。ai-agent-book から来ていて、表に挙げたシステムで確認が取れているわけではありません。

**コマンドの綴りではなく、何をするかを検査する。** agent が shell コマンドの実行を要求します。`decide()` に見えるのは tool 名 1 つなので、
実際の判断はコマンド文字列についてのものになります。文字列の deny リストがよくある答えですが、これは失敗します。`rm -rf /` は簡単に捕まえられます。次はすり抜けます。

- `find . -exec rm {} \;` は削除をフラグの中に隠します。
- `$(echo rm) -rf /` は shell の実行時に `rm` という語を組み立てます。
- `curl -o /etc/crontab` は、書き込みコマンドを名指しせずにファイルを書きます。

対処はパーサーです。テキストではなく構造を読みます。コマンドをプログラムと引数に分けます。どのフラグが値を取るかを知っているので、
引数とフラグを区別できます。そのうえで、各プログラムが何をするかを問います。`-exec` は自分のコマンドを抱えているので、そのコマンドも
検査されます。`-o` は書き込み先のファイルを指すので、そのパスが書き込みとして検査されます。

コストは、パーサーがプログラムごとの規則を必要とすることです。知らないプログラムは読めないプログラムなので、その後ろにはやはり sandbox が要ります。

**通ってしまう破壊的な近道を止める。** 壊れたテーブルを直す方法は 2 つあり、どちらも動くテーブルで終わります。
1 つはマイグレーションです。もう 1 つは削除してゼロから作り直します。結果の検査 (セクション 21) は最終状態しか見ないので、どちらも通します。

対処は、行き先だけでなく経路にもゲートをかけることです。削除して作り直す道は、作り直したテーブルが正しくなるとしても止めたままにします。
コストは、作り直しが本当に正しい対処である場合に、人の承認が要るようになることです。

**sandbox が制限するもの。** ゲートは間違いえます。sandbox は、間違った `allow` の代償を小さく保つものです。3 つの制限がほとんどの仕事をこなします。

- **外向き通信。** 既定でネットワークを遮断します。許可した通信は、許可ホストの一覧を持つプロキシ経由で送ります。
  これは trifecta の中で最も安く切れる 1 本です。agent はコードを読めますし、ファイルも書けます。ただ、それをどこにも送れません。
- **マウント。** ソースは読み取り専用でマウントします。認証情報のファイルはそもそもマウントしません。書き込める作業ディレクトリを 1 つだけ与え、他は与えません。
  開けないファイルを、agent は漏らせません。
- **クォータ。** CPU、メモリ、ディスク、実時間に上限を設けます。上限に達したら、tool の結果としてエラーを返します。
  プロセスを黙って強制終了しないでください。モデルはタイムアウトを読んで、より短いコマンドを試せます。黙って落とすと、読むものが何も残りません。

**ユーザーを二度待たせずに尋ねる。** ゲートが `ask` を返し、ユーザーは待っている状態になります。検査そのものが遅かった場合、
プロンプトが出る前にもう一度待たされたことになります。投機的な検査は、その最初の待ちをなくします。順序はこうです。

- harness は permission の検査をバックグラウンドで開始します。
- 画面には進捗の行がすぐ出ます。この行はシステムに何の変更も加えません。
- その行が出ている間に検査が `allow` を返せば、tool が走り、プロンプトは出ません。
- 検査がまだ決まらなければ、その行が確認プロンプトに変わります。

これでも安全な理由はこうです。先に走っているのは検査だけです。tool 自体は答えを待ち続けます。

---

## システム別

各 agent が副作用にどうゲートをかけ、モードをどう切り替え、判断をどう覚えるかです。

| | Claude Code | mini-swe-agent | deepseek-harness |
| --- | --- | --- | --- |
| **利点** | モード、順序付きの規則、sandbox 化による細かい制御。 | 数分で監査可能。拒否はモデルへ返却。 | 拒否が緩むことはなし。sandbox 化は判断がつかなければ閉じる側。 |
| **欠点** | 状態が多い。bypass と事前承認の経路は狭く保つ必要あり。 | どのコマンドも同じ扱い。記憶は一切なし。 | 方針の置き場所がガード、承認、sandbox、プリセットに分散。 |
| **理由** | 毎回尋ねると疲れを生むため、承認を保存。 | プロンプトと正規表現で十分。被害を抑えるのは環境。 | 関心ごとに、閉じる側に倒す独立したサービスを配置。 |
| **方法: gate point** | 各 tool の前。web、MCP、リモートは別々にゲート。 | ステップの実行前。Enter で承認、コメントで拒否。 | 実行前イベント、続いて拒否だけを出すガード。 |
| **方法: permission modes** | 既定、編集承認済み、plan、拒否、bypass。 | `human`、`confirm`、`yolo`。実行中に切り替え可能。 | sandbox のモードに ask か never を組み合わせ、プリセットとして提供。 |
| **方法: sandbox** | bash を sandbox の中で実行可能。 | 環境クラスが sandbox そのもの。ホスト、コンテナ、ラッパー。 | プロバイダが各 argv を包み、拒否は分類されて返却。 |
| **方法: rule persistence** | 規則は優先順位で session か設定に統合。 | 設定の正規表現。一致するとプロンプトを省略。 | 設定の変更はログのイベント。再生による方針の畳み込み。 |

---

## 失敗モード

- **パターン照合のすり抜け。** 文字列の deny リストは shell の書き換えを取りこぼします。コマンドを構文解析して、実際に何をするかを検査してください。パーサーの後ろに sandbox を残してください。
- **モードが開きすぎ。** 広すぎる allow 規則や bypass モードは、後続の危険な呼び出しを黙って通しえます。bypass の範囲を絞り、有効なモードを画面に出してください。
- **承認疲れ。** 毎回尋ねると、ユーザーは読まずに承認するようになります。低リスクの区分は事前承認しつつ、破壊的な行動は明示のままにしてください。
- **subagent での黙った拒否。** child agent には尋ねる先の端末がないことがあります。静かに失敗させず、プロンプトを親へ持ち上げてください。
- **sandbox が無効。** 許可されたコマンドが sandbox の外で走るなら、permission のプロンプトが最後の検査になります。sandbox を通らない経路は方針の後ろに置いてください。
- **承認済みの呼び出しを通じた持ち出し。** 個々の呼び出しはそれぞれゲートを通れても、session 全体としては秘密を読んで外に送っていることがあります。
  既定でネットワークを遮断し、3 つめの能力をそもそも使えなくしてください。
- **検証は通るが破壊的。** 削除して作り直す道は、最終状態が正しいので結果の検査を通ります。最終状態だけでなく、行動そのものを検査してください。
- **汚染された memory。** memory ファイルに注入された指示は、以降のすべての session で読み戻されます。保存された memory は信頼できないコンテンツとして扱い、運用者の規則としては決して扱わないでください。

---

## 実行

[`src/`](src/) は 02 を引き継ぎ、次を足します。

- [`permissions.py`](src/permissions.py): 4 つのモードにまたがる `decide`。
- [`loop.py`](src/loop.py): `_dispatch` の中で、実行前に各呼び出しにゲートをかけます。

```bash
python sections/03-permission-sandbox/src/test.py         # offline checks, no key
uv run python sections/03-permission-sandbox/src/demo.py  # live demo, needs a key
```

---

## 出典

- [Claude Code source](https://github.com/yasasbanukaofficial/claude-code):
  `QueryEngine.ts`, `hooks/useCanUseTool.tsx`, `types/permissions.ts`, `utils/permissions/PermissionUpdate.ts`。
- [Claude Code sandbox and web gates](https://github.com/yasasbanukaofficial/claude-code): `tools/BashTool/shouldUseSandbox.ts`, `tools/WebFetchTool/preapproved.ts`。
- [mini-swe-agent source](https://github.com/swe-agent/mini-swe-agent): `agents/interactive.py`, `environments/docker.py`, `environments/extra/bubblewrap.py`。
- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness) の `dsh-v0.1.0-rc.7`:
  `docs/subsystems/tools.md`, `docs/subsystems/approval.md`, `docs/subsystems/sandbox.md`, `docs/subsystems/permission-presets.md`,
  `packages/sandbox/sandbox-local/README.md`, `packages/shell/bash-sandbox/README.md`。
- [ai-agent-book · chapter 5](https://github.com/bojieli/ai-agent-book/blob/main/book/chapter5.md) (『深入理解 AI Agent』, 李博杰。中国語の原文が正典):
  memory による増幅の軸、sandbox の外向き通信、マウントとクォータの方針、意味に基づくコマンドの構文解析、投機的な permission 検査、
  そして結果だけでなく経路を縛ること。これらの設計についての単一の出典です。
- [The lethal trifecta for AI agents](https://simonwillison.net/2025/Jun/16/the-lethal-trifecta/) (Simon Willison):
  非公開データへのアクセス、信頼できないコンテンツ、外部との通信。組み合わせてはいけない 3 つの能力です。
- [learn-claude-code · s03_permission](https://github.com/shareAI-lab/learn-claude-code): セクションの組み立て方。
