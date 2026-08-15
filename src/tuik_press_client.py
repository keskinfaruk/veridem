"""
Client for TÜİK's veriportali.tuik.gov.tr press-release API, an
undocumented sibling of the SDMX Web Service (tuik_client.py). TÜİK
publishes mortality, migration, and fresher fertility figures through its
press bulletins that SDMX does not carry at all.

Two endpoints, both stateless (no session, no cookies):

    GET /api/en/press            every theme's *current* press ID (~169)
    GET /api/en/press/{id}       one release, including its table list

`X-Requested-With: XMLHttpRequest` is required on both: without it the WAF
answers "access denied" or 404. It is an XHR-vs-navigation gate, not bot
detection.

Only current releases are exposed per theme. A release's own
`previousPresses` field is the way back to older ones, unused so far.
"""

import io
from urllib.parse import urljoin

import pandas as pd
import requests

from net import with_retries

BASE_URL = "https://veriportali.tuik.gov.tr"
PRESS_API = f"{BASE_URL}/api/en/press"

HEADERS = {
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
    ),
}


def _get_json(url: str, what: str) -> dict | list:
    resp = with_retries(requests.get, url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("isError"):
        raise RuntimeError(f"{what}: API returned isError -- {payload.get('message')}")
    return payload["data"]


def discover_press_ids(category: str | None = None) -> list[dict]:
    """Every theme's current press ID. Entries look like `{"id": "58018",
    "title": "Birth Statistics", "categoryId": 11, "categoryName":
    "Population and Demography"}`. `category` filters on an exact
    `categoryName` match; the endpoint always returns the full list.
    """
    entries = _get_json(PRESS_API, "press listing")
    if category is not None:
        entries = [e for e in entries if e.get("categoryName") == category]
    return entries


def fetch_press(press_id: int) -> dict:
    """One release's full JSON body: content, metadata, and its
    downloadable `tables` / `statisticalTables` lists."""
    return _get_json(f"{PRESS_API}/{press_id}", f"press {press_id}")


def download_table(table: dict) -> bytes:
    """Raw .xls bytes for one entry from a release's table list."""
    resp = with_retries(requests.get, urljoin(BASE_URL, table["url"]), headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.content


def parse_table(xls_bytes: bytes) -> pd.DataFrame:
    """Read a press table into a raw, unheadered frame.

    These are old-format .xls (BIFF/OLE2), so the xlrd engine is required
    explicitly (openpyxl cannot read the format). header=None because every
    table carries title/subtitle rows and a two-line Turkish/English header
    that pandas cannot auto-detect; slicing is each parser's own job (see
    fetch_tuik_press_indicators.py).
    """
    return pd.read_excel(io.BytesIO(xls_bytes), header=None, engine="xlrd")


def find_table(press_data: dict, title_contains: str) -> dict:
    """First table whose title contains `title_contains` (case-insensitive),
    searching both `tables` and `statisticalTables`. Matched by title rather
    than index, which shifts release to release as TÜİK reorders tables."""
    needle = title_contains.lower()
    candidates = press_data.get("tables", []) + press_data.get("statisticalTables", [])
    for t in candidates:
        if needle in t["title"].lower():
            return t
    raise KeyError(f"no table containing {title_contains!r} -- available: {[t['title'] for t in candidates]}")
