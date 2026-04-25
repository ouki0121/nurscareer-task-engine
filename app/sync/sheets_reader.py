"""Google Sheets からデータを読み取るモジュール"""

import gspread
from google.oauth2.service_account import Credentials
from typing import Optional
import os
import yaml


def load_config() -> dict:
    config_path = os.path.join(os.path.dirname(__file__), "..", "..", "config", "nurscareer.yaml")
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_sheets_client(credentials_path: Optional[str] = None) -> gspread.Client:
    config = load_config()
    creds_path = credentials_path or os.path.expanduser(config["gcp"]["credentials_path"])
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets.readonly",
        "https://www.googleapis.com/auth/drive.readonly",
    ]
    credentials = Credentials.from_service_account_file(creds_path, scopes=scopes)
    return gspread.authorize(credentials)


def read_sheet_as_records(
    client: gspread.Client,
    spreadsheet_id: str,
    tab_name: str,
    header_row: int,
    data_start_row: int,
) -> list[dict]:
    """シートを読み取り、ヘッダー行をキーとしたdict のリストを返す"""
    spreadsheet = client.open_by_key(spreadsheet_id)
    worksheet = spreadsheet.worksheet(tab_name)

    all_values = worksheet.get_all_values()
    if len(all_values) < header_row:
        return []

    headers = all_values[header_row - 1]  # 0-indexed
    records = []
    for row_idx in range(data_start_row - 1, len(all_values)):
        row = all_values[row_idx]
        if not any(cell.strip() for cell in row):
            continue  # 空行スキップ
        record = {}
        for col_idx, header in enumerate(headers):
            header = header.strip()
            if not header:
                continue
            value = row[col_idx] if col_idx < len(row) else ""
            record[header] = value
        records.append(record)
    return records


def read_deals(client: gspread.Client, config: dict) -> list[dict]:
    """セールスヨミ表を読み取り"""
    sheets_config = config["sheets"]
    tab = sheets_config["tabs"]["deals"]
    return read_sheet_as_records(
        client=client,
        spreadsheet_id=sheets_config["spreadsheet_id"],
        tab_name=tab["name"],
        header_row=tab["header_row"],
        data_start_row=tab["data_start_row"],
    )


def read_invoices(client: gspread.Client, config: dict) -> list[dict]:
    """★請求管理シートを読み取り"""
    sheets_config = config["sheets"]
    tab = sheets_config["tabs"]["invoices"]
    return read_sheet_as_records(
        client=client,
        spreadsheet_id=sheets_config["spreadsheet_id"],
        tab_name=tab["name"],
        header_row=tab["header_row"],
        data_start_row=tab["data_start_row"],
    )


def read_companies(client: gspread.Client, config: dict) -> list[dict]:
    """医療法人DBを読み取り"""
    sheets_config = config["sheets"]
    tab = sheets_config["tabs"]["companies"]
    return read_sheet_as_records(
        client=client,
        spreadsheet_id=sheets_config["spreadsheet_id"],
        tab_name=tab["name"],
        header_row=tab["header_row"],
        data_start_row=tab["data_start_row"],
    )


def read_ca_targets(client: gspread.Client, config: dict) -> list[dict]:
    """KPI管理（週次）からCA別目標を読み取り"""
    sheets_config = config["sheets"]
    tab = sheets_config["tabs"]["ca_targets"]
    return read_sheet_as_records(
        client=client,
        spreadsheet_id=sheets_config["spreadsheet_id"],
        tab_name=tab["name"],
        header_row=tab["header_row"],
        data_start_row=tab["data_start_row"],
    )


def read_lead_transfer_log(client: gspread.Client, config: dict) -> list[dict]:
    """自動転送ログを読み取り"""
    sheets_config = config["sheets"]
    tab = sheets_config["tabs"]["lead_transfer_log"]
    return read_sheet_as_records(
        client=client,
        spreadsheet_id=sheets_config["spreadsheet_id"],
        tab_name=tab["name"],
        header_row=tab["header_row"],
        data_start_row=tab["data_start_row"],
    )
