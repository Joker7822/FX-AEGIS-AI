# FX AEGIS AI v3.1 — AI予測と仮想売買をローソク足へ自動連携

更新：2026年11月15日（JST）。**研究専用・実注文なし。**

## 結論

`h1_ml_retrainer.py`の学習結果CSV（`validation`、`diagnostic_only`）と`execution_lab.py`の仮想約定記録を、ダッシュボードv3.0互換の`annotations.json`に自動変換します。
FRED日次値からH1足・仮想OHLCを作ることはありません。元の`bidask_h1.csv`と`provenance.json`を同時に指定してください。

## 実行手順（人工データのテスト例）

```bash
python execution_lab.py fixture --rows 1250 --out data/bidask_fixture
python h1_ml_retrainer.py \
  --quotes data/bidask_fixture/bidask_h1_synthetic.csv \
  --provenance data/bidask_fixture/provenance.json \
  --allow-synthetic --slippage-pips-per-side 0.2 \
  --commission-jpy-per-roundtrip 0 --out results/h1_ml_fixture
python ml_chart_overlay.py \
  --quotes data/bidask_fixture/bidask_h1_synthetic.csv \
  --provenance data/bidask_fixture/provenance.json \
  --ml-results results/h1_ml_fixture \
  --allow-synthetic --out results/h1_ml_fixture/annotations.json
```

人工データに対する`--allow-synthetic`は必須です。実ブローカーから取得したデータはこの指定なしで取り込みますが、出典の真正性をハッシュのみで証明することはできません。

## ダッシュボードでの表示

1. [公開ダッシュボード](https://joker7822.github.io/FX-AEGIS-AI/)を開きます。
2. H1のCSVと出典JSONを読み込みます。
3. 「売買・AI予測JSON」に`annotations.json`を読み込みます。
4. ローソク足上にエントリー・決済を表示し、予測の正誤、Brierスコアを確認します。判定に使う実現ラベルは、閲覧中の価格CSVの後続2本の始値からブラウザが再計算します。

価格ファイル・出典ファイル・注釈はブラウザ上で処理されます。研究用価格・予測JSONをGitHubの`docs/`や公開Artifactsに保存しないでください。

## 入力整合性のチェック

- 学習レポートのSHA-256および価格・出典JSONのハッシュを照合します。
- 60/20/20の時系列境界を保持し、検証区間と診断区間を混同しません。
- 学習ラベルの確定時刻が予測時点を越えていないか確認します。
- エントリーをシグナルの次H1始値、決済をその次H1始値へ結びつけます。
- Bid/Askと申告スリッページに対する仮想約定価格を厳密に検査します。
- 欠損時間をまたいで正答判定できない予測は、スキップ件数として記録して表示対象から除外します。
- 実約定済みと誤解されるような損切り/利確ラインは**自動で作成しません**（実装されていないため）。
- 4MB・取引3,000件・予測20,000件の画面上限を超える場合は失敗とし、無言でデータを間引きません。
- 既存の`annotations.json`は上書きしません。

## 予測確率の定義

画面表示用`probability_up`は、同じ予測時点の`p_up_logistic`と`p_up_boost`を算術平均した値です。第三の学習済みモデルではなく**未校正の合成値**です。各モデルの予測値と検証区間名も注釈に保持します。

出典：各関数の実装 [`h1_ml_retrainer.py`](h1_ml_retrainer.py)、[`execution_lab.py`](execution_lab.py)、[`docs/annotations.js`](docs/annotations.js)。