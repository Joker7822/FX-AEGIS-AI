# FX AEGIS AI v2.0 — 自己進化AIの過剰最適化審査

更新：2026年11月14日（日本時間）／**研究専用、実注文は行いません。**

## 結論

AI候補を増やすほど、同じ過去データに偶然合う候補も増えます。v2.0では、
**探索した全候補**を同じ参考基準と比較し、複数試行・時系列の依存・過去の再探索履歴を考慮して、
不確かな候補を紙上運用候補に昇格させない審査層を追加しました。

**将来のプラス収益、統計的な優位性、実際の約定可能性は確認できていません。**

## 実装と厳密な限界

| 層 | 実装 | 注意点 |
|---|---|---|
| 学習・選定境界 | 候補の遺伝的探索は2021年より前のデータのみ | 2021〜2026年は過去に閲覧済みで、新鮮な未使用データではない |
| 比較対象 | 事前固定 EMA15 / EMA100、買いのみ、元本25% | この基準にも利益保証はない |
| 候補の評価 | 全候補に2倍の仮定コストを適用し、基準との差を日次対数損益で計算 | FREDのNY正午参考値であってBid/Askやスワップではない |
| 多重比較 | 20観測値のブロック・ブートストラップ、共通再標本化のmax-T、512試行 | 現在の候補集合の探索バイアスに対する**探索的**補正。非定常性や全週の事後選択は完全には補正できない |
| 採用禁止条件 | 候補の監査欠落、過去週の未補正探索、新基準に未到達 | すべて失敗時は自動で `NO_STATISTICALLY_SCREENED_CANDIDATE` |
| 順位の不安定性 | 8連続ブロックを半分ずつ70通り選び、試験ブロックの順位劣化を報告 | CSCVに着想を得た説明指標であり**正式PBOでも将来損失確率でもない** |
| 時系列の前進検証 | 事前固定の4戦略から過去ブロックだけで1つ選び、次のブロックを計算 | 比較用の研究指標。進化モデル自体の独立OOS検証と誤認しない |
| 新規観測 | 候補ロック後の126日次参考観測値＋リスク審査 | この段階でも紙上候補のみ。実注文は無効 |

## 運用手順

```bash
pip install -r requirements-repro.txt
python -m unittest discover -s tests -v
python self_evolve.py --population 16 --generations 5 --seed 42 --seed-mode weekly
```

`research/evolution_latest.json` 内の `overfit_audit.status`・`candidate_tests`・`rank_instability`・
`walk_forward_fixed_library` を参照してください。公開用 `docs/data/evolution.json` は
`statistically_screened_count`・`overfit_audit_status`・`rank_instability_fraction` などの**概要だけ**を公開します。

## 自動採用を拒否する主な理由

- `HISTORIC_TRIAL_FAMILY_INCOMPLETE`：過去の週次探索を含めた全候補の比較分布が保存されていないため、全期間を通じた補正ができない。
- `DUPLICATE_OR_MISSING_TRIALS`：審査する候補の完全性を確認できない。
- `INCOMPLETE_CANDIDATE_UNIVERSE`：計算量上限で全候補を審査できない。
- `NO_STATISTICALLY_SCREENED_CANDIDATE`：既存の収益・ドローダウン基準と統計審査を同時に満たす候補がない。
- `LEGACY_CHALLENGER_REQUIRES_INDEPENDENT_AUDIT`：v2.0以前の候補は審査記録を持たず自動昇格できない。

## 次に必要な証拠

- 出所・再配布許諾・タイムゾーン・Bid/Ask・スプレッド・スワップが確認できる**実際のH1データ**。
- 市場環境が変わっても再現するかの前向き紙上検証。仮定コストだけでは約定可能性を証明しない。
- 週次探索を続ける場合には、**全過去候補のリターン行列と探索試行数**を保存して、全探索をまとめた統計検定を行う。現在は不足時に**審査を拒否する**。

### 参考資料

- Bailey et al. (2017), [The Probability of Backtest Overfitting](https://escholarship.org/uc/item/4w1110bb)
- Sullivan, Timmermann and White (1999), [Data-Snooping, Technical Trading Rule Performance, and the Bootstrap](https://doi.org/10.1111/0022-1082.00163)
- [FRED DEXJPUS, USD/JPY NY正午の参考値](https://fred.stlouisfed.org/series/DEXJPUS)
