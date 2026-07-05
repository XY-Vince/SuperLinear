#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  scripts/git_publish.sh --repo OWNER/REPO --message "Commit message" [--description "Text"] [--branch main]

Purpose:
  Initialize this directory as a git repo if needed, commit the current non-ignored
  files, create a private GitHub repo with gh when no origin exists, and push.

Notes:
  - Requires GitHub CLI: gh auth login
  - The GitHub repo is private by default.
  - Review git status before running on sensitive workspaces.
USAGE
}

repo=""
message=""
description="Private SuperLinear agent workspace."
branch="main"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo)
      repo="${2:-}"
      shift 2
      ;;
    --message)
      message="${2:-}"
      shift 2
      ;;
    --description)
      description="${2:-}"
      shift 2
      ;;
    --branch)
      branch="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ -z "$repo" || -z "$message" ]]; then
  usage >&2
  exit 2
fi

if ! command -v git >/dev/null 2>&1; then
  echo "git is required." >&2
  exit 1
fi

if ! command -v gh >/dev/null 2>&1; then
  echo "GitHub CLI is required. Install it, then run: gh auth login" >&2
  exit 1
fi

gh auth status >/dev/null

if [[ ! -d .git ]]; then
  git init
  git branch -M "$branch"
fi

current_branch="$(git branch --show-current || true)"
if [[ -z "$current_branch" ]]; then
  git checkout -B "$branch"
elif [[ "$current_branch" != "$branch" ]]; then
  git branch -M "$branch"
fi

echo "Current status:"
git status --short

git add -A

if ! git diff --cached --quiet; then
  git commit -m "$message"
else
  echo "No staged changes to commit."
fi

if ! git remote get-url origin >/dev/null 2>&1; then
  gh repo create "$repo" --private --description "$description" --source=. --remote=origin
fi

git push -u origin "$branch"
echo "Published to $repo on branch $branch."
