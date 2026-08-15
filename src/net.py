"""
Shared HTTP retry helper for every source client.

TUIK's SDMX service and its press portal are both prone to transient read
timeouts under load while otherwise healthy. Retry those, and only those:
an HTTP error status is a real answer, never retried.
"""

import time

import requests

MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 5


def with_retries(fn, *args, **kwargs):
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return fn(*args, **kwargs)
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
            if attempt == MAX_RETRIES:
                raise
            time.sleep(RETRY_BACKOFF_SECONDS * attempt)
