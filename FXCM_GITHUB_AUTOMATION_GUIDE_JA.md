# FX AEGIS AI — FXCM H1公開GitHub自動取得（暗号化保存）

更新日：2026-11-21（日本時間）。GitHub Actionsを公開リポジトリで利用し、価格データを平文公開しない構成。

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
設定後は毎週火曜日17:37 JSTに、FXCM週次CSVのファイル名に使われるISO週番号について**公式の月曜更新日を経過した最新のラベルから遡る4週**のUSDJPY H1 Bid/Askを取得する。スケジュールの遅延や欠落はあり得る。**11月21日(土)時点で2026-W46はHTTP 404だったため、改修後はW45を最新の取得対象とする。**

取得ファイルはActionsの一時領域だけで検証し、GPG AES256で暗号化した fxcm-usdjpy-h1.tar.gpg のみをActions artifactへ30日保管する。
このartifactは公開リポジトリのアクセス権やキー漏えいのリスクを残し、完全な非公開ストレージではない。データ自体はActions Secretに保存されない。

artifactのZIPをPC/Termuxへ保存後、暗号化ファイルを安全な端末で復号：

gpg --output fxcm-usdjpy-h1.tar --decrypt fxcm-usdjpy-h1.tar.gpg
mkdir -p fxcm-private
tar -xf fxcm-usdjpy-h1.tar -C fxcm-private

bidask_h1.csv と provenance.json をWebチャートに2ファイル同時に読込。取引注文・OANDA接続・自動学習承認は行わない。

preflight success と fetch-encrypt skipped の組み合わせは、実データ取得未実行を意味する。実取得を主張できるのはfetch-encrypt成功時のみ。

## FXCM週次CSVのファイルラベルと収録時間窓（2026-11-21 JST）

**現時点の取得コードでは次の厳格な時間窓を採用します。** FXCM公式READMEはH1週次ファイルを毎週月曜更新とし、公式のPythonサンプルはURLにISO週番号を使用、時刻はUTCと記載しています。ただし**URL週番号とファイル内の正確な収録日の対応は公式READMEに明記されていません**。

公開GitHub Actions #19・#22・#25・#29 で得た固定コード診断では、2026年W43・W44・W45のファイルはいずれも「指定されたISO週の日付に始まり、翌ISO週の日付に終わる」パターンで、隣接ファイル間に同一H1時刻はなく、空白が1～6日あります。また、2026-W46は11月21日(土)にHTTP 404でした。この観測と公式の月曜更新を合わせた**検証可能なファイルラベル解釈**を採用します。

- ファイル名 `{year}/{week}.csv.gz` の週を **W** とすると、許容する先頭は **Wの最終日・日曜21:00 UTC以降**です（夏冬時間のどちらにも対応する早めの下限）。
- 許容する末尾は**翌ISO週 W+1 の土曜日00:00 UTCより前**です。これはデータを長期間まで無条件に受け入れるためではなく、日曜夜～翌週金曜日の狭い時間窓に制限するためです。
- Wの月曜00:00～土曜20:00のバー、または翌ISO週の土曜以降のバーは受け入れず停止します。時刻の補正・バー削除・重複の黙認は行いません。
- 各週のファイル内でUTC整時、昇順、全8項目のBid/Ask OHLC、値域・スプレッド等を検証し、取得対象4ファイル間の重複・逆順も停止します。
- provenance.json内の非公開メタデータでは `week_partition_mode=FXCM_SUNDAY_LABEL_NEXT_WEEK` と記録します。

**公開済み週の保守的な選択**：現在のISO週番号を `W_now` としたとき、直近のファイルラベルは `W_now-2` です。ラベルWのFXCMセッションがW+1金曜日に完了し、W+2月曜日の更新日を迎えたものだけを対象にします。これによって、土曜日に最新完了暦週のまだ未公開のラベルを要求しません。ただし月曜の実際の更新時刻や配信停止までは保証できず、該当ファイルが404なら安全に失敗します。利用許諾・Secret・暗号化保存・注文禁止は変更しません。

これは**既存の実CSVに基づく条件付き技術仮説**であり、FXCM公式に週ラベル規則の追加確認が取れたことを意味しません。変更後、実FXCMデータに対して新しい厳格時間窓の検証と暗号化Artifact生成が成功するかを再確認する必要があります。旧方式の `SUNDAY_SESSION_START` / `ISO_CALENDAR_WEEK` は今後の本番ファイル受入れには使用しません（旧人工データ互換テストでのみ維持）。

公式出典：
- https://github.com/fxcm/MarketData/blob/master/CandleData/README.md
- https://github.com/fxcm/MarketData/blob/master/CandleData/CandleData(pandas).py
- https://www.fxcm.com/markets/trading-details/trading-hours/

実際の診断出典：
- https://github.com/Joker7822/FX-AEGIS-AI/actions/runs/44866107667
- https://github.com/Joker7822/FX-AEGIS-AI/actions/runs/44867047931
- https://github.com/Joker7822/FX-AEGIS-AI/actions/runs/44867820279
- https://github.com/Joker7822/FX-AEGIS-AI/actions/runs/44868803767

## 取得失敗時の安全な診断（2026-11-20 JST）

`fetch-encrypt` が失敗したときは、ジョブ内の `Acquire, validate, and encrypt only in runner temp` のログに `FXCM_SAFE_DIAGNOSTIC=...` を表示します。固定された分類コードのみを出し、HTTP応答本文・URLのクエリ・個別レート・ファイル内容・パスフレーズ・ローカル保存パスは出力しません。

- `SOURCE_HTTP_404` / `SOURCE_HTTP_403` / `SOURCE_HTTP_429` / `SOURCE_HTTP_5XX`: FXCM公式週次ファイルのHTTP応答（原因確定には提供側の確認が必要）
- `SOURCE_CONNECTION_ERROR` / `SOURCE_TIMEOUT`: 通信失敗
- `SOURCE_CSV_WEEK_BEFORE` / `SOURCE_CSV_WEEK_AFTER`: 許可された取引週より前／後のUTC時刻。価格や行内容はログに出ない
- `SOURCE_CSV_WEEK_BOUNDARY_MIXED` / `SOURCE_WEEK_CONVENTION_DRIFT`: 同一ファイルまたは複数週の週境界方式が矛盾し、安全のため停止
- `SOURCE_CSV_...` / `SOURCE_GZIP_...`: データ形式・妥当性の検証失敗
- `H1_DATA_VALIDATION_FAILED`: 未分類の検証拒否。価格の安全性を確認できないため取得中断
- `LOCAL_IO_FAILED`: 保存処理の失敗

### 週外H1バーの安全な追加診断（2026-11-21 JST）

`SOURCE_CSV_WEEK_BEFORE` / `SOURCE_CSV_WEEK_AFTER` が発生した場合、公開Actionsログに次の**分類のみ**を追加します。生の日時、価格、CSV内容、HTTP本文、秘密鍵は出しません。

- `FXCM_SAFE_WEEK_OFFSET=0_2H`: 許容週境界の外側へ0～2時間
- `FXCM_SAFE_WEEK_OFFSET=3_23H`: 同3～23時間
- `FXCM_SAFE_WEEK_OFFSET=1_6D`: 同1～6日
- `FXCM_SAFE_WEEK_OFFSET=7_27D`: 同7～27日
- `FXCM_SAFE_WEEK_OFFSET=28D_PLUS`: 同28日以上
- `FXCM_SAFE_ARCHIVE_POSITION=1..8`: **今回の実行で取得対象にした週次ファイルの順番**。実際のバー時刻やファイル内容ではなく、エラーを返したアーカイブの順序番号です。

上記の `FXCM_SAFE_WEEK_OFFSET` は、**最初に拒否されたH1行**の週境界からの相対距離です。その値だけではファイル全体の最終時刻や正しい週番号は確定できません。

### ファイル末尾までの追加スキャン（2026-11-21 JST）

`SOURCE_CSV_WEEK_AFTER` で拒否したときは、CSVを日時列だけで最後まで読み直し、機密性を維持した**集計分類のみ**を追加します。

- `FXCM_SAFE_FILE_TAIL_OFFSET`: 最後のH1時刻が許容終了境界を超えた相対距離。`0_2H` / `3_23H` / `1_6D` / `7_27D` / `28D_PLUS`、超過なしなら `NONE`。
- `FXCM_SAFE_FILE_AFTER_BARS`: 許容終了境界を超えるバーの本数。`NONE` / `1_3` / `4_24` / `25_120` / `121_PLUS`。
- ファイル中に日時の逆順・重複・無効な日時・過大な行数がある場合、集計は `UNAVAILABLE` とし、元の検証失敗は維持します。

### ISO週番号との対応診断（2026-11-21 JST）

FXCM公式のH1週次CSVはファイルパスに週番号を用い、公式PythonサンプルはISO週番号を計算しています。しかし各CSVの収録日時が実際にURLのISO週と一致するかは、個々のファイルで検証する必要があります。

`SOURCE_CSV_WEEK_AFTER` の場合、**最初と最後のH1のISO週がリクエスト週に対して何週ずれているか**を安全なラベルで追加表示します。

- `FXCM_SAFE_FIRST_ISO_WEEK`: ファイル最初の足が属するISO週（相対区分）
- `FXCM_SAFE_LAST_ISO_WEEK`: ファイル最後の足が属するISO週（相対区分）
- 固定区分：`FAR_BEFORE`（2週以上前）、`PREV_1`（1週前）、`REQUESTED`（指定ISO週）、`NEXT_1`（1週後）、`NEXT_2`（2週後）、`FAR_AFTER`（3週以上後）、`UNAVAILABLE`（無効な日時・並び順・データ量などによって判定不能）

例：`REQUESTED` → `NEXT_1` はファイルが指定週から翌週にまたがることを、`NEXT_1` → `NEXT_1` はファイル全体が翌ISO週に収まることを意味します。これらは**価格や生日時を出さずに区別する分類**であり、実際にそうなっているという事前の主張ではありません。

### 隣接週の収録範囲・重複診断（2026-11-21 JST）

`SOURCE_CSV_WEEK_AFTER` が発生し、対象4週中に**次の週次ファイルがある場合に限り**、すでに承認された同じFXCM H1取得先から次のファイルを追加で読み込みます。許可されたファイル名以外のURLは使わず、両ファイルのUTC H1日時列だけを比較します。**検証エラーのため本番取得は引き続き中断し、両ファイルを結合・削除・保存しません。**

- `FXCM_SAFE_ADJACENT_RELATION=OVERLAP_SHARED`：収録時刻の範囲に重なりがあり、同一H1日時も存在
- `OVERLAP_NO_SHARED`：収録時刻の範囲は重なるが、同一H1日時は存在しない
- `CONTIGUOUS`：前ファイル末尾と次ファイル先頭がUTCで1時間連続
- `GAP`：両ファイル間に1時間超の空き（通常の週末休場を含み得る）
- `UNAVAILABLE`：次ファイルを取得できない、CSV構造が不正、日時が整時UTCでない、逆順・重複がある、上限超過など
- `FXCM_SAFE_ADJACENT_SHARED_H1`：同一UTC日時の共有H1本数の固定区分 `NONE` / `1_3` / `4_24` / `25_120` / `121_PLUS` / `UNAVAILABLE`

### 最大4週の収録範囲マップ（2026-11-21 JST）

FXCM公式文書は週番号付きH1ファイルとUTCタイムスタンプを明記しますが、各週次ファイルが暦上のISO週で正確に閉じるか、隣接ファイルの空白が週末休場かどうかは明確にしていません。

`SOURCE_CSV_WEEK_AFTER` で拒否された場合、その実行で**既に取得許可を受けている残りの週次ファイル**（通常は全4週のうち残り3週）を取得して、時刻列だけで順次比較します。

公開Actionsログは各ファイルにつき1行の固定分類で、例えば
`FXCM_SAFE_ARCHIVE_MAP_2=REQUESTED/REQUESTED/GAP/NONE/1_6D`
と記録されます。接尾の `2` は許可された取得対象中の**2番目**を意味します。

値の順序は以下の通りです：
- **先頭H1のISO週区分**：`PREV_1`、`REQUESTED`、`NEXT_1`等（各ファイル自身のURL指定ISO週を基準）
- **末尾H1のISO週区分**：同上
- **直前のファイルとの関係**：`OVERLAP_SHARED` / `OVERLAP_NO_SHARED` / `CONTIGUOUS` / `GAP` / `UNAVAILABLE`
- **両ファイルの同一H1日時の件数区分**：`NONE` / `1_3` / `4_24` / `25_120` / `121_PLUS` / `UNAVAILABLE`
- **時系列の空白時間の区分**：`NONE` / `0_2H` / `3_23H` / `1_6D` / `7_27D` / `28D_PLUS` / `UNAVAILABLE`。差の分類は「次のファイル先頭 − 前ファイル末尾 − 1時間」です。

### 4番目のファイルが `UNAVAILABLE` になる理由の切り分け（2026-11-21 JST）

指定済み週次ファイルの日時・範囲診断が `UNAVAILABLE` になっても、**原因を特定したとは限りません**。そこで、`FXCM_SAFE_ARCHIVE_STATUS_N`（Nは取得対象の順序）を追加し、次のような**固定の安全なエラー識別子だけ**を記録します。

- `VALID`：日時列の読み取り・整時・昇順・重複なしを確認できた（価格やCSV全体を検証した意味ではない）
- `SOURCE_HTTP_404` / `SOURCE_HTTP_403` / `SOURCE_HTTP_429` / `SOURCE_HTTP_5XX` / `SOURCE_HTTP_OTHER`：FXCM週次ファイルへのHTTP応答分類
- `SOURCE_CONNECTION_ERROR` / `SOURCE_TIMEOUT`：ネットワーク応答不良
- `SOURCE_GZIP_INVALID` / `SOURCE_GZIP_CORRUPT` / `SOURCE_GZIP_OVERSIZED` / `SOURCE_REDIRECT_REJECTED`：圧縮形式やリダイレクト検証で拒否
- `SOURCE_CSV_ENCODING` / `SOURCE_CSV_HEADER` / `SOURCE_CSV_ROW` / `SOURCE_CSV_NO_BARS` / `SOURCE_CSV_TOO_MANY_BARS`：CSV構造または行数
- `SOURCE_CSV_TIMESTAMP` / `SOURCE_CSV_TIMESTAMP_ALIGNMENT` / `SOURCE_CSV_ORDER`：日時の解釈・UTC整時・順序異常
- 上記に当てはまらない場合は、既存の `safe_failure_code` による固定の失敗分類

この値には**HTTP応答本文・正確な日付・価格・URL・秘密情報は含めません**。第4ファイルの実際の失敗理由は、修正後の本人承認済み `fetch-and-encrypt` 再実行が必要です。なお `VALID` であっても元のH1収録期間エラーは継続し、暗号化Artifactは生成しません。

取得失敗・日時不正・重複・逆順・行数超過などで信頼性のある分類が作れない場合は `UNAVAILABLE` です。欠損があっても残りの許可済みファイルの調査を続けますが、**通常の検証エラーは変更せず、保存・公開・暗号化・発注には進みません**。確認できるのは日時の並びと範囲だけであり、価格の同一性や欠損原因を確定するものではありません。

**公開するのは固定分類だけ**です。時刻・週番号・ファイルURL・個別レート・Bid/Ask・秘密情報・実データはログに出しません。日時の一致は価格値の一致を意味しません。価格の一致検証、データの重複解消、適正なファイル境界の確定は別の課題です。

これは診断専用の再走査です。**FXCM提供の日時・価格の書き換え、データ削除、重複の黙認、週境界の拡張は行いません。** 最初のエラーに続いて追加される集計は、問題のファイル全体の傾向を判断するためだけに使います。具体的な日時や価格はActionsログに出しません。取得・検証条件は緩和せず、エラー時は暗号化Artifactを作りません。この情報によって大幅な週番号のずれか小幅な時刻区切りのずれかを切り分け、次の修正を安全に判断します。

分類コードは**失敗原因の調査用**であり、取得成功や出典真正性を保証しません。自動再試行、未検証データの受入れ、実売買は有効化されません。修正後に `fetch-and-encrypt` を明示的に再実行して判断してください。

GitHub Secrets公式：https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets
