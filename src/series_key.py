"""
Builds SDMX series keys programmatically from a dataflow's DSD.

Never hardcode a series key template: TUIK dataflows share DSDs across many
datasets, dimension order varies by DSD, and a DSD version bump silently
breaks a hardcoded key. Always fetch the DSD, read the actual dimension
order, and build the key from that.
"""

import xml.etree.ElementTree as ET

from tuik_client import NS, get


def get_dimension_order(agency: str, dataflow_id: str, version: str, token: str) -> list[str]:
    """Fetch a dataflow's DSD and return non-time dimension IDs in series-key order.

    The series key is positional and excludes the TimeDimension (time filtering
    is done separately via startPeriod/endPeriod query parameters).
    """
    resp = get(
        f"dataflow/{agency}/{dataflow_id}/{version}",
        token,
        params={"references": "children"},
    )
    root = ET.fromstring(resp.content)

    dims = root.findall(".//str:DataStructure/.//str:DimensionList/str:Dimension", NS)
    dims.sort(key=lambda d: int(d.get("position")))
    return [d.get("id") for d in dims]


def build_series_key(dim_order: list[str], filters: dict[str, str]) -> str:
    """Build a dot-separated positional series key.

    `filters` maps dimension id -> code. Dimensions not in `filters` are left
    as an empty segment (no filter on that dimension), per SDMX REST spec.
    Raises if `filters` names a dimension that doesn't exist in `dim_order`,
    since that's a sign the DSD has changed shape.
    """
    unknown = set(filters) - set(dim_order)
    if unknown:
        raise ValueError(f"filters reference dimensions not in this DSD: {unknown}")
    return ".".join(filters.get(dim, "") for dim in dim_order)
