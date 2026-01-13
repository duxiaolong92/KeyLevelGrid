"""
Telegram 命令处理器

处理用户命令并返回响应
不再依赖 EMA 通道指标
"""

from typing import Any, Dict, Optional, TYPE_CHECKING

from key_level_grid.utils.logger import get_logger

if TYPE_CHECKING:
    from key_level_grid.strategy import KeyLevelGridStrategy


class CommandHandler:
    """
    命令处理器
    
    处理 Telegram 命令并生成响应
    """
    
    def __init__(self, strategy: Optional["KeyLevelGridStrategy"] = None):
        self.strategy = strategy
        self.logger = get_logger(__name__)
    
    def set_strategy(self, strategy: "KeyLevelGridStrategy") -> None:
        """设置策略引用"""
        self.strategy = strategy
    
    def handle_status(self) -> str:
        """处理 /status 命令"""
        if not self.strategy:
            return "❌ 策略未连接"
        
        status = self.strategy.get_status()
        
        running = "🟢 运行中" if status.get("running") else "🔴 已停止"
        symbol = status.get("symbol", "N/A")
        price = status.get("current_price", 0)
        
        # 使用市场指标
        indicators = status.get("indicators", {})
        adx = indicators.get("adx", 0)
        rsi = indicators.get("rsi", 0)
        
        trend = "无趋势"
        if adx and adx > 25:
            trend = "有趋势"
        
        return f"""
📊 <b>策略状态</b>

├ 状态: {running}
├ 交易对: {symbol}
├ 当前价格: {price:.4f if price else 'N/A'}
├ ADX: {adx:.1f if adx else 'N/A'} ({trend})
└ RSI: {rsi:.1f if rsi else 'N/A'}
"""
    
    def handle_position(self) -> str:
        """处理 /position 命令"""
        if not self.strategy:
            return "❌ 策略未连接"
        
        status = self.strategy.get_status()
        position = status.get("position", {})
        
        if not position.get("has_position"):
            return "📭 当前无持仓"
        
        direction = position.get("direction", "none")
        pnl = position.get("unrealized_pnl", 0)
        
        return f"""
💼 <b>当前持仓</b>

├ 方向: {'🟢' if direction == 'long' else '🔴'} {direction.upper()}
├ 入场价: {position.get('entry_price', 0):.4f}
├ 仓位: {position.get('position_usdt', 0):.2f} USDT
├ 未实现盈亏: {'📈' if pnl >= 0 else '📉'} {pnl:.2f} USDT
├ R倍数: {position.get('risk_reward', 0):.2f}R
└ 止损价: {position.get('stop_loss', 0):.4f}
"""
    
    def handle_indicators(self) -> str:
        """处理 /indicators 命令 (替代原来的 /tunnel)"""
        if not self.strategy:
            return "❌ 策略未连接"
        
        data = self.strategy.get_display_data()
        indicators = data.get("indicators", {})
        
        if not indicators:
            return "❌ 无指标数据"
        
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
        
        return f"""
📈 <b>市场指标</b>

├ MACD: {macd:.4f if macd else 'N/A'}
├ MACD柱: {macd_hist:.4f if macd_hist else 'N/A'}
├ RSI: {rsi:.1f if rsi else 'N/A'} ({rsi_status})
├ ADX: {adx:.1f if adx else 'N/A'} ({trend})
├ ATR: {atr:.4f if atr else 'N/A'}
└ 量比: {volume_ratio:.2f if volume_ratio else 'N/A'}x
"""
    
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
    
    def handle_levels(self) -> str:
        """处理 /levels 命令 - 显示关键价位"""
        if not self.strategy:
            return "❌ 策略未连接"
        
        data = self.strategy.get_display_data()
        price = data.get("price", {}).get("current", 0)
        resistance = data.get("resistance_levels", [])[:5]
        support = data.get("support_levels", [])[:5]
        
        text = f"📍 <b>关键价位</b>\n\n当前价: {price:.4f}\n\n"
        
        text += "<b>阻力位:</b>\n"
        for i, r in enumerate(resistance):
            r_price = r.get("price", 0)
            pct = ((r_price - price) / price * 100) if price > 0 else 0
            source = self._format_source(r.get("source", ""))
            tf = self._format_timeframe(r.get("timeframe", ""))
            strength = r.get("strength", 0)
            text += f"├ R{i+1}: {r_price:.4f} (+{pct:.1f}%) [{source}] {tf} 💪{strength:.0f}\n"
        
        text += "\n<b>支撑位:</b>\n"
        for i, s in enumerate(support):
            s_price = s.get("price", 0)
            pct = ((price - s_price) / price * 100) if price > 0 else 0
            source = self._format_source(s.get("source", ""))
            tf = self._format_timeframe(s.get("timeframe", ""))
            strength = s.get("strength", 0)
            text += f"├ S{i+1}: {s_price:.4f} (-{pct:.1f}%) [{source}] {tf} 💪{strength:.0f}\n"
        
        return text
    
    def handle_orders(self) -> str:
        """处理 /orders 命令"""
        if not self.strategy:
            return "❌ 策略未连接"
        
        # 当前简化实现
        status = self.strategy.get_status()
        pending = status.get("pending_signal")
        
        if pending:
            return f"""
📋 <b>待处理信号</b>

├ 类型: {pending.get('signal_type', 'N/A')}
├ 入场价: {pending.get('entry_price', 0):.4f}
└ 评分: {pending.get('score', 0)}/100

等待确认中...
"""
        else:
            return "📭 无待处理订单"
    
    def handle_account(self) -> str:
        """处理 /account 命令"""
        if not self.strategy:
            return "❌ 策略未连接"
        
        config = self.strategy.config.position_config
        
        return f"""
💰 <b>账户配置</b>

├ 总资金: {config.total_capital:.2f} USDT
├ 最大风险: {config.max_risk_usdt:.2f} USDT
├ 单笔风险: {config.risk_per_trade:.1%}
└ 最大杠杆: {config.max_leverage}x
"""
    
    def handle_stats(self) -> str:
        """处理 /stats 命令"""
        if not self.strategy:
            return "❌ 策略未连接"
        
        # 简化实现，可扩展为历史统计
        return """
📈 <b>交易统计</b>

功能开发中...
"""
    
    def get_help_text(self) -> str:
        """获取帮助文本"""
        return """
📚 <b>Key Level Grid Bot 帮助</b>

<b>查询命令:</b>
/status - 策略运行状态
/position - 当前持仓信息
/indicators - 市场指标
/levels - 关键价位
/orders - 待处理订单
/account - 账户配置
/stats - 交易统计

<b>控制命令:</b>
/stop - 停止策略
/closeall - 平掉所有仓位

<b>信号交互:</b>
收到信号后，点击确认/拒绝按钮
"""
