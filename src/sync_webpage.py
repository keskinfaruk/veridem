"""
Syncs veridem's public output to the `webpage` repo (faruk.page).

Two files are updated in a checked-out copy of that repo:
    veridem/changes.xml   a byte-for-byte copy of feed.py's output, the
                          chronological record of what was posted
    veridem/index.html    the card set: one entry per watched series, always
                          showing its current value

The two behave differently on purpose. The feed and the Bluesky account are
append-only history; the page is current state, rebuilt whole on every run so
a card is replaced in place rather than a second one appended.

veridem owns the block between its markers inside <main>, plus that page's
title and description. The site chrome around it is hand-maintained and must
survive untouched.

Direct commit, no PR: this output is facts only and does not need the review
gate data changes do.
"""

import re
import shutil
import subprocess
import sys
from pathlib import Path

from cards import REGION_END, REGION_START, build_cards, render_region
from feed import FEED_PATH
from schema import connect

PAGE_TITLE = "Indicators &mdash; Faruk Keskin"
PAGE_DESCRIPTION = (
    "Current values for every demographic indicator veridem watches for Türkiye, "
    "from TurkStat and Eurostat."
)

REGION_RE = re.compile(re.escape(REGION_START) + r".*?" + re.escape(REGION_END), re.DOTALL)
# Before the region grew to cover the heading, veridem owned only the card list.
# Falling back to the whole <main> body migrates such a page in one pass.
MAIN_RE = re.compile(r"(<main class=\"wrap\">)(.*?)(</main>)", re.DOTALL)
TITLE_RE = re.compile(r"<title>.*?</title>", re.DOTALL)
DESCRIPTION_RE = re.compile(r'(<meta name="description" content=")(.*?)(">)', re.DOTALL)


def update_index_html(index_path: Path, region: str) -> bool:
    """Swap the managed block for `region`, and keep the page title and
    description in step with it. Returns False when the page has neither the
    markers nor a <main class="wrap">, which means it changed shape and needs a
    look rather than a silent no-op."""
    page = index_path.read_text(encoding="utf-8")

    if REGION_RE.search(page):
        updated = REGION_RE.sub(lambda _: region, page, count=1)
    elif MAIN_RE.search(page):
        updated = MAIN_RE.sub(lambda m: f"{m.group(1)}\n{region}\n{m.group(3)}", page, count=1)
    else:
        return False

    updated = TITLE_RE.sub(f"<title>{PAGE_TITLE}</title>", updated, count=1)
    updated = DESCRIPTION_RE.sub(lambda m: m.group(1) + PAGE_DESCRIPTION + m.group(3), updated, count=1)

    index_path.write_text(updated, encoding="utf-8")
    return True


def _run(cmd: list[str], cwd: Path) -> None:
    subprocess.run(cmd, cwd=cwd, check=True)


def sync(webpage_repo: Path) -> bool:
    """Returns True if there was something to commit, False if the sync left
    the webpage working tree unchanged."""
    target_dir = webpage_repo / "veridem"
    target_dir.mkdir(exist_ok=True)

    if FEED_PATH.exists():
        shutil.copy(FEED_PATH, target_dir / "changes.xml")

    cards = build_cards(connect())
    if not update_index_html(target_dir / "index.html", render_region(cards)):
        raise RuntimeError(
            f"{target_dir / 'index.html'} has neither the veridem markers nor a "
            "<main class=\"wrap\"> -- refusing to guess where the card block belongs"
        )
    print(f"Rendered {len(cards)} card(s) into veridem/index.html")

    _run(["git", "add", "veridem/changes.xml", "veridem/index.html"], webpage_repo)
    if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=webpage_repo).returncode == 0:
        return False

    _run(["git", "config", "user.name", "veridem-bot"], webpage_repo)
    _run(["git", "config", "user.email", "actions@users.noreply.github.com"], webpage_repo)
    _run(["git", "commit", "-m", "Update veridem indicators and feed"], webpage_repo)
    _run(["git", "push"], webpage_repo)
    return True


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: sync_webpage.py <path to checked-out webpage repo>", file=sys.stderr)
        return 1
    changed = sync(Path(sys.argv[1]))
    print("Synced, pushed." if changed else "No change -- nothing to push.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
