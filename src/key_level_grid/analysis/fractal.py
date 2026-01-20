"""
分形点提取器 (LEVEL_GENERATION.md v3.2.5)

基于斐波那契周期提取多时间框架的分形高低点。

四层级系统:
- L1 战略层 (1w/3d): 回溯 [8, 21, 55] - 长期边界锚定
- L2 骨架层 (1d): 回溯 [13, 34, 55, 89] - 主网格定义
- L3 中继层 (4h): 回溯 [8, 21, 55] - 主交易执行层
- L4 战术层 (15m): 回溯 [34, 55, 144] - 种子池 (仅用于补全)

核心算法:
- 分形条件: 极值点左右各有 lookback 根 K 线低于/高于该点
"""

from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field
from key_level_grid.core.scoring import FractalPoint, calculate_base_score


# 默认斐波那契回溯周期 (通用)
DEFAULT_FIBONACCI_LOOKBACK = [8, 13, 21, 34, 55, 89]

# V3.2.5 四层级独立回溯周期配置
LAYER_FIBONACCI_LOOKBACK = {
    "l1": [8, 21, 55],           # 战略层: 1w/3d
    "l2": [13, 34, 55, 89],      # 骨架层: 1d
    "l3": [8, 21, 55],           # 中继层: 4h
    "l4": [34, 55, 144],         # 战术层: 15m
}

# 时间框架到层级的映射
TIMEFRAME_TO_LAYER = {
    "1w": "l1",
    "3d": "l1",
    "1d": "l2",
    "4h": "l3",
    "15m": "l4",
}


class FractalExtractor:
    """
    MTF 分形点提取器 (V3.2.5)
    
    从不同时间框架的 K 线数据中提取分形高低点，
    作为支撑/阻力位的候选。
    
    支持四层级独立回溯周期配置。
    """
    
    def __init__(
        self,
        fibonacci_lookback: Optional[List[int]] = None,
        config: Optional[Dict] = None,
        layer_lookbacks: Optional[Dict[str, List[int]]] = None,
    ):
        """
        初始化分形提取器
        
        Args:
            fibonacci_lookback: 通用斐波那契回溯周期列表 (向后兼容)
            config: 配置字典 (从 config.yaml 加载)
            layer_lookbacks: 四层级独立回溯周期配置
        """
        self.config = config or {}
        self.fibonacci_lookback = fibonacci_lookback or DEFAULT_FIBONACCI_LOOKBACK
        
        # 加载四层级配置
        self.layer_lookbacks = layer_lookbacks or self._load_layer_lookbacks()
    
    def _load_layer_lookbacks(self) -> Dict[str, List[int]]:
        """从配置加载四层级回溯周期"""
        result = dict(LAYER_FIBONACCI_LOOKBACK)  # 默认值
        
        tf_config = self.config.get("timeframes", {})
        
        # L1 战略层
        l1_config = tf_config.get("l1_strategy", {})
        if "fib_lookback" in l1_config:
            result["l1"] = l1_config["fib_lookback"]
        
        # L2 骨架层
        l2_config = tf_config.get("l2_skeleton", {})
        if "fib_lookback" in l2_config:
            result["l2"] = l2_config["fib_lookback"]
        
        # L3 中继层
        l3_config = tf_config.get("l3_relay", {})
        if "fib_lookback" in l3_config:
            result["l3"] = l3_config["fib_lookback"]
        
        # L4 战术层
        l4_config = tf_config.get("l4_tactical", {})
        if "fib_lookback" in l4_config:
            result["l4"] = l4_config["fib_lookback"]
        
        return result
    
    def get_layer_for_timeframe(self, timeframe: str) -> Optional[str]:
        """获取时间框架对应的层级"""
        return TIMEFRAME_TO_LAYER.get(timeframe)
    
    def get_lookback_for_layer(self, layer: str) -> List[int]:
        """获取层级对应的回溯周期"""
        return self.layer_lookbacks.get(layer, self.fibonacci_lookback)
    
    def get_lookback_for_timeframe(self, timeframe: str) -> List[int]:
        """获取时间框架对应的回溯周期"""
        layer = self.get_layer_for_timeframe(timeframe)
        if layer:
            return self.get_lookback_for_layer(layer)
        return self.fibonacci_lookback
    
    def extract_fractals(
        self,
        klines: List[Dict],
        timeframe: str,
        lookback_periods: Optional[List[int]] = None,
        layer: Optional[str] = None,
    ) -> List[FractalPoint]:
        """
        从 K 线数据中提取分形点
        
        Args:
            klines: K 线数据 [{"open": x, "high": x, "low": x, "close": x, "timestamp": x}, ...]
            timeframe: 时间框架 "1w" | "3d" | "1d" | "4h" | "15m"
            lookback_periods: 自定义回溯周期 (覆盖层级配置)
            layer: 指定层级 (覆盖时间框架推断)
        
        Returns:
            分形点列表 (按价格降序)
        """
        if not klines or len(klines) < 3:
            return []
        
        # 确定回溯周期
        if lookback_periods:
            periods = lookback_periods
        elif layer:
            periods = self.get_lookback_for_layer(layer)
        else:
            periods = self.get_lookback_for_timeframe(timeframe)
        
        # 确定层级
        actual_layer = layer or self.get_layer_for_timeframe(timeframe)
        
        all_fractals: List[FractalPoint] = []
        
        for period in periods:
            # 跳过超出数据范围的周期
            if len(klines) < period * 2 + 1:
                continue
            
            # 提取高点和低点
            highs = self._find_swing_highs(klines, period, timeframe, actual_layer)
            lows = self._find_swing_lows(klines, period, timeframe, actual_layer)
            
            all_fractals.extend(highs)
            all_fractals.extend(lows)
        
        # 去重（相同价格只保留最高周期）
        unique_fractals = self._deduplicate_fractals(all_fractals)
        
        # 按价格降序排列
        return sorted(unique_fractals, key=lambda f: f.price, reverse=True)
    
    def _find_swing_highs(
        self,
        klines: List[Dict],
        period: int,
        timeframe: str,
        layer: Optional[str] = None,
    ) -> List[FractalPoint]:
        """
        寻找摆动高点
        
        条件: 该 K 线的 high 是左右各 period 根 K 线中最高的
        """
        highs = []
        n = len(klines)
        
        for i in range(period, n - period):
            current_high = float(klines[i].get("high", 0))
            is_swing_high = True
            
            # 检查左侧
            for j in range(i - period, i):
                if float(klines[j].get("high", 0)) >= current_high:
                    is_swing_high = False
                    break
            
            # 检查右侧
            if is_swing_high:
                for j in range(i + 1, i + period + 1):
                    if float(klines[j].get("high", 0)) >= current_high:
                        is_swing_high = False
                        break
            
            if is_swing_high:
                highs.append(FractalPoint(
                    price=current_high,
                    timestamp=int(klines[i].get("timestamp", 0)),
                    type="HIGH",
                    timeframe=timeframe,
                    period=period,
                    kline_index=i,
                    layer=layer,  # V3.2.5: 记录层级
                ))
        
        return highs
    
    def _find_swing_lows(
        self,
        klines: List[Dict],
        period: int,
        timeframe: str,
        layer: Optional[str] = None,
    ) -> List[FractalPoint]:
        """
        寻找摆动低点
        
        条件: 该 K 线的 low 是左右各 period 根 K 线中最低的
        """
        lows = []
        n = len(klines)
        
        for i in range(period, n - period):
            current_low = float(klines[i].get("low", float("inf")))
            is_swing_low = True
            
            # 检查左侧
            for j in range(i - period, i):
                if float(klines[j].get("low", float("inf"))) <= current_low:
                    is_swing_low = False
                    break
            
            # 检查右侧
            if is_swing_low:
                for j in range(i + 1, i + period + 1):
                    if float(klines[j].get("low", float("inf"))) <= current_low:
                        is_swing_low = False
                        break
            
            if is_swing_low:
                lows.append(FractalPoint(
                    price=current_low,
                    timestamp=int(klines[i].get("timestamp", 0)),
                    type="LOW",
                    timeframe=timeframe,
                    period=period,
                    kline_index=i,
                    layer=layer,  # V3.2.5: 记录层级
                ))
        
        return lows
    
    def _deduplicate_fractals(
        self,
        fractals: List[FractalPoint],
        price_tolerance: float = 0.001,  # 0.1% 价格容差
    ) -> List[FractalPoint]:
        """
        去重分形点
        
        相同价格的分形点只保留周期最大的一个
        """
        if not fractals:
            return []
        
        # 按价格分组
        price_groups: Dict[float, List[FractalPoint]] = {}
        
        for f in fractals:
            # 四舍五入到容差精度
            key = round(f.price / (f.price * price_tolerance)) * (f.price * price_tolerance)
            
            # 找相近价格的组
            matched_key = None
            for existing_key in price_groups:
                if abs(existing_key - f.price) / f.price < price_tolerance:
                    matched_key = existing_key
                    break
            
            if matched_key is not None:
                price_groups[matched_key].append(f)
            else:
                price_groups[f.price] = [f]
        
        # 每组取周期最大的
        unique = []
        for group in price_groups.values():
            best = max(group, key=lambda x: x.period)
            unique.append(best)
        
        return unique
    
    def extract_from_mtf(
        self,
        klines_by_tf: Dict[str, List[Dict]],
    ) -> Dict[str, List[FractalPoint]]:
        """
        从多时间框架数据中提取分形点
        
        Args:
            klines_by_tf: {"1w": [...], "1d": [...], "4h": [...], "15m": [...]}
        
        Returns:
            {"1w": [FractalPoint, ...], "1d": [...], "4h": [...], "15m": [...]}
        """
        result = {}
        
        for tf, klines in klines_by_tf.items():
            result[tf] = self.extract_fractals(klines, tf)
        
        return result
    
    def extract_from_layers(
        self,
        klines_by_layer: Dict[str, List[Dict]],
        layer_timeframes: Optional[Dict[str, str]] = None,
    ) -> Dict[str, List[FractalPoint]]:
        """
        按层级从 K 线数据中提取分形点 (V3.2.5)
        
        Args:
            klines_by_layer: {"l1": [...], "l2": [...], "l3": [...], "l4": [...]}
            layer_timeframes: 层级到时间框架的映射 (可选)
        
        Returns:
            {"l1": [FractalPoint, ...], "l2": [...], "l3": [...], "l4": [...]}
        """
        # 默认层级到时间框架映射
        default_tf_map = {
            "l1": "1w",
            "l2": "1d",
            "l3": "4h",
            "l4": "15m",
        }
        tf_map = layer_timeframes or default_tf_map
        
        result = {}
        
        for layer, klines in klines_by_layer.items():
            timeframe = tf_map.get(layer, "4h")
            result[layer] = self.extract_fractals(
                klines, 
                timeframe, 
                layer=layer,
            )
        
        return result


def get_anchor_price(klines: List[Dict], lookback: int = 55) -> Optional[float]:
    """
    获取锚点价格 (最近 N 根 K 线的最高/最低点)
    
    Args:
        klines: K 线数据
        lookback: 回溯周期
    
    Returns:
        (highest_high + lowest_low) / 2 作为锚点
    """
    if not klines:
        return None
    
    recent = klines[-lookback:] if len(klines) >= lookback else klines
    
    highs = [float(k.get("high", 0)) for k in recent]
    lows = [float(k.get("low", float("inf"))) for k in recent]
    
    if not highs or not lows:
        return None
    
    return (max(highs) + min(lows)) / 2


def get_anchor_by_layer(
    klines_by_layer: Dict[str, List[Dict]],
    anchor_layer: str = "l2",
    anchor_period: int = 55,
) -> Optional[float]:
    """
    按层级获取锚点价格 (V3.2.5)
    
    默认使用 L2 骨架层 (1d) 的 55x 周期作为锚点
    
    Args:
        klines_by_layer: {"l1": [...], "l2": [...], ...}
        anchor_layer: 锚点层级 (默认 "l2")
        anchor_period: 锚点回溯周期 (默认 55)
    
    Returns:
        锚点价格
    """
    klines = klines_by_layer.get(anchor_layer)
    if not klines:
        return None
    
    return get_anchor_price(klines, anchor_period)
