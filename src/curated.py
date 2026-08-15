"""
The curated list: the single definition of what this project publishes.

data/curated_cards.csv names one series per card, keyed by
(source, indicator, sex, age) and always for Türkiye. Two consumers:

    cards.py            builds the website card set from it
    instant_notice.py   uses it as the gate on what may post to Bluesky

Kept in its own module so both can import it without a cycle.
"""

from pathlib import Path

import pandas as pd

CURATED_PATH = Path(__file__).resolve().parent.parent / "data" / "curated_cards.csv"

# Domain order on the page. Anything not listed sorts last.
DOMAIN_ORDER = ("Fertility", "Mortality", "Migration", "Population", "Nuptiality")

CARD_KEY = ["source", "indicator", "sex", "age"]
REF_AREA = "TR"


def load_curated(path: Path | None = None) -> pd.DataFrame:
    """The curated list in page order: domain first, then file order."""
    df = pd.read_csv(path or CURATED_PATH, dtype=str, keep_default_na=False)
    missing = set(CARD_KEY + ["domain"]) - set(df.columns)
    if missing:
        raise ValueError(f"curated_cards.csv missing columns: {missing}")
    df["_d"] = df["domain"].map(
        lambda d: DOMAIN_ORDER.index(d) if d in DOMAIN_ORDER else len(DOMAIN_ORDER)
    )
    return df.sort_values("_d", kind="stable").drop(columns="_d").reset_index(drop=True)


def curated_keys(path: Path | None = None) -> set[tuple[str, str, str, str]]:
    """(source, indicator, sex, age) for every card, for the posting gate."""
    return set(map(tuple, load_curated(path)[CARD_KEY].values))
