from __future__ import annotations

import html
import json
import logging
import random
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader

from .keywords import (
    CompiledMatcher,
    KeywordConfigError,
    KeywordRule,
    clean_text,
    compile_matcher,
    normalize,
    parse_exclusion_input,
    parse_keyword_input,
    rules_from_config_dict,
)

BASE_URL = "https://cestat.gov.in/viewcauselist"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/139 Safari/537.36 cestat-monitor/0.1"
DATE_FORMAT = "%d-%m-%Y"
IST = ZoneInfo("Asia/Kolkata")
IST_DATETIME_FORMAT = "%d-%m-%Y %H:%M:%S IST"
REQUEST_PAUSE_SECONDS = 1.0
TRANSIENT_ERROR_MARKERS = ("timed out", "connection", "429", "500", "502", "503", "504")


class MonitorError(RuntimeError):
    pass


@dataclass(frozen=True)
class Bench:
    value: str
    name: str


@dataclass
class PdfRecord:
    date: str
    bench: str
    bench_id: str
    pdf_id: str
    url: str
    result_type: str
    uploaded_at: str = ""
    bench_type: str = ""
    status: str = "found"
    error: str = ""
    pages: int = 0
    text_status: str = "not_processed"
    matches: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class SearchResult:
    requested_date: str
    bench: Bench
    status: str
    pdfs: list[PdfRecord] = field(default_factory=list)
    error: str = ""


def parse_start_date(value: str | None) -> date:
    if not value:
        return datetime.now(IST).date()
    try:
        return datetime.strptime(value.strip(), DATE_FORMAT).date()
    except ValueError as exc:
        raise MonitorError(f"Invalid start date '{value}'. Use DD-MM-YYYY, for example 01-09-2026.") from exc


def parse_report_date(value: str) -> date:
    return datetime.strptime(value.strip(), DATE_FORMAT).date()


def now_ist() -> datetime:
    return datetime.now(IST)


def format_ist_datetime(value: str | datetime | None, assume_ist_if_naive: bool = True) -> str:
    if not value:
        return "—"
    if isinstance(value, datetime):
        dt = value
    else:
        text = value.strip()
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            for fmt in ("%Y-%m-%d %H:%M:%S", "%d-%m-%Y %H:%M:%S"):
                try:
                    dt = datetime.strptime(text, fmt)
                    break
                except ValueError:
                    dt = None
            if dt is None:
                return text
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST if assume_ist_if_naive else None)
    else:
        dt = dt.astimezone(IST)
    return dt.strftime(IST_DATETIME_FORMAT)


def sort_report_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(
        results,
        key=lambda result: (parse_report_date(result["requested_date"]), result["bench"]["name"].casefold()),
    )
    for result in ordered:
        result["pdfs"] = sorted(
            result.get("pdfs", []),
            key=lambda pdf: (pdf.get("uploaded_at") or "", pdf.get("pdf_id", "")),
        )
    return ordered


def _friendly_label(value: str) -> str:
    return {
        "success_with_links": "Links found",
        "success_empty": "No PDFs",
        "checked_no_match": "No match",
        "matched": "Matched",
        "failed": "Failed",
        "text_extracted": "Text OK",
        "empty_text_possible_scan": "Scan / empty",
        "not_processed": "Not processed",
        "regular": "Regular",
        "supplementary": "Supplementary",
    }.get(value, value.replace("_", " "))


def date_range(start: date, days: int = 7) -> list[date]:
    return [start + timedelta(days=offset) for offset in range(days)]


def match_text(
    text: str,
    keywords: Sequence[str | KeywordRule],
    exclusions: list[str],
    page_number: int,
) -> list[dict[str, Any]]:
    matcher = compile_matcher(keywords, exclusions)
    return matcher.match_page(text, page_number)


def _consolidate_pdf_matches(raw_matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    pages_by_key: dict[tuple[str, str], list[int]] = {}
    for item in raw_matches:
        key = (item["keyword"].casefold(), normalize(item.get("snippet", "")))
        page = int(item["page"])
        if key not in grouped:
            entry: dict[str, Any] = {
                "keyword": item["keyword"],
                "page": page,
                "snippet": item.get("snippet", ""),
            }
            if item.get("confidence"):
                entry["confidence"] = item["confidence"]
            if item.get("matched_texts"):
                entry["matched_texts"] = list(item["matched_texts"])
            grouped[key] = entry
            pages_by_key[key] = [page]
        else:
            if page not in pages_by_key[key]:
                pages_by_key[key].append(page)
            if item.get("matched_texts"):
                existing_texts = grouped[key].setdefault("matched_texts", [])
                for txt in item["matched_texts"]:
                    if txt not in existing_texts:
                        existing_texts.append(txt)
    consolidated: list[dict[str, Any]] = []
    for key, entry in grouped.items():
        pages = pages_by_key[key]
        if len(pages) > 1:
            entry["pages"] = pages
        consolidated.append(entry)
    return consolidated


def _merge_rules(base: list[KeywordRule], extra: list[KeywordRule]) -> list[KeywordRule]:
    merged = list(base)
    by_label = {r.label.casefold(): idx for idx, r in enumerate(merged)}
    for nr in extra:
        key = nr.label.casefold()
        if key in by_label:
            idx = by_label[key]
            existing = merged[idx]
            merged_ex = tuple(dict.fromkeys((*existing.exclude_nearby, *nr.exclude_nearby)))
            merged[idx] = KeywordRule(
                label=existing.label,
                aliases=existing.aliases,
                proximity_terms=existing.proximity_terms,
                mode=nr.mode if nr.mode == "exact" else existing.mode,
                case_sensitive=existing.case_sensitive or nr.case_sensitive,
                confidence=existing.confidence,
                exclude_nearby=merged_ex,
            )
        else:
            by_label[key] = len(merged)
            merged.append(nr)
    return merged


def load_config(
    path: Path,
    override: str = "",
    keyword_mode: str = "replace",
    extra_exclusions: str = "",
    match_style: str = "smart",
) -> tuple[list[KeywordRule], list[str]]:
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MonitorError(f"Could not read keyword configuration: {exc}") from exc

    style_norm = match_style.strip().lower()
    use_exact = style_norm.startswith("exact")
    default_mode = "exact" if use_exact else "smart"
    case_sensitive = use_exact

    try:
        base_rules = rules_from_config_dict(
            config,
            default_mode=default_mode,
            case_sensitive=case_sensitive,
        )
        if use_exact:
            base_rules = [
                KeywordRule(
                    label=r.label,
                    aliases=r.aliases,
                    proximity_terms=r.proximity_terms,
                    mode="exact",
                    case_sensitive=True,
                    confidence=r.confidence,
                    exclude_nearby=r.exclude_nearby,
                )
                for r in base_rules
            ]
        base_exclusions = [str(item).strip() for item in config.get("excluded_phrases", []) if str(item).strip()]
        added_exclusions = parse_exclusion_input(extra_exclusions)
        exclusions = list(dict.fromkeys([*base_exclusions, *added_exclusions]))

        if override.strip():
            override_rules = parse_keyword_input(
                override,
                default_mode=default_mode,
                case_sensitive=case_sensitive,
            )
            mode_norm = keyword_mode.strip().lower()
            if mode_norm.startswith("add"):
                rules = _merge_rules(base_rules, override_rules)
            else:
                rules = override_rules
        else:
            rules = base_rules
    except KeywordConfigError as exc:
        raise MonitorError(str(exc)) from exc

    if not rules:
        raise MonitorError("No keywords configured. Add keywords to config/keywords.json or the workflow input.")
    return rules, exclusions


class CestatClient:
    def __init__(self, timeout: tuple[float, float] = (20, 60), attempts: int = 4, backoff: float = 1.5):
        self.timeout = timeout
        self.attempts = attempts
        self.backoff = backoff

    def _request(self, session: requests.Session, method: str, url: str, **kwargs: Any) -> requests.Response:
        last_error: Exception | None = None
        for attempt in range(1, self.attempts + 1):
            try:
                response = session.request(method, url, timeout=self.timeout, **kwargs)
                if response.status_code == 429 or response.status_code >= 500:
                    if attempt == self.attempts:
                        response.raise_for_status()
                    retry_after = response.headers.get("Retry-After")
                    delay = float(retry_after) if retry_after and retry_after.isdigit() else self.backoff ** attempt + random.random()
                    logging.warning("CESTAT returned HTTP %s; retry %d/%d in %.1fs", response.status_code, attempt, self.attempts - 1, delay)
                    time.sleep(delay)
                    continue
                response.raise_for_status()
                return response
            except (requests.RequestException, ValueError) as exc:
                last_error = exc
                if attempt == self.attempts:
                    break
                delay = self.backoff ** attempt + random.random()
                logging.warning("Request failed (%s); retry %d/%d in %.1fs", exc, attempt, self.attempts - 1, delay)
                time.sleep(delay)
        raise MonitorError(f"Request failed after {self.attempts} attempts: {last_error}")

    def discover_benches(self) -> list[Bench]:
        session = requests.Session()
        session.headers.update({"User-Agent": USER_AGENT})
        response = self._request(session, "GET", BASE_URL)
        soup = BeautifulSoup(response.text, "html.parser")
        options = soup.select('select[name="schemas"] option')
        benches: list[Bench] = []
        seen: set[str] = set()
        for option in options:
            value = (option.get("value") or "").strip()
            name = " ".join(option.get_text(" ", strip=True).split())
            if value and name and not option.has_attr("disabled") and value not in seen:
                benches.append(Bench(value, name))
                seen.add(value)
        if not benches:
            raise MonitorError("CESTAT returned no available benches; the page structure may have changed.")
        logging.info("Discovered %d benches: %s", len(benches), ", ".join(bench.name for bench in benches))
        return benches

    def search(self, requested: date, bench: Bench) -> SearchResult:
        session = requests.Session()
        session.headers.update({"User-Agent": USER_AGENT, "Referer": BASE_URL})
        try:
            initial = self._request(session, "GET", BASE_URL)
            soup = BeautifulSoup(initial.text, "html.parser")
            form = soup.select_one("form#cauform")
            if not form:
                raise MonitorError("CESTAT did not return the expected search form or CSRF token.")
            data: dict[str, str] = {}
            for field in form.select('input[type="hidden"][name]'):
                data[field["name"]] = field.get("value", "")
            csrf_token = next(
                (value.strip() for name, value in data.items() if "csrf" in name.casefold() and value.strip()),
                "",
            )
            if not csrf_token:
                meta_csrf = soup.select_one('meta[name*="csrf" i][content], meta[name="X-CSRF-TOKEN"][content]')
                if meta_csrf and (meta_csrf.get("content") or "").strip():
                    meta_name = (meta_csrf.get("name") or "").strip()
                    csrf_field = "csrf_test_name" if meta_name.upper() == "X-CSRF-TOKEN" else meta_name
                    csrf_token = meta_csrf["content"].strip()
                    data[csrf_field] = csrf_token
            if not csrf_token:
                raise MonitorError("CESTAT did not return the expected search form or CSRF token.")
            data.update({"schemas": bench.value, "from": requested.strftime(DATE_FORMAT), "captcha_code": "111111"})
            action = urljoin(BASE_URL, form.get("action") or BASE_URL)
            response = self._request(session, "POST", action, data=data)
            result_soup = BeautifulSoup(response.text, "html.parser")
            body_text = normalize(result_soup.get_text(" ", strip=True))
            expected_date = normalize(requested.strftime(DATE_FORMAT))
            if expected_date not in body_text and requested.strftime("%Y-%m-%d") not in body_text:
                raise MonitorError("CESTAT response did not confirm the requested date; search contract may have changed.")
            pdfs: list[PdfRecord] = []
            for table_index, table in enumerate(result_soup.select("table")):
                heading = normalize(table.get_text(" ", strip=True))
                prev = table.find_previous_sibling()
                prev_heading = normalize(prev.get_text(" ", strip=True)) if prev else ""
                result_type = "supplementary" if ("supplemen" in heading or "supplemen" in prev_heading) else "regular"
                for row in table.select("tr"):
                    cells = [" ".join(cell.get_text(" ", strip=True).split()) for cell in row.select("th, td")]
                    link = row.select_one('a[href*="/openfilec/"]')
                    if not link:
                        continue
                    url = urljoin(BASE_URL, link["href"])
                    pdf_id = url.rstrip("/").split("/")[-1]
                    bench_type = cells[1] if len(cells) > 1 else ""
                    uploaded_at = cells[2] if len(cells) > 2 else ""
                    pdfs.append(
                        PdfRecord(
                            requested.strftime(DATE_FORMAT),
                            bench.name,
                            bench.value,
                            pdf_id,
                            url,
                            result_type,
                            uploaded_at,
                            bench_type,
                        )
                    )
            unique = {record.url: record for record in pdfs}
            return SearchResult(requested.strftime(DATE_FORMAT), bench, "success_with_links" if unique else "success_empty", list(unique.values()))
        except Exception as exc:
            message = str(exc)
            logging.error("%s, %s: %s", requested.strftime(DATE_FORMAT), bench.name, message)
            return SearchResult(requested.strftime(DATE_FORMAT), bench, "failed", error=message)


def extract_and_match(
    record: PdfRecord,
    keywords: Sequence[str | KeywordRule],
    exclusions: list[str],
    client: CestatClient,
    matcher: CompiledMatcher | None = None,
    suppressions: dict[str, int] | None = None,
) -> PdfRecord:
    try:
        session = requests.Session()
        session.headers.update({"User-Agent": USER_AGENT, "Referer": BASE_URL, "Accept": "application/pdf,text/html;q=0.9,*/*;q=0.8"})
        response = client._request(session, "GET", record.url)
        content = response.content
        if not content.startswith(b"%PDF-"):
            raise MonitorError("CESTAT returned non-PDF content")
        reader = PdfReader(__import__("io").BytesIO(content))
        record.pages = len(reader.pages)
        active_matcher = matcher or compile_matcher(keywords, exclusions)
        raw_matches: list[dict[str, Any]] = []
        for page_number, page in enumerate(reader.pages, 1):
            text = page.extract_text() or ""
            if text.strip():
                record.text_status = "text_extracted"
            raw_matches.extend(active_matcher.match_page(text, page_number, suppressions=suppressions))
        record.matches = _consolidate_pdf_matches(raw_matches)
        if record.text_status == "not_processed":
            record.text_status = "empty_text_possible_scan"
        record.status = "matched" if record.matches else "checked_no_match"
    except Exception as exc:
        record.status = "failed"
        record.error = str(exc)
        logging.error("PDF %s failed: %s", record.pdf_id, record.error)
    return record


def is_transient_error(message: str) -> bool:
    error = normalize(message)
    return any(marker in error for marker in TRANSIENT_ERROR_MARKERS)


def should_retry_pdf(record: PdfRecord) -> bool:
    return record.status == "failed" and is_transient_error(record.error)


def should_retry_search(result: SearchResult) -> bool:
    return result.status == "failed" and is_transient_error(result.error)


def reset_pdf_for_retry(record: PdfRecord) -> None:
    record.status = "found"
    record.error = ""
    record.pages = 0
    record.text_status = "not_processed"
    record.matches.clear()


def _badge(label: str, kind: str) -> str:
    return f'<span class="badge badge-{kind}">{html.escape(label)}</span>'


def _format_weekday_short(date_str: str) -> str:
    try:
        return parse_report_date(date_str).strftime("%a")
    except ValueError:
        return ""


def _highlight_snippet(snippet: str, keyword: str, matched_texts: list[str] | None = None) -> str:
    if not snippet:
        return ""
    targets = [t for t in (matched_texts or []) if t]
    if targets:
        sorted_targets = sorted(set(targets), key=len, reverse=True)
        pattern = re.compile(r"(?<!\w)(?:" + "|".join(re.escape(t) for t in sorted_targets) + r")(?!\w)", re.IGNORECASE)
    else:
        cleaned_kw = clean_text(keyword)
        if not cleaned_kw:
            return html.escape(snippet)
        pattern = re.compile(r"(?<!\w)" + re.escape(cleaned_kw) + r"(?!\w)", re.IGNORECASE)
    parts: list[str] = []
    last = 0
    for match in pattern.finditer(snippet):
        parts.append(html.escape(snippet[last:match.start()]))
        parts.append(f'<mark class="kw-hit">{html.escape(snippet[match.start():match.end()])}</mark>')
        last = match.end()
    parts.append(html.escape(snippet[last:]))
    return "".join(parts)


def _render_page_links(pdf_url: str, match: dict[str, Any]) -> str:
    pages = match.get("pages") or [match.get("page", 1)]
    links = [
        f'<a class="match-page" href="{html.escape(pdf_url)}#page={int(p)}" target="_blank" rel="noopener noreferrer" title="Open PDF at page {int(p)}">p. {int(p)} ↗</a>'
        for p in pages
    ]
    return " ".join(links)


def _pdf_match_signature(pdf: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    matches = pdf.get("matches", [])
    return tuple(sorted({(m["keyword"].casefold(), normalize(m.get("snippet", ""))) for m in matches}))


def _find_superseded_pdf_ids(result: dict[str, Any]) -> dict[str, str]:
    pdfs = result.get("pdfs", [])
    superseded: dict[str, str] = {}
    sig_to_latest: dict[tuple[tuple[str, str], ...], str] = {}
    for pdf in reversed(pdfs):
        if not pdf.get("matches"):
            continue
        sig = _pdf_match_signature(pdf)
        if not sig:
            continue
        if sig in sig_to_latest and sig_to_latest[sig] != pdf.get("pdf_id"):
            superseded[pdf.get("pdf_id", "")] = sig_to_latest[sig]
        else:
            sig_to_latest[sig] = pdf.get("pdf_id", "")
    return superseded


def _search_status_kind(status: str) -> str:
    if status == "failed":
        return "fail"
    if status == "success_empty":
        return "muted"
    return "ok"


def _pdf_status_kind(status: str) -> str:
    if status == "failed":
        return "fail"
    if status == "matched":
        return "match"
    return "ok"


def _collect_report_stats(payload: dict[str, Any]) -> dict[str, int]:
    stats = {
        "total_pdfs": 0,
        "matched_pdfs": 0,
        "total_matches": 0,
        "benches_with_matches": 0,
        "warning_count": 0,
        "failed_pdfs": 0,
        "failed_searches": 0,
    }
    matched_benches: set[str] = set()
    for result in payload["results"]:
        if result.get("status") == "failed":
            stats["failed_searches"] += 1
        for pdf in result.get("pdfs", []):
            stats["total_pdfs"] += 1
            matches = pdf.get("matches", [])
            if matches:
                stats["matched_pdfs"] += 1
                stats["total_matches"] += len(matches)
                matched_benches.add(result["bench"]["name"])
            if pdf.get("status") == "failed":
                stats["failed_pdfs"] += 1
            if pdf.get("error") or pdf.get("text_status") != "text_extracted":
                stats["warning_count"] += 1
    stats["benches_with_matches"] = len(matched_benches)
    return stats


def _render_filter_chips(payload: dict[str, Any]) -> str:
    kw_counts: dict[str, int] = {}
    bench_counts: dict[str, int] = {}
    date_counts: dict[str, int] = {}
    for result in payload["results"]:
        date_str = result["requested_date"]
        bench_name = result["bench"]["name"]
        date_counts.setdefault(date_str, 0)
        for pdf in result.get("pdfs", []):
            matches = pdf.get("matches", [])
            if not matches:
                continue
            date_counts[date_str] = date_counts.get(date_str, 0) + 1
            bench_counts[bench_name] = bench_counts.get(bench_name, 0) + 1
            for kw in {m["keyword"] for m in matches}:
                kw_counts[kw] = kw_counts.get(kw, 0) + 1

    kw_buttons = ['<button type="button" class="chip active" data-filter-type="keyword" data-filter-value="">All keywords</button>']
    for kw in payload.get("keywords", []):
        count = kw_counts.get(kw, 0)
        if count > 0:
            kw_buttons.append(
                f'<button type="button" class="chip has-hits" data-filter-type="keyword" data-filter-value="{html.escape(kw.casefold())}">'
                f'{html.escape(kw)} <span class="chip-count">{count}</span></button>'
            )
    zero_kws = [kw for kw in payload.get("keywords", []) if kw_counts.get(kw, 0) == 0]
    zero_note = (
        f'<span class="zero-kw-note" title="Checked with 0 matches">0 hits: {html.escape(", ".join(zero_kws))}</span>'
        if zero_kws
        else ""
    )

    bench_buttons = ['<button type="button" class="chip active" data-filter-type="bench" data-filter-value="">All benches</button>']
    for bench_name, count in sorted(bench_counts.items()):
        bench_buttons.append(
            f'<button type="button" class="chip has-hits" data-filter-type="bench" data-filter-value="{html.escape(bench_name.casefold())}">'
            f'{html.escape(bench_name)} <span class="chip-count">{count}</span></button>'
        )

    date_buttons = ['<button type="button" class="chip active" data-filter-type="date" data-filter-value="">All dates</button>']
    for date_str in sorted(date_counts.keys(), key=parse_report_date):
        count = date_counts[date_str]
        wk = _format_weekday_short(date_str)
        wk_label = f" ({wk})" if wk else ""
        badge = f' <span class="chip-count">{count}</span>' if count else ""
        cls = "chip has-hits" if count else "chip chip-muted"
        date_buttons.append(
            f'<button type="button" class="{cls}" data-filter-type="date" data-filter-value="{html.escape(date_str.casefold())}">'
            f'{html.escape(date_str[:5])}{html.escape(wk_label)}{badge}</button>'
        )

    return (
        '<div class="chip-bars">'
        f'<div class="chip-group"><span class="chip-label">Keyword:</span><div class="chip-list">{"".join(kw_buttons)}{zero_note}</div></div>'
        + (f'<div class="chip-group"><span class="chip-label">Bench:</span><div class="chip-list">{"".join(bench_buttons)}</div></div>' if bench_counts else "")
        + f'<div class="chip-group"><span class="chip-label">Date:</span><div class="chip-list">{"".join(date_buttons)}</div></div>'
        + "</div>"
    )


def _render_matches_feed(payload: dict[str, Any]) -> str:
    cards: list[str] = []
    for result in payload["results"]:
        date_str = result["requested_date"]
        wk = _format_weekday_short(date_str)
        bench_name = result["bench"]["name"]
        superseded_map = _find_superseded_pdf_ids(result)
        for pdf in result.get("pdfs", []):
            matches = pdf.get("matches", [])
            if not matches:
                continue
            pdf_id = pdf.get("pdf_id", "")
            pdf_url = pdf.get("url", "")
            bench_type = (pdf.get("bench_type") or "").strip()
            type_label = _friendly_label(pdf.get("result_type", ""))
            uploaded = format_ist_datetime(pdf.get("uploaded_at"))
            pages = pdf.get("pages", 0)
            kw_set = sorted({m["keyword"] for m in matches})
            kw_attr = "|".join(k.casefold() for k in kw_set)
            newer_id = superseded_map.get(pdf_id)
            superseded_badge = (
                f'<span class="badge badge-muted" title="Identical matches also appear in newer upload PDF {html.escape(newer_id)}">'
                f'Earlier upload (see PDF {html.escape(newer_id)})</span>'
                if newer_id
                else ""
            )
            division_badge = f'<span class="division-pill">{html.escape(bench_type)}</span>' if bench_type else ""
            match_items = "".join(
                f'<li class="feed-match-item" data-keyword="{html.escape(m["keyword"].casefold())}">'
                f'<div class="feed-match-top">'
                f'<span class="match-kw">{html.escape(m["keyword"])}</span>'
                f'{_badge("Broad", "muted") if m.get("confidence") == "standard" else ""}'
                f'{_render_page_links(pdf_url, m)}'
                f'</div>'
                f'<p class="snippet">{_highlight_snippet(m.get("snippet", ""), m["keyword"], m.get("matched_texts"))}</p>'
                f'</li>'
                for m in matches
            )
            cards.append(
                f'<article class="match-card{" is-superseded" if newer_id else ""}" '
                f'data-date="{html.escape(date_str.casefold())}" '
                f'data-bench="{html.escape(bench_name.casefold())}" '
                f'data-keywords="{html.escape(kw_attr)}">'
                f'<div class="match-card-head">'
                f'<div class="match-card-title">'
                f'<span class="match-date-pill">{html.escape(date_str)}{f" · {html.escape(wk)}" if wk else ""}</span>'
                f'<span class="match-bench-pill">{html.escape(bench_name)}</span>'
                f'{division_badge}'
                f'<span class="type-pill">{html.escape(type_label)}</span>'
                f'{superseded_badge}'
                f'</div>'
                f'<div class="match-card-meta">'
                f'<a class="pdf-btn" href="{html.escape(pdf_url)}" target="_blank" rel="noopener noreferrer">Open PDF {html.escape(pdf_id)} ↗</a>'
                f'<span class="meta-item">{pages} pg{"s" if pages != 1 else ""}</span>'
                f'<span class="meta-item mono" title="Uploaded timestamp (IST)">Uploaded: {html.escape(uploaded)}</span>'
                f'</div>'
                f'</div>'
                f'<ul class="match-list feed-match-list">{match_items}</ul>'
                f'</article>'
            )
    if not cards:
        return '<div class="empty-feed">No keyword matches found across any bench in this 7-day window.</div>'
    return "".join(cards)


def _render_pdf_detail_rows(result: dict[str, Any]) -> str:
    rows: list[str] = []
    superseded_map = _find_superseded_pdf_ids(result)
    for pdf in result.get("pdfs", []):
        has_matches = bool(pdf.get("matches"))
        has_failures = pdf.get("status") == "failed"
        row_class = "pdf-row"
        if has_matches:
            row_class += " has-matches"
        if has_failures:
            row_class += " has-failures"
        match_count = len(pdf.get("matches", []))
        kw_list = sorted({m["keyword"] for m in pdf.get("matches", [])})
        keywords = ", ".join(kw_list)
        kw_attr = "|".join(k.casefold() for k in kw_list)
        uploaded = format_ist_datetime(pdf.get("uploaded_at"))
        bench_type = (pdf.get("bench_type") or "").strip() or "—"
        status_label = _friendly_label(pdf.get("status", ""))
        text_label = _friendly_label(pdf.get("text_status", ""))
        type_label = _friendly_label(pdf.get("result_type", ""))
        newer_id = superseded_map.get(pdf.get("pdf_id", ""))
        superseded_note = f' <span class="badge badge-muted" title="Same matches in newer PDF {html.escape(newer_id)}">Earlier upload</span>' if newer_id else ""
        match_cell = f'<span class="count-badge match">{match_count}</span>' if match_count else "—"
        rows.append(
            f'<tr class="{row_class}" data-has-matches="{1 if has_matches else 0}" data-has-failures="{1 if has_failures else 0}" data-keywords="{html.escape(kw_attr)}">'
            f'<td class="col-expand"><button type="button" class="expand-btn child-expand" aria-expanded="false" aria-label="Show match details" {"disabled" if not has_matches else ""}><span class="chev" aria-hidden="true"></span></button></td>'
            f'<td data-label="PDF"><a class="pdf-link" href="{html.escape(pdf["url"])}" target="_blank" rel="noopener noreferrer">{html.escape(pdf["pdf_id"])} ↗</a>{superseded_note}</td>'
            f'<td data-label="Division"><span class="bench-type-cell">{html.escape(bench_type)}</span></td>'
            f'<td data-label="Type"><span class="type-pill">{html.escape(type_label)}</span></td>'
            f'<td data-label="Uploaded (IST)" class="mono">{html.escape(uploaded)}</td>'
            f'<td data-label="Status">{_badge(status_label, _pdf_status_kind(pdf.get("status", "")))}</td>'
            f'<td data-label="Text">{_badge(text_label, "muted")}</td>'
            f'<td data-label="Pages" class="num">{pdf.get("pages", 0)}</td>'
            f'<td data-label="Matches" class="num">{match_cell}</td>'
            f'<td data-label="Keywords" class="kw-cell">{html.escape(keywords) if keywords else "—"}</td>'
            "</tr>"
        )
        if has_matches:
            match_items = "".join(
                f'<li><div class="feed-match-top"><span class="match-kw">{html.escape(m["keyword"])}</span>{_badge("Broad", "muted") if m.get("confidence") == "standard" else ""}{_render_page_links(pdf["url"], m)}</div>'
                f'<p class="snippet">{_highlight_snippet(m.get("snippet", ""), m["keyword"], m.get("matched_texts"))}</p></li>'
                for m in pdf.get("matches", [])
            )
            rows.append(
                f'<tr class="detail-row child-detail" hidden>'
                f'<td colspan="10"><div class="detail-panel match-panel"><h4>Matches · PDF {html.escape(pdf["pdf_id"])} ({html.escape(bench_type)})</h4><ul class="match-list">{match_items}</ul></div></td>'
                "</tr>"
            )
        if pdf.get("error"):
            rows.append(
                f'<tr class="detail-row error-detail" data-has-failures="1">'
                f'<td colspan="10"><div class="detail-panel error-panel">{html.escape(pdf["error"])}</div></td>'
                "</tr>"
            )
    return "".join(rows)


def _render_search_group_rows(payload: dict[str, Any]) -> str:
    rows: list[str] = []
    for index, result in enumerate(payload["results"]):
        pdfs = result.get("pdfs", [])
        result_matches = sum(bool(p.get("matches")) for p in pdfs)
        pdf_failures = sum(p.get("status") == "failed" for p in pdfs)
        search_failed = result.get("status") == "failed"
        result_failures = pdf_failures + (1 if search_failed else 0)
        has_matches = result_matches > 0
        has_failures = result_failures > 0
        row_class = "group-row"
        if has_matches:
            row_class += " has-matches"
        if has_failures:
            row_class += " has-failures"
        group_id = f"group-{index}"
        sort_date = parse_report_date(result["requested_date"]).isoformat()
        wk = _format_weekday_short(result["requested_date"])
        wk_html = f' <span class="wk-pill">{html.escape(wk)}</span>' if wk else ""
        group_kws = sorted({m["keyword"].casefold() for p in pdfs for m in p.get("matches", [])})
        kw_attr = "|".join(group_kws)
        search_label = _friendly_label(result.get("status", ""))
        match_cell = f'<span class="count-badge match">{result_matches}</span>' if result_matches else '<span class="count-zero">0</span>'
        fail_cell = f'<span class="count-badge fail">{result_failures}</span>' if result_failures else '<span class="count-zero">0</span>'
        rows.append(
            f'<tr class="{row_class}" data-group="{group_id}" data-sort-date="{sort_date}" data-has-matches="{1 if has_matches else 0}" data-has-failures="{1 if has_failures else 0}" data-date="{html.escape(result["requested_date"])}" data-bench="{html.escape(result["bench"]["name"])}" data-keywords="{html.escape(kw_attr)}">'
            f'<td class="col-expand"><button type="button" class="expand-btn group-expand" aria-expanded="false" aria-controls="{group_id}" aria-label="Expand {html.escape(result["requested_date"])} {html.escape(result["bench"]["name"])}"><span class="chev" aria-hidden="true"></span></button></td>'
            f'<td data-label="Date (IST)" class="date-cell"><time datetime="{sort_date}">{html.escape(result["requested_date"])}</time>{wk_html}</td>'
            f'<td data-label="Bench"><span class="bench-name">{html.escape(result["bench"]["name"])}</span></td>'
            f'<td data-label="Search">{_badge(search_label, _search_status_kind(result.get("status", "")))}</td>'
            f'<td data-label="PDFs" class="num">{len(pdfs)}</td>'
            f'<td data-label="Matches" class="num">{match_cell}</td>'
            f'<td data-label="Failures" class="num">{fail_cell}</td>'
            "</tr>"
        )
        detail_class = "group-detail"
        if has_matches:
            detail_class += " auto-open"
        rows.append(
            f'<tr id="{group_id}" class="{detail_class}" data-parent-group="{group_id}" data-has-matches="{1 if has_matches else 0}" data-has-failures="{1 if has_failures else 0}" hidden>'
            f'<td colspan="7"><div class="nested-wrap">'
            + (f'<div class="detail-panel error-panel">{html.escape(result["error"])}</div>' if result.get("error") else "")
            + (
                '<div class="nested-scroll"><table class="nested-table responsive-table"><thead><tr>'
                '<th class="col-expand" aria-hidden="true"></th><th>PDF</th><th>Division</th><th>Type</th><th>Uploaded (IST)</th><th>Status</th><th>Text</th><th>Pages</th><th>Matches</th><th>Keywords</th>'
                "</tr></thead><tbody>"
                + _render_pdf_detail_rows(result)
                + "</tbody></table></div>"
                if pdfs
                else '<p class="empty-note">No PDFs returned for this date and bench.</p>'
            )
            + "</div></td></tr>"
        )
    return "".join(rows)


def build_html_report(payload: dict[str, Any], stats: dict[str, int]) -> str:
    from .report_page import assemble_html_report

    generated_at_ist = format_ist_datetime(payload.get("generated_at"))
    return assemble_html_report(
        payload,
        stats,
        _render_search_group_rows(payload),
        generated_at_ist,
        matches_feed_html=_render_matches_feed(payload),
        filter_chips_html=_render_filter_chips(payload),
    )



def generate_report(payload: dict[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = dict(payload)
    payload["results"] = sort_report_results(list(payload["results"]))
    (output_dir / "results.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    stats = _collect_report_stats(payload)
    (output_dir / "index.html").write_text(build_html_report(payload, stats), encoding="utf-8")
    (output_dir / "summary.md").write_text(generate_summary_markdown(payload), encoding="utf-8")


def generate_summary_markdown(payload: dict[str, Any], pages_url: str = "") -> str:
    lines = [
        "# CESTAT keyword report",
        "",
        f"**Range:** {payload['start_date']} to {payload['end_date']}",
        f"**Generated:** {format_ist_datetime(payload['generated_at'])}",
        "",
    ]
    if pages_url:
        lines.extend([f"**Full report:** [{pages_url}]({pages_url})", ""])
    total_pdfs = 0
    matched_pdfs = 0
    failed_searches = 0
    failed_pdfs = 0
    match_rows: list[str] = []
    failure_rows: list[str] = []
    for result in payload["results"]:
        if result.get("status") == "failed":
            failed_searches += 1
            failure_rows.append(f"- **{result['requested_date']} {result['bench']['name']}** (search): {result.get('error', '')}")
        for pdf in result.get("pdfs", []):
            total_pdfs += 1
            if pdf.get("status") == "failed":
                failed_pdfs += 1
                failure_rows.append(f"- **{result['requested_date']} {result['bench']['name']}** PDF {pdf['pdf_id']}: {pdf.get('error', '')}")
            if pdf.get("matches"):
                matched_pdfs += 1
                keywords = ", ".join(sorted({match["keyword"] for match in pdf["matches"]}))
                first = pdf["matches"][0]
                snippet = first.get("snippet", "").replace("\n", " ")[:160]
                bench_type = (pdf.get("bench_type") or "").strip()
                division_part = f" ({bench_type})" if bench_type else ""
                match_rows.append(
                    f"- **{result['requested_date']} {result['bench']['name']}**{division_part} · PDF [{pdf['pdf_id']}]({pdf['url']}#page={first['page']}) · {keywords}\n  page {first['page']}: {snippet}"
                )
    lines.extend([
        "## Summary",
        "",
        f"- Matching PDFs: **{matched_pdfs}**",
        f"- PDFs checked: **{total_pdfs}**",
        f"- Failed searches: **{failed_searches}**",
        f"- Failed PDFs: **{failed_pdfs}**",
        "",
    ])
    if match_rows:
        lines.extend(["## Matches", "", *match_rows, ""])
    else:
        lines.extend(["## Matches", "", "No keyword matches in this run.", ""])
    if failure_rows:
        lines.extend(["## Failures", "", *failure_rows, ""])
    lines.append(f"**Keywords:** {', '.join(payload['keywords'])}")
    if payload.get("excluded_phrases"):
        lines.append(f"**Exclusions:** {', '.join(payload['excluded_phrases'])}")
    suppressions = payload.get("suppressions") or {}
    if suppressions:
        total_suppressed = sum(suppressions.values())
        top_reasons = ", ".join(
            f"{reason}: {count}"
            for reason, count in sorted(suppressions.items(), key=lambda item: (-item[1], item[0]))[:6]
        )
        lines.append(f"**Suppressed noisy hits:** {total_suppressed} ({top_reasons})")
    return "\n".join(lines)


def _filter_benches(benches: list[Bench], bench_filter: str) -> list[Bench]:
    if not bench_filter or not bench_filter.strip():
        return benches
    tokens = [tok.strip().casefold() for tok in bench_filter.split(",") if tok.strip()]
    if not tokens or "all" in tokens:
        return benches
    matched = [
        bench
        for bench in benches
        if any(tok in bench.name.casefold() or tok == bench.value.casefold() for tok in tokens)
    ]
    if not matched:
        available = ", ".join(b.name for b in benches)
        raise MonitorError(f"No benches matched '{bench_filter}'. Available benches: {available}")
    return matched


def run(
    start: date,
    keywords: Sequence[str | KeywordRule],
    exclusions: list[str],
    bench_limit: int | None = None,
    days: int = 7,
    output_dir: Path = Path("output"),
    bench_filter: str = "",
) -> dict[str, Any]:
    client = CestatClient()
    benches = _filter_benches(client.discover_benches(), bench_filter)
    if bench_limit is not None:
        benches = benches[:bench_limit]
    matcher = compile_matcher(keywords, exclusions)
    suppressions: dict[str, int] = {}
    tasks = [(day, bench) for day in date_range(start, days) for bench in benches]
    results: list[SearchResult] = []
    for day, bench in tasks:
        logging.info("Searching %s %s", day.strftime(DATE_FORMAT), bench.name)
        results.append(client.search(day, bench))
        time.sleep(REQUEST_PAUSE_SECONDS)
    retryable_searches = [(index, day, bench) for index, (day, bench) in enumerate(tasks) if should_retry_search(results[index])]
    if retryable_searches:
        logging.info("Retrying %d transiently failed search(es) sequentially", len(retryable_searches))
    for index, day, bench in retryable_searches:
        time.sleep(REQUEST_PAUSE_SECONDS * 2)
        results[index] = client.search(day, bench)
        time.sleep(REQUEST_PAUSE_SECONDS)
    all_pdfs = [pdf for result in results for pdf in result.pdfs]
    for pdf in all_pdfs:
        extract_and_match(pdf, keywords, exclusions, client, matcher=matcher, suppressions=suppressions)
        time.sleep(REQUEST_PAUSE_SECONDS)
    retryable_pdfs = [pdf for pdf in all_pdfs if should_retry_pdf(pdf)]
    if retryable_pdfs:
        logging.info("Retrying %d transiently failed PDF(s) sequentially", len(retryable_pdfs))
    for pdf in retryable_pdfs:
        time.sleep(REQUEST_PAUSE_SECONDS * 2)
        reset_pdf_for_retry(pdf)
        extract_and_match(pdf, keywords, exclusions, client, matcher=matcher, suppressions=suppressions)
        time.sleep(REQUEST_PAUSE_SECONDS)
    results.sort(key=lambda item: (parse_report_date(item.requested_date), item.bench.name.casefold()))
    keyword_labels = [k.label if isinstance(k, KeywordRule) else str(k) for k in keywords]
    payload = {
        "generated_at": now_ist().isoformat(timespec="seconds"),
        "start_date": start.strftime(DATE_FORMAT),
        "end_date": (start + timedelta(days=days - 1)).strftime(DATE_FORMAT),
        "days": days,
        "keywords": keyword_labels,
        "excluded_phrases": exclusions,
        "suppressions": suppressions,
        "benches": [asdict(bench) for bench in benches],
        "results": [asdict(result) for result in results],
    }
    generate_report(payload, output_dir)
    return payload
