# Agent harness architecture

The vocabulary sections use when they explain harness mechanisms. A term here is the one section text uses; the `_Avoid_` words are named once, mapped, and never adopted.

## Sources

**System under study**:
A harness the repo reads end to end and gives a column in per-system tables and a row in the Systems table.
_Avoid_: studied product, vendor example

**Mechanism source**:
An external project cited for one mechanism. Named in a section body and in Sources, never given a column or a Systems-table row.
_Avoid_: system, case study

## Improvement

**Improvement loop**:
The outer loop that turns evidence from runs into changes to the harness. The canonical term in every section.
_Avoid_: harnessing, self-improvement loop, recursive improvement

**Harness adaptation**:
An improvement loop where an improver edits an executor's configuration from probes, with no grader in the loop, inside one task and across a stream of tasks.
_Avoid_: recursive agent harnessing, task-level recursion, harnessing-level recursion

**Executor**:
The frozen agent whose configuration gets edited. Its loop, tools, and weights stay unchanged.
_Avoid_: substrate, solver

**Configuration**:
The executor's editable files: system prompt, guardrails, memory file, tool guidance, subagents, skills, scripts, and plugins or hooks.
_Avoid_: execution harness

**Improver**:
A separate session that edits a copy of the executor's configuration. It never edits its own instructions or gates.
_Avoid_: improvement harness, optimizer

**Edit surface**:
The declared set of configuration files the improver may change. Anything off it is never written back.
_Avoid_: edit region, search space

**Probe**:
One run of a candidate configuration on the same task, read as a behavioral contrast, never as a score.
_Avoid_: rollout, trial

**Library**:
A per-domain store of adopted configurations, each with a lesson. A task reads only entries from earlier tasks.
_Avoid_: improvement library, experience bank
