"""
Telegram 通知管理模块

负责各类交易通知的发送，所有金额使用 USDT 计价
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Any

from key_level_grid.utils.logger import get_logger


@dataclass
class NotifyConfig:
    """通知配置"""
    # 必要通知（建议开启）
    startup: bool = True              # 启动通知
    shutdown: bool = True             # 停止通知
    error: bool = True                # 错误通知
    order_filled: bool = True         # 成交通知
    
    # 可选通知
    order_placed: bool = False        # 挂单通知（可能较频繁）
    order_cancelled: bool = False     # 取消通知
    grid_rebuild: bool = True         # 网格重建
    orders_summary: bool = True       # 挂单汇总（启动时发送）
    
    # 风险通知
    risk_warning: bool = True         # 风险预警
    near_stop_loss_pct: float = 0.02  # 距止损预警阈值 2%
    
    # 汇总通知
    daily_summary: bool = True        # 每日汇总
    daily_summary_time: str = "20:00"
    
    # 心跳（可选）
    heartbeat: bool = False
    heartbeat_interval_hours: int = 4
    
    # 防刷屏
    min_notify_interval_sec: int = 5  # 同类通知最小间隔


class NotificationManager:
    """
    通知管理器
    
    统一管理各类交易通知的格式和发送
    所有金额使用 USDT 计价
    
    支持两种模式:
    1. 传入 bot 实例 (需要 bot.start() 后才能发送)
    2. 传入 bot_token 和 chat_id (直接通过 HTTP API 发送，推荐)
    """
    
    def __init__(
        self, 
        bot=None, 
        config: Optional[NotifyConfig] = None,
        bot_token: str = "",
        chat_id: str = "",
    ):
        """
        Args:
            bot: KeyLevelTelegramBot 实例 (可选)
            config: 通知配置
            bot_token: Telegram Bot Token (直接发送模式)
            chat_id: Telegram Chat ID (直接发送模式)
        """
        self.bot = bot
        self.config = config or NotifyConfig()
        self.logger = get_logger(__name__)
        
        # 直接发送模式的配置
        self._bot_token = bot_token or (bot.config.bot_token if bot and hasattr(bot, 'config') else "")
        self._chat_id = chat_id or (bot.config.chat_id if bot and hasattr(bot, 'config') else "")
        
        # 统计
        self._stats = {
            "buy_count": 0,
            "buy_amount": 0.0,
            "sell_count": 0,
            "sell_amount": 0.0,
            "realized_pnl": 0.0,
            "errors": 0,
            "grid_rebuilds": 0,
        }
        
        # 上次通知时间（防刷屏）
        self._last_notify_time: Dict[str, float] = {}
        
        # 风险预警状态
        self._risk_warning_sent = False
    
    async def _send_message(self, text: str) -> bool:
        """
        发送消息 (优先使用 HTTP API 直接发送)
        
        Returns:
            bool: 是否发送成功
        """
        # 优先使用直接 HTTP API 发送
        if self._bot_token and self._chat_id:
            try:
                import aiohttp
                url = f"https://api.telegram.org/bot{self._bot_token}/sendMessage"
                payload = {
                    "chat_id": self._chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                }
                async with aiohttp.ClientSession() as session:
                    async with session.post(url, json=payload, timeout=10) as resp:
                        result = await resp.json()
                        if result.get("ok"):
                            return True
                        else:
                            self.logger.error(f"Telegram API 错误: {result}")
                            return False
            except Exception as e:
                self.logger.error(f"发送 Telegram 消息失败: {e}")
                return False
        
        # 回退到 Bot 实例发送
        if self.bot and hasattr(self.bot, 'app') and self.bot.app:
            try:
                await self._send_message(text)
                return True
            except Exception as e:
                self.logger.error(f"通过 Bot 发送消息失败: {e}")
                return False
        
        self.logger.warning("无法发送 Telegram 消息: 未配置 token/chat_id 且 Bot 未启动")
        return False
    
    def _can_notify(self, notify_type: str) -> bool:
        """检查是否可以发送通知（防刷屏）"""
        import time
        now = time.time()
        last_time = self._last_notify_time.get(notify_type, 0)
        if now - last_time < self.config.min_notify_interval_sec:
            return False
        self._last_notify_time[notify_type] = now
        return True
    
    async def notify_startup(
        self,
        symbol: str,
        exchange: str,
        current_price: float,
        account: Dict[str, Any],
        position: Dict[str, Any],
        pending_orders: List[Dict[str, Any]],
        grid_config: Dict[str, Any],
        resistance_levels: List[Dict[str, Any]] = None,
        support_levels: List[Dict[str, Any]] = None,
    ) -> None:
        """
        策略启动通知
        
        Args:
            symbol: 交易对
            exchange: 交易所
            current_price: 当前价格
            account: 账户信息 {total_balance, available, frozen}
            position: 持仓信息 {value, avg_price, unrealized_pnl, pnl_pct}
            pending_orders: 挂单列表 [{side, price, amount}, ...]
            grid_config: 网格配置 {max_position, leverage, num_grids}
            resistance_levels: 阻力位列表 [{price, strength, source}, ...]
            support_levels: 支撑位列表 [{price, strength, source}, ...]
        """
        if not self.config.startup:
            return
        
        resistance_levels = resistance_levels or []
        support_levels = support_levels or []
        
        # 账户信息
        total_balance = account.get("total_balance", 0)
        available = account.get("available", 0)
        
        # 网格配置
        max_position = grid_config.get("max_position", 0)
        leverage = grid_config.get("leverage", 0)
        num_grids = grid_config.get("num_grids", 0)
        
        text = f"""
🚀 <b>策略已启动</b>

📊 <b>{symbol}</b> | {exchange.upper()}
├ 当前价格: ${current_price:,.2f}
├ 账户余额: {total_balance:,.2f} USDT
├ 可用余额: {available:,.2f} USDT
└ 杠杆: {leverage}x
"""
        
        # 关键价位 - 阻力位（按价格降序）
        if resistance_levels:
            resistance_sorted = sorted(resistance_levels, key=lambda x: -x.get("price", 0))
            text += f"\n🔴 <b>阻力位</b> ({len(resistance_sorted)}个)\n"
            for i, r in enumerate(resistance_sorted, 1):
                r_price = r.get("price", 0)
                strength = r.get("strength", 0)
                pct = ((r_price - current_price) / current_price * 100) if current_price > 0 else 0
                text += f"├ R{i}: ${r_price:,.2f} (+{pct:.1f}%) 强度:{strength:.0f}\n"
        
        # 关键价位 - 支撑位（按价格降序）
        if support_levels:
            support_sorted = sorted(support_levels, key=lambda x: -x.get("price", 0))
            text += f"\n🟢 <b>支撑位</b> ({len(support_sorted)}个)\n"
            for i, s in enumerate(support_sorted, 1):
                s_price = s.get("price", 0)
                strength = s.get("strength", 0)
                pct = ((current_price - s_price) / current_price * 100) if current_price > 0 else 0
                text += f"├ S{i}: ${s_price:,.2f} (-{pct:.1f}%) 强度:{strength:.0f}\n"
        
        # 挂单信息
        buy_orders = [o for o in pending_orders if o.get("side") == "buy"]
        sell_orders = [o for o in pending_orders if o.get("side") == "sell"]
        
        if buy_orders:
            total_buy = sum(o.get("amount", 0) for o in buy_orders)
            text += f"\n📋 <b>买单挂单</b> ({len(buy_orders)}个, 共 {total_buy:,.0f} USDT)\n"
            # 按价格降序排列
            buy_orders_sorted = sorted(buy_orders, key=lambda x: -x.get("price", 0))
            for i, order in enumerate(buy_orders_sorted, 1):
                price = order.get("price", 0)
                amount = order.get("amount", 0)
                pct = ((price - current_price) / current_price * 100) if current_price > 0 else 0
                text += f"├ #{i}: ${price:,.2f} ({pct:+.1f}%) | {amount:,.0f} USDT\n"
        
        if sell_orders:
            total_sell = sum(o.get("amount", 0) for o in sell_orders)
            text += f"\n📋 <b>卖单挂单</b> ({len(sell_orders)}个, 共 {total_sell:,.0f} USDT)\n"
            sell_orders_sorted = sorted(sell_orders, key=lambda x: x.get("price", 0))
            for i, order in enumerate(sell_orders_sorted, 1):
                price = order.get("price", 0)
                amount = order.get("amount", 0)
                pct = ((price - current_price) / current_price * 100) if current_price > 0 else 0
                text += f"├ #{i}: ${price:,.2f} ({pct:+.1f}%) | {amount:,.0f} USDT\n"
        
        # 持仓信息
        pos_value = position.get("value", 0)
        if pos_value > 0:
            avg_price = position.get("avg_price", 0)
            unrealized_pnl = position.get("unrealized_pnl", 0)
            pnl_pct = position.get("pnl_pct", 0)
            
            pnl_emoji = "📈" if unrealized_pnl >= 0 else "📉"
            pnl_sign = "+" if unrealized_pnl >= 0 else ""
            
            text += f"""
💼 <b>当前持仓</b>
├ 持仓价值: {pos_value:,.2f} USDT
├ 均价: ${avg_price:,.2f}
└ 盈亏: {pnl_emoji} {pnl_sign}{unrealized_pnl:,.2f} USDT ({pnl_sign}{pnl_pct:.2%})
"""
        else:
            text += "\n💼 当前无持仓\n"
        
        text += f"\n⚙️ 网格配置: {num_grids}档 | 最大仓位 {max_position:,.0f} USDT"
        
        await self._send_message(text.strip())
    
    async def notify_shutdown(
        self,
        reason: str = "手动停止",
        position: Optional[Dict[str, Any]] = None,
        total_pnl: float = 0,
    ) -> None:
        """策略停止通知"""
        if not self.config.shutdown:
            return
        
        text = f"""
🛑 <b>策略已停止</b>

├ 原因: {reason}
├ 停止时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
        
        if position and position.get("value", 0) > 0:
            text += f"""└ 剩余持仓: {position.get('value', 0):,.2f} USDT

⚠️ 注意: 仍有持仓，请手动处理
"""
        else:
            text += "└ 持仓已清空\n"
        
        if total_pnl != 0:
            pnl_emoji = "📈" if total_pnl >= 0 else "📉"
            pnl_sign = "+" if total_pnl >= 0 else ""
            text += f"\n💰 本次运行盈亏: {pnl_emoji} {pnl_sign}{total_pnl:,.2f} USDT"
        
        await self._send_message(text.strip())
    
    async def notify_order_filled(
        self,
        side: str,
        symbol: str,
        fill_price: float,
        fill_amount: float,
        grid_index: int = 0,
        total_grids: int = 0,
        position_after: Optional[Dict[str, Any]] = None,
        realized_pnl: float = 0,
    ) -> None:
        """
        订单成交通知
        
        Args:
            side: buy/sell
            symbol: 交易对
            fill_price: 成交价格
            fill_amount: 成交金额 (USDT)
            grid_index: 成交档位
            total_grids: 总档位数
            position_after: 成交后持仓 {value, avg_price, unrealized_pnl, pnl_pct}
            realized_pnl: 实现盈亏 (仅卖出时)
        """
        if not self.config.order_filled:
            return
        
        if not self._can_notify("order_filled"):
            return
        
        # 更新统计
        if side.lower() == "buy":
            self._stats["buy_count"] += 1
            self._stats["buy_amount"] += fill_amount
            side_emoji = "🟢"
            side_text = "买入"
        else:
            self._stats["sell_count"] += 1
            self._stats["sell_amount"] += fill_amount
            self._stats["realized_pnl"] += realized_pnl
            side_emoji = "🔴"
            side_text = "卖出"
        
        # 档位信息
        grid_info = f"#{grid_index} / {total_grids}" if grid_index > 0 else ""
        
        if side.lower() == "sell" and realized_pnl != 0:
            # 止盈成交
            pnl_emoji = "📈" if realized_pnl >= 0 else "📉"
            pnl_sign = "+" if realized_pnl >= 0 else ""
            pnl_pct = realized_pnl / fill_amount if fill_amount > 0 else 0
            
            text = f"""
🎯 <b>止盈成交</b>

{side_emoji} {side_text} <b>{symbol}</b>
├ 成交价: ${fill_price:,.2f}
├ 成交额: {fill_amount:,.2f} USDT
├ 实现盈亏: {pnl_emoji} {pnl_sign}{realized_pnl:,.2f} USDT ({pnl_sign}{pnl_pct:.2%})
└ 档位: {grid_info}
"""
        else:
            # 普通成交
            text = f"""
✅ <b>订单成交</b>

{side_emoji} {side_text} <b>{symbol}</b>
├ 成交价: ${fill_price:,.2f}
├ 成交额: {fill_amount:,.2f} USDT
└ 档位: {grid_info}
"""
        
        # 持仓更新
        if position_after and position_after.get("value", 0) > 0:
            pos_value = position_after.get("value", 0)
            avg_price = position_after.get("avg_price", 0)
            unrealized_pnl = position_after.get("unrealized_pnl", 0)
            pnl_pct = position_after.get("pnl_pct", 0)
            
            pnl_emoji = "📈" if unrealized_pnl >= 0 else "📉"
            pnl_sign = "+" if unrealized_pnl >= 0 else ""
            
            text += f"""
💼 <b>持仓更新</b>
├ 持仓价值: {pos_value:,.2f} USDT
├ 均价: ${avg_price:,.2f}
└ 盈亏: {pnl_emoji} {pnl_sign}{unrealized_pnl:,.2f} USDT ({pnl_sign}{pnl_pct:.2%})
"""
        elif position_after:
            text += "\n💼 持仓已清空"
        
        await self._send_message(text.strip())
    
    async def notify_orders_placed(
        self,
        symbol: str,
        orders: List[Dict[str, Any]],
        action: str = "new",  # new, rebuild, update
    ) -> None:
        """
        挂单通知
        
        Args:
            symbol: 交易对
            orders: 挂单列表 [{side, price, amount}, ...]
            action: new=新建, rebuild=重建, update=更新
        """
        if not self.config.order_placed and action != "rebuild":
            return
        
        if action == "rebuild" and not self.config.grid_rebuild:
            return
        
        if not self._can_notify("orders_placed"):
            return
        
        buy_orders = [o for o in orders if o.get("side") == "buy"]
        sell_orders = [o for o in orders if o.get("side") == "sell"]
        
        action_text = {
            "new": "📋 新建挂单",
            "rebuild": "🔄 网格重建",
            "update": "📝 挂单更新",
        }.get(action, "📋 挂单")
        
        text = f"<b>{action_text}</b> | {symbol}\n"
        
        if buy_orders:
            total_buy = sum(o.get("amount", 0) for o in buy_orders)
            text += f"\n🟢 <b>买单</b> ({len(buy_orders)}个, 共 {total_buy:,.0f} USDT)\n"
            buy_orders_sorted = sorted(buy_orders, key=lambda x: -x.get("price", 0))
            for i, order in enumerate(buy_orders_sorted[:5], 1):
                price = order.get("price", 0)
                amount = order.get("amount", 0)
                text += f"├ ${price:,.2f} | {amount:,.0f} USDT\n"
            if len(buy_orders) > 5:
                text += f"└ ... 还有 {len(buy_orders) - 5} 个\n"
        
        if sell_orders:
            total_sell = sum(o.get("amount", 0) for o in sell_orders)
            text += f"\n🔴 <b>卖单</b> ({len(sell_orders)}个, 共 {total_sell:,.0f} USDT)\n"
            sell_orders_sorted = sorted(sell_orders, key=lambda x: x.get("price", 0))
            for i, order in enumerate(sell_orders_sorted[:5], 1):
                price = order.get("price", 0)
                amount = order.get("amount", 0)
                text += f"├ ${price:,.2f} | {amount:,.0f} USDT\n"
            if len(sell_orders) > 5:
                text += f"└ ... 还有 {len(sell_orders) - 5} 个\n"
        
        if action == "rebuild":
            self._stats["grid_rebuilds"] += 1
            text += f"\n⚠️ 网格重建次数: {self._stats['grid_rebuilds']}"
        
        await self._send_message(text.strip())
    
    async def notify_grid_rebuild(
        self,
        symbol: str,
        reason: str,
        old_anchor: float,
        new_anchor: float,
        new_orders: List[Dict[str, Any]],
    ) -> None:
        """
        网格重建通知
        
        Args:
            symbol: 交易对
            reason: 重建原因
            old_anchor: 旧锚点价格
            new_anchor: 新锚点价格
            new_orders: 新挂单列表
        """
        if not self.config.grid_rebuild:
            return
        
        self._stats["grid_rebuilds"] += 1
        
        move_pct = (new_anchor - old_anchor) / old_anchor if old_anchor > 0 else 0
        move_emoji = "📈" if move_pct > 0 else "📉"
        
        buy_orders = [o for o in new_orders if o.get("side") == "buy"]
        total_buy = sum(o.get("amount", 0) for o in buy_orders)
        
        text = f"""
🔄 <b>网格重建</b>

📊 <b>{symbol}</b>
├ 原因: {reason}
├ 旧锚点: ${old_anchor:,.2f}
├ 新锚点: ${new_anchor:,.2f}
└ 偏移: {move_emoji} {move_pct:+.2%}

📋 新网格: {len(buy_orders)}档买单, 共 {total_buy:,.0f} USDT
"""
        
        await self._send_message(text.strip())
    
    async def notify_error(
        self,
        error_type: str,
        error_msg: str,
        context: str = "",
        suggestion: str = "",
    ) -> None:
        """
        错误通知
        
        Args:
            error_type: 错误类型
            error_msg: 错误信息
            context: 上下文（发生位置）
            suggestion: 建议操作
        """
        if not self.config.error:
            return
        
        self._stats["errors"] += 1
        
        text = f"""
❌ <b>系统错误</b>

⚠️ 类型: {error_type}
├ 错误: {error_msg}
├ 上下文: {context if context else 'N/A'}
├ 时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
        
        if suggestion:
            text += f"└ 建议: {suggestion}\n"
        
        text += f"\n📊 累计错误: {self._stats['errors']} 次"
        
        await self._send_message(text.strip())
    
    async def notify_risk_warning(
        self,
        warning_type: str,
        current_price: float,
        stop_price: float,
        position_value: float,
        current_pnl: float,
        estimated_loss: float,
    ) -> None:
        """
        风险预警通知
        
        Args:
            warning_type: 预警类型 (near_stop_loss, large_loss, etc.)
            current_price: 当前价格
            stop_price: 止损价格
            position_value: 持仓价值
            current_pnl: 当前盈亏
            estimated_loss: 预计止损亏损
        """
        if not self.config.risk_warning:
            return
        
        # 防止重复发送
        if warning_type == "near_stop_loss" and self._risk_warning_sent:
            return
        
        distance_pct = (stop_price - current_price) / current_price if current_price > 0 else 0
        
        pnl_sign = "+" if current_pnl >= 0 else ""
        pnl_pct = current_pnl / position_value if position_value > 0 else 0
        loss_pct = estimated_loss / position_value if position_value > 0 else 0
        
        text = f"""
⚠️ <b>风险预警</b>

🔴 价格接近止损线！

├ 当前价格: ${current_price:,.2f}
├ 止损价格: ${stop_price:,.2f}
├ 距离: {distance_pct:+.2%}
├ 持仓价值: {position_value:,.2f} USDT
├ 当前盈亏: {pnl_sign}{current_pnl:,.2f} USDT ({pnl_sign}{pnl_pct:.2%})
└ 触发止损预计亏损: {estimated_loss:,.2f} USDT ({loss_pct:.2%})

💡 建议: 关注市场走势，考虑是否手动干预
"""
        
        self._risk_warning_sent = True
        await self._send_message(text.strip())
    
    def reset_risk_warning(self) -> None:
        """重置风险预警状态（价格远离止损线时调用）"""
        self._risk_warning_sent = False
    
    async def send_daily_summary(
        self,
        date: str = None,
        realized_pnl: float = 0,
        unrealized_pnl: float = 0,
        position_value: float = 0,
        available_balance: float = 0,
        filled_grids: int = 0,
        total_grids: int = 0,
    ) -> None:
        """
        每日汇总通知
        
        Args:
            date: 日期
            realized_pnl: 实现盈亏
            unrealized_pnl: 未实现盈亏
            position_value: 持仓价值
            available_balance: 可用余额
            filled_grids: 已成交档位
            total_grids: 总档位
        """
        if not self.config.daily_summary:
            return
        
        if date is None:
            date = datetime.now().strftime("%Y-%m-%d")
        
        total_pnl = realized_pnl + unrealized_pnl
        total_pnl_emoji = "📈" if total_pnl >= 0 else "📉"
        total_pnl_sign = "+" if total_pnl >= 0 else ""
        
        total_asset = position_value + available_balance
        
        text = f"""
📊 <b>每日汇总</b> - {date}

💰 <b>今日盈亏</b>
├ 实现盈亏: {'+' if realized_pnl >= 0 else ''}{realized_pnl:,.2f} USDT
├ 未实现盈亏: {'+' if unrealized_pnl >= 0 else ''}{unrealized_pnl:,.2f} USDT
└ 总计: {total_pnl_emoji} {total_pnl_sign}{total_pnl:,.2f} USDT

📈 <b>交易统计</b>
├ 买入成交: {self._stats['buy_count']} 次 (共 {self._stats['buy_amount']:,.0f} USDT)
├ 卖出成交: {self._stats['sell_count']} 次 (共 {self._stats['sell_amount']:,.0f} USDT)
├ 网格重建: {self._stats['grid_rebuilds']} 次
└ 错误次数: {self._stats['errors']} 次

💼 <b>当前状态</b>
├ 持仓价值: {position_value:,.2f} USDT
├ 可用余额: {available_balance:,.2f} USDT
├ 总资产: {total_asset:,.2f} USDT
└ 网格档位: {filled_grids}/{total_grids} 已成交
"""
        
        await self._send_message(text.strip())
        
        # 重置每日统计
        self._reset_daily_stats()
    
    def _reset_daily_stats(self) -> None:
        """重置每日统计"""
        self._stats = {
            "buy_count": 0,
            "buy_amount": 0.0,
            "sell_count": 0,
            "sell_amount": 0.0,
            "realized_pnl": 0.0,
            "errors": 0,
            "grid_rebuilds": 0,
        }
    
    async def notify_heartbeat(
        self,
        symbol: str,
        current_price: float,
        position_value: float,
        unrealized_pnl: float,
        uptime_hours: float,
    ) -> None:
        """
        心跳通知
        
        Args:
            symbol: 交易对
            current_price: 当前价格
            position_value: 持仓价值
            unrealized_pnl: 未实现盈亏
            uptime_hours: 运行时长（小时）
        """
        if not self.config.heartbeat:
            return
        
        pnl_emoji = "📈" if unrealized_pnl >= 0 else "📉"
        pnl_sign = "+" if unrealized_pnl >= 0 else ""
        
        text = f"""
💚 <b>系统运行中</b>

├ 交易对: {symbol}
├ 当前价格: ${current_price:,.2f}
├ 持仓价值: {position_value:,.2f} USDT
├ 盈亏: {pnl_emoji} {pnl_sign}{unrealized_pnl:,.2f} USDT
└ 运行时长: {uptime_hours:.1f} 小时
"""
        
        await self._send_message(text.strip())
    
    def get_stats(self) -> dict:
        """获取通知统计"""
        return self._stats.copy()


# ============================================
# 便捷的独立通知函数（无需 Bot 实例）
# ============================================

class SimpleNotifier:
    """
    简易通知器
    
    不依赖 Bot 实例，直接通过 HTTP API 发送
    用于错误处理等无法访问 Bot 的场景
    """
    
    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.logger = get_logger(__name__)
    
    async def send(self, text: str) -> bool:
        """发送消息"""
        try:
            import aiohttp
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            payload = {
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": "HTML",
            }
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload) as resp:
                    return resp.status == 200
        except Exception as e:
            self.logger.error(f"发送 Telegram 消息失败: {e}")
            return False
    
    async def notify_error(self, error_type: str, error_msg: str, context: str = "") -> bool:
        """发送错误通知"""
        text = f"""
❌ <b>系统错误</b>

⚠️ 类型: {error_type}
├ 错误: {error_msg}
├ 上下文: {context if context else 'N/A'}
└ 时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
        return await self.send(text.strip())
