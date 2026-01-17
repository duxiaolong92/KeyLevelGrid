"""
心理位匹配器 (LEVEL_GENERATION.md v3.1.0)

识别心理关口并对齐分形点:
- 斐波那契回撤位: 0.382, 0.5, 0.618, 0.786
- 整数位: x000, x500 等大整数
- 对齐规则: 若分形点距心理位 < 1%, 强制吸附
"""

from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
import math


# 默认斐波那契比例
DEFAULT_FIB_RATIOS = [0.236, 0.382, 0.5, 0.618, 0.786, 1.0, 1.272, 1.618]


@dataclass
class PsychologyLevel:
    """心理关口"""
    price: float
    type: str  # "fib" | "round"
    ratio: Optional[float] = None  # 斐波那契比例 (若为 fib 类型)
    label: str = ""


class PsychologyMatcher:
    """
    心理位匹配器
    
    将分形点对齐到最近的心理关口，
    提升水位的市场认可度。
    """
    
    def __init__(
        self,
        fib_ratios: Optional[List[float]] = None,
        snap_tolerance: float = 0.01,  # 1% 吸附容差
        config: Optional[Dict] = None,
    ):
        """
        初始化心理位匹配器
        
        Args:
            fib_ratios: 斐波那契比例列表
            snap_tolerance: 吸附容差 (若分形点距心理位 < 此值，则吸附)
            config: 配置字典
        """
        self.fib_ratios = fib_ratios or DEFAULT_FIB_RATIOS
        self.snap_tolerance = snap_tolerance
        self.config = config or {}
    
    def calculate_fib_levels(
        self,
        high: float,
        low: float,
    ) -> List[PsychologyLevel]:
        """
        计算斐波那契回撤/延伸位
        
        Args:
            high: 区间最高价
            low: 区间最低价
        
        Returns:
            斐波那契心理位列表 (按价格降序)
        """
        if high <= low:
            return []
        
        diff = high - low
        levels = []
        
        for ratio in self.fib_ratios:
            # 回撤 (从高到低)
            retracement_price = high - diff * ratio
            levels.append(PsychologyLevel(
                price=retracement_price,
                type="fib",
                ratio=ratio,
                label=f"Fib {ratio:.3f}",
            ))
            
            # 延伸 (超出区间)
            if ratio > 1.0:
                extension_up = high + diff * (ratio - 1)
                extension_down = low - diff * (ratio - 1)
                levels.append(PsychologyLevel(
                    price=extension_up,
                    type="fib",
                    ratio=ratio,
                    label=f"Fib Ext {ratio:.3f}",
                ))
                if extension_down > 0:
                    levels.append(PsychologyLevel(
                        price=extension_down,
                        type="fib",
                        ratio=ratio,
                        label=f"Fib Ext -{ratio:.3f}",
                    ))
        
        # 去重并排序
        seen = set()
        unique = []
        for lvl in sorted(levels, key=lambda x: x.price, reverse=True):
            price_key = round(lvl.price, 2)
            if price_key not in seen:
                seen.add(price_key)
                unique.append(lvl)
        
        return unique
    
    def find_round_numbers(
        self,
        price_min: float,
        price_max: float,
    ) -> List[PsychologyLevel]:
        """
        在价格范围内找整数位
        
        规则:
        - 5 位数: x0000, x5000 (如 90000, 95000)
        - 4 位数: x000 (如 9000)
        - 小于 1000: x00 (如 500)
        
        Args:
            price_min: 最低价
            price_max: 最高价
        
        Returns:
            整数位列表 (按价格降序)
        """
        levels = []
        
        # 确定整数位间距
        price_range = price_max - price_min
        
        if price_max >= 10000:
            # BTC 级别: 1000 和 5000 整数位
            intervals = [10000, 5000, 1000]
        elif price_max >= 1000:
            # ETH 级别: 100 和 500
            intervals = [1000, 500, 100]
        elif price_max >= 100:
            intervals = [100, 50, 10]
        else:
            intervals = [10, 5, 1]
        
        for interval in intervals:
            # 找范围内的整数位
            start = math.floor(price_min / interval) * interval
            end = math.ceil(price_max / interval) * interval
            
            current = start
            while current <= end:
                if price_min <= current <= price_max:
                    levels.append(PsychologyLevel(
                        price=current,
                        type="round",
                        label=f"Round {current:,.0f}",
                    ))
                current += interval
        
        # 去重并排序
        seen = set()
        unique = []
        for lvl in sorted(levels, key=lambda x: x.price, reverse=True):
            if lvl.price not in seen:
                seen.add(lvl.price)
                unique.append(lvl)
        
        return unique
    
    def snap_to_psychology(
        self,
        price: float,
        psychology_levels: List[PsychologyLevel],
        tolerance: Optional[float] = None,
    ) -> Tuple[float, Optional[PsychologyLevel]]:
        """
        将价格吸附到最近的心理位
        
        Args:
            price: 原始价格
            psychology_levels: 心理位列表
            tolerance: 吸附容差 (默认使用初始化时的值)
        
        Returns:
            (吸附后价格, 匹配的心理位) 或 (原价格, None)
        """
        if not psychology_levels or price <= 0:
            return price, None
        
        tol = tolerance if tolerance is not None else self.snap_tolerance
        
        best_match = None
        best_distance = float("inf")
        
        for psy in psychology_levels:
            if psy.price <= 0:
                continue
            
            distance = abs(price - psy.price) / price
            if distance < tol and distance < best_distance:
                best_distance = distance
                best_match = psy
        
        if best_match:
            return best_match.price, best_match
        
        return price, None
    
    def get_psychology_weight(
        self,
        matched_level: Optional[PsychologyLevel],
    ) -> float:
        """
        获取心理位权重
        
        Args:
            matched_level: 匹配的心理位 (None 表示无匹配)
        
        Returns:
            权重 (1.0 或 1.2)
        """
        if matched_level is None:
            return 1.0
        
        # 从配置读取权重
        weight = self.config.get("scoring", {}).get("psychology_weight", 1.2)
        return float(weight)
    
    def find_all_psychology_levels(
        self,
        klines: List[Dict],
    ) -> List[PsychologyLevel]:
        """
        从 K 线数据中提取所有心理位
        
        Args:
            klines: K 线数据
        
        Returns:
            心理位列表 (合并斐波那契和整数位)
        """
        if not klines:
            return []
        
        # 计算价格范围
        highs = [float(k.get("high", 0)) for k in klines]
        lows = [float(k.get("low", float("inf"))) for k in klines]
        
        price_max = max(highs)
        price_min = min(lows)
        
        # 获取斐波那契位
        fib_levels = self.calculate_fib_levels(price_max, price_min)
        
        # 获取整数位
        round_levels = self.find_round_numbers(price_min, price_max)
        
        # 合并并去重
        all_levels = fib_levels + round_levels
        
        # 按价格降序
        return sorted(all_levels, key=lambda x: x.price, reverse=True)
