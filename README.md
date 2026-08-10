# veridem

Watches official demographic statistics for Türkiye and Europe, detects
substantive changes against its own stored history, and publishes what
changed. *veri* (data) + *demografi*.

## What's here

- **TurkStat SDMX connector** (`src/tuik_client.py`, `src/series_key.py`) —
  TurkStat's SDMX 2.1 Web Service requires a Keycloak-issued access token per
  request; series keys are built programmatically from each dataflow's live
  Data Structure Definition rather than hardcoded, since dimension order
  varies between dataflows and can change between DSD versions.
- **Eurostat connector** (`src/eurostat_client.py`) — no authentication
  required; fetches `SDMX-CSV` directly.
- **TurkStat press-release connector** (`src/tuik_press_client.py`,
  `src/fetch_tuik_press_indicators.py`) — a second TurkStat data path,
  covering mortality and internal migration figures TurkStat doesn't
  publish via SDMX at all, plus fresher fertility figures than the SDMX
  service currently has.
- **A shared observations schema** (`src/schema.py`) that every connector
  normalizes into, so any indicator from any source can be queried the
  same way regardless of where it came from.
- **Immutable snapshot storage** (`src/snapshot.py`) — every fetch run
  writes a new, never-overwritten file; nothing is ever updated or deleted
  in place, so revision history falls out for free.
- **Indicator-map-driven fetch pipelines** (`src/fetch_tuik_indicators.py`,
  `src/fetch_eurostat_indicators.py`) — which indicators get fetched, from
  which dataflow, with which code, is data in `data/indicator_map.csv`, not
  hardcoded per script.
- **Change detection** (`src/diff.py`, `src/dataflow_inventory.py`) —
  classifies every difference between two snapshots (new period, revision,
  withdrawal, new series, new dataflow, structural change) by comparing
  against this project's own history, not anything an API declares.
- **Sanity checks** (`src/sanity.py`) — plausible-range and year-on-year
  volatility checks per indicator, the demographic judgement a raw diff
  can't provide on its own.
- **Change reports** (`src/report.py`) — turns a set of detected changes
  into a readable report, e.g. `NEW: Total Fertility Rate, Türkiye, 2026 —
  Value 1.42, Previous 1.48, Change -0.06 (-4.1%)`, with trend context and
  a short recent-series table.
- **Instant notifications** (`src/instant_notice.py`, `src/feed.py`,
  `src/bluesky_client.py`) — a condensed, fact-only rendering of the same
  changes, published to an Atom feed and a Bluesky account the moment a
  Türkiye figure changes.
- **Daily automation** (`src/daily_run.py`, `.github/workflows/daily.yml`)
  — a scheduled run that fetches every source, diffs it, and opens a pull
  request only when something substantive changed. Silent otherwise.

## Data currently in the bank

**TurkStat SDMX — Türkiye, national:**
- Total fertility rate (TFR), age-specific fertility rate (ASFR)
- Mean age of mother at childbearing, mean age at first marriage (by sex)
- Crude birth rate

**TurkStat press releases — Türkiye, national:**
- Total live births, adolescent fertility rate, mean age at first birth
- Crude death rate, total/infant/under-five/neonatal/post-neonatal deaths
  and rates (by sex)
- Internal migration volume and rate (by sex)

**Eurostat — every country/aggregate each dataflow publishes (Europe: ~57–60
geos, including EU/EFTA/euro-area aggregates):**
- Total fertility rate (TFR), mean age of mother at childbearing
- Crude birth rate, crude death rate
- Rate of natural population change, rate of total population change
- Population on 1 January
- Life expectancy at birth, at 15, and at 65 (by sex), infant mortality rate

Eurostat's demography data covers Europe only — there is no worldwide
aggregate in any of these dataflows.

## Setup

```
python -m venv .venv
.venv\Scripts\Activate.ps1        # PowerShell
pip install -r requirements.txt
copy .env.example .env            # then fill in TUIK_API_KEY
```

Eurostat's connector and the TurkStat press-release connector need no
credentials. TurkStat's SDMX connector needs an API key from the TurkStat
Data Portal (`https://giris.tuik.gov.tr`), SMS-verified against a Turkish
mobile number. Publishing to Bluesky needs an App Password for the
account you're posting from (`BLUESKY_DATA_HANDLE` /
`BLUESKY_DATA_APP_PASSWORD`); neither fetching nor change detection needs it.

## Usage

Fetch/backfill every indicator currently in `data/indicator_map.csv`:

```
python src/fetch_tuik_indicators.py
python src/fetch_eurostat_indicators.py
python src/fetch_tuik_press_indicators.py
```

Each run writes one immutable parquet file per dataflow under
`data/raw/{source}/{date}/`. Query everything that's been fetched so far
with DuckDB, regardless of source:

```python
from schema import connect

con = connect()
con.execute(
    "SELECT source, ref_area, time_period, obs_value "
    "FROM observations WHERE indicator = 'TFR' ORDER BY time_period"
).df()
```

Run the full daily pipeline (fetch every source, diff against the last
run, print a change report) locally:

```
python src/daily_run.py
```

## Repo layout

```
data/
  indicator_map.csv              # source code -> normalized indicator, per dataflow
  raw/{source}/{date}/*.parquet  # immutable snapshots, one file per fetch run
  inventory/tuik/{date}/         # TurkStat dataflow-catalogue snapshots
src/
  tuik_client.py                 # TurkStat SDMX auth + request client, with retry/backoff
  eurostat_client.py             # Eurostat SDMX client
  tuik_press_client.py           # TurkStat press-release API client
  series_key.py                  # builds TurkStat series keys from a live DSD
  schema.py                      # observations schema + DuckDB view
  snapshot.py                    # immutable snapshot writer
  fetch_tuik_indicators.py       # generic TurkStat SDMX fetch pipeline
  fetch_eurostat_indicators.py   # generic Eurostat fetch pipeline
  fetch_tuik_press_indicators.py # TurkStat press-release fetch pipeline
  fetch_tfr.py                   # minimal single-indicator reference fetch
  dataflow_inventory.py          # TurkStat dataflow-catalogue snapshot + diff
  diff.py                        # observation-level change detection
  sanity.py                      # plausible-range / volatility checks
  report.py                      # change report generator
  instant_notice.py              # condensed fact-only notice text
  baseline_notice.py             # one-time backfill notices for existing data
  feed.py                        # Atom feed of instant notices
  bluesky_client.py              # minimal AT Protocol client
  sync_webpage.py                # syncs the feed to a public static site
  daily_run.py                   # daily pipeline entry point
  post_baseline_notice.py        # drains the baseline-notice queue
  milestone_dataflow_count.py    # counts how many dataflows TurkStat's SDMX service publishes
```

## Design notes

- **`observations` is a DuckDB view, not a loaded copy.** It's computed live
  over `data/raw/**/*.parquet` on every connect, so a query is always
  current with whatever snapshot files exist on disk — there's no separate
  load or sync step to forget.
- **Snapshots are append-only.** A re-run never overwrites a previous
  snapshot file; `write_snapshot()` raises if the target path already
  exists. A revision to a previously-published value is a *new row* in a
  *new* snapshot, never an edit to an old one.
- **`other_dims` is stored as a JSON string, not a parquet struct**, so
  files from dataflows with different extra dimensions can be read back
  together.
- **`source` is part of a series' identity.** TurkStat's and Eurostat's
  figures for the same country and indicator are never averaged or merged
  — they're stored as separate rows (different `source` values) precisely
  so they can be compared side by side without conflating them. The same
  applies between TurkStat's SDMX service and its press releases
  (`source='tuik'` vs. `source='tuik_press'`): a press figure can predate
  the SDMX service's own administrative revisions for the same period.
- **Substantive change vs. technical change.** A new data point or a
  revised value is detected by comparing against this project's own
  history, never by anything an API declares — SDMX version numbers track
  structure, not content, and don't change when a dataflow simply gains a
  new year's data.

## Data sources & attribution

All demographic data in this repository originates from official
statistical institutes and is reused here under their respective public
data-reuse policies, retrieved programmatically rather than authored by
this project:

- **TurkStat (Türkiye İstatistik Kurumu / TÜİK)** — SDMX Web Service
  (`source='tuik'`) and press-release tables (`source='tuik_press'`).
  TÜİK's own [Legal Notice](https://www.tuik.gov.tr/Kurumsal/Yasal_Uyari)
  permits reuse of data from its website, publications, and databases
  without prior authorization, provided the source is cited — which this
  note is that citation. TÜİK holds copyright in the underlying data;
  this project claims none.
- **Eurostat** (`source='eurostat'`) — reuse of Eurostat data is authorised
  for personal, non-commercial, and commercial purposes provided Eurostat
  is acknowledged as the source, per [Commission Decision 2011/833/EU](https://ec.europa.eu/eurostat/en/help/copyright-notice)
  and Eurostat's own copyright notice. © European Union.

If you reuse data from this repository, cite the original institute
(TurkStat or Eurostat, per the `source` column) as well as this repository.

## License

This project's own code is licensed under the MIT License — see
[`LICENSE`](LICENSE). That license covers the code only, not the
underlying statistical data; see *Data sources & attribution* above for
the terms that apply to the data itself.

## Citing this repository

If you use this project or its data pipeline in your own work, please
cite it — see [`CITATION.cff`](CITATION.cff) for machine-readable citation
metadata (GitHub renders a "Cite this repository" option from this file).
