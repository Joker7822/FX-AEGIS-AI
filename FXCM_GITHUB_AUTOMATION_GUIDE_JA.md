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

## 取引週の開始境界（2026-11-20 JST）

FXCM公式の週次H1ファイルはUTC時刻で、FXCMが公表する外国為替の市場再開は日曜17:00～17:15米東部時間です。UTCでは夏時間21時台・標準時間22時台に当たるため、月曜0時開始のISO週との間に差が生じ得ます。

`SOURCE_CSV_WEEK_MISMATCH` に対しては、指定されたISO週の開始より前の**日曜21:00、22:00、23:00 UTC**の確定H1バーだけを、その翌ISO週に属する可能性がある取引週データとして受理します。次の日曜21:00以降、3時間を超える前倒し、および他週の日時は拒否します。元のUTC時刻と価格・Bid/Ask・ファイルハッシュは変更せず、全バーを検査し、週をまたぐ重複・逆順も拒否します。

この時間窓はFXCM公式の市場時間に基づく**限定的な互換対応**です。今回のエラーが日曜夜のバーによるものか、提供ファイル自体の週番号不整合によるものかは、再実行しない限り確認できません。フォーマット検証・利用許諾・暗号化・注文禁止は従来どおりです。

参考：https://github.com/fxcm/MarketData/blob/master/CandleData/README.md
市場時間：https://www.fxcm.com/markets/trading-details/trading-hours/

## 取得失敗時の安全な診断（2026-11-20 JST）

`fetch-encrypt` が失敗したときは、ジョブ内の `Acquire, validate, and encrypt only in runner temp` のログに `FXCM_SAFE_DIAGNOSTIC=...` を表示します。固定された分類コードのみを出し、HTTP応答本文・URLのクエリ・個別レート・ファイル内容・パスフレーズ・ローカル保存パスは出力しません。

- `SOURCE_HTTP_404` / `SOURCE_HTTP_403` / `SOURCE_HTTP_429` / `SOURCE_HTTP_5XX`: FXCM公式週次ファイルのHTTP応答（原因確定には提供側の確認が必要）
- `SOURCE_CONNECTION_ERROR` / `SOURCE_TIMEOUT`: 通信失敗
- `SOURCE_CSV_...` / `SOURCE_GZIP_...`: データ形式・妥当性の検証失敗
- `H1_DATA_VALIDATION_FAILED`: 未分類の検証拒否。価格の安全性を確認できないため取得中断
- `LOCAL_IO_FAILED`: 保存処理の失敗

分類コードは**失敗原因の調査用**であり、取得成功や出典真正性を保証しません。自動再試行、未検証データの受入れ、実売買は有効化されません。修正後に `fetch-and-encrypt` を明示的に再実行して判断してください。

GitHub Secrets公式：https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets
