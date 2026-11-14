from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

DEFAULT_CONTEXT_WINDOW = 80
DEFAULT_PROXIMITY_WINDOW = 120
_SEP_CLASS = r"[\s.\-/,:;]+"
_FLEX_SEP_CLASS = r"[\s.\-/,:;]*"
_EX_SEP_CLASS = r"[\s.\-/,:;()\[\]]+"


class KeywordConfigError(ValueError):
    """Raised when keyword configuration or user input is invalid."""


def clean_text(value: str) -> str:
    """Normalize PDF-extracted text and repair line-wrap hyphenation."""
    if not value:
        return ""
    value = value.replace("\ufffd", "-").replace("\u00ad", "")
    value = re.sub(r"[\u2010-\u2015\u2212]", "-", value)
    value = re.sub(r"[\u2018\u2019]", "'", value)
    value = re.sub(r"[\u201c\u201d]", '"', value)
    # Rejoin lowercase line-wrap hyphenation (e.g. "Anti-\ndumping" -> "Antidumping")
    value = re.sub(r"([A-Za-z])-\s*\r?\n\s*([a-z])", r"\1\2", value)
    # Separate capitalized line-wrap hyphenation (e.g. "Shree-\nCement" -> "Shree Cement")
    value = re.sub(r"([A-Za-z])-\s*\r?\n\s*([A-Z])", r"\1 \2", value)
    return " ".join(value.split())


def normalize(value: str) -> str:
    return clean_text(value).casefold()


@dataclass(frozen=True)
class KeywordRule:
    label: str
    aliases: tuple[str, ...] = ()
    proximity_terms: tuple[str, ...] = ()
    mode: str = "smart"
    case_sensitive: bool = False
    confidence: str = "high"
    exclude_nearby: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "label": self.label,
            "mode": self.mode,
            "confidence": self.confidence,
        }
        if self.proximity_terms:
            data["proximity_terms"] = list(self.proximity_terms)
        else:
            data["aliases"] = list(self.aliases or (self.label,))
        if self.case_sensitive:
            data["case_sensitive"] = True
        if self.exclude_nearby:
            data["exclude_nearby"] = list(self.exclude_nearby)
        return data


@dataclass(frozen=True)
class _CompiledExclusion:
    text: str
    pattern: re.Pattern[str]


@dataclass(frozen=True)
class _CompiledRule:
    rule: KeywordRule
    alias_patterns: tuple[re.Pattern[str], ...]
    proximity_patterns: tuple[re.Pattern[str], ...]
    nearby_exclusions: tuple[_CompiledExclusion, ...]


def _split_outside_quotes(raw: str) -> list[str]:
    """Split text on commas or newlines outside double quotes."""
    items: list[str] = []
    buf: list[str] = []
    in_quotes = False
    for ch in raw:
        if ch == '"':
            in_quotes = not in_quotes
            buf.append(ch)
        elif not in_quotes and ch in ",\r\n":
            chunk = "".join(buf).strip()
            if chunk:
                items.append(chunk)
            buf.clear()
        else:
            buf.append(ch)
    tail = "".join(buf).strip()
    if tail:
        items.append(tail)
    return items


def _tokenize_expression(expr: str) -> list[tuple[str, bool]]:
    """Return list of (token, was_quoted) pairs respecting double quotes."""
    tokens: list[tuple[str, bool]] = []
    i = 0
    n = len(expr)
    while i < n:
        while i < n and expr[i].isspace():
            i += 1
        if i >= n:
            break
        if expr[i] == '"':
            end = expr.find('"', i + 1)
            if end == -1:
                tokens.append((expr[i + 1 :].strip(), True))
                break
            tokens.append((expr[i + 1 : end].strip(), True))
            i = end + 1
        elif expr[i] == "-" and i + 1 < n and expr[i + 1] == '"':
            end = expr.find('"', i + 2)
            if end == -1:
                tokens.append(("-" + expr[i + 2 :].strip(), True))
                break
            tokens.append(("-" + expr[i + 2 : end].strip(), True))
            i = end + 1
        else:
            start = i
            while i < n and not expr[i].isspace():
                i += 1
            if i > start:
                tokens.append((expr[start:i], False))
    return [(tok, quoted) for tok, quoted in tokens if tok]


def parse_keyword_input(
    raw: str,
    default_mode: str = "smart",
    case_sensitive: bool = False,
) -> list[KeywordRule]:
    """Parse user-friendly comma/newline keyword mini-syntax into KeywordRules."""
    if not raw or not raw.strip():
        return []
    rules: list[KeywordRule] = []
    seen_keys: set[tuple[Any, ...]] = set()
    for item in _split_outside_quotes(raw):
        tokens = _tokenize_expression(item)
        if not tokens:
            continue
        positive: list[tuple[str, bool]] = []
        excludes: list[str] = []
        for tok, quoted in tokens:
            if tok.startswith("-") and len(tok) > 1:
                ex_val = clean_text(tok[1:])
                if ex_val:
                    excludes.append(ex_val)
            elif tok == "-":
                continue
            else:
                positive.append((tok, quoted))
        if not positive:
            raise KeywordConfigError(
                f"Found exclusion '{item}' without a keyword. Include a keyword before '-word'."
            )

        has_plus = any((not quoted and "+" in tok) for tok, quoted in positive)
        if has_plus:
            terms: list[str] = []
            current: list[str] = []
            for tok, quoted in positive:
                if quoted:
                    current.append(tok)
                    continue
                parts = tok.split("+")
                for idx, part in enumerate(parts):
                    if part:
                        current.append(part)
                    if idx < len(parts) - 1:
                        joined = clean_text(" ".join(current))
                        if joined:
                            terms.append(joined)
                        current.clear()
            tail = clean_text(" ".join(current))
            if tail:
                terms.append(tail)
            if len(terms) < 2:
                raise KeywordConfigError(
                    f"Proximity rule '{item}' needs at least two terms around '+' (for example: Shree + Cement)."
                )
            label = " + ".join(terms)
            rule = KeywordRule(
                label=label,
                proximity_terms=tuple(terms),
                mode=default_mode,
                case_sensitive=case_sensitive,
                confidence="high",
                exclude_nearby=tuple(dict.fromkeys(excludes)),
            )
        else:
            is_single_quoted = len(positive) == 1 and positive[0][1]
            phrase = clean_text(" ".join(tok for tok, _ in positive))
            if not phrase:
                continue
            rule_mode = "exact" if is_single_quoted else default_mode
            rule = KeywordRule(
                label=phrase,
                aliases=(phrase,),
                mode=rule_mode,
                case_sensitive=case_sensitive,
                confidence="high" if len(phrase.split()) > 1 or len(phrase) >= 5 else "standard",
                exclude_nearby=tuple(dict.fromkeys(excludes)),
            )
        key = (
            rule.label.casefold(),
            rule.aliases,
            rule.proximity_terms,
            rule.mode,
            rule.case_sensitive,
            rule.exclude_nearby,
        )
        if key not in seen_keys:
            seen_keys.add(key)
            rules.append(rule)
    return rules


def parse_exclusion_input(raw: str) -> list[str]:
    """Parse comma/newline exclusion input into a deduplicated list of phrases."""
    if not raw or not raw.strip():
        return []
    seen: set[str] = set()
    result: list[str] = []
    for item in _split_outside_quotes(raw):
        cleaned = clean_text(item.strip('"').lstrip("-").strip())
        if cleaned and cleaned.casefold() not in seen:
            seen.add(cleaned.casefold())
            result.append(cleaned)
    return result


def _compile_phrase_pattern(phrase: str, mode: str, case_sensitive: bool) -> re.Pattern[str] | None:
    cleaned = clean_text(phrase)
    if not cleaned:
        return None
    flags = 0 if case_sensitive else re.IGNORECASE
    if mode == "exact":
        escaped = r"\s+".join(re.escape(part) for part in cleaned.split())
        return re.compile(r"(?<!\w)" + escaped + r"(?!\w)", flags)

    tokens = [tok for tok in re.split(_SEP_CLASS, cleaned) if tok]
    if not tokens:
        return None
    parts: list[str] = []
    for idx, tok in enumerate(tokens):
        low = tok.casefold()
        is_last = idx == len(tokens) - 1
        if is_last and low in ("ltd", "limited"):
            parts.append(r"(?:ltd|limited)\.?")
        elif is_last and low in ("pvt", "private"):
            parts.append(r"(?:pvt|private)\.?")
        else:
            parts.append(re.escape(tok))
    body = _FLEX_SEP_CLASS.join(parts)
    return re.compile(r"(?<!\w)" + body + r"(?!\w)", flags)


def _compile_exclusion_pattern(phrase: str) -> _CompiledExclusion | None:
    words = [w for w in re.split(_EX_SEP_CLASS, clean_text(phrase)) if w]
    if not words:
        return None
    pattern = re.compile(
        r"(?<!\w)" + _EX_SEP_CLASS.join(re.escape(w) for w in words) + r"(?!\w)",
        re.IGNORECASE,
    )
    return _CompiledExclusion(text=clean_text(phrase), pattern=pattern)


def compile_matcher(
    rules: Iterable[KeywordRule | str],
    exclusions: Iterable[str] = (),
    default_mode: str = "smart",
    case_sensitive: bool = False,
    context_window: int = DEFAULT_CONTEXT_WINDOW,
    proximity_window: int = DEFAULT_PROXIMITY_WINDOW,
) -> "CompiledMatcher":
    compiled_rules: list[_CompiledRule] = []
    for item in rules:
        if isinstance(item, str):
            parsed = parse_keyword_input(item, default_mode=default_mode, case_sensitive=case_sensitive)
            for rule in parsed:
                compiled_rules.append(_compile_single_rule(rule))
        else:
            compiled_rules.append(_compile_single_rule(item))

    compiled_exclusions: list[_CompiledExclusion] = []
    for ex in exclusions:
        ce = _compile_exclusion_pattern(ex)
        if ce is not None:
            compiled_exclusions.append(ce)
    return CompiledMatcher(
        rules=tuple(compiled_rules),
        global_exclusions=tuple(compiled_exclusions),
        context_window=context_window,
        proximity_window=proximity_window,
    )


def _compile_single_rule(rule: KeywordRule) -> _CompiledRule:
    alias_patterns: list[re.Pattern[str]] = []
    if not rule.proximity_terms:
        sources = rule.aliases or (rule.label,)
        for alias in sources:
            pat = _compile_phrase_pattern(alias, rule.mode, rule.case_sensitive)
            if pat is not None:
                alias_patterns.append(pat)

    prox_patterns: list[re.Pattern[str]] = []
    for term in rule.proximity_terms:
        pat = _compile_phrase_pattern(term, rule.mode, rule.case_sensitive)
        if pat is not None:
            prox_patterns.append(pat)

    nearby_exclusions: list[_CompiledExclusion] = []
    for ex in rule.exclude_nearby:
        ce = _compile_exclusion_pattern(ex)
        if ce is not None:
            nearby_exclusions.append(ce)

    return _CompiledRule(
        rule=rule,
        alias_patterns=tuple(alias_patterns),
        proximity_patterns=tuple(prox_patterns),
        nearby_exclusions=tuple(nearby_exclusions),
    )


def _extract_snippet(display_text: str, start: int, end: int, radius: int = 110) -> str:
    n = len(display_text)
    raw_start = max(0, start - radius)
    raw_end = min(n, end + radius)
    if raw_start > 0:
        space_left = display_text.rfind(" ", 0, raw_start)
        if space_left != -1 and raw_start - space_left <= 25:
            raw_start = space_left + 1
        else:
            space_right = display_text.find(" ", raw_start, start)
            if space_right != -1:
                raw_start = space_right + 1
    if raw_end < n:
        space_right = display_text.find(" ", raw_end)
        if space_right != -1 and space_right - raw_end <= 25:
            raw_end = space_right
        else:
            space_left = display_text.rfind(" ", end, raw_end)
            if space_left != -1:
                raw_end = space_left
    snippet = display_text[raw_start:raw_end].strip()
    if raw_start > 0:
        snippet = "…" + snippet
    if raw_end < n:
        snippet = snippet + "…"
    return snippet


def _find_proximity_hits(
    display_text: str,
    patterns: tuple[re.Pattern[str], ...],
    max_window: int,
) -> list[tuple[int, int, tuple[str, ...]]]:
    if len(patterns) < 2:
        return []
    term_spans: list[list[tuple[int, int, str]]] = []
    for pat in patterns:
        spans = [(m.start(), m.end(), m.group(0)) for m in pat.finditer(display_text)]
        if not spans:
            return []
        term_spans.append(spans)

    hits: list[tuple[int, int, tuple[str, ...]]] = []
    used_spans: list[tuple[int, int]] = []
    for anchor_start, anchor_end, anchor_text in term_spans[0]:
        chosen: list[tuple[int, int, str]] = [(anchor_start, anchor_end, anchor_text)]
        ok = True
        for other_spans in term_spans[1:]:
            best: tuple[int, int, str] | None = None
            best_dist = max_window + 1
            for s, e, txt in other_spans:
                span_lo = min(anchor_start, s)
                span_hi = max(anchor_end, e)
                if span_hi - span_lo <= max_window:
                    dist = abs(s - anchor_start)
                    if dist < best_dist:
                        best_dist = dist
                        best = (s, e, txt)
            if best is None:
                ok = False
                break
            chosen.append(best)
        if not ok:
            continue
        lo = min(s for s, _, _ in chosen)
        hi = max(e for _, e, _ in chosen)
        if any(lo < u_hi and u_lo < hi for u_lo, u_hi in used_spans):
            continue
        used_spans.append((lo, hi))
        matched_texts = tuple(dict.fromkeys(txt for _, _, txt in sorted(chosen, key=lambda c: c[0])))
        hits.append((lo, hi, matched_texts))
    return hits


class CompiledMatcher:
    def __init__(
        self,
        rules: tuple[_CompiledRule, ...],
        global_exclusions: tuple[_CompiledExclusion, ...],
        context_window: int = DEFAULT_CONTEXT_WINDOW,
        proximity_window: int = DEFAULT_PROXIMITY_WINDOW,
    ):
        self.rules = rules
        self.global_exclusions = global_exclusions
        self.context_window = context_window
        self.proximity_window = proximity_window

    def match_page(
        self,
        text: str,
        page_number: int,
        suppressions: dict[str, int] | None = None,
    ) -> list[dict[str, Any]]:
        display_text = clean_text(text)
        if not display_text:
            return []

        global_spans: list[tuple[int, int, str]] = []
        for ex in self.global_exclusions:
            for m in ex.pattern.finditer(display_text):
                global_spans.append((m.start(), m.end(), ex.text))

        candidates: list[tuple[int, int, KeywordRule, tuple[str, ...]]] = []
        for compiled in self.rules:
            rule = compiled.rule
            nearby_spans: list[tuple[int, int, str]] = []
            if compiled.nearby_exclusions:
                for ex in compiled.nearby_exclusions:
                    for m in ex.pattern.finditer(display_text):
                        nearby_spans.append((m.start(), m.end(), ex.text))

            raw_hits: list[tuple[int, int, tuple[str, ...]]] = []
            if compiled.proximity_patterns:
                raw_hits.extend(
                    _find_proximity_hits(display_text, compiled.proximity_patterns, self.proximity_window)
                )
            else:
                for pat in compiled.alias_patterns:
                    for m in pat.finditer(display_text):
                        raw_hits.append((m.start(), m.end(), (m.group(0),)))

            for start, end, matched_texts in raw_hits:
                blocked_by = self._find_blocking_exclusion(start, end, global_spans, nearby_spans)
                if blocked_by is not None:
                    if suppressions is not None:
                        key = f"{rule.label} (near {blocked_by})"
                        suppressions[key] = suppressions.get(key, 0) + 1
                    continue
                candidates.append((start, end, rule, matched_texts))

        candidates.sort(key=lambda item: (item[0], -(item[1] - item[0]), item[2].label.casefold()))
        kept: list[tuple[int, int, KeywordRule, tuple[str, ...]]] = []
        for start, end, rule, matched_texts in candidates:
            if any(k_start <= start and end <= k_end for k_start, k_end, _, _ in kept):
                continue
            kept.append((start, end, rule, matched_texts))

        matches: list[dict[str, Any]] = []
        for start, end, rule, matched_texts in kept:
            snippet = _extract_snippet(display_text, start, end)
            matches.append(
                {
                    "keyword": rule.label,
                    "page": page_number,
                    "snippet": snippet,
                    "confidence": rule.confidence,
                    "matched_texts": list(matched_texts),
                }
            )
        return matches

    def _find_blocking_exclusion(
        self,
        start: int,
        end: int,
        global_spans: list[tuple[int, int, str]],
        nearby_spans: list[tuple[int, int, str]],
    ) -> str | None:
        win_start = max(0, start - self.context_window)
        win_end = end + self.context_window
        for ex_start, ex_end, ex_label in global_spans:
            if start < ex_end and ex_start < end:
                return ex_label
            if ex_start < win_end and win_start < ex_end:
                return ex_label
        for ex_start, ex_end, ex_label in nearby_spans:
            if ex_start < win_end and win_start < ex_end:
                return ex_label
        return None


def rules_from_config_dict(
    config: dict[str, Any],
    default_mode: str = "smart",
    case_sensitive: bool = False,
) -> list[KeywordRule]:
    """Load KeywordRules from either structured 'entities' or legacy 'keywords' list."""
    rules: list[KeywordRule] = []
    entities = config.get("entities")
    if isinstance(entities, list) and entities:
        for entry in entities:
            if isinstance(entry, str):
                rules.extend(parse_keyword_input(entry, default_mode=default_mode, case_sensitive=case_sensitive))
                continue
            if not isinstance(entry, dict):
                continue
            label = clean_text(str(entry.get("label", "")))
            if not label:
                continue
            prox = tuple(clean_text(str(t)) for t in entry.get("proximity_terms", []) if clean_text(str(t)))
            aliases = tuple(clean_text(str(a)) for a in entry.get("aliases", []) if clean_text(str(a)))
            if not prox and not aliases:
                aliases = (label,)
            mode = str(entry.get("mode", default_mode)).lower()
            if mode not in ("smart", "exact"):
                mode = default_mode
            confidence = str(entry.get("confidence", "high")).lower()
            if confidence not in ("high", "standard"):
                confidence = "high"
            cs = bool(entry.get("case_sensitive", case_sensitive))
            exclude_nearby = tuple(
                clean_text(str(ex)) for ex in entry.get("exclude_nearby", []) if clean_text(str(ex))
            )
            rules.append(
                KeywordRule(
                    label=label,
                    aliases=aliases,
                    proximity_terms=prox,
                    mode=mode,
                    case_sensitive=cs,
                    confidence=confidence,
                    exclude_nearby=exclude_nearby,
                )
            )
    for kw in config.get("keywords", []):
        rules.extend(parse_keyword_input(str(kw), default_mode=default_mode, case_sensitive=case_sensitive))
    return rules
