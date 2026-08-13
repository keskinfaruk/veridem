"""
Indicator-candidate discovery: what's newly available in TUIK SDMX
(category 11, Population and Demography) and tuik_press (same category)
that isn't wired into this project yet.

Diff-based ("new since yesterday's snapshot"), not "every untracked
dataflow/table" -- many are already known and deliberately deferred, so
flagging all of them every run would bury a genuinely new one in noise.
Reuses dataflow_inventory.py's NEW_DATAFLOW detection (filtered to
category 11) and press_table_inventory.py's PRESS_TABLE_NEW class.

Neither a demographic change nor catalogue bookkeeping, so it skips
CHANGE_REPORT.md and the private technical log: its own report becomes a
GitHub issue comment instead (see daily.yml).
"""

import pandas as pd

CANDIDATE_REPORT_PATH_NAME = "CANDIDATE_INDICATORS.md"


def sdmx_candidates(inv_changes: pd.DataFrame, theme11_ids: set[str]) -> pd.DataFrame:
    """This run's NEW_DATAFLOW rows (from dataflow_inventory.diff_inventory())
    restricted to dataflows under SDMX category 11."""
    if inv_changes.empty:
        return inv_changes.iloc[0:0]
    new_flows = inv_changes[inv_changes["change_class"] == "NEW_DATAFLOW"]
    return new_flows[new_flows["dataflow_id"].isin(theme11_ids)].reset_index(drop=True)


def has_candidates(sdmx_df: pd.DataFrame, press_df: pd.DataFrame) -> bool:
    return not sdmx_df.empty or not press_df.empty


def render_report(sdmx_df: pd.DataFrame, press_df: pd.DataFrame) -> str:
    """One dated Markdown section, meant to become a GitHub issue
    comment (see daily.yml) -- not accumulated locally the way
    technical_changes_log.md is, the issue thread itself is the record.
    Returns "" if there's nothing to report."""
    if not has_candidates(sdmx_df, press_df):
        return ""

    lines = ["New indicator candidates found by today's scan, not yet in the pipeline:", ""]

    if not sdmx_df.empty:
        lines.append(f"### TUIK SDMX, category 11 ({len(sdmx_df)})")
        lines.append("")
        for _, row in sdmx_df.iterrows():
            name = row["name_new"] if pd.notna(row.get("name_new")) else ""
            lines.append(f"- `{row['dataflow_id']}`{f': {name}' if name else ''}")
        lines.append("")

    if not press_df.empty:
        lines.append(f"### tuik_press tables ({len(press_df)})")
        lines.append("")
        for theme, group in press_df.groupby("theme_title"):
            lines.append(f"- **{theme}**")
            for _, row in group.iterrows():
                lines.append(f"  - {row['table_title']}")
        lines.append("")

    lines.append(
        "To add one: SDMX, a new row in `data/indicator_map.csv`; tuik_press, "
        "a new parser + `TARGETS` entry in `fetch_tuik_press_indicators.py`. "
        "The baseline notice queue picks up a newly-added series automatically "
        "on the next scheduled run, no separate step needed."
    )
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    """Manual/local check: run today's scan against the real committed
    inventory history, print the report, write nothing. For a real run,
    daily_run.py calls sdmx_candidates()/render_report() directly with
    that run's own fresh inv_changes/press table diff."""
    import dataflow_inventory
    import press_table_inventory
    import tuik_categories
    from schema import connect

    con = connect()
    inv_old, inv_new = dataflow_inventory.latest_two_inventory_snapshots(con)
    inv_changes = dataflow_inventory.diff_inventory(con, inv_old, inv_new) if inv_new else pd.DataFrame()

    pt_old, pt_new = press_table_inventory.latest_two_inventory_snapshots(con)
    press_changes = press_table_inventory.diff_inventory(con, pt_old, pt_new) if pt_new else pd.DataFrame()

    theme11_ids = tuik_categories.theme11_dataflow_ids()
    sdmx_df = sdmx_candidates(inv_changes, theme11_ids)

    report = render_report(sdmx_df, press_changes)
    print(report if report else "No new candidates against the currently committed snapshots.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
