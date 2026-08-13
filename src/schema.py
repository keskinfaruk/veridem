"""
Canonical schema for the `observations` long table, and the DuckDB view
that makes it queryable with plain SQL.

Raw snapshot parquet files under data/raw/{source}/{date}/ are the source of
truth: immutable, append-only, committed to git (see snapshot.py). DuckDB
never holds its own copy of the data -- `observations` is a view computed
live over those files every time you connect, so a query can never drift
from what's actually committed, and there's no separate load step to forget
to run.
"""

from pathlib import Path

import duckdb

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
RAW_DIR = DATA_DIR / "raw"
# Deliberately NOT under RAW_DIR: observations' view globs `RAW_DIR/**/*.parquet`,
# and inventory snapshots have a totally different schema -- nesting them
# under raw/ would get them silently unioned into `observations` as
# mostly-null rows (union_by_name doesn't error on a schema mismatch, it
# just pads with NULL).
INVENTORY_DIR = DATA_DIR / "inventory" / "tuik"
# Separate catalogue: the tuik_press theme list (see press_dataflow_inventory.py),
# not TUIK's SDMX dataflow catalogue -- a different service with a different
# shape, so it gets its own directory and view rather than being unioned in.
PRESS_INVENTORY_DIR = DATA_DIR / "inventory" / "tuik_press"
# A third, finer-grained catalogue: table titles inside each tuik_press
# Population-and-Demography release, one level deeper than the theme list
# above. Own directory for the same union-by-name reason.
PRESS_TABLE_INVENTORY_DIR = DATA_DIR / "inventory" / "tuik_press_tables"

# Column order and DuckDB types for the `observations` long table -- the
# enforced source of truth every connector must write to.
OBSERVATIONS_SCHEMA = {
    "source": "VARCHAR",  # 'tuik' | 'eurostat' | 'un' | 'worldbank'
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


# Column order and DuckDB types for the `dataflow_inventory` table -- TUIK's
# dataflow catalogue itself (id/version/name), snapshotted the same
# immutable way as observations. See dataflow_inventory.py.
INVENTORY_SCHEMA = {
    "dataflow_id": "VARCHAR",
    "version": "VARCHAR",
    "agency_id": "VARCHAR",
    "name": "VARCHAR",
    "snapshot_id": "VARCHAR",
    "retrieved_at": "TIMESTAMP",
}


# Column order and DuckDB types for the `press_dataflow_inventory` table --
# the tuik_press theme catalogue (title/category), snapshotted the same
# immutable way. See press_dataflow_inventory.py. TÜİK's press listing API
# gives no stable theme ID -- `title` is the identity column; `current_press_id`
# is carried along for reference only and deliberately excluded from the
# diff, since it changes on every routine release (not a catalogue change).
PRESS_INVENTORY_SCHEMA = {
    "title": "VARCHAR",
    "category_id": "VARCHAR",
    "category_name": "VARCHAR",
    "current_press_id": "VARCHAR",
    "snapshot_id": "VARCHAR",
    "retrieved_at": "TIMESTAMP",
}


# Column order and DuckDB types for the `press_table_inventory` table.
# Identity is (theme_title, table_title); `list_type` is descriptive
# only, not part of the diff key.
PRESS_TABLE_INVENTORY_SCHEMA = {
    "theme_title": "VARCHAR",
    "table_title": "VARCHAR",
    "list_type": "VARCHAR",
    "snapshot_id": "VARCHAR",
    "retrieved_at": "TIMESTAMP",
}


def _typed_select_list(table_schema: dict) -> str:
    """CAST every column explicitly so a view's types never depend on
    whatever dtype a given parquet file happened to infer at write time."""
    return ",\n            ".join(
        f'CAST("{col}" AS {typ}) AS {col}' for col, typ in table_schema.items()
    )


def _register_view(con: duckdb.DuckDBPyConnection, view_name: str, dir_path: Path, table_schema: dict) -> None:
    """Register `view_name` as a live DuckDB view over every parquet file
    under `dir_path`, typed per `table_schema`. Falls back to an empty,
    correctly-typed view when no files exist yet, so downstream code never
    needs a special case for "nothing fetched yet"."""
    existing = list(dir_path.glob("**/*.parquet")) if dir_path.exists() else []
    if not existing:
        empty_cols = ", ".join(f"CAST(NULL AS {typ}) AS {col}" for col, typ in table_schema.items())
        con.execute(f"CREATE VIEW {view_name} AS SELECT {empty_cols} WHERE FALSE")
        return

    glob = str(dir_path / "**" / "*.parquet").replace("\\", "/")
    con.execute(
        f"""
        CREATE VIEW {view_name} AS
        SELECT
            {_typed_select_list(table_schema)}
        FROM read_parquet('{glob}', union_by_name=true)
        """
    )


def connect(raw_dir: Path | None = None, db_path: str | None = None) -> duckdb.DuckDBPyConnection:
    """Open a DuckDB connection with `observations`, `dataflow_inventory`,
    `press_dataflow_inventory`, and `press_table_inventory` registered as
    views over every committed raw snapshot parquet file.

    `db_path` defaults to in-memory: there's nothing to persist, since each
    view recomputes from the committed files on every connect. Pass a path
    only if you want a scratch file for exploration.
    """
    raw_dir = raw_dir or RAW_DIR
    con = duckdb.connect(db_path or ":memory:")
    _register_view(con, "observations", raw_dir, OBSERVATIONS_SCHEMA)
    _register_view(con, "dataflow_inventory", INVENTORY_DIR, INVENTORY_SCHEMA)
    _register_view(con, "press_dataflow_inventory", PRESS_INVENTORY_DIR, PRESS_INVENTORY_SCHEMA)
    _register_view(con, "press_table_inventory", PRESS_TABLE_INVENTORY_DIR, PRESS_TABLE_INVENTORY_SCHEMA)
    return con


def load_indicator_map(
    con: duckdb.DuckDBPyConnection, csv_path: Path | None = None
) -> duckdb.DuckDBPyConnection:
    """Register `indicator_map` (source codes -> normalized indicator codes)
    as a DuckDB table, read live from the committed CSV -- the same
    live-from-committed-file pattern as `observations`."""
    csv_path = csv_path or (DATA_DIR / "indicator_map.csv")
    path = str(csv_path).replace("\\", "/")
    con.execute(f"CREATE OR REPLACE TABLE indicator_map AS SELECT * FROM read_csv_auto('{path}')")
    return con


if __name__ == "__main__":
    # Smoke test: "what is the TFR series for Turkiye?" in one query, without
    # thinking about where the data came from.
    con = connect()
    print(
        con.execute(
            "SELECT indicator, ref_area, time_period, obs_value "
            "FROM observations WHERE indicator = 'TFR' ORDER BY time_period"
        ).df()
    )
