"""
Atom feed of instant Türkiye change notices, separate from the blog's own
feed.xml in the `webpage` repo: these are generated fact notices, not
hand-written posts. New entries go in at the top, existing ones stay, oldest
roll off past MAX_ENTRIES. Nothing is lost, since git preserves the full
history; this is just a rolling window for subscribers.

FEED_PATH is this repo's working copy. FEED_URL / SITE_URL point at where
the feed is publicly reachable, synced there by the daily workflow.
"""

import shutil
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

FEED_PATH = Path(__file__).resolve().parent.parent / "changes.xml"
FEED_URL = "https://faruk.page/veridem/changes.xml"
SITE_URL = "https://faruk.page/veridem/indicators.html"
MAX_ENTRIES = 200

ATOM_NS = "http://www.w3.org/2005/Atom"
ET.register_namespace("", ATOM_NS)


def _tag(name: str) -> str:
    return f"{{{ATOM_NS}}}{name}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _entry_id(notice: dict) -> str:
    """A stable, globally-unique, non-dereferencing identifier: the `tag:`
    URI scheme (RFC 4151), used because no per-entry permalink exists yet.
    Built from the notice's own snapshot_id rather than a batch-wide one,
    since one run can carry notices from several dataflows."""
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    slug = (
        f"{notice['indicator']}-{notice['ref_area']}-{notice['time_period']}-"
        f"{notice['change_class']}-{notice['snapshot_id']}"
    )
    return f"tag:faruk.page,{date}:veridem/changes/{slug}"


def _load_existing_entries(path: Path) -> list[ET.Element]:
    if not path.exists():
        return []
    root = ET.parse(path).getroot()
    return root.findall(_tag("entry"))


def _build_entry(notice: dict) -> ET.Element:
    """<title> is the short headline, which is what a reader's list view
    shows. <summary> is the same bluesky_text the Bluesky post carries, so
    the synced webpage list matches that level of detail. <content> carries
    the full report block for readers that want trend context."""
    entry = ET.Element(_tag("entry"))
    ET.SubElement(entry, _tag("title")).text = notice["title"]
    ET.SubElement(entry, _tag("id")).text = _entry_id(notice)
    ET.SubElement(entry, _tag("updated")).text = _now_iso()
    summary = ET.SubElement(entry, _tag("summary"))
    summary.set("type", "text")
    summary.text = notice["bluesky_text"]
    content = ET.SubElement(entry, _tag("content"))
    content.set("type", "text")
    content.text = notice["feed_content"]
    return entry


def append_notices(notices: list[dict], path: Path | None = None) -> Path:
    """Prepend `notices` as new entries, newest first, keeping at most
    MAX_ENTRIES. Only call with a non-empty list: this always rewrites the
    file, so an empty list would bump `updated` for no reason."""
    path = path or FEED_PATH
    existing = _load_existing_entries(path)
    new_entries = [_build_entry(n) for n in notices]
    _write_feed((new_entries + existing)[:MAX_ENTRIES], path)
    return path


def _write_feed(entries: list[ET.Element], path: Path) -> None:
    root = ET.Element(_tag("feed"))
    ET.SubElement(root, _tag("title")).text = "veridem — instant change notices (Türkiye)"
    ET.SubElement(root, _tag("id")).text = FEED_URL
    link_self = ET.SubElement(root, _tag("link"))
    link_self.set("rel", "self")
    link_self.set("href", FEED_URL)
    link_alt = ET.SubElement(root, _tag("link"))
    link_alt.set("href", SITE_URL)
    ET.SubElement(root, _tag("updated")).text = _now_iso()
    author = ET.SubElement(root, _tag("author"))
    ET.SubElement(author, _tag("name")).text = "veridem"
    ET.SubElement(root, _tag("subtitle")).text = (
        "Fact-only notices the moment a Türkiye indicator changes — no "
        "interpretation. See the veridem blog for narrative and context."
    )

    for entry in entries:
        root.append(entry)

    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    tree.write(path, encoding="UTF-8", xml_declaration=True)


def seed_from_published(webpage_repo: Path, path: Path | None = None) -> bool:
    """Copy the published feed into this repo's working copy, so append_notices()
    prepends onto the real history instead of starting from nothing.

    A fresh CI checkout has no local changes.xml (it is deliberately never
    committed here), so without this the next sync would overwrite the public
    history. Refuses to overwrite an existing local file: whoever seeded it
    first in this run already pulled the published history in, and clobbering
    it would drop notices written since.
    """
    path = path or FEED_PATH
    if path.exists():
        return False
    src = webpage_repo / "veridem" / "changes.xml"
    if not src.exists():
        return False
    shutil.copy(src, path)
    return True


def reset(path: Path | None = None) -> Path:
    """Write an empty feed, discarding every existing entry.

    Only for deliberately starting the channel over. The daily run never calls
    this; it is reachable through the workflow's `reset_feed` input.
    """
    path = path or FEED_PATH
    _write_feed([], path)
    return path
