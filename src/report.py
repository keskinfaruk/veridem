"""
Change report generator.

Turns diff.py's / dataflow_inventory.py's output into a human-readable
report -- something a demographer can act on, not "dataflow DF_DOGUM
version 2.1 -> 2.2". Blog-post drafting is a separate, later concern; this
is just the report.

`generate_change_report()` returns "" when there is nothing to report --
callers should treat that as "exit quietly, no notification".
"""

import pandas as pd

from sanity import run_checks

# Optional named reference value per indicator, shown as a distance-from
# line in the trend context when defined. Not every indicator has one.
REFERENCE_VALUES = {
    "TFR": ("replacement level", 2.10),
}

# Display names for the `source` column. Shown in every block header --
# TurkStat and Eurostat report slightly different numbers for the same
# indicator (confirmed, e.g. CBR), so which one a figure came from is never
# left implicit here: source is part of a series' identity. "TurkStat" (not
# "TÜİK") in this display text specifically -- the institute's own English
# name; the internal source id ('tuik'), module names, and file paths stay
# unchanged, since renaming those touches the whole committed data bank for
# no real benefit.
SOURCE_LABELS = {"tuik": "TurkStat", "eurostat": "Eurostat", "tuik_press": "TurkStat press release"}


def source_label(source: str) -> str:
    return SOURCE_LABELS.get(source, source)


# "Total"/"Men"/"Women" -- an explicit population-type line shown near
# Value/Previous/Change in every block: the title already states this via
# instant_notice._area_source_label()'s sex clause, but that's easy to miss
# when skimming straight to the numbers -- worth stating twice rather than
# leaving it implicit in body text a reader can skip past. Distinct from
# instant_notice.SEX_LABELS ("men"/"women", lowercase, built for an inline
# sentence fragment) -- this is a standalone field label, always shown
# (including the plain 'Total' case) rather than omitted for the common case.
POPULATION_TYPE_LABELS = {"T": "Total", "M": "Men", "F": "Women"}


def population_type_label(sex: str) -> str:
    return POPULATION_TYPE_LABELS.get(sex, sex)


# Natural display names -- used by report.py's own block headers (see
# `public` param on _value_change_block() etc. below) and re-imported by
# instant_notice.py under the same names. indicator_map.csv's own codes
# (TFR, CBR, ...) are still the right thing to grep for internally, which
# is why CHANGE_REPORT.md (public=False, the default) keeps raw codes --
# only the public single-notice feed_content instant_notice.build_notices()
# builds passes public=True.
INDICATOR_LABELS = {
    "TFR": "Total Fertility Rate (TFR)",
    "ASFR": "Age-Specific Fertility Rate (ASFR)",
    "CBR": "Crude Birth Rate (CBR)",
    "CDR": "Crude Death Rate (CDR)",
    "MEAN_AGE_CHILDBEARING": "Mean Age at Childbearing",
    "MEAN_AGE_FIRST_MARRIAGE": "Mean Age at First Marriage",
    "NATURAL_GROWTH_RATE": "Natural Growth Rate",
    "POP_GROWTH_RATE": "Population Growth Rate",
    "POP_JAN1": "Population (1 January)",
    "LIFE_EXPECTANCY_BIRTH": "Life Expectancy at Birth",
    "LIFE_EXPECTANCY_15": "Life Expectancy at Age 15",
    "LIFE_EXPECTANCY_65": "Life Expectancy at Age 65",
    "INFANT_MORTALITY_RATE": "Infant Mortality Rate",
    "TOTAL_BIRTHS": "Total Live Births",
    "ADOLESCENT_FERTILITY_RATE": "Adolescent Fertility Rate",
    "MEAN_AGE_FIRST_BIRTH": "Mean Age at First Birth",
    "TOTAL_DEATHS": "Total Deaths",
    "INFANT_DEATHS": "Infant Deaths",
    "UNDER5_DEATHS": "Under-Five Deaths",
    "UNDER5_MORTALITY_RATE": "Under-Five Mortality Rate",
    "NEONATAL_DEATHS": "Neonatal Deaths",
    "NEONATAL_MORTALITY_RATE": "Neonatal Mortality Rate",
    "POST_NEONATAL_DEATHS": "Post-Neonatal Deaths",
    "POST_NEONATAL_MORTALITY_RATE": "Post-Neonatal Mortality Rate",
    "INTERNAL_MIGRATION_VOLUME": "Internal Migration Volume",
    "INTERNAL_MIGRATION_RATE": "Internal Migration Rate",
    "TOTAL_POPULATION": "Total Population",
    "NUMBER_OF_MARRIAGES": "Number of Marriages",
    "CRUDE_MARRIAGE_RATE": "Crude Marriage Rate",
    "NUMBER_OF_DIVORCES": "Number of Divorces",
    "CRUDE_DIVORCE_RATE": "Crude Divorce Rate",
    "IMMIGRANTS": "Immigrants",
    "EMIGRANTS": "Emigrants",
    "HEALTHY_LIFE_YEARS": "Healthy Life Years",
}

REF_AREA_LABELS = {"TR": "Türkiye"}


def indicator_label(indicator: str) -> str:
    return INDICATOR_LABELS.get(indicator, indicator)


def area_label(ref_area: str) -> str:
    return REF_AREA_LABELS.get(ref_area, ref_area)


# Decimal places per indicator -- lets _value_change_block() (CHANGE_REPORT.md,
# and every feed_content built through generate_change_report()) format
# Value/Previous/Change/Recent-series/trend-average consistently instead of
# pandas' raw `:g`/`.2f` (which produces "8.53724e+07"-style scientific
# notation for population counts, and "9.20" instead of "9.2" for indicators
# like ADOLESCENT_FERTILITY_RATE that are only meaningfully precise to 1
# decimal). instant_notice.py imports this rather than defining its own copy.
INDICATOR_DECIMALS = {
    "TFR": 2,
    "ASFR": 1,
    "MEAN_AGE_CHILDBEARING": 1,
    "MEAN_AGE_FIRST_MARRIAGE": 1,
    "CBR": 1,
    "CDR": 1,
    "NATURAL_GROWTH_RATE": 1,
    "POP_GROWTH_RATE": 1,
    "POP_JAN1": 0,
    "LIFE_EXPECTANCY_BIRTH": 1,
    "LIFE_EXPECTANCY_15": 1,
    "LIFE_EXPECTANCY_65": 1,
    "INFANT_MORTALITY_RATE": 1,
    "TOTAL_BIRTHS": 0,
    "ADOLESCENT_FERTILITY_RATE": 1,
    "MEAN_AGE_FIRST_BIRTH": 1,
    "TOTAL_DEATHS": 0,
    "INFANT_DEATHS": 0,
    "UNDER5_DEATHS": 0,
    "UNDER5_MORTALITY_RATE": 1,
    "NEONATAL_DEATHS": 0,
    "NEONATAL_MORTALITY_RATE": 1,
    "POST_NEONATAL_DEATHS": 0,
    "POST_NEONATAL_MORTALITY_RATE": 1,
    "INTERNAL_MIGRATION_VOLUME": 0,
    "INTERNAL_MIGRATION_RATE": 1,
    "TOTAL_POPULATION": 0,
    "NUMBER_OF_MARRIAGES": 0,
    "CRUDE_MARRIAGE_RATE": 1,
    "NUMBER_OF_DIVORCES": 0,
    "CRUDE_DIVORCE_RATE": 1,
    "IMMIGRANTS": 0,
    "EMIGRANTS": 0,
    "HEALTHY_LIFE_YEARS": 1,
}


def format_number(value: float, indicator: str, signed: bool = False) -> str:
    """The one place a raw obs_value becomes display text, for every block
    in this module and in instant_notice.py/baseline_notice.py -- Value,
    Previous, Change, 5-yr average, and every line of Recent series all go
    through this now, not a mix of `:g`/`.2f`/bare pandas formatting.
    `signed` forces a leading +/- (the Change line; direction is otherwise
    implicit in the sign of the number, but a bare "-0.60" reads faster
    with the sign than without).
    """
    decimals = INDICATOR_DECIMALS.get(indicator)
    if decimals is None:
        return f"{value:+g}" if signed else f"{value:g}"
    return f"{value:+,.{decimals}f}" if signed else f"{value:,.{decimals}f}"


def _prior_point(history: pd.Series, current_period: str) -> tuple[str | None, float | None]:
    """(period, value) of the point immediately before current_period in the
    real historical series. Not assumed to be current_period - 1 (a series
    can have gaps, e.g. Eurostat's TFR skips 2020-2021 for Turkiye) and NOT
    assumed to be the series' last entry: a REVISED or backfilled
    NEW_PERIOD can land on a period that isn't the newest one in the
    fetched history.

    This is what makes _value_change_block() below correct for the common
    NEW_PERIOD case: a genuine NEW_PERIOD row's `old_value` (the diff row's
    own old/new pair) is legitimately NaN -- no period existed before to
    compare against on that axis -- so Previous/Change must come from the
    series' own history, not the diff row.
    """
    if current_period not in history.index:
        return None, None
    pos = history.index.get_loc(current_period)
    if pos == 0:
        return None, None
    return history.index[pos - 1], history.iloc[pos - 1]


RECENT_SERIES_LENGTH = 5
TREND_AVERAGE_WINDOW = 5

# Report priority order, most newsworthy first: a fresh period is the main
# event, structural/DSD changes are lowest priority (parser risk, not
# necessarily new data).
#
# Split by audience: OBS_CLASS_ORDER is demographic data, rendered into
# CHANGE_REPORT.md/the PR. TECHNICAL_CLASS_ORDER (catalogue-level, TUIK's
# SDMX dataflow catalogue) and PRESS_TECHNICAL_CLASS_ORDER (catalogue-level,
# tuik_press's theme catalogue -- see press_dataflow_inventory.py) never go
# into a PR -- see technical_log.py. CLASS_HEADINGS is shared by all three.
OBS_CLASS_ORDER = ["NEW_PERIOD", "REVISED", "WITHDRAWN", "NEW_SERIES"]
TECHNICAL_CLASS_ORDER = ["NEW_DATAFLOW", "DATAFLOW_WITHDRAWN", "STRUCTURAL"]
PRESS_TECHNICAL_CLASS_ORDER = ["PRESS_THEME_NEW", "PRESS_THEME_WITHDRAWN"]
CLASS_HEADINGS = {
    "NEW_PERIOD": "New periods",
    "REVISED": "Revisions",
    "WITHDRAWN": "Withdrawn values",
    "NEW_SERIES": "New series",
    "NEW_DATAFLOW": "New dataflows",
    "DATAFLOW_WITHDRAWN": "Withdrawn dataflows",
    "STRUCTURAL": "Structural changes (DSD version bumps)",
    "PRESS_THEME_NEW": "New press themes",
    "PRESS_THEME_WITHDRAWN": "Withdrawn press themes",
}


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def series_history(con, row: pd.Series, snapshot_id: str) -> pd.Series:
    """Full time series for one series key, as fetched in `snapshot_id`.

    Assumes the fetcher pulled full history in that run, not just an
    incremental slice -- true of every connector built so far
    (fetch_tuik_indicators.py / fetch_eurostat_indicators.py both backfill
    the whole series every run) -- so this already contains every period up
    to and including the new one, not just what changed.
    """
    df = con.execute(
        "SELECT time_period, obs_value FROM observations "
        "WHERE source=? AND dataflow_id=? AND indicator=? AND ref_area=? AND freq=? "
        "AND sex=? AND age=? AND other_dims=? AND snapshot_id=? "
        "ORDER BY time_period",
        [
            row["source"], row["dataflow_id"], row["indicator"], row["ref_area"], row["freq"],
            row["sex"], row["age"], row["other_dims"], snapshot_id,
        ],
    ).df()
    return df.set_index("time_period")["obs_value"]


def trend_streak(history: pd.Series) -> tuple[int, int]:
    """(sign, streak) of the latest run of same-direction year-on-year
    moves: sign is -1/0/1, streak is how many consecutive periods (including
    the latest) moved that same way. Shared by direction() and any other
    language's rendering of the same fact -- the streak count itself must
    never differ between them, only the words wrapped around it.
    """
    deltas = history.diff().dropna()
    if deltas.empty:
        return 0, 0
    sign = 1 if deltas.iloc[-1] > 0 else (-1 if deltas.iloc[-1] < 0 else 0)
    if sign == 0:
        return 0, 1
    streak = 0
    for d in reversed(deltas.tolist()):
        s = 1 if d > 0 else (-1 if d < 0 else 0)
        if s == sign:
            streak += 1
        else:
            break
    return sign, streak


def direction(history: pd.Series) -> str:
    sign, streak = trend_streak(history)
    if streak == 0:
        return "First value on record"
    if sign == 0:
        return "Flat vs previous period"
    word = "increase" if sign > 0 else "decline"
    label = "Up" if sign > 0 else "Down"
    # Threshold is 3, not 2: two data points moving the same way is easily
    # coincidence for a genuinely noisy series (life expectancy is the clear
    # case -- a single-year rebound would otherwise read as "2nd consecutive
    # annual increase", overstating what two points actually support).
    if streak > 2:
        return f"{label} -- {_ordinal(streak)} consecutive annual {word}"
    return f"{label} vs previous period"


def record(history: pd.Series) -> str | None:
    """None, or a note that history's last entry is the highest/lowest value
    the series has ever recorded. Like direction(), this reads off the
    series' *last* entry as "the current one" -- correct whenever the
    reported period is genuinely the newest on file, which callers outside
    this module should verify before trusting it for anything else."""
    latest = history.iloc[-1]
    span = f"since {history.index.min()}"
    if (history == history.max()).sum() == 1 and latest == history.max():
        return f"Highest value in the series ({span})"
    if (history == history.min()).sum() == 1 and latest == history.min():
        return f"Lowest value in the series ({span})"
    return None


def _trend_context_lines(indicator: str, history: pd.Series) -> list[str]:
    lines = [f"    Direction      {direction(history)}"]
    record_note = record(history)
    if record_note:
        lines.append(f"    Record         {record_note}")
    window = history.tail(TREND_AVERAGE_WINDOW)
    lines.append(f"    {len(window)}-yr average   {format_number(window.mean(), indicator)}")
    ref = REFERENCE_VALUES.get(indicator)
    if ref:
        label, value = ref
        lines.append(
            f"    Distance from {label} ({value:g}): "
            f"{format_number(history.iloc[-1] - value, indicator, signed=True)}"
        )
    return lines


def _recent_series_lines(indicator: str, history: pd.Series, marker_period: str, marker_label: str) -> list[str]:
    """Plain-text mini table of the last RECENT_SERIES_LENGTH periods.
    Values are right-justified to a common width so the decimal points
    line up in a monospace rendering -- every value in one of these blocks
    shares the same indicator, so the same fixed decimal count from
    format_number() means right-justifying is enough; no separate
    decimal-alignment logic needed."""
    recent = history.tail(RECENT_SERIES_LENGTH)
    formatted = {period: format_number(value, indicator) for period, value in recent.items()}
    width = max(len(s) for s in formatted.values())
    lines = []
    for period, value in recent.items():
        arrow = f"   <- {marker_label}" if period == marker_period else ""
        lines.append(f"    {period}  {formatted[period].rjust(width)}{arrow}")
    return lines


def _sanity_lines(indicator: str, value: float, history: pd.Series) -> list[str]:
    lines = []
    for status, message in run_checks(indicator, value, history):
        tag = "ok" if status == "ok" else "warn"
        lines.append(f"    [{tag}]".ljust(12) + message)
    return lines


def _value_change_block(
    row: pd.Series, header_verb: str, con, include_sanity: bool = True, public: bool = False
) -> str:
    """Shared body for NEW_PERIOD and REVISED entries: population type,
    value/previous/change header, trend context, recent series, optionally
    sanity checks.

    Two distinct comparisons get shown where they apply:

        - "Revised from X" -- REVISED only, same time_period, straight off
          the diff row's own old_value/new_value pair.
        - "Previous ... (year)" / "Change" -- the prior calendar year's
          value from the series' own history (_prior_point()), which is
          what actually answers "how does this compare to last time" for
          the common NEW_PERIOD case. The diff row's old_value is NaN for a
          genuine NEW_PERIOD (no such period existed before), which is
          exactly why this couldn't come from the diff row alone.
    """
    history = series_history(con, row, row["new_snapshot_id"])
    value = row["new_value"]
    indicator = row["indicator"]
    period = row["time_period"]
    header_indicator = indicator_label(indicator) if public else indicator
    header_area = area_label(row["ref_area"]) if public else row["ref_area"]

    lines = [
        f"{header_verb}: {header_indicator}, {header_area} ({source_label(row['source'])}), {period}",
        "",
        f"  Population       {population_type_label(row['sex'])}",
        f"  Value            {format_number(value, indicator)} ({period})",
    ]

    if header_verb == "REVISED" and pd.notna(row["old_value"]):
        lines.append(f"  Revised from     {format_number(row['old_value'], indicator)}")

    prior_period, prior_value = _prior_point(history, period)
    if prior_period is not None:
        delta = value - prior_value
        pct = f" ({delta / prior_value * 100:+.1f}%)" if prior_value else ""
        lines.append(f"  Previous         {format_number(prior_value, indicator)} ({prior_period})")
        lines.append(f"  Change           {format_number(delta, indicator, signed=True)}{pct}")
    lines.append("")

    if len(history) >= 1:
        lines.append("  Trend context")
        lines.extend(_trend_context_lines(indicator, history))
        lines.append("")
        lines.append("  Recent series")
        lines.extend(_recent_series_lines(indicator, history, period, "new" if header_verb == "NEW" else "revised"))

    if include_sanity:
        lines.append("")
        lines.append("  Sanity checks")
        lines.extend(_sanity_lines(indicator, value, history if len(history) else pd.Series([value])))

    return "\n".join(lines)


def _withdrawn_block(row: pd.Series, public: bool = False) -> str:
    header_indicator = indicator_label(row["indicator"]) if public else row["indicator"]
    header_area = area_label(row["ref_area"]) if public else row["ref_area"]
    return (
        f"WITHDRAWN: {header_indicator}, {header_area} ({source_label(row['source'])}), "
        f"{row['time_period']}\n\n"
        f"  Population       {population_type_label(row['sex'])}\n"
        f"  Previously       {format_number(row['old_value'], row['indicator'])}\n"
        f"  Now              missing from the latest fetch"
    )


def _new_series_block(row: pd.Series, con, include_sanity: bool = True, public: bool = False) -> str:
    history = series_history(con, row, row["new_snapshot_id"])
    indicator = row["indicator"]
    header_indicator = indicator_label(indicator) if public else indicator
    header_area = area_label(row["ref_area"]) if public else row["ref_area"]
    lines = [
        f"NEW SERIES: {header_indicator}, {header_area} ({source_label(row['source'])}) "
        f"(sex={row['sex']}, age={row['age']})",
        "",
        f"  Population             {population_type_label(row['sex'])}",
        f"  First observed value   {format_number(row['new_value'], indicator)} ({row['time_period']})",
    ]
    if len(history) > 1:
        lines.append(f"  History fetched        {history.index.min()}-{history.index.max()} ({len(history)} periods)")
    if include_sanity:
        lines.append("")
        lines.append("  Sanity checks")
        lines.extend(_sanity_lines(indicator, row["new_value"], history if len(history) else pd.Series([row["new_value"]])))
    return "\n".join(lines)


def _inventory_block(row: pd.Series) -> str:
    if row["change_class"] == "NEW_DATAFLOW":
        name = f" -- {row['name_new']}" if pd.notna(row.get("name_new")) else ""
        return f"NEW DATAFLOW: {row['dataflow_id']}{name}"
    if row["change_class"] == "DATAFLOW_WITHDRAWN":
        name = f" -- {row['name_old']}" if pd.notna(row.get("name_old")) else ""
        version = f" (was version {row['version_old']})" if pd.notna(row.get("version_old")) else ""
        return (
            f"DATAFLOW WITHDRAWN: {row['dataflow_id']}{name}{version} -- "
            "no longer in TÜİK's catalogue; any watched indicator on it will "
            "fail to fetch until removed from data/indicator_map.csv or TÜİK "
            "republishes it"
        )
    return (
        f"STRUCTURAL: {row['dataflow_id']} DSD version "
        f"{row['version_old']} -> {row['version_new']} -- verify the parser still matches"
    )


def _press_inventory_block(row: pd.Series) -> str:
    if row["change_class"] == "PRESS_THEME_NEW":
        category = f" -- {row['category_name_new']}" if pd.notna(row.get("category_name_new")) else ""
        return f"NEW PRESS THEME: {row['title']}{category}"
    category = f" -- {row['category_name_old']}" if pd.notna(row.get("category_name_old")) else ""
    return (
        f"PRESS THEME WITHDRAWN: {row['title']}{category} -- no longer in TÜİK's "
        "press catalogue; any watched tuik_press indicator sourced from it will "
        "fail to fetch until removed from fetch_tuik_press_indicators.py or TÜİK "
        "republishes it"
    )


def generate_change_report(
    obs_changes: pd.DataFrame,
    con,
    include_sanity: bool = True,
    public: bool = False,
) -> str:
    """Build the full text change report from a diff_observations()-shaped
    DataFrame -- demographic data changes only. Returns "" if there is
    nothing to report at all -- treat that as "no notification", never send
    an empty report anywhere.

    Catalogue-level changes (NEW_DATAFLOW/DATAFLOW_WITHDRAWN/STRUCTURAL)
    never come through here -- see technical_log.py, which renders those
    into a private, directly-committed log instead (reuses
    _inventory_block() below).

    `include_sanity`: CHANGE_REPORT.md (the PR document actually reviewed
    before merging) keeps the [ok]/[warn] sanity-check lines by default --
    that's exactly the audience that check machinery is meant to serve.
    instant_notice.build_notices() passes False when building a single
    row's feed_content for the *public* Atom feed -- readers there get the
    compact "⚠" flag already folded into bluesky_text()/headline() when
    something's actually off; a wall of "[ok] within plausible range"
    lines for everything else is noise, not signal, for that audience.

    `public`: same reasoning, applied to the block headers.
    CHANGE_REPORT.md (public=False, the default) keeps raw indicator/
    ref_area codes -- deliberately, they're what you'd grep for.
    instant_notice.build_notices() passes True so the public feed's header
    reads "Adolescent Fertility Rate, Türkiye" instead of
    "ADOLESCENT_FERTILITY_RATE, TR", matching what baseline notices, the
    Atom entry title, and the Bluesky text already show.
    """
    if obs_changes.empty:
        return ""

    sections = []
    for change_class in OBS_CLASS_ORDER:
        rows = obs_changes[obs_changes["change_class"] == change_class]
        if rows.empty:
            continue

        blocks = []
        for _, row in rows.iterrows():
            if change_class == "NEW_PERIOD":
                blocks.append(_value_change_block(row, "NEW", con, include_sanity, public))
            elif change_class == "REVISED":
                blocks.append(_value_change_block(row, "REVISED", con, include_sanity, public))
            elif change_class == "WITHDRAWN":
                blocks.append(_withdrawn_block(row, public))
            elif change_class == "NEW_SERIES":
                blocks.append(_new_series_block(row, con, include_sanity, public))

        heading = f"## {CLASS_HEADINGS[change_class]} ({len(rows)})"
        sections.append(heading + "\n\n" + "\n\n".join(blocks))

    return "\n\n".join(sections) + "\n"
