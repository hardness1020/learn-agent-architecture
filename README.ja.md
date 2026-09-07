<h1 align="center" style="margin-top: 0;">Awesome Agent Architecture</h1>

<p align="center">
  <strong>現代の AI agent が LLM の周りにどう組み立てられているかを学びます。</strong><br>
</p>

<p align="center">
  <a href="#セクション一覧"><img src="https://img.shields.io/badge/Focus-Harness_Engineering-8250df" alt="Focus: Harness Engineering"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-d29922" alt="License"></a>
  <br>
  <a href="https://github.com/anthropics/claude-code"><img src="https://img.shields.io/badge/Claude_Code-D97757" alt="Claude Code"></a>
  <a href="https://github.com/NousResearch/hermes-agent"><img src="https://img.shields.io/badge/Hermes_Agent-1A1A1A" alt="Hermes Agent"></a>
  <a href="https://github.com/swe-agent/mini-swe-agent"><img src="https://img.shields.io/badge/mini--swe--agent-7E56D8" alt="mini-swe-agent"></a>
  <a href="https://github.com/deepseek-ai/deepseek-harness"><img src="https://img.shields.io/badge/deepseek--harness-4D6BFE" alt="deepseek-harness"></a>
</p>

<p align="center">
  <img src="https://github.com/user-attachments/assets/472d8152-5e46-4e39-9f09-e77dcd07936a" alt="Awesome Agent Architecture">
</p>

<p align="center">
  <a href="README.md">English</a> · <a href="README.zh-TW.md">繁體中文</a> · <a href="README.zh-CN.md">简体中文</a> · <strong>日本語</strong> · <a href="README.ko.md">한국어</a>
</p>

推論するのはモデルです。harness はその推論を制御された行動に変えます。tool を実行し、呼び出しをまたいで状態を保ち、副作用にゲートをかけ、複数の loop を調整します。
モデルを一度呼び出すだけでは、そのどれもできません。

この repo は harness をセクションごとに解説します。loop、tool、memory、permission、context、task、そしてインターフェースです。
一度身につければ多くの agent が読めるようになります。コーディング用の tool もチャットアシスタントも自律実行型の runner も、違いのほとんどは harness の選択にあるからです。

1 セクションでは収まらない話題は、2 つの姉妹 repo で掘り下げています。

- [learn-agent-memory](https://github.com/hardness1020/learn-agent-memory): memory loop を本番向けの memory サブシステムまで拡張します。
- [learn-deepseek-harness](https://github.com/hardness1020/learn-deepseek-harness): deepseek-harness をゼロから学びます。plugin の接合部を 1 つずつ追います。

**目次:** [Loop](#agent-loop) · [学び方](#学び方) · [研究対象のシステム](#研究対象のシステム) ·
[セクション一覧](#セクション一覧) · [リポジトリ構成](#リポジトリ構成) · [デモの実行](#デモの実行)

---

## Agent loop

![The agent loop](assets/the-agent-loop.png)

ほとんどの agent は同じ制御フローを共有します。モデルを呼び出し、要求された tool を実行し、結果を追記し、もう一度モデルを呼び出します。

loop そのものは小さいです。工学の大部分はその周りにあります。tool の dispatch、副作用のゲート、context の管理、状態の永続化、そして他の loop との調整です。

---

## 学び方

どのセクションも独立していて、同じ 4 部構成のレンズを使います。

1. **導入。** この層がどんな問題を解くか。
2. **仕組み。** 一般的な設計と制御フロー。
3. **システム別。** 実在のシステムがどう実装しているか。
4. **失敗モード。** 何が壊れ、どう緩和するか。

この repo から学ぶには、次のようにしてください。

- **セクションを順番に読んでください。どのセクションも 1 つ前の層の上に積み上がります。**
- 実行できるセクションでは、まず `src/loop.py` を読み、それから `demo.py` を動かしてください。
- あるセクションの `src/` を 1 つ前のセクションと diff してください。その差分が、そのセクションが足した唯一の仕組みです。

---

## 研究対象のシステム

どのシステムも、以下のセクションのための実例です。

| システム | 使われる理由 | 何を読み取るか | セクション | 調査したバージョン |
| --- | --- | --- | --- | --- |
| **Claude Code**  | 最前線のコーディング agent。ファイルを編集し、コマンドを実行し、実際の repo に変更を反映します。 | harness の全体像。まずここから | 0 から 23 (すべて)        | v2.1.88         |
| **Hermes Agent** | 長期的なアシスタント。あなたを覚え、作業の流れを学び、どこでも動きます。 | memory、skill、常時接続の channel | 7, 9, 14, 16, 19, 21, 22 | v2026.7.1 |
| **mini-swe-agent** | 研究用のベースライン。bash tool が 1 つだけ、約 150 行です。 | 最小構成で完結した loop、予算、eval harness | 0 から 3, 8, 10, 11, 20 から 23 | v2.4.5 |
| **deepseek-harness** | plugin 中心の harness。loop さえ差し替え可能な plugin です。 | plugin の接合部、永続的な session log、ACP | 1 から 8, 10 から 14, 16 から 21 | dsh-v0.1.0-rc.7 |
| *(more soon)* | | | | |

> 今後 OpenClaw や aider を含む他のシステムを追加できます。
> さらに深く掘り下げる姉妹 repo が 2 つあります。memory 層は [learn-agent-memory](https://github.com/hardness1020/learn-agent-memory)、
> deepseek-harness をゼロから学ぶなら [learn-deepseek-harness](https://github.com/hardness1020/learn-deepseek-harness) です。

---

## セクション一覧

基本の loop から自走する harness まで、8 つの層があります。各行は独立した 1 本の解説へのリンクです。

> セクション 9 は [learn-agent-memory](https://github.com/hardness1020/learn-agent-memory) に続きます。その memory loop を本番規模へ広げる 10 段階です。

![The learning path](assets/learning-path.png)

| #  | セクション | 問い | 主な仕組み |
| -- | --- | --- | --- |
|    | **層 0 · 土台** | | |
| 0  | [Harness の主題](sections/00-harness-thesis/README.ja.md) | agency はどこから来るのか | モデルと harness、行動、観測、permission |
|    | **層 1 · 中心の loop** | | |
| 1  | [Agent loop](sections/01-agent-loop/README.ja.md) | agent はどうやって動き続けるのか | `messages[]`、loop、`stop_reason` |
| 2  | [Tool runtime](sections/02-tool-runtime/README.ja.md) | tool はどう呼ばれ、どう振り分けられるのか | registry、schema、dispatch、遅延読み込みの検索 |
| 3  | [Permission と sandbox](sections/03-permission-sandbox/README.ja.md) | 副作用にはどうゲートをかけるのか | permission モード、承認、sandbox 化 |
| 4  | [Hooks](sections/04-hooks/README.ja.md) | 拡張は loop のどこに取り付くのか | `PreToolUse`、`PostToolUse`、ライフサイクルイベント |
|    | **層 2 · 複雑な作業** | | |
| 5  | [計画と todo](sections/05-planning-todos/README.ja.md) | 大きな仕事はどう分解するのか | plan mode、todo リスト、編集前の承認 |
| 6  | [Subagent](sections/06-subagents/README.ja.md) | 部分問題はどう切り離すのか | 新しい `messages[]`、委譲、child loop |
| 7  | [Skill](sections/07-skills/README.ja.md) | 能力はどう必要に応じて読み込むのか | `SKILL.md`、カタログ、段階的な開示 |
| 8  | [Context 管理](sections/08-context-management/README.ja.md) | 長い session をどうやって窓に収めるのか | 予算管理、スタブ、compaction、要約 |
|    | **層 3 · 知識と回復力** | | |
| 9  | [Memory](sections/09-memory/README.ja.md) | 実行をまたいでどう覚えるのか | 選別、recall、抽出、統合 |
| 10 | [System prompt の組み立て](sections/10-system-prompt/README.ja.md) | prompt は turn ごとにどう作られるのか | prompt の各区画、実行中の状態、cache 境界 |
| 11 | [エラー回復](sections/11-error-recovery/README.ja.md) | 長い仕事はどう失敗を乗り切るのか | リトライ、あふれからの回復、フォールバックモデル |
|    | **層 4 · 長時間実行と非同期** | | |
| 12 | [Task システム](sections/12-task-system/README.ja.md) | 仕事は turn を越えてどう残るのか | task レコード、依存関係、ロック |
| 13 | [バックグラウンド実行](sections/13-background-execution/README.ja.md) | 仕事はどうやってメイン loop の外で動くのか | ハンドル、task の状態、通知キュー |
| 14 | [スケジューリング](sections/14-scheduling/README.ja.md) | agent はどうやって後から動くのか | cron、スリープ、リモートからの起動、キュー |
| 15 | [Worktree の分離](sections/15-worktree-isolation/README.ja.md) | 並行作業はどう衝突を避けるのか | git worktree、cwd の束縛、安全な後片付け |
|    | **層 5 · マルチ agent** | | |
| 16 | [協調](sections/16-coordination/README.ja.md) | 多数の agent はどう会話するのか | inbox、ブロードキャスト、permission の持ち上げ |
| 17 | [プロトコル](sections/17-protocols/README.ja.md) | agent はどう合意し、どうきれいに止まるのか | 計画の承認、停止のハンドシェイク |
| 18 | [自律性](sections/18-autonomy/README.ja.md) | agent はどう自分たちを組織するのか | アイドル周期、task の確保、自己組織化 |
|    | **層 6 · 拡張と統合** | | |
| 19 | [MCP / plugin / channel](sections/19-mcp-plugins-channels/README.ja.md) | harness はどう外の世界に手を伸ばすのか | トランスポート、channel、tool プールの組み立て |
| 20 | [可観測性と評価](sections/20-observability/README.ja.md) | 動いているとどう分かるのか | トレース、メトリクス、eval、失敗の分析 |
| 23 | [評価](sections/23-evaluation/README.ja.md) | 変更で良くなったとどう分かるのか | eval 環境、リセット、判定器、Pass^k |
|    | **層 7 · 組み合わせ** | | |
| 21 | [Loop エンジニアリング](sections/21-loop-engineering/README.ja.md) | loop はどう積み重なって自走するシステムになるのか | 検証 loop、トリガー、予算、成熟度レベル |
| 22 | [グラフエンジニアリング](sections/22-graph-engineering/README.ja.md) | 制御フローはいつモデルからコードへ移るのか | ノード、コードで書いた辺、循環、ノードとしての agent |

---

## リポジトリ構成

24 本のセクション解説がすべて揃っています。`00-harness-thesis/` から `23-evaluation/` までです。

```text
awesome-agent-architecture/
├── README.md                  # top-level map
├── sections/                  # one folder per section
│   ├── 00-harness-thesis/     # README.md per section
│   ├── 01-agent-loop/src/     # runnable chain starts here
│   ├── ...
│   └── 23-evaluation/
└── references/                # primary sources and prior art
```

各セクションのフォルダは `NN-name/` という名前で、`README.md` を 1 つ含みます。

セクション 1 から 23 には実行できる `src/` も付いています。コードはセクションごとに積み上がります。
各セクションは仕組みを 1 つ足して `loop.py` を発展させるので、隣り合うセクションの diff が変更点そのものになります。

1 セクションでは収まらない深掘りは、それぞれ独立した repo にあります。
[learn-agent-memory](https://github.com/hardness1020/learn-agent-memory) はセクション 9 の loop を完全な memory サブシステムへ広げます。
[learn-deepseek-harness](https://github.com/hardness1020/learn-deepseek-harness) は deepseek-harness をゼロから、plugin の接合部を 1 つずつ学びます。

---

## デモの実行

セクション 1 から 23 には実行できるデモが付いています。repo のルートで一度だけ準備します。

```bash
uv venv
uv pip install -r requirements.txt
cp .env.example .env        # then add your ANTHROPIC_API_KEY
```

依存関係は [`requirements.txt`](requirements.txt) に固定してあります。`.env` は gitignore されていて、次を持ちます。

- `ANTHROPIC_API_KEY`
- 省略可能な `ANTHROPIC_MODEL`
- 省略可能な `ANTHROPIC_BASE_URL`

実行できるセクションには次があります。

- `test.py`: オフラインの検査。キーは要りません。
- `demo.py`: API に対する実際のデモ。

```bash
python sections/01-agent-loop/src/test.py         # offline
uv run python sections/01-agent-loop/src/demo.py  # live
```

---

## 貢献するには

- **システムを足す。** 新しい agent を同じセクション構成に当てはめてください。
- **セクションを深める。** 仕組み、より分かりやすい図、より鋭い失敗モードを足してください。
- **記述を正す。** これらはソース、ドキュメント、実際の挙動からの再構成です。出典付きの訂正を歓迎します。

推測より、名前があって検証できる仕組みを優先してください。出典を挙げてください。
PR のチェックリスト全体は [CONTRIBUTING.md](CONTRIBUTING.md) を見てください。

---

## 参考文献

- [claude-code](https://github.com/yasasbanukaofficial/claude-code): 仕組みの名前と実装の場所をたどるために使った Claude Code のソースのバックアップ。
- [hermes-agent](https://github.com/NousResearch/hermes-agent): 2 つめの研究対象にしたオープンソースの agent harness (MIT)。
- [mini-swe-agent](https://github.com/swe-agent/mini-swe-agent): 3 つめの研究対象にした最小構成の SWE agent (MIT)。
- [deepseek-harness](https://github.com/deepseek-ai/deepseek-harness): 4 つめの研究対象にした plugin ベースの agent harness (MIT)。
- [learn-claude-code](https://github.com/shareAI-lab/learn-claude-code): コード起点の harness 再構成と、セクションの組み立て方。
- [Anthropic Agent Skills best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices): skill における段階的な開示のレベル。
- [Anthropic prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching): cache の区切り、TTL、価格、最小 token 数。
- [cobusgreyling/loop-engineering](https://github.com/cobusgreyling/loop-engineering): loop の構成要素と成熟度レベル。
- [LangChain · The art of loop engineering](https://www.langchain.com/blog/the-art-of-loop-engineering): 積み重なる 4 つの loop。
- [Addy Osmani · Loop engineering](https://addyosmani.com/blog/loop-engineering/): agent loop のための構成要素の組み合わせ方。
- [MindStudio · What is loop engineering](https://www.mindstudio.ai/blog/what-is-loop-engineering-autonomous-ai-agent-workflows): 自律的なワークフローの終了条件。
- [Lilian Weng · Harness engineering for self-improvement](https://lilianweng.github.io/posts/2026-07-04-harness/): 改善の loop と、loop の外に置くゲート。
- [LangChain · 3 years of graph engineering](https://www.langchain.com/blog/3-years-of-graph-engineering-with-langgraph): ノード、辺、循環、そしてノードとしての agent。
- [Anthropic · Building effective agents](https://www.anthropic.com/engineering/building-effective-agents): ワークフローと agent の違い、および 5 つのワークフロー類型。
- [Google · Why we built ADK 2.0](https://developers.googleblog.com/en/why-we-built-adk-20/): コードで書くルーティングと、ノード間の context 分離。
- [ai-agent-book](https://github.com/bojieli/ai-agent-book): 李博杰 著『深入理解 AI Agent』(Apache-2.0)。第 6 章が評価のセクションの土台です。
