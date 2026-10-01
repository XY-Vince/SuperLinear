# Multi-Agent OS Provenance Record

- **Distribution Version**: `v1.0.1` (SuperLinear Hardened Edition)
- **Upstream Release**: Multi-Agent OS v1.0 Final (`multi-agent-os-v1.0.zip`)
- **Upstream SHA-256**: `a5ba98091896a9302f3bee7ddc7e2961223c743723e595ddfe540fb18adffbab`
- **Import Date**: 2026-10-01
- **Host Workspace**: `SuperLinear` (base commit: `b5210a73dfbaf71490fe6a8e3f81e761353eeec7`)

---

## Modifications in v1.0.1

1. **`tools/verify_lease.py` Security & Gate Hardening**:
   - Eliminated `shell=True` in subprocess calls; using array arguments (`shell=False`).
   - Enforced commit validation using `git rev-parse --verify --quiet <ref>^{commit}` to prevent command/flag injection.
   - Enforced Fail-Closed validation: required schema fields, ISO 8601 with explicit timezone, strictly future expiration.
   - Implemented dynamic risk floor calculation and risk monotonicity enforcement (declared risk $\ge$ computed minimum risk; R0 touching control plane paths fails).
   - Added `--lease-ref` support to load authoritative lease metadata from trusted branches (`origin/main` / `main`), preventing self-authorized leases inside feature branches.
   - Added standard CLI `--help` with `argparse`.

2. **`tools/worktree_review.sh` Isolation & Path Safety**:
   - Fixed argument parsing for `clean` action (`WORKTREE_DIR` defaults to `$2` instead of unused `$3`).
   - Added canonical path resolution via `realpath` and prefix whitelist enforcement (`/tmp/agent-review-*` or designated review root).
   - Enforced registration check via `git worktree list --porcelain` before deletion to prevent arbitrary directory removal.

3. **`tools/init_project.py` Subcommands & Idempotency**:
   - Added `new` and `adopt` subcommands.
   - Added `--dry-run` flag and `.agent-os-manifest.json` generation.
   - In `adopt` mode, existing `AGENTS.md` files are analyzed to generate `.merge-suggestion` rather than silently skipped or overwritten.

4. **Automated Verification**:
   - Added dedicated test suite in `multi-agent-os/tests/` covering injection resistance, fail-closed handling, risk monotonicity, and CLI operations.
