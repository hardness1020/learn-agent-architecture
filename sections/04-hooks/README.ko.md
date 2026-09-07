# 4 · Hooks

[English](README.md) · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · **한국어**

> Hooks 루프 주변의 고정 지점에서 동작을 추가합니다.

Hooks 는 사용자 설정 콜백입니다. 도구 호출 전에, 도구 호출 후, 프롬프트 제출 시, 또는 세션 시작 및 종료 시 실행될 수 있습니다.

hooks 를 로깅, 검증, 알림, 소규모 정책 체크에 사용하세요. hooks 없이는 각 새로운 동작을 위해 루프를 수정하거나 포크해야 합니다.

Hooks 는 루프를 작게 유지합니다. 루프는 고정 이벤트를 노출합니다. 확장은 이러한 이벤트에 연결됩니다.

---

## 메커니즘

![메커니즘 다이어그램](assets/04-hooks.png)

`Hooks` 객체는 이벤트 이름을 콜백 목록에 매핑합니다. 루프는 사용자 정의 검사를 직접 호출하지 않습니다. 대신 `_dispatch` 는 명명된 이벤트를 발동합니다.

도구 실행에는 두 가지 중요한 지점이 있습니다:

- `PreToolUse`는 권한 게이트 전에 실행됩니다. 호출을 차단하거나 입력을 재작성할 수 있습니다.
- `PostToolUse`는 도구 호출이 성공한 후 실행됩니다. 결과를 관찰할 수 있습니다.

### 새로 추가: hooks

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

- `on(event, fn)`는 콜백을 등록합니다.
- `fire_pre`는 `PreToolUse` 콜백을 실행합니다.
- 사전 훅(pre-hook)은 차단을 위해 `{"deny": True}`를 반환할 수 있습니다.
- 사전 훅(pre-hook)은 입력을 재작성하기 위해 `{"updated_args": ...}`를 반환할 수 있습니다.
- `fire_post`는 실행 후 옵저버들을 실행합니다.

### 통합 방식

`_dispatch`에는 두 개의 호출이 추가됩니다:

```python
# src/loop.py _dispatch
blocked, args, msg = hooks.fire_pre(name, args)          # 4 · PreToolUse
if blocked: return res(msg)
decision = permissions.decide(tool, mode, allow_rules)   # 3 · gate (section 3)
...                                                      # deny / ask short-circuit
out = res(run_tool(tool, args))                          # 2 · execute -> tool_result
hooks.fire_post(name, args, out)                         # 4 · PostToolUse
```

- 차단되거나 거부된 호출은 절대 `run_tool`에 도달하지 않습니다.
- `PostToolUse`는 성공적인 실행 후에만 실행됩니다.
- Hooks는 권한 결과를 강화할 수 있지만, 완화해서는 안 됩니다.
- Claude Code에서, `resolveHookPermissionDecision`는 hook 출력과 규칙 기반 권한을 조정합니다.

데모에서는 `PreToolUse` hook을 사용하여 `rm -rf`를 `bypassPermissions` 아래에서도 차단합니다.

이 섹션에서는 라이프사이클 hooks를 다룹니다. `hooks/` 폴더에 있는 React 렌더 hooks는 동일한 단어를 공유하는 관련 없는 UI 코드입니다.

### 대비: 워터폴 hooks

Claude Code에서, hook은 외부 명령입니다. harness는 이를 서브프로세스로 실행하고 종료 코드와 출력을 읽습니다.
deepseek-harness에서 hook은 harness 프로세스 내에서 실행되는 일반 함수입니다.
도구 호출 전에 실행되는 것과 같은 명명된 이벤트에 등록합니다.
그리고 종료 코드 대신 deny, ask 또는 allow와 같은 일반 값인 타입화된 결정을 반환합니다.

여러 hooks가 하나의 이벤트에 등록할 수 있습니다. 이들은 체인을 형성하며, 이벤트는 첫 번째 것만 실행됩니다.
각 hook은 이벤트 데이터와 함께 `next()` 콜백을 받으며, 그 후 두 가지 동작 중 하나를 선택합니다:

- `next()`를 호출하지 않고 결정을 반환합니다. 체인은 여기서 멈추며, 아래에 있는 Hooks는 실행되지 않습니다.
- `next()`를 호출합니다. 체인의 나머지가 결정을 내리고, 이 hook은 그 결과를 그대로 또는 조정하여 반환합니다.

dsh는 이 디스패치 방식을 워터폴이라고 부릅니다. 기존 Claude Code 셸 hooks도 여전히 작동합니다: 브리지가 이를 실행합니다
그리고 그들의 출력을 같은 유형의 결정으로 바꿉니다. 여러 쉘 hooks가 동시에 응답할 때,
그 다리는 가장 엄격한 답을 유지한다: 거부가 요청보다 우세하고, 요청이 허용보다 우세하다.

[`src/waterfall.py`](src/waterfall.py)는 이것의 축소판입니다. 이것은 대조 데모이며, `_dispatch`에 연결되어 있지 않아서, 이후 섹션들은 동일한 루프를 이어갑니다.

### 추가 읽기

이것들 중 어느 것도 `src/`에 없습니다. 이것은 ai-agent-book에서 가져온 것이며, 표에 있는 시스템들이 확인된 것은 아닙니다.

예제는 쓰기 시 린트입니다. 쓰기 또는 편집 도구가 반환됩니다. 그런 다음 hook이 변경된 파일에 대해 린터를 실행합니다.
이것은 진단 정보를 tool result에 추가합니다. 모델은 다음 차례에 오류를 읽으며, 쓰기 확인 옆에서 읽습니다.
hook이 없으면, 그 오류는 다음 빌드 또는 테스트 실행을 기다립니다.

이 패턴이 저렴하게 유지되는 이유는 두 가지입니다.

- 진단 정보가 tool result 안으로 다시 들어가므로, 추가 차례가 필요 없습니다.
- 검사 대상이 전체 프로젝트가 아닌 하나의 파일이므로, 쓰기 작업만큼의 시간이 걸립니다.

이 패턴에는 한계가 있습니다. 차단된 쓰기는 절대 실행되지 않으므로, hook은 진단 정보를 생성하지 않습니다.

---

## 시스템별

각 에이전트가 루프 주변에서 인터셉션 포인트를 어떻게 노출하는지.

| | Claude Code | deepseek-harness |
| --- | --- | --- |
| **장점** | 사용자가 루프를 편집하지 않고 동작을 확장할 수 있음: 로깅, 검증, 알림, 정책 검사. | Hooks는 처리 중 plugins에 있음; 기존 셸 hooks는 여전히 실행됨. |
| **단점** | 고정된 이벤트 목록이 한계임. hook은 이벤트가 있는 곳에서만 가로챌 수 있음. | 배우야 할 hook 방식이 두 가지; 브리지는 일부만 덮어쓰며 입력을 다시 작성할 수 없음. |
| **이유** | 루프를 작게 유지함. 새로운 동작은 포크가 아닌 고정 이벤트에 연결됨. | 확장 표면은 harness 자체가 실행되는 이벤트 시스템임. |
| **방법: hook 이벤트** | 도구, 프롬프트, 세션, 정지, subagent, 압축, 설정에 걸쳐 27개의 라이프사이클 이벤트. | 단계별 워터폴 및 직렬 이벤트; 브리지는 셸 hooks에 연결됨. |
| **방법: 발화점(fire point)** | 설정에서 불러오고 시작 시 고정됨. `PreToolUse`는 권한 게이트 이전에 발화됨. | 실행 전 워터폴(pre-execute waterfall)에서, 거부 전용(deny-only) 가드 이전에. |
| **방법: 차단하거나 수정할 수 있는가?** | 예. 거부, 요청, 입력 업데이트, 컨텍스트 추가, 또는 중지; 규칙과 조정됨. | 예, 타입된 결정(typed decisions)을 통해; 셸(shell) hooks는 거부 > 요청 > 허용 순으로 접기(fold). |

---

## 실패 모드

- **Hook은 권한을 우회함.** hook은 거부된 행동을 허용하려 할 수 있음. hook 출력을 규칙 기반 권한과 맞춤.
- **hook 루프를 영원히 중지.** `Stop` hook은 차단, 자기 수정(trigger self-correction) 트리거, 다시 발화 가능. 이미 활성화된 중지(stop) hook을 추적.
- **Hook 세션 중 구성 변경.** 프로세스가 시작 후 설정을 편집할 수 있습니다. hook 구성을 한 번 스냅샷하세요.
- **느린 hook이 루프를 멈춤.** hook이 느린 작업을 위해 셸 아웃할 수 있습니다. 타임아웃을 추가하세요.
- **PostToolUse가 예상치 않게 중지됨.** 포스트 훅이 `preventContinuation`를 반환하면, 충돌이 아니라 정상 종료로 표시하세요.
- **진단이 결과를 폭주시킴.** 전체 프로젝트에 대한 린트 실행은 실제 작성 내용보다 더 많은 텍스트를 반환할 수 있습니다. 변경된 파일만 확인하고, 추가되는 내용을 제한하세요.

---

## 실행 가능

[`src/`](src/)는 03을 이어받아 다음을 추가합니다:

- [`hooks.py`](src/hooks.py): `Hooks` 객체로 `fire_pre` 및 `fire_post`와 함께.
- [`loop.py`](src/loop.py): `_dispatch`는 게이트 전에 `PreToolUse`를 발사하고, 실행 후에는 `PostToolUse`를 발사합니다.
- [`waterfall.py`](src/waterfall.py): deepseek-harness 대비: hook 체인과 `next()` 위임, 그리고 가장 엄격한-우선 병합(거부 > 요청 > 허용).
- [`test.py`](src/test.py): 사전 훅은 `bypassPermissions` 아래에서도 `rm -rf`를 차단합니다; 워터폴 검사는 결정, 위임, 병합을 모두 다룹니다.

```bash
python sections/04-hooks/src/test.py         # offline checks, no key
uv run python sections/04-hooks/src/demo.py  # live demo, needs a key
```

---

## 출처

- [Claude Code 출처](https://github.com/yasasbanukaofficial/claude-code):
  `types/hooks.ts`, `entrypoints/sdk/coreTypes.ts`, `services/tools/toolHooks.ts`, `query/stopHooks.ts`, `services/tools/toolExecution.ts`, `setup.ts`.
- [deepseek-harness 출처](https://github.com/deepseek-ai/deepseek-harness) at `dsh-v0.1.0-rc.7`:
  `packages/hooks/README.md`, `packages/hooks/hooks-claude-code/README.md`, `packages/hooks/hook-protocol/README.md`,
  `docs/cordis-primer.md`, `docs/subsystems/core.md`.
- [learn-claude-code · s04_hooks](https://github.com/shareAI-lab/learn-claude-code): 섹션 프레이밍.
- [ai-agent-book · chapter 5](https://github.com/bojieli/ai-agent-book/blob/main/book/chapter5.md) (《深入理解 AI Agent》, 李博杰; 중국 원문이 기준):
  쓰기 시 린트: 도구 계층이 쓰기 후 린터를 실행하고 진단 정보를 tool result에 추가합니다.
