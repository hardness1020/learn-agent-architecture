# 10 · System prompt assembly

[English](README.md) · **繁體中文** · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · [한국어](README.ko.md)

> 每一輪都根據當下狀態，重新產生真正需要的 system prompt。

system prompt 是 agent 的常駐指令集，會說明身分、規則、可用工具、專案 context，以及目前啟用的功能。

在實際的 agent 系統中，它不能只是一段寫死的文字。

工具、memory、輸出風格、MCP server 和執行模式都可能隨 session 改變，prompt 必須反映當下真正啟用的內容。

一個 prompt 組裝器解決三個問題：

1. 每項功能都有固定的位置加入自己的指令。
2. 未啟用的功能不會出現在 prompt 中。
3. 穩定不變的段落可以使用 prompt caching。

沒有明確的組裝機制，prompt 很快就會變得過時、臃腫，也難以安全修改。

---

## 核心機制

![機制圖](assets/10-system-prompt-assembly.png)

把 prompt 拆成一組具名段落。有些內容固定不變，有些會根據即時狀態產生；不適用的段落直接回傳 `None`。

組裝流程很單純：依序解析每個段落，略過 `None`，再把剩下的內容串起來。

```python
sections = [
    intro, system_rules, doing_tasks, tools_section,
    session_guidance(), memory(), env_info(),
    output_style(), mcp_instructions(),
]
prompt = [s for s in resolve(sections) if s is not None]
```

兩條規則讓它保持可控：

1. 依狀態納入段落，不要靠關鍵字猜測。
2. 讓易變的內容遠離穩定的 prompt 前綴。

### 本章新增：段落與組裝

```python
@dataclass
class Section:                                          # src/prompt.py
    name: str
    compute: Callable    # (state) -> str | None ; static sections ignore state

def static(name, text) -> Section:
    return Section(name, lambda _state: text)

def assemble(sections, state) -> str:                  # the prompt for this turn
    parts = (s.compute(state) for s in sections)
    return "\n\n".join(p for p in parts if p is not None)
```

哪些段落要依狀態納入，由這份段落清單自己掌管。`compute` 回傳 `None` 就略過：

```python
DEMO_SECTIONS = [
    static("intro", "You are a tiny agent. ..."),
    Section("tools", lambda s: "Tools: " + ", ".join(s["tools"]) if s.get("tools") else None),
    Section("env", lambda s: f"cwd: {s['cwd']}" if s.get("cwd") else None),
    Section("mcp", lambda s: "MCP servers connected; ..." if s.get("mcp") else None),
]
```

第 9 章回想出的記憶不放進 system prompt，而是用一則 `<system-reminder>` 訊息注入對話。這樣 prompt 前綴不會跟著記憶變動，cache 比較守得住。

### Prompt caching

大多數 system prompt 段落在一次 session 中是穩定的。demo 設了一個頂層的 cache 斷點：

```python
client.messages.create(model=MODEL, system=assemble(DEMO_SECTIONS, state),
                       messages=messages, cache_control={"type": "ephemeral"})
```

穩定的內容應該排在易變的內容之前。如果一個會變動的值出現在前面，它可能會讓更多 cache 失效。

這條規則之所以嚴格，是因為價目表。cache 是拿 token 前綴精確比對的。
前面改掉一個 token，它後面所有 cache 過的 token 就全部作廢。讀 cache 大約只要新鮮 input token 的十分之一，寫 cache 反而比新鮮的還貴。
所以挪動一個字，就可能讓原本吃得到 cache 的呼叫變成全價。
最常見的原因有兩個：prompt 開頭附近印了時間戳或 token 數，以及工具清單的順序每次跑都不一樣。

Claude Code 也使用一個明確的動態邊界。當較小的動態尾段變動時，這能保護一大段靜態前綴。

### 如何接進現有架構

loop 會在每次模型呼叫前產生 prompt：

```python
for _ in range(max_steps):                             # src/loop.py
    messages = context.manage(messages, summarizer=summarizer)
    system = prompt(registry, session) if prompt else None   # 10 · assemble from live state
    response = model(messages, registry, system)
    ...
```

- `prompt` 是一個閉包住段落清單的可呼叫物件。
- 它讀取即時狀態，例如啟用中的工具和 session 模式。
- 傳入 `prompt=None` 會維持第 9 章的行為。

### 對照：段落 registry

上面那份清單寫死在一個檔案裡。要加段落就得改那個檔案，而檔案裡的先後順序就是 prompt 的順序。

deepseek-harness 則改由註冊表組裝 prompt。每個 plugin 註冊一個具名段落，再用數字決定排列位置。
數字照慣例分成幾個區段：harness 身分最前面，接著是部署方的 persona，再來才是工具指引。
組裝時照數字排序，所以 plugin 不必知道別人註冊了什麼，也能找到自己的位置。

registry 還帶來兩條規則。

- 某個 agent 可以用一個已經存在的名字，註冊自己的段落。那個 agent 看到的是自己那份，其他人照樣用共用的。
- 段落文字裡可以放 `{{variables}}`，而且 render 很嚴格。名字對不上就直接報錯，不會讓 prompt 帶著一個洞送出去。

會變的事實不放進這份 prompt。它們以快照的形式接到對話後面，而且只有 render 出來的文字真的變了才接，前綴因此對 cache 友善。

[`src/registry.py`](src/registry.py) 就是這件事的精簡版。它是對照用的 demo，沒有接進 `assemble()`，所以後面的章節照樣沿用同一套 prompt 程式碼。

### 延伸閱讀

以下設計 `src/` 都沒有實作，出自 ai-agent-book，也未經下面表格的系統證實。

**邊界前面每加一個條件，前綴就多一份：**邊界之前放一個看執行期狀況決定的條件，cache 就得存兩份前綴，條件的每種結果各一份。
三個條件變八份。十個條件超過一千份，而且每一份都要各自暖機，等於幾乎每個 session 都是冷啟動。
把有條件的段落挪到邊界之後，前綴就又只剩一份。

**一種任務類型挑一組示例，挑完就別再動：**few-shot 示例也放在前綴裡，所以上面那條規則一樣管得到它。
每次請求都去挑最相關的示例，等於每次呼叫都重寫前綴，cache 就用不上了。
固定一組示例，貼合度是會差一點，但整個 session 的前綴都是熱的。

**狀態列讓模型知道現在跑到哪：**模型看不到 harness，所以有些 harness 會把執行期狀態寫成 context 尾端的幾行字：

- 已經呼叫過幾次 tool
- 當前的 TODO
- 過了多久
- 工作目錄

這幾行要一直保持最新，做法有兩種，兩種都有代價。
每一輪就地換掉這一段，狀態就只有一份是真的，但尾巴被重寫，它後面的 cache 也沒了。
每一輪往後追加一段新的，cache 保得住，但舊的段落會留在歷史裡，模型可能照著已經變掉的狀態行動。
Claude Code 走的是追加，用的就是第 9 章那些 `<system-reminder>` 訊息。
不管選哪一種，這段文字都要用程式讀真實狀態產生。叫 LLM 去摘要，會多一次呼叫、多一份延遲，還可能寫錯。

**外面來的文字是資料，不是命令：**抓回來的網頁、檔案、issue 留言、MCP server 的回應，全都是資料，沒有一句是使用者說的。
這些文字沒有任何標記就送進去，裡面某一句長得像指令的話，就會跟 system prompt 平起平坐。這就是 prompt injection。
威脅模型和執行層的解法歸第 3 章管：權限和 sandbox 決定一個被挾持的 agent 到底能做什麼。
prompt 這一層可以更早一步動手，做法是把指令和資料分開：

- 外部內容包在有標記的區塊裡，標明它從哪裡來。並在 prompt 裡講清楚：標記過的內容是拿來讀的資料，不是要照做的指令。
- role 分得乾淨。指令走 system prompt，結果走 `tool_result` 區塊，人講的話走 user turn。
- 忠誠對象在 prompt 裡講一次：agent 為使用者和營運方工作，任何從 tool 進來的文字都改不了這件事。書裡把這叫 principal loyalty。

**prompt 這一層不是邊界：**模型還是可能被說服而違規，所以第 3 章的檢查照樣要跑。
prompt 層降低發生機率，執行層限制損害範圍。

---

## 不同系統怎麼做

每一輪如何產生 prompt。

| | Claude Code | mini-swe-agent | deepseek-harness |
| --- | --- | --- | --- |
| **優點** | 不會留著過時的指令，工具指引對得上啟用中的工具集。 | 只從 config render 一次，沒有東西要失效。 | 每一項 prompt 事實都有一個負責人，引用錯了會直接報錯。 |
| **限制** | 多了段落 registry、cache 失效規則和排序紀律。 | prompt 在 run 中途改不了。 | registry、scope 和排序區段，全都是要維護的機制。 |
| **設計原因** | 工具、記憶和模式會因 session 而異。 | 假設工具集在 run 中途不會變。 | plugin 各自擁有自己的事實，所以 prompt 由多個段落組裝而成，不是直接修改字串。 |
| **做法：assembly point** | 一個 prompt 組裝器，每個段落各回傳一個字串。 | config 裡的 Jinja2 template，變數缺了會直接報錯。 | 一個 registry，加上每個 scope 都能調整的組裝事件。 |
| **做法：sections** | 靜態與動態段落，專案脈絡走 context 訊息。 | 兩份 template：system 與 instance。 | 有名字的段落排在數字區段裡，scope 可以用同名蓋掉。 |
| **做法：when built** | 每一輪根據即時狀態產生，動態段落會被 memoize。 | 只在 run 開始時組一次。 | 每一步組一次。會變的事實改以快照附加。 |

---

## 常見問題

- **易變文字打壞 cache：**把會變動的內容放到後面，或放到 prompt 前綴之外。
- **段落 cache 過時：**當 session 狀態改變時，清掉被記憶的段落。
- **Prompt 提到不存在的工具：**從即時啟用的工具集生成工具文字。
- **context 混進 prompt：**當專案檔案、日期和 git 狀態經常變動時，把它們放進 context 訊息。
- **Prompt 覆寫互相衝突：**用單一 resolver 定義優先順序。
- **cache key 變太多份：**邊界之前每多一個條件，要各自暖機的前綴就翻倍。有條件的段落一律放到邊界之後。
- **狀態區塊過期：**追加式的狀態會越積越多，模型可能照著舊的那一份行動。標清楚哪一份最新，或是就地換掉並接受 cache 重建。
- **外部內容被當成指令：**tool 結果照來源加上標記，並在 prompt 裡說明標記過的就是資料。真正的邊界仍然是第 3 章的權限檢查。

---

## 動手跑跑看

[`src/`](src/) 承接 09 並加入：

- [`prompt.py`](src/prompt.py)：`Section`、`static` 和 `assemble`。
- [`registry.py`](src/registry.py)：deepseek-harness 的對照：段落註冊時帶一個排序數字，scope 可以蓋掉同名段落，`{{variable}}` 嚴格 render。
- [`loop.py`](src/loop.py)：每一輪重新產生 prompt。
- [`demo.py`](src/demo.py)：加入頂層的 `cache_control`。
- [`test.py`](src/test.py)：檢查段落會依狀態正確納入或略過；registry 的檢查涵蓋排序、同名覆蓋，以及變數對不上就報錯。

```bash
python sections/10-system-prompt/src/test.py         # offline checks, no key
uv run python sections/10-system-prompt/src/demo.py  # live demo, needs a key
```

---

## 參考資料

- [Claude Code 原始碼](https://github.com/yasasbanukaofficial/claude-code)：`constants/prompts.ts`、`constants/systemPromptSections.ts`、`utils/api.ts`、`QueryEngine.ts`。
- [mini-swe-agent source](https://github.com/swe-agent/mini-swe-agent)：`config/mini.yaml`、
  `agents/default.py` 的 `_render_template` 與 `get_template_vars`、`models/utils/cache_control.py`。
- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness)（`dsh-v0.1.0-rc.7`）：
  `packages/core/system-prompt/README.md`、`packages/core/system-prompt/src/index.ts`、`packages/core/agent-loop/src/runtime-context.ts`、
  `docs/subsystems/system-prompt.md`、`docs/agent-lifecycle.md`。
- [Anthropic prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)：cache 斷點、TTL、定價，以及 token 下限。
- [Claude Code prompt caching 文件](https://code.claude.com/docs/en/prompt-caching)：靜態前綴與動態尾段之間那道明確的 cache 邊界。
- [ai-agent-book · 第 2 章](https://github.com/bojieli/ai-agent-book/blob/main/book/chapter2.md)（《深入理解 AI Agent》，李博杰，以中文原版為準）：
  KV cache 的成本模型、cache 作為架構約束（邊界之前的條件會讓 cache key 翻倍）、few-shot 的前綴穩定性、
  agent 狀態列以及就地替換和往後追加之間的取捨，還有 context 層的注入防禦與 principal loyalty。
  書中的狀態列與 loyalty 數據都是作者自己的評測，屬於單一來源，這裡不引用那些數字。
- [learn-claude-code · s10_system_prompt](https://github.com/shareAI-lab/learn-claude-code)：章節框架。
