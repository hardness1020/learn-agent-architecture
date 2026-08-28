# 7 · Skills

[English](README.md) · **繁體中文** · [简体中文](README.zh-CN.md)

> skill 把一套專業工作流程包起來，只在任務需要時載入。

skill 可以讓通用 agent 在特定任務上具備專業能力。它打包一整套工作流程，包括要遵循的指令、可執行的 script，以及需要參考的檔案。
agent 只有在任務用得到時才載入對應的 skill，因此可以擁有大量專門能力，又不必一開始就把所有內容塞進 context。

每個 skill 都是一個資料夾，核心檔案是 `SKILL.md`。frontmatter 負責命名與描述，本文放操作指令；資料夾中還能附上 script 和參考資料，需要時再讀取。

agent 必須知道有哪些 skill 可用，但不該在每個 turn 都載入所有 skill 的完整內容。

skill 系統必須做到：

1. 用很低的 token 成本列出可用 skill。
2. 只有選中某個 skill 時，才載入完整指令。
3. 允許 skill 引用額外檔案，但不預先載入。
4. 從 built-in、user、project、plugin 或 MCP 等來源探索 skill。

沒有這一層，prompt 不是塞得太滿，就是 agent 根本找不到已經存在的擴充能力。

---

## 核心機制

![機制圖](assets/07-skills.png)

skill 使用 progressive disclosure。模型只會看到剛好足夠的資訊，來決定要不要載入更多。

1. **Metadata：**來自 frontmatter 的 `name` 和 `description`，再加上這個 skill 的路徑。這份 catalog 只佔少量 token，所以一直放在 system prompt 裡。
2. **Instructions：**`SKILL.md` 的本文。只有在某個任務需要這個 skill 時，模型才會去讀這個檔案。
3. **Resources：**skill 資料夾裡的額外檔案。指令指向它們時，模型用同一個 file tool 讀取。

不需要專門的 skill tool。只要 catalog 列出每個 skill 的名稱和路徑，agent 就用一般的 Read tool 去讀那個檔案來載入 skill。L2 和 L3 都只是讀檔而已。

description 這一行最關鍵。它是路由條件，不是摘要。
模型在決定要不要載入之前，就只看得到這一行。所以要寫清楚什麼時候該用，也要寫什麼時候不該用。
最好再附一個反例。只寫個主題名稱，模型只能用猜的。

### 本章新增：掃描 skill 並加入 prompt

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

- `load_skills` 掃描 `SKILL.md` 檔案，只保留 frontmatter 給 catalog。
- `catalog_prompt` 把這份 catalog 渲染進 system prompt，每個 skill 一行，附上要讀取的路徑。
- 本文和 resource 都是普通檔案。一般的 Read tool 在需要時載入它們，所以不需要專門的 skill tool。
- Read tool 的範圍限制在 skills 目錄內，所以 skill 名稱永遠無法逃逸到檔案系統其他地方。

### 本章新增：讓 skill store 持續演進

skill 系統不是只有載入這件事。skill store 本身也會成長、也會汰舊（Hermes 稱之為 skill 演化）。

成長靠寫入。agent 把一段做完的工作流程沉澱成新的 skill，下一次執行就直接載入指令，不用重新摸索：

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

- `WriteSkill` 是包住這個函式、面向模型的 tool。寫入 skill 會改動檔案系統，屬於有副作用的操作，所以第 3 章的權限閘門預設會先徵詢使用者；只有 allow 規則預先核准過，才會直接放行。
- 寫出來的檔案就是普通的 `SKILL.md`。沒有任何特殊標記：下一次 `load_skills` 掃描會把它當成一般的 skill 編入 catalog。
- 名稱的解析和檢查方式跟 `read_tool` 檢查路徑一樣，所以不論讀或寫，都逃不出 skills 目錄。

要汰舊，得先量測。載入 skill 本身就是使用訊號，所以 `read_tool` 在讀檔的同時順手記錄：

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

- 這筆記錄以 skill 的資料夾名稱為 key，取自模型讀取的路徑。讀 resource（L3）不會累計，只有讀 `SKILL.md` 本文（L2）才算。
- 沒有記錄的 skill，`last_used_at` 是 0，所以從未用過的 skill 也算 stale。
- `stale_skills` 是一份報告，不是一個動作。要怎麼處理是 curator 的工作；Hermes 用一個背景 curator agent 處理同樣的訊號（封存、整併、釘選）。
- 資料會形成一個跨多次執行的 loop：讀取操作更新 `.usage.json`，curator 再讀取它，catalog 反映保留下來的 skill，`WriteSkill` 則加入新條目。

### 如何接進現有架構

loop 不用改。讀取 skill 就是一次普通的工具呼叫，tool 結果照樣進入 `messages[]`。

三層各有位置：catalog 放在 system prompt。skill 本文要等模型讀了 `SKILL.md`，才會進到對話裡。resource 檔案則等到真的用到時才讀。

載入後的 skill 文字就在 `messages[]` 裡，所以之後 context 不夠用時，它會跟其他訊息一起被壓縮（第 8 章）。skill 本文要寫短，大型參考資料改成指向檔案。

### 延伸閱讀

以下設計 `src/` 都沒有實作，出自 ai-agent-book 和廠商文件，也未經下面表格的系統證實。

**catalog 要花多少成本：**progressive disclosure 讓一個很大的 skill store 變便宜，但沒有讓它變免費。
catalog 就在 prefix 裡，prefill 時要被讀一次，之後每個 turn 都要再送一次。
第一個 turn 之後那段 prefix 就被快取住，所以重送的成本很低。
載入本文比較貴，而且本文會一直佔著 context，直到有東西來壓縮它。
所以真正要盯的數字是 catalog 掛了幾個 skill，而不是本文被讀了幾次。

**catalog 放在哪：**這份列表可以放在 system prompt，`src/` 就是這樣做的。
它也可以放進某個用來啟用 skill 的 tool description 中，open standard 同時允許這兩種方式。
差別在這筆 token 算到哪邊。放 system prompt，它就是每個 session prefix 的一部分。
放進 tool description，prefix 就小一些，模型改成透過那個 tool 去看這份列表。

**Deferred tool loading：**tool 也可以用同一套做法，理由也一樣：schema 很大，但大部分 turn 根本用不到。
prefix 裡只留 tool 名稱和一行描述，模型要用到某個 tool 時，才去要完整 schema。
要來的 schema 接在 context 尾端，所以快取住的 prefix 完全沒被動到，前面的東西也都不用重算。
skill 是這個 repo 第一次碰到 progressive disclosure 的地方；照書上說，同一套做法現在也長到 tool 這一層了（第 2 章）。

**什麼時候該寫一個 skill：**假設某一趟執行第一次把一段長流程跑對了，這該不該存成 skill？
這裡的 demo 說該。流程一跑完，agent 就呼叫一次 `WriteSkill`，下一次掃描把它編進 catalog。
這是能把整個 loop 演出來的最小規則，也是可執行程式碼實際在做的事。

**書裡的門檻更高：**書的答案是不該，因為一趟執行不算證據。
要讓一個 skill 變成正式能力，書要求四件事：

- 同一個模式至少在兩趟沒有失敗的執行裡出現過。
- 驗證那一步不能來自提出這個 skill 的那趟執行。Voyager 也是這樣做：環境確認過，skill 才進得了 library。
- 動手寫之前先搜一下 store。如果已經有很像的，就去改它，不要再多開一個重複的。
- 這趟踩到的坑要留在本文裡，不要只留走得通的那條路。

**該選哪一種：**兩種都說得通，因為它們回答的不是同一個問題。
一次成功比較好教機制，demo 也短。門檻則是在 store 長到幾百個的時候，擋住那些只用過一次的筆記。
中間還可以插一個 candidate 步驟。沉澱出來的流程先落成 candidate，不直接進 catalog。
它會經過起草、測試、評估、修訂，才被升級。Anthropic 的 Skill Creator 就是跑這個 loop。
放到本章的程式碼裡，就是多一個暫存資料夾，`load_skills` 先跳過它，等 curator 升級才收。

**整併是離線做的：**curator 是排程跑的，不是即時跑的。書裡叫它 sleep-time learning，分成五步：

1. **觸發：**排程時間到、系統閒置，或 store 大小超過上限。
2. **定位：**先對 store 做一次快照，後面每一步才都能回滾。
3. **蒐集與合併：**讀使用記錄和最近幾次執行，把幾乎重複的 skill 併成一個，再把 candidate 收進來。
4. **驗證與核准：**拿產生它們的那幾次執行，去檢查合併後的本文。沒過的就不收。
5. **修剪與建索引：**依固定規則封存過期的 skill，然後重建 catalog。

**為什麼一定要離線：**把 curator 放在離線跑，本身就是一條安全邊界。線上 loop 只負責執行和記錄，跑到一半絕不去動 store。
所以一趟剛好成功的執行沒辦法把自己升級，agent 從外面讀進來的文字，也沒辦法在兩個 turn 之間變成永久指令。

---

## 不同系統怎麼做

各 agent 如何描述、觸發並找到 skill。

| | Claude Code | Hermes Agent | deepseek-harness |
| --- | --- | --- | --- |
| **優點** | catalog 有預算上限。skill 能 fork，還能限制 tool。 | curator 會整併新 skill、封存過期的。 | catalog 放在對話歷史裡，內容一變就換新的。 |
| **限制** | 描述太含糊，模型就不會去載入。 | 自動改動需要釘選和暫存核准來把關。 | 每次換掉 catalog 都會往歷史裡多塞訊息。 |
| **設計原因** | skill 還要 fork、還要限制 tool，單純讀檔不夠用。 | 載入只是一半，store 本身還要能成長、能汰舊。 | session 跑到一半，skill 就可能變了。 |
| **做法：skill format** | `SKILL.md` 資料夾，frontmatter 還能限制可用的 tool。 | 同樣的形式，依分類資料夾整理。 | 一個資料夾或一個扁平檔案。誰能呼叫寫在 frontmatter。 |
| **做法：load trigger** | invoke `Skill` tool 注入本文；動到符合的檔案也會觸發。 | `skill_view` 回傳本文，並累計使用次數。 | 要用到的時候，一個 tool 才去現讀本文。 |
| **做法：discovery** | built-in、user、project、plugin、MCP 來源。 | bundled、optional、user、plugin、hub 來源。 | 註冊的 provider 疊在分層的 scope 上，根目錄有排名。 |

---

## 常見問題

- **skill 從不觸發：**描述太含糊。把觸發條件直接寫進描述裡。
- **catalog 變得太大：**skill 太多會擠爆 prompt。讓 skill 保持聚焦，並讓 loader 做裁剪。
- **壓縮後本文遺失：**重新讀取該 skill 檔案，或讓本文保持簡短。
- **Path traversal：**catalog 會把路徑交給模型。把 Read tool 的範圍限制在 skills 目錄，讓 `../` 無法逃出去。
- **forked skill 失去即時 context：**只在自成一體的工作上使用 forked skill。
- **供應鏈裡的毒 skill：**裝進來的第三方 skill 本質是外部內容，卻是當成指令載入的。
  這比一個被下毒的網頁還危險，因為 catalog 已經替它背書了。安裝前先把本文和附帶的 script 都讀過，版本要釘住，更新時再看一次。
- **注入的文字變成永久的：**`messages[]` 裡的 prompt injection，session 結束就沒了；同一段文字寫進 `SKILL.md`，之後每次執行都會載入。
  所以沒審過的外部內容，絕不能餵給 `WriteSkill`。新 skill 先當 candidate 放著，等另一道流程核准。也絕不讓 skill 去改那道核准閘門。
- **使用次數會高估學習成效：**載入不等於照做。次數只說明 catalog 路由對了，不代表這個 skill 改變了結果。
  要看兩個數字：skill 有沒有被觸發，以及那次執行有沒有變好。

---

## 動手跑跑看

[`src/`](src/) 沿用 06 並加上：

- [`skills.py`](src/skills.py)：catalog 掃描、system prompt 列表、限定範圍的 `Read` tool，以及演化那一半（`WriteSkill`、`record_use`、`stale_skills`）。
- `skills/<name>/SKILL.md`：範例 skill，包含一個帶有 resource 檔案的 skill。
- [`loop.py`](src/loop.py)：未變動，因為載入一個 skill 只是讀一個檔案。
- [`test.py`](src/test.py)：檢查 catalog 掃描、prompt 列表、檔案載入、path traversal 的拒絕、使用計數、staleness，以及 agent 寫出的 skill 進入 catalog。
- [`demo.py`](src/demo.py)：agent 用了一個 skill，接著存下一個新的；收尾的掃描顯示 store 長大了。

```bash
python sections/07-skills/src/test.py         # offline checks, no key
uv run python sections/07-skills/src/demo.py  # live demo, needs a key
```

---

## 參考資料

- [Claude Code 原始碼](https://github.com/yasasbanukaofficial/claude-code)：
  `skills/loadSkillsDir.ts`、`skills/bundledSkills.ts`、`skills/mcpSkillBuilders.ts`、`tools/SkillTool/SkillTool.ts`、`tools/SkillTool/prompt.ts`。
- [Hermes Agent 原始碼](https://github.com/NousResearch/hermes-agent)：
  `tools/skills_tool.py`（`skills_list`、`skill_view`）、`tools/skill_usage.py`、`hermes_cli/curator.py`、`tools/skills_hub.py`、`tools/skills_ast_audit.py`。
- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness)（`dsh-v0.1.0-rc.7`）：
  `packages/skill/skill/src/index.ts`、`packages/skill/skill-filesystem/src/index.ts`、`packages/skill/tool-skill/src/index.ts`、
  `docs/subsystems/skills.md`、`docs/tool-catalog.md`。
- [Anthropic Agent Skills best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices)：progressive disclosure 的層級。
- [learn-claude-code · s07_skill_loading](https://github.com/shareAI-lab/learn-claude-code)：章節框架。
- [ai-agent-book](https://github.com/bojieli/ai-agent-book)：`book/chapter2.md`、`book/chapter8.md`，以中文原版為準。
- [Agent Skills open standard](https://agentskills.io)：catalog 放哪裡，system prompt 或啟用用 tool 的 description。
- [Claude Code · prompt caching](https://code.claude.com/docs/en/prompt-caching)：載入的 skill 本文會落在哪裡、成本是多少。
- [Voyager](https://arxiv.org/abs/2305.16291)：環境驗證過，skill 才進得了 library。
- [Anthropic Skill Creator](https://github.com/anthropics/skills)：升級之前先起草、測試、評估、修訂。
- Lin et al.，[arXiv:2605.30621](https://arxiv.org/abs/2605.30621)，轉引自書：更新有沒有落地、有沒有幫上忙，是兩個要分開量的數字。
