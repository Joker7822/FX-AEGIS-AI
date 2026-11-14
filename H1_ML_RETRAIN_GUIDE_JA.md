# FX AEGIS AI v2.2 — H1機械学習モデルの自動再学習

更新日：2026年11月14日（JST）。研究用。実注文や紙上運用候補への自動昇格はありません。

## 結論
1時間足のUSDJPY Bid/Ask確定足から特徴量を作り、ロジスティック回帰と勾配ブースティングの2モデルを定期再学習して合議予測します。過去に教師ラベルが確定した行だけをfitに使用するため、将来データの混入を防ぎます。予測後の売買計算は既存のexecution_lab.pyに委ね、買いはAskで入りBidで出る、売りはBidで入りAskで出る検証です。

## デモ（人工データ・投資判断禁止）

    pip install -r requirements-repro.txt
    python execution_lab.py fixture --rows 1250 --out data/bidask_fixture
    python h1_ml_retrainer.py --quotes data/bidask_fixture/bidask_h1_synthetic.csv --provenance data/bidask_fixture/provenance.json --allow-synthetic --slippage-pips-per-side 0.2 --commission-jpy-per-roundtrip 0 --out results/ml_fixture

既定設定は72本ごと再学習、学習サンプル300本以上、履歴最大2,500件、2モデルの一致閾値60%です。データは時系列順に60%研究・20%検証・20%診断専用とし、後ろの20%を使ってパラメーター選択しません。

実ブローカーのH1データを使用するときは、execution_lab.pyで指定するCSV列・来歴JSON・ハッシュ・利用許諾が必須です。暗号鍵、ブローカー履歴、口座情報を公開リポジトリへ保存しないでください。スワップ情報が不足する時間帯は取引を見送ります。

## 生成物
h1_ml_retraining_report.json、validation_predictions.csv、validation_retraining_events.csv、validation_trades.csv、およびdiagnostic_only版の3ファイルを生成します。全結果は後方検証であり、利益保証はありません。確率の校正は未実装です。

## 限界
バー終値確定後すぐ次足始値で約定できるという理想化した前提が残ります。スリッページは仮値、H1にはティック単位の約定・資金調達・証拠金維持率は再現されません。ハッシュは真正なブローカー由来であることを証明しません。現時点で将来のプラス収益は確認できません。

関連：execution_lab.py / h1_evolution_guard.py / H1_EVOLUTION_GUARD_GUIDE_JA.md
