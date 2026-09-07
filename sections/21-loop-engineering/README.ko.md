# 21 · Loop engineering

[English](README.md) · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · **한국어**

> 다음 prompt를 쓰는 일을 멈춥니다. 사람 없이 agent를 실행하는 loop를 설계합니다.

앞의 모든 섹션은 모델 호출 하나를 둘러싼 메커니즘을 하나씩 추가합니다. 이 섹션은 그것들을 조합합니다.

loop engineering은 엔지니어링 노력이 어디로 들어가는지가 바뀌는 것을 가리키는 이름입니다.
agent에게 turn마다 prompt를 주는 대신, 할 일을 찾아내고, agent를 실행하고, 출력을 검사하고, 다음에 무엇을 할지 정하는 바깥 시스템을 만듭니다.
사람은 운영자에서 설계자로 옮겨 갑니다.

바깥 loop는 다음을 해야 합니다:

1. 사용자만이 아니라 트리거에서도 실행을 시작합니다(섹션 14).
2. 출력이 완료로 인정되기 전에 검사합니다.
3. 희망이 아니라 예산으로 멈춥니다.
4. 상태를 남겨서 다음 실행이 처음부터 다시 하지 않고 이어서 하게 합니다(섹션 9, 12).
5. 아무도 지켜보지 않았을 때에도 무슨 일이 있었는지 보고합니다(섹션 20).

이 층이 없으면 사람이 바깥 loop입니다. 사람이 손으로 prompt를 주고, 읽고, 판단하고, 다시 시도하며, 사람이 손을 놓는 순간 agent도 일을 멈춥니다.

---

## 메커니즘

![Mechanism diagram](assets/21-loop-engineering.png)

단순한 버전: agent loop를 세 개의 loop가 더 감싼 모양입니다. 각각은 안쪽의 것을 감싸고, 각각 다른 질문에 답합니다.

1. **agent loop**(섹션 1). task가 끝난 것처럼 보일 때까지 tool을 호출합니다. 답하는 질문: 한 단계는 어떻게 끝나는가.
2. **검증 loop.** 출력을 rubric에 비추어 채점합니다. 실패는 예산이 남는 동안 재시도로 되돌아갑니다. 답하는 질문: 정말로 끝났는가.
3. **event loop.** cron 일정, 웹훅, 채널이 실행을 시작시킵니다(섹션 14, 섹션 19). 답하는 질문: 일은 언제 시작되는가.
4. **개선 loop.** trace와 eval(섹션 20)이 harness 설정, skill, 모델의 변경으로 이어집니다. 답하는 질문: 시스템이 나아지고 있는가.
   가장 성숙한 형태에서 이 loop는 harness 자체를 고칩니다: trace에서 약점을 캐내고, 범위를 제한한 수정을 제안하고, 회귀 세트로 검증합니다.
   loop 구조는 손으로 설계한 템플릿이 아니라 탐색 공간이 됩니다.

데이터는 바깥으로 흐릅니다. 트리거가 발화해 prompt를 큐에 넣습니다. agent loop가 후보를 만듭니다. 채점기가 점수를 매깁니다.
실패하면 피드백을 덧붙이고 예산이 남아 있는 동안 다시 시도합니다. 통과하면 그 task의 채널로 전달합니다.
실행의 trace는 telemetry에 쌓이고, 개선 loop가 거기서 그것을 읽습니다.

### 새로 추가: 검증 loop

앞선 섹션들이 만들지 않은 유일한 loop입니다. 안쪽 loop는 모델이 끝났다고 말하면 멈춥니다. 검증 loop는 "끝났다"를 검사받은 주장으로 만듭니다:

```python
def verified_run(task, worker, checker, budget=2):    # src/verify.py
    feedback = ""
    attempts = []
    for n in range(1, budget + 1):                    # the ceiling: harness-enforced
        out = worker(task + feedback)                 # the inner loop (section 1)
        verdict = checker(task, out)                  # a separate checker (section 6)
        attempts.append({"attempt": n, "passed": verdict["passed"], "reason": verdict["reason"]})
        if verdict["passed"]:
            return {"ok": True, "output": out, "attempts": attempts}
        feedback = f"\n\nA prior attempt was rejected... Why it failed: {verdict['reason']}"
    return {"ok": False, "output": None, "attempts": attempts}   # budget spent: escalate
```

- 채점기는 새 context를 가진 별도의 agent입니다(섹션 6). 자기 출력을 자기가 채점하는 워커는 대체로 그것을 통과시킵니다.
  `agent_checker`가 그런 agent를 만듭니다: 채점할 때마다 새 `messages[]` 위에서 안쪽 loop를 돌리고, 판정의 첫 단어로 PASS 또는 FAIL을 받습니다.
- rubric은 loop 바깥에 고정되어 있습니다. 모델은 그것을 만족시킬 수는 있어도 다시 쓸 수는 없습니다.
- 피드백은 데이터입니다. 실패한 판정이 prompt의 일부로 재시도에 실려 들어가므로, 두 번째 시도는 첫 번째 시도가 무엇을 틀렸는지 압니다.
- `ok: False`는 에스컬레이션 신호입니다. 시도 기록이 사람에게 넘어가고, loop는 영원히 재시도하지 않습니다.

통과냐 실패냐 하나만으로는 신호가 빈약합니다. 판정을 세 가지 질문으로 나누고, 각 질문이 근거를 대게 합니다:

- **결과.** 실행이 남긴 상태가 맞는가. 근거: 상태 그 자체이며, 코드로 검사할 수 있는 곳이면 코드로 검사합니다(섹션 23).
- **과정.** 실행이 규칙을 지켰는가: 허용된 tool, 요구되는 순서, 건너뛴 확인 없음. 근거: trace에 남은 tool 호출.
- **품질.** 어떤 코드 검사로도 표현할 수 없는 부분에서 답이 좋은가. 근거: rubric이며, 어긋난 항목을 지목합니다.

실행 가능한 코드는 세 번째 질문만 채점합니다. 결과 검사와 과정 검사에는 무슨 일이 있었는지 기록하는 환경이 필요한데, 그것이 바로 섹션 23이 만드는 것입니다.
셋을 나누면 무엇을 고쳐야 하는지 알 수 있습니다. 결과가 통과하고 과정이 실패했다면 그 실행은 운이 좋았던 것입니다. 과정이 통과하고 결과가 실패했다면 규칙이 틀린 것입니다.

### 예산과 정지 조건

모든 loop에는 모델이 말로 넘어갈 수 없는 상한이 필요합니다: 반복 횟수, token 예산, 실제 시간 제한, 또는 헛돎 카운터(새로 찾은 것이 없는 라운드가 K번이면 정지).

상한을 강제하는 것은 harness입니다. 모델에게 제발 멈춰 달라고 부탁하는 것은 힌트이지 정지 조건이 아닙니다.
`verified_run`에서 상한은 `range()`의 경계입니다: `budget + 1`번째 시도는 일어날 수 없습니다.

### 성숙도 단계

loop engineering 출처들은 loop를 어디까지 믿고 맡기는지에 따라 등급을 매깁니다:

- **L1 · 보고.** loop는 읽고 보고합니다. 사람이 행동합니다.
- **L2 · 보조.** loop는 변경 초안을 만듭니다. 사람이 승인합니다.
- **L3 · 무인.** loop가 행동합니다. 사람은 사후에 감사합니다.

이 단계는 permission에 대한 결정입니다(섹션 3). loop는 현재 단계의 출력이 지루할 만큼 계속 맞은 뒤에야 한 단계 올립니다.

### 통합 방식

이 섹션은 새로운 기본 요소를 추가하지 않습니다. 앞선 것들을 조합한 것입니다:

- 트리거는 섹션 14의 일정과 섹션 19의 채널입니다.
- 워커는 섹션 1의 loop이고, 섹션 6의 subagent로 만드는 쪽과 검사하는 쪽을 나눕니다.
- 병렬 loop는 섹션 15의 worktree에서 격리됩니다.
- 실행 사이의 상태는 섹션 9의 memory와 섹션 12의 task 기록에 있습니다.
- 보고와 trace는 섹션 20입니다. 개선 loop는 섹션 20의 측정을 harness 변경으로 되돌려 닫습니다.

실행 가능한 코드도 같은 식으로 연결합니다. `run_turn`은 섹션 20과 바이트 단위로 동일하고, 검증이 그것을 바깥에서 감쌉니다:

```python
def worker(prompt):                                # src/demo.py · the inner loop, unchanged
    return run_turn([{"role": "user", "content": prompt}], model, reg, Session(mode=DEFAULT))

checker = agent_checker(RUBRIC, model)             # a fresh grader agent, no tools
result = verified_run("What is 27 + 15? Use the add tool.", worker, checker, budget=2)
```

새로운 것은 규율입니다: 끝내기 전에 채점하고, 시작하기 전에 예산을 정하고, 언제나 보고합니다.

### 더 읽을거리

이 내용은 `src/`에 없습니다. ai-agent-book과 공개된 자기 개선 연구에서 온 것이며, 표에 있는 시스템들에서 확인된 내용은 아닙니다.

**배운 것을 어디로 보낼지.** 어떤 실행이 스테이징 데이터베이스에 다른 연결 문자열이 필요하다는 것을 알아냈다고 합시다. 그것은 어디로 가야 합니까?
개선 loop에서 어려운 부분은 교훈을 찾는 것이 아닙니다. 그 교훈이 어디에 놓일지 고르는 것입니다. 놓을 자리는 네 군데입니다:

- **지식 문서.** 어떤 실행이 발견한 사실 하나. 쓰기도 싸고 지우기도 쌉니다. agent는 task에 필요해지면 그것을 다시 읽습니다(섹션 9).
- **prompt 또는 skill.** 반복되어야 하는 행동. 그것을 불러오는 모든 turn에서 context를 소모합니다(섹션 7).
- **프로그램.** 매번 똑같이 실행되는 절차. 추론 시점에는 비용이 들지 않고, 테스트할 수 있습니다(섹션 2).
- **가중치.** 최후의 수단. 느리고, 비싸고, 되돌리기가 가장 어렵습니다. 이 저장소의 harness 주제 밖입니다.

규칙은 그 변경을 담을 수 있는 가장 작은 자리를 고르는 것입니다. 가장 작다는 말은 검사하기 가장 쉽고 되돌리기 가장 쉽다는 뜻이기도 합니다.
연결 문자열은 사실이므로 문서로 갑니다. system prompt로 가지 않습니다.

두 번째 자리인 prompt나 skill이 가장 많이 남용되므로, 여기에는 그 자리만의 게이트가 필요합니다.
수정은 한 번 잘못된 실행이 아니라 여러 번 반복된 실패를 근거로 씁니다.
언제 적용되는지 밝혀서, 관련 없는 실행에서는 조용히 있게 합니다.
그다음 두 번 검사합니다: 그 수정과 가까운 사례에서 한 번, 그리고 그 수정을 만들 때 쓰지 않은 홀드아웃 세트에서 한 번.
먼저 트래픽 일부에만 내보내고, 롤백은 준비해 둡니다.
Karpathy는 이것을 system prompt learning이라고 부릅니다: 가중치 대신 단어를 고치는 것입니다.
ACE는 prompt 전체를 다시 쓰는 대신 번호가 붙은 context 항목을 고쳐서 수정 하나하나를 작게 유지합니다.

**tool 사용자에서 tool 제작자로.** 세 번째 자리인 프로그램은 앞선 섹션들이 만들지 않은 것입니다.
skill은 모델에게 지시를 건네지만, 모델은 그것을 여전히 읽고 따라야 합니다(섹션 7). 컴파일된 workflow는 harness에게 모델 없이 실행되는 프로그램을 건넵니다.
agent가 같은 종류의 티켓을 열 번 예약했다고 합시다. 다섯 단계면 그것이 프로그램이 됩니다:

1. **포착.** 잘 된 실행 하나를 기록합니다: 어떤 호출을 어떤 순서로 했는지, 그리고 각 호출 앞뒤의 상태.
2. **매개변수화.** 실행마다 달라진 것은 인자로 바꿉니다. 그대로였던 것이 프로그램이 됩니다.
3. **리셋 위에서 검증.** 깨끗한 환경에서 다시 재생합니다(섹션 23). 모든 단계는 실행 전 검사, 실행 후 검사, 그리고 최종 상태 검사를 받습니다.
4. **재생.** 다음에 조건이 맞는 task가 오면 프로그램을 그대로 끝까지 실행합니다. 모델 호출이 없으므로 빠르고, 싸고, 매번 똑같습니다.
5. **무효화.** 검사 하나만 실패해도 그 프로그램은 물러납니다. task는 모델에게 돌아가고, 모델은 새 프로그램을 포착할 수 있습니다.

tool을 만드는 일도 반대편에서 본 같은 생애 주기입니다. agent는 할 수 없는 일을 만나면 라이브러리를 찾아
tool로 감싸고, registry가 받아들이기 전에 검증합니다(섹션 2).
두 움직임 모두 비싼 탐색 한 번을 검사할 수 있는 싼 능력으로 바꿉니다.
둘 다 다섯 번째 단계가 필요합니다. 그것들이 기대고 만들어진 사이트나 API가 바뀌기 때문입니다.

**harness 고치기.** loop가 prompt만이 아니라 harness 코드를 바꾸려 한다고 합시다. 그러면 패치를 받기 전에 계약이 필요합니다.
변경 계약은 네 가지를 밝힙니다: 어떤 trace가 얼마나 자주 실패했는지, 근본 원인, 그 변경이 무엇을 개선해야 하는지, 그리고 되돌리는 방법.
계약이 없으면 패치도 없습니다. 사람이 그 계약을 읽고, 그것이 스스로를 고치는 loop와 아무도 감사할 수 없는 loop를 갈라놓습니다.
loop가 고쳐도 되는 코드는 미리 선언해 둡니다. permission, 예산, 게이트는 그 영역 밖에 있어서 loop가 닿을 수 없습니다(섹션 3).

loop가 어디를 탐색하는지는 사다리입니다. 맨 아래 칸은 prompt 안의 규칙 하나입니다.
그 위로는 context를 어떻게 조립하는지, 그다음 workflow, 그다음 harness 코드, 그다음 변경을 제안하는 코드입니다.
아래 칸이 실패했을 때에만 한 칸 올라갑니다. 한 칸 올라갈 때마다 탐색은 넓어지고 그에 대한 검사는 약해집니다.
prompt 규칙 하나는 하루면 A/B 테스트할 수 있습니다. 변경을 제안하는 코드를 바꾸면, 그 뒤의 모든 변경이 다르게 제안됩니다.

**온라인 실행, 오프라인 학습기.** 둘은 떼어 놓습니다. 온라인 loop는 task를 실행하고 무슨 일이 있었는지 기록합니다.
교훈을 뽑거나, skill을 승격하거나, prompt를 고치지는 않습니다.
별도의 오프라인 loop가 여러 실행을 한꺼번에 읽어서, 반복되는 실패를 찾고, 후보 변경을 작성하고, 검증하고, 버전을 릴리스합니다.

이 분리가 실행 하나가 agent를 다시 쓰는 것을 막습니다. 운 좋은 경로 하나는 패턴이 아닙니다.
agent에게 무엇을 기억하라고 시킨 웹 페이지는 근거가 아닙니다.
여러 실행에서 같은 신호가 나올 것을 요구하고 거기에 검증 게이트를 두면, 둘 다 릴리스에 들어가지 못합니다.

이 분리는 무엇을 측정할지도 바꿉니다. 숫자는 하나가 아니라 둘을 봅니다:

- **갱신.** loop가 좋은 후보를 내놓고 있는가. 몇 개를 제안했고, 몇 개가 검증을 통과했고, 몇 개가 롤백되었는가.
- **이득.** 출시된 변경이 도움이 되고 있는가. 그 변경이 겨냥한 실행에서 실제로 불러와지는가, agent가 그것을 따르는가, 홀드아웃 성능이 움직이는가.

둘 다 봅니다. 첫 번째만 보면, 맞기는 하지만 한 번도 불러와지지 않는 skill이 실패한 갱신처럼 보이고, loop는 자기 자신에 대해 틀린 결론을 내립니다.

---

## 시스템별

각 agent가 바깥 loop들을 조합하는 방식입니다.

| | Claude Code | Hermes Agent | mini-swe-agent | deepseek-harness |
| --- | --- | --- | --- | --- |
| **장점** | 스크립트로 짠 검증에 단단한 예산까지 있음. | 예산에 더해, 롤백이 있는 개선 loop까지 있음. | 실행마다 단단한 청구 한도가 있음. | 바깥 loop가 발행된 event에 plugin으로 붙음. |
| **단점** | 소스에는 닫힌 개선 loop가 없음. | 채점 후 재시도 loop가 기본 제공되지 않음. | 예산 쪽 절반뿐임. | 작업을 검사하는 것이 없고, 예산은 라운드 수뿐임. |
| **이유** | 바깥 loop는 직접 스크립트로 짜는 프로그램임. | 개선은 모델까지 닿아야 함. | 실행 하나가 채점되는 task 하나임. | loop 자체가 plugin이므로 제어가 거기에 붙음. |
| **방법: 검증** | 스크립트로 짠 단계들과 판정기 패널. | 만드는 쪽과 검사하는 쪽, 그리고 오프라인 테스트. | 없음. SWE-bench가 오프라인에서 채점함. | 기본 제공 없음. 완료는 스스로 선언함. |
| **방법: event loop** | cron, 깨우기, 원격 트리거. | 제한된 tool 집합을 쓰는 cron. | 없음. 실행기는 시간이 아니라 task를 스케줄함. | 알림이 로그에서 turn으로 재생됨. |
| **방법: 개선 loop** | 재개 가능한 workflow가 캐시에서 재생됨. | 실행이 학습 데이터가 됨. | 없음. 예산뿐임. | 출시된 것 없음. 붙일 지점만 존재함. |

---

## 실패 모드

- **정지 조건 없음.** 상한이 없는 재시도 loop는 누군가 청구서를 볼 때까지 token을 태웁니다. 완화: harness가 강제하는 반복, token, 시간 예산.
- **자기 채점.** 워커가 자기 출력을 통과시키므로, 검증 loop가 아무것도 검증하지 않습니다. 완화: 별도의 검사 agent와 loop 바깥에 고정된 rubric.
- **거수기 rubric.** 항상 통과시키는 채점기는 없는 것보다 나쁩니다. 나쁜 출력에 검증됨이라는 딱지를 붙이기 때문입니다.
  완화: 적대적 검증(검사기에게 반박해 보라고 prompt를 줌)과 주기적인 사람의 표본 검사.
- **너무 이른 무인 운영.** loop의 L1 보고를 한 번도 검사해 보지 않은 채 L3 쓰기 권한을 줍니다.
  완화: 섹션 3의 permission으로 게이트를 걸고, 성숙도 사다리를 한 번에 한 단계씩 올라갑니다.
- **조용한 품질 저하.** 무인 loop의 품질이 떨어지는데 아무도 그 출력을 읽지 않습니다. 완화: 하트비트, 항상 전달되는 보고, 통과율과 비용에 대한 섹션 20의 지표.
- **상태 기억 상실.** 실행마다 같은 일을 다시 찾아내고 다시 합니다. 완화: 발견한 것을 memory나 task 기록에 남기고(섹션 9, 12), 실행 시작 때 읽습니다.
- **게이트를 빠져나가는 자기 수정 harness.** harness 코드를 고칠 수 있는 개선 loop는 자기를 막는 코드도 고칠 수 있습니다.
  완화: permission과 예산은 loop가 고칠 수 있는 어떤 것보다도 바깥에 둡니다(섹션 3).
- **대리 목표 이탈.** 열린 작업에서 rubric은 진짜 목표를 대신할 뿐입니다. loop는 그 대신 rubric을 만족시키는 법을 배웁니다:
  익숙한 코드를 재사용하고, 잡음을 발견으로 읽고, 통과한 실행만 남깁니다. 점수는 올라가고 진짜 목표는 미끄러집니다.
  완화: 실패한 실행도 근거에 남기고, 홀드아웃 세트를 새로 고치고, 사람이 출력을 진짜 목표에 비추어 검사합니다.
- **모든 교훈이 prompt 수정이 됨.** prompt가 가장 쓰기 쉬운 자리라서 결국 전부 거기로 갑니다. prompt는 자기 규칙끼리 어긋날 때까지 자랍니다.
  완화: 교훈이 무엇이냐에 따라 자리를 고릅니다. 사실은 문서로, 절차는 프로그램으로 가고, prompt는 반복되어야 하는 행동을 위한 자리입니다.
- **환경보다 오래 남은 컴파일된 workflow.** 사이트나 API가 바뀌었는데 프로그램은 그대로 재생됩니다. 모델보다 빠르게 틀린 상태를 씁니다.
  완화: 재생의 모든 단계 앞뒤로 검사하고, 검사가 처음 실패하면 그 프로그램을 물립니다.
- **온라인 실행이 스스로 교훈을 승격함.** 실행 도중에 교훈을 뽑는 agent는 방금 운이 좋았던 경로를 승격할 수 있고, 기억하라고 심어 둔 신뢰할 수 없는 페이지의 내용을 적을 수도 있습니다.
  완화: 온라인 loop는 근거만 기록하게 합니다. 별도의 오프라인 단계가 릴리스 전에 후보를 검증합니다.

---

## 실행 방법

[`src/`](src/)는 20을 이어받고 다음을 추가합니다:

- [`verify.py`](src/verify.py): 검증 loop(`verified_run`: 채점, 피드백 재시도, 예산, 에스컬레이션)와 판정마다 새로 만드는 채점기 `agent_checker`.
- [`test.py`](src/test.py): 첫 시도 통과, 재시도까지 전달되는 피드백, 예산 상한, PASS/FAIL 판정 계약에 대한 오프라인 검사.
- [`demo.py`](src/demo.py): 실제 검증 실행 하나. add tool을 가진 워커, 고정된 rubric으로 채점하는 별도의 검사기, 예산을 다 썼을 때의 에스컬레이션.

loop는 바뀌지 않습니다. 검증이 그것을 바깥에서 감쌉니다.

```bash
python sections/21-loop-engineering/src/test.py         # offline checks, no key
uv run python sections/21-loop-engineering/src/demo.py  # live demo, needs a key
```

---

## 출처

- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness), `dsh-v0.1.0-rc.7` 기준:
  `docs/subsystems/core.md`, `packages/workflow/tool-ralph/README.md`, `packages/schedule/schedule/README.md`, `docs/subsystems/goal.md`.
- [cobusgreyling/loop-engineering](https://github.com/cobusgreyling/loop-engineering): 구성 요소와 준비도 단계.
- [LangChain · The art of loop engineering](https://www.langchain.com/blog/the-art-of-loop-engineering): 겹겹이 쌓인 네 개의 loop.
- [Addy Osmani · Loop engineering](https://addyosmani.com/blog/loop-engineering/): 조합된 구성 요소.
- [MindStudio · What is loop engineering](https://www.mindstudio.ai/blog/what-is-loop-engineering-autonomous-ai-agent-workflows): 목표 조건.
- [Lilian Weng · Harness engineering for self-improvement](https://lilianweng.github.io/posts/2026-07-04-harness/): 개선 loop를 깊이 다루고, 게이트를 loop 바깥에 둡니다.
- [ai-agent-book · chapter 8](https://github.com/bojieli/ai-agent-book/blob/main/book/chapter8.md) (《深入理解 AI Agent》, 李博杰. 중국어 원문이 기준):
  세 겹의 검증 층, 배운 변경이 놓일 자리를 고르는 규칙, prompt learning 게이트, 변경 계약,
  메타 최적화 사다리, 온라인과 오프라인의 분리, 진화 지표의 분리, 그리고 검증 가능한 loop의 경계.
- [PreAct](https://arxiv.org/abs/2606.17929): trajectory를 사전 검사, 사후 검사, 저장 전 검사를 갖춘 매개변수화된 workflow로 컴파일한 뒤 모델 없이 재생합니다.
  제1저자의 이름이 책 저자와 같으므로, 보고된 재생 속도 향상(대략 8.5배에서 13배)은 단일 출처로 읽습니다.
- [Alita](https://arxiv.org/abs/2505.20286): 능력의 공백이 tool 제작을 촉발하고, 그 tool은 라이브러리에 들어가기 전에 검증됩니다.
- Karpathy · "system prompt learning" (X, 2025년 5월 11일): 가중치 대신 단어를 고치는 것을 세 번째 학습 패러다임으로 명명.
- [ACE](https://arxiv.org/abs/2510.04618): prompt 전체를 다시 쓰는 대신 안정적인 id가 붙은 context 항목을 점진적으로 고칩니다.
- [Lin et al.](https://arxiv.org/abs/2605.30621): harness 갱신과 harness 이득을 따로 측정하고, 둘을 구분하기 위해 모델 교체를 사용합니다.
- [AHE](https://arxiv.org/abs/2604.25850)와 [Self-Harness](https://arxiv.org/abs/2606.09498): 스스로를 고치는 harness를 위한 변경 계약과 제한된 후보 공간.
- [Claude Code](https://code.claude.com/docs): `/loop`, `ScheduleWakeup`, `Workflow` 스키마. 소스 백업이 아니라 tool 스키마와 문서화된 동작에서 가져왔습니다.
- [Hermes Agent source](https://github.com/NousResearch/hermes-agent):
  `agent/iteration_budget.py`, `cron/scheduler.py`, `tools/skill_manager_tool.py`, `hermes_cli/curator.py`, `agent/trajectory.py`.
- [mini-swe-agent source](https://github.com/swe-agent/mini-swe-agent): `agents/default.py`의 `AgentConfig`와 `query()`, `agents/interactive.py`, `run/benchmarks/swebench.py`.
