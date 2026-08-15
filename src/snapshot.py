"""
Immutable, append-only raw snapshot storage.

Every fetch run writes one file per (source, dataflow) under
data/raw/{source}/{date}/{dataflow_id}__{snapshot_id}.parquet and never
touches an existing file again. A revision to a previously-published value
shows up as a *new row* in a *new* snapshot file, never as an edit to an
old one.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from schema import OBSERVATIONS_SCHEMA, RAW_DIR


def new_snapshot_id(source: str) -> str:
    """A snapshot_id embeds the retrieval timestamp, so re-running the same
    fetch twice in a day never collides with an earlier run's file."""
    return f"{source}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"


def dumps_other_dims(d: dict) -> str:
    """other_dims is a JSON string rather than a parquet struct so files from
    dataflows with different extra dimensions can be read back together with
    union_by_name; a struct column's fields would have to match exactly."""
    return json.dumps(d, ensure_ascii=False, sort_keys=True)


def write_snapshot(
    df: pd.DataFrame,
    source: str,
    dataflow_id: str,
    snapshot_id: str | None = None,
    raw_dir: Path | None = None,
) -> Path:
    """Write `df`, already normalized to the observations schema, as one
    immutable snapshot file. Raises if the target exists (snapshots are
    append-only) or if `df` is missing a required column."""
    missing = set(OBSERVATIONS_SCHEMA) - set(df.columns)
    if missing:
        raise ValueError(f"DataFrame missing required observations columns: {missing}")

    snapshot_id = snapshot_id or new_snapshot_id(source)
    raw_dir = raw_dir or RAW_DIR
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_dir = raw_dir / source / date_str
    out_dir.mkdir(parents=True, exist_ok=True)

    out_path = out_dir / f"{dataflow_id}__{snapshot_id}.parquet"
    if out_path.exists():
        raise FileExistsError(
            f"{out_path} already exists -- raw snapshots are immutable and "
            "append-only, never overwritten. Something is re-using a snapshot_id."
        )

    df = df[list(OBSERVATIONS_SCHEMA.keys())]  # enforce column order
    df.to_parquet(out_path, index=False)
    return out_path
