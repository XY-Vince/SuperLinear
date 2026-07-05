---
name: whoami
description: Generate collaboration identity prompts from scenario questionnaires and map user answers to operational axioms for coding, writing, research, and creative work. Use when a user asks to discover who they are, define communication preferences, build an identity prompt, run a MBTI-like collaboration quiz, or create/update a whoami profile.
---

# Whoami

## Overview

Use this skill to turn a user's implicit collaboration preferences into an operational identity prompt. It is not a personality diagnosis; it produces working defaults for assistants.

## Workflow

1. Run or present `references/questionnaire-v0.2.json`. In this project, present questions and review notes in Chinese by default unless the user asks for another language.
2. Collect one option per question as an answer JSON file.
   - Prefer project-local private paths: `identity/whoami/answers/<name>.json` for answers and `identity/whoami/profiles/<name>.md` for generated profiles.
   - Validate answer files with `python3 scripts/validate_skill_data.py --answers identity/whoami/answers/<name>.json` before generating profiles.
3. Generate the identity profile:

```bash
python3 scripts/generate_identity.py --answers answers.json --out profile.md
```

4. Review the generated profile with the user. Treat every axiom as a candidate until the user confirms it or repeated task corrections validate it.
5. Promote only durable, behavior-changing preferences into long-term memory or future system prompts.

## Project Customization

- Keep user-specific answers and generated profiles out of committed artifacts unless the user explicitly asks to share them.
- When editing questions, keep option keys as `a`, `b`, `c`, and `d`; every weighted dimension must exist in `references/axiom-catalog-v0.2.json`.
- When editing axioms, keep activation dimensions tied to catalog dimensions and write prompt rules as operational assistant behavior, not personality claims.
- After changing `questionnaire-v0.2.json`, `axiom-catalog-v0.2.json`, or the answer schema, run:

```bash
python3 scripts/validate_skill_data.py
python3 scripts/generate_identity.py --answers references/answers.sample.json --out /tmp/whoami-profile-smoke.md
```

## Upgrade Notes

- Upstream source: `https://github.com/ZaynJarvis/whoami-skill/tree/main/skill/whoami`.
- Installed from upstream commit `0173855a496bb577199f20185494f0cf67610129`.
- On upstream refresh, preserve this project's Chinese-default workflow notes, `references/answers.schema.json`, `references/answers.sample.json`, `scripts/validate_skill_data.py`, and private output paths unless deliberately replacing them.

## Output Standards

Good output is specific enough to change assistant behavior:

- when to ask vs proceed,
- how much evidence to require,
- how direct critique should be,
- how to handle coding, writing, research, and creative modes,
- what not to infer or remember.

Bad output uses flattering labels, broad archetypes, or clinical claims. Do not tell the user they are a personality type.

## References

- `references/questionnaire-v0.2.json` - 40 scenario-based single-choice questions.
- `references/axiom-catalog-v0.2.json` - operational axiom candidates and activation rules.
- `references/output-template.md` - generated identity prompt structure.
- `references/answers.schema.json` - JSON shape for answer documents.
- `references/answers.sample.json` - sample answer file for smoke tests.

## Safety

Do not infer sensitive traits, diagnoses, politics, values, or personal life facts from the questionnaire. Store only operational collaboration preferences, and only when the user wants them reused.
