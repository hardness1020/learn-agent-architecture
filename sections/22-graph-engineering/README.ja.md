# 22 · Graph engineering

[English](README.md) · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · **日本語** · [한국어](README.ko.md)

> 次に何を動かすかをモデルに尋ねるのはやめましょう。既に分かっている経路をコードに書き、判断が要るところにだけモデルを使いましょう。

セクション 21 は 1 つの agent の周りに loop を積み上げました。このセクションは、モデル呼び出しとモデル呼び出しの間の仕事を形づけます。

多くのタスクには、どのモデル呼び出しよりも前から分かっている構造があります。動く前にチケットを分類する、commit の前に diff をレビューする、外部に出す前に承認を取る、といったものです。
素の agent loop は、次に何をするかをモデルに尋ねることで、その構造を毎回発見し直します。モデル任せのルーティングは遅く、token を使い、実行ごとにぶれます。

Graph engineering は、既に分かっている構造を有向グラフとしてコードに書きます。

1. ノードが仕事をします。ノードは素のコードでも、1 回のモデル呼び出しでも、agent の実行全体でも構いません。
2. エッジが次のノードを選びます。harness はそれをモデル呼び出しではなくコードで評価します。
3. 循環は許されます。やり直し、レビュー後の修正、人間による一時停止には、どれも後ろ向きの経路が要ります。
4. 状態はグラフを流れる 1 つのレコードです。各ノードがそれを読み、自分の更新を書きます。

構造を持つのはコードです。判断を供給するのはモデルです。loop (セクション 21) はそうしたグラフの最小形です。ノードが 2 つと後ろ向きのエッジが 1 本。このセクションはそれを一般化します。

---

## 仕組み

![Mechanism diagram](assets/22-graph-engineering.png)

単純な形は 3 つの部品でできています。ノード名を関数に対応付ける dict、各ノードを次に動くものに対応付ける dict、そして全ノードが読み書きする 1 つの state dict です。

```python
def run_graph(nodes, edges, state, start, budget=20):  # src/graph.py
    state = dict(state)
    trace = []
    node = start
    for _ in range(budget):                        # the ceiling: harness-enforced
        state.update(nodes[node](state) or {})     # a node returns only its updates
        trace.append(node)
        step = edges.get(node, END)
        node = step(state) if callable(step) else step   # a coded edge: no model call
        if node == END:
            return {"ok": True, "state": state, "trace": trace}
    return {"ok": False, "state": state, "trace": trace}   # budget spent: escalate
```

- `nodes` は dispatch のマップです (セクション 2)。ノードは状態を読み、自分が変えたキーだけを返します。
- エッジは固定の名前 (決定的) か、状態を受け取る callable (条件付き) です。どちらにせよ harness はそれをコードで評価します。ルーティングに token はかかりません。
- エッジを持たないノードはグラフを終わらせます。予算はセクション 21 の上限です。循環はその回数で止まり、`ok: False` でエスカレーションします。
- `trace` はどのノードがどの順で動いたかを記録します。これがセクション 20 に渡る実行の記録です。

### ノード: 決定性から自律性までのスケール

各ノードはこのスケール上の一点を選びます。

- **コードノード。** パース、検証、固定の API 呼び出し。決定的で、token を使いません。
- **モデルノード。** LLM 呼び出しが 1 回。分類器などです。範囲の限られた判断。
- **agent ノード。** tool を持つセクション 1 の loop 全体。固定された枠の中での、終わりの決まっていない判断。

`agent_node` は内側の loop をノードとして載せます。訪問のたびに、状態から組み立てた新しい `messages[]` の上で `run_turn` を走らせます。
ですからそのノードは、実行全体ではなく、自分の prompt 組み立て器が渡したものだけを見ます。

このスケールが予算の規律そのものです。分岐が分かっているところはコードでルーティングし、モデル呼び出しは判断が要るノードの中でだけ使います。

### 名前の付いた形

出典が名前を付けている workflow のパターンは、どれもグラフの形です。

- **Prompt chaining。** ノードの一本道で、間にコードのゲートが入ります。
- **Routing。** 条件付きエッジが 1 本、専門ノードへ fan out します。
- **Parallelization。** 兄弟の枝が同時に走り、1 つのノードで合流します。タスクを分割する (sectioning) 場合と、投票のために繰り返す (voting) 場合があります。
- **Orchestrator-workers。** 実行時に fan-out を決めるノードと、その後の合流ノード。エッジの集合は動的ですが、形はやはりグラフです。
- **Evaluator-optimizer。** worker ノードと checker ノードを、後ろ向きのエッジ 1 本でつないだもの。セクション 21 の検証 loop を部分グラフにしたものです。

呼び名は出典の間で定まっていません。`ai-agent-book` は同じ範囲を「協調トポロジ」と「オーケストレーション」として扱い、
「graph engineering」には用語の注記で触れるだけです。このセクションは自分の呼び名を通します。ここで述べているのは、コードに書かれたグラフだからです。
別の出典を読むときは、言葉ではなく仕組みで突き合わせてください。

### グラフにしない方がよいとき

終わりの決まっていない仕事は、あらかじめ決めた経路になじみません。深い調査や難しいデバッグには実行時に立ち上がる計画が要ります。前もって描いたグラフは、解決に必要な経路を禁じてしまいます。
出典が示す規則は、どのみち強制するつもりの構造だけをコードに書くこと (動く前に分類する、commit の前にレビューする、送る前に承認する)、
そして結果が目に見えて良くなるときだけ構造を足すことです。それ以外はすべて、素の loop を使ってモデルに計画させます。

よくあるのは折衷です。固定されたグラフの中の 1 ノードとして agent を置きます。グラフはレビューが必ず行われることを保証し、agent は自分の枠の中で進め方を決めます。

### 既存の構成への組み込み方

このセクションは小さな primitive を 1 つ (エッジのマップ) だけ足し、残りは再利用します。

- ノードの仕事はセクション 1 の loop です。`agent_node` は `run_turn` をそのまま包みます。
- コードで書かれたエッジは、セクション 2 の dispatch の規律に従います。モデルの出力ではなく、マップです。
- worker と checker をノードに分けるのはセクション 6 です。兄弟の枝はセクション 15 の worktree で隔離します。
- ステップ予算とエスカレーションの契約はセクション 21 です。
- trace はセクション 20 のテレメトリに供給されます。どのエッジが発火したかを見れば、どの枝が死んでいるかが分かります。

実行可能コードは、上の図にある demo のグラフをそのまま組みます。

```python
nodes = {                                          # src/demo.py
    "classify": lambda s: {"route": "math" if any(c.isdigit() for c in s["task"]) else "prose"},
    "math": agent_node(prompt, model, math_reg),   # a full agent run as one node
    "prose": agent_node(prompt, model, Registry()),
    "check": check_node,                           # section 21's checker, now a node
}
edges = {
    "classify": lambda s: s["route"],              # a coded edge: routing costs no tokens
    "math": "check",
    "prose": "check",
    "check": lambda s: END if s["verdict"]["passed"] else s["route"],   # the cycle
}
```

### さらに読む

以下はどれも `src/` にはありません。ai-agent-book に由来するもので、表にあるシステムで確認が取れているわけではありません。

**フェーズノード。** フェーズノードは 1 つの仕事を一連の段階として動かし、どの段階も同じ `messages[]` を共有します。
探索、実装、レビューは 1 つの仕事の段階であって、3 つの別々の仕事ではありません。
trajectory がある段階の発見を次の段階へ運ぶので、どの段階もタスクを頭から読み直しません。

**フェーズごとの tool。** 各フェーズは専用の system prompt と専用の tool 一式を持ち、フェーズが変わると harness が両方を差し替えます。
履歴はそのまま残るので、次のフェーズのために詰め直すものはありません。本の記述にある 3 つのフェーズは次のとおりです。

- **探索。** 読むことと検索すること。
- **実装。** 編集することと実行すること。
- **レビュー。** 読むことと、判定を返す tool。

**ゲート tool。** モデルは `finish_exploring` のようなゲート tool を呼ぶことでフェーズを抜けます。
harness はその呼び出しをエッジとして読み、次のフェーズを開始します。ゲートが唯一の出口なので、フェーズがいつ終わるかを決めるのはモデルではなく harness です。

**経路。** 最初に探索が走り、次に実装、次にレビューです。レビューが不合格なら実行は実装に戻り、
実装はレビューの指摘が既に trajectory に入った状態から再開します。このセクションの言い方では、後ろ向きのエッジが 1 本ある一本道であり、
上に出てきた evaluator-optimizer と同じ形です。

**どちらを載せるか。** 枝が互いに無関係なら、新しい `messages[]` を使います。ノードが 1 つの仕事の段階なら、1 本の trajectory を使います。
新しい `messages[]` は各ノードの window を小さく保ち、その枝を独立に保ちます。
1 本の trajectory はそれまでの発見をすべて視界に残しますが、経路が長くなるほど window をより多く埋めます。この選択は context の問題です (セクション 8)。

**何をもって multi-agent と呼ぶか。** 本はこの設計を multi-agent と呼びます。フェーズごとに prompt と tool が変わるからです。
このリポジトリでは、prompt と tool が変わる 1 つの agent と呼びます。どちらの呼び名でも仕組みは同じなので、結果を引くときはどちらの定義のことかを言ってください。

**単一ソース。** この記述は本自身の実験に基づいています。独立した報告による裏付けはありません。

---

## システム別

各 agent が次に何を動かすかをどう決めるか。

| | Claude Code | Hermes Agent | mini-swe-agent |
| --- | --- | --- | --- |
| **利点** | コードでルーティング。token もぶれもなし。終わったノードは再開時に再生される。 | グラフを書く必要がない。構造がタスクに合う。 | グラフ全体をひと目で監査できる。 |
| **欠点** | グラフは実行ごとのスクリプトであって、再利用できる宣言されたグラフではない。 | ルーティングが token を使い、実行ごとにぶれうる。 | どのタスクにも同じ形が 1 つだけ。枝が専門化できない。 |
| **理由** | オーケストレーションはプログラム。一度書いて決定的に動かす。 | アシスタントの仕事は事前に宣言するには終わりが決まっていなさすぎる。 | 選択はモデルに残す。harness は 1 循環だけ。 |
| **方法: nodes** | ノードごとに subagent 1 つ、スキーマ検査付きの構造化出力。 | 委譲した subagent。深さと同時実行数に上限あり。 | 2 つ。モデルのステップと環境のステップ。 |
| **方法: routing** | 段階と段階の間は素のコード。条件分岐、loop、fan-out。 | モデルが tool call でルーティングする。コードのエッジはなし。 | submit か予算切れまで、固定の循環が 1 つ。 |
| **方法: state** | 戻り値が次へ引き継がれる。journal がノードの出力を記録し、再開に使う。 | 結果は完了キュー経由で返ってくる。 | メッセージのリストが状態のすべて。 |

---

## 失敗モード

- **モデルを router にする。** ルーティングをモデルに投げると token を燃やし、レイテンシが増え、実行ごとにぶれます。上流での誤ルートは、その後のすべてを誤った方向に送ります。
  緩和策: 遷移はコードで評価し、モデル呼び出しは判断が要るノードのために取っておきます。
- **グラフにしすぎる。** 探索が必要だったタスクに固定のグラフをかぶせると、解決に必要な経路を禁じてしまいます。
  緩和策: どのみち強制するつもりの構造だけをコードに書き、終わりの決まっていない仕事は素の loop に任せます。
- **失敗のエッジがない。** FAIL の送り先がない検査ノードは、まずい出力を下流に流します。
  緩和策: どの検査ノードにも、予算付きの後ろ向きエッジを与えます (セクション 21)。
- **上限のない循環。** 上限のないやり直しエッジは永遠に回ります。緩和策: harness が強制するステップ予算。予算切れはエスカレーションを意味します。
- **状態の肥大。** どのノードも自分の出力を丸ごと共有状態に流し込み、後段のノードがそこに埋もれます。
  緩和策: 状態の境界を厳格にします。ノードは必要な部分集合だけを読み、自分の更新だけを返します (セクション 8)。
- **実行途中での死。** 長いグラフがノード 7 で死に、ノード 1 から再開します。
  緩和策: 各ノードの出力を記録し、再開時には終わったノードを記録から再生します (セクション 11、12)。
- **終わらないフェーズ。** モデルがゲート tool を呼ばないので、フェーズは同じ prompt と同じ tool のまま働き続けます。止めるのは予算だけです。
  緩和策: ゲートを唯一の出口にし、フェーズごとにステップ予算を与えます。予算切れなら次のフェーズに移るか、エスカレーションします。
- **すべてのフェーズを抱えた trajectory。** 1 本の trajectory はフェーズごとに伸びます。そこには現在のフェーズが載せていない tool への呼び出しも残っていて、モデルはそれをまた試すかもしれません。
  緩和策: 現在のフェーズとその tool をフェーズの prompt で名指しし、載っていない tool への呼び出しは明確なエラーで拒否し、終わったフェーズは compaction します (セクション 8)。

---

## 実行

[`src/`](src/) は 21 を引き継ぎ、次を追加します。

- [`graph.py`](src/graph.py): `run_graph` (ノードの dispatch マップ、固定エッジと条件付きエッジ、引き継がれる状態、ステップ予算) と、内側の loop をノードとして載せる `agent_node`。
- [`test.py`](src/test.py): 一本道の順序と状態のマージ、コードだけのルーティング、予算で止まる循環、agent ノードの訪問ごとに新しい `messages[]` になることのオフライン検査。
- [`demo.py`](src/demo.py): ルーティングされる実行を 1 回。コードノードが分類し、コードのエッジがルーティングし、agent ノードが答え、
  セクション 21 の checker が採点し、不合格の判定はフィードバックとともに循環して戻ります。

loop は変わりません。いつそれが動くかをグラフが決めます。

```bash
python sections/22-graph-engineering/src/test.py         # offline checks, no key
uv run python sections/22-graph-engineering/src/demo.py  # live demo, needs a key
```

---

## 出典

- [LangChain · 3 years of graph engineering](https://www.langchain.com/blog/3-years-of-graph-engineering-with-langgraph): ノード、エッジ、循環、ノードとしての agent、グラフにしない方がよいとき。
- [Anthropic · Building effective agents](https://www.anthropic.com/engineering/building-effective-agents): workflow と agent の対比、および 5 つの workflow の形。
- [Google · Why we built ADK 2.0](https://developers.googleblog.com/en/why-we-built-adk-20/): コードでのルーティング、ノード間の context 隔離、workflow のノードに置く agent。
- [Claude Code](https://code.claude.com/docs): `Workflow` のスクリプト契約 (パイプライン、並列 fan-out、構造化出力、再開)。
  ソースのバックアップではなく、tool スキーマと文書化された挙動から。
- [Hermes Agent source](https://github.com/NousResearch/hermes-agent): `tools/delegate_tool.py`、`tools/async_delegation.py`、`batch_runner.py`。
- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness) の `dsh-v0.1.0-rc.7`:
  `docs/subsystems/workflow.md`、`packages/workflow/tool-workflow/README.md`: 実行ごとにモデルが書くスクリプト。永続的なグラフはなし。
- [mini-swe-agent source](https://github.com/swe-agent/mini-swe-agent): `agents/default.py`、`run/benchmarks/swebench.py` にある実行 loop と予算。
- [ai-agent-book · chapter 10](https://github.com/bojieli/ai-agent-book/blob/main/book/chapter10.md) (《深入理解 AI Agent》、李博杰、多 Agent 协作。中国語原文が正典):
  1 本の trajectory 上での多段階の役割切り替え。フェーズごとの system prompt と tool 一式、tool call としてのフェーズゲート、レビューから実装へ戻るルーティング。
  根拠は本自身の実験だけです。同じ章は「協調トポロジ」と「オーケストレーション」を主要な用語として使い続けています。
