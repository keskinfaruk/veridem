"""
One-time backfill: "current on record" notices for every Türkiye series
already in the data bank, seeding the feed and Bluesky account with data
already fetched rather than waiting for the next real release.

This is a deliberate exception to instant_notice.py's rule that NEW_SERIES
stays out of NOTICE_CLASSES because a series debuting with its whole history
is backfill, not a fresh publication. These *are* that backfill. What keeps
posting them honest: every notice leads with "Current on record", and
instant_notice.headline()/bluesky_text() are never called from here, since
their wording reads like a real release notice and would mislead for a value
that has sat in the bank for up to a year.

The trend facts after that prefix are real and independently useful, so the
comparison, streak, record and sanity machinery is reused rather than
rewritten thinner.

Not wired into daily_run.py. Only post_baseline_notice.py invokes this,
draining a fixed queue built once by build_queue().
"""

import json
from collections import defaultdict, deque
from pathlib import Path

import pandas as pd

from diff import SERIES_KEY, latest_two_snapshots
from instant_notice import (
    area_source_label,
    compose_post,
    filter_posting_sources,
    prior_comparison_clause,
    recent_extreme_note,
)
from report import (
    _prior_point,
    _recent_series_lines,
    _trend_context_lines,
    format_number,
    indicator_label,
    population_type_label,
    series_history,
)
from schema import connect

BASELINE_PREFIX = "Current on record"

# Committed to git deliberately: the persistent record of which entries have
# gone out and when, rewritten by post_baseline_notice.py on each run. Built
# once by main(), never regenerated automatically, so a re-run can never
# reset already-posted entries back to pending.
QUEUE_PATH = Path(__file__).resolve().parent.parent / "data" / "baseline_notices_queue.json"


def current_tr_rows(con) -> pd.DataFrame:
    """One row per distinct Türkiye series in the bank, each carrying its
    latest snapshot's newest period and value."""
    dataflows = con.execute("SELECT DISTINCT source, dataflow_id FROM observations").df()
    frames = []
    for _, df_row in dataflows.iterrows():
        _, latest_id = latest_two_snapshots(con, df_row["source"], df_row["dataflow_id"])
        if latest_id is None:
            continue
        snap = con.execute(
            "SELECT * FROM observations WHERE source=? AND dataflow_id=? AND snapshot_id=? AND ref_area='TR'",
            [df_row["source"], df_row["dataflow_id"], latest_id],
        ).df()
        if snap.empty:
            continue
        latest = snap.loc[snap.groupby(SERIES_KEY)["time_period"].idxmax()].copy()
        latest["new_value"] = latest["obs_value"]
        latest["new_snapshot_id"] = latest["snapshot_id"]
        frames.append(latest)
    if not frames:
        return pd.DataFrame(columns=SERIES_KEY + ["time_period", "new_value", "new_snapshot_id"])
    return pd.concat(frames, ignore_index=True).sort_values(["indicator", "sex", "source"]).reset_index(drop=True)


def baseline_headline(row: pd.Series, history: pd.Series) -> str:
    """Framed as a baseline restatement, never as something that just
    happened: no "revised from" clause, no wording borrowed from the real
    change-notice template."""
    indicator, period = row["indicator"], row["time_period"]
    text = (
        f"{BASELINE_PREFIX} -- {indicator_label(indicator)} ({area_source_label(row)}) "
        f"{period}: {format_number(row['new_value'], indicator)}"
    )
    prior_period, prior_value = _prior_point(history, period)
    if prior_period is not None:
        text += ", " + prior_comparison_clause(row["new_value"], prior_period, prior_value, indicator)
    return text


def baseline_bluesky_text(row: pd.Series, con, url: str | None = None) -> str:
    history = series_history(con, row, row["new_snapshot_id"])
    return compose_post(baseline_headline(row, history), row, history, url, include_sanity=True)


def baseline_feed_content(row: pd.Series, history: pd.Series) -> str:
    """Full feed-entry body: the same shape as report._value_change_block(),
    headed as a baseline restatement with an explicit note, so nobody reading
    changes.xml mistakes it for a real release.

    A strict superset of baseline_bluesky_text()'s content. No sanity-check
    section: the compact warning flag already covers anything genuinely off,
    and a wall of "[ok]" lines is noise for a public feed reader.
    """
    value, indicator, period = row["new_value"], row["indicator"], row["time_period"]
    lines = [
        f"{BASELINE_PREFIX}: {indicator_label(indicator)}, {area_source_label(row)}, {period}",
        "",
        f"  Population       {population_type_label(row['sex'])}",
        f"  Value            {format_number(value, indicator)} ({period})",
    ]

    prior_period, prior_value = _prior_point(history, period)
    if prior_period is not None:
        delta = value - prior_value
        pct = f" ({delta / prior_value * 100:+.1f}%)" if prior_value else ""
        lines.append(f"  Previous         {format_number(prior_value, indicator)} ({prior_period})")
        lines.append(f"  Change           {format_number(delta, indicator, signed=True)}{pct}")

    lines += [
        "",
        "  Note             Not a new release -- this is the value already on",
        "                   record in veridem's data bank, posted as part of a",
        "                   one-time backfill so this feed has content to show",
        "                   before the next real change occurs.",
    ]

    if len(history) >= 1:
        lines += ["", "  Trend context"] + _trend_context_lines(indicator, history)
        recent_note = recent_extreme_note(history, period, value)
        if recent_note:
            lines.append(f"    Recent         {recent_note}")
        lines += ["", "  Recent series"] + _recent_series_lines(indicator, history, period, "current")

    return "\n".join(lines)


def build_queue(con, base_url: str | None = None, only_latest_year: bool = True) -> list[dict]:
    """One baseline-notice dict per current Türkiye series, in post order.

    Shaped to match instant_notice.build_notices() so feed.append_notices()
    works unmodified, plus the sex/age/source/posted fields this module needs
    for identity tracking. change_class is the literal "BASELINE", never one
    of diff.py's real classes, so a feed entry ID can never collide with a
    genuine change's.

    `only_latest_year` drops every series not yet at the bank's most recent
    year, computed live rather than hardcoded: different indicators sit at
    different "current" years depending on each source's publication lag, and
    posting a mix of years in one batch would be confusing.

    filter_posting_sources() is applied first, the same eligibility gate the
    real-time path uses. Without it, a series added purely for archive or
    dashboard purposes would still surface as a public baseline notice.
    """
    rows = filter_posting_sources(current_tr_rows(con))
    if only_latest_year and not rows.empty:
        latest_year = rows["time_period"].max()
        dropped = rows[rows["time_period"] != latest_year]
        if not dropped.empty:
            print(
                f"Dropping {len(dropped)} series not yet at {latest_year} "
                f"(stuck at {sorted(dropped['time_period'].unique())}): "
                + ", ".join(sorted(dropped["indicator"].unique()))
            )
        rows = rows[rows["time_period"] == latest_year]

    queue = []
    for _, row in rows.iterrows():
        history = series_history(con, row, row["new_snapshot_id"])
        queue.append(
            {
                "title": baseline_headline(row, history),
                "bluesky_text": baseline_bluesky_text(row, con, url=base_url),
                "feed_content": baseline_feed_content(row, history),
                "indicator": row["indicator"],
                "ref_area": row["ref_area"],
                "sex": row["sex"],
                "age": row["age"],
                "source": row["source"],
                "time_period": row["time_period"],
                "change_class": "BASELINE",
                "snapshot_id": row["new_snapshot_id"],
                "posted": False,
                "posted_at": None,
                "bluesky_uri": None,
            }
        )
    return queue


# Post-order grouping only. build_queue()'s own order is alphabetical by
# indicator within source, which front-loads a long run of near-identical
# mortality sex-breakdown posts. Not a concept the data carries, and never
# written back to the data bank.
DOMAIN_ORDER = ("Fertility", "Mortality", "Migration", "Population", "Nuptiality")
_DOMAIN = {
    ind: domain
    for domain, indicators in {
        "Fertility": ("TFR", "ASFR", "ADOLESCENT_FERTILITY_RATE", "MEAN_AGE_CHILDBEARING",
                      "MEAN_AGE_FIRST_BIRTH", "TOTAL_BIRTHS", "CBR"),
        "Mortality": ("CDR", "INFANT_DEATHS", "INFANT_MORTALITY_RATE", "NEONATAL_DEATHS",
                      "NEONATAL_MORTALITY_RATE", "POST_NEONATAL_DEATHS", "POST_NEONATAL_MORTALITY_RATE",
                      "TOTAL_DEATHS", "UNDER5_DEATHS", "UNDER5_MORTALITY_RATE", "HEALTHY_LIFE_YEARS"),
        "Migration": ("INTERNAL_MIGRATION_VOLUME", "INTERNAL_MIGRATION_RATE", "IMMIGRANTS", "EMIGRANTS"),
        "Population": ("NATURAL_GROWTH_RATE", "POP_GROWTH_RATE", "POP_JAN1", "TOTAL_POPULATION"),
        "Nuptiality": ("MEAN_AGE_FIRST_MARRIAGE", "NUMBER_OF_MARRIAGES", "CRUDE_MARRIAGE_RATE",
                       "NUMBER_OF_DIVORCES", "CRUDE_DIVORCE_RATE"),
    }.items()
    for ind in indicators
}


def interleave_by_domain(queue: list[dict], order: tuple[str, ...] = DOMAIN_ORDER) -> list[dict]:
    """Round-robin the queue across domains, one entry per domain per round,
    instead of posting a long same-domain block. Preserves each domain's own
    relative order.

    Safe on a partly-posted queue: post_next() only ever reads the first
    unposted entry, so reordering cannot disturb what has already gone out.
    """
    buckets: dict[str, deque] = defaultdict(deque)
    for entry in queue:
        buckets[_DOMAIN.get(entry["indicator"], "Other")].append(entry)
    all_domains = list(order) + [d for d in buckets if d not in order]

    result = []
    while any(buckets[d] for d in all_domains):
        for d in all_domains:
            if buckets[d]:
                result.append(buckets[d].popleft())
    return result


def _entry_key(entry: dict) -> tuple:
    """Identity of one queue entry. Entries queued before `age` was added
    default to '_T'."""
    return (entry["source"], entry["indicator"], entry["sex"], entry.get("age", "_T"), entry["ref_area"])


def append_new_series(
    existing_queue: list[dict], con, base_url: str | None = None, only_latest_year: bool = True
) -> list[dict]:
    """Candidate baseline notices for series not already in `existing_queue`.
    Purely additive: the caller does `existing_queue + append_new_series(...)`
    and writes the result back, so posted/posted_at state is never disturbed.
    """
    candidates = build_queue(con, base_url=base_url, only_latest_year=only_latest_year)
    seen = {_entry_key(e) for e in existing_queue}
    return [c for c in candidates if _entry_key(c) not in seen]


def main() -> int:
    """Build the queue once and write it. Refuses to overwrite an existing
    queue file: delete it deliberately first, so a stray re-run can never
    wipe out which entries have already been posted."""
    if QUEUE_PATH.exists():
        print(f"{QUEUE_PATH} already exists -- delete it first if you really want to rebuild.")
        return 1
    queue = build_queue(connect())
    QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
    QUEUE_PATH.write_text(json.dumps(queue, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {len(queue)} baseline notice(s) to {QUEUE_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
