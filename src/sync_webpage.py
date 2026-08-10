"""
Syncs the instant-notice feed to the public `webpage` repo (faruk.page),
since `veridem` itself is private and changes.xml would otherwise never be
reachable by anyone outside the account.

Two things get updated in a checked-out copy of the webpage repo:
    - veridem/changes.xml  -- a straight copy of this repo's own feed.py
      output, byte for byte.
    - veridem/index.html   -- the human-readable list, regenerated from the
      same Atom entries. Static, matching the site's "plain HTML, no build
      step" approach -- not a client-side fetch of the XML.

Direct commit, no PR -- this channel's output is facts-only and doesn't
need the review gate blog posts do.

Only meant to run when daily_run.py actually produced at least one Turkiye
notice this run -- calling it with an unchanged changes.xml is harmless
(git just has nothing to commit) but wasteful.
"""

import html
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from feed import ATOM_NS, FEED_PATH, MAX_ENTRIES

# Bounded by the same limit as the feed itself, so every indicator stays
# accessible on the page rather than dropping off a second, tighter cap.
MAX_LISTED = MAX_ENTRIES
LIST_START = '<ul class="post-list" id="updates-list">'
LIST_END = "</ul>"
EMPTY_NOTE_RE = re.compile(r'<p class="text-muted" id="empty-note">.*?</p>\n?', re.DOTALL)


def _tag(name: str) -> str:
    return f"{{{ATOM_NS}}}{name}"


def _read_entries(feed_path: Path) -> list[dict]:
    """`text` reads <summary> (bluesky_text) rather than <title> (the bare
    headline), so the page shows the same level of detail as the Bluesky
    post -- bluesky_text already contains the title as its own prefix, so
    nothing is lost. The full ASCII report block lives in <content>, not
    read here; see feed.py's _build_entry()."""
    root = ET.parse(feed_path).getroot()
    entries = []
    for entry in root.findall(_tag("entry")):
        entries.append(
            {
                "text": entry.find(_tag("summary")).text,
                "date": (entry.find(_tag("updated")).text or "")[:10],
            }
        )
    return entries


def _render_list_html(entries: list[dict]) -> str:
    items = []
    for e in entries[:MAX_LISTED]:
        items.append(
            f'\t\t<li>\n\t\t\t<time datetime="{e["date"]}">{e["date"]}</time>\n'
            f'\t\t\t<span>{html.escape(e["text"])}</span>\n\t\t</li>'
        )
    marker = (
        "\t\t<!-- Synced automatically from veridem's daily run -- newest first. "
        "See changes.xml for the machine-readable version. -->"
    )
    return "\n".join(items + [marker])


def update_index_html(index_path: Path, entries: list[dict]) -> None:
    html = index_path.read_text(encoding="utf-8")

    start = html.index(LIST_START) + len(LIST_START)
    end = html.index(LIST_END, start)
    html = html[:start] + "\n" + _render_list_html(entries) + "\n\t" + html[end:]

    # The "no updates yet" placeholder only makes sense while the list is
    # genuinely empty -- remove it entirely once there's real content.
    if entries:
        html = EMPTY_NOTE_RE.sub("", html)

    index_path.write_text(html, encoding="utf-8")


def _run(cmd: list[str], cwd: Path) -> None:
    subprocess.run(cmd, cwd=cwd, check=True)


def sync(webpage_repo: Path) -> bool:
    """Returns True if there was something to commit, False if the sync
    left webpage's working tree unchanged (nothing new to push)."""
    target_dir = webpage_repo / "veridem"
    target_dir.mkdir(exist_ok=True)

    shutil.copy(FEED_PATH, target_dir / "changes.xml")
    entries = _read_entries(FEED_PATH)
    update_index_html(target_dir / "index.html", entries)

    _run(["git", "add", "veridem/changes.xml", "veridem/index.html"], webpage_repo)
    diff = subprocess.run(
        ["git", "diff", "--cached", "--quiet"], cwd=webpage_repo
    )
    if diff.returncode == 0:
        return False  # nothing actually changed

    _run(["git", "config", "user.name", "veridem-bot"], webpage_repo)
    _run(["git", "config", "user.email", "actions@users.noreply.github.com"], webpage_repo)
    _run(["git", "commit", "-m", "Update veridem changes feed"], webpage_repo)
    _run(["git", "push"], webpage_repo)
    return True


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: sync_webpage.py <path to checked-out webpage repo>", file=sys.stderr)
        return 1
    webpage_repo = Path(sys.argv[1])
    if not FEED_PATH.exists():
        print(f"{FEED_PATH} doesn't exist -- nothing to sync", file=sys.stderr)
        return 1
    changed = sync(webpage_repo)
    print("Synced, pushed." if changed else "No change -- nothing to push.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
