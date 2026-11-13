# FX AEGIS AI v1.5 — 過去20年間の実データ活用ガイド

**検証対象：2006-11-13 ～ 2026-11-13（暦日。休日にデータがないのは通常）**

本機能は**研究・検証専用**です。実売買機能はありません。ソース元の真正性と実際の収益性を確認したという意味ではありません。

## データ源を分離する理由

- **FRED `DEXJPUS`**：FRBを原典とする「ニューヨーク正午のUSD当たり日本円」**日次参照レート**。長期変動率・252営業日移動平均・年別相場統計を計算します。OHLCでも約定可能価格でもありません。
- **Dukascopy等のブローカー取引履歴**：実売買検証用の**1時間足OHLC**。既存のマルチAI・進化AIバックテストはこちらを入力します。取得元・タイムゾーン・bid/ask・スプレッドが本物かは別途要確認。Dukascopy Japanは履歴データのダウンロード案内を公開していますが、デモ口座のログインが必要です。

## 1. インストール

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

## 2. FREDの20年・日次データを取得・分析

以下は、ユーザーのインターネット接続環境で実行してください。

```bash
python history20.py fetch-fred --method graph --out results/history20
```

FRED側のWeb向けCSVダウンロードが使えない環境では、APIキーを自身で取得して `FRED_API_KEY` 環境変数に設定し、次を実行します。

```bash
python history20.py fetch-fred --method api --out results/history20
```

APIキーはログに書き込まず、GitHubにも置かないでください。FREDのWebサイトからDEXJPUSのCSVを手動ダウンロードした場合は次の方法もあります。

```bash
python history20.py import-fred --csv path/to/DEXJPUS.csv --out results/history20
```

**ファイルからのインポートは出典未認証として記録**されます。誤認防止のため、FREDの表から1時間足OHLCを捏造したり、当時利用できなかった改訂済みFRED値を歴史的売買シグナルに混ぜたりする処理はしません。

作成されるファイル：

- `results/history20/fred_reference_daily.csv` — 欠損日を含む日次参照データ
- `results/history20/reference_regimes.csv` — 日次リターン・63日年率換算変動率・252日移動平均・レジーム
- `results/history20/reference_years.csv` — 年別変動率・レート変化（**取引損益ではない**）
- `results/history20/history20_summary.json` — 実際の開始・終了、取得件数、データ出典、欠損、制約

本ファイルは翌日や週末にデータ欠損があり得ます。対象終了日の2026-11-13まで含まれているかは、`actual_end` と `has_full_requested_span` で必ず確認してください。

## 3. 20年間のUSD/JPY 1時間足を入手・整形

[デューカスコピー・ジャパンの公式ヒストリカルデータページ](https://www.dukascopy.jp/marketwatch/historical/) などで USD/JPY・1時間足を選び、CSVファイルを取得してください。データの掲載開始時期・20年分の欠け・利用条件・Bid/Ask・スプレッドの有無は取得データで確認する必要があります。**本ZIPには20年分の実相場データを同梱していません**。

CSVに`time,open,high,low,close`があり日時に `Z` 等の明示的なUTCオフセットがある場合、既存の`main.py`が直接読み取れます。

取得CSVが `Gmt time,Open,High,Low,Close` などの表記で、**記録時刻がUTCであることを取得元で確認できた場合のみ**、例えば次を使います：

```bash
python history20.py import-h1 \
  --csv data/broker_export_h1.csv \
  --out data/usdjpy_h1_20y_utc.csv \
  --source-timezone UTC
```

実際に異なるタイムゾーンならそのタイムゾーンを指定します。曖昧なDSTの時刻は自動推測せずエラーにします。

```bash
python history20.py audit-h1 \
  --csv data/usdjpy_h1_20y_utc.csv \
  --out results/history20/hourly_quality.json \
  --require-20y
```

`--require-20y` は、始点・終点と全月の95%以上が月300本以上でなければ終了コードを非ゼロにします。**これは記録の量に関する検査で、データの真正性・1本ごとの欠落・実行可能スプレッドを証明しません。** 不完全なデータを補間して架空バーを作成しません。

## 4. 実1時間足による20年時系列バックテスト

前段の品質ゲートが通過した場合に実行します：

```bash
python main.py \
  --csv data/usdjpy_h1_20y_utc.csv \
  --strategy trend \
  --retrain-every-bars 4320 \
  --max-training-bars 25000 \
  --out results/h1_20y
```

**4320本間隔での再学習、最大25000本の学習履歴は処理負担を抑えるための仮設定**です。利益に最適化された設定ではありません。全20年が揃っていれば、既存エンジンが概ね最初の12年を初期学習、次の4年を検証、最後の4年を未使用テストに割り当てます。正確な境界日付は `summary.json` の記録を参照してください。ウォークフォワード学習ではラベルが確定していない直近行を除外します。

`max_training_bars`によって各学習時点で**過去の全20年を一度に学習するわけではありません**。20年間の履歴を時間の順番に評価し、その時点までの過去25000本（最大）でモデルを更新します。この違いは市場レジームが変化する長期研究に重要です。

通貨ペアの真の取引コストが記録されていない場合は、コードのスプレッド・スリッページ推定値に依存します。リアルな約定を保証せず、含み損の最大値、証拠金、スワップ等も未再現です。

終了後、年別検証結果を作ります：

```bash
python history20.py trade-years --results results/h1_20y --out results/history20
```

`strategy_years.csv` と `strategy_years_summary.json` に年別損益、負けた年を出力します。年別の勝率が高くても将来の利益は保証されません。最後の未使用テストで何度も戦略条件を調整するのは過学習です。

## 5. 1画面ダッシュボード

```bash
python dashboard.py \
  --results results/h1_20y \
  --market data/usdjpy_h1_20y_utc.csv \
  --history-report results/history20/history20_summary.json \
  --hourly-quality results/history20/hourly_quality.json \
  --reference-daily results/history20/fred_reference_daily.csv \
  --data-label 'ユーザー取得・実市場データ真正性は未確認'
```

ブラウザで `http://127.0.0.1:8501` を開きます。「20年」ボタンでFRED日次参照レートを表示します。「価格」ボタンはAI検証用H1データ、「資産推移」は決済済み仮想残高です。同じ一画面上で切り替え、スクロール領域を追加しません。

## 6. 自律進化エージェントとの関係

既存 `evolution_engine.py` は、`--csv data/usdjpy_h1_20y_utc.csv` を指定して20年間の実バーを候補探索・検証に使うことができます。ただし進化探索では多数のモデルを学習するため、20年H1の全期間探索は計算資源を大きく消費します。探索した全候補を最終未使用区間に何度も照合し、採用に使わないでください。スプレッド2倍の負荷検証や不採用判定は維持します。

## 出典・確認日

- FRED `DEXJPUS` 公式系列：<https://fred.stlouisfed.org/series/DEXJPUS>
- FRED API 観測値：<https://fred.stlouisfed.org/docs/api/fred/series_observations.html>
- FRED CSVダウンロードの公式説明：<https://fred.stlouisfed.org/help/data/downloading/using-the-download-data-link/>
- FRB データ配信仕様の変更（2026-11-12告知）：<https://www.federalreserve.gov/datadownload/Choose.aspx?rel=h10>
- Dukascopy Japan 公式履歴データ：<https://www.dukascopy.jp/marketwatch/historical/>

本機能の作成日：2026-11-13（JST）。この環境では**FRED/ブローカーへの実ネットワーク取得を完了していない**ため、20年間の実市場データによる収益性は「確認できません」。
