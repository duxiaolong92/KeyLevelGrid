"""
信号过滤链

采用责任链模式，依次检查各项过滤条件
"""

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional

from key_level_grid.utils.logger import get_logger
from key_level_grid.core.models import Kline
from key_level_grid.core.config import FilterConfig
from key_level_grid.signal.generator import KeyLevelSignal
from key_level_grid.core.types import SignalType


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
        """过滤信号"""
        pass


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
        """检查是否在冷却期内"""
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


class ConfirmationFilter(SignalFilter):
    """确认过滤器"""
    
    name = "ConfirmationFilter"
    
    def filter(
        self,
        signal: KeyLevelSignal,
        klines: List[Kline],
        config: FilterConfig
    ) -> FilterResult:
        """检查是否有K线确认"""
        if len(klines) < 1:
            return FilterResult(False, self.name, "K线数据不足")
        
        latest = klines[-1]
        
        if not latest.is_closed:
            return FilterResult(False, self.name, "等待K线收盘确认")
        
        is_long = signal.signal_type in [SignalType.BREAKOUT_LONG, SignalType.PULLBACK_LONG]
        
        if signal.signal_type in [SignalType.BREAKOUT_LONG, SignalType.BREAKOUT_SHORT]:
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


class TimeFilter(SignalFilter):
    """交易时间过滤器"""
    
    name = "TimeFilter"
    
    def filter(
        self,
        signal: KeyLevelSignal,
        klines: List[Kline],
        config: FilterConfig
    ) -> FilterResult:
        """检查是否在交易时间内"""
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
            ConfirmationFilter(),
            self._cooldown_filter,
            TimeFilter(),
        ]
    
    def filter(
        self,
        signal: KeyLevelSignal,
        klines: List[Kline]
    ) -> Optional[KeyLevelSignal]:
        """执行过滤链"""
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
                
                signal.filters_passed = passed_filters
                signal.filters_failed = failed_filters
                return None
        
        signal.filters_passed = passed_filters
        signal.filters_failed = failed_filters
        
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
