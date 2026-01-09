"""
信号过滤模块 (V2.3 简化版)

注意: V2.3 纯网格策略不需要这些过滤器
保留代码供后续版本使用

采用责任链模式，依次检查各项过滤条件
不再依赖 EMA 通道指标
"""

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional

from key_level_grid.utils.logger import get_logger
from key_level_grid.models import Kline, KeyLevelGridState
from key_level_grid.signal import KeyLevelSignal, SignalType


@dataclass
class FilterConfig:
    """过滤器配置"""
    # MACD趋势过滤
    macd_trend_enabled: bool = True
    
    # 成交量过滤
    volume_confirmation: bool = True
    volume_min_ratio: float = 1.0
    
    # RSI 过滤
    rsi_enabled: bool = True
    rsi_overbought: float = 70.0      # 超买阈值
    rsi_oversold: float = 30.0        # 超卖阈值
    
    # ADX 过滤 (趋势强度)
    adx_enabled: bool = True
    adx_min: float = 20.0             # 最小趋势强度
    
    # 冷却过滤
    cooldown_hours: int = 8
    
    # 时间过滤
    time_filter_enabled: bool = False
    trading_hours: List[int] = None   # 允许交易的小时 (UTC)
    
    # 多周期过滤
    mtf_enabled: bool = True
    mtf_min_confidence: float = 40.0


@dataclass
class FilterResult:
    """过滤结果"""
    passed: bool
    filter_name: str
    reason: str = ""
    
    def __bool__(self) -> bool:
        return self.passed


class SignalFilter(ABC):
    """信号过滤器基类"""
    
    name: str = "BaseFilter"
    
    @abstractmethod
    def filter(
        self,
        signal: KeyLevelSignal,
        klines: List[Kline],
        config: FilterConfig
    ) -> FilterResult:
        """
        过滤信号
        
        Args:
            signal: 待过滤的信号
            klines: K线数据
            config: 过滤配置
            
        Returns:
            FilterResult
        """
        pass


class MACDTrendFilter(SignalFilter):
    """MACD趋势过滤器"""
    
    name = "MACDTrendFilter"
    
    def filter(
        self,
        signal: KeyLevelSignal,
        klines: List[Kline],
        config: FilterConfig
    ) -> FilterResult:
        """
        检查 MACD 方向与信号方向是否一致
        
        做多信号要求 MACD 柱状图为正或上升
        做空信号要求 MACD 柱状图为负或下降
        """
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
        elif abs(macd_hist) < 0.0001:  # MACD 接近零
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
        """
        检查成交量是否放大
        
        突破时成交量应 > volume_min_ratio 倍均量
        """
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
        """
        检查 RSI 是否在合理范围
        
        做多信号不应在超买区
        做空信号不应在超卖区
        """
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
        """
        检查 ADX 是否表明存在趋势
        
        ADX < 20: 无趋势，不适合趋势交易
        ADX >= 20: 有趋势
        """
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


class ConfirmationFilter(SignalFilter):
    """确认过滤器"""
    
    name = "ConfirmationFilter"
    
    def filter(
        self,
        signal: KeyLevelSignal,
        klines: List[Kline],
        config: FilterConfig
    ) -> FilterResult:
        """
        检查是否有K线确认
        
        突破信号: 需要收盘确认
        回踩信号: 需要反弹/回落K线确认
        """
        if len(klines) < 1:
            return FilterResult(False, self.name, "K线数据不足")
        
        latest = klines[-1]
        
        # 检查是否已收盘
        if not latest.is_closed:
            return FilterResult(False, self.name, "等待K线收盘确认")
        
        is_long = signal.signal_type in [SignalType.BREAKOUT_LONG, SignalType.PULLBACK_LONG]
        
        # 突破信号检查
        if signal.signal_type in [SignalType.BREAKOUT_LONG, SignalType.BREAKOUT_SHORT]:
            # 检查影线比例
            if latest.total_range > 0:
                wick_ratio = (
                    latest.upper_wick if is_long else latest.lower_wick
                ) / latest.total_range
                
                if wick_ratio > 0.5:
                    return FilterResult(
                        False, self.name,
                        f"影线过长: {wick_ratio:.1%}"
                    )
        
        return FilterResult(True, self.name, "确认通过")


class CooldownFilter(SignalFilter):
    """冷却期过滤器"""
    
    name = "CooldownFilter"
    
    def __init__(self):
        self._last_signal_times: dict = {}
    
    def filter(
        self,
        signal: KeyLevelSignal,
        klines: List[Kline],
        config: FilterConfig
    ) -> FilterResult:
        """
        检查是否在冷却期内
        
        同一类型信号需要间隔 cooldown_hours 小时
        """
        signal_type = signal.signal_type.value
        last_time = self._last_signal_times.get(signal_type, 0)
        
        if last_time == 0:
            return FilterResult(True, self.name, "无历史信号")
        
        cooldown_ms = config.cooldown_hours * 60 * 60 * 1000
        elapsed = int(time.time() * 1000) - last_time
        
        if elapsed >= cooldown_ms:
            return FilterResult(True, self.name, "冷却期已过")
        else:
            remaining_hours = (cooldown_ms - elapsed) / (60 * 60 * 1000)
            return FilterResult(
                False, self.name,
                f"冷却期内: 剩余 {remaining_hours:.1f} 小时"
            )
    
    def record_signal(self, signal: KeyLevelSignal) -> None:
        """记录信号时间"""
        self._last_signal_times[signal.signal_type.value] = signal.timestamp


class TimeFilter(SignalFilter):
    """交易时间过滤器"""
    
    name = "TimeFilter"
    
    def filter(
        self,
        signal: KeyLevelSignal,
        klines: List[Kline],
        config: FilterConfig
    ) -> FilterResult:
        """
        检查是否在交易时间内
        
        可配置允许交易的小时
        """
        if not config.time_filter_enabled:
            return FilterResult(True, self.name, "时间过滤已禁用")
        
        if config.trading_hours is None:
            return FilterResult(True, self.name, "无交易时间限制")
        
        from datetime import datetime, timezone
        current_hour = datetime.now(timezone.utc).hour
        
        if current_hour in config.trading_hours:
            return FilterResult(True, self.name, f"当前时间 {current_hour}:00 UTC 允许交易")
        else:
            return FilterResult(
                False, self.name,
                f"非交易时间: {current_hour}:00 UTC"
            )


class SignalFilterChain:
    """
    信号过滤链
    
    按顺序执行所有过滤器，任一失败则拒绝信号
    """
    
    def __init__(self, config: Optional[FilterConfig] = None):
        self.config = config or FilterConfig()
        self.logger = get_logger(__name__)
        
        # 初始化过滤器链
        self._cooldown_filter = CooldownFilter()
        self.filters: List[SignalFilter] = [
            MACDTrendFilter(),
            VolumeFilter(),
            RSIFilter(),
            ADXFilter(),
            ConfirmationFilter(),
            self._cooldown_filter,
            TimeFilter(),
        ]
    
    def filter(
        self,
        signal: KeyLevelSignal,
        klines: List[Kline]
    ) -> Optional[KeyLevelSignal]:
        """
        执行过滤链
        
        Args:
            signal: 待过滤信号
            klines: K线数据
            
        Returns:
            通过的信号 或 None (被过滤)
        """
        passed_filters = []
        failed_filters = []
        
        for f in self.filters:
            result = f.filter(signal, klines, self.config)
            
            if result.passed:
                passed_filters.append(f"{f.name}: {result.reason}")
            else:
                failed_filters.append(f"{f.name}: {result.reason}")
                
                self.logger.info(
                    f"信号被过滤: {signal.signal_type.value}, "
                    f"过滤器={f.name}, 原因={result.reason}"
                )
                
                # 更新信号状态
                signal.filters_passed = passed_filters
                signal.filters_failed = failed_filters
                return None
        
        # 所有过滤器通过
        signal.filters_passed = passed_filters
        signal.filters_failed = failed_filters
        
        # 记录信号用于冷却
        self._cooldown_filter.record_signal(signal)
        
        self.logger.info(
            f"信号通过所有过滤: {signal.signal_type.value}, "
            f"评分={signal.score}, 等级={signal.grade.value}"
        )
        
        return signal
    
    def add_filter(self, filter_: SignalFilter, index: int = -1) -> None:
        """添加过滤器"""
        if index < 0:
            self.filters.append(filter_)
        else:
            self.filters.insert(index, filter_)
    
    def remove_filter(self, filter_name: str) -> bool:
        """移除过滤器"""
        for i, f in enumerate(self.filters):
            if f.name == filter_name:
                self.filters.pop(i)
                return True
        return False
