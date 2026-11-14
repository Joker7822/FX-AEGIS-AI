# FX AEGIS AI v2.9 — ローソク足とチャートの表示最適化

更新日：2026年11月15日（JST）

## 結論

公開ダッシュボードはFREDの**日次参考値を折れ線**で表示し、ローソク足はユーザーが自分の端末で読み込んだUSD/JPYの**H1 Bid/Ask OHLC**だけを表示します。
FRED DEXJPUSは米国・ニューヨーク正午の単一参考レートであり、始値・高値・安値・終値（OHLC）を含みません。参考レートからOHLCを創作しません。

## 使い方

1. 先に権利を確認したDukascopyのティックを、`dukascopy_h1.py` で**非公開** `bidask_h1.csv` と `provenance.json` に変換します。人工生成データはデモ用です。
2. [FX AEGIS AI 公開ダッシュボード](https://joker7822.github.io/FX-AEGIS-AI/)をHTTPSで開きます。
3. **「H1 CSV＋出典JSONを読み込む（端末内のみ）」**を押し、2ファイルを同時に選択します。
4. SHA-256、UTC H1時刻、OHLC、Bid/Askの整合性が一致すれば**ローソク足**が表示されます。出典元が真正であることまでは、このチェックでは証明できません。
5. チャート上部で**H1・D1（UTC集約）／Bid・Ask／EMA20・EMA50／期間**を選択します。ホイールでズーム、横ドラッグで移動、カーソルを重ねるとOHLCと日時を確認できます。キーボードでも左右矢印、`+`、`-`、`Home`が使えます。
6. **クリア**でメモリ上のデータを消去します。ページ再読込でも消えます。ブラウザのLocalStorageには保存しません。

## 安全性とデータ解釈

- ファイルはJavaScriptのFile APIとWeb Crypto（HTTPS）により**ブラウザ内でのみ**読み取ります。元データをサーバーにアップロードしません。
- 公開ダッシュボードの元の参考レート、仮想資産、AI審査結果は変わりません。**ローカルOHLCは仮想資産計算に自動投入しません**。
- D1はUTC日付ごとにH1観測から集約します。欠損日・短い取引日には不完全な日足が含まれる可能性があり、ホバー時に注意を表示します。
- ローソク足が画面の横幅より多いときは、**実測H1から複数足を区間集約**して表示します（表示欄に明記）。横軸の間隔が同じでも、それは連続した取引時間が保証された意味ではありません。
- SHA-256の一致はデータの整合性を示しますが、ブローカー由来・利用許諾・約定可能性・将来利益の証拠ではありません。
- 実注文・OANDA APIへの通信・H1データの自動取得は、この画面改善には含まれません。

## 開発者向けテスト

```bash
node --check docs/chart-model.js
node --check docs/chart-view.js
node --check docs/app.js
node --test tests/test_chart_model.cjs
```

ソースは外部チャートCDNに依存せず、プライベート価格データも配布しません。

### 参照

- [FRED DEXJPUS](https://fred.stlouisfed.org/series/DEXJPUS) — 日次参考値
- [Dukascopy公式履歴データ](https://wiki.dukascopy.com/en/development/data-export/) — ティック元データ
- [MDN Web Crypto: SubtleCrypto.digest](https://developer.mozilla.org/en-US/docs/Web/API/SubtleCrypto/digest) — ブラウザ内ハッシュ
