"""
V3.0 MTF 水位生成回测脚本

功能:
1. 加载历史 K 线数据
2. 使用 LevelCalculator 生成水位
3. 分析水位有效性 (价格是否触及)
4. 对比 V2 vs V3 水位质量

使用方法:
    python scripts/backtest/v3_level_backtest.py --symbol BTCUSDT --days 30
"""

import sys
import json
import argparse
import logging
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field

# 添加 src 目录到 path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from key_level_grid.level_calculator import LevelCalculator
from key_level_grid.analysis.resistance import ResistanceCalculator
from key_level_grid.core.config import ResistanceConfig
from key_level_grid.core.scoring import LevelScore


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


# ============================================
# 数据结构
# ============================================

@dataclass
class LevelPerformance:
    """水位表现统计"""
    price: float
    score: float
    source_timeframes: List[str]
    is_resonance: bool
    
    # 表现统计
    touched: bool = False           # 是否被触及
    touch_count: int = 0            # 触及次数
    bounced: bool = False           # 是否反弹
    bounce_pct: float = 0.0         # 反弹幅度
    broke_through: bool = False     # 是否突破
    
    # 时间统计
    first_touch_bars: int = 0       # 首次触及的 K 线数
    total_bars: int = 0             # 总 K 线数


@dataclass
class BacktestResult:
    """回测结果"""
    symbol: str
    start_date: str
    end_date: str
    total_bars: int
    
    # V3.0 统计
    v3_levels_count: int = 0
    v3_touched_count: int = 0
    v3_bounced_count: int = 0
    v3_avg_score: float = 0.0
    v3_resonance_count: int = 0
    v3_levels: List[LevelPerformance] = field(default_factory=list)
    
    # V2.0 统计 (对比用)
    v2_levels_count: int = 0
    v2_touched_count: int = 0
    v2_bounced_count: int = 0
    
    # 综合评估
    v3_hit_rate: float = 0.0        # 触及率
    v3_bounce_rate: float = 0.0     # 反弹率
    v2_hit_rate: float = 0.0
    v2_bounce_rate: float = 0.0


# ============================================
# 回测引擎
# ============================================

class V3LevelBacktester:
    """V3.0 水位回测器"""
    
    def __init__(self, config: Optional[Dict] = None):
        """
        初始化回测器
        
        Args:
            config: 配置字典
        """
        self.config = config or self._get_default_config()
        
        # 初始化 V3.0 LevelCalculator
        self.level_calculator = LevelCalculator(self.config)
        
        # 初始化 V2.0 ResistanceCalculator (对比用)
        resistance_config = ResistanceConfig(
            swing_lookbacks=[5, 13, 34],
            fib_ratios=[0.382, 0.5, 0.618, 1.0, 1.618],
            merge_tolerance=0.005,
            min_distance_pct=0.001,
            max_distance_pct=0.30,
        )
        self.resistance_calculator = ResistanceCalculator(resistance_config)
        
        # 回测参数
        self.bounce_threshold = 0.005  # 0.5% 视为反弹
        self.touch_tolerance = 0.001   # 0.1% 触及容差
    
    def _get_default_config(self) -> Dict:
        """获取默认配置"""
        return {
            "level_generation": {
                "fibonacci_lookback": [8, 21, 55],
                "timeframes": ["1d", "4h", "15m"],
                "score_thresholds": {
                    "mtf_resonance": 100,
                    "strong": 60,
                    "normal": 30,
                },
            },
            "resistance": {
                "min_distance_pct": 0.001,
                "max_distance_pct": 0.30,
                "merge_tolerance": 0.005,
            },
        }
    
    def run_backtest(
        self,
        klines_by_tf: Dict[str, List[Dict]],
        symbol: str = "BTCUSDT",
        lookback_bars: int = 100,
        forward_bars: int = 50,
    ) -> BacktestResult:
        """
        运行回测
        
        Args:
            klines_by_tf: 多时间框架 K 线数据
            symbol: 交易对
            lookback_bars: 用于生成水位的回溯 K 线数
            forward_bars: 用于验证水位的前向 K 线数
        
        Returns:
            BacktestResult
        """
        main_tf = "4h"
        main_klines = klines_by_tf.get(main_tf, [])
        
        if not main_klines or len(main_klines) < lookback_bars + forward_bars:
            logger.error("K 线数据不足")
            return BacktestResult(
                symbol=symbol,
                start_date="",
                end_date="",
                total_bars=0,
            )
        
        # 分割数据
        train_klines = {
            tf: klines[:lookback_bars] for tf, klines in klines_by_tf.items()
        }
        test_klines = main_klines[lookback_bars:lookback_bars + forward_bars]
        
        # 获取当前价格 (训练数据最后一根 K 线收盘价)
        current_price = float(main_klines[lookback_bars - 1]["close"])
        
        logger.info(f"回测配置: lookback={lookback_bars}, forward={forward_bars}, price={current_price:.2f}")
        
        # 生成 V3.0 水位
        v3_levels = self._generate_v3_levels(train_klines, current_price)
        
        # 生成 V2.0 水位
        v2_levels = self._generate_v2_levels(train_klines[main_tf], current_price)
        
        # 验证水位表现
        v3_performances = self._evaluate_levels(
            levels=v3_levels,
            test_klines=test_klines,
            current_price=current_price,
        )
        
        v2_performances = self._evaluate_levels(
            levels=v2_levels,
            test_klines=test_klines,
            current_price=current_price,
        )
        
        # 计算统计
        result = self._calculate_stats(
            symbol=symbol,
            main_klines=main_klines,
            v3_performances=v3_performances,
            v2_performances=v2_performances,
            forward_bars=forward_bars,
        )
        
        return result
    
    def _generate_v3_levels(
        self,
        klines_by_tf: Dict[str, List[Dict]],
        current_price: float,
    ) -> List[Tuple[float, LevelScore]]:
        """生成 V3.0 水位"""
        levels = self.level_calculator.generate_target_levels(
            klines_by_tf=klines_by_tf,
            current_price=current_price,
            role="support",
            max_levels=10,
        )
        return levels or []
    
    def _generate_v2_levels(
        self,
        klines: List[Dict],
        current_price: float,
    ) -> List[Tuple[float, LevelScore]]:
        """生成 V2.0 水位 (用于对比)"""
        try:
            # 转换 K 线格式
            from key_level_grid.models import Kline
            kline_objs = [
                Kline(
                    timestamp=k.get("timestamp", 0),
                    open=k.get("open", 0),
                    high=k.get("high", 0),
                    low=k.get("low", 0),
                    close=k.get("close", 0),
                    volume=k.get("volume", 0),
                )
                for k in klines
            ]
            
            support_levels = self.resistance_calculator.calculate_support_levels(
                current_price=current_price,
                klines={"4h": kline_objs},
            )
            
            # 转换为统一格式
            levels = []
            for lvl in support_levels[:10]:
                if lvl.price < current_price:
                    score = LevelScore(
                        base_score=lvl.strength,
                        source_timeframes=["4h"],
                        final_score=lvl.strength,
                    )
                    levels.append((lvl.price, score))
            
            return levels
        except Exception as e:
            logger.warning(f"V2.0 水位生成失败: {e}")
            return []
    
    def _evaluate_levels(
        self,
        levels: List[Tuple[float, LevelScore]],
        test_klines: List[Dict],
        current_price: float,
    ) -> List[LevelPerformance]:
        """评估水位表现"""
        performances = []
        
        for price, score in levels:
            perf = LevelPerformance(
                price=price,
                score=score.final_score,
                source_timeframes=score.source_timeframes,
                is_resonance=score.is_resonance,
                total_bars=len(test_klines),
            )
            
            # 检查是否被触及
            for i, kline in enumerate(test_klines):
                low = float(kline.get("low", float("inf")))
                high = float(kline.get("high", 0))
                close = float(kline.get("close", 0))
                
                # 触及判断
                touch_price = price * (1 + self.touch_tolerance)
                if low <= touch_price:
                    if not perf.touched:
                        perf.touched = True
                        perf.first_touch_bars = i + 1
                    perf.touch_count += 1
                    
                    # 反弹判断 (触及后收盘价高于水位)
                    if close > price * (1 + self.bounce_threshold):
                        perf.bounced = True
                        perf.bounce_pct = max(
                            perf.bounce_pct,
                            (close - price) / price * 100
                        )
                    
                    # 突破判断 (收盘价低于水位)
                    if close < price * (1 - self.touch_tolerance):
                        perf.broke_through = True
            
            performances.append(perf)
        
        return performances
    
    def _calculate_stats(
        self,
        symbol: str,
        main_klines: List[Dict],
        v3_performances: List[LevelPerformance],
        v2_performances: List[LevelPerformance],
        forward_bars: int,
    ) -> BacktestResult:
        """计算统计指标"""
        # 日期范围
        start_ts = main_klines[0].get("timestamp", 0)
        end_ts = main_klines[-1].get("timestamp", 0)
        start_date = datetime.fromtimestamp(start_ts / 1000).strftime("%Y-%m-%d") if start_ts else ""
        end_date = datetime.fromtimestamp(end_ts / 1000).strftime("%Y-%m-%d") if end_ts else ""
        
        # V3 统计
        v3_touched = sum(1 for p in v3_performances if p.touched)
        v3_bounced = sum(1 for p in v3_performances if p.bounced)
        v3_resonance = sum(1 for p in v3_performances if p.is_resonance)
        v3_avg_score = (
            sum(p.score for p in v3_performances) / len(v3_performances)
            if v3_performances else 0
        )
        
        # V2 统计
        v2_touched = sum(1 for p in v2_performances if p.touched)
        v2_bounced = sum(1 for p in v2_performances if p.bounced)
        
        return BacktestResult(
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            total_bars=forward_bars,
            # V3
            v3_levels_count=len(v3_performances),
            v3_touched_count=v3_touched,
            v3_bounced_count=v3_bounced,
            v3_avg_score=v3_avg_score,
            v3_resonance_count=v3_resonance,
            v3_levels=v3_performances,
            v3_hit_rate=v3_touched / len(v3_performances) * 100 if v3_performances else 0,
            v3_bounce_rate=v3_bounced / v3_touched * 100 if v3_touched else 0,
            # V2
            v2_levels_count=len(v2_performances),
            v2_touched_count=v2_touched,
            v2_bounced_count=v2_bounced,
            v2_hit_rate=v2_touched / len(v2_performances) * 100 if v2_performances else 0,
            v2_bounce_rate=v2_bounced / v2_touched * 100 if v2_touched else 0,
        )


# ============================================
# 模拟数据生成 (用于测试)
# ============================================

def generate_mock_klines(
    days: int = 30,
    base_price: float = 95000,
    volatility: float = 0.02,
) -> Dict[str, List[Dict]]:
    """
    生成模拟 K 线数据 (带有典型市场波动结构)
    
    Args:
        days: 天数
        base_price: 基准价格
        volatility: 波动率
    
    Returns:
        {"1d": [...], "4h": [...], "15m": [...]}
    """
    import random
    import math
    random.seed(42)
    
    result = {}
    
    # 1d K 线 - 生成有周期性的数据
    num_1d = days
    result["1d"] = _generate_wave_klines(
        num_bars=num_1d,
        interval_ms=24 * 60 * 60 * 1000,
        base_price=base_price,
        volatility=volatility,
        wave_period=7,  # 7 天一个周期
    )
    
    # 4h K 线
    num_4h = days * 6
    result["4h"] = _generate_wave_klines(
        num_bars=num_4h,
        interval_ms=4 * 60 * 60 * 1000,
        base_price=base_price,
        volatility=volatility * 0.6,
        wave_period=42,  # 7 天 = 42 根 4h K 线
    )
    
    # 15m K 线
    num_15m = days * 96
    result["15m"] = _generate_wave_klines(
        num_bars=num_15m,
        interval_ms=15 * 60 * 1000,
        base_price=base_price,
        volatility=volatility * 0.3,
        wave_period=192,  # 2 天周期
    )
    
    return result


def _generate_wave_klines(
    num_bars: int,
    interval_ms: int,
    base_price: float,
    volatility: float,
    wave_period: int = 20,
) -> List[Dict]:
    """
    生成带波浪结构的 K 线数据
    
    创建典型的市场结构:
    - 上涨趋势 -> 回调 -> 继续上涨 -> 下跌
    - 形成明显的摆动高低点
    """
    import random
    import math
    import time
    
    klines = []
    now = int(time.time() * 1000)
    
    # 生成价格序列 (正弦波 + 趋势 + 噪声)
    prices = []
    trend = 0.0001  # 轻微上涨趋势
    
    for i in range(num_bars + 1):
        # 主波浪 (大周期)
        wave1 = math.sin(2 * math.pi * i / wave_period) * volatility * 3
        # 次波浪 (小周期)
        wave2 = math.sin(2 * math.pi * i / (wave_period / 3)) * volatility * 1.5
        # 趋势
        trend_component = i * trend
        # 随机噪声
        noise = random.gauss(0, volatility * 0.5)
        
        price_factor = 1 + wave1 + wave2 + trend_component + noise
        prices.append(base_price * price_factor)
    
    # 生成 K 线
    for i in range(num_bars):
        close_time = now - (num_bars - i - 1) * interval_ms
        
        open_price = prices[i]
        close_price = prices[i + 1]
        
        # 生成 high/low (确保包含 open/close)
        price_range = abs(close_price - open_price)
        extra_wick = random.uniform(0, price_range * 0.5 + volatility * base_price * 0.3)
        
        high_price = max(open_price, close_price) + extra_wick * random.uniform(0.3, 1.0)
        low_price = min(open_price, close_price) - extra_wick * random.uniform(0.3, 1.0)
        
        # 成交量 (价格剧烈波动时成交量增加)
        volume_base = 500
        volume_spike = abs(close_price - open_price) / open_price * 10000
        volume = volume_base + volume_spike + random.uniform(0, 200)
        
        klines.append({
            "timestamp": close_time - interval_ms,
            "open": open_price,
            "high": high_price,
            "low": low_price,
            "close": close_price,
            "volume": volume,
            "close_time": close_time,
        })
    
    return klines


# ============================================
# 报告生成
# ============================================

def print_report(result: BacktestResult) -> None:
    """打印回测报告"""
    print("\n" + "=" * 60)
    print("📊 V3.0 MTF 水位生成回测报告")
    print("=" * 60)
    print(f"交易对: {result.symbol}")
    print(f"日期范围: {result.start_date} ~ {result.end_date}")
    print(f"验证周期: {result.total_bars} 根 K 线")
    print()
    
    print("📈 V3.0 (MTF 评分) 统计:")
    print(f"  - 生成水位数: {result.v3_levels_count}")
    print(f"  - 触及水位数: {result.v3_touched_count} ({result.v3_hit_rate:.1f}%)")
    print(f"  - 有效反弹数: {result.v3_bounced_count} ({result.v3_bounce_rate:.1f}%)")
    print(f"  - 平均评分: {result.v3_avg_score:.1f}")
    print(f"  - 共振水位数: {result.v3_resonance_count}")
    print()
    
    print("📉 V2.0 (传统阻力位) 统计:")
    print(f"  - 生成水位数: {result.v2_levels_count}")
    print(f"  - 触及水位数: {result.v2_touched_count} ({result.v2_hit_rate:.1f}%)")
    print(f"  - 有效反弹数: {result.v2_bounced_count} ({result.v2_bounce_rate:.1f}%)")
    print()
    
    # 对比
    print("🔍 V3 vs V2 对比:")
    hit_diff = result.v3_hit_rate - result.v2_hit_rate
    bounce_diff = result.v3_bounce_rate - result.v2_bounce_rate
    print(f"  - 触及率差异: {hit_diff:+.1f}%")
    print(f"  - 反弹率差异: {bounce_diff:+.1f}%")
    
    if hit_diff > 0:
        print("  ✅ V3.0 水位触及率更高")
    if bounce_diff > 0:
        print("  ✅ V3.0 水位反弹率更高")
    
    # 详细水位列表
    print()
    print("📋 V3.0 水位详情:")
    print("-" * 60)
    print(f"{'价格':>12} {'评分':>8} {'来源':>12} {'触及':>6} {'反弹':>6} {'反弹%':>8}")
    print("-" * 60)
    for lvl in result.v3_levels:
        touched = "✓" if lvl.touched else "-"
        bounced = "✓" if lvl.bounced else "-"
        sources = ",".join(lvl.source_timeframes)
        print(f"{lvl.price:>12.2f} {lvl.score:>8.1f} {sources:>12} {touched:>6} {bounced:>6} {lvl.bounce_pct:>7.2f}%")
    
    print("=" * 60)


# ============================================
# 主入口
# ============================================

def main():
    parser = argparse.ArgumentParser(description="V3.0 MTF 水位生成回测")
    parser.add_argument("--symbol", default="BTCUSDT", help="交易对")
    parser.add_argument("--days", type=int, default=30, help="回测天数")
    parser.add_argument("--lookback", type=int, default=100, help="生成水位的回溯 K 线数")
    parser.add_argument("--forward", type=int, default=50, help="验证水位的前向 K 线数")
    parser.add_argument("--output", help="输出 JSON 文件路径")
    
    args = parser.parse_args()
    
    logger.info(f"开始回测: {args.symbol}, 天数={args.days}")
    
    # 生成模拟数据 (实际使用时应从交易所获取)
    klines_by_tf = generate_mock_klines(days=args.days)
    
    # 运行回测
    backtester = V3LevelBacktester()
    result = backtester.run_backtest(
        klines_by_tf=klines_by_tf,
        symbol=args.symbol,
        lookback_bars=args.lookback,
        forward_bars=args.forward,
    )
    
    # 打印报告
    print_report(result)
    
    # 保存 JSON
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        # 转换为可序列化格式
        result_dict = {
            "symbol": result.symbol,
            "start_date": result.start_date,
            "end_date": result.end_date,
            "total_bars": result.total_bars,
            "v3": {
                "levels_count": result.v3_levels_count,
                "touched_count": result.v3_touched_count,
                "bounced_count": result.v3_bounced_count,
                "avg_score": result.v3_avg_score,
                "resonance_count": result.v3_resonance_count,
                "hit_rate": result.v3_hit_rate,
                "bounce_rate": result.v3_bounce_rate,
            },
            "v2": {
                "levels_count": result.v2_levels_count,
                "touched_count": result.v2_touched_count,
                "bounced_count": result.v2_bounced_count,
                "hit_rate": result.v2_hit_rate,
                "bounce_rate": result.v2_bounce_rate,
            },
        }
        
        with open(output_path, "w") as f:
            json.dump(result_dict, f, indent=2)
        
        logger.info(f"结果已保存到: {output_path}")


if __name__ == "__main__":
    main()
