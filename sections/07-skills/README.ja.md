# 7 · Skills

[English](README.md) · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · **日本語** · [한국어](README.ko.md)

> skill は専門知識を自己完結の形にまとめた束です。手順に加えてスクリプトやファイルを含み、タスクが必要としたときだけ読み込まれます。

skill は、汎用の agent を 1 つの仕事の専門家に変えます。
まとめるのはワークフローです。従うべき手順に加えて、実行するスクリプトと参照する資料ファイルが入ります。
agent は必要なときだけ skill を読み込むので、1 つの agent が多くの専門能力に手を伸ばせます。最初から全部を読み込む必要はありません。

skill は `SKILL.md` を 1 つ置いたフォルダです。frontmatter がその skill の名前と説明を持ちます。
本文には手順が入り、フォルダには追加のスクリプトや資料ファイルを同梱できます。これらは skill がそれを使うときだけ読み込まれます。

agent は skill が存在することを知る必要があります。ただし、毎 turn すべての skill の本文の分を支払うべきではありません。

skill のシステムに必要なことは次の 4 つです。

1. 使える skill を安価に一覧すること。
2. 選ばれた skill の手順だけを、そのとき読み込むこと。
3. skill から追加ファイルを指し示せること。ただし自動では読み込まないこと。
4. 組み込み、ユーザー、プロジェクト、plugin、MCP という各ソースから skill を見つけること。

このレイヤがないと、prompt が大きくなりすぎるか、agent が自分の拡張を見つけられなくなります。

---

## 仕組み

![Mechanism diagram](assets/07-skills.png)

skill は段階的な開示を使います。モデルには、これ以上読み込むかどうかを判断できるだけの情報しか見せません。

1. **メタデータ。** frontmatter の `name` と `description`、それに skill のパスです。この安価なカタログは毎 turn の system prompt に載ります。
2. **手順。** `SKILL.md` の本文です。モデルは、タスクがその skill を必要としたときだけこのファイルを読みます。
3. **リソース。** skill フォルダの追加ファイルです。手順がそれらを指し示したとき、モデルは同じファイル用の tool で読みます。

skill 専用の tool は要りません。カタログが各 skill の名前とパスを示していれば、
agent は通常の Read tool でそのファイルを読むだけで skill を読み込めます。L2 も L3 も、どちらもただのファイル読み込みです。

仕事の大半を担うのは description です。これは要約ではなく、振り分けの条件です。
モデルが判断する前に見えるのは、その 1 行だけです。だから、どんなときに使うのか、どんなときに使わないのかを書きます。
反例を 1 つ添えます。話題を挙げるだけの行では、モデルは当て推量をします。

### 本節の追加: skill を走査して prompt に一覧する

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

- `load_skills` は `SKILL.md` を走査し、カタログ用に frontmatter だけを保持します。
- `catalog_prompt` はそのカタログを system prompt に展開します。skill ごとに 1 行、読むべきパス付きです。
- 本文とリソースはただのファイルです。通常の Read tool が必要に応じて読み込むので、skill 専用の tool はありません。
- Read tool は skills ディレクトリに範囲を限定しているので、skill 名がファイルシステムへ抜け出すことはありません。

### 本節の追加: store が進化する

読み込みは skill システムの半分にすぎません。store も成長し、そして衰えます (Hermes はこれを skill evolution と呼びます)。

成長は書き込みです。agent は完了したワークフローを蒸留して新しい skill にします。次の実行は、手順を発見し直す代わりに読み込むだけで済みます。

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

- `WriteSkill` は、この関数をモデル側に見せる tool です。skill を書くのは副作用なので、ルールで事前承認されていない限り、セクション 3 のゲートが確認を求めます。
- 書き出されるのは通常の `SKILL.md` です。特別な印は付きません。次の `load_skills` の走査が、手書きの skill と同じようにカタログに載せます。
- 名前は `read_tool` がパスを検査するのと同じ手順で解決し検査します。したがって store はどちらの方向からも抜け出せません。

衰えは測定から始まります。skill を読み込むことが使用のシグナルなので、`read_tool` が読み込みの副作用としてそれを記録します。

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

- 記録の鍵は skill のフォルダ名で、モデルが読んだパスから取ります。リソースの読み込み (L3) では増えず、`SKILL.md` の本文 (L2) だけが増やします。
- 記録のない skill は `last_used_at` が 0 なので、一度も使われていない skill も古いものとして数えられます。
- `stale_skills` は報告であって、動作ではありません。それをどう扱うかを決めるのは管理役の仕事です。Hermes は同じシグナルでバックグラウンドの管理役 agent を走らせます (アーカイブ、統合、固定)。
- データの流れは実行をまたぐ loop になります。読み込みが `.usage.json` を増やし、管理役がそれを読み、カタログが生き残ったものを映し、`WriteSkill` が新しい項目を流し込みます。

### 既存の構成への組み込み

loop は変わりません。skill を読むと tool result が返り、それが `messages[]` に入ります。

カタログは system prompt に置きます。本文が会話に入るのは、モデルがファイルを読んだ後だけです。リソースのファイルは、必要になったときだけ後から読まれます。

読み込まれた skill のテキストは `messages[]` にあるので、context が埋まれば他の message と同じように compaction の対象になります (セクション 8)。
skill の本文は短く保ち、大きな参照資料はファイルを指し示します。

### さらに読む

ここに書くことは `src/` にはありません。ai-agent-book とベンダーのドキュメントに基づくもので、表にあるシステムで確認が取れているわけではありません。

**カタログの費用。** 段階的な開示は、大きな skill の store の費用を下げます。ただの 0 にするわけではありません。
カタログは prefix に置かれるので、prefill で一度読まれ、その後は毎 turn 送り直されます。
最初の turn の後はその prefix がキャッシュされるので、送り直しは安く済みます。
読み込んだ本文はもっと費用がかかり、何かが compaction するまで window に残ります。
だから見るべき数字は、本文が読まれる頻度ではなく、カタログが載せている skill の数です。

**カタログの置き場所。** 一覧は system prompt に置けます。`src/` はそうしています。
起動用の tool 1 つの description の中に置くこともできます。オープンな標準はどちらも認めています。
違いは token がどこに落ちるかです。system prompt に置けば、すべての session の prefix の一部になります。
tool の description に置けば prefix は小さいままで、モデルはその tool を通じて一覧に届きます。

**tool の遅延読み込み。** tool も同じやり方を使えます。理由も同じで、スキーマは大きく、ほとんどの turn では要らないからです。
prefix には tool の名前と 1 行の説明だけを置きます。モデルは必要になったときに完全なスキーマを求めます。
そのスキーマは context の末尾に追加されるので、キャッシュ済みの prefix には触れず、その前にあるものを再計算する必要もありません。
このリポジトリが段階的な開示に最初に出会ったのは skill でした。同じやり方が tool のレイヤにも移りつつあると本は報告しています (セクション 2)。

**skill をいつ書くか。** ある実行が、長いワークフローを初めて正しく完了したとします。それを skill にすべきでしょうか。
ここでのデモは「する」と答えます。ワークフローが終わると agent が `WriteSkill` を呼び、次の走査がそれをカタログに載せます。
これは loop を見せる最小のルールであり、実行できるコードがやっていることです。

**本はもっと高い基準を示す。** 本は「しない」と答えます。1 回の実行では証拠が足りないからです。
skill が正式な能力になる前に、4 つのことを求めます。

- 失敗しなかった実行が少なくとも 2 回あり、そこに同じパターンが出ていること。
- その skill を提案した実行の外から来る検査。Voyager はこのやり方です。環境が確認してから skill がライブラリに入ります。
- まず store を検索すること。近いものがすでにあるなら、重複を足さずにそれを直します。
- その実行がはまった落とし穴を本文に残すこと。うまくいった道筋だけではありません。

**どちらのルールを取るか。** どちらにも根拠があります。答えている問いが違うからです。
初回の成功は仕組みを教え、デモを短く保ちます。回数のしきい値は、数百の store が一度きりのメモで埋まるのを止めます。
その間に候補という段階を挟めます。蒸留したワークフローは、カタログの項目ではなく候補として着地します。
そこから草案、テスト、評価、修正を経て昇格します。Anthropic の Skill Creator はこの loop を回します。
このセクションのコードで言えば、管理役が昇格させるまで `load_skills` が読み飛ばす待機用フォルダにあたります。

**統合はオフラインで走る。** 管理役は定期実行の処理であって、実行中のものではありません。本はこれを sleep-time learning と呼び、5 つの手順を挙げます。

1. **起動。** schedule、待機時間、またはサイズの上限を超えた store。
2. **把握。** まず store のスナップショットを取り、後のどの手順も巻き戻せるようにします。
3. **収集と統合。** 使用の記録と最近の実行を読みます。ほぼ重複するものを 1 つの skill にまとめます。候補を取り込みます。
4. **検証と承認。** 統合した本文を、それを生んだ実行と突き合わせて検査します。通らなかったものは入れません。
5. **整理と索引付け。** 決められたルールで古い skill をアーカイブし、カタログを作り直します。

**なぜオフラインが重要か。** 管理役をオフラインで走らせること自体が安全の境界になります。オンラインの loop は実行し、記録します。
実行の途中で store を編集することはありません。だから、たまたまうまくいった 1 回の実行が自分を昇格させることはできず、
agent が外から読んだテキストが turn の間に恒久的な指示になることもありません。

---

## システム別

各 agent が skill をどう説明し、どう起動し、どう見つけるか。

| | Claude Code | Hermes Agent | deepseek-harness |
| --- | --- | --- | --- |
| **利点** | 予算に収まる。skill は fork でき、tool を絞れる。 | 管理役が新しい skill を統合し、古いものをアーカイブする。 | カタログが履歴に載り、変わったときに更新される。 |
| **欠点** | 曖昧な description は skill を埋もれさせる。 | 自動の変更には固定と段階的な承認が要る。 | カタログの書き直しが履歴に message を増やす。 |
| **理由** | skill は fork して tool を絞るので、ファイル読み込みだけでは足りない。 | 読み込みは仕事の半分。store は成長も衰えもしなければならない。 | session の実行中にも skill は変わる。 |
| **方法: skill の形式** | `SKILL.md` のフォルダ。frontmatter で tool を制限できる。 | 同じ形で、カテゴリ別のフォルダに整理される。 | 束またはフラットなファイル。frontmatter が誰から呼べるかを決める。 |
| **方法: 読み込みの起動** | `Skill` の呼び出しが本文を注入。ファイルの一致でも発火する。 | `skill_view` が本文を返し、使用回数を増やす。 | 要求に応じて tool が本文を読み直す。 |
| **方法: 発見** | 組み込み、ユーザー、プロジェクト、plugin、MCP のソース。 | 同梱、任意、ユーザー、plugin、ハブのソース。 | 提供側が階層化されたスコープと順位付けされたルートをまたいで統合する。 |

---

## 失敗モード

- **skill が発火しない。** description が曖昧すぎる。起動条件の形で description を書く。
- **カタログが大きくなりすぎる。** skill が多すぎると prompt を圧迫する。skill を絞り込み、ローダーに削らせる。
- **compaction の後に本文が失われる。** skill のファイルを読み直すか、本文を短く保つ。
- **パスの踏み越え。** カタログはモデルにパスを渡す。Read tool を skills ディレクトリに限定し、`../` で抜け出せないようにする。
- **fork した skill が現在の context を失う。** fork する skill は自己完結の作業にだけ使う。
- **供給経路から入り込む汚染された skill。** 導入した第三者の skill は外部のコンテンツだが、指示として読み込まれる。
  カタログがすでにそれを保証しているので、汚染された web ページより届く範囲が広い。
  導入前に本文と同梱スクリプトを読む。バージョンを固定する。更新のたびにもう一度確認する。
- **注入されたテキストが恒久化する。** `messages[]` への prompt injection は session とともに消える。同じテキストが `SKILL.md` に書かれると、以後すべての実行で読み込まれる。
  だから、未確認の外部コンテンツを `WriteSkill` に届かせてはいけない。新しい skill は、別の処理が承認するまで候補として保留する。
  その承認のゲートを skill に編集させてはいけない。
- **使用回数が学習を過大に見せる。** skill を読み込むことは、それに従うことではない。読み込み回数が示すのは、カタログが振り分けたことだけ。
  skill が結果を変えたかどうかは示さない。代わりに 2 つの数字を追う。skill が発火したか、そして実行が良くなったか。

---

## 実行

[`src/`](src/) は 06 を引き継ぎ、次を追加します。

- [`skills.py`](src/skills.py): カタログの走査、system prompt への一覧、パスを限定した `Read` tool、そして進化の側 (`WriteSkill`、`record_use`、`stale_skills`)。
- `skills/<name>/SKILL.md`: 見本の skill。リソースファイル付きのものを含む。
- [`loop.py`](src/loop.py): skill の読み込みはただのファイル読み込みなので変更なし。
- [`test.py`](src/test.py): カタログの走査、prompt への一覧、ファイルの読み込み、パスの踏み越えの拒否、使用回数の増加、古さの判定、agent が書いた skill がカタログに入ることを検査。
- [`demo.py`](src/demo.py): agent が skill を使い、その後で新しい skill を保存する。最後の走査で store が増えたことが分かる。

```bash
python sections/07-skills/src/test.py         # offline checks, no key
uv run python sections/07-skills/src/demo.py  # live demo, needs a key
```

---

## 出典

- [Claude Code source](https://github.com/yasasbanukaofficial/claude-code):
  `skills/loadSkillsDir.ts`, `skills/bundledSkills.ts`, `skills/mcpSkillBuilders.ts`, `tools/SkillTool/SkillTool.ts`, `tools/SkillTool/prompt.ts`.
- [Hermes Agent source](https://github.com/NousResearch/hermes-agent):
  `tools/skills_tool.py` (`skills_list`, `skill_view`), `tools/skill_usage.py`, `hermes_cli/curator.py`, `tools/skills_hub.py`, `tools/skills_ast_audit.py`.
- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness) at `dsh-v0.1.0-rc.7`:
  `packages/skill/skill/src/index.ts`, `packages/skill/skill-filesystem/src/index.ts`, `packages/skill/tool-skill/src/index.ts`,
  `docs/subsystems/skills.md`, `docs/tool-catalog.md`.
- [Anthropic Agent Skills best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices): progressive disclosure levels.
- [learn-claude-code · s07_skill_loading](https://github.com/shareAI-lab/learn-claude-code): section framing.
- [ai-agent-book](https://github.com/bojieli/ai-agent-book): `book/chapter2.md`, `book/chapter8.md`, Chinese original canonical.
- [Agent Skills open standard](https://agentskills.io): catalog placement, system prompt or activation-tool description.
- [Claude Code · prompt caching](https://code.claude.com/docs/en/prompt-caching): where a loaded skill body lands and what it costs.
- [Voyager](https://arxiv.org/abs/2305.16291): a skill enters the library only after the environment verifies it.
- [Anthropic Skill Creator](https://github.com/anthropics/skills): draft, test, evaluate, revise before promotion.
- Lin et al., [arXiv:2605.30621](https://arxiv.org/abs/2605.30621), via the book: whether an update lands and whether it helps are separate measurements.
