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


def format_task_list(task_list: AgentTaskList) -> str:
    """AgentTaskListをSlackメッセージにフォーマット"""
    today_str = task_list.tasks[0].agent_name if task_list.tasks else ""
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

    # カテゴリ別にグルーピング
    current_priority_label = None
    task_num = 1

    for task in task_list.tasks:
        if task.priority_label != current_priority_label:
            current_priority_label = task.priority_label
            lines.append(f"*【{task.priority_label}】{task.category}*")

        amount_str = f"（¥{task.amount:,.0f}）" if task.amount else ""
        elapsed_str = f"（{task.days_elapsed}日経過）" if task.days_elapsed else ""

        lines.append(f"{task_num}. {task.candidate_name}{amount_str}{elapsed_str}")
        lines.append(f"   → {task.description}")
        task_num += 1

        # 同じ優先度ラベル内では改行しない。ラベルが変わったら空行
        next_idx = task_list.tasks.index(task) + 1
        if next_idx < len(task_list.tasks) and task_list.tasks[next_idx].priority_label != current_priority_label:
            lines.append("")

    lines.append("")
    lines.append("━" * 20)

    return "\n".join(lines)


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
