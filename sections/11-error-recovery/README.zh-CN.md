# 11 · Error recovery

[English](README.md) · [繁体中文](README.zh-TW.md) · **简体中文**

> 先判断错误类型，再决定要重试、调整，还是停止。

一次 agent 执行通常包含多次模型调用，其中任何一步都可能因网络问题、服务过载、rate limit、输出上限或 context overflow 而失败。

而且会出错的不只有模型调用。针对 production coding agent 的研究，通常把失败分成四个层级：

- **API：**timeout、rate limit 和过载。
- **tool：**指令返回非零，或是 handler 抛出异常。
- **context：**prompt overflow，或是 API 不收的消息历史。
- **control flow：**一直重复、却走不到任何地方的步骤。

先确认错误发生在哪一层，再决定重试次数。若顺序反过来，预算很容易浪费在重试也无法解决的问题上。

loop 对不同的失败需要不同的响应：

1. 暂时性错误可以重试。
2. prompt 或输出上限造成的问题，要先调整内容再重试。
3. 确定无法复原时，就停止。

没有复原机制，一次短暂的 API 故障就可能让执行已久的任务前功尽弃。

---

## 核心机制

![机制图](assets/11-error-recovery.png)

最直接的做法，是用 retry helper 包住模型调用。它先判断错误类型，再执行次数受限的复原策略。

- 暂时性的状态码会退避后重试。
- prompt overflow 会执行一次压缩 callback，然后重试。
- 反覆的过载可以触发 fallback model。
- 未知或不可重试的错误会被抛出。

### 本章添加：分类、backoff 与 retry helper

```python
RETRY_STATUS = {408, 409, 429}                         # src/recovery.py; these plus any 5xx

def should_retry(status) -> bool:
    return status in RETRY_STATUS or (status is not None and 500 <= status < 600)

def retry_delay(attempt, retry_after=None) -> float:   # exponential backoff + jitter
    if retry_after is not None:
        return float(retry_after)
    base = min(BASE_DELAY * 2 ** (attempt - 1), MAX_DELAY)
    return base + base * 0.25 * random()
```

overflow 会在一般状态处理之前先检查。如果压缩能缩小 prompt，`prompt_too_long` 错误就是可复原的。

```python
def _status(e):
    return getattr(e, "status_code", None)

def _is_overflow(e) -> bool:
    return getattr(e, "overflow", False) or "prompt is too long" in str(e).lower()
```

`with_retry` 持有每次尝试的状态：

```python
def with_retry(call, on_overflow=None, fallback_model=None,
               max_retries=DEFAULT_MAX_RETRIES, sleep=time.sleep):
    consecutive_529 = 0
    overflowed = False
    for attempt in range(1, max_retries + 2):
        try:
            return call()
        except Exception as e:
            if _is_overflow(e):
                if on_overflow is None or overflowed:
                    raise
                overflowed = True
                on_overflow()
                continue
            status = _status(e)
            if status is None:
                raise
            if status == 529:
                consecutive_529 += 1
                if fallback_model and consecutive_529 >= MAX_529_RETRIES:
                    raise FallbackTriggered(fallback_model)
            if attempt > max_retries or not should_retry(status):
                raise
            sleep(retry_delay(attempt, getattr(e, "retry_after", None)))
```

### 如何集成到现有架构

loop 把它的模型调用包起来：

```python
response = recovery.with_retry(
    lambda: model(messages, registry, system),
    on_overflow=lambda: _reactive_trim(messages),
    fallback_model=fallback_model)
```

- Recovery 只包住模型调用。
- `_reactive_trim` 就地修改 `messages[]`，供一次 overflow 重试使用。
- 当 recovery 放弃时，错误会被浮现出来，而不是被藏起来。

### 延伸阅读

以下设计 `src/` 都没有实现，出自 ai-agent-book，也未经下面表格的系统证实。

**怎么抓到不会抛出异常的 loop：**假设 agent 跑了一次测试、读到同一个错误，然后又把同一份测试跑一次。
没有东西会抛出异常，所以重试路径一条也不会启动，任何界限也都碰不到。这是 control flow 层的失败，得自己配一套检测。

检测靠的是 fingerprint：tool 名称加上参数。同一个 fingerprint 又出现，就是 agent 在重做同一次调用。
步数上限的确会结束这次执行，但那时整份预算都花光了。fingerprint 计数器几步之内就能结束，而且说得出是哪一次调用卡住。

复原路径也各自需要计数器。每条路各数各的失败次数，一直失败的那条就会自己跳闸，不用等到全局上限。

**连上了却没声音的 stream 怎么收掉：**stream 可能接得上、吐了几个 token，然后就停住。
那个时候 connect timeout 早就过了，所以什么都不会触发，loop 就一直等下去。

解法是再加一个计时器。在 connect timeout 旁边放一个 idle watchdog，时间窗内没有 token 进来就取消这次调用。
接着 retry helper 会把这次取消当成一般的暂时性失败来处理。

**消息历史坏掉之后怎么补：**一轮中途 crash，可能留下一个没有对应 `tool_result` 的 `tool_use` block。
下一次请求会卡在消息格式，而不是卡在工作本身，而且成对关系没修好之前，它会一直卡在那里。

至于「修」是什么意思，得看这份 transcript 是拿来做什么的。答案有两种：

- **产品用的 harness 会修：**塞一个 placeholder 结果，写明这次调用被中断，执行就能继续。
- **录训练数据的 harness 不修：**编一个结果出来，等于教模型一个根本没发生过的步骤。

**失败要让调用方看到多少：**复原不是一个决定。要照失败该有多明显来分级：

1. **安静重试：**调用方只看得到最后的结果。
2. **降级后继续：**返回一份缩水的结果，并说清楚少了什么。
3. **把失败浮现出来：**列出试过哪些方法，让模型可以换一条路走。

前两级产生的错误需要隔离。先留在 helper 里面，等复原真的放弃了才放出去。
太早传到模型面前的错误会被当成最终结果，模型可能因此重做一件其实已经成功的工作。

**别让复原自己喂自己：**错误路径上可能会触发 hook、摘要或通知。
那些事情又去调用一次模型，然后又失败一次，这次失败又把同一条路径再触发一次。

有两条规则可以把这串断开。第一，在错误路径上把带副作用的逻辑关掉。第二，带一个递回深度计数器收拾漏网的。
后台调用则完全不重试：它们不在关键路径上，重试只会把主 loop 需要的额度花掉。

**这些界限是怎么订出来的：**这一节每个界限都是有人挑出来的数字：重试几次、失败几次就停、idle 的时间窗多长。
每一个都要照实际量到的失败来挑，不是靠直觉。书里「压缩试三次就停」这个界限，就是从生产环境反覆复原失败的数据里得出来的。

---

## 不同系统怎么做

Recovery 包住模型调用。loop 主体维持不变。

| | Claude Code | mini-swe-agent | deepseek-harness |
| --- | --- | --- | --- |
| **优点** | 针对性的复原路径，救回的 run 比一概重试更多。 | 只有三条路径要维护，crash 也留得下完整轨迹。 | 每次重试都写进 log，session 续跑后也知道自己重试过什么。 |
| **限制** | 要维护的分支与界限更多。 | 救回的 run 较少。overflow 会中止，连续三次格式错误也会。 | 没有 fallback 模型。always 模式会无上限地一直重试。 |
| **设计原因** | 一次暂时的 API 失败不该终结长任务。 | 重试、把格式错误还给模型，其余命名退出。 | log 才是事实，所以复原是重开一个 turn 重放。 |
| **做法：retry** | 带退避重试 429、408、409 和 5xx，`retry-after` 优先。 | tenacity 退避 4 到 60 秒，最多 10 次。 | 失败的 turn 收掉后发一则错误事件，接着开新的 turn。 |
| **做法：token handling** | 提高输出上限、在 `max_tokens` 停止后续写，或压缩。 | 没有，overflow 直接中止 run。 | 一个统一的 overflow 代码，先修剪再摘要。 |
| **做法：model fallback** | 反覆过载（529）后改用 fallback 模型。 | 没有。 | 没有。重试的 turn 会重建同一个请求。 |

---

## 常见问题

- **Retry storm：**许多 client 同时对过载重试会让负载更糟。限制重试次数并尊重 `retry-after`。
- **无限复原：**提高上限、续写和压缩都可能无限 loop。为每条路径设界限。
- **overflow 无法缩小：**如果一次 reactive compaction 失败，就停止，而不是永无止境地压缩。
- **错误消失：**一个被吞掉的错误会让 transcript 少了结果。在复原用尽之后，把失败浮现出来。
- **Stop hook 重播 API 错误：**对 API 错误消息略过 stop hook。
- **卡住了，却没有错误：**一直重复的调用不会抛出任何东西，重试路径一条也不会启动。数重复的 tool 加参数 fingerprint，把这次执行停掉。
- **stream 静静停住：**stream 可能接上之后就没声音。这时 connect timeout 早就过了，什么都不会触发。加一个 idle watchdog。
- **修补污染了记录：**塞一个 placeholder `tool_result`，产品环境的执行是活下去了，但也记下了一个根本没跑过的步骤。transcript 要留着当训练数据，就别修。
- **中间错误外泄：**复原还没结束就送出去的错误，会被当成最终结果，模型就白做一轮工。先留在 helper 里，等复原放弃了再放出去。

---

## 动手跑跑看

[`src/`](src/) 承接 10 并加入：

- [`recovery.py`](src/recovery.py)：重试分类、退避、overflow 处理，以及 fallback 触发。
- [`loop.py`](src/loop.py)：把它的模型调用包在 `with_retry` 里。
- [`test.py`](src/test.py)：用一个假的不稳定调用驱动每一条路径。
- [`demo.py`](src/demo.py)：在一次 live 执行中注入一次模拟过载。

```bash
python sections/11-error-recovery/src/test.py         # offline checks, no key
uv run python sections/11-error-recovery/src/demo.py  # live demo, needs a key
```

---

## 参考资料

- [Claude Code 源代码](https://github.com/yasasbanukaofficial/claude-code)：
  `services/api/withRetry.ts`、`query.ts`、`services/api/claude.ts`、`services/api/errors.ts`、`query/tokenBudget.ts`、`utils/context.ts`。
- [mini-swe-agent source](https://github.com/swe-agent/mini-swe-agent)：
  `models/utils/retry.py`、`models/litellm_model.py`、`agents/default.py` 的 `run()` 与 `max_consecutive_format_errors`。
- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness)（`dsh-v0.1.0-rc.7`）：
  `packages/llm/llm-retry/README.md`、`packages/llm/llm-retry/src/types.ts`、`packages/core/agent-loop/src/agent.ts`、
  `docs/subsystems/llm-streaming.md`、`docs/subsystems/core.md`、`docs/subsystems/persistence.md`。
- [ai-agent-book · 第 5 章](https://github.com/bojieli/ai-agent-book/blob/main/book/chapter5.md)（《深入理解 AI Agent》，李博杰，以中文原版为准）：
  四层失败分类、用 tool 加参数做 loop fingerprint、idle watchdog、`tool_result` 成对修补以及产品与训练数据两套标准、
  分级复原加错误隔离，还有防死亡螺旋的那几招。它的注脚 ch5-3 说这套分类来自对生产环境 agent 的研究（其中包含 Claude Code），
  也提醒实现变动很快。书里「压缩试三次就停」这个界限，同样是从量到的生产环境失败里订出来的。
- [learn-claude-code · s11_error_recovery](https://github.com/shareAI-lab/learn-claude-code)：章节框架。
