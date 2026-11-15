# FX AEGIS AI v3.6 — 非公開H1スナップショットの原子的受け渡し

更新日：**2026年11月15日（日本時間）**。

## 結論

`private_h1_inbox.py` は、ユーザーが**別途合法的に取得済み**の USD/JPY H1 Bid/Ask の完全なCSVと出典JSONを、ローカルの非公開受け渡しフォルダーに配置します。途中書込中の価格ファイルをモデルが読み取るのを防ぐため、両ファイルのSHA-256・価格整合性・最終バー確定状態を検査した後、`ready.json`と一緒に**原子的にディレクトリ名を確定**します。

監視処理 `poll` は、受領済みのモデル固定記録を前提に、古いH1終端から順に**1スナップショットだけ**検証・非公開領域へ複製し、`forward_supervisor.run_once()` と `forward_dashboard_bridge.write_export()` を呼びます。成功後に `processed/` に移動するまで受領完了扱いにはしません。

**本実装は外部APIへの接続、データのダウンロード、常時稼働ホストの設定、OSスケジューラーの登録、注文送信を行いません。** OANDAは従来の手動許可制のままで、発注機能も無効です。

## 事前準備：実ブローカーデータをユーザー側で管理

1. 正当に入手した、**新しい確定H1までを含む**完全な`bidask_h1.csv`と、内容のSHA-256に一致する`provenance.json`を用意します。データの利用許諾・時刻規則・提供元を自分で確認してください。ハッシュだけで真正性は確認できません。
2. 一度だけ、`forward_monitor.py init`で**過去の学習データを固定**します。監視器の`out`には、そのときに作成した`monitor_state.json`と`immutable_model_lock.json`が必要です。非公開ホストで最新確定H1データを継続して用意する仕組みは、まだ別途必要です。
3. 保存領域は公開リポジトリの`docs/`や`.github/`ではなく、`private_quotes/`と`private_reports/`またはアクセス制限したリポジトリ外のディレクトリを使用します。入力ファイルは75MiB、出典JSONは128KiB以内です。

## 1. 取得済みのファイルから受け渡しスナップショットを生成

```bash
python private_h1_inbox.py publish \
  --quotes private_quotes/dukascopy_latest/bidask_h1.csv \
  --provenance private_quotes/dukascopy_latest/provenance.json \
  --inbox private_quotes/inbox
```

正常時は`READY_PRIVATE_SNAPSHOT`と、内容のハッシュに基づくスナップショットIDを返します。同一内容を再投稿した場合は`ALREADY_PUBLISHED`です。ファイルのタイムスタンプやSHA-256は**利用者管理のローカル資料**であり、外部の時刻証明や提供元の署名ではありません。

受け渡し後の構造：

```text
private_quotes/inbox/
  <ハッシュ32桁>/
    bidask_h1.csv
    provenance.json
    ready.json
  processed/
    <検証・出力まで成功したハッシュ32桁>/
```

## 2. 完成したスナップショットを前向き監視へ渡す

```bash
python private_h1_inbox.py poll \
  --inbox private_quotes/inbox \
  --out private_reports/forward_monitor
```

- **成功：** `PRIVATE_SNAPSHOT_PROCESSED`。`private_reports/forward_monitor/forward_status.json`を端末内でダッシュボードに読み込めます。
- **新しいファイルがない：** `NO_READY_SNAPSHOT`。これは**市場データの正常配信を保証しません**。
- **異常：** `BLOCKED_PRIVATE_INBOX_REVIEW_REQUIRED`、または`BLOCKED_*`。元のスナップショットと監視ログを保全し、原因を調査してください。実行の自動復旧・モデルの無審査再固定は行いません。
- `poll`は毎回高々1件しか処理しません。複数の完了済みスナップショットがある場合は、古いH1終端から処理し、`queue_depth_remaining`で未処理件数を示します。**遅延取得した足は適時の前向き予測になりません。**
- 正常処理であっても本番の注文発注や約定成立を意味しません。

監視プロセス自身の稼働状態は以下で確認できます。

```bash
python forward_supervisor.py health \
  --out private_reports/forward_monitor \
  --max-heartbeat-age-minutes 90
```

`health`も市場データの健全性を独立に証明できません。

## 3. 非公開ホストの定期実行について

OSスケジューラー（systemd timer等）の登録は**ユーザー側で明示的に行う作業**です。本リポジトリにブローカー価格の取得用スケジュールは登録しません。定期利用する場合、取得元システムが**完成したデータだけ**をローカルに保存した後に`publish`、続けて`poll`を1回ずつ実行してください。取引休止時には`NO_READY_SNAPSHOT`が正常に起こります。

- `ready.json`の無い途中ディレクトリは読み取りません。ロックが残った場合は、元の状態を保全してから調査してください。
- 同じ時間帯・同じ本数のH1なのに価格SHA-256が異なるスナップショットが複数ある場合、どちらも監視せず停止します。
- 初回のモデル凍結、データ権利の確認、障害回復時の独立した新規ジャーナル作成は人間の承認が必要です。
- 監視ログ・価格データ・予測履歴はGitHubにコミット・Artifactとして配布しません。

## 4. テスト

```bash
python -m unittest discover -s tests -p 'test_private_h1_inbox.py' -v
python -m unittest discover -s tests -v
```

統合テストはすべて**人工のH1データ**を使用します。取引所やブローカーが提示した実価格で試験したものではありません。取得の適法性、外部時刻の独立認証、実際の注文執行と収益性は、この動作テストでは確認できません。

参照コード：[`execution_lab.py`](execution_lab.py)、[`forward_monitor.py`](forward_monitor.py)、[`forward_supervisor.py`](forward_supervisor.py)、[`forward_dashboard_bridge.py`](forward_dashboard_bridge.py)、[`DUKASCOPY_OFFLINE_GUIDE_JA.md`](DUKASCOPY_OFFLINE_GUIDE_JA.md)。
