# Changelog

## v1.2
- Upgraded protocol goal to "Direct, Grounded & Proportionate Communication".
- Expanded Core Invariant with Grounded & Proportionate Claims:
  - Match claim strength to evidence; distinguish observed vs. inferred vs. unverified.
  - Scope completion claims (passing a test != live stability or absence of defects).
  - Replace celebration, self-praise, and dramatic metaphors with factual results.
  - Objective error reporting without assigning motives or inventing explanations.
  - Urgency resilience: user urgency changes priority, never truth standard.
- Calibrated Presets:
  - `coding.md`: Diagnostic findings vs. unverified root cause; risk-proportional verification; no ceremonial testing.
  - `academic.md`: Evidence-led; no speculative intent.
  - `investment.md`: Downside risks for investment theses; no redundant risk warnings on simple lookups.
  - `marketplace.md`: Truthful selling points without superlative hype; cognitive/physical help allowed.
- Overhauled `lint/ncs-lint.py`:
  - Added clause-level isolation preventing single literal allowance from masking entire line.
  - Added markdown code-block and inline code skipping.
  - Broadened Chinese classifier allowances (`这块 + [名词]`).
  - Allowed genuine cognitive/technical capability offers (`我可以协助核对译文`).
  - Fixed English boundary regex (`\b` raw string).
  - Added scoped claim review rules: `unbounded_success_claim`, `unsupported_guarantee`, `dramatic_or_dismissive_framing`.
  - Differentiated `style_warning` from `claim_review`.
  - Fixed CLI exit codes (I/O error takes priority as 2).
- Added comprehensive regression suite (`tests/`) and behavioral evaluation scenarios (`eval/`).

## v1.1
- Removed independent LLM style auditor.
- Formalized 3-layer architecture: Global Invariant → Context Preset → Warning-only Lint.
- Replaced keyword blacklist logic with structure-aware lint patterns and literal-use allowances.
- Added explicit protection for normal human courtesy.
- Adopted static-copy deployment for the Core rule across Antigravity, Codex, and Claude Code.
- Added Marketplace, Academic, Investment, and Coding presets.
