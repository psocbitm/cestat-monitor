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

import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader

BASE_URL = "https://cestat.gov.in/viewcauselist"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/139 Safari/537.36 cestat-monitor/0.1"
DATE_FORMAT = "%d-%m-%Y"
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
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Asia/Kolkata")).date()
    try:
        return datetime.strptime(value.strip(), DATE_FORMAT).date()
    except ValueError as exc:
        raise MonitorError(f"Invalid start date '{value}'. Use DD-MM-YYYY, for example 01-09-2026.") from exc


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


def generate_report(payload: dict[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "results.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    rows: list[str] = []
    total_pdfs = 0
    matched_pdfs = 0
    warning_count = 0
    failed_pdfs = 0
    for result in payload["results"]:
        result_pdfs = result.get("pdfs", [])
        result_matches = sum(bool(pdf.get("matches")) for pdf in result_pdfs)
        result_failures = sum(pdf.get("status") == "failed" for pdf in result_pdfs) + (1 if result.get("error") else 0)
        total_pdfs += len(result_pdfs)
        matched_pdfs += result_matches
        failed_pdfs += result_failures
        warning_count += sum(bool(pdf.get("error") or pdf.get("text_status") != "text_extracted") for pdf in result_pdfs)
        section_class = (" has-matches" if result_matches else " no-matches") + (" has-failures" if result_failures else "")
        rows.append(f"<section class=\"result{section_class}\"><h2>{html.escape(result['requested_date'])} <span>{html.escape(result['bench']['name'])}</span></h2><p>Status: <b>{html.escape(result['status'])}</b> · {result_matches} matching PDF(s) · {len(result_pdfs)} PDF(s) checked · {result_failures} failure(s)</p>")
        if result.get("error"):
            rows.append(f"<p class=error>{html.escape(result['error'])}</p>")
        for pdf in result.get("pdfs", []):
            pdf_class = (" has-matches" if pdf.get("matches") else " no-matches") + (" has-failures" if pdf.get("status") == "failed" else "")
            rows.append(f"<article class=\"pdf{pdf_class}\"><h3><a href=\"{html.escape(pdf['url'])}\">PDF {html.escape(pdf['pdf_id'])}</a></h3><p>{html.escape(pdf['status'])} · {html.escape(pdf['text_status'])}</p>")
            for match in pdf.get("matches", []):
                rows.append(f"<p><strong>{html.escape(match['keyword'])}</strong>, page {match['page']}: {html.escape(match['snippet'])}</p>")
            if pdf.get("error"): rows.append(f"<p class=error>{html.escape(pdf['error'])}</p>")
            rows.append("</article>")
        rows.append("</section>")
    page = """<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"><title>CESTAT keyword report</title><style>
    :root{font:16px system-ui,sans-serif;color:#17202a;background:#eef3f7}body{max-width:1100px;margin:0 auto;padding:1rem}header{background:#123047;color:#fff;padding:1.5rem;border-radius:10px;margin-bottom:1rem}h1{margin:.1rem 0 .6rem;font-size:1.7rem}h2{margin:0;font-size:1.15rem}h2 span{font-weight:400;color:#536675}section,article{background:#fff;padding:1rem;margin:1rem 0;border:1px solid #d5e0e8;border-radius:8px}section{border-left:5px solid #9aabb7}section.has-matches{border-left-color:#16805c}section.has-failures,article.has-failures{border-left-color:#c2410c;border-color:#f0b58f}article{margin:.7rem 0;background:#f9fbfc}article.has-matches{border-color:#8bd2b6}.toolbar{display:flex;gap:1rem;align-items:center;flex-wrap:wrap;background:#fff;padding:1rem;border:1px solid #d5e0e8;border-radius:8px}.toolbar label{font-weight:650;cursor:pointer}.stats{display:flex;gap:1rem;flex-wrap:wrap;color:#d8edf4}.stat b{color:#fff;font-size:1.25rem}.error{color:#a33;background:#fff0f0;padding:.5rem;border-radius:5px}a{color:#075985;font-weight:650}small{color:#536675}.hidden{display:none!important}</style></head><body><header><h1>CESTAT keyword report</h1><p>""" + html.escape(payload["start_date"]) + " to " + html.escape(payload["end_date"]) + " · Generated " + html.escape(payload["generated_at"]) + "</p><div class=stats><span class=stat><b>""" + str(matched_pdfs) + "</b> matching PDFs</span><span class=stat><b>""" + str(total_pdfs) + "</b> PDFs checked</span><span class=stat><b>""" + str(failed_pdfs) + "</b> failures</span><span class=stat><b>""" + str(warning_count) + "</b> warnings</span></div></header><div class=toolbar><label><input id=matches-only type=checkbox> Show only PDFs with matches (failures stay visible)</label><small id=filter-summary></small></div><p><strong>Keywords:</strong> """ + html.escape(", ".join(payload["keywords"])) + "</p>" + "".join(rows) + "<script>const toggle=document.querySelector('#matches-only');const sections=[...document.querySelectorAll('section.result')];const summary=document.querySelector('#filter-summary');function applyFilter(){const only=toggle.checked;let shown=0;sections.forEach(section=>{const matches=section.classList.contains('has-matches');const failures=section.classList.contains('has-failures');section.classList.toggle('hidden',only&&!matches&&!failures);if(matches||failures)shown+=1;section.querySelectorAll('article.pdf').forEach(pdf=>pdf.classList.toggle('hidden',only&&!pdf.classList.contains('has-matches')&&!pdf.classList.contains('has-failures')))});summary.textContent=only?shown+' result(s) with matches or failures shown':'All date/bench results shown'}toggle.addEventListener('change',applyFilter);applyFilter()</script></body></html>"
    (output_dir / "index.html").write_text(page, encoding="utf-8")


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
    results.sort(key=lambda item: (item.requested_date, item.bench.name))
    payload = {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"), "start_date": start.strftime(DATE_FORMAT), "end_date": (start + timedelta(days=days - 1)).strftime(DATE_FORMAT), "days": days, "keywords": keywords, "excluded_phrases": exclusions, "benches": [asdict(bench) for bench in benches], "results": [asdict(result) for result in results]}
    generate_report(payload, output_dir)
    return payload
