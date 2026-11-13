# FX AEGIS AI v1.6 — GitHub履歴データ・自動実行・公開ダッシュボード

更新日: 2026-11-13 (JST)

## 目的

GitHubを、公開可能な長期為替**参考レート**の保存・版管理と、Pythonテスト・データ更新・読み取り専用ダッシュボード公開の自動化に使用します。FXブローカーへの注文機能は含まれません。

## 公開ダッシュボード

静的サイト公開パス: `docs/`。GitHub Pagesの通常のURL候補は `https://joker7822.github.io/FX-AEGIS-AI/` ですが、**実際に公開成功したかの確認が必要**です。URL候補だけでデプロイ完了とは判定しません。

### GitHub側で必要な初期設定

1. GitHub `Joker7822/FX-AEGIS-AI` → **Settings → Pages** を開く。
2. **Build and deployment → Source: GitHub Actions** を選択。
3. **Actions** タブの `FX AEGIS AI · Historical Refresh, Test & Pages` を開いて **Run workflow**。リポジトリでGitHub Actionsが許可されていることを確認する。
4. Runの両ジョブが緑になるまで確認。`deploy-pages`の出力URLを開く。
5. `docs/data/research.json`の `data_status` が `FRED_REFERENCE_AVAILABLE`、`provenance.source` が FRED を示すことを確認する。

GitHub PagesのSourceを変更できない場合、公開URLが使えるとは限りません。Actionsの権限設定が必要なリポジトリでは `GITHUB_TOKEN` の Contents write が拒否されることがあります。

## 自動処理

`.github/workflows/research-and-pages.yml` は **UTC 02:13（月〜金）＝JST 11:13** に定期実行されます。手動 `workflow_dispatch` とmainへの対象ファイルのpushにも対応。

1. `python -m unittest discover -s tests -v` ですべての研究コードを検査。
2. `python github_pipeline.py refresh` でFRED DEXJPUSの観測値を取得・検証。
3. `python github_pipeline.py build` でWeb用JSONを生成。
4. `datasets/fred/*.csv`と`metadata.json`、`docs/data/research.json`をGitへコミット。Git履歴で過去版を保持。
5. `docs/`をGitHub Pagesに配信。

失敗したAPI取得結果は保存せず、ワークフローを失敗扱いにします。GitHubのスケジュール実行は時刻どおりとは限らず、公開リポジトリでは長期間活動がない場合停止することがあります。

さらに `.github/workflows/weekly-research.yml` は毎週土曜日UTC 02:47（JST 11:47）に研究用参考シナリオと人工H1の回帰テストを実行し、結果をGitHub Actionsの30日アーティファクトへ保存します。これも実売買成績ではありません。

## シミュレーション

ブラウザ上のシナリオパネルで「初期資金、配分、短期・長期EMA、仮定スプレッド、売買方向」を変更し、計算できます。`github_pipeline.py`には純粋関数`simulate_reference`もあります。

**これは実際の取引バックテストではありません。** FREDのUSD/JPY NY正午参考値で、前日までに判明するEMA情報に基づき仮想的に資金推移を計算します。スワップ、実際のbid/ask、スプレッド急拡大、注文失敗、証拠金、税金、過去当時のデータ改定状態は再現できません。利益がプラスでも収益保証になりません。

### ローカルテスト

```bash
pip install -r requirements.txt
python -m unittest discover -s tests -v
python github_pipeline.py refresh
python github_pipeline.py build
python -m http.server 8000 --directory docs
```

`http://localhost:8000` を開いてください。ローカル環境にインターネットアクセスがない場合、`refresh`は失敗し、偽データは生成しません。`build`はデータなし表示のみを生成できます。

## 実際の20年分のH1データについて

GitHubのこのデータセットは**日次の公式参考レートのみ**です。H1バックテストには利用許諾のあるブローカー由来のH1データと、スプレッド・タイムゾーン・欠損検証が必要です。大容量/有料データを公開リポジトリへコミットしないでください。必要な場合はprivateストレージやライセンスの確認後に別の実験用データ基盤を作ります。

公式資料: https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site
