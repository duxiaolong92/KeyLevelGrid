#!/usr/bin/env python3
"""
命令行强制重置网格

重构说明 (Progressive Mapping):
- 重置网格时会自动构建邻位映射 (level_mapping)
- 支持保留或清空 fill_counter
- 支持保留或清空 active_inventory
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

# 添加 src 目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dotenv import load_dotenv

from key_level_grid.strategy import KeyLevelGridStrategy
from key_level_grid.utils.logger import setup_file_logging


async def run_rebuild(config_path: str, preserve_counters: bool = False, preserve_inventory: bool = False) -> int:
    load_dotenv()
    strategy = KeyLevelGridStrategy.from_yaml(config_path)

    # 禁用 Telegram
    strategy.config.tg_enabled = False
    strategy._tg_bot = None
    strategy._notifier = None
    strategy._sl_synced_from_exchange = True

    pm = strategy.position_manager

    # 校验实盘配置
    if not strategy.config.dry_run:
        api_key = os.getenv(strategy.config.api_key_env or "", "")
        api_secret = os.getenv(strategy.config.api_secret_env or "", "")
        if not api_key or not api_secret:
            print("❌ 未检测到交易所 API 环境变量，无法进行实盘重置网格")
            print(f"需要设置: {strategy.config.api_key_env} / {strategy.config.api_secret_env}")
            return 2

    # 保存旧状态（如果需要保留）
    old_fill_counters = {}
    old_inventory = []
    if pm.state and (preserve_counters or preserve_inventory):
        if preserve_counters:
            old_fill_counters = {
                lvl.level_id: (lvl.price, lvl.fill_counter)
                for lvl in pm.state.support_levels_state
                if lvl.fill_counter > 0
            }
            print(f"📊 保留 fill_counter: {len(old_fill_counters)} 个水位")
        
        if preserve_inventory:
            old_inventory = list(pm.state.active_inventory)
            print(f"📊 保留 active_inventory: {len(old_inventory)} 笔")

    await strategy.kline_feed.start()

    klines = strategy.kline_feed.get_cached_klines(
        strategy.config.kline_config.primary_timeframe
    )
    if len(klines) >= 50:
        strategy._current_state = strategy.indicator.calculate(klines)

    print("\n🔄 开始重置网格...")
    ok = await strategy.force_rebuild_grid()

    if ok and pm.state:
        # 恢复保留的状态
        if preserve_counters and old_fill_counters:
            restored_count = 0
            for lvl in pm.state.support_levels_state:
                # 按价格匹配（因为 level_id 可能改变）
                for old_id, (old_price, old_counter) in old_fill_counters.items():
                    if abs(lvl.price - old_price) < old_price * 0.001:
                        lvl.fill_counter = old_counter
                        restored_count += 1
                        break
            print(f"✅ 恢复 fill_counter: {restored_count}/{len(old_fill_counters)}")
        
        if preserve_inventory and old_inventory:
            pm.state.active_inventory = old_inventory
            print(f"✅ 恢复 active_inventory: {len(old_inventory)} 笔")
        
        # 重建邻位映射（基于可能恢复的 fill_counter）
        if preserve_counters:
            pm.rebuild_level_mapping()
            print(f"✅ 重建 level_mapping: {len(pm.state.level_mapping)} 个映射")
        
        pm._save_state()
        
        # 显示新状态
        print(f"\n📍 新网格状态:")
        print(f"   - 支撑位: {len(pm.state.support_levels_state)}")
        print(f"   - 阻力位: {len(pm.state.resistance_levels_state)}")
        print(f"   - 邻位映射: {len(pm.state.level_mapping)}")
        
        # 显示映射详情
        if pm.state.level_mapping:
            print(f"\n   映射详情:")
            all_levels = {lvl.level_id: lvl for lvl in pm.state.support_levels_state + pm.state.resistance_levels_state}
            for src_id, tgt_id in sorted(pm.state.level_mapping.items()):
                src = all_levels.get(src_id)
                tgt = all_levels.get(tgt_id)
                if src and tgt:
                    print(f"     L_{src_id}({src.price:.0f}) → L_{tgt_id}({tgt.price:.0f})")

    await strategy.kline_feed.stop()
    return 0 if ok else 1


def main():
    parser = argparse.ArgumentParser(description="Key Level Grid 强制重置网格")
    parser.add_argument(
        "--config", "-c",
        default="configs/config.yaml",
        help="配置文件路径"
    )
    parser.add_argument(
        "--log-file",
        default=None,
        help="日志文件路径（可选）"
    )
    parser.add_argument(
        "--preserve-counters",
        action="store_true",
        help="保留 fill_counter（按价格匹配恢复）"
    )
    parser.add_argument(
        "--preserve-inventory",
        action="store_true",
        help="保留 active_inventory（持仓记录）"
    )
    args = parser.parse_args()

    log_file = setup_file_logging(log_file=args.log_file)
    print(f"📝 日志文件: {log_file}")

    config_path = Path(args.config)
    if not config_path.exists():
        project_root = Path(__file__).parent.parent
        config_path = project_root / args.config
        if not config_path.exists():
            raise SystemExit(f"配置文件不存在: {args.config}")

    code = asyncio.run(run_rebuild(
        str(config_path),
        preserve_counters=args.preserve_counters,
        preserve_inventory=args.preserve_inventory,
    ))
    raise SystemExit(code)


if __name__ == "__main__":
    main()
