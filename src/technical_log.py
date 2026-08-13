"""
Append-only, private log of catalogue-level ("technical") changes -- two
independent catalogues, TUIK's SDMX dataflow list (NEW_DATAFLOW,
DATAFLOW_WITHDRAWN, STRUCTURAL -- dataflow_inventory.py) and tuik_press's
theme list (PRESS_THEME_NEW, PRESS_THEME_WITHDRAWN --
press_dataflow_inventory.py), logged into the same dated section.

Distinct from demographic data changes (NEW_PERIOD/REVISED/WITHDRAWN/
NEW_SERIES on an actual indicator value, see report.py/diff.py): a
technical change means one of TUIK's service catalogues changed shape, not
that a demographic figure moved. Never posted anywhere public and never
goes through a PR -- daily.yml commits this file straight to main (same
pattern as baseline_notice.py's queue-progress bookkeeping). Kept only as
reference material for the project owner's own blog writing.
"""

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from report import (
    CLASS_HEADINGS,
    PRESS_TECHNICAL_CLASS_ORDER,
    TECHNICAL_CLASS_ORDER,
    _inventory_block,
    _press_inventory_block,
)

LOG_PATH = Path(__file__).resolve().parent.parent / "data" / "technical_changes_log.md"


def _class_rows(changes: pd.DataFrame, change_class: str) -> pd.DataFrame:
    """changes["change_class"] == ..., safe for an empty frame that may not
    even have a change_class column (e.g. a bare, no-op pd.DataFrame())."""
    if changes.empty:
        return changes
    return changes[changes["change_class"] == change_class]


def render_entry(
    inventory_changes: pd.DataFrame,
    when: datetime | None = None,
    press_changes: pd.DataFrame | None = None,
) -> str:
    """One dated Markdown section covering one run's catalogue-level
    changes across both catalogues, grouped by class -- same per-row
    rendering report.py uses for these classes (_inventory_block() /
    _press_inventory_block()), just dated and meant to accumulate here run
    over run instead of going into a one-shot PR body. Returns "" if
    there's nothing to log in either catalogue."""
    press_changes = press_changes if press_changes is not None else pd.DataFrame()
    if inventory_changes.empty and press_changes.empty:
        return ""
    when = when or datetime.now(timezone.utc)

    lines = [f"## {when.strftime('%Y-%m-%d')}", ""]
    for change_class in TECHNICAL_CLASS_ORDER:
        rows = _class_rows(inventory_changes, change_class)
        if rows.empty:
            continue
        lines.append(f"### {CLASS_HEADINGS[change_class]} ({len(rows)})")
        lines.append("")
        for _, row in rows.iterrows():
            lines.append(f"- {_inventory_block(row)}")
        lines.append("")
    for change_class in PRESS_TECHNICAL_CLASS_ORDER:
        rows = _class_rows(press_changes, change_class)
        if rows.empty:
            continue
        lines.append(f"### {CLASS_HEADINGS[change_class]} ({len(rows)})")
        lines.append("")
        for _, row in rows.iterrows():
            lines.append(f"- {_press_inventory_block(row)}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def append_entry(
    inventory_changes: pd.DataFrame,
    when: datetime | None = None,
    press_changes: pd.DataFrame | None = None,
) -> bool:
    """Append one run's catalogue changes (both catalogues) to LOG_PATH,
    newest entry last (chronological, matching how you'd actually read back
    through it). Returns whether anything was written -- False (no-op) when
    both inputs are empty, same "no event, no action" convention as
    everything else in the daily run."""
    entry = render_entry(inventory_changes, when, press_changes)
    if not entry:
        return False

    if LOG_PATH.exists():
        prior = LOG_PATH.read_text(encoding="utf-8")
        LOG_PATH.write_text(prior.rstrip("\n") + "\n\n" + entry, encoding="utf-8")
    else:
        LOG_PATH.write_text(entry, encoding="utf-8")
    return True
