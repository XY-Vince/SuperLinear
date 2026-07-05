#!/usr/bin/env python3
"""Generate a collaboration identity prompt from whoami answers."""

from __future__ import annotations

import argparse
import json
import textwrap
from pathlib import Path
from typing import Any


SKILL_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_QUESTIONNAIRE = SKILL_ROOT / "references" / "questionnaire-v0.2.json"
DEFAULT_CATALOG = SKILL_ROOT / "references" / "axiom-catalog-v0.2.json"
DEFAULT_TEMPLATE = SKILL_ROOT / "references" / "output-template.md"


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def bullet(lines: list[str]) -> str:
    return "\n".join(f"- {line}" for line in lines) if lines else "- None yet."


def score_answers(
    questionnaire: dict[str, Any],
    catalog: dict[str, Any],
    answer_doc: dict[str, Any],
) -> dict[str, int]:
    dimensions = {dim["id"]: 0 for dim in catalog["dimensions"]}
    answers = answer_doc.get("answers", {})
    questions = questionnaire["questions"]

    missing = [q["id"] for q in questions if q["id"] not in answers]
    if missing:
        raise SystemExit(f"Missing answers: {', '.join(missing)}")

    for question in questions:
        qid = question["id"]
        choice = answers[qid]
        options = question["options"]
        if choice not in options:
            raise SystemExit(f"Invalid answer for {qid}: {choice!r}")
        for dimension, weight in options[choice].get("weights", {}).items():
            dimensions.setdefault(dimension, 0)
            dimensions[dimension] += int(weight)

    return dimensions


def condition_true(condition: dict[str, Any], scores: dict[str, int]) -> bool:
    value = scores.get(condition["dimension"], 0)
    if "min" in condition and value < condition["min"]:
        return False
    if "max" in condition and value > condition["max"]:
        return False
    return True


def activates(axiom: dict[str, Any], scores: dict[str, int]) -> bool:
    activation = axiom.get("activation", {})
    if "all" in activation:
        return all(condition_true(cond, scores) for cond in activation["all"])
    if "any" in activation:
        return any(condition_true(cond, scores) for cond in activation["any"])
    return False


def selected_axioms(catalog: dict[str, Any], scores: dict[str, int]) -> list[dict[str, Any]]:
    return [axiom for axiom in catalog["axioms"] if activates(axiom, scores)]


def dimension_lookup(catalog: dict[str, Any]) -> dict[str, dict[str, str]]:
    return {dim["id"]: dim for dim in catalog["dimensions"]}


def orientation(score: int) -> str:
    if score >= 4:
        return "high"
    if score <= -3:
        return "low"
    if score >= 2:
        return "lean high"
    if score <= -1:
        return "lean low"
    return "neutral"


def top_dimensions(
    scores: dict[str, int], dims: dict[str, dict[str, str]], limit: int = 8
) -> list[tuple[str, int, str]]:
    ranked = sorted(scores.items(), key=lambda kv: (abs(kv[1]), kv[0]), reverse=True)
    return [(dim, score, orientation(score)) for dim, score in ranked[:limit] if score != 0]


def score_snapshot(scores: dict[str, int], dims: dict[str, dict[str, str]]) -> str:
    rows = ["| Dimension | Score | Reading |", "| --- | ---: | --- |"]
    for dim, score, read in top_dimensions(scores, dims, limit=12):
        name = dims.get(dim, {}).get("name", dim)
        rows.append(f"| {name} | {score} | {read} |")
    return "\n".join(rows)


def strong_defaults(scores: dict[str, int], dims: dict[str, dict[str, str]]) -> str:
    lines: list[str] = []
    for dim, score, read in top_dimensions(scores, dims, limit=10):
        if read == "neutral":
            continue
        entry = dims.get(dim, {"name": dim, "high": dim, "low": dim})
        behavior = entry["high"] if score > 0 else entry["low"]
        lines.append(f"{entry['name']}: {behavior}")
    return bullet(lines)


def activated_axioms(axioms: list[dict[str, Any]]) -> str:
    return bullet(
        [f"{axiom['id']} {axiom['title']}: {axiom['prompt_rule']}" for axiom in axioms]
    )


def axiom_conditions(axiom: dict[str, Any]) -> list[dict[str, Any]]:
    activation = axiom.get("activation", {})
    return activation.get("all", []) + activation.get("any", [])


def axiom_dimensions(axiom: dict[str, Any]) -> set[str]:
    return {cond["dimension"] for cond in axiom_conditions(axiom)}


def axiom_signal_strength(axiom: dict[str, Any], scores: dict[str, int]) -> tuple[int, int, str]:
    values = [abs(scores.get(cond["dimension"], 0)) for cond in axiom_conditions(axiom)]
    if not values:
        return (0, 0, axiom["id"])
    return (max(values), sum(values), axiom["id"])


def strongest_axioms(
    axioms: list[dict[str, Any]], scores: dict[str, int], limit: int = 5
) -> list[dict[str, Any]]:
    return sorted(
        axioms,
        key=lambda axiom: axiom_signal_strength(axiom, scores),
        reverse=True,
    )[:limit]


def fragment_axioms(
    axioms: list[dict[str, Any]], scores: dict[str, int], limit: int = 12
) -> list[dict[str, Any]]:
    priority_dims = {
        "privacy",
        "memory",
        "stake_alignment",
        "normal_path",
        "proactivity",
        "writing_voice",
        "taste",
        "novelty",
    }
    priority = [
        axiom
        for axiom in axioms
        if axiom_dimensions(axiom) & priority_dims
    ]
    selected = strongest_axioms(priority, scores, limit=min(8, limit))
    selected_ids = {axiom["id"] for axiom in selected}
    remaining = [axiom for axiom in axioms if axiom["id"] not in selected_ids]
    selected.extend(strongest_axioms(remaining, scores, limit=limit - len(selected)))
    return selected


def prompt_fragment(axioms: list[dict[str, Any]], scores: dict[str, int]) -> str:
    lines = [
        "Use this profile as task-behavior defaults, not as a personality description.",
        "Resolve conflicts in this order: current user instruction, task risk, task mode, then profile default.",
        "Do not infer personality, values, profession, psychology, or sensitive traits from these preferences.",
    ]
    for axiom in fragment_axioms(axioms, scores):
        lines.append(axiom["prompt_rule"])
    return bullet(lines)


def score(scores: dict[str, int], key: str) -> int:
    return scores.get(key, 0)


def collaboration_defaults(scores: dict[str, int]) -> str:
    lines = []
    if score(scores, "autonomy") >= 4:
        lines.append("Proceed when the task is clear and reversible; ask before irreversible external actions.")
    elif score(scores, "autonomy") <= -2:
        lines.append("Offer options or a short plan before acting on open-ended tasks.")
    if score(scores, "clarification") >= 3:
        lines.append("Clarify ambiguous requirements before implementing.")
    elif score(scores, "clarification") <= 0 and score(scores, "autonomy") >= 2:
        lines.append("Use explicit assumptions instead of pausing for minor ambiguity.")
    if score(scores, "verification") >= 4:
        lines.append("Treat verification as part of the deliverable, not an optional appendix.")
    if score(scores, "stake_alignment") >= 3:
        lines.append("For high-stakes work, run a grill-me pass before action: prior, uncertainty, failure condition, and success standard.")
    if score(scores, "challenge") >= 3 or score(scores, "directness") >= 4:
        lines.append("Push back on weak assumptions with concrete risks and a better path.")
    if score(scores, "proactivity") >= 4:
        lines.append("After solving the requested task, suggest the smallest useful next move.")
    elif score(scores, "proactivity") <= -3:
        lines.append("Stop at the requested deliverable unless asked to expand scope.")
    return bullet(lines)


def coding_mode(scores: dict[str, int]) -> str:
    lines = []
    if score(scores, "code_action") >= 4:
        lines.append("Read the relevant code, patch directly, and verify before reporting back.")
    elif score(scores, "code_action") <= -2:
        lines.append("Explain the approach or options before editing code.")
    if score(scores, "source_truth") >= 5:
        lines.append("Identify the source of truth first: repository, tests, logs, deploy state, or issue tracker.")
    if score(scores, "normal_path") >= 4:
        lines.append("If the default path is broken, name it as a system issue even when a workaround exists.")
    if score(scores, "planning_depth") >= 4:
        lines.append("For architecture-heavy work, compare approaches before implementation.")
    if score(scores, "cost_time") <= -3:
        lines.append("Prefer the cheapest meaningful check and ask before launching long or expensive runs.")
    elif score(scores, "cost_time") >= 4:
        lines.append("Use deeper verification or parallel agents when quality depends on it.")
    return bullet(lines)


def writing_mode(scores: dict[str, int]) -> str:
    lines = []
    if score(scores, "writing_voice") >= 4:
        lines.append("Make the point of view digestible while preserving the user's voice, rhythm, judgment, and trail.")
    elif score(scores, "writing_voice") <= -2:
        lines.append("Rewrite freely when clarity, audience fit, or persuasion improves.")
    if score(scores, "brevity") >= 4:
        lines.append("Lead with the point and avoid low-density background.")
    if score(scores, "structure") >= 4:
        lines.append("Use headings, bullets, and explicit verdicts when they reduce decision load.")
    return bullet(lines)


def research_mode(scores: dict[str, int]) -> str:
    lines = []
    if score(scores, "evidence") >= 4:
        lines.append("Use primary sources or direct artifacts for claims that affect a decision.")
    if score(scores, "fact_guess_plan") >= 3:
        lines.append("Separate observed facts, inference, and next checks.")
    if score(scores, "source_truth") >= 5:
        lines.append("Name the authority before summarizing: paper, repo, docs, logs, or source material.")
    if score(scores, "brevity") >= 4:
        lines.append("Compress known background and focus on judgment-changing deltas: new distinctions, mechanisms, boundaries, failure modes, trade-offs, or better compression.")
    if score(scores, "evidence") >= 4 and score(scores, "brevity") >= 2:
        lines.append("Park interesting but non-decision material as a trail instead of deep-reading it by default.")
    return bullet(lines)


def creative_mode(scores: dict[str, int]) -> str:
    lines = []
    if score(scores, "novelty") >= 4:
        lines.append("Bring new directions, but make them concrete enough to accept or reject.")
    elif score(scores, "taste") >= 4:
        lines.append("Stay within the brief, but make sharper execution choices inside that boundary.")
    else:
        lines.append("Stay close to the brief unless asked to explore.")
    if score(scores, "taste") >= 4:
        lines.append("Make sharper taste calls: audience, mood, constraints, and style defaults.")
    if score(scores, "novelty") >= 3 or score(scores, "taste") >= 4:
        lines.append("In divergent phases, present 4-9 concrete directions as a grid before narrowing.")
    if score(scores, "mode_separation") >= 4:
        lines.append("Do not reuse code/research communication defaults for creative ideation.")
    return bullet(lines)


def decision_policy(scores: dict[str, int]) -> str:
    lines = []
    if score(scores, "speed") >= 4:
        lines.append("Prefer a usable first artifact when rollback is cheap.")
    elif score(scores, "speed") <= -2:
        lines.append("Prefer completeness and coherence over a fast rough artifact.")
    if score(scores, "risk") >= 3:
        lines.append("Bold options are acceptable when failure is reversible and visible.")
    else:
        lines.append("Keep changes conservative around money, privacy, external publication, deletion, and irreversible state.")
    if score(scores, "planning_depth") >= 4:
        lines.append("Spend more time on sequencing for ambiguous, architectural, or high-blast-radius tasks.")
    if score(scores, "cost_time") >= 4:
        lines.append("Longer runs, extra tools, and parallel research are acceptable when they materially improve quality.")
    elif score(scores, "cost_time") <= -3:
        lines.append("Keep the default path fast and inexpensive unless the user authorizes more effort.")
    elif score(scores, "cost_time") <= 1 and score(scores, "verification") >= 4:
        lines.append("Do not add tests for ceremony; use the cheapest check that changes confidence.")
    return bullet(lines)


def communication_rules(scores: dict[str, int]) -> str:
    lines = []
    if score(scores, "brevity") >= 4:
        lines.append("Start with the answer or recommendation; keep caveats below the main point.")
    elif score(scores, "brevity") <= -2:
        lines.append("Include enough reasoning that the user can audit the conclusion.")
    if score(scores, "directness") >= 4:
        lines.append("Use direct, specific language; avoid diplomatic filler.")
    if score(scores, "structure") >= 4:
        lines.append("Use compact structure for choices, tradeoffs, and review findings.")
    if score(scores, "handoff") >= 5:
        lines.append("After resumes or handoffs, restate what state is known before continuing.")
    elif score(scores, "handoff") <= 0 and score(scores, "autonomy") >= 3:
        lines.append("After restarts or handoffs, forward-fix from the latest visible state; pull prior context only when it materially unblocks the work.")
    if score(scores, "warmth") >= 4:
        lines.append("Use a warmer collaborator tone without losing specificity.")
    elif score(scores, "warmth") <= -3:
        lines.append("Keep the register neutral and tool-like.")
    if score(scores, "language_locale") >= 3:
        lines.append("Follow the user's current language and locale conventions by default.")
    return bullet(lines)


def known_dislikes(scores: dict[str, int]) -> str:
    lines = []
    if score(scores, "brevity") >= 4:
        lines.append("Long generic background before the actual answer.")
    if score(scores, "writing_voice") >= 4:
        lines.append("Preserving a raw point of view that remains too hard to digest.")
    if score(scores, "evidence") >= 4:
        lines.append("Unsupported claims presented as fact.")
    if score(scores, "code_action") >= 4:
        lines.append("Plans that stop short of implementation when the task is actionable.")
    if score(scores, "directness") >= 4:
        lines.append("Vague hedging when a concrete judgment is possible.")
    if score(scores, "normal_path") >= 4:
        lines.append("Workarounds treated as success while the default path remains broken.")
    if score(scores, "verification") >= 4 and score(scores, "cost_time") <= 1:
        lines.append("Testing ceremony that does not change the decision.")
    if score(scores, "privacy") >= 4:
        lines.append("Sensitive personal inference from ordinary collaboration preferences.")
    if score(scores, "proactivity") <= -3:
        lines.append("Unasked roadmap expansion after the requested task is done.")
    if score(scores, "warmth") <= -3:
        lines.append("Encouraging or emotionally warm phrasing when a neutral tool response is enough.")
    return bullet(lines)


def calibration_notes(scores: dict[str, int], dims: dict[str, dict[str, str]]) -> str:
    uncertain = [
        dims.get(dim, {}).get("name", dim)
        for dim, value in sorted(scores.items())
        if abs(value) <= 1
    ][:6]
    lines = [
        "Treat this profile as candidate guidance until corrected in real tasks.",
        "Promote a preference only after repeated corrections or explicit user confirmation.",
    ]
    if uncertain:
        lines.append("Low-signal dimensions to re-test: " + ", ".join(uncertain) + ".")
    return bullet(lines)


def memory_boundaries(scores: dict[str, int]) -> str:
    lines = []
    if score(scores, "memory") >= 4:
        lines.append("Remember durable collaboration preferences when the user explicitly confirms them or repeats them across tasks.")
        lines.append("Treat loaded memory as recall hints, not facts; re-check memory when it affects a decision.")
    else:
        lines.append("Default to session-local preferences unless the user asks to remember them.")
    if score(scores, "privacy") >= 4:
        lines.append("Do not store sensitive traits, diagnoses, politics, values, or personal life inferences.")
    else:
        lines.append("Store operational preferences only: communication, review, coding, writing, and creative defaults.")
    return bullet(lines)


def summary(scores: dict[str, int], dims: dict[str, dict[str, str]], axioms: list[dict[str, Any]]) -> str:
    strongest = top_dimensions(scores, dims, limit=5)
    readable = ", ".join(
        f"{dims.get(dim, {}).get('name', dim)} {read}" for dim, _score, read in strongest
    )
    axiom_ids_list = [axiom["id"] for axiom in axioms]
    if len(axiom_ids_list) > 10:
        axiom_ids = ", ".join(axiom_ids_list[:10]) + f", ... ({len(axiom_ids_list)} total)"
    else:
        axiom_ids = ", ".join(axiom_ids_list) or "none"
    return (
        "This profile describes operational collaboration defaults inferred from the "
        f"questionnaire. Highest-scoring operational defaults: {readable}. Activated axiom candidates: "
        f"{axiom_ids}. Use it as a starting contract, then revise from real corrections."
    )


def render_profile(
    scores: dict[str, int],
    catalog: dict[str, Any],
    template: str,
) -> str:
    dims = dimension_lookup(catalog)
    axioms = selected_axioms(catalog, scores)
    replacements = {
        "summary": summary(scores, dims, axioms),
        "score_snapshot": score_snapshot(scores, dims),
        "prompt_fragment": prompt_fragment(axioms, scores),
        "strong_defaults": strong_defaults(scores, dims),
        "activated_axioms": activated_axioms(axioms),
        "collaboration_defaults": collaboration_defaults(scores),
        "coding_mode": coding_mode(scores),
        "writing_mode": writing_mode(scores),
        "research_mode": research_mode(scores),
        "creative_mode": creative_mode(scores),
        "decision_policy": decision_policy(scores),
        "communication_rules": communication_rules(scores),
        "known_dislikes": known_dislikes(scores),
        "calibration_notes": calibration_notes(scores, dims),
        "memory_boundaries": memory_boundaries(scores),
    }

    output = template
    for key, value in replacements.items():
        output = output.replace(f"{{{{{key}}}}}", value)
    return output.rstrip() + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate a whoami collaboration identity prompt.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent(
            """\
            Answer file shape:
              {
                "questionnaire_id": "whoami-v0.2",
                "answers": {"q01": "b", "q02": "c", "...": "..."}
              }
            """
        ),
    )
    parser.add_argument("--answers", required=True, type=Path, help="Path to answer JSON.")
    parser.add_argument("--out", type=Path, help="Path to write generated Markdown profile.")
    parser.add_argument("--questionnaire", default=DEFAULT_QUESTIONNAIRE, type=Path)
    parser.add_argument("--catalog", default=DEFAULT_CATALOG, type=Path)
    parser.add_argument("--template", default=DEFAULT_TEMPLATE, type=Path)
    parser.add_argument("--json", action="store_true", help="Print scores and activated axioms as JSON.")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    questionnaire = load_json(args.questionnaire)
    catalog = load_json(args.catalog)
    answer_doc = load_json(args.answers)
    scores = score_answers(questionnaire, catalog, answer_doc)

    if args.json:
        print(
            json.dumps(
                {
                    "scores": scores,
                    "activated_axioms": [a["id"] for a in selected_axioms(catalog, scores)],
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return

    template = args.template.read_text(encoding="utf-8")
    profile = render_profile(scores, catalog, template)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(profile, encoding="utf-8")
    else:
        print(profile, end="")


if __name__ == "__main__":
    main()
