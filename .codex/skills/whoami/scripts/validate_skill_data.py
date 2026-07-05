#!/usr/bin/env python3
"""Validate whoami questionnaire, axiom catalog, and optional answer files."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


SKILL_ROOT = Path(__file__).resolve().parents[1]
QUESTIONNAIRE = SKILL_ROOT / "references" / "questionnaire-v0.2.json"
CATALOG = SKILL_ROOT / "references" / "axiom-catalog-v0.2.json"


def load_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        raise SystemExit(f"Missing file: {path}")
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON in {path}: {exc}")


def require(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def unique_ids(items: list[dict[str, Any]], key: str, label: str, errors: list[str]) -> set[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for item in items:
        value = item.get(key)
        if not isinstance(value, str):
            errors.append(f"{label} has missing/non-string {key}: {item!r}")
            continue
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    if duplicates:
        errors.append(f"{label} duplicate {key}: {', '.join(sorted(duplicates))}")
    return seen


def validate_catalog(catalog: dict[str, Any], errors: list[str]) -> set[str]:
    dimensions = catalog.get("dimensions")
    axioms = catalog.get("axioms")
    require(isinstance(dimensions, list), "catalog.dimensions must be a list", errors)
    require(isinstance(axioms, list), "catalog.axioms must be a list", errors)
    if not isinstance(dimensions, list) or not isinstance(axioms, list):
        return set()

    dimension_ids = unique_ids(dimensions, "id", "dimension", errors)
    unique_ids(axioms, "id", "axiom", errors)

    for dimension in dimensions:
        dim_id = dimension.get("id", "<missing>")
        for field in ("name", "low", "high"):
            require(isinstance(dimension.get(field), str), f"dimension {dim_id} missing {field}", errors)

    for axiom in axioms:
        axiom_id = axiom.get("id", "<missing>")
        require(isinstance(axiom.get("title"), str), f"axiom {axiom_id} missing title", errors)
        require(isinstance(axiom.get("prompt_rule"), str), f"axiom {axiom_id} missing prompt_rule", errors)
        activation = axiom.get("activation")
        require(isinstance(activation, dict), f"axiom {axiom_id} activation must be an object", errors)
        if not isinstance(activation, dict):
            continue
        groups = [name for name in ("all", "any") if name in activation]
        require(bool(groups), f"axiom {axiom_id} activation must contain all or any", errors)
        for group in groups:
            conditions = activation.get(group)
            require(isinstance(conditions, list), f"axiom {axiom_id} activation.{group} must be a list", errors)
            if not isinstance(conditions, list):
                continue
            for index, condition in enumerate(conditions):
                dim = condition.get("dimension") if isinstance(condition, dict) else None
                require(dim in dimension_ids, f"axiom {axiom_id} condition {index} uses unknown dimension {dim!r}", errors)
                has_bound = isinstance(condition, dict) and ("min" in condition or "max" in condition)
                require(has_bound, f"axiom {axiom_id} condition {index} must define min or max", errors)
    return dimension_ids


def validate_questionnaire(questionnaire: dict[str, Any], dimension_ids: set[str], errors: list[str]) -> set[str]:
    questions = questionnaire.get("questions")
    require(isinstance(questionnaire.get("questionnaire_id"), str), "questionnaire_id must be a string", errors)
    require(isinstance(questions, list), "questionnaire.questions must be a list", errors)
    if not isinstance(questions, list):
        return set()

    question_ids = unique_ids(questions, "id", "question", errors)
    for question in questions:
        qid = question.get("id", "<missing>")
        require(isinstance(question.get("prompt"), str), f"question {qid} missing prompt", errors)
        options = question.get("options")
        require(isinstance(options, dict), f"question {qid} options must be an object", errors)
        if not isinstance(options, dict):
            continue
        require(set(options) == {"a", "b", "c", "d"}, f"question {qid} options must be exactly a,b,c,d", errors)
        for option_id, option in options.items():
            label = f"question {qid} option {option_id}"
            require(isinstance(option.get("label"), str), f"{label} missing label", errors)
            weights = option.get("weights", {})
            require(isinstance(weights, dict), f"{label} weights must be an object", errors)
            if not isinstance(weights, dict):
                continue
            for dim, weight in weights.items():
                require(dim in dimension_ids, f"{label} uses unknown dimension {dim!r}", errors)
                require(isinstance(weight, int), f"{label} weight for {dim!r} must be an integer", errors)
    return question_ids


def validate_answers(path: Path, questionnaire: dict[str, Any], question_ids: set[str], errors: list[str]) -> None:
    doc = load_json(path)
    label = str(path)
    require(doc.get("questionnaire_id") == questionnaire.get("questionnaire_id"), f"{label} questionnaire_id mismatch", errors)
    answers = doc.get("answers")
    require(isinstance(answers, dict), f"{label} answers must be an object", errors)
    if not isinstance(answers, dict):
        return

    missing = sorted(question_ids - set(answers))
    extra = sorted(set(answers) - question_ids)
    if missing:
        errors.append(f"{label} missing answers: {', '.join(missing)}")
    if extra:
        errors.append(f"{label} has extra answers: {', '.join(extra)}")

    questions_by_id = {question["id"]: question for question in questionnaire["questions"]}
    for qid, answer in answers.items():
        if qid not in questions_by_id:
            continue
        options = questions_by_id[qid]["options"]
        require(answer in options, f"{label} invalid answer for {qid}: {answer!r}", errors)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate whoami skill data files.")
    parser.add_argument("--questionnaire", type=Path, default=QUESTIONNAIRE)
    parser.add_argument("--catalog", type=Path, default=CATALOG)
    parser.add_argument("--answers", type=Path, nargs="*", default=[])
    args = parser.parse_args()

    errors: list[str] = []
    catalog = load_json(args.catalog)
    questionnaire = load_json(args.questionnaire)
    dimension_ids = validate_catalog(catalog, errors)
    question_ids = validate_questionnaire(questionnaire, dimension_ids, errors)
    for answers_path in args.answers:
        validate_answers(answers_path, questionnaire, question_ids, errors)

    if errors:
        print("whoami skill data is invalid:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("whoami skill data is valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
