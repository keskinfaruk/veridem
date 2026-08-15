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
therefore settles at exactly the size of the curated list.
"""

import html
from pathlib import Path

import pandas as pd

from curated import REF_AREA, load_curated
from instant_notice import (
    SEX_LABELS,
    _age_band_label,
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


def _summary(row, history) -> str:
    """How the figure moved, for the collapsed card. The value and its period
    are already shown large above it, so this states only the comparison."""
    prior_period, prior_value = _prior_point(history, row["time_period"])
    if prior_period is None:
        return "first value on record"
    return prior_comparison_clause(row["new_value"], prior_period, prior_value, row["indicator"])


def _breakdown(row) -> str:
    """Only the sex or age split, when the series has one. Area and source are
    carried by the card's colour and the legend, so repeating them on every card
    is noise; without this the three life-expectancy cards would read alike."""
    parts = [p for p in (SEX_LABELS.get(row["sex"]), _age_band_label(row["age"])) if p]
    return ", ".join(parts)


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
        detail.append((
            "History",
            f"{history.index.min()} to {history.index.max()} ({len(history)} periods)",
        ))

    recent = history.tail(RECENT_SERIES_LENGTH)
    return {
        "source": row["source"],
        "indicator": indicator,
        "sex": row["sex"],
        "age": row["age"],
        "domain": spec["domain"],
        "label": indicator_label(indicator),
        "breakdown": _breakdown(row),
        "source_label": source_label(row["source"]),
        "period": period,
        "value": format_number(value, indicator),
        "unit": row["unit"],
        "summary": _summary(row, history),
        "detail": detail,
        "recent": [(p, format_number(v, indicator)) for p, v in recent.items()],
    }


def build_cards(con, path: Path | None = None) -> list[dict]:
    """Every curated card the bank can currently fill, in page order."""
    cards = []
    for _, spec in load_curated(path).iterrows():
        card = build_card(con, spec)
        if card:
            cards.append(card)
    return cards


# --- rendering ---------------------------------------------------------------
# The page is a static file in another repo with no build step, so the markup
# must be self-contained: <details> gives expand-on-click without JavaScript,
# and one <style> block travels with the region rather than requiring a change
# to the site's own stylesheet.
#
# Colours come from that stylesheet's own tokens (--bg-raised, --border,
# --text-muted), so both of the site's themes are inherited rather than guessed
# at. Only the two source hues are defined here, scoped to .vd so nothing global
# is touched, with a dark variant matching the site's own prefers-color-scheme
# switch.

REGION_START = "<!-- veridem:start -->"
REGION_END = "<!-- veridem:end -->"

# Source identity is carried by colour plus the legend, which is why a card no
# longer repeats "Türkiye, TurkStat press release" on every entry.
SOURCE_CLASS = {"tuik_press": "s-tuik", "tuik": "s-tuik", "eurostat": "s-estat"}
LEGEND = [
    ("s-tuik", "TurkStat press releases", "TÜİK haber bültenleri"),
    ("s-estat", "Eurostat", "Eurostat"),
]

CARD_STYLE = """<style>
.vd{--vd-tuik:#2f6b58;--vd-estat:#3f5b7d;--vd-tint:rgba(0,0,0,.02)}
@media (prefers-color-scheme:dark){.vd{--vd-tuik:#7cc2a8;--vd-estat:#93b4dd;--vd-tint:rgba(255,255,255,.03)}}
.vd .s-tuik{--vd-c:var(--vd-tuik)}
.vd .s-estat{--vd-c:var(--vd-estat)}

.vd-legend{display:flex;flex-wrap:wrap;gap:1.1rem;margin:0 0 2rem;padding:0;list-style:none;
 font-size:.82rem;color:var(--text-muted)}
.vd-legend li{display:flex;align-items:center;gap:.45rem}
.vd-legend i{width:.62rem;height:.62rem;border-radius:50%;background:var(--vd-c);flex:none}

.vd-dom{font-size:.72rem;letter-spacing:.14em;text-transform:uppercase;color:var(--text-muted);
 font-weight:400;margin:2.2rem 0 .75rem;padding-bottom:.4rem;border-bottom:1px solid var(--border)}
.vd-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(16.5rem,1fr));
 gap:.7rem;margin:0;padding:0;list-style:none}

.vd-card{background:var(--bg-raised);border:1px solid var(--border);
 border-left:3px solid var(--vd-c);border-radius:4px;overflow:hidden;
 transition:border-color .15s}
.vd-card:hover{border-color:var(--vd-c)}
.vd-card>summary{cursor:pointer;list-style:none;padding:.85rem 1rem;position:relative}
.vd-card>summary::-webkit-details-marker{display:none}
.vd-card>summary:focus-visible{outline:2px solid var(--vd-c);outline-offset:-2px}

/* Chevron drawn from borders: no font glyph, and no CSS escape to get wrong. */
.vd-card>summary::after{content:"";position:absolute;right:1rem;top:1.15rem;
 width:.4rem;height:.4rem;border-right:1.5px solid var(--text-muted);
 border-bottom:1.5px solid var(--text-muted);transform:rotate(45deg);
 opacity:.5;transition:transform .18s,opacity .18s}
.vd-card[open]>summary::after{transform:rotate(225deg);opacity:.9}
@media (prefers-reduced-motion:reduce){
 .vd-card,.vd-card>summary::after{transition:none}
}

.vd-name{display:block;font-size:.83rem;line-height:1.35;color:var(--text-muted);
 padding-right:1.4rem;margin-bottom:.3rem}
.vd-split{color:var(--vd-c)}
.vd-val{display:block;font-size:1.6rem;line-height:1.15;font-variant-numeric:tabular-nums;
 letter-spacing:-.01em}
.vd-yr{font-size:.78rem;color:var(--text-muted);margin-left:.45rem;letter-spacing:0}
.vd-sum{display:block;font-size:.78rem;color:var(--text-muted);margin-top:.35rem}

.vd-body{padding:0 1rem 1rem;font-size:.79rem;background:var(--vd-tint)}
.vd-body dl{display:grid;grid-template-columns:auto 1fr;gap:.22rem .9rem;margin:0;
 padding-top:.8rem;border-top:1px solid var(--border)}
.vd-body dt{color:var(--text-muted)}
.vd-body dd{margin:0;font-variant-numeric:tabular-nums}
.vd-hist{margin-top:.7rem;padding-top:.6rem;border-top:1px solid var(--border);
 font-variant-numeric:tabular-nums;display:flex;flex-wrap:wrap;gap:.15rem .9rem}
.vd-hist span{color:var(--text-muted)}
.vd-hist b{font-weight:400;color:var(--text)}
</style>"""


def _card_html(card: dict) -> str:
    split = (
        f'<span class="vd-split"> &middot; {html.escape(card["breakdown"])}</span>'
        if card["breakdown"]
        else ""
    )
    detail = "".join(
        f"<dt>{html.escape(k)}</dt><dd>{html.escape(v)}</dd>" for k, v in card["detail"]
    )
    hist = "".join(
        f"<span>{html.escape(p)} <b>{html.escape(v)}</b></span>" for p, v in card["recent"]
    )
    return (
        f'<li><details class="vd-card {SOURCE_CLASS.get(card["source"], "s-tuik")}">'
        f"<summary>"
        f'<span class="vd-name">{html.escape(card["label"])}{split}</span>'
        f'<span class="vd-val">{html.escape(card["value"])}'
        f'<span class="vd-yr">{html.escape(card["period"])}</span></span>'
        f'<span class="vd-sum">{html.escape(card["summary"])}</span>'
        f"</summary>"
        f'<div class="vd-body"><dl>{detail}</dl><div class="vd-hist">{hist}</div></div>'
        f"</details></li>"
    )


def _legend_html() -> str:
    items = "".join(
        f'<li class="{cls}"><i></i><span data-en>{html.escape(en)}</span>'
        f"<span data-tr>{html.escape(tr)}</span></li>"
        for cls, en, tr in LEGEND
    )
    return f'<ul class="vd-legend">{items}</ul>'


def render_region(cards: list[dict]) -> str:
    """The whole veridem-owned block: heading, description, colour legend, and
    the card set, grouped by domain.

    Rebuilt whole on every run, which is what gives the page its upsert
    behaviour: a card is replaced in place rather than a second one appended, so
    the page always shows exactly one entry per curated series.
    """
    parts = [
        REGION_START,
        CARD_STYLE,
        '<div class="vd">',
        "<h1 data-en>Indicators</h1>",
        "<h1 data-tr>Göstergeler</h1>",
        '<p data-en class="text-muted">Current values for every demographic indicator veridem '
        "watches for Türkiye. Each card is replaced in place when a newer figure is published. "
        'A machine-readable <a href="./changes.xml">Atom feed</a> records each change as it '
        "happens.</p>",
        '<p data-tr class="text-muted">Veridem\'in Türkiye için izlediği her demografik '
        "göstergenin güncel değeri. Yeni bir değer yayımlandığında kart yerinde güncellenir. "
        'Değişiklikler ayrıca <a href="./changes.xml">Atom akışına</a> kaydedilir.</p>',
        _legend_html(),
    ]

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

    parts += [
        "</div>",
        "<!-- Rebuilt by veridem on every run. One card per watched series, "
        "always showing its current value. -->",
        REGION_END,
    ]
    return "\n".join(parts)


def main() -> int:
    from schema import connect

    cards = build_cards(connect())
    print(f"{len(cards)} of {len(load_curated())} curated cards can be filled from the bank\n")
    domain = None
    for c in cards:
        if c["domain"] != domain:
            domain = c["domain"]
            print(f"-- {domain}")
        split = f" ({c['breakdown']})" if c["breakdown"] else ""
        print(f"   {c['label']}{split}: {c['value']} ({c['period']}), {c['summary']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
