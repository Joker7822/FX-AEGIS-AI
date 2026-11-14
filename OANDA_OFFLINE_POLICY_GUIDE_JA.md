# FX AEGIS AI v2.7 — OANDA通信なしのポリシー監査

更新：2026-11-15（日本時間）

## 結論

実際のOANDA APIアクセス、トークン照会、データ取得を一切行わずに、GitHub Actionsのコードに書かれた安全条件を監査します。
この検査は**GitHub上の変数・Secretの設定値を取得しません**。手動OANDAワークフローの起動も行いません。

## 検査内容

- OANDA研究ワークフローは workflow_dispatch だけで起動し、定期実行・pushで起動しない
- 初期選択は preflight-only と confirm_private_download=no
- 権限は contents: read のみ
- readiness ジョブはトークンを参照せず、定型のechoと条件分岐のみ
- 実データ研究ジョブには、手動入力2件とGitHub Variables2件の全条件が必須
- CLIの明示的 --enable-api、トークン・利用許諾の事前検査、利用後のファイル削除が維持されている
- 生データをGitHub Artifact、Pages、git pushへ公開しない

**注意：** これは保守的な静的監査であり、YAML全体の形式的証明ではありません。OANDAの実際の利用条件・Secretの有無・通信可否は確認できません。

## 実行方法

\`\`\`bash
python oanda_policy_audit.py
python -m unittest discover -s tests -p 'test_oanda_policy_audit.py' -v
\`\`\`

自動監査：GitHub Actions の「Offline OANDA Security Audit (No Broker Access)」を参照してください。
ワークフローの安全条件を壊す変更を検出したら FAIL にします。

[GitHub Actions](https://github.com/Joker7822/FX-AEGIS-AI/actions) /
[OANDA公式API](https://developer.oanda.com/rest-live-v20/instrument-ep/)
