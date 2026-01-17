#!/usr/bin/env python3
"""
命令行清空配额计数器（fill_counter）与邻位映射

重构说明 (Progressive Mapping):
- 清空 fill_counter 后，卖单配额也会归零
- 可选择是否同时重建邻位映射
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from key_level_grid.strategy import KeyLevelGridStrategy
from key_level_grid.utils.logger import setup_file_logging


def main() -> None:
    parser = argparse.ArgumentParser(description="Key Level Grid 清空配额计数器")
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
        "--reason",
        default="cli_manual_override",
        help="清空原因标记"
    )
    parser.add_argument(
        "--rebuild-mapping",
        action="store_true",
        help="清空后重建邻位映射（推荐）"
    )
    parser.add_argument(
        "--clear-mapping",
        action="store_true",
        help="同时清空邻位映射（完全重置）"
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

    strategy = KeyLevelGridStrategy.from_yaml(str(config_path))
    strategy.config.tg_enabled = False

    pm = strategy.position_manager
    restored = pm.restore_state(current_price=0)
    if not restored:
        print("⚠️ 未找到可恢复的网格状态文件")
        raise SystemExit(1)

    # 显示当前状态
    if pm.state:
        print(f"\n📊 当前状态:")
        print(f"   - 支撑位数量: {len(pm.state.support_levels_state)}")
        print(f"   - 阻力位数量: {len(pm.state.resistance_levels_state)}")
        print(f"   - 邻位映射数量: {len(pm.state.level_mapping)}")
        print(f"   - 活跃持仓数量: {len(pm.state.active_inventory)}")
        
        # 显示 fill_counter
        fill_counts = [
            (lvl.level_id, lvl.price, lvl.fill_counter)
            for lvl in pm.state.support_levels_state
            if lvl.fill_counter > 0
        ]
        if fill_counts:
            print(f"\n   已成交水位:")
            for level_id, price, count in fill_counts:
                print(f"     L_{level_id}({price:.2f}): {count} 次")

    # 清空计数器
    pm.clear_fill_counters(reason=args.reason)
    print("\n✅ fill_counter 已清空")

    # 处理邻位映射
    if args.clear_mapping:
        if pm.state:
            pm.state.level_mapping = {}
            pm._save_state()
        print("✅ level_mapping 已清空")
    elif args.rebuild_mapping:
        pm.rebuild_level_mapping()
        print(f"✅ level_mapping 已重建: {len(pm.state.level_mapping)} 个映射")
    else:
        print("💡 提示: 使用 --rebuild-mapping 可重建邻位映射")

    print("\n📍 操作完成")


if __name__ == "__main__":
    main()
