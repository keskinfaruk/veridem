"""
The daily run -- the one script the GitHub Actions cron workflow calls:

    1. Fetch every watched TUIK + Eurostat + TUIK-press indicator, and the
       TUIK dataflow inventory (see fetch_tuik_indicators.py,
       fetch_eurostat_indicators.py, fetch_tuik_press_indicators.py,
       dataflow_inventory.py).
    2. For every dataflow that already had a prior snapshot, diff the fresh
       one against it.
    3. Discard the fresh snapshot file for any dataflow where nothing
       changed. A fetch always writes a *new* file (new snapshot_id) even
       when the underlying data is byte-identical to last time -- keeping
       every one of those forever would turn "immutable snapshot history"
       into mostly noise. Exiting silently on no change means no action at
       all, not a silent commit of a redundant file.
    4. Whatever *did* change gets kept. A demographic data change gets
       written into a change report and signalled to the workflow
       (`has_changes`) so it knows whether to open a PR. A catalogue-level
       change (TUIK's own service changing shape, not a demographic figure)
       never goes through the PR -- it's appended to a private log
       (technical_log.py) that daily.yml commits straight to main
       (`has_technical_changes`).

Failure handling: every fetch step is isolated so one source's outage
never blocks detecting real changes in another source. But the run still
exits non-zero if anything failed, so the workflow shows red and GitHub's
own failed-scheduled-workflow email is the failure notification --
deliberately not a second, custom-built notification channel.

tuik_press differs from the other two sources in one respect worth
remembering: it's an undocumented, reverse-engineered API (see
tuik_press_client.py's module docstring), not a stable published service.
The mitigation is the same "fail loudly" mechanism as everything else
here, not a bespoke gate: its parsers already raise ValueError on a
structural surprise (missing header row, no year columns -- see
fetch_tuik_press_indicators.py) rather than silently emitting wrong rows,
which surfaces as a normal fetch error in `errors` below and a red run,
same as a TUIK/Eurostat outage. A shape change that *doesn't* trip a
parser error but does produce a wrong number is the residual risk this
doesn't cover -- guarded only as well as sanity.py's plausible-range/
volatility checks catch it, same exposure the SDMX sources already have.
instant_notice.py's filter_curated_press() additionally keeps a single
press release (up to ~26 series at once) from bursting that many public
posts in one day -- only each release's headline indicator is
instant-notice-eligible.
"""

import os
import sys
import traceback
from pathlib import Path

import pandas as pd

import bluesky_client
import dataflow_inventory as dataflow_inventory_module
import fetch_eurostat_indicators
import fetch_tuik_indicators
import fetch_tuik_press_indicators
from dataflow_inventory import diff_inventory, latest_two_inventory_snapshots
from diff import diff_observations, latest_two_snapshots
from feed import append_notices
from instant_notice import build_notices
from report import generate_change_report
from schema import INVENTORY_DIR, RAW_DIR, connect
from technical_log import append_entry as append_technical_log_entry

REPORT_PATH = Path(__file__).resolve().parent.parent / "CHANGE_REPORT.md"


def _run_fetchers() -> list[str]:
    """Run every fetcher, collecting error messages without letting one
    source's failure stop the others from running."""
    errors = []
    for label, fn in [
        ("TUIK indicators", fetch_tuik_indicators.main),
        ("Eurostat indicators", fetch_eurostat_indicators.main),
        ("TUIK press indicators", fetch_tuik_press_indicators.main),
        ("TUIK dataflow inventory", dataflow_inventory_module.main),
    ]:
        print(f"\n=== {label} ===")
        try:
            fn()
        except Exception as e:  # noqa: BLE001 -- deliberately broad: one source's
            # failure must never take the others down with it.
            print(f"ERROR: {label} fetch failed: {e}", file=sys.stderr)
            traceback.print_exc()
            errors.append(f"{label} fetch failed: {e}")
    return errors


def _find_snapshot_file(base_dir: Path, snapshot_id: str, dataflow_id: str | None) -> Path | None:
    """Locate a snapshot's parquet file by the snapshot_id embedded in its
    filename -- matches the naming snapshot.py / dataflow_inventory.py use.
    """
    pattern = f"**/{dataflow_id}__{snapshot_id}.parquet" if dataflow_id else f"**/inventory__{snapshot_id}.parquet"
    matches = list(base_dir.glob(pattern))
    return matches[0] if matches else None


def _withdrawn_tuik_dataflow_ids(con) -> set[str]:
    """TUIK dataflow_ids with observations on record but absent from the
    latest catalogue inventory -- i.e. TUIK withdrew them. Without this
    exclusion, diff_observations() would re-classify the one remaining
    historical snapshot as a brand-new NEW_SERIES every single run (old_id
    stays None forever), since these dataflows never get fetched again.
    Only meaningful for source == 'tuik' -- other sources aren't tracked by
    this inventory. See ROADMAP_LOG.md for how this was found.
    """
    _, latest_inv_id = latest_two_inventory_snapshots(con)
    if latest_inv_id is None:
        return set()
    current = set(
        con.execute(
            "SELECT DISTINCT dataflow_id FROM dataflow_inventory WHERE snapshot_id = ?", [latest_inv_id]
        ).df()["dataflow_id"]
    )
    ever_fetched = set(
        con.execute("SELECT DISTINCT dataflow_id FROM observations WHERE source = 'tuik'").df()["dataflow_id"]
    )
    return ever_fetched - current


def _prune_unchanged_and_collect_changes() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Diff every dataflow's fresh snapshot against its previous one.
    Delete the fresh file where nothing changed; keep it (and record the
    change) where something did. Returns (observation_changes, inventory_changes).
    """
    con = connect()
    obs_changes = []
    withdrawn_tuik_ids = _withdrawn_tuik_dataflow_ids(con)

    dataflows = con.execute("SELECT DISTINCT source, dataflow_id FROM observations").df()
    for _, row in dataflows.iterrows():
        if row["source"] == "tuik" and row["dataflow_id"] in withdrawn_tuik_ids:
            continue
        old_id, new_id = latest_two_snapshots(con, row["source"], row["dataflow_id"])
        # old_id may be None (first-ever fetch for this dataflow) -- that's
        # not "nothing to compare", diff_observations treats it correctly as
        # everything being a NEW_SERIES debut. new_id is never None here:
        # this dataflow came from a DISTINCT query over observations, so at
        # least one snapshot of it necessarily exists.
        changes = diff_observations(con, row["source"], row["dataflow_id"], old_id, new_id)
        new_file = _find_snapshot_file(RAW_DIR, new_id, row["dataflow_id"])
        if changes.empty:
            if new_file:
                new_file.unlink()
        else:
            obs_changes.append(changes)

    inv_old, inv_new = latest_two_inventory_snapshots(con)
    inv_changes = pd.DataFrame()
    if inv_new is not None:
        inv_changes = diff_inventory(con, inv_old, inv_new)
        inv_file = _find_snapshot_file(INVENTORY_DIR, inv_new, None)
        if inv_changes.empty and inv_file:
            inv_file.unlink()

    obs_result = pd.concat(obs_changes, ignore_index=True) if obs_changes else pd.DataFrame()
    return obs_result, inv_changes


def _publish_instant_notices(obs_changes: pd.DataFrame, con) -> tuple[list[str], bool]:
    """Turkiye-only instant fact notices. Updates changes.xml
    unconditionally (needs no credentials); posts to Bluesky only if
    BLUESKY_DATA_HANDLE / BLUESKY_DATA_APP_PASSWORD are configured. Returns
    (error strings, whether any Turkiye notice actually existed) -- the
    latter is distinct from "has_changes" overall, since a run's only
    changes could be entirely non-Turkiye (e.g. a Eurostat figure for
    another country), in which case changes.xml is correctly never touched.
    """
    notices = build_notices(obs_changes, con)
    if not notices:
        return [], False

    errors = []

    try:
        path = append_notices(notices)
        print(f"Wrote {len(notices)} instant notice(s) to {path}")
    except Exception as e:  # noqa: BLE001 -- a feed-write failure must not
        # block the Bluesky attempt below, or the PR this function's caller
        # still needs to open for the underlying data change.
        print(f"ERROR: failed to update changes.xml: {e}", file=sys.stderr)
        traceback.print_exc()
        errors.append(f"changes.xml update failed: {e}")

    try:
        handle, app_password = bluesky_client.get_credentials()
        session = bluesky_client.create_session(handle, app_password)
        for notice in notices:
            bluesky_client.post(session, notice["bluesky_text"])
            print(f"Posted to Bluesky: {notice['title']}")
    except Exception as e:  # noqa: BLE001
        print(f"ERROR: Bluesky posting failed: {e}", file=sys.stderr)
        traceback.print_exc()
        errors.append(f"Bluesky posting failed: {e}")

    return errors, True


def main() -> int:
    errors = _run_fetchers()

    print("\n=== Diffing fresh snapshots against prior ones ===")
    obs_changes, inv_changes = _prune_unchanged_and_collect_changes()

    con = connect()  # fresh connection: some snapshot files were just deleted above
    report_text = generate_change_report(obs_changes, con)
    has_changes = bool(report_text)
    has_tr_notice = False

    if has_changes:
        REPORT_PATH.write_text(report_text, encoding="utf-8")
        print(f"\nChanges detected -- wrote {REPORT_PATH}")
        print("\n=== Instant Turkiye notices ===")
        notice_errors, has_tr_notice = _publish_instant_notices(obs_changes, con)
        errors.extend(notice_errors)
    elif REPORT_PATH.exists():
        REPORT_PATH.unlink()
    if not has_changes:
        print("\nNo changes detected. Exiting quietly.")

    # Catalogue-level changes never go through CHANGE_REPORT.md/the PR --
    # see technical_log.py.
    print("\n=== Technical changes log (catalogue-level, private) ===")
    has_technical_changes = False
    if inv_changes.empty:
        print("No catalogue-level changes.")
    else:
        try:
            has_technical_changes = append_technical_log_entry(inv_changes)
            print(f"Logged {len(inv_changes)} catalogue change(s) to data/technical_changes_log.md")
        except Exception as e:  # noqa: BLE001 -- a log-write failure must not
            # block has_changes/has_tr_notice from being reported correctly.
            print(f"ERROR: technical changes log write failed: {e}", file=sys.stderr)
            traceback.print_exc()
            errors.append(f"technical changes log write failed: {e}")

    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as f:
            f.write(f"has_changes={'true' if has_changes else 'false'}\n")
            f.write(f"has_tr_notice={'true' if has_tr_notice else 'false'}\n")
            f.write(f"has_technical_changes={'true' if has_technical_changes else 'false'}\n")

    if errors:
        print(f"\n{len(errors)} error(s) during this run:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
