# 7 · Skill

[English](README.md) · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · **한국어**

> skill은 전문성을 한 덩어리로 담은 묶음입니다. 지시문에 스크립트와 파일을 더한 것이고, 작업에 필요할 때만 불러옵니다.

skill은 범용 agent를 한 가지 일의 전문가로 바꿉니다.
skill은 작업 흐름을 포장합니다. 따라야 할 지시문에, 실행할 스크립트와 참고할 자료 파일을 더한 것입니다.
agent는 작업이 요구할 때만 skill을 불러오므로, agent 하나가 전부를 미리 올려놓지 않고도 여러 전문 능력에 닿을 수 있습니다.

skill 하나는 `SKILL.md` 파일이 들어 있는 폴더입니다. frontmatter가 skill의 이름과 설명을 적습니다.
본문에는 지시문이 들어가고, 폴더에는 추가 스크립트와 자료 파일을 함께 담을 수 있습니다. 이 파일들은 skill이 실제로 쓸 때만 불러옵니다.

agent는 어떤 skill이 있는지 알아야 하지만, 매 turn마다 모든 skill 본문의 비용을 치러서는 안 됩니다.

skill 시스템은 다음을 해내야 합니다.

1. 쓸 수 있는 skill을 싸게 나열합니다.
2. skill이 선택되었을 때만 전체 지시문을 불러옵니다.
3. skill이 추가 파일을 가리키되 그 파일이 자동으로 불려오지는 않게 합니다.
4. 내장, 사용자, 프로젝트, plugin, MCP 소스에서 skill을 찾아냅니다.

이 계층이 없으면 prompt가 너무 커지거나, agent가 자기 확장 기능을 찾지 못합니다.

---

## 메커니즘

![Mechanism diagram](assets/07-skills.png)

skill은 progressive disclosure를 씁니다. 모델은 더 불러올지 말지 결정할 만큼의 정보만 봅니다.

1. **메타데이터.** frontmatter의 `name`과 `description`, 그리고 skill의 경로입니다. 이 값싼 카탈로그는 매 turn마다 system prompt에 실려 갑니다.
2. **지시문.** `SKILL.md`의 본문입니다. 모델은 작업에 그 skill이 필요할 때만 파일을 읽습니다.
3. **자료.** skill 폴더 안의 추가 파일입니다. 지시문이 그 파일을 가리키면, 모델은 같은 파일 tool로 읽습니다.

skill 전용 tool은 필요 없습니다. 카탈로그가 각 skill의 이름과 경로를 적어 주기만 하면,
agent는 평범한 Read tool로 파일을 읽어 skill을 불러옵니다. L2와 L3은 둘 다 그냥 파일 읽기입니다.

일의 대부분은 description이 합니다. description은 요약이 아니라 라우팅 조건입니다.
모델이 결정하기 전에 보는 것은 그 한 줄뿐입니다. 그러니 언제 이 skill을 쓰는지, 그리고 언제 쓰지 않는지를 적습니다.
반례를 하나 넣습니다. 주제만 적어 놓은 줄은 모델을 추측하게 만듭니다.

### 새로 추가: skill 스캔과 prompt 목록

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

- `load_skills`는 `SKILL.md` 파일들을 스캔해 카탈로그용 frontmatter만 남깁니다.
- `catalog_prompt`는 그 카탈로그를 system prompt에 넣습니다. skill 하나에 한 줄이고, 읽을 경로가 함께 들어갑니다.
- 본문과 자료는 평범한 파일입니다. 평범한 Read tool이 필요할 때 불러오므로, skill 전용 tool은 없습니다.
- Read tool은 skill 디렉터리로 범위가 묶여 있어서, skill 이름이 파일 시스템 바깥으로 빠져나갈 수 없습니다.

### 새로 추가: 저장소의 진화

불러오기는 skill 시스템의 절반입니다. 저장소는 자라기도 하고 쇠퇴하기도 합니다 (hermes-agent는 이를 skill evolution이라 부릅니다).

성장은 쓰기입니다. agent는 끝난 작업 흐름을 새 skill로 정제해 둡니다. 그러면 다음 실행은 다시 알아내는 대신 지시문을 불러옵니다.

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

- `WriteSkill`은 이 함수를 감싼 모델용 tool입니다. skill을 쓰는 것은 부수 효과이므로, 규칙이 미리 승인하지 않는 한 섹션 3의 gate가 사용자에게 묻습니다.
- 쓰인 파일은 평범한 `SKILL.md`입니다. 특별한 표시는 없고, 다음 `load_skills` 스캔이 사람이 쓴 skill과 똑같이 카탈로그에 올립니다.
- 이름은 `read_tool`이 경로를 검사하는 방식과 똑같이 resolve하고 검사합니다. 그래서 저장소는 어느 방향으로도 빠져나갈 수 없습니다.

쇠퇴는 측정에서 시작합니다. skill을 불러오는 것이 사용 신호이므로, `read_tool`이 읽기의 부수 효과로 이를 기록합니다.

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

- 기록의 키는 skill의 폴더 이름이고, 모델이 읽은 경로에서 가져옵니다. 자료 읽기(L3)는 값을 올리지 않고, `SKILL.md` 본문(L2)만 올립니다.
- 기록이 없는 skill은 `last_used_at`이 0이므로, 한 번도 쓰이지 않은 skill도 낡은 것으로 셉니다.
- `stale_skills`는 보고서이지 조치가 아닙니다. 무엇을 할지 정하는 것은 큐레이터의 일이고, hermes-agent는 같은 신호로 백그라운드 큐레이터 agent를 돌립니다 (보관, 통합, 고정).
- 데이터 흐름은 실행을 가로지르는 loop입니다. 읽기가 `.usage.json`을 올리고, 큐레이터가 그것을 읽고, 카탈로그가 살아남은 것을 반영하고, `WriteSkill`이 새 항목을 넣습니다.

### 통합 방식

loop는 바뀌지 않습니다. skill을 읽으면 tool result가 돌아오고, 그것이 `messages[]`에 들어갑니다.

카탈로그는 system prompt에 자리합니다. 본문은 모델이 파일을 읽은 뒤에야 대화에 들어옵니다. 자료 파일은 필요할 때만 나중에 읽습니다.

불러온 skill 텍스트는 `messages[]`에 살기 때문에, context가 차면 다른 메시지처럼 compaction 대상이 됩니다 (섹션 8).
skill 본문은 짧게 유지하고, 큰 참고 자료는 파일로 가리킵니다.

### 더 읽을거리

여기부터는 `src/`에 없습니다. ai-agent-book과 벤더 문서에서 온 내용이고, 표에 있는 시스템들에서 확인된 것은 아닙니다.

**카탈로그의 비용.** progressive disclosure는 큰 skill 저장소의 비용을 낮춥니다. 공짜로 만들지는 않습니다.
카탈로그는 prefix에 앉아 있어서, prefill에서 한 번 읽히고 그 뒤로는 매 turn마다 다시 보내집니다.
첫 turn 이후에는 그 prefix가 캐시되므로, 다시 보내는 비용은 쌉니다.
불러온 본문은 비용이 더 크고, 무언가 compaction할 때까지 window에 남습니다.
그래서 지켜볼 숫자는 본문을 얼마나 자주 읽는지가 아니라, 카탈로그가 몇 개의 skill을 담고 있는지입니다.

**카탈로그가 사는 곳.** 목록은 system prompt에 둘 수 있고, `src/`가 하는 방식이 그것입니다.
활성화용 tool 하나의 description 안에 둘 수도 있습니다. 공개 표준은 둘 다 허용합니다.
차이는 token이 어디에 떨어지느냐입니다. system prompt에 두면 모든 session의 prefix에 포함됩니다.
tool description에 두면 prefix가 더 작게 유지되고, 모델은 그 tool을 거쳐 목록에 닿습니다.

**tool의 지연 로딩.** tool도 같은 이유로 같은 패턴을 쓸 수 있습니다. 스키마는 크고, 대부분의 turn은 그것이 필요 없습니다.
prefix에는 tool 이름과 한 줄 설명만 남깁니다. 모델은 필요할 때 전체 스키마를 요청합니다.
그 스키마는 context 끝에 덧붙으므로, 캐시된 prefix는 건드려지지 않고 그 앞의 어떤 것도 다시 계산할 필요가 없습니다.
이 리포지터리가 progressive disclosure를 처음 만난 곳이 skill입니다. 책은 같은 패턴이 tool 계층으로 옮겨 가고 있다고 보고합니다 (섹션 2).

**언제 skill을 쓸 것인가.** 어떤 실행이 긴 작업 흐름을 처음으로 제대로 끝냈다고 합시다. 이것을 skill로 만들어야 할까요?
여기 데모는 그렇다고 답합니다. 작업 흐름이 끝나면 agent가 `WriteSkill`을 호출하고, 다음 스캔이 그것을 카탈로그에 올립니다.
loop를 보여 주는 가장 작은 규칙이고, 실행 가능한 코드가 하는 일이 그것입니다.

**책이 요구하는 더 높은 기준.** 책은 아니라고 답합니다. 한 번의 실행은 근거로 너무 적기 때문입니다.
책은 skill이 정식 능력이 되기 전에 네 가지를 요구합니다.

- 실패하지 않은 실행 최소 두 번에서 같은 패턴이 나올 것.
- 그 skill을 제안한 실행 바깥에서 온 검증이 있을 것. Voyager가 이렇게 동작합니다. 환경이 확인해 준 뒤에야 skill이 라이브러리에 들어갑니다.
- 먼저 저장소를 검색할 것. 비슷한 것이 이미 있으면 중복을 더하지 말고 그것을 고칠 것.
- 그 실행이 부딪힌 함정을 본문에 남길 것. 잘 풀린 경로만 남기지 말 것.

**어느 규칙을 고를 것인가.** 둘 다 근거가 있습니다. 서로 다른 질문에 답하기 때문입니다.
첫 성공 규칙은 메커니즘을 가르치고 데모를 짧게 유지합니다. 지지 횟수 기준은 수백 개짜리 저장소가 한 번 쓰인 메모로 차는 것을 막습니다.
그 사이에 후보 단계가 있습니다. 정제된 작업 흐름이 카탈로그 항목이 아니라 후보로 들어옵니다.
후보는 승격 전에 초안이 만들어지고, 시험되고, 평가되고, 수정됩니다. Anthropic의 Skill Creator가 그 loop를 돌립니다.
이 섹션의 코드에서는, 큐레이터가 승격하기 전까지 `load_skills`가 건너뛰는 대기 폴더가 될 것입니다.

**통합은 오프라인에서 돕니다.** 큐레이터는 실행 중이 아니라 예약된 작업입니다. 책은 이를 sleep-time learning이라 부르고 다섯 단계를 줍니다.

1. **발화.** schedule, 유휴 시간대, 또는 크기 한도를 넘긴 저장소.
2. **정렬.** 먼저 저장소의 스냅샷을 떠 둡니다. 이후 모든 단계를 되돌릴 수 있게 하기 위해서입니다.
3. **수집과 병합.** 사용 기록과 최근 실행을 읽습니다. 거의 같은 것들을 skill 하나로 접습니다. 후보를 끌어옵니다.
4. **검증과 승인.** 병합된 본문을 그것을 만들어 낸 실행과 대조해 확인합니다. 통과하지 못한 것은 들어오지 못합니다.
5. **정리와 색인.** 정해진 규칙으로 낡은 skill을 보관하고, 카탈로그를 다시 만듭니다.

**왜 오프라인이 중요한가.** 큐레이터를 오프라인으로 돌리는 것 자체가 안전 경계입니다. 온라인 loop는 실행하고 기록합니다.
실행 도중에 저장소를 고치지는 않습니다. 그래서 운 좋은 한 번의 실행이 스스로를 승격시킬 수 없고,
agent가 바깥에서 읽어 온 텍스트가 turn 사이에 영구 지시문이 될 수 없습니다.

---

## 시스템별

각 agent가 skill을 설명하고, 발화시키고, 찾아내는 방식입니다.

| | Claude Code | Hermes Agent | deepseek-harness |
| --- | --- | --- | --- |
| **장점** | 예산 안에 들어감. skill이 fork하고 tool 범위를 좁힐 수 있음. | 큐레이터가 새 skill을 병합하고 낡은 것을 보관함. | 카탈로그가 히스토리에 실려 가고, 바뀌면 갱신됨. |
| **단점** | 모호한 description은 skill을 숨김. | 자동 변경에는 고정과 단계별 승인이 필요함. | 카탈로그를 다시 쓰면 히스토리에 메시지가 늘어남. |
| **이유** | skill이 fork하고 tool 범위를 정하므로, 파일 읽기만으로는 부족함. | 불러오기는 절반일 뿐이고, 저장소는 자라고 쇠퇴해야 함. | session이 도는 동안에도 skill이 바뀜. |
| **방법: skill format** | `SKILL.md` 폴더. frontmatter로 tool을 제한할 수 있음. | 같은 형태이고, 카테고리 폴더로 분류됨. | 묶음 또는 단일 파일. frontmatter가 호출 권한을 정함. |
| **방법: load trigger** | `Skill` 호출이 본문을 주입하고, 파일 일치로도 발화함. | `skill_view`가 본문을 돌려주면서 사용 횟수를 올림. | tool이 요청을 받아 본문을 다시 읽음. |
| **방법: discovery** | 내장, 사용자, 프로젝트, plugin, MCP 소스. | 번들, 선택, 사용자, plugin, 허브 소스. | provider가 계층화된 범위와 순위 매긴 루트를 병합함. |

---

## 실패 모드

- **skill이 아예 발화하지 않음.** description이 너무 모호합니다. 발화 조건 형태로 description을 씁니다.
- **카탈로그가 너무 커짐.** skill이 너무 많으면 prompt를 밀어냅니다. skill을 좁게 유지하고 로더가 다듬게 합니다.
- **compaction 후 본문이 사라짐.** skill 파일을 다시 읽거나 본문을 짧게 유지합니다.
- **경로 탈출.** 카탈로그는 모델에게 경로를 건네줍니다. Read tool을 skill 디렉터리로 묶어서 `../`로 빠져나가지 못하게 합니다.
- **fork한 skill이 살아 있는 context를 잃음.** fork한 skill은 독립적으로 완결되는 작업에만 씁니다.
- **공급망에서 온 오염된 skill.** 설치한 서드파티 skill은 바깥 콘텐츠이지만, 지시문으로 불려 옵니다.
  카탈로그가 이미 그것을 보증하고 있으므로, 오염된 웹 페이지보다 영향 범위가 넓습니다.
  설치 전에 본문과 함께 담긴 스크립트를 읽습니다. 버전을 고정합니다. 갱신할 때 다시 검토합니다.
- **주입된 텍스트가 영구화됨.** `messages[]`에 들어온 prompt injection은 session과 함께 사라집니다. 같은 텍스트가 `SKILL.md`에 쓰이면 이후 모든 실행에서 불려 옵니다.
  그러니 검토되지 않은 바깥 콘텐츠가 `WriteSkill`에 닿게 하지 않습니다. 별도 과정이 승인할 때까지 새 skill은 후보로 붙잡아 둡니다.
  skill이 그 승인 gate를 고치게 두지 않습니다.
- **사용 횟수가 학습을 부풀림.** skill을 불러오는 것과 그것을 따르는 것은 다릅니다. 불러온 횟수는 카탈로그가 라우팅했다는 뜻입니다.
  그 skill이 결과를 바꿨다는 뜻은 아닙니다. 대신 두 숫자를 추적합니다. skill이 발화했는가, 그리고 실행이 나아졌는가.

---

## 실행 방법

[`src/`](src/)는 06을 이어받아 다음을 추가합니다.

- [`skills.py`](src/skills.py): 카탈로그 스캔, system prompt 목록, 경로가 묶인 `Read` tool, 그리고 진화 쪽 절반(`WriteSkill`, `record_use`, `stale_skills`).
- `skills/<name>/SKILL.md`: 예제 skill들. 자료 파일이 딸린 것도 하나 있습니다.
- [`loop.py`](src/loop.py): skill을 불러오는 것이 그냥 파일 읽기이므로 바뀌지 않았습니다.
- [`test.py`](src/test.py): 카탈로그 스캔, prompt 목록, 파일 불러오기, 경로 탈출 거부, 사용 횟수 증가, 낡음 판정, 그리고 agent가 쓴 skill이 카탈로그에 들어오는지 확인합니다.
- [`demo.py`](src/demo.py): agent가 skill을 쓴 뒤 새 skill을 저장합니다. 마지막 스캔이 저장소가 커진 것을 보여 줍니다.

```bash
python sections/07-skills/src/test.py         # offline checks, no key
uv run python sections/07-skills/src/demo.py  # live demo, needs a key
```

---

## 출처

- [Claude Code 소스](https://github.com/yasasbanukaofficial/claude-code):
  `skills/loadSkillsDir.ts`, `skills/bundledSkills.ts`, `skills/mcpSkillBuilders.ts`, `tools/SkillTool/SkillTool.ts`, `tools/SkillTool/prompt.ts`.
- [Hermes Agent 소스](https://github.com/NousResearch/hermes-agent):
  `tools/skills_tool.py` (`skills_list`, `skill_view`), `tools/skill_usage.py`, `hermes_cli/curator.py`, `tools/skills_hub.py`, `tools/skills_ast_audit.py`.
- [deepseek-harness 소스](https://github.com/deepseek-ai/deepseek-harness), `dsh-v0.1.0-rc.7` 기준:
  `packages/skill/skill/src/index.ts`, `packages/skill/skill-filesystem/src/index.ts`, `packages/skill/tool-skill/src/index.ts`,
  `docs/subsystems/skills.md`, `docs/tool-catalog.md`.
- [Anthropic Agent Skills best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices): progressive disclosure의 단계.
- [learn-claude-code · s07_skill_loading](https://github.com/shareAI-lab/learn-claude-code): 섹션 구성 참고.
- [ai-agent-book](https://github.com/bojieli/ai-agent-book): `book/chapter2.md`, `book/chapter8.md`, 중국어 원문이 기준.
- [Agent Skills open standard](https://agentskills.io): 카탈로그를 어디에 둘 것인가, system prompt인가 활성화 tool의 description인가.
- [Claude Code · prompt caching](https://code.claude.com/docs/en/prompt-caching): 불러온 skill 본문이 어디에 떨어지고 비용이 얼마인가.
- [Voyager](https://arxiv.org/abs/2305.16291): 환경이 검증한 뒤에야 skill이 라이브러리에 들어감.
- [Anthropic Skill Creator](https://github.com/anthropics/skills): 승격 전에 초안, 시험, 평가, 수정.
- Lin et al., [arXiv:2605.30621](https://arxiv.org/abs/2605.30621), 책을 통해 인용: 갱신이 반영되었는가와 그것이 도움이 되었는가는 별개의 측정.
