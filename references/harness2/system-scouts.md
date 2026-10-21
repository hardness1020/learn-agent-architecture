# harness² research notes · studied systems vs section 24 (harness adaptation)

Scope: for each system under study, the closest shipped mechanism to section 24 (an improver edits a frozen executor's configuration from probes, with no grader, inside
one task and across a stream of tasks). Feeds the section 24 per-system table. Produced by four read-only scouts (one per system), each with an adversarial self-check.
Nothing in any clone was executed. Anything marked inferred is a reading of call sites, not a stated claim.

Decisions this note supports (grilling ticket #105): columns Claude Code, Hermes Agent, deepseek-harness; mini-swe-agent dropped (all cells none).
How rows: who edits, evidence, edit surface, checks, carry over, undo. Claude Code cells describe v2.1.88 only: no internal-only or post-pin features.

| System | Ref read | Verdict |
| --- | --- | --- |
| Claude Code (pinned v2.1.88) | source backup `a371abb` (v2.1.88) plus docs | partial |
| Hermes Agent | tag v2026.7.1, `7c1a029` | partial |
| mini-swe-agent | tag v2.4.5, `e187bcb` | none |
| deepseek-harness | tag dsh-v0.1.0-rc.7, `99f6f02` | partial |

---

## Claude Code (pinned v2.1.88) · verdict: partial

**Ref read.**

Source: github.com/yasasbanukaofficial/claude-code shallow clone at commit a371abbe75ffa0d0a3c92290e2bbf56a7ef54367 (2026-04-05), treated as the v2.1.88 backup per the
task; the version string itself is not in source (MACRO.VERSION). Files read: services/extractMemories/{extractMemories.ts,prompts.ts},
services/autoDream/{autoDream.ts,config.ts,consolidationLock.ts,consolidationPrompt.ts}, memdir/{memdir.ts,memoryTypes.ts,paths.ts}, utils/hooks/skillImprovement.ts,
hooks/useSkillImprovementSurvey.ts, screens/REPL.tsx:4900, skills/bundled/{skillify.ts,remember.ts,verify.ts,index.ts}, commands/{init.ts,insights.ts,init-verifiers.ts},
commands.ts, tools/AgentTool/{agentMemory.ts,agentMemorySnapshot.ts,loadAgentsDir.ts}, utils/permissions/{filesystem.ts,permissions.ts},
utils/settings/{validateEditTool.ts,changeDetector.ts}, utils/skills/skillChangeDetector.ts, utils/hooks/hooksConfigSnapshot.ts, utils/hooks.ts
(executeConfigChangeHooks), utils/forkedAgent.ts, query/stopHooks.ts, utils/backgroundHousekeeping.ts, services/MagicDocs/magicDocs.ts. Docs fetched 2026-10-20 (current
docs, well past v2.1.88): code.claude.com/docs/en/memory, sub-agents, settings, skills, hooks (ConfigChange), checkpointing, commands, settings-reference, changelog,
llms.txt.

**Summary.**

Claude Code has no harness² loop: no reference run, no candidate edits, no probes, and no compose-or-keep decision. The nearest shipped pieces are auto memory writers.
The main agent writes notes mid-task. A forked background agent runs extraction after each finished turn. A second fork ("dream") consolidates notes after enough time and
sessions have passed. Both forks are limited by code to writing the memory folder, and the main agent always needs approval to edit settings, skills, subagents, or
commands. That matches harness² on two points: a separate session edits a declared surface, and the gates sit outside that surface. Evidence comes from user corrections
and confirmations in the transcript, with no grader and no execution contrast. Carried notes load into every later session of the repo without being probed again. That is
the opposite of harness² copy-then-reprobe reuse. The source also contains a skill-improvement loop (it spots corrections during a skill run, a human approves, and a
small model rewrites the skill file). It is internal-only and behind flags, so it does not count as shipped.

### Dimensions

- **who_edits**: Four editors. (1) The main agent mid-task: its system prompt says "You should build up this memory system over time". (2) A forked background agent after
  each turn (extractMemories) that "shares the parent's prompt cache". It runs on the main thread only and is skipped when the main agent already wrote memory that turn.
  (3) A forked background agent across sessions (autoDream, the "dream" consolidation pass). (4) A human through /memory and /init, or by copying /insights suggestions.
  Internal-only: a skill-improvement detector plus rewriter, and the /skillify and /remember skills.
  - Anchors: memdir/memdir.ts:241 buildMemoryLines; services/extractMemories/extractMemories.ts:1-14 header, :348 hasMemoryWritesSince skip, :532 agentId guard;
    query/stopHooks.ts:141-156 (EXTRACT_MEMORIES and executeAutoDream, main thread only); services/autoDream/autoDream.ts:2-11 header, :224 runForkedAgent;
    utils/hooks/skillImprovement.ts:68-182, :188 applySkillImprovement; screens/REPL.tsx:4900 "external" === 'ant' guard on SkillImprovementSurvey;
    skills/bundled/skillify.ts:159 and remember.ts:5 USER_TYPE !== 'ant' return; docs memory#auto-memory: "Claude reads and writes memory files during your session"
- **trigger**: Extraction runs when a query loop ends with a final response that has no tool calls. It runs every N eligible turns (remote default 1) and is coalesced
  while a run is in flight. Dream checks on every turn end and fires only when 24h have passed since the last consolidation, at least 5 other sessions were touched, and
  the lock is free (defaults, remotely tunable). The main agent writes whenever it judges a note useful, or right away when the user says "remember". Skill improvement
  (internal) checks every 5 user messages while a project skill is in use. /init, /insights, /memory run only on user command. There is no per-task improver trigger.
  - Anchors: services/extractMemories/extractMemories.ts:5-6 header, :374-385 tengu_bramble_lintel throttle, :557-564 stash for trailing run;
    services/autoDream/autoDream.ts:5-8 gate order, :63-66 DEFAULTS minHours 24 minSessions 5; services/extractMemories/prompts.ts:87 explicit remember;
    utils/hooks/skillImprovement.ts:31 TURN_BATCH_SIZE = 5, :84-88
- **edit_surface**: For the background forks the surface is fixed in code: Read/Grep/Glob, read-only Bash, and Edit/Write only on paths inside the auto-memory folder.
  Everything else is denied ("MCP, Agent, write-capable Bash ... will be denied"). It covers memory notes only: a MEMORY.md index plus topic files. These load into the
  system prompt, so they act as prompt text, but they are not the prompt, skills, hooks, or settings. The main agent has no declared surface. It can edit any file its
  permissions allow. Edits to settings.json, settings.local.json, .claude/skills, .claude/agents, and .claude/commands always ask. Subagents with a memory field get their
  own per-agent-type memory folder. Internal skill improvement edits only the project skill file that was in use.
  - Anchors: services/extractMemories/extractMemories.ts:171-222 createAutoMemCanUseTool; services/extractMemories/prompts.ts:37; utils/permissions/filesystem.ts:224-241
    isClaudeConfigFilePath ("Always ask when Claude Code tries to edit its own config files"); utils/permissions/filesystem.ts:1565-1580 auto-memory write carve-out;
    tools/AgentTool/agentMemory.ts:52-64 getAgentMemoryDir; utils/hooks/skillImprovement.ts:58-66 findProjectSkill, :198
- **evidence**: The input is the conversation transcript, read for user signals. The feedback memory type says: "Record from failure AND success". It is saved when the
  user corrects the agent or "confirms a non-obvious approach worked". The extraction fork "MUST only use content from the last ~N messages" and is told not to verify
  ("no grepping source files ... no git commands"). Dream also greps old transcripts and checks notes against the current codebase. The memory rules exclude "Debugging
  solutions or fix recipes", so execution-derived recipes are deliberately not stored. Memory and skill edits use no grader, score, tests, reference run, or probe.
  /insights does use an LLM to label each past session (outcome fully_achieved..not_achieved, satisfaction, friction), but only to fill a report that suggests CLAUDE.md
  lines, skills, and hooks.
  - Anchors: memdir/memoryTypes.ts:58-61 feedback type; memdir/memoryTypes.ts:185-194 WHAT_NOT_TO_SAVE_SECTION; services/extractMemories/prompts.ts:41;
    services/autoDream/consolidationPrompt.ts:33-42 Phase 2; utils/hooks/skillImprovement.ts:103-124 classifier prompt (corrections, preferences);
    commands/insights.ts:430-455 FACET_EXTRACTION_PROMPT, :1016 outcome enum, :1393-1432 suggestions incl. claude_md_additions
- **validation**: No structural check covers memory writes at write time. At load, MEMORY.md is cut to its line and byte caps and a WARNING line is appended. An Edit to a
  Claude settings file is schema-checked before and after, and the edit is rejected only when a valid file would become invalid. Skill files are parsed at load, and
  invalid hooks in skill frontmatter are dropped with a debug log. The internal skill rewrite checks only that the model's answer contains an <updated_file> tag; the
  frontmatter is never checked. A user-written ConfigChange hook can veto a settings or skill change before it applies, but no hook ships by default. The edit-permission
  prompt is the main pre-effect check.
  - Anchors: memdir/memdir.ts:56-100 truncateEntrypointContent; utils/settings/validateEditTool.ts:14-45 validateInputForSettingsFileEdit;
    tools/FileEditTool/FileEditTool.ts:346 (only caller; not in FileWriteTool); skills/loadSkillsDir.ts:136-152 parseHooksFromFrontmatter;
    utils/hooks/skillImprovement.ts:254-260; utils/settings/changeDetector.ts:290-301, utils/skills/skillChangeDetector.ts:262-273; docs
    hooks#configchange-decision-control
- **invalid_edit** (inferred): Invalid settings Edit: the tool returns an error and the full schema to the model, and the file stays unchanged. The model may try again
  inside the normal loop, with no fixed retry cap. Skill rewrite with no tag: the error is logged, nothing is written, no retry. Extraction error: the cursor does not
  advance, so the next turn looks at the same messages again. Dream failure or kill: only the lock timestamp is rewound, so the dream fires again later. Notes already
  written are not reverted. Change vetoed by a ConfigChange hook: the running session keeps its old config, but the file on disk is already changed. Nothing matches the
  harness² rule of one retry and then keeping the current config.
  - Anchors: utils/settings/validateEditTool.ts:36-41 errorCode 10; utils/hooks/skillImprovement.ts:255-260; services/extractMemories/extractMemories.ts:429-436,
    :497-503; services/autoDream/autoDream.ts:258-271, consolidationLock.ts:91-108 rollbackConsolidationLock; utils/settings/changeDetector.ts:290-301
- **scope**: All carried changes persist. Auto memory is per repository, shared across worktrees, and machine-local. It is kept out of the transcript cleanup sweep.
  Subagent memory persists per agent type at user, project (checked into git), or local scope. CLAUDE.md and skills are edited in place and persist. Session memory is per
  session and is not configuration. Nothing is scoped per task.
  - Anchors: docs memory#storage-location; tools/AgentTool/agentMemory.ts:12-13 AgentMemoryScope; docs sub-agents#enable-persistent-memory
- **cross_task_store**: Store: a folder per repo, ~/.claude/projects/<project>/memory/. It holds MEMORY.md, a one-line-per-entry index, plus one topic file per memory,
  typed user, feedback, project, or reference. The index is keyed by repository, not by domain. It stores notes only, never an adopted configuration. Lessons are not
  gated on execution contrasts. Each subagent with memory gets its own folder per agent type. Behind a feature flag, a project snapshot folder can seed user-scope agent
  memory.
  - Anchors: memdir/paths.ts:212-214 path resolution; memdir/memdir.ts:34-38 ENTRYPOINT_NAME, caps; services/extractMemories/prompts.ts:68-81 two-step save;
    tools/AgentTool/agentMemorySnapshot.ts:30-33, :97-142; tools/AgentTool/loadAgentsDir.ts:262-292, :348 feature('AGENT_MEMORY_SNAPSHOT')
- **reuse**: Carried changes load on their own. The MEMORY.md index goes into every session's system prompt, truncated to its caps. A model picks up to 5 topic files per
  query, or the agent reads them on demand. Subagent memory goes into that subagent's prompt. CLAUDE.md loads at launch. Skill edits hot-reload mid-session. Nothing is
  probed again before reuse. At recall time the prompt asks the model to check a note against current files and to trust what it sees now. The only copy-based reuse is
  the flagged snapshot path, which copies snapshot files into agent memory on first run, with no probe. The opposite of harness² start-from-base-and-reprobe.
  - Anchors: memdir/findRelevantMemories.ts:20 (up to 5); memdir/memoryTypes.ts:201-202 MEMORY_DRIFT_CAVEAT; tools/AgentTool/loadAgentsDir.ts:482-484
    loadAgentMemoryPrompt; docs skills#edit-a-skill-during-a-session; tools/AgentTool/agentMemorySnapshot.ts:57-77 copySnapshotToLocal
- **gates_outside**: Strong. The background forks' tool gate is fixed in code and cannot be widened from inside: no write Bash, no MCP, no Agent, and no writes outside
  the memory folder. Edits by the main agent to settings, skills, subagents, and commands always ask, and this holds even under bypassPermissions or a PreToolUse allow.
  In auto mode they go to the classifier. In v2.1.88 source, project settings cannot redirect the memory folder. Managed policy can force allowManagedHooksOnly or
  disableAllHooks, and non-managed settings cannot disable managed hooks. ConfigChange hooks can never block policy changes. Root CLAUDE.md is not on the protected list,
  so it falls under normal edit permission (inferred from list contents).
  - Anchors: services/extractMemories/extractMemories.ts:171-222; utils/permissions/permissions.ts:1144-1152 ("bypass-immune"), :526-537;
    utils/permissions/filesystem.ts:1302-1336 safety check before allow rules; memdir/paths.ts:174-185 getAutoMemPathSetting excludes projectSettings;
    utils/hooks/hooksConfigSnapshot.ts:16-46 getHooksFromAllowedSources; utils/hooks.ts:4232-4236
- **rollback** (inferred): No versioning or kept-current fallback. Users edit or delete notes through /memory, since the notes are plain markdown. Project-scope files
  (CLAUDE.md, .claude/skills, project agent memory) can be reverted with git. /rewind checkpoints cover Edit and Write calls made by the main agent in the session. Forked
  writers pass a no-op file-history callback, so their memory edits are likely not rewindable. The internal skill rewrite uses a raw file write, also outside checkpoints.
  Current docs confirm that subagent edits are generally not restored. The dream rollback rewinds only the scheduling lock, not the notes.
  - Anchors: docs memory#audit-and-edit-your-memory; utils/forkedAgent.ts:432 updateFileHistoryState: () => {}; tools/FileEditTool/FileEditTool.ts:431-438
    fileHistoryTrackEdit; utils/hooks/skillImprovement.ts:263 fs.writeFile; docs checkpointing#subagent-edits-not-restored; services/autoDream/consolidationLock.ts:91-108

### Draft table cells (unlocked; the PRD ticket locks wording)

| | Claude Code |
| --- | --- |
| **Pros** | A note helps from the next session on, with no extra run. Background writers can only touch the memory folder. |
| **Cons** | No change is tested before later sessions load it. A wrong note stays until someone fixes it. |
| **Why** | Assumes the user's corrections and confirmations are the evidence worth keeping, so notes record them as they happen. |
| **How: who edits** | The working agent mid-task, plus background forks after each turn and across sessions. Skill and CLAUDE.md changes go through a human. |
| **How: evidence** | User corrections and confirmations in the transcript. No reference run, no probe, no score. |
| **How: edit surface** | Background writers may write only memory notes. Edits to settings, skills, and subagents always need approval. |
| **How: carry over** | Notes load into every later session of that repo. No probe runs before reuse. |
| **How: rollback** | No versions. A person edits or deletes notes, and git covers project files. |

### Consistency with sections 7, 9, 21

Section 9 agrees: a "forked agent writes new memory files at run end", and consolidation is "gated by time, session count, and a lock". Two nuances to add. First, both
forks sit behind remote feature flags (tengu_passport_quail default false; tengu_onyx_plover or the autoDreamEnabled setting), and extraction also needs a build flag.
Second, the dream pass appears nowhere in public docs. Section 7 makes no skill-evolution claim for Claude Code, which fits the public build. The v2.1.88 source does hold
an internal skill-improvement detector and rewriter, but it is internal-only (REPL guard, remote flag default false). The skillify and remember skills are internal-only
too. Section 21 says "No closed improvement loop in source". That still holds: the internal skill path proposes and applies changes but never checks whether they helped.
The section 21 row "Resumable workflows replay from cache" covers a different mechanism. Version drift to watch: current docs (v2.1.2xx+) describe features missing at the
pin. They include a write-time MEMORY.md over-limit error, autoMemoryDirectory being readable from project settings under a trust rule (v2.1.88 source excludes project
settings), /verify recording its own recipe and editing it "only when it steered a run wrong" (the closest execution-evidence edit, but post-pin), /run-skill-generator,
and /doctor prompt-audit (v2.1.283+). Do not put these in a v2.1.88 column.

### Changed after verification

- Downgraded skill improvement from 'closest shipped match' to 'internal-only, not shipped'. The survey UI renders only under the internal-build guard (REPL.tsx:4900),
  and init needs feature('SKILL_IMPROVEMENT') plus a remote flag that defaults to false.
- Marked extraction as 'enabled state unverified'. It needs build flag EXTRACT_MEMORIES and remote flag tengu_passport_quail (default false). The docs only say Claude
  reads and writes memory during the session.
- Dropped /verify recipe recording (documented edits happen only after a failed run) from the cells. It is post-pin. At v2.1.88 verify is internal-only and its SKILL.md
  is missing from the backup.
- Dropped the MEMORY.md write-time limit error from validation. No write-time check exists in v2.1.88; only load-time truncation with a warning.
- Reported autoMemoryDirectory from source (project settings excluded), not from current docs (any scope plus a trust rule), and flagged the drift.
- Dropped the dream 'rollback' as a config rollback. It rewinds only the lock timestamp, and notes written before a failure stay.
- Dropped /doctor prompt-audit (v2.1.283+, post-pin).
- Ruled out /init as evidence-based. It surveys the codebase and interviews the user, never execution. On an existing CLAUDE.md it only suggests improvements.
- Ruled out MagicDocs. It is internal-only and edits marked docs, not agent configuration.
- Kept the rollback dimension marked inferred. Forked-edit checkpoint exclusion comes from a no-op callback, not from a test.
- Kept the claim that the main agent's .claude config edits always ask. Verified it is bypass-immune even under bypassPermissions and a PreToolUse allow. In auto mode the
  classifier can approve, so the cell says 'need approval', not 'ask a human'.

### Not verified

- That the backup is exactly v2.1.88. The version is injected at build time (MACRO.VERSION), so this relies on the repo's claim.
- Whether EXTRACT_MEMORIES and tengu_passport_quail were on for public v2.1.88 users.
- Whether autoDream was on by default for public users (tengu_onyx_plover). It is not mentioned in any docs page.
- Whether the Write tool validates settings.json. Only the Edit tool calls the settings validator.
- Whether memory edits by forked agents can be undone with /rewind. Inferred from the no-op file-history callback.
- The bodies of files missing from the backup: verify/SKILL.md, runSkillGenerator, dream.ts (KAIROS dream skill).
- Team memory sync (TEAMMEM) behavior and its cross-machine store.
- Auto-mode classifier behavior on .claude config edits.
- Whether the SKILL_IMPROVEMENT build flag shipped later to external users. Current skills docs do not mention it.

---

## Hermes Agent · verdict: partial

**Ref read.**

Tag v2026.7.1 found and cloned (--depth 1), commit 7c1a029553d87c43ecff8a3821336bc95872213b (2026-07-01). Docs read from the in-repo docs source at the same tag:
website/docs/user-guide/features/curator.md, skills.md ("Agent-Managed Skills (skill_manage tool)"), memory.md; served at
https://hermes-agent.nousresearch.com/docs/user-guide/features/curator (not fetched live). Repo sections read: 07-skills, 09-memory, 21-loop-engineering (tables and
Sources).

**Summary.**

Closest match: the background self-improvement review (a forked agent that replays a finished turn's transcript and edits skills and MEMORY.md/USER.md) plus the curator
(periodic deterministic prune, with an opt-in forked LLM consolidation pass). Like harness², a separate editor changes configuration files from execution evidence with no
grader or score, edits are checked structurally, protected surfaces and budgets sit outside the editor's reach, and a curator run can be rolled back. Unlike harness²,
there is no reference run, no candidate probing, no compose-or-keep decision, and no fixed one-retry rule. Carried changes go into one global per-profile store and are
auto-loaded into every later session, not copied into a new proposal and re-probed. Verdict partial: the shape of the editor and its gates matches, the evidence loop and
reuse model do not.

### Dimensions

- **who_edits**: Four writers. (1) The running agent mid-task via skill_manage and memory; the tool schema says "If you used a skill and hit issues not covered by it,
  patch it immediately." (2) Background review: a forked AIAgent in a daemon thread after a turn, replaying the transcript, max_iterations=16, runtime tool whitelist of
  memory+skills only, session-DB persistence disabled. It shares the parent session_id for cache reasons, so it is a fork, not a separate stored session. (3) Curator:
  deterministic prune with no LLM, plus an opt-in forked AIAgent consolidation pass (max_iterations=9999). (4) Human: approves staged writes, pins, rolls back, restores,
  edits/deletes via journey.
  - Anchors: tools/skill_manager_tool.py SKILL_MANAGE_SCHEMA L1408-1440; agent/background_review.py _run_review_in_thread L572, AIAgent(max_iterations=16) L648-662,
    _persist_disabled L688, set_thread_tool_whitelist L756-769; agent/curator.py run_curator_review L1480, _run_llm_review L1809 (max_iterations=9999 L1889);
    agent/learning_mutations.py module docstring (hermes journey delete|edit); tools/write_approval.py module docstring
- **trigger**: Review: at turn end, only if there is a final response and no interrupt, when tool-calling iterations since the last skill_manage call reach
  skills.creation_nudge_interval (default 10) or user turns since the last memory call reach memory.nudge_interval (default 10). Counters reset whenever the live agent
  itself calls skill_manage or memory. Curator: checked on CLI session start and on a gateway tick; runs when interval_hours (default 168) elapsed and idle >=
  min_idle_hours (default 2). First run is deferred one full interval. Manual: `hermes curator run [--dry-run] [--consolidate]`.
  - Anchors: agent/turn_finalizer.py L455-479; agent/conversation_loop.py L690-692; agent/turn_context.py L295-301; agent/tool_executor.py L341-344; agent/agent_init.py
    L1208-1216, L1301-1304; agent/curator.py DEFAULT_* L56-64, should_run_now L219-269, maybe_run_curator L1958; cli.py L12955, gateway/run.py L19002
- **edit_surface** (inferred): Review fork: MEMORY.md/USER.md (memory toolset, only if enabled) and skills: SKILL.md plus support files under references/, templates/,
  scripts/, assets/. Enforced by a runtime thread tool whitelist ("Only memory/skill tools are allowed") and by a write guard that refuses pinned, bundled, hub-installed,
  external-dir, and protected built-in skills; it must skill_view a file before writing it. Declared in code, not per run. Outside: system prompt/persona, config.yaml,
  tool definitions, hooks, plugins, subagent config. Curator fork: no toolset whitelist is passed, and the default is "start with everything", so it holds terminal and
  file tools; its surface (curator-managed candidate list) is stated in the prompt and enforced only inside skill_manage guards, not on terminal moves.
  - Anchors: agent/background_review.py L753-769; tools/skill_manager_tool.py _background_review_write_guard L281, _background_review_read_before_write_guard L366,
    ALLOWED_SUBDIRS L462, _validate_file_path L692; agent/curator.py CURATOR_REVIEW_PROMPT L403-554 (hard rules, toolset incl. terminal), AIAgent(...) without
    enabled_toolsets L1878-1894; model_tools.py _compute_tool_definitions else-branch L388-391
- **evidence**: Review: the finished conversation snapshot (messages incl. tool results) plus a signal list in the prompt: user corrections, "Non-trivial technique, fix,
  workaround", a loaded skill that "turned out to be wrong". The prompt pushes action: "A pass that does nothing is a missed learning opportunity". Curator: usage
  telemetry (view/use/patch counts, last_activity, state) and skill content; prompt says judge "on CONTENT, not on use_count" and "fewer than 10 archives, you stopped too
  early". No grader, no score, no tests, no reference run, no probe of a candidate. Trajectory saving is training data with only a completed flag, off by default, and
  never feeds a config edit.
  - Anchors: agent/background_review.py _SKILL_REVIEW_PROMPT L171-274, _COMBINED_REVIEW_PROMPT L276; agent/curator.py _render_candidate_list L1458, CURATOR_REVIEW_PROMPT
    L436-443, L529-532; agent/trajectory.py save_trajectory L30; agent/agent_init.py save_trajectories default False L181
- **validation**: Skills: name regex and length, single-segment category, YAML frontmatter with name and description (<=1024 chars), non-empty body, 100k char cap, 1 MiB
  support file cap, path allowlist with traversal check, patch must match and must keep frontmatter valid, absorbed_into target must exist, name-collision check. Optional
  keyword security scan (skills.guard_agent_created, default false) rolls the write back on block. Memory: threat-pattern scan, char budget (2200/1375), duplicate reject,
  drift guard on replace/remove. Optional human gate (write_approval, default false) stages writes for /skills approve or /memory approve. Nothing compiles scripts or
  runs the edited configuration.
  - Anchors: tools/skill_manager_tool.py _validate_name L469, _validate_frontmatter L508, _validate_content_size L547, _create_skill L776, _patch_skill L897-1007,
    _security_scan_skill L121; tools/memory_tool.py add L336-386 (_scan_memory_content L343); tools/write_approval.py evaluate_gate L253-303; hermes_cli/config.py
    memory.write_approval L2060, skills.guard_agent_created L2211, skills.write_approval L2223
- **invalid_edit** (inferred): The tool returns {success: false, error} to the editing agent and the file stays as it was (scan blocks restore the original content or
  remove a new skill dir). Retry is model-driven, bounded only by the fork's iteration budget; the read-before-write refusal says "then retry the write". No fixed
  one-retry rule. A curator delete without absorbed_into fails closed: "Keeping '{name}' active." If the agent gives up, the current configuration simply remains.
  - Anchors: tools/skill_manager_tool.py _edit_skill L868-876, _create_skill L813-816, _background_review_read_before_write_guard L381-392,
    _curator_consolidation_delete_guard L405-452; agent/background_review.py max_iterations=16 L650
- **scope**: Persistent across sessions and tasks, per profile (HERMES_HOME). Not per task and not per domain. Memory writes hit disk immediately but reach the system
  prompt only on the next session start or a compression rebuild.
  - Anchors: tools/memory_tool.py module docstring L1-23; agent/system_prompt.py invalidate_system_prompt L496-503; agent/conversation_compression.py L651;
    tools/memory_tool.py get_memory_dir L55
- **cross_task_store**: One store per profile: ~/.hermes/skills/ (category folders, SKILL.md plus support files), .usage.json sidecar (counts, state
  active/stale/archived, pinned, agent-created flag), .archive/, and ~/.hermes/memories/MEMORY.md and USER.md (entries split by a section sign). Indexed by skill name,
  description, and category folder. No per-domain library, and no lesson tied to an execution contrast.
  - Anchors: tools/skill_manager_tool.py module docstring directory layout; tools/skill_usage.py _usage_file L85, STATE_* L53-55, archive_skill L696, mark_agent_created
    L646; tools/memory_tool.py ENTRY_DELIMITER
- **reuse**: Auto-loaded, not copied. The skill catalog (name plus description) is built into the system prompt; skill_manage clears the skills prompt cache so the next
  prompt build sees the change; bodies load on demand via skill_view from disk. Memory enters as a frozen snapshot at session start. A later task never starts from a base
  configuration and never re-probes a carried change.
  - Anchors: tools/skill_manager_tool.py clear_skills_system_prompt_cache L1375; agent/prompt_builder.py clear_skills_system_prompt_cache L1265; tools/memory_tool.py
    format_for_system_prompt L615; tools/skills_tool.py skill_view bump_view/bump_use L1644-1649
- **gates_outside** (inferred): Review fork cannot reach: config.yaml (curator.*, write_approval, nudge intervals, backup), permissions, budgets (its own max_iterations
  is set in code), hooks, plugins, Hermes code. It also has dangerous commands auto-denied, recursive nudges disabled, no session-DB writes, and
  protected/pinned/hub/bundled/external skills refused. Curator fork: same skill guards via the background_review origin, but it holds terminal and file tools, so config
  and code are not structurally out of reach; only prompt rules keep it off them.
  - Anchors: agent/background_review.py _bg_review_auto_deny L592-601, nudge=0 L675-676, whitelist L756-769; agent/curator.py _memory_write_origin='background_review'
    L1905, nudge=0 L1896-1897; tools/skill_manager_tool.py _background_review_write_guard L281-363; model_tools.py L388-391
- **rollback** (inferred): Curator: a tar.gz snapshot of the whole skills tree (plus cron skill links, usage, archive, curator state) before every non-dry curator run,
  keep 5; `hermes curator rollback [--id] [--list]` restores and first takes a safety snapshot so the rollback is undoable. Curator deletes become archives; `hermes
  curator restore <skill>`. Manual `hermes curator backup`. Review-fork edits get no per-edit snapshot; they are covered only by a later curator pre-run snapshot or a
  manual backup. Memory has no version history; undo means manual remove/edit or journey delete. With write_approval on, a human can reject staged writes before they
  land.
  - Anchors: agent/curator_backup.py module docstring L1-37, snapshot_skills L211, rollback L539; agent/curator.py run_curator_review snapshot L1535-1544;
    hermes_cli/curator.py _cmd_restore L275, _cmd_rollback L406; tools/skill_manager_tool.py _delete_skill archive path L1079-1090;
    website/docs/user-guide/features/curator.md "Backups and rollback"

### Draft table cells (unlocked; the PRD ticket locks wording)

| | Hermes Agent |
| --- | --- |
| **Pros** | Learns from busy sessions with no setup. Writes are checked for shape, and protected skills are refused. A curator run can be undone in one step. |
| **Cons** | Nothing runs an edit before it lands. A wrong lesson loads in every later session. Review edits between curator runs have no snapshot. |
| **Why** | Assumes the model can judge a lesson from the transcript alone, so no grader or probe run is needed. |
| **How: who edits** | A background copy of the agent after busy turns, plus a curator about once a week. The live agent can edit too. |
| **How: evidence** | The finished transcript and skill usage counts. No grader, no score, no probe run. |
| **How: checks** | Format, size, and path checks. Protected skills are refused. Human approval is optional and off by default. |
| **How: carry over** | Edits go into one shared skill and memory store. Every later session loads them automatically. |
| **How: undo** | A snapshot before each curator run, restored with one command. Archived skills can be brought back. |

### Consistency with sections 7, 9, 21

Agrees: §7 Cons (pins, staged approvals), §7 Why (store grows and decays), §7 load trigger (skill_view bumps view/use). §9 extraction cell (memory tool writes
mid-session, writes can be staged). §21 "Budgets, plus an improvement loop with rollback" and "No built-in grade-and-retry loop" (no grader exists). Conflicts and
nuances: (1) §7 Pros "A curator merges new skills" and §7 body "background curator agent ... (archive, consolidate, pin)": at v2026.7.1 consolidation is opt-in
(curator.consolidate default false). The default run is a deterministic prune with no agent, and pin is a human CLI action. (2) §9 Cons "New writes only reach the prompt
next session": a compression rebuild also reloads memory from disk mid-session. (3) §21 "How: improvement loop: Runs become training data" points at trajectory saving
(off by default), not the shipped config-editing loop. The background skill/memory review is the closer improvement loop. §21 rollback covers the skills tree at curator
runs only, not memory or review-fork edits. (4) Hermes docs drift from code: curator.md says the consolidation fork uses max_iterations=8 (code: 9999), and says pinned
skills are off-limits to the agent's skill_manage (code: foreground blocks only delete; background blocks all writes).

### Changed after verification

- Curator 'merges skills': changed to prune-only by default; LLM consolidation is opt-in (DEFAULT_CONSOLIDATE=False, agent/curator.py L64).
- 'Separate background session': changed to a forked agent sharing the parent session_id with DB persistence disabled (background_review.py L688, L723).
- 'Edit surface declared up front': only the review fork has an enforced runtime whitelist; the curator fork gets all toolsets by default (model_tools.py L388-391), so
  its surface is prompt-declared. Marked inferred.
- Used code over docs for curator max_iterations (9999, not 8) and for pin semantics (foreground blocks delete only).
- §9 'next session only' refined: compression rebuild also reloads memory (system_prompt.py L496-503).
- invalid_edit: no fixed one-retry rule; retries are model-driven within the iteration budget. Marked inferred.
- Dropped trajectory saving as an adaptation match: it is training data, off by default, and never edits configuration.
- Dropped the skills hub: a human install path with scanning, not an improver.
- Dropped optional darwinian-evolver and dspy skills: optional user-loaded skills, not a shipped harness mechanism.
- Rollback for review-fork edits marked inferred: no per-edit snapshot exists in code; coverage depends on a later curator or manual snapshot.

### Not verified

- Whether terminal commands in the curator fork go through dangerous-command approval (no approval callback is set in the curator thread, unlike the review fork).
- Whether an approved staged skill write, replayed later, re-applies the background read-before-write and pinned guards (the origin at replay time looks foreground).
- Whether the parent session's skill catalog refreshes mid-session after a review edit. The parent keeps its cached system prompt, so this is inferred to happen at the
  next session or a compression rebuild.
- Gateway tick frequency for the curator check.
- Hosted docs pages were not fetched live; the in-repo docs source at the tag was read instead.
- No code or tests were executed.

---

## mini-swe-agent · verdict: none

**Ref read.**

Tag v2.4.5, commit e187bcb2ff5825d85761a6f9c1f98c9fa6cfbc79 (shallow clone into scratchpad/clones/mini-swe-agent/src, read only, nothing run). Code read:
src/minisweagent/agents/default.py, agents/interactive.py, agents/__init__.py, config/__init__.py, config/{mini,default,benchmarks/swebench}.yaml (keys), run/mini.py,
run/benchmarks/{swebench,swebench_single,programbench}.py, run/benchmarks/utils/{common,batch_progress}.py, run/utilities/{config,mini_extra,inspector}.py,
models/__init__.py, models/extra/roulette.py, environments/local.py, utils/serialize.py, pyproject.toml, AGENTS.md. Docs read from the clone's docs/ folder (source of
mini-swe-agent.com, no live fetch): docs/faq.md, docs/usage/swebench.md (FAQ), docs/usage/output_files.md, docs/advanced/yaml_configuration.md,
docs/advanced/global_configuration.md, docs/advanced/cookbook.md. Repo columns read: sections/21-loop-engineering/README.md table and Sources; sections/07-skills and
09-memory have no mini-swe-agent column.

**Summary.**

mini-swe-agent ships no harness adaptation. Config is a YAML file (prompt templates, limits, model and environment settings) that a person writes; it is read once per
process, merged left to right with CLI overrides, and never written by any code path. No code reads a past trajectory or result back into a later run's prompt or config;
saved results serve only resume (skip finished SWE-bench instances) and offline grading. The only mid-run config change is a human raising step and cost limits at the
LimitsExceeded boundary, or switching confirm mode, in memory only. Validation is incidental (pydantic typed fields, Jinja StrictUndefined), with no probe run, retry, or
fallback. Budgets are checked in harness code before each model call, which the agent's shell cannot reach, but no edit surface is declared because no automated editor
exists.

### Dimensions

- **who_edits**: A human only. Before a run: edits a YAML config, passes several -c specs or key=value overrides, merged later-wins. Global .env (model name, keys, global
  limits) edited via `mini-extra config set/unset/edit`. Mid-run (InteractiveAgent only): human types new step_limit and cost_limit after LimitsExceeded when stdin is a
  TTY, and switches mode with /u /c /y. No agent, background session, or improver edits config.
  - Anchors: src/minisweagent/run/mini.py main() L70-92: `configs = [get_config_from_spec(spec) for spec in config_spec]` then recursive_merge;
    src/minisweagent/utils/serialize.py recursive_merge L6-29: 'Later dictionaries take precedence'; src/minisweagent/agents/interactive.py InteractiveAgent.query()
    L80-94: `self.config.step_limit = int(input("New step limit: "))`; src/minisweagent/agents/interactive.py _prompt_and_handle_slash_commands L201-208 (mode switch);
    src/minisweagent/run/utilities/config.py set/unset/edit L99-127
- **trigger**: Config is read once at process start (CLI main). Mid-run change only at the LimitsExceeded boundary (human prompt) or a human slash command. Wall time
  cannot be lifted: TimeExceeded is re-raised. No automatic trigger, no between-task trigger in batch mode.
  - Anchors: src/minisweagent/run/benchmarks/swebench.py main() L235-241 (config built once before the thread pool); src/minisweagent/agents/interactive.py query()
    L75-79: 'A wall-clock limit can't be lifted by raising the step/cost limits'
- **edit_surface** (inferred): No declared edit surface. The whole YAML is open to the human: agent (system_template, instance_template, step_limit, cost_limit, mode,
  whitelist_actions), model (observation_template, format_error_template, model_kwargs, model_name), environment (cwd, timeout, env, environment_class), run
  (env_startup_command). Prompt and budgets live in the same file. Config classes are plain pydantic BaseModel with no extra=forbid, so unknown keys are silently ignored
  (pydantic v2 default).
  - Anchors: src/minisweagent/config/benchmarks/swebench.yaml top-level keys agent/environment/model; docs/advanced/yaml_configuration.md 'Overall structure': agent,
    environment, model, run; src/minisweagent/agents/default.py AgentConfig L19-35; grep: no model_config/ConfigDict/forbid anywhere in src; pyproject.toml 'pydantic >=
    2.0'
- **evidence**: None inside the harness: no grader, score, or tests in the loop. Each run saves a .traj.json with full messages plus the config used, cost, exit_status,
  submission. SWE-bench batch also writes preds.json and exit_statuses_<time>.yaml. Grading is external (sb-cli or SWE-bench harness). A human can browse trajectories in
  the inspector TUI. Nothing turns this into an edit.
  - Anchors: src/minisweagent/agents/default.py serialize() L157-178: 'config': {'agent': self.config.model_dump(mode="json")}; docs/usage/output_files.md 'Trajectory
    files contain the full history of an agent run, including all messages, configuration, and metadata'; docs/usage/swebench.md 'Evaluating on SWE-bench' (sb-cli,
    swebench.harness.run_evaluation); src/minisweagent/run/benchmarks/utils/batch_progress.py _save_overview_data_yaml L175-178
- **validation**: Incidental only, for human-written config: get_config_path raises FileNotFoundError; pydantic type-checks known fields at construction; Jinja
  StrictUndefined raises on undefined template variables at render. System and instance templates render at run start; observation and format-error templates render at
  first use mid-run. No structural check against a declared surface, no probe run, no reference run.
  - Anchors: src/minisweagent/config/__init__.py get_config_path L12-28; src/minisweagent/agents/default.py _render_template L66-67 (StrictUndefined), run() L92-95;
    src/minisweagent/models/utils/actions_text.py L30, L53 (format_error_template, observation_template rendered with StrictUndefined)
- **invalid_edit** (inferred): No retry, no fallback to a previous config. Missing config file aborts the CLI before any task. In SWE-bench batch: bad agent section or
  template is caught per instance and recorded as exit_status = exception type with empty submission; a bad model section raises before the per-instance try, escapes as
  an uncaught future error, logged as 'Uncaught <Type>'. Batch continues to the next task with the same bad config. In single runs the exception propagates after an exit
  message is saved.
  - Anchors: src/minisweagent/run/benchmarks/swebench.py process_instance L134 (get_model before try), L145-160 (except: `exit_status, result = type(e).__name__, ""`);
    src/minisweagent/run/benchmarks/swebench.py process_futures L245-254 -> on_uncaught_exception; src/minisweagent/run/benchmarks/utils/batch_progress.py
    on_uncaught_exception L158-159; src/minisweagent/agents/default.py run() L115-117 handle_uncaught_exception then raise
- **scope** (inferred): Per process invocation. One merged config is shared read-only by every instance of a batch (ProgramBench deep-copies per instance). Interactive
  limit changes mutate the in-memory config only and die with the process; because serialize() dumps self.config at save time, the raised limits appear in that run's
  trajectory.
  - Anchors: src/minisweagent/run/benchmarks/swebench.py L236-241, L259 (same config passed to every process_instance); src/minisweagent/run/benchmarks/programbench.py
    process_instance L77 `inst_config = copy.deepcopy(config)`; src/minisweagent/agents/default.py serialize() L168
- **cross_task_store**: None for config changes. What persists: per-instance <id>/<id>.traj.json, preds.json keyed by instance_id, exit_statuses yaml, global .env, and
  for `mini` a single last_mini_run.traj.json overwritten each run. None is indexed for reuse, none is a library of configurations or lessons.
  - Anchors: src/minisweagent/run/benchmarks/swebench.py update_preds_file L97-108; src/minisweagent/run/mini.py DEFAULT_OUTPUT_FILE L23 `global_config_dir /
    "last_mini_run.traj.json"`; src/minisweagent/agents/default.py save() L180-188 `path.write_text(...)`
- **reuse**: No automatic reuse. A later task loads the same YAML from disk. preds.json is read only to skip finished instances unless --redo-existing; a redo first
  deletes the old pred and trajectory. No code reads a past trajectory into a run (only the inspector reads trajectories). A human reuses a config by passing its path
  with -c or setting MSWEA_MINI_CONFIG_PATH.
  - Anchors: src/minisweagent/run/benchmarks/swebench.py main() L229-232 'Skipping {len(existing_instances)} existing instances';
    src/minisweagent/run/benchmarks/swebench.py process_instance L131-133 (remove_from_preds_file, unlink traj); docs/usage/swebench.md FAQ 'The completed instances are
    inferred from `preds.json`'; grep of read_text/json.load in src: only config, preds.json, model registry, inspector; src/minisweagent/run/mini.py L22
    MSWEA_MINI_CONFIG_PATH
- **gates_outside** (inferred): Per-run budgets (step_limit, cost_limit, wall_time_limit_seconds) are checked in harness Python before each model call; process-wide
  MSWEA_GLOBAL_COST_LIMIT / MSWEA_GLOBAL_CALL_LIMIT checked on every model query; confirm mode and whitelist in InteractiveAgent; docker per SWE-bench instance. The agent
  acts only through bash, so it cannot change in-memory budgets. But budgets sit in the same human-edited YAML as the prompt, and in local `mini` mode bash runs on the
  host, so nothing structural stops a command from writing a config file; it would only take effect on a later run since config is read once (default confirm mode asks
  the human first).
  - Anchors: src/minisweagent/agents/default.py query() L128-145 (LimitsExceeded, TimeExceeded); src/minisweagent/models/__init__.py GlobalModelStats.add L25-31;
    models/litellm_model.py query() L86; src/minisweagent/environments/local.py LocalEnvironment.execute + _run (subprocess on host, cwd default os.getcwd());
    src/minisweagent/config/mini.yaml `mode: confirm`
- **rollback** (inferred): None built in. Config files are plain files, so a human reverts by hand or version control. Each trajectory embeds the config it ran with, so a
  previous config can be recovered from the log. Batch reruns use --redo-existing, which clears that instance's prior pred and trajectory first.
  - Anchors: src/minisweagent/agents/default.py serialize() L167-170; src/minisweagent/run/benchmarks/swebench.py L131-133, L212

### Draft table cells (unlocked; the PRD ticket locks wording)

| | mini-swe-agent |
| --- | --- |
| **Pros** | Nothing drifts. Every task runs the same config file, which a person can read in full. |
| **Cons** | Nothing adapts. Lessons stay in saved run logs until a person edits the file by hand. |
| **Why** | The agent stays minimal and fixed. A run is one graded task, and tuning is left to people. |
| **How: who edits** | A person, before the run. Mid-run, a person can only raise step and cost limits or switch confirm mode. |
| **How: evidence** | None inside the harness. Each run saves its full log and the config it used. Benchmarks grade results offline. |
| **How: checks** | Typed fields and strict templates fail a bad file when first used. No probe run, no retry, no fallback. |
| **How: carry over** | None. Every task loads the same file. Saved results only let a batch skip finished tasks. |
| **How: gates** | Budgets are checked in harness code before each model call. The agent acts only through shell commands. |

### Consistency with sections 7, 9, 21

Agrees with section 21: Why 'One run is one graded task', verification 'None. SWE-bench grades offline.', improvement loop 'None. Budgets only.', Cons 'Only the budget
half'. Section 21 Sources anchor `AgentConfig` and `query()` in agents/default.py, agents/interactive.py, run/benchmarks/swebench.py; same anchors used here. Sections 7
and 9 have no mini-swe-agent column, matching the stated deliberate absences (skills, memory); this finding adds no skill or memory store. No conflict found. Proposed
'evidence' cell reuses section 21's offline-grading phrasing.

### Changed after verification

- Trajectory save timing: planned 'saved after every step'. SWE-bench batch config sets no output_path, so batch trajectories save only at task end (process_instance
  finally); mini and ProgramBench save per step. Matches docs FAQ 'Trajectories are only saved upon completion'.
- Invalid config: planned 'each task fails and is recorded'. Refined: bad model section raises before the per-task try and is logged as 'Uncaught <Type>'; bad agent
  section or template is caught and recorded as exit_status; missing config file aborts the batch before any task.
- Checks cell: planned 'fail at start'. Observation and format-error templates render mid-run, so changed to 'fail when first used'.
- Gates: planned 'agent cannot reach config'. True for in-memory budgets only; in local mode host bash could write a YAML or .env, effective next run. Marked inferred,
  not a shipped mechanism.
- Dropped roulette and interleaving models as adaptation: random or fixed-sequence model choice, no evidence used.
- Dropped cookbook ValidatingAgent as a validation match: it blocks shell commands, not config edits, and is docs example code, not shipped.
- Why cell: dropped 'keeps scores measuring the model'; FAQ 'don't want to overfit to a specific agent scaffold' is about fine-tuning, so grounded Why in AGENTS.md
  'simplest, smallest, most readable agent' and section 21's existing Why.
- Unknown-key behavior marked inferred: relies on pydantic v2 default extra='ignore', not executed.

### Not verified

- Runtime behavior: nothing executed, all from reading code at e187bcb.
- pydantic ignoring unknown config keys: library default, not tested here.
- External tooling outside the pinned repo (SWE-agent org projects, forks, papers) that tunes mini-swe-agent configs or prompts.
- Changes after v2.4.5.
- Every model class calls GLOBAL_MODEL_STATS.add: confirmed by grep for litellm, portkey, portkey_response, requesty; litellm_response import seen but call site not read;
  openrouter models not checked.
- Live docs site mini-swe-agent.com not fetched; docs read from the repo's docs/ folder at the tag.

---

## deepseek-harness · verdict: partial

**Ref read.**

Tag dsh-v0.1.0-rc.7, commit 99f6f02fecdb7dff40c3fbc9470f5907c29f74ca (2026-08-17), shallow clone read only. Read:
apps/cli/config/agent-presets/cordis/{agent.cordis.yml,preset.yml,skills/editing-cordis-compositions/SKILL.md, skills/cordis-plugin-development/SKILL.md (grep)};
packages/preset/agent-presets/README.md + src/{index.ts,discovery.ts,authoring.ts,mount.ts}; packages/client/ui-agent-preset/README.md;
packages/bundle/web-app/{cordis.patch.yml,src/index.ts}; packages/bundle/base/cordis.patch.yml; apps/cli/src/profile-boot.ts (grep); packages/boot/app-boot/src/index.ts
(addHarnessSourceSection); packages/extensions/tool-cordis/{README.md,src/index.ts,src/prompt.ts};
packages/extensions/cordis-host-runner/{README.md,src/index.ts,src/registry.ts}; docs/tool-catalog.md (tool-cordis row); vendor/hmr/src/index.ts;
docs/cordis-tutorial/06-composition-and-hmr.md; packages/workflow/tool-ralph/README.md; docs/subsystems/{skills.md,goal.md,core.md (grep)};
packages/skill/skill-filesystem/README.md; packages/context/agent-instructions/README.md; packages/sandbox/sandbox-policy/README.md; .agents/notes (implemented:
self-referential-cordis-toolset, third-party-memory-mcp-examples, web-plugin-configuration, creator-guidance-introduce-cue; proposed: human-review-skill-maintenance).
Repo-wide greps for improvement/reflection/lesson/curator/optimizer terms and for feedback consumers. Also read this repo's sections 7, 9, 21 tables and Sources, and
research notes on branches research/dsh-* (sections-05-08, 09-11, 19-23).

**Summary.**

deepseek-harness ships no evidence-driven improvement loop: nothing makes a reference run, probes a candidate, uses a score, or writes a lesson. This confirms the section
21 cell "None shipped". The closest thing it ships covers the config-authoring half of section 24. The opt-in `cordis` preset (Creator mode, 创造模式) "exists so a person can
ask an agent to author another agent". That agent copies a preset folder (plugin rows for persona, tools, compaction, delegation, plus skill folders), edits the copy, and
checks it structurally with a real mount (packages resolve, configs validate, every row activates, no process-global service leaks). After that, a person tries it in a
new session. Running sessions are frozen on the composition generation they joined, and later sessions pick a preset by id from an on-disk roster. Separately, the same
preset lets the live agent define versioned in-process plugins. Each version is immutable, and the current-version pointer moves only after a fully successful activation.
These plugins are process-local and never persisted. So the verdict is partial: some shapes match (separate session, frozen executor, structural checks, copy to reuse),
but the trigger is human and there is no execution evidence, probe, compose step, or lesson.

### Dimensions

- **who_edits**: Closest: an agent in a separate, person-driven Creator-mode session (shipped `cordis` preset) authors ANOTHER preset's files for later sessions: "It
  exists so a person can ask an agent to author another agent" and "a composition this agent writes becomes a preset other sessions mount". It is interactive, not a
  background improver. Secondary: in that same preset the running agent can define and run versioned in-process plugins (tool-cordis) that "may register tools, prompt
  contributions, or listeners" for later requests. Humans edit settings namespaces via the web Plugins page. Nothing edits configuration automatically.
  - Anchors: apps/cli/config/agent-presets/cordis/agent.cordis.yml L1-12 header comment; apps/cli/config/agent-presets/cordis/preset.yml (name: 创造模式);
    packages/client/ui-agent-preset/README.md L41-43 ("let the agent draft one in Creator mode"); packages/preset/agent-presets/README.md L135 ("authored by a person or
    by an agent"); packages/extensions/tool-cordis/README.md L90 (Later requests after cordis_run); packages/extensions/tool-cordis/src/index.ts L149 cordis_define, L241
    cordis_run
- **trigger**: A person's request in a session they started on the Creator preset. No trigger fires on run end, on failure, on a schedule, or on feedback. Goal rounds and
  the Ralph tool never touch configuration. Ralph's README defers evaluator policy.
  - Anchors: apps/cli/config/agent-presets/cordis/agent.cordis.yml L4; packages/client/ui-agent-preset/README.md L43 (dashed add-card stages the cordis preset and starts
    a new session); packages/workflow/tool-ralph/README.md L88-93 Known Limitations; packages/extensions/tool-cordis/src/prompt.ts L10-12 (use only when the user intends
    to design or create)
- **edit_surface**: One preset directory: `agent.cordis.yml` (plugin rows incl. persona text = system prompt, tool rows, compaction, delegation, skill dirs), `preset.yml`
  (display name and description only), skill folders, assets. The surface is declared in prose (persona and skill: "Off-limits", "Decide the plane first", "What not to
  move into a preset"), not as a machine-checked file list. Partial enforcement: the mount rejects rows that publish a process-global service. `remove()` refuses shipped
  presets. Writes outside the session workspace need sandbox escalation. Privilege is not capped: "a preset is exactly as privileged as the plugins it names", and trust
  "exists so consumers can present that difference, not to enforce it". Dynamic plugins are a second surface: live process services, tools, prompt sections, and browser
  UI slots.
  - Anchors: packages/preset/agent-presets/README.md L5, L53-61 Authoring, L71-82 Display metadata, L133-135 Trust;
    apps/cli/config/agent-presets/cordis/skills/editing-cordis-compositions/SKILL.md L10-14 Off-limits, L16-28, L152-154; packages/preset/agent-presets/src/mount.ts
    leakedServices L189, mountPreset L332; packages/preset/agent-presets/src/authoring.ts deleteComposition L182-195
- **evidence** (inferred): None from execution. No reference run, no candidate probe on a task, no grader, no score, no tests. The only checks are structural: a mount,
  then "ask the user to start a session on the new preset and confirm the tool list". Feedback events and per-message ratings exist, but they are only exported to
  telemetry. No consumer turns them into config edits.
  - Anchors: apps/cli/config/agent-presets/cordis/skills/editing-cordis-compositions/SKILL.md L105-122 Verifying a change;
    packages/session/session-telemetry-otel/src/index.ts L245 (only non-feedback consumer of feedback/record found); packages/workflow/tool-ralph/README.md L88
- **validation** (inferred): Three layers. (1) Discovery health: the YAML must parse in the loader's own dialect, and the file must be a list of rows that each name a
  plugin, recursing into groups. Otherwise the row carries a `broken` reason. (2) Mount validation through `standingKeyFor(id)`, which does the same mount a session start
  does. It rejects an unresolved package, an invalid row config, a row that never activated, and a service leaked into the root realm. mount.ts itself checks inactive
  rows and leaked services. The first two failures surface from the loader. (3) A human-run session. The skill warns: "Do not treat the roster's `broken` field as
  validation." For dynamic plugins, define only prechecks syntax. The current-version pointer moves only after complete activation success. Browser halves need user
  approval.
  - Anchors: packages/preset/agent-presets/src/discovery.ts entryListProblem L55, compositionProblem L86; packages/preset/agent-presets/src/index.ts resolveMountable
    L233-238, standingKeyFor ~L480; packages/preset/agent-presets/src/mount.ts mountPreset L332-381, inactiveRows L283;
    apps/cli/config/agent-presets/cordis/skills/editing-cordis-compositions/SKILL.md L107-116; packages/extensions/cordis-host-runner/src/index.ts commitActivation
    L981-983, requiresApproval L278-282
- **invalid_edit**: A broken preset stays listed with its reason. It cannot become the default, pickers drop it, and mount refuses it up front. A failed mount disposes
  its subtree and rolls back agent creation, so a session is never half composed. A failed recompose restores the previous composition. A failed standing mount is
  forgotten, so the next session retries after a fix. There is no automatic retry and no retry cap: the Creator agent is told to read the error and fix it. For dynamic
  plugins, a failed update keeps the old current-version pointer, but the old run is already stopped ("Failure does not automatically restart the old version"). The model
  is told to "retry autonomously", and no count bound was found. Compare harness², which allows one retry and then keeps the current config running.
  - Anchors: packages/client/ui-agent-preset/README.md L49; packages/preset/agent-presets/README.md L16, L31, L49; packages/preset/agent-presets/src/index.ts
    ensureStanding catch ~L526-528; packages/preset/agent-presets/src/mount.ts L367-380 (dispose then PresetMountError); packages/extensions/tool-cordis/src/prompt.ts
    L42-46, L106; packages/extensions/tool-cordis/src/index.ts L241-252 cordis_run description
- **scope**: Preset edits are persistent on disk under `$DSH_HOME/.agent-presets/<id>` and apply to sessions created afterwards. A session's preset is fixed at creation,
  except for blank-session recompose. Each standing generation records the file's mtime and size. An edited file starts a new generation for later sessions, while joined
  sessions keep theirs. Dynamic plugins are process-local and session-owned. They vanish on restart and "cannot be promoted automatically".
  - Anchors: packages/preset/agent-presets/README.md L31, L94-96; packages/preset/agent-presets/src/index.ts ensureStanding L489-528, compositionStamp L546;
    packages/client/ui-agent-preset/README.md L9; packages/extensions/cordis-host-runner/README.md L28; packages/extensions/tool-cordis/README.md L19
- **cross_task_store**: The preset roster: one directory per preset, indexed by directory id, across ranked roots. The shipped `system` root comes first, then the user
  root, and an earlier root wins a duplicate id. Discovery is unmemoized, so the roster re-reads disk on every call. Metadata is display-only (name, description). There
  is no domain index, no lesson field, and no execution record attached. Skill folders travel inside a preset. No memory store ships; memory is only default-off
  third-party MCP examples.
  - Anchors: packages/preset/agent-presets/README.md L11-27, L84-102; packages/preset/agent-presets/src/discovery.ts scanRoot L139, discoverPresets L177;
    packages/bundle/web-app/cordis.patch.yml L410-424 (roster, default standard); .agents/notes/implemented/feature/2026-07-31-third-party-memory-mcp-examples.md Decision
- **reuse**: A person picks a preset when a session starts, through the new-session chip or the default setting, and the whole composition is mounted. No model retrieves
  or selects presets. New presets are copy-only: `copy(from, id)` copies a whole directory, keeps the source description, drops its name and order, then the copy is
  edited. This resembles harness² copy-into-new-proposal, but the copy is re-checked only by a mount, never re-probed on a task.
  - Anchors: packages/preset/agent-presets/README.md L24, L53-61, L104-113; packages/preset/agent-presets/src/authoring.ts copyComposition L136-169;
    packages/client/ui-agent-preset/README.md L11-17, L37
- **gates_outside** (inferred): Sandbox, approval, and permission rows are host-plane and stated as "a deliberate boundary". The shipped sandbox default is
  `workspace-write`, so writes to the user preset root need an escalation the user sees and approves. Under `danger-full-access` set by env, that gate disappears. Shipped
  presets refuse delete, and the persona says never edit them. Browser halves of dynamic plugins need user approval. These are partly instruction-only: preset privilege
  is not capped by mechanism.
  - Anchors: apps/cli/config/agent-presets/cordis/skills/editing-cordis-compositions/SKILL.md L69, L154; packages/bundle/base/cordis.patch.yml L172-175 (mode default
    workspace-write); packages/preset/agent-presets/src/authoring.ts deleteComposition L188-190; apps/cli/config/agent-presets/cordis/agent.cordis.yml L27;
    packages/preset/agent-presets/README.md L135
- **rollback**: Running sessions keep the generation they joined, so a bad edit never reaches them. Preset files have no version history. Undo is another file edit or a
  delete, and the shipped presets remain known-good sources to copy from. Dynamic plugins keep immutable package versions, and rollback means running an earlier package.
  Dev HMR restores old module caches when a plugin re-import fails, and logs a failed config reload.
  - Anchors: packages/preset/agent-presets/README.md L31, L37, L61; packages/extensions/tool-cordis/src/prompt.ts L36-46; vendor/hmr/src/index.ts rollback L482-499,
    L532-539; refreshConfig L300-312

### Draft table cells (unlocked; the PRD ticket locks wording)

| | deepseek-harness |
| --- | --- |
| **Pros** | Running sessions never change underneath. A real mount rejects broken rows first. |
| **Cons** | Nothing runs the new preset on a task. No evidence or lesson travels with it. |
| **Why** | An agent is a composition file, so a person asks an agent to write one. |
| **How: who edits** | A separate Creator-mode agent, when a person asks. Never on its own. |
| **How: evidence** | None. No reference run, probe, or score. A person tries the preset. |
| **How: validation** | A file shape check, then a real mount: rows resolve, validate, and start. |
| **How: carry over** | Preset folders picked by id at session start. New ones start as copies. |
| **How: rollback** | Running sessions keep their version. Undo is another edit or a delete. |

### Consistency with sections 7, 9, 21

Section 21 "How: improvement loop: None shipped; the attach points exist" is confirmed: no shipped plugin turns run evidence into config changes. Nuance for section 24:
the relevant shipped surface is not the loop events section 21 names. It is the preset roster plus Creator-mode authoring, which is person-triggered and evidence-free, so
the claim still holds. Section 21 Cons "Nothing checks the work" also agrees, because Creator mode checks structure only. Section 7 ("Skills change while a session runs",
catalog refreshes) agrees: an agent write into a skill root refreshes the catalog on the next step. But nothing prompts skill authoring from experience, and there is no
curator (unlike Hermes). Section 9 has no dsh column, which matches: no memory store ships, only default-off third-party memory MCP examples. Section 10 ("prompt is
assembled, never edited") is compatible: Creator edits the persona text inside a preset row, not the assembled prompt string. Section 19 ("A patch replaces one row by
id") matches composition-as-rows.

### Changed after verification

- Dropped the tool-cordis README's five-tool list (cordis_inspect/define/run/stop/undefine) as authority. The source and generated tool catalog at the tag show seven
  tools with immutable versioned packages, version pointers, and approval for browser halves. The README and the editing-cordis-compositions skill
  (cordis_mount/cordis_unmount) use stale names.
- Corrected 'a failed dynamic-plugin update keeps the old version running' to: the old current-version pointer is kept, but the old run is already stopped and must be
  re-run to roll back (prompt.ts L46).
- Downgraded 'mount checks config schema' to inferred. mount.ts directly checks inactive rows and leaked services. Unresolved packages and invalid configs surface from
  the loader, per the skill text.
- Downgraded gates from enforced to partly instruction-only. Preset privilege is explicitly not capped (README L135), and the escalation gate depends on the sandbox mode
  (env can set danger-full-access).
- Rejected 'tool-cordis ships by default'. It is only in the opt-in shipped `cordis` preset. The generated catalog calls it 'Not in any shipped tree (a deliberate
  opt-in)'.
- Rejected the proposed review-skill maintenance note as a match. It is a contributor process run by a private out-of-repo tool, status proposed, and not part of the
  harness.
- Kept verdict at partial rather than none, only because the config-authoring half (separate session, frozen executor, structural mount check, copy-to-reuse roster) is
  real shipped code. The evidence half is none.

### Not verified

- Absence of any evidence-driven editor rests on repo-wide greps (improvement/reflection/lesson/curator/optimizer terms, feedback consumers), not a read of every package.
- Whether some plugin row inside an isolate realm could let a preset relax sandbox or approval for its own agent; README says privilege is not capped, but no test or code
  path was traced.
- Whether production installs run HMR against the harness checkout (base bundle hmr root is '.'; web-app prompt names the checkout). The
  running-agent-edits-harness-source path is inferred, not traced.
- No retry cap on dynamic-plugin repair: no counter found by grep in tool-cordis or cordis-host-runner sources.
- Headless bundle preset roster wiring not checked; web-app bundle and apps/cli profile-boot were.
- Nothing was executed (by rule), so mount-validation and generation-stamp behavior come from source and docs, not observed runs.
