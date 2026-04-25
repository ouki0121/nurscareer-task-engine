"""Cloud Run エントリポイント

エンドポイント:
  POST /sync          - Sheets → BQ 同期（10分に1回 Cloud Scheduler から呼ばれる）
  POST /generate-tasks - タスク生成 + Slack投稿（毎朝8:00 JST）
  GET  /health        - ヘルスチェック
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
    """Sheets → BQ 同期"""
    try:
        from .sync.runner import run_sync
        results = run_sync()
        return jsonify({"status": "ok", "results": results})
    except Exception as e:
        logger.error(f"Sync failed: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/generate-tasks", methods=["POST"])
def generate_tasks():
    """タスク生成 + Slack投稿"""
    try:
        config = load_config()
        gcp = config["gcp"]

        # BQからデータ読み取り
        from google.cloud import bigquery
        from google.oauth2.service_account import Credentials

        creds_path = os.path.expanduser(gcp["credentials_path"])
        credentials = Credentials.from_service_account_file(creds_path)
        bq_client = bigquery.Client(project=gcp["project_id"], credentials=credentials)
        dataset = gcp["dataset"]

        # deals テーブル読み取り
        deals_query = f"SELECT * FROM `{gcp['project_id']}.{dataset}.deals`"
        deals = [dict(row) for row in bq_client.query(deals_query).result()]

        # invoices テーブル読み取り
        invoices_query = f"SELECT * FROM `{gcp['project_id']}.{dataset}.invoices`"
        invoices = [dict(row) for row in bq_client.query(invoices_query).result()]

        # CA別目標（BQまたはconfigから。v1はconfig固定値で代用可能）
        ca_targets = _load_ca_targets(bq_client, gcp["project_id"], dataset)

        # タスク生成
        from .engine.task_generator import TaskEngine
        engine = TaskEngine(
            deals=deals,
            invoices=invoices,
            ca_targets=ca_targets,
            config=config,
        )
        all_tasks = engine.generate_all()

        # Slack投稿
        from .notifier.slack_poster import get_slack_client, post_tasks_to_slack
        slack_client = get_slack_client()

        # dry_run パラメータでSlack投稿をスキップ可能
        dry_run = request.args.get("dry_run", "false").lower() == "true"

        results = {}
        for agent_name, task_list in all_tasks.items():
            if dry_run:
                from .notifier.slack_poster import format_task_list
                results[agent_name] = {
                    "task_count": len(task_list.tasks),
                    "message_preview": format_task_list(task_list)[:500],
                }
            else:
                success = post_tasks_to_slack(slack_client, task_list)
                results[agent_name] = {
                    "task_count": len(task_list.tasks),
                    "posted": success,
                }

        return jsonify({
            "status": "ok",
            "agents": len(all_tasks),
            "results": results,
        })

    except Exception as e:
        logger.error(f"Task generation failed: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


def _load_ca_targets(bq_client, project_id: str, dataset: str) -> dict:
    """CA別月間目標を取得。v1は空dictで返す（目標ギャップ計算はv2で精緻化）"""
    try:
        query = f"SELECT * FROM `{project_id}.{dataset}.ca_targets` LIMIT 1"
        list(bq_client.query(query).result())
        # TODO: KPI管理（週次）のCA別目標をパースする
        return {}
    except Exception:
        logger.info("ca_targets table not found. Using empty targets (v1)")
        return {}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=True)
