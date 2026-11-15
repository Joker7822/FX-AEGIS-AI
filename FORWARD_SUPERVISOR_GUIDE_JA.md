# FX AEGIS AI v3.4 — 非公開H1監視の稼働管理と改ざん検出

更新：**2026-11-15（日本時間）**。**研究専用です。実売買、注文API、OANDA自動接続は一切有効化しません。**

## 結論

`forward_supervisor.py` は、既存の `forward_monitor.py` が固定したモデルを**手動または利用者の非公開ホスト上のスケジューラー**から1回だけ監視します。データのダウンロード、認証、定期実行の登録、モデルの再学習・自動昇格は**このプログラムにはありません**。

新しいH1足が見つかれば監視器の `observe` に渡し、結果を `operations_events.jsonl` に記録します。新しい足がない場合も、**プロセスが動作した記録**を残します。市場が開いていること、実際に価格が配信されたこと、第三者により時刻が証明されたこととは区別します。

## 利用開始前の必須作業

1. USD/JPYのH1 Bid/Ask履歴と `provenance.json` を**利用者が正当に取得し、非公開領域に保管**してください。価格ファイルの由来の真正性は独立検証していません。
2. 既存の [FORWARD_MONITOR_GUIDE_JA.md](FORWARD_MONITOR_GUIDE_JA.md) に従い、 `forward_monitor.py init` で履歴モデルを固定します。**監視器は自動で `init` しません。**
3. ブローカー価格CSVを新しい確定H1足を含む**全履歴スナップショット**に更新し、同時に `provenance.json` のSHA-256を更新します。前の価格を修正・削除した場合は監視器が停止します。

## 新しい確定足を監視する（通信・発注なし）

```bash
python forward_supervisor.py run-once \
  --quotes private_quotes/dukascopy_current/bidask_h1.csv \
  --provenance private_quotes/dukascopy_current/provenance.json \
  --out private_reports/forward_monitor
```

`run-once` はネットワークアクセスをせず、ローカルH1ファイルだけを読む**1回の処理**です。同じ足を再投入しても新しい予測は作りません。

別途確認済みの市場営業カレンダーで「この時刻に確定足が来るはず」と判断している場合だけ、オプション `--expect-new-bar` を加えられます。期待されたデータがない場合は `BLOCKED_EXPECTED_H1_MISSING` として停止します。**週末・取引休止期間には指定しないでください。** 営業カレンダーは本ソフトでは提供しません。

## 稼働監視・アラート連携

```bash
python forward_supervisor.py health \
  --out private_reports/forward_monitor \
  --max-heartbeat-age-minutes 90
```

終了コード：正常稼働 `0`、停止・監視未開始・ハートビート遅延 `2`、改変検出などの検査エラー `3`。監視サービスはこれを利用して**利用者管理の監視システム**へアラートを出せますが、GitHubへの公開投稿はしません。

以下の状態は同じ意味ではありません。

| 状態 | 意味 |
|---|---|
| `OBSERVED_USER_SUPPLIED_QUOTES` | 利用者提供データを監視に渡した。市場価格の真正性は未確認 |
| `OBSERVED_SYNTHETIC_TEST_ONLY` | 人工データの動作確認 |
| `IDLE_NO_NEW_H1` | 監視器は動いたが、追加されたH1足はない。市場営業中の配信正常性は未確認 |
| `STALE_SUPERVISOR_HEARTBEAT` | 設定時間を超えて監視器自体が稼働していない |
| `BLOCKED_EXPECTED_H1_MISSING` | 利用者が新足を期待したのに、新足がない |
| `BLOCKED_MODEL_DRIFT_REVIEW_REQUIRED` | v3.3の予測劣化判定で停止。自動再開しない |
| `BLOCKED_OPERATIONAL_ERROR` | 検証・入力・監視の処理に失敗。停止し人間が調査 |

**重要：** `health=healthy` は**監視プロセスの稼働**を示すだけで、**ブローカー配信の健全性やAIの利益の証明ではありません**。監視器は時間外・週末・祝日を自動判定できません。

## 監査と障害時の扱い

- `operations_events.jsonl` に各実行時刻、状態、観測件数、モニター状態のハッシュ、直前イベントのハッシュを順次保存。
- `operations_status.json` は最後のイベントのコピーです。イベントチェーンと不一致なら実行を拒否します。
- 別経路で `monitor_state.json` を変更しても、次回の `health` / `run-once` で拒否します。
- 観測処理エラーや予測劣化時には停止状態を記録し、**同じ監査ディレクトリでの再開機能はありません**。元の記録は保全し、人間が検証後に**別の独立したモデル固定・監査ディレクトリ**を作成してください。
- SHA-256の連結は偶発的なファイル変更検出用です。管理者がログ全体を書き換えた場合を防げる**電子署名や第三者時刻証明ではありません**。
- OSの時計は真正な第三者証拠ではありません。初回受信の厳密な外部証明には独立した時刻認証・証拠保管が別途必要です。

## 自分の非公開ホストで定期呼び出しを行う場合

運用ホストのタスクスケジューラー／systemd timerで、必要な間隔ごとに `run-once` を実行できます。例えば**H1確定から5分後**に利用者側で設定してください。ただし、利用許諾、取り込み時間、データ更新の完了、営業日、タイムゾーン、スナップショット書込の原子性を別途管理する必要があります。`--expect-new-bar` の自動指定は推奨しません。

**このGitHubリポジトリでは、実市場H1の定期取得・非公開ホストのセットアップ・スケジューラーの登録はしていません。** GitHub Actionsでは合成データのテストのみ実行します。

### 再現試験

```bash
python -m unittest discover -s tests -p 'test_forward_supervisor.py' -v
python -m unittest discover -s tests -p 'test_forward_monitor.py' -v
```

出典・関連実装：[`forward_monitor.py`](forward_monitor.py)、[`forward_paper.py`](forward_paper.py)、[`execution_lab.py`](execution_lab.py)、[GitHub Actions公式ドキュメント](https://docs.github.com/en/actions)。
