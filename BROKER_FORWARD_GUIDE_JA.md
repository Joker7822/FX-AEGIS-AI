# FX AEGIS AI v2.3 — OANDA H1 Bid/Askと前向き紙上検証

**2026年11月14日 / 研究専用。利益保証・実売買・自動注文なし。**

## 結論

- OANDA v20の `USD_JPY` / `granularity=H1` / `price=BA` を認証付きで取り込む。
- 完了したH1だけ取り込み、BidとAskのOHLC8列を保存。日時はUTCの**足の開始時刻**。
- APIから取得したデータと利用権限の説明、ハッシュ、取得期間、欠損・スプレッド統計を保存。
- 学習時点の固定データとコード・依存ライブラリをロック。ロック後に新規取得した完全なH1足のみで紙上評価。
- 生のブローカー価格、口座情報、APIキーは公開GitHub/Pages/Actions成果物へ送信しない。

## データ提供元と制限

公式API説明：

- <https://developer.oanda.com/rest-live-v20/instrument-ep/>
- <https://developer.oanda.com/rest-live-v20/instrument-df/>
- <https://help.oanda.com/us/en/faqs/api-user-guide.htm>

API利用には有効なOANDA v20アクセストークン、適用される契約・地域の確認が必要です。
**現在、実H1の取得成功・許諾の確認・実市場での収益性は確認できません。**
1リクエスト5,000本上限のため約21日ごとに分割取得します。対応する価格履歴が20年全て存在するとは限りません。
「価格の取得」と「実際の取引約定」は別です。スリッページ、スワップ、約定拒否、週末、流動性を完全には再現しません。

## ローカル：公式APIから取得（ライセンス確認後）

```bash
pip install -r requirements-repro.txt
# 環境変数として OANDA_API_TOKEN を安全に設定。コードやコマンド行に直接書かない。
python broker_h1_oanda.py \
  --from-utc 2026-08-01T00:00:00Z \
  --to-utc 2026-11-01T00:00:00Z \
  --out private_quotes/oanda_h1_2026q3 \
  --environment practice \
  --license-scope 'private research; redistribution not approved' \
  --license-acknowledged
```

上記の日付・パスは**実行例**です。指定時期にデータが存在するかはAPIで確認が必要です。
トークンは標準出力、GitHubソース、公開ダッシュボードには出しません。
`--license-acknowledged` は必要な権利を自分で確認した場合にのみ指定してください。
既存の取得スナップショットは上書きしません。必要なら別の出力先を使用してください。

## H1モデルの既存ウォークフォワード検証

```bash
python h1_ml_retrainer.py \
  --quotes private_quotes/oanda_h1_2026q3/bidask_h1.csv \
  --provenance private_quotes/oanda_h1_2026q3/provenance.json \
  --slippage-pips-per-side 0.2 \
  --commission-jpy-per-roundtrip 0 \
  --out private_reports/ret_training_2026q3
```

スリッページ `0.2`・手数料 `0` は**計算例**であり、実際のブローカー費用を意味しません。
未知のロールオーバーに重なる売買は `execution_lab` が拒否します。

## 実際の前向き紙上検証

1. 本日までの**確定済み**H1スナップショットでモデルを固定する。
2. モデル固定から将来に取得する足を、元の学習価格を変更せずに追加した別のスナップショットとして保存する。
3. 前向きの結果はモデルを一切再学習しないまま生成する。古い期間を「新規前向き」とみなさない。

```bash
python forward_paper.py freeze \
  --quotes private_quotes/oanda_initial/bidask_h1.csv \
  --provenance private_quotes/oanda_initial/provenance.json \
  --lock private_reports/model_lock.json \
  --slippage-pips-per-side 0.2 \
  --commission-jpy-per-roundtrip 0

# 新規のデータ取得後（元の履歴接頭部が完全一致することが必須）
python forward_paper.py evaluate \
  --quotes private_quotes/oanda_extended/bidask_h1.csv \
  --provenance private_quotes/oanda_extended/provenance.json \
  --lock private_reports/model_lock.json \
  --out private_reports/forward_evaluation
```

重要：`oanda_extended` は初回スナップショットの**全行を含み、同じ丸め精度とバー時刻で完全一致する**履歴が必要です。
スナップショット生成時点から少なくとも1時間経ってから次の足が完成します。十分な新規観測がない場合は売買0件が正常です。
モデルの学習特徴量には確定H1終値、現在までのローリング指標を使用します。
トークンや価格を含まないロックJSONは個別環境に保存します。将来のコード/依存関係が変わるとロックを使った評価は停止します。

## GitHub Actions：手動オプトイン

- `Settings → Secrets and variables → Actions → Secrets` に `OANDA_API_TOKEN` を設定。
- `Variables` に `OANDA_RESEARCH_LICENSE_OK=true` を**利用規約確認後**設定。
- Actions の **Private OANDA H1 Research (Manual)** を選択し、期間・環境・仮定コストを指定して手動実行。
- データはGitHub Actions runnerの一時ディレクトリだけに置き、最後に削除。Artifacts/公開リポジトリ/Pagesにアップロードしない。
- このActionsは**履歴の再学習テストのみ**で、前向き紙上検証の継続履歴を保存しません。長期運用は別途、利用規約に適合した非公開ストレージが必要です。
- 設定前は「未設定のためスキップ」です。定期実行、実ブローカーへの接続、将来プラス収益は確認できません。

## 安全確認

```bash
python execution_lab.py fixture --out data/bidask_fixture
python -m unittest discover -s tests -v
```

人工生成テストが通ることと、リアルな約定・利益が確認されたことは異なります。
