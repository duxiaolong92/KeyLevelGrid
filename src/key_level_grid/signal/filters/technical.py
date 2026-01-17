"""
技术指标过滤器

基于 MACD, RSI, ADX, 成交量等指标的过滤器
"""

from typing import List

from key_level_grid.core.models import Kline
from key_level_grid.core.types import SignalType
from key_level_grid.core.config import FilterConfig
from key_level_grid.signal.generator import KeyLevelSignal
from key_level_grid.signal.filters.chain import SignalFilter, FilterResult


class MACDTrendFilter(SignalFilter):
    """MACD趋势过滤器"""
    
    name = "MACDTrendFilter"
    
    def filter(
        self,
        signal: KeyLevelSignal,
        klines: List[Kline],
        config: FilterConfig
    ) -> FilterResult:
        """检查 MACD 方向与信号方向是否一致"""
        if not config.macd_trend_enabled:
            return FilterResult(True, self.name, "MACD趋势过滤已禁用")
        
        if signal.market_state is None:
            return FilterResult(True, self.name, "无市场状态，跳过")
        
        macd_hist = signal.market_state.macd_histogram
        if macd_hist is None:
            return FilterResult(True, self.name, "无MACD数据，跳过")
        
        is_long = signal.signal_type in [SignalType.BREAKOUT_LONG, SignalType.PULLBACK_LONG]
        
        if is_long and macd_hist > 0:
            return FilterResult(True, self.name, f"MACD 动能为正 ({macd_hist:.4f})")
        elif not is_long and macd_hist < 0:
            return FilterResult(True, self.name, f"MACD 动能为负 ({macd_hist:.4f})")
        elif abs(macd_hist) < 0.0001:
            return FilterResult(True, self.name, "MACD 接近零，允许通过")
        else:
            return FilterResult(
                False, self.name,
                f"MACD方向不一致: 信号={'多' if is_long else '空'}, MACD={macd_hist:.4f}"
            )


class VolumeFilter(SignalFilter):
    """成交量确认过滤器"""
    
    name = "VolumeFilter"
    
    def filter(
        self,
        signal: KeyLevelSignal,
        klines: List[Kline],
        config: FilterConfig
    ) -> FilterResult:
        """检查成交量是否放大"""
        if not config.volume_confirmation:
            return FilterResult(True, self.name, "成交量过滤已禁用")
        
        if signal.market_state is None or signal.market_state.volume_ratio is None:
            return FilterResult(True, self.name, "无成交量数据，跳过")
        
        volume_ratio = signal.market_state.volume_ratio
        
        if volume_ratio >= config.volume_min_ratio:
            return FilterResult(
                True, self.name,
                f"成交量确认: {volume_ratio:.2f}x 均量"
            )
        else:
            return FilterResult(
                False, self.name,
                f"成交量不足: {volume_ratio:.2f}x < {config.volume_min_ratio}x"
            )


class RSIFilter(SignalFilter):
    """RSI 超买超卖过滤器"""
    
    name = "RSIFilter"
    
    def filter(
        self,
        signal: KeyLevelSignal,
        klines: List[Kline],
        config: FilterConfig
    ) -> FilterResult:
        """检查 RSI 是否在合理范围"""
        if not config.rsi_enabled:
            return FilterResult(True, self.name, "RSI过滤已禁用")
        
        if signal.market_state is None or signal.market_state.rsi is None:
            return FilterResult(True, self.name, "无RSI数据，跳过")
        
        rsi = signal.market_state.rsi
        is_long = signal.signal_type in [SignalType.BREAKOUT_LONG, SignalType.PULLBACK_LONG]
        
        if is_long and rsi > config.rsi_overbought:
            return FilterResult(
                False, self.name,
                f"超买区域不宜做多: RSI={rsi:.1f} > {config.rsi_overbought}"
            )
        elif not is_long and rsi < config.rsi_oversold:
            return FilterResult(
                False, self.name,
                f"超卖区域不宜做空: RSI={rsi:.1f} < {config.rsi_oversold}"
            )
        
        return FilterResult(
            True, self.name,
            f"RSI 正常: {rsi:.1f}"
        )


class ADXFilter(SignalFilter):
    """ADX 趋势强度过滤器"""
    
    name = "ADXFilter"
    
    def filter(
        self,
        signal: KeyLevelSignal,
        klines: List[Kline],
        config: FilterConfig
    ) -> FilterResult:
        """检查 ADX 是否表明存在趋势"""
        if not config.adx_enabled:
            return FilterResult(True, self.name, "ADX过滤已禁用")
        
        if signal.market_state is None or signal.market_state.adx is None:
            return FilterResult(True, self.name, "无ADX数据，跳过")
        
        adx = signal.market_state.adx
        
        if adx >= config.adx_min:
            return FilterResult(
                True, self.name,
                f"趋势强度足够: ADX={adx:.1f}"
            )
        else:
            return FilterResult(
                False, self.name,
                f"趋势强度不足: ADX={adx:.1f} < {config.adx_min}"
            )
