"""Self-contained HTML shell for the CESTAT keyword report."""

from __future__ import annotations

import html
from typing import Any


def assemble_html_report(
    payload: dict[str, Any],
    stats: dict[str, int],
    table_body: str,
    generated_at_ist: str,
    matches_feed_html: str = "",
    filter_chips_html: str = "",
) -> str:
    keywords = html.escape(", ".join(payload["keywords"]))
    exclusions = html.escape(", ".join(payload.get("excluded_phrases", [])))
    failures = stats["failed_pdfs"] + stats["failed_searches"]
    total_matches = stats.get("total_matches", 0)
    benches_with_matches = stats.get("benches_with_matches", 0)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CESTAT Keyword Monitor · {html.escape(payload["start_date"])} to {html.escape(payload["end_date"])}</title>
<style>
:root {{
  --bg: #e8eef3; --surface: #fff; --ink: #152231; --muted: #5a6b7a;
  --brand: #0f2d44; --brand-soft: #c5dce8; --line: #d2dde6;
  --ok: #0f6d4f; --ok-bg: #e6f4ec; --match: #0a5f85; --match-bg: #e3f2fa;
  --fail: #b45309; --fail-bg: #fff4e5; --shadow: 0 12px 40px rgba(15,35,55,.09);
  font-family: "Segoe UI", system-ui, -apple-system, sans-serif; color: var(--ink); background: var(--bg);
}}
*, *::before, *::after {{ box-sizing: border-box }}
html {{ -webkit-text-size-adjust: 100%; scroll-behavior: smooth }}
body {{ margin: 0; min-width: 0 }}
.shell {{ max-width: 1280px; margin: 0 auto; padding: 1rem; min-width: 0 }}
.hero {{
  background: linear-gradient(145deg, #0f2d44 0%, #1a4d6e 55%, #1e5f7a 100%);
  color: #fff; border-radius: 18px; padding: 1.35rem 1.5rem; box-shadow: var(--shadow); margin-bottom: 1rem;
}}
.hero-top {{ display: flex; flex-wrap: wrap; justify-content: space-between; align-items: flex-start; gap: .75rem }}
.hero h1 {{ margin: 0 0 .35rem; font-size: clamp(1.35rem, 2.5vw, 1.75rem); line-height: 1.2 }}
.hero .sub {{ margin: 0; color: var(--brand-soft); font-size: .95rem; line-height: 1.5 }}
.hero .tz {{ opacity: .85; font-size: .82rem; margin-top: .25rem }}
.hero-nav {{ display: flex; gap: .5rem; flex-wrap: wrap }}
.hero-link {{
  display: inline-flex; align-items: center; gap: .35rem; padding: .45rem .8rem;
  border-radius: 999px; background: rgba(255,255,255,.14); color: #fff; text-decoration: none;
  font-size: .84rem; font-weight: 650; border: 1px solid rgba(255,255,255,.24);
}}
.hero-link:hover {{ background: rgba(255,255,255,.22) }}
.stats {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(135px, 1fr)); gap: .65rem; margin-top: 1rem }}
.stat {{
  background: rgba(255,255,255,.11); border: 1px solid rgba(255,255,255,.2);
  border-radius: 12px; padding: .6rem .85rem;
}}
.stat b {{ display: block; font-size: 1.4rem; font-weight: 750; line-height: 1.2 }}
.stat span {{ font-size: .78rem; opacity: .9 }}

.filter-panel {{
  background: var(--surface); border: 1px solid var(--line); border-radius: 16px;
  padding: .9rem 1.1rem; margin-bottom: 1rem; box-shadow: var(--shadow);
}}
.controls {{
  display: flex; flex-wrap: wrap; gap: .65rem; align-items: center;
}}
.search-wrap {{ position: relative; flex: 1 1 260px; min-width: 0; display: flex; align-items: center }}
.search-wrap input[type=search] {{
  width: 100%; padding: .55rem 2.2rem .55rem .8rem; border: 1px solid var(--line);
  border-radius: 10px; font: inherit; background: #fbfdff;
}}
.search-wrap input[type=search]:focus {{ outline: 2px solid var(--match); border-color: transparent; background: #fff }}
.kbd-hint {{
  position: absolute; right: .6rem; font-size: .72rem; color: var(--muted);
  border: 1px solid var(--line); border-radius: 5px; padding: .05rem .35rem; background: #f3f7fb; pointer-events: none;
}}
.controls label {{ display: inline-flex; align-items: center; gap: .4rem; font-weight: 600; font-size: .88rem; cursor: pointer; user-select: none }}
.btn {{
  border: 1px solid var(--line); background: #f6f9fc; color: var(--ink);
  border-radius: 10px; padding: .48rem .8rem; font: inherit; font-size: .88rem; font-weight: 650; cursor: pointer; white-space: nowrap;
}}
.btn:hover {{ background: #edf3f8 }}
.btn:focus-visible, .expand-btn:focus-visible, .chip:focus-visible {{ outline: 2px solid #0a5f85; outline-offset: 2px }}

.chip-bars {{ margin-top: .75rem; padding-top: .75rem; border-top: 1px solid #edf2f6; display: flex; flex-direction: column; gap: .5rem }}
.chip-group {{ display: flex; align-items: baseline; gap: .55rem; flex-wrap: wrap }}
.chip-label {{ font-size: .76rem; font-weight: 750; text-transform: uppercase; letter-spacing: .04em; color: var(--muted); min-width: 68px }}
.chip-list {{ display: flex; flex-wrap: wrap; gap: .4rem; align-items: center; flex: 1 }}
.chip {{
  display: inline-flex; align-items: center; gap: .35rem; padding: .28rem .65rem;
  border-radius: 999px; border: 1px solid var(--line); background: #f8fbfd; color: var(--ink);
  font: inherit; font-size: .82rem; font-weight: 600; cursor: pointer; transition: all .12s ease;
}}
.chip:hover {{ background: #eaf3f9; border-color: #a9cbe0 }}
.chip.active {{ background: var(--brand); color: #fff; border-color: var(--brand) }}
.chip.chip-muted {{ opacity: .65 }}
.chip-count {{
  display: inline-flex; align-items: center; justify-content: center;
  min-width: 1.25rem; padding: .05rem .35rem; border-radius: 999px;
  font-size: .72rem; font-weight: 750; background: var(--match-bg); color: var(--match);
}}
.chip.active .chip-count {{ background: rgba(255,255,255,.22); color: #fff }}
.zero-kw-note {{ font-size: .78rem; color: var(--muted); margin-left: .35rem }}

.meta-bar {{
  display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center;
  gap: .5rem 1rem; margin: 0 0 .85rem; color: var(--muted); font-size: .85rem;
}}
#filter-summary {{ font-weight: 650; color: var(--brand) }}

.section-head {{
  display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center;
  gap: .5rem; margin: 1.1rem 0 .65rem;
}}
.section-head h2 {{ margin: 0; font-size: 1.18rem; color: var(--brand); display: flex; align-items: center; gap: .5rem }}
.section-sub {{ font-size: .85rem; color: var(--muted); margin: 0 }}

.matches-grid {{ display: flex; flex-direction: column; gap: .75rem; margin-bottom: 1.4rem }}
.match-card {{
  background: var(--surface); border: 1px solid #bfdced; border-left: 5px solid var(--match);
  border-radius: 14px; padding: .85rem 1rem; box-shadow: var(--shadow);
}}
.match-card.is-superseded {{ border-left-color: #94a3b8; border-color: var(--line); background: #fafcfe }}
.match-card-head {{
  display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center;
  gap: .5rem .75rem; padding-bottom: .6rem; border-bottom: 1px solid #e6eff5;
}}
.match-card-title {{ display: flex; flex-wrap: wrap; align-items: center; gap: .45rem }}
.match-date-pill {{
  background: var(--brand); color: #fff; font-weight: 750; font-size: .82rem;
  padding: .22rem .6rem; border-radius: 8px; font-variant-numeric: tabular-nums;
}}
.match-bench-pill {{
  background: var(--match-bg); color: var(--match); font-weight: 800; font-size: .84rem;
  padding: .22rem .6rem; border-radius: 8px;
}}
.division-pill {{
  background: #eff6ff; color: #1e40af; border: 1px solid #bfdbfe;
  font-weight: 700; font-size: .78rem; padding: .18rem .55rem; border-radius: 999px;
}}
.match-card-meta {{ display: flex; flex-wrap: wrap; align-items: center; gap: .6rem; font-size: .82rem; color: var(--muted) }}
.pdf-btn {{
  display: inline-flex; align-items: center; gap: .25rem;
  background: var(--match); color: #fff; text-decoration: none;
  font-weight: 700; font-size: .8rem; padding: .28rem .68rem; border-radius: 8px;
}}
.pdf-btn:hover {{ background: #084c6b }}
.meta-item {{ white-space: nowrap }}
.feed-match-list {{ margin: .55rem 0 0; padding: 0; list-style: none }}
.feed-match-item {{ padding: .5rem 0; border-bottom: 1px dashed #dbe8f0 }}
.feed-match-item:last-child {{ border-bottom: none; padding-bottom: 0 }}
.feed-match-top {{ display: flex; flex-wrap: wrap; align-items: center; gap: .45rem }}
.match-kw {{
  display: inline-block; background: var(--match-bg); color: var(--match);
  font-weight: 800; font-size: .78rem; padding: .12rem .5rem; border-radius: 6px;
}}
.match-page {{
  display: inline-flex; align-items: center; font-size: .78rem; font-weight: 700;
  color: var(--brand); background: #eef4f8; padding: .12rem .48rem; border-radius: 6px;
  text-decoration: none; border: 1px solid var(--line);
}}
.match-page:hover {{ background: #dce9f2; text-decoration: underline }}
.snippet {{ margin: .35rem 0 0; color: #2b3d4f; font-size: .88rem; line-height: 1.5; word-break: break-word }}
mark.kw-hit {{
  background: #fef08a; color: #713f12; font-weight: 800;
  padding: .05rem .28rem; border-radius: 4px; box-shadow: 0 0 0 1px #fde047;
}}
.empty-feed {{
  background: var(--surface); border: 1px dashed var(--line); border-radius: 14px;
  padding: 1.25rem; text-align: center; color: var(--muted); font-size: .92rem;
}}

.legend {{ display: flex; flex-wrap: wrap; gap: .5rem .75rem; font-size: .8rem; color: var(--muted) }}
.legend span {{ display: inline-flex; align-items: center; gap: .35rem }}
.swatch {{ width: 10px; height: 10px; border-radius: 3px; flex-shrink: 0 }}
.swatch.match {{ background: var(--match-bg); border: 1px solid #8ecae6 }}
.swatch.fail {{ background: var(--fail-bg); border: 1px solid #f0b58f }}
.table-card {{
  background: var(--surface); border: 1px solid var(--line); border-radius: 16px;
  box-shadow: var(--shadow); min-width: 0; overflow: hidden;
}}
.table-scroll {{ overflow-x: auto; -webkit-overflow-scrolling: touch; max-width: 100% }}
.responsive-table {{ width: 100%; min-width: 680px; border-collapse: collapse; font-size: .92rem }}
.responsive-table thead th {{
  position: sticky; top: 0; z-index: 2; text-align: left;
  background: #f3f7fb; color: #3a4f61; font-size: .72rem;
  letter-spacing: .05em; text-transform: uppercase; padding: .72rem .6rem;
  border-bottom: 1px solid var(--line); white-space: nowrap;
}}
.responsive-table td {{ padding: .68rem .6rem; border-bottom: 1px solid #edf2f6; vertical-align: middle }}
.group-row, .pdf-row.has-matches {{ cursor: pointer }}
.responsive-table tbody tr.group-row:hover td {{ background: #f3f8fc }}
.group-row.has-matches td {{ background: linear-gradient(90deg, var(--match-bg) 0%, transparent 70%) !important }}
.group-row.has-failures td {{ background: linear-gradient(90deg, var(--fail-bg) 0%, transparent 70%) !important }}
.pdf-row.has-matches td {{ background: #f4faff }}
.pdf-row.has-failures td {{ background: #fffaf3 }}
.col-expand {{ width: 44px; text-align: center; padding-left: .4rem; padding-right: .4rem }}
.expand-btn {{
  width: 32px; height: 32px; border: 1px solid var(--line); border-radius: 9px;
  background: #fff; cursor: pointer; display: inline-flex; align-items: center; justify-content: center;
}}
.expand-btn:disabled {{ opacity: .3; cursor: default }}
.expand-btn[aria-expanded="true"] .chev {{ transform: rotate(135deg); margin-top: -2px }}
.chev {{
  width: 7px; height: 7px; border-right: 2px solid #3a4f61; border-bottom: 2px solid #3a4f61;
  transform: rotate(-45deg); transition: transform .15s ease;
}}
.badge {{
  display: inline-block; padding: .2rem .55rem; border-radius: 999px; font-size: .72rem;
  font-weight: 700; letter-spacing: .02em; white-space: nowrap; max-width: 100%;
}}
.badge-ok {{ background: var(--ok-bg); color: var(--ok) }}
.badge-match {{ background: var(--match-bg); color: var(--match) }}
.badge-fail {{ background: var(--fail-bg); color: var(--fail) }}
.badge-muted {{ background: #f1f5f9; color: var(--muted) }}
.count-badge {{ display: inline-flex; min-width: 1.5rem; justify-content: center; padding: .15rem .45rem; border-radius: 999px; font-weight: 800; font-size: .78rem }}
.count-badge.match {{ background: var(--match); color: #fff }}
.count-badge.fail {{ background: var(--fail); color: #fff }}
.count-zero {{ color: var(--muted); font-variant-numeric: tabular-nums }}
.num {{ font-variant-numeric: tabular-nums; text-align: right }}
.mono {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: .82rem; white-space: nowrap }}
.bench-name {{ font-weight: 700 }}
.bench-type-cell {{ font-weight: 650; font-size: .84rem; color: #1e3a5f }}
.date-cell time {{ font-weight: 700; font-variant-numeric: tabular-nums }}
.wk-pill {{ font-size: .75rem; color: var(--muted); font-weight: 600; margin-left: .25rem }}
.type-pill {{ font-size: .8rem; color: var(--muted); background: #f1f5f9; padding: .12rem .45rem; border-radius: 6px }}
.pdf-link {{ color: #0a5f85; font-weight: 700; text-decoration: none }}
.pdf-link:hover {{ text-decoration: underline }}
.nested-wrap {{ padding: .5rem .45rem .85rem .45rem; min-width: 0 }}
.nested-scroll {{ overflow-x: auto; -webkit-overflow-scrolling: touch; border-radius: 12px; border: 1px solid var(--line) }}
.nested-table {{ min-width: 780px; border: none }}
.nested-table thead th {{ background: #eef4f8; top: 0 }}
.detail-panel {{ padding: .75rem .9rem; margin: .45rem .2rem }}
.match-panel {{ background: #f4faff; border: 1px solid #cfe8f5; border-radius: 12px }}
.match-panel h4 {{ margin: 0 0 .5rem; font-size: .9rem }}
.error-panel {{
  background: var(--fail-bg); color: #7c2d12; border: 1px solid #f0b58f; border-radius: 12px;
  font-size: .88rem; line-height: 1.45; word-break: break-word;
}}
.match-list {{ margin: 0; padding: 0; list-style: none }}
.match-list li {{ padding: .55rem 0; border-bottom: 1px solid #dceaf3 }}
.match-list li:last-child {{ border-bottom: none }}
.kw-cell {{ max-width: 220px; word-break: break-word; font-size: .86rem }}
.empty-note {{ color: var(--muted); padding: .6rem .75rem; font-size: .9rem }}
.filter-hidden {{ display: none !important }}
.group-detail > td {{ padding: 0; background: #f8fbfd; border-bottom: 1px solid var(--line) }}
.detail-row > td {{ padding: 0; background: #fafcfe }}

@media (max-width: 820px) {{
  .shell {{ padding: .65rem }}
  .controls {{ align-items: stretch }}
  .controls label, .btn, .search-wrap {{ width: 100%; flex: 1 1 100% }}
  .chip-label {{ min-width: 100%; margin-bottom: .1rem }}
  .responsive-table:not(.nested-table) thead {{ display: none }}
  .responsive-table:not(.nested-table) tr.group-row {{
    display: block; margin: .5rem .65rem; border: 1px solid var(--line); border-radius: 12px; overflow: hidden;
  }}
  .responsive-table:not(.nested-table) tr.group-row td {{
    display: grid; grid-template-columns: 38% 1fr; gap: .2rem .65rem;
    border: none; padding: .45rem .75rem; background: transparent !important;
  }}
  .responsive-table:not(.nested-table) tr.group-row td::before {{
    content: attr(data-label); font-size: .68rem; text-transform: uppercase;
    letter-spacing: .04em; color: var(--muted); font-weight: 700;
  }}
  .responsive-table:not(.nested-table) tr.group-row td.col-expand {{
    grid-column: 1 / -1; display: flex; justify-content: flex-start; padding-bottom: 0;
  }}
  .responsive-table:not(.nested-table) tr.group-row td.col-expand::before {{ content: none }}
  .group-detail {{ display: block }}
  .group-detail > td {{ display: block; border: none }}
  .nested-table thead {{ display: none }}
  .nested-table tr.pdf-row {{
    display: block; margin: .45rem .5rem; border: 1px solid var(--line); border-radius: 10px;
  }}
  .nested-table tr.pdf-row td {{
    display: grid; grid-template-columns: 38% 1fr; gap: .15rem .5rem; border: none; padding: .4rem .65rem;
  }}
  .nested-table tr.pdf-row td::before {{
    content: attr(data-label); font-size: .65rem; text-transform: uppercase; color: var(--muted); font-weight: 700;
  }}
  .nested-table tr.pdf-row td.col-expand {{ grid-column: 1 / -1 }}
  .nested-table tr.pdf-row td.col-expand::before {{ content: none }}
  .detail-row {{ display: table-row }}
  .detail-row td {{ display: table-cell }}
}}
</style>
</head>
<body>
<div class="shell">
  <header class="hero">
    <div class="hero-top">
      <div>
        <h1>CESTAT Causelist Keyword Monitor</h1>
        <p class="sub">Window: <strong>{html.escape(payload["start_date"])}</strong> to <strong>{html.escape(payload["end_date"])}</strong> · Generated {html.escape(generated_at_ist)}</p>
        <p class="tz">All dates and timestamps are in India Standard Time (IST).</p>
      </div>
      <nav class="hero-nav" aria-label="Quick jump">
        <a class="hero-link" href="#matches-section">Matched Cases ({stats["matched_pdfs"]})</a>
        <a class="hero-link" href="#schedule-section">All Searches ({len(payload["results"])})</a>
        <a class="hero-link" href="results.json" target="_blank" rel="noopener noreferrer">JSON Data ↗</a>
      </nav>
    </div>
    <div class="stats">
      <div class="stat"><b>{stats["matched_pdfs"]}</b><span>matching PDFs</span></div>
      <div class="stat"><b>{total_matches}</b><span>keyword hits</span></div>
      <div class="stat"><b>{benches_with_matches}</b><span>benches with hits</span></div>
      <div class="stat"><b>{stats["total_pdfs"]}</b><span>PDFs checked</span></div>
      <div class="stat"><b>{failures}</b><span>failures</span></div>
    </div>
  </header>

  <section class="filter-panel" aria-label="Filters">
    <div class="controls">
      <div class="search-wrap">
        <input type="search" id="table-search" placeholder="Search date, bench, division, PDF ID, keyword, party name…" autocomplete="off">
        <span class="kbd-hint" aria-hidden="true">/</span>
      </div>
      <label><input type="checkbox" id="hide-superseded" checked> Hide earlier duplicate uploads</label>
      <label><input type="checkbox" id="matches-only"> Matches &amp; failures only in table</label>
      <button type="button" class="btn" id="expand-matches">Expand all matches</button>
      <button type="button" class="btn" id="collapse-all">Collapse all</button>
      <button type="button" class="btn" id="reset-filters">Reset filters</button>
    </div>
    {filter_chips_html}
  </section>

  <div class="meta-bar">
    <span id="filter-summary"></span>
    <span><strong>Monitored:</strong> {keywords}{f" · <strong>Excluded:</strong> {exclusions}" if exclusions else ""}</span>
  </div>

  <section id="matches-section" aria-label="Matched Cases">
    <div class="section-head">
      <h2>Matched Causelist PDFs</h2>
      <p class="section-sub">Click any <strong>p. N ↗</strong> badge to open the PDF directly at that page.</p>
    </div>
    <div class="matches-grid" id="matches-feed">
      {matches_feed_html}
    </div>
  </section>

  <section id="schedule-section" aria-label="All Date and Bench Searches">
    <div class="section-head">
      <h2>All Date &amp; Bench Searches ({len(payload["results"])})</h2>
      <div class="legend" aria-hidden="true">
        <span><i class="swatch match"></i> Has keyword matches</span>
        <span><i class="swatch fail"></i> Has failures</span>
      </div>
    </div>
    <div class="table-card">
      <div class="table-scroll">
        <table class="responsive-table" id="report-table">
          <thead>
            <tr>
              <th class="col-expand" aria-hidden="true"></th>
              <th>Date (IST)</th>
              <th>Bench</th>
              <th>Search</th>
              <th class="num">PDFs</th>
              <th class="num">Matches</th>
              <th class="num">Failures</th>
            </tr>
          </thead>
          <tbody>{table_body}</tbody>
        </table>
      </div>
    </div>
  </section>
</div>
<script>
const table = document.getElementById('report-table');
const feed = document.getElementById('matches-feed');
const matchesOnly = document.getElementById('matches-only');
const hideSuperseded = document.getElementById('hide-superseded');
const searchInput = document.getElementById('table-search');
const summary = document.getElementById('filter-summary');
const groupRows = [...table.querySelectorAll('tr.group-row')];
const matchCards = [...(feed ? feed.querySelectorAll('article.match-card') : [])];
const chips = [...document.querySelectorAll('.chip[data-filter-type]')];

const activeFilters = {{ keyword: '', bench: '', date: '' }};

function setExpanded(btn, open) {{
  if (!btn || btn.disabled) return;
  btn.setAttribute('aria-expanded', open ? 'true' : 'false');
}}
function toggleGroup(btn) {{
  const row = btn.closest('tr.group-row');
  const detail = document.getElementById(row.dataset.group);
  const open = btn.getAttribute('aria-expanded') !== 'true';
  setExpanded(btn, open);
  if (detail) detail.hidden = !open;
}}
function togglePdf(btn) {{
  const row = btn.closest('tr.pdf-row');
  let detail = row.nextElementSibling;
  while (detail && !detail.classList.contains('child-detail')) detail = detail.nextElementSibling;
  const open = btn.getAttribute('aria-expanded') !== 'true';
  setExpanded(btn, open);
  if (detail) detail.hidden = !open;
}}
table.addEventListener('click', (e) => {{
  if (e.target.closest('a')) return;
  const btn = e.target.closest('.expand-btn');
  if (btn) {{
    if (btn.classList.contains('group-expand')) toggleGroup(btn);
    if (btn.classList.contains('child-expand')) togglePdf(btn);
    return;
  }}
  const groupRow = e.target.closest('tr.group-row');
  if (groupRow) {{
    const gBtn = groupRow.querySelector('.group-expand');
    if (gBtn) toggleGroup(gBtn);
    return;
  }}
  const pdfRow = e.target.closest('tr.pdf-row');
  if (pdfRow) {{
    const cBtn = pdfRow.querySelector('.child-expand');
    if (cBtn && !cBtn.disabled) togglePdf(cBtn);
  }}
}});

document.addEventListener('keydown', (e) => {{
  if (e.key === '/' && document.activeElement !== searchInput && !['INPUT', 'TEXTAREA'].includes(document.activeElement?.tagName)) {{
    e.preventDefault();
    searchInput.focus();
  }}
}});

chips.forEach((chip) => {{
  chip.addEventListener('click', () => {{
    const type = chip.dataset.filterType;
    const val = chip.dataset.filterValue || '';
    activeFilters[type] = (activeFilters[type] === val && val !== '') ? '' : val;
    chips.filter((c) => c.dataset.filterType === type).forEach((c) => {{
      c.classList.toggle('active', (c.dataset.filterValue || '') === activeFilters[type]);
    }});
    applyFilters();
  }});
}});

function hasKeywordAttr(el, kw) {{
  if (!kw) return true;
  const list = (el.dataset.keywords || '').split('|').filter(Boolean);
  return list.includes(kw);
}}
function rowVisible(row, q) {{
  const text = ((row.dataset.date || '') + ' ' + (row.dataset.bench || '') + ' ' + row.textContent).toLowerCase();
  return !q || text.includes(q);
}}
function pdfFullText(pdfRow) {{
  let text = pdfRow.textContent || '';
  let next = pdfRow.nextElementSibling;
  while (next && next.classList.contains('detail-row')) {{
    text += ' ' + (next.textContent || '');
    next = next.nextElementSibling;
  }}
  return text.toLowerCase();
}}

function applyFilters() {{
  const only = matchesOnly.checked;
  const skipSuperseded = hideSuperseded ? hideSuperseded.checked : false;
  const q = searchInput.value.trim().toLowerCase();
  const kwFilter = activeFilters.keyword;
  const benchFilter = activeFilters.bench;
  const dateFilter = activeFilters.date;

  let shownCards = 0;
  matchCards.forEach((card) => {{
    const isSuperseded = card.classList.contains('is-superseded');
    const dateOk = !dateFilter || (card.dataset.date || '') === dateFilter;
    const benchOk = !benchFilter || (card.dataset.bench || '') === benchFilter;
    const kwOk = hasKeywordAttr(card, kwFilter);
    const qOk = !q || card.textContent.toLowerCase().includes(q);
    const visible = (!skipSuperseded || !isSuperseded) && dateOk && benchOk && kwOk && qOk;
    card.classList.toggle('filter-hidden', !visible);
    if (visible) {{
      shownCards += 1;
      card.querySelectorAll('.feed-match-item').forEach((item) => {{
        const itemKwOk = !kwFilter || item.dataset.keyword === kwFilter;
        const itemQOk = !q || card.dataset.bench.includes(q) || card.dataset.date.includes(q) || item.textContent.toLowerCase().includes(q);
        item.classList.toggle('filter-hidden', !(itemKwOk && itemQOk));
      }});
    }}
  }});

  let shownGroups = 0;
  groupRows.forEach((row) => {{
    const detail = document.getElementById(row.dataset.group);
    const hasMatches = row.dataset.hasMatches === '1';
    const hasFailures = row.dataset.hasFailures === '1';
    const rowDate = (row.dataset.date || '').toLowerCase();
    const rowBench = (row.dataset.bench || '').toLowerCase();
    const dateOk = !dateFilter || rowDate === dateFilter;
    const benchOk = !benchFilter || rowBench === benchFilter;
    const kwOk = hasKeywordAttr(row, kwFilter);

    const groupMatched = rowVisible(row, q);
    const pdfRows = detail ? [...detail.querySelectorAll('tr.pdf-row')] : [];
    let anyPdfMatched = false;
    pdfRows.forEach((pdfRow) => {{
      const pdfKwOk = hasKeywordAttr(pdfRow, kwFilter);
      const pdfQOk = !q || groupMatched || pdfFullText(pdfRow).includes(q);
      if (pdfKwOk && pdfQOk) anyPdfMatched = true;
    }});
    const detailMatched = detail ? detail.textContent.toLowerCase().includes(q) : false;
    const queryOk = !q || groupMatched || anyPdfMatched || detailMatched;

    const visible = (!only || hasMatches || hasFailures) && dateOk && benchOk && kwOk && queryOk;
    row.classList.toggle('filter-hidden', !visible);
    if (detail) {{
      detail.classList.toggle('filter-hidden', !visible);
      if (!visible) {{
        detail.hidden = true;
        setExpanded(row.querySelector('.group-expand'), false);
      }}
    }}
    if (visible) shownGroups += 1;
    pdfRows.forEach((pdfRow) => {{
      const pdfKwOk = !kwFilter || hasKeywordAttr(pdfRow, kwFilter);
      const pdfQOk = !q || groupMatched || pdfFullText(pdfRow).includes(q);
      const pdfVisible = visible && pdfKwOk && pdfQOk;
      pdfRow.classList.toggle('filter-hidden', !pdfVisible);
      let next = pdfRow.nextElementSibling;
      while (next && next.classList.contains('detail-row')) {{
        next.classList.toggle('filter-hidden', !pdfVisible);
        next = next.nextElementSibling;
      }}
    }});
  }});
  summary.textContent = shownCards + ' matched PDF card(s) · ' + shownGroups + ' date/bench search(es) shown';
}}

matchesOnly.addEventListener('change', applyFilters);
if (hideSuperseded) hideSuperseded.addEventListener('change', applyFilters);
searchInput.addEventListener('input', applyFilters);

document.getElementById('reset-filters').addEventListener('click', () => {{
  searchInput.value = '';
  matchesOnly.checked = false;
  if (hideSuperseded) hideSuperseded.checked = true;
  activeFilters.keyword = '';
  activeFilters.bench = '';
  activeFilters.date = '';
  chips.forEach((c) => c.classList.toggle('active', !c.dataset.filterValue));
  applyFilters();
}});

document.getElementById('expand-matches').addEventListener('click', () => {{
  groupRows.forEach((row) => {{
    if (row.classList.contains('filter-hidden') || row.dataset.hasMatches !== '1') return;
    const btn = row.querySelector('.group-expand');
    const detail = document.getElementById(row.dataset.group);
    setExpanded(btn, true);
    if (detail) detail.hidden = false;
    detail?.querySelectorAll('tr.pdf-row.has-matches').forEach((pdfRow) => {{
      if (pdfRow.classList.contains('filter-hidden')) return;
      const childBtn = pdfRow.querySelector('.child-expand');
      let childDetail = pdfRow.nextElementSibling;
      while (childDetail && !childDetail.classList.contains('child-detail')) childDetail = childDetail.nextElementSibling;
      setExpanded(childBtn, true);
      if (childDetail) childDetail.hidden = false;
    }});
  }});
}});

document.getElementById('collapse-all').addEventListener('click', () => {{
  table.querySelectorAll('.expand-btn').forEach((btn) => setExpanded(btn, false));
  table.querySelectorAll('.group-detail, .child-detail').forEach((row) => {{ row.hidden = true; }});
}});

groupRows.forEach((row) => {{
  if (row.dataset.hasMatches !== '1') return;
  const btn = row.querySelector('.group-expand');
  const detail = document.getElementById(row.dataset.group);
  setExpanded(btn, true);
  if (detail) detail.hidden = false;
}});
applyFilters();
</script>
</body>
</html>"""
