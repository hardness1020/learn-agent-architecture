# 3 · Permission & sandbox

[English](README.md) · [繁体中文](README.zh-TW.md) · **简体中文**

> 每个动作真正影响系统前，都必须先通过检查。

模型可以要求使用任何已启用的工具，permission 层则负责判断这次调用能不能真的执行。

没有 permission gate 的工具运行环境，几乎等同于一个没人看管的远端 shell。

一次错误的工具调用就可能删除文件、泄漏机密，或推送错误的代码。单靠信任模型不能构成安全边界，程序必须在执行前检查每个请求。

原因很简单：模型读到的内容不一定是你写的。网页、issue 留言或 repo 里的文件，都可能暗藏操控 agent 的指令。
风险取决于三项能力：agent 能读取私密数据、会接触不可信内容，而且能把数据送到外部。
只有其中两项时，风险还能控制；三项同时出现，恶意内容就可能诱使 agent 读取机密并外传。这个组合称为 lethal trifecta。

持久化 memory 会进一步放大风险。恶意指令一旦被写进 memory 文件（第 9 章），下一次 session 就会把它读回来，让一次注入延续到原本对话结束之后。

permission gate 无法直接拿掉这三种能力，否则 agent 也无法完成工作。它改用两层保护：会凑齐这三项能力的调用，前面先摆一道决策；已经放行的调用，后面再用 sandbox 限制影响范围。

permission 层必须做到：

1. 在每个工具调用执行前先查看它。
2. 决定 `allow`、`ask` 或 `deny`。
3. 当高风险的调用尚未预先批准时，询问用户。
4. 当调用真的执行时，限制它造成的损害。

没有这一层，一次错误的工具调用就可能造成无法回复的后果。

---

## 核心机制

![机制图](assets/03-permission-and-sandbox.png)

一个纯函数负责做出 permission 决策。它读取工具、目前的 mode，以及所有的 allow 规则，并返回三个值之一：

- `allow`：执行工具。
- `ask`：暂停并询问用户。
- `deny`：不执行工具。

mode 会改变默认行为。举例来说，plan mode 允许只读工具，但在计划批准前拒绝编辑。

### 本章添加：permission gate

`decide()` 就是整个 permission 决策：

```python
def decide(tool, mode, allow_rules) -> str:      # src/permissions.py (new)
    if mode == BYPASS:                            # operator opted out
        return "allow"
    if mode == PLAN:                              # exploring, not acting yet
        if tool.is_read_only:           return "allow"
        if tool.name == "ExitPlanMode": return "ask"     # approval handshake (section 5)
        return "deny"                             # no side effects until approved
    if tool.is_read_only or tool.name in allow_rules:
        return "allow"
    if mode == ACCEPT_EDITS and tool.is_edit:
        return "allow"                            # a class of work pre-approved
    return "ask"                                  # default: when unsure, ask
```

这个函数没有 I/O。这让它可以一个 mode 一个 mode 地轻松测试。

### 如何集成到现有架构

gate 在 `_dispatch` 内部执行，就在 `run_tool` 之前：

```python
def _dispatch(block, registry, mode, allow_rules, approver):   # src/loop.py
    ...                                                  # resolve tool (section 2)
    decision = decide(tool, mode, allow_rules)           # 3 · the gate, the new line
    if decision == "deny":
        return res(f"{name} not allowed in {mode} mode")
    if decision == "ask" and not approver(name, block.input):
        return res(f"{name} denied by user")
    return res(run_tool(tool, block.input))              # only now does it run
```

- loop 主体和第 1、2 章相同，没有改变。
- 只有 `_dispatch` 多了 gate。
- `deny` 以及未批准的 `ask` 永远不会抵达 `run_tool`。
- 拒绝结果仍会以 `tool_result` 返回，所以模型看得到发生了什么，并能随之调整。
- `approver` 默认为 `False`，所以 `ask` 代表「否」，除非用户批准。

关键不变条件维持不变：每个工具调用都会产生一则结果消息，即使真正的动作没有执行。

真实系统会加上规则优先序、记住的批准，以及沙箱化的执行。这些都是同一个 gate 的延伸。

### 延伸阅读

以下设计 `src/` 都没有实现，出自 ai-agent-book，也未经下面表格的系统证实。

**先看懂指令，不要比对字符串：**agent 要求跑一条 shell 指令。`decide()` 只看得到一个工具名称，所以真正要判断的是那串指令。
一般的做法是拉一份字符串 deny 列表，而这招会失败。`rm -rf /` 很好抓，下面这几条会过：

- `find . -exec rm {} \;` 把删除塞在 flag 里面。
- `$(echo rm) -rf /` 是 shell 跑的时候才把 `rm` 这个字拼出来。
- `curl -o /etc/crontab` 写了一个文件，却没用到任何一个写入指令。

解法是换一个解析器，让它读结构而不是读字面。它先把指令拆成程序和参数，也知道哪些 flag 后面要吃掉一个值，所以分得出参数和 flag。
接着它问每个程序会做什么。`-exec` 自己带一条指令，那条指令也要一起检查。`-o` 指的是要写入的文件，那个路径就当成一次写入来检查。

代价是解析器得为每个程序准备一套规则。它不认识的程序就读不懂，所以沙箱还是要挡在它后面。

**结果对了，过程也可能要挡：**一张坏掉的数据表有两种修法，最后都会得到一张好的数据表：一种是做 migration，
另一种是把它砍掉整个重建。结果检查（第 21 章）两种都会放行，因为它只看终态。

解法是连路线一起管，不是只管终点。就算重建出来的数据表是对的，砍掉重建这个动作照样要挡。
代价是真的该重建的时候，也得找人来批准。

**沙箱挡掉哪些东西：**gate 也会判断错。沙箱的作用，就是让判断错的那次 `allow` 不要付出太大代价。有三个限制做掉大部分的工。

- **Egress：**网络默认挡掉，放行的流量走一个握有 host 允许列表的 proxy。
  三只脚里，这一只砍起来最便宜。agent 照样读代码、照样写文件，只是哪里都送不出去。
- **Mount：**源代码用只读挂载。凭证文件一个都不要挂。只给一个可写的工作目录，其他都不给。
  agent 打不开的文件，就外泄不出去。
- **Quota：**CPU、内存、磁盘、wall clock 时间都设上限。踩到上限的时候，回一个错误当 tool result，
  不要无声把 process 杀掉。模型读得到 timeout，就知道换一条短一点的指令。无声杀掉，它什么都读不到。

**要问用户，但不要让他等两次：**gate 返回 `ask`，用户现在就在等。如果检查本身也慢，那在提示框出现以前，他已经先等过一次了。
推测式检查把前面那一次等待拿掉。顺序是这样：

- harness 把 permission 检查丢到后台跑。
- 画面上马上先显示一行进度。那行进度不会改动系统上的任何东西。
- 如果那行还在显示的时候检查就回了 `allow`，工具直接执行，提示框不会出现。
- 如果检查还没定案，那行进度就换成确认提示。

这样为什么还是安全的：提早跑的只有检查，工具本身还是要等检查给答案。

---

## 不同系统怎么做

各个 agent 如何控制副作用、切换 mode，以及记住决策。

| | Claude Code | mini-swe-agent | deepseek-harness |
| --- | --- | --- | --- |
| **优点** | mode、有序规则与沙箱化提供精确的控制。 | 几分钟就能审计完。拒绝会落回对话，模型读得到原因。 | 拒绝只会收紧，沙箱判定 fail closed。 |
| **限制** | 要推敲的状态很多。bypass 和预先批准的路径都必须保持狭窄。 | 对每条指令一视同仁，而且什么都不记。 | 政策分散在 guard、approval、沙箱和 preset 之间。 |
| **设计原因** | 每次调用都问会造成批准疲劳，所以系统会把批准记下来。 | 损害交给环境去限制，一个确认提示加一份 regex 列表就够了。 | 每个关注点都是自己独立的 fail-closed 服务。 |
| **做法：gate point** | 每个工具执行前。Web、MCP 与远端执行各有批准路径。 | 每一步的指令执行前。按 Enter 就批准，留言就是拒绝。 | 先跑 pre-execute 事件，再跑只会拒绝的 guard。 |
| **做法：permission modes** | Default、edit-approved、plan、deny 与 bypass。 | `human`、`confirm` 与 `yolo`，执行期可以切换。 | 沙箱 mode 加上 ask 或 never，打包成 preset。 |
| **做法：sandbox** | Bash 可以在沙箱内执行。 | 环境 class 就是沙箱：主机本身、容器，或包住执行。 | provider 逐次把 argv 包起来，拒绝会分类好读回来。 |
| **做法：rule persistence** | 规则依优先序合并，可存到 session 或 settings。 | 白名单 regex 只写在 config，符合的指令跳过确认。 | 旋钮变动是 log 事件，重放折叠出政策。 |

---

## 常见问题

- **Pattern-match bypass：**字符串式的 deny 列表会漏掉 shell 的各种变体。先把指令解析出来，看它实际会做什么，再让沙箱挡在解析器后面。
- **Mode 开得太宽：**一条范围过大的 allow 规则或 bypass mode，可能让后续的高风险调用悄悄执行。限缩 bypass 的范围，并让目前的 mode 显示出来。
- **批准疲劳：**每次调用都询问，会训练用户不看内容就批准。预先批准低风险的类别，但让破坏性动作维持明确询问。
- **subagent 内的无声拒绝：**子 agent 可能没有终端机可以询问。应把提示往上转给父 agent 代问，而不是无声失败。
- **沙箱被停用：**若一个被允许的指令在沙箱外执行，permission 提示就是最后一道检查。任何未沙箱化的路径都要用政策挡在后面。
- **被批准的调用照样外泄：**每一次调用单独看都过得了 gate，整个 session 合起来却还是读到机密又把它送出去。
  网络默认就挡掉，第三种能力根本没得用。
- **验得过但很破坏：**砍掉重建也能通过结果检查，因为终态是对的。要检查的是动作，不是只有终态。
- **memory 被下毒：**注入到 memory 文件里的指令，之后每一次 session 都会被读回来。把存下来的 memory 当成不可信的内容，绝不当成操作者的规则。

---

## 动手跑跑看

[`src/`](src/) 承接 02 并加上：

- [`permissions.py`](src/permissions.py)：涵盖四种 mode 的 `decide`。
- [`loop.py`](src/loop.py)：在 `_dispatch` 中于执行前控制每个调用。

```bash
python sections/03-permission-sandbox/src/test.py         # offline checks, no key
uv run python sections/03-permission-sandbox/src/demo.py  # live demo, needs a key
```

---

## 参考资料

- [Claude Code 源代码](https://github.com/yasasbanukaofficial/claude-code)：`QueryEngine.ts`、`hooks/useCanUseTool.tsx`、`types/permissions.ts`、`utils/permissions/PermissionUpdate.ts`。
- [Claude Code 沙箱与 web gate](https://github.com/yasasbanukaofficial/claude-code)：`tools/BashTool/shouldUseSandbox.ts`、`tools/WebFetchTool/preapproved.ts`。
- [mini-swe-agent source](https://github.com/swe-agent/mini-swe-agent)：`agents/interactive.py`、`environments/docker.py`、`environments/extra/bubblewrap.py`。
- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness)（`dsh-v0.1.0-rc.7`）：
  `docs/subsystems/tools.md`、`docs/subsystems/approval.md`、`docs/subsystems/sandbox.md`、`docs/subsystems/permission-presets.md`、
  `packages/sandbox/sandbox-local/README.md`、`packages/shell/bash-sandbox/README.md`。
- [ai-agent-book · 第 5 章](https://github.com/bojieli/ai-agent-book/blob/main/book/chapter5.md)（《深入理解 AI Agent》，李博杰，以中文原版为准）：
  memory 把攻击放大的那个维度、沙箱的 egress 与 mount、quota 政策、语义式的指令解析、推测式 permission 检查，
  以及管路径而不是只管结果。这几项设计只有这一个来源。
- [The lethal trifecta for AI agents](https://simonwillison.net/2025/Jun/16/the-lethal-trifecta/)（Simon Willison）：
  私密数据、不可信内容、对外通讯，这三种能力不能凑在一起。
- [learn-claude-code · s03_permission](https://github.com/shareAI-lab/learn-claude-code)：section framing。
