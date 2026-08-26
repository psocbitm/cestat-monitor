"""Self-contained HTML shell for the CESTAT keyword report."""

from __future__ import annotations

import html
from typing import Any


def assemble_html_report(
    payload: dict[str, Any],
    stats: dict[str, int],
    table_body: str,
    generated_at_ist: str,
) -> str:
    keywords = html.escape(", ".join(payload["keywords"]))
    failures = stats["failed_pdfs"] + stats["failed_searches"]
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CESTAT keyword report</title>
<style>
:root {{
  --bg: #e8eef3; --surface: #fff; --ink: #152231; --muted: #5a6b7a;
  --brand: #0f2d44; --brand-soft: #c5dce8; --line: #d2dde6;
  --ok: #0f6d4f; --ok-bg: #e6f4ec; --match: #0a5f85; --match-bg: #e3f2fa;
  --fail: #b45309; --fail-bg: #fff4e5; --shadow: 0 12px 40px rgba(15,35,55,.09);
  font-family: "Segoe UI", system-ui, -apple-system, sans-serif; color: var(--ink); background: var(--bg);
}}
*, *::before, *::after {{ box-sizing: border-box }}
html {{ -webkit-text-size-adjust: 100% }}
body {{ margin: 0; min-width: 0 }}
.shell {{ max-width: 1240px; margin: 0 auto; padding: 1rem; min-width: 0 }}
.hero {{
  background: linear-gradient(145deg, #0f2d44 0%, #1a4d6e 55%, #1e5f7a 100%);
  color: #fff; border-radius: 18px; padding: 1.35rem 1.5rem; box-shadow: var(--shadow); margin-bottom: 1rem;
}}
.hero h1 {{ margin: 0 0 .4rem; font-size: clamp(1.35rem, 2.5vw, 1.75rem); line-height: 1.2 }}
.hero .sub {{ margin: 0; color: var(--brand-soft); font-size: .95rem; line-height: 1.5 }}
.hero .tz {{ opacity: .85; font-size: .82rem; margin-top: .35rem }}
.stats {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: .65rem; margin-top: 1rem }}
.stat {{
  background: rgba(255,255,255,.11); border: 1px solid rgba(255,255,255,.2);
  border-radius: 12px; padding: .6rem .85rem;
}}
.stat b {{ display: block; font-size: 1.4rem; font-weight: 750; line-height: 1.2 }}
.stat span {{ font-size: .78rem; opacity: .9 }}
.controls {{
  display: flex; flex-wrap: wrap; gap: .65rem; align-items: center;
  background: var(--surface); border: 1px solid var(--line); border-radius: 14px;
  padding: .85rem 1rem; margin-bottom: .85rem; box-shadow: var(--shadow);
}}
.controls label {{ display: flex; align-items: center; gap: .45rem; font-weight: 650; font-size: .92rem }}
.controls input[type=search] {{
  flex: 1 1 200px; min-width: 0; width: 100%; max-width: 100%;
  padding: .55rem .75rem; border: 1px solid var(--line); border-radius: 10px; font: inherit;
}}
.btn {{
  border: 1px solid var(--line); background: #f6f9fc; color: var(--ink);
  border-radius: 10px; padding: .5rem .8rem; font: inherit; font-weight: 650; cursor: pointer; white-space: nowrap;
}}
.btn:hover {{ background: #edf3f8 }}
.btn:focus-visible, .expand-btn:focus-visible {{ outline: 2px solid #0a5f85; outline-offset: 2px }}
.meta {{ margin: 0 0 1rem; color: var(--muted); font-size: .92rem; line-height: 1.5 }}
.legend {{ display: flex; flex-wrap: wrap; gap: .5rem .75rem; margin-bottom: .75rem; font-size: .8rem; color: var(--muted) }}
.legend span {{ display: inline-flex; align-items: center; gap: .35rem }}
.swatch {{ width: 10px; height: 10px; border-radius: 3px; flex-shrink: 0 }}
.swatch.match {{ background: var(--match-bg); border: 1px solid #8ecae6 }}
.swatch.fail {{ background: var(--fail-bg); border: 1px solid #f0b58f }}
.table-card {{
  background: var(--surface); border: 1px solid var(--line); border-radius: 16px;
  box-shadow: var(--shadow); min-width: 0;
}}
.table-scroll {{ overflow-x: auto; -webkit-overflow-scrolling: touch; max-width: 100% }}
.responsive-table {{ width: 100%; min-width: 640px; border-collapse: collapse; font-size: .92rem }}
.responsive-table thead th {{
  position: sticky; top: 0; z-index: 2; text-align: left;
  background: #f3f7fb; color: #3a4f61; font-size: .72rem;
  letter-spacing: .05em; text-transform: uppercase; padding: .72rem .6rem;
  border-bottom: 1px solid var(--line); white-space: nowrap;
}}
.responsive-table td {{ padding: .68rem .6rem; border-bottom: 1px solid #edf2f6; vertical-align: middle }}
.responsive-table tbody tr.group-row:nth-child(4n+1) td {{ background: #fbfcfd }}
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
.date-cell time {{ font-weight: 700; font-variant-numeric: tabular-nums }}
.type-pill {{ font-size: .82rem; color: var(--muted) }}
.pdf-link {{ color: #0a5f85; font-weight: 700; text-decoration: none }}
.pdf-link:hover {{ text-decoration: underline }}
.nested-wrap {{ padding: .5rem .45rem .85rem .45rem; min-width: 0 }}
.nested-scroll {{ overflow-x: auto; -webkit-overflow-scrolling: touch; border-radius: 12px; border: 1px solid var(--line) }}
.nested-table {{ min-width: 720px; border: none }}
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
.match-kw {{ font-weight: 800; color: var(--match) }}
.match-page {{ margin-left: .5rem; color: var(--muted); font-size: .82rem }}
.snippet {{ margin: .3rem 0 0; color: var(--muted); font-size: .86rem; line-height: 1.45; word-break: break-word }}
.kw-cell {{ max-width: 200px; word-break: break-word; font-size: .86rem }}
.empty-note {{ color: var(--muted); padding: .6rem .75rem; font-size: .9rem }}
.filter-hidden {{ display: none !important }}
#filter-summary {{ color: var(--muted); font-size: .88rem; flex: 1 1 auto; min-width: 0 }}
.group-detail > td {{ padding: 0; background: #f8fbfd; border-bottom: 1px solid var(--line) }}
.detail-row > td {{ padding: 0; background: #fafcfe }}
@media (max-width: 820px) {{
  .shell {{ padding: .65rem }}
  .controls {{ align-items: stretch }}
  .controls label, .btn {{ width: 100% }}
  .controls input[type=search] {{ flex: 1 1 100% }}
  #filter-summary {{ width: 100% }}
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
    <h1>CESTAT keyword report</h1>
    <p class="sub">{html.escape(payload["start_date"])} to {html.escape(payload["end_date"])} (IST)</p>
    <p class="sub">Generated {html.escape(generated_at_ist)}</p>
    <p class="tz">All dates and timestamps are shown in India Standard Time (IST).</p>
    <div class="stats">
      <div class="stat"><b>{stats["matched_pdfs"]}</b><span>matching PDFs</span></div>
      <div class="stat"><b>{stats["total_pdfs"]}</b><span>PDFs checked</span></div>
      <div class="stat"><b>{failures}</b><span>failures</span></div>
      <div class="stat"><b>{stats["warning_count"]}</b><span>warnings</span></div>
    </div>
  </header>
  <div class="controls">
    <label><input type="checkbox" id="matches-only"> Matches & failures only</label>
    <input type="search" id="table-search" placeholder="Filter date, bench, PDF, keyword…" autocomplete="off">
    <button type="button" class="btn" id="expand-matches">Expand matches</button>
    <button type="button" class="btn" id="collapse-all">Collapse all</button>
    <span id="filter-summary"></span>
  </div>
  <p class="meta"><strong>Keywords:</strong> {keywords}</p>
  <div class="legend" aria-hidden="true">
    <span><i class="swatch match"></i> Has keyword matches</span>
    <span><i class="swatch fail"></i> Has failures</span>
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
</div>
<script>
const table = document.getElementById('report-table');
const matchesOnly = document.getElementById('matches-only');
const searchInput = document.getElementById('table-search');
const summary = document.getElementById('filter-summary');
const groupRows = [...table.querySelectorAll('tr.group-row')];

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
  const btn = e.target.closest('.expand-btn');
  if (!btn) return;
  if (btn.classList.contains('group-expand')) toggleGroup(btn);
  if (btn.classList.contains('child-expand')) togglePdf(btn);
}});
function rowVisible(row, q) {{
  const text = ((row.dataset.date || '') + ' ' + (row.dataset.bench || '') + ' ' + row.textContent).toLowerCase();
  return !q || text.includes(q);
}}
function applyFilters() {{
  const only = matchesOnly.checked;
  const q = searchInput.value.trim().toLowerCase();
  let shown = 0;
  groupRows.forEach((row) => {{
    const detail = document.getElementById(row.dataset.group);
    const hasMatches = row.dataset.hasMatches === '1';
    const hasFailures = row.dataset.hasFailures === '1';
    const visible = (!only || hasMatches || hasFailures) && rowVisible(row, q);
    row.classList.toggle('filter-hidden', !visible);
    if (detail) {{
      detail.classList.toggle('filter-hidden', !visible);
      if (!visible) {{
        detail.hidden = true;
        setExpanded(row.querySelector('.group-expand'), false);
      }}
    }}
    if (visible) shown += 1;
    detail?.querySelectorAll('tr.pdf-row').forEach((pdfRow) => {{
      const pdfVisible = visible && rowVisible(pdfRow, q);
      pdfRow.classList.toggle('filter-hidden', !pdfVisible);
      let next = pdfRow.nextElementSibling;
      while (next && next.classList.contains('detail-row')) {{
        next.classList.toggle('filter-hidden', !pdfVisible);
        next = next.nextElementSibling;
      }}
    }});
  }});
  summary.textContent = shown + ' date/bench group(s) shown';
}}
matchesOnly.addEventListener('change', applyFilters);
searchInput.addEventListener('input', applyFilters);
document.getElementById('expand-matches').addEventListener('click', () => {{
  groupRows.forEach((row) => {{
    if (row.classList.contains('filter-hidden') || row.dataset.hasMatches !== '1') return;
    const btn = row.querySelector('.group-expand');
    const detail = document.getElementById(row.dataset.group);
    setExpanded(btn, true);
    if (detail) detail.hidden = false;
    detail?.querySelectorAll('tr.pdf-row.has-matches').forEach((pdfRow) => {{
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
