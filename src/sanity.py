"""
Sanity checks for the change report: the demographic judgement a raw diff
cannot provide on its own.

    - plausible range per indicator, to catch a decimal-point slip or a unit
      mismatch, not to second-guess a real number
    - year-on-year volatility against a series' own history, plus a fixed
      absolute cap for life expectancy

Not implemented, because the underlying data is not in the bank: sex ratio
at birth (no sex-of-infant dimension is populated in any TUIK fertility
dataflow checked so far) and provincial values summing to the national
total (no provincial breakdown fetched yet).

Every check returns (status, message) with status in {"ok", "warn"} and
never raises or blocks anything: these are for the report to display, not a
gate.
"""

import pandas as pd

# Plausible value ranges per normalized indicator, in the indicator's own
# natural unit. Deliberately generous.
PLAUSIBLE_RANGES = {
    "TFR": (0.5, 8.0),  # children per woman
    "ASFR": (0.0, 400.0),  # per 1,000 women in the age group
    "MEAN_AGE_CHILDBEARING": (15.0, 45.0),  # years
    "MEAN_AGE_FIRST_MARRIAGE": (15.0, 45.0),  # years
    "CBR": (0.0, 60.0),  # per 1,000 population
    "CDR": (0.0, 40.0),  # per 1,000 population
    "NATURAL_GROWTH_RATE": (-30.0, 40.0),  # per 1,000 population
    "POP_GROWTH_RATE": (-50.0, 60.0),  # per 1,000 population
    "POP_JAN1": (1_000, 2_000_000_000),  # persons -- wide, just catches unit slips
    "LIFE_EXPECTANCY_BIRTH": (30.0, 95.0),  # years
    "LIFE_EXPECTANCY_15": (20.0, 85.0),  # years
    "LIFE_EXPECTANCY_65": (2.0, 35.0),  # years
    "INFANT_MORTALITY_RATE": (0.0, 200.0),  # per 1,000 live births
    # tuik_press indicators -- same "catch a decimal-point/unit slip, not a
    # real number" intent as the rest of this table.
    "TOTAL_BIRTHS": (300_000, 3_000_000),  # persons
    "ADOLESCENT_FERTILITY_RATE": (0.0, 300.0),  # per 1,000 women aged 15-19
    "MEAN_AGE_FIRST_BIRTH": (15.0, 40.0),  # years
    "TOTAL_DEATHS": (100_000, 2_000_000),  # persons
    "INFANT_DEATHS": (0, 50_000),  # persons
    "UNDER5_MORTALITY_RATE": (0.0, 250.0),  # per 1,000 live births
    "NEONATAL_DEATHS": (0, 50_000),  # persons
    "NEONATAL_MORTALITY_RATE": (0.0, 200.0),  # per 1,000 live births
    "POST_NEONATAL_DEATHS": (0, 50_000),  # persons
    "POST_NEONATAL_MORTALITY_RATE": (0.0, 200.0),  # per 1,000 live births
    "UNDER5_DEATHS": (0, 50_000),  # persons
    "INTERNAL_MIGRATION_VOLUME": (0, 20_000_000),  # persons
    "INTERNAL_MIGRATION_RATE": (0.0, 20.0),  # percent of population
    "TOTAL_POPULATION": (50_000_000, 150_000_000),  # persons -- wide, just catches unit slips
    "NUMBER_OF_MARRIAGES": (100_000, 2_000_000),  # persons
    "CRUDE_MARRIAGE_RATE": (0.0, 30.0),  # per 1,000 population
    "NUMBER_OF_DIVORCES": (10_000, 500_000),  # persons
    "CRUDE_DIVORCE_RATE": (0.0, 10.0),  # per 1,000 population
    "IMMIGRANTS": (0, 5_000_000),  # persons
    "EMIGRANTS": (0, 5_000_000),  # persons
    "HEALTHY_LIFE_YEARS": (0.0, 90.0),  # years
}

# Indicators where any year-on-year move bigger than this absolute amount is
# flagged regardless of the series' own historical volatility.
MAX_PLAUSIBLE_YOY_DELTA = {
    "LIFE_EXPECTANCY_BIRTH": 1.0,
    "LIFE_EXPECTANCY_15": 1.0,
    "LIFE_EXPECTANCY_65": 1.0,
}

# Generic volatility check for every other indicator: flag a move bigger
# than this many standard deviations of the series' own prior year-on-year
# changes. Deliberately loose -- meant to catch a data error, not flag
# ordinary demographic movement.
VOLATILITY_STDEV_MULTIPLIER = 4.0
MIN_HISTORY_FOR_VOLATILITY_CHECK = 5  # fewer prior deltas than this -> too little history to judge


def check_plausible_range(indicator: str, value: float) -> tuple[str, str]:
    bounds = PLAUSIBLE_RANGES.get(indicator)
    if bounds is None:
        return "warn", f"no plausible range configured for {indicator} -- add one to sanity.py"
    lo, hi = bounds
    if lo <= value <= hi:
        return "ok", "within plausible range for the indicator"
    return "warn", f"{value} is outside the plausible range [{lo}, {hi}] for {indicator}"


def check_yoy_volatility(indicator: str, history: pd.Series) -> tuple[str, str]:
    """`history` is a series of obs_value ordered by time_period, ending
    with the new/revised value being checked."""
    if len(history) < 2:
        return "ok", "not enough history to check year-on-year volatility"

    latest_delta = history.iloc[-1] - history.iloc[-2]

    abs_cap = MAX_PLAUSIBLE_YOY_DELTA.get(indicator)
    if abs_cap is not None and abs(latest_delta) > abs_cap:
        return (
            "warn",
            f"year-on-year change of {latest_delta:+.2f} exceeds the "
            f"{abs_cap:g} plausible cap for {indicator}",
        )

    prior_deltas = history.diff().dropna()
    if len(prior_deltas) <= MIN_HISTORY_FOR_VOLATILITY_CHECK:
        return "ok", "year-on-year change within historical volatility (limited history)"

    stdev = prior_deltas.iloc[:-1].std()
    # Also require the move to exceed the largest the series has ever made,
    # not just N x its own stdev: a smooth series can have a stdev so tiny
    # that an ordinary move trips the multiplier alone. The multiplier stays
    # the primary, per-series-adaptive signal; this is a floor under it.
    prior_max_abs = prior_deltas.iloc[:-1].abs().max()
    if stdev and abs(latest_delta) > VOLATILITY_STDEV_MULTIPLIER * stdev and abs(latest_delta) > prior_max_abs:
        return (
            "warn",
            f"year-on-year change of {latest_delta:+.2f} is "
            f"{abs(latest_delta) / stdev:.1f}x the series' own historical volatility "
            f"and exceeds every prior year-on-year move (largest so far: {prior_max_abs:+.2f})",
        )
    return "ok", "year-on-year change within historical volatility"


def run_checks(indicator: str, value: float, history: pd.Series) -> list[tuple[str, str]]:
    """Run every applicable check for one new/revised observation. `history`
    should already include the new value as its final point."""
    return [
        check_plausible_range(indicator, value),
        check_yoy_volatility(indicator, history),
    ]
