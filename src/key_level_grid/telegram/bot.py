"""
Telegram Bot 核心模块

使用 python-telegram-bot 库实现 Bot 功能
"""

import asyncio
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, TYPE_CHECKING

from key_level_grid.utils.logger import get_logger

if TYPE_CHECKING:
    from key_level_grid.strategy import KeyLevelGridStrategy

try:
    from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
    from telegram.ext import (
        Application,
        CommandHandler as TGCommandHandler,
        CallbackQueryHandler,
        MessageHandler,
        filters,
        ContextTypes,
    )
    TELEGRAM_AVAILABLE = True
except ImportError:
    TELEGRAM_AVAILABLE = False
    Update = None
    InlineKeyboardButton = None
    InlineKeyboardMarkup = None
    ReplyKeyboardMarkup = None
    KeyboardButton = None


@dataclass
class TelegramConfig:
    """Telegram 配置"""
    bot_token: str
    chat_id: str
    
    # 确认设置
    confirmation_enabled: bool = True
    confirmation_timeout_sec: int = 60
    auto_confirm_on_timeout: bool = False
    
    # 权限
    allowed_user_ids: List[int] = None
    admin_user_ids: List[int] = None


class KeyLevelTelegramBot:
    """
    关键位网格策略 Telegram Bot
    
    功能:
    1. 信号通知与确认
    2. 状态查询
    3. 策略控制
    """
    
    def __init__(
        self,
        config: TelegramConfig,
        strategy: Optional["KeyLevelGridStrategy"] = None
    ):
        if not TELEGRAM_AVAILABLE:
            raise ImportError(
                "telegram 库未安装，请运行: pip install python-telegram-bot"
            )
        
        self.config = config
        self.strategy = strategy
        self.logger = get_logger(__name__)
        
        # Bot 应用
        self.app: Optional[Application] = None
        
        # 待确认的信号
        self._pending_confirmations: Dict[str, dict] = {}
        
        # 回调处理器
        self._on_confirm: Optional[Callable] = None
        self._on_reject: Optional[Callable] = None

        # 最近一次收到指令的时间戳（用于卡死检测）
        self._last_update_ts: float = time.time()
    
    def set_strategy(self, strategy: "KeyLevelGridStrategy") -> None:
        """设置策略引用"""
        self.strategy = strategy
    
    async def start(self) -> None:
        """启动 Bot"""
        self.app = Application.builder().token(self.config.bot_token).build()
        
        # 注册命令处理器
        self.app.add_handler(TGCommandHandler("start", self._cmd_start))
        self.app.add_handler(TGCommandHandler("help", self._cmd_help))
        self.app.add_handler(TGCommandHandler("status", self._cmd_status))
        self.app.add_handler(TGCommandHandler("position", self._cmd_position))
        self.app.add_handler(TGCommandHandler("orders", self._cmd_orders))
        self.app.add_handler(TGCommandHandler("indicators", self._cmd_indicators))
        self.app.add_handler(TGCommandHandler("levels", self._cmd_levels))
        self.app.add_handler(TGCommandHandler("rebuild", self._cmd_rebuild))
        self.app.add_handler(TGCommandHandler("stop", self._cmd_stop))
        self.app.add_handler(TGCommandHandler("closeall", self._cmd_close_all))
        
        # 注册回调处理器 (按钮点击)
        self.app.add_handler(CallbackQueryHandler(self._handle_callback))
        
        # 注册消息处理器 (菜单按钮)
        self.app.add_handler(MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            self._handle_menu_button
        ))
        
        # 注册错误处理器
        self.app.add_error_handler(self._error_handler)
        
        # 启动 Bot
        self.logger.info("正在初始化 Telegram Bot...")
        await self.app.initialize()
        await self.app.start()
        
        # 删除可能存在的 webhook（webhook 会阻止 polling）
        self.logger.info("清除可能存在的 webhook...")
        await self.app.bot.delete_webhook(drop_pending_updates=True)
        
        # 启动 polling
        self.logger.info("正在启动 Telegram polling...")
        await self.app.updater.start_polling(
            allowed_updates=Update.ALL_TYPES,  # 接收所有类型的更新
        )
        
        # 验证 polling 状态
        if self.app.updater.running:
            self.logger.info(f"✅ Telegram Bot polling 已启动，chat_id={self.config.chat_id}")
            self._last_update_ts = time.time()
        else:
            self.logger.error("❌ Telegram Bot polling 启动失败")
    
    async def _error_handler(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        """处理 Bot 错误"""
        self.logger.error(f"Telegram Bot 错误: {context.error}", exc_info=context.error)
        
        # 如果是网络错误，尝试重新发送
        import telegram.error
        if isinstance(context.error, (telegram.error.NetworkError, telegram.error.TimedOut)):
            self.logger.warning("网络错误，Bot 将自动重试...")
    
    def _get_main_menu(self) -> ReplyKeyboardMarkup:
        """获取主菜单键盘"""
        keyboard = [
            [KeyboardButton("📊 当前持仓"), KeyboardButton("📋 当前挂单")],
            [KeyboardButton("🔄 更新网格"), KeyboardButton("📍 关键价位")],
            [KeyboardButton("🔍 查询价位")],  # 查询任意标的的支撑/阻力位
        ]
        return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    async def stop(self) -> None:
        """停止 Bot"""
        if self.app:
            await self.app.updater.stop()
            await self.app.stop()
            await self.app.shutdown()
        
        self.logger.info("Telegram Bot 已停止")
    
    def is_running(self) -> bool:
        """检查 Bot 是否正在运行"""
        if not self.app or not self.app.updater:
            return False
        return self.app.updater.running

    def get_last_update_ts(self) -> float:
        """获取最近一次收到用户指令的时间戳"""
        return self._last_update_ts
    
    async def restart(self) -> None:
        """重启 Bot"""
        self.logger.info("正在重启 Telegram Bot...")
        try:
            await self.stop()
        except Exception as e:
            self.logger.warning(f"停止 Bot 时出错: {e}")
        
        await asyncio.sleep(2)
        await self.start()
        self.logger.info("Telegram Bot 已重启")

    def _mark_alive(self) -> None:
        """更新最近活动时间戳"""
        self._last_update_ts = time.time()
    
    async def send_message(self, text: str, parse_mode: str = "HTML") -> None:
        """发送消息"""
        if self.app:
            await self.app.bot.send_message(
                chat_id=self.config.chat_id,
                text=text,
                parse_mode=parse_mode
            )
    
    async def send_signal_confirmation(
        self,
        signal_id: str,
        signal_data: dict,
        timeout_sec: Optional[int] = None
    ) -> None:
        """
        发送信号确认请求
        
        Args:
            signal_id: 信号ID
            signal_data: 信号数据
            timeout_sec: 超时时间
        """
        timeout = timeout_sec or self.config.confirmation_timeout_sec
        
        # 构建消息
        signal_type = signal_data.get("signal_type", "N/A")
        symbol = signal_data.get("symbol", "N/A")
        entry_price = signal_data.get("entry_price", 0)
        stop_loss = signal_data.get("stop_loss", 0)
        score = signal_data.get("score", 0)
        grade = signal_data.get("grade", "N/A")
        
        direction = "🟢 做多" if "long" in signal_type.lower() else "🔴 做空"
        
        text = f"""
🎯 <b>新交易信号</b>

{direction} <b>{symbol}</b>

📊 <b>信号详情</b>
├ 类型: {signal_type}
├ 入场价: {entry_price:.4f}
├ 止损价: {stop_loss:.4f}
├ 评分: {score}/100
└ 等级: {grade}

⏰ 等待确认 ({timeout}秒超时)
"""
        
        # 创建确认按钮
        keyboard = [
            [
                InlineKeyboardButton("✅ 确认开仓", callback_data=f"confirm_{signal_id}"),
                InlineKeyboardButton("❌ 拒绝", callback_data=f"reject_{signal_id}"),
            ],
            [
                InlineKeyboardButton("📊 查看详情", callback_data=f"detail_{signal_id}"),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # 保存待确认信号
        self._pending_confirmations[signal_id] = {
            "signal_data": signal_data,
            "timeout": timeout,
            "confirmed": False,
            "rejected": False,
        }
        
        # 发送消息
        await self.app.bot.send_message(
            chat_id=self.config.chat_id,
            text=text,
            parse_mode="HTML",
            reply_markup=reply_markup
        )
        
        # 启动超时任务
        asyncio.create_task(self._handle_confirmation_timeout(signal_id, timeout))
    
    async def _handle_confirmation_timeout(
        self,
        signal_id: str,
        timeout_sec: int
    ) -> None:
        """处理确认超时"""
        await asyncio.sleep(timeout_sec)
        
        if signal_id in self._pending_confirmations:
            pending = self._pending_confirmations[signal_id]
            
            if not pending["confirmed"] and not pending["rejected"]:
                if self.config.auto_confirm_on_timeout:
                    # 自动确认
                    await self._confirm_signal(signal_id)
                    await self.send_message("⏰ 超时自动确认")
                else:
                    # 自动拒绝
                    pending["rejected"] = True
                    await self.send_message("⏰ 确认超时，信号已失效")
                
                del self._pending_confirmations[signal_id]
    
    async def _confirm_signal(self, signal_id: str) -> None:
        """确认信号"""
        if self.strategy:
            self.strategy.confirm_signal()
        
        if self._on_confirm:
            await self._on_confirm(signal_id)
    
    async def _reject_signal(self, signal_id: str) -> None:
        """拒绝信号"""
        if self.strategy:
            self.strategy.reject_signal()
        
        if self._on_reject:
            await self._on_reject(signal_id)
    
    async def _handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """处理按钮回调"""
        query = update.callback_query
        await query.answer()
        
        self.logger.info(f"收到回调: {query.data}")
        self._mark_alive()
        
        data = query.data
        
        if data.startswith("confirm_"):
            signal_id = data.replace("confirm_", "")
            if signal_id in self._pending_confirmations:
                self._pending_confirmations[signal_id]["confirmed"] = True
                await self._confirm_signal(signal_id)
                try:
                    await query.edit_message_text("✅ 已确认开仓")
                except Exception:
                    pass
                del self._pending_confirmations[signal_id]
        
        elif data.startswith("reject_"):
            signal_id = data.replace("reject_", "")
            if signal_id in self._pending_confirmations:
                self._pending_confirmations[signal_id]["rejected"] = True
                await self._reject_signal(signal_id)
                try:
                    await query.edit_message_text("❌ 已拒绝信号")
                except Exception:
                    pass
                del self._pending_confirmations[signal_id]
        
        elif data.startswith("detail_"):
            signal_id = data.replace("detail_", "")
            if signal_id in self._pending_confirmations:
                signal_data = self._pending_confirmations[signal_id]["signal_data"]
                detail_text = self._format_signal_detail(signal_data)
                await query.message.reply_text(detail_text, parse_mode="HTML")
        
        elif data == "rebuild_confirm":
            try:
                await query.edit_message_text("🔄 正在更新网格...")
            except Exception:
                pass  # 忽略消息未修改的错误
            
            if self.strategy:
                try:
                    result = await self.strategy.force_rebuild_grid()
                    if result:
                        await query.message.reply_text(
                            "✅ <b>网格更新成功</b>\n\n"
                            f"已根据最新支撑/阻力位重新挂单",
                            parse_mode="HTML"
                        )
                    else:
                        await query.message.reply_text("⚠️ 网格更新失败，请查看日志")
                except Exception as e:
                    await query.message.reply_text(f"❌ 更新失败: {e}")
        
        elif data == "rebuild_cancel":
            try:
                await query.edit_message_text("❌ 已取消更新网格")
            except Exception:
                pass
        
        elif data == "closeall_confirm":
            try:
                await query.edit_message_text("🔄 正在平仓...")
            except Exception:
                pass
            
            if self.strategy:
                try:
                    # TODO: 实现平仓逻辑
                    await query.message.reply_text("⚠️ 平仓功能尚未实现")
                except Exception as e:
                    await query.message.reply_text(f"❌ 平仓失败: {e}")
        
        elif data == "closeall_cancel":
            try:
                await query.edit_message_text("❌ 已取消平仓")
            except Exception:
                pass
    
    def _format_signal_detail(self, signal_data: dict) -> str:
        """格式化信号详情"""
        return f"""
📋 <b>信号详情</b>

├ 信号ID: {signal_data.get('signal_id', 'N/A')}
├ 时间戳: {signal_data.get('timestamp', 0)}
├ 当前价格: {signal_data.get('current_price', 0):.4f}
├ 入场价: {signal_data.get('entry_price', 0):.4f}
├ 止损价: {signal_data.get('stop_loss', 0):.4f}
├ 止盈价: {signal_data.get('take_profits', [])}
├ 置信度: {signal_data.get('confidence', 0):.1f}%
├ 触发原因: {signal_data.get('trigger_reason', 'N/A')}
└ 通过过滤: {signal_data.get('filters_passed', [])}
"""
    
    # ===== 命令处理器 =====
    
    async def _cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """处理 /start 命令"""
        user = update.effective_user
        self.logger.info(f"收到 /start 命令，用户: {user.id} ({user.username})")
        
        text = """
🎰 <b>Key Level Grid Strategy Bot</b>

关键位网格交易策略机器人

请使用下方菜单操作，或输入命令：
/position - 当前持仓
/orders - 当前挂单
/rebuild - 更新网格
/levels - 关键价位
/help - 更多帮助
"""
        await update.message.reply_text(
            text, 
            parse_mode="HTML",
            reply_markup=self._get_main_menu()
        )
        self.logger.info("已发送欢迎消息和菜单")
    
    async def _cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """处理 /help 命令"""
        text = """
📚 <b>帮助信息</b>

<b>查询命令:</b>
/status - 策略运行状态
/position - 当前持仓信息
/indicators - 市场指标状态
/levels - 关键价位
/orders - 当前挂单

<b>控制命令:</b>
/stop - 停止策略
/closeall - 平掉所有仓位

<b>信号确认:</b>
收到信号后点击按钮确认或拒绝
"""
        await update.message.reply_text(text, parse_mode="HTML")
    
    async def _cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """处理 /status 命令"""
        if not self.strategy:
            await update.message.reply_text("❌ 策略未连接")
            return
        
        status = self.strategy.get_status()
        
        running = "🟢 运行中" if status.get("running") else "🔴 已停止"
        symbol = status.get("symbol", "N/A")
        price = status.get("current_price", 0)
        
        indicators = status.get("indicators", {})
        adx = indicators.get("adx", 0)
        rsi = indicators.get("rsi", 0)
        
        # 趋势判断
        trend = "无趋势"
        trend_emoji = "➡️"
        if adx and adx > 40:
            trend = "强趋势"
            trend_emoji = "📈"
        elif adx and adx > 25:
            trend = "弱趋势"
            trend_emoji = "📊"
        
        from datetime import datetime
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        text = f"""
📊 <b>策略状态</b>

├ 状态: {running}
├ 交易对: {symbol}
├ 当前价格: {price:.4f if price else 'N/A'}
├ 趋势强度: {trend_emoji} ADX={adx:.1f if adx else 'N/A'} ({trend})
└ RSI: {rsi:.1f if rsi else 'N/A'}

🕐 {timestamp}
"""
        await update.message.reply_text(text, parse_mode="HTML")
    
    async def _cmd_position(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """处理 /position 命令"""
        if not self.strategy:
            await update.message.reply_text("❌ 策略未连接")
            return
        
        # 使用 get_display_data 获取真实持仓数据
        data = self.strategy.get_display_data()
        position = data.get("position", {})
        
        # 检查是否有持仓 (value > 0 或 qty > 0)
        value = position.get("value", 0)
        qty = position.get("qty", 0)
        if not position or (value <= 0 and qty <= 0):
            await update.message.reply_text("📭 当前无持仓")
            return
        
        direction = position.get("side", "long")
        dir_emoji = "🟢" if direction == "long" else "🔴"
        
        pnl = position.get("unrealized_pnl", 0)
        pnl_emoji = "📈" if pnl >= 0 else "📉"
        
        # 获取当前价格
        price_obj = data.get("price", {})
        current_price = price_obj.get("current", 0) if isinstance(price_obj, dict) else 0
        
        # 计算盈亏百分比
        entry_price = position.get("avg_entry_price", 0)
        if entry_price > 0 and current_price > 0:
            pnl_pct = (current_price - entry_price) / entry_price if direction == "long" else (entry_price - current_price) / entry_price
        else:
            pnl_pct = 0
        
        # 网格底线：优先从配置读取 manual_lower，回退到持久化状态
        grid_floor = position.get("grid_floor", 0)
        config_lower = 0
        if self.strategy:
            grid_config = getattr(self.strategy.position_manager, 'grid_config', None)
            if grid_config and grid_config.range_mode == "manual" and grid_config.manual_lower > 0:
                config_lower = grid_config.manual_lower
        display_floor = config_lower if config_lower > 0 else grid_floor
        
        # 计算止损相关数据
        sl_id = getattr(self.strategy, "_stop_loss_order_id", None) if self.strategy else None
        # 优先使用实际止损触发价，回退到 grid_floor
        sl_trigger_price = getattr(self.strategy, "_stop_loss_trigger_price", 0) if self.strategy else 0
        sl_price = sl_trigger_price if sl_trigger_price > 0 else grid_floor
        
        # 止损触发时的价值和预计亏损
        if sl_price > 0 and qty > 0 and entry_price > 0:
            sl_value = sl_price * qty  # 止损触发时的平仓价值
            sl_loss = (entry_price - sl_price) * qty  # 预计亏损（做多）
            stop_loss_line = f"触发价=${sl_price:,.2f}, 价值: {sl_value:,.0f} USDT, 预计亏损: {sl_loss:,.0f} USDT"
        elif sl_price > 0:
            stop_loss_line = f"触发价=${sl_price:,.2f}"
        else:
            stop_loss_line = "未设置"
        
        # 如果止损单未提交，添加提示
        if not sl_id and display_floor > 0:
            stop_loss_line += " (待提交)"
        
        from datetime import datetime
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        text = f"""
💼 <b>当前持仓</b>

├ 方向: {dir_emoji} {direction.upper()}
├ 数量: {qty:.6f} BTC
├ 价值: {value:,.2f} USDT
├ 均价: ${entry_price:,.2f}
├ 当前价: ${current_price:,.2f}
├ 未实现盈亏: {pnl_emoji} {pnl:+,.2f} USDT ({pnl_pct:+.2%})
├ 网格底线: ${display_floor:,.2f}
└ 止损单: {stop_loss_line}

🕐 {timestamp}
"""
        await update.message.reply_text(text, parse_mode="HTML")
    
    async def _cmd_indicators(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """处理 /indicators 命令"""
        if not self.strategy:
            await update.message.reply_text("❌ 策略未连接")
            return
        
        data = self.strategy.get_display_data()
        indicators = data.get("indicators", {})
        
        if not indicators:
            await update.message.reply_text("❌ 无指标数据")
            return
        
        macd = indicators.get("macd", 0)
        macd_hist = indicators.get("macd_histogram", 0)
        rsi = indicators.get("rsi", 0)
        adx = indicators.get("adx", 0)
        atr = indicators.get("atr", 0)
        volume_ratio = indicators.get("volume_ratio", 0)
        
        # 趋势判断
        trend = "震荡"
        if adx and adx > 40:
            trend = "强趋势"
        elif adx and adx > 25:
            trend = "弱趋势"
        
        # RSI 状态
        rsi_status = "正常"
        if rsi and rsi > 70:
            rsi_status = "超买"
        elif rsi and rsi < 30:
            rsi_status = "超卖"
        
        from datetime import datetime
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        text = f"""
📈 <b>市场指标</b>

├ MACD: {macd:.4f if macd else 'N/A'}
├ MACD柱: {macd_hist:.4f if macd_hist else 'N/A'}
├ RSI: {rsi:.1f if rsi else 'N/A'} ({rsi_status})
├ ADX: {adx:.1f if adx else 'N/A'} ({trend})
├ ATR: {atr:.4f if atr else 'N/A'}
└ 量比: {volume_ratio:.2f if volume_ratio else 'N/A'}x

🕐 {timestamp}
"""
        await update.message.reply_text(text, parse_mode="HTML")
    
    async def _cmd_levels(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """
        处理 /levels 命令 - 显示关键价位
        
        支持两种用法:
        1. /levels         - 显示当前策略标的的关键价位
        2. /levels TSLA 4h 1d  - 查询任意标的的关键价位
        """
        args = context.args if context.args else []
        
        # 如果有参数，查询任意标的
        if args:
            await self._query_external_levels(update, args)
            return
        
        # 无参数，显示当前策略标的
        if not self.strategy:
            await update.message.reply_text(
                "❌ 策略未连接\n\n"
                "💡 你可以查询任意标的:\n"
                "/levels TSLA 4h 1d\n"
                "/levels BTCUSDT 4h 1d"
            )
            return
        
        data = self.strategy.get_display_data()
        price = data.get("price", {}).get("current", 0)
        resistance = data.get("resistance_levels", [])
        support = data.get("support_levels", [])
        
        text = self._format_levels_text(
            symbol="当前标的",
            timeframes=[],
            price=price,
            resistance=resistance,
            support=support,
        )
        
        await update.message.reply_text(text, parse_mode="HTML")
    
    async def _query_external_levels(self, update: Update, args: list) -> None:
        """
        查询任意标的的关键价位
        
        Args:
            args: [symbol, timeframe1, timeframe2, ...]
        """
        import time
        
        if len(args) < 2:
            await update.message.reply_text(
                "❌ 参数不足\n\n"
                "用法: /levels <标的> <周期1> [周期2] ...\n"
                "示例:\n"
                "  /levels TSLA 4h 1d\n"
                "  /levels BTCUSDT 4h\n"
                "  /levels AAPL 1d"
            )
            return
        
        symbol = args[0].upper()
        timeframes = [tf.lower() for tf in args[1:]]
        
        # 限流检查（每用户每分钟 5 次）
        user_id = update.effective_user.id
        cache_key = f"levels_query_{user_id}"
        now = time.time()
        
        if not hasattr(self, "_query_rate_limit"):
            self._query_rate_limit = {}
        
        user_queries = self._query_rate_limit.get(cache_key, [])
        # 清理 1 分钟前的记录
        user_queries = [t for t in user_queries if now - t < 60]
        
        if len(user_queries) >= 5:
            await update.message.reply_text("⚠️ 查询太频繁，请稍后再试（每分钟限 5 次）")
            return
        
        user_queries.append(now)
        self._query_rate_limit[cache_key] = user_queries
        
        # 发送处理中消息
        processing_msg = await update.message.reply_text(
            f"⏳ 正在计算 {symbol} 关键价位..."
        )
        
        try:
            # 调用计算逻辑
            result = await self._calculate_external_levels(symbol, timeframes)
            
            if result.get("error"):
                await processing_msg.edit_text(f"❌ {result['error']}")
                return
            
            # 格式化输出
            text = self._format_levels_text(
                symbol=symbol,
                timeframes=timeframes,
                price=result["current_price"],
                resistance=result["resistance"],
                support=result["support"],
            )
            
            # 如果使用了较低的阈值，添加提示
            min_strength_used = result.get("min_strength_used", 60)
            if min_strength_used < 60:
                text += f"\n\n<i>⚠️ 该标的波动较小，使用了较低阈值 (≥{min_strength_used})</i>"
            
            await processing_msg.edit_text(text, parse_mode="HTML")
            
        except Exception as e:
            self.logger.error(f"查询 {symbol} 关键价位失败: {e}", exc_info=True)
            await processing_msg.edit_text(f"❌ 查询失败: {str(e)[:100]}")
    
    def _load_resistance_config(self):
        """从配置文件加载阻力位配置"""
        import os
        import yaml
        from key_level_grid.resistance import ResistanceConfig
        
        config_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "..", "configs", "config.yaml"
        )
        
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                raw_config = yaml.safe_load(f)
            resistance_raw = raw_config.get("resistance", {})
            
            return ResistanceConfig(
                swing_lookbacks=resistance_raw.get('swing_lookbacks', [5, 13, 34]),
                fib_ratios=resistance_raw.get('fib_ratios', [0.382, 0.5, 0.618, 1.0, 1.618]),
                merge_tolerance=resistance_raw.get('merge_tolerance', 0.005),
                min_distance_pct=resistance_raw.get('min_distance_pct', 0.005),
                max_distance_pct=resistance_raw.get('max_distance_pct', 0.30),
            )
        except Exception as e:
            self.logger.warning(f"加载配置文件失败: {e}，使用默认值")
            return ResistanceConfig()

    async def _calculate_external_levels(self, symbol: str, timeframes: list) -> dict:
        """
        计算任意标的的关键价位
        
        自动检测数据源（币圈/美股）
        """
        from key_level_grid.models import Timeframe
        from key_level_grid.resistance import ResistanceCalculator, ResistanceConfig
        
        # 检测数据源
        crypto_suffixes = ["USDT", "USD", "BTC", "ETH", "BUSD", "USDC"]
        is_crypto = any(symbol.endswith(suffix) for suffix in crypto_suffixes)
        
        try:
            if is_crypto:
                # 币圈：使用 Gate 期货
                klines_dict = await self._fetch_gate_klines_for_query(symbol, timeframes)
            else:
                # 美股：使用 Polygon
                klines_dict = await self._fetch_polygon_klines_for_query(symbol, timeframes)
            
            if not klines_dict or not klines_dict.get(timeframes[0]):
                return {"error": f"未获取到 {symbol} 的 K 线数据"}
            
            primary_klines = klines_dict[timeframes[0]]
            current_price = primary_klines[-1].close
            
            # 计算价位：优先使用策略配置，否则从配置文件加载
            if self.strategy and hasattr(self.strategy, 'position_manager'):
                # 使用策略的 resistance_calc（已包含配置）
                calculator = self.strategy.position_manager.resistance_calc
            else:
                # 从配置文件加载参数
                config = self._load_resistance_config()
                calculator = ResistanceCalculator(config)
            
            resistances = calculator.calculate_resistance_levels(
                current_price=current_price,
                klines=primary_klines,
                direction="long",
                klines_by_timeframe=klines_dict,  # 新的多周期参数
            )
            
            supports = calculator.calculate_support_levels(
                current_price=current_price,
                klines=primary_klines,
                klines_by_timeframe=klines_dict,  # 新的多周期参数
            )
            
            # 格式化结果（自动降级阈值）
            # 先尝试 min_strength=60，如果结果太少则降低到 40，再降低到 30
            for min_strength in [60, 40, 30]:
                resistance_list = [
                    {
                        "price": r.price,
                        "strength": r.strength,
                        "type": r.level_type.value if hasattr(r.level_type, 'value') else str(r.level_type),
                    }
                    for r in resistances if r.strength >= min_strength
                ][:10]
                
                support_list = [
                    {
                        "price": s.price,
                        "strength": s.strength,
                        "type": s.level_type.value if hasattr(s.level_type, 'value') else str(s.level_type),
                    }
                    for s in supports if s.strength >= min_strength
                ][:10]
                
                # 如果有足够的结果，使用当前阈值
                if len(resistance_list) >= 3 or len(support_list) >= 3:
                    break
            
            return {
                "current_price": current_price,
                "resistance": resistance_list,
                "support": support_list,
                "min_strength_used": min_strength,  # 返回实际使用的阈值
            }
            
        except Exception as e:
            self.logger.error(f"计算 {symbol} 价位失败: {e}", exc_info=True)
            return {"error": str(e)}
    
    async def _fetch_gate_klines_for_query(self, symbol: str, timeframes: list) -> dict:
        """获取 Gate.io 期货 K 线用于查询"""
        from key_level_grid.gate_kline_feed import GateKlineFeed
        from key_level_grid.models import KlineFeedConfig, Timeframe
        
        primary_tf = Timeframe.from_string(timeframes[0])
        aux_tfs = [Timeframe.from_string(tf) for tf in timeframes[1:]] if len(timeframes) > 1 else []
        
        config = KlineFeedConfig(
            symbol=symbol,
            primary_timeframe=primary_tf,
            auxiliary_timeframes=aux_tfs,
            history_bars=500,
        )
        
        feed = GateKlineFeed(config)
        await feed.start()
        
        result = {}
        try:
            klines = await feed.get_latest_klines(primary_tf)
            result[timeframes[0]] = klines
            
            for tf_str in timeframes[1:]:
                tf = Timeframe.from_string(tf_str)
                klines = feed.get_cached_klines(tf)
                result[tf_str] = klines
        finally:
            await feed.stop()
        
        return result
    
    async def _fetch_polygon_klines_for_query(self, symbol: str, timeframes: list) -> dict:
        """获取 Polygon K 线用于查询"""
        from key_level_grid.polygon_kline_feed import PolygonKlineFeed
        from key_level_grid.models import Timeframe
        
        feed = PolygonKlineFeed(symbol)
        await feed.start()
        
        result = {}
        try:
            for tf_str in timeframes:
                tf = Timeframe.from_string(tf_str)
                klines = await feed.get_klines(tf, 500)
                result[tf_str] = klines
        finally:
            await feed.stop()
        
        return result
    
    def _format_levels_text(
        self,
        symbol: str,
        timeframes: list,
        price: float,
        resistance: list,
        support: list,
    ) -> str:
        """格式化关键价位文本"""
        # 类型简写映射
        type_map = {
            "swing_high": "SW", "swing_low": "SW",
            "fib_retracement": "FIB", "fib_extension": "FIB",
            "psychological": "PSY", "volume_node": "VOL",
            "resistance": "R", "support": "S",
        }
        
        def get_type_abbr(level_type: str) -> str:
            return type_map.get(level_type, level_type[:3].upper() if level_type else "?")
        
        # 阻力位按价格降序排列
        resistance = sorted(resistance, key=lambda x: -x.get("price", 0))[:10]
        # 支撑位按价格降序排列
        support = sorted(support, key=lambda x: -x.get("price", 0))[:10]
        
        tf_str = f"（{' + '.join(timeframes)}）" if timeframes else ""
        text = f"📍 <b>{symbol} 关键价位</b>{tf_str}\n\n当前价: ${price:,.2f}\n\n"
        
        text += "<b>阻力位:</b>\n"
        if resistance:
            for i, r in enumerate(resistance):
                r_price = r.get("price", 0)
                strength = r.get("strength", 0)
                level_type = get_type_abbr(r.get("type", ""))
                pct = ((r_price - price) / price * 100) if price > 0 else 0
                text += f"├ R{i+1}: ${r_price:,.2f} (+{pct:.1f}%) [{level_type}] 💪{strength:.0f}\n"
        else:
            text += "├ 无阻力位数据\n"
        
        text += "\n<b>支撑位:</b>\n"
        if support:
            for i, s in enumerate(support):
                s_price = s.get("price", 0)
                strength = s.get("strength", 0)
                level_type = get_type_abbr(s.get("type", ""))
                pct = ((price - s_price) / price * 100) if price > 0 else 0
                text += f"├ S{i+1}: ${s_price:,.2f} (-{pct:.1f}%) [{level_type}] 💪{strength:.0f}\n"
        else:
            text += "├ 无支撑位数据\n"
        
        from datetime import datetime
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        text += f"\n<i>类型: SW=摆动点 FIB=斐波那契 PSY=心理关口 VOL=成交密集区</i>\n\n🕐 {timestamp}"
        
        return text
    
    async def _cmd_stop(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """处理 /stop 命令"""
        # 权限检查
        user_id = update.effective_user.id
        if self.config.admin_user_ids and user_id not in self.config.admin_user_ids:
            await update.message.reply_text("❌ 权限不足")
            return
        
        if self.strategy:
            asyncio.create_task(self.strategy.stop())
            await update.message.reply_text("🛑 正在停止策略...")
        else:
            await update.message.reply_text("❌ 策略未连接")
    
    async def _cmd_close_all(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """处理 /closeall 命令"""
        # 权限检查
        user_id = update.effective_user.id
        if self.config.admin_user_ids and user_id not in self.config.admin_user_ids:
            await update.message.reply_text("❌ 权限不足")
            return
        
        if not self.strategy:
            await update.message.reply_text("❌ 策略未连接")
            return
        
        if not self.strategy.position_manager.state:
            await update.message.reply_text("📭 当前无持仓")
            return
        
        # 确认对话框
        keyboard = [
            [
                InlineKeyboardButton("✅ 确认平仓", callback_data="closeall_confirm"),
                InlineKeyboardButton("❌ 取消", callback_data="closeall_cancel"),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "⚠️ 确认平掉所有仓位?",
            reply_markup=reply_markup
        )
    
    def set_callbacks(
        self,
        on_confirm: Optional[Callable] = None,
        on_reject: Optional[Callable] = None
    ) -> None:
        """设置回调函数"""
        self._on_confirm = on_confirm
        self._on_reject = on_reject
    
    async def _handle_menu_button(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """处理菜单按钮点击"""
        text = update.message.text
        user_id = update.effective_user.id
        self.logger.info(f"收到菜单按钮: {text}")
        self._mark_alive()
        
        # 初始化用户状态存储
        if not hasattr(self, "_user_states"):
            self._user_states = {}
        
        # 菜单按钮列表（点击这些按钮时清除等待状态）
        menu_buttons = [
            "📊 当前持仓", "📋 当前挂单", "🔄 更新网格", 
            "📍 关键价位", "🔍 查询价位", "📈 市场指标", "❓ 帮助"
        ]
        
        try:
            # 如果点击了菜单按钮，清除等待状态
            if text in menu_buttons:
                if user_id in self._user_states:
                    del self._user_states[user_id]
            
            # 处理菜单按钮
            if text == "📊 当前持仓":
                await self._cmd_position(update, context)
            elif text == "📋 当前挂单":
                await self._cmd_orders(update, context)
            elif text == "🔄 更新网格":
                await self._cmd_rebuild(update, context)
            elif text == "📍 关键价位":
                await self._cmd_levels(update, context)
            elif text == "🔍 查询价位":
                await self._prompt_levels_query(update, context)
            elif text == "📈 市场指标":
                await self._cmd_indicators(update, context)
            elif text == "❓ 帮助":
                await self._cmd_help(update, context)
            else:
                # 非菜单按钮消息，检查是否在等待输入
                if user_id in self._user_states and self._user_states[user_id].get("waiting_for") == "levels_query":
                    await self._handle_levels_query_input(update, context, text)
                else:
                    self.logger.debug(f"忽略未知消息: {text}")
        except Exception as e:
            self.logger.error(f"处理菜单按钮异常: {e}", exc_info=True)
            try:
                await update.message.reply_text(f"❌ 操作失败: {e}")
            except Exception:
                pass
    
    async def _prompt_levels_query(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """提示用户输入标的和周期"""
        user_id = update.effective_user.id
        
        # 初始化用户状态存储
        if not hasattr(self, "_user_states"):
            self._user_states = {}
        
        # 设置等待状态
        self._user_states[user_id] = {
            "waiting_for": "levels_query",
            "timestamp": __import__("time").time(),
        }
        
        text = """
🔍 <b>查询任意标的的支撑/阻力位</b>

请输入 <b>标的代码</b> 和 <b>周期</b>：

<b>格式:</b> <code>标的 周期1 [周期2] [周期3]</code>

<b>示例:</b>
• <code>TSLA 4h 1d</code> - 美股特斯拉
• <code>AAPL 1d</code> - 美股苹果
• <code>BTCUSDT 4h 1d</code> - 币圈比特币
• <code>ETHUSDT 15m 4h 1d</code> - 币圈以太坊

<b>支持周期:</b> 15m, 1h, 4h, 1d, 1w

<i>输入 "取消" 返回菜单</i>
"""
        await update.message.reply_text(text, parse_mode="HTML")
    
    async def _handle_levels_query_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
        """处理用户输入的标的和周期"""
        user_id = update.effective_user.id
        
        # 清除等待状态
        if hasattr(self, "_user_states") and user_id in self._user_states:
            del self._user_states[user_id]
        
        # 检查是否取消
        if text.lower() in ["取消", "cancel", "q", "quit"]:
            await update.message.reply_text(
                "✅ 已取消查询",
                reply_markup=self._get_main_menu()
            )
            return
        
        # 解析输入（支持空格或逗号分隔）
        # 先将逗号替换为空格，再分割
        normalized = text.replace(",", " ").replace("，", " ")  # 支持中英文逗号
        parts = [p.strip() for p in normalized.split() if p.strip()]
        
        if len(parts) < 2:
            await update.message.reply_text(
                "❌ 格式错误，请输入：<code>标的 周期</code>\n"
                "例如：<code>TSLA 4h 1d</code> 或 <code>BTCUSDT 5m, 15m</code>",
                parse_mode="HTML",
                reply_markup=self._get_main_menu()
            )
            return
        
        # 调用现有的查询逻辑
        args = parts  # [symbol, tf1, tf2, ...]
        await self._query_external_levels(update, args)
    
    async def _cmd_orders(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """处理 /orders 命令 - 查看当前挂单"""
        if not self.strategy:
            await update.message.reply_text("❌ 策略未连接")
            return
        
        data = self.strategy.get_display_data()
        pending_orders = data.get("pending_orders", [])
        
        if not pending_orders:
            await update.message.reply_text("📭 当前无挂单")
            return
        
        # 获取当前价格
        price_obj = data.get("price", {})
        current_price = price_obj.get("current", 0) if isinstance(price_obj, dict) else 0
        
        # 分类买单和卖单
        buy_orders = [o for o in pending_orders if o.get("side") == "buy"]
        sell_orders = [o for o in pending_orders if o.get("side") == "sell"]
        
        text = f"📋 <b>当前挂单</b>\n\n当前价格: ${current_price:,.2f}\n"

        # 卖单在上，按价格降序（显示全部）
        if sell_orders:
            total_sell = sum(o.get("amount", 0) for o in sell_orders)
            text += f"\n🔴 <b>卖单</b> ({len(sell_orders)}个, 共 {total_sell:,.0f} USDT)\n"
            sell_orders_sorted = sorted(sell_orders, key=lambda x: -x.get("price", 0))
            for i, order in enumerate(sell_orders_sorted, 1):
                price = order.get("price", 0)
                amount = order.get("amount", 0)
                pct = (price - current_price) / current_price * 100 if current_price > 0 else 0
                prefix = "└" if i == len(sell_orders_sorted) else "├"
                text += f"{prefix} ${price:,.2f} | {amount:,.0f}U | {pct:+.1f}%\n"

        # 买单在下，按价格降序（显示全部）
        if buy_orders:
            total_buy = sum(o.get("amount", 0) for o in buy_orders)
            text += f"\n🟢 <b>买单</b> ({len(buy_orders)}个, 共 {total_buy:,.0f} USDT)\n"
            buy_orders_sorted = sorted(buy_orders, key=lambda x: -x.get("price", 0))
            for i, order in enumerate(buy_orders_sorted, 1):
                price = order.get("price", 0)
                amount = order.get("amount", 0)
                pct = (price - current_price) / current_price * 100 if current_price > 0 else 0
                prefix = "└" if i == len(buy_orders_sorted) else "├"
                text += f"{prefix} ${price:,.2f} | {amount:,.0f}U | {pct:+.1f}%\n"
        
        from datetime import datetime
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        text += f"\n🕐 {timestamp}"
        
        await update.message.reply_text(text, parse_mode="HTML")
    
    async def _cmd_rebuild(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """处理 /rebuild 命令 - 强制更新网格"""
        # 权限检查
        user_id = update.effective_user.id
        if self.config.admin_user_ids and user_id not in self.config.admin_user_ids:
            await update.message.reply_text("❌ 权限不足")
            return
        
        if not self.strategy:
            await update.message.reply_text("❌ 策略未连接")
            return
        
        # 确认对话框
        keyboard = [
            [
                InlineKeyboardButton("✅ 确认更新", callback_data="rebuild_confirm"),
                InlineKeyboardButton("❌ 取消", callback_data="rebuild_cancel"),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "🔄 <b>确认更新网格?</b>\n\n"
            "此操作将:\n"
            "1. 撤销所有现有挂单\n"
            "2. 重新计算支撑/阻力位\n"
            "3. 根据新价位重新挂单\n\n"
            "⚠️ 已成交的仓位不会受影响",
            parse_mode="HTML",
            reply_markup=reply_markup
        )

