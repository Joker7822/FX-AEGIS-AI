# USD/JPY Historical Storage

This directory is for **official FRED DEXJPUS daily reference observations** only. It is not a brokerage execution-price feed.

On its first successful GitHub Actions run, `github_pipeline.py refresh` writes:

- `dexjpus_daily.csv`: `date,close`, only observed NY-noon reference rates, starting 2006-11-13.
- `metadata.json`: retrieved UTC timestamp, source URL, source payload SHA-256 and CSV SHA-256, first/last dates and row count.

The scheduled workflow commits updates to the main branch so **Git history** retains previous snapshots. There are no hourly candles or fabricated spreads in this dataset.

The site rebuild writes `docs/data/research.json` for a static public dashboard. It is publicly visible. Never commit broker API tokens, account details, restricted data, or secrets.

Official source: https://fred.stlouisfed.org/series/DEXJPUS
