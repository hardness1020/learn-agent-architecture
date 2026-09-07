# Contributing

Thanks for contributing. This repo is a set of educational reconstructions.
Every claim should trace back to source code, docs, or observed behavior.
This checklist is what reviewers check on every PR.

## Before you open a PR

- Check open PRs and issues for overlap. One topic per PR.
- Read the section you want to change. Each section follows a fixed shape:
  Opening, Mechanism, Per system, Failure modes, Runnable, Sources.
- If you maintain or are affiliated with a project you are adding, say so in the PR description.

## Where content goes

- Source links belong in the `## Sources` block of the section they ground.
  A source should back a claim the section text makes.
- Nothing goes after the per-system table except its closing rule.
- Benchmarks go in the section they evaluate. General agent and harness evaluation
  belongs in section 23. Memory benchmarks belong in section 9 and the
  production-memory track. Group entries by purpose, one line each on what it measures,
  with paper and canonical repo links.

## Writing rules

- Short sentences. One idea per sentence.
- No em or en dashes. Use periods, commas, parentheses, or `·`.
- No line over 180 characters. Check: `awk 'length($0)>180' <file>` returns nothing.
- Prefer named, verifiable mechanisms over speculation. Cite sources.

## Translations

Every `README.md` ships with `README.zh-TW.md`, `README.zh-CN.md`, `README.ja.md`, and `README.ko.md`.
If you edit one, update all five in the same commit.
Write natural spoken Chinese, Japanese, and Korean. Keep technical terms in English
(`stop_reason`, `PreToolUse`, hook, harness, loop, prompt, token).

## Commits

- Conventional commits (`docs:`, `fix:`, `feat:`).
- Keep messages under 50 words.

## Review expectations

- PRs that add a single external link with no surrounding claim are usually too thin.
  Tie the addition to the section text, or expand it into an organized set.
- Reviewers may ask you to consolidate overlapping PRs into one.
