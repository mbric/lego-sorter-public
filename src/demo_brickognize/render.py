"""Pure HTML builders for match panels, tables, and catalog views."""

from __future__ import annotations

import html
from datetime import datetime

from .brickognize_client import Candidate
from .session_catalog import SCAN_DISPLAY_FIELDS, VALUED_INVENTORY_FIELDS

MATCH_PANEL_HEIGHT = 300  # fixed right-column region so live session below stays put


def format_links(candidate: Candidate) -> str:
    if not candidate.external_links:
        return ""
    links = " · ".join(
        f'<a href="{html.escape(link.url)}" target="_blank">{html.escape(link.name)}</a>'
        for link in candidate.external_links
    )
    return f"<div style='margin-top:6px;font-size:0.9em'>{links}</div>"


def format_candidate(candidate: Candidate, rank: int) -> str:
    score_pct = f"{candidate.score * 100:.1f}%"
    category = candidate.category or candidate.item_type
    return (
        f"<div style='margin-bottom:12px'>"
        f"<strong>#{rank + 1}</strong> "
        f"<span style='font-size:1.1em'>{html.escape(candidate.name)}</span> "
        f"<code>{html.escape(candidate.id)}</code> "
        f"({html.escape(category)}) — <strong>{score_pct}</strong>"
        f"{format_links(candidate)}"
        f"</div>"
    )


def reference_html(candidate: Candidate | None) -> str:
    if not candidate or not candidate.img_url:
        return "<p style='margin:0;opacity:0.6'><em>No reference image.</em></p>"
    return (
        f'<img src="{html.escape(candidate.img_url)}" '
        f'alt="{html.escape(candidate.name)}" '
        f'style="max-width:100%;max-height:180px;border-radius:8px;object-fit:contain" />'
    )


def match_panel_html(summary: str, reference: str) -> str:
    """Fixed-height match + reference block; keeps live session panel from shifting."""
    stamp = datetime.now().strftime("%H:%M:%S")
    return (
        f"<div style='height:{MATCH_PANEL_HEIGHT}px;min-height:{MATCH_PANEL_HEIGHT}px;"
        f"max-height:{MATCH_PANEL_HEIGHT}px;overflow:hidden;"
        "display:flex;flex-direction:column;gap:8px;box-sizing:border-box'>"
        f"<div style='flex:0 0 auto;overflow:hidden'>{summary}</div>"
        "<div style='flex:1 1 auto;overflow:hidden;display:flex;"
        "align-items:center;justify-content:flex-start'>"
        f"{reference}</div>"
        f"<!-- {stamp} --></div>"
    )


def empty_match_panel(
    message: str = "Hold a piece still to identify…",
) -> str:
    return match_panel_html(
        f"<p style='margin:0;opacity:0.55;text-align:center;padding-top:40px'>"
        f"{html.escape(message)}</p>",
        "",
    )


def format_valued_rows(rows: list[list]) -> list[list]:
    """Make empty price cells readable in the Gradio table."""
    formatted: list[list] = []
    for part_id, part_name, color_name, count, unit_price, line_value in rows:
        formatted.append(
            [
                part_id,
                part_name,
                color_name,
                count,
                unit_price if unit_price is not None else "—",
                line_value if line_value is not None else "—",
            ]
        )
    return formatted


def html_table(
    headers: tuple[str, ...],
    rows: list[list],
    *,
    empty: str = "Nothing here yet.",
) -> str:
    if not rows:
        return f"<p style='opacity:0.7;margin:0'>{html.escape(empty)}</p>"
    th = "".join(
        f"<th style='text-align:left;padding:5px 8px;border-bottom:1px solid rgba(255,255,255,0.15);"
        f"white-space:nowrap'>{html.escape(h)}</th>"
        for h in headers
    )
    body = []
    for row in rows:
        tds = "".join(
            f"<td style='padding:5px 8px;border-bottom:1px solid rgba(255,255,255,0.06);"
            f"white-space:nowrap'>{html.escape(str(cell))}</td>"
            for cell in row
        )
        body.append(f"<tr>{tds}</tr>")
    stamp = datetime.now().strftime("%H:%M:%S")
    return (
        f"<div style='overflow-x:auto'>"
        f"<table style='width:100%;border-collapse:collapse;font-size:0.88em'>"
        f"<thead><tr>{th}</tr></thead>"
        f"<tbody>{''.join(body)}</tbody></table></div>"
        f"<!-- {stamp} -->"
    )


def inventory_table_html(rows: list[list]) -> str:
    return html_table(
        VALUED_INVENTORY_FIELDS,
        rows,
        empty="No scans yet — inventory will appear as you scan.",
    )


def scans_table_html(rows: list[list]) -> str:
    return html_table(SCAN_DISPLAY_FIELDS, rows, empty="No scans recorded yet.")


def shape_only_summary(note: str) -> str:
    return f"<p>{html.escape(note)}</p><p><em>Color runs after a shape match.</em></p>"
