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

Because this repository is private, the generated report is uploaded as a private workflow artifact. GitHub Pages is not used as a privacy boundary.

### Viewing workflow runs and reports

1. Open the repository in GitHub (you must be signed in and have access to the private repo):
   - **https://github.com/psocbitm/cestat-monitor**
2. Click the **Actions** tab at the top.
3. Click **CESTAT keyword monitor** in the left sidebar to see all runs.
4. Open a run to see step status. Green means the monitor finished with no failed searches.
5. Scroll to **Artifacts** at the bottom of a completed run and download `cestat-report-<run-id>`.
6. Unzip the artifact. Open `output/index.html` in your browser for the report, or read `output/results.json`.

From the terminal (after `gh auth login`):

```sh
gh run list --repo psocbitm/cestat-monitor
gh run view <run-id> --repo psocbitm/cestat-monitor
gh run download <run-id> --repo psocbitm/cestat-monitor
open cestat-report-*/output/index.html
```

The workflow uses sequential requests (no parallelism), longer timeouts, and a second sequential pass for transient search and PDF failures. A successful empty result means no causelist or no matching PDF; request, CAPTCHA, malformed-response, download, and extraction failures are reported separately.
