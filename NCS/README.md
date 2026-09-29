# NCS Protocol v1.2

A lightweight cross-agent style and communication calibration system for direct, evidence-grounded, and proportionate interaction while avoiding scripted customer-service language and dramatic overstatement.

## Architecture

```text
Layer 1 — Global Invariant (Direct, Grounded & Proportionate Core)
        ↓
Layer 2 — Context Presets (Marketplace / Academic / Investment / Coding)
        ↓
Layer 3 — Warning-only Static Lint (Style Warnings & Scoped Claim Reviews)
```

### Layer 1 — Global Invariant
`NCS.md` is the canonical specification. The Core block is copied statically into each platform's global rules file.

### Layer 2 — Context Presets
- `presets/marketplace.md`
- `presets/academic.md`
- `presets/investment.md`
- `presets/coding.md`

Presets specify domain calibrations and tone specialization on top of the Core Invariant. They are consumed in two ways:
1. **Prompt Mixins**: Reference or append the domain preset file into project/role rules (e.g. `SuperLinear/AGENTS.md` specializes with `presets/coding.md`) rather than duplicating the global Core Invariant.
2. **Linter Domain Rules**: Pass `--preset <name>` to `lint/ncs-lint.py` to enable targeted domain checks (e.g. investment hype, marketplace superlatives).

### Layer 3 — Warning-only Static Lint
`lint/ncs-lint.py`

Use for outward-facing text when useful: Marketplace listings/messages, email drafts, social posts, formal memos, exported user-facing artifacts. Do not run it on every ordinary analysis turn.

The lint warns only, never rewrites, skips code blocks, evaluates clauses individually, and separates:
- `style_warning`: Customer-service padding, empty openers, marketing hype.
- `claim_review`: Unbounded success assertions, unprovable guarantees, and dramatic framing that warrant evidence checks.

## Platform Deployment & Canonical Hierarchy

- **Canonical Specification**: `NCS.md` is the single source of truth for protocol rules.
- **Platform Global Rules** (Layer 1 Core Invariant):
  - Antigravity: `~/.gemini/GEMINI.md` (injected globally as `user_global`) → see `deployment/antigravity-GEMINI-snippet.md`
  - Codex: `~/.codex/AGENTS.md` (injected globally) → see `deployment/codex-AGENTS-snippet.md`
  - Claude Code: `~/.claude/CLAUDE.md` (injected globally) → see `deployment/claude-CLAUDE-snippet.md`
- **Workspace-Level Rules**:
  - Project rules (such as `SuperLinear/AGENTS.md`) inherit the Core Invariant from the platform global rules and specialize with Layer 2 Presets (`presets/coding.md`) to avoid duplicate token consumption.

## Lint Usage

```bash
# Check a file using Core Invariant rules
python3 lint/ncs-lint.py draft.md

# Activate domain-specific preset rules (coding / marketplace / investment / academic)
python3 lint/ncs-lint.py --preset investment draft.md

# List available presets
python3 lint/ncs-lint.py --list-presets

# Pipe via stdin
echo '这边已经查到价格' | python3 lint/ncs-lint.py -

# Output machine-readable JSON
python3 lint/ncs-lint.py --json draft.md
```

Exit codes:
- `0`: Clean (no warnings or claim reviews)
- `1`: Warnings or claim reviews detected
- `2`: File not found or CLI read/usage error

## Design Principles
1. Friendly is fine; servile or performatively enthusiastic is not.
2. Match claim strength to evidence; passing a test != live stability or bug-free.
3. Replace celebration and dramatic metaphors with concrete results.
4. User urgency changes priority, never truth standard.
5. Role prompts may specialize NCS, but may not override it.
6. Lint warns and prompts review; it never rewrites.
7. Literal meaning beats keyword bans.
