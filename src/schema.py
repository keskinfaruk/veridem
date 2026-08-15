"""
Canonical schema for the `observations` long table and the DuckDB views
that make every stored snapshot queryable with plain SQL.

Raw snapshot parquet files under data/raw/{source}/{date}/ are the source of
truth: immutable, append-only, committed to git (see snapshot.py). DuckDB
holds no copy of its own -- every view is computed live over those files on
each connect, so a query can never drift from what is committed and there is
no load step to forget.
"""

from pathlib import Path

import duckdb

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
RAW_DIR = DATA_DIR / "raw"

# Catalogue snapshots live outside RAW_DIR on purpose: the `observations`
# view globs RAW_DIR/**/*.parquet with union_by_name, which pads a schema
# mismatch with NULLs instead of erroring, so nesting them under raw/ would
# silently union them in as mostly-null observation rows.
INVENTORY_DIR = DATA_DIR / "inventory" / "tuik"
PRESS_INVENTORY_DIR = DATA_DIR / "inventory" / "tuik_press"
PRESS_TABLE_INVENTORY_DIR = DATA_DIR / "inventory" / "tuik_press_tables"

# Column order and DuckDB types for the `observations` long table -- the
# schema every connector must normalize into.
OBSERVATIONS_SCHEMA = {
    "source": "VARCHAR",  # 'tuik' | 'tuik_press' | 'eurostat'
    "dataflow_id": "VARCHAR",
    "indicator": "VARCHAR",  # normalized code, e.g. 'TFR'
    "ref_area": "VARCHAR",  # 'TR', 'TR51' (NUTS-3), province code
    "freq": "VARCHAR",  # 'A' | 'Q' | 'M'
    "sex": "VARCHAR",  # 'T' | 'M' | 'F'
    "age": "VARCHAR",  # '_T', 'Y15T19', ...
    "unit": "VARCHAR",
    "other_dims": "VARCHAR",  # JSON-encoded string of source-specific extras
    "time_period": "VARCHAR",  # ISO 8601: '2026', '2026-Q1'
    "obs_value": "DOUBLE",
    "obs_flag": "VARCHAR",  # SDMX status flags
    "snapshot_id": "VARCHAR",  # which run produced this row
    "retrieved_at": "TIMESTAMP",
}

# TÜİK's SDMX dataflow catalogue itself (id/version/name).
INVENTORY_SCHEMA = {
    "dataflow_id": "VARCHAR",
    "version": "VARCHAR",
    "agency_id": "VARCHAR",
    "name": "VARCHAR",
    "snapshot_id": "VARCHAR",
    "retrieved_at": "TIMESTAMP",
}

# The tuik_press theme catalogue. TÜİK's press listing exposes no stable
# theme ID, so `title` is the identity column. `current_press_id` is carried
# for reference and excluded from the diff: it changes on every routine
# release, which is not a catalogue change.
PRESS_INVENTORY_SCHEMA = {
    "title": "VARCHAR",
    "category_id": "VARCHAR",
    "category_name": "VARCHAR",
    "current_press_id": "VARCHAR",
    "snapshot_id": "VARCHAR",
    "retrieved_at": "TIMESTAMP",
}

# Table titles inside each tuik_press release, one level finer than the
# theme catalogue. Identity is (theme_title, table_title); `list_type` is
# descriptive only and not part of the diff key.
PRESS_TABLE_INVENTORY_SCHEMA = {
    "theme_title": "VARCHAR",
    "table_title": "VARCHAR",
    "list_type": "VARCHAR",
    "snapshot_id": "VARCHAR",
    "retrieved_at": "TIMESTAMP",
}

# view name -> (directory, schema), the full set registered by connect().
VIEWS = {
    "observations": (RAW_DIR, OBSERVATIONS_SCHEMA),
    "dataflow_inventory": (INVENTORY_DIR, INVENTORY_SCHEMA),
    "press_dataflow_inventory": (PRESS_INVENTORY_DIR, PRESS_INVENTORY_SCHEMA),
    "press_table_inventory": (PRESS_TABLE_INVENTORY_DIR, PRESS_TABLE_INVENTORY_SCHEMA),
}


def _register_view(con: duckdb.DuckDBPyConnection, view: str, dir_path: Path, table_schema: dict) -> None:
    """Register `view` over every parquet file under `dir_path`, with every
    column CAST explicitly so the view's types never depend on whatever dtype
    a given file inferred at write time. Falls back to an empty but correctly
    typed view when no files exist yet, so callers need no "nothing fetched
    yet" special case."""
    if not (dir_path.exists() and any(dir_path.glob("**/*.parquet"))):
        empty = ", ".join(f"CAST(NULL AS {typ}) AS {col}" for col, typ in table_schema.items())
        con.execute(f"CREATE VIEW {view} AS SELECT {empty} WHERE FALSE")
        return

    cols = ",\n            ".join(f'CAST("{c}" AS {t}) AS {c}' for c, t in table_schema.items())
    glob = str(dir_path / "**" / "*.parquet").replace("\\", "/")
    con.execute(
        f"CREATE VIEW {view} AS SELECT\n            {cols}\n"
        f"        FROM read_parquet('{glob}', union_by_name=true)"
    )


def connect(raw_dir: Path | None = None, db_path: str | None = None) -> duckdb.DuckDBPyConnection:
    """Open a DuckDB connection with every view in VIEWS registered over the
    committed snapshot files.

    `db_path` defaults to in-memory: there is nothing to persist, since each
    view recomputes from the committed files on every connect. Pass a path
    only for a scratch file to explore in.
    """
    con = duckdb.connect(db_path or ":memory:")
    for view, (dir_path, table_schema) in VIEWS.items():
        _register_view(con, view, raw_dir if view == "observations" and raw_dir else dir_path, table_schema)
    return con


def latest_two_snapshots(
    con: duckdb.DuckDBPyConnection, view: str, where: str = "", params: list | None = None
) -> tuple[str | None, str | None]:
    """(previous_snapshot_id, latest_snapshot_id) in `view`, ordered by
    retrieval time. previous is None when only one snapshot exists; both are
    None when there are none. `where` optionally scopes to one series or
    dataflow."""
    clause = f"WHERE {where} " if where else ""
    ids = con.execute(
        f"SELECT snapshot_id, min(retrieved_at) AS t FROM {view} {clause}"
        "GROUP BY snapshot_id ORDER BY t",
        params or [],
    ).df()["snapshot_id"].tolist()
    if not ids:
        return (None, None)
    if len(ids) == 1:
        return (None, ids[0])
    return (ids[-2], ids[-1])
