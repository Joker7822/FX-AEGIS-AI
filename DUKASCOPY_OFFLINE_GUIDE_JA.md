# FX AEGIS AI v2.4 — Dukascopyオフライン取込（OANDA利用保留）

更新：2026-11-14。**実相場の20年分の取得はまだ完了していません。**

## 運用方針

- **Dukascopy：** 公式ダウンロード画面または契約上許される取得方法で入手した**ローカルファイルだけ**を変換。コード自体はS3、AWS、外部APIへ接続しません。
- **OANDA v20：** 取得コードは保存しますが、**保留状態**。APIのCLIには `--enable-api` が必要で、GitHub Actionsには未設定の明示的な `OANDA_RESEARCH_ENABLED=true` が必要です。トークン登録も実データ取得も行っていません。
- **公開GitHub：** Pythonコードと合成テストのみ。生ティック、H1価格、認証情報、個別モデル証跡は非公開で管理します。
- 価格の取得元であることや利用権の真正性はSHA-256だけでは証明できません。規約・データ範囲・サマータイムを確認してください。

## 1. データを入手する

[Dukascopy Japanの公式ヒストリカルデータ画面](https://www.dukascopy.jp/marketwatch/historical/)でUSD/JPY、BidとAsk、時刻設定を選び、**データ利用条件を確認**したうえで保存します。設定した時刻を記録してください。CSV形式は出力方法で列名が変わり得るため、そのままではなく次の形式に正規化してください。

```csv
 time,bid,ask
 2024-01-02T00:00:00Z,142.100,142.111
 2024-01-02T00:01:00Z,142.120,142.131
```

※実際のCSVでは先頭の空白を含めず、`time,bid,ask` を正確に指定してください。日本時間などの日時を含む場合は、UTCに変換し `Z` または明示的オフセットを付与してください。列が `Time,Open,High,Low,Close` の**片側ローソク足**しかない場合はBid/Askティックとして使用できません。

### 正規化済みCSVをH1に変換

```bash
python dukascopy_h1.py \
  --input private_quotes/raw_usdjpy_ticks.csv \
  --input-format tick-csv \
  --out private_quotes/dukascopy_2024_01 \
  --license-scope "private research only; no redistribution" \
  --license-acknowledged
```

## 2. 公式日次 `.bi5` 形式をオフライン変換

**新しい日次レイアウトのファイルだけ**を入力可能です。公式仕様は20バイト・ビッグエンディアン、時刻はUTC日初からのミリ秒、USD/JPY価格スケールは1000、月は0始まりです。たとえば1月は `00`、11月は `10` です。

```
private_quotes/bi5/USDJPY/
  2024/00/02_ticks.bi5
  2024/00/03_ticks.bi5
  ...
```

```bash
python dukascopy_h1.py \
  --input private_quotes/bi5/USDJPY \
  --input-format bi5-daily \
  --out private_quotes/dukascopy_2024_01 \
  --license-scope "private research only; no redistribution" \
  --license-acknowledged
```

**2006年頃の古いhourly `.bi5` は、この新日次デコーダーの対象外です。** 古いファイルを日次形式と決めつけず、レイアウト確認後に別のデコーダーを作成します。20年の網羅性は確認できていません。

## 3. 取込後の検査・H1機械学習

`private_quotes/dukascopy_2024_01/bidask_h1.csv` と `provenance.json` が作成されます。

```bash
python execution_lab.py validate \
  --quotes private_quotes/dukascopy_2024_01/bidask_h1.csv \
  --provenance private_quotes/dukascopy_2024_01/provenance.json \
  --out private_reports/dukascopy_audit

python h1_ml_retrainer.py \
  --quotes private_quotes/dukascopy_2024_01/bidask_h1.csv \
  --provenance private_quotes/dukascopy_2024_01/provenance.json \
  --slippage-pips-per-side 0.2 \
  --commission-jpy-per-roundtrip 0 \
  --out private_reports/dukascopy_ml
```

実運用費用は実際の契約・約定データに合わせて変更してください。引き続き研究専用であり、将来の利益は未確認です。

## 4. 監査・プライバシー

- 既存の出力を上書きしない（スナップショット不変化）
- UTCの時系列、Bid≦Ask、異常スプレッド、ローソク足の高低、1時間足の連続性を検査
- ティックからH1のOHLCを**新規作成**するが、存在しない時間足は埋めない
- `provenance.json` に入力SHA-256、変換後SHA-256、観測数、除外した未確定足を記録
- 古いデータや入力元の確認が取れなければ、実取引の適格性を主張しない
- S3 `Requester Pays` の費用が発生する取得処理は実装・実行しない

### 公式出典

- [Dukascopy Japan：ヒストリカルデータ取得](https://www.dukascopy.jp/marketwatch/historical/)
- [Dukascopy公式：Historical Price Data, `.bi5` フォーマット](https://wiki.dukascopy.com/en/development/data-export/)
