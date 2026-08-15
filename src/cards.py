"""
The curated card set: one card per watched series, always showing its current
value.

data/curated_cards.csv is the list. It is the single definition of what may be
published at all, used in two places:

    - here, to build the card set the website shows
    - instant_notice.py, to decide whether a change may post to Bluesky

The list itself lives in curated.py so both can import it without a cycle.

Cards are rebuilt from the data bank on every run rather than accumulated in a
state file, so the page can never drift from what is actually stored, and a
card is replaced in place instead of a new one being appended. The page
therefore settles at exactly len(CARDS) entries.
"""

import html
from pathlib import Path

import pandas as pd

from curated import REF_AREA, load_curated
from instant_notice import (
    area_source_label,
    prior_comparison_clause,
    recent_extreme_note,
)
from report import (
    RECENT_SERIES_LENGTH,
    _prior_point,
    direction,
    format_number,
    indicator_label,
    record,
    series_history,
    source_label,
)


def _latest_row(con, spec) -> pd.Series | None:
    """The newest observation for one card's series, from its latest snapshot."""
    df = con.execute(
        "SELECT * FROM observations WHERE source=? AND indicator=? AND sex=? AND age=? "
        "AND ref_area=? ORDER BY snapshot_id DESC, time_period DESC LIMIT 1",
        [spec["source"], spec["indicator"], spec["sex"], spec["age"], REF_AREA],
    ).df()
    if df.empty:
        return None
    row = df.iloc[0].copy()
    row["new_value"] = row["obs_value"]
    row["new_snapshot_id"] = row["snapshot_id"]
    return row


def build_card(con, spec) -> dict | None:
    """One card: the current value, how it moved, and the detail shown when the
    card is expanded. None when the bank holds nothing for that series yet."""
    row = _latest_row(con, spec)
    if row is None:
        return None

    indicator, period, value = row["indicator"], row["time_period"], row["new_value"]
    history = series_history(con, row, row["new_snapshot_id"])

    detail = []
    prior_period, prior_value = _prior_point(history, period)
    if prior_period is not None:
        delta = value - prior_value
        pct = f" ({delta / prior_value * 100:+.1f}%)" if prior_value else ""
        detail.append(("Previous", f"{format_number(prior_value, indicator)} ({prior_period})"))
        detail.append(("Change", f"{format_number(delta, indicator, signed=True)}{pct}"))
    if len(history) > 1:
        detail.append(("Direction", direction(history)))
        note = record(history) or recent_extreme_note(history, period, value)
        if note:
            detail.append(("Standing", note))
        detail.append(("History", f"{history.index.min()} to {history.index.max()} "
                                  f"({len(history)} periods)"))

    recent = history.tail(RECENT_SERIES_LENGTH)
    return {
        "source": row["source"],
        "indicator": indicator,
        "sex": row["sex"],
        "age": row["age"],
        "domain": spec["domain"],
        "label": indicator_label(indicator),
        "qualifier": area_source_label(row),
        "source_label": source_label(row["source"]),
        "period": period,
        "value": format_number(value, indicator),
        "unit": row["unit"],
        "summary": _summary(row, history),
        "detail": detail,
        "recent": [(p, format_number(v, indicator)) for p, v in recent.items()],
    }


def _summary(row, history) -> str:
    """The one-line sentence shown on the collapsed card, matching the wording
    of the Bluesky post for the same figure."""
    indicator, period, value = row["indicator"], row["time_period"], row["new_value"]
    text = f"{format_number(value, indicator)} in {period}"
    prior_period, prior_value = _prior_point(history, period)
    if prior_period is not None:
        text += ", " + prior_comparison_clause(value, prior_period, prior_value, indicator)
    return text


def build_cards(con, path: Path | None = None) -> list[dict]:
    """Every curated card that the bank can currently fill, in page order."""
    cards = []
    for _, spec in load_curated(path).iterrows():
        card = build_card(con, spec)
        if card:
            cards.append(card)
    return cards


def main() -> int:
    from schema import connect

    cards = build_cards(connect())
    curated = len(load_curated())
    print(f"{len(cards)} of {curated} curated cards can be filled from the bank\n")
    domain = None
    for c in cards:
        if c["domain"] != domain:
            domain = c["domain"]
            print(f"-- {domain}")
        print(f"   {c['label']} ({c['qualifier']})")
        print(f"      {c['summary']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


# --- rendering ---------------------------------------------------------------
# The website page is a static file in another repo with no build step, so the
# markup has to be self-contained: <details> gives the expand-on-click
# behaviour with no JavaScript, and one <style> block travels with the region
# rather than requiring a change to the site's own stylesheet.

REGION_START = "<!-- veridem:cards:start -->"
REGION_END = "<!-- veridem:cards:end -->"

CARD_STYLE = """<style>
.vd-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(15rem,1fr));gap:.75rem;margin:0 0 1.75rem;padding:0;list-style:none}
.vd-dom{margin:1.5rem 0 .6rem;font-size:.72rem;letter-spacing:.13em;text-transform:uppercase;opacity:.6}
.vd-card{border:1px solid rgba(128,128,128,.28);border-radius:3px;padding:.7rem .85rem}
.vd-card>summary{cursor:pointer;list-style:none;display:block}
.vd-card>summary::-webkit-details-marker{display:none}
.vd-card>summary::after{content:"+";float:right;opacity:.45;font-weight:400}
.vd-card[open]>summary::after{content:"\2212"}
.vd-name{display:block;font-size:.82rem;opacity:.75;padding-right:1rem}
.vd-val{display:block;font-size:1.5rem;line-height:1.25;font-variant-numeric:tabular-nums}
.vd-yr{font-size:.75rem;opacity:.55;margin-left:.35rem}
.vd-sum{display:block;font-size:.78rem;opacity:.7;margin-top:.3rem}
.vd-body{margin-top:.7rem;padding-top:.6rem;border-top:1px solid rgba(128,128,128,.22);font-size:.79rem}
.vd-body dl{display:grid;grid-template-columns:auto 1fr;gap:.18rem .8rem;margin:0}
.vd-body dt{opacity:.55}
.vd-body dd{margin:0;font-variant-numeric:tabular-nums}
.vd-hist{margin-top:.55rem;font-variant-numeric:tabular-nums}
.vd-hist span{display:inline-block;margin-right:.7rem;opacity:.75}
.vd-src{margin-top:.55rem;font-size:.72rem;opacity:.5}
</style>"""


def _card_html(card: dict) -> str:
    detail = "".join(
        f"<dt>{html.escape(k)}</dt><dd>{html.escape(v)}</dd>" for k, v in card["detail"]
    )
    hist = "".join(
        f"<span>{html.escape(p)} {html.escape(v)}</span>" for p, v in card["recent"]
    )
    return (
        f'<li><details class="vd-card">'
        f'<summary>'
        f'<span class="vd-name">{html.escape(card["label"])}</span>'
        f'<span class="vd-val">{html.escape(card["value"])}'
        f'<span class="vd-yr">{html.escape(card["period"])}</span></span>'
        f'<span class="vd-sum">{html.escape(card["summary"])}</span>'
        f"</summary>"
        f'<div class="vd-body"><dl>{detail}</dl>'
        f'<div class="vd-hist">{hist}</div>'
        f'<div class="vd-src">{html.escape(card["qualifier"])}</div>'
        f"</div></details></li>"
    )


def render_region(cards: list[dict]) -> str:
    """The full managed block: one card per curated series, grouped by domain.

    Rebuilt whole on every run, which is what gives the page its upsert
    behaviour: a card is replaced in place rather than a second one appended,
    so the page always shows exactly one entry per curated series.
    """
    parts = [REGION_START, CARD_STYLE]
    domain = None
    for card in cards:
        if card["domain"] != domain:
            if domain is not None:
                parts.append("</ul>")
            domain = card["domain"]
            parts.append(f'<h2 class="vd-dom">{html.escape(domain)}</h2><ul class="vd-grid">')
        parts.append(_card_html(card))
    if domain is not None:
        parts.append("</ul>")
    parts.append(
        "<!-- Rebuilt by veridem on every run. One card per watched series, "
        "always showing its current value. -->"
    )
    parts.append(REGION_END)
    return "\n".join(parts)
