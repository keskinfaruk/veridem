"""
Atom feed of instant Turkiye change notices. Separate from the blog's own
feed.xml in the `webpage` repo -- this one is generated fact notices, not
hand-written posts, but uses the same incremental idiom: new entries go in
at the top, existing ones stay, oldest roll off past MAX_ENTRIES. Full
history isn't lost either way -- git preserves it regardless -- this is
just a rolling window for subscribers.

FEED_PATH is this repo's own working copy. FEED_URL / SITE_URL point at
where the feed is actually publicly reachable (faruk.page/veridem/),
synced there from this file by the daily workflow.
"""

import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

FEED_PATH = Path(__file__).resolve().parent.parent / "changes.xml"
FEED_URL = "https://faruk.page/veridem/changes.xml"
SITE_URL = "https://faruk.page/veridem/"
MAX_ENTRIES = 200

ATOM_NS = "http://www.w3.org/2005/Atom"
ET.register_namespace("", ATOM_NS)


def _tag(name: str) -> str:
    return f"{{{ATOM_NS}}}{name}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _entry_id(notice: dict) -> str:
    """A stable, globally-unique, non-dereferencing identifier -- the
    standard `tag:` URI scheme (RFC 4151), used because there's no real
    per-entry permalink to point at yet. Built from the notice's own
    snapshot_id (not a shared one for the whole batch) -- a single
    daily_run.py invocation can carry notices from several different
    dataflows, each with its own snapshot_id."""
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
    """<title> is the short headline (Atom convention -- a feed reader's
    inbox/list view shows this). <summary> is the same bluesky_text every
    real Bluesky post carries, so the synced webpage list shows the same
    level of detail as the Bluesky post rather than just the bare headline.
    <content> carries the full ASCII report block, for a feed reader that
    wants the richer trend-context/recent-series detail.
    """
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
    """Prepend `notices` as new Atom entries, newest first, keeping at most
    MAX_ENTRIES total. Callers should only call this when `notices` is
    non-empty -- it always rewrites the file, so an empty list would just
    bump `updated` for no reason.
    """
    path = path or FEED_PATH
    existing = _load_existing_entries(path)
    new_entries = [_build_entry(n) for n in notices]

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

    for entry in (new_entries + existing)[:MAX_ENTRIES]:
        root.append(entry)

    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    tree.write(path, encoding="UTF-8", xml_declaration=True)
    return path
