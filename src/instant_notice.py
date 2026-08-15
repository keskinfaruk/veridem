"""
Condensed, fact-only notices for the instant change channel: shorter
renderings of exactly the same facts report.py produces, never new content.

    headline()      one line, used as the Atom entry title
    bluesky_text()  <=300 graphemes, used as the Bluesky post body

English, because this channel serves researchers doing a quick check of what
changed, who mostly work in English for this kind of release. A
Turkish-narrative audience is better served by long-form blog content.

Scoped to Türkiye only: posting every change across ~60 European geos would
be noise. filter_turkiye() is the gate every caller applies first.

Trend comparisons come from the series' own history via series_history(),
never from the diff row's old/new pair alone. That pair only means something
for REVISED; for a NEW_PERIOD (the common case) old_value is legitimately
blank, so relying on it would produce a bare, comparison-free headline for
the most common event this channel exists to report.
"""

import re

import pandas as pd

from report import (
    REF_AREA_LABELS,
    _prior_point,
    direction,
    format_number,
    generate_change_report,
    indicator_label,
    record,
    series_history,
    source_label,
)
from sanity import check_plausible_range, check_yoy_volatility

BLUESKY_MAX_GRAPHEMES = 300

# The medium-term comparison is a 10-year high/low rather than "vs the value
# N years ago": a single arbitrary past anchor is misleading for a volatile
# series, where an unlucky year during a mortality shock would make an
# otherwise flat decade look like a sharp move.
RECENT_WINDOW_YEARS = 10

# A single dataflow shedding more than this many values in one run is not a
# routine event on a channel that posts one fact at a time. It is either a
# genuine mass withdrawal, which deserves a human read before it is
# announced, or a pipeline fault presenting as one. Either way the notices
# are suppressed and the change report still carries every row, so the PR
# shows the full picture.
MAX_AUTO_POST_WITHDRAWALS = 10

# Which change classes get an instant notice. NEW_SERIES is excluded: a
# series debuting with its whole fetched history is either backfill noise or
# simply the start of tracking something already available, and the data
# alone cannot tell those apart from a genuine new publication. WITHDRAWN is
# rare and notable enough to keep.
NOTICE_CLASSES = {"NEW_PERIOD", "REVISED", "WITHDRAWN"}

# Curated per-release subset for source == 'tuik_press': one TurkStat press
# release can flip ~10-26 series at once, and posting each separately would
# be a burst rather than the steady one-fact-at-a-time cadence this channel
# keeps. Everything outside this set stays in the data bank and
# CHANGE_REPORT.md without posting.
#
# ASFR and HEALTHY_LIFE_YEARS are deliberately absent: both split across
# several age groups with no single national figure, and
# filter_posting_sources() matches on (indicator, sex) only, so curating
# either would make every age group separately eligible.
CURATED_PRESS_INDICATORS = {
    ("TFR", "T"),                          # Birth Statistics headline
    ("ADOLESCENT_FERTILITY_RATE", "T"),
    ("CBR", "T"),
    ("MEAN_AGE_CHILDBEARING", "T"),
    ("MEAN_AGE_FIRST_BIRTH", "T"),
    ("CDR", "T"),                          # Death and Causes of Death headline
    ("INFANT_MORTALITY_RATE", "T"),
    ("UNDER5_MORTALITY_RATE", "T"),
    ("INTERNAL_MIGRATION_RATE", "T"),      # Internal Migration headline
    ("TOTAL_POPULATION", "T"),             # ABPRS headline
    ("CRUDE_MARRIAGE_RATE", "T"),          # Marriage and Divorce headline
    ("CRUDE_DIVORCE_RATE", "T"),
    ("MEAN_AGE_FIRST_MARRIAGE", "M"),      # no combined-sex figure exists in
    ("MEAN_AGE_FIRST_MARRIAGE", "F"),      # the source table, so both post
    ("IMMIGRANTS", "T"),                   # International Migration headline
    ("EMIGRANTS", "T"),
}

# Stated inline only when a series actually carries a breakdown; omitted for
# the common sex='T' case.
SEX_LABELS = {"M": "men", "F": "women"}

# Y15T19-style bands only (ASFR's 7 age groups are the real case). An
# indicator like LIFE_EXPECTANCY_15 already says "at Age 15" in its own
# label, so restating its age code would be redundant.
_AGE_BAND_RE = re.compile(r"^Y(\d+)T(\d+)$")


def filter_turkiye(changes: pd.DataFrame) -> pd.DataFrame:
    """The one gate every caller applies before building notices."""
    if changes.empty:
        return changes
    return changes[(changes["ref_area"] == "TR") & (changes["change_class"].isin(NOTICE_CLASSES))]


def filter_posting_sources(changes: pd.DataFrame) -> pd.DataFrame:
    """Second gate, applied after filter_turkiye().

    source == 'tuik' (SDMX) never posts: tuik_press carries the same
    indicators sooner, so SDMX is fetched and stored for the archive only.
    source == 'tuik_press' is restricted to CURATED_PRESS_INDICATORS. Every
    other source (eurostat) passes through unrestricted.
    """
    if changes.empty:
        return changes
    is_tuik_sdmx = changes["source"] == "tuik"
    is_press = changes["source"] == "tuik_press"
    press_allowed = changes.apply(lambda r: (r["indicator"], r["sex"]) in CURATED_PRESS_INDICATORS, axis=1)
    return changes[~is_tuik_sdmx & (~is_press | press_allowed)]


def _age_band_label(age: str) -> str | None:
    m = _AGE_BAND_RE.match(age)
    return f"ages {m.group(1)}-{m.group(2)}" if m else None


def area_source_label(row: pd.Series) -> str:
    """'Türkiye, Eurostat, women, ages 15-19' -- area, source, and any sex or
    age-band breakdown in one parenthetical. TurkStat and Eurostat report
    slightly different numbers for the same indicator, so which one a figure
    came from is never left implicit. The age-band clause matters for ASFR,
    whose 7 series would otherwise produce identical labels."""
    parts = [REF_AREA_LABELS.get(row["ref_area"], row["ref_area"]), source_label(row["source"])]
    sex_label = SEX_LABELS.get(row["sex"])
    if sex_label:
        parts.append(sex_label)
    age_label = _age_band_label(row["age"])
    if age_label:
        parts.append(age_label)
    return ", ".join(parts)


def prior_comparison_clause(current_value: float, prior_period: str, prior_value: float, indicator: str) -> str:
    delta = current_value - prior_value
    trend = "up" if delta > 0 else "down" if delta < 0 else "unchanged"
    pct = f" ({delta / prior_value * 100:+.1f}%)" if prior_value else ""
    return f"{trend} from {format_number(prior_value, indicator)} in {prior_period}{pct}"


def _is_alltime_extreme(history: pd.Series, current_value: float) -> bool:
    if (history == history.max()).sum() == 1 and current_value == history.max():
        return True
    return (history == history.min()).sum() == 1 and current_value == history.min()


def recent_extreme_note(history: pd.Series, current_period: str, current_value: float) -> str | None:
    """Whether current_value is the highest/lowest of the trailing
    RECENT_WINDOW_YEARS. Position-aware, so it stays correct for a revision
    to a non-latest period, unlike report.record()/direction(). Suppressed
    when the value is also the all-time record: that is the strictly stronger
    fact, no need to state both."""
    if _is_alltime_extreme(history, current_value):
        return None
    try:
        cutoff = str(int(current_period) - RECENT_WINDOW_YEARS + 1)
    except (ValueError, TypeError):
        return None  # non-numeric period (a multi-year range) -- skip, don't guess
    window = history[(history.index >= cutoff) & (history.index <= current_period)]
    if len(window) < 2:
        return None
    if (window == window.max()).sum() == 1 and current_value == window.max():
        return f"Highest in {RECENT_WINDOW_YEARS} years"
    if (window == window.min()).sum() == 1 and current_value == window.min():
        return f"Lowest in {RECENT_WINDOW_YEARS} years"
    return None


def sanity_flags(indicator: str, value: float, history: pd.Series, is_latest: bool) -> list[str]:
    """Compact warn-only flags for public text. build_notices() builds
    feed_content with include_sanity=False, so this terse "look closer" flag
    is the only sanity signal public readers see.

    check_plausible_range inspects `value` directly and always runs;
    check_yoy_volatility reads history's last entry as the current one, so it
    only runs when `is_latest`.
    """
    flags = []
    if check_plausible_range(indicator, value)[0] != "ok":
        flags.append("outside plausible range")
    if is_latest and len(history) > 1 and check_yoy_volatility(indicator, history)[0] != "ok":
        flags.append("unusually large year-on-year move")
    return flags


def compose_post(lead: str, row: pd.Series, history: pd.Series, url: str | None, include_sanity: bool) -> str:
    """Build a <=300-grapheme post from `lead` plus trend, record, 10-year
    extreme and sanity clauses, each appended only if it still fits.

    Shared by bluesky_text() and baseline_notice.baseline_bluesky_text(),
    which differ only in their lead sentence.

    Bluesky counts graphemes rather than bytes or UTF-16 units. For the plain
    ASCII plus Turkish-letter text produced here (no combining marks, no
    emoji) Python's len() on the str is an accurate proxy.
    """
    text = lead + "."
    url_budget = len(url) + 1 if url else 0

    def try_append(extra: str) -> None:
        nonlocal text
        if len(text) + len(extra) <= BLUESKY_MAX_GRAPHEMES - url_budget:
            text += extra

    # direction()/record() read the series' last entry as "the current one",
    # correct whenever the reported period is the newest on file (always true
    # for a fresh NEW_PERIOD) but wrong for a revision to an older period.
    # Skipped entirely in that case rather than risk describing the wrong one.
    is_latest = len(history) > 0 and row["time_period"] == history.index[-1]
    if len(history) > 1 and is_latest:
        try_append(f" {direction(history)}.")
        record_note = record(history)
        if record_note:
            try_append(f" {record_note}.")

    if len(history) > 1:
        recent_note = recent_extreme_note(history, row["time_period"], row["new_value"])
        if recent_note:
            try_append(f" {recent_note}.")

    if include_sanity:
        for flag in sanity_flags(row["indicator"], row["new_value"], history, is_latest):
            try_append(f" ⚠ {flag}.")

    if url:
        text = f"{text} {url}"

    if len(text) > BLUESKY_MAX_GRAPHEMES:
        if url:
            budget = BLUESKY_MAX_GRAPHEMES - len(url) - 5  # "... " + link
            text = text[: max(budget, 0)].rstrip() + "... " + url
        else:
            text = text[: BLUESKY_MAX_GRAPHEMES - 1].rstrip() + "…"
    return text


def _value_headline(row: pd.Series, history: pd.Series) -> str:
    value, period, indicator = row["new_value"], row["time_period"], row["indicator"]
    text = (
        f"{indicator_label(indicator)} ({area_source_label(row)}) "
        f"{period}: {format_number(value, indicator)}"
    )
    if row["change_class"] == "REVISED" and pd.notna(row["old_value"]):
        text += f", revised from {format_number(row['old_value'], indicator)}"

    prior_period, prior_value = _prior_point(history, period)
    if prior_period is not None:
        text += ", " + prior_comparison_clause(value, prior_period, prior_value, indicator)
    return text


def headline(row: pd.Series, history: pd.Series | None = None) -> str:
    """One-line, fact-only summary: the Atom entry title, and the base of the
    Bluesky text. `history` should be the series' full history including the
    current point; omit it only for WITHDRAWN, where no value comparison
    applies."""
    if row["change_class"] == "WITHDRAWN":
        return (
            f"{indicator_label(row['indicator'])} ({area_source_label(row)}) "
            f"{row['time_period']}: value withdrawn "
            f"(was {format_number(row['old_value'], row['indicator'])})"
        )
    return _value_headline(row, history if history is not None else pd.Series(dtype=float))


def _history_for(row: pd.Series, con) -> pd.Series:
    if row["change_class"] not in ("NEW_PERIOD", "REVISED"):
        return pd.Series(dtype=float)
    return series_history(con, row, row["new_snapshot_id"])


def bluesky_text(row: pd.Series, con, url: str | None = None) -> str:
    history = _history_for(row, con)
    return compose_post(
        headline(row, history),
        row,
        history,
        url,
        include_sanity=row["change_class"] in ("NEW_PERIOD", "REVISED"),
    )


def suppress_mass_withdrawals(changes: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Drop every notice from a dataflow that withdrew more than
    MAX_AUTO_POST_WITHDRAWALS values in this run.

    Counted over all of `changes`, before the Türkiye filter: the fault or
    event is dataflow-wide even when only a few of its rows would post.
    Returns the surviving rows and one description per suppressed dataflow.
    """
    if changes.empty or "change_class" not in changes:
        return changes, []
    withdrawn = changes[changes["change_class"] == "WITHDRAWN"]
    if withdrawn.empty:
        return changes, []

    counts = withdrawn.groupby(["source", "dataflow_id"]).size()
    over = counts[counts > MAX_AUTO_POST_WITHDRAWALS]
    if over.empty:
        return changes, []

    notes = [f"{src}/{flow}: {n} withdrawals" for (src, flow), n in over.items()]
    blocked = set(over.index)
    keep = ~changes.set_index(["source", "dataflow_id"]).index.isin(blocked)
    return changes[keep], notes


def build_notices(
    changes: pd.DataFrame, con, base_url: str | None = None
) -> tuple[list[dict], list[str]]:
    """One notice per Türkiye change, ready for feed.py / bluesky_client.py.

    `base_url`, if given, becomes a per-notice link appended to the Bluesky
    text. Returns (notices, suppressed) where `suppressed` describes any
    dataflow held back by suppress_mass_withdrawals().
    """
    changes, suppressed = suppress_mass_withdrawals(changes)
    tr_changes = filter_posting_sources(filter_turkiye(changes))
    notices = []
    for _, row in tr_changes.iterrows():
        history = _history_for(row, con)
        notices.append(
            {
                "title": headline(row, history),
                "bluesky_text": compose_post(
                    headline(row, history), row, history, base_url,
                    include_sanity=row["change_class"] in ("NEW_PERIOD", "REVISED"),
                ),
                "feed_content": generate_change_report(
                    pd.DataFrame([row]), con, include_sanity=False, public=True
                ),
                "indicator": row["indicator"],
                "ref_area": row["ref_area"],
                "time_period": row["time_period"],
                "change_class": row["change_class"],
                "snapshot_id": row["new_snapshot_id"],
            }
        )
    return notices, suppressed
