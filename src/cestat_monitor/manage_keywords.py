from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .keywords import (
    KeywordConfigError,
    KeywordRule,
    parse_exclusion_input,
    parse_keyword_input,
    rules_from_config_dict,
)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise KeywordConfigError(f"Could not read {path}: {exc}") from exc


def _write_config(path: Path, rules: list[KeywordRule], exclusions: list[str]) -> None:
    if not rules:
        raise KeywordConfigError("Cannot save an empty keyword list. Add at least one keyword or use 'reset'.")
    payload = {
        "entities": [rule.to_dict() for rule in rules],
        "excluded_phrases": exclusions,
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _format_rule_short(rule: KeywordRule) -> str:
    parts = [rule.label]
    if rule.exclude_nearby:
        parts.append(f"(ignoring near: {', '.join(rule.exclude_nearby)})")
    if rule.mode == "exact":
        parts.append("[exact]")
    return " ".join(parts)


def apply_keyword_action(
    config_path: Path,
    default_path: Path,
    action: str,
    keywords_raw: str = "",
    exclusions_raw: str = "",
) -> dict[str, Any]:
    action_norm = action.strip().lower()
    alias_map = {
        "add": "add",
        "add to defaults": "add",
        "remove": "remove",
        "remove from defaults": "remove",
        "replace": "replace",
        "replace all defaults": "replace",
        "reset": "reset",
        "reset to factory defaults": "reset",
        "view": "view",
        "view current defaults": "view",
    }
    op = alias_map.get(action_norm)
    if op is None:
        raise KeywordConfigError(f"Unknown action '{action}'.")

    before_data = _read_json(config_path)
    before_rules = rules_from_config_dict(before_data)
    before_exclusions = [str(x).strip() for x in before_data.get("excluded_phrases", []) if str(x).strip()]

    if op == "view":
        return {
            "action": op,
            "changed": False,
            "before_rules": before_rules,
            "before_exclusions": before_exclusions,
            "after_rules": before_rules,
            "after_exclusions": before_exclusions,
        }

    if op == "reset":
        default_data = _read_json(default_path)
        after_rules = rules_from_config_dict(default_data)
        after_exclusions = [str(x).strip() for x in default_data.get("excluded_phrases", []) if str(x).strip()]
        _write_config(config_path, after_rules, after_exclusions)
        return {
            "action": op,
            "changed": (before_rules != after_rules or before_exclusions != after_exclusions),
            "before_rules": before_rules,
            "before_exclusions": before_exclusions,
            "after_rules": after_rules,
            "after_exclusions": after_exclusions,
        }

    new_rules = parse_keyword_input(keywords_raw)
    new_exclusions = parse_exclusion_input(exclusions_raw)

    if op == "add":
        if not new_rules and not new_exclusions:
            raise KeywordConfigError("Nothing to add. Enter at least one keyword or exclusion.")
        merged_rules = list(before_rules)
        by_label = {r.label.casefold(): idx for idx, r in enumerate(merged_rules)}
        for nr in new_rules:
            key = nr.label.casefold()
            if key in by_label:
                idx = by_label[key]
                existing = merged_rules[idx]
                merged_ex = tuple(dict.fromkeys((*existing.exclude_nearby, *nr.exclude_nearby)))
                merged_rules[idx] = KeywordRule(
                    label=existing.label,
                    aliases=existing.aliases,
                    proximity_terms=existing.proximity_terms,
                    mode=nr.mode if nr.mode == "exact" else existing.mode,
                    case_sensitive=existing.case_sensitive or nr.case_sensitive,
                    confidence=existing.confidence,
                    exclude_nearby=merged_ex,
                )
            else:
                by_label[key] = len(merged_rules)
                merged_rules.append(nr)

        merged_exclusions = list(before_exclusions)
        ex_seen = {e.casefold() for e in merged_exclusions}
        for ex in new_exclusions:
            if ex.casefold() not in ex_seen:
                ex_seen.add(ex.casefold())
                merged_exclusions.append(ex)

        _write_config(config_path, merged_rules, merged_exclusions)
        after_rules, after_exclusions = merged_rules, merged_exclusions

    elif op == "remove":
        if not new_rules and not new_exclusions:
            raise KeywordConfigError("Nothing to remove. Enter at least one keyword or exclusion.")
        remove_labels = {r.label.casefold() for r in new_rules}
        after_rules = [
            r
            for r in before_rules
            if r.label.casefold() not in remove_labels
            and not any(a.casefold() in remove_labels for a in r.aliases)
        ]
        remove_ex = {e.casefold() for e in new_exclusions}
        after_exclusions = [e for e in before_exclusions if e.casefold() not in remove_ex]
        if not after_rules:
            raise KeywordConfigError(
                "Removing those keywords would leave 0 keywords. Use 'replace' or 'reset' instead."
            )
        _write_config(config_path, after_rules, after_exclusions)

    elif op == "replace":
        if not new_rules:
            raise KeywordConfigError("Replace requires at least one keyword.")
        after_rules = new_rules
        after_exclusions = new_exclusions if new_exclusions else before_exclusions
        _write_config(config_path, after_rules, after_exclusions)

    return {
        "action": op,
        "changed": (before_rules != after_rules or before_exclusions != after_exclusions),
        "before_rules": before_rules,
        "before_exclusions": before_exclusions,
        "after_rules": after_rules,
        "after_exclusions": after_exclusions,
    }


def format_summary_markdown(result: dict[str, Any]) -> str:
    lines = [
        f"## Keyword Configuration ({result['action'].title()})",
        "",
        f"**Changed:** {'Yes' if result['changed'] else 'No (already up to date)'}",
        "",
        "### Active Default Keywords",
        "",
    ]
    for rule in result["after_rules"]:
        lines.append(f"- {_format_rule_short(rule)}")
    lines.extend(["", "### Global Exclusions", ""])
    if result["after_exclusions"]:
        lines.append(", ".join(f"`{ex}`" for ex in result["after_exclusions"]))
    else:
        lines.append("None")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage default CESTAT monitor keywords")
    parser.add_argument("--action", required=True, help="add, remove, replace, reset, or view")
    parser.add_argument("--keywords", default="", help="Comma or newline separated keywords")
    parser.add_argument("--exclusions", default="", help="Comma or newline separated exclusions")
    parser.add_argument("--config", type=Path, default=Path("config/keywords.json"))
    parser.add_argument("--default-config", type=Path, default=Path("config/keywords.default.json"))
    parser.add_argument("--summary-file", type=Path, help="Optional markdown summary output file")
    args = parser.parse_args()

    try:
        result = apply_keyword_action(
            config_path=args.config,
            default_path=args.default_config,
            action=args.action,
            keywords_raw=args.keywords,
            exclusions_raw=args.exclusions,
        )
        md = format_summary_markdown(result)
        print(md)
        if args.summary_file:
            with args.summary_file.open("a", encoding="utf-8") as fh:
                fh.write("\n" + md + "\n")
        return 0
    except KeywordConfigError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        if args.summary_file:
            with args.summary_file.open("a", encoding="utf-8") as fh:
                fh.write(f"\n## Keyword Update Failed\n\n> {exc}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
