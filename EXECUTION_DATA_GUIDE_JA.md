# FX AEGIS AI v1.9 — H1 Bid/Ask 実データ検証（研究専用）

更新日：2026年11月14日（JST）

## 結論

FREDの20年日次参照レートでの概算収益と、ブローカーの実際のBid/Ask価格による取引コスト込み検証を**分離**しました。現時点ではユーザーの本物のBid/Askデータはまだ投入されておらず、利益の再現性は確認できません。

`execution_lab.py` は、USD/JPYのH1（1時間足）だけに対応する**研究用・非発注**プログラムです。API接続・秘密鍵・実注文のコードはありません。

## 1. 最初に人工データで検証

```bash
pip install -r requirements-repro.txt
python -m unittest discover -s tests -v
python execution_lab.py fixture --out data/bidask_fixture
python execution_lab.py validate \
  --quotes data/bidask_fixture/bidask_h1_synthetic.csv \
  --provenance data/bidask_fixture/provenance.json \
  --allow-synthetic --out results/execution_demo
python execution_lab.py simulate \
  --quotes data/bidask_fixture/bidask_h1_synthetic.csv \
  --provenance data/bidask_fixture/provenance.json \
  --allow-synthetic --slippage-pips-per-side 0.2 \
  --commission-jpy-per-roundtrip 0 --out results/execution_demo
```

人工データの結果はソフトウェア検査だけに使用し、収益の根拠として扱いません。

## 2. 実データのファイル形式

**データ提供元の利用規約に従い、許諾済みのBid/Ask OHLCだけを自分の端末に保存してください。** GitHub公開リポジトリに商用価格データ・口座情報・APIキーを追加しないでください。

`data/private_broker/usdjpy_h1.csv`（列名はこの順番、時刻はUTC・バー開始時刻）：

```csv
time,bid_open,bid_high,bid_low,bid_close,ask_open,ask_high,ask_low,ask_close
2025-01-06T09:00:00Z,157.100,157.135,157.085,157.120,157.113,157.148,157.098,157.133
2025-01-06T10:00:00Z,157.120,157.165,157.110,157.150,157.133,157.178,157.123,157.163
```

実データに添える `data/private_broker/provenance.json` の例：

```json
{
  "schema":"fx-aegis-h1-bidask-v1",
  "source_kind":"BROKER_BID_ASK_H1",
  "source_name":"YOUR_BROKER_AND_PRICE_FEED",
  "symbol":"USDJPY",
  "timestamp_semantics":"BAR_OPEN",
  "account_currency":"JPY",
  "quote_timezone":"UTC",
  "license_scope":"PRIVATE_RESEARCH_ONLY",
  "sha256":"REPLACE_WITH_ACTUAL_SHA256"
}
```

CSV実体のSHA-256をPythonで計算して `sha256` に記載します。

```bash
python -c "import hashlib,pathlib;print(hashlib.sha256(pathlib.Path('data/private_broker/usdjpy_h1.csv').read_bytes()).hexdigest())"
```

**注意：ハッシュが一致しても出所の真正性は認証されません。** 同じ提供元・価格定義・取引口座条件であることを別途確認してください。FREDの日次参照レートをH1 OHLCに変換してはいけません。

## 3. 実データの検査と研究用売買

```bash
python execution_lab.py validate \
  --quotes data/private_broker/usdjpy_h1.csv \
  --provenance data/private_broker/provenance.json \
  --out results/private_h1

python execution_lab.py simulate \
  --quotes data/private_broker/usdjpy_h1.csv \
  --provenance data/private_broker/provenance.json \
  --fast 15 --slow 100 --allocation 0.25 \
  --max-spread-pips 2.0 --slippage-pips-per-side 0.2 \
  --commission-jpy-per-roundtrip 0 \
  --out results/private_h1
```

上記の手数料0円は**例示用**です。実際のブローカーの公表条件に基づいて数値を入力してください。スリッページ0.2 pipsも仮定で、約定拒否・流動性・急変時のコスト拡大を再現しません。

- `quote_audit.json`：価格検査・件数・欠損・スプレッド分布・出所情報
- `execution_report.json`：仮想収益率・最大DD・拒否理由・計算上の制約
- `trades.csv`：買いAsk・売りBidの実行仮定、コスト、円換算損益
- `equity.csv`：**決済後のみ**の仮想損益推移（含み損最大値ではない）

初期設定は次のバーの始値で建て、さらに次のバーの始値で決済します。シグナル生成は完成したバーの終値までしか見ません。別のAIモデルが外部CSVでシグナルを出す場合、その外部モデルの未来情報混入は**別途監査が必要**です。

外部シグナルの書式（時刻は**シグナル元となったバーの開始時刻**）：

```csv
bar_open_time,side
2025-01-06T09:00:00Z,1
2025-01-06T10:00:00Z,0
```

これを `--signals data/private_broker/signals.csv` で指定できます。`side`は買い`1`、売り`-1`、見送り`0`です。

## 4. スワップの扱い

ロールオーバーの正確な時刻とスワップが不明な場合、21時/22時UTCの周辺または日付を跨ぐ可能性がある取引を**拒否**します。これは厳密なブローカーカレンダーの代替ではなく、安全側の近似です。

実際のブローカーが示す日別ロールオーバー情報がある場合、次のCSVを `--swap-ledger` に指定します（**日付ごとの網羅的な記録**が必要です。額はUSD1,000あたりのJPY、プラスとマイナス両方を許容）。

```csv
date_utc,rollover_time_utc,swap_long_jpy_per_1000usd,swap_short_jpy_per_1000usd
2025-01-06,2025-01-06T22:00:00Z,-5,2
2025-01-07,2025-01-07T22:00:00Z,-4,1
```

入力されていない日を含む取引は拒否します。週末や三倍スワップなども**提供元の事実値**を反映してください。入力だけでは網羅性・正確性を自動保証できません。

## 5. 既知の制限と次の検証

本コードはH1の**始値**しか約定に使わないため、指値、逆指値、約定速度、板の流動性、ギャップ時の保有継続、建玉証拠金、ストップアウト、税金、取引所休業、建値の変動やティック内の最大含み損を再現しません。スプレッドやスワップが記録されていても**実運用で同一成績になることは確認できません**。

「正しい次の段階」は、複数年の実Bid/Ask・スワップ情報を用いて時系列で検証し、想定コスト増加（2倍・5倍）と完全未使用期間での再現性を調べることです。検証に合格しても実注文機能は自動では有効化しません。

## GitHub公開と自動実行

`.github/workflows/weekly-research.yml` および `research-and-pages.yml` では**人工Bid/Askの動作テストのみ**自動実行します。私有のブローカーデータをGitHubへアップロードする設計にはしていません。出力はローカル `results/` に保存してください。
