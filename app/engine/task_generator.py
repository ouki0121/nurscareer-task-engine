"""タスク生成ロジック ①〜⑩
仕様書: ~/.claude/projects/-Users-keitaiito/memory/nurscareer_ai_task_engine_spec.md
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional
import logging

logger = logging.getLogger(__name__)


@dataclass
class Task:
    priority: int  # 1=最優先, 10=最低
    priority_label: str  # 🔴最優先, 🟠高, 🟡中, 🔵通常, 🟢低, 📋事務
    category: str
    candidate_name: str
    description: str
    agent_name: Optional[str] = None  # MQLはNone
    deal_source: Optional[str] = None
    amount: Optional[int] = None
    days_elapsed: Optional[int] = None


@dataclass
class AgentTaskList:
    agent_name: str
    tasks: list[Task] = field(default_factory=list)
    monthly_target: int = 0
    current_revenue: int = 0
    forecasted_revenue: int = 0

    @property
    def gap(self) -> int:
        return max(0, self.monthly_target - self.forecasted_revenue)

    @property
    def achievement_rate(self) -> float:
        if self.monthly_target == 0:
            return 0.0
        return self.current_revenue / self.monthly_target


def _s(value) -> str:
    """None安全な文字列変換"""
    return str(value) if value else ""


def _parse_date_safe(value) -> Optional[date]:
    """安全に日付パース"""
    if not value:
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, datetime):
        return value.date()
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _days_since(d: Optional[date], today: date) -> Optional[int]:
    if not d:
        return None
    return (today - d).days


def _get_seasonal_context(today: date, config: dict) -> dict:
    """シーズナル変数を取得"""
    month = today.month
    seasonal = config.get("seasonal", {})
    for period_name, period in seasonal.items():
        if month in period.get("months", []):
            return {"name": period_name, **period}
    return {"name": "normal", "context": "", "emphasis": ""}


def _priority_label(priority: int) -> str:
    return {
        1: "🔴 最優先",
        2: "🟠 高",
        3: "🟠 高",
        4: "🟡 中",
        5: "🟡 中",
        6: "🟡 中",
        7: "🔵 通常",
        8: "🔵 通常",
        9: "🟢 低",
        10: "📋 事務",
    }.get(priority, "📋 事務")


class TaskEngine:
    def __init__(self, deals: list[dict], invoices: list[dict], ca_targets: dict, config: dict):
        self.deals = deals
        self.invoices = invoices
        self.ca_targets = ca_targets  # {agent_name: monthly_target_amount}
        self.config = config
        self.today = date.today()
        self.seasonal = _get_seasonal_context(self.today, config)

    def generate_all(self) -> dict[str, AgentTaskList]:
        """全CAのタスクリストを生成"""
        # CA一覧を取得（担当者が入っている取引から）
        agents = set()
        for deal in self.deals:
            agent = deal.get("agent_name") or ""
            if agent.strip():
                agents.add(agent.strip())

        result = {}
        for agent in agents:
            agent_deals = [d for d in self.deals if (d.get("agent_name") or "").strip() == agent]
            task_list = self._generate_for_agent(agent, agent_deals)
            result[agent] = task_list

        # MQL掘り起こしタスクは全員に配布（目標ギャップがある人のみ）
        mql_deals = [d for d in self.deals if d.get("status") == "MQL（展開可能×3ヶ月以降）"]
        for agent, task_list in result.items():
            if task_list.gap > 0 and mql_deals:
                mql_tasks = self._generate_mql_tasks(agent, mql_deals, task_list.gap)
                task_list.tasks.extend(mql_tasks)

        # タスクを優先度順にソート
        for task_list in result.values():
            task_list.tasks.sort(key=lambda t: (t.priority, -(t.amount or 0)))

        return result

    def _generate_for_agent(self, agent_name: str, deals: list[dict]) -> AgentTaskList:
        """1人のCAのタスクリストを生成"""
        target = self.ca_targets.get(agent_name, 0)
        current_month = self.today.strftime("%Y/%m")

        task_list = AgentTaskList(
            agent_name=agent_name,
            monthly_target=target,
        )

        for deal in deals:
            status = (deal.get("status") or "").strip()
            tasks = self._generate_tasks_for_deal(deal, status)
            task_list.tasks.extend(tasks)

        # 受注予測を計算
        task_list.forecasted_revenue = self._calculate_forecast(deals)
        task_list.current_revenue = self._calculate_current_revenue(deals)

        return task_list

    def _generate_tasks_for_deal(self, deal: dict, status: str) -> list[Task]:
        """1取引からタスクを生成"""
        tasks = []
        candidate = deal.get("candidate_name") or "不明"
        agent = deal.get("agent_name") or ""
        amount = deal.get("gross_profit") or deal.get("estimated_amount")
        if isinstance(amount, str):
            amount = None

        # ① フォーム回答へのアタック
        if status == "フォーム回答":
            form_date = _parse_date_safe(deal.get("form_response_date"))
            first_appt = _parse_date_safe(deal.get("first_appointment_date"))
            days = _days_since(form_date, self.today)

            if first_appt and first_appt <= self.today:
                # 初回アポ済み → 求人探索
                tasks.append(Task(
                    priority=1,
                    priority_label=_priority_label(1),
                    category="求人探索",
                    candidate_name=candidate,
                    description=f"初回アポ済み（{first_appt}）。求人を探索し求まるステータスへ進める",
                    agent_name=agent,
                    deal_source=deal.get("deal_source"),
                    amount=amount,
                    days_elapsed=days,
                ))
            else:
                # 未アポ → 即架電
                source = deal.get("deal_source") or ""
                is_urgent = "顕在層" in source or "リスティング" in source
                urgency_note = "競合バッティング注意。即架電" if is_urgent else "アポ獲得"
                if self.seasonal.get("name") == "high_speed":
                    urgency_note += f"（{self.seasonal['emphasis']}）"
                tasks.append(Task(
                    priority=1,
                    priority_label=_priority_label(1),
                    category="フォーム回答アタック",
                    candidate_name=candidate,
                    description=f"{urgency_note}。チャネル: {source}",
                    agent_name=agent,
                    deal_source=source,
                    amount=amount,
                    days_elapsed=days,
                ))

        # ② 内定提示 → クロージング
        elif status == "内定提示":
            tasks.append(Task(
                priority=2,
                priority_label=_priority_label(2),
                category="クロージング",
                candidate_name=candidate,
                description="内定提示済み。承諾に向けてクロージング",
                agent_name=agent,
                amount=amount,
            ))

        # ③ 面接セット済み → 面接フォロー
        elif status == "面接セット済み":
            interview_date = _parse_date_safe(deal.get("interview_date"))
            difficulty = deal.get("difficulty", "")

            if interview_date == self.today:
                desc = "本日面接。面接後即フォロー → クロージング"
            elif interview_date and interview_date < self.today:
                days = _days_since(interview_date, self.today)
                desc = f"面接から{days}日経過。結果確認 → 内定取得を急ぐ"
            elif interview_date:
                desc = f"面接予定: {interview_date}。事前準備・リマインド"
            else:
                desc = "面接日程未確定。日程確定を急ぐ"

            if difficulty and "難あり" in str(difficulty):
                desc += "【難あり】予備の事業者への面接セットも準備"

            tasks.append(Task(
                priority=3,
                priority_label=_priority_label(3),
                category="面接フォロー",
                candidate_name=candidate,
                description=desc,
                agent_name=agent,
                amount=amount,
            ))

        # ④ 求まる済み → 面接セット推進
        elif status == "求まる済み":
            match_date = _parse_date_safe(deal.get("job_match_date"))
            days = _days_since(match_date, self.today)
            difficulty = deal.get("difficulty", "")

            desc = "事業者へ面接打診 → 事まる取得 → 面接セットへ"
            if days and days > 3:
                desc = f"求まるから{days}日経過。早急に事まる取得を"
            if difficulty and "難あり" in str(difficulty):
                desc += "【難あり】予備の事業者も並行で探索"

            tasks.append(Task(
                priority=4,
                priority_label=_priority_label(4),
                category="面接セット推進",
                candidate_name=candidate,
                description=desc,
                agent_name=agent,
                amount=amount,
                days_elapsed=days,
            ))

        # ⑤ 不合格・再調整中 → リカバリー
        elif status == "不合格・再調整中":
            tasks.append(Task(
                priority=5,
                priority_label=_priority_label(5),
                category="リカバリー",
                candidate_name=candidate,
                description="再度求まるを取るための求人探索。紹介不可ならMQLへリリース",
                agent_name=agent,
                amount=amount,
            ))

        # ⑥ 早期退職（返金）→ 再展開 or リリース
        elif status == "早期退職（返金）":
            tasks.append(Task(
                priority=6,
                priority_label=_priority_label(6),
                category="早期退職対応",
                candidate_name=candidate,
                description="再転職意向を確認。意向ありなら新レコード作成→求人探索。なければMQLリリース",
                agent_name=agent,
                amount=amount,
            ))

        # ⑦ SQL → 時期到来アタック
        elif status == "SQL（展開可能×3ヶ月以内転職）":
            forecast_month = deal.get("forecast_month", "")
            start_month = deal.get("start_month", "")
            desc = f"受注予測月: {forecast_month}。アポ獲得 → 転職活動開始"
            if self.seasonal.get("name") == "nurturing":
                desc += f"（{self.seasonal['emphasis']}）"

            tasks.append(Task(
                priority=7,
                priority_label=_priority_label(7),
                category="SQL時期到来",
                candidate_name=candidate,
                description=desc,
                agent_name=agent,
                amount=amount,
            ))

        # ⑧ 請求管理シート反映済み / 入金確認済み → 入社後フォロー
        elif status in ("請求管理シート反映済み", "入金確認済み"):
            follow_up_task = self._check_follow_up_timing(deal, candidate, agent)
            if follow_up_task:
                tasks.append(follow_up_task)

        # ⑩ 内定承諾 → 請求管理シート移行
        elif status == "内定承諾":
            tasks.append(Task(
                priority=10,
                priority_label=_priority_label(10),
                category="請求管理シート移行",
                candidate_name=candidate,
                description="★請求管理シートに請求情報を記載 → ステータスを「請求管理シート反映済み」に変更",
                agent_name=agent,
                amount=amount,
            ))

        return tasks

    def _check_follow_up_timing(self, deal: dict, candidate: str, agent: str) -> Optional[Task]:
        """入社後フォローのタイミングチェック"""
        # 請求管理シートから正確な入職月を検索
        start_month_str = None
        for inv in self.invoices:
            if inv.get("candidate_name") == candidate:
                start_month_str = inv.get("start_month")
                break

        if not start_month_str:
            start_month_str = deal.get("start_month")

        if not start_month_str:
            return None

        # 入職月をパース（"2026/2" や "2026年2月" 形式）
        start_date = _parse_date_safe(start_month_str)
        if not start_date:
            # "2026/2" 形式の場合、月初日として扱う
            try:
                parts = start_month_str.replace("年", "/").replace("月", "").split("/")
                year = int(parts[0])
                month = int(parts[1])
                start_date = date(year, month, 1)
            except (ValueError, IndexError):
                return None

        follow_up_months = self.config.get("follow_up", {}).get("schedule_months_after_start", [2, 4, 6, 8, 10, 12])

        for months_after in follow_up_months:
            follow_up_date = date(
                start_date.year + (start_date.month + months_after - 1) // 12,
                (start_date.month + months_after - 1) % 12 + 1,
                1,
            )
            # フォロー日の前後7日以内ならタスク生成
            if abs((self.today - follow_up_date).days) <= 7:
                return Task(
                    priority=8,
                    priority_label=_priority_label(8),
                    category="入社後フォロー",
                    candidate_name=candidate,
                    description=f"入職{months_after}ヶ月フォロー（入職月: {start_month_str}）。職場の様子確認 → 再転職ニーズ・リファラル獲得",
                    agent_name=agent,
                )

        return None

    def _generate_mql_tasks(self, agent_name: str, mql_deals: list[dict], gap: int) -> list[Task]:
        """⑨ MQL掘り起こしタスク（目標ギャップがある場合のみ）"""
        tasks = []
        # セグメント・転職時期が近い順にソート
        scored = []
        for deal in mql_deals:
            score = 0
            segment = _s(deal.get("segment"))
            if "Aセグ" in segment:
                score += 3
            elif "Bセグ" in segment:
                score += 2
            elif "Cセグ" in segment:
                score += 1
            # 受注予測月が近いほどスコアUP
            forecast = _s(deal.get("forecast_month"))
            if forecast and str(self.today.month) in forecast:
                score += 5
            scored.append((score, deal))

        scored.sort(key=lambda x: -x[0])

        # 上位3件を推奨
        for _, deal in scored[:3]:
            candidate = _s(deal.get("candidate_name")) or "不明"
            segment = _s(deal.get("segment"))
            source = _s(deal.get("deal_source"))
            tasks.append(Task(
                priority=9,
                priority_label=_priority_label(9),
                category="掘り起こし推奨",
                candidate_name=candidate,
                description=f"MQL掘り起こし候補。{segment} / 元チャネル: {source}",
                agent_name=None,  # 共有プール
                amount=deal.get("gross_profit"),
            ))

        return tasks

    def _calculate_forecast(self, deals: list[dict]) -> int:
        """受注予測金額を計算"""
        total = 0
        win_rates = self.config.get("forecast", {}).get("win_rates", {})

        for deal in deals:
            status = _s(deal.get("status"))
            profit = deal.get("gross_profit")
            if not profit or not isinstance(profit, (int, float)):
                continue

            difficulty = _s(deal.get("difficulty"))
            is_difficult = bool(difficulty and "難あり" in difficulty)

            if status == "求まる済み":
                rate = win_rates.get("求まる済み", 0.33)
                if is_difficult:
                    rate *= 0.5  # 難あり割引（簡易版）
                total += profit * rate
            elif status == "面接セット済み":
                rate = win_rates.get("面接セット済み", 0.35)
                if is_difficult:
                    rate *= 0.5
                total += profit * rate
            elif status == "内定提示":
                rate = win_rates.get("内定提示", 0.90)
                total += profit * rate
            elif status == "内定承諾":
                total += profit

        return int(total)

    def _calculate_current_revenue(self, deals: list[dict]) -> int:
        """当月の確定受注金額（内定承諾以上）"""
        total = 0
        for deal in deals:
            status = _s(deal.get("status"))
            if status in ("内定承諾", "請求管理シート反映済み", "入金確認済み"):
                profit = deal.get("gross_profit")
                if profit and isinstance(profit, (int, float)):
                    total += profit
        return int(total)
