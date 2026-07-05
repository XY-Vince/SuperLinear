# Codex Planning Prompt Template

Use this template when ChatGPT or Codex does an upstream planning pass and the output should be pasted into Codex for execution.

```text
Goal:
<one concrete outcome>

Context:
- <repo, branch, files, docs, screenshots, errors, prior decisions>

Constraints:
- Always preserve private files and secrets.
- Follow AGENTS.md.
- Ask before irreversible, external, public, or high-cost actions.
- Keep the change narrow unless the task explicitly asks for architecture work.

Plan:
1. Inspect <specific files or docs>.
2. Implement <specific change>.
3. Run <verification commands>.
4. Review the diff for regressions.

Done when:
- <observable completion criteria>

Verification:
- <commands or manual checks>

Report back:
- Files changed.
- Why the approach was chosen.
- Verification result.
- Residual risk or follow-up.
```
