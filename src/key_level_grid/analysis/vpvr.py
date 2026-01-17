"""
成交量分布分析器 (LEVEL_GENERATION.md v3.1.0)

VPVR (Volume Profile Visible Range) 分析:
- 识别高成交量节点 (HVN): 筹码密集区
- 识别低成交量节点 (LVN): 真空区
- 计算控制价 (POC): 最高成交量价格
"""

from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
import statistics
from key_level_grid.core.scoring import VPVRData, VolumeZone


class VPVRAnalyzer:
    """
    成交量分布分析器
    
    通过分析价格区间内的成交量分布，
    识别支撑/阻力位的能量验证。
    """
    
    def __init__(
        self,
        bucket_count: int = 50,
        hvn_threshold: float = 0.20,  # Top 20% 为 HVN
        lvn_threshold: float = 0.10,  # Bottom 10% 为 LVN
        config: Optional[Dict] = None,
    ):
        """
        初始化 VPVR 分析器
        
        Args:
            bucket_count: 价格分桶数量
            hvn_threshold: HVN 阈值 (占总成交量比例)
            lvn_threshold: LVN 阈值 (占总成交量比例)
            config: 配置字典
        """
        self.bucket_count = bucket_count
        self.hvn_threshold = hvn_threshold
        self.lvn_threshold = lvn_threshold
        self.config = config or {}
    
    def analyze(self, klines: List[Dict]) -> Optional[VPVRData]:
        """
        分析 K 线数据的成交量分布
        
        Args:
            klines: K 线数据 [{"high": x, "low": x, "volume": x}, ...]
        
        Returns:
            VPVRData 或 None (数据不足时)
        """
        if not klines or len(klines) < 10:
            return None
        
        # 计算价格范围
        all_highs = [float(k.get("high", 0)) for k in klines]
        all_lows = [float(k.get("low", float("inf"))) for k in klines]
        
        price_max = max(all_highs)
        price_min = min(all_lows)
        
        if price_max <= price_min:
            return None
        
        # 创建价格桶
        bucket_size = (price_max - price_min) / self.bucket_count
        buckets = [0.0] * self.bucket_count
        
        # 分配成交量到桶
        for kline in klines:
            high = float(kline.get("high", 0))
            low = float(kline.get("low", 0))
            volume = float(kline.get("volume", 0))
            
            if high <= low or volume <= 0:
                continue
            
            # 将成交量均匀分配到覆盖的桶
            start_bucket = int((low - price_min) / bucket_size)
            end_bucket = int((high - price_min) / bucket_size)
            
            start_bucket = max(0, min(start_bucket, self.bucket_count - 1))
            end_bucket = max(0, min(end_bucket, self.bucket_count - 1))
            
            num_buckets = end_bucket - start_bucket + 1
            volume_per_bucket = volume / num_buckets
            
            for i in range(start_bucket, end_bucket + 1):
                buckets[i] += volume_per_bucket
        
        total_volume = sum(buckets)
        if total_volume <= 0:
            return None
        
        # 找 POC (成交量最高的桶)
        poc_bucket = buckets.index(max(buckets))
        poc_price = price_min + (poc_bucket + 0.5) * bucket_size
        
        # 计算阈值
        sorted_volumes = sorted(buckets, reverse=True)
        hvn_vol_threshold = sorted_volumes[int(len(sorted_volumes) * self.hvn_threshold)]
        lvn_vol_threshold = sorted_volumes[int(len(sorted_volumes) * (1 - self.lvn_threshold))]
        
        # 识别 HVN 和 LVN 区间
        hvn_zones = []
        lvn_zones = []
        
        i = 0
        while i < self.bucket_count:
            bucket_vol = buckets[i]
            bucket_low = price_min + i * bucket_size
            bucket_high = bucket_low + bucket_size
            
            if bucket_vol >= hvn_vol_threshold:
                # 找连续的 HVN
                zone_start = bucket_low
                while i < self.bucket_count and buckets[i] >= hvn_vol_threshold:
                    i += 1
                zone_end = price_min + i * bucket_size
                hvn_zones.append((zone_start, zone_end))
            elif bucket_vol <= lvn_vol_threshold:
                # 找连续的 LVN
                zone_start = bucket_low
                while i < self.bucket_count and buckets[i] <= lvn_vol_threshold:
                    i += 1
                zone_end = price_min + i * bucket_size
                lvn_zones.append((zone_start, zone_end))
            else:
                i += 1
        
        return VPVRData(
            poc_price=poc_price,
            hvn_zones=hvn_zones,
            lvn_zones=lvn_zones,
            total_volume=total_volume,
            price_range=(price_min, price_max),
        )
    
    def get_volume_weight(
        self,
        price: float,
        vpvr: VPVRData,
    ) -> Tuple[float, VolumeZone]:
        """
        获取指定价格的成交量权重
        
        Args:
            price: 目标价格
            vpvr: VPVR 分析结果
        
        Returns:
            (权重, 区域类型)
            - HVN: 1.3
            - Normal: 1.0
            - LVN: 0.6
        """
        # 从配置读取权重
        weights = self.config.get("scoring", {}).get("volume_weights", {})
        hvn_weight = float(weights.get("hvn", 1.3))
        normal_weight = float(weights.get("normal", 1.0))
        lvn_weight = float(weights.get("lvn", 0.6))
        
        zone_type = vpvr.get_zone_type(price)
        
        if zone_type == VolumeZone.HVN:
            return hvn_weight, zone_type
        elif zone_type == VolumeZone.LVN:
            return lvn_weight, zone_type
        else:
            return normal_weight, zone_type
    
    def is_near_poc(
        self,
        price: float,
        vpvr: VPVRData,
        tolerance: float = 0.01,  # 1% 容差
    ) -> bool:
        """
        判断价格是否接近 POC
        
        Args:
            price: 目标价格
            vpvr: VPVR 分析结果
            tolerance: 容差百分比
        
        Returns:
            True if near POC
        """
        if vpvr.poc_price <= 0:
            return False
        
        return abs(price - vpvr.poc_price) / vpvr.poc_price <= tolerance
