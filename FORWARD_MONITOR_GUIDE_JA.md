# FX AEGIS AI v3.3 — モデル固定・新規H1観測・劣化時の自動停止

更新日：**2026年11月15日（日本時間）**。研究専用。ネットワーク接続・API認証・注文送信はありません。

## 結論

`forward_monitor.py` は、モデル凍結後に初めて取り込んだH1確定足だけを時刻付きで記録します。新しい足が期限内（既定：確定時刻から **15分以内**）に届き、ターゲットの次の始値がまだ発生していない場合にだけ、固定モデルで**次の値動きの予測**を作ります。遅れて取得した過去足は観測ログに記録しても**適時の前向き予測数には加算しません**。

機械学習モデルは研究用のロジスティック回帰＋HistGradientBoosting（v2.2互換）であり、凍結した過去の学習データでのみ再現学習します。このモジュールは将来のデータで再学習したり、注文したりしません。並行する自己進化研究は `autonomous_research.py` で別途扱い、評価対象の固定モデルに自動反映しません。

## 1. 既存の非公開H1データで固定する

利用者本人が、契約上許される方法で取得済みの`bidask_h1.csv`と`provenance.json`が必要です。Dukascopyオフライン変換器の出力を利用できます。ブローカー由来の真正性はハッシュだけでは証明できません。

```bash
python forward_monitor.py init \
  --quotes private_quotes/dukascopy_current/bidask_h1.csv \
  --provenance private_quotes/dukascopy_current/provenance.json \
  --out private_reports/forward_monitor \
  --slippage-pips-per-side 0.2 \
  --commission-jpy-per-roundtrip 0
```

モデル固定の日時は実行時のUTC時計から取得します。**過去時刻の指定や巻き戻しは実ブローカーデータでは禁止**です。`init` で保存した `immutable_model_lock.json` は上書きできません。

## 2. 新たな確定H1を受信した後に実行する

```bash
python forward_monitor.py observe \
  --quotes private_quotes/dukascopy_current/bidask_h1.csv \
  --provenance private_quotes/dukascopy_current/provenance.json \
  --out private_reports/forward_monitor
```

新しい確定足を**元の時系列に追記した完全なスナップショット**として用意します。価格CSVを更新するたびに、`provenance.json`の`sha256`も正しく再計算しなければ読み込みを拒否します。1時間ごとに自分の非公開ホストで `observe` を実行する運用を想定していますが、このリポジトリでは定期通信を有効化していません。

**監査対象：** 元の履歴が変更・短縮されていないか、ソース・使用許諾表記が変わっていないか、前回の時計より逆行していないか、足が確定済みか、今回初めて見た足が確定後15分以内か。複数時間が一括で追記された場合、遅れた足は前向き予測として再計算しません。

## 3. 予測と劣化停止

- 予測確率は**2モデルの単純平均**で、校正済み確率ではありません。売買方向は従来どおり両モデルの閾値条件で決めます。注文は作りません。
- ラベルは予測時点では未確定として保存します。`signal_i`の後続2本の連続H1足が実際に観測されて初めて、始値 `i+1` と `i+2` のBid/Ask中間値から正誤を判定します。
- 既定では直近24件のラベル付き予測を監視し、方向正答率が**40%未満**またはBrierスコアが**0.32超**なら `PAUSED_MODEL_DRIFT_REVIEW_REQUIRED` となり、新たな予測を停止します。あくまで**探索的な運用閾値**で、統計的に校正した検定ではありません。
- 停止したモデルは**自動再開しません**。学習設定やコードが変わった場合も続行しません。人間の確認後、独立したモデル固定・独立した検証ログを作り直してください。
- `monitor_state.json`には観測時刻、対象の足、適時判定、予測値、後日判明した正誤、アラート、SHA-256を記録します。フォルダー内の状態を上書き・消去して「前向き成果」を作り直してはいけません。

## 4. テスト・限界

```bash
python -m unittest discover -s tests -p 'test_forward_monitor.py' -v
```

テストは**人工的に生成したH1足だけ**を使います。人工データのみ、擬似的な検証時計 `_test_asof` を許可します。

ローカル端末の時計、保存ファイルや出典の真正性は**独立した第三者のタイムスタンプでは証明できません**。発注・約定・約定可能なBid/Ask・実際のスリッページ・実市場での優位性・前向き実績の独立検証は、これとは別の仕組みが必要です。研究用に戻す保護は備えていますが、収益性や運用適格性の保証ではありません。

OANDAのデータ取得は引き続き手動の複数条件制御です。本変更ではGitHubにAPIトークンも商用履歴も登録しません。

関連するコード：[`forward_paper.py`](forward_paper.py)、[`h1_ml_retrainer.py`](h1_ml_retrainer.py)、[`execution_lab.py`](execution_lab.py)、[`autonomous_research.py`](autonomous_research.py)。
