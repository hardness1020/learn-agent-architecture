<h1 align="center" style="margin-top: 0;">Awesome Agent Architecture</h1>

<p align="center">
  <strong>最新の AI Agent が LLM を中心にどのように構築されているかを学びます。</strong><br>
</p>

<p align="center">
  <a href="#セクション"><img src="https://img.shields.io/badge/Focus-Harness_Engineering-8250df" alt="Focus: Harness Engineering"></a>
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

モデルは推論します。harness はその推論を制御された行動へ変換します。ツールを実行し、呼び出し間で状態を保持し、副作用を制御し、複数の loop を調整します。
モデル呼び出しだけでは、これらを実現できません。

このリポジトリは harness をセクションごとに説明します：ループ、ツール、メモリ、権限、コンテキスト、タスク、インターフェース。
一度学べば、多くのエージェントを理解できます。コーディングツール、チャットアシスタント、自律実行者は主に harness の選択が異なります。

一つのセクションだけでは扱いきれない内容は、次の二つの関連リポジトリで詳しく説明しています。

- [learn-agent-memory](https://github.com/hardness1020/learn-agent-memory)：memory loop を実運用向けの memory subsystem へ拡張します。
- [learn-deepseek-harness](https://github.com/hardness1020/learn-deepseek-harness)：deepseek-harness を一から学び、plugin seam を一つずつ理解します。

**内容:** [Loop](#agent-loop) · [学習方法](#学習方法) · [対象システム](#研究対象システム) ·
[セクション](#セクション) · [構成](#リポジトリ構造) · [デモ実行](#デモの実行)

---

## Agent Loop

![agent loop](assets/the-agent-loop.png)

ほとんどのエージェントは同じ制御フローを共有しています：モデルを呼び出し、要求されたツールを実行し、結果を追加し、再びモデルを呼び出します。

ループは小さいです。その周りにほとんどのエンジニアリングが存在します：ディスパッチツール、ゲートの副作用、コンテキストの管理、状態の永続化、そして他のループの調整。

---

## 学習方法

各セクションは独立しており、同じ四部構成のレンズを使用します：

1. **オープニング。** この層が解決する問題。
2. **メカニズム。** 一般的な設計と制御フロー。
3. **システムごと。** 実際のシステムでの実装方法。
4. **障害モード。** 何が壊れるか、そしてどのように対処するか。

このリポジトリから学ぶには：

- **セクションを順番に読むこと。各セクションは前の層に基づいて構築されています。**
- 実行可能なセクションの場合、`src/loop.py` を読み、それから `demo.py` を実行してください。
- セクション`src/`をその前のセクションと比較してください。この差分は、そのセクションが追加する唯一のメカニズムです。

---

## 研究対象システム

各システムは下記のセクションのための作業例です。

| システム | 人々が使う理由 | 読む目的 | セクション | 調査したバージョン |
| --- | --- | --- | --- | --- |
| **Claude Code**  | 最先端の coding agent。実際のリポジトリでファイルを編集し、コマンドを実行して変更を反映します。 | 完全な harness。まずここから       | 0〜23（すべて）        | v2.1.88         |
| **Hermes Agent** | 長期利用向け assistant。ユーザーを記憶し、workflow を学習して、さまざまな環境で動作します。 | Memory、skills、常時接続 channel | 7, 9, 14, 16, 19, 21, 22 | v2026.7.1 |
| **mini-swe-agent** | 研究用 baseline。bash tool 一つ、約150行。 | 最小の完全な loop、budget、eval harness | 0〜3、8、10、11、20〜23 | v2.4.5 |
| **deepseek-harness** | Plugin-first harness。loop 自体も交換可能な plugin です。 | Plugin seam、永続的な session log、ACP | 1〜8、10〜14、16〜21 | dsh-v0.1.0-rc.7 |
| *(続報あり)* | | | | |

> 後で追加のシステムを追加できます。OpenClaw や aider も含まれます。
> 2つの関連リポジトリがさらに詳しく扱います: メモリ層用 [learn-agent-memory](https://github.com/hardness1020/learn-agent-memory)、
> そして学習用 [learn-deepseek-harness](https://github.com/hardness1020/learn-deepseek-harness) で deepseek-harness をゼロから学習します。

---

## セクション

基本的なループから自律的に動作するharnessまで、八つの層があります。各行は独立した説明文にリンクしています。

> セクション9は[learn-agent-memory](https://github.com/hardness1020/learn-agent-memory)で続きます：メモリループを本番環境にスケールするさらに10段階。

![学習パス](assets/learning-path.png)

| #  | セクション                                                  | 質問                                               | 主要なメカニズム                                      |
| -- | ------------------------------------------------------------ | -------------------------------------------------- | ----------------------------------------------------- |
|    | **レイヤー 0 · 基礎**                             |                                                    |                                                       |
| 0  | [Harness thesis](sections/00-harness-thesis/)                 | Agent の主体性はどこから生まれるのか？                       | Model vs harness、action、observation、permission  |
|    | **レイヤー 1 · コアループ**                               |                                                    |                                                       |
| 1  | [Agent Loop](sections/01-agent-loop/)                         | Agent はどのように動作を続けるのか？                      | `messages[]`、loop、`stop_reason`                 |
| 2  | [Tool Runtime](sections/02-tool-runtime/)                     | Tool はどのように呼び出され、振り分けられるのか？                   | Registry、schema、dispatch、deferred search          |
| 3  | [Permission & sandbox](sections/03-permission-sandbox/)   | 副作用はどのように制御されますか？                        | 権限モード、承認、サンドボックス               |
| 4  | [Hooks](sections/04-hooks/)                                   | 拡張機能はどのようにループに接続されますか？              | `PreToolUse`, `PostToolUse`, ライフサイクルイベント     |
|    | **レイヤー2 · 複雑な作業**                            |                                                    |                                                       |
| 5  | [Planning & todos](sections/05-planning-todos/)           | 大きな作業はどのように分解されるか？                        | Plan mode、todo list、編集前の承認           |
| 6  | [Subagents](sections/06-subagents/)                           | サブ問題はどのように孤立されるか？                      | 新しい`messages[]`、委任、子ループ          |
| 7  | [Skills](sections/07-skills/)                                 | 能力はどのように必要に応じてロードされるか？             | `SKILL.md`、カタログ、progressive disclosure         |
| 8  | [Context management](sections/08-context-management/)         | 長時間のセッションはどのようにウィンドウに収まるか？               | 予算管理、スタブ、圧縮、要約               |
|    | **レイヤー3 · 知識と回復力**                  |                                                    |                                                       |
| 9  | [Memory](sections/09-memory/)                                 | 実行をまたいで、どのように記憶を保持するのか？                  | Selection、recall、extraction、consolidation          |
| 10 | [System prompt assembly](sections/10-system-prompt/)          | 各 turn の prompt はどのように構築されるのか？                 | Prompt section、live state、cache boundary         |
| 11 | [Error recovery](sections/11-error-recovery/)                 | 長時間の task はどのように障害から回復するのか？              | Retry、overflow recovery、fallback model            |
|    | **レイヤー4 · 長期実行＆非同期**                         |                                                    |                                                       |
| 12 | [タスクシステム](sections/12-task-system/)                   | どのようにして作業はターンを超えて持続するのか？    | タスク記録、依存関係、ロック                           |
| 13 | [Background execution](sections/13-background-execution/)      | どのようにして作業はメインループ外で実行されるのか？ | ハンドル、タスク状態、通知キュー                       |
| 14 | [スケジューリング](sections/14-scheduling/)                     | どのようにしてエージェントは後で実行されるのか？        | Cron、スリープ、リモートトリガー、キュー      |
| 15 | [Worktree isolation](sections/15-worktree-isolation/)         | 並列作業はどのように衝突を回避しますか？           | Git worktrees、cwdバインディング、安全なクリーンアップ              |
|    | **レイヤー5 · マルチエージェント**                             |                                                    |                                                       |
| 16 | [調整](sections/16-coordination/)                     | 多くのエージェントはどのように通信しますか？                           | 受信箱、ブロードキャスト、パーミッションバブリング              |
| 17 | [プロトコル](sections/17-protocols/)                           | エージェントはどのように合意し、正しく停止しますか？              | 計画承認、シャットダウンハンドシェイク                    |
| 18 | [自律](sections/18-autonomy/)                             | エージェントはどのように自分自身を組織化するのか？                 | 待機サイクル、タスクの獲得、自己組織化          |
|    | **レイヤー6 · 拡張と統合**                 |                                                    |                                                       |
| 19 | [MCP / plugins / チャンネル](sections/19-mcp-plugins-channels/) | harnessはどのように世界に到達するのか？              | 輸送、チャンネル、ツールプールの組み立て              |
| 20 | [Observability & evaluation](sections/20-observability/)  | どのようにそれが機能するかを知るのか？                           | トレース、メトリクス、evals、障害分析             |
| 23 | [Evaluation](sections/23-evaluation/)                         | 変更が改善につながったかどうかはどうやってわかるのか？            | Eval 環境、リセット、ジャッジ、Pass^k             |
|    | **レイヤー 7 · 構成**                             |                                                    |                                                       |
| 21 | [Loop engineering](sections/21-loop-engineering/)             | ループはどのように積み重なって自律的に動くシステムになるのか？ | 検証ループ、トリガー、予算、成熟度レベル |
| 22 | [Graph engineering](sections/22-graph-engineering/)           | 制御フローはいつモデルからコードに移るのか？ | ノード、コード化されたエッジ、サイクル、ノードとしてのエージェント           |

---

## リポジトリ構造

すべての24のセクションの解説は、`00-harness-thesis/`から`23-evaluation/`まで揃っています。

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

各セクションフォルダは`NN-name/`であり、`README.md`を含んでいます。

セクション1から23も実行可能な`src/`を持っています。コードはセクションごとに累積されます。
各セクションは1つのメカニズムを追加し`loop.py`を進化させるので、隣接するセクションの差分を見ると何が変わったかがわかります。

1つのセクションに収まりきらない深い解析は、それぞれ独自のリポジトリに存在します。
[learn-agent-memory](https://github.com/hardness1020/learn-agent-memory)はセクション9のループを完全なメモリサブシステムに拡張します。
[learn-deepseek-harness](https://github.com/hardness1020/learn-deepseek-harness)はdeepseek-harnessを一から学習し、1つのpluginの継ぎ目ずつ進めます。

---

## デモの実行

セクション1から23までは実行可能なデモが含まれています。リポジトリのルートから一度セットアップしてください:

```bash
uv venv
uv pip install -r requirements.txt
cp .env.example .env        # then add your ANTHROPIC_API_KEY
```

固定された依存関係は[`requirements.txt`](requirements.txt)にあります。`.env`はgitignoreされており、以下を保持しています:

- `ANTHROPIC_API_KEY`
- オプション `ANTHROPIC_MODEL`
- オプション `ANTHROPIC_BASE_URL`

各実行可能なセクションには以下があります:

- `test.py`: オフラインチェック、キーは不要です。
- `demo.py`: APIに対するライブデモ。

```bash
python sections/01-agent-loop/src/test.py         # offline
uv run python sections/01-agent-loop/src/demo.py  # live
```

---

## コントリビューション

- **システムを追加する。** 同じセクション構造に新しいエージェントを挿入してください。
- **セクションを深める。** メカニズム、より明確な図、またはより鋭い失敗モードを追加してください。
- **記録を訂正する。** これらはソース、ドキュメント、動作からの再構築です。ソースに基づく訂正は歓迎します。

推測よりも、名前が確認可能なメカニズムを優先してください。ソースを引用してください。
完全なPRチェックリストについては、[CONTRIBUTING.md](CONTRIBUTING.md) を参照してください。

---

## 参考文献

- [claude-code](https://github.com/yasasbanukaofficial/claude-code): メカニズム名と実装パスに使用された Claude Code のソースバックアップ。
- [hermes-agent](https://github.com/NousResearch/hermes-agent): 研究対象として使用された 2 番目のシステムであるオープンソース agent harness (MIT)。
- [mini-swe-agent](https://github.com/swe-agent/mini-swe-agent): 研究対象として使用された 3 番目のシステムである最小限の SWE エージェント (MIT)。
- [deepseek-harness](https://github.com/deepseek-ai/deepseek-harness): 研究対象として使用された 4 番目のシステムであるプラグインベースの agent harness (MIT)。
- [learn-claude-code](https://github.com/shareAI-lab/learn-claude-code): コードファースト harness 再構築とセクションフレーミング。
- [Anthropic Agent Skills ベストプラクティス](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices): Progressive disclosure レベルの skills。
- [Anthropic prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching): キャッシュブレークポイント、TTL、価格設定、およびトークン最小値。
- [cobusgreyling/loop-engineering](https://github.com/cobusgreyling/loop-engineering): ループ構築ブロックと準備レベル。
- [LangChain · loop engineering の技法](https://www.langchain.com/blog/the-art-of-loop-engineering): 4つの積み重ねループ。
- [Addy Osmani · Loop engineering](https://addyosmani.com/blog/loop-engineering/): エージェントループのための組み合わせブロック。
- [MindStudio · loop engineering とは](https://www.mindstudio.ai/blog/what-is-loop-engineering-autonomous-ai-agent-workflows): 自律ワークフローのための目標条件。
- [Lilian Weng · 自己改善のためのHarness engineering](https://lilianweng.github.io/posts/2026-07-04-harness/): 改善ループ、ループ外にゲート。
- [LangChain · graph engineeringの3年](https://www.langchain.com/blog/3-years-of-graph-engineering-with-langgraph): ノード、エッジ、サイクル、ノードとしてのエージェント。
- [Anthropic · 効果的なエージェントの構築](https://www.anthropic.com/engineering/building-effective-agents): ワークフローとエージェントおよび5つのワークフロー形状。
- [Google · なぜADK 2.0を作ったのか](https://developers.googleblog.com/en/why-we-built-adk-20/): コードでのルーティングとノード間のコンテキスト分離。
- [ai-agent-book](https://github.com/bojieli/ai-agent-book): 李博杰著『深入理解AI Agent』（Apache-2.0）。第6章はevaluationのセクションを基礎とする。
