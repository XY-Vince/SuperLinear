# Review Record

- **Task**: T-XXX
- **Reviewer**: AntiGravity (or independent reviewer)
- **Base SHA**: `<base_sha>`
- **Target SHA**: `<target_sha>`
- **Date**: YYYY-MM-DD
- **Verdict**: PASS / REVISE / ESCALATE

---

## Scope Reviewed
- List of files, components, and requirements verified.

## Scope NOT Reviewed
- Explicitly state any files, third-party libraries, or mock data not covered.

---

## Findings

### BLOCKING
*Issues that prevent merge into canonical main.*
1. `path/to/file:line` - Description of defect, invariant violation, or security issue.

### NON-BLOCKING
*Suggestions, minor improvements, or debt items.*
1. `path/to/file:line` - Nit / readability / performance suggestion.

### QUESTIONS
*Clarifications needed from Builder or Human.*
1. Question regarding architectural intent or design assumption.

---

## Independent Validation Performed
*Reviewer must execute checks independently against Target SHA.*
- [ ] Checked out Target SHA into clean worktree
- [ ] Diff inspected prior to reading Builder narrative
- [ ] Executed: `<command 1>` (e.g. `pytest`) -> Status: PASS
- [ ] Executed: `<command 2>` (e.g. `ruff check .`) -> Status: PASS
