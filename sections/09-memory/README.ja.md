# 9 · Memory

[English](README.md) · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · **日本語** · [한국어](README.ko.md)

> 永続的な事実を会話の外に保管します。

`messages[]` は 1 回の実行分のメモリです。実行で終了し、実行中に圧縮することができます。

長期記憶は違います。会話以外の永続的な事実を保存し、将来のターンのために関連する事実を呼び出します。

メモリは次のことを行う必要があります。

1. 何を節約する価値があるかを決定します。
2. 会話の外でそれを書きます。
3. 関連する項目だけを思い出してください。
4. 古いアイテムや重複したアイテムを時間をかけてクリーンアップします。

記憶力がないと、エージェントは質問を繰り返し、セッション間でユーザーの設定を忘れてしまいます。すべてを保存すると、リコールがうるさくなり、陳腐化します。

このセクションでは最小限のループを構築します。実稼働規模では、メモリは独自のサブシステムに成長します。
イベント ログ、型指定されたレコード、一時的な事実、およびハイブリッド検索を使用します。 [learn-agent-memory](https://github.com/hardness1020/learn-agent-memory) リポジトリはこれをカバーしています。

---

## メカニズム

![機構図](assets/09-memory.png)

メモリは、ファイル ストア、インデックス、オンデマンド リコールを加えたものです。

ループはストア全体を読み取りません。安価なインデックスを読み取り、現在のクエリに一致する少数のメモリ ファイルのみをロードします。

したがって、問題はファイルがどのように見つかるかになります。リコールはファイルごとに 1 つのインデックス行をランク付けします。ファイルが選択された後にのみ本文が開きます。
インデックスラインが唯一の入り口です。

次の 4 つの操作があります。

- **選択**により、何を保存するかが決まります。 grep、git、またはプロジェクト ファイルを使用して再度導出できないファクトを保存します。
- **Recall** はクエリ時に実行されます。既存のメモリをランク付けし、選択されたボディのみをターンの `messages[]` に注入します。
  ユーザーテキストの前にある `<system-reminder>` ブロックとして。
- **抽出**は実行終了時に実行されます。新しいメモリ ファイルを書き込みます。
- **統合**はめったに実行されません。重複したエントリをマージし、古いエントリを削除します。

読み取りを思い出してください。抽出書き込み。これらの方向を分離しておくことで、店舗の偶発的な拡大を避けることができます。

### 新機能: インデックス、リコール、抽出、およびストア

ストアは、`.md` ファイルのディレクトリです。 `load_index` はフロントマターのみを読み取ります。

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

リコールはクエリに対してインデックスをランク付けします。オフラインでは、デモでは単語のオーバーラップを使用します。 Live では、セレクターでメモリ名を選択できます。

```python
def recall(mems, query, k=RECALL_K, selector=None) -> list[Memory]:
    if selector is not None:
        chosen = set(selector(manifest(mems), query))  # live: an LLM returns names to inject
        return [m for m in mems if m.name in chosen][:k]
    scored = ((_overlap(query, m), m) for m in mems)
    hits = sorted((s for s in scored if s[0]), key=lambda s: s[0], reverse=True)
    return [m for _score, m in hits[:k]]
```

抽出はストアを拡張する唯一の操作です。

```python
def extract(memory_dir, messages, extractor) -> list[Path]:
    written = []
    for m in extractor(messages) or []:
        path = Path(memory_dir) / f"{m['name']}.md"
        path.write_text(_render(m))
        written.append(path)
    return written
```

上記のメモリ ディレクトリには抽出された事実が保持されており、これが唯一のストアではありません。生の履歴は 2 番目の履歴として機能します。各実行のテキストをログに記録し、キーワードで検索し直します。
ログにはすべてが保存されるため、抽出できなかった事実を見つけることができます。エルメスの`state.db`のデザインです。

`log_run` は、実行終了時に各実行のテキストを SQLite FTS5 テーブルに追加します。

```python
def log_run(db_path, session_id, messages) -> int:     # src/memory.py
    rows = [(session_id, m["role"], t) for m in messages if (t := _text_of(m))]
    con = _db(db_path)                                  # CREATE VIRTUAL TABLE ... USING fts5
    con.executemany("INSERT INTO session_log VALUES (?, ?, ?)", rows)
    con.commit()
    con.close()
    return len(rows)
```

- `_text_of` は、1 つのメッセージを検索可能なテキストにフラット化します。プレーンな文字列が通過し、API 応答はテキスト ブロックのみを保持します。ツール使用ブロックにはテキストが含まれず、ドロップアウトされます。
- 各行は `(session_id, role, content)` です。セッション ID はリネージ キーであるため、ヒットによって、過去のどの実行からのものであるかがわかります。
- FTS5 は CPython の `sqlite3` 内に同梱されているため、ログには依存関係がありません。

`search_sessions` は、モデル呼び出しを行わずに、ランク付けされたログを読み取ります。

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

- クエリ単語は `OR` と結合するため、どの単語でもヒットします。 `ORDER BY rank` (bm25) は、最適な一致を最初に配置します。
  それは、あいまいなランキングによるキーワードの想起であり、エルメスの `session_search` の形です。
- `k` は、`RECALL_K` が注入されたメモリを制限するのと同じ理由 (量よりも精度) で、返された行を制限します。
- `search_tool` は、これを読み取り専用 `SessionSearch` ツールとしてラップするため、過去のセッションを参照するかどうかは、ターン中に行われるモデルの決定となります。
  抽出されたメモリのリコールは、ターン前に行われた harness の決定のままです。 2つの道は、誰が引き金を引くかという点で異なります。

`Store` はループが使用するハンドルで、実行終了時に両方のストアにフィードします。

```python
def write(self, messages) -> list[Path]:               # Store.write, called at run end
    if self.db is not None:
        log_run(self.db, self.session_id, messages)     # everything, searchable later
    return extract(self.root, messages, self.extractor) if self.extractor else []   # the distilled few
```

セレクター、エクストラクター、およびセッション データベースはすべてオプションであるため、テストはオフラインで実行できます。

### 統合方法

メモリはループの両端をラップします。

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

- リコールはターンの前に 1 回実行され、選択したメモリ テキストが挿入されます。
- 別のツールを呼び出すことなくモデルが停止すると、Extract が実行されます。
- `memory=None` はセクション 8 のループ動作を維持します。
- 呼び出されたテキストは `messages[]` に入力されるため、後で context management で圧縮できます。

### さらに読む

`src/` は 1 つのフラット ディレクトリを保持し、それをグロブするため、すべてのファイルが無料でインデックスに配置されます。グロブするには大きすぎるストアは、その到達可能性を取り戻す必要があります。それを行うのは 3 つの部分です。

- **書かれたインデックス。** 維持されている 1 つのファイルにすべての記憶がリストされているため、ツリーをたどることなく思い出すとランク付けできるものになります。
- **メモリ間のリンク。** ファイルは関連ファイルをポイントするため、リコールはすでにロードされているファイルからのリンクをたどることができます。
- **階層化された要約。** 各レベルは以下のレベルを要約するため、クエリが必要とする深さで読み取りを停止できます。

OpenViking のナレッジ ストアは 3 つすべてを組み合わせて、すべてのファイルに URI を与えます。 `src/` には何もありません。

---

## システムごと

各エージェントが記憶をどのように保存、呼び出し、抽出、統合するか。

| | Claude Code | Hermes Agent |
| --- | --- | --- |
| **長所** |リコールは、キーワードよりも関連性を判断します。バックグラウンド タスクによってストアが清掃されます。 |メモリはプロンプト内に留まり、キャッシュはウォームな状態を保ちます。検索にはモデル呼び出しは必要ありません。 |
| **短所** |リコールごとにモデル呼び出しが追加されます。統合には独自のゲートが必要です。 |キーワードの再現精度はそれほど高くありません。新しい書き込みは次のセッションのプロンプトにのみ到達します。 |
| **理由** |すべてを保存するとリコールにノイズが多くなるため、セレクターは少数のメモリのみを挿入します。 |抽出では事実が見逃される可能性があるため、生の履歴は 2 番目のストアとして検索可能なままになります。 |
| **方法: 保存** |フロントマターを含むマークダウン ファイル。 MEMORY.md はインデックスであり、メモリ本体ではありません。 | 2 つのマークダウン ファイル (観察、ユーザー プロファイル) と SQLite セッション ログ。 |
| **方法: 思い出してください** |モデルはインデックスを読み取り、最大 5 を取得します。ボディは鮮度ノートを取得します。 |セッション開始時の凍結されたプロンプト スナップショットと、過去のセッションのキーワード検索。 |
| **方法: 抽出** |フォークされたエージェントは、実行終了時に新しいメモリ ファイルを書き込みます。 |メモリ ツールは、セッション中にエントリをディスクに書き込みます。書き込みは承認のために段階的に実行できます。 |
| **方法: 統合** |バックグラウンド タスクは、時間、セッション数、ロックによってゲートされてマージおよびプルーニングされます。 |障害追跡を使用して、キャラクター予算のオーバーフロー時にモデルを書き換えます。 |

---

## 障害モード

- **リコールでは有用な記憶が失われます。** セレクターを調整し、説明を具体的にしてください。
- **リコールはターンに溢れます。** 注入されるメモリの数に上限を設け、精度を優先します。
- **古い記憶は事実として扱われます。** 年齢または鮮度のメタデータを含めます。
- **ストアが騒がしくなる** 重複や矛盾を統合します。
- **導出可能なファクトの保存** grep、git、またはソース ファイルの方が適切に応答できるファクトを保存しないでください。
- **抽出では詳細が欠落します。** 圧縮により、抽出前のニュアンスが削除されている可能性があります。実行終了近くで抽出し、重要な事実をファイルに保存します。
- **リンクされていないメモリ ファイル。** インデックスにはファイルがリストされておらず、そのファイルにリンクしている他のファイルもありません。 Recall ではそれが見つからないため、書き込みは無駄になりました。
  ファイルを書き込むのと同じ手順でインデックス行を書き込みます。関連するファイルを相互にリンクします。

---

## 実行可能

[`src/`](src/) 08 を前方に繰り上げて次を追加します。

- [`memory.py`](src/memory.py): `Store`、インデックスのロード、リコール、抽出、およびセッション ログ (`log_run`、`search_sessions`、 `SessionSearch` ツール)。
- [`loop.py`](src/loop.py): 最初のターンにリコールされ、実行終了時に抽出されます。
- [`test.py`](src/test.py): 一時ストア上で 4 つの操作を実行し、過去のセッションをログに記録して検索します。
- [`demo.py`](src/demo.py): エージェントは、`SessionSearch` 経由で過去のセッションの生の履歴から応答します。

```bash
python sections/09-memory/src/test.py         # offline checks, no key
uv run python sections/09-memory/src/demo.py  # live demo, needs a key
```

---

## ソース

- [Claude Code ソース](https://github.com/yasasbanukaofficial/claude-code): `memdir/findRelevantMemories.ts`、`memdir/memdir.ts`、`services/SessionMemory/sessionMemory.ts`。
- [Claude Code メモリ サービス](https://github.com/yasasbanukaofficial/claude-code): `services/extractMemories/extractMemories.ts`、`services/autoDream/autoDream.ts`。
- [Hermes Agent ソース](https://github.com/NousResearch/hermes-agent):
  `tools/memory_tool.py`、`hermes_state.py` (`SessionDB`)、`tools/session_search_tool.py`、`tools/write_approval.py`。
- [learn-claude-code · s09_memory](https://github.com/shareAI-lab/learn-claude-code): セクション フレーム。
- [ai-agent-book](https://github.com/bojieli/ai-agent-book): `book/chapter3.md`、中国語オリジナルの正規版。ファイルシステムの知識パラダイム。
- [OpenViking](https://github.com/volcengine/OpenViking): URI、階層化された概要、Wiki スタイルのクロスリンクを含むファイルとしてのナレッジ。
