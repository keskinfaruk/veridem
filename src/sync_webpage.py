"""
Syncs veridem's public output to the `webpage` repo (faruk.page).

Two files are updated in a checked-out copy of that repo:
    veridem/changes.xml        a byte-for-byte copy of feed.py's output, the
                               chronological record of what was posted
    veridem/indicators.html    the card set: one entry per watched series,
                               always showing its current value

The two behave differently on purpose. The feed and the Bluesky account are
append-only history; the page is current state, rebuilt whole on every run so
a card is replaced in place rather than a second one appended.

veridem owns the block between its markers inside <main>, plus that page's
title and description. The site chrome around it is hand-maintained and must
survive untouched, which is why the page was moved with `git mv` rather than
generated from scratch.

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

PAGE_PATH = "veridem/indicators.html"
LEGACY_PAGE = "veridem/index.html"
PROJECTS_PAGE = "projects.html"
OLD_LINK = "./veridem/index.html"
NEW_LINK = "./veridem/indicators.html"

# /veridem/ points at the project's own site. Static hosting cannot issue a
# 301, so this is a meta refresh with a canonical link for search engines and
# a visible fallback for anyone whose browser blocks the refresh.
REDIRECT_PAGE = "veridem/index.html"
REDIRECT_TARGET = "https://veridem.faruk.page/"
REDIRECT_HTML = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>veridem</title>
<link rel="canonical" href="{REDIRECT_TARGET}">
<meta http-equiv="refresh" content="0; url={REDIRECT_TARGET}">
<meta name="robots" content="noindex">
</head>
<body>
<p>Redirecting to <a href="{REDIRECT_TARGET}">veridem.faruk.page</a>.</p>
</body>
</html>
"""

PAGE_TITLE = "Indicators &mdash; Faruk Keskin"
PAGE_DESCRIPTION = (
    "Current values for some demographic indicators veridem watches for Türkiye, "
    "from TurkStat and Eurostat."
)

REGION_RE = re.compile(re.escape(REGION_START) + r".*?" + re.escape(REGION_END), re.DOTALL)
# Before the region grew to cover the heading, veridem owned only the card list.
# Falling back to the whole <main> body migrates such a page in one pass.
MAIN_RE = re.compile(r"(<main class=\"wrap\">)(.*?)(</main>)", re.DOTALL)
TITLE_RE = re.compile(r"<title>.*?</title>", re.DOTALL)
DESCRIPTION_RE = re.compile(r'(<meta name="description" content=")(.*?)(">)', re.DOTALL)


def _run(cmd: list[str], cwd: Path) -> None:
    subprocess.run(cmd, cwd=cwd, check=True)


def migrate_page(webpage_repo: Path) -> bool:
    """Move the page to its own filename, once, so /veridem/ is free for a
    landing page later. Uses `git mv` so the hand-written header, nav and
    footer come across untouched and git records it as a rename."""
    if (webpage_repo / PAGE_PATH).exists() or not (webpage_repo / LEGACY_PAGE).exists():
        return False
    _run(["git", "mv", LEGACY_PAGE, PAGE_PATH], webpage_repo)
    print(f"Moved {LEGACY_PAGE} -> {PAGE_PATH}")
    return True


def repoint_links(webpage_repo: Path) -> bool:
    """Point the Projects page at the page's new filename. Idempotent: a repo
    already carrying the new link is left alone."""
    path = webpage_repo / PROJECTS_PAGE
    if not path.exists():
        return False
    page = path.read_text(encoding="utf-8")
    if OLD_LINK not in page:
        return False
    path.write_text(page.replace(OLD_LINK, NEW_LINK), encoding="utf-8")
    print(f"Repointed {PROJECTS_PAGE} at {NEW_LINK}")
    return True


def ensure_redirect(webpage_repo: Path) -> bool:
    """Put the redirect at /veridem/ if nothing is there. Written once and
    never rewritten, so a hand-edited landing page replacing it is safe.

    Must run after migrate_page(), which frees that filename."""
    path = webpage_repo / REDIRECT_PAGE
    if path.exists():
        return False
    path.write_text(REDIRECT_HTML, encoding="utf-8")
    print(f"Wrote {REDIRECT_PAGE} redirecting to {REDIRECT_TARGET}")
    return True


def update_page(page_path: Path, region: str) -> bool:
    """Swap the managed block for `region`, and keep the page title and
    description in step with it. Returns False when the page has neither the
    markers nor a <main class="wrap">, which means it changed shape and needs a
    look rather than a silent no-op."""
    page = page_path.read_text(encoding="utf-8")

    if REGION_RE.search(page):
        updated = REGION_RE.sub(lambda _: region, page, count=1)
    elif MAIN_RE.search(page):
        updated = MAIN_RE.sub(lambda m: f"{m.group(1)}\n{region}\n{m.group(3)}", page, count=1)
    else:
        return False

    updated = TITLE_RE.sub(f"<title>{PAGE_TITLE}</title>", updated, count=1)
    updated = DESCRIPTION_RE.sub(
        lambda m: m.group(1) + PAGE_DESCRIPTION + m.group(3), updated, count=1
    )

    page_path.write_text(updated, encoding="utf-8")
    return True


def sync(webpage_repo: Path) -> bool:
    """Returns True if there was something to commit, False if the sync left
    the webpage working tree unchanged."""
    (webpage_repo / "veridem").mkdir(exist_ok=True)
    migrate_page(webpage_repo)
    ensure_redirect(webpage_repo)
    repoint_links(webpage_repo)

    if FEED_PATH.exists():
        shutil.copy(FEED_PATH, webpage_repo / "veridem" / "changes.xml")

    cards = build_cards(connect())
    page_path = webpage_repo / PAGE_PATH
    if not page_path.exists():
        raise RuntimeError(f"{page_path} does not exist and no page was there to move")
    if not update_page(page_path, render_region(cards)):
        raise RuntimeError(
            f"{page_path} has neither the veridem markers nor a <main class=\"wrap\"> "
            "-- refusing to guess where the card block belongs"
        )
    print(f"Rendered {len(cards)} card(s) into {PAGE_PATH}")

    _run(["git", "add", "-A", "veridem", PROJECTS_PAGE], webpage_repo)
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
