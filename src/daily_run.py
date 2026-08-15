"""
The daily run, called by the GitHub Actions cron workflow:

    1. Fetch every watched TUIK + Eurostat + tuik_press indicator, plus all
       three catalogues (see inventory.py).
    2. Diff each fresh snapshot against its prior one; delete the fresh file
       wherever nothing changed.
    3. A demographic change opens a PR (`has_changes`). A catalogue change
       goes to a private log instead, never a PR (`has_technical_changes`).
    4. Separately, scan for indicator candidates not yet wired into the
       project, surfaced as a GitHub issue (`has_new_candidates`).

Every fetch step is isolated so one source's failure cannot stop the others,
but the run exits non-zero if anything failed: a red workflow run and
GitHub's own failure email are the notification, with no separate channel.
"""

import os
import sys
import traceback
from pathlib import Path

import pandas as pd

import bluesky_client
import candidate_indicators
import fetch_eurostat_indicators
import fetch_tuik_indicators
import fetch_tuik_press_indicators
import instant_notice
import inventory
from diff import diff_observations, latest_two_snapshots
from feed import append_notices
from report import generate_change_report
from schema import RAW_DIR, connect
from technical_log import append_entry as append_technical_log_entry

REPO_ROOT = Path(__file__).resolve().parent.parent
REPORT_PATH = REPO_ROOT / "CHANGE_REPORT.md"
CANDIDATE_REPORT_PATH = REPO_ROOT / candidate_indicators.CANDIDATE_REPORT_PATH_NAME

# Appended to every instant notice's Bluesky text. Points at the landing page
# for now; becomes more useful once real indicator pages exist, with no
# template change needed then.
DASHBOARD_URL = "https://veridem.faruk.page/"


def _run_fetchers() -> list[str]:
    """Run every fetcher, collecting error messages without letting one
    source's failure stop the others from running."""
    steps = [
        ("TUIK indicators", fetch_tuik_indicators.main),
        ("Eurostat indicators", fetch_eurostat_indicators.main),
        ("TUIK press indicators", fetch_tuik_press_indicators.main),
    ]
    steps += [(cat.label, lambda n=name: inventory.refresh(n)) for name, cat in inventory.CATALOGUES.items()]

    errors = []
    for label, fn in steps:
        print(f"\n=== {label} ===")
        try:
            fn()
        except Exception as e:  # noqa: BLE001 -- one source must never take the others down
            print(f"ERROR: {label} fetch failed: {e}", file=sys.stderr)
            traceback.print_exc()
            errors.append(f"{label} fetch failed: {e}")
    return errors


def _observation_snapshot_path(dataflow_id: str, snapshot_id: str) -> Path | None:
    matches = list(RAW_DIR.glob(f"**/{dataflow_id}__{snapshot_id}.parquet"))
    return matches[0] if matches else None


def _withdrawn_tuik_dataflow_ids(con) -> set[str]:
    """TUIK dataflow_ids with observations on record but absent from the
    latest catalogue snapshot, i.e. TUIK withdrew them.

    Without this exclusion, diff_observations() would re-classify the one
    remaining historical snapshot as a brand-new NEW_SERIES every run
    (old_id stays None forever), since these are never fetched again.
    """
    _, latest_inv_id = inventory.latest_two(con, inventory.CATALOGUES["tuik_dataflows"])
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


def _prune_observations(con) -> pd.DataFrame:
    """Diff every dataflow's fresh snapshot against its previous one. Delete
    the fresh file where nothing changed; keep it where something did."""
    withdrawn = _withdrawn_tuik_dataflow_ids(con)
    changes = []

    for _, row in con.execute("SELECT DISTINCT source, dataflow_id FROM observations").df().iterrows():
        source, dataflow_id = row["source"], row["dataflow_id"]
        if source == "tuik" and dataflow_id in withdrawn:
            continue
        # old_id may be None (first-ever fetch), which diff_observations
        # correctly treats as a NEW_SERIES debut. new_id is never None: this
        # dataflow came from a DISTINCT query over observations.
        old_id, new_id = latest_two_snapshots(con, source, dataflow_id)
        diff = diff_observations(con, source, dataflow_id, old_id, new_id)
        if diff.empty:
            path = _observation_snapshot_path(dataflow_id, new_id)
            if path:
                path.unlink()
        else:
            changes.append(diff)

    return pd.concat(changes, ignore_index=True) if changes else pd.DataFrame()


def _prune_catalogues(con) -> dict[str, pd.DataFrame]:
    """Same prune-if-unchanged pass for each catalogue in inventory.py."""
    results = {}
    for name, cat in inventory.CATALOGUES.items():
        old_id, new_id = inventory.latest_two(con, cat)
        if new_id is None:
            results[name] = pd.DataFrame()
            continue
        diff = cat.diff(con, old_id, new_id)
        if diff.empty:
            path = inventory.snapshot_path(cat, new_id)
            if path:
                path.unlink()
        results[name] = diff
    return results


def _publish_instant_notices(obs_changes: pd.DataFrame, con) -> tuple[list[str], bool]:
    """Türkiye-only instant fact notices. Updates changes.xml unconditionally
    (needs no credentials); posts to Bluesky only if BLUESKY_DATA_HANDLE /
    BLUESKY_DATA_APP_PASSWORD are configured.

    Returns (errors, whether any Türkiye notice existed). The latter differs
    from has_changes overall: a run's only changes could be entirely
    non-Türkiye, in which case changes.xml is correctly never touched.
    """
    notices, suppressed = instant_notice.build_notices(obs_changes, con, base_url=DASHBOARD_URL)
    for note in suppressed:
        print(
            f"SUPPRESSED: {note} exceeds the auto-post limit "
            f"({instant_notice.MAX_AUTO_POST_WITHDRAWALS}). Not posted; see the change report.",
            file=sys.stderr,
        )
    if not notices:
        return [], False

    errors = []
    try:
        path = append_notices(notices)
        print(f"Wrote {len(notices)} instant notice(s) to {path}")
    except Exception as e:  # noqa: BLE001 -- must not block the Bluesky attempt or the PR
        print(f"ERROR: failed to update changes.xml: {e}", file=sys.stderr)
        traceback.print_exc()
        errors.append(f"changes.xml update failed: {e}")

    try:
        session = bluesky_client.create_session(*bluesky_client.get_credentials())
        for notice in notices:
            bluesky_client.post(session, notice["bluesky_text"])
            print(f"Posted to Bluesky: {notice['title']}")
    except Exception as e:  # noqa: BLE001
        print(f"ERROR: Bluesky posting failed: {e}", file=sys.stderr)
        traceback.print_exc()
        errors.append(f"Bluesky posting failed: {e}")

    return errors, True


def _log_technical_changes(catalogue_changes: dict[str, pd.DataFrame], errors: list[str]) -> bool:
    """Catalogue changes go to a private log, never to CHANGE_REPORT.md or a
    PR. See technical_log.py."""
    dataflows = catalogue_changes["tuik_dataflows"]
    themes = catalogue_changes["press_themes"]
    if dataflows.empty and themes.empty:
        print("No catalogue-level changes.")
        return False
    try:
        written = append_technical_log_entry(dataflows, press_changes=themes)
        print(f"Logged {len(dataflows) + len(themes)} catalogue change(s) to data/technical_changes_log.md")
        return written
    except Exception as e:  # noqa: BLE001 -- must not block the results above being reported
        print(f"ERROR: technical changes log write failed: {e}", file=sys.stderr)
        traceback.print_exc()
        errors.append(f"technical changes log write failed: {e}")
        return False


def _scan_candidates(catalogue_changes: dict[str, pd.DataFrame], errors: list[str]) -> bool:
    """Indicator candidates not yet wired into the pipeline. Neither a
    demographic change nor catalogue bookkeeping, so it becomes a GitHub
    issue rather than a PR or a technical-log entry."""
    if CANDIDATE_REPORT_PATH.exists():
        CANDIDATE_REPORT_PATH.unlink()
    try:
        sdmx = candidate_indicators.sdmx_candidates(
            catalogue_changes["tuik_dataflows"], inventory.theme11_dataflow_ids()
        )
        tables = catalogue_changes["press_tables"]
        if not candidate_indicators.has_candidates(sdmx, tables):
            print("No new indicator candidates.")
            return False
        CANDIDATE_REPORT_PATH.write_text(
            candidate_indicators.render_report(sdmx, tables), encoding="utf-8"
        )
        print(f"Found candidate(s) -- wrote {CANDIDATE_REPORT_PATH}")
        return True
    except Exception as e:  # noqa: BLE001 -- must not block the results above being reported
        print(f"ERROR: indicator-candidate scan failed: {e}", file=sys.stderr)
        traceback.print_exc()
        errors.append(f"indicator-candidate scan failed: {e}")
        return False


def _write_github_outputs(**flags: bool) -> None:
    github_output = os.environ.get("GITHUB_OUTPUT")
    if not github_output:
        return
    with open(github_output, "a", encoding="utf-8") as f:
        for name, value in flags.items():
            f.write(f"{name}={'true' if value else 'false'}\n")


def main() -> int:
    errors = _run_fetchers()

    print("\n=== Diffing fresh snapshots against prior ones ===")
    con = connect()
    obs_changes = _prune_observations(con)
    catalogue_changes = _prune_catalogues(con)

    con = connect()  # fresh connection: some snapshot files were just deleted
    report_text = generate_change_report(obs_changes, con)
    has_changes = bool(report_text)
    has_tr_notice = False

    if has_changes:
        REPORT_PATH.write_text(report_text, encoding="utf-8")
        print(f"\nChanges detected -- wrote {REPORT_PATH}")
        print("\n=== Instant Türkiye notices ===")
        notice_errors, has_tr_notice = _publish_instant_notices(obs_changes, con)
        errors.extend(notice_errors)
    else:
        REPORT_PATH.unlink(missing_ok=True)
        print("\nNo changes detected. Exiting quietly.")

    print("\n=== Technical changes log (catalogue-level, private) ===")
    has_technical_changes = _log_technical_changes(catalogue_changes, errors)

    print("\n=== Indicator candidates (not yet in the pipeline) ===")
    has_new_candidates = _scan_candidates(catalogue_changes, errors)

    _write_github_outputs(
        has_changes=has_changes,
        has_tr_notice=has_tr_notice,
        has_technical_changes=has_technical_changes,
        has_new_candidates=has_new_candidates,
    )

    if errors:
        print(f"\n{len(errors)} error(s) during this run:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
