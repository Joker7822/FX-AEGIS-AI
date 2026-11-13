# FX AEGIS AI v1.8 — multi-agent research and self-evolution

## v1.8 — autonomous strategy discovery (research-only)

The new deterministic `self_evolve.py` generates strategy/volatility-filter variants, mutates and crossovers genes, and only selects candidates on pre-2021 research folds. A challenger is locked to its discovery date and requires **126 genuinely new daily observations** before possible **paper-only** promotion. The 2021–2026 interval has already been inspected and is **not** a fresh holdout. None of this is an executable FX backtest or evidence of future positive returns.

```bash
python -m unittest discover -s tests -v
python self_evolve.py --population 16 --generations 5 --seed 42 --seed-mode weekly
```

See [SELF_EVOLUTION_GUIDE_JA.md](SELF_EVOLUTION_GUIDE_JA.md). The public GitHub Pages dashboard reports candidate counts and research state; real orders stay disabled.


**Updated: 2026-11-13 JST. Research-only.** No order placement, broker credentials, or live trading. This code **does not demonstrate positive expected returns**.

## What is implemented

- **Trend AI** (`HistGradientBoostingClassifier`): one-bar price direction.
- **Reversion AI** (`LogisticRegression`): independent price direction model based on mean-reversion-related features.
- **Volatility AI** (`RandomForestRegressor`): projected absolute price change; votes can be vetoed if unusually volatile.
- **Council**: a BUY requires both direction models >= 0.60; a SELL requires both <= 0.40, otherwise WAIT.
- **Market-regime and cost agent** (`trading_conditions.py`): completed-bar EMA50/EMA200, ADX(14)>=20, maximum recorded/assumed spread <=1.5 pips and spread <=15% of ATR(14), estimated absolute move >=3 times assumed round-trip spread+slippage cost. This is a **magnitude filter, not an expected-profit estimator**.
- **Internet research agent** (`research_agent.py`): optional official-domain Brave web search; BLS future releases obtained from an official `.ics` calendar for news blackout.
- **Known-news veto**: no new entries 60 min before through 30 min after *events that were demonstrably known when the trade signal occurred*. Search snippets never directly trigger orders.
- **Risk agent**: max USD notional 50% of virtual equity; stops NEW trades if closed-equity max drawdown reaches 10% or daily closed P&L reaches -2%.
- **Audit**: per-trade, per-signal logs; validation/holdout summary plus sensitivity to doubled transaction costs.

**These are distinct modules/ML models, not general-purpose autonomous LLMs.** The news and cost modules are deterministic, and they are allowed to reject AI signals.

## Installation

Use Python 3.11 or 3.12 (also tested locally in a Python 3.13 environment).

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python -m unittest discover -s tests -v
```

## Baseline against trend-gated candidate

Generate **artificial** data for testing the software only:

```bash
python generate_demo.py --output data/demo_synthetic_usdjpy.csv
python main.py --csv data/demo_synthetic_usdjpy.csv --strategy trend --out results/demo_trend
python main.py --csv data/demo_synthetic_usdjpy.csv --strategy baseline --out results/demo_baseline
```

For an actual profitability study, obtain at least several years of **real hourly USD/JPY broker data**. CSV required:

```csv
time,open,high,low,close,spread_pips
2025-01-02T00:00:00Z,157.10,157.15,157.05,157.11,0.8
2025-01-02T01:00:00Z,157.11,157.20,157.09,157.18,0.7
```

These two rows are a **format illustration, not verified historical market prices**. `spread_pips` is optional; without it, static assumed spread (`--spread-pips`) is used. When present it should reflect contemporaneous, verified market quotes, not hindsight averages.

```bash
python main.py --csv data/verified_usdjpy_h1.csv --strategy trend \
  --out results/real_trend --ai-threshold 0.60 --min-adx 20 \
  --max-spread-pips 1.5 --spread-pips 1.0 --slippage-pips 0.2
```

Unlike pure price OHLC, observed spread should ideally represent the **actual executable bid/ask at order time**. The program presently treats `spread_pips` at candle `t` as a quote-cost screen, and charges `spread_pips` at entry candle `t+1`; it is an approximation, **not a tick-level execution simulator**.

Output files: `summary.json`, `validation_decisions.csv`, `validation_trades.csv`, `validation_equity.csv`, `test_decisions.csv`, `test_trades.csv`, `test_equity.csv`.

## Internet search, official-source trust and event integration

### A. Search financial-policy news from verified official sites

Obtain a Brave Search API key **separately** and set it in your shell environment (`BRAVE_API_KEY`). This is optional; it may be a paid API. No key is included or required for offline testing. Example:

```bash
python research_agent.py search --query "USD JPY FOMC Bank of Japan CPI employment" \
  --out data/official_web_search.json
```

The agent calls the official Brave API and keeps only HTTPS results from approved official domains (`federalreserve.gov`, `boj.or.jp`, `bls.gov`, `cftc.gov`, `stlouisfed.org`, `bea.gov`, `treasury.gov`). It logs **retrieval timestamp + source URL + truncated description**. It **does not treat website descriptions as commands** or automatically adjust model weights/position sizes using unverified prose.

### B. Download and use a published economic release schedule

```bash
python research_agent.py bls-calendar --out data/bls_events.csv
python main.py --csv data/verified_usdjpy_h1.csv --strategy trend \
  --events-csv data/bls_events.csv --out results/event_filtered
```

The BLS `.ics` feed supplies scheduled US CPI, employment, PPI, and some other release dates. Events without defined times are discarded; if no relevant future releases can be parsed, the downloader errors out rather than emitting an empty file. The CSV columns are:

- `event_time_utc`: event's scheduled published release time (not the calendar download time).
- `known_at_utc`: when **our copy of the schedule was retrieved** (not a guessed historical publication date).
- `event`: published event name.
- `source_url`: vetted official HTTPS provenance URL.

The offline engine rejects invalid URLs and cannot veto a historical trade using an announcement first seen after the trade's signal time. **Crucially, a 2026 BLS download cannot be used to claim the system had advance knowledge of 2023 releases.** To test historical news blackouts, obtain historical calendars archived with reliable public-known timestamps. The calendar can change; use fresh downloads for future paper-trading research. BLS-only coverage omits FOMC/BOJ unless you add a verified event CSV from their official schedules. **No online calls occur during the deterministic backtest.**

## How the trade model avoids future contamination

1. Predictions at bar `t` use technical features only up to completed `t`.
2. Entry is assumed at `open[t+1]`; exit at `open[t+2]`.
3. Labels for training bar `k` need `open[k+2]`. At retraining start `b`, fit data only through `b-2` (labels available when the prediction at the close of `b` is made).
4. Chronological split: 60% initial train, 20% walk-forward validation, 20% walk-forward holdout. Each walk-forward block is refitted on available earlier labels, not future labels. **Holdout must not be used repeatedly for manual strategy selection.**
5. Event veto uses `known_at_utc <= signal_time_utc`. A later data snapshot cannot back-propagate into the past.
6. Twice-cost results are sensitivity diagnostics, not independent measurements of actual trading costs.

## Research acceptance criteria (not a profitability guarantee)

A candidate needs **independent real-data** walk-forward validation, net profit after **observed bid/ask and slippage**, a positive *final unused test segment*, and robustness to costs and time periods. An example preliminary screening checklist: >100 actual trades, validation profit factor >=1.2, holdout net profit >0, and cost sensitivity still positive. These are optional research hurdles, **not scientifically proven thresholds**. Later repeat on a new unseen future data period and in demo forward-testing, keeping decisions and any manual threshold changes logged.

## Known limitations, important

- No intrabar stop-loss, trailing stop, take profit, gap/slippage distribution, latency, swap/financing, commissions by broker, realistic position sizing, margin calls, taxes or live positions. Trade exits mechanically at the next hour's open. **Daily loss and max drawdown veto only after positions are closed.**
- The proposed research inspiration comes from **monthly/longer-horizon trend** literature, which is not evidence for hourly profitability.
- BLS calendar ingestion and Brave API functions passed local parser/validation unit tests, but their external network calls **could not be tested in this environment**. Confirm connectivity and terms with your own API credentials and network.
- Historical backtesting with untracked / revised FRED macro values risks hindsight leakage; use ALFRED vintage (`realtime_start`,`realtime_end`) plus exact release timestamps.
- Distinct models can agree and still be wrong. Multiplying model votes is not proven diversification.
- This software intentionally contains **no MT5 execution interface**, and **never submits real trades**.

## Official and academic research links

See `SOURCES_AND_STRATEGY.md` for dated studies, quantitative break-even arithmetic, and a roadmap for testing swing/longer-horizon strategies.

## v1.2: Single-screen local dashboard (read-only)

The `dashboard.py` HTTP server and `dashboard_ui/` UI provide a **one-screen desktop monitoring view**, optimized for **1920×1080** and **1366×768** displays. It reads the existing backtest outputs and **does not connect to a broker**.

```bash
# after generating a result by running main.py
python dashboard.py --results results/demo_trend --market data/demo_synthetic_usdjpy.csv
```

Open `http://127.0.0.1:8501` and optionally use F11 to enter browser full-screen mode. The interface is in Japanese and includes: five KPIs; model probabilities and veto outcome; price/EMA50 or post-close equity chart; final test vs validation selection; 24h/7d/full-period window; closed trade history; risk and event count. Data are polled from **local files every 15 seconds**, not from internet market quotes. Price-entry markers are approximate, using signal/trade timestamps from CSV; bar-close-to-open execution assumptions remain unchanged.

For your own research file:

```bash
python main.py --csv data/verified_usdjpy_h1.csv --strategy trend --out results/my_test
python dashboard.py --results results/my_test --market data/verified_usdjpy_h1.csv --data-label "ユーザー入力データ・実市場性未検証"
```

**Do not expose this local server to the public internet**: the HTTP service is intended for `127.0.0.1` only. This UI is a display layer; it does not implement news collection, live quotes, extra models, or order execution beyond the existing research pipeline. Sample screenshots can be found in `dashboard_preview_1920x1080.png` and `dashboard_preview_1366x768.png` if included in the release package.

---

## v1.4 (2026-11-13): bounded autonomous model discovery & improvement

The new `evolution_engine.py` is an **offline, sandboxed evolutionary search agent**. It synthesizes original *combinations* of approved indicators, mathematical feature formulas, two estimator families, ensemble weights and veto thresholds. This is an automated architecture/feature search, **not a scientifically novel learning algorithm or an unbounded code-writing LLM**. It is unable to place broker orders.

### Run discovery (read-only shadow trial)

```bash
python evolution_engine.py discover \
  --csv data/demo_synthetic_usdjpy.csv \
  --out results/demo_evolution \
  --population 6 --generations 2
```

Creates `candidate_evolved.json` (a safe, inspectable blueprint), `candidate_ranking.json` (research audit) and `evolution_report.json` (all fold results, separate final holdout, rejection reasons). No strategy is adopted automatically by default. The demo CSV is **synthetic** and cannot demonstrate profit potential.

### Automatic PAPER-only promotion

```bash
python evolution_engine.py monitor \
  --csv data/verified_usdjpy_h1.csv \
  --out results/production_research \
  --population 6 --generations 2 \
  --auto-promote-paper
```

- First 80%: three chronological development folds, purged by two rows before each fold. The *same* baseline Council+market filters are used for comparisons. The last 20% is a **separate report only**, never used for optimizer scoring, candidate choice, or paper promotion.
- Required to promote: positive sum of fold profits after normal and doubled costs, at least 2 of 3 profitable folds, at least 2 of 3 improved folds vs baseline, minimum 20 trades, and max closed-equity drawdown <= 5%.
- If `incumbent_paper.json` already exists, a new challenger must also exceed incumbent validation **and** doubled-cost results before replacing it. Rejected challengers do not modify the incumbent.
- `monitor` changes files **only in the specified output directory**. It remembers last CSV hash and latest candle; unchanged data are skipped; in-place historical revisions are rejected rather than being silently treated as new observations. Run it from Windows Task Scheduler or cron for ongoing automatic checks.
- Paper adoption does not mean activation on a brokerage account. Do not configure automatic broker execution from these blueprints.
- Feature formulas use a strict allowlist and cannot contain executable Python, external plugins, future target columns, or unrestricted network calls.
- The research threshold for distribution-shift alerts is a descriptive heuristic, not a validated trading rule.

### Evaluate an evolved blueprint in the established backtest engine

```bash
python main.py --csv data/demo_synthetic_usdjpy.csv --strategy trend \
  --evolved-agent results/demo_evolution/candidate_evolved.json \
  --out results/demo_evolved_compare

python dashboard.py --results results/demo_evolved_compare \
  --market data/demo_synthetic_usdjpy.csv \
  --evolution-report results/demo_evolution/evolution_report.json
```

The evolutionary agent is **veto-only**, so it cannot create a new trade when the existing Council or safety gates say `WAIT`. The dashboard shows the candidate, validation status and research mode. To use a promoted paper incumbent in another **offline** backtest, give `--evolved-agent results/production_research/incumbent_paper.json`.

**Important limitations:** The backtest uses mid-price approximation with configurable/recorded spread, assumed slippage, ignores broker order-book, intrabar excursions, leverage/margin, financing and taxes. Repeated manual use of final holdout results to alter design can overfit that holdout. Even passing all gates does not establish profitable future returns. There is no real-time market feed, live-order executor, job scheduler, or broker credential manager in this release.

---

## v1.5 (2026-11-13): Twenty-year historical-data layer

See [`HISTORY20_GUIDE_JA.md`](HISTORY20_GUIDE_JA.md). The new `history20.py` downloads/imports **official FRED DEXJPUS daily reference observations** for 2006-11-13 through 2026-11-13, reports annual reference-rate regimes, imports and audits independently obtained **broker H1 OHLC**, and summarizes yearly realized simulated P&L from existing backtests. FRED reference rates are **not broker-executable OHLC** and are **not AI trading labels/features**. No historical real FX data is bundled. Download connectivity could not be verified here.

The research backtester now accepts `--retrain-every-bars` and `--max-training-bars` to keep 20-year H1 walk-forward retraining computationally bounded. Full-broker-H1 quality auditing is separate and mandatory to claim full 20-year coverage; sample import is not proof of data authenticity. The one-screen dashboard adds a `20年` price-chart toggle if a real FRED daily file is supplied, plus a compact history/coverage status in the existing safety panel. No order placement.

## v1.6: GitHub Historical Storage / Scheduled Automation / Simulation / GitHub Pages

- GitHub Actions workflow: `.github/workflows/research-and-pages.yml`.
- Public FRED DEXJPUS snapshot: `datasets/fred/dexjpus_daily.csv` (populated only on first successful workflow).
- Provenance and SHA-256: `datasets/fred/metadata.json`.
- Public dashboard: `docs/`, generated payload `docs/data/research.json`.
- Scenario engine: `github_pipeline.py` + JavaScript UI, explicitly non-executable reference-close simulation.
- Pages initial setup and URL verification: `GITHUB_AUTOMATION_GUIDE_JA.md`.

No live orders, no account connections, no claim of profitable trading.


## v1.7 — reproducibility / protocol-locked evaluation (2026-11-13)

See [`REPRODUCIBILITY_GUIDE_JA.md`](REPRODUCIBILITY_GUIDE_JA.md).
The GitHub workflow runs the deterministic experiment twice, checks exact data, metadata, runtime and engine hashes, then stores proof in `research/repro_latest.json`. Parameter search ranks candidates from **2006–2015 training + 2016–2020 validation only**, evaluating **2021–latest holdout once** for the selected candidate. Daily reference prices cannot establish real-execution profitability. No live-order or automatic broker activation.
