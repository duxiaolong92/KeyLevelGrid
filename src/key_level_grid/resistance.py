"""
阻力位/支撑位计算模块 (增强版)

特性:
1. 多尺度摆动点识别 (5/13/34)
2. 成交量密集区 (Volume Profile)
3. 多周期融合 (4H + 1D)
4. 最小盈亏比过滤
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Dict, Tuple

from key_level_grid.utils.logger import get_logger
from key_level_grid.models import Kline, KeyLevelGridState


class LevelType(Enum):
    """价位类型"""
    SWING_HIGH = "swing_high"              # 摆动高点
    SWING_LOW = "swing_low"                # 摆动低点
    FIBONACCI = "fibonacci"                 # 斐波那契
    PSYCHOLOGICAL = "psychological"         # 心理关口 (整数位)
    VOLUME_NODE = "volume_node"             # 成交量密集区


@dataclass
class PriceLevel:
    """价格关键位"""
    price: float
    level_type: LevelType
    strength: float             # 强度 0-100
    description: str = ""
    source: str = ""            # 来源标识
    touches: int = 1            # 触及次数
    timeframe: str = "4h"       # 识别周期 "4h" | "1d" | "multi"
    
    def __lt__(self, other: "PriceLevel") -> bool:
        return self.price < other.price
    
    def __repr__(self) -> str:
        return f"PriceLevel({self.price:.4f}, {self.level_type.value}, str={self.strength:.0f}, tf={self.timeframe})"


@dataclass
class ResistanceConfig:
    """阻力位配置"""
    # 多尺度摆动点
    swing_lookbacks: List[int] = field(default_factory=lambda: [5, 13, 34])
    swing_weights: List[float] = field(default_factory=lambda: [0.2, 0.3, 0.5])  # 长期权重更高
    
    # 成交量密集区
    volume_enabled: bool = True
    volume_bucket_pct: float = 0.01    # 价格分桶 1%
    volume_top_pct: float = 0.20       # Top 20% 成交量区
    
    # 多周期融合
    multi_timeframe: bool = True
    mtf_boost: float = 0.30            # 多周期叠加强度提升 30%
    auxiliary_boost: float = 1.2       # 辅助周期（非主周期）强度加成
    
    # 过滤
    min_distance_pct: float = 0.005    # 最小距离 0.5%
    max_distance_pct: float = 0.30     # 最大距离 30%
    min_rr_for_tp: float = 1.5         # 止盈最小盈亏比
    
    # 合并
    merge_tolerance: float = 0.005     # 0.5% 内合并 (减小以保留更多细分关口)
    
    # 斐波那契扩展
    fib_ratios: List[float] = field(default_factory=lambda: [
        1.0, 1.272, 1.618, 2.0, 2.618
    ])
    
    # 强度衰减
    strength_decay_bars: int = 200     # 超过200根K线强度减半


class ResistanceCalculator:
    """
    阻力位/支撑位计算器 (增强版)
    
    增强功能:
    1. 多尺度摆动点 (5/13/34 三尺度)
    2. 成交量密集区 (Volume Profile)
    3. 多周期融合 (4H + 1D)
    4. 最小盈亏比过滤
    
    强度评估维度:
    - 触及次数 (30%)
    - 周期级别 (25%)
    - 来源类型 (20%)
    - 时间衰减 (15%)
    - 反应强度 (10%)
    """
    
    def __init__(self, config: Optional[ResistanceConfig] = None):
        self.config = config or ResistanceConfig()
        self.logger = get_logger(__name__)
    
    # ==================== 主入口 ====================
    
    def calculate_resistance_levels(
        self,
        current_price: float,
        klines: List[Kline],
        direction: str = "long",
        klines_1d: Optional[List[Kline]] = None,
        stop_loss: Optional[float] = None,
        primary_timeframe: str = "4h",
        *,
        klines_by_timeframe: Optional[Dict[str, List[Kline]]] = None,
    ) -> List[PriceLevel]:
        """
        计算阻力位 (多周期融合版)
        
        支持两种调用方式：
        1. 旧接口（向后兼容）：klines + klines_1d + primary_timeframe
        2. 新接口（推荐）：klines_by_timeframe={"4h": [...], "1d": [...]}
        
        Args:
            current_price: 当前价格
            klines: 主周期 K线列表（旧接口）
            direction: 交易方向 "long" | "short"
            klines_1d: 辅助周期 K线列表（旧接口，已废弃）
            stop_loss: 止损价 (可选，用于过滤低盈亏比)
            primary_timeframe: 主周期名称（旧接口）
            klines_by_timeframe: 多周期 K线字典（新接口，推荐）
                格式: {"4h": [...], "1d": [...]} 或 {"15m": [...], "4h": [...], "1d": [...]}
                第一个为主周期，后续为辅助周期（最多支持 3 个周期）
            
        Returns:
            按综合得分排序的阻力位列表
        """
        # 统一转换为多周期字典格式
        if klines_by_timeframe:
            tf_dict = klines_by_timeframe
        else:
            # 向后兼容：从旧参数构建字典
            tf_dict = {primary_timeframe: klines}
            if klines_1d:
                tf_dict["1d"] = klines_1d
        
        return self._calculate_levels_multi_tf(
            current_price=current_price,
            klines_by_timeframe=tf_dict,
            direction=direction,
            stop_loss=stop_loss,
            level_type="resistance",
        )
    
    def calculate_support_levels(
        self,
        current_price: float,
        klines: List[Kline],
        klines_1d: Optional[List[Kline]] = None,
        primary_timeframe: str = "4h",
        *,
        klines_by_timeframe: Optional[Dict[str, List[Kline]]] = None,
    ) -> List[PriceLevel]:
        """
        计算支撑位 (做多止损参考)
        
        支持两种调用方式：
        1. 旧接口（向后兼容）：klines + klines_1d + primary_timeframe
        2. 新接口（推荐）：klines_by_timeframe={"4h": [...], "1d": [...]}
        
        Args:
            current_price: 当前价格
            klines: 主周期 K线列表（旧接口）
            klines_1d: 辅助周期 K线列表（旧接口，已废弃）
            primary_timeframe: 主周期名称（旧接口）
            klines_by_timeframe: 多周期 K线字典（新接口，推荐）
            
        Returns:
            按价格排序的支撑位列表 (从高到低)
        """
        # 统一转换为多周期字典格式
        if klines_by_timeframe:
            tf_dict = klines_by_timeframe
        else:
            # 向后兼容：从旧参数构建字典
            tf_dict = {primary_timeframe: klines}
            if klines_1d:
                tf_dict["1d"] = klines_1d
        
        return self._calculate_levels_multi_tf(
            current_price=current_price,
            klines_by_timeframe=tf_dict,
            direction="short",  # 支撑位在价格下方
            stop_loss=None,
            level_type="support",
        )
    
    def _calculate_levels_multi_tf(
        self,
        current_price: float,
        klines_by_timeframe: Dict[str, List[Kline]],
        direction: str,
        stop_loss: Optional[float],
        level_type: str,  # "resistance" or "support"
    ) -> List[PriceLevel]:
        """
        多周期融合计算价位（内部核心方法）
        
        Args:
            current_price: 当前价格
            klines_by_timeframe: 多周期 K线字典，如 {"4h": [...], "1d": [...]}
            direction: "long" 找阻力位，"short" 找支撑位
            stop_loss: 止损价（可选）
            level_type: "resistance" 或 "support"
            
        Returns:
            排序后的价位列表
        """
        if not klines_by_timeframe:
            return []
        
        # 获取周期列表（限制最多 3 个）
        timeframes = list(klines_by_timeframe.keys())[:3]
        if not timeframes:
            return []
        
        primary_tf = timeframes[0]
        auxiliary_tfs = timeframes[1:]
        
        all_levels: List[PriceLevel] = []
        
        # === 主周期价位 ===
        primary_klines = klines_by_timeframe.get(primary_tf, [])
        if primary_klines:
            levels_primary = self._calculate_single_timeframe(
                primary_klines, current_price, direction, primary_tf
            )
            if level_type == "support":
                levels_primary = [l for l in levels_primary if l.price < current_price]
            all_levels.extend(levels_primary)
        
        # === 辅助周期价位（强度加成） ===
        if self.config.multi_timeframe:
            for aux_tf in auxiliary_tfs:
                aux_klines = klines_by_timeframe.get(aux_tf, [])
                if not aux_klines:
                    continue
                
                levels_aux = self._calculate_single_timeframe(
                    aux_klines, current_price, direction, aux_tf
                )
                
                # 辅助周期强度加成（默认 1.2）
                for level in levels_aux:
                    level.strength = min(100, level.strength * self.config.auxiliary_boost)
                
                if level_type == "support":
                    levels_aux = [l for l in levels_aux if l.price < current_price]
                
                all_levels.extend(levels_aux)
        
        # === 多周期融合 ===
        if self.config.multi_timeframe and len(timeframes) > 1:
            all_levels = self._fuse_multi_timeframe(all_levels)
        
        # === 合并相近价位 ===
        merged = self._merge_levels(all_levels)
        
        if level_type == "support":
            merged = [l for l in merged if l.price < current_price]
        
        # === 过滤 ===
        filtered = self._filter_levels(merged, current_price, direction, stop_loss)
        
        # === 排序: 综合强度和距离 ===
        return self._sort_levels(filtered, current_price, direction)
    
    # ==================== 单周期计算 ====================
    
    def _calculate_single_timeframe(
        self,
        klines: List[Kline],
        current_price: float,
        direction: str,
        timeframe: str
    ) -> List[PriceLevel]:
        """计算单周期阻力位"""
        levels: List[PriceLevel] = []
        
        # 1. 多尺度摆动点
        swing_levels = self._find_multi_scale_swings(klines, current_price, direction, timeframe)
        levels.extend(swing_levels)
        
        # 2. 成交量密集区
        if self.config.volume_enabled:
            volume_levels = self._find_volume_nodes(klines, current_price, direction, timeframe)
            levels.extend(volume_levels)
        
        # 3. 斐波那契位
        fib_levels = self._calculate_fib_extensions(klines, current_price, direction, timeframe)
        levels.extend(fib_levels)
        
        # 4. 整数关口
        psych_levels = self._find_psychological_levels(current_price, direction, timeframe)
        levels.extend(psych_levels)
        
        return levels
    
    # ==================== 多尺度摆动点 ====================
    
    def _find_multi_scale_swings(
        self,
        klines: List[Kline],
        current_price: float,
        direction: str,
        timeframe: str
    ) -> List[PriceLevel]:
        """
        多尺度摆动点识别
        
        三尺度:
        - 短期 (lookback=5):  近期小波动，权重 0.2
        - 中期 (lookback=13): 标准波段，权重 0.3
        - 长期 (lookback=34): 主要结构，权重 0.5
        """
        all_levels: List[PriceLevel] = []
        
        for lookback, weight in zip(self.config.swing_lookbacks, self.config.swing_weights):
            scale_levels = self._find_swings_single_scale(
                klines, current_price, direction, timeframe, lookback
            )
            # 应用尺度权重调整基础强度
            for level in scale_levels:
                # 长期摆动点强度更高
                level.strength = level.strength * (1 + weight)
                level.source = f"swing_{lookback}"
            all_levels.extend(scale_levels)
        
        return all_levels
    
    def _find_swings_single_scale(
        self,
        klines: List[Kline],
        current_price: float,
        direction: str,
        timeframe: str,
        lookback: int
    ) -> List[PriceLevel]:
        """单尺度摆动点识别"""
        levels: List[PriceLevel] = []
        
        if len(klines) < lookback * 2 + 1:
            return levels
        
        for i in range(lookback, len(klines) - lookback):
            high = klines[i].high
            low = klines[i].low
            
            # 检查是否为摆动高点 (左右 N 根的高点都低于当前)
            is_swing_high = all(
                klines[j].high <= high 
                for j in range(i - lookback, i + lookback + 1) 
                if j != i
            )
            
            # 检查是否为摆动低点 (左右 N 根的低点都高于当前)
            is_swing_low = all(
                klines[j].low >= low 
                for j in range(i - lookback, i + lookback + 1) 
                if j != i
            )
            
            # 计算基础强度 (含时间衰减)
            bars_ago = len(klines) - 1 - i
            time_decay = max(0.5, 1.0 - bars_ago / self.config.strength_decay_bars)
            base_strength = 60 * time_decay
            
            if is_swing_high and direction == "long" and high > current_price:
                levels.append(PriceLevel(
                    price=high,
                    level_type=LevelType.SWING_HIGH,
                    strength=base_strength,
                    description=f"摆动高点 (L{lookback}, {bars_ago}根前)",
                    source=f"swing_{lookback}",
                    timeframe=timeframe
                ))
            
            if is_swing_low and direction == "short" and low < current_price:
                levels.append(PriceLevel(
                    price=low,
                    level_type=LevelType.SWING_LOW,
                    strength=base_strength,
                    description=f"摆动低点 (L{lookback}, {bars_ago}根前)",
                    source=f"swing_{lookback}",
                    timeframe=timeframe
                ))
        
        return levels
    
    # ==================== 成交量密集区 ====================
    
    def _find_volume_nodes(
        self,
        klines: List[Kline],
        current_price: float,
        direction: str,
        timeframe: str
    ) -> List[PriceLevel]:
        """
        成交量密集区识别 (Volume Profile)
        
        方法:
        1. 将价格区间分桶 (每 1% 一个桶)
        2. 统计每个桶的累计成交量
        3. Top 20% 成交量的桶 = 密集区
        4. 密集区边界 = 支撑/阻力
        """
        if len(klines) < 20:
            return []
        
        # 找价格范围
        all_highs = [k.high for k in klines]
        all_lows = [k.low for k in klines]
        price_min, price_max = min(all_lows), max(all_highs)
        price_range = price_max - price_min
        
        if price_range == 0:
            return []
        
        # 分桶
        bucket_size = current_price * self.config.volume_bucket_pct
        if bucket_size == 0:
            bucket_size = price_range / 100  # fallback
        
        buckets: Dict[int, float] = {}  # bucket_idx -> total_volume
        
        for k in klines:
            # 将该 K 线的成交量分配到对应的价格桶
            mid_price = (k.high + k.low) / 2
            bucket_idx = int((mid_price - price_min) / bucket_size)
            
            volume = k.volume if k.volume else 0
            buckets[bucket_idx] = buckets.get(bucket_idx, 0) + volume
        
        if not buckets:
            return []
        
        # 找 Top 20% 成交量桶
        volumes = sorted(buckets.values(), reverse=True)
        top_index = max(1, int(len(volumes) * self.config.volume_top_pct))
        threshold = volumes[top_index - 1] if top_index <= len(volumes) else 0
        max_volume = volumes[0] if volumes else 1
        
        levels: List[PriceLevel] = []
        
        for bucket_idx, volume in buckets.items():
            if volume >= threshold:
                # 桶的中心价格
                bucket_price = price_min + (bucket_idx + 0.5) * bucket_size
                
                # 成交量越大，强度越高 (50-80 分)
                volume_ratio = volume / max_volume if max_volume > 0 else 0
                strength = 50 + 30 * volume_ratio
                
                if direction == "long" and bucket_price > current_price:
                    levels.append(PriceLevel(
                        price=bucket_price,
                        level_type=LevelType.VOLUME_NODE,
                        strength=strength,
                        description=f"成交密集区 (Vol {volume_ratio:.0%})",
                        source="volume_node",
                        timeframe=timeframe
                    ))
                elif direction == "short" and bucket_price < current_price:
                    levels.append(PriceLevel(
                        price=bucket_price,
                        level_type=LevelType.VOLUME_NODE,
                        strength=strength,
                        description=f"成交密集区 (Vol {volume_ratio:.0%})",
                        source="volume_node",
                        timeframe=timeframe
                    ))
        
        return levels
    
    # ==================== 斐波那契 ====================
    
    def _calculate_fib_extensions(
        self,
        klines: List[Kline],
        current_price: float,
        direction: str,
        timeframe: str
    ) -> List[PriceLevel]:
        """计算斐波那契扩展位"""
        if len(klines) < 50:
            return []
        
        # 使用最近 100 根 K 线的高低点
        recent = klines[-100:] if len(klines) > 100 else klines
        recent_high = max(k.high for k in recent)
        recent_low = min(k.low for k in recent)
        trend_range = recent_high - recent_low
        
        if trend_range == 0:
            return []
        
        levels: List[PriceLevel] = []
        
        if direction == "long":
            for ratio in self.config.fib_ratios:
                fib_price = recent_low + trend_range * ratio
                if fib_price > current_price:
                    # 1.618 和 2.618 是黄金比例，强度更高
                    strength = 55 if ratio in [1.618, 2.618] else 40
                    levels.append(PriceLevel(
                        price=fib_price,
                        level_type=LevelType.FIBONACCI,
                        strength=strength,
                        description=f"Fib {ratio}",
                        source=f"fib_{ratio}",
                        timeframe=timeframe
                    ))
        else:
            for ratio in self.config.fib_ratios:
                fib_price = recent_high - trend_range * ratio
                if fib_price < current_price and fib_price > 0:
                    strength = 55 if ratio in [1.618, 2.618] else 40
                    levels.append(PriceLevel(
                        price=fib_price,
                        level_type=LevelType.FIBONACCI,
                        strength=strength,
                        description=f"Fib {ratio}",
                        source=f"fib_{ratio}",
                        timeframe=timeframe
                    ))
        
        return levels
    
    # ==================== 心理关口 ====================
    
    def _find_psychological_levels(
        self,
        current_price: float,
        direction: str,
        timeframe: str
    ) -> List[PriceLevel]:
        """查找整数关口 (心理价位)"""
        # 根据价格量级确定步长 (多层级)
        # 大价格资产 (如 BTC) 需要更细的关口
        levels: List[PriceLevel] = []
        
        if current_price >= 10000:
            # 大价格: 生成 500, 1000, 5000, 10000 级别的关口
            steps = [500, 1000, 5000, 10000]
        elif current_price >= 1000:
            steps = [100, 500, 1000]
        elif current_price >= 100:
            steps = [10, 50, 100]
        elif current_price >= 10:
            steps = [1, 5, 10]
        elif current_price >= 1:
            steps = [0.1, 0.5, 1]
        elif current_price >= 0.1:
            steps = [0.01, 0.05, 0.1]
        else:
            steps = [0.001, 0.005, 0.01]
        
        seen_prices = set()
        
        for step in steps:
            base = (current_price // step) * step
            
            # 向上找 5 个关口
            for i in range(1, 6):
                if direction == "long":
                    price = base + step * i
                else:
                    price = base - step * (i - 1)
                    if price <= 0:
                        continue
                
                # 避免重复
                price_key = round(price, 6)
                if price_key in seen_prices:
                    continue
                seen_prices.add(price_key)
                
                # 大步长的关口强度更高
                strength = 35 + (steps.index(step) * 5)
                
                levels.append(PriceLevel(
                    price=price,
                    level_type=LevelType.PSYCHOLOGICAL,
                    strength=strength,
                    description=f"心理关口 {price:.2f}",
                    source="round_number",
                    timeframe=timeframe
                ))
        
        return levels
    
    # ==================== 多周期融合 ====================
    
    def _fuse_multi_timeframe(self, levels: List[PriceLevel]) -> List[PriceLevel]:
        """
        多周期融合
        
        同一价位在多个周期出现，强度提升 30%
        """
        if not levels:
            return levels
        
        # 按价格分组
        price_groups: Dict[float, List[PriceLevel]] = {}
        tolerance = self.config.merge_tolerance
        
        for level in levels:
            found_group = False
            for group_price in list(price_groups.keys()):
                if abs(level.price - group_price) / group_price < tolerance:
                    price_groups[group_price].append(level)
                    found_group = True
                    break
            
            if not found_group:
                price_groups[level.price] = [level]
        
        # 融合
        fused: List[PriceLevel] = []
        for group_price, group_levels in price_groups.items():
            # 检查是否多周期
            timeframes = set(l.timeframe for l in group_levels)
            
            # 取最强的作为基础
            best = max(group_levels, key=lambda x: x.strength)
            
            if len(timeframes) > 1:
                # 多周期叠加，强度提升
                best.strength = min(100, best.strength * (1 + self.config.mtf_boost))
                best.timeframe = "multi"
                sources = set(l.source for l in group_levels)
                best.source = "+".join(sources)
                best.description = f"{best.description} (多周期共振)"
            
            # 累加触及次数
            best.touches = sum(l.touches for l in group_levels)
            
            fused.append(best)
        
        return fused
    
    # ==================== 过滤与排序 ====================
    
    def _filter_levels(
        self,
        levels: List[PriceLevel],
        current_price: float,
        direction: str,
        stop_loss: Optional[float] = None
    ) -> List[PriceLevel]:
        """
        过滤阻力位
        
        1. 距离太近 (< 0.5%) - 忽略
        2. 距离太远 (> 30%) - 忽略
        3. 盈亏比 < 1.5R - 标记但保留
        """
        filtered: List[PriceLevel] = []
        
        for level in levels:
            distance_pct = abs(level.price - current_price) / current_price
            
            # 距离过滤
            if distance_pct < self.config.min_distance_pct:
                continue
            if distance_pct > self.config.max_distance_pct:
                continue
            
            # 方向过滤
            if direction == "long" and level.price <= current_price:
                continue
            if direction == "short" and level.price >= current_price:
                continue
            
            # 盈亏比标记 (如果提供了止损价)
            if stop_loss and stop_loss > 0:
                risk = abs(current_price - stop_loss)
                if risk > 0:
                    reward = abs(level.price - current_price)
                    rr = reward / risk
                    if rr < self.config.min_rr_for_tp:
                        # 标记为低盈亏比
                        level.description = f"{level.description} (RR<{self.config.min_rr_for_tp})"
            
            filtered.append(level)
        
        return filtered
    
    def _sort_levels(
        self,
        levels: List[PriceLevel],
        current_price: float,
        direction: str
    ) -> List[PriceLevel]:
        """
        综合排序
        
        排序权重:
        - 强度 60%
        - 距离 40% (近的优先)
        """
        def sort_key(level: PriceLevel) -> float:
            distance = abs(level.price - current_price) / current_price
            # 距离越近分数越高 (最近的 = 1.0)
            distance_score = max(0, 1 - distance / self.config.max_distance_pct)
            
            # 综合分 = 强度 * 0.6 + 距离分 * 40
            return level.strength * 0.6 + distance_score * 40
        
        return sorted(levels, key=sort_key, reverse=True)
    
    def _merge_levels(self, levels: List[PriceLevel]) -> List[PriceLevel]:
        """
        合并相近的价位 (容差内)
        
        合并规则:
        1. 多来源叠加 (swing + volume + fib) → 强度加成
        2. 保留所有来源信息
        3. 累加触及次数
        """
        if not levels:
            return []
        
        # 按价格分组
        price_groups: Dict[float, List[PriceLevel]] = {}
        tolerance = self.config.merge_tolerance
        
        for level in levels:
            found_group = False
            for group_price in list(price_groups.keys()):
                if group_price > 0 and abs(level.price - group_price) / group_price < tolerance:
                    price_groups[group_price].append(level)
                    found_group = True
                    break
            
            if not found_group:
                price_groups[level.price] = [level]
        
        merged: List[PriceLevel] = []
        
        for group_price, group_levels in price_groups.items():
            # 统计来源类型
            sources = set(l.source for l in group_levels)
            level_types = set(l.level_type for l in group_levels)
            timeframes = set(l.timeframe for l in group_levels)
            
            # 取强度最高的作为基础
            best = max(group_levels, key=lambda x: x.strength)
            
            # 多来源叠加加成
            num_sources = len(sources)
            if num_sources > 1:
                # 每多一个来源，强度 +15%
                source_boost = 1 + 0.15 * (num_sources - 1)
                best.strength = min(100, best.strength * source_boost)
                
                # 更新来源信息
                best.source = "+".join(sorted(sources))
                
                # 更新描述
                source_names = {
                    "swing_5": "摆动点",
                    "swing_13": "摆动点",
                    "swing_34": "摆动点",
                    "volume_node": "成交密集",
                    "round_number": "心理关口",
                }
                source_display = []
                for s in sources:
                    if "swing" in s:
                        if "摆动点" not in source_display:
                            source_display.append("摆动点")
                    elif "fib" in s:
                        source_display.append("斐波那契")
                    elif s in source_names:
                        source_display.append(source_names[s])
                    else:
                        source_display.append(s)
                best.description = " + ".join(source_display)
            
            # 多周期叠加
            if len(timeframes) > 1:
                best.timeframe = "multi"
                best.strength = min(100, best.strength * (1 + self.config.mtf_boost))
            
            # 累加触及次数
            best.touches = sum(l.touches for l in group_levels)
            
            merged.append(best)
        
        return merged


# ==================== 止盈相关 ====================

@dataclass
class TakeProfitLevel:
    """止盈级别"""
    price: float
    close_pct: float          # 平仓比例 (0-1)
    rr_multiple: float        # R倍数
    reason: str


@dataclass
class TakeProfitPlan:
    """止盈计划"""
    levels: List[TakeProfitLevel]
    total_position_usdt: float
    entry_price: float
    stop_loss: float
    
    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "entry_price": self.entry_price,
            "stop_loss": self.stop_loss,
            "levels": [
                {
                    "price": l.price,
                    "close_pct": l.close_pct,
                    "rr_multiple": l.rr_multiple,
                    "reason": l.reason
                }
                for l in self.levels
            ]
        }


class ResistanceBasedTakeProfit:
    """
    基于阻力位的止盈策略
    
    规则:
    1. 第一止盈必须达到 1.5R 以上
    2. 根据阻力位强度决定平仓比例
    3. 最后一个止盈留 10% 仓位跟踪
    """
    
    def __init__(self, min_rr_ratio: float = 1.5):
        self.min_rr_ratio = min_rr_ratio
        self.logger = get_logger(__name__)
    
    def create_take_profit_plan(
        self,
        entry_price: float,
        stop_loss: float,
        resistance_levels: List[PriceLevel],
        direction: str = "long",
        max_levels: int = 4
    ) -> TakeProfitPlan:
        """
        创建止盈计划
        
        Args:
            entry_price: 入场价
            stop_loss: 止损价
            resistance_levels: 阻力位列表
            direction: 交易方向
            max_levels: 最大止盈级别数
            
        Returns:
            TakeProfitPlan
        """
        risk_distance = abs(entry_price - stop_loss)
        if risk_distance == 0:
            return TakeProfitPlan(
                levels=[],
                total_position_usdt=0,
                entry_price=entry_price,
                stop_loss=stop_loss
            )
        
        tp_levels: List[TakeProfitLevel] = []
        remaining_pct = 1.0
        
        for i, resistance in enumerate(resistance_levels[:max_levels]):
            # 计算 R 倍数
            if direction == "long":
                profit_distance = resistance.price - entry_price
            else:
                profit_distance = entry_price - resistance.price
            
            if profit_distance <= 0:
                continue
            
            rr_multiple = profit_distance / risk_distance
            
            # 第一止盈必须 >= min_rr_ratio
            if len(tp_levels) == 0 and rr_multiple < self.min_rr_ratio:
                continue
            
            # 根据阻力位强度决定平仓比例
            close_pct = self._calculate_close_pct(
                resistance.strength,
                remaining_pct,
                is_last=(i == min(len(resistance_levels), max_levels) - 1)
            )
            
            tp_levels.append(TakeProfitLevel(
                price=resistance.price,
                close_pct=close_pct,
                rr_multiple=rr_multiple,
                reason=resistance.description
            ))
            
            remaining_pct -= close_pct
            if remaining_pct <= 0.1:  # 保留10%跟踪
                break
        
        # 如果没有合适的阻力位，使用默认 R 倍数
        if not tp_levels:
            tp_levels = self._create_default_plan(
                entry_price, stop_loss, risk_distance, direction
            )
        
        return TakeProfitPlan(
            levels=tp_levels,
            total_position_usdt=0,  # 由调用者填充
            entry_price=entry_price,
            stop_loss=stop_loss
        )
    
    def _calculate_close_pct(
        self,
        strength: float,
        remaining_pct: float,
        is_last: bool
    ) -> float:
        """
        根据阻力位强度计算平仓比例
        
        强度 > 80: 平仓 40-50%
        强度 60-80: 平仓 30-40%
        强度 < 60: 平仓 20-30%
        """
        if is_last:
            return max(0, remaining_pct - 0.1)  # 保留10%
        
        if strength >= 80:
            base_pct = 0.40
        elif strength >= 60:
            base_pct = 0.30
        else:
            base_pct = 0.20
        
        return min(base_pct, remaining_pct - 0.1)
    
    def _create_default_plan(
        self,
        entry_price: float,
        stop_loss: float,
        risk_distance: float,
        direction: str
    ) -> List[TakeProfitLevel]:
        """创建默认止盈计划 (无阻力位时)"""
        default_levels: List[TakeProfitLevel] = []
        
        # 默认 R 倍数: 1.5R, 2.5R, 4R
        rr_targets = [1.5, 2.5, 4.0]
        close_pcts = [0.40, 0.30, 0.20]
        
        for rr, pct in zip(rr_targets, close_pcts):
            if direction == "long":
                price = entry_price + risk_distance * rr
            else:
                price = entry_price - risk_distance * rr
            
            if price <= 0:
                continue
            
            default_levels.append(TakeProfitLevel(
                price=price,
                close_pct=pct,
                rr_multiple=rr,
                reason=f"默认 {rr}R"
            ))
        
        return default_levels
