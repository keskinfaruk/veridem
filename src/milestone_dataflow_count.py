"""
Authenticates against TUIK's Keycloak token endpoint using the API key
from .env, then fetches the full dataflow inventory and prints how many
dataflows TUIK publishes.
"""

import sys
import xml.etree.ElementTree as ET

import requests

from tuik_client import NS, get, get_access_token


def main() -> int:
    print("Requesting access token...")
    try:
        token = get_access_token()
    except (RuntimeError, requests.HTTPError) as e:
        print(f"ERROR: token request failed: {e}", file=sys.stderr)
        return 1
    print("Token acquired.")

    print("Fetching dataflow/TR/all...")
    try:
        resp = get("dataflow/TR/all/latest", token, params={"detail": "full"})
    except requests.HTTPError as e:
        print(f"ERROR: dataflow request failed: {e}", file=sys.stderr)
        print(f"Response body: {e.response.text[:1000]}", file=sys.stderr)
        return 1

    root = ET.fromstring(resp.content)
    count = len(root.findall(".//str:Dataflow", NS))
    print(f"\nTUIK publishes {count} dataflows.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
