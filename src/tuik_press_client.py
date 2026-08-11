"""
Client for TÜİK's veriportali.tuik.gov.tr press-release API -- a second,
undocumented sibling of the SDMX Web Service (tuik_client.py). TÜİK
publishes mortality, migration, and fresher fertility figures through its
press bulletins that aren't available via SDMX at all; this is a
stateless HTTP path to the same official Excel tables the bulletins are
built from.

Mechanism, reverse-engineered from the data portal's own network calls (no
official documentation exists for this endpoint):

    GET https://veriportali.tuik.gov.tr/api/en/press/{press_id}
    Header: X-Requested-With: XMLHttpRequest

Returns the press release as JSON, including a `tables` array -- one entry
per named data table shown on the page, each with a signed download URL
for that table as a real (old-format BIFF/OLE2) .xls file. The
XMLHttpRequest header is the only non-obvious requirement: a request
without it gets a generic WAF "access denied" or a 404 -- this isn't real
bot detection (no session, no cookie, no JS challenge), just an
XHR-vs-navigation gate. Confirmed stateless: works from a cold request
with no prior page load and no cookies.

Press-ID discovery: the /en/press-releases page itself calls

    GET https://veriportali.tuik.gov.tr/api/en/press

with no ID and no params -- returns a flat list of ~169 entries, one per
theme across every TÜİK subject, each with that theme's *current* press
ID. Only gives the current release per theme, not history -- each press's
own `previousPresses` field (seen in fetch_press()'s response, not yet
used here) is the way to walk backward if older releases are ever needed.

This module itself stays a thin, generic client (discover/fetch/download/
parse) -- the real connector built on top of it is
fetch_tuik_press_indicators.py, which normalizes specific tables into
`observations` rows under its own source tag, `source='tuik_press'`. That
source is never blended into the SDMX-sourced 'tuik' series for the same
indicator/period, since the two aren't guaranteed to agree -- press
figures can predate the SDMX service's own administrative revisions.
"""

import io
import time
from urllib.parse import urljoin

import pandas as pd
import requests

BASE_URL = "https://veriportali.tuik.gov.tr"

# Same rationale as tuik_client.py's _with_retries: retry transient
# timeouts/connection errors only, never an HTTP error status.
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 5


def _with_retries(fn, *args, **kwargs):
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return fn(*args, **kwargs)
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
            if attempt == MAX_RETRIES:
                raise
            time.sleep(RETRY_BACKOFF_SECONDS * attempt)


# Same path serves two purposes: bare (no ID) lists every theme's current
# press ID (discover_press_ids()); with an ID appended, one release's full
# content (fetch_press()).
PRESS_API = f"{BASE_URL}/api/en/press"

# X-Requested-With is the one header the WAF actually checks for -- see
# module docstring. User-Agent is included out of caution.
HEADERS = {
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
    ),
}

def discover_press_ids(category: str | None = None) -> list[dict]:
    """Every theme's *current* press ID, across all of TÜİK, in one
    stateless call -- see module docstring. Each entry looks like
    `{"id": "58018", "title": "Birth Statistics", "url": "/en/press/58018",
    "categoryId": 11, "categoryName": "Population and Demography"}`.

    `category`, when given, filters to an exact `categoryName` match (e.g.
    "Population and Demography") -- convenience, not a separate API call;
    the underlying endpoint always returns the full ~169-entry list.

    Gives the *current* release per theme only, not history. A press's own
    `previousPresses` field (present in fetch_press()'s response, not yet
    used by this module) is the way to walk backward if older releases are
    ever needed -- e.g. to backfill years the current release doesn't cover.
    """
    resp = _with_retries(requests.get, PRESS_API, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("isError"):
        raise RuntimeError(f"press listing: API returned isError -- {payload.get('message')}")
    entries = payload["data"]
    if category is not None:
        entries = [e for e in entries if e.get("categoryName") == category]
    return entries


def fetch_press(press_id: int) -> dict:
    """One press release's full JSON body -- content, metadata, and the
    `tables` list of downloadable Excel tables."""
    resp = _with_retries(requests.get, f"{PRESS_API}/{press_id}", headers=HEADERS, timeout=30)
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("isError"):
        raise RuntimeError(f"press {press_id}: API returned isError -- {payload.get('message')}")
    return payload["data"]


def download_table(table: dict) -> bytes:
    """Raw .xls bytes for one entry from a press release's `tables` list."""
    resp = _with_retries(requests.get, urljoin(BASE_URL, table["url"]), headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.content


def parse_table(xls_bytes: bytes) -> pd.DataFrame:
    """TÜİK's press-release tables are old-format .xls (BIFF/OLE2), not
    .xlsx -- needs the xlrd engine explicitly (pandas' default .xls engine
    changed in a past version bump, openpyxl doesn't read this format at
    all). header=None: every table seen so far has a title/subtitle row
    and a two-line (Turkish\\nEnglish) header row that pandas can't
    usefully auto-detect -- slicing which rows/columns matter is the
    caller's job, table by table (not attempted generically here; see
    fetch_tuik_press_indicators.py's per-table parser functions)."""
    return pd.read_excel(io.BytesIO(xls_bytes), header=None, engine="xlrd")


def find_table(press_data: dict, title_contains: str) -> dict:
    """First table whose title contains `title_contains` (case-insensitive)
    -- avoids hardcoding index positions, which shift release to release
    as TÜİK adds/reorders tables. Searches both `tables` (the small set
    shown inline on the release page) and `statisticalTables` (the fuller
    download catalog, e.g. the "Tables and Graphics" section) -- same
    shape (title/url), just two different lists TÜİK's API returns them in.
    """
    needle = title_contains.lower()
    candidates = press_data.get("tables", []) + press_data.get("statisticalTables", [])
    for t in candidates:
        if needle in t["title"].lower():
            return t
    raise KeyError(f"no table containing {title_contains!r} -- available: {[t['title'] for t in candidates]}")


DEMO_CATEGORY = "Population and Demography"

# One representative table per demo theme -- headline indicators for that
# domain, not an exhaustive pull. Keyed by theme title, resolved to a press
# ID via discover_press_ids() below.
DEMO_TABLES = {
    "Birth Statistics": "Basic fertility indicators",
    "Death and Causes of Death Statistics": "Basic mortality indicators",
    "Internal Migration Statistics": "Provincial in-migration, out-migration, net migration",
    "International Migration Statistics": "Immigrants and emigrants by age group and sex",
    "Life Tables": "Healthy life years",
}


def main() -> None:
    """Discover -> fetch -> list tables -> download -> parse, for real,
    across every demo theme. Proves the *whole* mechanism end to end,
    press-ID discovery included -- no hardcoded ID anywhere in this run --
    and prints actual parsed values so the output is checkable against the
    live press bulletins by eye."""
    catalogue = discover_press_ids(category=DEMO_CATEGORY)
    print(f"Discovered {len(catalogue)} current press releases under {DEMO_CATEGORY!r}:")
    by_title = {e["title"]: int(e["id"]) for e in catalogue}
    for title, press_id in sorted(by_title.items()):
        flag = " <- demo" if title in DEMO_TABLES else ""
        print(f"  {press_id:>6}  {title}{flag}")

    for label, wanted_table in DEMO_TABLES.items():
        press_id = by_title[label]  # KeyError here would mean discovery missed a theme -- want that loud
        print(f"\n{'=' * 70}\n{label} (press {press_id}, discovered)\n{'=' * 70}")
        data = fetch_press(press_id)
        print(f"Title: {data['title']} | Period: {data.get('period')} | Date: {data.get('date')}")
        print(f"{len(data.get('tables', []))} tables available:")
        for t in data.get("tables", []):
            print(f"  - {t['title']}")

        table = find_table(data, wanted_table)
        print(f"\nDownloading and parsing: {table['title']!r}")
        df = parse_table(download_table(table))
        print(f"Shape: {df.shape}")
        print(df.to_string(max_rows=15))


if __name__ == "__main__":
    main()
