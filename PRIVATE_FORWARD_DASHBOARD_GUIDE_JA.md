# FX AEGIS AI v3.5 — 非公開H1監視と前向き予測ダッシュボード

更新：**2026年11月15日（日本時間）**。研究専用。**OANDA API自動取得・実売買・ブローカー注文は禁止のまま**。

## 結論

非公開H1データのフルスナップショットを更新後、`forward_dashboard_bridge.py run-once` で既存の固定モデル監視を**1回だけ**実行し、端末上の`forward_status.json`を生成します。GitHub Pagesの[前向き予測専用ビュー](https://joker7822.github.io/FX-AEGIS-AI/forward-status.html)にそのファイルを手動選択して、採点済み／未判定、正答率、Brierスコア、停止理由、予測時系列を表示できます。

**この実装にはデータダウンロード、スケジューラー登録、ネットワーク送信、ブローカー注文、モデルの自動再学習／昇格はありません。** GitHub Actionsでは人工データだけで安全性をテストします。実際の継続取得・非公開ホスト・実市場前向き成績は確認できません。

## 1. モデル固定（初回のみ）

本人が利用権限を有するUSD/JPY H1 Bid/Ask `bidask_h1.csv` と `provenance.json` を、非公開環境に準備してください。

```bash
python forward_monitor.py init \
  --quotes private_quotes/dukascopy_current/bidask_h1.csv \
  --provenance private_quotes/dukascopy_current/provenance.json \
  --out private_reports/forward_monitor \
  --slippage-pips-per-side 0.2 \
  --commission-jpy-per-roundtrip 0
```

実際の契約・想定執行条件に応じて、スリッページ／手数料は事前に確定してください。モデル固定を過去時刻へ巻き戻す操作は、実市場のデータでは禁止されています。

## 2. 利用者側で更新済みデータを安全に受け取る

データ取得機能はまだありません。正当な手段で取得した、**新しい確定H1足を末尾に追加した完全なCSV**と、更新後CSVのSHA-256を記した`provenance.json`が必要です。取り込み前の過去足が改変・欠落していた場合、監視器が停止します。

- `--expect-new-bar` は外部の市場営業カレンダーで確定足を期待できる場合だけ指定します。週末や取引休止期間では使わないでください。
- 価格CSVと出典JSONは更新途中の状態を監視器に読ませないでください。非公開領域で一時ファイルから原子的に公開する方法を利用者側で用意する必要があります。
- 更新中に入力ファイルが変更された場合はSHA-256の再検査で処理を中断します。これは完全なファイル一貫性保証ではありません。

## 3. 1回監視してプライベートな閲覧用JSONを更新

```bash
python forward_dashboard_bridge.py run-once \
  --quotes private_quotes/dukascopy_current/bidask_h1.csv \
  --provenance private_quotes/dukascopy_current/provenance.json \
  --out private_reports/forward_monitor
```

監視だけ行いたい場合は従来の`forward_supervisor.py run-once`も使用できます。すでに監視済みなら、**監視を増やさず**表示用JSONだけを再作成できます。

```bash
python forward_dashboard_bridge.py export \
  --out private_reports/forward_monitor
```

出力ファイルは`private_reports/forward_monitor/forward_status.json`です。プライベート領域に0600権限で保存し、GitHubにプッシュしたり`docs/`にコピーしたりしないでください。画面は端末でローカル選択したJSONだけを読み込みます。

## 4. ダッシュボードの操作

1. [前向き予測ダッシュボード](https://joker7822.github.io/FX-AEGIS-AI/forward-status.html)を開く。
2. `＋ forward_status.json を選択`からローカルのJSONを読み込む。
3. 採点済みの予測件数・未判定件数・方向正答率・Brier・直近24件・停止状態と時系列を確認する。
4. 新しい結果が必要なときは、非公開ホスト上でデータとJSONを更新したあと、**ファイルを再選択**する。ページは自動で非公開ファイルを取得しない。

### 監視状態の読み方

`IDLE_NO_NEW_H1`は監視器が動いたものの新足がない状態であり、データ配信の正常性の証明ではありません。`BLOCKED_*`は停止状態で、監査記録を保全し、必要な確認なしには自動復帰しません。

「正答率」は予測方向と事後に観測した値動きの一致率であり、取引利益率ではありません。「Brierスコア」も研究用の予測診断値です。損切り・利確・約定・先行情報による成績向上を意味しません。表示用レポートは直近720件を含み、全期間の集計は監視状態から計算します。

## 5. 検証とプライバシーの限界

```bash
python -m unittest discover -s tests -p 'test_forward_dashboard_bridge.py' -v
node --test tests/test_forward_report.cjs
```

- `forward_status.json`には価格OHLCやAPIトークンを含みませんが、**シグナル時刻や研究上の確率はプライベート情報として扱ってください**。
- JSONの内部ハッシュは改変事故の検出用です。独立したブローカー真正性証明、電子署名や第三者タイムスタンプではありません。
- 固定モデルのコード・依存パッケージが変わると、従来のロックの続行は拒否されます。再固定は新しい独立研究として扱います。
- 本ツールはOSスケジューラーや非公開ホストを準備しません。実市場の定期取得・契約上の許諾、真正な前向き予測実績は未確認です。
- OANDAの研究用データ取得ワークフローは従来どおり手動確認制です。**自動発注の接続はありません。**

関連ソース：[`forward_supervisor.py`](forward_supervisor.py)、[`forward_monitor.py`](forward_monitor.py)、[`forward_dashboard_bridge.py`](forward_dashboard_bridge.py)、[`docs/forward-report.js`](docs/forward-report.js)。
