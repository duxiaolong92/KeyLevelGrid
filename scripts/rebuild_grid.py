#!/usr/bin/env python3
"""
命令行强制重置网格
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


async def run_rebuild(config_path: str) -> int:
    load_dotenv()
    strategy = KeyLevelGridStrategy.from_yaml(config_path)

    # 禁用 Telegram
    strategy.config.tg_enabled = False
    strategy._tg_bot = None
    strategy._notifier = None
    strategy._sl_synced_from_exchange = True

    # 校验实盘配置
    if not strategy.config.dry_run:
        api_key = os.getenv(strategy.config.api_key_env or "", "")
        api_secret = os.getenv(strategy.config.api_secret_env or "", "")
        if not api_key or not api_secret:
            print("❌ 未检测到交易所 API 环境变量，无法进行实盘重置网格")
            print(f"需要设置: {strategy.config.api_key_env} / {strategy.config.api_secret_env}")
            return 2

    await strategy.kline_feed.start()

    klines = strategy.kline_feed.get_cached_klines(
        strategy.config.kline_config.primary_timeframe
    )
    if len(klines) >= 50:
        strategy._current_state = strategy.indicator.calculate(klines)

    ok = await strategy.force_rebuild_grid()

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
    args = parser.parse_args()

    log_file = setup_file_logging(log_file=args.log_file)
    print(f"📝 日志文件: {log_file}")

    config_path = Path(args.config)
    if not config_path.exists():
        project_root = Path(__file__).parent.parent
        config_path = project_root / args.config
        if not config_path.exists():
            raise SystemExit(f"配置文件不存在: {args.config}")

    code = asyncio.run(run_rebuild(str(config_path)))
    raise SystemExit(code)


if __name__ == "__main__":
    main()
