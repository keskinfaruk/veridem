"""
Shared client for TUIK's SDMX 2.1 Web Service.

Auth flow per TUIK SDMX Web Service Documentation section 4.3.1:

    POST https://giris.tuik.gov.tr/realms/web/protocol/openid-connect/token
    Content-Type: application/x-www-form-urlencoded
    grant_type=password
    client_id=nsi-ws-consumer
    api_key={API_KEY}

The returned access_token is used as a Bearer token against
https://nsiws.tuik.gov.tr/rest/...
"""

import os
import time

import requests
from dotenv import load_dotenv

TOKEN_URL = "https://giris.tuik.gov.tr/realms/web/protocol/openid-connect/token"
BASE_URL = "https://nsiws.tuik.gov.tr/rest"

# TUIK's NSI instance is prone to transient read timeouts under load, even
# when the service is otherwise healthy. Retry those -- and only those,
# never an HTTP error status -- with a short backoff before giving up.
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

# SDMX-ML 2.1 namespaces, used throughout for parsing structure/data responses.
NS = {
    "str": "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/structure",
    "mes": "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/message",
    "com": "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/common",
    "gen": "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/data/generic",
}


def get_access_token(api_key: str | None = None) -> str:
    """Fetch a fresh Keycloak access token. Tokens expire (~1hr) — never cache to disk."""
    load_dotenv()
    api_key = api_key or os.environ.get("TUIK_API_KEY")
    if not api_key:
        raise RuntimeError(
            "TUIK_API_KEY not set -- checked .env (local) and the environment "
            "(CI: set it as a repository secret)"
        )

    resp = _with_retries(
        requests.post,
        TOKEN_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "grant_type": "password",
            "client_id": "nsi-ws-consumer",
            "api_key": api_key,
        },
        timeout=30,
    )
    resp.raise_for_status()
    token = resp.json().get("access_token")
    if not token:
        raise RuntimeError(f"Token response had no access_token: {resp.text[:500]}")
    return token


def get(path: str, token: str, params: dict | None = None, **kwargs) -> requests.Response:
    """Authenticated GET against the TUIK SDMX REST base URL.

    `path` is relative to BASE_URL, e.g. "dataflow/TR/all/latest".
    """
    url = f"{BASE_URL}/{path.lstrip('/')}"
    resp = _with_retries(
        requests.get,
        url,
        headers={"Authorization": f"Bearer {token}"},
        params=params,
        timeout=kwargs.pop("timeout", 60),
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

    Series keys with many empty (unfiltered) trailing dimensions produce runs
    of consecutive dots (e.g. "TR.A.NG_TDH..............."). IIS's request
    filtering treats ".." anywhere in a URL path as a directory-traversal
    attempt and rejects it with a bare 404 — before the request ever reaches
    the SDMX application, so the server-side error format never shows up.

    Percent-encoding the dots (%2E) avoids the raw ".." pattern, but `requests`
    normally re-decodes %2E back to a literal "." before sending, because "."
    is an RFC 3986 unreserved character — so the trap reappears silently.
    We build a fully prepared request and overwrite `.url` after preparation
    to stop that re-normalization from undoing the encoding.
    """
    encoded_key = series_key.replace(".", "%2E")
    url = f"{BASE_URL}/data/{agency},{dataflow_id},{version}/{encoded_key}"
    if params:
        from urllib.parse import urlencode

        url = f"{url}?{urlencode(params)}"

    session = requests.Session()
    req = requests.Request("GET", url, headers={"Authorization": f"Bearer {token}"})
    prepared = req.prepare()
    prepared.url = url  # stop requests from undoing the %2E encoding
    resp = _with_retries(session.send, prepared, timeout=60)
    resp.raise_for_status()
    return resp
