# 7 · Skills

[English](README.md) · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · **日本語** · [한국어](README.ko.md)

> skill は、専門知識、手順、およびスクリプトとファイルの自己完結型バンドルであり、タスクで必要な場合にのみロードされます。

skill は、総合エージェントを 1 つの仕事のスペシャリストに変えます。
これには、従うべき手順に加えて、実行するスクリプトと参照する参照ファイルなどのワークフローがパッケージ化されています。
エージェントは、タスクで呼び出された場合にのみ skill をロードするため、1 つのエージェントは、事前にすべてをロードしなくても、多くの特殊な機能にアクセスできます。

各 skill は、`SKILL.md` ファイルを含むフォルダーです。前付では、skill の名前と説明を記載しています。
本体には命令が保持されており、フォルダーには、skill が使用する場合にのみ読み込まれる追加のスクリプトと参照ファイルをバンドルできます。

エージェントは skills が存在することを知っている必要がありますが、毎ターンすべての skill ボディに対して支払う必要はありません。

skill システムは次のことを行う必要があります。

1. skills を安く出品します。
2. skill が選択されている場合にのみ、完全な命令をロードします。
3. skills が追加のファイルを自動的にロードせずに指すようにします。
4. 組み込み、ユーザー、プロジェクト、plugin、または MCP ソースから skills を検出します。

このレイヤーがないと、プロンプトが大きすぎるか、エージェントがその拡張子を見つけることができません。

---

## メカニズム

![機構図](assets/07-skills.png)

Skills は progressive disclosure を使用します。モデルは、さらにロードするかどうかを決定するのに十分な情報のみを参照します。

1. **メタデータ** フロントマターの `name` および `description`、および skill のパス。この安いカタログは毎ターン system prompt に乗っています。
2. **説明書** `SKILL.md` 本体。モデルは、タスクが skill を必要とする場合にのみファイルを読み取ります。
3. **リソース。** skill フォルダー内の追加ファイル。モデルは、命令がそれらを指す場合、同じファイル ツールを使用してそれらを読み取ります。

スキル固有のツールは必要ありません。カタログで各 skill とそのパスに名前が付けられたら、
エージェントは、通常の読み取りツールでファイルを読み取ることによって、skill をロードします。 L2 と L3 はどちらも単なるファイル読み取りです。

説明はほとんどの作業を行います。これはルーティング条件であり、概要ではありません。
モデルが決定する前に確認するのは、その 1 行だけです。したがって、skill をいつ使用するべきか、また、いつ使用しないかを決めてください。
反例を 1 つ追加します。トピックに名前を付けるだけの行は、モデルに推測を与えます。

### 新機能: skills をスキャンし、プロンプトにリストします。

```python
@dataclass
class Skill:                                   # src/skills.py
    name: str
    description: str                           # L1: frontmatter -> the catalog
    path: Path                                # SKILL.md; the body is read on demand

def load_skills(skills_dir) -> list[Skill]:    # L1: scan <dir>/<name>/SKILL.md at startup
    skills = []
    for sub in sorted(Path(skills_dir).iterdir()):
        meta, _ = _split((sub / "SKILL.md").read_text())   # keep frontmatter, not the body
        skills.append(Skill(meta["name"], meta["description"], sub / "SKILL.md"))
    return skills

def catalog_prompt(skills, base_dir) -> str:   # L1: the block added to the system prompt
    lines = [f"- {s.name}: {s.description} (read {s.path.relative_to(base_dir)})" for s in skills]
    return "Available skills (read a skill's path with the Read tool):\n" + "\n".join(lines)
```

- `load_skills` は、`SKILL.md` ファイルをスキャンし、カタログの前部分のみを保持します。
- `catalog_prompt` は、そのカタログを system prompt にレンダリングし、skill ごとに 1 行、読み取りパスを指定します。
- 本体とリソースはプレーンファイルです。通常の読み取りツールはオンデマンドでそれらをロードするため、スキル固有のツールはありません。
- 読み取りツールのスコープは skills ディレクトリであるため、skill 名がファイル システムにエスケープされることはありません。

### 新機能: ストアは進化します

読み込みは skill システムの半分です。店舗も成長と衰退を繰り返します（エルメスではこれをskillの進化と呼んでいます）。

成長とは書くことだ。エージェントは、完了したワークフローを新しい skill に抽出するため、次回の実行では命令を再検出するのではなく、命令をロードします。

```python
def write_skill(skills_dir, name, description, body) -> Path:   # src/skills.py
    base = Path(skills_dir).resolve()
    target = (base / name / "SKILL.md").resolve()
    if not target.is_relative_to(base):              # a name can never escape the skills dir
        raise ValueError(f"skill name {name!r} escapes the skills dir")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(f"---\nname: {name}\ndescription: {description}\n---\n{body}\n")
    return target
```

- `WriteSkill` は、この機能に関するモデル対応ツールです。 skill の書き込みは副作用であるため、ルールで事前承認されていない限り、セクション 3 のゲートが要求します。
- ・書き込まれたファイルは通常の`SKILL.md`です。特別なマークは何もありません。次の `load_skills` スキャンでは、手書きの skill のようにカタログ化されます。
- 名前は、`read_tool` がパスをチェックするのと同じ方法で解決およびチェックされるため、ストアはどちらの方向からもエスケープできません。

減衰は測定から始まります。 skill のロードは使用シグナルであるため、`read_tool` は読み取りの副作用としてそれを記録します。

```python
if target.name == "SKILL.md":                # inside read_tool's read()
    record_use(base, target.parent.name)     # loading a skill counts as use
```

```python
def record_use(skills_dir, name, now=None) -> dict:
    path = Path(skills_dir) / USAGE_FILE     # .usage.json, one record per skill
    usage = json.loads(path.read_text()) if path.exists() else {}
    entry = usage.setdefault(name, {"uses": 0})
    entry["uses"] += 1
    entry["last_used_at"] = now if now is not None else time.time()
    path.write_text(json.dumps(usage))
    return entry

def stale_skills(skills_dir, skills, now=None, stale_after=STALE_AFTER) -> list[str]:
    usage = ...                                  # load .usage.json, default {}
    return [s.name for s in skills
            if now - usage.get(s.name, {}).get("last_used_at", 0) >= stale_after]
```

- skill のフォルダー名のレコード キー。モデルが読み取ったパスから取得されます。リソースの読み取り (L3) ではバンプされず、`SKILL.md` ボディ (L2) のみがバンプされます。
- レコードのない skill には `last_used_at` 0 があるため、一度も使用されていない skills も古いものとしてカウントされます。
- `stale_skills` はアクションではなくレポートです。それをどうするかを決めるのはキュレーターの仕事です。 Hermes は、同じシグナル (アーカイブ、統合、ピン) でバックグラウンド キュレーター エージェントを実行します。
- データ フローは実行間のループです。バンプ `.usage.json` を読み取り、キュレーターがそれを読み取り、生き残ったものがカタログに反映され、`WriteSkill` が新しいエントリをフィードします。

### 統合方法

ループは変わりません。 skill を読み取ると、`messages[]` に入る tool result が返されます。

カタログは system prompt に属します。本文は、モデルがファイルを読み取った後にのみ会話に入ります。リソース ファイルは、後で必要な場合にのみ読み取られます。

ロードされた skill テキストは `messages[]` に存在するため、コンテキストがいっぱいになったときに他のメッセージと同様に圧縮できます (セクション 8)。
skill 本体を短くし、大きな参照用のファイルをポイントします。

### さらに読む

これは `src/` にはありません。これは ai-agent-book およびベンダーのドキュメントからのものであり、表内のシステムについては確認されていません。

**カタログの価格** Progressive disclosure は、大規模な skill ストアのコストを削減します。無料になるわけではありません。
カタログはプレフィックス内に存在するため、プレフィル時に 1 回読み取られ、その後はターンごとに再送信されます。
最初のターンの後、そのプレフィックスはキャッシュされるため、再送信のコストは低くなります。
ロードされたボディはコストが高くなりますが、何かがボディを圧縮するまでウィンドウ内に留まります。
したがって、注目すべき数値は、本体が読み取られる頻度ではなく、カタログに含まれる skills の数です。

**カタログが存在する場所。** リストは system prompt に配置できます。これは、`src/` が行うことです。
1 つのアクティベーション ツールの説明内に含めることもできます。オープンスタンダードでは両方が許可されています。
トレードオフは、トークンがどこに着地するかです。 system prompt では、これらはすべてのセッションのプレフィックスの一部です。
ツールの説明では、プレフィックスは小さいままであり、代わりにモデルはそのツールを通じてリストに到達します。

**ツールの読み込みの遅延。** ツールは同じ理由で同じパターンを使用できます。スキーマが大きく、ほとんどのターンではスキーマが必要ありません。
プレフィックスには、ツール名と 1 行の説明のみが保持されます。モデルは、必要な場合に完全なスキーマを要求します。
このスキーマはコンテキストの最後に追加されるため、キャッシュされたプレフィックスは変更されず、再計算する必要はありません。
Skills は、このリポジトリが progressive disclosure と最初に出会った場所でした。この本では、同じパターンがツール層に移行していると報告しています (セクション 2)。

**skill を記述する場合。** 実行によって長いワークフローが初めて正しく終了したとします。それは skill になるべきでしょうか?
ここのデモは「はい」と言っています。ワークフローが終了し、エージェントが `WriteSkill` を呼び出し、次のスキャンでカタログに登録されます。
これはループを示す最小のルールであり、実行可能なコードが行うことです。

**この本のハードルは高いです。** この本ではノーと言っています。1 回の実行では証拠が少なすぎるからです。
skill が正式な機能になる前に、次の 4 つのことが求められます。

- 失敗しなかった少なくとも 2 回の実行で同じパターン。
- skill を提案した実行から得られたものではない小切手。 Voyager は次のように動作します。環境がそれを確認した後、skill がライブラリに入ります。
- まずはお店探し。近いものがすでに存在する場合は、重複を追加する代わりにパッチを適用します。
- うまくいったパスだけでなく、ランがぶつかった落とし穴も体に残しました。

**どちらのルールを選択するか。** 答えが異なるため、両方とも擁護可能です。
最初の成功はメカニズムを教え、デモを短くします。サポートしきい値は、数百のストアが一度使用されたノートでいっぱいになるのを防ぐものです。
候補ステップはそれらの間にあります。抽出されたワークフローは、カタログ エントリとしてではなく、候補として表示されます。
昇進前に起草、テスト、評価、改訂が行われます。 Anthropic の Skill Creator がそのループを実行します。
このセクションのコードでは、キュレーターがプロモートするまで `load_skills` がスキップするステージング フォルダーになります。

**統合はオフラインで実行されます。** キュレーターは、ライブ パスではなく、スケジュールされたパスです。この本ではこれを睡眠時間学習と呼び、次の 5 つのステップを示しています。

1. **トリガー** スケジュール、アイドル状態のウィンドウ、またはサイズ制限を超えたストア。
2. **方向性** 最初にストアのスナップショットを作成し、後のすべてのステップをロールバックできるようにします。
3. **収集してマージします。** 使用記録と最近の実行を読み取ります。ほぼ重複したものを 1 つの skill に折りたたみます。候補者を引き込みます。
4. **検証して承認します。** マージされたボディを、それを生成した実行と比較して確認します。失敗したものはそのまま残ります。
5. **プルーニングとインデックス付け。** 古い skills を一定のルールに従ってアーカイブし、カタログを再構築します。

**オフラインが重要な理由** キュレーターをオフラインで実行すること自体が安全境界線です。オンライン ループが実行され、記録されます。
実行中にストアを編集することはありません。つまり、一度の幸運な走行がそれ自体を宣伝することはできません。
また、エージェントが外部から読み取ったテキストは、ターン間の永続的な指示になることはできません。

---

## システムごと

各エージェントが skills をどのように記述し、トリガーし、検出するか。

| | Claude Code | Hermes Agent | deepseek-harness |
| --- | --- | --- | --- |
| **長所** |予算に合わせて。 skill はツールをフォークおよびスコープできます。 |キュレーターは新しい skills をマージし、古いものはアーカイブします。 |カタログは履歴を反映し、変更されると更新されます。 |
| **短所** |曖昧な説明は skills を隠します。 |自動変更にはピンと段階的な承認が必要です。 |カタログを書き換えると、履歴にメッセージが追加されます。 |
| **理由** | Skills フォーク ツールとスコープ ツールがあるため、ファイルの読み取りだけでは十分ではありません。 |読み込みは仕事の半分です。店は成長し衰退しなければなりません。 | Skills はセッションの実行中に変更されます。 |
| **方法: skill 形式** | `SKILL.md` フォルダー;フロントマターによりツールが制限される可能性があります。 |同じ形状をカテゴリフォルダーに分類します。 |バンドルまたはフラット ファイル。 Frontmatter は、呼び出し可能なユーザーを設定します。 |
| **方法: トリガーをロード** | `Skill` 呼び出しは本体を挿入します。ファイルの一致でも起動されます。 | `skill_view` は本体を返し、使用カウントをバンピングします。 |ツールは要求に応じて本文を再読み取りします。 |
| **方法: 発見** |組み込み、ユーザー、プロジェクト、plugin、MCP ソース。 |バンドル、オプション、ユーザー、plugin、およびハブ ソース。 |プロバイダーは、階層化されたスコープとランク付けされたルートをマージします。 |

---

## 障害モード

- **Skill は起動しません。** 説明が曖昧すぎます。トリガー形式の説明を書きます。
- **カタログが大きすぎます。** skills が多すぎると、プロンプトが混雑する可能性があります。 skills に焦点を当てたままにして、ローダーをトリミングさせます。
- **圧縮後にボディが失われます。** skill ファイルを再読み込みするか、ボディを短くしてください。
- **パス トラバーサル。** カタログはモデルにパスを渡します。読み取りツールのスコープを skills ディレクトリに設定し、`../` がエスケープできないようにします。
- **フォークされた skill はライブ コンテキストを失います。** フォークされた skills は自己完結型の作業にのみ使用してください。
- **サプライ チェーンからの有害な skill。** インストールされたサードパーティの skill は外部コンテンツですが、命令としてロードされます。
  カタログがすでにそれを保証しているため、毒された Web ページよりもリーチが広がります。
  インストールする前に、本体とバンドルされているスクリプトを読んでください。バージョンを固定します。アップデート時に再度見直します。
- **挿入されたテキストは永続的になります。** `messages[]` のプロンプト挿入はセッションとともに終了します。 `SKILL.md` に書き込まれた同じテキストが、以降の実行ごとにロードされます。
  したがって、未レビューの外部コンテンツが `WriteSkill` に到達しないようにしてください。別のパスで承認されるまで、新しい skills を候補者として保持します。
  skill にその承認ゲートを編集させないでください。
- **使用カウントの過大学習。** skill のロードはそれに従っていません。ロードカウントは、カタログがルーティングされたことを示します。
  skill が結果を変えたとは言っていません。代わりに 2 つの数値を追跡します。skill が発火したかどうか、および走りが良くなったかどうかです。

---

## 実行可能

[`src/`](src/) 06 を前方に繰り上げて次を追加します。

- [`skills.py`](src/skills.py): カタログ スキャン、システム プロンプト リスト、パス スコープの `Read` ツール、および進化の半分 (`WriteSkill`、`record_use`、 `stale_skills`)。
- `skills/<name>/SKILL.md`: サンプル skills (リソース ファイルを含むものを含む)。
- [`loop.py`](src/loop.py): skill のロードは単なるファイルの読み取りであるため、変更されません。
- [`test.py`](src/test.py): カタログ スキャン、プロンプト リスト、ファイル ロード、パス トラバーサル拒否、使用量の増加、古さ、およびカタログに入るエージェント作成の skill をチェックします。
- [`demo.py`](src/demo.py): エージェントは skill を使用し、新しいものを保存します。閉店時のスキャンは店舗が成長したことを示しています。

```bash
python sections/07-skills/src/test.py         # offline checks, no key
uv run python sections/07-skills/src/demo.py  # live demo, needs a key
```

---

## ソース

- [Claude Code ソース](https://github.com/yasasbanukaofficial/claude-code):
  `skills/loadSkillsDir.ts`、`skills/bundledSkills.ts`、`skills/mcpSkillBuilders.ts`、`tools/SkillTool/SkillTool.ts`、`tools/SkillTool/prompt.ts`。
- [Hermes Agent ソース](https://github.com/NousResearch/hermes-agent):
  `tools/skills_tool.py` (`skills_list`、`skill_view`)、`tools/skill_usage.py`、`hermes_cli/curator.py`、`tools/skills_hub.py`、 `tools/skills_ast_audit.py`。
- [deepseek-harness ソース](https://github.com/deepseek-ai/deepseek-harness) `dsh-v0.1.0-rc.7`:
  `packages/skill/skill/src/index.ts`、`packages/skill/skill-filesystem/src/index.ts`、`packages/skill/tool-skill/src/index.ts`、
  `docs/subsystems/skills.md`、`docs/tool-catalog.md`。
- [Anthropic Agent Skills ベスト プラクティス](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices): progressive disclosure レベル。
- [learn-claude-code · s07_skill_loading](https://github.com/shareAI-lab/learn-claude-code): セクションのフレーム化。
- [ai-agent-book](https://github.com/bojieli/ai-agent-book): `book/chapter2.md`、`book/chapter8.md`、中国語オリジナルの正規版。
- [エージェント Skills オープン スタンダード](https://agentskills.io): カタログの配置、system prompt、またはアクティベーション ツールの説明。
- [Claude Code · prompt caching](https://code.claude.com/docs/en/prompt-caching): ロードされた skill ボディが着地する場所とそのコスト。
- [Voyager](https://arxiv.org/abs/2305.16291): skill は、環境が検証した後にのみライブラリに入ります。
- [Anthropic Skill Creator](https://github.com/anthropics/skills): 昇格前にドラフト、テスト、評価、修正します。
- Lin et al.、[arXiv:2605.30621](https://arxiv.org/abs/2605.30621)、書籍より: 更新が適用されるかどうかと、更新が役立つかどうかは別の測定です。
