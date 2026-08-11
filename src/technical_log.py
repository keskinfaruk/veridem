"""
Append-only, private log of catalogue-level ("technical") changes --
NEW_DATAFLOW, DATAFLOW_WITHDRAWN, STRUCTURAL.

These are distinct from demographic data changes (NEW_PERIOD/REVISED/
WITHDRAWN/NEW_SERIES on an actual indicator value, see report.py /
diff.py): a technical change means TUIK's own service catalogue changed
shape -- a dataflow appeared, disappeared, or its DSD version bumped -- not
that a demographic figure moved. Confirmed real, not hypothetical: TUIK
withdrew 23 marriage/divorce dataflows between two inventory snapshots on
2026-08-11 (see dataflow_inventory.py).

Never posted anywhere public, and deliberately never goes through a PR --
there's nothing here for a human to approve before it's safe to keep
permanently, unlike an actual data change. daily.yml commits this file
directly to main after any run with catalogue changes, same direct-push
pattern already used for baseline_notice.py's queue-progress bookkeeping.
Kept only for the project owner's own reference -- e.g. noticing TUIK
quietly discontinued its divorce statistics is useful raw material for a
blog post, even though the withdrawal itself is never worth a public post
the way a new TFR figure is.
"""

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from report import CLASS_HEADINGS, TECHNICAL_CLASS_ORDER, _inventory_block

LOG_PATH = Path(__file__).resolve().parent.parent / "data" / "technical_changes_log.md"


def render_entry(inventory_changes: pd.DataFrame, when: datetime | None = None) -> str:
    """One dated Markdown section covering one run's catalogue-level
    changes, grouped by class -- same per-row rendering report.py uses for
    these classes (_inventory_block()), just dated and meant to accumulate
    here run over run instead of going into a one-shot PR body. Returns ""
    if there's nothing to log."""
    if inventory_changes.empty:
        return ""
    when = when or datetime.now(timezone.utc)

    lines = [f"## {when.strftime('%Y-%m-%d')}", ""]
    for change_class in TECHNICAL_CLASS_ORDER:
        rows = inventory_changes[inventory_changes["change_class"] == change_class]
        if rows.empty:
            continue
        lines.append(f"### {CLASS_HEADINGS[change_class]} ({len(rows)})")
        lines.append("")
        for _, row in rows.iterrows():
            lines.append(f"- {_inventory_block(row)}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def append_entry(inventory_changes: pd.DataFrame, when: datetime | None = None) -> bool:
    """Append one run's catalogue changes to LOG_PATH, newest entry last
    (chronological, matching how you'd actually read back through it).
    Returns whether anything was written -- False (no-op) when
    inventory_changes is empty, same "no event, no action" convention as
    everything else in the daily run."""
    entry = render_entry(inventory_changes, when)
    if not entry:
        return False

    if LOG_PATH.exists():
        prior = LOG_PATH.read_text(encoding="utf-8")
        LOG_PATH.write_text(prior.rstrip("\n") + "\n\n" + entry, encoding="utf-8")
    else:
        LOG_PATH.write_text(entry, encoding="utf-8")
    return True
