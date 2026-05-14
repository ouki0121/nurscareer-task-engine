"""Cloud Run エントリポイント

エンドポイント:
  POST /sync          - Sheets → BQ 同期（v2で使用。v1では不要）
  POST /generate-tasks - タスク生成 + Slack投稿（毎朝8:00 JST）
  GET  /health        - ヘルスチェック

v1: Sheets API直読み（BQ不要）
v2: BQ経由（Sheets → BQ同期 → BQクエリ）
"""

import os
import logging
import yaml
from flask import Flask, jsonify, request

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)


def load_config() -> dict:
    config_path = os.path.join(os.path.dirname(__file__), "..", "config", "nurscareer.yaml")
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


@app.route("/sync", methods=["POST"])
def sync():
    """Sheets → BQ 同期（v2用。v1ではスキップ可能）"""
    try:
        from .sync.runner import run_sync
        results = run_sync()
        return jsonify({"status": "ok", "results": results})
    except Exception as e:
        logger.error(f"Sync failed: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/generate-tasks", methods=["POST"])
def generate_tasks():
    """タスク生成 + Slack投稿（v1: Sheets API直読み）"""
    try:
        config = load_config()

        # v1: Sheets APIから直接読み取り
        from .sync.sheets_reader import (
            get_sheets_client,
            read_deals,
            read_invoices,
        )
        from .sync.bq_writer import (
            transform_deal_record,
            transform_invoice_record,
        )

        sheets_client = get_sheets_client()

        # ヨミ表（deals）読み取り + 正規化
        logger.info("Reading deals from Sheets...")
        raw_deals = read_deals(sheets_client, config)
        deals = [
            transform_deal_record(r, i)
            for i, r in enumerate(raw_deals, start=1)
            if r.get("求職者名", "").strip()
        ]
        logger.info(f"Loaded {len(deals)} deals")

        # 請求管理シート（invoices）読み取り + 正規化
        logger.info("Reading invoices from Sheets...")
        raw_invoices = read_invoices(sheets_client, config)
        invoices = [
            transform_invoice_record(r, i)
            for i, r in enumerate(raw_invoices, start=1)
            if r.get("法人名", "").strip()
        ]
        logger.info(f"Loaded {len(invoices)} invoices")

        # CA別目標をKPI週次シートから取得
        ca_targets = _load_ca_targets_from_sheets(sheets_client, config)

        # タスク生成
        from .engine.task_generator import TaskEngine
        engine = TaskEngine(
            deals=deals,
            invoices=invoices,
            ca_targets=ca_targets,
            config=config,
        )
        all_tasks = engine.generate_all()

        # dry_run パラメータでSlack投稿をスキップ可能
        dry_run = request.args.get("dry_run", "false").lower() == "true"

        if dry_run:
            # dry_run: Slack投稿せずプレビューを返す
            from .notifier.slack_poster import format_task_list
            results = {}
            for agent_name, task_list in all_tasks.items():
                results[agent_name] = {
                    "task_count": len(task_list.tasks),
                    "message": format_task_list(task_list),
                }
            return jsonify({
                "status": "ok",
                "mode": "dry_run",
                "agents": len(all_tasks),
                "results": results,
            })

        # Slack投稿（共通チャネルにCA別1メッセージずつ）
        from .notifier.slack_poster import get_slack_client, post_tasks_to_slack
        slack_client = get_slack_client()
        task_channel = config.get("slack", {}).get("task_channel")

        results = {}
        for agent_name, task_list in all_tasks.items():
            success = post_tasks_to_slack(slack_client, task_list, channel=task_channel)
            results[agent_name] = {
                "task_count": len(task_list.tasks),
                "posted": success,
            }

        return jsonify({
            "status": "ok",
            "mode": "live",
            "agents": len(all_tasks),
            "results": results,
        })

    except Exception as e:
        logger.error(f"Task generation failed: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


def _load_ca_targets_from_sheets(sheets_client, config: dict) -> dict:
    """KPI管理（週次）シートからCA別月間目標金額を取得"""
    from datetime import date
    try:
        spreadsheet = sheets_client.open_by_key(config["sheets"]["spreadsheet_id"])
        ws = spreadsheet.worksheet("KPI管理（週次）")
        all_values = ws.get_all_values()

        # ヘッダー行（row 4, index 3）から当月のALL列を見つける
        header_row = all_values[3] if len(all_values) > 3 else []
        current_month = date.today().month
        month_col_idx = None
        for i, cell in enumerate(header_row):
            if f"{current_month}月ALL" in cell or f"{current_month}月(〜" in cell:
                month_col_idx = i
                break

        if month_col_idx is None:
            logger.warning(f"Could not find column for {current_month}月ALL")
            return {}

        # 「XX個人目標金額」の行を探してCA名と目標を取得
        ca_targets = {}
        for row in all_values:
            if not row or len(row) <= month_col_idx:
                continue
            cell_a = row[0].strip()
            if "個人目標金額" in cell_a:
                # "和田個人目標金額" → "和田"
                ca_name = cell_a.replace("個人目標金額", "").strip()
                try:
                    target = int(float(row[month_col_idx].replace(",", "").replace("¥", "").strip() or "0"))
                    if target > 0:
                        ca_targets[ca_name] = target
                        logger.info(f"CA target: {ca_name} = ¥{target:,}")
                except (ValueError, IndexError):
                    pass

        return ca_targets
    except Exception as e:
        logger.warning(f"Failed to load CA targets: {e}")
        return {}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=True)
