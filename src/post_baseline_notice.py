"""
Posts the next due entry from the one-time baseline-notice queue (see
baseline_notice.py), at most one per UTC calendar day, so the feed and
Bluesky account do not show many entries on the same day, which would look
like an unannounced data dump rather than the honest one-time seed it is.

Runs once a day on daily.yml's schedule: a no-op on days it is not yet due,
and once the queue is drained. Idempotent, since the first run of a day
advances the queue and updates `posted_at`.

Before checking what is due, folds in any series newly present in the bank
that the queue does not have yet, so a newly-added indicator joins
automatically.

Takes the checked-out `webpage` repo path as its one argument, to seed this
repo's never-committed changes.xml from the persisted copy at
webpage/veridem/changes.xml before appending. Without that, append_notices()
would see no existing entries in a fresh checkout and a later
sync_webpage.py call would overwrite the real public feed history with just
this run's entry. The sync itself is still sync_webpage.py's job.
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import bluesky_client
from baseline_notice import QUEUE_PATH, append_new_series, interleave_by_domain
from feed import append_notices, seed_from_published
from schema import connect


def _load_queue() -> list[dict]:
    if not QUEUE_PATH.exists():
        raise FileNotFoundError(
            f"{QUEUE_PATH} doesn't exist -- run `python baseline_notice.py` once "
            "to build it before using this script."
        )
    return json.loads(QUEUE_PATH.read_text(encoding="utf-8"))


def _save_queue(queue: list[dict]) -> None:
    QUEUE_PATH.write_text(json.dumps(queue, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _due(queue: list[dict], now: datetime) -> bool:
    """
    Due once the UTC calendar date has advanced past the last post's. A date
    comparison rather than an exact 24h gap, so a run landing a few minutes
    earlier than the previous day's cannot push a post out to the following day.
    """
    posted_ats = [e["posted_at"] for e in queue if e["posted_at"]]
    if not posted_ats:
        return True
    last = max(datetime.fromisoformat(ts) for ts in posted_ats)
    return now.date() > last.date()


def _append_new_candidates(queue: list[dict]) -> tuple[list[dict], int]:
    """Fold in any series newly present in the bank that isn't in the
    queue yet. Purely additive; a no-op when nothing's new."""
    con = connect()
    new_entries = append_new_series(queue, con)
    if not new_entries:
        return queue, 0
    return interleave_by_domain(queue + new_entries), len(new_entries)


def post_next(webpage_repo: Path, now: datetime | None = None) -> tuple[bool, int]:
    """Post the next unposted queue entry if one exists and enough time has
    passed since the last one. Returns (posted, newly_queued) -- posted is
    True if something was posted this run; newly_queued is how many
    not-previously-queued series got folded in (0 most days)."""
    now = now or datetime.now(timezone.utc)
    queue = _load_queue()
    queue, added = _append_new_candidates(queue)
    if added:
        _save_queue(queue)
        print(f"Queued {added} new baseline notice(s) for series not previously in the queue.")

    pending = [e for e in queue if not e["posted"]]
    if not pending:
        print("Baseline notice queue is fully drained -- nothing to post.")
        return False, added
    if not _due(queue, now):
        print("Not due yet -- already posted a baseline notice today (UTC).")
        return False, added

    entry = pending[0]
    print(f"Posting: {entry['title']}")

    seed_from_published(webpage_repo)
    append_notices([entry])

    handle, app_password = bluesky_client.get_credentials()
    session = bluesky_client.create_session(handle, app_password)
    result = bluesky_client.post(session, entry["bluesky_text"])

    entry["posted"] = True
    entry["posted_at"] = now.isoformat()
    entry["bluesky_uri"] = result.get("uri")
    _save_queue(queue)
    print(f"Posted. {len(pending) - 1} remaining in the queue.")
    return True, added


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: post_baseline_notice.py <path to checked-out webpage repo>", file=sys.stderr)
        return 1
    webpage_repo = Path(sys.argv[1])

    try:
        posted, added = post_next(webpage_repo)
    except Exception as e:  # noqa: BLE001 -- must not take daily.yml's other steps down with it
        print(f"ERROR: baseline notice posting failed: {e}", file=sys.stderr)
        return 1

    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as f:
            f.write(f"posted_baseline={'true' if posted else 'false'}\n")
            f.write(f"queue_updated={'true' if (posted or added) else 'false'}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
