"""
Client for TÜİK's SDMX 2.1 Web Service.

Auth per the SDMX Web Service Documentation section 4.3.1: POST the API key
to the Keycloak token endpoint with grant_type=password and
client_id=nsi-ws-consumer, then send the returned access_token as a Bearer
token against https://nsiws.tuik.gov.tr/rest.
Also builds SDMX series keys from a dataflow's live DSD. Never hardcode a
key template: TÜİK dataflows share DSDs across many datasets, dimension
order varies by DSD, and a version bump silently breaks a fixed key.
"""

import os
import xml.etree.ElementTree as ET
from urllib.parse import urlencode

import requests
from dotenv import load_dotenv

from net import with_retries

TOKEN_URL = "https://giris.tuik.gov.tr/realms/web/protocol/openid-connect/token"
BASE_URL = "https://nsiws.tuik.gov.tr/rest"

# TUIK's NSI instance intermittently accepts a data request and then never
# answers, while healthy responses arrive in well under a second. A generous
# ceiling therefore buys nothing and only delays the retry, which usually
# succeeds immediately, so data requests get a short deliberate one.
DATA_TIMEOUT_SECONDS = 30
STRUCTURE_TIMEOUT_SECONDS = 60

# SDMX-ML 2.1 namespaces, used throughout for parsing structure/data responses.
NS = {
    "str": "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/structure",
    "mes": "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/message",
    "com": "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/common",
    "gen": "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/data/generic",
}


def get_access_token(api_key: str | None = None) -> str:
    """Fetch a fresh Keycloak access token. Tokens expire after ~1 hour, so
    never cache one to disk."""
    load_dotenv()
    api_key = api_key or os.environ.get("TUIK_API_KEY")
    if not api_key:
        raise RuntimeError(
            "TUIK_API_KEY not set -- checked .env (local) and the environment "
            "(CI: set it as a repository secret)"
        )

    resp = with_retries(
        requests.post,
        TOKEN_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={"grant_type": "password", "client_id": "nsi-ws-consumer", "api_key": api_key},
        timeout=30,
    )
    resp.raise_for_status()
    token = resp.json().get("access_token")
    if not token:
        raise RuntimeError(f"Token response had no access_token: {resp.text[:500]}")
    return token


def get(path: str, token: str, params: dict | None = None, **kwargs) -> requests.Response:
    """Authenticated GET against BASE_URL. `path` is relative, e.g.
    "dataflow/TR/all/latest"."""
    resp = with_retries(
        requests.get,
        f"{BASE_URL}/{path.lstrip('/')}",
        headers={"Authorization": f"Bearer {token}"},
        params=params,
        timeout=kwargs.pop("timeout", STRUCTURE_TIMEOUT_SECONDS),
        **kwargs,
    )
    resp.raise_for_status()
    return resp


def fetch_data(
    agency: str,
    dataflow_id: str,
    version: str,
    series_key: str,
    token: str,
    params: dict | None = None,
) -> requests.Response:
    """Fetch observations for a series key.

    Keys with many unfiltered trailing dimensions contain runs of
    consecutive dots ("TR.A.NG_TDH..............."). IIS rejects any URL path
    containing ".." as directory traversal, with a bare 404 raised before the
    request reaches the SDMX application. Percent-encoding the dots avoids
    that, but `requests` re-decodes %2E during preparation because "." is an
    RFC 3986 unreserved character, so the URL is overwritten after
    preparation to keep the encoding intact.
    """
    url = f"{BASE_URL}/data/{agency},{dataflow_id},{version}/{series_key.replace('.', '%2E')}"
    if params:
        url = f"{url}?{urlencode(params)}"

    prepared = requests.Request("GET", url, headers={"Authorization": f"Bearer {token}"}).prepare()
    prepared.url = url  # stop requests from undoing the %2E encoding
    resp = with_retries(requests.Session().send, prepared, timeout=DATA_TIMEOUT_SECONDS)
    resp.raise_for_status()
    return resp


def get_dimension_order(agency: str, dataflow_id: str, version: str, token: str) -> list[str]:
    """A dataflow's non-time dimension IDs, in series-key order. The key is
    positional and excludes the TimeDimension: time is filtered separately
    via startPeriod/endPeriod query parameters."""
    resp = get(f"dataflow/{agency}/{dataflow_id}/{version}", token, params={"references": "children"})
    dims = ET.fromstring(resp.content).findall(
        ".//str:DataStructure/.//str:DimensionList/str:Dimension", NS
    )
    dims.sort(key=lambda d: int(d.get("position")))
    return [d.get("id") for d in dims]


def build_series_key(dim_order: list[str], filters: dict[str, str]) -> str:
    """Build a dot-separated positional series key. Dimensions absent from
    `filters` are left as an empty segment (unfiltered), per the SDMX REST
    spec. Raises if `filters` names a dimension the DSD does not have, which
    means the DSD changed shape."""
    unknown = set(filters) - set(dim_order)
    if unknown:
        raise ValueError(f"filters reference dimensions not in this DSD: {unknown}")
    return ".".join(filters.get(dim, "") for dim in dim_order)
