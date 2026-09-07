# 4 · Hooks

[English](README.md) · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · **한국어**

> hook은 loop 주위의 정해진 지점에 동작을 더합니다.

hook은 사용자가 설정하는 callback입니다. tool call 전, tool call 후, prompt가 제출될 때, session이 시작하거나 멈출 때 실행될 수 있습니다.

hook은 로그 기록, 검증, 알림, 작은 정책 검사에 씁니다. hook이 없으면 새 동작마다 loop를 고치거나 fork해야 합니다.

hook은 loop를 작게 유지합니다. loop는 정해진 이벤트를 드러냅니다. 확장은 그 이벤트에 붙습니다.

---

## 메커니즘

![Mechanism diagram](assets/04-hooks.png)

`Hooks` 객체는 이벤트 이름을 callback 목록에 대응시킵니다. loop는 사용자가 만든 검사를 직접 호출하지 않습니다. 대신 `_dispatch`가 이름 붙은 이벤트를 발화합니다.

tool 실행에는 중요한 지점이 둘 있습니다.

- `PreToolUse`는 permission gate 앞에서 실행됩니다. 호출을 막거나 입력을 고쳐 쓸 수 있습니다.
- `PostToolUse`는 tool call이 성공한 뒤에 실행됩니다. 결과를 관찰할 수 있습니다.

### 새로 더하는 것: hook

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

- `on(event, fn)`은 callback을 등록합니다.
- `fire_pre`는 `PreToolUse` callback들을 실행합니다.
- pre-hook은 `{"deny": True}`를 반환해 호출을 막을 수 있습니다.
- pre-hook은 `{"updated_args": ...}`를 반환해 입력을 고쳐 쓸 수 있습니다.
- `fire_post`는 실행 뒤에 관찰자들을 돌립니다.

### 통합 방식

`_dispatch`에 호출 두 개가 더해집니다.

```python
# src/loop.py _dispatch
blocked, args, msg = hooks.fire_pre(name, args)          # 4 · PreToolUse
if blocked: return res(msg)
decision = permissions.decide(tool, mode, allow_rules)   # 3 · gate (section 3)
...                                                      # deny / ask short-circuit
out = res(run_tool(tool, args))                          # 2 · execute -> tool_result
hooks.fire_post(name, args, out)                         # 4 · PostToolUse
```

- 막히거나 거부된 호출은 절대 `run_tool`까지 가지 않습니다.
- `PostToolUse`는 실행이 성공한 뒤에만 돕니다.
- hook은 permission 결과를 더 조일 수는 있어도 느슨하게 풀어서는 안 됩니다.
- Claude Code에서는 `resolveHookPermissionDecision`이 hook의 출력과 규칙 기반 permission을 맞춰 정리합니다.

데모는 `PreToolUse` hook으로 `bypassPermissions` 아래에서도 `rm -rf`를 막습니다.

이 섹션은 수명 주기 hook을 다룹니다. `hooks/` 폴더에 있는 React 렌더링 hook은 단어만 같을 뿐 관계없는 UI 코드입니다.

### 대비: waterfall hook

Claude Code에서 hook은 외부 명령입니다. harness는 그것을 하위 프로세스로 실행하고 종료 코드와 출력을 읽습니다.
deepseek-harness에서 hook은 harness 프로세스 안에서 도는 평범한 함수입니다.
hook은 이름 붙은 이벤트에 등록되는데, tool call 앞에서 발화하는 이벤트가 그런 예입니다.
그리고 종료 코드 대신 타입이 붙은 결정을 반환합니다. deny, ask, allow 같은 평범한 값입니다.

하나의 이벤트에 hook 여럿이 등록될 수 있습니다. 이들은 사슬을 이루고, 이벤트는 첫 번째 hook만 발화시킵니다.
각 hook은 이벤트 데이터와 함께 `next()` callback을 받고, 두 가지 중 하나를 고릅니다.

- `next()`를 부르지 않고 결정을 반환합니다. 사슬은 여기서 멈추고, 아래쪽 hook은 아예 실행되지 않습니다.
- `next()`를 부릅니다. 사슬의 나머지가 결정하고, 이 hook은 그 결과를 그대로 또는 손질해서 반환합니다.

dsh는 이런 dispatch 방식을 waterfall이라고 부릅니다. 기존 Claude Code의 shell hook도 그대로 동작하는데, 브리지가 그것들을 실행하고
출력을 같은 타입의 결정으로 바꿉니다. shell hook 여럿이 한꺼번에 답하면, 브리지는 가장 엄격한 답을 남깁니다.
deny가 ask를 이기고, ask가 allow를 이깁니다.

[`src/waterfall.py`](src/waterfall.py)는 이것을 최소로 줄인 것입니다. 대비용 데모라 `_dispatch`에 연결되어 있지 않고, 그래서 뒤 섹션들은 같은 loop를 그대로 이어받습니다.

### 더 읽을거리

이 내용은 `src/`에 없습니다. ai-agent-book에서 온 것이며, 표에 있는 시스템들에서 확인된 내용은 아닙니다.

예는 쓰기 시점의 lint입니다. 쓰기나 편집 tool이 결과를 반환합니다. 그러면 hook이 바뀐 파일에 linter를 돌립니다.
그리고 진단 결과를 tool 결과에 덧붙입니다. 모델은 다음 turn에서 쓰기 확인 메시지 옆에 붙은 그 오류를 읽습니다.
hook이 없으면 그 오류는 다음 빌드나 테스트 실행까지 기다립니다.

이 패턴이 싸게 유지되는 이유는 둘입니다.

- 진단 결과가 tool 결과 안으로 돌아가므로, turn이 더 필요하지 않습니다.
- 검사가 프로젝트 전체가 아니라 파일 하나만 보므로, 쓰기와 비슷한 시간이면 끝납니다.

이 패턴에는 한계가 하나 있습니다. 막힌 쓰기는 실행되지 않으므로, hook은 진단 결과를 내놓지 않습니다.

---

## 시스템별

각 agent가 loop 주위의 가로채기 지점을 어떻게 드러내는지 봅니다.

| | Claude Code | deepseek-harness |
| --- | --- | --- |
| **장점** | 사용자가 loop를 고치지 않고 동작을 확장함. 로그 기록, 검증, 알림, 정책 검사. | hook이 프로세스 내 plugin. 기존 shell hook도 그대로 돎. |
| **단점** | 정해진 이벤트 목록이 곧 한계. hook은 이벤트가 있는 자리에서만 가로챔. | 익힐 hook 방식이 둘. 브리지는 일부만 다루고 입력을 고쳐 쓰지 못함. |
| **이유** | loop를 작게 유지함. 새 동작은 fork가 아니라 정해진 이벤트에 붙음. | 확장 인터페이스가 harness 자신이 돌아가는 이벤트 시스템 그 자체. |
| **방법: hook 이벤트** | tool, prompt, session, stop, subagent, compact, setup에 걸친 수명 주기 이벤트 27개. | 단계마다 waterfall 이벤트와 직렬 이벤트. 브리지가 shell hook을 붙임. |
| **방법: 발화 지점** | 설정에서 로드해 시작 시점에 고정. `PreToolUse`는 permission gate 앞에서 발화. | 실행 직전 waterfall 안, 거부만 하는 guard 앞. |
| **방법: 막거나 고칠 수 있는가?** | 예. 거부, 질문, 입력 갱신, context 추가, 정지. 규칙과 맞춰 정리됨. | 예. 타입이 붙은 결정으로. shell hook은 deny > ask > allow로 접힘. |

---

## 실패 모드

- **hook의 permission 우회.** hook이 거부된 행동을 허용하려 들 수 있습니다. hook의 출력을 규칙 기반 permission과 맞춰 정리합니다.
- **Stop hook의 무한 반복.** `Stop` hook은 정지를 막고, 자기 교정을 부르고, 다시 발화할 수 있습니다. stop hook이 이미 동작 중인지 추적합니다.
- **session 도중의 hook 설정 변경.** 어떤 프로세스가 시작 뒤에 설정을 고칠 수 있습니다. hook 설정은 한 번만 찍어 둡니다.
- **느린 hook이 loop를 멈춰 세움.** hook이 느린 작업을 shell로 넘길 수 있습니다. timeout을 둡니다.
- **PostToolUse의 예기치 않은 정지.** post-hook이 `preventContinuation`을 반환하면, 크래시가 아니라 정상적인 정지로 드러냅니다.
- **진단 결과가 결과를 뒤덮음.** 프로젝트 전체에 lint를 돌리면 쓰기 자체보다 많은 텍스트가 돌아올 수 있습니다. 바뀐 파일만 검사하고, 덧붙이는 양에 상한을 둡니다.

---

## 실행 방법

[`src/`](src/)는 03을 이어받아 다음을 더합니다.

- [`hooks.py`](src/hooks.py): `fire_pre`와 `fire_post`를 가진 `Hooks` 객체.
- [`loop.py`](src/loop.py): `_dispatch`가 gate 앞에서 `PreToolUse`를, 실행 뒤에 `PostToolUse`를 발화합니다.
- [`waterfall.py`](src/waterfall.py): deepseek-harness와의 대비. `next()` 위임이 있는 hook 사슬과, 가장 엄격한 쪽이 이기는 병합(deny > ask > allow).
- [`test.py`](src/test.py): pre-hook이 `bypassPermissions` 아래에서도 `rm -rf`를 막습니다. waterfall 검사는 결정, 위임, 병합을 다룹니다.

```bash
python sections/04-hooks/src/test.py         # offline checks, no key
uv run python sections/04-hooks/src/demo.py  # live demo, needs a key
```

---

## 출처

- [Claude Code source](https://github.com/yasasbanukaofficial/claude-code):
  `types/hooks.ts`, `entrypoints/sdk/coreTypes.ts`, `services/tools/toolHooks.ts`, `query/stopHooks.ts`, `services/tools/toolExecution.ts`, `setup.ts`.
- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness) `dsh-v0.1.0-rc.7`:
  `packages/hooks/README.md`, `packages/hooks/hooks-claude-code/README.md`, `packages/hooks/hook-protocol/README.md`,
  `docs/cordis-primer.md`, `docs/subsystems/core.md`.
- [learn-claude-code · s04_hooks](https://github.com/shareAI-lab/learn-claude-code): 섹션 구성.
- [ai-agent-book · chapter 5](https://github.com/bojieli/ai-agent-book/blob/main/book/chapter5.md) (《深入理解 AI Agent》, 李博杰. 중국어 원문이 기준):
  쓰기 시점의 lint. tool 계층이 쓰기 뒤에 linter를 돌리고 진단 결과를 tool 결과에 덧붙입니다.
