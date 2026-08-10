"""
Observation-level change detection.

Compares two snapshots of the same (source, dataflow_id) pair in the
`observations` table and classifies every difference into one of four
observation-level change classes:

    NEW_PERIOD  - a time period appeared for a series that already existed
    NEW_SERIES  - a dimension combination (age/sex/ref_area/...) appeared
                  that was never seen in the old snapshot at all
    REVISED     - a value for an existing (series, period) changed
    WITHDRAWN   - a value that existed in the old snapshot is gone in the new one

STRUCTURAL and NEW_DATAFLOW are dataflow-catalogue-level classes, not
observation-level ones -- see dataflow_inventory.py for those.

This is deliberately snapshot-to-snapshot, not "latest vs first ever": the
daily run always diffs the newest snapshot against the one immediately
before it, so a revision that gets revised again later shows up as its own
event each time, and the full history of how a figure moved is just the
sequence of snapshots -- nothing here ever needs to be re-derived.
"""

import pandas as pd

# Columns that identify a "series" -- everything about an observation except
# the time period and the value itself. Two rows matching on all of these
# are the same series at a (possibly different) point in time.
SERIES_KEY = ["source", "dataflow_id", "indicator", "ref_area", "freq", "sex", "age", "other_dims"]

# Parquet doubles round-trip exactly, so this is just a guard against
# genuine floating-point noise, not a tolerance for "close enough" revisions.
VALUE_EPS = 1e-9


def _snapshot_frame(con, source: str, dataflow_id: str, snapshot_id: str) -> pd.DataFrame:
    return con.execute(
        "SELECT * FROM observations WHERE source = ? AND dataflow_id = ? AND snapshot_id = ?",
        [source, dataflow_id, snapshot_id],
    ).df()


def latest_two_snapshots(con, source: str, dataflow_id: str) -> tuple[str | None, str | None]:
    """Return (previous_snapshot_id, latest_snapshot_id) for a dataflow,
    ordered by retrieval time. previous is None if fewer than 2 snapshots
    exist yet; latest is None only if there are zero."""
    df = con.execute(
        "SELECT snapshot_id, min(retrieved_at) AS retrieved_at "
        "FROM observations WHERE source = ? AND dataflow_id = ? "
        "GROUP BY snapshot_id ORDER BY retrieved_at",
        [source, dataflow_id],
    ).df()
    if df.empty:
        return (None, None)
    if len(df) == 1:
        return (None, df["snapshot_id"].iloc[0])
    return (df["snapshot_id"].iloc[-2], df["snapshot_id"].iloc[-1])


def _empty_result() -> pd.DataFrame:
    cols = SERIES_KEY + [
        "time_period", "change_class", "old_value", "new_value",
        "old_snapshot_id", "new_snapshot_id",
    ]
    return pd.DataFrame(columns=cols)


def diff_observations(
    con, source: str, dataflow_id: str, old_snapshot_id: str | None, new_snapshot_id: str
) -> pd.DataFrame:
    """Classify every observation-level change between two snapshots of the
    same dataflow. `old_snapshot_id=None` means "no prior snapshot exists"
    -- every row in the new snapshot is then a NEW_SERIES debut, which is
    the correct call: nothing has a history to be a mere NEW_PERIOD against.
    Returns one row per changed (series, time_period); empty if nothing
    changed.
    """
    new = _snapshot_frame(con, source, dataflow_id, new_snapshot_id)
    old = _snapshot_frame(con, source, dataflow_id, old_snapshot_id) if old_snapshot_id else new.iloc[0:0]

    if old.empty and new.empty:
        return _empty_result()

    merge_cols = SERIES_KEY + ["time_period"]
    merged = old[merge_cols + ["obs_value"]].merge(
        new[merge_cols + ["obs_value"]],
        on=merge_cols,
        how="outer",
        suffixes=("_old", "_new"),
        indicator=True,
    )

    old_series = set(map(tuple, old[SERIES_KEY].drop_duplicates().values)) if not old.empty else set()

    def classify(row):
        if row["_merge"] == "left_only":
            return "WITHDRAWN"
        if row["_merge"] == "right_only":
            key = tuple(row[c] for c in SERIES_KEY)
            return "NEW_SERIES" if key not in old_series else "NEW_PERIOD"
        old_v, new_v = row["obs_value_old"], row["obs_value_new"]
        old_na, new_na = pd.isna(old_v), pd.isna(new_v)
        if old_na and new_na:
            return None  # both missing (e.g. a suppressed/provisional-with-no-figure
            # observation, seen in practice on Eurostat's confidential geos) -- unchanged
        if old_na != new_na:
            return "REVISED"  # a value appeared or was withdrawn while the period persisted
        if abs(old_v - new_v) > VALUE_EPS:
            return "REVISED"
        return None  # unchanged

    merged["change_class"] = merged.apply(classify, axis=1)
    changed = merged[merged["change_class"].notna()].copy()
    changed["old_value"] = changed["obs_value_old"]
    changed["new_value"] = changed["obs_value_new"]
    changed["old_snapshot_id"] = old_snapshot_id
    changed["new_snapshot_id"] = new_snapshot_id
    changed = changed.drop(columns=["_merge", "obs_value_old", "obs_value_new"])

    # A debuting series brings its whole fetched history with it (e.g. 24
    # years for a first-ever TFR fetch) -- that's one NEW_SERIES event, not
    # one per period. Collapse to a single row per series, keeping the
    # latest period as the representative one.
    is_new_series = changed["change_class"] == "NEW_SERIES"
    if is_new_series.any():
        debuts = (
            changed[is_new_series]
            .sort_values("time_period")
            .groupby(SERIES_KEY, as_index=False)
            .tail(1)
        )
        changed = pd.concat([changed[~is_new_series], debuts], ignore_index=True)

    return changed.sort_values(["indicator", "ref_area", "time_period"]).reset_index(drop=True)


def diff_all_dataflows(con) -> pd.DataFrame:
    """Run diff_observations() for every (source, dataflow_id) that has at
    least one snapshot, comparing its latest two. This is what the daily run
    actually calls -- one pass over everything currently in the data bank."""
    dataflows = con.execute(
        "SELECT DISTINCT source, dataflow_id FROM observations ORDER BY 1, 2"
    ).df()

    results = []
    for _, row in dataflows.iterrows():
        old_id, new_id = latest_two_snapshots(con, row["source"], row["dataflow_id"])
        if new_id is None:
            continue
        changes = diff_observations(con, row["source"], row["dataflow_id"], old_id, new_id)
        if not changes.empty:
            results.append(changes)

    return pd.concat(results, ignore_index=True) if results else _empty_result()
