"""
分形点提取器 (LEVEL_GENERATION.md v3.1.0)

基于斐波那契周期提取多时间框架的分形高低点。

核心算法:
- 回溯周期: [8, 13, 21, 34, 55, 89] (斐波那契数列)
- 多时间框架: 1d (趋势), 4h (战略), 15m (战术)
- 分形条件: 极值点左右各有 lookback 根 K 线低于/高于该点
"""

from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field
from key_level_grid.core.scoring import FractalPoint, calculate_base_score


# 默认斐波那契回溯周期
DEFAULT_FIBONACCI_LOOKBACK = [8, 13, 21, 34, 55, 89]


class FractalExtractor:
    """
    MTF 分形点提取器
    
    从不同时间框架的 K 线数据中提取分形高低点，
    作为支撑/阻力位的候选。
    """
    
    def __init__(
        self,
        fibonacci_lookback: Optional[List[int]] = None,
        config: Optional[Dict] = None,
    ):
        """
        初始化分形提取器
        
        Args:
            fibonacci_lookback: 斐波那契回溯周期列表
            config: 配置字典 (从 config.yaml 加载)
        """
        self.config = config or {}
        self.fibonacci_lookback = fibonacci_lookback or DEFAULT_FIBONACCI_LOOKBACK
    
    def extract_fractals(
        self,
        klines: List[Dict],
        timeframe: str,
        lookback_periods: Optional[List[int]] = None,
    ) -> List[FractalPoint]:
        """
        从 K 线数据中提取分形点
        
        Args:
            klines: K 线数据 [{"open": x, "high": x, "low": x, "close": x, "timestamp": x}, ...]
            timeframe: 时间框架 "1d" | "4h" | "15m"
            lookback_periods: 自定义回溯周期
        
        Returns:
            分形点列表 (按价格降序)
        """
        if not klines or len(klines) < 3:
            return []
        
        periods = lookback_periods or self.fibonacci_lookback
        all_fractals: List[FractalPoint] = []
        
        for period in periods:
            # 跳过超出数据范围的周期
            if len(klines) < period * 2 + 1:
                continue
            
            # 提取高点和低点
            highs = self._find_swing_highs(klines, period, timeframe)
            lows = self._find_swing_lows(klines, period, timeframe)
            
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
                ))
        
        return highs
    
    def _find_swing_lows(
        self,
        klines: List[Dict],
        period: int,
        timeframe: str,
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
            klines_by_tf: {"1d": [...], "4h": [...], "15m": [...]}
        
        Returns:
            {"1d": [FractalPoint, ...], "4h": [...], "15m": [...]}
        """
        result = {}
        
        for tf, klines in klines_by_tf.items():
            result[tf] = self.extract_fractals(klines, tf)
        
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
