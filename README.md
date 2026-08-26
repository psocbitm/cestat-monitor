# CESTAT Keyword Monitor

A private GitHub Actions workflow that checks CESTAT causelists for seven inclusive dates, discovers all available benches, downloads every returned PDF, and reports configured keyword matches.

## Local use

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
python -m cestat_monitor.main --start-date 27-08-2026
```

The report is written to `output/index.html` and `output/results.json`. The default keywords are in `config/keywords.json`. A non-empty multiline `--keywords` value temporarily replaces them.

## Controlled smoke run

The smoke command intentionally uses exactly two dates and the first two discovered benches, while processing every PDF returned for those selections:

```sh
python scripts/smoke.py --start-date 27-08-2026
```

The live site is server-rendered HTML, so the client parses the form and result tables to obtain CSRF data, dynamic benches, and PDF links. It uses the currently observed hidden CAPTCHA value `111111`; if CESTAT changes that contract, the run fails clearly rather than bypassing a CAPTCHA.

## GitHub Actions

The workflow runs at 5:00 AM IST (`30 23 * * *` UTC) and can also be started manually from **Actions -> CESTAT keyword monitor -> Run workflow**. Manual inputs are an optional `DD-MM-YYYY` start date and optional multiline keywords. No search input creates a commit.

Because this repository is private, the generated report is uploaded as a private workflow artifact. GitHub Pages is not used as a privacy boundary. Download an artifact from the workflow run, or use `gh run download` after authentication.

The workflow uses two parallel PDF workers, followed by a sequential second pass for transient PDF failures only. A successful empty result means no causelist or no matching PDF; request, CAPTCHA, malformed-response, download, and extraction failures are reported separately.
