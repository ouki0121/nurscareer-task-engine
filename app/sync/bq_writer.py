"""BigQuery へデータを書き込む（Upsert）モジュール"""

from google.cloud import bigquery
from google.oauth2.service_account import Credentials
from datetime import datetime
import re
import os
import logging

logger = logging.getLogger(__name__)


def get_bq_client(project_id: str, credentials_path: str) -> bigquery.Client:
    creds_path = os.path.expanduser(credentials_path)
    credentials = Credentials.from_service_account_file(creds_path)
    return bigquery.Client(project=project_id, credentials=credentials)


def parse_date(value: str) -> str | None:
    """日付文字列をYYYY-MM-DD形式に正規化。パース不能ならNone"""
    if not value or not value.strip():
        return None
    value = value.strip()
    patterns = [
        (r"^\d{4}/\d{1,2}/\d{1,2}$", "%Y/%m/%d"),
        (r"^\d{4}-\d{1,2}-\d{1,2}$", "%Y-%m-%d"),
        (r"^\d{4}年\d{1,2}月\d{1,2}日$", "%Y年%m月%d日"),
    ]
    for pattern, fmt in patterns:
        if re.match(pattern, value):
            try:
                return datetime.strptime(value, fmt).strftime("%Y-%m-%d")
            except ValueError:
                continue
    return None


def parse_currency(value: str) -> int | None:
    """通貨文字列を整数に変換。¥1,234,567 → 1234567"""
    if not value or not value.strip():
        return None
    cleaned = value.replace("¥", "").replace(",", "").replace(" ", "").strip()
    if cleaned.startswith("-"):
        sign = -1
        cleaned = cleaned[1:]
    else:
        sign = 1
    try:
        return sign * int(float(cleaned))
    except (ValueError, TypeError):
        return None


def transform_deal_record(raw: dict, row_index: int) -> dict:
    """ヨミ表の1行をBQスキーマに変換"""
    return {
        "deal_id": f"DEAL-{row_index:05d}",
        "registration_date": parse_date(raw.get("登録日", "")),
        "line_user_id": raw.get("LINE ID", "") or None,
        "friend_registration_month": raw.get("友達登録月", "") or None,
        "agent_name": raw.get("担当者", "") or None,
        "candidate_name": raw.get("求職者名", "") or None,
        "forecast_month": raw.get("受注予測月", "") or None,
        "start_month": raw.get("入職予測月", "") or None,
        "deal_source": raw.get("新規流入チャネル", "") or None,
        "crm_channel": raw.get("CRMチャネル", "") or None,
        "referral_company": raw.get("IF／送客企業", "") or None,
        "segment": raw.get("セグ", "") or None,
        "status": raw.get("客観ステータス", "") or None,
        "subjective_status": raw.get("主観ステータス", "") or None,
        "difficulty": raw.get("難ありステータス", "") or None,
        "estimated_amount": parse_currency(raw.get("見積・売上金額（税抜）", "")),
        "cost": parse_currency(raw.get("原価（マージン）", "")),
        "gross_profit": parse_currency(raw.get("売上総利益", "")),
        "refund_amount": parse_currency(raw.get("返金・辞退金額", "")),
        "placement_company": raw.get("入職先名", "") or None,
        "form_response_date": parse_date(raw.get("フォーム回答日", "")),
        "first_appointment_date": parse_date(raw.get("初回アポ日", "")),
        "job_match_date": parse_date(raw.get("求人まる日", "")),
        "interview_date": parse_date(raw.get("面接予定日", "")),
        "offer_acceptance_date": parse_date(raw.get("内定承諾日", "")),
        "payment_due_date": parse_date(raw.get("入金予定日", "")),
        "refund_end_date": parse_date(raw.get("返金規定終了日", "")),
        "last_action_date": parse_date(raw.get("最終アクション日", "")),
        "next_appointment": parse_date(raw.get("次回アポ", "")),
        "synced_at": datetime.utcnow().isoformat(),
    }


def transform_invoice_record(raw: dict, row_index: int) -> dict:
    """請求管理シートの1行をBQスキーマに変換"""
    return {
        "invoice_id": f"INV-{row_index:05d}",
        "company_name": raw.get("法人名", "") or None,
        "candidate_name": raw.get("求職者名", "") or None,
        "order_date": parse_date(raw.get("受注日", "")),
        "agent_name": raw.get("mela担当者名", "") or None,
        "contact_department": raw.get("部署・担当者名", "") or None,
        "contact_email": raw.get("請求送付連絡先", "") or None,
        "address": raw.get("送付先住所", "") or None,
        "start_month": raw.get("入職月", "") or None,
        "category": raw.get("カテゴリー", "") or None,
        "amount_excl_tax": parse_currency(raw.get("請求額（税抜）", "")),
        "amount_incl_tax": parse_currency(raw.get("請求額（税込）", "")),
        "invoice_date": parse_date(raw.get("請求日", "")),
        "payment_date": parse_date(raw.get("入金日", "")),
        "refund_amount": parse_currency(raw.get("返金金額（税抜）", "")),
        "payment_status": raw.get("ステータス（宗像）", "") or None,
        "synced_at": datetime.utcnow().isoformat(),
    }


def load_to_bq(
    client: bigquery.Client,
    dataset: str,
    table_name: str,
    records: list[dict],
) -> int:
    """BQテーブルにTRUNCATE + LOADで書き込み（Upsert簡易版）"""
    table_ref = f"{client.project}.{dataset}.{table_name}"

    job_config = bigquery.LoadJobConfig(
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
        autodetect=True,
    )

    job = client.load_table_from_json(records, table_ref, job_config=job_config)
    job.result()

    table = client.get_table(table_ref)
    logger.info(f"Loaded {table.num_rows} rows to {table_ref}")
    return table.num_rows
