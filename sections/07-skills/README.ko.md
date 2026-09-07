# 7 · Skills

[English](README.md) · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · **한국어**

> skill은 필요할 때만 로드되는 전문 지식, 지침 및 스크립트와 파일을 포함한 자체 포함 번들입니다.

skill은 일반 에이전트를 특정 작업의 전문가로 전환합니다.
이는 지침, 실행할 스크립트 및 참조할 파일을 포함한 워크플로를 패키징합니다.
에이전트는 작업에서 호출될 때만 skill을 로드하므로, 하나의 에이전트가 모든 전문 기능을 한꺼번에 로드하지 않고도 여러 전문 기능에 접근할 수 있습니다.

각 skill은 `SKILL.md` 파일이 있는 폴더입니다. 전면부에는 skill의 이름과 설명이 포함됩니다.
본문에는 지침이 포함되며, 폴더는 skill이 사용할 때만 로드되는 추가 스크립트와 참조 파일을 함께 포함할 수 있습니다.

에이전트는 skills가 존재한다는 것을 알아야 하지만, 매 턴마다 모든 skill 본체에 대해 비용을 지불해서는 안 됩니다.

skill 시스템은 다음을 수행해야 합니다:

1. 이용 가능한 skills를 저렴하게 나열합니다.
2. skill이 선택될 때만 전체 지침을 로드합니다.
3. skills가 자동으로 로드하지 않고 추가 파일을 가리킬 수 있게 합니다.
4. 내장형, 사용자, 프로젝트, plugin 또는 MCP 소스에서 skills를 발견합니다.

이 계층이 없으면 프롬프트가 너무 크거나 에이전트가 확장 기능을 찾을 수 없습니다.

---

## 메커니즘

![메커니즘 다이어그램](assets/07-skills.png)

Skills는 progressive disclosure를 사용합니다. 모델은 더 로드할지 결정할 만큼만 정보를 봅니다.

1. **메타데이터.** frontmatter에서 `name`와 `description`, 그리고 skill의 경로. 이 저렴한 카탈로그는 매번 system prompt에 탑재됩니다.
2. **지침.** `SKILL.md` 본문. 모델은 작업에 skill이 필요할 때만 파일을 읽습니다.
3. **리소스.** skill 폴더의 추가 파일. 지침에서 이를 지시하면 모델은 동일한 파일 도구로 파일을 읽습니다.

특정 기술 도구는 필요하지 않습니다. 카탈로그가 각 skill 및 그 경로를 이름 지정하면,
에이전트는 기본 Read 도구로 파일을 읽어 skill을 로드합니다. L2와 L3는 모두 단순한 파일 읽기입니다.

설명이 대부분의 작업을 수행합니다. 요약이 아니라 라우팅 조건입니다.
모델이 결정을 내리기 전에 보는 유일한 한 줄입니다. 그래서 skill을 언제 사용할지 말하고, 언제 사용하지 않을지 말하세요.
하나의 반례를 추가하세요. 주제만을 이름으로 하는 한 줄은 모델이 추측하게 만듭니다.

### 새로 추가: skills를 스캔하고 프롬프트에 나열합니다.

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

- `load_skills`는 `SKILL.md` 파일을 스캔하고 카탈로그를 위해 프런트매터만 유지합니다.
- `catalog_prompt`는 그 카탈로그를 system prompt로 렌더링하며, skill마다 한 줄씩, 읽을 수 있는 경로와 함께 표시합니다.
- 본문과 리소스는 일반 파일입니다. 일반 Read 도구는 필요에 따라 이를 로드하므로, 특정 기술 도구는 없습니다.
- Read 도구는 skills 디렉터리에 국한되어 있으므로, skill 이름이 파일 시스템으로 탈출할 수 없습니다.

### 새로운 기능: 상점이 진화하다

로딩은 skill 시스템의 절반이다. 상점은 또한 성장하고 쇠퇴한다(Hermes는 이를 skill 진화라고 부른다).

성장은 기록이다. 에이전트는 완성된 작업 흐름을 새로운 skill로 증류하므로, 다음 실행 시 지침을 재발견하는 대신 로드한다:

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

- `WriteSkill`는 이 기능을 위한 모델 대상 도구이다. skill 작성은 부수 효과이므로, 규칙이 사전 승인하지 않는 한 섹션-3 게이트가 묻는다.
- 작성된 파일은 일반 `SKILL.md`이다. 특별한 표시 없이, 다음 `load_skills` 스캔은 손으로 쓴 skill처럼 이를 카탈로그로 등록한다.
- 이름은 `read_tool`가 경로를 확인하는 것과 같은 방식으로 확인되므로, 상점은 어느 쪽 방향으로도 탈출할 수 없다.

분해는 측정에서 시작됩니다. skill을 로드하는 것은 사용 신호이며, 따라서 `read_tool`는 이를 읽기의 부작용으로 기록합니다:

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

- 기록은 모델이 읽은 경로에서 가져온 skill의 폴더 이름을 기준으로 합니다. 리소스 읽기(L3)는 이를 증가시키지 않고, 오직 `SKILL.md` 본체(L2)만 증가시킵니다.
- 기록이 없는 skill은 `last_used_at`가 0이므로, 사용되지 않은 skills도 오래된 것으로 간주됩니다.
- `stale_skills`는 동작이 아닌 보고서입니다. 그것을 어떻게 처리할지는 큐레이터의 업무이며; Hermes는 동일한 신호(보관, 통합, 고정)에 대해 백그라운드 큐레이터 에이전트를 실행합니다.
- 데이터 흐름은 실행 간 루프입니다: 읽기가 `.usage.json`를 증가시키고, 큐레이터가 이를 읽으며, 카탈로그는 남아있는 내용을 반영하고, `WriteSkill`는 새로운 항목을 제공합니다.

### 통합 방식

루프는 변경되지 않습니다. skill을 읽으면 tool result가 반환되어 `messages[]`로 들어갑니다.

카탈로그는 system prompt에 속합니다. 본문은 모델이 파일을 읽은 후에만 대화에 들어갑니다. 리소스 파일은 필요할 때만 나중에 읽습니다.

로드된 skill 텍스트가 `messages[]`에 존재하기 때문에, 문맥이 가득 찰 때 다른 메시지처럼 압축할 수 있습니다(섹션 8).
skill 본문은 짧게 유지하고 큰 참조를 위해 파일을 가리키십시오.

### 추가 읽기

이 중 어느 것도 `src/`에 포함되어 있지 않습니다. 이것은 ai-agent-book과 공급업체 문서에서 나온 것이며, 표에 있는 시스템에 대해 확인된 것은 아닙니다.

**카탈로그의 비용.** Progressive disclosure는 대형 skill 저장소 비용을 낮춥니다. 무료로 만들지는 않습니다.
카탈로그는 접두사에 위치하므로, 사전 채움(prefill) 시 한 번 읽히고 그 이후 모든 턴에서 다시 전송됩니다.
첫 번째 턴 후에는 그 접두사가 캐시되어 있으므로, 다시 전송하는 비용은 적습니다.
로딩된 본문(body)은 비용이 더 들며, 압축(compact)될 때까지 창(window)에 남아 있습니다.
따라서 주목해야 할 숫자는 본문이 얼마나 자주 읽히는지가 아니라, 카탈로그가 얼마만큼의 skills를 운반하는지입니다.

**카탈로그가 존재하는 위치.** 목록(listing)은 system prompt에 위치할 수 있으며, 이는 `src/`가 수행하는 일입니다.
또한 하나의 활성화 도구(activation tool) 설명 안에 위치할 수도 있습니다. 오픈 스탠더드(open standard)는 두 가지 방식 모두 허용합니다.
트레이드오프(trade-off)는 토큰이 어디에 위치하느냐입니다. system prompt에서는 모든 세션의 접두사 일부가 됩니다.
도구 설명에서 접두사는 더 작게 유지되며, 모델은 대신 해당 도구를 통해 목록에 접근합니다.

**지연된 도구 로딩.** 도구들은 동일한 이유로 같은 패턴을 사용할 수 있습니다: 스키마가 크고 대부분의 턴에서는 필요하지 않기 때문입니다.
접두사는 도구 이름과 한 줄 설명만 보관합니다. 모델은 필요할 때 전체 스키마를 요청합니다.
그 스키마는 컨텍스트 끝에 추가되므로, 캐시된 접두사는 그대로 유지되고 그 앞의 내용은 재계산할 필요가 없습니다.
Skills는 이 저장소가 progressive disclosure를 처음 만난 곳이었습니다. 책에서도 도구 계층으로 이동할 때 동일한 패턴을 보고 있습니다(2장).

**skill을 언제 작성해야 하는가.** 한 실행이 긴 워크플로를 처음으로 올바르게 완료했을 때라고 말합니다. 그것이 skill이 되어야 할까요?
여기 데모는 예라고 말합니다. 워크플로우가 완료되고, 에이전트가 `WriteSkill`를 호출하며, 다음 스캔이 그것을 카탈로그화합니다.
이것이 루프를 보여주는 가장 작은 규칙이며, 실행 가능한 코드가 하는 일입니다.

**책의 더 높은 기준.** 책에서는 아니라고 말합니다. 한 번 실행된 것은 충분한 증거가 아니기 때문입니다.
skill이 공식 기능이 되기 전에 책에서는 네 가지를 요구합니다:

- 실패하지 않은 최소 두 번의 실행에서 동일한 패턴.
- skill을 제안한 실행에서 나오지 않은 확인. Voyager는 이렇게 작동합니다: 환경이 이를 확인한 후 skill이 라이브러리에 들어갑니다.
- 먼저 저장소 검색. 이미 존재하는 유사한 것이 있으면 중복을 추가하는 대신 패치합니다.
- 달리기가 부딪힌 함정들은 단지 작동한 경로뿐만 아니라 몸 안에 남아 있었다.

**어떤 규칙을 선택할까.** 둘 다 방어할 만하다, 왜냐하면 서로 다른 질문에 답하기 때문이다.
첫 번째 성공은 메커니즘을 배우게 하고 시연을 짧게 유지시킨다. 지원 한계는 수백 개의 저장소가 한 번만 사용된 메모로 가득 차는 것을 막는 것이다.
후보 단계가 그들 사이에 놓여 있습니다. 정제된 워크플로우는 카탈로그 항목이 아니라 후보로서 나타납니다.
홍보되기 전에 초안 작성, 테스트, 평가 및 수정이 이루어집니다. Anthropic의 Skill Creator가 그 과정을 실행합니다.
이 섹션의 코드에서 그것은 `load_skills`가 큐레이터가 승격시킬 때까지 건너뛰는 임시 폴더가 될 것입니다.

**통합은 오프라인으로 실행됩니다.** 큐레이터는 실시간 처리가 아니라 예약된 수행입니다. 책에서는 이를 수면 시간 학습(sleep-time learning)이라고 부르며 다섯 단계로 제시합니다:

1. **트리거.** 일정, 유휴 시간 또는 크기 제한을 초과한 스토어.
2. **정렬.** 나중 단계에서 언제든 롤백할 수 있도록 먼저 스토어를 스냅샷 합니다.
3. **수집 및 병합.** 사용 기록과 최근 실행을 읽습니다. 거의 중복된 항목들을 하나로 합치고 skill에 넣습니다. 후보들을 가져옵니다.
4. **검증 및 승인.** 병합된 내용을 이를 생성한 실행 기록과 비교합니다. 실패한 것은 제외됩니다.
5. **가지치기 및 색인.** 고정 규칙으로 오래된 skills를 아카이브한 뒤, 카탈로그를 다시 구축합니다.

**오프라인이 중요한 이유.** 큐레이터를 오프라인으로 실행하는 것 자체가 안전 경계입니다. 온라인 루프는 실행되고 기록합니다.
실행 중간에 스토어를 편집하지 않습니다. 따라서 한 번의 운 좋은 실행이 스스로를 승격시킬 수 없으며,
외부에서 에이전트가 읽은 텍스트는 각 턴 사이에 영구적인 지침이 될 수 없습니다.

---

## 시스템별

각 에이전트가 skills를 어떻게 설명하고, 트리거하며, 찾는지.

| | Claude Code | Hermes Agent | deepseek-harness |
| --- | --- | --- | --- |
| **장점** | 예산에 맞습니다. skill은 도구를 분기하고 범위를 설정할 수 있습니다. | 큐레이터는 새로운 skills를 병합하고 오래된 것을 보관합니다. | 카탈로그는 기록에 남아 변경 시 갱신됩니다. |
| **단점(Cons)** | 모호한 설명이 skills를 숨깁니다. | 자동 변경에는 핀과 단계별 승인이 필요합니다. | 카탈로그 재작성은 히스토리에 메시지를 추가합니다. |
| **이유(Why)** | Skills 포크와 스코프 도구 때문에, 파일 읽기만으로는 충분하지 않습니다. | 로딩이 작업의 절반이며, 스토어는 성장하고 쇠퇴해야 합니다. | Skills 세션 실행 중에 변경됩니다. |
| **방법: skill 형식(How: skill format)** | `SKILL.md` 폴더; 프론트매터는 도구를 제한할 수 있습니다. | 동일한 형태, 카테고리 폴더로 분류됨. | 번들 또는 단일 파일. 프론트매터는 호출 가능자를 설정합니다. |
| **방법: 로드 트리거(How: load trigger)** | `Skill` 호출은 본문을 주입합니다; 파일 일치 시에도 트리거됩니다. | `skill_view`는 본문을 반환하여 사용 횟수를 증가시킵니다. | 도구는 요청 시 본문을 다시 읽습니다. |
| **방법: 발견** | 내장, 사용자, 프로젝트, plugin, MCP 소스. | 번들, 선택적, 사용자, plugin, 및 허브 소스. | 제공자는 계층화된 범위와 순위가 매겨진 루트를 통해 병합됩니다. |

---

## 실패 모드

- **Skill은 절대 실행되지 않습니다.** 설명이 너무 모호합니다. 트리거 모양의 설명을 작성하세요.
- **카탈로그가 너무 커집니다.** 너무 많은 skills가 프롬프트를 혼잡하게 만들 수 있습니다. skills를 집중시키고 로더가 다듬도록 하세요.
- **본문이 압축 후 사라집니다.** skill 파일을 다시 읽거나 본문을 짧게 유지하세요.
- **경로 탐색.** 카탈로그가 모델에 경로를 제공합니다. Read 도구의 범위를 skills 디렉토리로 제한하여 `../`가 벗어나지 못하게 하세요.
- **포크된 skill은 실시간 컨텍스트를 잃습니다.** 자체적으로 완결된 작업에는 포크된 skills만 사용하세요.
- **공급망에서 오염된 skill.** 설치된 서드파티 skill은 외부 콘텐츠지만, 명령어로 로드됩니다.
  이로 인해 오염된 웹페이지보다 더 광범위하게 영향을 미칠 수 있습니다. 왜냐하면 카탈로그가 이미 이를 보증하기 때문입니다.
  설치 전에 본문과 모든 번들 스크립트를 읽으세요. 버전을 고정하고, 업데이트 시 다시 검토하세요.
- **삽입된 텍스트는 영구적이 됩니다.** `messages[]`에서의 프롬프트 삽입은 세션과 함께 종료됩니다. 동일한 텍스트를 `SKILL.md`에 작성하면 이후 모든 실행 시 로드됩니다.
  따라서 검토되지 않은 외부 콘텐츠가 `WriteSkill`에 도달하지 않도록 하세요. 새로운 skills는 별도의 검토 과정을 통해 승인될 때까지 후보로 보관하세요.
  skill이 해당 승인 게이트를 수정하게 하지 마십시오.
- **사용 횟수는 학습을 과대평가합니다.** skill을 로드하는 것은 그것을 따르는 것이 아닙니다. 로드 횟수는 카탈로그가 라우팅되었음을 나타냅니다.
  이는 skill이 결과를 변경했음을 의미하지 않습니다. 대신 두 가지 숫자를 추적하십시오: skill이 실행되었는지, 그리고 실행이 개선되었는지 여부입니다.

---

## 실행 가능

[`src/`](src/)는 06을 이어받아 다음을 추가합니다:

- [`skills.py`](src/skills.py): 카탈로그 스캔, 시스템 프롬프트 목록, 경로 범위 `Read` 도구, 그리고 진화 부분(`WriteSkill`, `record_use`, `stale_skills`).
- `skills/<name>/SKILL.md`: 리소스 파일이 포함된 샘플 skills를 포함합니다.
- [`loop.py`](src/loop.py): skill을 로딩하는 것은 단순한 파일 읽기이므로 변경되지 않음.
- [`test.py`](src/test.py): 카탈로그 스캔, 프롬프트 목록, 파일 로드, 경로 탐색 거부, 사용 증가, 오래됨, 그리고 에이전트가 작성한 skill이 카탈로그에 들어가는 것을 확인합니다.
- [`demo.py`](src/demo.py): 에이전트가 skill을 사용한 후 새 것으로 저장합니다; 마감 스캔에서는 매장이 성장한 것으로 나타납니다.

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
- [deepseek-harness 소스](https://github.com/deepseek-ai/deepseek-harness) at `dsh-v0.1.0-rc.7`:
  `packages/skill/skill/src/index.ts`, `packages/skill/skill-filesystem/src/index.ts`, `packages/skill/tool-skill/src/index.ts`,
  `docs/subsystems/skills.md`, `docs/tool-catalog.md`.
- [Anthropic Agent Skills 모범 사례](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices): progressive disclosure 수준.
- [learn-claude-code · s07_skill_loading](https://github.com/shareAI-lab/learn-claude-code): 섹션 프레이밍.
- [ai-agent-book](https://github.com/bojieli/ai-agent-book): `book/chapter2.md`, `book/chapter8.md`, 중국어 원본 정식판.
- [Agent Skills 개방 표준](https://agentskills.io): 카탈로그 배치, system prompt 또는 활성화 도구 설명.
- [Claude Code · prompt caching](https://code.claude.com/docs/en/prompt-caching): 적재된 skill 본체가 어디에 착지하는지와 비용이 얼마인지.
- [Voyager](https://arxiv.org/abs/2305.16291): skill이 환경에서 검증될 때만 도서관에 들어감.
- [Anthropic Skill Creator](https://github.com/anthropics/skills): 홍보 전에 초안 작성, 테스트, 평가, 수정.
- Lin 외, [arXiv:2605.30621](https://arxiv.org/abs/2605.30621), 책을 통해: 업데이트가 적용되는지와 그것이 도움이 되는지는 별개의 측정임.
