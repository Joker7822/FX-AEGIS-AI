# FX AEGIS AI v3.0 — ローソク足に売買／AI予測を重ねる（研究用）

更新：2026年11月15日（日本時間）。**実注文・API発注・生データの外部送信は行いません。**

## 結論

v2.9のH1 Bid/Askローソク足に、研究用JSONから「買い・売りのエントリー（▲▼）」「決済（○）」「損切り（赤点線）」「利確（緑点線）」を重ねます。予測の正誤は、記録された正答ラベルではなく、実際に読み込んだBid/Askの**後続H1始値**からブラウザで再計算します（正答率とBrierスコア）。

## 使い方

1. 公開ダッシュボードに `bidask_h1.csv` と `provenance.json` を同時選択して読み込みます。
2. `annotations.json` を選択します。すべてブラウザ内で処理され、アップロードしません。
3. 「エントリー／決済」「損切り／利確」「AI予測の正誤」でオーバーレイの表示を切り替えます。H1／D1、Bid／Ask、表示期間、ズームにも対応します。

## JSON仕様（例示のみ。価格は仮想値・実データではない）

```json
{
  "schema": "fx-aegis-h1-research-overlay-v1",
  "quotes_sha256": "ここにprovenance.jsonのsha256値を設定",
  "approved_for_live_trading": false,
  "execution_assumption": "H1_OPEN_HYPOTHETICAL",
  "synthetic": true,
  "trades": [
    {
      "id": "sample-01", "side": "long",
      "signal_time": "2024-04-01T01:00:00Z",
      "entry_time": "2024-04-01T02:00:00Z",
      "exit_time": "2024-04-01T03:00:00Z",
      "entry_price": 150.05, "exit_price": 150.06,
      "stop_loss": 149.50, "take_profit": 151.00
    }
  ],
  "predictions": [
    {"signal_time": "2024-04-01T01:00:00Z", "probability_up": 0.8}
  ]
}
```

時刻はUTCのH1足の開始時刻。シグナルの次足の始値を仮定エントリーとし、決済は後のH1始値に記録します。注文価格は*申告された研究値*で、約定真正性は検証できません。SL/TPラインは**仮定水準**であり、ティックがないH1データだけでは発動順序や約定を確定できません。

予測の結果は、シグナル時刻をiとして、i+1のBid/Ask中間始値からi+2のBid/Ask中間始値への上昇を1、それ以外を0と判定します。2本が連続していない場合は拒否します。正答率とBrierスコアは観測済み区間の診断値であり、今後の利益や実行可能性を保証しません。JSONの予測値そのものが事前に生成されたかどうかは外部監査なしに証明できません。

- JSONファイル上限4MB、取引3,000件、予測20,000件
- H1のCSVと出典JSONをSHA-256検証し、追加の研究JSONも同じ価格SHA-256と一致しなければ拒否
- 参照FRED日次データからOHLCや取引位置は生成しない
- ローカルH1を削除・変更した場合は注釈も破棄
- 研究用シミュレーションであり**実売買実績ではありません**

機能の仕様は `docs/annotations.js` と `tests/test_chart_annotations.cjs` を参照してください。
