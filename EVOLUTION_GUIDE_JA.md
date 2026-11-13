# FX AEGIS AI v1.4 — 自律進化AI導入ガイド

更新日: 2026年11月13日（JST）

## 結論

「特徴量の数式」「使用モデル2種類」「融合比率」「売買承認の確率閾値」を遺伝的探索で新しく組み合わせ、候補を生成して過去データで自動比較する機能を追加しました。**新しい機械学習理論をAIが発明したと証明できる機能ではありません。** 任意のPythonコードを自動生成して実行する方式は採用していません。

## 各AIの役割

1. 設計AI：許可されたモデル・指標・演算から新しい設計図（JSON）を生成
2. 交叉・突然変異AI：評価結果をもとに候補の設計を変更
3. 評価AI：時系列の学習・検証で現行の合議AIより損益が改善するか比較
4. ストレスAI：スプレッド・スリッページが2倍でも検証損益がプラスか確認
5. 承認ゲート：条件未達なら新モデルを不採用。合格時も**紙上研究設定のみ**更新
6. 監視AI：CSVが新しい確定足を含むときのみ研究を再実行し、データ改ざんや同時刻の差し替えを拒否。過去200足とその前200足の分布変化を簡易検知

## 実行コマンド

```bash
pip install -r requirements.txt
python -m unittest discover -s tests -v
python evolution_engine.py discover --csv data/demo_synthetic_usdjpy.csv --out results/demo_evolution --population 6 --generations 2
python dashboard.py --results results/demo_evolved_compare --market data/demo_synthetic_usdjpy.csv --evolution-report results/demo_evolution/evolution_report.json
```

進化AIを合議AIに接続した検証は、`python main.py --csv data/demo_synthetic_usdjpy.csv --strategy trend --evolved-agent results/demo_evolution/candidate_evolved.json --out results/demo_evolved_compare` を事前に実行してください。

## 自動的な研究用採用

```bash
python evolution_engine.py monitor --csv data/verified_usdjpy_h1.csv --out results/production_research --auto-promote-paper
```

実CSVはUTCの日時、open/high/low/closeを含め、正式なデータソースから取得してください。`monitor`は1回動いて終了するプログラムです。継続運用にはOS側のスケジューラから定期実行してください。実注文API・秘密鍵は不要です。

採用条件（研究用の初期値）: 3期間のうち2期間以上で利益プラス、2期間以上で現行合議AIより改善、合計20取引以上、通常コストと2倍コスト双方で検証損益プラス、最大確定損益DD 5%以内。既存の紙上採用モデルがあれば、双方のコスト条件でそれを上回る必要があります。

## 見てはいけないものと見てよいもの

学習および候補の選択では「最終20%」を参照しません。レポートの最終20%は検証が済んだ後に表示されます。**その値を見て探索条件を変更すると最終20%も実質的に学習に利用したのと同じ状態となり、独立検証とは見なせません。** 新データの別期間を確保してください。

## 安全上の制限

- 未検証の設計図から注文APIへ接続できません。
- 生成AIの「自己改造」ではなく、許可リスト内の自動探索です。
- 損益・勝率・利益係数が良くても将来の利益を保証しません。
- 人工データの成績は実相場の優位性を示しません。
- 実注文は実装されておらず、デモ口座でのフォワード検証も未実施です。

## 技術的背景と参考資料

- scikit-learn, TimeSeriesSplit / gap: https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html
- scikit-learn, Common pitfalls / Data leakage: https://scikit-learn.org/stable/common_pitfalls.html
- scikit-learn, Nested vs non-nested cross-validation: https://scikit-learn.org/stable/auto_examples/model_selection/plot_nested_cross_validation_iris.html
