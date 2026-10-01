#!/usr/bin/env python3
"""
init_project.py — Multi-Agent OS v1.0.1 Project Initializer & Adopter (SuperLinear Hardened Edition)

Subcommands:
  new <target_dir>     Scaffold a new Lite+ project from scratch.
  adopt <target_dir>   Inject Lite+ baseline into an existing project without destroying existing configuration.

Options:
  --dry-run            Simulate operations without making changes to filesystem.
"""

import sys
import os
import json
import shutil
import argparse
from datetime import datetime, timezone

VERSION = "1.0.1"


def get_template_base_dir() -> str:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(script_dir)


def run_init(mode: str, target_dir: str, dry_run: bool = False) -> None:
    target_dir = os.path.abspath(target_dir)
    base_dir = get_template_base_dir()

    print(f"=== Multi-Agent OS v{VERSION} Project Initializer [{mode.upper()}] ===")
    print(f"Target Directory: {target_dir}")
    print(f"Dry Run: {dry_run}\n")

    manifest = {
        "version": VERSION,
        "mode": mode,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "target_dir": target_dir,
        "created_files": [],
        "existing_files": [],
        "suggestions": []
    }

    if not dry_run:
        os.makedirs(target_dir, exist_ok=True)

    # Core files to install
    files_to_copy = [
        ("core/PROJECT.md", "PROJECT.md"),
        ("core/AGENT_PROTOCOL.md", "AGENT_PROTOCOL.md"),
        ("adapters/codex/AGENTS.md", "AGENTS.md"),
        ("control/tasks/T-000-template.yaml", "control/tasks/T-000-template.yaml"),
        ("control/tasks/README.md", "control/tasks/README.md"),
    ]

    for src_rel, dst_rel in files_to_copy:
        src = os.path.join(base_dir, src_rel)
        dst = os.path.join(target_dir, dst_rel)

        if not os.path.exists(src):
            print(f"[WARN] Template source not found: {src_rel}")
            continue

        if os.path.exists(dst):
            manifest["existing_files"].append(dst_rel)
            if mode == "adopt" and dst_rel == "AGENTS.md":
                # Special handling for existing AGENTS.md in adopt mode
                suggestion_rel = "AGENTS.md.merge-suggestion"
                suggestion_dst = os.path.join(target_dir, suggestion_rel)
                suggestion_content = (
                    "<!-- Multi-Agent OS Lite+ Integration Suggestion -->\n"
                    "# Multi-Agent OS Integration\n"
                    "This project adopts Multi-Agent OS Lite+ v1.0.1.\n"
                    "- Specification: PROJECT.md\n"
                    "- Protocol & Merge Authority: AGENT_PROTOCOL.md\n"
                    "- Task Leases & Merge Gates: control/tasks/\n"
                    "- Builder: Follow task scope and touched areas; do not self-authorize merges.\n\n"
                )
                print(f"[ADOPT] Existing AGENTS.md detected. Generating merge suggestion: {suggestion_rel}")
                if not dry_run:
                    with open(suggestion_dst, "w", encoding="utf-8") as f:
                        f.write(suggestion_content)
                manifest["suggestions"].append(suggestion_rel)
            else:
                print(f"[SKIP] Existing file preserved: {dst_rel}")
        else:
            print(f"[CREATE] Installing: {dst_rel}")
            manifest["created_files"].append(dst_rel)
            if not dry_run:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy(src, dst)

    manifest_file = os.path.join(target_dir, ".agent-os-manifest.json")
    if not dry_run:
        with open(manifest_file, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)
        print(f"[MANIFEST] Installation manifest written to .agent-os-manifest.json")

    print(f"\n[DONE] Successfully processed {len(manifest['created_files'])} file(s).")
    if manifest["suggestions"]:
        print("\nAction required:")
        for s in manifest["suggestions"]:
            print(f"  - Review {s} and merge relevant entry lines into your existing AGENTS.md")


def main():
    parser = argparse.ArgumentParser(
        description=f"Multi-Agent OS v{VERSION} Project Initializer & Adopter (SuperLinear Hardened Edition)"
    )
    subparsers = parser.add_subparsers(dest="subcommand", required=True, help="Subcommand to execute")

    # 'new' subcommand
    new_parser = subparsers.add_parser("new", help="Scaffold a new Lite+ project from scratch")
    new_parser.add_argument("target_dir", help="Path to project directory")
    new_parser.add_argument("--dry-run", action="store_true", help="Simulate without writing files")

    # 'adopt' subcommand
    adopt_parser = subparsers.add_parser("adopt", help="Inject Lite+ baseline into an existing project")
    adopt_parser.add_argument("target_dir", help="Path to existing project directory")
    adopt_parser.add_argument("--dry-run", action="store_true", help="Simulate without writing files")

    args = parser.parse_args()
    run_init(args.subcommand, args.target_dir, args.dry_run)


if __name__ == "__main__":
    main()
