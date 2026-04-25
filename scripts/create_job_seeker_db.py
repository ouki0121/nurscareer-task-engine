"""セールスヨミ表から求職者DBを生成するスクリプト

ロジック:
1. ヨミ表の全レコードを読み取り
2. 求職者名(F列) + LINE UserID(C列) で重複判定
3. 登録日(B列)が最も古いレコードを求職者情報として採用
4. I列の流入チャネルを初回流入チャネルとして固定
5. 求職者DBタブに書き出し
"""

import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime
import os
import sys


SPREADSHEET_ID = "1A2M9_FhcCzLQsxJ7H3OwOrfEgWjDpezzBu6SBboJFck"
CREDENTIALS_PATH = os.path.expanduser("~/.claude/secrets/bigquery-mcp.json")
YOMI_TAB = "セールスヨミ表"
OUTPUT_TAB = "求職者DB"
HEADER_ROW = 50  # ヨミ表のヘッダー行
DATA_START_ROW = 51


def get_client():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    credentials = Credentials.from_service_account_file(CREDENTIALS_PATH, scopes=scopes)
    return gspread.authorize(credentials)


def parse_date(value: str) -> datetime | None:
    if not value or not value.strip():
        return None
    value = value.strip()
    for fmt in ["%Y/%m/%d", "%Y-%m-%d", "%Y年%m月%d日"]:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def main():
    print("Connecting to Google Sheets...")
    client = get_client()
    spreadsheet = client.open_by_key(SPREADSHEET_ID)

    # ヨミ表を読み取り
    print(f"Reading {YOMI_TAB}...")
    yomi = spreadsheet.worksheet(YOMI_TAB)
    all_values = yomi.get_all_values()
    print(f"Total rows: {len(all_values)}")

    # ヘッダー取得
    headers = all_values[HEADER_ROW - 1]  # 0-indexed
    print(f"Headers: {headers[:12]}")

    # カラムインデックスを取得（B列=index 1, C列=index 2, etc.）
    # ヨミ表はA列から始まるが、A列は空
    col_map = {}
    for i, h in enumerate(headers):
        h = h.strip()
        if h:
            col_map[h] = i

    print(f"Column mapping: {list(col_map.keys())[:15]}")

    # 必要なカラムのインデックス
    idx_registration_date = col_map.get("登録日", 1)
    idx_line_id = col_map.get("LINE ID", 2)
    idx_friend_month = col_map.get("友達登録月", 3)
    idx_agent = col_map.get("担当者", 4)
    idx_name = col_map.get("求職者名", 5)
    idx_channel = col_map.get("新規流入チャネル", 8)
    idx_segment = col_map.get("セグ", 11)

    # データ行を処理
    records = []
    for row_idx in range(DATA_START_ROW - 1, len(all_values)):
        row = all_values[row_idx]
        name = row[idx_name].strip() if idx_name < len(row) else ""
        if not name:
            continue

        line_id = row[idx_line_id].strip() if idx_line_id < len(row) else ""
        reg_date_str = row[idx_registration_date].strip() if idx_registration_date < len(row) else ""
        friend_month = row[idx_friend_month].strip() if idx_friend_month < len(row) else ""
        channel = row[idx_channel].strip() if idx_channel < len(row) else ""
        segment = row[idx_segment].strip() if idx_segment < len(row) else ""

        reg_date = parse_date(reg_date_str)

        records.append({
            "name": name,
            "line_id": line_id,
            "registration_date": reg_date,
            "registration_date_str": reg_date_str,
            "friend_month": friend_month,
            "channel": channel,
            "segment": segment,
        })

    print(f"Total deal records: {len(records)}")

    # 重複排除: 求職者名 + LINE UserID でグルーピング
    # LINE IDが「不明」や空の場合は名前のみでグルーピング
    seekers = {}

    for r in records:
        # キー生成: LINE IDが有効ならLINE ID、なければ名前
        line_id = r["line_id"]
        name = r["name"]

        if line_id and line_id != "不明" and line_id != "不明（重複？）" and not line_id.startswith("不明"):
            key = f"LINE:{line_id}"
        else:
            key = f"NAME:{name}"

        if key not in seekers:
            seekers[key] = r
        else:
            # 登録日が古い方を採用
            existing = seekers[key]
            if r["registration_date"] and existing["registration_date"]:
                if r["registration_date"] < existing["registration_date"]:
                    # 古い方を採用するが、名前は残す（空の場合があるので）
                    if not r["name"] and existing["name"]:
                        r["name"] = existing["name"]
                    seekers[key] = r
            elif r["registration_date"] and not existing["registration_date"]:
                seekers[key] = r

    print(f"Unique job seekers: {len(seekers)}")

    # 求職者DBのデータを構築
    output_header = [
        "求職者ID",
        "LINE UserID",
        "求職者名",
        "初回登録日",
        "友達登録月",
        "初回流入チャネル",
        "セグメント",
        "取引数",
    ]

    output_rows = [output_header]
    seeker_list = sorted(seekers.values(), key=lambda x: x["registration_date"] or datetime.max)

    for i, s in enumerate(seeker_list, start=1):
        # 同じキーの取引数をカウント
        line_id = s["line_id"]
        name = s["name"]
        if line_id and line_id != "不明" and not line_id.startswith("不明"):
            deal_count = sum(
                1 for r in records
                if r["line_id"] == line_id
            )
        else:
            deal_count = sum(
                1 for r in records
                if r["name"] == name
            )

        output_rows.append([
            f"JS-{i:04d}",
            s["line_id"] if s["line_id"] and s["line_id"] != "不明" else "",
            s["name"],
            s["registration_date_str"],
            s["friend_month"],
            s["channel"],
            s["segment"],
            str(deal_count),
        ])

    print(f"Output rows (including header): {len(output_rows)}")

    # 求職者DBタブに書き出し
    print(f"Writing to {OUTPUT_TAB}...")
    try:
        output_ws = spreadsheet.worksheet(OUTPUT_TAB)
    except gspread.exceptions.WorksheetNotFound:
        output_ws = spreadsheet.add_worksheet(title=OUTPUT_TAB, rows=len(output_rows) + 10, cols=10)

    # クリアしてから書き込み
    output_ws.clear()
    output_ws.update(range_name="A1", values=output_rows)

    print(f"Done! {len(output_rows) - 1} job seekers written to {OUTPUT_TAB}")

    # サマリー
    line_id_count = sum(1 for r in output_rows[1:] if r[1])  # LINE IDがある人
    multi_deal = sum(1 for r in output_rows[1:] if int(r[7]) > 1)  # 複数取引がある人
    print(f"\nSummary:")
    print(f"  Total seekers: {len(output_rows) - 1}")
    print(f"  With LINE ID: {line_id_count}")
    print(f"  Without LINE ID: {len(output_rows) - 1 - line_id_count}")
    print(f"  Multiple deals: {multi_deal}")


if __name__ == "__main__":
    main()
