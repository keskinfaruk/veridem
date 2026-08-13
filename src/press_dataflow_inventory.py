"""
TUIK press-bulletin theme catalogue: snapshot + diff.

Same idea as dataflow_inventory.py, one layer over -- that module tracks
TUIK's SDMX dataflow catalogue, this one tracks the ~169-theme catalogue
behind veriportali.tuik.gov.tr's press API (tuik_press_client.py), which is
a separate service with its own shape and its own risk of silently
changing. Two catalogue-level change classes:

    PRESS_THEME_NEW         - a theme appeared that wasn't in the prior
                               snapshot (e.g. TUIK starts a new bulletin
                               series)
    PRESS_THEME_WITHDRAWN   - a theme present in the old snapshot is gone
                               from the new one

Deliberately NOT diffed: `current_press_id`, the release ID discover_press_ids()
returns for each theme. That field changes every time TUIK publishes a new
bulletin for a theme that already exists -- normal release cadence, the
press-API equivalent of a NEW_PERIOD, not a catalogue-shape change. TUIK's
press listing gives no separate theme ID; `title` is the identity column
(see tuik_press_client.py's discover_press_ids() docstring for the raw
shape). A future STRUCTURAL-style class could compare category_id/
category_name per title if TUIK is ever seen recategorising a theme, but
nothing here has demonstrated that happening yet.

This describes the catalogue itself, not any indicator's values -- own
snapshot directory (data/inventory/tuik_press/, same reasoning as
data/inventory/tuik/ in schema.py: kept out of data/raw/ so the
observations glob can't union it in) and own DuckDB view,
`press_dataflow_inventory`.
"""

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from schema import PRESS_INVENTORY_DIR, PRESS_INVENTORY_SCHEMA
from tuik_press_client import discover_press_ids


def fetch_inventory() -> pd.DataFrame:
    entries = discover_press_ids()

    snapshot_id = f"tuik_press_inventory_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    retrieved_at = datetime.now(timezone.utc)

    rows = [
        {
            "title": e["title"],
            "category_id": str(e.get("categoryId")) if e.get("categoryId") is not None else None,
            "category_name": e.get("categoryName"),
            "current_press_id": str(e.get("id")) if e.get("id") is not None else None,
            "snapshot_id": snapshot_id,
            "retrieved_at": retrieved_at,
        }
        for e in entries
    ]
    df = pd.DataFrame(rows, columns=list(PRESS_INVENTORY_SCHEMA))
    df.attrs["snapshot_id"] = snapshot_id
    return df


def write_inventory_snapshot(df: pd.DataFrame) -> Path:
    snapshot_id = df.attrs.get("snapshot_id") or df["snapshot_id"].iloc[0]
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_dir = PRESS_INVENTORY_DIR / date_str
    out_dir.mkdir(parents=True, exist_ok=True)

    out_path = out_dir / f"press_inventory__{snapshot_id}.parquet"
    if out_path.exists():
        raise FileExistsError(
            f"{out_path} already exists -- inventory snapshots are immutable and "
            "append-only too, never overwritten."
        )
    df.to_parquet(out_path, index=False)
    return out_path


def latest_two_inventory_snapshots(con) -> tuple[str | None, str | None]:
    """Same idea as dataflow_inventory.latest_two_inventory_snapshots(), for
    the press theme catalogue."""
    df = con.execute(
        "SELECT snapshot_id, min(retrieved_at) AS retrieved_at FROM press_dataflow_inventory "
        "GROUP BY snapshot_id ORDER BY retrieved_at"
    ).df()
    if df.empty:
        return (None, None)
    if len(df) == 1:
        return (None, df["snapshot_id"].iloc[0])
    return (df["snapshot_id"].iloc[-2], df["snapshot_id"].iloc[-1])


def diff_inventory(con, old_snapshot_id: str | None, new_snapshot_id: str) -> pd.DataFrame:
    """Classify catalogue-level changes between two press-theme snapshots,
    keyed on `title` (see module docstring for why -- no stable theme ID
    exists). `current_press_id` is intentionally not compared."""
    new = con.execute(
        "SELECT * FROM press_dataflow_inventory WHERE snapshot_id = ?", [new_snapshot_id]
    ).df()
    old = (
        con.execute("SELECT * FROM press_dataflow_inventory WHERE snapshot_id = ?", [old_snapshot_id]).df()
        if old_snapshot_id
        else new.iloc[0:0]
    )

    merged = old[["title", "category_name"]].merge(
        new[["title", "category_name"]],
        on="title",
        how="outer",
        suffixes=("_old", "_new"),
        indicator=True,
    )

    def classify(row):
        if row["_merge"] == "right_only":
            return "PRESS_THEME_NEW"
        if row["_merge"] == "left_only":
            return "PRESS_THEME_WITHDRAWN"
        return None

    merged["change_class"] = merged.apply(classify, axis=1)
    changed = merged[merged["change_class"].notna()].copy()
    return (
        changed[["title", "category_name_old", "category_name_new", "change_class"]]
        .sort_values("title")
        .reset_index(drop=True)
    )


def main() -> int:
    print("Fetching TUIK press theme catalogue...")
    df = fetch_inventory()
    out_path = write_inventory_snapshot(df)
    print(f"Saved {len(df)} press themes to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
