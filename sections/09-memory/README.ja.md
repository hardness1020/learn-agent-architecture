# 9 · Memory

[English](README.md) · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · **日本語** · [한국어](README.ko.md)

> 長く残る事実を、会話の外に保存します。

`messages[]` は 1 回の実行のための memory です。実行とともに終わり、実行中に compaction されることもあります。

長期の memory はそれとは違います。長く残る事実を会話の外に保存し、後の turn で関係するものだけを recall します。

memory に必要なことは次の 4 つです。

1. 何を保存する価値があるかを判断すること。
2. それを会話の外に書くこと。
3. 関係するものだけを recall すること。
4. 古い項目や重複した項目を、時間をかけて片付けること。

memory がないと、agent は同じ質問を繰り返し、session をまたいでユーザーの好みを忘れます。何もかも保存すれば、recall は雑音まじりになり、内容も古びます。

このセクションは最小の loop を作ります。本番の規模では、memory はそれ自体が 1 つのサブシステムに育ちます。
イベントログ、型付きの記録、時間つきの事実、ハイブリッドな検索を伴います。[learn-agent-memory](https://github.com/hardness1020/learn-agent-memory) のリポジトリがそれを扱います。

---

## 仕組み

![Mechanism diagram](assets/09-memory.png)

memory は、ファイルの store と index と、必要に応じた recall の組み合わせです。

loop は store 全体を読みません。安価な index を読み、いまのクエリに合う数個の memory ファイルだけを読み込みます。

そこで問題は、ファイルがどう見つかるかになります。recall はファイル 1 件につき index の 1 行を順位付けします。本文を開くのは、ファイルが選ばれた後だけです。
index の行が唯一の入口です。

操作は 4 つあります。

- **選別** は何を保存するかを決めます。grep や git やプロジェクトのファイルからは導き直せない事実を保存します。
- **recall** はクエリの時点で走ります。既存の memory を順位付けし、選ばれた本文だけをその turn の `messages[]` に注入します。
  ユーザーのテキストの前に置く `<system-reminder>` ブロックとして入れます。
- **抽出** は実行の終わりに走ります。新しい memory ファイルを書きます。
- **統合** はまれに走ります。重複をまとめ、古い項目を削ります。

recall は読み、抽出は書きます。この向きを分けておくと、store が意図せず膨らむのを避けられます。

### 本節の追加: index、recall、抽出、そして store

store は `.md` ファイルのディレクトリです。`load_index` は frontmatter だけを読みます。

```python
def load_index(memory_dir) -> list[Memory]:            # src/memory.py
    mems = []
    for md in sorted(Path(memory_dir).glob("*.md")):
        if md.name == "MEMORY.md":                     # the index file is not a memory
            continue
        meta, _body = _split(md.read_text())           # frontmatter only, never the body
        mems.append(Memory(md.stem, meta.get("type", ""), meta.get("description", ""), md))
    return mems

def manifest(mems) -> str:                             # one cheap line per memory
    return "\n".join(f"- {m.name} ({m.type}): {m.description}" for m in mems)
```

recall は index をクエリと突き合わせて順位付けします。オフラインではデモは単語の重なりを使います。ライブでは、選択器が memory の名前を選べます。

```python
def recall(mems, query, k=RECALL_K, selector=None) -> list[Memory]:
    if selector is not None:
        chosen = set(selector(manifest(mems), query))  # live: an LLM returns names to inject
        return [m for m in mems if m.name in chosen][:k]
    scored = ((_overlap(query, m), m) for m in mems)
    hits = sorted((s for s in scored if s[0]), key=lambda s: s[0], reverse=True)
    return [m for _score, m in hits[:k]]
```

store を増やす操作は抽出だけです。

```python
def extract(memory_dir, messages, extractor) -> list[Path]:
    written = []
    for m in extractor(messages) or []:
        path = Path(memory_dir) / f"{m['name']}.md"
        path.write_text(_render(m))
        written.append(path)
    return written
```

上の memory のディレクトリは蒸留した事実を持ちますが、store はそれだけではありません。加工前の履歴も 2 つめの store になります。実行ごとのテキストをログに書き、後からキーワードで検索します。
ログはすべてを残すので、抽出が見落とした事実も見つけられます。Hermes の `state.db` はこの設計です。

`log_run` は実行の終わりに、各実行のテキストを SQLite の FTS5 テーブルに追記します。

```python
def log_run(db_path, session_id, messages) -> int:     # src/memory.py
    rows = [(session_id, m["role"], t) for m in messages if (t := _text_of(m))]
    con = _db(db_path)                                  # CREATE VIRTUAL TABLE ... USING fts5
    con.executemany("INSERT INTO session_log VALUES (?, ?, ?)", rows)
    con.commit()
    con.close()
    return len(rows)
```

- `_text_of` は 1 つの message を検索できるテキストに平坦化します。素の文字列はそのまま通り、API の応答はテキストのブロックだけを残します。tool use のブロックはテキストを持たないので落ちます。
- 各行は `(session_id, role, content)` です。session id は系統の鍵なので、ヒットがどの過去の実行から来たかを示せます。
- FTS5 は CPython の `sqlite3` に同梱されているので、このログに依存は増えません。

`search_sessions` はログを読み戻します。順位付きで、モデル呼び出しはありません。

```python
def search_sessions(db_path, query, k=SEARCH_K) -> list[tuple]:
    words = _words(query)                               # the same tokenizer recall uses
    if not words or not Path(db_path).exists():
        return []
    con = _db(db_path)
    rows = con.execute("SELECT session_id, role, content FROM session_log "
                       "WHERE session_log MATCH ? ORDER BY rank LIMIT ?",
                       (" OR ".join(sorted(words)), k)).fetchall()
    con.close()
    return rows
```

- クエリの単語は `OR` でつなぐので、どの単語でもヒットします。`ORDER BY rank` (bm25) が最良の一致を先頭に置きます。
  これはあいまいな順位付けを伴うキーワードの recall で、Hermes の `session_search` と同じ形です。
- `k` が返す行数を抑えます。`RECALL_K` が注入する memory を抑えるのと同じ理由で、量より精度を取ります。
- `search_tool` はこれを読み取り専用の `SessionSearch` tool として包みます。だから過去の session を参照するかどうかはモデルの判断で、turn の途中で下されます。
  抽出済み memory の recall は harness の判断のままで、turn の前に下されます。2 つの経路は、誰が引き金を引くかが違います。

`Store` は loop が使う取っ手で、実行の終わりに両方の store へ流し込むようになりました。

```python
def write(self, messages) -> list[Path]:               # Store.write, called at run end
    if self.db is not None:
        log_run(self.db, self.session_id, messages)     # everything, searchable later
    return extract(self.root, messages, self.extractor) if self.extractor else []   # the distilled few
```

選択器、抽出器、session の db はすべて任意なので、テストはオフラインで走ります。

### 既存の構成への組み込み

memory は loop の両端を包みます。

```python
if memory is not None:                                 # before the loop
    user_text = messages[-1]["content"]
    recalled = memory.recall(user_text)
    if recalled:
        messages[-1]["content"] = f"<system-reminder>\n{recalled}\n</system-reminder>\n\n{user_text}"
...
if response.stop_reason != "tool_use":
    if memory is not None:
        memory.write(messages)                         # run ends: extract
    return final_text(response)
```

- recall は turn の前に一度走り、選ばれた memory のテキストを注入します。
- 抽出は、モデルが次の tool call なしで止まったときに走ります。
- `memory=None` にすると、セクション 8 の loop の振る舞いのままです。
- recall されたテキストは `messages[]` に入るので、後から context management が compaction できます。

### さらに読む

`src/` はフラットなディレクトリを 1 つ持ち、それを glob します。だから全ファイルがただで index に載ります。glob しきれないほど大きな store では、その到達性を自分で取り戻す必要があります。それを担う部品が 3 つあります。

- **書かれた index。** 保守された 1 つのファイルがすべての memory を並べるので、recall はツリーを歩かずに順位付けの対象を得られます。
- **memory どうしのリンク。** ファイルが関連するファイルを指すので、recall はすでに読み込んだファイルからリンクをたどれます。
- **階層化された要約。** 各層が 1 つ下の層を要約するので、読み込みはクエリが必要とする深さで止められます。

OpenViking の知識の store はこの 3 つを組み合わせ、すべてのファイルに URI を与えます。これらはどれも `src/` にはありません。

---

## システム別

各 agent が memory をどう保存し、recall し、抽出し、統合するか。

| | Claude Code | Hermes Agent |
| --- | --- | --- |
| **利点** | recall の関連性の判断がキーワードより優れる。バックグラウンドの task が store を掃除する。 | memory が prompt に載り、キャッシュが温まったまま。検索にモデル呼び出しが要らない。 |
| **欠点** | recall のたびにモデル呼び出しが増える。統合には専用のゲートが要る。 | キーワードの recall は精度が落ちる。新しい書き込みが prompt に届くのは次の session。 |
| **理由** | 何もかも保存すると recall が雑音まじりになるので、選択器がわずかな memory だけを注入する。 | 抽出は事実を取りこぼすので、2 つめの store として加工前の履歴を検索可能に残す。 |
| **方法: store** | frontmatter 付きの markdown ファイル。MEMORY.md は index であり、memory の本文ではない。 | markdown ファイル 2 つ (観測、ユーザーのプロフィール) と SQLite の session ログ。 |
| **方法: recall** | モデルが index を読み、最大 5 件を選ぶ。本文には鮮度の注記が付く。 | session 開始時に固定した prompt のスナップショットと、過去の session のキーワード検索。 |
| **方法: 抽出** | fork した agent が実行の終わりに新しい memory ファイルを書く。 | memory の tool が session の途中でディスクに項目を書く。書き込みは承認待ちにできる。 |
| **方法: 統合** | バックグラウンドの task がまとめて削る。時間、session 数、ロックでゲートされる。 | 文字数の上限を超えたときにモデルが書き直す。失敗を追跡する。 |

---

## 失敗モード

- **recall が有用な memory を取り逃がす。** 選択器を調整し、description を具体的に保つ。
- **recall が turn を埋め尽くす。** 注入する memory の数に上限を設け、精度を優先する。
- **古い memory が事実として扱われる。** 経過時間や鮮度のメタデータを含める。
- **store が雑音まじりになる。** 重複と矛盾を統合する。
- **導き直せる事実を保存する。** grep、git、ソースファイルのほうがうまく答えられる事実は保存しない。
- **抽出が細部を取り逃がす。** compaction が抽出の前に細かい違いを取り除いていることがある。実行の終わり近くで抽出し、重要な事実はファイルに保つ。
- **リンクのない memory ファイル。** index にそのファイルが載らず、他のどのファイルからもリンクされていない。recall は見つけられないので、その書き込みは無駄になる。
  ファイルを書くのと同じ手順で index の行も書く。関連するファイルどうしをリンクする。

---

## 実行

[`src/`](src/) は 08 を引き継ぎ、次を追加します。

- [`memory.py`](src/memory.py): `Store`、index の読み込み、recall、抽出、そして session ログ (`log_run`、`search_sessions`、`SessionSearch` tool)。
- [`loop.py`](src/loop.py): 最初の turn に recall し、実行の終わりに抽出する。
- [`test.py`](src/test.py): 一時的な store で 4 つの操作をたどり、その後に過去の session をログして検索する。
- [`demo.py`](src/demo.py): agent が `SessionSearch` を通じて、過去の session の加工前の履歴から答える。

```bash
python sections/09-memory/src/test.py         # offline checks, no key
uv run python sections/09-memory/src/demo.py  # live demo, needs a key
```

---

## 出典

- [Claude Code source](https://github.com/yasasbanukaofficial/claude-code): `memdir/findRelevantMemories.ts`, `memdir/memdir.ts`, `services/SessionMemory/sessionMemory.ts`.
- [Claude Code memory services](https://github.com/yasasbanukaofficial/claude-code): `services/extractMemories/extractMemories.ts`, `services/autoDream/autoDream.ts`.
- [Hermes Agent source](https://github.com/NousResearch/hermes-agent):
  `tools/memory_tool.py`, `hermes_state.py` (`SessionDB`), `tools/session_search_tool.py`, `tools/write_approval.py`.
- [learn-claude-code · s09_memory](https://github.com/shareAI-lab/learn-claude-code): section framing.
- [ai-agent-book](https://github.com/bojieli/ai-agent-book): `book/chapter3.md`, Chinese original canonical. The file-system knowledge paradigm.
- [OpenViking](https://github.com/volcengine/OpenViking): knowledge as files with URIs, layered summaries, and wiki-style cross-links.
