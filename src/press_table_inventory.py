"""
tuik_press table-level catalogue: snapshot + diff, one level finer-grained
than press_dataflow_inventory.py's theme list.

Scoped to the "Population and Demography" category only. For each theme,
snapshots every table title in its current release (`tables` +
`statisticalTables` combined). Diffing two snapshots surfaces
PRESS_TABLE_NEW: a table title that wasn't there last time, excluding
anything fetch_tuik_press_indicators.py already parses (COVERED_TABLES).

No withdrawal/structural class here (unlike dataflow_inventory.py/
press_dataflow_inventory.py): a table disappearing doesn't block
anything this project has built.
"""

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from fetch_tuik_press_indicators import COVERED_TABLES
from schema import PRESS_TABLE_INVENTORY_DIR, PRESS_TABLE_INVENTORY_SCHEMA
from tuik_press_client import discover_press_ids, fetch_press

CATEGORY = "Population and Demography"


def fetch_inventory() -> pd.DataFrame:
    themes = discover_press_ids(category=CATEGORY)

    snapshot_id = f"tuik_press_tables_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    retrieved_at = datetime.now(timezone.utc)

    rows = []
    for theme in themes:
        theme_title = theme["title"]
        press_data = fetch_press(int(theme["id"]))
        for list_type in ("tables", "statisticalTables"):
            for t in press_data.get(list_type, []):
                rows.append(
                    {
                        "theme_title": theme_title,
                        "table_title": t["title"],
                        "list_type": list_type,
                        "snapshot_id": snapshot_id,
                        "retrieved_at": retrieved_at,
                    }
                )
    df = pd.DataFrame(rows, columns=list(PRESS_TABLE_INVENTORY_SCHEMA))
    df.attrs["snapshot_id"] = snapshot_id
    return df


def write_inventory_snapshot(df: pd.DataFrame) -> Path:
    snapshot_id = df.attrs.get("snapshot_id") or df["snapshot_id"].iloc[0]
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_dir = PRESS_TABLE_INVENTORY_DIR / date_str
    out_dir.mkdir(parents=True, exist_ok=True)

    out_path = out_dir / f"press_tables__{snapshot_id}.parquet"
    if out_path.exists():
        raise FileExistsError(
            f"{out_path} already exists -- inventory snapshots are immutable and "
            "append-only too, never overwritten."
        )
    df.to_parquet(out_path, index=False)
    return out_path


def latest_two_inventory_snapshots(con) -> tuple[str | None, str | None]:
    df = con.execute(
        "SELECT snapshot_id, min(retrieved_at) AS retrieved_at FROM press_table_inventory "
        "GROUP BY snapshot_id ORDER BY retrieved_at"
    ).df()
    if df.empty:
        return (None, None)
    if len(df) == 1:
        return (None, df["snapshot_id"].iloc[0])
    return (df["snapshot_id"].iloc[-2], df["snapshot_id"].iloc[-1])


def diff_inventory(con, old_snapshot_id: str | None, new_snapshot_id: str) -> pd.DataFrame:
    """New (theme_title, table_title) pairs in `new` not present in `old`,
    excluding anything COVERED_TABLES already accounts for. Keyed on the
    pair, not list_type -- a table moving between `tables` and
    `statisticalTables` between releases isn't a new candidate."""
    new = con.execute(
        "SELECT DISTINCT theme_title, table_title FROM press_table_inventory WHERE snapshot_id = ?",
        [new_snapshot_id],
    ).df()
    old = (
        con.execute(
            "SELECT DISTINCT theme_title, table_title FROM press_table_inventory WHERE snapshot_id = ?",
            [old_snapshot_id],
        ).df()
        if old_snapshot_id
        else new.iloc[0:0]
    )

    merged = new.merge(old, on=["theme_title", "table_title"], how="left", indicator=True)
    added = merged[merged["_merge"] == "left_only"][["theme_title", "table_title"]].reset_index(drop=True)
    # Explicit early return for the 0-row case: df[[]] means "select zero
    # columns" in pandas, not "select zero rows", so an empty boolean mask
    # applied below would silently drop theme_title/table_title too.
    if added.empty:
        return added.assign(change_class=pd.Series(dtype="object"))

    covered_mask = added.apply(lambda r: (r["theme_title"], r["table_title"]) in COVERED_TABLES, axis=1)
    added = added[~covered_mask].copy()
    added["change_class"] = "PRESS_TABLE_NEW"
    return added.sort_values(["theme_title", "table_title"]).reset_index(drop=True)


def main() -> int:
    print("Fetching tuik_press table catalogue (Population and Demography)...")
    df = fetch_inventory()
    out_path = write_inventory_snapshot(df)
    print(f"Saved {len(df)} table entries across {df['theme_title'].nunique()} themes to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
