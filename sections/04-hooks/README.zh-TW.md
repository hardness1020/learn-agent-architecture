# 4 · Hooks

[English](README.md) · **繁體中文** · [简体中文](README.zh-CN.md)

> hook 讓你在 loop 的固定節點插入額外行為。

hook 是可以自行設定的 callback，能在工具呼叫前後、prompt 送出時，或 session 開始與結束時執行。

記錄、驗證、通知和簡單的政策檢查都很適合用 hook。少了這層擴充點，每加入一種行為都得修改 loop，甚至另外維護一份分支。

hook 的價值在於讓 loop 保持精簡。loop 只需要公開固定的生命週期事件，其他行為再掛到對應事件上。

---

## 核心機制

![機制圖](assets/04-hooks.png)

`Hooks` 物件會把事件名稱對應到一組 callback。loop 不必直接知道有哪些自訂檢查，只要由 `_dispatch` 在正確的時間觸發具名事件。

在工具執行方面，有兩個重要的點：

- `PreToolUse` 在 permission gate 之前執行。它可以擋下呼叫，或改寫輸入。
- `PostToolUse` 在工具呼叫成功之後執行。它可以觀察結果。

### 本章新增：hook

```python
class Hooks:                                     # src/hooks.py
    def fire_pre(self, name, args):               # PreToolUse: block or rewrite
        for fn in self._hooks["PreToolUse"]:
            out = fn(name, args) or {}
            if out.get("updated_args"): args = out["updated_args"]
            if out.get("deny"):         return True, args, out.get("message", "")
        return False, args, ""
    def fire_post(self, name, args, result):      # PostToolUse: observe
        for fn in self._hooks["PostToolUse"]: fn(name, args, result)
```

- `on(event, fn)` 註冊一個 callback。
- `fire_pre` 執行 `PreToolUse` 的 callback。
- pre-hook 可以回傳 `{"deny": True}` 來擋下呼叫。
- pre-hook 可以回傳 `{"updated_args": ...}` 來改寫輸入。
- `fire_post` 在執行之後跑觀察者。

### 如何接進現有架構

`_dispatch` 加入了兩個呼叫：

```python
# src/loop.py _dispatch
blocked, args, msg = hooks.fire_pre(name, args)          # 4 · PreToolUse
if blocked: return res(msg)
decision = permissions.decide(tool, mode, allow_rules)   # 3 · gate (section 3)
...                                                      # deny / ask short-circuit
out = res(run_tool(tool, args))                          # 2 · execute -> tool_result
hooks.fire_post(name, args, out)                         # 4 · PostToolUse
```

- 被擋下或被拒絕的呼叫永遠不會抵達 `run_tool`。
- `PostToolUse` 只在成功執行之後才會跑。
- hook 可以收緊 permission 的結果，但不應該放寬它。
- 在 Claude Code 中，`resolveHookPermissionDecision` 會把 hook 輸出和以規則為基礎的 permission 加以協調。

demo 用一個 `PreToolUse` hook，即使在 `bypassPermissions` 之下也擋下 `rm -rf`。

本章談的是生命週期 hook。放在 `hooks/` 資料夾中的 React render hook，是不相干的 UI 程式碼，只是共用同一個字。

### 對照：waterfall hooks

Claude Code 的 hook 是一條外部指令：harness 開一個子行程去跑它，再讀它的 exit code 和輸出。
deepseek-harness 的 hook 則是一個普通函式，直接在 harness 行程裡跑。
它掛在一個具名事件上，例如工具呼叫前會觸發的那個事件。
它回傳的也不是 exit code，而是型別化決策：deny、ask、allow 這種普通的值。

同一個事件可以掛好幾個 hook。它們排成一條鏈，事件觸發時只會跑第一個。
每個 hook 拿到事件資料和一個 `next()` callback，然後二選一：

- 不呼叫 `next()`，直接回傳決策。鏈就停在這裡，排在後面的 hook 都不會跑。
- 呼叫 `next()`，讓鏈的其餘部分先決定，這個 hook 再把那個結果回傳，可以原樣回傳，也可以先改一下。

dsh 把這種派發方式叫做 waterfall。原本 Claude Code 的 shell hook 也還能用：一個 bridge 幫忙執行它們，
把輸出轉成同樣的型別化決策。好幾個 shell hook 同時回答時，bridge 取最嚴格的那個：deny 蓋過 ask，ask 蓋過 allow。

[`src/waterfall.py`](src/waterfall.py) 就是這套機制的 strip-down。它是對照用的 demo，沒有接進 `_dispatch`，後面的章節照樣沿用原本的 loop。

### 延伸閱讀

以下設計 `src/` 都沒有實作，出自 ai-agent-book，也未經下面表格的系統證實。

例子是寫入後跑 lint。write 或 edit 工具一回傳，hook 就對剛改過的那個檔案跑 linter，
再把診斷訊息加進 tool result。模型下一輪就會看到這個錯誤，位置就在寫入成功的訊息旁邊。
少了這個 hook，同樣的錯誤要等到下次 build 或跑測試才會冒出來。

這個做法成本低，有兩個原因。

- 診斷訊息是包在 tool result 裡一起回去的，不用多跑一輪。
- 檢查只跑一個檔案，不是整個專案，花的時間跟那次寫入差不多。

這個做法有一個限制。寫入被擋下就不會執行，hook 也就不會有診斷訊息可以加。

---

## 不同系統怎麼做

各個 agent 如何在 loop 周圍提供攔截點。

| | Claude Code | deepseek-harness |
| --- | --- | --- |
| **優點** | 使用者不必改動 loop 就能擴充行為。適合做記錄、驗證、通知和政策檢查。 | hook 是行程內的 plugin，既有的 shell hook 照樣能跑。 |
| **限制** | 固定的事件清單同時也是它的界限。hook 只能在系統對外提供事件的地方進行攔截。 | 兩套 hook 做法都要學。bridge 只涵蓋一部分事件，也不能改寫工具輸入。 |
| **設計原因** | 讓 loop 保持精簡。新行為掛接到固定事件上，不用改動或分岔 loop。 | 擴充用的介面，就是 harness 自己在跑的那套事件系統。 |
| **做法：hook events** | 固定的 27 個生命週期事件，涵蓋 tool、prompt、session、stop、subagent、compact 與 setup。 | 每個階段都有 waterfall 和 serial 事件，shell hook 靠 bridge 接上來。 |
| **做法：fire point** | 從 settings 載入，啟動時凍結。`PreToolUse` 在 permission gate 之前觸發。 | 在 pre-execute waterfall 裡，位在只會拒絕的 guard 之前。 |
| **做法：can block or modify?** | 可以。拒絕、詢問、更新輸入、加入 context，或停止。hook 輸出會和以規則為基礎的 permission 加以協調。 | 可以，靠型別化決策。多個 shell hook 取最嚴格的：deny > ask > allow。 |

---

## 常見問題

- **hook 繞過 permission：**hook 可能試圖允許一個已被拒絕的動作。要把 hook 輸出對照以規則為基礎的 permission 來解析。
- **Stop hook 無限 loop：**一個 `Stop` hook 可能擋下、觸發自我修正，然後又再次觸發。要追蹤 stop hook 是否已經在運作中。
- **hook 設定在 session 中途改變：**某個程序可能在啟動後修改 settings。要對 hook 設定做一次快照。
- **慢速 hook 卡住 loop：**hook 可能 shell out 去做很慢的工作。要加上 timeout。
- **PostToolUse 意外停止：**若 post-hook 回傳 `preventContinuation`，要把它呈現為一個優雅的停止，而不是崩潰。
- **診斷訊息淹沒結果：**整個專案跑一次 lint，回來的文字可能比寫入本身還多。只檢查剛改過的那個檔案，加回去的量也要設上限。

---

## 動手跑跑看

[`src/`](src/) 承接 03 並加上：

- [`hooks.py`](src/hooks.py)：帶有 `fire_pre` 與 `fire_post` 的 `Hooks` 物件。
- [`loop.py`](src/loop.py)：`_dispatch` 在 gate 之前觸發 `PreToolUse`，在執行之後觸發 `PostToolUse`。
- [`waterfall.py`](src/waterfall.py)：deepseek-harness 的對照：一條 hook 鏈，用 `next()` 往下傳，多個結果取最嚴格的（deny > ask > allow）。
- [`test.py`](src/test.py)：一個 pre-hook 即使在 `bypassPermissions` 之下也擋下 `rm -rf`；waterfall 檢查涵蓋直接決定、交給下游，和取最嚴格。

```bash
python sections/04-hooks/src/test.py         # offline checks, no key
uv run python sections/04-hooks/src/demo.py  # live demo, needs a key
```

---

## 參考資料

- [Claude Code 原始碼](https://github.com/yasasbanukaofficial/claude-code)：
  `types/hooks.ts`、`entrypoints/sdk/coreTypes.ts`、`services/tools/toolHooks.ts`、`query/stopHooks.ts`、`services/tools/toolExecution.ts`、`setup.ts`。
- [deepseek-harness 原始碼](https://github.com/deepseek-ai/deepseek-harness)（`dsh-v0.1.0-rc.7`）：
  `packages/hooks/README.md`、`packages/hooks/hooks-claude-code/README.md`、`packages/hooks/hook-protocol/README.md`、
  `docs/cordis-primer.md`、`docs/subsystems/core.md`。
- [learn-claude-code · s04_hooks](https://github.com/shareAI-lab/learn-claude-code)：section framing。
- [ai-agent-book · 第 5 章](https://github.com/bojieli/ai-agent-book/blob/main/book/chapter5.md)（《深入理解 AI Agent》，李博杰，以中文原版為準）：
  寫入後跑 lint：工具層在寫入之後跑 linter，把診斷訊息加進 tool result。
