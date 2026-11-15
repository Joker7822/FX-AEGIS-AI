# FX AEGIS AI v3.8 — H1価格CSVの自動読込とローソク足表示

更新：2026-11-15（日本時間）

旧版でローソク足が表示されなかった原因は、H1価格CSVと出典JSONが未読込の状態ではローソク足表示を無効にする設計だったことです。

今回、画面を開いた時点で、リポジトリに含めた**人工H1デモ168本**をブラウザが自動で取得し、CSVのSHA-256と出典JSONの一致を確認したあとにローソク足を描画します。
デモのソースは `docs/demo/bidask_h1_synthetic.csv` と `docs/demo/provenance.json` です。
これは固定式で生成した人工的なOHLCであり、実ブローカーデータ、実際のUSD/JPY、予測・取引実績ではありません。2025年3月3日〜3月9日（UTC）の架空価格です。

## 実際のH1価格に切り替える

1. 合法的に取得したUSDJPYのH1 Bid/Ask履歴を `bidask_h1.csv` として用意してください。
2. CSVと一対の `provenance.json` を画面の「H1 CSV＋出典JSONを読み込む」から2ファイル同時に選択します。
3. SHA-256と列構成・OHLC整合性が合格した場合のみ、人工デモの代わりに端末内のデータからローソク足を表示します。
4. 「実データを解除」を押すと人工H1デモに戻ります。

## 操作・安全性

- H1／D1、Bid／Ask、EMA20／50、＋／−、大きく表示、スマートフォンの2本指ピンチに対応
- 人工デモ中は取引注釈・予測表示を無効化し、常に人工デモであることを表示
- 実データはブラウザ内のみで読込。ユーザーのCSVや秘密鍵をGitHubへアップロードしない
- FREDの日次参考値から偽のH1ローソク足を生成しない
- OANDA API自動取得や実売買は無効のまま

## 検証

```bash
node --test tests/test_chart_model.cjs tests/test_chart_annotations.cjs tests/test_chart_zoom.cjs tests/test_chart_demo.cjs
python -m unittest discover -s tests -v
```

実際のH1データの保有、契約条件、正当な利用可能性、実市場での予測成績は今回確認できません。
