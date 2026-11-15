# FX AEGIS AI v3.2 — 自律型・自己進化・自己再学習「研究エンジン」

更新：2026年11月15日（日本時間）

## 結論

既存の遺伝的戦略探索、H1 Bid/Askモデル再学習、複数比較補正を組み合わせ、**新しいH1観測が増えたときだけ**自律的に研究サイクルを進める `autonomous_research.py` を追加しました。**実売買・ペーパー取引の自動承認、注文送信、ブローカー接続は実装していません。** 完全な市場適応・利益の確約・金融規制への適合は検証できません。

## 処理の流れ

1. 自分で入手済みのH1 Bid/Ask CSVと出典JSONを照合（ソース宣言の真正性は独立には確認できない）。
2. 記録済みの前回スナップショットと照合し、過去の足の改変・途中削除・異なる価格提供元・政策の勝手な変更を拒否。
3. 初回は1200本以上で実施。次回以降は**新しい確定H1足が24本以上**増えた場合だけ学習を更新。同一データ再実行は `NO_NEW_H1_BARS`、不足は `WAIT_MORE_COMPLETED_H1_BARS`。
4. ロジスティック回帰＋勾配ブースティングを各時点で確定したラベルだけで再学習。Chronological 60/20/20分割。**最後20%は記述統計専用で、新規の真正なフォワードテストではない**。
5. 登録済み**全**戦略候補のH1執行モデルを検証。既存のブロック・ブートストラップ最大統計量、2倍・5倍コストストレス等を再利用。過去の試行数が揃わない場合は統計的な昇格を認めない。
6. AI予測CSVと仮定の売買約定履歴から`ml_annotations.json`を生成。損切り・利確がシミュレートされていなければ表示値は捏造しない。
7. 検証結果を `runs/<入力ハッシュ>/` に不変スナップショットとして保存し、`state.json` を原子的に更新。同時実行と中途半端な成果物を拒否。
8. 自動採用は一切しない。結果は常に `HOLD_RESEARCH_ONLY_NO_AUTO_PROMOTION`、`approved_for_live_trading=false`。

**注意：** 元データそのものの真正性や、遺伝子探索履歴が漏れなく登録されているかは、ファイル内部のハッシュだけでは証明できません。検証対象期間を繰り返し観察すると過学習のリスクは残ります。真のフォワード成績はモデル固定後に未観測だった時点のデータで別途記録してください。

## 人工データで安全に試す

```bash
python execution_lab.py fixture --rows 1250 --seed 42 --out data/autonomous_fixture
python h1_evolution_guard.py fixture-catalog --out data/autonomous_fixture/universe.json
python autonomous_research.py \
  --quotes data/autonomous_fixture/bidask_h1_synthetic.csv \
  --provenance data/autonomous_fixture/provenance.json \
  --universe data/autonomous_fixture/universe.json \
  --allow-synthetic --probability-threshold 0.51 \
  --slippage-pips-per-side 0.2 \
  --commission-jpy-per-roundtrip 0 \
  --bootstrap-reps 64 \
  --out results/autonomous_v32_smoke
```

同じコマンドを2回実行すると、2回目は `NO_NEW_H1_BARS` になり再学習しません。`results/`と`private_reports/`はGit管理対象外です。

## 実際のブローカー履歴を利用する場合

本人が正当に入手したH1 Bid/Ask と利用条件を確認してから、 `--allow-synthetic` を外し、`--quotes`、`--provenance`、`--universe` に**非公開**ファイルを指定してください。実データの戦略候補集はFRED参照データから2021-01-01より前に選定済みの完全履歴のみ受け付けます。

再学習パラメータ・取引コスト・スワップ台帳を変更した場合、同じ状態ファイル上で再評価することは拒否します。**研究条件を変える場合は、新しい独立研究として履歴を引き継がず別記録を作り、比較試行数を管理してください。**

`state.json` には前回の入力SHA-256・先頭からのバー内容ハッシュ・候補集ハッシュ・学習設定を保持します。以前の状態ファイルを消して同じディレクトリで再開する操作は拒否されます。

## 自動運用の範囲

- GitHubの既存スケジュールで実行するのは**合成データの安全性回帰テストだけ**。検証成功は実市場での学習完了を意味しません。
- 本物の研究データを継続投入するには、利用者管理の非公開実行環境・合法なデータ供給契約・更新手順が必要。現時点では、取得の完全自動化は**未実装**。
- OANDA API の通常取得は、既存の手動承認4条件で引き続き保留。ブローカー注文APIは呼ばない。
- 運用中のモデルを自律的に入れ替える処理、自己改変コードの自動デプロイ、リアルマネーによる運用判断は**意図的に無効**。

参考ソース：[`execution_lab.py`](execution_lab.py)、[`self_evolve.py`](self_evolve.py)、[`h1_evolution_guard.py`](h1_evolution_guard.py)、[`h1_ml_retrainer.py`](h1_ml_retrainer.py)、[`ml_chart_overlay.py`](ml_chart_overlay.py)。
