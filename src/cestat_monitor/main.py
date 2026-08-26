from __future__ import annotations

import argparse
import logging
from pathlib import Path

from .core import MonitorError, load_config, parse_start_date, run


def main() -> int:
    parser = argparse.ArgumentParser(description="Search CESTAT causelist PDFs")
    parser.add_argument("--start-date", help="Start date in DD-MM-YYYY; defaults to today in Asia/Kolkata")
    parser.add_argument("--keywords", default="", help="Optional multiline keyword override")
    parser.add_argument("--config", type=Path, default=Path("config/keywords.json"))
    parser.add_argument("--output", type=Path, default=Path("output"))
    parser.add_argument("--bench-limit", type=int, help="Limit benches for the controlled smoke run")
    parser.add_argument("--days", type=int, default=7, help="Number of dates to check; production default is 7")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        start = parse_start_date(args.start_date)
        keywords, exclusions = load_config(args.config, args.keywords)
        if args.days < 1 or args.days > 7:
            raise MonitorError("Days must be between 1 and 7.")
        payload = run(start, keywords, exclusions, args.bench_limit, args.days, args.output)
        failures = sum(result["status"] == "failed" for result in payload["results"])
        matches = sum(bool(pdf["matches"]) for result in payload["results"] for pdf in result["pdfs"])
        logging.info("Completed %s to %s: %d date/bench searches, %d matching PDFs, %d failed searches", payload["start_date"], payload["end_date"], len(payload["results"]), matches, failures)
        return 1 if failures else 0
    except MonitorError as exc:
        logging.error("ERROR: %s", exc)
        return 2
    except Exception:
        logging.exception("ERROR: unexpected failure; inspect the workflow artifact for diagnostics")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
