#!/usr/bin/env bash
# worktree_review.sh — Setup or teardown an isolated git worktree for AntiGravity independent review.
# Version: v1.0.1 (SuperLinear Hardened Edition)
set -euo pipefail

usage() {
    echo "Multi-Agent OS v1.0.1 Git Worktree Review Tool (SuperLinear Hardened)"
    echo "Usage:"
    echo "  $0 start <target_sha> [worktree_dir]"
    echo "  $0 clean [worktree_dir]"
    echo "  $0 --help | -h"
    echo ""
    echo "Security constraints:"
    echo "  worktree_dir must strictly reside under one of the allowed prefixes:"
    echo "    - /tmp/agent-review-* (or /private/tmp/agent-review-* on macOS)"
    echo "    - /tmp/agent-reviews/* (or /private/tmp/agent-reviews/* on macOS)"
    echo "    - ~/.agent-reviews/worktrees/*"
}

if [ $# -lt 1 ]; then
    usage
    exit 1
fi

ACTION="$1"

if [ "$ACTION" = "--help" ] || [ "$ACTION" = "-h" ] || [ "$ACTION" = "help" ]; then
    usage
    exit 0
fi

case "$ACTION" in
    start)
        TARGET_SHA="${2:-}"
        WORKTREE_DIR="${3:-/tmp/agent-review-worktree}"
        if [ -z "$TARGET_SHA" ]; then
            echo "[FAIL] target_sha is required for start"
            usage
            exit 1
        fi
        ;;
    clean)
        TARGET_SHA=""
        WORKTREE_DIR="${2:-/tmp/agent-review-worktree}"
        ;;
    *)
        echo "[FAIL] Unknown action: $ACTION"
        usage
        exit 1
        ;;
esac

# 1. Canonicalize path
RESOLVED_DIR="$(python3 -c "import os, sys; print(os.path.realpath(os.path.expanduser(sys.argv[1])))" "$WORKTREE_DIR")"

# Safe prefixes (canonicalized)
CANON_TMP="$(python3 -c "import os; print(os.path.realpath('/tmp'))")"
SAFE_PREFIX_1="${CANON_TMP}/agent-review-"
SAFE_PREFIX_2="${CANON_TMP}/agent-reviews/"
SAFE_PREFIX_3="$(python3 -c "import os; print(os.path.realpath(os.path.expanduser('~/.agent-reviews/worktrees')))")/"

# Also support raw /tmp prefixes before symlink resolution
RAW_PREFIX_1="/tmp/agent-review-"
RAW_PREFIX_2="/tmp/agent-reviews/"
RAW_PREFIX_3="${HOME}/.agent-reviews/worktrees/"

# 2. Security whitelist check
is_safe_path=0
if [[ "$RESOLVED_DIR" == ${SAFE_PREFIX_1}* ]] || [[ "$RESOLVED_DIR" == ${SAFE_PREFIX_2}* ]] || [[ "$RESOLVED_DIR" == ${SAFE_PREFIX_3}* ]] || \
   [[ "$RESOLVED_DIR" == ${RAW_PREFIX_1}* ]]  || [[ "$RESOLVED_DIR" == ${RAW_PREFIX_2}* ]]  || [[ "$RESOLVED_DIR" == ${RAW_PREFIX_3}* ]]; then
    is_safe_path=1
fi

if [ "$is_safe_path" -ne 1 ]; then
    echo "[FAIL] Security Violation: path '$RESOLVED_DIR' is not within an allowed review directory."
    echo "Allowed prefixes: ${SAFE_PREFIX_1}*, ${SAFE_PREFIX_2}*, ${SAFE_PREFIX_3}*"
    exit 1
fi

# Helper function to check if directory is registered in git worktree list
is_registered_worktree() {
    local target="$1"
    # Match exact line 'worktree <path>'
    git worktree list --porcelain | grep -E "^worktree (${target}|$(python3 -c "import os, sys; print(os.path.realpath(sys.argv[1]))" "$target"))$" >/dev/null 2>&1
}

case "$ACTION" in
    start)
        # Validate target_sha
        if ! git rev-parse --verify --quiet "${TARGET_SHA}^{commit}" >/dev/null 2>&1; then
            echo "[FAIL] Invalid target_sha: '$TARGET_SHA' is not a valid commit object"
            exit 1
        fi

        echo "Preparing isolated review worktree at: $RESOLVED_DIR for SHA: $TARGET_SHA"

        # Cleanup existing directory if present
        if [ -d "$RESOLVED_DIR" ]; then
            if is_registered_worktree "$RESOLVED_DIR"; then
                echo "Removing previously registered worktree at $RESOLVED_DIR..."
                git worktree remove --force "$RESOLVED_DIR" >/dev/null 2>&1 || true
            fi
            if [ -d "$RESOLVED_DIR" ]; then
                # Safe fallback cleanup: only if it contains .git file (worktree marker)
                if [ -f "$RESOLVED_DIR/.git" ]; then
                    rm -rf "$RESOLVED_DIR"
                else
                    echo "[FAIL] Existing directory '$RESOLVED_DIR' is not a git worktree. Aborting."
                    exit 1
                fi
            fi
        fi

        mkdir -p "$(dirname "$RESOLVED_DIR")"
        git worktree add -d "$RESOLVED_DIR" "$TARGET_SHA"
        echo "✓ Isolated worktree ready at: $RESOLVED_DIR"
        echo "Next steps for AntiGravity Reviewer:"
        echo "  1. cd $RESOLVED_DIR"
        echo "  2. Inspect diff and run independent validation (e.g. pytest, npm test)"
        echo "  3. Save review record to ~/.agent-reviews/<project>/<task>/<target_sha>.md"
        echo "  4. $0 clean $RESOLVED_DIR"
        ;;

    clean)
        echo "Cleaning up review worktree at: $RESOLVED_DIR"
        if [ ! -d "$RESOLVED_DIR" ]; then
            echo "✓ Directory $RESOLVED_DIR does not exist. Nothing to clean."
            exit 0
        fi

        if is_registered_worktree "$RESOLVED_DIR"; then
            git worktree remove --force "$RESOLVED_DIR"
            echo "✓ Registered git worktree removed successfully."
        elif [ -f "$RESOLVED_DIR/.git" ]; then
            # Detached leftover worktree with .git file
            rm -rf "$RESOLVED_DIR"
            git worktree prune >/dev/null 2>&1 || true
            echo "✓ Leftover worktree files cleaned up."
        else
            echo "[FAIL] Refusing to remove '$RESOLVED_DIR': directory exists but is neither a registered git worktree nor a valid worktree checkout."
            exit 1
        fi
        ;;
esac
