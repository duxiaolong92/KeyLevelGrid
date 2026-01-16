#!/usr/bin/env python3
"""
命令行清空配额计数器（fill_counter）
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

    pm.clear_fill_counters(reason=args.reason)
    print("✅ 配额计数器已清空")


if __name__ == "__main__":
    main()
