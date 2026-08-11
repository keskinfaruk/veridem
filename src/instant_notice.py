"""
Condensed, fact-only notices for the instant change channel. Two output
lengths from the same underlying data as report.py -- deliberately no new
content generation, just shorter renderings of the same facts:

    - headline()      one line, used as the Atom feed entry title
    - bluesky_text()  <=300 graphemes, used as the Bluesky post body

English, deliberately: this channel's audience is researchers/demographers
doing a quick check of what changed, who mostly work in English for this
kind of release (TurkStat's own international releases, Eurostat, academic
demography all default to it); a Turkish-narrative audience is better
served by long-form blog content instead.

Scoped to Turkiye only (ref_area == 'TR'). filter_turkiye() is the one gate
every caller applies before building notices from a diff_observations()
-shaped DataFrame -- posting every change across ~60 European geos would
be noise, not signal.

Trend comparisons are derived from the series' own historical values via
series_history(), never from the diff row's old_value/new_value pair
alone -- that pair only means something for REVISED (same period, value
changed). For a genuine NEW_PERIOD event (a new year's data appearing --
the common case) old_value is legitimately blank, since no row existed for
that period before; relying on it alone would silently produce a bare,
comparison-free headline for the most common real event this channel
exists to report.

The medium-term comparison is a 10-year high/low, not "vs the value N
years ago": comparing to a single arbitrary past point is misleading for a
genuinely volatile series (life expectancy is the clear case -- an unlucky
single-year anchor during a mortality shock would make an otherwise flat
decade look like a sharp move). A 10-year high/low is self-explanatory and
robust to which exact year gets compared against.
"""

import re

import pandas as pd

from report import (
    INDICATOR_DECIMALS,
    INDICATOR_LABELS,
    REF_AREA_LABELS,
    _prior_point,
    direction,
    format_number as _format_number,
    generate_change_report,
    indicator_label as _indicator_label,
    record,
    series_history,
    source_label,
)
from sanity import check_plausible_range, check_yoy_volatility

BLUESKY_MAX_GRAPHEMES = 300
RECENT_WINDOW_YEARS = 10

# INDICATOR_DECIMALS / _format_number / _prior_point / INDICATOR_LABELS /
# REF_AREA_LABELS / _indicator_label actually live in report.py, so
# CHANGE_REPORT.md and this module's feed_content share the exact same
# per-indicator formatting and prior-year lookup -- re-imported here under
# their original names so every call site in this file and in
# baseline_notice.py keeps working unchanged.

# Which change classes get an instant notice at all. NEW_SERIES is
# deliberately excluded: a series debuting with its whole fetched history
# attached isn't "TurkStat/Eurostat published something new today" -- it's
# either backfill noise or just starting to track an indicator that was
# already accessible, and the data alone can't tell those two apart from a
# genuine new publication. WITHDRAWN is rare and notable enough to keep.
NOTICE_CLASSES = {"NEW_PERIOD", "REVISED", "WITHDRAWN"}

# Curated per-release subset for source == 'tuik_press': a single TurkStat
# press release can flip ~10-26 series at once (the mortality release alone
# is 10 indicators x up to 3 sexes). Posting every one separately would be
# a burst, not the steady one-fact-at-a-time cadence every other post on
# this channel has. Bundling them into one multi-fact post was considered
# and rejected -- it breaks the "one number, no interpretation" simplicity
# every existing post has. Instead: only these headline indicators (Total
# sex only) are eligible for an instant notice at all when source is
# 'tuik_press' -- everything else from that release stays fully visible in
# the data bank and CHANGE_REPORT.md, it just doesn't trigger a public post.
CURATED_PRESS_INDICATORS = {
    ("TFR", "T"),                          # Birth Statistics release headline
    ("ADOLESCENT_FERTILITY_RATE", "T"),    # Birth Statistics
    ("CDR", "T"),                          # Death and Causes of Death release headline
    ("INFANT_MORTALITY_RATE", "T"),        # Death and Causes of Death
    ("UNDER5_MORTALITY_RATE", "T"),        # Death and Causes of Death
    ("INTERNAL_MIGRATION_RATE", "T"),      # Internal Migration Statistics release headline
}


def filter_curated_press(changes: pd.DataFrame) -> pd.DataFrame:
    """Second gate, run after filter_turkiye() in build_notices() below --
    restricts source=='tuik_press' rows to CURATED_PRESS_INDICATORS; every
    other source passes through unrestricted, since a single SDMX/Eurostat
    dataflow update has never produced this kind of same-day multi-series
    burst in practice.
    """
    if changes.empty:
        return changes
    is_press = changes["source"] == "tuik_press"
    allowed = changes.apply(lambda r: (r["indicator"], r["sex"]) in CURATED_PRESS_INDICATORS, axis=1)
    return changes[~is_press | allowed]


# Some indicators (e.g. mean age at first marriage) carry separate male and
# female series -- a real run posts each as its own notice, identical
# wording apart from the number, so which one is which is never left to be
# inferred. Omitted entirely for sex='T' (the common case, no breakdown).
SEX_LABELS = {"M": "men", "F": "women"}

# Y15T19-style age-band codes -- ASFR's 7 age groups are the real case
# (Y15T19 .. Y45T49). Only bands like this need stating explicitly: an
# indicator like LIFE_EXPECTANCY_15 already says "at Age 15" in its own
# name (INDICATOR_LABELS), so restating its age code (Y15) would be
# redundant, not clarifying -- this only fires for a genuine T-to-T range.
_AGE_BAND_RE = re.compile(r"^Y(\d+)T(\d+)$")


def _age_band_label(age: str) -> str | None:
    m = _AGE_BAND_RE.match(age)
    return f"ages {m.group(1)}-{m.group(2)}" if m else None


def filter_turkiye(changes: pd.DataFrame) -> pd.DataFrame:
    """The one gate every caller applies before building notices."""
    if changes.empty:
        return changes
    return changes[(changes["ref_area"] == "TR") & (changes["change_class"].isin(NOTICE_CLASSES))]


def _area_source_label(row: pd.Series) -> str:
    """'Türkiye, Eurostat' -- area, source, and (when the series has a sex
    and/or age-band breakdown) sex/age, together in one parenthetical.
    TurkStat and Eurostat report slightly different numbers for the same
    indicator (confirmed, e.g. CBR), so which one a figure came from is
    never left implicit -- same reasoning as report.py's source_label(),
    just folded into this channel's compact one-line format instead of a
    separate line. The age-band clause matters for ASFR, which has 7
    age-specific series that would otherwise produce a byte-for-byte
    identical label apart from the number.
    """
    area = REF_AREA_LABELS.get(row["ref_area"], row["ref_area"])
    parts = [area, source_label(row["source"])]
    sex_label = SEX_LABELS.get(row["sex"])
    if sex_label:
        parts.append(sex_label)
    age_label = _age_band_label(row["age"])
    if age_label:
        parts.append(age_label)
    return ", ".join(parts)


def _prior_comparison_clause(current_value: float, prior_period: str, prior_value: float, indicator: str) -> str:
    prior_str = _format_number(prior_value, indicator)
    delta = current_value - prior_value
    trend = "up" if delta > 0 else "down" if delta < 0 else "unchanged"
    pct = f" ({delta / prior_value * 100:+.1f}%)" if prior_value else ""
    return f"{trend} from {prior_str} in {prior_period}{pct}"


def _is_alltime_extreme(history: pd.Series, current_value: float) -> bool:
    if (history == history.max()).sum() == 1 and current_value == history.max():
        return True
    return (history == history.min()).sum() == 1 and current_value == history.min()


def _recent_extreme_note(history: pd.Series, current_period: str, current_value: float) -> str | None:
    """Note when current_value is the highest/lowest value in the trailing
    RECENT_WINDOW_YEARS years -- position-aware (works correctly even for a
    non-latest reported period, unlike report.py's record()/direction()),
    and suppressed whenever the same value is *also* the all-time record:
    that's the strictly stronger fact, no need to state both.
    """
    if _is_alltime_extreme(history, current_value):
        return None
    try:
        cutoff = str(int(current_period) - RECENT_WINDOW_YEARS + 1)
    except (ValueError, TypeError):
        return None  # non-numeric period (e.g. quarterly) -- skip, don't guess
    window = history[(history.index >= cutoff) & (history.index <= current_period)]
    if len(window) < 2:
        return None
    if (window == window.max()).sum() == 1 and current_value == window.max():
        return f"Highest in {RECENT_WINDOW_YEARS} years"
    if (window == window.min()).sum() == 1 and current_value == window.min():
        return f"Lowest in {RECENT_WINDOW_YEARS} years"
    return None


def _value_headline(row: pd.Series, history: pd.Series) -> str:
    area_source = _area_source_label(row)
    value = row["new_value"]
    current_period = row["time_period"]
    label = _indicator_label(row["indicator"])
    text = f"{label} ({area_source}) {current_period}: {_format_number(value, row['indicator'])}"

    if row["change_class"] == "REVISED" and pd.notna(row["old_value"]):
        old_str = _format_number(row["old_value"], row["indicator"])
        text += f", revised from {old_str}"

    prior_period, prior_value = _prior_point(history, current_period)
    if prior_period is not None:
        text += ", " + _prior_comparison_clause(value, prior_period, prior_value, row["indicator"])

    return text


def headline(row: pd.Series, history: pd.Series | None = None) -> str:
    """One-line, fact-only summary. Used as the Atom entry title, and as
    the base of the Bluesky post text. `history` should be the series'
    full history including the current point (series_history()'s return
    value) for NEW_PERIOD/REVISED rows -- omit only for WITHDRAWN, where no
    value comparison applies.
    """
    if row["change_class"] == "WITHDRAWN":
        area_source = _area_source_label(row)
        label = _indicator_label(row["indicator"])
        old_str = _format_number(row["old_value"], row["indicator"])
        return f"{label} ({area_source}) {row['time_period']}: value withdrawn (was {old_str})"
    return _value_headline(row, history if history is not None else pd.Series(dtype=float))


def _sanity_flags(indicator: str, value: float, history: pd.Series, is_latest: bool) -> list[str]:
    """Compact WARN-only flags for the public Bluesky/headline text.
    build_notices() below builds feed_content with include_sanity=False --
    full [ok]/[warn] detail is for CHANGE_REPORT.md's reviewer audience,
    not the public feed -- so this terse "look closer at this one" flag is
    the only sanity-check signal public readers see at all.
    check_plausible_range checks `value` directly, so it always runs;
    check_yoy_volatility reads history's last entry as "the current one"
    the same way direction()/record() do, so it only runs when `is_latest`.
    """
    flags = []
    status, _ = check_plausible_range(indicator, value)
    if status != "ok":
        flags.append("outside plausible range")
    if is_latest and len(history) > 1:
        status, _ = check_yoy_volatility(indicator, history)
        if status != "ok":
            flags.append("unusually large year-on-year move")
    return flags


def bluesky_text(row: pd.Series, con, url: str | None = None) -> str:
    """Fact-only text, <=300 graphemes: the headline, plus direction/streak,
    record, and sanity-flag clauses, each added only if there's still room.
    Bluesky counts graphemes, not bytes or UTF-16 units -- for the plain
    ASCII + Turkish-letter text this produces (no combining marks, no
    emoji), Python's len() on the str is an accurate proxy, one Python
    character per grapheme.
    """
    history = pd.Series(dtype=float)
    if row["change_class"] in ("NEW_PERIOD", "REVISED"):
        history = series_history(con, row, row["new_snapshot_id"])

    text = headline(row, history) + "."
    url_budget = len(url) + 1 if url else 0

    def try_append(extra: str) -> None:
        nonlocal text
        if len(text) + len(extra) <= BLUESKY_MAX_GRAPHEMES - url_budget:
            text += extra

    # direction()/record() (report.py) read the series' *last* entry as
    # "the current one" -- correct whenever the reported period is the
    # newest one on file, which is the overwhelming common case (a fresh
    # NEW_PERIOD always is), but wrong for a REVISED/backfilled period that
    # isn't the newest (see _prior_point()'s docstring for the same class
    # of issue). Guarded here rather than fixed in report.py, which several
    # other things already depend on -- skip these clauses entirely rather
    # than risk stating a streak/record that describes a different period.
    is_latest = len(history) > 0 and row["time_period"] == history.index[-1]
    if len(history) > 1 and is_latest:
        try_append(f" {direction(history)}.")
        record_note = record(history)
        if record_note:
            try_append(f" {record_note}.")

    # Position-aware (unlike record()/direction() above), so this runs
    # regardless of is_latest -- correct for a revision to a non-latest
    # period too, not just a fresh NEW_PERIOD.
    if len(history) > 1:
        recent_note = _recent_extreme_note(history, row["time_period"], row["new_value"])
        if recent_note:
            try_append(f" {recent_note}.")

    if row["change_class"] in ("NEW_PERIOD", "REVISED"):
        for flag in _sanity_flags(row["indicator"], row["new_value"], history, is_latest):
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


def build_notices(changes: pd.DataFrame, con, base_url: str | None = None) -> list[dict]:
    """One notice per Turkiye change, ready for feed.py / bluesky_client.py.

    `base_url`, if given, becomes a per-notice link (e.g. to a future
    dashboard indicator page) appended to the Bluesky text and worked into
    the byte-offset facet math -- optional, unused until a dashboard
    exists to link to.
    """
    tr_changes = filter_curated_press(filter_turkiye(changes))
    notices = []
    for _, row in tr_changes.iterrows():
        history = pd.Series(dtype=float)
        if row["change_class"] in ("NEW_PERIOD", "REVISED"):
            history = series_history(con, row, row["new_snapshot_id"])
        notices.append(
            {
                "title": headline(row, history),
                "bluesky_text": bluesky_text(row, con, url=base_url),
                "feed_content": generate_change_report(pd.DataFrame([row]), con, include_sanity=False, public=True),
                "indicator": row["indicator"],
                "ref_area": row["ref_area"],
                "time_period": row["time_period"],
                "change_class": row["change_class"],
                "snapshot_id": row["new_snapshot_id"],
            }
        )
    return notices
