# Task & Writer Lease Governance

## Purpose
In Multi-Agent OS v1.0, a **Writer Lease** is an **authorization and merge-eligibility record**, not a simulated local filesystem lock.

## Rules
1. **Human Authority**: Only Human may grant, renew, transfer, or revoke a lease.
2. **Merge Gate Check**: Before a branch merges into canonical `main`, the gate validates:
   - `status == ACTIVE`
   - Current time `< expires_at`
   - `base_sha` is an ancestor of the branch HEAD
   - Changed paths $\subseteq$ `touched_areas`
   - Declared risk matches or exceeds auto-computed risk
   - Required CI checks pass
   - Required independent review satisfied (for R2 / R3)
3. **Location**:
   - For GitHub projects: Recommended in PR / Issue metadata or `control/tasks/T-xxx.yaml` committed to `main`.
   - For Local projects: `control/tasks/T-xxx.yaml` on `main`. (The version in the feature branch cannot self-authorize).
