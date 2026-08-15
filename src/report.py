"""
Change report generator: turns diff.py's and inventory.py's output into
something a demographer can act on, not "dataflow DF_DOGUM version 2.1 ->
2.2".

generate_change_report() returns "" when there is nothing to report. Callers
must treat that as "exit quietly, no notification".
"""

import pandas as pd

from sanity import run_checks

# Optional named reference value per indicator, shown as a distance-from line
# in the trend context. Not every indicator has one.
REFERENCE_VALUES = {
    "TFR": ("replacement level", 2.10),
}

# Display names for `source`, shown in every block header. TurkStat and
# Eurostat report slightly different numbers for the same indicator
# (confirmed, e.g. CBR), so a figure's origin is never left implicit: source
# is part of a series' identity. "TurkStat" is the institute's own English
# name; the internal source id ('tuik'), module names and file paths stay
# as they are, since renaming those would touch the whole committed bank.
SOURCE_LABELS = {"tuik": "TurkStat", "eurostat": "Eurostat", "tuik_press": "TurkStat press release"}

# Shown as its own field near Value/Previous/Change. The title already states
# this via instant_notice.area_source_label()'s sex clause, but that is easy
# to miss when skimming to the numbers, so it is stated twice rather than
# left implicit in body text. Always shown, including the plain 'Total' case.
POPULATION_TYPE_LABELS = {"T": "Total", "M": "Men", "F": "Women"}

# Natural display names, used by this module's block headers and by
# instant_notice.py. indicator_map.csv's own codes stay the right thing to
# grep for internally, which is why CHANGE_REPORT.md (public=False) keeps
# raw codes; only the public feed passes public=True.
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

# Decimal places per indicator, so format_number() stays consistent instead
# of pandas' raw formatting (which yields "8.53724e+07" for population counts
# and "9.20" where only 1 decimal is meaningful).
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

RECENT_SERIES_LENGTH = 5
TREND_AVERAGE_WINDOW = 5

# Report priority, most newsworthy first. Split by audience: OBS_CLASS_ORDER
# is demographic data, rendered into CHANGE_REPORT.md and the PR. The two
# technical orders are catalogue-level and never reach a PR (see
# technical_log.py). CLASS_HEADINGS is shared by all three.
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


def source_label(source: str) -> str:
    return SOURCE_LABELS.get(source, source)


def population_type_label(sex: str) -> str:
    return POPULATION_TYPE_LABELS.get(sex, sex)


def indicator_label(indicator: str) -> str:
    return INDICATOR_LABELS.get(indicator, indicator)


def area_label(ref_area: str) -> str:
    return REF_AREA_LABELS.get(ref_area, ref_area)


def format_number(value: float, indicator: str, signed: bool = False) -> str:
    """The single place a raw obs_value becomes display text, for this module
    and for instant_notice.py / cards.py. `signed` forces a leading
    +/- for the Change line, which reads faster with the sign than without."""
    decimals = INDICATOR_DECIMALS.get(indicator)
    if decimals is None:
        return f"{value:+g}" if signed else f"{value:g}"
    return f"{value:+,.{decimals}f}" if signed else f"{value:,.{decimals}f}"


def _ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _prior_point(history: pd.Series, current_period: str) -> tuple[str | None, float | None]:
    """(period, value) of the point immediately before current_period in the
    real series.

    Not assumed to be current_period - 1 (a series can have gaps: Eurostat's
    TFR skips 2020-2021 for Türkiye) and not assumed to be the series' last
    entry (a revision or backfilled period can land anywhere).

    This is what makes _value_change_block() correct for NEW_PERIOD: that
    row's own old_value is legitimately NaN, since no period existed before
    to compare against, so Previous/Change must come from the series history.
    """
    if current_period not in history.index:
        return None, None
    pos = history.index.get_loc(current_period)
    if pos == 0:
        return None, None
    return history.index[pos - 1], history.iloc[pos - 1]


def series_history(con, row: pd.Series, snapshot_id: str) -> pd.Series:
    """Full time series for one series key as fetched in `snapshot_id`.

    Assumes the fetcher pulled full history in that run rather than an
    incremental slice, which is true of every connector built so far, so this
    already contains every period up to and including the new one.
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
    """(sign, streak) of the latest run of same-direction year-on-year moves.
    sign is -1/0/1; streak counts consecutive periods moving that way,
    including the latest."""
    deltas = history.diff().dropna()
    if deltas.empty:
        return 0, 0
    sign = 1 if deltas.iloc[-1] > 0 else (-1 if deltas.iloc[-1] < 0 else 0)
    if sign == 0:
        return 0, 1
    streak = 0
    for d in reversed(deltas.tolist()):
        if (1 if d > 0 else (-1 if d < 0 else 0)) != sign:
            break
        streak += 1
    return sign, streak


def direction(history: pd.Series) -> str:
    sign, streak = trend_streak(history)
    if streak == 0:
        return "First value on record"
    if sign == 0:
        return "Flat vs previous period"
    word = "increase" if sign > 0 else "decline"
    label = "Up" if sign > 0 else "Down"
    # Threshold is 3, not 2: two points moving the same way is easily
    # coincidence for a noisy series. Life expectancy is the clear case,
    # where a single-year rebound would otherwise read as "2nd consecutive
    # annual increase" and overstate what two points support.
    if streak > 2:
        return f"{label} -- {_ordinal(streak)} consecutive annual {word}"
    return f"{label} vs previous period"


def record(history: pd.Series) -> str | None:
    """None, or a note that history's last entry is the series' highest or
    lowest ever. Reads the last entry as "the current one", correct whenever
    the reported period is genuinely the newest on file; callers outside this
    module should verify that before relying on it."""
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


def _recent_series_lines(
    indicator: str, history: pd.Series, marker_period: str, marker_label: str
) -> list[str]:
    """Plain-text mini table of the last RECENT_SERIES_LENGTH periods. Values
    are right-justified to a common width so decimal points line up in a
    monospace rendering; every value in a block shares one indicator, so the
    fixed decimal count makes right-justifying sufficient."""
    recent = history.tail(RECENT_SERIES_LENGTH)
    formatted = {period: format_number(value, indicator) for period, value in recent.items()}
    width = max(len(s) for s in formatted.values())
    return [
        f"    {period}  {formatted[period].rjust(width)}"
        + (f"   <- {marker_label}" if period == marker_period else "")
        for period in recent.index
    ]


def _sanity_lines(indicator: str, value: float, history: pd.Series) -> list[str]:
    return [
        f"    [{'ok' if status == 'ok' else 'warn'}]".ljust(12) + message
        for status, message in run_checks(indicator, value, history)
    ]


def _value_change_block(
    row: pd.Series, header_verb: str, con, include_sanity: bool = True, public: bool = False
) -> str:
    """Shared body for NEW_PERIOD and REVISED entries.

    Two distinct comparisons appear where they apply:

        "Revised from X"        REVISED only, same period, straight off the
                                diff row's own old/new pair.
        "Previous" / "Change"   the prior period's value from the series'
                                own history, which is what answers "how does
                                this compare to last time" for NEW_PERIOD.
    """
    history = series_history(con, row, row["new_snapshot_id"])
    value, indicator, period = row["new_value"], row["indicator"], row["time_period"]
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
        lines += ["  Trend context"] + _trend_context_lines(indicator, history)
        lines += ["", "  Recent series"]
        lines += _recent_series_lines(
            indicator, history, period, "new" if header_verb == "NEW" else "revised"
        )

    if include_sanity:
        lines += ["", "  Sanity checks"]
        lines += _sanity_lines(indicator, value, history if len(history) else pd.Series([value]))

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
        lines.append(
            f"  History fetched        {history.index.min()}-{history.index.max()} "
            f"({len(history)} periods)"
        )
    if include_sanity:
        lines += ["", "  Sanity checks"]
        lines += _sanity_lines(
            indicator, row["new_value"], history if len(history) else pd.Series([row["new_value"]])
        )
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
    """Build the full text report from a diff_observations()-shaped frame:
    demographic changes only. Returns "" when there is nothing to report;
    treat that as "no notification" and never send an empty report anywhere.

    Catalogue-level changes never come through here. technical_log.py renders
    those into a private, directly-committed log, reusing _inventory_block().

    `include_sanity`: CHANGE_REPORT.md, the document actually reviewed before
    merging, keeps the [ok]/[warn] lines, since that is the audience the
    checks serve. The public feed passes False: readers there already get a
    compact warning folded into the Bluesky text, and a wall of "[ok]" lines
    is noise for them.

    `public`: same reasoning for block headers. CHANGE_REPORT.md keeps raw
    indicator and ref_area codes, which are what you would grep for. The
    public feed passes True so a header reads "Adolescent Fertility Rate,
    Türkiye" rather than "ADOLESCENT_FERTILITY_RATE, TR".
    """
    if obs_changes.empty:
        return ""

    renderers = {
        "NEW_PERIOD": lambda r: _value_change_block(r, "NEW", con, include_sanity, public),
        "REVISED": lambda r: _value_change_block(r, "REVISED", con, include_sanity, public),
        "WITHDRAWN": lambda r: _withdrawn_block(r, public),
        "NEW_SERIES": lambda r: _new_series_block(r, con, include_sanity, public),
    }

    sections = []
    for change_class in OBS_CLASS_ORDER:
        rows = obs_changes[obs_changes["change_class"] == change_class]
        if rows.empty:
            continue
        blocks = [renderers[change_class](row) for _, row in rows.iterrows()]
        sections.append(f"## {CLASS_HEADINGS[change_class]} ({len(rows)})\n\n" + "\n\n".join(blocks))

    return "\n\n".join(sections) + "\n"
