"""
Catalogue-level snapshots and diffs: what each source *offers*, not what any
indicator's value is.

Three catalogues, all stored the same immutable, append-only way as
observations, each with its own directory and DuckDB view (see schema.py):

    tuik_dataflows  TÜİK's SDMX dataflow list
                    NEW_DATAFLOW / DATAFLOW_WITHDRAWN / STRUCTURAL
    press_themes    the tuik_press theme list (~169 entries, all subjects)
                    PRESS_THEME_NEW / PRESS_THEME_WITHDRAWN
    press_tables    table titles inside each tuik_press Population and
                    Demography release, one level finer than the themes
                    PRESS_TABLE_NEW

STRUCTURAL is a DSD version bump: parser risk, not necessarily new data,
since SDMX versions track structure rather than content. DATAFLOW_WITHDRAWN
is a whole dataset going away, distinct from a single value disappearing
(that is observation-level WITHDRAWN, in diff.py).

press_tables has no withdrawal or structural class: a table disappearing
blocks nothing this project has built.
"""

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import pandas as pd

from schema import (
    INVENTORY_DIR,
    INVENTORY_SCHEMA,
    PRESS_INVENTORY_DIR,
    PRESS_INVENTORY_SCHEMA,
    PRESS_TABLE_INVENTORY_DIR,
    PRESS_TABLE_INVENTORY_SCHEMA,
    latest_two_snapshots,
)
from tuik_client import NS, get, get_access_token
from tuik_press_client import discover_press_ids, fetch_press

COMMON_NAME_TAG = "{http://www.sdmx.org/resources/sdmxml/schemas/v2_1/common}Name"
STR = "{http://www.sdmx.org/resources/sdmxml/schemas/v2_1/structure}"
DEMO_CATEGORY = "Population and Demography"
DEMO_CATEGORY_ID = "11"


def _new_frame(rows: list[dict], table_schema: dict, snapshot_id: str) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=list(table_schema))
    df.attrs["snapshot_id"] = snapshot_id
    return df


def _snapshot_id(prefix: str) -> str:
    return f"{prefix}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"


def _old_new(
    con, view: str, old_id: str | None, new_id: str, columns: list[str]
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """The two snapshots to diff, restricted to `columns`. A None old_id (no
    prior snapshot exists) yields an empty slice of the new frame, so every
    diff below can treat a first-ever run as an ordinary outer join."""
    new = con.execute(f"SELECT * FROM {view} WHERE snapshot_id = ?", [new_id]).df()[columns]
    if old_id is None:
        return new.iloc[0:0], new
    old = con.execute(f"SELECT * FROM {view} WHERE snapshot_id = ?", [old_id]).df()[columns]
    return old, new


# --- TÜİK SDMX dataflow catalogue -----------------------------------------


def fetch_dataflows(token: str | None = None) -> pd.DataFrame:
    token = token or get_access_token()
    root = ET.fromstring(get("dataflow/TR/all/latest", token, params={"detail": "full"}).content)

    snapshot_id = _snapshot_id("tuik_inventory")
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
    return _new_frame(rows, INVENTORY_SCHEMA, snapshot_id)


def diff_dataflows(con, old_id: str | None, new_id: str) -> pd.DataFrame:
    cols = ["dataflow_id", "version", "name"]
    old, new = _old_new(con, "dataflow_inventory", old_id, new_id, cols)
    merged = old.merge(
        new,
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
    changed = merged[merged["change_class"].notna()]
    return (
        changed[["dataflow_id", "version_old", "version_new", "name_old", "name_new", "change_class"]]
        .sort_values("dataflow_id")
        .reset_index(drop=True)
    )


# --- tuik_press theme catalogue -------------------------------------------


def fetch_press_themes() -> pd.DataFrame:
    snapshot_id = _snapshot_id("tuik_press_inventory")
    retrieved_at = datetime.now(timezone.utc)
    rows = [
        {
            "title": e["title"],
            "category_id": str(e["categoryId"]) if e.get("categoryId") is not None else None,
            "category_name": e.get("categoryName"),
            "current_press_id": str(e["id"]) if e.get("id") is not None else None,
            "snapshot_id": snapshot_id,
            "retrieved_at": retrieved_at,
        }
        for e in discover_press_ids()
    ]
    return _new_frame(rows, PRESS_INVENTORY_SCHEMA, snapshot_id)


def diff_press_themes(con, old_id: str | None, new_id: str) -> pd.DataFrame:
    """Keyed on `title`; `current_press_id` is deliberately not compared."""
    cols = ["title", "category_name"]
    old, new = _old_new(con, "press_dataflow_inventory", old_id, new_id, cols)
    merged = old.merge(
        new,
        on="title",
        how="outer",
        suffixes=("_old", "_new"),
        indicator=True,
    )
    merged["change_class"] = merged["_merge"].map(
        {"right_only": "PRESS_THEME_NEW", "left_only": "PRESS_THEME_WITHDRAWN"}
    )
    changed = merged[merged["change_class"].notna()]
    return (
        changed[["title", "category_name_old", "category_name_new", "change_class"]]
        .sort_values("title")
        .reset_index(drop=True)
    )


# --- tuik_press table catalogue -------------------------------------------


def fetch_press_tables() -> pd.DataFrame:
    snapshot_id = _snapshot_id("tuik_press_tables")
    retrieved_at = datetime.now(timezone.utc)
    rows = []
    for theme in discover_press_ids(category=DEMO_CATEGORY):
        press_data = fetch_press(int(theme["id"]))
        for list_type in ("tables", "statisticalTables"):
            for t in press_data.get(list_type, []):
                rows.append(
                    {
                        "theme_title": theme["title"],
                        "table_title": t["title"],
                        "list_type": list_type,
                        "snapshot_id": snapshot_id,
                        "retrieved_at": retrieved_at,
                    }
                )
    return _new_frame(rows, PRESS_TABLE_INVENTORY_SCHEMA, snapshot_id)


def diff_press_tables(con, old_id: str | None, new_id: str) -> pd.DataFrame:
    """New (theme_title, table_title) pairs, excluding tables
    fetch_tuik_press_indicators.py already parses. Keyed on the pair, not
    list_type: a table moving between `tables` and `statisticalTables`
    between releases is not a new candidate."""
    from fetch_tuik_press_indicators import COVERED_TABLES

    cols = ["theme_title", "table_title"]
    old, new = _old_new(con, "press_table_inventory", old_id, new_id, cols)
    old, new = old.drop_duplicates(), new.drop_duplicates()

    merged = new.merge(old, on=cols, how="left", indicator=True)
    added = merged[merged["_merge"] == "left_only"][cols].reset_index(drop=True)
    if added.empty:
        # Explicit: an empty boolean mask on df[[]] selects zero *columns*,
        # not zero rows, which would drop theme_title/table_title entirely.
        return added.assign(change_class=pd.Series(dtype="object"))

    covered = added.apply(lambda r: (r["theme_title"], r["table_title"]) in COVERED_TABLES, axis=1)
    return added[~covered].assign(change_class="PRESS_TABLE_NEW").sort_values(cols).reset_index(drop=True)


def theme11_dataflow_ids(token: str | None = None) -> set[str]:
    """Every dataflow_id currently categorised under SDMX category 11
    (Population and Demography) or a descendant, read live from TÜİK's own
    categorisation catalogue.

    The Target Ref `id` is a full dot-joined path from the scheme root, not
    the leaf category's own id: DF_DOGUM_TEMEL_DOG_GOST maps to Target id
    `11.11_9.11_9_3` even though that leaf's own id is just `11_9_3`. So
    every dataflow under 11 has a Target id of `11` or starting with `11.`.
    """
    token = token or get_access_token()
    root = ET.fromstring(get("categorisation/TR/all", token).content)

    ids = set()
    for cat in root.findall(f".//{STR}Categorisation"):
        target, source = cat.find(f"{STR}Target/Ref"), cat.find(f"{STR}Source/Ref")
        if target is None or source is None:
            continue
        category_id = target.get("id", "")
        if category_id == DEMO_CATEGORY_ID or category_id.startswith(f"{DEMO_CATEGORY_ID}."):
            ids.add(source.get("id"))
    return ids


# --- catalogue registry ----------------------------------------------------


@dataclass(frozen=True)
class Catalogue:
    """One catalogue's full wiring. `file_prefix` is the filename stem both
    write_snapshot() and snapshot_path() use, so the writer and the pruner
    can never drift apart."""

    label: str
    view: str
    directory: Path
    file_prefix: str
    fetch: Callable[[], pd.DataFrame]
    diff: Callable[..., pd.DataFrame]


CATALOGUES = {
    "tuik_dataflows": Catalogue(
        "TUIK dataflow inventory",
        "dataflow_inventory",
        INVENTORY_DIR,
        "inventory",
        fetch_dataflows,
        diff_dataflows,
    ),
    "press_themes": Catalogue(
        "TUIK press theme inventory",
        "press_dataflow_inventory",
        PRESS_INVENTORY_DIR,
        "press_inventory",
        fetch_press_themes,
        diff_press_themes,
    ),
    "press_tables": Catalogue(
        "TUIK press table inventory",
        "press_table_inventory",
        PRESS_TABLE_INVENTORY_DIR,
        "press_tables",
        fetch_press_tables,
        diff_press_tables,
    ),
}


def write_snapshot(cat: Catalogue, df: pd.DataFrame) -> Path:
    """Write one catalogue snapshot. Raises rather than overwrite: catalogue
    snapshots are append-only, like observations."""
    snapshot_id = df.attrs.get("snapshot_id") or df["snapshot_id"].iloc[0]
    out_dir = cat.directory / datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_dir.mkdir(parents=True, exist_ok=True)

    out_path = out_dir / f"{cat.file_prefix}__{snapshot_id}.parquet"
    if out_path.exists():
        raise FileExistsError(f"{out_path} already exists -- snapshots are never overwritten.")
    df.to_parquet(out_path, index=False)
    return out_path


def latest_two(con, cat: Catalogue) -> tuple[str | None, str | None]:
    return latest_two_snapshots(con, cat.view)


def snapshot_path(cat: Catalogue, snapshot_id: str) -> Path | None:
    matches = list(cat.directory.glob(f"**/{cat.file_prefix}__{snapshot_id}.parquet"))
    return matches[0] if matches else None


def refresh(name: str) -> Path:
    """Fetch and store one catalogue's current state."""
    cat = CATALOGUES[name]
    df = cat.fetch()
    out_path = write_snapshot(cat, df)
    print(f"Saved {len(df)} {name} row(s) to {out_path}")
    return out_path


def main() -> int:
    for name in CATALOGUES:
        refresh(name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
