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


MAX_TASKS_PER_CATEGORY = 5  # カテゴリ別の表示上限


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

    # カテゴリ別にグルーピングして表示上限あり
    current_priority_label = None
    category_count = 0
    category_hidden = 0
    task_num = 1

    for i, task in enumerate(task_list.tasks):
        if task.priority_label != current_priority_label:
            # 前のカテゴリの省略表示
            if category_hidden > 0:
                lines.append(f"   _...他{category_hidden}件_")
            if current_priority_label is not None:
                lines.append("")
            current_priority_label = task.priority_label
            category_count = 0
            category_hidden = 0
            lines.append(f"*【{task.priority_label}】{task.category}*")

        category_count += 1
        if category_count <= MAX_TASKS_PER_CATEGORY:
            amount_str = f"（¥{task.amount:,.0f}）" if task.amount else ""
            elapsed_str = f"（{task.days_elapsed}日経過）" if task.days_elapsed else ""
            lines.append(f"{task_num}. {task.candidate_name}{amount_str}{elapsed_str}")
            lines.append(f"   → {task.description}")
            task_num += 1
        else:
            category_hidden += 1

    # 最後のカテゴリの省略表示
    if category_hidden > 0:
        lines.append(f"   _...他{category_hidden}件_")

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


def _generate_insights(task_list: AgentTaskList) -> list[str]:
    """タスクリストのデータから示唆を生成"""
    insights = []
    tasks = task_list.tasks

    # 1. フォーム回答の滞留チェック
    form_tasks = [t for t in tasks if t.category in ("フォーム回答アタック", "求人探索")]
    stale_forms = [t for t in form_tasks if t.days_elapsed and t.days_elapsed > 30]
    if stale_forms:
        insights.append(
            f"⚠️ フォーム回答のまま30日以上放置が *{len(stale_forms)}件*。"
            f"対応不要ならMQLに変更してヨミ表をクリーンに保ちましょう"
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

    # 4. SQL時期切れチェック
    sql_tasks = [t for t in tasks if t.category == "SQL時期到来"]
    past_sql = [t for t in sql_tasks if "1月" in t.description or "2月" in t.description or "3月" in t.description]
    if len(past_sql) > 3:
        insights.append(
            f"📋 受注予測月が過去のSQLが *{len(past_sql)}件*。"
            f"時期を更新するか、アタック済みならステータスを進めましょう"
        )

    # 5. 目標ギャップと掘り起こし
    if task_list.gap > 0:
        avg_unit_price = 900000  # 平均単価約90万
        needed_deals = task_list.gap / avg_unit_price
        insights.append(
            f"📈 目標ギャップ ¥{task_list.gap:,.0f} → "
            f"あと約{needed_deals:.1f}件の受注が必要。MQL掘り起こしで埋められる可能性あり"
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
