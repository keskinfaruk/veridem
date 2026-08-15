"""
Observation-level change detection.

Compares two snapshots of the same (source, dataflow_id) pair and classifies
every difference:

    NEW_PERIOD  a time period appeared for a series that already existed
    NEW_SERIES  a dimension combination appeared that the old snapshot
                had no trace of
    REVISED     a value for an existing (series, period) changed
    WITHDRAWN   a value present in the old snapshot is gone

Catalogue-level classes (NEW_DATAFLOW, STRUCTURAL, ...) live in
inventory.py; they describe a service changing shape, not a figure moving.

Deliberately snapshot-to-snapshot, not "latest vs first ever": the daily run
diffs the newest snapshot against the one immediately before it, so a value
revised twice produces its own event each time and the full history of how a
figure moved is just the sequence of snapshots.
"""

import json

import pandas as pd

from schema import latest_two_snapshots as _latest_two

# Everything about an observation except the period and the value itself.
# Two rows matching on all of these are the same series.
SERIES_KEY = ["source", "dataflow_id", "indicator", "ref_area", "freq", "sex", "age", "other_dims"]

# Keys the connectors sweep into other_dims that describe the *file*, not the
# series. Eurostat's SDMX-CSV carries "LAST UPDATE", which changes on every
# republication even when no value moves; leaving it in the identity made a
# routine republication read as the whole dataflow being withdrawn and
# re-created. Stripped for comparison only: stored snapshots keep them.
METADATA_DIMS = {"LAST UPDATE", "DATAFLOW"}


def series_identity(other_dims: str) -> str:
    """other_dims with file-level metadata removed, for identity comparison."""
    try:
        parsed = json.loads(other_dims) if other_dims else {}
    except (TypeError, ValueError):
        return other_dims
    if not isinstance(parsed, dict):
        return other_dims
    return json.dumps(
        {k: v for k, v in parsed.items() if k not in METADATA_DIMS},
        ensure_ascii=False,
        sort_keys=True,
    )

# Parquet doubles round-trip exactly, so this guards against floating-point
# noise only, not a tolerance for "close enough" revisions.
VALUE_EPS = 1e-9

RESULT_COLUMNS = SERIES_KEY + [
    "time_period", "change_class", "old_value", "new_value",
    "old_snapshot_id", "new_snapshot_id",
]


def latest_two_snapshots(con, source: str, dataflow_id: str) -> tuple[str | None, str | None]:
    """(previous, latest) snapshot_id for one dataflow. previous is None when
    only one snapshot exists yet."""
    return _latest_two(con, "observations", "source = ? AND dataflow_id = ?", [source, dataflow_id])


def _snapshot_frame(con, source: str, dataflow_id: str, snapshot_id: str) -> pd.DataFrame:
    return con.execute(
        "SELECT * FROM observations WHERE source = ? AND dataflow_id = ? AND snapshot_id = ?",
        [source, dataflow_id, snapshot_id],
    ).df()


def diff_observations(
    con, source: str, dataflow_id: str, old_snapshot_id: str | None, new_snapshot_id: str
) -> pd.DataFrame:
    """Classify every observation-level change between two snapshots of one
    dataflow. `old_snapshot_id=None` means no prior snapshot exists, so every
    row is a NEW_SERIES debut: nothing has a history to be a mere NEW_PERIOD
    against. One row per changed (series, period); empty if nothing changed.
    """
    new = _snapshot_frame(con, source, dataflow_id, new_snapshot_id)
    old = _snapshot_frame(con, source, dataflow_id, old_snapshot_id) if old_snapshot_id else new.iloc[0:0]

    if old.empty and new.empty:
        return pd.DataFrame(columns=RESULT_COLUMNS)

    # Match on the metadata-stripped identity, but carry the real other_dims
    # through so callers can still look the series up in its own snapshot.
    identity_key = [c for c in SERIES_KEY if c != "other_dims"] + ["_identity"]
    for frame in (old, new):
        frame["_identity"] = frame["other_dims"].map(series_identity)

    merge_cols = identity_key + ["time_period"]
    merged = old[merge_cols + ["other_dims", "obs_value"]].merge(
        new[merge_cols + ["other_dims", "obs_value"]],
        on=merge_cols,
        how="outer",
        suffixes=("_old", "_new"),
        indicator=True,
    )
    merged["other_dims"] = merged["other_dims_new"].fillna(merged["other_dims_old"])

    old_series = set(map(tuple, old[identity_key].drop_duplicates().values)) if not old.empty else set()

    def classify(row):
        if row["_merge"] == "left_only":
            return "WITHDRAWN"
        if row["_merge"] == "right_only":
            key = tuple(row[c] for c in identity_key)
            return "NEW_SERIES" if key not in old_series else "NEW_PERIOD"
        old_v, new_v = row["obs_value_old"], row["obs_value_new"]
        old_na, new_na = pd.isna(old_v), pd.isna(new_v)
        if old_na and new_na:
            # Both missing, e.g. a suppressed observation on a confidential
            # Eurostat geo. Unchanged.
            return None
        if old_na != new_na:
            return "REVISED"  # a value appeared or vanished while the period persisted
        return "REVISED" if abs(old_v - new_v) > VALUE_EPS else None

    merged["change_class"] = merged.apply(classify, axis=1)
    changed = merged[merged["change_class"].notna()].copy()
    changed["old_value"] = changed["obs_value_old"]
    changed["new_value"] = changed["obs_value_new"]
    changed["old_snapshot_id"] = old_snapshot_id
    changed["new_snapshot_id"] = new_snapshot_id
    changed = changed.drop(
        columns=["_merge", "obs_value_old", "obs_value_new", "other_dims_old", "other_dims_new"]
    )

    # A debuting series brings its whole fetched history with it (24 years for
    # a first TFR fetch). That is one NEW_SERIES event, not one per period, so
    # collapse to a single row keeping the latest period as representative.
    is_new_series = changed["change_class"] == "NEW_SERIES"
    if is_new_series.any():
        debuts = (
            changed[is_new_series]
            .sort_values("time_period")
            .groupby(identity_key, as_index=False)
            .tail(1)
        )
        changed = pd.concat([changed[~is_new_series], debuts], ignore_index=True)

    changed = changed.drop(columns=["_identity"])
    return changed[RESULT_COLUMNS].sort_values(
        ["indicator", "ref_area", "time_period"]
    ).reset_index(drop=True)
