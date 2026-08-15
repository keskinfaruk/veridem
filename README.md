# veridem

Watches official demographic statistics for Türkiye and Europe, detects
substantive changes against its own stored history, and publishes what
changed. *veri* (data) + *demografi*.

## How it works

A scheduled run fetches every watched indicator from TurkStat's press-release
tables and Eurostat daily, and from TurkStat's SDMX Web Service weekly, then
writes each fetch as an immutable Parquet snapshot. Each fresh snapshot is
diffed against the previous one and every difference is classified: a new
period, a revision, a withdrawal, or a new series. A demographic change opens
a pull request carrying a readable change report, and merging it is what
updates the data bank.

Published output has two forms, both limited to a curated list of indicators
for Türkiye (`data/curated_cards.csv`). A new year's figure or a revision to
one posts a single fact to a Bluesky account and an Atom feed, in order, as
history. The same figures also appear as a card set on a static page, one
card per watched series, replaced in place so the page always shows current
values rather than a growing log. A value disappearing from a source never
posts anywhere.

Changes are detected by comparing against this project's own stored history,
never by anything an API declares. A catalogue-level change (TurkStat gaining
or losing a whole dataflow, or a DSD version bump) is a different thing at a
different layer: it is logged privately and never treated as a publishable
event.

## Setup

```
python -m venv .venv
.venv\Scripts\Activate.ps1        # PowerShell
pip install -r requirements.txt
copy .env.example .env            # then fill in TUIK_API_KEY
```

Eurostat and the TurkStat press-release connector need no credentials.
TurkStat's SDMX connector needs an API key from the TurkStat Data Portal
(`https://giris.tuik.gov.tr`), SMS-verified against a Turkish mobile number.
Posting to Bluesky needs an App Password; neither fetching nor change
detection needs it.

## Usage

Fetch every indicator currently listed in `data/indicator_map.csv`:

```
python src/fetch_tuik_indicators.py
python src/fetch_eurostat_indicators.py
python src/fetch_tuik_press_indicators.py
```

Query everything fetched so far with DuckDB, regardless of source:

```python
from schema import connect

con = connect()
con.execute(
    "SELECT source, ref_area, time_period, obs_value "
    "FROM observations WHERE indicator = 'TFR' ORDER BY time_period"
).df()
```

Run the full daily pipeline (fetch, diff, report) locally:

```
python src/daily_run.py
```

## Data sources and attribution

All demographic data in this repository originates from official statistical
institutes and is reused under their respective public data-reuse policies,
retrieved programmatically rather than authored by this project:

- **TurkStat (Türkiye İstatistik Kurumu / TÜİK)**: SDMX Web Service
  (`source='tuik'`) and press-release tables (`source='tuik_press'`). TÜİK's
  [Legal Notice](https://www.tuik.gov.tr/Kurumsal/Yasal_Uyari) permits reuse
  of data from its website, publications, and databases without prior
  authorization provided the source is cited, which this note is. TÜİK holds
  copyright in the underlying data; this project claims none.
- **Eurostat** (`source='eurostat'`): reuse is authorised for personal,
  non-commercial, and commercial purposes provided Eurostat is acknowledged
  as the source, per [Commission Decision 2011/833/EU](https://ec.europa.eu/eurostat/en/help/copyright-notice)
  and Eurostat's own copyright notice. © European Union.

If you reuse data from this repository, cite the original institute
(TurkStat or Eurostat, per the `source` column) as well as this repository.

## License

This project's own code is licensed under the MIT License: see
[`LICENSE`](LICENSE). That license covers the code only, not the underlying
statistical data; see *Data sources and attribution* above for the terms that
apply to the data itself.

## Citing this repository

See [`CITATION.cff`](CITATION.cff) for machine-readable citation metadata
(GitHub renders a "Cite this repository" option from it).
