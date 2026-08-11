"""
Append-only, private log of catalogue-level ("technical") changes --
NEW_DATAFLOW, DATAFLOW_WITHDRAWN, STRUCTURAL.

Distinct from demographic data changes (NEW_PERIOD/REVISED/WITHDRAWN/
NEW_SERIES on an actual indicator value, see report.py/diff.py): a
technical change means TUIK's own service catalogue changed shape, not
that a demographic figure moved. Never posted anywhere public and never
goes through a PR -- daily.yml commits this file straight to main (same
pattern as baseline_notice.py's queue-progress bookkeeping). Kept only as
reference material for the project owner's own blog writing. See
ROADMAP_LOG.md for the investigation that led to this.
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
