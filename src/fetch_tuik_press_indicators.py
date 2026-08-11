"""
Real connector for TÜİK's press-release API -- turns tuik_press_client.py's
raw table downloads into normalized `observations` rows and writes them
through the same immutable-snapshot path every other source uses.

`source='tuik_press'` -- never blended with `source='tuik'` (SDMX) even
for an indicator both cover. A press figure can predate the SDMX service's
own administrative revisions for the same period; keeping them as separate
series is a feature (a real, trackable preliminary-vs-final gap), not a
workaround.

Unlike fetch_tuik_indicators.py / fetch_eurostat_indicators.py, this isn't
indicator_map.csv-driven for the actual parsing -- these are hand-designed
Excel exports, not a generic SDMX series shape, so each table gets its own
parser function below (three so far: fertility, mortality, internal
migration). indicator_map.csv still gets a descriptive row per indicator
this produces, for discoverability -- it just isn't read back by the code
here the way the SDMX connectors read it.

**Critical: dataflow_id here is a stable slug (e.g.
PRESS_BASIC_FERTILITY_INDICATORS), never the press_id.** TÜİK issues a new
press_id every year -- diff.py/daily_run.py key snapshot history by
(source, dataflow_id), so using press_id directly would silently start a
brand-new "dataflow" every single year and lose all revision history.
press_id is only ever used to *locate* the current table via
discover_press_ids(); what gets persisted is keyed by the stable slug.

Four tables covered so far:

    - Basic fertility indicators (Birth Statistics theme): mostly refreshes
      TFR/CBR (already SDMX-sourced) to a newer year, but adds two
      indicators with no SDMX coverage at all: adolescent fertility rate
      and mean age at first birth.
    - Basic mortality indicators (Death and Causes of Death Statistics):
      TÜİK publishes zero mortality statistics via SDMX -- this table
      alone provides native (not Eurostat-proxy) crude death rate, total/
      infant/under-five/neonatal/post-neonatal deaths and rates, by sex.
    - Size and proportion of population migrated across provinces by sex
      (Internal Migration Statistics): TÜİK publishes zero migration
      statistics via SDMX at all. Deliberately NOT the province-level
      in/out/net-migration table (also available, see
      tuik_press_client.py's demo) -- that needs proper NUTS-3 `ref_area`
      codes, which nothing in this repo builds yet; this table is national
      only (total people who moved between provinces, and what share of
      the population that is), meaningful at the national level without
      that prerequisite. Provincial migration is future work.
    - Population, annual growth rate (The Results of Address Based
      Population Registration System theme): national total population and
      growth rate, full 2007-2025 history in one fetch. Deliberately NOT
      the province/age-group/sex breakdown table (also available in this
      release's statisticalTables) -- that's a bigger indicator on its own
      (population pyramids, age-dependency data), future work.
"""

import re
from datetime import datetime, timezone

import pandas as pd

from snapshot import dumps_other_dims, write_snapshot
from tuik_press_client import discover_press_ids, download_table, fetch_press, find_table, parse_table

REF_AREA = "TR"
FREQ = "A"
CATEGORY = "Population and Demography"

# '2022(r)' -> ('2022', 'r'); '2008 (3)' (space before the footnote, seen
# in the population table) -> ('2008', '3'); '2009.0' (plain float, no
# footnote on that column) -> ('2009', None). The parenthesized marker's
# meaning is table-specific (see each parse_* function's own docstring) --
# carried through into obs_flag verbatim, not interpreted here.
_YEAR_RE = re.compile(r"^(\d{4})(?:\.0+)?\s*(?:\((\w+)\))?$")


def _clean_year(raw) -> tuple[str | None, str | None]:
    m = _YEAR_RE.match(str(raw).strip())
    return (m.group(1), m.group(2)) if m else (None, None)


def _to_float(val) -> float | None:
    if pd.isna(val) or (isinstance(val, str) and val.strip() in ("", "-")):
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Basic fertility indicators -- long format, one row per year, fixed columns.
# '(r)' marks a year revised via updated administrative records (the
# table's own footnote) -- same meaning as the mortality table's '(r)' below.
# ---------------------------------------------------------------------------

FERTILITY_TABLE = "Basic fertility indicators"
FERTILITY_DATAFLOW_ID = "PRESS_BASIC_FERTILITY_INDICATORS"
FERTILITY_COLUMNS = {
    1: ("TOTAL_BIRTHS", "PERSONS"),
    2: ("CBR", "PER_1000"),
    3: ("TFR", "CHILDREN_PER_WOMAN"),
    4: ("ADOLESCENT_FERTILITY_RATE", "PER_1000"),
    5: ("MEAN_AGE_CHILDBEARING", "YEARS"),
    6: ("MEAN_AGE_FIRST_BIRTH", "YEARS"),  # '-' before 2014 -- not yet collected that far back
}


def parse_fertility_table(df: pd.DataFrame) -> list[dict]:
    rows = []
    for _, r in df.iterrows():
        year, flag = _clean_year(r[0])
        if year is None:
            continue
        for col, (indicator, unit) in FERTILITY_COLUMNS.items():
            value = _to_float(r[col])
            if value is None:
                continue
            rows.append(
                {"indicator": indicator, "sex": "T", "time_period": year, "obs_value": value, "obs_flag": flag, "unit": unit}
            )
    return rows


# ---------------------------------------------------------------------------
# Basic mortality indicators -- wide format: indicator+sex as rows (label
# forward-filled across a 1- or 3-row group), years as columns. Two
# distinct footnote markers on year columns, carried through verbatim
# rather than merged: '(r)' = death data itself revised via updated
# administrative records; '(1)' = rate recalculated because the
# *birth-data* denominator was revised -- genuinely different things, so
# obs_flag keeps them distinct.
# ---------------------------------------------------------------------------

MORTALITY_TABLE = "Basic mortality indicators"
MORTALITY_DATAFLOW_ID = "PRESS_BASIC_MORTALITY_INDICATORS"

# Matched by substring, case-insensitive, against EITHER language, not just
# English. Unlike the header row (one cell, "Göstergeler\nIndicators"), each
# *data* row-group's label is split across two physical rows -- Turkish on
# the "Toplam-Total" row, English on the "Erkek-Male"/"Erkek-Boy" row,
# nothing on the third ("Kadın-Female"/"Kız-Girl") row. An English-only
# matcher would silently drop every group's first (Total) row and misfile
# its values under whichever indicator a prior row had left `current` set to.
#
# Order matters for a second reason: real substring collisions, checked
# both languages -- "Neonatal mortality rate" is a substring of "Post
# neonatal mortality rate" (and the Turkish equivalents collide the same
# way; "Ölüm sayısı"/"Ölüm hızı" bare are substrings of nearly every other
# Turkish label here) -- every post-neonatal and total-death/rate entry is
# placed as late as it needs to be to never shadow a more specific one
# still below it.
MORTALITY_INDICATORS = [
    ("Number of Infant deaths", "Bebek ölüm sayısı", "INFANT_DEATHS", "PERSONS"),
    ("Infant mortality rate", "Bebek ölüm hızı", "INFANT_MORTALITY_RATE", "PER_1000"),
    ("Number of under five deaths", "Beş yaş altı ölüm sayısı", "UNDER5_DEATHS", "PERSONS"),
    ("Under five mortality rate", "Beş yaş altı ölüm hızı", "UNDER5_MORTALITY_RATE", "PER_1000"),
    ("Number of post neonatal deaths", "Post neonatal ölüm sayısı", "POST_NEONATAL_DEATHS", "PERSONS"),
    ("Post neonatal mortality rate", "Post neonatal ölüm hızı", "POST_NEONATAL_MORTALITY_RATE", "PER_1000"),
    ("Number of neonatal deaths", "Neonatal ölüm sayısı", "NEONATAL_DEATHS", "PERSONS"),
    ("Neonatal mortality rate", "Neonatal ölüm hızı", "NEONATAL_MORTALITY_RATE", "PER_1000"),
    ("Number of deaths", "Ölüm sayısı", "TOTAL_DEATHS", "PERSONS"),
    ("Crude death rate", "Kaba ölüm hızı", "CDR", "PER_1000"),
]
MORTALITY_SEX_LABELS = {
    "toplam-total": "T",
    "erkek-male": "M",
    "kadın-female": "F",
    "erkek-boy": "M",
    "kız-girl": "F",
}


def _match_mortality_indicator(label: str) -> tuple[str, str] | None:
    low = label.lower()
    for needle_en, needle_tr, code, unit in MORTALITY_INDICATORS:
        if needle_en.lower() in low or needle_tr.lower() in low:
            return code, unit
    return None


def parse_mortality_table(df: pd.DataFrame) -> list[dict]:
    header_row = next((i for i, v in df[0].items() if isinstance(v, str) and "Indicators" in v), None)
    if header_row is None:
        raise ValueError(f"{MORTALITY_TABLE}: couldn't find the 'Indicators' header row -- table shape changed?")

    year_cols: dict[int, tuple[str, str | None]] = {}
    for col in range(2, df.shape[1]):
        year, flag = _clean_year(df.iat[header_row, col])
        if year:
            year_cols[col] = (year, flag)
    if not year_cols:
        raise ValueError(f"{MORTALITY_TABLE}: no year columns found in header row {header_row} -- table shape changed?")

    rows = []
    current: tuple[str, str] | None = None
    for i in range(header_row + 1, len(df)):
        label = df.iat[i, 0]
        if isinstance(label, str) and label.strip().startswith(("TÜİK,", "TurkStat,")):
            break  # footer/source block reached
        if isinstance(label, str) and label.strip():
            matched = _match_mortality_indicator(label)
            if matched:
                current = matched
        if current is None:
            continue  # haven't reached the first real indicator row yet

        sex_cell = df.iat[i, 1]
        sex = MORTALITY_SEX_LABELS.get(str(sex_cell).strip().lower(), "T") if pd.notna(sex_cell) else "T"
        indicator, unit = current
        for col, (year, flag) in year_cols.items():
            value = _to_float(df.iat[i, col])
            if value is None:
                continue
            rows.append({"indicator": indicator, "sex": sex, "time_period": year, "obs_value": value, "obs_flag": flag, "unit": unit})
    return rows


# ---------------------------------------------------------------------------
# Size and proportion of population migrated across provinces by sex --
# long format like fertility, but two 3-column groups (volume, rate) side
# by side with a blank spacer column between them. Deliberately the
# national-volume table, not the province-level in/out/net-migration one --
# see module docstring. '(1)'/'(2)' mark a real methodology break (foreign
# population excluded/included), not a data revision -- kept in obs_flag
# verbatim: flag it, don't hide it.
# ---------------------------------------------------------------------------

MIGRATION_TABLE = "Size and proportion of population migrated across provinces by sex"
MIGRATION_DATAFLOW_ID = "PRESS_INTERNAL_MIGRATION_VOLUME"
MIGRATION_COLUMNS = {
    1: ("INTERNAL_MIGRATION_VOLUME", "T", "PERSONS"),
    2: ("INTERNAL_MIGRATION_VOLUME", "M", "PERSONS"),
    3: ("INTERNAL_MIGRATION_VOLUME", "F", "PERSONS"),
    5: ("INTERNAL_MIGRATION_RATE", "T", "PERCENT"),
    6: ("INTERNAL_MIGRATION_RATE", "M", "PERCENT"),
    7: ("INTERNAL_MIGRATION_RATE", "F", "PERCENT"),
}


def parse_internal_migration_table(df: pd.DataFrame) -> list[dict]:
    rows = []
    for _, r in df.iterrows():
        year, flag = _clean_year(r[0])
        if year is None:
            continue
        for col, (indicator, sex, unit) in MIGRATION_COLUMNS.items():
            value = _to_float(r[col])
            if value is None:
                continue
            rows.append(
                {"indicator": indicator, "sex": sex, "time_period": year, "obs_value": value, "obs_flag": flag, "unit": unit}
            )
    return rows


# ---------------------------------------------------------------------------
# Population, annual growth rate -- long format like fertility: one row per
# year, fixed columns, full 2007-2025 history in one fetch (unlike the
# province-level tables in this same release, which only carry the current
# and prior year). TOTAL_POPULATION is a new code, not Eurostat's POP_JAN1
# -- different reference date (Dec 31, ABPRS) and methodology, kept
# distinct rather than implied equivalent. POP_GROWTH_RATE reuses the
# Eurostat indicator's name since it's the same concept, kept separate via
# source= like every other indicator both sources cover.
# ---------------------------------------------------------------------------

POPULATION_TABLE = "Population, Annual Growth Rate of Population, Number of Provinces, Districts, Towns, Villages and  Population Density"
POPULATION_DATAFLOW_ID = "PRESS_POPULATION_TOTAL"
POPULATION_COLUMNS = {
    1: ("TOTAL_POPULATION", "PERSONS"),
    2: ("POP_GROWTH_RATE", "PER_1000"),
}


def parse_population_table(df: pd.DataFrame) -> list[dict]:
    rows = []
    for _, r in df.iterrows():
        year, flag = _clean_year(r[0])
        if year is None:
            continue
        for col, (indicator, unit) in POPULATION_COLUMNS.items():
            value = _to_float(r[col])
            if value is None:
                continue
            rows.append(
                {"indicator": indicator, "sex": "T", "time_period": year, "obs_value": value, "obs_flag": flag, "unit": unit}
            )
    return rows


# ---------------------------------------------------------------------------
# Driver -- shared by all four tables above.
# ---------------------------------------------------------------------------

# (theme title, table title, dataflow_id, parser)
TARGETS = [
    ("Birth Statistics", FERTILITY_TABLE, FERTILITY_DATAFLOW_ID, parse_fertility_table),
    ("Death and Causes of Death Statistics", MORTALITY_TABLE, MORTALITY_DATAFLOW_ID, parse_mortality_table),
    ("Internal Migration Statistics", MIGRATION_TABLE, MIGRATION_DATAFLOW_ID, parse_internal_migration_table),
    ("The Results of Address Based Population Registration System", POPULATION_TABLE, POPULATION_DATAFLOW_ID, parse_population_table),
]


def fetch_one(theme_title: str, table_title: str, dataflow_id: str, parser, press_ids: dict[str, int]) -> pd.DataFrame:
    press_id = press_ids[theme_title]  # KeyError here means discovery missed a theme -- want that loud
    press_data = fetch_press(press_id)
    table = find_table(press_data, table_title)
    raw = parse_table(download_table(table))
    tidy = parser(raw)

    snapshot_id = f"tuik_press_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    retrieved_at = datetime.now(timezone.utc)
    out_rows = [
        {
            "source": "tuik_press",
            "dataflow_id": dataflow_id,
            "indicator": t["indicator"],
            "ref_area": REF_AREA,
            "freq": FREQ,
            "sex": t["sex"],
            "age": "_T",
            "unit": t["unit"],
            "other_dims": dumps_other_dims({}),
            "time_period": t["time_period"],
            "obs_value": t["obs_value"],
            "obs_flag": t["obs_flag"],
            "snapshot_id": snapshot_id,
            "retrieved_at": retrieved_at,
        }
        for t in tidy
    ]
    df = pd.DataFrame(out_rows)
    df.attrs["snapshot_id"] = snapshot_id
    df.attrs["press_id"] = press_id
    df.attrs["press_period"] = press_data.get("period")
    return df


def main() -> int:
    press_ids = {e["title"]: int(e["id"]) for e in discover_press_ids(category=CATEGORY)}

    for theme_title, table_title, dataflow_id, parser in TARGETS:
        print(f"Fetching {table_title!r} ({theme_title})...")
        df = fetch_one(theme_title, table_title, dataflow_id, parser, press_ids)
        if df.empty:
            print("  WARNING: no observations parsed -- skipping snapshot")
            continue
        df = df.sort_values(["indicator", "sex", "time_period"]).reset_index(drop=True)
        out_path = write_snapshot(df, "tuik_press", dataflow_id, snapshot_id=df.attrs["snapshot_id"])
        print(
            f"  press {df.attrs['press_id']} (period {df.attrs['press_period']}): "
            f"saved {len(df)} observations, {df['indicator'].nunique()} indicators to {out_path}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
