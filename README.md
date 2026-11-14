# CESTAT Causelist Keyword Monitor

Automatically checks CESTAT causelists across all benches for 7 days, downloads every PDF, and highlights matches for your companies and legal terms.

- **Automatic runs:** Every day at **9:00 AM** and **9:00 PM IST**
- **Live report (bookmark this):** **https://psocbitm.github.io/cestat-monitor/**

> **Note:** Because the live report is hosted on GitHub Pages, this repository and the live report URL are **public**. Do not add confidential notes or secrets.

---

## 1. Where to View Results

* **Live Dashboard (easiest):** Open **https://psocbitm.github.io/cestat-monitor/**. Use the keyword/bench/date buttons or the search box to filter hits, and click any `p. X ↗` link to jump straight to that page in the PDF.
* **Inside GitHub (no website needed):** Go to the **Actions** tab → click any finished run → read the **Summary** tab at the top. You can also download the `cestat-report-<run-id>` zip at the bottom of the run page to open `index.html` offline.

---

## 2. How to Type Keywords (No Coding Needed)

Just type **comma-separated words**, like a Google search:

| What you type | What it does | Example |
|---|---|---|
| `Ultratech, Dalmia` | **Smart match** — automatically handles `Ltd`/`Limited`, dots (`A.C.C.`), hyphens, and PDF line breaks | `ACC Ltd` matches `ACC Limited` |
| `Word1 + Word2` | **Both words near each other** — avoids noisy matches when a single word is too common | `Shree + Cement` skips `Shree Ganesh` |
| `Word -ignore1 -ignore2` | **Ignore nearby words** — skips matches when unwanted words appear nearby | `ACC -cargo -import` skips `Air Cargo Complex (ACC)` |
| `"Exact Phrase"` | **Exact phrase** — matches those exact words in order | `"Orient Paper"` |

---

## 3. How to Run a One-Off Search (Without Changing Saved Defaults)

Want to check a new company or a specific date without messing up the daily list?

1. Open the **Actions** tab at the top of this repo.
2. Click **CESTAT keyword monitor** in the left sidebar.
3. Click the **Run workflow** button on the right, fill in only what you need, and click **Run workflow**:
   * **keywords:** Type comma-separated terms (e.g. `Ultratech, Shree + Cement`). Leave blank to use the saved defaults.
   * **How to apply the keywords above:**
     * `Add to saved defaults` *(recommended)* — searches your new words **plus** your normal list.
     * `Search ONLY these keywords` — ignores the normal list for this one run.
   * **exclude:** Optional words to ignore nearby (e.g. `Air Cargo, Sahar`).
   * **Matching style:** Keep `Smart (Recommended)` unless you want strict case-sensitive matching for short acronyms (`Exact / Case-sensitive`).
   * **start_date:** Leave blank for today (IST), or type `DD-MM-YYYY` (e.g. `27-08-2026`).
   * **days:** Pick `1` for a quick today-only check, or `7` for the full week.
   * **benches:** Leave blank for all benches, or type specific ones like `Delhi, Mumbai`.
   * **Update the public GitHub Pages dashboard?:**
     * **Unchecked (default):** Safe for test/one-off searches. Results appear in the run's **Summary** tab and downloadable artifact without overwriting the live website.
     * **Checked:** Updates the live dashboard at `https://psocbitm.github.io/cestat-monitor/`.

---

## 4. How to Change or Reset the Saved Default Keywords

You can permanently add, remove, or reset your daily keyword list in 10 seconds—no file editing required:

1. Open the **Actions** tab → click **Manage Default Keywords** in the left sidebar.
2. Click **Run workflow** and choose an action:
   * **View current defaults** — prints the active keyword list and exclusions in the Summary tab.
   * **Add to defaults** — adds your new keywords/exclusions to the saved list.
   * **Remove from defaults** — deletes the listed keywords/exclusions from the saved list.
   * **Replace all defaults** — replaces the entire saved keyword list with what you typed.
   * **Reset to factory defaults** — restores the original baseline list from `config/keywords.default.json`.
3. Fill in **keywords** and/or **exclusions** (comma-separated, same mini-syntax as above).
4. Keep **Immediately run a fresh scan** checked if you want the live website refreshed right away.

---

## For Developers (Local Use & Smoke Runs)

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
python -m cestat_monitor.main --start-date 27-08-2026
```

Useful CLI flags: `--keywords`, `--keyword-mode (add|replace)`, `--exclude`, `--match-style (smart|exact)`, `--bench`, `--days (1..7)`.

**Controlled smoke run** (checks 2 dates and the first 2 discovered benches):

```sh
python scripts/smoke.py --start-date 27-08-2026
```

**Manage defaults locally:**

```sh
python -m cestat_monitor.manage_keywords --action view
python -m cestat_monitor.manage_keywords --action add --keywords "Ultratech, Shree + Cement"
python -m cestat_monitor.manage_keywords --action reset
```

**Notes on scraping & failures:**
The CESTAT site is server-rendered HTML; the client parses CSRF tokens, dynamic benches, and PDF links using sequential requests and retries transient failures. It uses the currently observed hidden CAPTCHA value `111111`; if CESTAT changes that contract, the run fails clearly rather than bypassing a CAPTCHA.
