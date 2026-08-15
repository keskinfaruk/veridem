"""
Minimal Bluesky (AT Protocol) client: enough to log in and post plain text
with an optional link. Raw `requests` rather than the `atproto` package,
since the protocol surface used here is three HTTP calls.

Credentials come from BLUESKY_DATA_HANDLE / BLUESKY_DATA_APP_PASSWORD,
loaded via .env locally. This module only reads values already in the
environment; it never prompts for or writes one.
"""

import os
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

BASE_URL = "https://bsky.social/xrpc"
POST_MAX_GRAPHEMES = 300


def get_credentials() -> tuple[str, str]:
    load_dotenv()
    handle = os.environ.get("BLUESKY_DATA_HANDLE")
    app_password = os.environ.get("BLUESKY_DATA_APP_PASSWORD")
    if not handle or not app_password:
        raise RuntimeError(
            "BLUESKY_DATA_HANDLE / BLUESKY_DATA_APP_PASSWORD not set -- "
            "checked .env (local) and the environment (CI: repository secrets)"
        )
    return handle, app_password


def create_session(handle: str, app_password: str) -> dict:
    """Authenticate with an App Password, never the account password
    (Bluesky Settings -> App Passwords). Returns accessJwt, did, and more."""
    resp = requests.post(
        f"{BASE_URL}/com.atproto.server.createSession",
        json={"identifier": handle, "password": app_password},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def byte_range(text: str, substring: str) -> tuple[int, int]:
    """UTF-8 byte offsets of `substring` within `text`.

    Bluesky's rich-text facets index into the post's UTF-8 *byte* encoding,
    not codepoint positions. Turkish characters (ş ğ ı ö ü ç İ) take two
    bytes each, so any Turkish text before a link shifts its byte offset away
    from its character offset. Never use `text.index()` for this.
    """
    encoded = text.encode("utf-8")
    sub_encoded = substring.encode("utf-8")
    start = encoded.index(sub_encoded)
    return start, start + len(sub_encoded)


def build_post_record(text: str, link_url: str | None = None) -> dict:
    """Build the app.bsky.feed.post record, with a link facet if `link_url`
    appears in `text`."""
    if len(text) > POST_MAX_GRAPHEMES:
        raise ValueError(f"post text is {len(text)} graphemes, over the {POST_MAX_GRAPHEMES} limit")

    record = {
        "$type": "app.bsky.feed.post",
        "text": text,
        "createdAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
    }
    if link_url and link_url in text:
        start, end = byte_range(text, link_url)
        record["facets"] = [
            {
                "index": {"byteStart": start, "byteEnd": end},
                "features": [{"$type": "app.bsky.richtext.facet#link", "uri": link_url}],
            }
        ]
    return record


def post(session: dict, text: str, link_url: str | None = None) -> dict:
    """Create a post. `session` is create_session()'s return value."""
    record = build_post_record(text, link_url=link_url)
    resp = requests.post(
        f"{BASE_URL}/com.atproto.repo.createRecord",
        headers={"Authorization": f"Bearer {session['accessJwt']}"},
        json={"repo": session["did"], "collection": "app.bsky.feed.post", "record": record},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def delete_post(session: dict, uri: str) -> None:
    """Delete a post by its at:// URI, as returned by post()."""
    resp = requests.post(
        f"{BASE_URL}/com.atproto.repo.deleteRecord",
        headers={"Authorization": f"Bearer {session['accessJwt']}"},
        json={"repo": session["did"], "collection": "app.bsky.feed.post", "rkey": uri.rsplit("/", 1)[-1]},
        timeout=30,
    )
    resp.raise_for_status()
