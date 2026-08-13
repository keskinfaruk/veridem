"""
TUIK SDMX categorisation: which dataflows sit under category 11
(Population and Demography), fetched live, never hardcoded.

`categorisation/TR/all` maps every dataflow to a category, but the
Target Ref `id` it gives is a full dot-joined path from the scheme root,
not the leaf category's own id (e.g. dataflow DF_DOGUM_TEMEL_DOG_GOST
maps to Target id `11.11_9.11_9_3`, even though that leaf category's own
`id` in categoryscheme/TR/all is just `11_9_3`). Every dataflow under
category 11 or a descendant has a Target id equal to `11` or starting
with `11.`.
"""

import xml.etree.ElementTree as ET

from tuik_client import get, get_access_token

STR = "{http://www.sdmx.org/resources/sdmxml/schemas/v2_1/structure}"


def theme11_dataflow_ids(token: str | None = None) -> set[str]:
    """Every dataflow_id currently categorised under 11 (Population and
    Demography) or one of its descendants, live from TUIK's own
    categorisation catalogue."""
    token = token or get_access_token()
    resp = get("categorisation/TR/all", token)
    root = ET.fromstring(resp.content)

    ids = set()
    for cat in root.findall(f".//{STR}Categorisation"):
        target = cat.find(f"{STR}Target/Ref")
        source = cat.find(f"{STR}Source/Ref")
        if target is None or source is None:
            continue
        category_id = target.get("id", "")
        if category_id == "11" or category_id.startswith("11."):
            ids.add(source.get("id"))
    return ids


def main() -> None:
    ids = theme11_dataflow_ids()
    print(f"{len(ids)} dataflow(s) currently under category 11 (Population and Demography):")
    for i in sorted(ids):
        print(f"  {i}")


if __name__ == "__main__":
    main()
