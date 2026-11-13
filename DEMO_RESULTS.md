# Software smoke-test results: synthetic OHLC, NOT real FX market prices

**Run on 2026-11-13 JST**. Generated with `python generate_demo.py` (2,400 synthetic hourly bars whose timestamps span 2023-01-01 through 2023-04-10 UTC, including fake weekend trading). The goal is to check that models, trade filters, expenses, tests and records run.

| Segment | Trades | Net virtual P&L | Virtual return | Profit factor |
|---|---:|---:|---:|---:|
| Validation 20% | 18 | -¥98.25 | -0.0098% | 0.7837 |
| Holdout 20% | 14 | -¥247.61 | -0.0248% | 0.4055 |
| Holdout, 2x-cost stress | 14 | -¥443.61 | -0.0444% | 0.2028 |

Independent virtual accounts each start with ¥1,000,000. **Synthetic results are not evidence either for or against real-trading expected returns.** The data are generated, the trades are few, and no market execution data are included.

**11 unit tests passed**, including original chronology, feature look-ahead, risk limits, cost calculations; additionally known-at news veto, rejection of untrusted domains, cost/trend veto, lag-indicator integrity, iCal fixture parsing and missing-API-key rejection.

**Important:** external BLS iCalendar / Brave API network access was not available for testing here. Those online endpoints are implemented but must be tested on a machine with internet access. No real news calendar file was injected into the 2023 synthetic run. All results above use deterministic local data only.
