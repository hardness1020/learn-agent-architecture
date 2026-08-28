# 4 · Hooks

[English](README.md) · [繁体中文](README.zh-TW.md) · **简体中文**

> hook 让你在 loop 的固定节点插入额外行为。

hook 是可以自行配置的 callback，能在工具调用前后、prompt 送出时，或 session 开始与结束时执行。

记录、验证、通知和简单的政策检查都很适合用 hook。少了这层扩展点，每加入一种行为都得修改 loop，甚至另外维护一份分支。

hook 的价值在于让 loop 保持精简。loop 只需要公开固定的生命周期事件，其他行为再挂到对应事件上。

---

## 核心机制

![机制图](assets/04-hooks.png)

`Hooks` 对象会把事件名称对应到一组 callback。loop 不必直接知道有哪些自订检查，只要由 `_dispatch` 在正确的时间触发命名事件。

在工具执行方面，有两个重要的点：

- `PreToolUse` 在 permission gate 之前执行。它可以挡下调用，或改写输入。
- `PostToolUse` 在工具调用成功之后执行。它可以观察结果。

### 本章添加：hook

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

- `on(event, fn)` 注册一个 callback。
- `fire_pre` 执行 `PreToolUse` 的 callback。
- pre-hook 可以返回 `{"deny": True}` 来挡下调用。
- pre-hook 可以返回 `{"updated_args": ...}` 来改写输入。
- `fire_post` 在执行之后跑观察者。

### 如何集成到现有架构

`_dispatch` 加入了两个调用：

```python
# src/loop.py _dispatch
blocked, args, msg = hooks.fire_pre(name, args)          # 4 · PreToolUse
if blocked: return res(msg)
decision = permissions.decide(tool, mode, allow_rules)   # 3 · gate (section 3)
...                                                      # deny / ask short-circuit
out = res(run_tool(tool, args))                          # 2 · execute -> tool_result
hooks.fire_post(name, args, out)                         # 4 · PostToolUse
```

- 被挡下或被拒绝的调用永远不会抵达 `run_tool`。
- `PostToolUse` 只在成功执行之后才会跑。
- hook 可以收紧 permission 的结果，但不应该放宽它。
- 在 Claude Code 中，`resolveHookPermissionDecision` 会把 hook 输出和以规则为基础的 permission 加以协调。

demo 用一个 `PreToolUse` hook，即使在 `bypassPermissions` 之下也挡下 `rm -rf`。

本章谈的是生命周期 hook。放在 `hooks/` 文件夹中的 React render hook，是不相干的 UI 代码，只是共享同一个字。

### 对照：waterfall hooks

Claude Code 的 hook 是一条外部指令：harness 开一个子进程去跑它，再读它的 exit code 和输出。
deepseek-harness 的 hook 则是一个普通函数，直接在 harness 进程里跑。
它挂在一个命名事件上，例如工具调用前会触发的那个事件。
它返回的也不是 exit code，而是类型化决策：deny、ask、allow 这种普通的值。

同一个事件可以挂好几个 hook。它们排成一条链，事件触发时只会跑第一个。
每个 hook 拿到事件数据和一个 `next()` callback，然后二选一：

- 不调用 `next()`，直接返回决策。链就停在这里，排在后面的 hook 都不会跑。
- 调用 `next()`，让链的其余部分先决定，这个 hook 再把那个结果返回，可以原样返回，也可以先改一下。

dsh 把这种分发方式叫做 waterfall。原本 Claude Code 的 shell hook 也还能用：一个 bridge 帮忙执行它们，
把输出转成同样的类型化决策。好几个 shell hook 同时回答时，bridge 取最严格的那个：deny 盖过 ask，ask 盖过 allow。

[`src/waterfall.py`](src/waterfall.py) 就是这套机制的 strip-down。它是对照用的 demo，没有接进 `_dispatch`，后面的章节照样沿用原本的 loop。

### 延伸阅读

以下设计 `src/` 都没有实现，出自 ai-agent-book，也未经下面表格的系统证实。

例子是写入后跑 lint。write 或 edit 工具一返回，hook 就对刚改过的那个文件跑 linter，
再把诊断消息加进 tool result。模型下一轮就会看到这个错误，位置就在写入成功的消息旁边。
少了这个 hook，同样的错误要等到下次 build 或跑测试才会冒出来。

这个做法成本低，有两个原因。

- 诊断消息是包在 tool result 里一起回去的，不用多跑一轮。
- 检查只跑一个文件，不是整个项目，花的时间跟那次写入差不多。

这个做法有一个限制。写入被挡下就不会执行，hook 也就不会有诊断消息可以加。

---

## 不同系统怎么做

各个 agent 如何在 loop 周围提供拦截点。

| | Claude Code | deepseek-harness |
| --- | --- | --- |
| **优点** | 用户不必改动 loop 就能扩展行为。适合做记录、验证、通知和政策检查。 | hook 是进程内的 plugin，既有的 shell hook 照样能跑。 |
| **限制** | 固定的事件列表同时也是它的界限。hook 只能在系统对外提供事件的地方进行拦截。 | 两套 hook 做法都要学。bridge 只涵盖一部分事件，也不能改写工具输入。 |
| **设计原因** | 让 loop 保持精简。新行为挂接到固定事件上，不用改动或分岔 loop。 | 扩展用的接口，就是 harness 自己在跑的那套事件系统。 |
| **做法：hook events** | 固定的 27 个生命周期事件，涵盖 tool、prompt、session、stop、subagent、compact 与 setup。 | 每个阶段都有 waterfall 和 serial 事件，shell hook 靠 bridge 接上来。 |
| **做法：fire point** | 从 settings 加载，启动时冻结。`PreToolUse` 在 permission gate 之前触发。 | 在 pre-execute waterfall 里，位在只会拒绝的 guard 之前。 |
| **做法：can block or modify?** | 可以。拒绝、询问、更新输入、加入 context，或停止。hook 输出会和以规则为基础的 permission 加以协调。 | 可以，靠类型化决策。多个 shell hook 取最严格的：deny > ask > allow。 |

---

## 常见问题

- **hook 绕过 permission：**hook 可能试图允许一个已被拒绝的动作。要把 hook 输出对照以规则为基础的 permission 来解析。
- **Stop hook 无限 loop：**一个 `Stop` hook 可能挡下、触发自我修正，然后又再次触发。要追踪 stop hook 是否已经在运作中。
- **hook 配置在 session 中途改变：**某个程序可能在启动后修改 settings。要对 hook 配置做一次快照。
- **慢速 hook 卡住 loop：**hook 可能 shell out 去做很慢的工作。要加上 timeout。
- **PostToolUse 意外停止：**若 post-hook 返回 `preventContinuation`，要把它呈现为一个优雅的停止，而不是崩溃。
- **诊断消息淹没结果：**整个项目跑一次 lint，回来的文字可能比写入本身还多。只检查刚改过的那个文件，加回去的量也要设上限。

---

## 动手跑跑看

[`src/`](src/) 承接 03 并加上：

- [`hooks.py`](src/hooks.py)：带有 `fire_pre` 与 `fire_post` 的 `Hooks` 对象。
- [`loop.py`](src/loop.py)：`_dispatch` 在 gate 之前触发 `PreToolUse`，在执行之后触发 `PostToolUse`。
- [`waterfall.py`](src/waterfall.py)：deepseek-harness 的对照：一条 hook 链，用 `next()` 往下传，多个结果取最严格的（deny > ask > allow）。
- [`test.py`](src/test.py)：一个 pre-hook 即使在 `bypassPermissions` 之下也挡下 `rm -rf`；waterfall 检查涵盖直接决定、交给下游，和取最严格。

```bash
python sections/04-hooks/src/test.py         # offline checks, no key
uv run python sections/04-hooks/src/demo.py  # live demo, needs a key
```

---

## 参考资料

- [Claude Code 源代码](https://github.com/yasasbanukaofficial/claude-code)：
  `types/hooks.ts`、`entrypoints/sdk/coreTypes.ts`、`services/tools/toolHooks.ts`、`query/stopHooks.ts`、`services/tools/toolExecution.ts`、`setup.ts`。
- [deepseek-harness 源代码](https://github.com/deepseek-ai/deepseek-harness)（`dsh-v0.1.0-rc.7`）：
  `packages/hooks/README.md`、`packages/hooks/hooks-claude-code/README.md`、`packages/hooks/hook-protocol/README.md`、
  `docs/cordis-primer.md`、`docs/subsystems/core.md`。
- [learn-claude-code · s04_hooks](https://github.com/shareAI-lab/learn-claude-code)：section framing。
- [ai-agent-book · 第 5 章](https://github.com/bojieli/ai-agent-book/blob/main/book/chapter5.md)（《深入理解 AI Agent》，李博杰，以中文原版为准）：
  写入后跑 lint：工具层在写入之后跑 linter，把诊断消息加进 tool result。
