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
    from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
    from telegram.ext import (
        Application,
        CommandHandler as TGCommandHandler,
        CallbackQueryHandler,
        ContextTypes,
    )
    TELEGRAM_AVAILABLE = True
except ImportError:
    TELEGRAM_AVAILABLE = False
    Update = None
    InlineKeyboardButton = None
    InlineKeyboardMarkup = None


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
        self.app.add_handler(TGCommandHandler("indicators", self._cmd_indicators))
        self.app.add_handler(TGCommandHandler("levels", self._cmd_levels))
        self.app.add_handler(TGCommandHandler("stop", self._cmd_stop))
        self.app.add_handler(TGCommandHandler("closeall", self._cmd_close_all))
        
        # 注册回调处理器 (按钮点击)
        self.app.add_handler(CallbackQueryHandler(self._handle_callback))
        
        # 启动 Bot
        await self.app.initialize()
        await self.app.start()
        await self.app.updater.start_polling()
        
        self.logger.info("Telegram Bot 已启动")
    
    async def stop(self) -> None:
        """停止 Bot"""
        if self.app:
            await self.app.updater.stop()
            await self.app.stop()
            await self.app.shutdown()
        
        self.logger.info("Telegram Bot 已停止")
    
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
        text = """
🎰 <b>Key Level Grid Strategy Bot</b>

关键位网格交易策略机器人

<b>可用命令:</b>
/status - 查看策略状态
/position - 查看当前持仓
/indicators - 查看市场指标
/levels - 查看关键价位
/help - 帮助信息
"""
        await update.message.reply_text(text, parse_mode="HTML")
    
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
        
        status = self.strategy.get_status()
        position = status.get("position", {})
        
        if not position.get("has_position"):
            await update.message.reply_text("📭 当前无持仓")
            return
        
        direction = position.get("direction", "none")
        dir_emoji = "🟢" if direction == "long" else "🔴"
        
        pnl = position.get("unrealized_pnl", 0)
        pnl_emoji = "📈" if pnl >= 0 else "📉"
        
        text = f"""
💼 <b>当前持仓</b>

├ 方向: {dir_emoji} {direction.upper()}
├ 入场价: {position.get('entry_price', 0):.4f}
├ 当前价格: {position.get('current_price', 0):.4f}
├ 仓位: {position.get('position_usdt', 0):.2f} USDT
├ 未实现盈亏: {pnl_emoji} {pnl:.2f} USDT
├ R倍数: {position.get('risk_reward', 0):.2f}R
├ 止损价: {position.get('stop_loss', 0):.4f}
└ 止损类型: {position.get('stop_type', 'N/A')}
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
        resistance = data.get("resistance_levels", [])[:5]
        support = data.get("support_levels", [])[:5]
        
        text = f"📍 <b>关键价位</b>\n\n当前价: {price:.4f}\n\n"
        
        text += "<b>阻力位:</b>\n"
        for i, r in enumerate(resistance):
            r_price = r.get("price", 0)
            pct = ((r_price - price) / price * 100) if price > 0 else 0
            text += f"├ R{i+1}: {r_price:.4f} (+{pct:.1f}%)\n"
        
        text += "\n<b>支撑位:</b>\n"
        for i, s in enumerate(support):
            s_price = s.get("price", 0)
            pct = ((price - s_price) / price * 100) if price > 0 else 0
            text += f"├ S{i+1}: {s_price:.4f} (-{pct:.1f}%)\n"
        
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

