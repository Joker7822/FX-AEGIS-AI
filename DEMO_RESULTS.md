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


## v1.4 自律進化AI 動作確認（2026-11-13）

`python evolution_engine.py discover --csv data/demo_synthetic_usdjpy.csv --out results/demo_evolution --population 4 --generations 2 --seed 42 --min-trades 10` の結果：

- 探索した異なる構造: 7候補
- 選択された候補の開発用3期間合計純損益: **+100.54円**
- 同条件の既存AI: **-359.77円**
- 取引コスト2倍の合計純損益: **-151.46円**
- 利益プラスだった開発用期間: **3期間中1期間**
- **自動採用: 拒否**（ストレス損益マイナス、期間の一貫性が不足）
- 最終20%の独立区間（参照用）: **+39.92円、1取引**。選抜・採用の判断には使用しません。

これらはすべて**人工生成データ**に基づくソフトウェア動作確認であり、FX実市場での収益性は一切示しません。
