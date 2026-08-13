"""
One-time backfill: "current on record" notices for every Turkiye series
already sitting in the data bank, seeding the instant-notice feed /
@veridemdata.bsky.social / faruk.page/veridem/ with data that's already
been fetched, rather than waiting for the next real TurkStat/Eurostat
release to trickle these out naturally.

This is a deliberate, one-time exception to instant_notice.py's own rule
that NEW_SERIES stays out of NOTICE_CLASSES because a series debuting with
its whole fetched history isn't a fresh publication, it's backfill noise.
These *are* that backfill. What makes posting them anyway honest rather
than a rule violation: every notice this module builds leads with
"Current on record", never with language that implies a fresh release,
and headline()/bluesky_text() in instant_notice.py are never called from
here for that reason -- their output reads exactly like a real release
notice, which would be misleading for a value that's been sitting in the
bank for up to a year.

The trend facts folded in after that prefix (direction, streak, 10-year
high/low, sanity flags) are real and independently useful, so this reuses
instant_notice.py's own comparison/streak/record/sanity-flag machinery
rather than writing a thinner version from scratch.

Deliberately NOT wired into daily_run.py's normal change-detection path --
this module is only ever invoked by post_baseline_notice.py, which drains
a fixed queue built once by build_queue() below.

Reuses instant_notice._area_source_label() directly (not duplicated here),
including its age-band clause -- so a real ASFR change notice through the
normal instant-notice path gets the same disambiguation these baseline
notices needed.
"""

import json
from pathlib import Path

import pandas as pd

from diff import SERIES_KEY, latest_two_snapshots
from schema import connect
from instant_notice import (
    BLUESKY_MAX_GRAPHEMES,
    _area_source_label,
    _format_number,
    _indicator_label,
    _prior_comparison_clause,
    _prior_point,
    _recent_extreme_note,
    _sanity_flags,
)
from report import (
    _recent_series_lines,
    _trend_context_lines,
    direction,
    format_number,
    population_type_label,
    record,
    series_history,
)

BASELINE_PREFIX = "Current on record"

# Committed to git deliberately -- this is the persistent record of which
# entries have already gone out and when, read and rewritten by
# post_baseline_notice.py on every scheduled run. Built once by main()
# below; never regenerated automatically afterwards, so a re-run of this
# module can never silently reset already-posted entries back to pending.
QUEUE_PATH = Path(__file__).resolve().parent.parent / "data" / "baseline_notices_queue.json"


def current_tr_rows(con) -> pd.DataFrame:
    """One row per distinct Turkiye series currently in the data bank, each
    carrying its own latest snapshot's newest time_period/value -- the same
    "latest snapshot per dataflow" idiom daily_run.py's diffing already
    uses (latest_two_snapshots()), just reading the newest snapshot
    unconditionally instead of diffing it against the one before.
    """
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
        idx = snap.groupby(SERIES_KEY)["time_period"].idxmax()
        latest = snap.loc[idx].copy()
        latest["new_value"] = latest["obs_value"]
        latest["new_snapshot_id"] = latest["snapshot_id"]
        frames.append(latest)
    if not frames:
        return pd.DataFrame(columns=SERIES_KEY + ["time_period", "new_value", "new_snapshot_id"])
    return pd.concat(frames, ignore_index=True).sort_values(["indicator", "sex", "source"]).reset_index(drop=True)


def baseline_headline(row: pd.Series, history: pd.Series) -> str:
    """Like instant_notice._value_headline(), but always framed as a
    baseline restatement, never as something that just happened -- no
    "revised from" clause (these were never actually revised today), no
    wording borrowed from the real change-notice template.
    """
    area_source = _area_source_label(row)
    label = _indicator_label(row["indicator"])
    value = row["new_value"]
    period = row["time_period"]
    text = f"{BASELINE_PREFIX} -- {label} ({area_source}) {period}: {_format_number(value, row['indicator'])}"

    prior_period, prior_value = _prior_point(history, period)
    if prior_period is not None:
        text += ", " + _prior_comparison_clause(value, prior_period, prior_value, row["indicator"])
    return text


def baseline_bluesky_text(row: pd.Series, con, url: str | None = None) -> str:
    """Same tail (direction/streak, record, 10-yr extreme, sanity flags) as
    instant_notice.bluesky_text() -- deliberately duplicated rather than
    refactored out of that module, just built on baseline_headline()
    instead of headline().
    """
    history = series_history(con, row, row["new_snapshot_id"])
    text = baseline_headline(row, history) + "."
    url_budget = len(url) + 1 if url else 0

    def try_append(extra: str) -> None:
        nonlocal text
        if len(text) + len(extra) <= BLUESKY_MAX_GRAPHEMES - url_budget:
            text += extra

    is_latest = len(history) > 0 and row["time_period"] == history.index[-1]
    if len(history) > 1 and is_latest:
        try_append(f" {direction(history)}.")
        record_note = record(history)
        if record_note:
            try_append(f" {record_note}.")

    if len(history) > 1:
        recent_note = _recent_extreme_note(history, row["time_period"], row["new_value"])
        if recent_note:
            try_append(f" {recent_note}.")

    for flag in _sanity_flags(row["indicator"], row["new_value"], history, is_latest):
        try_append(f" ⚠ {flag}.")

    if url:
        text = f"{text} {url}"

    if len(text) > BLUESKY_MAX_GRAPHEMES:
        if url:
            budget = BLUESKY_MAX_GRAPHEMES - len(url) - 5
            text = text[: max(budget, 0)].rstrip() + "... " + url
        else:
            text = text[: BLUESKY_MAX_GRAPHEMES - 1].rstrip() + "…"

    return text


def baseline_feed_content(row: pd.Series, history: pd.Series) -> str:
    """Full feed-entry body -- same shape as report.py's _value_change_block()
    (population type, value/previous/change, trend context, recent series)
    but headed as a baseline restatement with an explicit note explaining
    why it exists, so nobody reading changes.xml directly mistakes this for
    a real TurkStat/Eurostat release.

    Deliberately kept as a strict superset of baseline_bluesky_text()'s
    content, not a differently-formatted rendering of a subset of it: it
    also includes the Previous/Change lines and the 10-year-high/low note.

    No "Sanity checks" section -- baseline_bluesky_text() already surfaces
    a compact "⚠" flag via _sanity_flags() when something's actually off;
    a wall of "[ok] within plausible range" lines for everything else is
    noise for a public feed reader, not signal. CHANGE_REPORT.md keeps the
    full detail; the public feed doesn't.
    """
    value = row["new_value"]
    indicator = row["indicator"]
    period = row["time_period"]
    lines = [
        f"{BASELINE_PREFIX}: {_indicator_label(indicator)}, {_area_source_label(row)}, {period}",
        "",
        f"  Population       {population_type_label(row['sex'])}",
        f"  Value            {_format_number(value, indicator)} ({period})",
    ]

    prior_period, prior_value = _prior_point(history, period)
    if prior_period is not None:
        delta = value - prior_value
        pct = f" ({delta / prior_value * 100:+.1f}%)" if prior_value else ""
        lines.append(f"  Previous         {_format_number(prior_value, indicator)} ({prior_period})")
        lines.append(f"  Change           {format_number(delta, indicator, signed=True)}{pct}")

    lines.append("")
    lines.append("  Note             Not a new release -- this is the value already on")
    lines.append("                   record in veridem's data bank, posted as part of a")
    lines.append("                   one-time backfill so this feed has content to show")
    lines.append("                   before the next real change occurs.")

    if len(history) >= 1:
        lines.append("")
        lines.append("  Trend context")
        lines.extend(_trend_context_lines(indicator, history))
        recent_note = _recent_extreme_note(history, period, value)
        if recent_note:
            lines.append(f"    Recent         {recent_note}")
        lines.append("")
        lines.append("  Recent series")
        lines.extend(_recent_series_lines(indicator, history, period, "current"))

    return "\n".join(lines)


def build_queue(con, base_url: str | None = None, only_latest_year: bool = True) -> list[dict]:
    """One baseline-notice dict per current Turkiye series, in the order
    they'll be posted. Same dict shape as instant_notice.build_notices()
    (title/bluesky_text/feed_content/indicator/ref_area/time_period/
    change_class/snapshot_id) so feed.append_notices() works unmodified --
    change_class is set to the literal string "BASELINE", never one of
    diff.py's real classes, so a feed entry ID can never collide with (or
    be mistaken for) a genuine change's ID.

    `only_latest_year`: different indicators can be stuck at different
    "current" years depending on each source's own publication lag (e.g.
    TFR/ASFR lag a year behind CBR/CDR at times). Rather than posting a
    mix of "current" years in the same batch, this drops every series not
    yet at the bank's own most recent year -- computed live from the data,
    not hardcoded, so a later rebuild naturally adjusts as more indicators
    catch up.
    """
    rows = current_tr_rows(con)
    if only_latest_year and not rows.empty:
        latest_year = rows["time_period"].max()
        dropped = rows[rows["time_period"] != latest_year]
        if not dropped.empty:
            print(
                f"Dropping {len(dropped)} series not yet at {latest_year} "
                f"(stuck at {sorted(dropped['time_period'].unique())}): "
                + ", ".join(sorted(dropped['indicator'].unique()))
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


# Domain grouping for the post order -- build_queue()'s own order is
# alphabetical by indicator within source, which front-loads a long run of
# near-identical mortality sex-breakdown posts. Not a concept the data
# itself carries (indicator_map.csv has no domain column) -- display/
# ordering-only, scoped to this queue, never written back to the data bank.
_DOMAIN = {}
for _ind in ("TFR", "ADOLESCENT_FERTILITY_RATE", "MEAN_AGE_CHILDBEARING", "MEAN_AGE_FIRST_BIRTH", "TOTAL_BIRTHS", "CBR"):
    _DOMAIN[_ind] = "Fertility"
for _ind in (
    "CDR", "INFANT_DEATHS", "INFANT_MORTALITY_RATE", "NEONATAL_DEATHS", "NEONATAL_MORTALITY_RATE",
    "POST_NEONATAL_DEATHS", "POST_NEONATAL_MORTALITY_RATE", "TOTAL_DEATHS", "UNDER5_DEATHS", "UNDER5_MORTALITY_RATE",
    "HEALTHY_LIFE_YEARS",
):
    _DOMAIN[_ind] = "Mortality"
for _ind in ("INTERNAL_MIGRATION_VOLUME", "INTERNAL_MIGRATION_RATE", "IMMIGRANTS", "EMIGRANTS"):
    _DOMAIN[_ind] = "Migration"
for _ind in ("NATURAL_GROWTH_RATE", "POP_GROWTH_RATE", "POP_JAN1", "TOTAL_POPULATION"):
    _DOMAIN[_ind] = "Population"
for _ind in ("MEAN_AGE_FIRST_MARRIAGE", "NUMBER_OF_MARRIAGES", "CRUDE_MARRIAGE_RATE", "NUMBER_OF_DIVORCES", "CRUDE_DIVORCE_RATE"):
    _DOMAIN[_ind] = "Nuptiality"
DOMAIN_ORDER = ("Fertility", "Mortality", "Migration", "Population", "Nuptiality")


def interleave_by_domain(queue: list[dict], order: tuple[str, ...] = DOMAIN_ORDER) -> list[dict]:
    """Round-robin the queue across domains -- one entry per domain per
    round, cycling `order`, until every domain's own items are exhausted --
    instead of posting a long same-domain (and often same-indicator, just
    split by sex) block before moving to the next. Preserves each domain's
    own existing relative order (build_queue()'s indicator/sex/source sort).

    Safe to run on a queue that's partly posted, not just a fresh one:
    post_next() (post_baseline_notice.py) only ever reads
    `[e for e in queue if not e["posted"]][0]` -- an already-posted entry's
    position in the list doesn't affect anything, so reordering the whole
    queue (posted entries included) can't disturb what's already gone out.
    """
    from collections import defaultdict, deque

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


def _existing_keys(queue: list[dict]) -> set[tuple]:
    """(source, indicator, sex, ref_area) for every entry already in a
    queue -- the identity build_queue() dicts actually carry (they don't
    store dataflow_id/freq/age/other_dims). Sufficient in practice: no
    current Turkiye series shares (source, indicator, sex, ref_area) with
    a different age breakdown."""
    return {(e["source"], e["indicator"], e["sex"], e["ref_area"]) for e in queue}


def append_new_series(
    existing_queue: list[dict], con, base_url: str | None = None, only_latest_year: bool = True
) -> list[dict]:
    """Candidate baseline notices for series NOT already present in
    existing_queue, in build_queue()'s own order/shape -- the append path
    for folding in a newly-added source without disturbing the
    posted/posted_at state of entries already queued. Purely additive:
    existing_queue itself is never modified here, the caller does
    `existing_queue + append_new_series(existing_queue, con)` and writes
    the result back.

    Runs only_latest_year over *every* current Turkiye series, existing and
    new together (build_queue()'s own current_tr_rows() call does this
    naturally) -- correct even when the already-queued entries and the new
    ones are different sources at the same calendar year: different
    `source` values never conflict, and the max-year computation doesn't
    care which source a row came from, just its time_period.
    """
    candidates = build_queue(con, base_url=base_url, only_latest_year=only_latest_year)
    seen = _existing_keys(existing_queue)
    return [c for c in candidates if (c["source"], c["indicator"], c["sex"], c["ref_area"]) not in seen]


def main() -> int:
    """Build the queue once and write it to QUEUE_PATH. Refuses to
    overwrite an existing queue file -- delete it deliberately first if you
    really want to rebuild from scratch, so a stray re-run can never wipe
    out which entries have already been posted."""
    if QUEUE_PATH.exists():
        print(f"{QUEUE_PATH} already exists -- delete it first if you really want to rebuild.")
        return 1
    con = connect()
    queue = build_queue(con)
    QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
    QUEUE_PATH.write_text(json.dumps(queue, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {len(queue)} baseline notice(s) to {QUEUE_PATH}")
    return 0


def append_main() -> int:
    """CLI entry for the append path: read the existing queue, compute new
    candidates via append_new_series(), print them for review, and stop --
    does NOT write QUEUE_PATH itself. The actual append-and-save is a
    separate, explicit step once the printed batch has been reviewed."""
    if not QUEUE_PATH.exists():
        print(f"{QUEUE_PATH} doesn't exist -- run `python baseline_notice.py` first.")
        return 1
    existing = json.loads(QUEUE_PATH.read_text(encoding="utf-8"))
    con = connect()
    new_entries = append_new_series(existing, con)
    print(f"{len(existing)} entries already queued; {len(new_entries)} new candidate(s) found.\n")
    for e in new_entries:
        print(f"[{len(e['bluesky_text'])} graphemes] {e['bluesky_text']}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
