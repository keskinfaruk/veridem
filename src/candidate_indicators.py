"""
Indicator-candidate discovery: what's newly available in TUIK SDMX
(category 11, Population and Demography) and tuik_press (same category)
that isn't wired into this project yet.

Diff-based ("new since yesterday's snapshot") rather than "every untracked
dataflow or table": many are already known and deliberately deferred, so
flagging all of them every run would bury a genuinely new one in noise.
Built on inventory.py's NEW_DATAFLOW (filtered to category 11) and
PRESS_TABLE_NEW classes.

Neither a demographic change nor catalogue bookkeeping, so it skips
CHANGE_REPORT.md and the private technical log: its report becomes a GitHub
issue comment instead (see daily.yml).
"""

import pandas as pd

CANDIDATE_REPORT_PATH_NAME = "CANDIDATE_INDICATORS.md"


def sdmx_candidates(inv_changes: pd.DataFrame, theme11_ids: set[str]) -> pd.DataFrame:
    """This run's NEW_DATAFLOW rows, restricted to SDMX category 11."""
    if inv_changes.empty:
        return inv_changes.iloc[0:0]
    new_flows = inv_changes[inv_changes["change_class"] == "NEW_DATAFLOW"]
    return new_flows[new_flows["dataflow_id"].isin(theme11_ids)].reset_index(drop=True)


def has_candidates(sdmx_df: pd.DataFrame, press_df: pd.DataFrame) -> bool:
    return not sdmx_df.empty or not press_df.empty


def render_report(sdmx_df: pd.DataFrame, press_df: pd.DataFrame) -> str:
    """One Markdown section, posted as a GitHub issue comment (see
    daily.yml). Not accumulated locally the way technical_changes_log.md is:
    the issue thread itself is the record. Returns "" if nothing to report."""
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
    """Manual check against the committed inventory history: prints the
    report, writes nothing. A real run goes through daily_run.py with that
    run's own fresh catalogue diffs."""
    import inventory
    from schema import connect

    con = connect()
    diffs = {}
    for name in ("tuik_dataflows", "press_tables"):
        cat = inventory.CATALOGUES[name]
        old_id, new_id = inventory.latest_two(con, cat)
        diffs[name] = cat.diff(con, old_id, new_id) if new_id else pd.DataFrame()

    sdmx_df = sdmx_candidates(diffs["tuik_dataflows"], inventory.theme11_dataflow_ids())
    report = render_report(sdmx_df, diffs["press_tables"])
    print(report if report else "No new candidates against the currently committed snapshots.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
