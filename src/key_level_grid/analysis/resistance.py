"""
支撑阻力位计算模块

特性:
1. 多尺度摆动点识别 (5/13/34)
2. 成交量密集区 (Volume Profile)
3. 多周期融合 (4H + 1D)
4. 最小盈亏比过滤
"""

from dataclasses import dataclass, field
from typing import List, Optional, Dict

from key_level_grid.utils.logger import get_logger
from key_level_grid.core.models import Kline
from key_level_grid.core.types import LevelType
from key_level_grid.core.config import ResistanceConfig


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


class ResistanceCalculator:
    """
    阻力位/支撑位计算器
    
    增强功能:
    1. 多尺度摆动点 (5/13/34 三尺度)
    2. 成交量密集区 (Volume Profile)
    3. 多周期融合 (4H + 1D)
    4. 最小盈亏比过滤
    """
    
    def __init__(self, config: Optional[ResistanceConfig] = None):
        self.config = config or ResistanceConfig()
        self.logger = get_logger(__name__)

    def _apply_strength_boost(self, base_strength: float, boost_ratio: float) -> float:
        """以递减方式应用强度加成"""
        if base_strength <= 0 or boost_ratio <= 0:
            return base_strength
        if base_strength >= 100:
            return 100.0
        boosted = base_strength + (100 - base_strength) * boost_ratio
        return min(100.0, boosted)
    
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
        """计算阻力位"""
        if klines_by_timeframe:
            tf_dict = klines_by_timeframe
        else:
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
        """计算支撑位"""
        if klines_by_timeframe:
            tf_dict = klines_by_timeframe
        else:
            tf_dict = {primary_timeframe: klines}
            if klines_1d:
                tf_dict["1d"] = klines_1d
        
        return self._calculate_levels_multi_tf(
            current_price=current_price,
            klines_by_timeframe=tf_dict,
            direction="short",
            stop_loss=None,
            level_type="support",
        )
    
    def _calculate_levels_multi_tf(
        self,
        current_price: float,
        klines_by_timeframe: Dict[str, List[Kline]],
        direction: str,
        stop_loss: Optional[float],
        level_type: str,
    ) -> List[PriceLevel]:
        """多周期融合计算价位"""
        if not klines_by_timeframe:
            return []
        
        timeframes = list(klines_by_timeframe.keys())[:3]
        if not timeframes:
            return []
        
        primary_tf = timeframes[0]
        auxiliary_tfs = timeframes[1:]
        
        all_levels: List[PriceLevel] = []
        
        # 主周期价位
        primary_klines = klines_by_timeframe.get(primary_tf, [])
        if primary_klines:
            levels_primary = self._calculate_single_timeframe(
                primary_klines, current_price, direction, primary_tf
            )
            if level_type == "support":
                levels_primary = [l for l in levels_primary if l.price < current_price]
            all_levels.extend(levels_primary)
        
        # 辅助周期价位
        if self.config.multi_timeframe:
            for aux_tf in auxiliary_tfs:
                aux_klines = klines_by_timeframe.get(aux_tf, [])
                if not aux_klines:
                    continue
                
                levels_aux = self._calculate_single_timeframe(
                    aux_klines, current_price, direction, aux_tf
                )
                
                for level in levels_aux:
                    level.strength = min(100, level.strength * self.config.auxiliary_boost)
                
                if level_type == "support":
                    levels_aux = [l for l in levels_aux if l.price < current_price]
                
                all_levels.extend(levels_aux)
        
        # 多周期融合
        if self.config.multi_timeframe and len(timeframes) > 1:
            all_levels = self._fuse_multi_timeframe(all_levels)
        
        # 合并相近价位
        merged = self._merge_levels(all_levels)
        
        if level_type == "support":
            merged = [l for l in merged if l.price < current_price]
        
        # 过滤
        filtered = self._filter_levels(merged, current_price, direction, stop_loss)
        
        # 排序
        return self._sort_levels(filtered, current_price, direction)
    
    def _calculate_single_timeframe(
        self,
        klines: List[Kline],
        current_price: float,
        direction: str,
        timeframe: str
    ) -> List[PriceLevel]:
        """计算单周期价位"""
        levels: List[PriceLevel] = []
        
        # 多尺度摆动点
        swing_levels = self._find_multi_scale_swings(klines, current_price, direction, timeframe)
        levels.extend(swing_levels)
        
        # 成交量密集区
        if self.config.volume_enabled:
            volume_levels = self._find_volume_nodes(klines, current_price, direction, timeframe)
            levels.extend(volume_levels)
        
        # 斐波那契位
        fib_levels = self._calculate_fib_extensions(klines, current_price, direction, timeframe)
        levels.extend(fib_levels)
        
        # 整数关口
        psych_levels = self._find_psychological_levels(current_price, direction, timeframe)
        levels.extend(psych_levels)
        
        return levels
    
    def _find_multi_scale_swings(
        self,
        klines: List[Kline],
        current_price: float,
        direction: str,
        timeframe: str
    ) -> List[PriceLevel]:
        """多尺度摆动点识别"""
        all_levels: List[PriceLevel] = []
        
        for lookback, weight in zip(self.config.swing_lookbacks, self.config.swing_weights):
            scale_levels = self._find_swings_single_scale(
                klines, current_price, direction, timeframe, lookback
            )
            for level in scale_levels:
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
            
            is_swing_high = all(
                klines[j].high <= high 
                for j in range(i - lookback, i + lookback + 1) 
                if j != i
            )
            
            is_swing_low = all(
                klines[j].low >= low 
                for j in range(i - lookback, i + lookback + 1) 
                if j != i
            )
            
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
    
    def _find_volume_nodes(
        self,
        klines: List[Kline],
        current_price: float,
        direction: str,
        timeframe: str
    ) -> List[PriceLevel]:
        """成交量密集区识别"""
        if len(klines) < 20:
            return []
        
        all_highs = [k.high for k in klines]
        all_lows = [k.low for k in klines]
        price_min, price_max = min(all_lows), max(all_highs)
        price_range = price_max - price_min
        
        if price_range == 0:
            return []
        
        bucket_size = current_price * self.config.volume_bucket_pct
        if bucket_size == 0:
            bucket_size = price_range / 100
        
        buckets: Dict[int, float] = {}
        
        for k in klines:
            mid_price = (k.high + k.low) / 2
            bucket_idx = int((mid_price - price_min) / bucket_size)
            volume = k.volume if k.volume else 0
            buckets[bucket_idx] = buckets.get(bucket_idx, 0) + volume
        
        if not buckets:
            return []
        
        volumes = sorted(buckets.values(), reverse=True)
        top_index = max(1, int(len(volumes) * self.config.volume_top_pct))
        threshold = volumes[top_index - 1] if top_index <= len(volumes) else 0
        max_volume = volumes[0] if volumes else 1
        
        levels: List[PriceLevel] = []
        
        for bucket_idx, volume in buckets.items():
            if volume >= threshold:
                bucket_price = price_min + (bucket_idx + 0.5) * bucket_size
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
        
        recent = klines[-100:] if len(klines) > 100 else klines
        recent_high = max(k.high for k in recent)
        recent_low = min(k.low for k in recent)
        trend_range = recent_high - recent_low
        
        if trend_range == 0:
            return []
        
        levels: List[PriceLevel] = []
        fib_ratios = [1.0, 1.272, 1.618, 2.0, 2.618]
        
        if direction == "long":
            for ratio in fib_ratios:
                fib_price = recent_low + trend_range * ratio
                if fib_price > current_price:
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
            for ratio in fib_ratios:
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
    
    def _find_psychological_levels(
        self,
        current_price: float,
        direction: str,
        timeframe: str
    ) -> List[PriceLevel]:
        """查找整数关口"""
        levels: List[PriceLevel] = []
        
        if current_price >= 10000:
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
            
            for i in range(1, 6):
                if direction == "long":
                    price = base + step * i
                else:
                    price = base - step * (i - 1)
                    if price <= 0:
                        continue
                
                price_key = round(price, 6)
                if price_key in seen_prices:
                    continue
                seen_prices.add(price_key)
                
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
    
    def _fuse_multi_timeframe(self, levels: List[PriceLevel]) -> List[PriceLevel]:
        """多周期融合"""
        if not levels:
            return levels
        
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
        
        fused: List[PriceLevel] = []
        for group_price, group_levels in price_groups.items():
            timeframes = set(l.timeframe for l in group_levels)
            best = max(group_levels, key=lambda x: x.strength)
            
            if len(timeframes) > 1:
                best.strength = self._apply_strength_boost(best.strength, self.config.mtf_boost)
                best.timeframe = "multi"
                sources = set(l.source for l in group_levels)
                best.source = "+".join(sources)
                best.description = f"{best.description} (多周期共振)"
            
            best.touches = sum(l.touches for l in group_levels)
            fused.append(best)
        
        return fused
    
    def _filter_levels(
        self,
        levels: List[PriceLevel],
        current_price: float,
        direction: str,
        stop_loss: Optional[float] = None
    ) -> List[PriceLevel]:
        """过滤价位"""
        filtered: List[PriceLevel] = []
        
        for level in levels:
            distance_pct = abs(level.price - current_price) / current_price
            
            if distance_pct < self.config.min_distance_pct:
                continue
            if distance_pct > self.config.max_distance_pct:
                continue
            
            if direction == "long" and level.price <= current_price:
                continue
            if direction == "short" and level.price >= current_price:
                continue
            
            if stop_loss and stop_loss > 0:
                risk = abs(current_price - stop_loss)
                if risk > 0:
                    reward = abs(level.price - current_price)
                    rr = reward / risk
                    if rr < self.config.min_rr_for_tp:
                        level.description = f"{level.description} (RR<{self.config.min_rr_for_tp})"
            
            filtered.append(level)
        
        return filtered
    
    def _sort_levels(
        self,
        levels: List[PriceLevel],
        current_price: float,
        direction: str
    ) -> List[PriceLevel]:
        """综合排序"""
        def sort_key(level: PriceLevel) -> float:
            distance = abs(level.price - current_price) / current_price
            distance_score = max(0, 1 - distance / self.config.max_distance_pct)
            return level.strength * 0.6 + distance_score * 40
        
        return sorted(levels, key=sort_key, reverse=True)
    
    def _merge_levels(self, levels: List[PriceLevel]) -> List[PriceLevel]:
        """合并相近价位"""
        if not levels:
            return []
        
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
            sources = set(l.source for l in group_levels)
            timeframes = set(l.timeframe for l in group_levels)
            
            best = max(group_levels, key=lambda x: x.strength)
            
            num_sources = len(sources)
            if num_sources > 1:
                source_boost = 0.15 * (num_sources - 1)
                best.strength = self._apply_strength_boost(best.strength, source_boost)
                best.source = "+".join(sorted(sources))
            
            if len(timeframes) > 1:
                best.timeframe = "multi"
                best.strength = self._apply_strength_boost(best.strength, self.config.mtf_boost)
            
            best.touches = sum(l.touches for l in group_levels)
            merged.append(best)
        
        return merged
