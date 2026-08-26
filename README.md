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

The workflow runs automatically at **9:00 AM and 9:00 PM IST** (`30 3 * * *` and `30 15 * * *` UTC) and can also be started manually from **Actions -> CESTAT keyword monitor -> Run workflow**. Manual inputs are an optional `DD-MM-YYYY` start date and optional multiline keywords. No search input uses today in IST.

Because reports are published to GitHub Pages, the repository is **public**. Anyone can view the causelist keyword report at the Pages URL below. Do not put secrets in the repo.

### Viewing reports (no download)

**Option A — bookmark the live report (recommended)**

After the first successful workflow run, enable GitHub Pages once:

1. Repo **Settings → Pages**
2. Under **Build and deployment**, set **Source** to **Deploy from a branch**
3. Set **Branch** to `gh-pages` / `/ (root)`, then **Save**

Each workflow run publishes the latest report. Bookmark:

**https://psocbitm.github.io/cestat-monitor/**

On a **public** repo, the report URL is visible to anyone on the internet.

**Option B — read the summary on the workflow run**

Open **Actions →** pick a run. The **Summary** tab at the top shows matches, failures, and stats without downloading anything.

### Workflow runs and artifacts

1. Open **https://github.com/psocbitm/cestat-monitor/actions**
2. Click **CESTAT keyword monitor** in the left sidebar.
3. Open a run for logs and the job summary.

Artifacts (`cestat-report-<run-id>`) are optional backups. Download only if you need an archived copy:

```sh
gh run download <run-id> --repo psocbitm/cestat-monitor
```

The workflow uses sequential requests (no parallelism), longer timeouts, and a second sequential pass for transient search and PDF failures. A successful empty result means no causelist or no matching PDF; request, CAPTCHA, malformed-response, download, and extraction failures are reported separately.
