"""
TUIK dataflow catalogue: snapshot + diff.

Fetches TUIK's full dataflow list (dataflow/TR/all) the same immutable-
snapshot way as observations, and diffs two snapshots to catch three
catalogue-level "technical change" classes, distinct from a substantive
data change:

    NEW_DATAFLOW       - an entirely new dataset appeared
    DATAFLOW_WITHDRAWN - a dataflow present in the old catalogue is gone
                          from the new one -- the dataset itself, not one
                          value in it (that's observation-level WITHDRAWN
                          in diff.py; a different thing at a different
                          layer)
    STRUCTURAL         - an existing dataflow's DSD version changed (parser
                          risk, not necessarily new data -- SDMX versions
                          track structure, not content)

This describes the catalogue itself, not any indicator's values, so it gets
its own snapshot directory (data/inventory/tuik/, deliberately outside
data/raw/ -- see schema.py) and its own DuckDB view, `dataflow_inventory`.
"""

import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from schema import INVENTORY_DIR, INVENTORY_SCHEMA
from tuik_client import NS, get, get_access_token

COMMON_NAME_TAG = "{http://www.sdmx.org/resources/sdmxml/schemas/v2_1/common}Name"


def fetch_inventory(token: str | None = None) -> pd.DataFrame:
    token = token or get_access_token()
    resp = get("dataflow/TR/all/latest", token, params={"detail": "full"})
    root = ET.fromstring(resp.content)

    snapshot_id = f"tuik_inventory_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    retrieved_at = datetime.now(timezone.utc)

    rows = []
    for d in root.findall(".//str:Dataflow", NS):
        name_el = d.find(COMMON_NAME_TAG)
        rows.append(
            {
                "dataflow_id": d.get("id"),
                "version": d.get("version"),
                "agency_id": d.get("agencyID"),
                "name": name_el.text if name_el is not None else None,
                "snapshot_id": snapshot_id,
                "retrieved_at": retrieved_at,
            }
        )
    df = pd.DataFrame(rows, columns=list(INVENTORY_SCHEMA))
    df.attrs["snapshot_id"] = snapshot_id
    return df


def write_inventory_snapshot(df: pd.DataFrame) -> Path:
    snapshot_id = df.attrs.get("snapshot_id") or df["snapshot_id"].iloc[0]
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_dir = INVENTORY_DIR / date_str
    out_dir.mkdir(parents=True, exist_ok=True)

    out_path = out_dir / f"inventory__{snapshot_id}.parquet"
    if out_path.exists():
        raise FileExistsError(
            f"{out_path} already exists -- inventory snapshots are immutable and "
            "append-only too, never overwritten."
        )
    df.to_parquet(out_path, index=False)
    return out_path


def latest_two_inventory_snapshots(con) -> tuple[str | None, str | None]:
    """Same idea as diff.latest_two_snapshots(), for the inventory table."""
    df = con.execute(
        "SELECT snapshot_id, min(retrieved_at) AS retrieved_at FROM dataflow_inventory "
        "GROUP BY snapshot_id ORDER BY retrieved_at"
    ).df()
    if df.empty:
        return (None, None)
    if len(df) == 1:
        return (None, df["snapshot_id"].iloc[0])
    return (df["snapshot_id"].iloc[-2], df["snapshot_id"].iloc[-1])


def diff_inventory(con, old_snapshot_id: str | None, new_snapshot_id: str) -> pd.DataFrame:
    """Classify catalogue-level changes between two inventory snapshots.

    A dataflow_id present in `old` but missing from `new` is
    DATAFLOW_WITHDRAWN -- TÜİK does occasionally pull a whole dataflow, not
    just individual values within one.
    """
    new = con.execute("SELECT * FROM dataflow_inventory WHERE snapshot_id = ?", [new_snapshot_id]).df()
    old = (
        con.execute("SELECT * FROM dataflow_inventory WHERE snapshot_id = ?", [old_snapshot_id]).df()
        if old_snapshot_id
        else new.iloc[0:0]
    )

    merged = old[["dataflow_id", "version", "name"]].merge(
        new[["dataflow_id", "version", "name"]],
        on="dataflow_id",
        how="outer",
        suffixes=("_old", "_new"),
        indicator=True,
    )

    def classify(row):
        if row["_merge"] == "right_only":
            return "NEW_DATAFLOW"
        if row["_merge"] == "left_only":
            return "DATAFLOW_WITHDRAWN"
        return "STRUCTURAL" if row["version_old"] != row["version_new"] else None

    merged["change_class"] = merged.apply(classify, axis=1)
    changed = merged[merged["change_class"].notna()].copy()
    return (
        changed[["dataflow_id", "version_old", "version_new", "name_old", "name_new", "change_class"]]
        .sort_values("dataflow_id")
        .reset_index(drop=True)
    )


def main() -> int:
    print("Fetching TUIK dataflow inventory...")
    df = fetch_inventory()
    out_path = write_inventory_snapshot(df)
    print(f"Saved {len(df)} dataflows to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
