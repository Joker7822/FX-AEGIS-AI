# FX AEGIS AI v2.6 — GitHub Actions復旧と研究API安全確認

更新日：2026年11月14日（日本時間）。

## 結論

GitHub Actions run 43598782328 は自動テストのインポート失敗により停止し、
後続の価格データ更新とPagesデプロイが実行されませんでした。

- 原因：`forward_paper.py` は `_fit_agents` を呼んでいましたが、
  `h1_ml_retrainer.py` にあった実装名は `_fit` でした。
- 修正：`_fit_agents = _fit` を同一モデル実装への互換別名として追加。
- 防止：`tests/test_model_api_contract.py` でモジュール間の一致を自動テスト。
- 売買判断アルゴリズムや採用条件は変更していません。
- 公開リポジトリにブローカーの価格データ、APIトークン、口座情報を保存しません。

## OANDAの状態

接続準備チェック専用モード `preflight-only` は引き続き初期値です。
`workflow_dispatch` による手動実行でのみ通信なし監査を実行できます。
実データ取得は以下をすべて指定しないと実行されません。

- 手動入力 `operation=fetch-and-evaluate` および `confirm_private_download=yes`
- GitHub Variables `OANDA_RESEARCH_ENABLED=true` と `OANDA_RESEARCH_LICENSE_OK=true`
- GitHub Secret `OANDA_API_TOKEN`

この修正では秘密情報の登録、OANDAへの通信、実売買は行っていません。

## 再現試験

```bash
python generate_demo.py
python main.py --csv data/demo_synthetic_usdjpy.csv --strategy trend --out results/demo_trend
python -m unittest discover -s tests -v
```

CIの実行結果を確認するときは、
[GitHub Actions](https://github.com/Joker7822/FX-AEGIS-AI/actions) を参照してください。
