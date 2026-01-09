"""
通用交易所执行器基类

在 `ExecutorBase` 的抽象接口之上，补充了：
- 纸交易资产池与余额更新
- 订单统计与 fill rate
- 安全策略（每日限额 / 紧急止损）
- 日切重置逻辑

不同交易所只需继承本类，实现具体下单/查询逻辑即可。
"""
from __future__ import annotations

from abc import ABC
from datetime import date
from typing import Dict, Optional, Tuple

from key_level_grid.utils.config import SafetyConfig
from key_level_grid.executor.base import ExecutorBase, Order
from key_level_grid.utils.logger import get_logger


class ExchangeExecutor(ExecutorBase, ABC):
    """统一封装各交易所执行器的通用能力。"""

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        paper_trading: bool = True,
        safety_config: Optional[SafetyConfig] = None,
        max_retries: int = 3,
        retry_delay_ms: int = 100,
        ioc_timeout_sec: float = 2.0,
        default_paper_balances: Optional[Dict[str, float]] = None,
        logger_name: Optional[str] = None,
    ) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.paper_trading = paper_trading
        self.safety = safety_config or SafetyConfig()
        self.max_retries = max_retries
        self.retry_delay_ms = retry_delay_ms
        self.ioc_timeout_sec = ioc_timeout_sec

        self.logger = get_logger(logger_name or self.__class__.__name__)

        # 纸交易模拟资产
        self._paper_balances: Dict[str, float] = default_paper_balances or {"USDT": 10000.0}

        # 统计信息
        self._stats = {
            "orders_submitted": 0,
            "orders_filled": 0,
            "orders_cancelled": 0,
            "orders_rejected": 0,
            "orders_failed": 0,
            "retries": 0,
        }

        # 每日安全统计
        self.daily_trades = 0
        self.daily_pnl = 0.0
        self._last_reset_date: Optional[date] = None

        if paper_trading:
            self.logger.info("📄 执行器启动：纸交易模式")
        else:
            self.logger.info("🔴 执行器启动：真实交易模式")

    # ------------------------------------------------------------------
    # 安全 & 统计
    # ------------------------------------------------------------------
    async def _pre_trade_safety_check(self, order: Order) -> Tuple[bool, str]:
        """交易前安全检查（真实交易使用）。"""
        self._reset_daily_stats_if_needed()

        if self.daily_trades >= self.safety.max_daily_trades:
            reason = f"每日交易次数上限 {self.daily_trades}/{self.safety.max_daily_trades}"
            self.logger.warning(reason)
            return False, reason

        order_value = order.quantity * (order.price or 0)
        if order_value > self.safety.max_position_value:
            reason = (
                f"订单金额超限 ${order_value:.2f} > ${self.safety.max_position_value:.2f}"
            )
            self.logger.warning(reason)
            return False, reason

        if self.daily_pnl < -abs(self.safety.emergency_stop_loss):
            reason = f"触发紧急止损 (PnL={self.daily_pnl:.2f})"
            self.logger.error(reason)
            return False, reason

        return True, ""

    def _reset_daily_stats_if_needed(self) -> None:
        """日期切换时重置统计。"""
        today = date.today()
        if self._last_reset_date == today:
            return

        if self._last_reset_date is not None:
            self.logger.info(
                "📊 每日统计重置",
                extra={"trades": self.daily_trades, "pnl": self.daily_pnl},
            )

        self.daily_trades = 0
        self.daily_pnl = 0.0
        self._last_reset_date = today

    # ------------------------------------------------------------------
    # 纸交易辅助
    # ------------------------------------------------------------------
    def _update_paper_balance(self, order: Order) -> None:
        """根据成交更新纸交易余额。"""
        if order.side.value == "buy":
            cost = order.filled_quantity * order.avg_fill_price + order.fees
            self._paper_balances["USDT"] = self._paper_balances.get("USDT", 0) - cost
        else:
            proceeds = order.filled_quantity * order.avg_fill_price - order.fees
            self._paper_balances["USDT"] = self._paper_balances.get("USDT", 0) + proceeds

    def get_stats(self) -> Dict:
        """返回执行器统计信息。"""
        return {
            **self._stats,
            "fill_rate": (
                self._stats["orders_filled"] / self._stats["orders_submitted"]
                if self._stats["orders_submitted"] > 0
                else 0.0
            ),
            "paper_balances": self._paper_balances.copy() if self.paper_trading else {},
        }


