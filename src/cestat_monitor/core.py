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
from typing import Any
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader

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


def normalize(value: str) -> str:
    value = value.replace("\ufffd", "-").replace("\u00ad", "")
    value = re.sub(r"[\u2010-\u2015\u2212]", "-", value)
    return " ".join(value.casefold().split())


def match_text(text: str, keywords: list[str], exclusions: list[str], page_number: int) -> list[dict[str, Any]]:
    normalized_text = normalize(text)
    normalized_exclusions = [normalize(item) for item in exclusions]
    matches: list[dict[str, Any]] = []
    for keyword in keywords:
        needle = normalize(keyword)
        if not needle:
            continue
        pattern = re.compile(r"(?<!\w)" + re.escape(needle) + r"(?!\w)")
        for occurrence in pattern.finditer(normalized_text):
            position = occurrence.start()
            if any(normalized_text.startswith(exclusion, position) for exclusion in normalized_exclusions):
                continue
            snippet = normalized_text[max(0, position - 100):occurrence.end() + 100]
            matches.append({"keyword": keyword, "page": page_number, "snippet": snippet})
    return matches


def load_config(path: Path, override: str = "") -> tuple[list[str], list[str]]:
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MonitorError(f"Could not read keyword configuration: {exc}") from exc
    keywords = [line.strip() for line in (override.splitlines() if override.strip() else config.get("keywords", [])) if line.strip()]
    exclusions = [str(item).strip() for item in config.get("excluded_phrases", []) if str(item).strip()]
    if not keywords:
        raise MonitorError("No keywords configured. Add keywords to config/keywords.json or the workflow input.")
    return keywords, exclusions


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
            token = soup.select_one('input[name="csrf_token"]')
            if not form or not token or not token.get("value"):
                raise MonitorError("CESTAT did not return the expected search form or CSRF token.")
            data: dict[str, str] = {}
            for field in form.select('input[type="hidden"][name]'):
                data[field["name"]] = field.get("value", "")
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
                result_type = "supplementary" if "supplement" in heading else "regular"
                for row in table.select("tr"):
                    cells = [" ".join(cell.get_text(" ", strip=True).split()) for cell in row.select("th, td")]
                    link = row.select_one('a[href*="/openfilec/"]')
                    if not link:
                        continue
                    url = urljoin(BASE_URL, link["href"])
                    pdf_id = url.rstrip("/").split("/")[-1]
                    pdfs.append(PdfRecord(requested.strftime(DATE_FORMAT), bench.name, bench.value, pdf_id, url, result_type, cells[2] if len(cells) > 2 else ""))
            unique = {record.url: record for record in pdfs}
            return SearchResult(requested.strftime(DATE_FORMAT), bench, "success_with_links" if unique else "success_empty", list(unique.values()))
        except Exception as exc:
            message = str(exc)
            logging.error("%s, %s: %s", requested.strftime(DATE_FORMAT), bench.name, message)
            return SearchResult(requested.strftime(DATE_FORMAT), bench, "failed", error=message)


def extract_and_match(record: PdfRecord, keywords: list[str], exclusions: list[str], client: CestatClient) -> PdfRecord:
    try:
        session = requests.Session()
        session.headers.update({"User-Agent": USER_AGENT, "Referer": BASE_URL, "Accept": "application/pdf,text/html;q=0.9,*/*;q=0.8"})
        response = client._request(session, "GET", record.url)
        content = response.content
        if not content.startswith(b"%PDF-"):
            raise MonitorError("CESTAT returned non-PDF content")
        reader = PdfReader(__import__("io").BytesIO(content))
        record.pages = len(reader.pages)
        for page_number, page in enumerate(reader.pages, 1):
            text = page.extract_text() or ""
            if text.strip():
                record.text_status = "text_extracted"
            record.matches.extend(match_text(text, keywords, exclusions, page_number))
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


def reset_pdf_for_retry(record: PdfRecord) -> None:
    record.status = "found"
    record.error = ""
    record.pages = 0
    record.text_status = "not_processed"
    record.matches.clear()


def _badge(label: str, kind: str) -> str:
    return f'<span class="badge badge-{kind}">{html.escape(label)}</span>'


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
    stats = {"total_pdfs": 0, "matched_pdfs": 0, "warning_count": 0, "failed_pdfs": 0, "failed_searches": 0}
    for result in payload["results"]:
        if result.get("status") == "failed":
            stats["failed_searches"] += 1
        for pdf in result.get("pdfs", []):
            stats["total_pdfs"] += 1
            if pdf.get("matches"):
                stats["matched_pdfs"] += 1
            if pdf.get("status") == "failed":
                stats["failed_pdfs"] += 1
            if pdf.get("error") or pdf.get("text_status") != "text_extracted":
                stats["warning_count"] += 1
    return stats


def _render_pdf_detail_rows(result: dict[str, Any]) -> str:
    rows: list[str] = []
    for pdf in result.get("pdfs", []):
        has_matches = bool(pdf.get("matches"))
        has_failures = pdf.get("status") == "failed"
        row_class = "pdf-row"
        if has_matches:
            row_class += " has-matches"
        if has_failures:
            row_class += " has-failures"
        match_count = len(pdf.get("matches", []))
        keywords = ", ".join(sorted({m["keyword"] for m in pdf.get("matches", [])}))
        uploaded = format_ist_datetime(pdf.get("uploaded_at"))
        status_label = _friendly_label(pdf.get("status", ""))
        text_label = _friendly_label(pdf.get("text_status", ""))
        type_label = _friendly_label(pdf.get("result_type", ""))
        match_cell = f'<span class="count-badge match">{match_count}</span>' if match_count else "—"
        rows.append(
            f'<tr class="{row_class}" data-has-matches="{1 if has_matches else 0}" data-has-failures="{1 if has_failures else 0}">'
            f'<td class="col-expand"><button type="button" class="expand-btn child-expand" aria-expanded="false" aria-label="Show match details" {"disabled" if not has_matches else ""}><span class="chev" aria-hidden="true"></span></button></td>'
            f'<td data-label="PDF"><a class="pdf-link" href="{html.escape(pdf["url"])}" target="_blank" rel="noopener noreferrer">{html.escape(pdf["pdf_id"])}</a></td>'
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
                f'<li><span class="match-kw">{html.escape(m["keyword"])}</span><span class="match-page">page {m["page"]}</span>'
                f'<p class="snippet">{html.escape(m.get("snippet", ""))}</p></li>'
                for m in pdf.get("matches", [])
            )
            rows.append(
                f'<tr class="detail-row child-detail" hidden>'
                f'<td colspan="9"><div class="detail-panel match-panel"><h4>Matches · PDF {html.escape(pdf["pdf_id"])}</h4><ul class="match-list">{match_items}</ul></div></td>'
                "</tr>"
            )
        if pdf.get("error"):
            rows.append(
                f'<tr class="detail-row error-detail" data-has-failures="1">'
                f'<td colspan="9"><div class="detail-panel error-panel">{html.escape(pdf["error"])}</div></td>'
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
        search_label = _friendly_label(result.get("status", ""))
        match_cell = f'<span class="count-badge match">{result_matches}</span>' if result_matches else '<span class="count-zero">0</span>'
        fail_cell = f'<span class="count-badge fail">{result_failures}</span>' if result_failures else '<span class="count-zero">0</span>'
        rows.append(
            f'<tr class="{row_class}" data-group="{group_id}" data-sort-date="{sort_date}" data-has-matches="{1 if has_matches else 0}" data-has-failures="{1 if has_failures else 0}" data-date="{html.escape(result["requested_date"])}" data-bench="{html.escape(result["bench"]["name"])}">'
            f'<td class="col-expand"><button type="button" class="expand-btn group-expand" aria-expanded="false" aria-controls="{group_id}" aria-label="Expand {html.escape(result["requested_date"])} {html.escape(result["bench"]["name"])}"><span class="chev" aria-hidden="true"></span></button></td>'
            f'<td data-label="Date (IST)" class="date-cell"><time datetime="{sort_date}">{html.escape(result["requested_date"])}</time></td>'
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
                '<th class="col-expand" aria-hidden="true"></th><th>PDF</th><th>Type</th><th>Uploaded (IST)</th><th>Status</th><th>Text</th><th>Pages</th><th>Matches</th><th>Keywords</th>'
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
    return assemble_html_report(payload, stats, _render_search_group_rows(payload), generated_at_ist)



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
                match_rows.append(
                    f"- **{result['requested_date']} {result['bench']['name']}** · PDF [{pdf['pdf_id']}]({pdf['url']}) · {keywords}\n  page {first['page']}: {snippet}"
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
    lines.append(f"Keywords: {', '.join(payload['keywords'])}")
    return "\n".join(lines)

def run(start: date, keywords: list[str], exclusions: list[str], bench_limit: int | None = None, days: int = 7, output_dir: Path = Path("output")) -> dict[str, Any]:
    client = CestatClient()
    benches = client.discover_benches()
    if bench_limit is not None:
        benches = benches[:bench_limit]
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
        extract_and_match(pdf, keywords, exclusions, client)
        time.sleep(REQUEST_PAUSE_SECONDS)
    retryable_pdfs = [pdf for pdf in all_pdfs if should_retry_pdf(pdf)]
    if retryable_pdfs:
        logging.info("Retrying %d transiently failed PDF(s) sequentially", len(retryable_pdfs))
    for pdf in retryable_pdfs:
        time.sleep(REQUEST_PAUSE_SECONDS * 2)
        reset_pdf_for_retry(pdf)
        extract_and_match(pdf, keywords, exclusions, client)
        time.sleep(REQUEST_PAUSE_SECONDS)
    results.sort(key=lambda item: (parse_report_date(item.requested_date), item.bench.name.casefold()))
    payload = {"generated_at": now_ist().isoformat(timespec="seconds"), "start_date": start.strftime(DATE_FORMAT), "end_date": (start + timedelta(days=days - 1)).strftime(DATE_FORMAT), "days": days, "keywords": keywords, "excluded_phrases": exclusions, "benches": [asdict(bench) for bench in benches], "results": [asdict(result) for result in results]}
    generate_report(payload, output_dir)
    return payload
