# ナースキャリア タスクエンジン

PJ名: ナースキャリア生産性向上PJ

## 概要
CA（キャリアエージェント）向けに毎朝8:00 JSTにSlackでデイリータスクを自動生成・投稿するシステム。
スプレッドシートのデータをBQに同期し、BQを唯一のデータソースとしてタスクロジックを実行する。

## アーキテクチャ
```
Google Sheets (入力元・書き換えない)
    ↓ 10分に1回バッチ同期
BigQuery (データソース)
    ↓ 毎朝8:00 JST
Task Engine (Cloud Run / Python)
    ↓
Slack DM (CA別にタスク投稿)
```

## 実行環境
- Cloud Run (Python)
- GCP Project: `skilled-loader-457802-c9`
- BQ Dataset: `hr_nurse_career`（新スキーマで再構築）
- 認証: `~/.claude/secrets/bigquery-mcp.json`（サービスアカウント）

## データソース（Sheets → BQ同期対象）
| シート | gid | BQテーブル |
|--------|-----|-----------|
| セールスヨミ表 | 2007688165 | deals |
| ★請求管理シート | 411151869 | invoices |
| KPI管理（週次） | 853694422 | ca_targets |
| 自動転送ログ | 2011711249 | lead_transfer_log |
| 医療法人DB | 1123026588 | companies |

Spreadsheet ID: `1A2M9_FhcCzLQsxJ7H3OwOrfEgWjDpezzBu6SBboJFck`

## タスク生成の優先度（①が最優先）
1. フォーム回答へのアタック
2. 内定提示 → クロージング
3. 面接セット済み → 面接フォロー
4. 求まる済み → 面接セット推進
5. 不合格・再調整中 → リカバリー
6. 早期退職（返金）→ 再展開 or リリース
7. SQL → 時期到来アタック
8. 請求管理シート反映済み/入金確認済み → 入社後フォロー
9. MQL → 掘り起こし（目標ギャップ時のみ）
10. 内定承諾 → 請求管理シート移行

## 重要な設計原則
- スプレッドシートは**読み取り専用**。書き換えない
- BQのみをデータソースとする
- ステータス判定はM列（客観ステータス）+ U〜Z列の日付で行う
- MQLは担当なし（共有プール）。それ以外は担当制
- 受注角度: 求まる済み=33%, 面接セット済み=35%, 内定提示=90%
- 難あり案件は受注角度を減算

## 仕様書
詳細仕様は `~/.claude/projects/-Users-keitaiito/memory/nurscareer_ai_task_engine_spec.md` を参照
