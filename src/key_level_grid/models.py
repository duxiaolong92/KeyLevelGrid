"""
关键位网格策略 - 数据模型

包含 K线、市场状态等核心数据结构
基于支撑/阻力位进行网格交易，不依赖 EMA 通道指标
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


class Timeframe(Enum):
    """
    K线周期
    
    支持的周期:
    - M1/M5/M15/M30: 分钟级
    - H1/H4: 小时级
    - D1: 日线
    - W1: 周线 (7天)
    """
    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    M30 = "30m"
    H1 = "1h"
    H4 = "4h"
    D1 = "1d"
    W1 = "1w"
    
    @classmethod
    def from_string(cls, value: str) -> "Timeframe":
        """
        从字符串解析周期
        
        支持多种格式:
        - "15m", "1h", "4h", "1d", "1w"
        - "15min", "1hour", "4hour", "1day", "7d", "7day"
        """
        value_lower = value.lower().strip()
        
        # 标准格式
        for tf in cls:
            if tf.value == value_lower:
                return tf
        
        # 别名映射
        aliases = {
            # 分钟
            "1min": cls.M1, "5min": cls.M5, "15min": cls.M15, "30min": cls.M30,
            # 小时
            "1hour": cls.H1, "4hour": cls.H4,
            # 天
            "1day": cls.D1, "daily": cls.D1,
            # 周
            "7d": cls.W1, "7day": cls.W1, "weekly": cls.W1,
        }
        if value_lower in aliases:
            return aliases[value_lower]
        
        raise ValueError(f"无效的周期: {value}，支持: 1m/5m/15m/30m/1h/4h/1d/1w")
    
    def to_milliseconds(self) -> int:
        """转换为毫秒"""
        mapping = {
            "1m": 60 * 1000,
            "5m": 5 * 60 * 1000,
            "15m": 15 * 60 * 1000,
            "30m": 30 * 60 * 1000,
            "1h": 60 * 60 * 1000,
            "4h": 4 * 60 * 60 * 1000,
            "1d": 24 * 60 * 60 * 1000,
            "1w": 7 * 24 * 60 * 60 * 1000,
        }
        return mapping[self.value]
    
    def to_display_name(self) -> str:
        """转换为中文显示名称"""
        mapping = {
            "1m": "1分钟",
            "5m": "5分钟",
            "15m": "15分钟",
            "30m": "30分钟",
            "1h": "1小时",
            "4h": "4小时",
            "1d": "日线",
            "1w": "周线",
        }
        return mapping.get(self.value, self.value)


@dataclass
class Kline:
    """K线数据"""
    timestamp: int          # 开盘时间 (毫秒)
    open: float
    high: float
    low: float
    close: float
    volume: float           # 成交量 (币)
    quote_volume: float     # 成交额 (USDT)
    trades: int             # 成交笔数
    is_closed: bool         # 是否已收盘
    
    @property
    def body(self) -> float:
        """K线实体"""
        return abs(self.close - self.open)
    
    @property
    def upper_wick(self) -> float:
        """上影线"""
        return self.high - max(self.open, self.close)
    
    @property
    def lower_wick(self) -> float:
        """下影线"""
        return min(self.open, self.close) - self.low
    
    @property
    def total_range(self) -> float:
        """总振幅"""
        return self.high - self.low
    
    @property
    def is_bullish(self) -> bool:
        """是否阳线"""
        return self.close > self.open
    
    @property
    def is_bearish(self) -> bool:
        """是否阴线"""
        return self.close < self.open
    
    @property
    def is_doji(self) -> bool:
        """是否十字星 (实体 < 10% 振幅)"""
        if self.total_range == 0:
            return True
        return self.body / self.total_range < 0.1
    
    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "timestamp": self.timestamp,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "quote_volume": self.quote_volume,
            "trades": self.trades,
            "is_closed": self.is_closed,
        }


@dataclass
class KlineFeedConfig:
    """
    K线数据源配置
    
    支持的周期: 15m, 1h, 4h, 1d, 1w (7d)
    
    示例:
        # 主周期 4H，辅助周期 1D
        config = KlineFeedConfig(
            symbol="BNBUSDT",
            primary_timeframe=Timeframe.H4,
            auxiliary_timeframes=[Timeframe.D1]
        )
        
        # 多周期分析: 主周期 1H，辅助周期 4H + 1D
        config = KlineFeedConfig(
            symbol="BTCUSDT",
            primary_timeframe=Timeframe.H1,
            auxiliary_timeframes=[Timeframe.H4, Timeframe.D1]
        )
        
        # 从字符串创建
        config = KlineFeedConfig.from_strings(
            symbol="ETHUSDT",
            primary="4h",
            auxiliary=["1d", "1w"]
        )
    """
    symbol: str = "BTCUSDT"
    primary_timeframe: Timeframe = field(default=Timeframe.H4)
    auxiliary_timeframes: List[Timeframe] = field(
        default_factory=lambda: [Timeframe.D1]
    )
    history_bars: int = 500                          # 历史K线数量
    update_interval_sec: int = 5                     # 更新间隔 (秒)
    
    # 健壮性配置
    max_retries: int = 3                             # 最大重试次数
    retry_base_delay_sec: float = 1.0                # 重试基础延迟
    ws_reconnect_delay_sec: float = 5.0              # WS 重连延迟
    request_timeout_sec: float = 10.0                # 请求超时
    
    @classmethod
    def from_strings(
        cls,
        symbol: str,
        primary: str = "4h",
        auxiliary: List[str] = None,
        **kwargs
    ) -> "KlineFeedConfig":
        """
        从字符串创建配置
        
        Args:
            symbol: 交易对
            primary: 主周期字符串 (如 "4h", "1h", "15m")
            auxiliary: 辅助周期列表 (如 ["1d", "1w"])
            **kwargs: 其他配置参数
            
        Returns:
            KlineFeedConfig 实例
        """
        primary_tf = Timeframe.from_string(primary)
        auxiliary_tfs = [Timeframe.from_string(tf) for tf in (auxiliary or ["1d"])]
        
        return cls(
            symbol=symbol,
            primary_timeframe=primary_tf,
            auxiliary_timeframes=auxiliary_tfs,
            **kwargs
        )
    
    def get_all_timeframes(self) -> List[Timeframe]:
        """获取所有需要订阅的周期"""
        return [self.primary_timeframe] + self.auxiliary_timeframes
    
    def get_timeframe_display(self) -> str:
        """获取周期显示文本"""
        primary = self.primary_timeframe.to_display_name()
        if self.auxiliary_timeframes:
            aux = " + ".join(tf.to_display_name() for tf in self.auxiliary_timeframes)
            return f"{primary} (辅助: {aux})"
        return primary


@dataclass
class KeyLevelGridState:
    """
    关键位网格状态
    
    基于支撑/阻力位的网格交易，不依赖 EMA 通道指标
    """
    timestamp: int              # 时间戳
    symbol: str                 # 交易对
    
    # 价格数据
    open: float
    high: float
    low: float
    close: float
    volume: float
    
    # 技术指标 (辅助判断，非核心)
    macd: Optional[float] = None
    macd_signal: Optional[float] = None
    macd_histogram: Optional[float] = None
    rsi: Optional[float] = None
    atr: Optional[float] = None          # 平均真实波幅
    adx: Optional[float] = None          # 趋势强度 (可选)
    
    # 成交量相关
    volume_ma: Optional[float] = None    # 均量
    volume_ratio: Optional[float] = None # 量比
    
    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "timestamp": self.timestamp,
            "symbol": self.symbol,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "macd": self.macd,
            "macd_signal": self.macd_signal,
            "macd_histogram": self.macd_histogram,
            "rsi": self.rsi,
            "atr": self.atr,
            "adx": self.adx,
            "volume_ma": self.volume_ma,
            "volume_ratio": self.volume_ratio,
        }


@dataclass
class TimeframeTrend:
    """
    单周期趋势状态
    
    基于价格与支撑/阻力位的关系判断趋势，不依赖 EMA
    """
    timeframe: Timeframe
    trend: str                    # "up" | "down" | "ranging" | "unknown"
    nearest_support: float = 0.0  # 最近支撑位
    nearest_resistance: float = 0.0  # 最近阻力位
    price_position: str = "middle"  # "near_support" | "near_resistance" | "middle"
    confidence: float = 0.0       # 趋势置信度 0-100
    
    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "timeframe": self.timeframe.value,
            "trend": self.trend,
            "nearest_support": self.nearest_support,
            "nearest_resistance": self.nearest_resistance,
            "price_position": self.price_position,
            "confidence": self.confidence,
        }

