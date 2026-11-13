# Sourced FX strategy research (compiled 2026-11-13 JST)

## Research conclusion

**Do not promise a positive-profit strategy from an AI confidence threshold alone.** The experimental candidate combines trend agreement, market-regime confirmation, event windows, cost limits and hard risk gates. Results on real USDJPY quotes remain **unverified**. These are falsifiable hypotheses, not expected investment returns.

## Relevant empirical and official sources

| Source / date | Confirmed content | Implication / caution |
|---|---|---|
| Moskowitz, Ooi & Pedersen, *Time Series Momentum*, Journal of Financial Economics, May 2012, https://doi.org/10.1016/j.jfineco.2011.11.003 | Time-series trend persistence was measured in 58 futures/forward markets including currencies over **1–12 month** horizons. | A research rationale for trend-following; **not proof of H1 USDJPY strategy profitability**. |
| Menkhoff et al., *Currency momentum strategies*, Journal of Financial Economics, 2012, https://www.sciencedirect.com/science/article/pii/S0304405X12001353 | Currency momentum returns are materially sensitive to bid/ask transaction costs. | Explicit spread/slippage stress testing is essential. |
| Filippou et al., *Importance of transaction costs for asset allocation in foreign exchange markets*, Journal of Financial Economics, **September 2024**, https://www.sciencedirect.com/science/article/pii/S0304405X24001090 | Transaction costs can erase strategy profitability, especially with large trade sizes/price impact. | Require post-cost evidence, not merely signal accuracy. |
| Brunnermeier, Nagel & Pedersen, *Carry Trades and Currency Crashes*, NBER working paper, November 2008; revised December 2013, https://www.nber.org/papers/w14473 | Carry trading is exposed to crash risk in liquidity squeezes. | Avoid assuming rate differentials always generate low-risk returns. |
| FRB, FOMC calendars for **2026**, https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm | Official meeting calendar and statement links. | Use for manually verified event blackout (not auto-ingested yet). |
| Bank of Japan, monetary policy meetings **2026**, https://www.boj.or.jp/en/mopo/mpmsche_minu/ | Official policy decision meeting schedule and materials. | Use for manually verified event blackout (not auto-ingested yet). |
| US Bureau of Labor Statistics, **2026** release calendar, https://www.bls.gov/schedule/2026/home.htm; iCal https://www.bls.gov/schedule/news_release/bls.ics | Calendar with scheduled US statistics and time zone stated as Eastern. | The research agent parses the iCal schedule and stamps retrieval time. |
| CFTC, COT reports and release schedule for **2026**, https://www.cftc.gov/MarketReports/CommitmentsofTraders/index.htm | Position report generally represents Tuesday's positions and is released Friday 3:30pm ET; holiday delays possible. | A Tuesday observation must **not** be available to a backtest on Wednesday or Thursday. |
| FRED/ALFRED API, https://fred.stlouisfed.org/docs/api/fred/realtime_period.html | ALFRED real-time periods track information historically known before later revisions. | Use vintage-aware historical macro factors. |
| MetaQuotes, Python bridge for MT5, https://www.mql5.com/en/docs/python_metatrader5 | MT5 can provide price data and order API. | Execution implementation is intentionally excluded until validation. |
| Japanese Financial Services Agency, FX risks, https://www.fsa.go.jp/ordinary/iwagai/ | Trading involves material loss and platform/market risks. | Risk limits are imperfect; outages can prevent execution. |

## Proposed initial candidate, operationally precise

At completion of hourly USDJPY bar `t`:

1. Allow BUY only if **both** separately fitted direction classifiers estimate P(up at next open-to-open)>=0.60. SELL only if both <=0.40. Classifier probability is **not** stop/target win probability.
2. Allow BUY only if `close[t] > EMA200[t]` and `EMA50[t] > EMA200[t]`; SELL uses both inverse inequalities.
3. Require `ADX14[t] >= 20`; avoid weak trend/sideways regimes.
4. Forecast magnitude from the volatility agent must be at least **3x** estimated complete round-trip spread/slippage; this is **not** the same as expected signed profit.
5. Indicative spread at signal time must be <= **1.5 pips** AND <= **15% of ATR(14)** (ATR in USDJPY pips). Actual spread at entry can differ.
6. Reject new entries from 60 minutes before through 30 minutes after known high-impact releases, but only where the calendar was already available at `t`.
7. Fixed 1,000 USD example size, maximum notional 50% of virtual equity; realized-equity drawdown limit 10%, realized same-UTC-day loss limit 2%.
8. Exit position at the open of the following bar. This is a **temporary mechanical research exit**, not yet an optimized signal, stop-loss or reward/risk policy.

**All thresholds are provisional.** Do not change them after reviewing a final holdout period and still call it unused holdout.

## What does 'positive expectancy' actually require?

Using a hypothetical fixed stop/target example (NOT implemented by the present one-bar engine):

- stop loss = 15 pips, take profit = 30 pips, all-in costs = 1.4 pips per round-trip.
- if trade wins with probability `p`, simplified expected pips per trade are `p * 30 - (1-p) * 15 - 1.4`.
- break-even probability is `(15+1.4)/(30+15)=0.36444...`, or **36.45%**.

The win probability must be **independently observed and robust** for **that exact stop/target/execution scheme**, not borrowed from model P(up). This simplified calculation excludes price gaps, partial fills, commissions, swaps and stop execution variance. Choosing a profitable-looking stop/target combination on historical data is not forward evidence.

### Proposed next research phase (NOT implemented here)

Test two distinct strategies on **real** historical bid/ask USDJPY series, with realistic intrabar order execution: (A) H4/D1 multi-week trend following using 50/200-period EMA or 20/55-day breakout, ATR-based sizing and stop/exit; (B) H1 AI direction with regime and news veto, current candidate. Treat the 2012 academic evidence as motivation for **A**, not **B**. Apply cost stress, purged chronological walk-forward selection, unseen final test, regime-by-regime score, and weeks of paper forward testing before any demo broker connector.
