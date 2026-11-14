# FX AEGIS AI v2.5 — OANDA接続準備・手動実行ガイド

更新：2026-11-14（日本時間）

## 結論

GitHub ActionsのOANDA研究ジョブに**通信なしの事前確認モード**と、
明示的な確認を要求する手動ダウンロードモードを追加しました。
この変更では、GitHubのリポジトリ変数・Secretは登録しておらず、OANDA APIとの通信も実施していません。

## 手順1：通信なしの安全確認

1. GitHubの **Actions → Private OANDA H1 Research (Manual) → Run workflow** を開きます。
2. `operation=preflight-only`、`confirm_private_download=no` を選択します（初期値）。
3. 実行ログに有効化変数の設定状況が表示されます。Secretの内容・存在確認は行わず、APIにも接続しません。

## 手順2：研究用取得を許可する場合（利用条件を確認後）

GitHub **Settings → Secrets and variables → Actions** で、
- Repository Variables: `OANDA_RESEARCH_ENABLED=true`
- Repository Variables: `OANDA_RESEARCH_LICENSE_OK=true`
- Repository Secret: `OANDA_API_TOKEN` （有効な個人トークン）

を設定します。APIトークンをソースコード、公開Issue、ダッシュボード、チャットには記載しないでください。

続いて同じワークフローを開き、`operation=fetch-and-evaluate` および
`confirm_private_download=yes` を明示的に選択し、履歴日数、環境、仮定コストを確認して手動実行します。
設定が足りなければジョブはスキップされ、トークンが欠けている場合は取得前に失敗します。

## 運用上の制限

- `workflow_dispatch` 以外のトリガーはありません。定期実行・pushではブローカーAPIにアクセスしません。
- APIから取得したデータはGitHub Actionsランナーの一時フォルダーに保存し、後で削除します。
- 生Bid/Askデータ・口座情報・研究結果を公開リポジトリやArtifactsにアップロードしません。
- `practice` は価格取得APIの接続先の選択であり、模擬注文の開始を意味しません。
- 研究用のH1バックテストは**前向き運用の実績ではありません**。予測収益は保証されません。
- APIの接続可能性、データ利用権、取得可能年数は取得前には確認できません。

公式情報：
- [GitHub Actions workflow_dispatch](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#workflow_dispatch)
- [GitHub Secrets・Variables](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets)
- [OANDA v20 Instruments Candles](https://developer.oanda.com/rest-live-v20/instrument-ep/)
