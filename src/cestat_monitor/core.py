from __future__ import annotations

import html
import json
import logging
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader

BASE_URL = "https://cestat.gov.in/viewcauselist"
USER_AGENT = "cestat-monitor/0.1 (personal research tool)"
DATE_FORMAT = "%d-%m-%Y"


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
    def __init__(self, timeout: tuple[float, float] = (15, 45), attempts: int = 3, backoff: float = 1.5):
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
        response = client._request(requests.Session(), "GET", record.url, headers={"Referer": BASE_URL})
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


def generate_report(payload: dict[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "results.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    rows: list[str] = []
    for result in payload["results"]:
        rows.append(f"<section><h2>{html.escape(result['requested_date'])} — {html.escape(result['bench']['name'])}</h2><p>Status: <b>{html.escape(result['status'])}</b></p>")
        if result.get("error"):
            rows.append(f"<p class=error>{html.escape(result['error'])}</p>")
        for pdf in result.get("pdfs", []):
            rows.append(f"<article><h3><a href=\"{html.escape(pdf['url'])}\">PDF {html.escape(pdf['pdf_id'])}</a></h3><p>{html.escape(pdf['status'])} · {html.escape(pdf['text_status'])}</p>")
            for match in pdf.get("matches", []):
                rows.append(f"<p><strong>{html.escape(match['keyword'])}</strong>, page {match['page']}: {html.escape(match['snippet'])}</p>")
            if pdf.get("error"): rows.append(f"<p class=error>{html.escape(pdf['error'])}</p>")
            rows.append("</article>")
        rows.append("</section>")
    page = "<!doctype html><meta charset=utf-8><title>CESTAT Monitor</title><style>body{font:16px system-ui;max-width:1100px;margin:2rem auto;padding:0 1rem;background:#f5f7fa;color:#17202a}section,article{background:white;padding:1rem;margin:1rem 0;border:1px solid #dce3ea;border-radius:8px}.error{color:#a33}a{color:#075985}</style><h1>CESTAT keyword report</h1><p>Generated: " + html.escape(payload["generated_at"]) + " · Range: " + html.escape(payload["start_date"]) + " to " + html.escape(payload["end_date"]) + "</p><p>Keywords: " + html.escape(", ".join(payload["keywords"])) + "</p>" + "".join(rows)
    (output_dir / "index.html").write_text(page, encoding="utf-8")


def run(start: date, keywords: list[str], exclusions: list[str], bench_limit: int | None = None, days: int = 7, output_dir: Path = Path("output")) -> dict[str, Any]:
    client = CestatClient()
    benches = client.discover_benches()
    if bench_limit is not None:
        benches = benches[:bench_limit]
    tasks = [(day, bench) for day in date_range(start, days) for bench in benches]
    results: list[SearchResult] = []
    with ThreadPoolExecutor(max_workers=min(3, max(1, len(tasks)))) as pool:
        futures = {pool.submit(client.search, day, bench): (day, bench) for day, bench in tasks}
        for future in as_completed(futures):
            results.append(future.result())
    all_pdfs = [pdf for result in results for pdf in result.pdfs]
    with ThreadPoolExecutor(max_workers=min(4, max(1, len(all_pdfs)))) as pool:
        futures = [pool.submit(extract_and_match, pdf, keywords, exclusions, client) for pdf in all_pdfs]
        for future in as_completed(futures):
            future.result()
    results.sort(key=lambda item: (item.requested_date, item.bench.name))
    payload = {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"), "start_date": start.strftime(DATE_FORMAT), "end_date": (start + timedelta(days=days - 1)).strftime(DATE_FORMAT), "days": days, "keywords": keywords, "excluded_phrases": exclusions, "benches": [asdict(bench) for bench in benches], "results": [asdict(result) for result in results]}
    generate_report(payload, output_dir)
    return payload
