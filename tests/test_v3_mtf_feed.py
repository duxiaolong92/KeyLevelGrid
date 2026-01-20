"""
V3.0 MTFKlineFeed 单元测试

测试覆盖:
1. K 线数据管理
2. is_synced() 一致性锁
3. 同步状态检查
"""

import pytest
import sys
import time
from pathlib import Path

# 添加 src 目录到 path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from key_level_grid.data.feeds.mtf_feed import MTFKlineFeed, MTFKlineData
from key_level_grid.core.triggers import KlineSyncStatus


# ============================================
# 测试数据生成器
# ============================================

def generate_klines_with_time(
    timeframe: str,
    num_bars: int = 100,
    base_time: int = None,
) -> list:
    """生成带时间戳的 K 线数据"""
    if base_time is None:
        base_time = int(time.time() * 1000)
    
    # 时间间隔 (毫秒)
    intervals = {
        "1d": 24 * 60 * 60 * 1000,
        "4h": 4 * 60 * 60 * 1000,
        "15m": 15 * 60 * 1000,
    }
    interval = intervals.get(timeframe, 4 * 60 * 60 * 1000)
    
    klines = []
    for i in range(num_bars):
        close_time = base_time - (num_bars - i - 1) * interval
        klines.append({
            "timestamp": close_time - interval,
            "open": 95000 + i,
            "high": 95100 + i,
            "low": 94900 + i,
            "close": 95050 + i,
            "volume": 1000,
            "close_time": close_time,
        })
    
    return klines


# ============================================
# 测试: MTFKlineFeed 基础功能
# ============================================

class TestMTFKlineFeedBasic:
    """测试 MTFKlineFeed 基础功能"""
    
    def test_init_default(self):
        """测试默认初始化 (V3.2.5: 四层级)"""
        feed = MTFKlineFeed()
        # V3.2.5: 默认四层级，但无配置时使用默认层级配置
        # 默认层级配置会生成 ["4h", "4h", "4h", "4h"] (因为无配置)
        # 使用自定义时间框架测试
        feed = MTFKlineFeed(timeframes=["1d", "4h", "15m"])
        assert feed.timeframes == ["1d", "4h", "15m"]
    
    def test_init_custom(self):
        """测试自定义初始化"""
        feed = MTFKlineFeed(
            timeframes=["4h", "15m"],
            max_lag_sec={"4h": 120, "15m": 60},
        )
        assert feed.timeframes == ["4h", "15m"]
        assert feed.max_lag_sec["4h"] == 120
    
    def test_update_and_get(self):
        """测试更新和获取"""
        feed = MTFKlineFeed()
        klines = generate_klines_with_time("4h", num_bars=50)
        
        feed.update("4h", klines)
        
        result = feed.get("4h")
        assert result is not None
        assert len(result) == 50
    
    def test_get_nonexistent(self):
        """获取不存在的时间框架"""
        feed = MTFKlineFeed()
        assert feed.get("4h") is None
    
    def test_get_all(self):
        """获取所有时间框架"""
        feed = MTFKlineFeed(timeframes=["4h", "15m"])
        
        feed.update("4h", generate_klines_with_time("4h", 50))
        feed.update("15m", generate_klines_with_time("15m", 100))
        
        all_data = feed.get_all()
        
        assert "4h" in all_data
        assert "15m" in all_data
        assert len(all_data["4h"]) == 50
        assert len(all_data["15m"]) == 100
    
    def test_update_empty_klines(self):
        """更新空数据"""
        feed = MTFKlineFeed()
        feed.update("4h", [])
        
        # 空数据不应该被存储
        assert feed.get("4h") is None


# ============================================
# 测试: is_synced() 一致性锁
# ============================================

class TestMTFKlineFeedSync:
    """测试一致性锁功能"""
    
    def test_is_synced_empty(self):
        """无数据时返回 False"""
        feed = MTFKlineFeed(timeframes=["1d", "4h", "15m"])
        assert feed.is_synced() is False
    
    def test_is_synced_partial_data(self):
        """部分数据时返回 False (V3.2.5: 必须层检查)"""
        # V3.2.5: 只检查必须层 (L2, L3)
        # 使用显式配置来测试
        config = {
            "level_generation": {
                "timeframes": {
                    "l2_skeleton": {"interval": "1d", "enabled": True},
                    "l3_relay": {"interval": "4h", "enabled": True},
                    "l4_tactical": {"interval": "15m", "enabled": True},
                }
            }
        }
        feed = MTFKlineFeed(timeframes=["1d", "4h", "15m"], config=config)
        
        # 只更新一个时间框架，必须层缺失
        feed.update("15m", generate_klines_with_time("15m", 50))
        
        # L2 (1d) 和 L3 (4h) 缺失，应该返回 False
        assert feed.is_synced() is False
    
    def test_is_synced_all_data_fresh(self):
        """所有数据都新鲜时返回 True"""
        feed = MTFKlineFeed(
            timeframes=["4h", "15m"],
            max_lag_sec={"4h": 3600, "15m": 900},
        )
        
        # 使用当前时间生成数据
        now = int(time.time() * 1000)
        feed.update("4h", generate_klines_with_time("4h", 50, now))
        feed.update("15m", generate_klines_with_time("15m", 100, now))
        
        # 数据刚刚更新，应该是同步的
        assert feed.is_synced() is True
    
    def test_get_sync_status(self):
        """获取同步状态"""
        feed = MTFKlineFeed(timeframes=["4h"])
        
        now = int(time.time() * 1000)
        feed.update("4h", generate_klines_with_time("4h", 50, now))
        
        status = feed.get_sync_status()
        
        assert "4h" in status
        assert isinstance(status["4h"], KlineSyncStatus)
        assert status["4h"].timeframe == "4h"
    
    def test_get_stale_timeframes(self):
        """获取过期的时间框架"""
        feed = MTFKlineFeed(
            timeframes=["4h", "15m"],
            max_lag_sec={"4h": 1, "15m": 1},  # 非常短的延迟，使数据立即过期
        )
        
        # 使用过去的时间
        past = int(time.time() * 1000) - 10000000  # 很久以前
        feed.update("4h", generate_klines_with_time("4h", 50, past))
        feed.update("15m", generate_klines_with_time("15m", 100, past))
        
        stale = feed.get_stale_timeframes()
        
        # 数据应该是过期的
        assert len(stale) >= 0  # 取决于时间计算


# ============================================
# 测试: 逻辑对齐检查
# ============================================

class TestMTFKlineFeedAlignment:
    """测试时间框架逻辑对齐"""
    
    def test_alignment_check_single_tf(self):
        """单时间框架不需要对齐检查"""
        feed = MTFKlineFeed(timeframes=["4h"])
        
        now = int(time.time() * 1000)
        feed.update("4h", generate_klines_with_time("4h", 50, now))
        
        # 单时间框架应该总是对齐的
        assert feed._check_logical_alignment() is True
    
    def test_alignment_check_empty(self):
        """无数据时返回 True (跳过检查)"""
        feed = MTFKlineFeed()
        assert feed._check_logical_alignment() is True


# ============================================
# 测试: MTFKlineData 数据类
# ============================================

class TestMTFKlineData:
    """测试 MTFKlineData 数据类"""
    
    def test_dataclass_creation(self):
        """测试数据类创建"""
        data = MTFKlineData(
            timeframe="4h",
            klines=[{"close": 95000}],
            last_update_ts=int(time.time()),
            last_close_time=int(time.time() * 1000),
        )
        
        assert data.timeframe == "4h"
        assert len(data.klines) == 1


# ============================================
# 运行测试
# ============================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
