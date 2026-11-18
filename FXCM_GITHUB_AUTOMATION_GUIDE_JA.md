# FX AEGIS AI — FXCM H1公開GitHub自動取得（暗号化保存）

更新日：2026-11-19（日本時間）。GitHub Actionsを公開リポジトリで利用し、価格データを平文公開しない構成。

## 実行条件（本人が権利を確認して設定）

FXCMの個人利用条件・EULAおよびクラウドでの暗号化保存が許されるか確認。許諾不明なら有効にしない。
公式資料：https://github.com/fxcm/MarketData/blob/master/CandleData/README.md
利用条件：https://www.fxcm.com/uk/forms/eula/

リポジトリ Settings → Secrets and variables → Actions → Variables で2つを登録：
- FXCM_PERSONAL_USE_ACK = true
- FXCM_CLOUD_STORAGE_RIGHTS_ACK = true

Actions Secrets に FXCM_ARCHIVE_PASSPHRASE = 32文字以上の強いランダムなパスフレーズを登録。秘密文字列はチャット・GitHub issueに貼らない。

## 使い方

Actions → FX AEGIS AI · FXCM Encrypted H1 Archive (Personal) → Run workflow から fetch-and-encrypt を選択する。デフォルトpreflight-onlyでは通信も価格取得もしない。
設定後は毎週火曜日17:37 JSTに最新の完了済み4週間のUSDJPY H1 Bid/Askを自動取得する。スケジュールの遅延や欠落はあり得る。

取得ファイルはActionsの一時領域だけで検証し、GPG AES256で暗号化した fxcm-usdjpy-h1.tar.gpg のみをActions artifactへ30日保管する。
このartifactは公開リポジトリのアクセス権やキー漏えいのリスクを残し、完全な非公開ストレージではない。データ自体はActions Secretに保存されない。

artifactのZIPをPC/Termuxへ保存後、暗号化ファイルを安全な端末で復号：

gpg --output fxcm-usdjpy-h1.tar --decrypt fxcm-usdjpy-h1.tar.gpg
mkdir -p fxcm-private
tar -xf fxcm-usdjpy-h1.tar -C fxcm-private

bidask_h1.csv と provenance.json をWebチャートに2ファイル同時に読込。取引注文・OANDA接続・自動学習承認は行わない。

preflight success と fetch-encrypt skipped の組み合わせは、実データ取得未実行を意味する。実取得を主張できるのはfetch-encrypt成功時のみ。

GitHub Secrets公式：https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets
