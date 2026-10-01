# Multi-Agent OS Provenance Record

- **Distribution Version**: `v1.0.1` (SuperLinear Hardened Edition)
- **Upstream Release**: Multi-Agent OS v1.0 Final (`multi-agent-os-v1.0.zip`)
- **Upstream SHA-256**: `a5ba98091896a9302f3bee7ddc7e2961223c743723e595ddfe540fb18adffbab`
- **Import Date**: 2026-10-01
- **Host Workspace**: `SuperLinear` (base commit: `b5210a73dfbaf71490fe6a8e3f81e761353eeec7`)
- **Architecture Model**: **AG-first, Codex-gated** (AntiGravity as high-throughput builder/architect; Codex as gatekeeper & reviewer).

---

## Modifications in v1.0.1 (Round 1 & Round 2 Hardening)

1. **`tools/verify_lease.py` Security & Gate Hardening**:
   - Eliminated `shell=True` in subprocess calls; using array arguments (`shell=False`).
   - Enforced commit validation using `git rev-parse --verify --quiet <ref>^{commit}` to prevent command/flag injection.
   - Enforced Fail-Closed validation: required schema fields, ISO 8601 with explicit timezone, strictly future expiration.
   - **Fail-Closed on Lease Source**: Authoritative lease must be present in trusted refs (`origin/main` / `main` / `--lease-ref`). Branch-local uncommitted files are strictly rejected unless `--allow-uncommitted-lease` is explicitly passed for local test.
   - Added metadata validation: `writer` (must be known agent), `granted_by`, `granted_at` (must be <= `expires_at`), and `branch` matching.
   - Fixed R0 risk detection: pure non-runtime documentation/metadata files (`.md`, `.txt`, `.rst`, images) without code changes allow `R0`; runtime code modifications require $\ge R1$.
   - Implemented dynamic risk floor calculation and risk monotonicity enforcement (declared risk $\ge$ computed minimum risk; R0 touching control plane paths fails).
   - Added standard CLI `--help` with `argparse`.

2. **`tools/worktree_review.sh` Isolation & Path Safety**:
   - Fixed argument parsing for `clean` action (`WORKTREE_DIR` defaults to `$2`).
   - Added canonical path resolution via `realpath` and prefix whitelist enforcement (`/tmp/agent-review-*` or designated review root, supporting macOS `/private/tmp`).
   - **Eliminated all fallback `rm -rf`**: Strictly verifies that target path is present in `git worktree list --porcelain`. Unregistered directories are rejected with an error; never deleted.

3. **`tools/init_project.py` Subcommands & Idempotency**:
   - Added `new` and `adopt` subcommands with `--dry-run` flag and `.agent-os-manifest.json` generation.
   - **Fail-Closed on Missing Templates**: Verifies all required distribution templates (`core/`, `adapters/`, `control/`) upfront. If any template is missing, aborts with non-zero exit code.
   - In `adopt` mode, existing `AGENTS.md` files are analyzed to generate `.merge-suggestion` (updated for AG-first, Codex-gated model) rather than silently skipped or overwritten.

4. **Role Architecture Alignment (AG-first, Codex-gated)**:
   - Updated `AGENT_PROTOCOL.md`, `AGENTS.md`, `SPEC_V1_FINAL.md`, `README.md`, `docs/operational-cheat-sheet.md`, and adapters.
   - AntiGravity is designated as the primary builder / architect for high throughput; Codex supervises leases and acts as gatekeeper.
   - Enforced **Session Separation Invariant**: The same AG session cannot review its own build.

5. **`MIGRATION_SETUP_GUIDE.md`**:
   - Completely revised to eliminate outdated/incompatible commands.
   - Corrected whitelist paths to `/tmp/agent-review-*`.
   - Replaced file-overwriting `cp` with safe backup + idempotent marker injection (`<!-- multi-agent-os:start/end -->`).
   - Instructed full distribution package installation rather than tools-only.
