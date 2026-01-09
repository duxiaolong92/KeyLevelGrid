"""
Telegram 通知管理模块

负责各类交易通知的发送
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional

from key_level_grid.utils.logger import get_logger


@dataclass
class NotifyConfig:
    """通知配置"""
    signal_generated: bool = True
    order_placed: bool = True
    order_filled: bool = True
    order_cancelled: bool = True
    stop_loss_triggered: bool = True
    take_profit_triggered: bool = True
    error: bool = True
    daily_summary: bool = True
    daily_summary_time: str = "20:00"


class NotificationManager:
    """
    通知管理器
    
    统一管理各类交易通知的格式和发送
    """
    
    def __init__(self, bot, config: Optional[NotifyConfig] = None):
        """
        Args:
            bot: KeyLevelTelegramBot 实例
            config: 通知配置
        """
        self.bot = bot
        self.config = config or NotifyConfig()
        self.logger = get_logger(__name__)
        
        # 统计
        self._stats = {
            "signals": 0,
            "trades": 0,
            "stop_losses": 0,
            "take_profits": 0,
            "errors": 0,
        }
    
    async def notify_signal(self, signal: dict) -> None:
        """通知新信号"""
        if not self.config.signal_generated:
            return
        
        self._stats["signals"] += 1
        
        signal_type = signal.get("signal_type", "N/A")
        symbol = signal.get("symbol", "N/A")
        entry_price = signal.get("entry_price", 0)
        score = signal.get("score", 0)
        grade = signal.get("grade", "N/A")
        
        direction = "🟢 做多" if "long" in signal_type.lower() else "🔴 做空"
        
        text = f"""
🎯 <b>新信号生成</b>

{direction} <b>{symbol}</b>
├ 入场价: {entry_price:.4f}
├ 评分: {score}/100
└ 等级: {grade}
"""
        await self.bot.send_message(text)
    
    async def notify_order_placed(self, order: dict) -> None:
        """通知订单已提交"""
        if not self.config.order_placed:
            return
        
        symbol = order.get("symbol", "N/A")
        side = order.get("side", "N/A")
        size = order.get("size_usdt", 0)
        price = order.get("price", 0)
        
        side_emoji = "🟢" if side.lower() == "buy" else "🔴"
        
        text = f"""
📝 <b>订单已提交</b>

{side_emoji} {side.upper()} <b>{symbol}</b>
├ 价格: {price:.4f}
└ 金额: {size:.2f} USDT
"""
        await self.bot.send_message(text)
    
    async def notify_order_filled(self, order: dict) -> None:
        """通知订单已成交"""
        if not self.config.order_filled:
            return
        
        self._stats["trades"] += 1
        
        symbol = order.get("symbol", "N/A")
        side = order.get("side", "N/A")
        size = order.get("size_usdt", 0)
        fill_price = order.get("fill_price", 0)
        
        side_emoji = "🟢" if side.lower() == "buy" else "🔴"
        
        text = f"""
✅ <b>订单已成交</b>

{side_emoji} {side.upper()} <b>{symbol}</b>
├ 成交价: {fill_price:.4f}
└ 成交额: {size:.2f} USDT
"""
        await self.bot.send_message(text)
    
    async def notify_stop_loss(self, result: dict) -> None:
        """通知止损触发"""
        if not self.config.stop_loss_triggered:
            return
        
        self._stats["stop_losses"] += 1
        
        symbol = result.get("symbol", "N/A")
        direction = result.get("direction", "N/A")
        entry = result.get("entry_price", 0)
        close = result.get("close_price", 0)
        pnl = result.get("pnl_usdt", 0)
        pnl_pct = result.get("pnl_pct", 0)
        
        pnl_emoji = "📈" if pnl >= 0 else "📉"
        
        text = f"""
🛑 <b>止损触发</b>

<b>{symbol}</b> {direction.upper()}
├ 入场价: {entry:.4f}
├ 平仓价: {close:.4f}
├ 盈亏: {pnl_emoji} {pnl:.2f} USDT ({pnl_pct:.2%})
└ 原因: 触发止损
"""
        await self.bot.send_message(text)
    
    async def notify_take_profit(self, result: dict) -> None:
        """通知止盈触发"""
        if not self.config.take_profit_triggered:
            return
        
        self._stats["take_profits"] += 1
        
        symbol = result.get("symbol", "N/A")
        rr = result.get("rr_multiple", 0)
        close_pct = result.get("close_pct", 0)
        close_usdt = result.get("close_usdt", 0)
        price = result.get("price", 0)
        
        text = f"""
🎯 <b>止盈触发</b>

<b>{symbol}</b>
├ R倍数: {rr:.1f}R
├ 平仓价: {price:.4f}
├ 平仓比例: {close_pct:.0%}
└ 平仓金额: {close_usdt:.2f} USDT
"""
        await self.bot.send_message(text)
    
    async def notify_add_position(self, result: dict) -> None:
        """通知加仓"""
        trigger = result.get("trigger", "N/A")
        price = result.get("price", 0)
        add_usdt = result.get("add_usdt", 0)
        total_usdt = result.get("total_usdt", 0)
        
        text = f"""
➕ <b>加仓触发</b>

├ 触发: {trigger}
├ 价格: {price:.4f}
├ 加仓: {add_usdt:.2f} USDT
└ 总仓位: {total_usdt:.2f} USDT
"""
        await self.bot.send_message(text)
    
    async def notify_error(self, error: str, context: str = "") -> None:
        """通知错误"""
        if not self.config.error:
            return
        
        self._stats["errors"] += 1
        
        text = f"""
❌ <b>错误</b>

{f'上下文: {context}' if context else ''}
错误: {error}
"""
        await self.bot.send_message(text)
    
    async def send_daily_summary(self, stats: dict) -> None:
        """发送每日统计"""
        if not self.config.daily_summary:
            return
        
        today = datetime.now().strftime("%Y-%m-%d")
        
        total_pnl = stats.get("total_pnl", 0)
        trades = stats.get("trades", 0)
        win_rate = stats.get("win_rate", 0)
        
        pnl_emoji = "📈" if total_pnl >= 0 else "📉"
        
        text = f"""
📊 <b>每日统计 - {today}</b>

├ 总盈亏: {pnl_emoji} {total_pnl:.2f} USDT
├ 交易次数: {trades}
├ 胜率: {win_rate:.1%}
├ 信号数: {self._stats['signals']}
├ 止损次数: {self._stats['stop_losses']}
└ 止盈次数: {self._stats['take_profits']}
"""
        await self.bot.send_message(text)
        
        # 重置统计
        self._stats = {
            "signals": 0,
            "trades": 0,
            "stop_losses": 0,
            "take_profits": 0,
            "errors": 0,
        }
    
    def get_stats(self) -> dict:
        """获取通知统计"""
        return self._stats.copy()

