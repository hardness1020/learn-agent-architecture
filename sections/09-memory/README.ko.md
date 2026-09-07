# 9 · Memory

[English](README.md) · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · **한국어**

> 대화 외부에 지속적인 사실을 저장하세요.

`messages[]`는 한 번의 실행을 위한 메모리입니다. 실행이 끝나면 종료되며 실행 중에 압축될 수 있습니다.

장기 메모리는 다릅니다. 대화 외부에 지속적인 사실을 저장한 후, 이후 턴에서 관련 정보를 불러옵니다.

메모리는 다음을 해야 합니다:

1. 저장할 가치가 있는 것을 결정합니다.
2. 대화 외부에 기록합니다.
3. 관련 항목만 불러옵니다.
4. 시간이 지나면서 오래되거나 중복된 항목을 정리합니다.

메모리가 없으면, 에이전트는 질문을 반복하고 세션 간에 사용자 선호를 잊습니다. 모든 것을 저장하면 기억이 복잡해지고 오래된 정보가 섞입니다.

이 섹션은 최소 루프를 구축합니다. 생산 규모에서는 메모리가 자체 하위 시스템으로 확장됩니다.
이벤트 로그, 유형 기록, 시간적 사실, 그리고 하이브리드 검색과 함께. 그 부분은 [learn-agent-memory](https://github.com/hardness1020/learn-agent-memory) 저장소에서 다룹니다.

---

## 메커니즘

![메커니즘 다이어그램](assets/09-memory.png)

메모리는 파일 저장소와 인덱스, 그리고 필요 시 불러오기가 결합된 것입니다.

루프는 전체 저장소를 읽지 않습니다. 저렴한 인덱스를 읽은 후, 현재 쿼리와 일치하는 몇 개의 메모리 파일만 불러옵니다.

그래서 질문은 파일이 어떻게 발견되는가가 됩니다. Recall은 파일당 하나의 인덱스 라인을 순위를 매깁니다. 파일이 선택된 후에만 본문을 엽니다.
인덱스 라인이 유일한 접근 방법입니다.

네 가지 작업이 있습니다:

- **선택(Selection)** 은 무엇을 저장할지 결정합니다. grep, git, 혹은 프로젝트 파일로 다시 파생될 수 없는 사실을 저장합니다.
- **리콜**은 쿼리 시점에 실행됩니다. 기존 메모리를 순위별로 평가하고 선택된 본문만 해당 턴의 `messages[]`에 삽입합니다.
  사용자 텍스트 앞에 `<system-reminder>` 블록으로.
- **추출**은 실행 종료 시 수행됩니다. 새로운 메모리 파일을 작성합니다.
- **통합**은 드물게 실행됩니다. 중복 항목을 병합하고 오래된 항목을 제거합니다.

읽기는 리콜입니다. 추출은 기록입니다. 이러한 방향을 분리하면 우발적인 저장 성장을 피할 수 있습니다.

### 새로움: 색인, 검색, 추출, 저장

그 저장소는 `.md` 파일들의 디렉토리입니다. `load_index`은 앞부분만 읽습니다:

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

리콜은 색인을 쿼리에 대해 평가합니다. 오프라인에서는 데모가 단어 중복을 사용합니다. 라이브에서는 선택기가 메모리 이름을 선택할 수 있습니다:

```python
def recall(mems, query, k=RECALL_K, selector=None) -> list[Memory]:
    if selector is not None:
        chosen = set(selector(manifest(mems), query))  # live: an LLM returns names to inject
        return [m for m in mems if m.name in chosen][:k]
    scored = ((_overlap(query, m), m) for m in mems)
    hits = sorted((s for s in scored if s[0]), key=lambda s: s[0], reverse=True)
    return [m for _score, m in hits[:k]]
```

추출은 저장소를 확장하는 유일한 작업입니다:

```python
def extract(memory_dir, messages, extractor) -> list[Path]:
    written = []
    for m in extractor(messages) or []:
        path = Path(memory_dir) / f"{m['name']}.md"
        path.write_text(_render(m))
        written.append(path)
    return written
```

위의 메모리 디렉토리는 정제된 사실을 담고 있으며, 이것이 유일한 저장소는 아닙니다. 원시 기록은 두 번째 저장소 역할을 합니다: 각 실행의 텍스트를 기록한 후 키워드로 다시 검색합니다.
로그는 모든 것을 보관하므로, 놓친 사실 추출도 여전히 찾을 수 있습니다. 이것이 Hermes의 `state.db` 설계의 배경입니다.

`log_run`는 각 실행의 텍스트를 실행 종료 시 SQLite FTS5 테이블에 추가합니다:

```python
def log_run(db_path, session_id, messages) -> int:     # src/memory.py
    rows = [(session_id, m["role"], t) for m in messages if (t := _text_of(m))]
    con = _db(db_path)                                  # CREATE VIRTUAL TABLE ... USING fts5
    con.executemany("INSERT INTO session_log VALUES (?, ?, ?)", rows)
    con.commit()
    con.close()
    return len(rows)
```

- `_text_of`는 하나의 메시지를 검색 가능한 텍스트로 평탄화합니다: 일반 문자열은 그대로 통과하고, API 응답은 텍스트 블록만 유지합니다. 도구 사용 블록에는 텍스트가 없으며 제외됩니다.
- 각 행은 `(session_id, role, content)`입니다. 세션 ID는 계보 키이므로, 검색 결과는 어떤 과거 실행에서 왔는지 말할 수 있습니다.
- FTS5는 CPython의 `sqlite3` 안에 포함되어 있으므로, 로그는 의존성 없이 사용됩니다.

`search_sessions`는 모델 호출 없이 로그를 읽고, 순위를 매깁니다:

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

- 쿼리 단어는 `OR`와 결합하므로, 어떤 단어든 일치할 수 있습니다; `ORDER BY rank`(bm25)은 최적의 일치를 먼저 배치합니다.
  이는 퍼지 순위와 함께하는 키워드 검색이며, Hermes의 `session_search`의 형태입니다.
- `k`는 반환되는 행의 수를 제한하는데, 이는 `RECALL_K`가 주입된 메모리 수를 제한하는 이유와 동일합니다: 양보다 정밀도를 우선합니다.
- `search_tool`는 이를 읽기 전용 `SessionSearch` 도구로 감싸므로, 과거 세션을 참조하는 것은 모델이 중간에 결정하는 사항입니다.
  추출된 메모리 검색은 harness의 결정에 따라 유지되며, 이는 턴 전 결정됩니다. 이 두 경로는 누가 트리거를 작동시키는지에서 차이가 있습니다.

`Store`는 루프가 사용하는 핸들이며, 현재 실행 종료 시 두 저장소 모두에 데이터를 제공합니다:

```python
def write(self, messages) -> list[Path]:               # Store.write, called at run end
    if self.db is not None:
        log_run(self.db, self.session_id, messages)     # everything, searchable later
    return extract(self.root, messages, self.extractor) if self.extractor else []   # the distilled few
```

선택기, 추출기, 세션 DB는 모두 선택 사항이므로 테스트를 오프라인에서 실행할 수 있습니다.

### 통합 방식

메모리는 루프 양쪽 끝을 감쌉니다:

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

- Recall은 턴 전에 한 번 실행되어 선택된 메모리 텍스트를 주입합니다.
- Extract는 모델이 다른 도구 호출 없이 중지될 때 실행됩니다.
- `memory=None`는 섹션-8 루프 동작을 유지합니다.
- Recall된 텍스트는 `messages[]`에 들어가므로 context management가 나중에 이를 압축할 수 있습니다.

### 추가 읽기

`src/`는 하나의 플랫 디렉토리를 유지하고 이를 전체적으로 처리하여 모든 파일이 인덱스에 무료로 포함되도록 합니다. 전체 처리하기에 너무 큰 저장소는 그 접근성을 다시 얻어야 합니다. 세 부분이 이를 수행합니다:

- **작성된 인덱스.** 하나의 유지되는 파일이 모든 기억을 나열하여, 트리를 탐색하지 않고도 회상이 순위를 매길 수 있도록 합니다.
- **기억 간의 링크.** 한 파일이 관련 파일을 가리켜, 회상이 이미 로드된 파일에서 링크를 따라갈 수 있도록 합니다.
- **계층 요약.** 각 레벨이 아래 레벨을 요약하여, 쿼리가 필요한 깊이에서 읽기를 중지할 수 있습니다.

OpenViking의 지식 저장소는 세 가지를 모두 결합하고 모든 파일에 URI를 제공합니다. 그 중 어느 것도 `src/`에는 없습니다.

---

## 시스템별

각 에이전트가 메모리를 저장, 회상, 추출 및 통합하는 방법.

| | Claude Code | Hermes Agent |
| --- | --- | --- |
| **장점** | 회상은 키워드보다 관련성을 더 잘 판단합니다. 백그라운드 작업이 저장소를 청소합니다. | 메모리가 프롬프트에 있고 캐시는 따뜻하게 유지됩니다. 검색에 모델 호출이 필요 없습니다. |
| **단점** | 각 리콜은 모델 호출을 추가합니다. 통합에는 별도의 게이팅이 필요합니다. | 키워드 리콜은 정확도가 낮습니다. 새로운 작성은 다음 세션에서만 프롬프트에 도달합니다. |
| **이유** | 모든 것을 저장하면 리콜이 시끄러워지므로 선택자가 일부 기억만 주입합니다. | 추출이 사실을 놓칠 수 있으므로 원본 기록은 두 번째 저장소로 검색 가능하게 유지됩니다. |
| **방법: 저장** | 프론트매터가 있는 마크다운 파일. MEMORY.md는 인덱스이며, 기억 본문이 아닙니다. | 두 개의 마크다운 파일(관찰, 사용자 프로필)과 SQLite 세션 로그. |
| **방법: 리콜** | 모델이 인덱스를 읽고 최대 5개를 선택합니다. 본문에는 최신성 노트가 있습니다. | 세션 시작 시 고정된 프롬프트 스냅샷과 과거 세션의 키워드 검색. |
| **방법: 추출** | 포크된 에이전트가 실행 종료 시 새 메모리 파일을 작성합니다. | 메모리 도구가 세션 중간에 항목을 디스크에 씁니다. 작성된 내용은 승인 대기 상태로 둘 수 있습니다. |
| **방법: 통합** | 백그라운드 작업이 시간, 세션 수, 잠금으로 제한되어 병합 및 정리를 수행합니다. | 캐릭터 예산을 초과하면 모델이 재작성하며, 실패 추적이 있습니다. |

---

## 실패 모드

- **유용한 메모리를 회상하지 못함.** 선택기를 조정하고 설명을 구체적으로 유지하세요.
- **회상으로 인해 턴이 과다하게 채워짐.** 삽입된 메모리 수를 제한하고 정밀도를 우선하세요.
- **오래된 메모리가 사실로 간주됨.** 연령 또는 신선도 메타데이터를 포함하세요.
- **스토어가 혼잡해짐.** 중복 및 모순을 통합하세요.
- **유도 가능한 사실 저장.** grep, git 또는 소스 파일이 더 잘 대답할 수 있는 사실은 저장하지 마세요.
- **추출에서 세부 정보 누락.** 추출 전에 압축으로 인해 뉘앙스가 사라졌을 수 있습니다. 실행이 끝날 무렵에 추출하고 중요한 사실은 파일에 보관하세요.
- **연결되지 않은 메모리 파일.** 색인에 파일이 없고 다른 파일이 그 파일을 링크하지 않습니다. 호출 시 절대 찾을 수 없으므로 기록이 낭비됩니다.
  파일을 기록할 때 색인 라인도 같은 단계에서 쓰세요. 관련 파일끼리 서로 연결하세요.

---

## 실행 가능

[`src/`](src/)는 08을 이어받아 다음을 추가합니다:

- [`memory.py`](src/memory.py): `Store`, 색인 로딩, 호출, 추출, 세션 로그(`log_run`, `search_sessions`, `SessionSearch` 도구).
- [`loop.py`](src/loop.py): 개시 회차에서 호출하고 실행 종료 시 추출합니다.
- [`test.py`](src/test.py): 임시 저장소에서 네 가지 연산을 수행한 후, 이전 세션을 기록하고 검색합니다.
- [`demo.py`](src/demo.py): 에이전트가 `SessionSearch`을 통해 이전 세션의 원시 기록에서 답변합니다.

```bash
python sections/09-memory/src/test.py         # offline checks, no key
uv run python sections/09-memory/src/demo.py  # live demo, needs a key
```

---

## 출처

- [Claude Code source](https://github.com/yasasbanukaofficial/claude-code): `memdir/findRelevantMemories.ts`, `memdir/memdir.ts`, `services/SessionMemory/sessionMemory.ts`.
- [Claude Code memory services](https://github.com/yasasbanukaofficial/claude-code): `services/extractMemories/extractMemories.ts`, `services/autoDream/autoDream.ts`.
- [Hermes Agent source](https://github.com/NousResearch/hermes-agent):
  `tools/memory_tool.py`, `hermes_state.py` (`SessionDB`), `tools/session_search_tool.py`, `tools/write_approval.py`.
- [learn-claude-code · s09_memory](https://github.com/shareAI-lab/learn-claude-code): 섹션 프레이밍.
- [ai-agent-book](https://github.com/bojieli/ai-agent-book): `book/chapter3.md`, 중국어 원본 정본. 파일 시스템 지식 패러다임.
- [OpenViking](https://github.com/volcengine/OpenViking): URI가 있는 파일로서의 지식, 계층화된 요약, 위키 스타일의 교차 링크.
