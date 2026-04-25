"""Sheets → BQ 同期のオーケストレーター"""

import logging
from .sheets_reader import (
    get_sheets_client,
    load_config,
    read_deals,
    read_invoices,
    read_companies,
    read_ca_targets,
    read_lead_transfer_log,
    read_job_seekers,
)
from .bq_writer import (
    get_bq_client,
    transform_deal_record,
    transform_invoice_record,
    load_to_bq,
)

logger = logging.getLogger(__name__)


def run_sync() -> dict:
    """全シートをBQに同期。結果サマリーを返す"""
    config = load_config()
    gcp = config["gcp"]
    sheets_config = config["sheets"]

    sheets_client = get_sheets_client()
    bq_client = get_bq_client(gcp["project_id"], gcp["credentials_path"])
    dataset = gcp["dataset"]

    results = {}

    # 1. セールスヨミ表 → deals
    try:
        raw_deals = read_deals(sheets_client, config)
        deals = [
            transform_deal_record(r, i)
            for i, r in enumerate(raw_deals, start=1)
            if r.get("求職者名", "").strip()  # 名前がない行はスキップ
        ]
        count = load_to_bq(bq_client, dataset, "deals", deals)
        results["deals"] = {"status": "ok", "rows": count}
        logger.info(f"deals: {count} rows synced")
    except Exception as e:
        results["deals"] = {"status": "error", "message": str(e)}
        logger.error(f"deals sync failed: {e}")

    # 2. ★請求管理シート → invoices
    try:
        raw_invoices = read_invoices(sheets_client, config)
        invoices = [
            transform_invoice_record(r, i)
            for i, r in enumerate(raw_invoices, start=1)
            if r.get("法人名", "").strip()
        ]
        count = load_to_bq(bq_client, dataset, "invoices", invoices)
        results["invoices"] = {"status": "ok", "rows": count}
        logger.info(f"invoices: {count} rows synced")
    except Exception as e:
        results["invoices"] = {"status": "error", "message": str(e)}
        logger.error(f"invoices sync failed: {e}")

    # 3. 医療法人DB → companies
    try:
        raw_companies = read_companies(sheets_client, config)
        companies = [
            {
                "company_id": f"CO-{i:04d}",
                "company_name": r.get("事業所名", "") or None,
                "corporate_name": r.get("法人格", "") or None,
                "address": r.get("住所", "") or None,
                "contact_department": r.get("担当部署／役職", "") or None,
                "contact_name": r.get("担当者様名", "") or None,
                "email": r.get("メールアドレス", "") or None,
                "status": r.get("ステータス", "") or None,
                "prefecture": r.get("都道府県", "") or None,
                "interview_style": r.get("面談スタイル", "") or None,
                "fee_level": r.get("手数料が高い", "") or None,
                "facility_quality": r.get("施設が綺麗か", "") or None,
                "interview_difficulty": r.get("面接難易度", "") or None,
                "placement_count": r.get("紹介実績社数", "") or None,
                "offer_count": r.get("内定実績者数", "") or None,
                "acceptance_count": r.get("承諾実績者数", "") or None,
                "is_blacklisted": r.get("地雷", "") == "TRUE",
            }
            for i, r in enumerate(raw_companies, start=1)
            if r.get("事業所名", "").strip()
        ]
        count = load_to_bq(bq_client, dataset, "companies", companies)
        results["companies"] = {"status": "ok", "rows": count}
        logger.info(f"companies: {count} rows synced")
    except Exception as e:
        results["companies"] = {"status": "error", "message": str(e)}
        logger.error(f"companies sync failed: {e}")

    # 4. 自動転送ログ → lead_transfer_log
    try:
        raw_log = read_lead_transfer_log(sheets_client, config)
        count = load_to_bq(bq_client, dataset, "lead_transfer_log", raw_log)
        results["lead_transfer_log"] = {"status": "ok", "rows": count}
        logger.info(f"lead_transfer_log: {count} rows synced")
    except Exception as e:
        results["lead_transfer_log"] = {"status": "error", "message": str(e)}
        logger.error(f"lead_transfer_log sync failed: {e}")

    # 5. 求職者DB → job_seekers
    try:
        raw_seekers = read_job_seekers(sheets_client, config)
        job_seekers = [
            {
                "job_seeker_id": r.get("求職者ID", "") or None,
                "line_user_id": r.get("LINE UserID", "") or None,
                "candidate_name": r.get("求職者名", "") or None,
                "first_registration_date": r.get("初回登録日", "") or None,
                "friend_registration_month": r.get("友達登録月", "") or None,
                "first_channel": r.get("初回流入チャネル", "") or None,
                "segment": r.get("セグメント", "") or None,
                "deal_count": int(r.get("取引数", "0") or "0"),
            }
            for r in raw_seekers
            if r.get("求職者名", "").strip()
        ]
        count = load_to_bq(bq_client, dataset, "job_seekers", job_seekers)
        results["job_seekers"] = {"status": "ok", "rows": count}
        logger.info(f"job_seekers: {count} rows synced")
    except Exception as e:
        results["job_seekers"] = {"status": "error", "message": str(e)}
        logger.error(f"job_seekers sync failed: {e}")

    return results
