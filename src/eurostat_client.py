"""
Client for Eurostat's SDMX 2.1 dissemination API.

No auth required (unlike TUIK). Eurostat's `format=SDMX-CSV` actually works
here -- TUIK's NSI instance ignores that parameter (see tuik_client.py) -- so
this client skips XML parsing entirely and fetches CSV straight into pandas.

Eurostat holds only the latest version of each dataset; there is no archive
of past versions, so this project's own snapshot history is the only
revision record.
"""

import xml.etree.ElementTree as ET
from io import StringIO

import pandas as pd
import requests

BASE_URL = "https://ec.europa.eu/eurostat/api/dissemination/sdmx/2.1"

NS = {
    "str": "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/structure",
}


def get_dimension_order(dataset: str, agency: str = "ESTAT") -> list[str]:
    """Fetch a dataset's DSD and return non-time dimension IDs in series-key
    order. Same principle as tuik_client.py / series_key.py: never hardcode
    a key template, always read the order from the live DSD."""
    resp = requests.get(
        f"{BASE_URL}/dataflow/{agency}/{dataset}",
        params={"references": "children"},
        timeout=60,
    )
    resp.raise_for_status()
    root = ET.fromstring(resp.content)
    dims = root.findall(".//str:DataStructure//str:DimensionList/str:Dimension", NS)
    dims.sort(key=lambda d: int(d.get("position")))
    return [d.get("id") for d in dims]


def build_series_key(dim_order: list[str], filters: dict[str, str]) -> str:
    """Build a dot-separated positional series key. `filters` maps dimension
    id (case-insensitive -- Eurostat DSDs use lowercase ids) to a code, which
    may itself be a '+'-joined list for SDMX OR semantics (e.g. multiple
    indicators in one request). Dimensions not in `filters` are left as an
    empty segment (no filter), per SDMX REST spec.
    """
    lookup = {k.lower(): v for k, v in filters.items()}
    unknown = set(lookup) - {d.lower() for d in dim_order}
    if unknown:
        raise ValueError(f"filters reference dimensions not in this DSD: {unknown}")
    return ".".join(lookup.get(dim.lower(), "") for dim in dim_order)


def fetch_data_csv(
    dataset: str, series_key: str, params: dict | None = None
) -> pd.DataFrame:
    """Fetch observations for a series key as SDMX-CSV."""
    query = {"format": "SDMX-CSV"}
    if params:
        query.update(params)
    resp = requests.get(f"{BASE_URL}/data/{dataset}/{series_key}", params=query, timeout=60)
    resp.raise_for_status()
    return pd.read_csv(StringIO(resp.text))
