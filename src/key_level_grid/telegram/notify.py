"""
Telegram 通知管理模块

负责各类交易通知的发送，所有金额使用 USDT 计价
"""

import asyncio
import time
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
    quota_event: bool = True          # 配额对齐/清空通知
    position_flux: bool = True        # 持仓变化通知
    order_sync: bool = True           # 挂单同步提醒
    system_info: bool = True          # 系统操作记录
    system_alert: bool = True         # 关键告警
    
    # 风险通知
    risk_warning: bool = True         # 风险预警
    near_stop_loss_pct: float = 0.02  # 距止损预警阈值 2%
    
    # 汇总通知
    daily_summary: bool = True        # 每日汇总
    daily_summary_time: str = "20:00"
    
    # 心跳（可选）
    heartbeat: bool = False
    heartbeat_interval_hours: int = 4
    heartbeat_idle_sec: int = 3600    # 无成交心跳阈值（秒）
    
    # 防刷屏
    min_notify_interval_sec: int = 5  # 同类通知最小间隔
    silent_mode: bool = True          # 静默模式（成交合并）
    merge_fill_window_sec: int = 5    # 成交合并窗口（秒）


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
        
        # 持仓变更合并
        self._position_flux_buffer: List[Dict[str, Any]] = []
        self._position_flux_task: Optional[asyncio.Task] = None
        self._last_trade_ts: float = 0
        self._last_heartbeat_ts: float = 0
        self._last_heartbeat_date: str = ""
    
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

    def _format_qty(self, qty: float) -> str:
        if qty >= 1:
            return f"{qty:.4f} BTC"
        if qty >= 0.01:
            return f"{qty:.6f} BTC"
        return f"{qty:.8f} BTC"

    async def _flush_position_flux(self) -> None:
        if self.config.merge_fill_window_sec > 0:
            await asyncio.sleep(self.config.merge_fill_window_sec)
        if not self._position_flux_buffer:
            return
        events = self._position_flux_buffer[:]
        self._position_flux_buffer = []
        self._position_flux_task = None
        text = self._format_position_flux(events)
        await self._send_message(text)

    def _format_position_flux(self, events: List[Dict[str, Any]]) -> str:
        if not events:
            return ""
        last = events[-1]
        if len(events) == 1:
            return (
                "🔄 <b>持仓变更通知</b>\n"
                f"<b>动作</b>: {last['action']} | <b>价格</b>: {last['price']}\n"
                f"<b>数量</b>: {last['qty']} | <b>当前总仓位</b>: {last['total_qty']}\n"
                f"<b>最新均价</b>: {last['avg_price']} | <b>当前 uPNL</b>: {last['pnl']}\n\n"
                "[📊 查看明细] [🛡 调整止损]"
            )
        lines = [
            "🔄 <b>持仓变更通知</b>（合并）",
        ]
        for evt in events:
            lines.append(
                f"- {evt['action']} @ {evt['price']} | {evt['qty']}"
            )
        lines.append("")
        lines.append(
            f"<b>当前总仓位</b>: {last['total_qty']} | "
            f"<b>最新均价</b>: {last['avg_price']} | <b>当前 uPNL</b>: {last['pnl']}"
        )
        lines.append("")
        lines.append("[📊 查看明细] [🛡 调整止损]")
        return "\n".join(lines)

    async def notify_position_flux(
        self,
        *,
        action: str,
        price: float,
        qty: float,
        total_qty: float,
        avg_price: float,
        pnl: float,
    ) -> None:
        if not self.config.position_flux:
            return
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        event = {
            "action": action,
            "price": f"${price:,.2f}" if price > 0 else "N/A",
            "qty": self._format_qty(qty),
            "total_qty": self._format_qty(total_qty),
            "avg_price": f"${avg_price:,.2f}" if avg_price > 0 else "N/A",
            "pnl": f"{pnl:+,.2f} USDT",
            "timestamp": now,
        }
        self._last_trade_ts = time.time()
        if self.config.silent_mode and self.config.merge_fill_window_sec > 0:
            self._position_flux_buffer.append(event)
            if not self._position_flux_task:
                self._position_flux_task = asyncio.create_task(
                    self._flush_position_flux()
                )
            return
        await self._send_message(self._format_position_flux([event]))

    async def notify_order_sync(
        self,
        *,
        symbol: str,
        order_type: str,
        status: str,
        price: float,
        new_qty: float,
        reason: str,
    ) -> None:
        if not self.config.order_sync:
            return
        if not self._can_notify("order_sync"):
            return
        
        # 计算 USDT 价值
        usdt_value = price * new_qty
        
        # 简化格式
        status_emoji = "🟢" if "buy" in order_type.lower() else "🔴"
        text = (
            f"{status_emoji} <b>{status}挂单</b>\n"
            f"价格: ${price:,.2f}\n"
            f"数量: {self._format_qty(new_qty)} (≈ ${usdt_value:,.0f} USDT)"
        )
        await self._send_message(text)

    async def notify_recon_summary(
        self,
        *,
        symbol: str,
        summary: str,
    ) -> None:
        if not self.config.order_sync:
            return
        if not self._can_notify("recon_summary"):
            return
        text = (
            "📝 <b>挂单同步</b>\n"
            f"{summary}"
        )
        await self._send_message(text)

    async def notify_system_info(
        self,
        *,
        event: str,
        result: str,
        duration_sec: Optional[float] = None,
    ) -> None:
        if not self.config.system_info:
            return
        if not self._can_notify("system_info"):
            return
        duration_text = f"{duration_sec:.1f}s" if duration_sec is not None else "N/A"
        text = (
            "ℹ️ <b>系统操作记录</b>\n"
            f"<b>事件</b>: {event}\n"
            f"<b>结果</b>: {result}\n"
            f"<b>耗时</b>: {duration_text}"
        )
        await self._send_message(text)

    async def notify_system_alert(
        self,
        *,
        error_type: str,
        error_code: str = "",
        error_msg: str,
        impact: str,
        suggestion: str = "",
        traceback_text: str = "",
    ) -> None:
        if not self.config.system_alert:
            return
        if not self._can_notify("system_alert"):
            return
        code_text = error_code or "N/A"
        text = (
            "🚨 <b>关键告警：系统异常</b>\n"
            f"<b>类型</b>: {error_type}\n"
            f"<b>错误码</b>: {code_text} | <b>信息</b>: {error_msg}\n"
            f"<b>影响</b>: {impact}\n"
        )
        if traceback_text:
            text += f"\n<code>{traceback_text}</code>\n"
        if suggestion:
            text += f"\n建议: {suggestion}"
        text += "\n\n[🛠 强制对账] [🔌 停止机器人]"
        await self._send_message(text.strip())

    async def notify_idle_heartbeat(
        self,
        *,
        symbol: str,
        current_price: float,
        position_value: float,
        unrealized_pnl: float,
        uptime_hours: float,
    ) -> None:
        if not self.config.heartbeat:
            return
        now_ts = time.time()
        now_dt = datetime.now()
        heartbeat_hours = int(getattr(self.config, "heartbeat_interval_hours", 0) or 0)
        daily_time = getattr(self.config, "daily_summary_time", "08:00") or "08:00"

        # 如果设置为每日心跳（>=24h），仅在指定时间发送一次
        if heartbeat_hours >= 24:
            try:
                hour, minute = daily_time.split(":")
                target_hour = int(hour)
                target_minute = int(minute)
            except ValueError:
                target_hour, target_minute = 8, 0

            if (now_dt.hour, now_dt.minute) < (target_hour, target_minute):
                return

            today = now_dt.strftime("%Y-%m-%d")
            if self._last_heartbeat_date == today:
                return

            self._last_heartbeat_date = today
            await self.notify_heartbeat(
                symbol=symbol,
                current_price=current_price,
                position_value=position_value,
                unrealized_pnl=unrealized_pnl,
                uptime_hours=uptime_hours,
            )
            return

        if self._last_trade_ts and now_ts - self._last_trade_ts < self.config.heartbeat_idle_sec:
            return
        if self._last_heartbeat_ts and now_ts - self._last_heartbeat_ts < self.config.heartbeat_idle_sec:
            return
        self._last_heartbeat_ts = now_ts
        await self.notify_heartbeat(
            symbol=symbol,
            current_price=current_price,
            position_value=position_value,
            unrealized_pnl=unrealized_pnl,
            uptime_hours=uptime_hours,
        )
    
    def _format_source(self, source: str) -> str:
        """格式化来源（支持复合来源如 swing_5+volume_node）"""
        if not source:
            return ""
        
        source_map = {
            "volume_node": "VOL",
            "round_number": "PSY",
        }
        
        parts = source.split("+")
        abbrs = []
        for p in parts:
            p = p.strip()
            if p.startswith("swing_"):
                abbrs.append(f"SW{p.replace('swing_', '')}")
            elif p.startswith("fib_"):
                abbrs.append(f"FIB{p.replace('fib_', '')}")
            elif p in source_map:
                abbrs.append(source_map[p])
            else:
                abbrs.append(p[:3].upper())
        return "+".join(abbrs)
    
    def _format_timeframe(self, tf: str) -> str:
        """格式化周期"""
        tf_map = {
            "1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m",
            "1h": "1H", "4h": "4H", "1d": "1D", "1w": "1W",
            "multi": "MTF",
        }
        return tf_map.get(tf, tf.upper() if tf else "")
    
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

        def get_progress_bar(percent: float) -> str:
            percent = max(0.0, min(percent, 1.0))
            length = 12
            filled = int(length * percent)
            bar = "▬" * filled + "●" + "▬" * (length - filled)
            return f"[{bar}]"

        total_balance = account.get("total_balance", 0)
        available = account.get("available", 0)
        leverage = grid_config.get("leverage", 0)
        num_grids = grid_config.get("num_grids", 0)
        sl_pct = grid_config.get("sl_pct", 0)
        grid_min = grid_config.get("grid_min", 0) or 0
        grid_max = grid_config.get("grid_max", 0) or 0
        grid_floor = grid_config.get("grid_floor", 0) or 0
        sell_quota_ratio = grid_config.get("sell_quota_ratio", 1.0)
        
        # 计算保留底仓比例（保留比例 = 1 - 卖出比例）
        retain_ratio = 1.0 - sell_quota_ratio

        pos_value = position.get("value", 0)
        avg_price = position.get("avg_price", 0)
        unrealized_pnl = position.get("unrealized_pnl", 0)
        pnl_pct = position.get("pnl_pct", 0) * 100 if position.get("pnl_pct", 0) else 0

        buy_orders = [o for o in pending_orders if o.get("side") == "buy"]
        sell_orders = [o for o in pending_orders if o.get("side") == "sell"]
        buy_cnt = len(buy_orders)
        sell_cnt = len(sell_orders)
        buy_total = sum(o.get("amount", 0) for o in buy_orders)
        sell_total = sum(o.get("amount", 0) for o in sell_orders)
        next_buy = max((o.get("price", 0) for o in buy_orders), default=0)
        next_sell = min((o.get("price", 0) for o in sell_orders), default=0)

        pos_percent = 0.5
        if grid_min > 0 and grid_max > grid_min and current_price > 0:
            pos_percent = (current_price - grid_min) / (grid_max - grid_min)
        pos_bar = get_progress_bar(pos_percent)

        # 配置行：根据是否有保留底仓动态显示
        if retain_ratio > 0:
            retain_pct = int(retain_ratio * 100)
            config_line = f"⚙️ <b>配置</b>: <code>{leverage}x</code> | <code>保留{retain_pct}%底仓</code>"
        else:
            config_line = f"⚙️ <b>配置</b>: <code>{leverage}x</code> | <code>{num_grids}档</code>"

        text = (
            f"🚀 <b>策略启动: {symbol} ({exchange.upper()})</b>\n"
            f"━━━━━━━━━━━━━━\n"
            f"💰 <b>资金</b>: <code>{total_balance:,.2f}</code> (可用: <code>{available:,.2f}</code>)\n"
            f"{config_line}\n"
            f"🌐 <b>区间</b>: <code>{grid_min:,.2f}</code> - <code>{grid_max:,.2f}</code>\n"
            f"📍 <b>位置</b>: <code>{pos_bar}</code>\n\n"
            f"💼 <b>持仓</b>: <code>{pos_value:,.2f} USDT</code> (@ <code>{avg_price:,.2f}</code>)\n"
            f"📈 <b>盈亏</b>: <code>{unrealized_pnl:+,.2f} ({pnl_pct:+.2f}%)</code>\n\n"
            f"🔔 <b>网格状态</b>:\n"
            f"🟢 买单: <code>{buy_cnt}个</code> (<code>{buy_total:,.0f} USDT</code>) | 最近: <code>${next_buy:,.2f}</code>\n"
            f"🔴 卖单: <code>{sell_cnt}个</code> (<code>{sell_total:,.0f} USDT</code>) | 最近: <code>${next_sell:,.2f}</code>\n"
            f"🛡 <b>核心防御</b>: <code>${grid_floor:,.2f}</code> (底线)\n"
            f"━━━━━━━━━━━━━━"
        )

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

    async def notify_quota_event(
        self,
        symbol: str,
        action: str,
        detail: str,
    ) -> None:
        """配额对齐/清空通知 - 暂时屏蔽"""
        # 暂时屏蔽配额事件推送
        return
        # if not self.config.quota_event:
        #     return
        # if not self._can_notify("quota_event"):
        #     return
        # action_text = {
        #     "reconcile": "🧩 配额对齐",
        #     "auto_clear": "🧹 配额清零",
        #     "manual_reset": "🧹 手动清空配额",
        # }.get(action, "🧩 配额事件")
        # timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        # text = (
        #     f"{action_text}\n\n"
        #     f"📊 <b>{symbol}</b>\n"
        #     f"{detail}\n"
        #     f"\n🕐 {timestamp}"
        # )
        # await self._send_message(text.strip())
    
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
