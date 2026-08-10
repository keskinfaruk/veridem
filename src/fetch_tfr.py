"""
Fetches a tidy Total Fertility Rate time series (Turkiye, national, annual)
from a live TUIK API call, parsed into the project's normalized long schema
and saved as Parquet: auth -> discover dataflow -> fetch DSD -> build series
key -> fetch data -> normalize -> persist -> display.

Kept as a minimal, single-indicator reference; fetch_tuik_indicators.py is
the generic, indicator_map.csv-driven pipeline used for real runs.
"""

import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import pandas as pd

from series_key import build_series_key, get_dimension_order
from snapshot import dumps_other_dims, write_snapshot
from tuik_client import NS, fetch_data, get_access_token

AGENCY = "TR"
DATAFLOW_ID = "DF_DOGUM_TEMEL_DOG_GOST"  # "Basic Fertility Indicators"
VERSION = "1.0"
INDICATOR_CODE = "NG_TDH"  # source code for Total Fertility Rate
INDICATOR_NORMALIZED = "TFR"


def fetch_tfr_series() -> pd.DataFrame:
    token = get_access_token()

    dim_order = get_dimension_order(AGENCY, DATAFLOW_ID, VERSION, token)
    filters = {"REF_AREA": "TR", "FREQ": "A", "INDICATOR": INDICATOR_CODE}
    key = build_series_key(dim_order, filters)

    resp = fetch_data(AGENCY, DATAFLOW_ID, VERSION, key, token)
    root = ET.fromstring(resp.content)

    snapshot_id = f"tuik_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    retrieved_at = datetime.now(timezone.utc)

    rows = []
    for series in root.findall(".//gen:Series", NS):
        series_values = {
            v.get("id"): v.get("value") for v in series.findall("gen:SeriesKey/gen:Value", NS)
        }
        # Dimensions already captured in named schema columns; keep the rest
        # (mostly "_Z" not-applicable for this dataflow) for full fidelity.
        known = {"REF_AREA", "FREQ", "INDICATOR"}
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
                    "dataflow_id": DATAFLOW_ID,
                    "indicator": INDICATOR_NORMALIZED,
                    "ref_area": series_values.get("REF_AREA"),
                    "freq": series_values.get("FREQ"),
                    "sex": "T",  # TFR is not sex-disaggregated
                    "age": "_T",  # TFR is not age-disaggregated (see ASFR for that)
                    "unit": attrs.get("UNIT_MEASURE"),
                    "other_dims": dumps_other_dims(other_dims),
                    "time_period": time_period,
                    "obs_value": float(obs_value),
                    "obs_flag": attrs.get("CONF_STATUS"),
                    "snapshot_id": snapshot_id,
                    "retrieved_at": retrieved_at,
                }
            )

    return pd.DataFrame(rows)


def main() -> int:
    print(f"Fetching TFR ({DATAFLOW_ID}, indicator={INDICATOR_CODE}) from TUIK...")
    df = fetch_tfr_series()
    df = df.sort_values("time_period").reset_index(drop=True)

    out_path = write_snapshot(df, "tuik", DATAFLOW_ID)
    print(f"Saved {len(df)} observations to {out_path}")

    print("\nTotal Fertility Rate — Turkiye, annual")
    print(df[["time_period", "obs_value", "obs_flag"]].to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
