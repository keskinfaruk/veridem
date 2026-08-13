"""
Connector for TÜİK's press-release API. Turns tuik_press_client.py's raw
table downloads into normalized `observations` rows, written through the
same immutable-snapshot path every other source uses.

`source='tuik_press'`, never blended with `source='tuik'` (SDMX): a press
figure can predate the SDMX service's own administrative revisions for
the same period, so the two are kept as distinct, comparable series.

Not indicator_map.csv-driven for parsing (unlike the SDMX connectors) --
these are hand-designed Excel exports, so each table gets its own parser
function below. indicator_map.csv still gets a descriptive row per
indicator produced here, for discoverability.

`dataflow_id` is a stable slug (e.g. PRESS_BASIC_FERTILITY_INDICATORS),
never the press_id: TÜİK issues a new press_id every year, and snapshot
history is keyed by (source, dataflow_id).

Nine tables covered, all national-level: basic fertility indicators,
basic mortality indicators, internal migration volume, total population
and growth rate (ABPRS), marriage/divorce headline figures, international
migration totals by sex, healthy life years by age group and sex (the
only one with an `age` dimension and a multi-year `time_period` range,
e.g. "2022-2024"), age-specific fertility rate, and mean age at first
marriage (both extracted from an otherwise-provincial table's national
total row).
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


# SDMX-style age-band codes (Y_LT1/Y15T19 convention). Returns None for a
# non-age label so callers can use that to skip footer/title rows.
_AGE_RANGE_RE = re.compile(r"^(\d+)-(\d+)$")
_AGE_PLUS_RE = re.compile(r"^(\d+)\+$")


def _age_group_code(label: str) -> str | None:
    label = label.strip()
    if label.isdigit():
        return f"Y{label}"
    m = _AGE_RANGE_RE.match(label)
    if m:
        return f"Y{m.group(1)}T{m.group(2)}"
    m = _AGE_PLUS_RE.match(label)
    if m:
        return f"Y_GE{m.group(1)}"
    return None


# Long format, one row per year, fixed columns. '(r)' marks a year
# revised via updated administrative records.

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


# Wide format: indicator+sex as rows (label forward-filled across a 1- or
# 3-row group), years as columns. '(r)' = death data revised; '(1)' = rate
# recalculated because the birth-data denominator was revised -- kept
# distinct in obs_flag.

MORTALITY_TABLE = "Basic mortality indicators"
MORTALITY_DATAFLOW_ID = "PRESS_BASIC_MORTALITY_INDICATORS"

# Matched by substring against either language: each data row-group's
# label splits across two physical rows (Turkish on Total, English on
# Male, nothing on Female), so an English-only matcher drops every Total
# row. Order matters: "Neonatal mortality rate" is a substring of "Post
# neonatal mortality rate", so more specific entries are listed later.
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


# National-volume table (not the province-level in/out/net-migration
# one). Two 3-column groups (volume, rate) with a blank spacer column.
# '(1)'/'(2)' mark a methodology break (foreign population excluded/
# included), not a revision -- kept in obs_flag as-is.

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


# Long format, one row per year, full 2007-2025 history in one fetch.
# TOTAL_POPULATION is distinct from Eurostat's POP_JAN1 (different
# reference date and methodology); POP_GROWTH_RATE shares the Eurostat
# indicator's name, kept separate via source=.

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


# Long format, one row per year. No sex breakdown in this table.

MARRIAGE_DIVORCE_TABLE = "Number of marriages, crude marriage rate, number of divorces and crude divorce rate"
MARRIAGE_DIVORCE_DATAFLOW_ID = "PRESS_MARRIAGE_DIVORCE_HEADLINE"
MARRIAGE_DIVORCE_COLUMNS = {
    1: ("NUMBER_OF_MARRIAGES", "PERSONS"),
    2: ("CRUDE_MARRIAGE_RATE", "PER_1000"),
    3: ("NUMBER_OF_DIVORCES", "PERSONS"),
    4: ("CRUDE_DIVORCE_RATE", "PER_1000"),
}


def parse_marriage_divorce_table(df: pd.DataFrame) -> list[dict]:
    rows = []
    for _, r in df.iterrows():
        year, flag = _clean_year(r[0])
        if year is None:
            continue
        for col, (indicator, unit) in MARRIAGE_DIVORCE_COLUMNS.items():
            value = _to_float(r[col])
            if value is None:
                continue
            rows.append(
                {"indicator": indicator, "sex": "T", "time_period": year, "obs_value": value, "obs_flag": flag, "unit": unit}
            )
    return rows


# National totals only -- the source table also breaks these down by age
# group, not parsed here. Keeps each year's "Toplam-Total" row; year is
# forward-filled since it's only given once per year-block.

MIGRATION_AGE_TABLE = "Immigrants and emigrants by age group and sex"
MIGRATION_AGE_DATAFLOW_ID = "PRESS_INTERNATIONAL_MIGRATION"
_MIGRATION_TOTAL_LABELS = {"toplam-total", "total"}
MIGRATION_AGE_COLUMNS = {
    3: ("IMMIGRANTS", "T", "PERSONS"),
    4: ("IMMIGRANTS", "M", "PERSONS"),
    5: ("IMMIGRANTS", "F", "PERSONS"),
    7: ("EMIGRANTS", "T", "PERSONS"),
    8: ("EMIGRANTS", "M", "PERSONS"),
    9: ("EMIGRANTS", "F", "PERSONS"),
}


def parse_international_migration_table(df: pd.DataFrame) -> list[dict]:
    rows = []
    current_year: str | None = None
    for _, r in df.iterrows():
        year, flag = _clean_year(r[0])
        if year is not None:
            current_year = year
        if current_year is None:
            continue  # haven't reached the first real year block yet

        age_label = r[1]
        if not (isinstance(age_label, str) and age_label.strip().lower() in _MIGRATION_TOTAL_LABELS):
            continue  # age-breakdown row, or footer -- not this parser's job

        for col, (indicator, sex, unit) in MIGRATION_AGE_COLUMNS.items():
            value = _to_float(r[col])
            if value is None:
                continue
            rows.append(
                {"indicator": indicator, "sex": sex, "time_period": current_year, "obs_value": value, "obs_flag": flag, "unit": unit}
            )
    return rows


# The only table with an age dimension and a multi-year time_period
# range (e.g. "2022-2024"), read from the title row rather than
# hardcoded since it advances release to release.

HEALTHY_LIFE_YEARS_TABLE = "Healthy life years"
HEALTHY_LIFE_YEARS_DATAFLOW_ID = "PRESS_HEALTHY_LIFE_YEARS"
_PERIOD_RANGE_RE = re.compile(r"(\d{4}-\d{4})")
HEALTHY_LIFE_YEARS_COLUMNS = {1: "T", 2: "M", 3: "F"}


def parse_healthy_life_years_table(df: pd.DataFrame) -> list[dict]:
    title_row = " ".join(str(v) for v in df.iloc[1].tolist() if pd.notna(v))
    m = _PERIOD_RANGE_RE.search(title_row)
    if not m:
        raise ValueError(f"{HEALTHY_LIFE_YEARS_TABLE}: no YYYY-YYYY period found in the title row -- table shape changed?")
    period = m.group(1)

    rows = []
    for _, r in df.iterrows():
        age_code = _age_group_code(str(r[0])) if pd.notna(r[0]) else None
        if age_code is None:
            continue  # title/header/footer row, or an unrecognized label
        for col, sex in HEALTHY_LIFE_YEARS_COLUMNS.items():
            value = _to_float(r[col])
            if value is None:
                continue
            rows.append(
                {
                    "indicator": "HEALTHY_LIFE_YEARS",
                    "sex": sex,
                    "age": age_code,
                    "time_period": period,
                    "obs_value": value,
                    "obs_flag": None,
                    "unit": "YEARS",
                }
            )
    return rows


# Age-specific fertility rate -- long format, one row per year, 7 age-band
# columns (15-19 .. 45-49). No sex breakdown (rate is per woman).

ASFR_TABLE = "Age specific fertility rates"
ASFR_DATAFLOW_ID = "PRESS_ASFR"
ASFR_AGE_COLUMNS = {1: "Y15T19", 2: "Y20T24", 3: "Y25T29", 4: "Y30T34", 5: "Y35T39", 6: "Y40T44", 7: "Y45T49"}


def parse_asfr_table(df: pd.DataFrame) -> list[dict]:
    rows = []
    for _, r in df.iterrows():
        year, flag = _clean_year(r[0])
        if year is None:
            continue
        for col, age_code in ASFR_AGE_COLUMNS.items():
            value = _to_float(r[col])
            if value is None:
                continue
            rows.append(
                {"indicator": "ASFR", "sex": "T", "age": age_code, "time_period": year, "obs_value": value, "obs_flag": flag, "unit": "PER_1000"}
            )
    return rows


# Mean age at first marriage, by sex -- national only, extracted from the
# "Türkiye" row of the provincial table (no separate national-only table
# exists for this figure). No combined-sex column exists in the source
# table, so this produces M/F rows only, never a T row.

MARRIAGE_AGE_TABLE = "Mean age at first marriage by province and sex"
MARRIAGE_AGE_DATAFLOW_ID = "PRESS_MEAN_AGE_FIRST_MARRIAGE"
_NATIONAL_ROW_LABELS = {"türkiye", "turkiye", "turkey"}


def parse_mean_age_first_marriage_table(df: pd.DataFrame) -> list[dict]:
    header_row = 3  # year headers, one per 3-column (male, female, spacer) block
    year_cols: dict[int, tuple[str, str | None]] = {}
    for col in range(1, df.shape[1]):
        year, flag = _clean_year(df.iat[header_row, col])
        if year:
            year_cols[col] = (year, flag)
    if not year_cols:
        raise ValueError(f"{MARRIAGE_AGE_TABLE}: no year columns found in header row {header_row} -- table shape changed?")

    national_row = next(
        (i for i in range(header_row + 1, len(df)) if str(df.iat[i, 0]).strip().lower() in _NATIONAL_ROW_LABELS),
        None,
    )
    if national_row is None:
        raise ValueError(f"{MARRIAGE_AGE_TABLE}: couldn't find the national (Türkiye) row -- table shape changed?")

    rows = []
    for col, (year, flag) in year_cols.items():
        male = _to_float(df.iat[national_row, col])
        female = _to_float(df.iat[national_row, col + 1]) if col + 1 < df.shape[1] else None
        if male is not None:
            rows.append({"indicator": "MEAN_AGE_FIRST_MARRIAGE", "sex": "M", "time_period": year, "obs_value": male, "obs_flag": flag, "unit": "YEARS"})
        if female is not None:
            rows.append({"indicator": "MEAN_AGE_FIRST_MARRIAGE", "sex": "F", "time_period": year, "obs_value": female, "obs_flag": flag, "unit": "YEARS"})
    return rows


# Driver, shared by all nine tables above: (theme title, table title,
# dataflow_id, parser).
TARGETS = [
    ("Birth Statistics", FERTILITY_TABLE, FERTILITY_DATAFLOW_ID, parse_fertility_table),
    ("Death and Causes of Death Statistics", MORTALITY_TABLE, MORTALITY_DATAFLOW_ID, parse_mortality_table),
    ("Internal Migration Statistics", MIGRATION_TABLE, MIGRATION_DATAFLOW_ID, parse_internal_migration_table),
    ("The Results of Address Based Population Registration System", POPULATION_TABLE, POPULATION_DATAFLOW_ID, parse_population_table),
    ("Marriage and Divorce Statistics", MARRIAGE_DIVORCE_TABLE, MARRIAGE_DIVORCE_DATAFLOW_ID, parse_marriage_divorce_table),
    ("International Migration Statistics", MIGRATION_AGE_TABLE, MIGRATION_AGE_DATAFLOW_ID, parse_international_migration_table),
    ("Life Tables", HEALTHY_LIFE_YEARS_TABLE, HEALTHY_LIFE_YEARS_DATAFLOW_ID, parse_healthy_life_years_table),
    ("Birth Statistics", ASFR_TABLE, ASFR_DATAFLOW_ID, parse_asfr_table),
    ("Marriage and Divorce Statistics", MARRIAGE_AGE_TABLE, MARRIAGE_AGE_DATAFLOW_ID, parse_mean_age_first_marriage_table),
]

# (theme_title, table_title) pairs already parsed above, read by
# press_table_inventory.py to exclude them from candidate discovery.
COVERED_TABLES = {(theme, table) for theme, table, _, _ in TARGETS}


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
            "age": t.get("age", "_T"),
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
