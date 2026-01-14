#!/usr/bin/env python
"""
关键价位计算 CLI 工具

支持加密货币和美股的支撑/阻力位计算

使用示例:
    python scripts/calc_levels.py TSLA 4h 1d              # 美股 TSLA
    python scripts/calc_levels.py BTCUSDT 4h 1d           # 币圈 BTC
    python scripts/calc_levels.py AAPL 1d --count 5       # 美股 AAPL，仅显示 5 个
    python scripts/calc_levels.py ETHUSDT 1h 4h 1d        # 币圈 ETH，多周期
    python scripts/calc_levels.py NVDA 4h --output json   # JSON 格式输出
"""

import argparse
import asyncio
import json
import os
import sys
from typing import List, Optional

import yaml

# 添加项目路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from dotenv import load_dotenv
load_dotenv()

from key_level_grid.models import Kline, Timeframe
from key_level_grid.resistance import ResistanceCalculator, ResistanceConfig
from key_level_grid.utils.logger import get_logger

logger = get_logger(__name__)


def detect_source(symbol: str) -> str:
    """
    自动检测数据源类型
    
    规则:
    - 包含 USDT/USD/BTC/ETH 后缀 → 币圈 (gate)
    - 纯字母 1~5 位 → 美股 (polygon)
    """
    symbol_upper = symbol.upper()
    
    # 币圈标识 → 使用 Gate 期货
    crypto_suffixes = ["USDT", "USD", "BTC", "ETH", "BUSD", "USDC"]
    for suffix in crypto_suffixes:
        if symbol_upper.endswith(suffix):
            return "gate"
    
    # 纯字母且长度 1-5 → 美股
    if symbol_upper.isalpha() and 1 <= len(symbol_upper) <= 5:
        return "polygon"
    
    # 默认尝试币圈 (Gate)
    return "gate"


async def fetch_gate_klines(symbol: str, timeframes: List[str], limit: int = 500) -> dict:
    """获取 Gate.io 期货 K 线数据"""
    from key_level_grid.gate_kline_feed import GateKlineFeed
    from key_level_grid.models import KlineFeedConfig
    
    # 转换周期
    primary_tf = Timeframe.from_string(timeframes[0])
    aux_tfs = [Timeframe.from_string(tf) for tf in timeframes[1:]] if len(timeframes) > 1 else []
    
    config = KlineFeedConfig(
        symbol=symbol.upper(),
        primary_timeframe=primary_tf,
        auxiliary_timeframes=aux_tfs,
        history_bars=limit,
    )
    
    feed = GateKlineFeed(config)
    await feed.start()
    
    result = {}
    try:
        # 获取主周期
        klines = await feed.get_latest_klines(primary_tf)
        result[timeframes[0]] = klines
        
        # 获取辅助周期
        for tf_str in timeframes[1:]:
            tf = Timeframe.from_string(tf_str)
            klines = feed.get_cached_klines(tf)
            result[tf_str] = klines
    finally:
        await feed.stop()
    
    return result


async def fetch_polygon_klines(symbol: str, timeframes: List[str], limit: int = 500) -> dict:
    """获取 Polygon 美股 K 线数据"""
    from key_level_grid.polygon_kline_feed import PolygonKlineFeed
    
    feed = PolygonKlineFeed(symbol)
    await feed.start()
    
    result = {}
    try:
        for tf_str in timeframes:
            tf = Timeframe.from_string(tf_str)
            klines = await feed.get_klines(tf, limit)
            result[tf_str] = klines
    finally:
        await feed.stop()
    
    return result


def load_resistance_config_from_yaml() -> dict:
    """从配置文件加载阻力位相关配置"""
    config_path = os.path.join(os.path.dirname(__file__), "..", "configs", "config.yaml")
    
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            raw_config = yaml.safe_load(f)
        return raw_config.get("resistance", {})
    except Exception as e:
        logger.warning(f"无法加载配置文件: {e}，使用默认值")
        return {}


def calculate_levels(
    klines_dict: dict,
    current_price: float,
    min_strength: int = 60,
    count: int = 10,
) -> dict:
    """
    计算支撑/阻力位（支持 1~3 个周期）
    
    Args:
        klines_dict: {timeframe: [Kline, ...]}，如 {"4h": [...], "1d": [...]}
        current_price: 当前价格
        min_strength: 最低强度阈值
        count: 返回数量
        
    Returns:
        {"resistance": [...], "support": [...], "current_price": ...}
    """
    # 从配置文件加载参数
    resistance_raw = load_resistance_config_from_yaml()
    
    config = ResistanceConfig(
        swing_lookbacks=resistance_raw.get('swing_lookbacks', [5, 13, 34]),
        fib_ratios=resistance_raw.get('fib_ratios', [0.382, 0.5, 0.618, 1.0, 1.618]),
        merge_tolerance=resistance_raw.get('merge_tolerance', 0.005),
        min_distance_pct=resistance_raw.get('min_distance_pct', 0.005),
        max_distance_pct=resistance_raw.get('max_distance_pct', 0.30),
    )
    calculator = ResistanceCalculator(config)
    
    # 获取周期列表（限制最多 3 个）
    timeframes = list(klines_dict.keys())[:3]
    if not timeframes:
        return {"resistance": [], "support": [], "current_price": current_price}
    
    # 检查是否有有效数据
    primary_tf = timeframes[0]
    primary_klines = klines_dict.get(primary_tf, [])
    if not primary_klines:
        return {"resistance": [], "support": [], "current_price": current_price}
    
    # 使用新的多周期接口
    # 计算阻力位
    resistances = calculator.calculate_resistance_levels(
        current_price=current_price,
        klines=primary_klines,  # 向后兼容参数
        direction="long",
        klines_by_timeframe=klines_dict,  # 新的多周期参数
    )
    
    # 计算支撑位
    supports = calculator.calculate_support_levels(
        current_price=current_price,
        klines=primary_klines,  # 向后兼容参数
        klines_by_timeframe=klines_dict,  # 新的多周期参数
    )
    
    # 过滤低强度并格式化结果
    resistance_list = [
        {
            "price": r.price,
            "strength": r.strength,
            "type": r.level_type.value if hasattr(r.level_type, 'value') else str(r.level_type),
            "source": getattr(r, 'source', ''),
            "timeframe": getattr(r, 'timeframe', ''),
            "description": getattr(r, 'description', ''),
            "distance_pct": (r.price - current_price) / current_price * 100,
        }
        for r in resistances if r.strength >= min_strength
    ][:count]
    
    support_list = [
        {
            "price": s.price,
            "strength": s.strength,
            "type": s.level_type.value if hasattr(s.level_type, 'value') else str(s.level_type),
            "source": getattr(s, 'source', ''),
            "timeframe": getattr(s, 'timeframe', ''),
            "description": getattr(s, 'description', ''),
            "distance_pct": (current_price - s.price) / current_price * 100,
        }
        for s in supports if s.strength >= min_strength
    ][:count]
    
    return {
        "resistance": resistance_list,
        "support": support_list,
        "current_price": current_price,
    }


def format_output_table(symbol: str, timeframes: List[str], result: dict) -> str:
    """格式化为表格输出"""
    current_price = result["current_price"]
    resistance = result["resistance"]
    support = result["support"]
    
    # 来源简写映射
    source_map = {
        "swing_5": "SW5", "swing_13": "SW13", "swing_21": "SW21", "swing_34": "SW34",
        "volume_node": "VOL",
        "round_number": "PSY",
    }
    
    # 周期简写映射
    tf_map = {
        "1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m",
        "1h": "1H", "4h": "4H", "1d": "1D", "1w": "1W",
        "multi": "MTF",  # 多周期融合
    }
    
    def format_source(source: str) -> str:
        """格式化来源（支持复合来源如 swing_5+volume_node）"""
        if not source:
            return "?"
        
        parts = source.split("+")
        abbrs = []
        for p in parts:
            p = p.strip()
            if p.startswith("swing_"):
                abbrs.append(f"SW{p.replace('swing_', '')}")
            elif p.startswith("fib_"):
                abbrs.append(f"FIB{p.replace('fib_', '')}")
            elif p in source_map:
                abbrs.append(source_map[p])
            else:
                abbrs.append(p[:3].upper())
        return "+".join(abbrs)
    
    def format_timeframe(tf: str) -> str:
        return tf_map.get(tf, tf.upper() if tf else "?")
    
    lines = [
        f"📍 {symbol.upper()} 关键价位分析（{' + '.join(timeframes)}）",
        "",
        f"当前价: ${current_price:,.2f}",
        "",
        f"阻力位 ({len(resistance)}):",
    ]
    
    # 阻力位按价格降序
    for i, r in enumerate(sorted(resistance, key=lambda x: -x["price"])):
        source_abbr = format_source(r.get("source", ""))
        tf_abbr = format_timeframe(r.get("timeframe", ""))
        lines.append(
            f"├ R{i+1}: ${r['price']:,.2f} (+{r['distance_pct']:.1f}%) [{source_abbr}] {tf_abbr} 💪{r['strength']:.0f}"
        )
    
    lines.append("")
    lines.append(f"支撑位 ({len(support)}):")
    
    # 支撑位按价格降序
    for i, s in enumerate(sorted(support, key=lambda x: -x["price"])):
        source_abbr = format_source(s.get("source", ""))
        tf_abbr = format_timeframe(s.get("timeframe", ""))
        lines.append(
            f"├ S{i+1}: ${s['price']:,.2f} (-{s['distance_pct']:.1f}%) [{source_abbr}] {tf_abbr} 💪{s['strength']:.0f}"
        )
    
    lines.append("")
    lines.append("来源: SW=摆动点 FIB=斐波那契 PSY=心理关口 VOL=成交密集区 | 周期: MTF=多周期融合")
    
    return "\n".join(lines)


async def main():
    parser = argparse.ArgumentParser(
        description="关键价位计算工具 - 支持加密货币和美股",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python scripts/calc_levels.py TSLA 4h 1d          # 美股 TSLA
  python scripts/calc_levels.py BTCUSDT 4h 1d       # 币圈 BTC
  python scripts/calc_levels.py AAPL 1d --count 5   # 仅显示 5 个
  python scripts/calc_levels.py NVDA 4h --output json
        """
    )
    
    parser.add_argument(
        "symbol",
        help="标的代码（如 TSLA, BTCUSDT, AAPL）"
    )
    parser.add_argument(
        "timeframes",
        nargs="+",
        help="K线周期（如 4h 1d），第一个为主周期"
    )
    parser.add_argument(
        "--min-strength",
        type=int,
        default=60,
        help="最低强度阈值（默认 60）"
    )
    parser.add_argument(
        "--count",
        type=int,
        default=10,
        help="返回数量（默认 10）"
    )
    parser.add_argument(
        "--output",
        choices=["table", "json"],
        default="table",
        help="输出格式（默认 table）"
    )
    parser.add_argument(
        "--source",
        choices=["gate", "polygon", "auto"],
        default="auto",
        help="数据源（默认 auto 自动检测，币圈用 Gate 期货，美股用 Polygon）"
    )
    
    args = parser.parse_args()
    
    symbol = args.symbol.upper()
    timeframes = [tf.lower() for tf in args.timeframes]
    
    # 检测数据源
    if args.source == "auto":
        source = detect_source(symbol)
    else:
        source = args.source
    
    print(f"⏳ 正在获取 {symbol} K线数据（{source}）...")
    
    try:
        # 获取 K 线数据
        if source == "gate":
            klines_dict = await fetch_gate_klines(symbol, timeframes)
        else:
            klines_dict = await fetch_polygon_klines(symbol, timeframes)
        
        # 检查数据
        primary_klines = klines_dict.get(timeframes[0], [])
        if not primary_klines:
            print(f"❌ 未获取到 {symbol} 的 K 线数据")
            return 1
        
        # 获取当前价格
        current_price = primary_klines[-1].close
        
        print(f"✅ 获取到 {len(primary_klines)} 条 K 线，当前价: ${current_price:,.2f}")
        print()
        
        # 计算价位
        result = calculate_levels(
            klines_dict=klines_dict,
            current_price=current_price,
            min_strength=args.min_strength,
            count=args.count,
        )
        
        # 输出结果
        if args.output == "json":
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            print(format_output_table(symbol, timeframes, result))
        
        return 0
        
    except Exception as e:
        print(f"❌ 错误: {e}")
        logger.error(f"计算失败: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
