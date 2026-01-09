"""
Telegram Bot 核心模块

使用 python-telegram-bot 库实现 Bot 功能
"""

import asyncio
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
            [KeyboardButton("📈 市场指标"), KeyboardButton("❓ 帮助")],
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
        
        data = query.data
        
        if data.startswith("confirm_"):
            signal_id = data.replace("confirm_", "")
            if signal_id in self._pending_confirmations:
                self._pending_confirmations[signal_id]["confirmed"] = True
                await self._confirm_signal(signal_id)
                await query.edit_message_text("✅ 已确认开仓")
                del self._pending_confirmations[signal_id]
        
        elif data.startswith("reject_"):
            signal_id = data.replace("reject_", "")
            if signal_id in self._pending_confirmations:
                self._pending_confirmations[signal_id]["rejected"] = True
                await self._reject_signal(signal_id)
                await query.edit_message_text("❌ 已拒绝信号")
                del self._pending_confirmations[signal_id]
        
        elif data.startswith("detail_"):
            signal_id = data.replace("detail_", "")
            if signal_id in self._pending_confirmations:
                signal_data = self._pending_confirmations[signal_id]["signal_data"]
                detail_text = self._format_signal_detail(signal_data)
                await query.message.reply_text(detail_text, parse_mode="HTML")
        
        elif data == "rebuild_confirm":
            await query.edit_message_text("🔄 正在更新网格...")
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
            await query.edit_message_text("❌ 已取消更新网格")
        
        elif data == "closeall_confirm":
            await query.edit_message_text("🔄 正在平仓...")
            if self.strategy:
                try:
                    # TODO: 实现平仓逻辑
                    await query.message.reply_text("⚠️ 平仓功能尚未实现")
                except Exception as e:
                    await query.message.reply_text(f"❌ 平仓失败: {e}")
        
        elif data == "closeall_cancel":
            await query.edit_message_text("❌ 已取消平仓")
    
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
        
        text = f"""
📊 <b>策略状态</b>

├ 状态: {running}
├ 交易对: {symbol}
├ 当前价格: {price:.4f if price else 'N/A'}
├ 趋势强度: {trend_emoji} ADX={adx:.1f if adx else 'N/A'} ({trend})
└ RSI: {rsi:.1f if rsi else 'N/A'}
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
        
        grid_floor = position.get("grid_floor", 0)
        
        text = f"""
💼 <b>当前持仓</b>

├ 方向: {dir_emoji} {direction.upper()}
├ 数量: {qty:.6f} BTC
├ 价值: {value:,.2f} USDT
├ 均价: ${entry_price:,.2f}
├ 当前价: ${current_price:,.2f}
├ 未实现盈亏: {pnl_emoji} {pnl:+,.2f} USDT ({pnl_pct:+.2%})
└ 网格底线: ${grid_floor:,.2f}
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
        
        text = f"""
📈 <b>市场指标</b>

├ MACD: {macd:.4f if macd else 'N/A'}
├ MACD柱: {macd_hist:.4f if macd_hist else 'N/A'}
├ RSI: {rsi:.1f if rsi else 'N/A'} ({rsi_status})
├ ADX: {adx:.1f if adx else 'N/A'} ({trend})
├ ATR: {atr:.4f if atr else 'N/A'}
└ 量比: {volume_ratio:.2f if volume_ratio else 'N/A'}x
"""
        await update.message.reply_text(text, parse_mode="HTML")
    
    async def _cmd_levels(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """处理 /levels 命令 - 显示关键价位"""
        if not self.strategy:
            await update.message.reply_text("❌ 策略未连接")
            return
        
        data = self.strategy.get_display_data()
        price = data.get("price", {}).get("current", 0)
        resistance = data.get("resistance_levels", [])
        support = data.get("support_levels", [])
        
        # 阻力位按价格降序排列（高价在前）
        resistance = sorted(resistance, key=lambda x: -x.get("price", 0))[:5]
        # 支撑位按价格降序排列（高价在前）
        support = sorted(support, key=lambda x: -x.get("price", 0))[:5]
        
        text = f"📍 <b>关键价位</b>\n\n当前价: ${price:,.2f}\n\n"
        
        text += "<b>阻力位:</b>\n"
        for i, r in enumerate(resistance):
            r_price = r.get("price", 0)
            pct = ((r_price - price) / price * 100) if price > 0 else 0
            text += f"├ R{i+1}: ${r_price:,.2f} (+{pct:.1f}%)\n"
        
        text += "\n<b>支撑位:</b>\n"
        for i, s in enumerate(support):
            s_price = s.get("price", 0)
            pct = ((price - s_price) / price * 100) if price > 0 else 0
            text += f"├ S{i+1}: ${s_price:,.2f} (-{pct:.1f}%)\n"
        
        await update.message.reply_text(text, parse_mode="HTML")
    
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
        self.logger.info(f"收到菜单按钮: {text}")
        
        if text == "📊 当前持仓":
            await self._cmd_position(update, context)
        elif text == "📋 当前挂单":
            await self._cmd_orders(update, context)
        elif text == "🔄 更新网格":
            await self._cmd_rebuild(update, context)
        elif text == "📍 关键价位":
            await self._cmd_levels(update, context)
        elif text == "📈 市场指标":
            await self._cmd_indicators(update, context)
        elif text == "❓ 帮助":
            await self._cmd_help(update, context)
        else:
            self.logger.debug(f"忽略未知消息: {text}")
    
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
        
        if buy_orders:
            total_buy = sum(o.get("amount", 0) for o in buy_orders)
            text += f"\n🟢 <b>买单</b> ({len(buy_orders)}个, 共 {total_buy:,.0f} USDT)\n"
            buy_orders_sorted = sorted(buy_orders, key=lambda x: -x.get("price", 0))
            for i, order in enumerate(buy_orders_sorted[:8], 1):
                price = order.get("price", 0)
                amount = order.get("amount", 0)
                pct = (price - current_price) / current_price * 100 if current_price > 0 else 0
                text += f"├ ${price:,.2f} | {amount:,.0f}U | {pct:+.1f}%\n"
            if len(buy_orders) > 8:
                text += f"└ ... 还有 {len(buy_orders) - 8} 个\n"
        
        if sell_orders:
            total_sell = sum(o.get("amount", 0) for o in sell_orders)
            text += f"\n🔴 <b>卖单</b> ({len(sell_orders)}个, 共 {total_sell:,.0f} USDT)\n"
            sell_orders_sorted = sorted(sell_orders, key=lambda x: x.get("price", 0))
            for i, order in enumerate(sell_orders_sorted[:8], 1):
                price = order.get("price", 0)
                amount = order.get("amount", 0)
                pct = (price - current_price) / current_price * 100 if current_price > 0 else 0
                text += f"├ ${price:,.2f} | {amount:,.0f}U | {pct:+.1f}%\n"
            if len(sell_orders) > 8:
                text += f"└ ... 还有 {len(sell_orders) - 8} 个\n"
        
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

