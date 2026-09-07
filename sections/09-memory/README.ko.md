# 9 · Memory

[English](README.md) · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · **한국어**

> 오래 남을 사실을 대화 밖에 저장합니다.

`messages[]`는 한 번의 실행을 위한 기억입니다. 실행과 함께 끝나고, 실행 도중에 compaction될 수도 있습니다.

장기 memory는 다릅니다. 오래 남을 사실을 대화 밖에 저장해 두었다가, 이후 turn에서 관련 있는 것만 recall합니다.

memory는 다음을 해내야 합니다.

1. 무엇을 저장할 가치가 있는지 결정합니다.
2. 그것을 대화 밖에 씁니다.
3. 관련 있는 항목만 recall합니다.
4. 시간이 지나면서 낡거나 중복된 항목을 정리합니다.

memory가 없으면 agent는 같은 질문을 반복하고 session 사이에 사용자 선호를 잊습니다. 반대로 전부 저장하면 recall이 시끄럽고 낡아집니다.

이 섹션은 최소한의 loop를 만듭니다. 프로덕션 규모에서는 memory가 그 자체로 하나의 서브시스템이 됩니다.
이벤트 로그, 타입이 있는 레코드, 시간축을 가진 사실, 하이브리드 검색이 붙습니다. [learn-agent-memory](https://github.com/hardness1020/learn-agent-memory) 리포지터리가 그것을 다룹니다.

---

## 메커니즘

![Mechanism diagram](assets/09-memory.png)

memory는 파일 저장소에 색인을 더하고, 필요할 때 하는 recall을 더한 것입니다.

loop는 저장소 전체를 읽지 않습니다. 값싼 색인을 읽고, 현재 질의에 맞는 memory 파일 몇 개만 불러옵니다.

그래서 질문은 파일이 어떻게 발견되는가가 됩니다. recall은 파일마다 색인 한 줄을 놓고 순위를 매깁니다. 본문은 파일이 뽑힌 뒤에야 엽니다.
색인 줄이 유일한 입구입니다.

연산은 네 가지입니다.

- **선택**은 무엇을 저장할지 정합니다. grep, git, 프로젝트 파일로 다시 알아낼 수 없는 사실을 저장합니다.
- **Recall**은 질의 시점에 돕니다. 기존 memory의 순위를 매기고, 선택된 본문만 그 turn의 `messages[]`에 주입합니다.
  사용자 텍스트 앞의 `<system-reminder>` 블록으로 들어갑니다.
- **추출**은 실행이 끝날 때 돕니다. 새 memory 파일을 씁니다.
- **통합**은 드물게 돕니다. 중복을 병합하고 낡은 항목을 정리합니다.

recall은 읽습니다. 추출은 씁니다. 이 두 방향을 갈라 두면 저장소가 뜻하지 않게 커지는 일을 막습니다.

### 새로 추가: 색인, recall, 추출, 그리고 저장소

저장소는 `.md` 파일들이 든 디렉터리입니다. `load_index`는 frontmatter만 읽습니다.

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

recall은 질의에 대해 색인의 순위를 매깁니다. 오프라인에서는 데모가 단어 겹침을 씁니다. 실제 환경에서는 selector가 memory 이름을 고를 수 있습니다.

```python
def recall(mems, query, k=RECALL_K, selector=None) -> list[Memory]:
    if selector is not None:
        chosen = set(selector(manifest(mems), query))  # live: an LLM returns names to inject
        return [m for m in mems if m.name in chosen][:k]
    scored = ((_overlap(query, m), m) for m in mems)
    hits = sorted((s for s in scored if s[0]), key=lambda s: s[0], reverse=True)
    return [m for _score, m in hits[:k]]
```

추출은 저장소를 키우는 유일한 연산입니다.

```python
def extract(memory_dir, messages, extractor) -> list[Path]:
    written = []
    for m in extractor(messages) or []:
        path = Path(memory_dir) / f"{m['name']}.md"
        path.write_text(_render(m))
        written.append(path)
    return written
```

위의 memory 디렉터리는 정제된 사실을 담고 있고, 저장소는 이것 하나만 있는 것이 아닙니다. 원시 히스토리도 두 번째 저장소가 됩니다. 실행마다 텍스트를 기록해 두고, 나중에 키워드로 되찾습니다.
로그는 전부를 남기므로, 추출이 놓친 사실도 여전히 찾을 수 있습니다. hermes-agent의 `state.db` 뒤에 있는 설계가 이것입니다.

`log_run`은 실행이 끝날 때 그 실행의 텍스트를 SQLite FTS5 테이블에 덧붙입니다.

```python
def log_run(db_path, session_id, messages) -> int:     # src/memory.py
    rows = [(session_id, m["role"], t) for m in messages if (t := _text_of(m))]
    con = _db(db_path)                                  # CREATE VIRTUAL TABLE ... USING fts5
    con.executemany("INSERT INTO session_log VALUES (?, ?, ?)", rows)
    con.commit()
    con.close()
    return len(rows)
```

- `_text_of`는 메시지 하나를 검색 가능한 텍스트로 평평하게 만듭니다. 평범한 문자열은 그대로 통과하고, API 응답은 텍스트 블록만 남깁니다. tool use 블록은 텍스트가 없어서 빠집니다.
- 각 행은 `(session_id, role, content)`입니다. session id가 계보 키이므로, 검색 결과가 어느 과거 실행에서 왔는지 말할 수 있습니다.
- FTS5는 CPython의 `sqlite3` 안에 함께 들어 있으므로, 이 로그에는 의존성 비용이 거저 따라옵니다.

`search_sessions`는 모델 호출 없이 로그를 순위와 함께 되읽습니다.

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

- 질의 단어는 `OR`로 이어지므로 어느 단어든 걸릴 수 있고, `ORDER BY rank` (bm25)가 가장 잘 맞는 것을 앞에 둡니다.
  퍼지 순위가 붙은 키워드 recall이고, hermes-agent의 `session_search`와 같은 형태입니다.
- `k`는 돌려주는 행 수를 제한합니다. `RECALL_K`가 주입되는 memory 수를 제한하는 것과 같은 이유입니다. 양보다 정확도입니다.
- `search_tool`은 이것을 읽기 전용 `SessionSearch` tool로 감쌉니다. 그래서 과거 session을 뒤질지는 모델이 turn 도중에 내리는 결정입니다.
  추출된 memory의 recall은 harness의 결정으로 남고, turn 전에 이루어집니다. 두 경로는 누가 방아쇠를 당기느냐가 다릅니다.

`Store`는 loop가 쓰는 핸들이고, 이제 실행이 끝날 때 두 저장소 모두에 넣습니다.

```python
def write(self, messages) -> list[Path]:               # Store.write, called at run end
    if self.db is not None:
        log_run(self.db, self.session_id, messages)     # everything, searchable later
    return extract(self.root, messages, self.extractor) if self.extractor else []   # the distilled few
```

selector, extractor, session db는 모두 선택 사항이므로 테스트를 오프라인으로 돌릴 수 있습니다.

### 통합 방식

memory는 loop의 양쪽 끝을 감쌉니다.

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

- recall은 turn 전에 한 번 돌면서 선택된 memory 텍스트를 주입합니다.
- 추출은 모델이 또 다른 tool call 없이 멈출 때 돕니다.
- `memory=None`이면 섹션 8의 loop 동작이 그대로 유지됩니다.
- recall된 텍스트는 `messages[]`에 들어가므로, 나중에 context 관리가 그것을 compaction할 수 있습니다.

### 더 읽을거리

`src/`는 평평한 디렉터리 하나를 glob하므로, 모든 파일이 거저 색인에 들어옵니다. glob하기에 너무 큰 저장소는 그 도달 가능성을 다시 벌어야 합니다. 세 가지가 그 일을 합니다.

- **쓰인 색인.** 유지 관리되는 파일 하나가 모든 memory를 나열하므로, recall은 트리를 걷지 않고도 순위를 매길 대상을 얻습니다.
- **memory 사이의 링크.** 한 파일이 관련 파일을 가리키므로, recall은 이미 불러온 파일에서 링크를 따라 밖으로 나갈 수 있습니다.
- **계층화된 요약.** 각 층이 아래 층을 요약하므로, 읽기가 질의에 필요한 깊이에서 멈출 수 있습니다.

OpenViking의 지식 저장소는 이 셋을 모두 합치고 모든 파일에 URI를 줍니다. 이 중 어느 것도 `src/`에는 없습니다.

---

## 시스템별

각 agent가 memory를 저장하고, recall하고, 추출하고, 통합하는 방식입니다.

| | Claude Code | Hermes Agent |
| --- | --- | --- |
| **장점** | recall이 키워드보다 관련성을 잘 판단함. 백그라운드 task가 저장소를 청소함. | memory가 prompt에 앉아 있어 캐시가 따뜻하게 유지됨. 검색에 모델 호출이 필요 없음. |
| **단점** | recall마다 모델 호출이 하나씩 늘어남. 통합에는 자체 통제가 필요함. | 키워드 recall은 정확도가 낮음. 새로 쓴 내용은 다음 session에야 prompt에 닿음. |
| **이유** | 전부 저장하면 recall이 시끄러워지므로, selector가 몇 개만 주입함. | 추출이 사실을 놓칠 수 있으므로, 원시 히스토리를 두 번째 저장소로 두고 검색 가능하게 함. |
| **방법: store** | frontmatter가 붙은 마크다운 파일. MEMORY.md는 색인이지 memory 본문이 아님. | 마크다운 파일 두 개(관찰, 사용자 프로필)와 SQLite session 로그. |
| **방법: recall** | 모델이 색인을 읽고 최대 5개를 고름. 본문에는 신선도 표시가 붙음. | session 시작 시점에 고정된 prompt 스냅샷, 그리고 과거 session의 키워드 검색. |
| **방법: extraction** | fork된 agent가 실행 끝에 새 memory 파일을 씀. | memory tool이 session 도중 항목을 디스크에 씀. 쓰기를 승인 대기로 둘 수 있음. |
| **방법: consolidation** | 백그라운드 task가 병합하고 정리함. 시간, session 횟수, 락으로 통제됨. | 문자 수 상한을 넘으면 모델이 다시 씀. 실패는 추적됨. |

---

## 실패 모드

- **recall이 쓸모 있는 memory를 놓침.** selector를 조정하고 description을 구체적으로 유지합니다.
- **recall이 turn을 넘치게 함.** 주입되는 memory 개수에 상한을 두고 정확도를 우선합니다.
- **낡은 memory가 사실로 취급됨.** 나이나 신선도 메타데이터를 넣습니다.
- **저장소가 시끄러워짐.** 중복과 모순을 통합합니다.
- **유도 가능한 사실을 저장함.** grep, git, 소스 파일이 더 잘 답할 수 있는 사실은 저장하지 않습니다.
- **추출이 세부를 놓침.** 추출 전에 compaction이 뉘앙스를 지웠을 수 있습니다. 실행 끝 가까이에서 추출하고 중요한 사실은 파일에 남깁니다.
- **연결되지 않은 memory 파일.** 색인이 그 파일을 나열하지 않고, 다른 어떤 파일도 그것을 링크하지 않습니다. recall이 절대 찾지 못하므로 그 쓰기는 낭비였습니다.
  파일을 쓰는 그 단계에서 색인 줄도 함께 씁니다. 관련 파일끼리 서로 링크합니다.

---

## 실행 방법

[`src/`](src/)는 08을 이어받아 다음을 추가합니다.

- [`memory.py`](src/memory.py): `Store`, 색인 로딩, recall, 추출, 그리고 session 로그(`log_run`, `search_sessions`, `SessionSearch` tool).
- [`loop.py`](src/loop.py): 첫 turn에 recall해 넣고 실행 끝에 추출합니다.
- [`test.py`](src/test.py): 임시 저장소에서 네 연산을 훑고, 이어서 과거 session을 기록하고 검색합니다.
- [`demo.py`](src/demo.py): agent가 `SessionSearch`로 과거 session의 원시 히스토리에서 답을 찾습니다.

```bash
python sections/09-memory/src/test.py         # offline checks, no key
uv run python sections/09-memory/src/demo.py  # live demo, needs a key
```

---

## 출처

- [Claude Code 소스](https://github.com/yasasbanukaofficial/claude-code): `memdir/findRelevantMemories.ts`, `memdir/memdir.ts`, `services/SessionMemory/sessionMemory.ts`.
- [Claude Code memory 서비스](https://github.com/yasasbanukaofficial/claude-code): `services/extractMemories/extractMemories.ts`, `services/autoDream/autoDream.ts`.
- [Hermes Agent 소스](https://github.com/NousResearch/hermes-agent):
  `tools/memory_tool.py`, `hermes_state.py` (`SessionDB`), `tools/session_search_tool.py`, `tools/write_approval.py`.
- [learn-claude-code · s09_memory](https://github.com/shareAI-lab/learn-claude-code): 섹션 구성 참고.
- [ai-agent-book](https://github.com/bojieli/ai-agent-book): `book/chapter3.md`, 중국어 원문이 기준. 파일 시스템 지식 패러다임.
- [OpenViking](https://github.com/volcengine/OpenViking): URI를 가진 파일로서의 지식, 계층화된 요약, 위키식 상호 링크.
