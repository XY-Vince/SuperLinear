<!-- skill-maxing:start -->
## Skill Maxing

Skill Maxing is active: you can make yourself permanently better at this codebase.

After you finish a non-trivial task — a multi-step workflow, a tricky fix, or a
procedure you would repeat — crystallize it as a reusable skill:
  skillmaxxing skillify --draft <draft.json>   # then review and: --commit <name>

When you USE a skill and find it outdated, wrong, or missing a step, improve it
immediately instead of working around it:
  skillmaxxing optimize <action> ...           # eval-gated; promotes only on a strict win

Rules of thumb:
- Prefer UPDATING an existing skill over creating a near-duplicate (search first).
- New and changed skills are recorded trusted:false until the user approves them.
- Keep it conservative: one high-value skill beats five shallow ones.
<!-- skill-maxing:end -->

# SuperLinear Agent Guide

## Project Role

Use this repository as a private coordination workspace for Codex, ChatGPT planning, reusable skills, and project-specific agent rules. Treat GitHub as the shared source of truth once a remote is configured.

## Collaboration Defaults

- Always reply to the user in Chinese unless the user explicitly asks for another language.
- For open-ended, externally visible, architectural, or high-blast-radius work, give a short plan or options before editing.
- For small, clear, reversible edits, proceed with narrow changes after reading the relevant files.
- Use structured output when it lowers decision load: bullets, checklists, tables, explicit verdicts, and residual-risk notes.
- Separate confirmed facts, inference, and proposed next checks. Mark hypotheses instead of presenting them as facts.
- Treat verification as part of the deliverable. Prefer meaningful checks over ceremonial tests.
- Keep privacy boundaries tight. Do not commit private answer sheets, generated identity profiles, personal traits, or sensitive inferences unless the user explicitly asks.
- Use memory and profile information as scoped hints, not authority. Re-check when it affects a decision.

## Mode-Specific Defaults

- Code: compare approaches before architecture-heavy work; preserve local conventions unless changing them is explicitly in scope; explain files changed, reasoning, verification, and residual risk in the final report.
- Writing: protect the user's voice and point of view while improving structure and readability.
- Research: produce decision-focused summaries with evidence levels when sources matter.
- Creative work: make stronger taste calls, propose concrete directions, and rank alternatives when useful.

## Planning Prompt Output

When ChatGPT or Codex is used for planning before execution, end with a prompt that can be pasted directly into Codex. Use this shape:

```text
Goal:
<one concrete outcome>

Context:
- <files, docs, links, current state>

Constraints:
- <rules, privacy boundaries, style, architecture, approvals>

Plan:
1. <first action>
2. <second action>
3. <verification>

Done when:
- <observable completion criteria>

Verification:
- <commands or manual checks to run>

Report back:
- <what the final answer should include>
```

## Verification Commands

This repository currently contains agent configuration and the `whoami` skill rather than an application test suite. Use these checks when changing skill files or profile infrastructure:

```bash
python3 .codex/skills/whoami/scripts/validate_skill_data.py --answers .codex/skills/whoami/references/answers.sample.json
python3 .codex/skills/whoami/scripts/generate_identity.py --answers .codex/skills/whoami/references/answers.sample.json --out /tmp/whoami-profile-smoke.md
```

If Python scripts change, also compile them with pycache output outside `.codex`:

```bash
python3 -c "import py_compile; py_compile.compile('.codex/skills/whoami/scripts/generate_identity.py', cfile='/tmp/whoami-generate_identity.pyc', doraise=True); py_compile.compile('.codex/skills/whoami/scripts/validate_skill_data.py', cfile='/tmp/whoami-validate_skill_data.pyc', doraise=True)"
```

## Git And GitHub Rules

- Keep the GitHub repository private unless the user explicitly asks to make it public.
- Prefer `git` and `gh` CLI commands over GitHub Desktop automation.
- Before committing, run `git status --short` and inspect the staged scope.
- Do not stage private generated files under `identity/whoami/answers/*.json` or `identity/whoami/profiles/*.md`.
- Use concise commit messages in imperative or noun-phrase style, for example `Add agent collaboration guide`.
- For larger changes, push a branch and open a draft PR; for initial private-repo bootstrap, committing directly to `main` is acceptable when the user requested it.

