"""Slack にCA別タスクリストを投稿するモジュール"""

import os
import logging
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from ..engine.task_generator import AgentTaskList, Task

logger = logging.getLogger(__name__)


def get_slack_client() -> WebClient:
    token = os.environ.get("SLACK_BOT_TOKEN")
    if not token:
        raise ValueError("SLACK_BOT_TOKEN environment variable is required")
    return WebClient(token=token)


# フォーム回答（放置案件）のみ省略。他は全件表示
FORM_RESPONSE_MAX = 5

# 全カテゴリ定義（タスクエンジンの①〜⑩に対応）
ALL_CATEGORIES = [
    {"key": "フォーム回答アタック", "label": "① フォーム回答アタック", "includes": ["フォーム回答アタック", "求人探索"]},
    {"key": "クロージング", "label": "② クロージング（内定提示）", "includes": ["クロージング"]},
    {"key": "面接フォロー", "label": "③ 面接フォロー（面接セット済み）", "includes": ["面接フォロー"]},
    {"key": "面接セット推進", "label": "④ 面接セット推進（求まる済み）", "includes": ["面接セット推進"]},
    {"key": "リカバリー", "label": "⑤ リカバリー（不合格・再調整中）", "includes": ["リカバリー"]},
    {"key": "早期退職対応", "label": "⑥ 早期退職対応", "includes": ["早期退職対応"]},
    {"key": "SQL時期到来", "label": "⑦ SQL 時期到来アタック", "includes": ["SQL時期到来"]},
    {"key": "入社後フォロー", "label": "⑧ 入社後フォロー", "includes": ["入社後フォロー"]},
    {"key": "掘り起こし", "label": "⑨ MQL 掘り起こし", "includes": ["掘り起こし推奨"]},
    {"key": "請求管理シート移行", "label": "⑩ 請求管理シート移行", "includes": ["請求管理シート移行"]},
]


def format_task_list(task_list: AgentTaskList) -> str:
    """AgentTaskListをSlackメッセージにフォーマット"""
    from datetime import date
    today = date.today()
    weekday = ["月", "火", "水", "木", "金", "土", "日"][today.weekday()]

    lines = []
    lines.append(f"🔔 *{task_list.agent_name}さんの今日のタスク*（{today.month}/{today.day} {weekday}）")
    lines.append("━" * 20)

    # 進捗サマリー
    achievement = f"{task_list.achievement_rate:.0%}" if task_list.monthly_target > 0 else "-"
    lines.append(
        f"📊 今月の進捗: ¥{task_list.current_revenue:,.0f} / "
        f"目標¥{task_list.monthly_target:,.0f}（{achievement}）"
    )
    lines.append(
        f"   受注予測: ¥{task_list.forecasted_revenue:,.0f}"
        f"（ギャップ: ¥{task_list.gap:,.0f}）"
    )
    lines.append("")

    if not task_list.tasks:
        lines.append("✅ タスクなし。素晴らしい！")
        return "\n".join(lines)

    # カテゴリ別に分類
    tasks_by_category = {}
    for task in task_list.tasks:
        tasks_by_category.setdefault(task.category, []).append(task)

    task_num = 1

    for cat_def in ALL_CATEGORIES:
        cat_tasks = []
        for include_key in cat_def["includes"]:
            cat_tasks.extend(tasks_by_category.get(include_key, []))

        if cat_def["key"] == "フォーム回答アタック":
            # フォーム回答のみ省略あり（放置案件が大量のため）
            _render_form_response_section(lines, cat_tasks, cat_def["label"], task_num)
            task_num += min(len(cat_tasks), FORM_RESPONSE_MAX)
        elif cat_def["key"] == "掘り起こし":
            # MQL掘り起こしはバイネーム指示しない。示唆セクションで言及
            pass  # 示唆で扱う
        elif cat_tasks:
            # 他カテゴリは全件表示
            lines.append(f"*【{cat_tasks[0].priority_label}】{cat_def['label']}*")
            for task in cat_tasks:
                amount_str = f"（¥{task.amount:,.0f}）" if task.amount else ""
                elapsed_str = f"（{task.days_elapsed}日経過）" if task.days_elapsed else ""
                lines.append(f"{task_num}. {task.candidate_name}{amount_str}{elapsed_str}")
                lines.append(f"   → {task.description}")
                task_num += 1
            lines.append("")
        else:
            # 該当なしを明記
            lines.append(f"*{cat_def['label']}*")
            lines.append("   ✅ 該当なし")
            lines.append("")

    # 示唆・アドバイスセクション
    insights = _generate_insights(task_list)
    if insights:
        lines.append("━" * 20)
        lines.append("💡 *データから見える示唆*")
        for insight in insights:
            lines.append(f"• {insight}")
        lines.append("")

    lines.append("━" * 20)

    return "\n".join(lines)


def _render_form_response_section(lines: list, tasks: list, label: str, start_num: int):
    """フォーム回答セクション。直近5件+省略"""
    if not tasks:
        lines.append(f"*{label}*")
        lines.append("   ✅ 該当なし")
        lines.append("")
        return

    # 新しい順（days_elapsed昇順）でソート
    sorted_tasks = sorted(tasks, key=lambda t: t.days_elapsed or 9999)

    lines.append(f"*【{sorted_tasks[0].priority_label}】{label}*")
    shown = 0
    for task in sorted_tasks[:FORM_RESPONSE_MAX]:
        amount_str = f"（¥{task.amount:,.0f}）" if task.amount else ""
        elapsed_str = f"（{task.days_elapsed}日経過）" if task.days_elapsed else ""
        lines.append(f"{start_num + shown}. {task.candidate_name}{amount_str}{elapsed_str}")
        lines.append(f"   → {task.description}")
        shown += 1

    if len(sorted_tasks) > FORM_RESPONSE_MAX:
        lines.append(f"   _...他{len(sorted_tasks) - FORM_RESPONSE_MAX}件（示唆を参照）_")
    lines.append("")


def _generate_insights(task_list: AgentTaskList) -> list[str]:
    """タスクリストのデータから示唆を生成"""
    insights = []
    tasks = task_list.tasks

    # 1. フォーム回答の件数異常チェック（10件超で警告）
    form_tasks = [t for t in tasks if t.category in ("フォーム回答アタック", "求人探索")]
    if len(form_tasks) > 10:
        stale_count = sum(1 for t in form_tasks if t.days_elapsed and t.days_elapsed > 30)
        insights.append(
            f"🚨 フォーム回答ステータスが *{len(form_tasks)}件*（正常は10件以下）。アタックが疎かになっている可能性があります。"
            f"\n  → 転職時期が今でない人は *MQL* に変更"
            f"\n  → 自分の取引として確保したい人は受注月を明記して *SQL* に変更"
            f"\n  →（30日以上放置: {stale_count}件）"
        )
    elif form_tasks:
        stale_forms = [t for t in form_tasks if t.days_elapsed and t.days_elapsed > 30]
        if stale_forms:
            insights.append(
                f"⚠️ フォーム回答のまま30日以上放置が *{len(stale_forms)}件*。"
                f"MQLかSQLへのステータス変更を検討してください"
            )

    # 2. 求まる済みの滞留チェック
    match_tasks = [t for t in tasks if t.category == "面接セット推進"]
    stale_matches = [t for t in match_tasks if t.days_elapsed and t.days_elapsed > 7]
    if stale_matches:
        insights.append(
            f"⏰ 求まるから7日以上経過が *{len(stale_matches)}件*。"
            f"事まる取得を急がないと求職者の温度が下がります"
        )

    # 3. 面接当日フォロー
    interview_today = [t for t in tasks if t.category == "面接フォロー" and "本日面接" in t.description]
    if interview_today:
        insights.append(
            f"🎯 本日面接が *{len(interview_today)}件*。"
            f"面接後30分以内のフォロー電話が内定承諾率を大きく左右します"
        )

    # 4. SQL件数異常チェック（20件超で警告）+ 時期切れ
    sql_tasks = [t for t in tasks if t.category == "SQL時期到来"]
    if len(sql_tasks) > 20:
        insights.append(
            f"🚨 SQLが *{len(sql_tasks)}件*（正常は20件以下）。受注月が不確かなまま放置されている可能性があります。"
            f"\n  → 一度コンタクトを取り、アタックする月を明確にしてG列（受注月）を更新してください"
        )
    else:
        past_sql = [t for t in sql_tasks if "1月" in t.description or "2月" in t.description or "3月" in t.description]
        if len(past_sql) > 3:
            insights.append(
                f"📋 受注予測月が過去のSQLが *{len(past_sql)}件*。"
                f"時期を更新するか、アタック済みならステータスを進めましょう"
            )

    # 5. 目標ギャップと掘り起こし（バイネーム指示ではなく件数の示唆）
    if task_list.gap > 0:
        avg_unit_price = 900000  # 平均単価約90万
        needed_deals = task_list.gap / avg_unit_price
        insights.append(
            f"📈 目標ギャップ ¥{task_list.gap:,.0f} → "
            f"あと約 *{needed_deals:.1f}件* の受注が必要。MQLプールから掘り起こしでカバーできる可能性あり"
        )

    # 6. クロージング案件の金額合計
    closing_tasks = [t for t in tasks if t.category == "クロージング"]
    closing_amount = sum(t.amount or 0 for t in closing_tasks)
    if closing_amount > 0:
        insights.append(
            f"🔥 内定提示済み *{len(closing_tasks)}件*（合計¥{closing_amount:,.0f}）。"
            f"本日中にクロージングできれば目標達成に大きく前進"
        )

    # 7. 早期退職案件
    early_quit = [t for t in tasks if t.category == "早期退職対応"]
    if early_quit:
        insights.append(
            f"🔄 早期退職案件 *{len(early_quit)}件*。"
            f"再転職意向があれば新レコード作成→即求人探索。ヘイトが溜まっていればMQLリリースの判断を"
        )

    return insights


def find_user_by_name(client: WebClient, agent_name: str) -> str | None:
    """CA名からSlackユーザーIDを検索"""
    try:
        result = client.users_list()
        for user in result["members"]:
            if user.get("deleted"):
                continue
            display_name = user.get("profile", {}).get("display_name", "")
            real_name = user.get("profile", {}).get("real_name", "")
            # display_name に「田中｜キャリアエース」のような形式を想定
            if agent_name in display_name or agent_name in real_name:
                return user["id"]
    except SlackApiError as e:
        logger.error(f"Failed to list users: {e}")
    return None


def post_tasks_to_slack(
    client: WebClient,
    task_list: AgentTaskList,
    channel: str | None = None,
) -> bool:
    """CA別にSlack DMまたは指定チャネルに投稿"""
    message = format_task_list(task_list)

    try:
        if channel:
            # 指定チャネルに投稿
            client.chat_postMessage(channel=channel, text=message, mrkdwn=True)
        else:
            # DM投稿
            user_id = find_user_by_name(client, task_list.agent_name)
            if not user_id:
                logger.warning(f"Slack user not found for {task_list.agent_name}")
                return False
            # DMチャネルを開く
            dm = client.conversations_open(users=[user_id])
            dm_channel = dm["channel"]["id"]
            client.chat_postMessage(channel=dm_channel, text=message, mrkdwn=True)

        logger.info(f"Posted tasks for {task_list.agent_name}")
        return True
    except SlackApiError as e:
        logger.error(f"Failed to post for {task_list.agent_name}: {e}")
        return False
