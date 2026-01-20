#!/usr/bin/env python
"""
V3.2.5 水位计算 CLI 工具

快速查看指定币种的支撑/阻力位

使用示例:
    # 使用 V3.2.5 配置查看 BNB 水位
    python scripts/tools/calc_levels.py BNBUSDT
    
    # 使用指定配置文件
    python scripts/tools/calc_levels.py BTCUSDT --config configs/config_v3_staging.yaml
    
    # JSON 格式输出
    python scripts/tools/calc_levels.py ETHUSDT --output json
    
    # 指定数量和最低评分
    python scripts/tools/calc_levels.py BNBUSDT --count 15 --min-score 30
"""

import argparse
import asyncio
import json
import os
import sys
from typing import Dict, List, Optional

import yaml

# 添加项目路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass  # 忽略 .env 加载失败

from key_level_grid.level_calculator import LevelCalculator
from key_level_grid.data.feeds.gate import GateKlineFeed
from key_level_grid.core.models import Timeframe
from key_level_grid.utils.logger import get_logger

logger = get_logger(__name__)

# 默认配置文件路径
DEFAULT_CONFIG = os.path.join(
    os.path.dirname(__file__), "..", "..", "configs", "config_v3_staging.yaml"
)


def load_config(config_path: str) -> dict:
    """加载配置文件"""
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


async def fetch_klines(
    symbol: str,
    timeframes: List[str],
    limit: int = 500,
) -> Dict[str, List[dict]]:
    """
    获取多时间框架 K 线数据
    
    Args:
        symbol: 交易对 (如 BNBUSDT)
        timeframes: 时间框架列表 (如 ["1d", "4h", "15m"])
        limit: K 线数量
    
    Returns:
        {timeframe: [kline_dict, ...]}
    """
    from key_level_grid.models import KlineFeedConfig
    from key_level_grid.gate_kline_feed import GateKlineFeed as OldGateKlineFeed
    
    result = {}
    
    for tf_str in timeframes:
        try:
            tf = Timeframe.from_string(tf_str)
            
            config = KlineFeedConfig(
                symbol=symbol.upper(),
                primary_timeframe=tf,
                auxiliary_timeframes=[],
                history_bars=limit,
            )
            
            feed = OldGateKlineFeed(config)
            await feed.start()
            
            try:
                klines = await feed.get_latest_klines(tf)
                # 转换为 dict 格式
                result[tf_str] = [
                    {
                        "open": k.open,
                        "high": k.high,
                        "low": k.low,
                        "close": k.close,
                        "volume": k.volume,
                        "timestamp": k.timestamp,
                    }
                    for k in klines
                ]
                print(f"  ✅ {tf_str}: {len(result[tf_str])} 根 K 线")
            finally:
                await feed.stop()
                
        except Exception as e:
            print(f"  ⚠️ {tf_str}: 获取失败 - {e}")
            result[tf_str] = []
    
    return result


def format_table(
    symbol: str,
    current_price: float,
    supports: List[tuple],
    resistances: List[tuple],
    config: dict,
) -> str:
    """格式化为表格输出"""
    
    # 获取配置信息
    level_gen = config.get("grid", {}).get("level_generation", {})
    timeframes = level_gen.get("timeframes", {})
    
    lines = [
        "=" * 70,
        f"📊 {symbol} V3.2.5 水位分析",
        "=" * 70,
        f"当前价格: ${current_price:,.2f}",
        "",
    ]
    
    # 配置信息
    lines.append("⚙️ 配置:")
    enabled_tfs = []
    for layer, cfg in timeframes.items():
        if cfg.get("enabled", True):
            enabled_tfs.append(f"{layer}({cfg.get('interval', '?')})")
    lines.append(f"  时间框架: {', '.join(enabled_tfs)}")
    
    atr = level_gen.get("atr_constraint", {})
    if atr.get("enabled"):
        lines.append(f"  ATR 约束: {atr.get('gap_min_atr_ratio', 0.5)}x ~ {atr.get('gap_max_atr_ratio', 3.0)}x")
    
    lines.append("")
    
    # 阻力位
    lines.append(f"📈 阻力位 ({len(resistances)}):")
    lines.append(f"{'价格':>12} | {'评分':>6} | {'来源':>15} | 距当前")
    lines.append("-" * 55)
    
    if resistances:
        for price, score in resistances[:15]:
            dist_pct = (price - current_price) / current_price * 100
            source = "+".join(score.source_timeframes) if score.source_timeframes else "?"
            lines.append(f"{price:>12.2f} | {score.final_score:>6.1f} | {source:>15} | +{dist_pct:.2f}%")
    else:
        lines.append("  (无)")
    
    lines.append("")
    
    # 支撑位
    lines.append(f"📉 支撑位 ({len(supports)}):")
    lines.append(f"{'价格':>12} | {'评分':>6} | {'来源':>15} | 距当前")
    lines.append("-" * 55)
    
    if supports:
        for price, score in supports[:15]:
            dist_pct = (price - current_price) / current_price * 100
            source = "+".join(score.source_timeframes) if score.source_timeframes else "?"
            lines.append(f"{price:>12.2f} | {score.final_score:>6.1f} | {source:>15} | {dist_pct:.2f}%")
    else:
        lines.append("  (无)")
    
    lines.append("")
    lines.append("=" * 70)
    lines.append("来源说明: 1d=日线, 4h=4小时, 15m=15分钟, filled=ATR补全")
    
    return "\n".join(lines)


def format_json(
    symbol: str,
    current_price: float,
    supports: List[tuple],
    resistances: List[tuple],
) -> str:
    """格式化为 JSON 输出"""
    
    def level_to_dict(price: float, score) -> dict:
        return {
            "price": price,
            "score": score.final_score,
            "base_score": score.base_score,
            "source_timeframes": score.source_timeframes,
            "is_resonance": score.is_resonance,
            "psychology_anchor": score.psychology_anchor,
            "distance_pct": (price - current_price) / current_price * 100,
        }
    
    result = {
        "symbol": symbol,
        "current_price": current_price,
        "resistance": [level_to_dict(p, s) for p, s in resistances],
        "support": [level_to_dict(p, s) for p, s in supports],
    }
    
    return json.dumps(result, indent=2, ensure_ascii=False)


async def main():
    parser = argparse.ArgumentParser(
        description="V3.2.5 水位计算工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python scripts/tools/calc_levels.py BNBUSDT
  python scripts/tools/calc_levels.py BTCUSDT --config configs/config.yaml
  python scripts/tools/calc_levels.py ETHUSDT --output json
  python scripts/tools/calc_levels.py BNBUSDT --count 20 --min-score 25
        """
    )
    
    parser.add_argument(
        "symbol",
        help="交易对 (如 BNBUSDT, BTCUSDT)"
    )
    parser.add_argument(
        "--config", "-c",
        default=DEFAULT_CONFIG,
        help=f"配置文件路径 (默认: {DEFAULT_CONFIG})"
    )
    parser.add_argument(
        "--count",
        type=int,
        default=15,
        help="显示数量 (默认 15)"
    )
    parser.add_argument(
        "--min-score",
        type=int,
        default=None,
        help="最低评分阈值 (默认从配置读取)"
    )
    parser.add_argument(
        "--output", "-o",
        choices=["table", "json"],
        default="table",
        help="输出格式 (默认 table)"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=500,
        help="K 线数量 (默认 500)"
    )
    
    args = parser.parse_args()
    
    symbol = args.symbol.upper()
    
    # 加载配置
    print(f"📂 加载配置: {args.config}")
    try:
        config = load_config(args.config)
    except Exception as e:
        print(f"❌ 配置加载失败: {e}")
        return 1
    
    level_gen_config = config.get("grid", {}).get("level_generation", {})
    
    # 解析时间框架
    timeframes_config = level_gen_config.get("timeframes", {})
    timeframes = []
    
    # 按层级顺序添加
    for layer in ["l1_strategy", "l2_skeleton", "l3_relay", "l4_tactical"]:
        tf_cfg = timeframes_config.get(layer, {})
        if tf_cfg.get("enabled", True):
            interval = tf_cfg.get("interval")
            if interval and interval not in timeframes:
                timeframes.append(interval)
    
    # 默认时间框架
    if not timeframes:
        timeframes = ["1d", "4h", "15m"]
    
    print(f"⏳ 获取 {symbol} K 线数据 ({', '.join(timeframes)})...")
    
    try:
        # 获取 K 线数据
        klines_by_tf = await fetch_klines(symbol, timeframes, args.limit)
        
        # 检查数据
        total_klines = sum(len(k) for k in klines_by_tf.values())
        if total_klines == 0:
            print(f"❌ 未获取到 {symbol} 的 K 线数据")
            return 1
        
        # 获取当前价格
        main_tf = timeframes[0] if timeframes else "4h"
        main_klines = klines_by_tf.get(main_tf, [])
        if not main_klines:
            # 尝试其他时间框架
            for tf in timeframes:
                if klines_by_tf.get(tf):
                    main_klines = klines_by_tf[tf]
                    break
        
        if not main_klines:
            print(f"❌ 无法获取当前价格")
            return 1
        
        current_price = main_klines[-1]["close"]
        print(f"📍 当前价格: ${current_price:,.2f}")
        print()
        
        # 创建 LevelCalculator
        calc = LevelCalculator(level_gen_config)
        
        # 生成支撑位
        print("🔄 计算支撑位...")
        supports = calc.generate_target_levels(
            klines_by_tf=klines_by_tf,
            current_price=current_price,
            role="support",
            max_levels=args.count * 2,
        )
        
        # 生成阻力位
        print("🔄 计算阻力位...")
        resistances = calc.generate_target_levels(
            klines_by_tf=klines_by_tf,
            current_price=current_price,
            role="resistance",
            max_levels=args.count * 2,
        )
        
        # 评分过滤
        min_score = args.min_score
        if min_score is None:
            min_score = level_gen_config.get("scoring", {}).get("min_score_threshold", 30)
        
        if supports:
            supports = [(p, s) for p, s in supports if s.final_score >= min_score][:args.count]
        else:
            supports = []
        
        if resistances:
            resistances = [(p, s) for p, s in resistances if s.final_score >= min_score][:args.count]
        else:
            resistances = []
        
        print()
        
        # 输出结果
        if args.output == "json":
            print(format_json(symbol, current_price, supports, resistances))
        else:
            print(format_table(symbol, current_price, supports, resistances, config))
        
        return 0
        
    except Exception as e:
        print(f"❌ 错误: {e}")
        logger.error(f"计算失败: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
