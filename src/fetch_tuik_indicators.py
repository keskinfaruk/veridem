"""
Generic, indicator_map-driven TUIK fetch pipeline.

Every dataflow audited so far shares the same generic shape -- a
REF_AREA/FREQ/INDICATOR-keyed series, sometimes with one extra dimension
carrying an age or sex breakdown -- so adding a TUIK indicator means adding
a row to data/indicator_map.csv, not writing a new script. (fetch_tfr.py is
a minimal single-indicator reference kept alongside this one, not part of
the same pipeline.)

`age_dim` / `sex_dim` in indicator_map.csv name the DSD dimension (if any)
that should populate the observations schema's `age` / `sex` columns for
that indicator; leave blank for indicators with no such breakdown (they get
'_T' / 'T'). Every other non-time dimension is left unfiltered in the series
key and is expected to come back fixed at '_Z' (not applicable) for
indicators that don't use it -- verify with a probe fetch before adding a
new row; never assume a series key's shape from a sibling dataflow.
"""

import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

from series_key import build_series_key, get_dimension_order
from snapshot import dumps_other_dims, write_snapshot
from tuik_client import NS, fetch_data, get_access_token

AGENCY = "TR"
REF_AREA = "TR"
FREQ = "A"

INDICATOR_MAP_PATH = Path(__file__).resolve().parent.parent / "data" / "indicator_map.csv"

# SDMX CL_CINSIYET codes -> this project's sex codes.
SEX_CODE_MAP = {"1": "M", "2": "F", "_T": "T", "_Z": "T"}


def _load_map() -> pd.DataFrame:
    imap = pd.read_csv(INDICATOR_MAP_PATH, dtype=str, keep_default_na=False)
    return imap[imap["source"] == "tuik"]


def fetch_indicator(row: pd.Series, token: str, snapshot_id: str) -> pd.DataFrame:
    """Fetch and normalize one indicator_map row's full series (all periods,
    all values of its age/sex breakdown dimension if any).

    `snapshot_id` is supplied by the caller, not generated here: several
    indicator_map rows can share one dataflow_id (TUIK bundles multiple
    indicators into a single dataflow), and every row for the same
    dataflow_id in one fetch run must land in the same snapshot -- see
    main()'s grouping. A snapshot_id generated per row here would give
    diff.py's latest_two_snapshots() (keyed on dataflow_id alone) two
    same-day snapshots to compare instead of today vs. yesterday.
    """
    dataflow_id = row["dataflow_id"]
    version = row["version"]
    indicator_code = row["source_indicator_code"]
    age_dim = row["age_dim"] or None
    sex_dim = row["sex_dim"] or None

    dim_order = get_dimension_order(AGENCY, dataflow_id, version, token)
    filters = {"REF_AREA": REF_AREA, "FREQ": FREQ, "INDICATOR": indicator_code}
    key = build_series_key(dim_order, filters)

    resp = fetch_data(AGENCY, dataflow_id, version, key, token)
    root = ET.fromstring(resp.content)

    retrieved_at = datetime.now(timezone.utc)
    known = {"REF_AREA", "FREQ", "INDICATOR"} | {d for d in (age_dim, sex_dim) if d}

    rows = []
    for series in root.findall(".//gen:Series", NS):
        series_values = {
            v.get("id"): v.get("value") for v in series.findall("gen:SeriesKey/gen:Value", NS)
        }
        age = series_values.get(age_dim, "_T") if age_dim else "_T"
        sex_raw = series_values.get(sex_dim, "_T") if sex_dim else "_T"
        sex = SEX_CODE_MAP.get(sex_raw, sex_raw)
        other_dims = {k: v for k, v in series_values.items() if k not in known}

        for obs in series.findall("gen:Obs", NS):
            time_period = obs.find("gen:ObsDimension", NS).get("value")
            obs_value = obs.find("gen:ObsValue", NS).get("value")
            attrs = {
                a.get("id"): a.get("value") for a in obs.findall("gen:Attributes/gen:Value", NS)
            }
            rows.append(
                {
                    "source": "tuik",
                    "dataflow_id": dataflow_id,
                    "indicator": row["indicator"],
                    "ref_area": series_values.get("REF_AREA", REF_AREA),
                    "freq": series_values.get("FREQ", FREQ),
                    "sex": sex,
                    "age": age,
                    "unit": attrs.get("UNIT_MEASURE"),
                    "other_dims": dumps_other_dims(other_dims),
                    "time_period": time_period,
                    "obs_value": float(obs_value),
                    "obs_flag": attrs.get("CONF_STATUS"),
                    "snapshot_id": snapshot_id,
                    "retrieved_at": retrieved_at,
                }
            )
    df = pd.DataFrame(rows)
    df.attrs["snapshot_id"] = snapshot_id
    return df


def main() -> int:
    imap = _load_map()
    token = get_access_token()

    # Grouped by dataflow_id, not iterated row by row: several indicator_map
    # rows can share one dataflow_id (see fetch_indicator()'s docstring), and
    # every one of them has to land in the same snapshot file.
    for dataflow_id, group in imap.groupby("dataflow_id", sort=False):
        snapshot_id = f"tuik_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
        frames = []
        withdrawn = False
        for _, row in group.iterrows():
            label = f"{row['indicator']} ({dataflow_id}, indicator={row['source_indicator_code']})"
            print(f"Fetching {label}...")
            try:
                frames.append(fetch_indicator(row, token, snapshot_id))
            except requests.exceptions.HTTPError as e:
                if e.response is not None and e.response.status_code == 404:
                    # The dataflow itself is gone from TUIK's catalogue, not a
                    # transient error -- dataflow_inventory.py's daily diff
                    # already catches and reports this as DATAFLOW_WITHDRAWN
                    # (see report.py). Skip the whole group rather than write
                    # a partial snapshot for it.
                    print(f"  WARNING: {dataflow_id} returned 404 -- dataflow no longer exists, skipping")
                    withdrawn = True
                    break
                if e.response is not None and e.response.status_code == 401:
                    # The token (fetched once at the top of main()) expired
                    # mid-run -- the indicator_map has grown enough that a
                    # full run can now outlast it. Refresh once and retry
                    # this row rather than losing the whole run.
                    print("  token expired, refreshing and retrying...")
                    token = get_access_token()
                    frames.append(fetch_indicator(row, token, snapshot_id))
                    continue
                raise
        if withdrawn:
            continue

        df = pd.concat(frames, ignore_index=True)
        if df.empty:
            print("  WARNING: no observations returned -- skipping snapshot")
            continue
        df = df.sort_values(["indicator", "age", "sex", "time_period"]).reset_index(drop=True)
        out_path = write_snapshot(df, "tuik", dataflow_id, snapshot_id=snapshot_id)
        print(f"  saved {len(df)} observations to {out_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
