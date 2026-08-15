"""
Generic, indicator_map-driven Eurostat fetch pipeline.

Each dataflow has its own dimension shape (DEMO_GIND/DEMO_FIND key their
indicators through `indic_de`; DEMO_MLEXPEC has no such dimension and keys
life expectancy through `age`, since that dataflow *is* life expectancy by
age and sex), but the batching and parsing below are shared: group rows by
dataflow_id, batch every code into one '+'-joined request, parse generically.

`geo` is deliberately left unfiltered, giving every geo Eurostat publishes
for that dataflow plus its own aggregates. Eurostat's demography datasets
cover Europe and a few immediate neighbours only (60 distinct geo codes on
DEMO_GIND); there is no worldwide aggregate. A global comparison needs a
separate connector.
"""

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from eurostat_client import build_series_key, fetch_data_csv, get_dimension_order
from snapshot import dumps_other_dims, write_snapshot

FREQ = "A"
INDICATOR_MAP_PATH = Path(__file__).resolve().parent.parent / "data" / "indicator_map.csv"


def _load_map() -> pd.DataFrame:
    imap = pd.read_csv(INDICATOR_MAP_PATH, dtype=str, keep_default_na=False)
    return imap[imap["source"] == "eurostat"]


def fetch_dataflow(dataflow_id: str, rows: pd.DataFrame) -> pd.DataFrame:
    """Fetch every indicator_map row for one Eurostat dataflow in a single
    batched request (codes joined with '+'), geo left unfiltered."""
    selector_dims = rows["selector_dim"].unique()
    fixed = rows[["fixed_dim", "fixed_value"]].drop_duplicates()
    sex_dims = set(rows["sex_dim"]) - {""}
    if len(selector_dims) != 1:
        raise ValueError(f"{dataflow_id}: rows disagree on selector_dim: {selector_dims}")
    if len(fixed) != 1:
        raise ValueError(f"{dataflow_id}: rows disagree on fixed_dim/fixed_value: {fixed.to_dict('records')}")
    if len(sex_dims) > 1:
        raise ValueError(f"{dataflow_id}: rows disagree on sex_dim: {sex_dims}")

    selector_dim = selector_dims[0]
    fixed_dim, fixed_value = fixed.iloc[0]
    sex_dim = next(iter(sex_dims), None) or None

    dim_order = get_dimension_order(dataflow_id)
    filters = {"freq": FREQ, selector_dim: "+".join(rows["source_indicator_code"])}
    if fixed_dim:
        filters[fixed_dim] = fixed_value
    key = build_series_key(dim_order, filters)

    raw = fetch_data_csv(dataflow_id, key)
    imap_by_code = rows.set_index("source_indicator_code")

    known_cols = {"freq", selector_dim, "geo", "TIME_PERIOD", "OBS_VALUE", "OBS_FLAG"}
    if fixed_dim:
        known_cols.add(fixed_dim)
    if sex_dim:
        known_cols.add(sex_dim)

    snapshot_id = f"eurostat_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    retrieved_at = datetime.now(timezone.utc)

    out_rows = []
    for _, r in raw.iterrows():
        code = r[selector_dim]
        if code not in imap_by_code.index:
            continue  # never trust an API to only echo back what was asked
        meta = imap_by_code.loc[code]
        age = code if selector_dim == "age" else "_T"
        sex = r[sex_dim] if sex_dim and pd.notna(r.get(sex_dim)) else "T"
        other_dims = {k: r[k] for k in raw.columns if k not in known_cols and pd.notna(r[k])}
        out_rows.append(
            {
                "source": "eurostat",
                "dataflow_id": dataflow_id,
                "indicator": meta["indicator"],
                "ref_area": r["geo"],
                "freq": r["freq"],
                "sex": sex,
                "age": age,
                "unit": meta["unit"],
                "other_dims": dumps_other_dims(other_dims),
                "time_period": str(r["TIME_PERIOD"]),
                "obs_value": float(r["OBS_VALUE"]),
                "obs_flag": r["OBS_FLAG"] if pd.notna(r["OBS_FLAG"]) else None,
                "snapshot_id": snapshot_id,
                "retrieved_at": retrieved_at,
            }
        )
    df = pd.DataFrame(out_rows)
    df.attrs["snapshot_id"] = snapshot_id
    return df


def main() -> int:
    imap = _load_map()
    for dataflow_id, rows in imap.groupby("dataflow_id"):
        indicators = ", ".join(rows["indicator"].unique())
        print(f"Fetching {indicators} ({dataflow_id}, all geos)...")
        df = fetch_dataflow(dataflow_id, rows)
        if df.empty:
            print("  WARNING: no observations returned -- skipping snapshot")
            continue
        df = df.sort_values(["indicator", "ref_area", "time_period"]).reset_index(drop=True)
        out_path = write_snapshot(df, "eurostat", dataflow_id, snapshot_id=df.attrs["snapshot_id"])
        print(f"  saved {len(df)} observations ({df['ref_area'].nunique()} geos) to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
