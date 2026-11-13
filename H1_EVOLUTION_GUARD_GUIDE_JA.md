# FX AEGIS AI v2.1 — H1 Bid/Ask統計審査と自己進化AIの接続

更新：2026年11月14日（JST）｜**研究専用・実売買は無効**

## 結論

v2.0までは、FRED（NY正午の日次参考レート）を使ったAI探索・多重比較の審査と、
H1 Bid/Askの注文価格を模擬する検証器が分離していました。v2.1では、**全候補の遺伝子定義を
固定保存し、同一のH1 Bid/Askデータで約定価格を模擬した損益を比較**します。
将来収益を改善できることは確認されていません。

## 審査の仕様

1. FREDで探索された **全候補** を `research/h1_candidate_universe.json` に保存。過去週を含めた探索試行数が現在の候補数より多い場合は、履歴不完全として採用を拒否。
2. H1 Bid/Ask OHLC（USDJPY・UTC・バー開始時刻）、出所情報とCSV SHA-256を検査。CSVのハッシュは提供者の真偽を証明しない。
3. シグナルは確定済みバーの中間終値のみから作り、**次バー始値で成行を模擬**。ロングはAsk買い→Bid売り、ショートはBid売り→Ask買い。翌バー始値で決済。
4. H1全体を時系列の **60%・20%・20%** に分割。各区間ごとに現金・損失停止状態をリセット。境界を越す取引を禁止。
5. 最初の60%で、同じブロック抽出を使うmax-Tブートストラップ（探索的）により全候補と固定EMA15/100の差を比較。
6. 中間20%で、プラス収益・基準超過・一定の取引数・DD8%以下・スリッページと手数料の2倍／5倍ストレスを確認。スプレッドはBid/Askに含まれており二重計上しない。
7. 最後の20%は**診断用**で、合否には一切使わない。一度見た結果は、新鮮な未来のデータではない。
8. **合格候補でも自動紙上昇格・実発注はしない**。別途将来の紙上運用と独立した審査が必要。

## まず人工データで試す（GitHub Actionsもこの方式）

```bash
pip install -r requirements-repro.txt
python execution_lab.py fixture --out data/bidask_fixture --rows 1250
python h1_evolution_guard.py fixture-catalog --out data/bidask_fixture/universe_fixture.json
python h1_evolution_guard.py audit \
  --quotes data/bidask_fixture/bidask_h1_synthetic.csv \
  --provenance data/bidask_fixture/provenance.json \
  --universe data/bidask_fixture/universe_fixture.json \
  --allow-synthetic \
  --slippage-pips-per-side 0.2 --commission-jpy-per-roundtrip 0 \
  --out results/h1_evolution_fixture.json
```

人工データの審査結果は常に `SYNTHETIC_TEST_ONLY` とし、採用数は必ずゼロです。

## 許諾済み実H1データを使う場合

**公開GitHubに実価格CSVをコミットしないでください。** データの利用規約・ライセンスを確認し、
`execution_lab.py` 仕様のBid/Ask CSV・出所JSON・該当日のスワップ台帳が必要です。
また、`self_evolve.py` を公式日次データで実行し、**全候補が含まれた**
`research/h1_candidate_universe.json`を生成してください。

```bash
python h1_evolution_guard.py audit \
  --quotes private_quotes/usdjpy_h1_bidask.csv \
  --provenance private_quotes/provenance.json \
  --universe research/h1_candidate_universe.json \
  --swap-ledger private_quotes/swap_schedule.csv \
  --slippage-pips-per-side 0.2 \
  --commission-jpy-per-roundtrip 0 \
  --out private_reports/h1_statistical_audit.json
```

スリッページ・手数料の数値は例です。実際の証券会社の契約条件に置き換えてください。
過去の週次探索が全て保存されていなければ審査は不完全扱いとなります。

## 再現性と限界

- `report_sha256`、`quote_sha256`、`universe_sha256`、全候補のパラメーター、区間ごとの結果を保存します。
- max-Tは候補集合・ブロック長・標本の定常性に依存する探索的検定です。将来の損失確率や利益を証明しません。
- H1ローソク足からは、ティックごとの約定、スリッページ急増、部分約定、証拠金強制決済を再現できません。
- 証券会社の実約定確認・スワップ・データ改訂・ライセンス・通信障害は別途評価が必要です。
- 審査を何度も繰り返せば複数試行の問題が増えるため、探索ログを永続保存してください。

### 参照コード

- `h1_evolution_guard.py` — H1統合検査
- `execution_lab.py` — Bid/Ask約定模擬
- `overfit_guard.py` — 多重比較の検定
- `self_evolve.py` — 全候補探索
- `tests/test_h1_evolution_guard.py` — 検証テスト

### 原データ・検証方法の参照

- [FRED DEXJPUS](https://fred.stlouisfed.org/series/DEXJPUS)
- [scikit-learn TimeSeriesSplit](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html)
- [金融庁：FXについて](https://www.fsa.go.jp/ordinary/iwagai/)
