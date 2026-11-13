# FX AEGIS AI v1.7 — 再現性を使った検証手順

更新日：2026年11月13日。**実売買に使用しない研究専用機能**です。

## 目的

過去データ・コード・依存パッケージ・パラメーターを固定し、同じ検証結果が再現できるか確認します。実験に都合の良い期間やパラメーターだけを選ぶ行為を防ぎます。再現性は統計的優位性や将来利益の証明ではありません。

1. 出所が明記されたFRED日次USD/JPYのみを取り込みます。`datasets/fred/metadata.json` とCSVのSHA-256・期間・件数が一致しなければ中断します。
2. `repro_lab.py capture` は検証設定、データ・コードのハッシュ、使用ライブラリのバージョン、3つの期間別参考成績、倍額コストを `research/repro_latest.json` に記録します。
3. `repro_lab.py verify` は保存された計算を再実行し、数値・環境・ハッシュが一致しないとエラー終了します。
4. `repro_lab.py scan` は候補48種類を2006～2015年と2016～2020年で比較し、2021年以降のホールドアウトを上位1候補に対してだけ算出します。再評価を繰り返すとホールドアウトは独立性を失います。
5. GitHub Actionsは平日更新と週次再実行時に試験を行い、結果の証跡をGitHub履歴とActionsの成果物に保存します。

## GitHubでのコマンド

```bash
python -m pip install -r requirements-repro.txt
python repro_lab.py capture --out research/repro_latest.json --fast 15 --slow 100 --allocation 0.25 --spread-pips 1.4
python repro_lab.py verify --proof research/repro_latest.json
python repro_lab.py scan --out research/scan_latest.json
python repro_lab.py publish --proof research/repro_latest.json --scan research/scan_latest.json --out docs/data/reproducibility.json
```

## 再現不能になったとき

- `Dataset hash ... differs`：価格データ改訂またはファイル改変。以前のGitコミットからデータとメタデータを復元してください。
- `Code bytes differ`：コードが変更されています。証跡に対応するGitリビジョンをチェックアウトしてください。
- `Runtime versions differ`：`requirements-repro.txt`の依存関係とPython環境を再構築してください。
- `Recomputed metrics differ`：計算仕様や環境を調査し、過去結果を上書きせず新しい実験としてください。
- `Insufficient sample`：対象期間を満たす検証データがありません。架空の相場データで埋めないでください。

## 実装上の制約

- 日次ニューヨーク正午レートには注文板、Bid/Ask、スプレッド時系列、約定、スワップがないため、シミュレーションは概算です。
- 乱数シード42を記録していますが、EMA戦略では乱数を使いません。既存の進化AIを全環境で完全一致させるには、追加の実行環境固定・並列処理の統制が必要です。
- 2021年以降のデータを何度も見て改良したパラメーターは、もはや未使用の最終検証ではありません。
- 結果一致でも、別の通貨、ブローカー、未来相場で利益が出るとは限りません。

GitHub: https://github.com/Joker7822/FX-AEGIS-AI
