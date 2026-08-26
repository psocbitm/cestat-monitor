"""Controlled live smoke run: exactly seven dates, with two benches selected."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from cestat_monitor.core import MonitorError, load_config, parse_start_date, run

parser = argparse.ArgumentParser()
parser.add_argument("--start-date", required=True, help="Start date in DD-MM-YYYY")
parser.add_argument("--output", type=Path, default=Path("smoke-output"))
args = parser.parse_args()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
try:
    keywords, exclusions = load_config(Path("config/keywords.json"))
    payload = run(parse_start_date(args.start_date), keywords, exclusions, bench_limit=2, days=2, output_dir=args.output)
    pdf_count = sum(len(result["pdfs"]) for result in payload["results"])
    pdf_failures = sum(pdf["status"] == "failed" for result in payload["results"] for pdf in result["pdfs"])
    print(f"Smoke run complete: {len(payload['results'])} date/bench searches, {pdf_count} PDFs processed, {pdf_failures} PDF failure(s).")
    if pdf_failures:
        raise SystemExit("Smoke run finished with PDF failures; inspect smoke-output/results.json.")
except MonitorError as exc:
    raise SystemExit(f"Smoke run failed: {exc}")
